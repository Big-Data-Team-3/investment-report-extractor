"""
Content filtering utilities for parsed web data.
Provides exact (rule-based), guidance (LLM-assisted), and AI (full reasoning) filtering modes.
"""

from typing import Dict, List
import re
import logging
from guidance import user, assistant, gen

logger = logging.getLogger(__name__)


# ============================================================================
# Main Filter Dispatchers
# ============================================================================
def filter_exact_mode(parsed_content: dict, intent_understanding: dict) -> dict:
    """Rule-based filtering - removes irrelevant items from each section"""
    
    keywords = intent_understanding.get('keywords', [])
    target_types = intent_understanding.get('target_content_types', [])
    
    # Only filter the target types, leave others as-is or empty them
    if 'documents' in target_types:
        parsed_content['documents'] = filter_documents_exact(
            parsed_content.get('documents', []),
            keywords
        )
    else:
        parsed_content['documents'] = []  # Not needed, clear it
    
    if 'links' in target_types:
        parsed_content['links'] = filter_links_exact(
            parsed_content.get('links', {}),
            keywords
        )
    else:
        parsed_content['links'] = {'internal': [], 'external': [], 'navigation': []}
    
    if 'tables' in target_types:
        parsed_content['tables'] = filter_tables_exact(
            parsed_content.get('tables', []),
            keywords
        )
    else:
        parsed_content['tables'] = []
    
    if 'content' in target_types:
        parsed_content['content'] = filter_content_exact(
            parsed_content.get('content', {}),
            keywords
        )
        parsed_content['structure'] = filter_structure_exact(
            parsed_content.get('structure', {}),
            keywords
        )
    else:
        parsed_content['content'] = {}
        parsed_content['structure'] = {}
    
    if 'images' in target_types:
        parsed_content['images'] = filter_images_exact(
            parsed_content.get('images', []),
            keywords
        )
    else:
        parsed_content['images'] = []
    
    return parsed_content


async def filter_guidance_mode(parsed_content: dict, intent_understanding: dict) -> dict:
    """LLM-assisted filtering - removes irrelevant items from each section"""
    from _guidance import load_model
    
    keywords = intent_understanding.get('keywords', [])
    target_types = intent_understanding.get('target_content_types', [])
    expanded_intent = intent_understanding.get('expanded_intent', '')
    
    lm = load_model()
    
    # ALWAYS filter links (needed for navigation) - never clear them!
    if parsed_content.get('links'):
        parsed_content['links'] = await filter_links_with_llm(
            parsed_content['links'],
            expanded_intent,
            lm
        )
    
    # Filter documents with LLM assistance
    if 'documents' in target_types and parsed_content.get('documents'):
        parsed_content['documents'] = await filter_documents_with_llm(
            parsed_content['documents'],
            expanded_intent,
            lm
        )
    else:
        parsed_content['documents'] = []
    
    # Filter tables with LLM assistance
    if 'tables' in target_types and parsed_content.get('tables'):
        parsed_content['tables'] = await filter_tables_with_llm(
            parsed_content['tables'],
            expanded_intent,
            lm
        )
    else:
        parsed_content['tables'] = []
    
    # Filter content
    if 'content' in target_types and parsed_content.get('content'):
        # Use exact for paragraphs, add AI summary
        parsed_content['content'] = filter_content_exact(
            parsed_content['content'],
            keywords
        )
        parsed_content['content']['ai_summary'] = await summarize_content_with_llm(
            parsed_content['content']['full_text'][:2000],
            expanded_intent,
            lm
        )
    else:
        parsed_content['content'] = {}
    
    # Images
    if 'images' in target_types:
        parsed_content['images'] = filter_images_exact(
            parsed_content.get('images', []),
            keywords
        )
    else:
        parsed_content['images'] = []
    
    return parsed_content


async def filter_ai_mode(parsed_content: dict, intent_understanding: dict) -> dict:
    """Full AI reasoning for complex filtering"""
    # For now, use guidance mode as the implementation
    return await filter_guidance_mode(parsed_content, intent_understanding)


# ============================================================================
# Enhanced IR-Specific Filters
# ============================================================================

def _is_ir_related_intent(intent_understanding: dict) -> bool:
    """Check if intent is related to investor relations"""
    keywords = intent_understanding.get('keywords', [])
    expanded_intent = intent_understanding.get('expanded_intent', '').lower()
    
    ir_keywords = ['investor', 'relations', 'ir', 'financial', 'documents', 'earnings', 'sec', 'filing']
    
    # Check keywords
    keyword_match = any(kw.lower() in [k.lower() for k in keywords] for kw in ir_keywords)
    
    # Check expanded intent
    intent_match = any(kw in expanded_intent for kw in ir_keywords)
    
    logger.debug(f"IR intent check - Keywords: {keyword_match}, Intent: {intent_match}")
    return keyword_match or intent_match


def filter_exact_mode_enhanced_ir(parsed_content: dict, intent_understanding: dict) -> dict:
    """Enhanced exact mode specifically for IR-related content using notebook logic"""
    logger.info("Using enhanced IR exact mode filtering")
    
    keywords = intent_understanding.get('keywords', [])
    target_types = intent_understanding.get('target_content_types', [])
    
    # Use enhanced IR link filtering
    if 'links' in target_types or not target_types:  # Always filter links for navigation
        parsed_content['links'] = filter_links_exact_enhanced_ir(
            parsed_content.get('links', {}), 
            keywords,
            parsed_content.get('tables', [])  # Pass tables for DOW 30 detection
        )
    
    # Use IR-focused document filtering
    if 'documents' in target_types:
        parsed_content['documents'] = filter_documents_exact_ir_focused(
            parsed_content.get('documents', []),
            keywords
        )
    else:
        parsed_content['documents'] = []
    
    # Use regular exact filtering for other content types
    if 'tables' in target_types:
        parsed_content['tables'] = filter_tables_exact(
            parsed_content.get('tables', []),
            keywords
        )
    else:
        parsed_content['tables'] = []
    
    if 'content' in target_types:
        parsed_content['content'] = filter_content_exact(
            parsed_content.get('content', {}),
            keywords
        )
        parsed_content['structure'] = filter_structure_exact(
            parsed_content.get('structure', {}),
            keywords
        )
    else:
        parsed_content['content'] = {}
        parsed_content['structure'] = {}
    
    if 'images' in target_types:
        parsed_content['images'] = filter_images_exact(
            parsed_content.get('images', []),
            keywords
        )
    else:
        parsed_content['images'] = []
    
    return parsed_content


def filter_links_exact_enhanced_ir(links: dict, keywords: List[str], tables: List[dict] = None) -> dict:
    """
    Enhanced IR link filtering with sophisticated scoring from notebook logic.
    Includes special handling for DOW 30 CNBC company profile links.
    """
    
    # Check if we have table links with company profiles (DOW 30 detection)
    table_company_links = []
    dow30_tickers = set()  # Dynamically collect DOW 30 tickers from the table
    
    if tables:
        for table in tables:
            for table_link in table.get('links', []):
                if table_link.get('is_company_profile', False):
                    # Extract ticker from URL
                    url = table_link['url']
                    ticker = None
                    
                    # Extract ticker from different URL patterns
                    if '/quotes/' in url:
                        ticker = url.split('/quotes/')[-1].split('?')[0].split('/')[0].upper()
                    elif '/symbol/' in url:
                        ticker = url.split('/symbol/')[-1].split('?')[0].split('/')[0].upper()
                    
                    # If we found a ticker, this is a valid company profile link
                    if ticker and len(ticker) <= 5:  # Valid ticker length
                        dow30_tickers.add(ticker)  # Add to our dynamic DOW 30 list
                        converted_link = {
                            'url': table_link['url'],
                            'text': table_link['text'],
                            'title': table_link.get('title', ''),
                            'ticker': ticker,
                            'relevance_score': 0.95,
                            'matched_patterns': ['dow30_company_profile']
                        }
                        table_company_links.append(converted_link)
                        logger.info(f"Found DOW 30 company: {ticker} ({table_link['text']}) -> {table_link['url']}")
    
    # If we found company profile links in tables, this is the DOW 30 page
    if table_company_links:
        logger.info(f"DOW 30 page detected - found {len(table_company_links)} companies: {sorted(dow30_tickers)}")
        
        # Return ONLY the company profile links from the DOW 30 table
        filtered_links = {
            'internal': table_company_links,
            'external': [],
            'navigation': []  # Don't include any other links!
        }
        
        return filtered_links
    
    # If no table company links found, check regular links but only for known patterns
    all_links = []
    for category in ['internal', 'external', 'navigation']:
        all_links.extend(links.get(category, []))
    
    cnbc_company_links = []
    for link in all_links:
        url = link.get('url', '').lower()
        text = link.get('text', '').lower()
        
        # Check for CNBC company profile patterns from notebook
        if ('/quotes/' in url or '/symbol/' in url or 'symbol' in url) and 'cnbc.com' in url:
            # Extract ticker
            ticker = None
            if '/quotes/' in url:
                ticker = url.split('/quotes/')[-1].split('?')[0].split('/')[0].upper()
            elif '/symbol/' in url:
                ticker = url.split('/symbol/')[-1].split('?')[0].split('/')[0].upper()
            
            # Include any valid CNBC company profile link (we don't know DOW 30 list yet)
            if ticker and len(ticker) <= 5:  # Valid ticker length
                link['relevance_score'] = 0.95
                link['matched_patterns'] = ['cnbc_company_profile']
                link['ticker'] = ticker
                cnbc_company_links.append(link)
                logger.info(f"Found CNBC company profile: {ticker} -> {link['url']}")
    
    # If we found CNBC company profile links, return only those
    if cnbc_company_links:
        logger.info(f"CNBC company profiles detected - returning {len(cnbc_company_links)} company links")
        
        # Return ONLY the company profile links
        filtered_links = {
            'internal': cnbc_company_links,
            'external': [],
            'navigation': []  # Don't include any other links!
        }
        
        return filtered_links
    
    # Regular IR link filtering if not a DOW 30 or CNBC company page
    return _filter_links_regular_ir(links, keywords)


def _filter_links_regular_ir(links: dict, keywords: List[str]) -> dict:
    """Enhanced IR link filtering using notebook scoring logic"""
    logger.debug(f"Enhanced IR link filtering for {sum(len(v) for v in links.values())} links")
    
    # IR-related URL patterns with scores (from notebook)
    ir_url_patterns = {
        'investor.': 50,
        'investors.': 50,
        'ir.': 50,
        '/investor': 40,
        '/investors': 40,
        '/investor-relations': 45,
        '/investorrelations': 45,
        '/shareholder': 35,
        '/ir': 30,
    }
    
    # IR-related text patterns with scores
    ir_text_patterns = {
        'investor relations': 40,
        'investors': 30,
        'investor': 25,
        'shareholder': 20,
        'financial information': 20,
        'stock information': 20,
        'ir': 15,
    }
    
    # Additional IR document keywords
    ir_doc_patterns = {
        'annual report': 30,
        '10-k': 35,
        '10-q': 35,
        '8-k': 35,
        'proxy': 25,
        'earnings': 25,
        'sec filing': 30,
        'presentation': 20,
        'transcript': 20,
    }
    
    filtered_links = {'internal': [], 'external': [], 'navigation': []}
    
    for category in ['internal', 'external', 'navigation']:
        for link in links.get(category, []):
            url_lower = link['url'].lower()
            text_lower = link.get('text', '').lower()
            title_lower = link.get('title', '').lower()
            combined_text = f"{text_lower} {title_lower}"
            
            # Calculate IR-specific score
            ir_score = 0
            
            # URL pattern scoring
            for pattern, points in ir_url_patterns.items():
                if pattern in url_lower:
                    ir_score += points
            
            # Text pattern scoring
            for pattern, points in ir_text_patterns.items():
                if pattern in combined_text:
                    ir_score += points
            
            # Document pattern scoring
            for pattern, points in ir_doc_patterns.items():
                if pattern in combined_text or pattern in url_lower:
                    ir_score += points
            
            # Keyword matching (original logic)
            keyword_score = 0
            if keywords:
                keywords_lower = [k.lower() for k in keywords]
                url_text = f"{url_lower} {combined_text}"
                keyword_score = sum(1 for kw in keywords_lower if kw in url_text)
            
            # Total score combines IR-specific patterns with keyword matching
            total_score = ir_score + (keyword_score * 10)  # Weight keyword matches
            
            # Only include links with meaningful scores
            if total_score > 15:  # Minimum threshold for IR relevance
                filtered_links[category].append({
                    **link,
                    'relevance_score': total_score,
                    'ir_score': ir_score,
                    'keyword_score': keyword_score,
                    'matched_patterns': _get_matched_patterns(url_lower, combined_text, ir_url_patterns, ir_text_patterns)
                })
        
        # Sort by total score
        filtered_links[category].sort(key=lambda x: x['relevance_score'], reverse=True)
    
    total_filtered = sum(len(v) for v in filtered_links.values())
    logger.debug(f"IR link filtering: {total_filtered} relevant links found")
    
    return filtered_links


def filter_documents_exact_ir_focused(documents: List[dict], keywords: List[str]) -> List[dict]:
    """IR-focused document filtering with enhanced scoring"""
    logger.debug(f"IR-focused document filtering for {len(documents)} documents")
    
    # IR-specific document keywords with weights
    ir_doc_keywords = {
        'annual report': 5,
        '10-k': 6,
        '10-q': 6,
        '8-k': 6,
        'proxy': 4,
        'earnings': 4,
        'financial': 3,
        'investor': 3,
        'sec filing': 5,
        'presentation': 3,
        'transcript': 3,
        'quarterly': 4,
        'results': 3,
    }
    
    filtered_docs = []
    keywords_lower = [k.lower() for k in keywords] if keywords else []
    
    for doc in documents:
        url_text = (doc['url'] + ' ' + doc.get('text', '')).lower()
        
        # Calculate IR-specific score
        ir_score = 0
        matched_ir_keywords = []
        for kw, weight in ir_doc_keywords.items():
            if kw in url_text:
                ir_score += weight
                matched_ir_keywords.append(kw)
        
        # Calculate user keyword score
        keyword_score = 0
        matched_keywords = []
        for kw in keywords_lower:
            if kw in url_text:
                keyword_score += 1
                matched_keywords.append(kw)
        
        # Total score: weight IR keywords higher
        total_score = (ir_score * 2) + keyword_score
        
        if total_score > 0:
            filtered_docs.append({
                **doc,
                'relevance_score': total_score,
                'ir_score': ir_score,
                'keyword_score': keyword_score,
                'matched_ir_keywords': matched_ir_keywords,
                'matched_keywords': matched_keywords
            })
    
    filtered_docs.sort(key=lambda x: x['relevance_score'], reverse=True)
    logger.debug(f"IR document filtering: {len(filtered_docs)} relevant documents found")
    
    return filtered_docs


def _get_matched_patterns(url_lower: str, text_lower: str, url_patterns: dict, text_patterns: dict) -> List[str]:
    """Helper function to get matched patterns for debugging"""
    matched = []
    
    for pattern in url_patterns:
        if pattern in url_lower:
            matched.append(f"URL:{pattern}")
    
    for pattern in text_patterns:
        if pattern in text_lower:
            matched.append(f"TEXT:{pattern}")
    
    return matched
# ============================================================================
# Exact Mode Filters (Rule-based)
# ============================================================================

def filter_documents_exact(documents: List[dict], keywords: List[str]) -> List[dict]:
    """Filter documents by keyword matching"""
    if not keywords:
        return documents
    
    filtered_docs = []
    keywords_lower = [k.lower() for k in keywords]
    
    for doc in documents:
        url_text = (doc['url'] + ' ' + doc.get('text', '')).lower()
        score = sum(1 for kw in keywords_lower if kw in url_text)
        
        if score > 0:
            filtered_docs.append({
                **doc,
                'relevance_score': score,
                'matched_keywords': [kw for kw in keywords_lower if kw in url_text]
            })
    
    filtered_docs.sort(key=lambda x: x['relevance_score'], reverse=True)
    return filtered_docs


def filter_links_exact(links: dict, keywords: List[str]) -> dict:
    """Filter links by keyword matching"""
    if not keywords:
        return links
    
    filtered_links = {'internal': [], 'external': [], 'navigation': []}
    keywords_lower = [k.lower() for k in keywords]
    
    for category in ['internal', 'external', 'navigation']:
        for link in links.get(category, []):
            url_text = (link['url'] + ' ' + link.get('text', '') + ' ' + link.get('title', '')).lower()
            score = sum(1 for kw in keywords_lower if kw in url_text)
            
            if score > 0:
                filtered_links[category].append({
                    **link,
                    'relevance_score': score,
                    'matched_keywords': [kw for kw in keywords_lower if kw in url_text]
                })
        
        filtered_links[category].sort(key=lambda x: x['relevance_score'], reverse=True)
    
    return filtered_links


def filter_tables_exact(tables: List[dict], keywords: List[str]) -> List[dict]:
    """Filter tables by keyword matching in headers and cells"""
    if not keywords:
        return tables
    
    filtered_tables = []
    keywords_lower = [k.lower() for k in keywords]
    
    for table in tables:
        table_text = ''
        
        if table.get('caption'):
            table_text += table['caption'] + ' '
        
        for header_row in table.get('headers', []):
            table_text += ' '.join(header_row) + ' '
        
        for row in table.get('body', [])[:5]:
            table_text += ' '.join(str(cell) for cell in row) + ' '
        
        table_text = table_text.lower()
        score = sum(1 for kw in keywords_lower if kw in table_text)
        
        if score > 0:
            filtered_tables.append({
                **table,
                'relevance_score': score,
                'matched_keywords': [kw for kw in keywords_lower if kw in table_text]
            })
    
    filtered_tables.sort(key=lambda x: x['relevance_score'], reverse=True)
    return filtered_tables


def filter_content_exact(content: dict, keywords: List[str]) -> dict:
    """Filter content paragraphs by keyword matching"""
    if not keywords or not content:
        return content
    
    keywords_lower = [k.lower() for k in keywords]
    filtered_paragraphs = []
    
    for para in content.get('paragraphs', []):
        para_lower = para.lower()
        score = sum(1 for kw in keywords_lower if kw in para_lower)
        
        if score > 0:
            filtered_paragraphs.append({
                'text': para,
                'relevance_score': score,
                'matched_keywords': [kw for kw in keywords_lower if kw in para_lower]
            })
    
    filtered_paragraphs.sort(key=lambda x: x['relevance_score'], reverse=True)
    
    return {
        'paragraphs': filtered_paragraphs[:20],
        'full_text': content.get('full_text', ''),
        'word_count': content.get('word_count', 0),
    }


def filter_images_exact(images: List[dict], keywords: List[str]) -> List[dict]:
    """Filter images by alt text and title matching"""
    if not keywords:
        return images
    
    filtered_images = []
    keywords_lower = [k.lower() for k in keywords]
    
    for img in images:
        img_text = (img.get('alt', '') + ' ' + img.get('title', '')).lower()
        score = sum(1 for kw in keywords_lower if kw in img_text)
        
        if score > 0:
            filtered_images.append({**img, 'relevance_score': score})
    
    filtered_images.sort(key=lambda x: x['relevance_score'], reverse=True)
    return filtered_images[:10]


def filter_structure_exact(structure: dict, keywords: List[str]) -> dict:
    """Filter headings by keyword matching"""
    if not keywords or not structure:
        return structure
    
    keywords_lower = [k.lower() for k in keywords]
    filtered_headings = []
    
    for heading in structure.get('headings', []):
        heading_text = heading['text'].lower()
        score = sum(1 for kw in keywords_lower if kw in heading_text)
        
        if score > 0:
            filtered_headings.append({**heading, 'relevance_score': score})
    
    return {
        'headings': filtered_headings,
        'heading_count': len(filtered_headings),
    }


# ============================================================================
# Guidance Mode Filters (LLM-assisted)
# ============================================================================

async def filter_documents_with_llm(documents: List[dict], intent: str, lm) -> List[dict]:
    """Use LLM to score document relevance"""
    if len(documents) > 20:
        from scraper import _extract_keywords
        keywords = _extract_keywords(intent)
        documents = filter_documents_exact(documents, keywords)[:20]
    
    scored_docs = []
    
    for doc in documents:
        with user():
            lm += f"""Given intent: "{intent}"
            
Rate the relevance of this document from 0.0 to 1.0:
- URL: {doc['url']}
- Text: {doc.get('text', 'N/A')}
- Type: {doc.get('type', 'N/A')}

Respond with ONLY a number between 0.0 and 1.0:"""
        
        with assistant():
            lm += gen(name='score', max_tokens=10)
        
        try:
            score = float(lm['score'].strip())
            score = max(0.0, min(1.0, score))
        except:
            score = 0.5
        
        if score > 0.3:
            scored_docs.append({**doc, 'relevance_score': score})
    
    scored_docs.sort(key=lambda x: x['relevance_score'], reverse=True)
    return scored_docs


async def filter_links_with_llm(links: dict, intent: str, lm) -> dict:
    """Use LLM to score link relevance"""
    filtered_links = {'internal': [], 'external': [], 'navigation': []}
    
    # Get internal links or empty list
    internal_links = links.get('internal', [])
    
    if not internal_links:
        # No links to filter, return empty structure
        return filtered_links
    
    # Process internal links (most important) - limit to 30 to avoid token overload
    for link in internal_links[:30]:
        with user():
            lm += f"""Given intent: "{intent}"
            
Rate relevance of this link (0.0-1.0):
- URL: {link['url']}
- Text: {link.get('text', 'N/A')}

Number only:"""
        
        with assistant():
            lm += gen(name='score', max_tokens=10)
        
        try:
            score = float(lm['score'].strip())
            score = max(0.0, min(1.0, score))
        except:
            score = 0.5
        
        if score > 0.4:
            filtered_links['internal'].append({
                **link,
                'relevance_score': score
            })
    
    # Sort by relevance
    for category in filtered_links:
        filtered_links[category].sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
    
    return filtered_links

async def filter_tables_with_llm(tables: List[dict], intent: str, lm) -> List[dict]:
    """Use LLM to identify relevant tables"""
    scored_tables = []
    
    for table in tables[:10]:
        table_summary = f"Caption: {table.get('caption', 'N/A')}\n"
        if table.get('headers'):
            table_summary += f"Headers: {', '.join(table['headers'][0][:10])}\n"
        table_summary += f"Rows: {table.get('row_count', 0)}"
        
        with user():
            lm += f"""Given intent: "{intent}"
            
Rate relevance of this table (0.0-1.0):
{table_summary}

Number only:"""
        
        with assistant():
            lm += gen(name='score', max_tokens=10)
        
        try:
            score = float(lm['score'].strip())
            score = max(0.0, min(1.0, score))
        except:
            score = 0.5
        
        if score > 0.4:
            scored_tables.append({**table, 'relevance_score': score})
    
    scored_tables.sort(key=lambda x: x['relevance_score'], reverse=True)
    return scored_tables


async def summarize_content_with_llm(content: str, intent: str, lm) -> str:
    """Summarize content relevance"""
    with user():
        lm += f"""Given intent: "{intent}"

Summarize how this content relates to the intent (2-3 sentences):

{content}

Summary:"""
    
    with assistant():
        lm += gen(name='summary', max_tokens=150)
    
    return lm['summary'].strip()