import asyncio
from playwright.async_api import async_playwright
import multiprocessing as mp
from _filters import filter_exact_mode, filter_guidance_mode, filter_ai_mode
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from typing import Dict, List, Optional
import re
import logging
from _guidance import load_env
from guidance import system, user, assistant, gen, select
from guidance.models import OpenAI
import time
from ir_extractor_exact import filter_links_exact_enhanced

# region Logging
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scraper.log'),
        logging.StreamHandler()
    ],
    force=True
)
logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.ERROR)  # Only errors
logging.getLogger('httpcore').setLevel(logging.ERROR)
logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)  # In case you use requests
# endregion

# region Public functions for external use
# ------------ Public functions for external use ------------




async def run_scraper(seed_url:str,root_intent:str,parent_context:dict=None, scraping_mode:str="exact"):
    """Main async function that orchestrates the scraping"""
    try:
        logger.info(f"Running scraper in {scraping_mode} mode for {seed_url}")
        
        # scrape the website
        content = await scrape_website(seed_url)
        logger.debug(f"Scraped {len(content)} bytes from {seed_url}")
        
        # Layer 1: parse the content and understand the intent
        parsed_content = parse_raw_content(content,seed_url,root_intent)
        
        # Layer 2: Intent understanding - skip LLM for exact mode
        if scraping_mode == "exact":
            intent_understanding = _create_rule_based_intent(root_intent)
            logger.debug("Using rule-based intent understanding (no LLM)")
        else:
            intent_understanding = _intent_understanding(parsed_content,root_intent)
            logger.debug("Using LLM-based intent understanding")
        
        # Layer 3: Intent-Based Filtering
        filtered_content = await filter_by_intent(parsed_content,intent_understanding, scraping_mode)
        
        # Layer 4: Link Prioritization
        next_links = await prioritize_links(filtered_content,intent_understanding)
        
        # Layer 5: Refine Intent for each next link - skip LLM for exact mode
        if scraping_mode == "exact":
            refined_links = _refine_intent_rule_based(next_links, intent_understanding, seed_url)
            logger.debug("Using rule-based intent refinement (no LLM)")
        else:
            refined_links = await refine_intent_for_links(
                next_links,
                intent_understanding,
                current_page_context={
                    "url": seed_url,
                    "title": parsed_content.get('title', ''),
                    "page_type": _classify_page_type(parsed_content),
                    "found_documents": filtered_content.get('documents', []),
                },
                root_intent=root_intent,
                parent_context=parent_context
            )
            logger.debug("Using LLM-based intent refinement")
        
        # Now each link has a refined_intent!
        logger.debug(f"Top 3 refined links:")
        for link in refined_links[:3]:
            logger.debug(f"  Link: {link['text']}")
            logger.debug(f"    Refined Intent: {link['refined_intent']}")
        logger.debug(f"Found {len(next_links)} links to follow")
        return {
            "parsed_content": parsed_content,
            "filtered_content": filtered_content,
            "next_links": refined_links,  # With refined intents!
        }
    except Exception as e:
        logger.error(f"Error in run_scraper for {seed_url}: {e}")
        return None

# endregion

# region Private Helper functions not for external use
# ------------ Private Helper functions not for external use ------------

def _create_rule_based_intent(root_intent: str) -> dict:
    """
    Create intent understanding using only rule-based analysis (no LLM).
    This is used for exact/manual mode to avoid any LLM calls.
    """
    # Extract keywords using simple pattern matching
    keywords = _extract_keywords_rule_based(root_intent)
    
    # Determine target content types based on keywords
    target_types = _determine_target_types_rule_based(root_intent, keywords)
    
    # Create a simple expanded intent (just cleaned up version)
    expanded_intent = root_intent.strip()
    
    return {
        'original_intent': root_intent,
        'expanded_intent': expanded_intent,
        'keywords': keywords,
        'target_content_types': target_types,
        'filtering_steps': ['keyword_matching', 'pattern_matching'],
        'relevance_criteria': 'keyword_presence',
        'next_actions': ['follow_relevant_links', 'extract_documents'],
    }


def _extract_keywords_rule_based(intent: str) -> List[str]:
    """Extract keywords from intent using rule-based patterns (no LLM)"""
    # Common IR/financial keywords
    ir_keywords = [
        'investor', 'relations', 'financial', 'earnings', 'documents', 
        'reports', 'sec', 'filing', '10-k', '10-q', '8-k', 'proxy',
        'annual', 'quarterly', 'presentation', 'transcript'
    ]
    
    # Extract keywords that appear in the intent
    intent_lower = intent.lower()
    found_keywords = []
    
    for keyword in ir_keywords:
        if keyword in intent_lower:
            found_keywords.append(keyword)
    
    # Add some common variations
    if 'ir' in intent_lower or 'investor relations' in intent_lower:
        found_keywords.extend(['investor', 'relations', 'ir'])
    
    if 'document' in intent_lower:
        found_keywords.extend(['documents', 'files', 'pdf'])
        
    if 'financial' in intent_lower:
        found_keywords.extend(['financial', 'finance', 'money'])
    
    # Remove duplicates and return
    return list(set(found_keywords))


def _determine_target_types_rule_based(intent: str, keywords: List[str]) -> List[str]:
    """Determine what content types to focus on using rules (no LLM)"""
    target_types = ['links']  # Always need links for navigation
    
    intent_lower = intent.lower()
    
    # Look for document-related terms
    doc_terms = ['document', 'report', 'filing', 'pdf', 'presentation', 'transcript']
    if any(term in intent_lower for term in doc_terms):
        target_types.append('documents')
    
    # Look for table/data terms
    table_terms = ['table', 'data', 'financial', 'numbers', 'metrics']
    if any(term in intent_lower for term in table_terms):
        target_types.append('tables')
    
    # Look for content terms
    content_terms = ['information', 'content', 'text', 'details']
    if any(term in intent_lower for term in content_terms):
        target_types.append('content')
    
    return target_types


def _refine_intent_rule_based(next_links: List[dict], intent_understanding: dict, current_url: str) -> List[dict]:
    """
    Refine intent for links using rule-based logic (no LLM).
    This creates simple refined intents based on link patterns.
    """
    refined_links = []
    base_intent = intent_understanding.get('original_intent', '')
    keywords = intent_understanding.get('keywords', [])
    
    for link in next_links:
        url = link.get('url', '').lower()
        text = link.get('text', '').lower()
        
        # Create refined intent based on link characteristics
        if any(pattern in url for pattern in ['investor', 'ir.', '/investors']):
            refined_intent = f"Navigate to investor relations section to find financial documents and reports"
        elif any(pattern in url for pattern in ['/earnings', '/financial', '/reports']):
            refined_intent = f"Access financial reports and earnings information"
        elif any(pattern in text for pattern in ['10-k', '10-q', '8-k', 'annual report']):
            refined_intent = f"Download SEC filing or annual report document"
        elif any(pattern in text for pattern in ['presentation', 'transcript']):
            refined_intent = f"Access investor presentation or earnings call transcript"
        elif 'pdf' in url or 'download' in url:
            refined_intent = f"Download document file"
        else:
            # Generic refined intent
            refined_intent = f"Explore link for investor relations content and financial documents"
        
        # Add the refined intent to the link
        refined_link = {**link}
        refined_link['refined_intent'] = refined_intent
        refined_links.append(refined_link)
    
    return refined_links


def parse_raw_content(content,base_url:str,intent:str):
    """
    Input raw html content and return a structured representation of the content
    Args:
        content: raw html content
        base_url: base URL for resolving relative links
        intent: user intent (not used in parsing but kept for compatibility)
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
    tables = _extract_tables(soup, base_url)
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

async def scrape_website(url: str, headless: bool = True, timeout: int = 60000, max_retries: int = 3):
    """Scrape a website with anti-bot measures and retries"""
    
    for attempt in range(max_retries):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=headless,
                    args=['--disable-blink-features=AutomationControlled']
                )
                
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080},
                )
                
                page = await context.new_page()
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
                
                # Use 'load' instead of 'networkidle'
                await page.goto(url, wait_until="load", timeout=timeout)
                await page.wait_for_timeout(1000)
                
                content = await page.content()
                await browser.close()
                
                logger.debug(f"Successfully scraped {url} (attempt {attempt + 1})")
                return content
                
        except Exception as e:
            logger.warning(f"Scrape attempt {attempt + 1}/{max_retries} failed for {url}: {str(e)[:200]}")
            
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.info(f"Retrying in {wait_time} seconds...")
                await asyncio.sleep(wait_time)
    
    logger.error(f"All {max_retries} attempts failed for {url}")
    raise Exception(f"Failed to scrape {url} after {max_retries} attempts")

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
        from _filters import _is_ir_related_intent, filter_exact_mode_enhanced_ir, filter_exact_mode
        # Use enhanced exact mode for IR-related intents
        if _is_ir_related_intent(intent_understanding):
            return filter_exact_mode_enhanced_ir(filtered, intent_understanding)
        else:
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

async def refine_intent_for_links(
    next_links: List[dict],
    intent_understanding: dict,
    current_page_context: dict,
    root_intent: str,
    parent_context: dict = None
) -> List[dict]:
    """
    Refine intent for each next link based on context and navigation goals
    
    Args:
        next_links: Prioritized list of links from prioritize_links()
        intent_understanding: Intent understanding from _intent_understanding()
        current_page_context: Info about current page (url, title, page_type, etc.)
        root_intent: Original user intent (never changes)
        parent_context: Context from parent page (how we got here)
    
    Returns:
        List of link objects with 'refined_intent' field added to each
    """
    
    from _guidance import load_model
    from guidance import system, user, assistant, gen
    
    if not next_links:
        return []
    
    lm = load_model()
    refined_links = []
    
    # Get context info
    expanded_intent = intent_understanding.get('expanded_intent', root_intent)
    target_types = intent_understanding.get('target_content_types', [])
    current_url = current_page_context.get('url', '')
    current_title = current_page_context.get('title', '')
    page_type = current_page_context.get('page_type', 'unknown')
    
    # Build context summary
    context_summary = f"""
    Current Page: {current_title} ({current_url})
    Page Type: {page_type}
    Root Goal: {root_intent}
    Current Objective: {expanded_intent}
    Looking For: {', '.join(target_types)}
    """
    
    if parent_context:
        context_summary += f"\nNavigation Path: {parent_context.get('navigation_path', 'N/A')}"
    
    # Process each link individually (limit to top 5 to save API calls and reduce rate limits)
    for link in next_links[:5]:
        link_url = link.get('url', '')
        link_text = link.get('text', '')
        link_category = link.get('category', 'internal')
        priority_score = link.get('final_priority', 0)
        
        # Generate refined intent for this specific link
        with system():
            lm += """You are a web scraping navigation expert. Given a root goal and current context, 
    generate a specific, actionable sub-intent for visiting a particular link. 
    The sub-intent should be concise (1-2 sentences) and describe what to look for on that page."""
        
        with user():
            lm += f"""{context_summary}

    Now I'm considering following this link:
    - Link Text: "{link_text}"
    - URL: {link_url}
    - Category: {link_category}
    - Priority Score: {priority_score:.2f}

    Given the root goal and current context, what should my specific intent be when visiting this page?
    Respond with a clear, actionable sub-intent (1-2 sentences):"""
        
        with assistant():
            lm += gen(name='sub_intent', max_tokens=100)
        
        refined_intent = lm['sub_intent'].strip()
        
        # Add refined intent to link object
        refined_links.append({
            **link,
            'refined_intent': refined_intent,
            'root_intent': root_intent,
            'context': {
                'from_page': current_url,
                'from_title': current_title,
                'depth': parent_context.get('depth', 0) + 1 if parent_context else 1,
            }
        })
        
        # Delay to avoid rate limits
        await asyncio.sleep(0.5)
    
    # Add remaining links without LLM refinement (use heuristic)
    for link in next_links[5:]:
        refined_links.append({
            **link,
            'refined_intent': _generate_heuristic_intent(link, intent_understanding, root_intent),
            'root_intent': root_intent,
            'context': {
                'from_page': current_url,
                'from_title': current_title,
                'depth': parent_context.get('depth', 0) + 1 if parent_context else 1,
            }
        })
    
    return refined_links


def _generate_heuristic_intent(link: dict, intent_understanding: dict, root_intent: str) -> str:
    """
    Generate a sub-intent using heuristics (for links beyond top 10)
    """
    link_text = link.get('text', '').lower()
    link_url = link.get('url', '').lower()
    target_types = intent_understanding.get('target_content_types', [])
    
    # Pattern-based intent generation
    if 'documents' in target_types:
        if any(word in link_text + link_url for word in ['investor', 'annual', 'report', 'filing']):
            return f"Navigate to find and download financial documents related to: {root_intent}"
        elif any(word in link_text + link_url for word in ['sec', '10-k', '10-q']):
            return f"Access SEC filings to find required documents for: {root_intent}"
    
    if 'tables' in target_types:
        if any(word in link_text + link_url for word in ['data', 'financial', 'metric', 'stat']):
            return f"Find and extract financial data tables for: {root_intent}"
    
    # Generic fallback
    return f"Explore '{link_text}' to find content relevant to: {root_intent}"

def _classify_page_type(parsed_content: dict) -> str:
    """
    Classify what type of page this is based on content
    """
    title = parsed_content.get('title', '').lower()
    headings = [h.get('text', '').lower() for h in parsed_content.get('structure', {}).get('headings', [])]
    all_text = ' '.join([title] + headings)
    
    # Check for page type patterns
    if any(word in all_text for word in ['investor relations', 'shareholder', 'sec filing']):
        return 'investor_relations'
    elif any(word in all_text for word in ['annual report', '10-k', '10-q', 'financial statement']):
        return 'financial_documents'
    elif parsed_content.get('documents') and len(parsed_content['documents']) > 5:
        return 'document_library'
    elif parsed_content.get('tables') and len(parsed_content['tables']) > 3:
        return 'data_page'
    elif len(parsed_content.get('links', {}).get('internal', [])) > 20:
        return 'navigation_hub'
    else:
        return 'content_page'

def _is_ir_related_intent(intent_understanding: dict) -> bool:
    """Check if intent is related to investor relations"""
    keywords = intent_understanding.get('keywords', [])
    ir_keywords = ['investor', 'relations', 'ir', 'financial', 'documents', 'earnings']
    return any(kw.lower() in [k.lower() for k in keywords] for kw in ir_keywords)

def filter_exact_mode_enhanced_ir(parsed_content: dict, intent_understanding: dict) -> dict:
    """Enhanced exact mode specifically for IR-related content"""
    # Use the enhanced link filtering
    parsed_content['links'] = filter_links_exact_enhanced(
        parsed_content.get('links', {}), 
        intent_understanding
    )
    
    # Use existing exact filtering for other content types
    return filter_exact_mode(parsed_content, intent_understanding)

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


def _extract_tables(soup: BeautifulSoup, base_url: str = None) -> List[Dict]:
    """Extract table data with header/body/footer differentiation and embedded links"""
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
        
        # Extract body rows (from tbody or remaining tr tags) with links
        body = []
        body_links = []  # Track links found in table body
        tbody = table.find('tbody')
        if tbody:
            for tr in tbody.find_all('tr'):
                cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
                if cells:
                    body.append(cells)
                    # Extract links from this row
                    row_links = _extract_table_row_links(tr, base_url)
                    body_links.extend(row_links)
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
                            # Extract links from this row
                            row_links = _extract_table_row_links(tr, base_url)
                            body_links.extend(row_links)
        
        # Extract footer rows (from tfoot)
        footer = []
        tfoot = table.find('tfoot')
        if tfoot:
            for tr in tfoot.find_all('tr'):
                cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
                if cells:
                    footer.append(cells)
        
        tables.append({
            'headers': headers,
            'body': body,
            'footer': footer,
            'links': body_links,  # Add links found in table
            'row_count': len(body),
            'column_count': len(headers[0]) if headers else (len(body[0]) if body else 0),
        })
    
    return tables


def _extract_table_row_links(tr, base_url: str = None) -> List[Dict]:
    """Extract links from a table row, specifically for DOW 30 company profiles"""
    links = []
    
    for a in tr.find_all('a', href=True):
        href = a['href'].strip()
        text = a.get_text(strip=True)
        
        # Skip empty or anchor-only links
        if not href or href.startswith('#') or href.startswith('javascript:'):
            continue
        
        # Resolve relative URLs to absolute URLs
        if base_url:
            absolute_url = urljoin(base_url, href)
        else:
            absolute_url = href
        
        # Check if this looks like a company profile link (DOW 30 specific)
        is_company_profile = (
            '/quotes/' in absolute_url.lower() or 
            '/symbol/' in absolute_url.lower() or 
            'symbol' in absolute_url.lower()
        )
        
        link_data = {
            'url': absolute_url,
            'text': text,
            'title': a.get('title', ''),
            'is_company_profile': is_company_profile
        }
        
        links.append(link_data)
        
        # Log company profile links for debugging
        if is_company_profile:
            logger.info(f"Found company profile link in table: {text} -> {absolute_url}")
    
    return links


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
    root_intent = "Our ultimate goal is to extract financial documents for each company from their respective IR(Investor Relations) pages. But we will start to go to such a page only from a seed URL in CNBC DOW30 index provide just now. We need to traverse smartly and click on relevant links to get to the specific company's IR page. After reaching that IR page, we need to look for any document link or presentation, transcript, press release within the IR page. Your role for now is to smartly scrape the websites from the Seed URL and provide relevant link directions to go to the next link in order to reach our ultimate goal, by leading ourselves to the IR page of each company in the DOW30 index from the seed URL (current)."
    logger.info("Starting the scraper...")
    start_time = time.time()
    asyncio.run(run_scraper(seed_url,root_intent))
    end_time = time.time()
    logger.info(f"Scraper completed in {end_time - start_time} seconds")
# endregion