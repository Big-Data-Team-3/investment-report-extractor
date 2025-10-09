"""
Content filtering utilities for parsed web data.
Provides exact (rule-based), guidance (LLM-assisted), and AI (full reasoning) filtering modes.
"""

from typing import Dict, List
import re
from guidance import user, assistant, gen


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