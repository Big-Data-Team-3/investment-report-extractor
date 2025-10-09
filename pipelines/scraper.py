import asyncio
from playwright.async_api import async_playwright
import multiprocessing as mp
from _filters import filter_exact_mode, filter_guidance_mode, filter_ai_mode
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from typing import Dict, List, Optional
import re
from _guidance import load_env
from guidance import system, user, assistant, gen, select
from guidance.models import OpenAI
import time
# region Public functions for external use
# ------------ Public functions for external use ------------

def parse_raw_content(content,base_url:str,intent:str):
    """
    Input raw html content and return a structured representation of the content
    Args:
        content: raw html content
    Returns:
        structured representation of the content, through a dictionary including:
        - title, metadata, text content, links, documents, images, structure
    """
    soup = BeautifulSoup(content, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg']):
        tag.decompose()
    title = _extract_title(soup)
    metadata = _extract_metadata(soup)
    content = _extract_main_content(soup)
    structure = _extract_structure(soup)
    links = _extract_links(soup, base_url)
    documents = _extract_documents(soup, base_url)
    images = _extract_images(soup, base_url)
    tables = _extract_tables(soup)
    return {
        'title': title,
        'metadata': metadata,
        'content': content,
        'structure': structure,
        'links': links,
        'documents': documents,
        'images': images,
        'tables': tables,
    }

async def scrape_website(url: str, headless: bool = True, timeout: int = 30000):
    """Scrape a website and return its HTML content"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=timeout)
        content = await page.content()
        await browser.close()
        return content

async def filter_by_intent(parsed_content:dict,intent_understanding:dict, mode:str):
    """
    Filter parsed content based on user intent
    Args:
        parsed_content: Parsed content from parse_raw_content()
        intent_understanding: Intent understanding from _intent_understanding()
        mode: Filtering mode - "exact" (rule-based), "guidance" (LLM-assisted), "ai" (full AI)
    Returns:
        Filtered content dictionary with only relevant information, based on the mode/level of scraping
    """
    
    # Start with a copy of the full structure
    filtered = {
        'title': parsed_content.get('title', ''),
        'metadata': parsed_content.get('metadata', {}),
        'content': parsed_content.get('content', {}),
        'structure': parsed_content.get('structure', {}),
        'links': parsed_content.get('links', {}),
        'documents': parsed_content.get('documents', []),
        'images': parsed_content.get('images', []),
        'tables': parsed_content.get('tables', []),
        'intent': intent_understanding.get('expanded_intent', ''),
    }
    
    # Dispatch to appropriate filtering strategy from filters.py
    # These functions will filter items within each key
    if mode == "exact":
        return filter_exact_mode(filtered, intent_understanding)
    elif mode == "guidance":
        return await filter_guidance_mode(filtered, intent_understanding)
    elif mode == "ai":
        return await filter_ai_mode(filtered, intent_understanding)
    else:
        raise ValueError(f"Unknown mode: {mode}. Must be 'exact', 'guidance', or 'ai'")

async def prioritize_links(filtered_content: dict, intent_understanding: dict) -> List[dict]:
    """
    Prioritize and rank links from filtered content based on intent
    Args:
        filtered_content: Filtered content from filter_by_intent()
        intent_understanding: Intent understanding from _intent_understanding()
    Returns:
        Sorted list of links with priority scores, ready to be followed
    """
    
    all_links = []
    keywords = intent_understanding.get('keywords', [])
    expanded_intent = intent_understanding.get('expanded_intent', '')
    
    # Combine all links with category weights
    links_data = filtered_content.get('links', {})
    
    # Internal links get highest base priority
    for link in links_data.get('internal', []):
        all_links.append({
            **link,
            'category': 'internal',
            'base_priority': 1.0,
            'final_priority': 0.0
        })
    
    # External links get medium base priority
    for link in links_data.get('external', []):
        all_links.append({
            **link,
            'category': 'external',
            'base_priority': 0.6,
            'final_priority': 0.0
        })
    
    # Navigation links get lowest base priority (usually not useful for crawling)
    for link in links_data.get('navigation', []):
        all_links.append({
            **link,
            'category': 'navigation',
            'base_priority': 0.3,
            'final_priority': 0.0
        })
    
    # Calculate final priority for each link
    for link in all_links:
        priority_score = link['base_priority']
        
        # Boost by existing relevance_score from filtering (if available)
        if 'relevance_score' in link:
            priority_score += link['relevance_score'] * 0.5
        
        # Boost by keyword matches in URL/text
        url_text = (link['url'] + ' ' + link.get('text', '') + ' ' + link.get('title', '')).lower()
        keyword_matches = sum(1 for kw in keywords if kw.lower() in url_text)
        priority_score += keyword_matches * 0.2
        
        # Boost by URL depth (prefer specific pages over generic ones)
        url_depth = link['url'].count('/')
        if url_depth > 3:  # Specific pages
            priority_score += 0.1
        
        # Penalize common non-content pages
        non_content_patterns = [
            'login', 'signin', 'signup', 'register', 'cart', 'checkout',
            'privacy', 'terms', 'cookie', 'subscribe', 'newsletter'
        ]
        if any(pattern in link['url'].lower() for pattern in non_content_patterns):
            priority_score *= 0.3
        
        # Boost document-related links if looking for documents
        if 'documents' in intent_understanding.get('target_content_types', []):
            doc_patterns = ['pdf', 'download', 'document', 'file', 'report', 'filing']
            if any(pattern in url_text for pattern in doc_patterns):
                priority_score += 0.3
        
        # Boost table/data-related links if looking for tables
        if 'tables' in intent_understanding.get('target_content_types', []):
            data_patterns = ['data', 'table', 'financials', 'statistics', 'metrics']
            if any(pattern in url_text for pattern in data_patterns):
                priority_score += 0.3
        
        link['final_priority'] = round(priority_score, 3)
    
    # Sort by final priority (highest first)
    all_links.sort(key=lambda x: x['final_priority'], reverse=True)
    
    # Filter out very low priority links (< 0.5)
    all_links = [link for link in all_links if link['final_priority'] >= 0.5]
    
    # Limit to top 50 links to avoid overwhelming
    limited_links = all_links[:50]
    return all_links

async def score_relevance(next_links:dict,intent_understanding:dict):
    """
    Input next links and intent understanding and return the relevance scores
    Args:
        next_links: next links
        intent_understanding: intent understanding
    """
    pass

async def run_scraper(seed_url:str,intent:str):
    """Main async function that orchestrates the scraping"""
    try:
        # scrape the website
        content = await scrape_website(seed_url)
        # Layer 1:parse the content and understand the intent
        parsed_content = parse_raw_content(content,seed_url,intent)
        # intent understanding
        intent_understanding = _intent_understanding(parsed_content,intent)
        # Layer 2: Intent-Based Filtering
        filtered_content = await filter_by_intent(parsed_content,intent_understanding, mode="ai")
        # Layer 3: Link Prioritization
        next_links = await prioritize_links(filtered_content,intent_understanding)
        # Layer 4: Confidence Scoring
        confidence_scores = await score_relevance(next_links,intent_understanding)
        composed_object = {
            "parsed_content": parsed_content,
            "filtered_content": filtered_content,
            "next_links": next_links,
            "confidence_scores": confidence_scores,
        }
        print("--------")
        print(f"Found {len(next_links)} links to follow")
        print("--------")
        return composed_object
    except Exception as e:
        print(f"Error: {e}")
        return None

# endregion

# region Private Helper functions not for external use
# ------------ Private Helper functions not for external use ------------

def _intent_understanding(parsed_content:dict,intent:str):
    """
    Input parsed content and intent and return the intent understanding
    Args:
        parsed_content: parsed content
        intent: intent
    Returns:
        
    """
     # Step 1: Extract basic patterns from intent
    intent_analysis = _analyze_intent_patterns(intent)
    
    # Step 2: Analyze available content
    content_summary = _summarize_available_content(parsed_content)
    
    # Step 3: Use LLM to generate extraction strategy (if intent is complex)
    if _is_complex_intent(intent):
        strategy = _generate_strategy_with_llm(intent, content_summary)
    else:
        strategy = _generate_strategy_rules(intent_analysis, content_summary)
    
    return {
        'original_intent': intent,
        'expanded_intent': strategy['expanded_intent'],
        'keywords': strategy['keywords'],
        'target_content_types': strategy['target_content_types'],
        'filtering_steps': strategy['filtering_steps'],
        'relevance_criteria': strategy['relevance_criteria'],
        'next_actions': strategy['next_actions'],
    }

def _analyze_intent_patterns(intent: str) -> Dict:
    """Extract patterns from intent using regex and NLP rules"""
    intent_lower = intent.lower()
    
    # Detect action verbs
    action_verbs = {
        'find': ['find', 'locate', 'get', 'retrieve', 'search'],
        'extract': ['extract', 'parse', 'scrape', 'collect'],
        'download': ['download', 'fetch', 'grab'],
        'analyze': ['analyze', 'understand', 'summarize'],
    }
    
    detected_actions = []
    for action, verbs in action_verbs.items():
        if any(verb in intent_lower for verb in verbs):
            detected_actions.append(action)
    
    # Detect target objects
    target_patterns = {
        'documents': r'(document|pdf|file|report|form|filing)',
        'links': r'(link|url|page|website)',
        'tables': r'(table|data|spreadsheet|financials?)',
        'content': r'(content|text|article|information)',
        'images': r'(image|photo|picture|graphic)',
    }
    
    detected_targets = []
    for target, pattern in target_patterns.items():
        if re.search(pattern, intent_lower):
            detected_targets.append(target)
    
    # Extract domain-specific keywords
    domain_keywords = {
        'investor_relations': ['investor', 'annual report', 'quarterly', 'earnings', 'sec filing', '10-k', '10-q'],
        'news': ['news', 'article', 'press release', 'announcement'],
        'products': ['product', 'catalog', 'pricing', 'specifications'],
        'contact': ['contact', 'email', 'phone', 'address'],
    }
    
    detected_domain = None
    for domain, keywords in domain_keywords.items():
        if any(kw in intent_lower for kw in keywords):
            detected_domain = domain
            break
    
    # Extract specific keywords (nouns, important terms)
    keywords = _extract_keywords(intent)
    
    return {
        'actions': detected_actions or ['find'],
        'targets': detected_targets or ['content'],
        'domain': detected_domain,
        'keywords': keywords,
    }


def _extract_keywords(text: str) -> List[str]:
    """Extract important keywords from text"""
    # Remove common stop words
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
    
    # Split and clean
    words = re.findall(r'\b\w+\b', text.lower())
    keywords = [w for w in words if w not in stop_words and len(w) > 3]
    
    return list(set(keywords))[:10]  # Top 10 unique keywords


def _summarize_available_content(parsed_content: dict) -> Dict:
    """Summarize what content is available in the parsed data"""
    return {
        'has_tables': len(parsed_content.get('tables', [])) > 0,
        'table_count': len(parsed_content.get('tables', [])),
        'has_documents': len(parsed_content.get('documents', [])) > 0,
        'document_count': len(parsed_content.get('documents', [])),
        'document_types': list(set(doc['type'] for doc in parsed_content.get('documents', []))),
        'has_internal_links': len(parsed_content.get('links', {}).get('internal', [])) > 0,
        'internal_link_count': len(parsed_content.get('links', {}).get('internal', [])),
        'has_external_links': len(parsed_content.get('links', {}).get('external', [])) > 0,
        'external_link_count': len(parsed_content.get('links', {}).get('external', [])),
        'has_navigation_links': len(parsed_content.get('links', {}).get('navigation', [])) > 0,
        'navigation_link_count': len(parsed_content.get('links', {}).get('navigation', [])),
        'word_count': parsed_content.get('content', {}).get('word_count', 0),
        'heading_count': parsed_content.get('structure', {}).get('heading_count', 0),
    }


def _is_complex_intent(intent: str) -> bool:
    """Determine if intent needs LLM understanding or simple rules"""
    # Complex if:
    # - Long and descriptive (>50 chars)
    # - Contains multiple clauses
    # - Contains ambiguous terms
    # - Contains conditional logic
    
    complex_indicators = [
        len(intent) > 50,
        ' and ' in intent.lower() and ' then ' in intent.lower(),
        any(word in intent.lower() for word in ['if', 'when', 'unless', 'provided that']),
        intent.count(',') > 2,
    ]
    
    return any(complex_indicators)


def _generate_strategy_rules(intent_analysis: Dict, content_summary: Dict) -> Dict:
    """Generate strategy using rule-based logic"""
    
    actions = intent_analysis['actions']
    targets = intent_analysis['targets']
    keywords = intent_analysis['keywords']
    
    # Determine what content types to focus on
    target_content_types = []
    if 'documents' in targets or 'download' in actions:
        target_content_types.append('documents')
    if 'links' in targets or 'find' in actions:
        target_content_types.append('links')
    if 'tables' in targets:
        target_content_types.append('tables')
    if 'content' in targets or 'extract' in actions:
        target_content_types.append('content')
    
    # Generate filtering steps
    filtering_steps = []
    
    if 'documents' in target_content_types:
        if content_summary['has_documents']:
            filtering_steps.append({
                'step': 1,
                'action': 'filter_documents',
                'criteria': f"Document URL or text contains keywords: {', '.join(keywords[:5])}",
                'expected_output': 'List of relevant document links'
            })
        else:
            filtering_steps.append({
                'step': 1,
                'action': 'search_links_for_documents',
                'criteria': f"Find links that might lead to documents (containing: {', '.join(keywords[:5])})",
                'expected_output': 'Links to follow for documents'
            })
    
    if 'links' in target_content_types:
        filtering_steps.append({
            'step': len(filtering_steps) + 1,
            'action': 'filter_links',
            'criteria': f"Link URL or text contains keywords: {', '.join(keywords[:5])}",
            'expected_output': 'Prioritized list of links to explore'
        })
    
    if 'tables' in target_content_types:
        filtering_steps.append({
            'step': len(filtering_steps) + 1,
            'action': 'extract_tables',
            'criteria': f"Tables with headers or content related to: {', '.join(keywords[:5])}",
            'expected_output': 'Structured table data'
        })
    
    if 'content' in target_content_types:
        filtering_steps.append({
            'step': len(filtering_steps) + 1,
            'action': 'extract_content',
            'criteria': f"Text content containing keywords: {', '.join(keywords[:5])}",
            'expected_output': 'Main text content'
        })
    
    # Generate next actions
    next_actions = []
    if content_summary['has_documents']:
        next_actions.append('Download and analyze documents')
    if content_summary['internal_link_count'] > 0:
        next_actions.append('Follow internal links for more content')
    if not content_summary['has_documents'] and not content_summary['has_tables']:
        next_actions.append('Navigate to linked pages to find target content')
    
    return {
        'expanded_intent': f"Action: {', '.join(actions)}. Targets: {', '.join(targets)}. Focus on: {', '.join(keywords[:5])}",
        'keywords': keywords,
        'target_content_types': target_content_types,
        'filtering_steps': filtering_steps,
        'relevance_criteria': {
            'keyword_match': keywords[:10],
            'content_types': target_content_types,
        },
        'next_actions': next_actions,
    }


def _generate_strategy_with_llm(intent: str, content_summary: Dict) -> Dict:
    """Use LLM to generate sophisticated extraction strategy"""
    
    lm = OpenAI("gpt-3.5-turbo",api_key=load_env())
    
    with system():
        lm += """You are an expert web scraping strategist. Given a user's intent and available content, 
        generate a detailed extraction strategy with specific steps."""
    
    with user():
        lm += f"""Intent: {intent}

Available content on the page:
- Tables: {content_summary['table_count']} found
- Documents: {content_summary['document_count']} found ({', '.join(content_summary['document_types']) if content_summary['document_types'] else 'none'})
- Internal links: {content_summary['internal_link_count']}
- Text content: {content_summary['word_count']} words

Please provide:
1. An expanded, clarified version of the intent
2. 5-10 specific keywords to look for
3. What content types to focus on (documents, links, tables, text)
4. Step-by-step filtering strategy
5. Criteria to assess relevance

Respond in this format:
EXPANDED_INTENT: [clarified intent]
KEYWORDS: [keyword1, keyword2, keyword3, ...]
FOCUS_ON: [content_type1, content_type2, ...]
STEPS:
1. [specific action with criteria]
2. [specific action with criteria]
...
RELEVANCE: [how to determine if content is relevant]
NEXT_ACTIONS: [what to do with the results]
"""
    
    with assistant():
        lm += gen(name='strategy', max_tokens=800)
    
    # Parse LLM response
    response = lm['strategy']
    
    expanded_intent = _extract_section(response, 'EXPANDED_INTENT')
    keywords_str = _extract_section(response, 'KEYWORDS')
    keywords = [k.strip() for k in keywords_str.split(',') if k.strip()]
    focus_str = _extract_section(response, 'FOCUS_ON')
    target_content_types = [f.strip().lower() for f in focus_str.split(',') if f.strip()]
    
    # Parse steps
    steps_section = _extract_section(response, 'STEPS', end_marker='RELEVANCE')
    filtering_steps = []
    for i, line in enumerate(steps_section.split('\n'), 1):
        line = line.strip()
        if line and (line[0].isdigit() or line.startswith('-')):
            # Remove numbering
            step_text = re.sub(r'^\d+\.?\s*|\-\s*', '', line)
            if step_text:
                filtering_steps.append({
                    'step': i,
                    'action': step_text,
                })
    
    relevance = _extract_section(response, 'RELEVANCE', end_marker='NEXT_ACTIONS')
    next_actions_str = _extract_section(response, 'NEXT_ACTIONS')
    next_actions = [a.strip() for a in next_actions_str.split('\n') if a.strip() and not a.strip().startswith('-')]
    
    return {
        'expanded_intent': expanded_intent or intent,
        'keywords': keywords[:15] if keywords else _extract_keywords(intent),
        'target_content_types': target_content_types or ['content'],
        'filtering_steps': filtering_steps,
        'relevance_criteria': {
            'description': relevance,
            'keyword_match': keywords,
        },
        'next_actions': next_actions,
    }


def _extract_section(text: str, marker: str, end_marker: str = None) -> str:
    """Extract section from formatted LLM response"""
    pattern = rf'{marker}:\s*(.+?)(?:{end_marker}:|$)'
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return ''


def _extract_title(soup: BeautifulSoup) -> str:
    """Extract page title"""
    # Try multiple sources
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    
    # Try og:title
    og_title = soup.find('meta', property='og:title')
    if og_title and og_title.get('content'):
        return og_title['content'].strip()
    
    # Try h1
    h1 = soup.find('h1')
    if h1:
        return h1.get_text(strip=True)
    
    return ''


def _extract_metadata(soup: BeautifulSoup) -> Dict:
    """Extract metadata from meta tags"""
    metadata = {}
    
    # Standard meta tags
    for meta in soup.find_all('meta'):
        name = meta.get('name') or meta.get('property')
        content = meta.get('content')
        if name and content:
            metadata[name] = content
    
    # Common useful metadata
    useful_keys = [
        'description', 'keywords', 'author', 'viewport',
        'og:title', 'og:description', 'og:image', 'og:url', 'og:type',
        'twitter:card', 'twitter:title', 'twitter:description'
    ]
    
    return {k: v for k, v in metadata.items() if k in useful_keys}


def _extract_main_content(soup: BeautifulSoup) -> Dict:
    """Extract main textual content with semantic structure"""
    
    # Try to find main content area
    main_content = None
    for selector in ['main', 'article', '[role="main"]', '#content', '.content']:
        main_content = soup.select_one(selector)
        if main_content:
            break
    
    # Fallback to body
    if not main_content:
        main_content = soup.body or soup
    
    # Extract text with paragraph structure
    paragraphs = []
    for p in main_content.find_all(['p', 'div'], recursive=True):
        text = p.get_text(strip=True)
        if text and len(text) > 20:  # Filter out tiny snippets
            paragraphs.append(text)
    
    # Extract all text (cleaned)
    full_text = main_content.get_text(separator='\n', strip=True)
    full_text = re.sub(r'\n{3,}', '\n\n', full_text)  # Remove excessive newlines
    
    return {
        'paragraphs': paragraphs[:50],  # Limit to avoid huge outputs
        'full_text': full_text,
        'word_count': len(full_text.split()),
    }


def _extract_structure(soup: BeautifulSoup) -> Dict:
    """Extract document structure (headings hierarchy)"""
    headings = []
    
    for level in range(1, 7):  # h1 to h6
        for heading in soup.find_all(f'h{level}'):
            text = heading.get_text(strip=True)
            if text:
                headings.append({
                    'level': level,
                    'text': text,
                })
    
    return {
        'headings': headings,
        'heading_count': len(headings),
    }


def _extract_links(soup: BeautifulSoup, base_url: Optional[str] = None) -> Dict:
    """Extract navigation and page links"""
    links = {
        'internal': [],
        'external': [],
        'navigation': [],
    }
    
    base_domain = urlparse(base_url).netloc if base_url else None
    
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        text = a.get_text(strip=True)
        
        # Skip empty or anchor-only links
        if not href or href.startswith('#') or href.startswith('javascript:'):
            continue
        
        # Resolve relative URLs
        if base_url:
            absolute_url = urljoin(base_url, href)
        else:
            absolute_url = href
        
        link_data = {
            'url': absolute_url,
            'text': text,
            'title': a.get('title', ''),
        }
        
        # Categorize link
        if _is_navigation_link(a):
            links['navigation'].append(link_data)
        elif base_domain and urlparse(absolute_url).netloc == base_domain:
            links['internal'].append(link_data)
        elif urlparse(absolute_url).netloc:
            links['external'].append(link_data)
    
    return links


def _extract_documents(soup: BeautifulSoup, base_url: Optional[str] = None) -> List[Dict]:
    """Extract document links (PDFs, docs, etc.)"""
    documents = []
    doc_extensions = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.csv']
    
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        
        # Check if it's a document
        if any(href.lower().endswith(ext) for ext in doc_extensions):
            if base_url:
                absolute_url = urljoin(base_url, href)
            else:
                absolute_url = href
            
            documents.append({
                'url': absolute_url,
                'text': a.get_text(strip=True),
                'type': href.split('.')[-1].upper(),
            })
    
    return documents


def _extract_images(soup: BeautifulSoup, base_url: Optional[str] = None) -> List[Dict]:
    """Extract image information"""
    images = []
    
    for img in soup.find_all('img', src=True):
        src = img['src'].strip()
        
        if base_url:
            absolute_url = urljoin(base_url, src)
        else:
            absolute_url = src
        
        images.append({
            'url': absolute_url,
            'alt': img.get('alt', ''),
            'title': img.get('title', ''),
        })
    
    return images[:20]  # Limit to avoid too many images


def _extract_tables(soup: BeautifulSoup) -> List[Dict]:
    """Extract table data with header/body/footer differentiation"""
    tables = []
    
    for table in soup.find_all('table')[:5]:  # Limit tables
        # Extract header rows (from thead or first rows with th tags)
        headers = []
        thead = table.find('thead')
        if thead:
            for tr in thead.find_all('tr'):
                cells = [th.get_text(strip=True) for th in tr.find_all(['th', 'td'])]
                if cells:
                    headers.append(cells)
        else:
            # Check first few rows for th tags
            for tr in table.find_all('tr')[:3]:
                if tr.find('th'):
                    cells = [cell.get_text(strip=True) for cell in tr.find_all(['th', 'td'])]
                    if cells:
                        headers.append(cells)
                else:
                    break  # Stop when we hit a row without th tags
        
        # Extract body rows (from tbody or remaining tr tags)
        body = []
        tbody = table.find('tbody')
        if tbody:
            for tr in tbody.find_all('tr'):
                cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
                if cells:
                    body.append(cells)
        else:
            # Get all rows that aren't in thead/tfoot and don't have th tags
            for tr in table.find_all('tr', recursive=False):
                # Skip if this row was already counted as header
                if not tr.find_parent('thead') and not tr.find_parent('tfoot'):
                    # Skip rows that are all th tags (header rows)
                    if not (tr.find('th') and not tr.find('td')):
                        cells = [cell.get_text(strip=True) for cell in tr.find_all(['td', 'th'])]
                        if cells and cells not in headers:
                            body.append(cells)
        
        # Extract footer rows (from tfoot)
        footer = []
        tfoot = table.find('tfoot')
        if tfoot:
            for tr in tfoot.find_all('tr'):
                cells = [cell.get_text(strip=True) for cell in tr.find_all(['td', 'th'])]
                if cells:
                    footer.append(cells)
        
        if headers or body or footer:
            tables.append({
                'headers': headers,
                'body': body,
                'footer': footer,
                'header_count': len(headers),
                'row_count': len(body),
                'footer_count': len(footer),
                'caption': table.find('caption').get_text(strip=True) if table.find('caption') else '',
            })
    
    return tables


def _is_navigation_link(tag) -> bool:
    """Determine if a link is likely navigation"""
    # Check if link is in nav, header, footer, or has nav-related classes
    nav_parents = tag.find_parents(['nav', 'header', 'footer'])
    if nav_parents:
        return True
    
    # Check classes
    classes = ' '.join(tag.get('class', []))
    nav_keywords = ['nav', 'menu', 'header', 'footer', 'breadcrumb']
    return any(keyword in classes.lower() for keyword in nav_keywords)

# endregion

# region Main function
# ------------ Main function ------------

if __name__ == "__main__":
    seed_url = "https://www.cnbc.com/dow-30/"
    intent = "Our ultimate goal is to extract financial documents for each company from their respective IR(Investor Relations) pages. But we will start to go to such a page only from a seed URL in CNBC DOW30 index provide just now. We need to traverse smartly and click on relevant links to get to the specific company's IR page. After reaching that IR page, we need to look for any document link or presentation, transcript, press release within the IR page. Your role for now is to smartly scrape the websites from the Seed URL and provide relevant link directions to go to the next link in order to reach our ultimate goal, by leading ourselves to the IR page of each company in the DOW30 index from the seed URL (current)."
    print("Starting the scraper...")
    start_time = time.time()
    asyncio.run(run_scraper(seed_url,intent))
    end_time = time.time()
    print(f"Scraper completed in {end_time - start_time} seconds")
# endregion