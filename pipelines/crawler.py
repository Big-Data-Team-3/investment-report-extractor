"""
Queue-Based Web Crawler with Intent-Driven Navigation

This module orchestrates multi-page crawling using a priority queue approach (BFS),
wrapping the single-page scraper.py functions with state persistence and parallel execution.
"""

import asyncio
import heapq
import json
import time
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from datetime import datetime

# Import from scraper module
from scraper import run_scraper, _classify_page_type
from _guidance import load_model
from guidance import system, user, assistant, gen
from ir_extractor_exact import IRExtractorExact

# region Logging
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ],
    force=True
)
logger = logging.getLogger(__name__)

# Suppress noisy third-party loggers
logging.getLogger('httpx').setLevel(logging.ERROR)  # Only errors
logging.getLogger('httpcore').setLevel(logging.ERROR)
logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)  # In case you use requests
# endregion

# region Configuration System
# ============================================================================

@dataclass
class CrawlerConfig:
    """Configuration for the crawler behavior"""
    max_depth: int = 4
    max_concurrent_workers: int = 5
    max_documents: int = 200
    timeout_seconds: int = 600
    priority_threshold: float = 0.5
    rate_limit_delay: float = 1.0
    max_queue_size: int = 500
    terminal_detection_mode: str = "rule"  # "rule" or "llm"
    scraping_mode: str = "exact"  # "exact" or "guidance" or "ai"
    state_file: str = "crawler_state.json"
    
    def to_dict(self) -> dict:
        """Convert config to dictionary"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'CrawlerConfig':
        """Create config from dictionary"""
        return cls(**data)
    
    def save(self, filepath: str = None):
        """Save config to JSON file"""
        filepath = filepath or self.state_file.replace('.json', '_config.json')
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Config saved to {filepath}")
    
    @classmethod
    def load(cls, filepath: str) -> 'CrawlerConfig':
        """Load config from JSON file"""
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)


# endregion

# region Data Structures
# ============================================================================

@dataclass
class QueueItem:
    """Represents an item in the crawl queue"""
    priority: float
    url: str
    intent: str
    depth: int
    parent_url: Optional[str] = None
    context: Dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    
    def __lt__(self, other):
        """For priority queue comparison (lower priority value = higher priority)"""
        return self.priority < other.priority
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'QueueItem':
        """Create from dictionary"""
        return cls(**data)


@dataclass
class TerminalResult:
    """Represents a final result (document, media, etc.)"""
    url: str
    type: str  # PDF, VIDEO, PRESS_RELEASE_PAGE, etc.
    title: str
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'TerminalResult':
        """Create from dictionary"""
        return cls(**data)


@dataclass
class CrawlerState:
    """Maintains state of the crawler for persistence"""
    visited_urls: Set[str] = field(default_factory=set)
    queued_urls: Set[str] = field(default_factory=set)  # Track URLs in queue for fast duplicate checking
    queue_items: List[QueueItem] = field(default_factory=list)
    results: Dict[str, List[TerminalResult]] = field(default_factory=lambda: {
        'documents': [],
        'media': [],
        'press_releases': []
    })
    statistics: Dict[str, Any] = field(default_factory=lambda: {
        'pages_crawled': 0,
        'max_depth_reached': 0,
        'total_documents_found': 0,
        'crawl_start_time': time.time(),
        'last_save_time': time.time(),
    })
    
    def to_dict(self) -> dict:
        """Convert state to dictionary for JSON serialization"""
        return {
            'visited_urls': list(self.visited_urls),
            'queued_urls': list(self.queued_urls),
            'queue_items': [item.to_dict() for item in self.queue_items],
            'results': {
                key: [result.to_dict() for result in results]
                for key, results in self.results.items()
            },
            'statistics': self.statistics
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'CrawlerState':
        """Create state from dictionary"""
        state = cls()
        state.visited_urls = set(data.get('visited_urls', []))
        state.queued_urls = set(data.get('queued_urls', []))
        state.queue_items = [QueueItem.from_dict(item) for item in data.get('queue_items', [])]
        state.results = {
            key: [TerminalResult.from_dict(result) for result in results]
            for key, results in data.get('results', {}).items()
        }
        state.statistics = data.get('statistics', {})
        return state
    
    def save(self, filepath: str):
        """Save state to JSON file"""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        self.statistics['last_save_time'] = time.time()
        logger.info(f"State saved to {filepath} (visited: {len(self.visited_urls)}, queue: {len(self.queue_items)})")
    
    @classmethod
    def load(cls, filepath: str) -> 'CrawlerState':
        """Load state from JSON file"""
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)


# endregion

# region Helper Functions
# ============================================================================

def normalize_url(url: str) -> str:
    """
    Normalize URL for deduplication
    - Remove fragments (#anchor)
    - Remove trailing slashes
    - Sort query parameters
    - Lowercase scheme and domain
    """
    try:
        parsed = urlparse(url)
        
        # Lowercase scheme and netloc
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        
        # Remove trailing slash from path
        path = parsed.path.rstrip('/')
        if not path:
            path = '/'
        
        # Sort query parameters
        query_params = parse_qs(parsed.query)
        sorted_query = urlencode(sorted(query_params.items()), doseq=True)
        
        # Reconstruct without fragment
        normalized = urlunparse((
            scheme,
            netloc,
            path,
            parsed.params,
            sorted_query,
            ''  # No fragment
        ))
        
        return normalized
    except Exception as e:
        logger.warning(f"Error normalizing URL {url}: {e}")
        return url


def extract_domain(url: str) -> str:
    """Extract domain from URL for rate limiting"""
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return ""


def should_stop_crawling(state: CrawlerState, config: CrawlerConfig) -> Tuple[bool, str]:
    """
    Check if crawling should stop based on termination conditions
    Returns (should_stop, reason)
    """
    # Check if queue is empty
    if not state.queue_items:
        return True, "Queue is empty"
    
    # Check document limit
    total_docs = sum(len(results) for results in state.results.values())
    if total_docs >= config.max_documents:
        return True, f"Reached max documents limit ({config.max_documents})"
    
    # Check timeout
    elapsed = time.time() - state.statistics['crawl_start_time']
    if elapsed >= config.timeout_seconds:
        return True, f"Timeout reached ({config.timeout_seconds}s)"
    
    # Check if all remaining queue items are below priority threshold
    high_priority_items = [item for item in state.queue_items if item.priority <= -config.priority_threshold]
    if not high_priority_items:
        return True, "No high-priority items remaining in queue"
    
    return False, ""


# endregion

# region Terminal Detection Functions
# ============================================================================

def classify_results_rule_based(
    scraper_result: dict,
    queue_item: QueueItem,
    root_intent: str,
    config: CrawlerConfig
) -> Tuple[List[TerminalResult], List[QueueItem]]:
    """
    Classify scraper results using rule-based logic
    Returns: (terminal_results, new_queue_items)
    """
    terminal_results = []
    new_queue_items = []
    
    if not scraper_result:
        return terminal_results, new_queue_items
    
    filtered_content = scraper_result.get('filtered_content', {})
    next_links = scraper_result.get('next_links', [])
    parsed_content = scraper_result.get('parsed_content', {})
    
    current_url = queue_item.url
    current_depth = queue_item.depth
    
    # Build navigation path
    navigation_path = queue_item.context.get('navigation_path', [])
    if isinstance(navigation_path, str):
        navigation_path = [navigation_path]
    navigation_path = list(navigation_path) + [current_url]
    
    # Extract documents as terminal results
    documents = filtered_content.get('documents', [])
    for doc in documents:
        terminal_results.append(TerminalResult(
            url=doc['url'],
            type=doc.get('type', 'DOCUMENT'),
            title=doc.get('text', 'Untitled Document'),
            metadata={
                'found_on_page': current_url,
                'navigation_path': navigation_path,
                'depth': current_depth,
                'intent_matched': queue_item.intent,
                'timestamp': datetime.now().isoformat(),
            }
        ))
    
    # Check page type for terminal classification
    page_type = _classify_page_type(parsed_content)
    
    # If this is a document library or press release page with content, consider it terminal
    if page_type in ['document_library', 'financial_documents']:
        # Already extracted documents above, but mark as terminal location
        pass
    
    # If we're at max depth, don't add more queue items
    if current_depth >= config.max_depth:
        return terminal_results, new_queue_items
    
    # Add next links to queue if they meet criteria
    for link in next_links:
        link_priority = link.get('final_priority', 0)
        
        # Skip low-priority links
        if link_priority < config.priority_threshold:
            continue
        
        # Create new queue item
        new_context = link.get('context', {})
        new_context['navigation_path'] = navigation_path
        
        new_item = QueueItem(
            priority=-link_priority,  # Negate for min-heap (higher priority = lower value)
            url=link['url'],
            intent=link.get('refined_intent', queue_item.intent),
            depth=current_depth + 1,
            parent_url=current_url,
            context=new_context,
            timestamp=time.time()
        )
        new_queue_items.append(new_item)
    
    return terminal_results, new_queue_items


async def classify_results_llm_based(
    scraper_result: dict,
    queue_item: QueueItem,
    root_intent: str,
    config: CrawlerConfig
) -> Tuple[List[TerminalResult], List[QueueItem]]:
    """
    Classify scraper results using LLM evaluation
    Returns: (terminal_results, new_queue_items)
    """
    terminal_results = []
    new_queue_items = []
    
    if not scraper_result:
        return terminal_results, new_queue_items
    
    filtered_content = scraper_result.get('filtered_content', {})
    next_links = scraper_result.get('next_links', [])
    parsed_content = scraper_result.get('parsed_content', {})
    
    current_url = queue_item.url
    current_depth = queue_item.depth
    current_intent = queue_item.intent
    
    # Build navigation path
    navigation_path = queue_item.context.get('navigation_path', [])
    if isinstance(navigation_path, str):
        navigation_path = [navigation_path]
    navigation_path = list(navigation_path) + [current_url]
    
    # First, extract obvious documents (these are always terminal)
    documents = filtered_content.get('documents', [])
    for doc in documents:
        terminal_results.append(TerminalResult(
            url=doc['url'],
            type=doc.get('type', 'DOCUMENT'),
            title=doc.get('text', 'Untitled Document'),
            metadata={
                'found_on_page': current_url,
                'navigation_path': navigation_path,
                'depth': current_depth,
                'intent_matched': current_intent,
                'timestamp': datetime.now().isoformat(),
                'detection_method': 'llm',
            }
        ))
    
    # Use LLM to evaluate goal achievement
    try:
        lm = load_model()
        
        page_summary = {
            'title': parsed_content.get('title', ''),
            'has_documents': len(documents) > 0,
            'document_count': len(documents),
            'has_tables': len(filtered_content.get('tables', [])) > 0,
            'next_links_count': len(next_links),
            'depth': current_depth,
        }
        
        with system():
            lm += """You are a web crawling goal evaluator. Given a page's content and the user's intent,
assess whether the goal has been achieved on this page. Score from 0.0 to 1.0."""
        
        with user():
            lm += f"""Root Goal: {root_intent}
Current Intent: {current_intent}
Page Summary: {json.dumps(page_summary, indent=2)}

Question: On a scale of 0.0 to 1.0, how well does this page achieve the current intent?
- 0.0-0.3: Not relevant, keep exploring
- 0.4-0.7: Partially relevant, found some content
- 0.8-1.0: Goal achieved, found target content

Respond with just a number between 0.0 and 1.0:"""
        
        with assistant():
            lm += gen(name='score', max_tokens=10)
        
        score_str = lm['score'].strip()
        goal_score = float(score_str)
        
        logger.debug(f"  LLM Goal Score: {goal_score:.2f} for {current_url}")
        
        # If goal highly achieved and at depth > 2, don't go deeper
        if goal_score > 0.8 and current_depth >= 2:
            logger.info(f"  Goal achieved at depth {current_depth}, not exploring further")
            return terminal_results, new_queue_items
        
        # If goal poorly achieved at depth > 2, abandon this branch
        if goal_score < 0.3 and current_depth > 2:
            logger.info(f"  Low relevance at depth {current_depth}, abandoning branch")
            return terminal_results, new_queue_items
        
    except Exception as e:
        logger.warning(f"  LLM evaluation failed: {e}, falling back to rule-based")
        # Fallback to rule-based
        return classify_results_rule_based(scraper_result, queue_item, root_intent, config)
    
    # If we haven't returned yet, add next links to queue
    if current_depth >= config.max_depth:
        return terminal_results, new_queue_items
    
    for link in next_links:
        link_priority = link.get('final_priority', 0)
        
        # Boost priority based on goal score (if we're getting warmer, prioritize children)
        if goal_score > 0.5:
            link_priority += 0.3
        
        # Skip low-priority links
        if link_priority < config.priority_threshold:
            continue
        
        # Create new queue item
        new_context = link.get('context', {})
        new_context['navigation_path'] = navigation_path
        new_context['parent_goal_score'] = goal_score
        
        new_item = QueueItem(
            priority=-link_priority,
            url=link['url'],
            intent=link.get('refined_intent', current_intent),
            depth=current_depth + 1,
            parent_url=current_url,
            context=new_context,
            timestamp=time.time()
        )
        new_queue_items.append(new_item)
    
    return terminal_results, new_queue_items


def classify_results(
    scraper_result: dict,
    queue_item: QueueItem,
    root_intent: str,
    config: CrawlerConfig,
    mode: str = "rule"
) -> Tuple[List[TerminalResult], List[QueueItem]]:
    """
    Dispatcher function for result classification
    """
    if mode == "llm":
        return asyncio.create_task(
            classify_results_llm_based(scraper_result, queue_item, root_intent, config)
        )
    else:
        return classify_results_rule_based(scraper_result, queue_item, root_intent, config)


# endregion

# region Worker Function
# ============================================================================

async def process_url_from_queue(
    queue_item: QueueItem,
    root_intent: str,
    config: CrawlerConfig,
    semaphore: asyncio.Semaphore
) -> dict:
    """
    Worker function that processes a single URL from the queue
    Returns dict with terminal_results and new_queue_items
    """
    async with semaphore:
        try:
            logger.info(f"[Depth {queue_item.depth}] Processing: {queue_item.url}")
            logger.debug(f"  Intent: {queue_item.intent[:100]}...")
            
            # Call the single-page scraper
            scraper_result = await run_scraper(queue_item.url, queue_item.intent, queue_item.context, config.scraping_mode)
            
            if not scraper_result:
                logger.error(f"  ✗ Failed to scrape {queue_item.url}")
                return {'terminal_results': [], 'new_queue_items': [], 'success': False}
            
            # Classify results based on mode
            if config.terminal_detection_mode == "llm":
                terminal_results, new_queue_items = await classify_results_llm_based(
                    scraper_result, queue_item, root_intent, config
                )
            else:
                terminal_results, new_queue_items = classify_results_rule_based(
                    scraper_result, queue_item, root_intent, config
                )
            
            logger.info(f"  ✓ Found {len(terminal_results)} terminal results, {len(new_queue_items)} new links")
            
            return {
                'terminal_results': terminal_results,
                'new_queue_items': new_queue_items,
                'success': True
            }
            
        except Exception as e:
            logger.error(f"  ✗ Error processing {queue_item.url}: {e}")
            return {'terminal_results': [], 'new_queue_items': [], 'success': False}


async def crawl_dow30_ir_pages(
    seed_url: str = "https://www.cnbc.com/dow-30/",
    config: CrawlerConfig = None,
    debug: bool = True,
    test: bool = False
) -> dict:
    """
    Specialized crawler for DOW 30 IR pages using the efficient ir_extractor_exact logic.
    This bypasses the complex multi-page crawling and directly extracts IR pages.
    When test is True, it will only crawl the first 3 companies.
    Args:
        seed_url: DOW 30 CNBC URL
        config: Crawler configuration
        debug: Enable debug logging
        
    Returns:
        dict with results and statistics
    """
    if config is None:
        config = CrawlerConfig()
    
    logger.info("="*80)
    logger.info("Starting DOW 30 IR Pages Crawler (Direct Extraction)")
    logger.info("="*80)
    logger.info(f"Seed URL: {seed_url}")
    logger.info(f"Using ir_extractor_exact.py logic for efficient IR discovery")
    logger.info("="*80)
    
    start_time = time.time()
    
    # Step 1: Extract DOW 30 companies from the seed URL
    logger.info("Step 1: Extracting DOW 30 companies from CNBC...")
    
    try:
        # Use the scraper to get the DOW 30 companies
        scraper_result = await run_scraper(
            seed_url, 
            "Extract DOW 30 company profile links from the table",
            scraping_mode="exact"
        )
        
        if not scraper_result or not scraper_result.get('filtered_content'):
            logger.error("Failed to extract companies from DOW 30 page")
            return {'companies': [], 'statistics': {'error': 'Failed to extract companies'}}
        
        # Extract company links from the filtered content
        filtered_content = scraper_result['filtered_content']
        company_links = filtered_content.get('links', {}).get('internal', [])
        
        # Filter for company profile links (should be DOW 30 companies)
        companies = []
        for link in company_links:
            if link.get('is_company_profile') or '/quotes/' in link['url']:
                # Extract ticker from URL
                ticker = ""
                if '/quotes/' in link['url']:
                    ticker = link['url'].split('/quotes/')[-1].split('?')[0].split('/')[0].upper()
                
                if ticker and len(ticker) <= 5:  # Valid ticker
                    companies.append({
                        'ticker': ticker,
                        'company_name': link.get('text', ticker),
                        'cnbc_url': link['url']
                    })
        
        logger.info(f"✓ Extracted {len(companies)} DOW 30 companies")
        
        if not companies:
            logger.error("No companies found in DOW 30 page")
            return {'companies': [], 'statistics': {'error': 'No companies found'}}
        
        # Limit to first 3 companies in test mode
        if test:
            original_count = len(companies)
            companies = companies[:3]
            logger.info(f"🧪 TEST MODE: Limited to first {len(companies)} companies (out of {original_count})")
        
    except Exception as e:
        logger.error(f"Error extracting DOW 30 companies: {e}")
        return {'companies': [], 'ir_pages': [], 'statistics': {'error': str(e)}}
    
    # Step 2: Use IR Extractor to find IR pages for all companies
    logger.info("Step 2: Finding IR pages using efficient extraction logic...")
    
    try:
        ir_extractor = IRExtractorExact()
        enriched_companies = await ir_extractor.extract_ir_pages_for_companies(
            companies, 
            debug=debug,
            test=test
        )
        
        logger.info(f"✓ Successfully processed {len(enriched_companies)} companies")
        
    except Exception as e:
        logger.error(f"Error during IR extraction: {e}")
        return {'companies': companies, 'ir_pages': [], 'statistics': {'error': str(e)}}
    
    # Step 3: Prepare results in crawler format
    terminal_results = []
    
    for company in enriched_companies:
        ir_url = company.get('investor_relations_url', '')
        company_website = company.get('company_website', '')
        
        if ir_url and ir_url != company_website:
            # This is a valid IR page
            terminal_results.append({
                'url': ir_url,
                'type': 'IR_PAGE',
                'title': f"{company['ticker']} Investor Relations",
                'metadata': {
                    'ticker': company['ticker'],
                    'company_name': company.get('company_name', ''),
                    'cnbc_url': company.get('cnbc_url', ''),
                    'company_website': company_website,
                    'extraction_method': 'ir_extractor_exact',
                    'timestamp': datetime.now().isoformat(),
                }
            })
    
    end_time = time.time()
    
    # Prepare final results
    final_results = {
        'companies': enriched_companies,
        'ir_pages': terminal_results,
        'statistics': {
            'total_companies_processed': len(enriched_companies),
            'total_ir_pages_found': len(terminal_results),
            'total_duration_seconds': end_time - start_time,
            'crawl_start_time': start_time,
            'crawl_end_time': end_time,
            'extraction_method': 'ir_extractor_exact',
            'success_rate': len(terminal_results) / len(enriched_companies) if enriched_companies else 0
        }
    }
    
    logger.info("="*80)
    logger.info("DOW 30 IR EXTRACTION COMPLETE")
    logger.info("="*80)
    logger.info(f"Companies Processed: {len(enriched_companies)}")
    logger.info(f"IR Pages Found: {len(terminal_results)}")
    logger.info(f"Success Rate: {final_results['statistics']['success_rate']:.1%}")
    logger.info(f"Duration: {end_time - start_time:.2f} seconds")
    logger.info("="*80)
    
    return final_results


# endregion

# region Main Orchestrator
# ============================================================================

async def crawl_with_queue(
    seed_url: str,
    root_intent: str,
    config: CrawlerConfig = None,
    resume_state: str = None
) -> dict:
    """
    Main orchestrator function for queue-based crawling
    
    Args:
        seed_url: Starting URL
        root_intent: Overall goal/intent
        config: Crawler configuration (uses defaults if None)
        resume_state: Path to saved state file for resuming
    
    Returns:
        dict with results and statistics
    """
    # Initialize config
    if config is None:
        config = CrawlerConfig()
    
    logger.info("="*80)
    logger.info("Starting Queue-Based Crawler")
    logger.info("="*80)
    logger.info(f"Seed URL: {seed_url}")
    logger.info(f"Root Intent: {root_intent[:100]}...")
    logger.info(f"Config: max_depth={config.max_depth}, workers={config.max_concurrent_workers}, mode={config.terminal_detection_mode}")
    logger.info("="*80)
    
    # Load or initialize state
    if resume_state and Path(resume_state).exists():
        logger.info(f"Resuming from state file: {resume_state}")
        state = CrawlerState.load(resume_state)
    else:
        logger.info("Starting fresh crawl")
        state = CrawlerState()
        # Add seed URL to queue
        normalized_seed_url = normalize_url(seed_url)
        seed_item = QueueItem(
            priority=-1.0,  # Highest priority
            url=normalized_seed_url,
            intent=root_intent,
            depth=0,
            parent_url=None,
            context={'navigation_path': []},
            timestamp=time.time()
        )
        heapq.heappush(state.queue_items, seed_item)
        state.queued_urls.add(normalized_seed_url)  # Track in queued_urls set
    
    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(config.max_concurrent_workers)
    
    # Domain rate limiting
    domain_last_request = {}
    
    # Main crawling loop
    iteration = 0
    
    while state.queue_items:
        iteration += 1
        
        # Check termination conditions
        should_stop, reason = should_stop_crawling(state, config)
        if should_stop:
            logger.info("="*80)
            logger.info(f"Stopping crawl: {reason}")
            logger.info("="*80)
            break
        
        logger.info(f"--- Iteration {iteration} ---")
        logger.info(f"Queue size: {len(state.queue_items)}, Visited: {len(state.visited_urls)}")
        
        # Pop top N items from queue (batch processing)
        batch_size = min(10, len(state.queue_items))
        batch = []
        
        for _ in range(batch_size):
            if not state.queue_items:
                break
            item = heapq.heappop(state.queue_items)
            
            # Remove from queued_urls set when popped from queue
            normalized_url = normalize_url(item.url)
            state.queued_urls.discard(normalized_url)  # Use discard to avoid KeyError if not present
            
            # Skip if already visited
            if normalized_url in state.visited_urls:
                continue
            
            # Check domain rate limiting
            domain = extract_domain(item.url)
            last_request_time = domain_last_request.get(domain, 0)
            time_since_last = time.time() - last_request_time
            
            if time_since_last < config.rate_limit_delay:
                # Put back in queue and skip for now
                heapq.heappush(state.queue_items, item)
                state.queued_urls.add(normalized_url)  # Add back to queued_urls set
                continue
            
            # Mark as visited and add to batch
            state.visited_urls.add(normalized_url)
            domain_last_request[domain] = time.time()
            batch.append(item)
        
        if not batch:
            logger.debug("No items ready to process (rate limiting), waiting...")
            await asyncio.sleep(config.rate_limit_delay)
            continue
        
        logger.info(f"Processing batch of {len(batch)} URLs")
        
        # Process batch in parallel
        tasks = [
            process_url_from_queue(item, root_intent, config, semaphore)
            for item in batch
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect results
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Task {idx} failed with exception: {result}")
                continue
            
            if not result['success']:
                continue
            
            # Add terminal results
            for term_result in result['terminal_results']:
                result_type = term_result.type
                
                # Categorize by type
                if result_type in ['PDF', 'DOC', 'DOCX', 'XLS', 'XLSX', 'CSV', 'DOCUMENT']:
                    state.results['documents'].append(term_result)
                elif result_type in ['VIDEO', 'IMAGE', 'MEDIA']:
                    state.results['media'].append(term_result)
                elif 'PRESS_RELEASE' in result_type or 'NEWS' in result_type:
                    state.results['press_releases'].append(term_result)
                else:
                    state.results['documents'].append(term_result)  # Default
            
            # Add new queue items with duplicate prevention
            for new_item in result['new_queue_items']:
                # Check queue size limit
                if len(state.queue_items) >= config.max_queue_size:
                    logger.warning(f"Queue size limit reached ({config.max_queue_size}), skipping low-priority items")
                    break
                
                # Normalize URL for duplicate checking
                normalized_new_url = normalize_url(new_item.url)
                
                # Skip if already visited
                if normalized_new_url in state.visited_urls:
                    logger.debug(f"Skipping already visited URL: {new_item.url}")
                    continue
                
                # Skip if already in queue (fast O(1) lookup)
                if normalized_new_url in state.queued_urls:
                    logger.debug(f"Skipping URL already in queue: {new_item.url}")
                    continue
                
                # Add to queue and track in queued_urls set
                heapq.heappush(state.queue_items, new_item)
                state.queued_urls.add(normalized_new_url)
                logger.debug(f"Added to queue: {new_item.url} (priority: {new_item.priority})")
            
            # Update statistics
            state.statistics['pages_crawled'] += 1
            state.statistics['max_depth_reached'] = max(
                state.statistics['max_depth_reached'],
                batch[idx].depth
            )
        
        # Update total documents found
        state.statistics['total_documents_found'] = sum(
            len(results) for results in state.results.values()
        )
        
        # Periodically save state
        if iteration % 10 == 0:
            state.save(config.state_file)
        
        # Log progress
        logger.info(f"Total documents found: {state.statistics['total_documents_found']}")
    
    # Final save
    state.save(config.state_file)
    
    # Calculate final statistics
    state.statistics['crawl_end_time'] = time.time()
    state.statistics['total_duration_seconds'] = (
        state.statistics['crawl_end_time'] - state.statistics['crawl_start_time']
    )
    
    # Prepare final results
    final_results = {
        'documents': [result.to_dict() for result in state.results['documents']],
        'media': [result.to_dict() for result in state.results['media']],
        'press_releases': [result.to_dict() for result in state.results['press_releases']],
        'statistics': state.statistics
    }
    
    return final_results


# endregion

# region Utility Functions
# ============================================================================

def save_results_to_file(results: dict, filepath: str):
    """Save final results to JSON file"""
    output_path = Path(filepath)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"Results saved to: {filepath}")


def print_crawl_summary(results: dict):
    """Pretty print crawl summary with enhanced statistics"""
    stats = results.get('statistics', {})
    
    logger.info("="*80)
    logger.info("CRAWL SUMMARY")
    logger.info("="*80)
    
    # Basic stats
    logger.info(f"Pages Crawled: {stats.get('pages_crawled', 0)}")
    logger.info(f"Max Depth Reached: {stats.get('max_depth_reached', 0)}")
    logger.info(f"Duration: {stats.get('total_duration_seconds', 0):.2f} seconds")
    
    # Calculate pages per second
    duration = stats.get('total_duration_seconds', 0)
    pages_crawled = stats.get('pages_crawled', 0)
    if duration > 0:
        logger.info(f"Crawl Rate: {pages_crawled/duration:.2f} pages/second")
    
    # Results found
    documents = results.get('documents', [])
    media = results.get('media', [])
    press_releases = results.get('press_releases', [])
    total_results = len(documents) + len(media) + len(press_releases)
    
    logger.info("\nResults Found:")
    logger.info(f"  Documents: {len(documents)}")
    logger.info(f"  Media: {len(media)}")
    logger.info(f"  Press Releases: {len(press_releases)}")
    logger.info(f"  Total Results: {total_results}")
    
    # Document types breakdown
    if documents:
        doc_types = {}
        for doc in documents:
            doc_type = doc.get('type', 'UNKNOWN')
            doc_types[doc_type] = doc_types.get(doc_type, 0) + 1
        
        logger.info("\nDocument Types Breakdown:")
        for doc_type, count in sorted(doc_types.items(), key=lambda x: x[1], reverse=True):
            logger.info(f"  {doc_type}: {count}")
    
    # Depth distribution
    if documents:
        depth_dist = {}
        for doc in documents:
            depth = doc.get('metadata', {}).get('depth', 0)
            depth_dist[depth] = depth_dist.get(depth, 0) + 1
        
        logger.info("\nResults by Depth:")
        for depth in sorted(depth_dist.keys()):
            logger.info(f"  Depth {depth}: {depth_dist[depth]} documents")
    
    # Sample documents
    if documents:
        logger.info("\nSample Documents (first 5):")
        for doc in documents[:5]:
            logger.info(f"  - {doc['title']}")
            logger.info(f"    URL: {doc['url']}")
            logger.info(f"    Type: {doc['type']}")
            logger.info(f"    Depth: {doc.get('metadata', {}).get('depth', 'N/A')}")
    
    logger.info("="*80)


async def resume_crawl(state_file: str, config: CrawlerConfig = None) -> dict:
    """Convenience function to resume a crawl from saved state"""
    if not Path(state_file).exists():
        raise FileNotFoundError(f"State file not found: {state_file}")
    
    # Load state to get seed URL and intent
    state = CrawlerState.load(state_file)
    
    # Try to infer seed URL from visited URLs (first one)
    if state.visited_urls:
        seed_url = list(state.visited_urls)[0]
    elif state.queue_items:
        seed_url = state.queue_items[0].url
    else:
        raise ValueError("Cannot determine seed URL from state file")
    
    # Try to get root intent from queue items
    if state.queue_items:
        root_intent = state.queue_items[0].intent
    else:
        root_intent = "Resume crawl"
    
    logger.info(f"Resuming crawl from: {seed_url}")
    
    return await crawl_with_queue(
        seed_url=seed_url,
        root_intent=root_intent,
        config=config,
        resume_state=state_file
    )


# endregion

# region Main Entry Point
# ============================================================================

if __name__ == "__main__":
    # Example configuration
    config = CrawlerConfig(
        max_depth=6,
        max_concurrent_workers=5,
        max_documents=200,
        timeout_seconds=600,
        priority_threshold=0.5,
        terminal_detection_mode="rule",  # Change to "llm" for LLM-based detection
        scraping_mode="exact",  # Change to "guidance" or "ai" for LLM-assisted scraping
        state_file="data/crawler_state.json"
    )
    
    # DOW 30 seed URL
    seed_url = "https://www.cnbc.com/dow-30/"
    
    # Choose crawler mode
    USE_EFFICIENT_IR_CRAWLER = True  # Set to False to use the old complex crawler
    
    logger.info("Starting crawler...")
    start_time = time.time()
    
    if USE_EFFICIENT_IR_CRAWLER:
        # Use the new efficient IR crawler
        logger.info("Using efficient IR extractor crawler (recommended)")
        results = asyncio.run(crawl_dow30_ir_pages(
            seed_url=seed_url,
            config=config,
            debug=True
        ))
        
        # Save results in a different format
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save enriched companies
        companies_file = f"data/dow30_companies_with_ir.json"
        with open(companies_file, 'w') as f:
            json.dump(results.get('companies', []), f, indent=2)
        logger.info(f"Companies with IR pages saved to: {companies_file}")
        
        # Save IR pages list
        ir_pages_file = f"data/dow30_ir_pages.json"
        with open(ir_pages_file, 'w') as f:
            json.dump(results.get('ir_pages', []), f, indent=2)
        logger.info(f"IR pages list saved to: {ir_pages_file}")
        
        # Print summary
        stats = results.get('statistics', {})
        logger.info("="*80)
        logger.info("EFFICIENT IR EXTRACTION SUMMARY")
        logger.info("="*80)
        logger.info(f"Companies Processed: {stats.get('total_companies_processed', 0)}")
        logger.info(f"IR Pages Found: {stats.get('total_ir_pages_found', 0)}")
        success_rate = stats.get('success_rate', 0)
        logger.info(f"Success Rate: {success_rate:.1%}" if isinstance(success_rate, (int, float)) else f"Success Rate: N/A")
        duration = stats.get('total_duration_seconds', 0)
        logger.info(f"Duration: {duration:.2f} seconds" if isinstance(duration, (int, float)) else "Duration: N/A")
        logger.info("="*80)
        
    else:
        # Use the old complex crawler
        logger.info("Using complex multi-page crawler (legacy)")
        root_intent = """Our ultimate goal is to extract financial documents for each company from their 
respective IR(Investor Relations) pages. But we will start to go to such a page only from a seed URL 
in CNBC DOW30 index provide just now. We need to traverse smartly and click on relevant links to get 
to the specific company's IR page. After reaching that IR page, we need to look for any document link 
or presentation, transcript, press release within the IR page. Your role for now is to smartly scrape 
the websites from the Seed URL and provide relevant link directions to go to the next link in order to 
reach our ultimate goal, by leading ourselves to the IR page of each company in the DOW30 index from 
the seed URL (current)."""
        
        results = asyncio.run(crawl_with_queue(
            seed_url=seed_url,
            root_intent=root_intent,
            config=config
        ))
        
        # Save results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_file = f"data/crawl_results.json"
        save_results_to_file(results, results_file)
        
        # Print summary
        print_crawl_summary(results)
    
    end_time = time.time()
    logger.info(f"Total execution time: {end_time - start_time:.2f} seconds")


# endregion

