"""
IR Document Extractor & Downloader
Extracts investor relations documents from company websites and downloads them
"""

import re
import random
import time
import requests
from datetime import datetime
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, asdict
from urllib.parse import urljoin, urlparse, unquote, urlunparse
import json
import os
from pathlib import Path
from collections import defaultdict
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from bs4 import BeautifulSoup
import logging

from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class DocumentInfo:
    """Structure to hold document information"""
    title: str
    url: str
    normalized_url: str
    document_type: str
    publication_date: Optional[str]
    file_extension: str
    relevance_score: float
    section: str
    subsection: str
    content_preview: str
    metadata: Dict[str, Any]
    is_direct_file: bool = False
    extracted_year: Optional[int] = None
    extracted_quarter: Optional[int] = None


# ============================================================================
# DOCUMENT EXTRACTOR
# ============================================================================

class IRDocumentExtractor:
    """Extract IR documents from company investor relations pages"""
    
    def __init__(self, debug: bool = True, max_sections: int = 20, 
                 request_delay: Tuple[float, float] = (2, 4), max_retries: int = 2):
        self.debug = debug
        self.max_sections = max_sections
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.file_extensions = ['.pdf', '.xlsx', '.xls', '.pptx', '.ppt', 
                                '.docx', '.doc', '.csv', '.mp3', '.mp4']
        self.seen_urls: Set[str] = set()
        self.driver = None
        self.current_year = datetime.now().year
    
    def _setup_selenium(self):
        """Setup Selenium WebDriver"""
        if self.debug:
            logger.info("Setting up ChromeDriver...")
        
        try:
            chrome_options = Options()
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.page_load_strategy = 'normal'
            
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.set_page_load_timeout(45)
            
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
            })
            
            if self.debug:
                logger.info("ChromeDriver ready")
            
            return driver
        except Exception as e:
            logger.error(f"Failed to setup ChromeDriver: {e}")
            raise
    
    def _normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication"""
        try:
            parsed = urlparse(url)
            normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, 
                                    parsed.params, '', ''))
            return normalized
        except Exception:
            return url
    
    def _is_valid_ir_url(self, url: str) -> bool:
        """Validate that URL is actually an IR page"""
        url_lower = url.lower()
        
        invalid_patterns = ['smartphone', 'iphone', 'product', 'shop', '/careers']
        if any(bad in url_lower for bad in invalid_patterns):
            return False
        
        valid_patterns = ['investor', 'ir.', 'shareholders', '/investor']
        return any(good in url_lower for good in valid_patterns)
    
    def _is_direct_file(self, url: str) -> bool:
        """Check if URL is direct downloadable file"""
        url_lower = url.lower()
        
        if any(ext in url_lower for ext in self.file_extensions):
            return True
        
        cdn_patterns = [
            r'\.q4cdn\.com/.*/files/', r'\.q4cdn\.com/.*/doc_',
            r'\.cloudfront\.net/', r'/static-files/[a-f0-9\-]{30,}',
            r'/files/doc_', r'/_assets/.*/', r'/doc_financials/',
            r'/doc_downloads/', r'/doc_earnings/'
        ]
        
        return any(re.search(pattern, url_lower) for pattern in cdn_patterns)
    
    def _wait_for_dynamic_content(self, url: str):
        """Wait for dynamic content to load"""
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            time.sleep(4)
            
            try:
                WebDriverWait(self.driver, 8).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='pdf'], a[href*='10-k'], a[href*='10-q']"))
                )
            except TimeoutException:
                try:
                    WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='financial'], a[href*='report']"))
                    )
                except TimeoutException:
                    WebDriverWait(self.driver, 5).until(
                        lambda d: len(d.find_elements(By.TAG_NAME, "a")) > 20
                    )
            
            time.sleep(2)
            
        except TimeoutException:
            logger.warning(f"Timeout waiting for dynamic content")
    
    def extract_documents(self, ir_url: str, ticker: str, 
                         company_name: str) -> List[DocumentInfo]:
        """Extract all documents with retry logic and global deduplication"""
        
        if not self._is_valid_ir_url(ir_url):
            logger.error(f"❌ {ticker}: Invalid IR URL: {ir_url}")
            return []
        
        if self.debug:
            logger.info(f"\n[{ticker}] {company_name}")
            logger.info(f"URL: {ir_url}")
        
        all_raw_documents = []
        
        for attempt in range(self.max_retries):
            try:
                if not self.driver:
                    self.driver = self._setup_selenium()
                
                self.driver.get(ir_url)
                self._wait_for_dynamic_content(ir_url)
                
                sections = self._discover_sections(ir_url)
                
                if self.debug:
                    logger.info(f"Found {len(sections)} main sections")
                
                for section_name, section_url in sections:
                    if self.debug:
                        logger.info(f"\nExtracting from section: {section_name}")
                    
                    try:
                        self.driver.get(section_url)
                        self._wait_for_dynamic_content(section_url)
                        time.sleep(random.uniform(*self.request_delay))
                        
                        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
                        section_docs = self._extract_all_files_from_page(
                            soup, section_url, section_name
                        )
                        
                        if self.debug:
                            logger.info(f"  Found {len(section_docs)} documents")
                        
                        all_raw_documents.extend(section_docs)
                    
                    except TimeoutException:
                        logger.warning(f"Timeout loading section: {section_name}")
                        continue
                    except Exception as e:
                        logger.error(f"Error processing section {section_name}: {e}")
                        continue
                
                if len(all_raw_documents) == 0:
                    if attempt < self.max_retries - 1:
                        logger.warning(f"No documents found for {ticker}, retry {attempt + 1}/{self.max_retries}")
                        time.sleep(5)
                        continue
                    else:
                        logger.error(f"❌ {ticker}: Failed to extract any documents after {self.max_retries} attempts")
                        return []
                
                break
                
            except Exception as e:
                if attempt < self.max_retries - 1:
                    logger.warning(f"Error for {ticker}, retrying: {e}")
                    time.sleep(5)
                else:
                    logger.error(f"❌ {ticker}: Failed after {self.max_retries} attempts: {e}")
                    return []
        
        all_raw_documents = [doc for doc in all_raw_documents if doc.is_direct_file]
        
        if self.debug:
            logger.info(f"\nTotal raw documents extracted: {len(all_raw_documents)}")
        
        deduplicated_documents = self._global_deduplicate_improved(all_raw_documents)
        
        latest_quarterly = self.identify_latest_quarterly_report(deduplicated_documents)
        if latest_quarterly and self.debug:
            q_info = f"Q{latest_quarterly.extracted_quarter}" if latest_quarterly.extracted_quarter else ""
            logger.info(f"📊 LATEST QUARTERLY: {latest_quarterly.document_type} - "
                       f"{latest_quarterly.title[:50]}... ({latest_quarterly.extracted_year} {q_info})")
        
        if self.debug:
            logger.info(f"After global deduplication: {len(deduplicated_documents)} unique latest documents")
            logger.info("\nFinal document list:")
            for doc in deduplicated_documents:
                date_info = f"({doc.extracted_year or '?'}"
                if doc.extracted_quarter:
                    date_info += f" Q{doc.extracted_quarter}"
                date_info += ")"
                logger.info(f"  • {doc.document_type}: {doc.title[:50]}... {date_info}")
        
        return deduplicated_documents
    
    def _global_deduplicate_improved(self, documents: List[DocumentInfo]) -> List[DocumentInfo]:
        """Prefer documents WITH years over documents WITHOUT years"""
        by_type = defaultdict(list)
        for doc in documents:
            by_type[doc.document_type].append(doc)
        
        latest_documents = []
        
        for doc_type, docs in by_type.items():
            docs_with_year = [d for d in docs if d.extracted_year is not None]
            docs_without_year = [d for d in docs if d.extracted_year is None]
            
            if docs_with_year:
                sorted_docs = sorted(
                    docs_with_year,
                    key=lambda d: (
                        d.extracted_year,
                        d.extracted_quarter if d.extracted_quarter is not None else -1,
                        d.relevance_score
                    ),
                    reverse=True
                )
                latest = sorted_docs[0]
                
                if latest.extracted_year < self.current_year - 1:
                    logger.error(f"🚨 {doc_type}: KEEPING OLD DOCUMENT from {latest.extracted_year} (should be {self.current_year} or {self.current_year-1})")
            else:
                if docs_without_year:
                    latest = docs_without_year[0]
                    logger.warning(f"⚠️ {doc_type}: No documents with extractable years, using first available")
                else:
                    continue
            
            latest_documents.append(latest)
            
            if self.debug and len(docs) > 1:
                logger.info(f"\n  📊 {doc_type}: Found {len(docs)} documents, keeping latest")
                logger.info(f"     ✅ KEPT: {latest.title[:40]} ({latest.extracted_year or 'NO YEAR'})")
                for discarded in sorted_docs[1:3] if docs_with_year else docs_without_year[1:3]:
                    logger.info(f"     ❌ Discarded: {discarded.title[:40]} ({discarded.extracted_year or 'NO YEAR'})")
        
        return latest_documents
    
    def identify_latest_quarterly_report(self, documents: List[DocumentInfo]) -> Optional[DocumentInfo]:
        """Identify THE single latest quarterly earnings report"""
        if not documents:
            return None
        
        quarterly_types = [
            'Press Release',
            '10-Q Quarterly Report',
            'Presentation',
            'CFO Commentary',
            'Transcript',
            'Excel Data'
        ]
        
        quarterly_docs = [d for d in documents 
                         if d.document_type in quarterly_types and d.extracted_quarter]
        
        if not quarterly_docs:
            quarterly_docs = [d for d in documents if d.document_type in quarterly_types]
        
        if not quarterly_docs:
            return None
        
        type_priority = {
            'Press Release': 1,
            '10-Q Quarterly Report': 2,
            'Presentation': 3,
            'CFO Commentary': 4,
            'Transcript': 5,
            'Excel Data': 6
        }
        
        sorted_docs = sorted(
            quarterly_docs,
            key=lambda d: (
                d.extracted_year if d.extracted_year else 0,
                d.extracted_quarter if d.extracted_quarter else 0,
                -type_priority.get(d.document_type, 99),
                d.relevance_score
            ),
            reverse=True
        )
        
        return sorted_docs[0]
    
    def _discover_sections(self, base_url: str) -> List[Tuple[str, str]]:
        """Discover sections with priority scoring"""
        sections = []
        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
        all_links = soup.find_all('a', href=True)
        
        priority_keywords = ['sec filing', '10-k', '10-q', 'financial', 'annual report', 
                            'quarterly', 'earnings', 'investor']
        
        for link in all_links:
            try:
                text = link.get_text(strip=True)
                href = link.get('href', '')
                
                if not href or href.startswith('#') or href.startswith('javascript:'):
                    continue
                
                if not text or len(text) < 3:
                    continue
                
                full_url = urljoin(base_url, href)
                
                if urlparse(full_url).netloc != urlparse(base_url).netloc:
                    continue
                
                if self._is_likely_section_link(text, href, link):
                    priority = sum(1 for kw in priority_keywords 
                                  if kw in text.lower() or kw in href.lower())
                    sections.append((text, full_url, priority))
            except Exception:
                continue
        
        sections.sort(key=lambda x: x[2], reverse=True)
        
        unique_sections = {}
        for name, url, priority in sections:
            normalized = self._normalize_url(url)
            if normalized not in unique_sections and normalized != self._normalize_url(base_url):
                unique_sections[normalized] = (name, url)
        
        result = list(unique_sections.values())[:self.max_sections]
        
        if self.debug and result:
            logger.info("Discovered sections (priority order):")
            for name, _ in result[:10]:
                logger.info(f"  • {name}")
        
        return result
    
    def _is_likely_section_link(self, text: str, href: str, link) -> bool:
        """Determine if link is likely a main section"""
        text_lower = text.lower()
        href_lower = href.lower()
        
        word_count = len(text.split())
        if word_count < 1 or word_count > 6:
            return False
        
        path_depth = len([p for p in urlparse(href_lower).path.split('/') if p])
        if path_depth > 3:
            return False
        
        financial_indicators = [
            'financial', 'investor', 'earning', 'report', 'filing', 'sec',
            'presentation', 'event', 'news', 'press', 'annual', 'quarterly',
            'result', 'document', 'library', 'information'
        ]
        
        has_financial_term = any(term in text_lower or term in href_lower 
                                 for term in financial_indicators)
        
        if not has_financial_term:
            return False
        
        exclude_terms = [
            'contact', 'email alert', 'subscribe', 'rss', 'faq', 'help',
            'cookie', 'privacy', 'terms', 'accessibility', 'sitemap'
        ]
        
        if any(term in text_lower for term in exclude_terms):
            return False
        
        return True
    
    def _extract_all_files_from_page(self, soup: BeautifulSoup, 
                                     base_url: str, section_name: str) -> List[DocumentInfo]:
        """Extract all file links from a page"""
        documents = []
        all_links = soup.find_all('a', href=True)
        
        for link in all_links:
            try:
                href = link.get('href', '')
                if not href or href.startswith('#') or href.startswith('javascript:'):
                    continue
                
                full_url = urljoin(base_url, href)
                normalized_url = self._normalize_url(full_url)
                
                if normalized_url in self.seen_urls:
                    continue
                
                if not self._is_direct_file(full_url):
                    continue
                
                self.seen_urls.add(normalized_url)
                
                text = link.get_text(strip=True)
                title = link.get('title', '')
                
                if not text or len(text) < 5:
                    text = self._extract_filename_from_url(full_url)
                
                doc_type = self._classify_document(full_url, text, title)
                pub_date = self._extract_date(text + ' ' + full_url + ' ' + title)
                year = self._extract_year_improved(text + ' ' + full_url + ' ' + title)
                quarter = self._extract_quarter(text + ' ' + full_url + ' ' + title)
                
                doc = DocumentInfo(
                    title=text[:200],
                    url=full_url,
                    normalized_url=normalized_url,
                    document_type=doc_type,
                    publication_date=pub_date,
                    file_extension=self._get_extension(full_url),
                    relevance_score=100.0,
                    section=section_name,
                    subsection=doc_type,
                    content_preview=text[:150],
                    metadata={'ticker': '', 'company': ''},
                    is_direct_file=True,
                    extracted_year=year,
                    extracted_quarter=quarter
                )
                
                documents.append(doc)
            except Exception:
                continue
        
        return documents
    
    def _classify_document(self, url: str, text: str, title: str) -> str:
        """Classify document type"""
        combined = f"{url} {text} {title}".lower()
        
        classifications = [
            ('10-K Annual Report', ['10-k', 'form 10-k', 'form10-k', '10k annual']),
            ('10-Q Quarterly Report', ['10-q', 'form 10-q', 'form10-q', '10q quarter']),
            ('8-K Current Report', ['8-k', 'form 8-k', 'form8-k']),
            ('Proxy Statement', ['proxy statement', 'proxy', 'def 14a', 'def14a']),
            ('Press Release', ['press release', 'earnings release']),
            ('CFO Commentary', ['cfo commentary', 'cfo comment']),
            ('Audio Webcast', ['webcast', 'audio', '.mp3', 'call.mp3']),
            ('Transcript', ['transcript', 'call transcript', 'earnings call']),
            ('Presentation', ['presentation', 'slides', 'deck', 'investor deck']),
            ('Revenue Trend', ['revenue trend', 'quarterly revenue']),
            ('Supplemental Data', ['supplemental', 'data table', 'supplemental data']),
            ('Annual Report', ['annual report']),
            ('Excel Data', ['.xlsx', '.xls']),
        ]
        
        for doc_type, keywords in classifications:
            if any(keyword in combined for keyword in keywords):
                return doc_type
        
        return 'PDF Document' if '.pdf' in url else 'Financial Document'
    
    def _extract_filename_from_url(self, url: str) -> str:
        """Extract clean filename from URL"""
        try:
            parsed = urlparse(url)
            filename = os.path.basename(unquote(parsed.path))
            filename = re.sub(r'[-_]', ' ', filename)
            filename = re.sub(r'\.(pdf|xlsx?|pptx?|docx?)$', '', filename, flags=re.I)
            filename = re.sub(r'\b[a-f0-9]{32,}\b', '', filename, flags=re.I)
            filename = re.sub(r'\s+', ' ', filename).strip()
            return filename or 'Financial Document'
        except Exception:
            return 'Financial Document'
    
    def _extract_date(self, text: str) -> Optional[str]:
        """Extract date from text"""
        patterns = [
            r'Q([1-4])\s*20([12]\d)',
            r'20([12]\d)\s*Q([1-4])',
            r'FY\s*(\d{2})',
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+20[12]\d',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(0)
        return None
    
    def _is_valid_year_context(self, year: int, context: str) -> bool:
        """Check if year is in a hex string context"""
        if not (2015 <= year <= self.current_year):
            return False
        
        hex_chars = len(re.findall(r'[a-f]', context.lower()))
        total_chars = len(context)
        
        if total_chars > 0 and (hex_chars / total_chars) > 0.4:
            return False
        
        return True
    
    def _extract_year_improved(self, text: str) -> Optional[int]:
        """Extract year with hex string detection"""
        text_clean = re.sub(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', '', text, flags=re.I)
        text_clean = re.sub(r'\b[a-f0-9]{8,}\b', '', text_clean, flags=re.I)
        
        year_match = re.search(r'20([12]\d)', text_clean)
        if year_match:
            year = int(year_match.group(0))
            context = text_clean[max(0, year_match.start()-10):year_match.end()+10]
            if self._is_valid_year_context(year, context):
                return year
        
        fy_match = re.search(r'fy\s*(\d{2,4})', text_clean, re.I)
        if fy_match:
            fy_year = fy_match.group(1)
            if len(fy_year) == 2:
                year = int(fy_year)
                full_year = 2000 + year
                if 2020 <= full_year <= self.current_year + 2:
                    return full_year
            elif len(fy_year) == 4:
                year = int(fy_year)
                if 2020 <= year <= self.current_year + 2:
                    return year
        
        return None
    
    def _extract_quarter(self, text: str) -> Optional[int]:
        """Extract quarter from text"""
        quarter_match = re.search(r'[q\-]([1-4])(?:\s|q|$)', text, re.I)
        return int(quarter_match.group(1)) if quarter_match else None
    
    def _get_extension(self, url: str) -> str:
        """Get file extension"""
        url_lower = url.lower()
        for ext in self.file_extensions:
            if ext in url_lower:
                return ext
        return '.unknown'
    
    def close(self):
        """Close WebDriver"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None


# ============================================================================
# DOCUMENT DOWNLOADER
# ============================================================================

class DocumentDownloader:
    """Download and organize documents in company-wise folders"""
    
    def __init__(self, base_output_dir: str = "data/reports", 
                 request_delay: tuple = (1, 3),
                 timeout: int = 60,
                 max_retries: int = 3):
        self.base_output_dir = Path(base_output_dir)
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_retries = max_retries
        
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        
        self.stats = {
            'total_companies': 0,
            'total_documents': 0,
            'successful_downloads': 0,
            'failed_downloads': 0,
            'skipped_existing': 0,
            'total_size_mb': 0
        }
    
    def sanitize_filename(self, filename: str, max_length: int = 150) -> str:
        """Clean filename for safe file system storage"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        
        filename = filename.strip('. ')
        filename = ' '.join(filename.split())
        
        if len(filename) > max_length:
            name, ext = os.path.splitext(filename)
            filename = name[:max_length - len(ext)] + ext
        
        return filename or 'document'
    
    def sanitize_company_name(self, company_name: str) -> str:
        """Clean company name for folder creation"""
        name = company_name.replace('/', '_').replace('\\', '_')
        name = ''.join(c for c in name if c.isalnum() or c in ' .-_&')
        name = ' '.join(name.split())
        return name.strip() or 'Unknown_Company'
    
    def get_file_extension(self, url: str, content_type: str = None) -> str:
        """Determine file extension from URL or content type"""
        parsed = urlparse(url)
        path = unquote(parsed.path)
        _, ext = os.path.splitext(path)
        
        if ext and len(ext) <= 6:
            return ext.lower()
        
        if content_type:
            content_type_map = {
                'application/pdf': '.pdf',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
                'application/vnd.ms-excel': '.xls',
                'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
                'application/vnd.ms-powerpoint': '.ppt',
                'audio/mpeg': '.mp3',
                'video/mp4': '.mp4',
                'text/csv': '.csv'
            }
            return content_type_map.get(content_type.split(';')[0].strip(), '.bin')
        
        return '.bin'
    
    def generate_filename(self, doc: Dict[str, Any], url: str) -> str:
        """Generate meaningful filename from document metadata"""
        doc_type = doc.get('document_type', 'Document')
        title = doc.get('title', '')
        year = doc.get('extracted_year')
        quarter = doc.get('extracted_quarter')
        
        parts = [doc_type]
        
        if year:
            if quarter:
                parts.append(f"{year}_Q{quarter}")
            else:
                parts.append(str(year))
        
        if title and len(title) > 10:
            title_clean = self.sanitize_filename(title[:50])
            if title_clean not in doc_type:
                parts.append(title_clean)
        
        filename = ' - '.join(parts)
        
        ext = doc.get('file_extension', self.get_file_extension(url))
        if not filename.endswith(ext):
            filename += ext
        
        return self.sanitize_filename(filename)
    
    def download_file(self, url: str, output_path: Path) -> bool:
        """Download single file with retry logic"""
        for attempt in range(self.max_retries):
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Referer': url
                }
                
                response = requests.get(url, headers=headers, timeout=self.timeout, stream=True)
                response.raise_for_status()
                
                total_size = int(response.headers.get('content-length', 0))
                
                with open(output_path, 'wb') as f:
                    if total_size == 0:
                        f.write(response.content)
                    else:
                        downloaded = 0
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                
                file_size_mb = output_path.stat().st_size / (1024 * 1024)
                self.stats['total_size_mb'] += file_size_mb
                
                logger.info(f"✅ Downloaded: {output_path.name} ({file_size_mb:.2f} MB)")
                return True
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"⚠️ Attempt {attempt + 1}/{self.max_retries} failed: {str(e)[:100]}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"❌ Failed to download: {url}")
                    return False
            except Exception as e:
                logger.error(f"❌ Unexpected error: {e}")
                return False
        
        return False
    
    def download_company_documents(self, ticker: str, company_name: str, 
                                   documents: List[Dict[str, Any]], 
                                   skip_existing: bool = True) -> Dict[str, Any]:
        """Download all documents for a single company"""
        if not documents:
            logger.info(f"⭐ {ticker}: No documents to download")
            return {'downloaded': 0, 'failed': 0, 'skipped': 0}
        
        company_folder_name = f"{ticker} - {self.sanitize_company_name(company_name)}"
        company_dir = self.base_output_dir / company_folder_name
        company_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"\n{'='*80}")
        logger.info(f"📁 {ticker}: {company_name}")
        logger.info(f"   Folder: {company_dir}")
        logger.info(f"   Documents to download: {len(documents)}")
        logger.info(f"{'='*80}")
        
        company_stats = {'downloaded': 0, 'failed': 0, 'skipped': 0}
        
        for i, doc in enumerate(documents, 1):
            try:
                url = doc.get('url')
                if not url:
                    logger.warning(f"⚠️ [{i}/{len(documents)}] No URL found")
                    company_stats['failed'] += 1
                    continue
                
                filename = self.generate_filename(doc, url)
                output_path = company_dir / filename
                
                if skip_existing and output_path.exists():
                    logger.info(f"⭐ [{i}/{len(documents)}] Already exists: {filename}")
                    company_stats['skipped'] += 1
                    self.stats['skipped_existing'] += 1
                    continue
                
                logger.info(f"⬇️ [{i}/{len(documents)}] Downloading: {filename}")
                success = self.download_file(url, output_path)
                
                if success:
                    company_stats['downloaded'] += 1
                    self.stats['successful_downloads'] += 1
                else:
                    company_stats['failed'] += 1
                    self.stats['failed_downloads'] += 1
                
                if i < len(documents):
                    delay = random.uniform(*self.request_delay)
                    time.sleep(delay)
                
            except Exception as e:
                logger.error(f"❌ Error processing document: {e}")
                company_stats['failed'] += 1
                self.stats['failed_downloads'] += 1
        
        metadata_path = company_dir / "_metadata.json"
        with open(metadata_path, 'w') as f:
            json.dump({
                'ticker': ticker,
                'company_name': company_name,
                'total_documents': len(documents),
                'documents': documents,
                'download_stats': company_stats,
                'downloaded_at': time.strftime('%Y-%m-%d %H:%M:%S')
            }, f, indent=2)
        
        logger.info(f"\n✅ {ticker} Complete: {company_stats['downloaded']} downloaded, "
                   f"{company_stats['failed']} failed, {company_stats['skipped']} skipped")
        
        return company_stats
    
    def download_all_companies(self, input_json_path: str, 
                              companies_to_process: List[str] = None,
                              skip_existing: bool = True) -> None:
        """Download documents for all companies from JSON file"""
        logger.info("\n" + "="*80)
        logger.info("📥 DOCUMENT DOWNLOADER - Company-wise folder structure")
        logger.info(f"📂 Output directory: {self.base_output_dir.absolute()}")
        logger.info("="*80)
        
        with open(input_json_path, 'r') as f:
            data = json.load(f)
        
        if 'all_documents' in data:
            all_documents = data['all_documents']
        else:
            all_documents = data
        
        if companies_to_process:
            all_documents = {k: v for k, v in all_documents.items() 
                           if k in companies_to_process}
        
        self.stats['total_companies'] = len(all_documents)
        self.stats['total_documents'] = sum(len(docs) for docs in all_documents.values())
        
        logger.info(f"\n📊 Processing {self.stats['total_companies']} companies")
        logger.info(f"📊 Total documents: {self.stats['total_documents']}")
        
        for company_num, (ticker, documents) in enumerate(all_documents.items(), 1):
            logger.info(f"\n[{company_num}/{self.stats['total_companies']}] Processing {ticker}...")
            
            company_name = 'Unknown Company'
            if documents and len(documents) > 0:
                company_name = documents[0].get('metadata', {}).get('company', company_name)
            
            self.download_company_documents(ticker, company_name, documents, skip_existing)
        
        self.print_summary()
    
    def print_summary(self) -> None:
        """Print download summary statistics"""
        logger.info("\n" + "="*80)
        logger.info("📊 DOWNLOAD SUMMARY")
        logger.info("="*80)
        logger.info(f"✅ Total companies processed: {self.stats['total_companies']}")
        logger.info(f"✅ Total documents: {self.stats['total_documents']}")
        logger.info(f"✅ Successfully downloaded: {self.stats['successful_downloads']}")
        logger.info(f"⭐ Skipped (already exist): {self.stats['skipped_existing']}")
        logger.info(f"❌ Failed downloads: {self.stats['failed_downloads']}")
        logger.info(f"💾 Total size: {self.stats['total_size_mb']:.2f} MB")
        logger.info(f"📂 Output directory: {self.base_output_dir.absolute()}")
        logger.info("="*80)
        
        if self.stats['successful_downloads'] > 0:
            success_rate = (self.stats['successful_downloads'] / 
                          (self.stats['successful_downloads'] + self.stats['failed_downloads'])) * 100
            logger.info(f"✅ Success rate: {success_rate:.1f}%")


# ============================================================================
# MAIN EXECUTION FUNCTIONS
# ============================================================================

def extract_all_companies(input_json_path: str, output_json_path: str = None,
                         start_index: int = 0, end_index: int = None,
                         debug: bool = True) -> Dict[str, Any]:
    """
    Extract documents for all companies
    
    Supports two JSON formats:
    1. Simple format: [{"ticker": "AAPL", "company_name": "Apple", "investor_relations_url": "..."}]
    2. Dow30 format: [{"url": "...", "metadata": {"ticker": "AAPL", "company_name": "Apple"}}]
    """
    print("\n" + "="*80)
    print("IR DOCUMENT EXTRACTION - ALL FIXES APPLIED")
    print("✅ JS redirects • Dynamic content • Fixed year extraction • Latest quarterly ID")
    print("="*80)
    
    with open(input_json_path, 'r') as f:
        companies = json.load(f)
    
    if end_index is not None:
        companies = companies[start_index:end_index]
    else:
        companies = companies[start_index:]
    
    print(f"Processing {len(companies)} companies (index {start_index} to {start_index + len(companies)})")
    
    extractor = IRDocumentExtractor(debug=debug)
    all_documents = {}
    latest_quarterly_reports = {}
    
    try:
        for i, company in enumerate(companies, 1):
            # Handle both old and new JSON formats
            if 'metadata' in company:
                # New format from dow30_ir_pages.json
                ticker = company.get('metadata', {}).get('ticker', 'UNKNOWN')
                company_name = company.get('metadata', {}).get('company_name', 'Unknown Company')
                ir_url = company.get('url', '')
            else:
                # Old format
                ticker = company.get('ticker', 'UNKNOWN')
                company_name = company.get('company_name', 'Unknown Company')
                ir_url = company.get('investor_relations_url', '')
            
            if not ir_url:
                logger.warning(f"No IR URL for {ticker}, skipping...")
                all_documents[ticker] = []
                continue
            
            print(f"\n[{i}/{len(companies)}] {ticker}")
            
            try:
                documents = extractor.extract_documents(ir_url, ticker, company_name)
                
                for doc in documents:
                    doc.metadata['ticker'] = ticker
                    doc.metadata['company'] = company_name
                
                all_documents[ticker] = documents
                
                latest_q = extractor.identify_latest_quarterly_report(documents)
                if latest_q:
                    latest_quarterly_reports[ticker] = latest_q
                
                time.sleep(random.uniform(2, 4))
                
            except Exception as e:
                logger.error(f"Error processing {ticker}: {e}")
                all_documents[ticker] = []
                continue
    
    finally:
        extractor.close()
    
    total = sum(len(docs) for docs in all_documents.values())
    with_docs = sum(1 for docs in all_documents.values() if docs)
    total_pdfs = sum(sum(1 for d in docs if '.pdf' in d.file_extension) 
                     for docs in all_documents.values())
    total_excel = sum(sum(1 for d in docs if '.xls' in d.file_extension) 
                      for docs in all_documents.values())
    old_docs = sum(sum(1 for d in docs if d.extracted_year and d.extracted_year < datetime.now().year - 1) 
                   for docs in all_documents.values())
    
    print("\n" + "="*80)
    print("EXTRACTION SUMMARY")
    print("="*80)
    print(f"✅ Total unique documents: {total}")
    print(f"✅ PDF files: {total_pdfs}")
    print(f"✅ Excel files: {total_excel}")
    print(f"✅ Companies with documents: {with_docs}/{len(companies)}")
    print(f"📊 Latest quarterly reports found: {len(latest_quarterly_reports)}")
    print(f"⚠️ Companies with NO documents: {len(companies) - with_docs}")
    print(f"🚨 Documents with old years (<2024): {old_docs}")
    if companies:
        print(f"✅ Average per company: {total/len(companies):.1f}")
    
    if output_json_path:
        export_data = {
            'all_documents': {},
            'latest_quarterly_reports': {}
        }
        
        for ticker, documents in all_documents.items():
            export_data['all_documents'][ticker] = [asdict(doc) for doc in documents]
        
        for ticker, doc in latest_quarterly_reports.items():
            export_data['latest_quarterly_reports'][ticker] = asdict(doc)
        
        with open(output_json_path, 'w') as f:
            json.dump(export_data, f, indent=2, default=str)
        
        print(f"\n✅ Exported to {output_json_path}")
        print(f"   - All documents: {total}")
        print(f"   - Latest quarterly: {len(latest_quarterly_reports)}")
    
    return {
        'all_documents': all_documents,
        'latest_quarterly_reports': latest_quarterly_reports
    }


def extract_in_batches(input_json_path: str, output_dir: str = "output",
                      batch_size: int = 10, debug: bool = True):
    """Extract documents in batches"""
    with open(input_json_path, 'r') as f:
        companies = json.load(f)
    
    total_companies = len(companies)
    num_batches = (total_companies + batch_size - 1) // batch_size
    
    print(f"\n{'='*80}")
    print(f"BATCH EXTRACTION: {total_companies} companies in {num_batches} batches")
    print(f"{'='*80}")
    
    os.makedirs(output_dir, exist_ok=True)
    all_results = {'all_documents': {}, 'latest_quarterly_reports': {}}
    
    for batch_num in range(num_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, total_companies)
        
        print(f"\n{'='*80}")
        print(f"BATCH {batch_num + 1}/{num_batches}: Companies {start_idx} to {end_idx}")
        print(f"{'='*80}")
        
        batch_output = os.path.join(output_dir, f"batch_{batch_num + 1:03d}.json")
        
        batch_results = extract_all_companies(
            input_json_path,
            output_json_path=batch_output,
            start_index=start_idx,
            end_index=end_idx,
            debug=debug
        )
        
        all_results['all_documents'].update(batch_results['all_documents'])
        all_results['latest_quarterly_reports'].update(batch_results['latest_quarterly_reports'])
        
        print(f"\n✅ Batch {batch_num + 1} complete. Saved to {batch_output}")
    
    combined_output = os.path.join(output_dir, "all_companies_combined.json")
    
    export_data = {
        'all_documents': {},
        'latest_quarterly_reports': {}
    }
    
    for ticker, documents in all_results['all_documents'].items():
        export_data['all_documents'][ticker] = [asdict(doc) for doc in documents]
    
    for ticker, doc in all_results['latest_quarterly_reports'].items():
        export_data['latest_quarterly_reports'][ticker] = asdict(doc)
    
    with open(combined_output, 'w') as f:
        json.dump(export_data, f, indent=2, default=str)
    
    print(f"\n{'='*80}")
    print(f"✅ ALL BATCHES COMPLETE")
    print(f"✅ Combined results saved to {combined_output}")
    print(f"{'='*80}")
    
    return all_results


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

if __name__ == "__main__":
    
    # ============================================================================
    # QUICK START: Extract and download for 3 companies
    # ============================================================================
    
    print("\n" + "="*80)
    print("🚀 QUICK START: Extracting and downloading for 3 companies")
    print("="*80)
    
    # Step 1: Extract document metadata for first 3 companies
    print("\n📋 Step 1: Extracting document metadata...")
    extraction_results = extract_all_companies(
        input_json_path='../data/dow30_ir_pages.json',
        output_json_path='../data/documents/all_companies.json',
        start_index=0,
        end_index=1,  # First 3 companies
        debug=True
    )
    
    # Step 2: Download all extracted files
    print("\n💾 Step 2: Downloading all files...")
    downloader = DocumentDownloader(
        base_output_dir="../data/reports",
        request_delay=(1, 2),  # Faster for testing
        timeout=60,
        max_retries=3
    )
    
    downloader.download_all_companies(
        input_json_path='../data/documents/all_companies.json',
        skip_existing=True
    )
    
    print("\n" + "="*80)
    print("✅ COMPLETE! Check '../data/reports/' for downloaded files")
    print("="*80)
    
    # ============================================================================
    # OTHER EXAMPLES (commented out)
    # ============================================================================
    
    # EXAMPLE 1: Extract documents from all companies
    # ------------------------------------------------
    """
    all_results = extract_all_companies(
        input_json_path='cnbc_companies_with_ir.json',
        output_json_path='all_documents.json',
        debug=True
    )
    """
    
    # EXAMPLE 2: Extract in batches (for large datasets)
    # ------------------------------------------------
    """
    batch_results = extract_in_batches(
        input_json_path='cnbc_companies_with_ir.json',
        output_dir='output_batches',
        batch_size=20,
        debug=True
    )
    """
    
    # EXAMPLE 3: Download only specific companies
    # ------------------------------------------------
    """
    downloader = DocumentDownloader(base_output_dir="data/reports")
    downloader.download_all_companies(
        input_json_path='all_documents.json',
        companies_to_process=['AAPL', 'MSFT', 'GOOGL'],
        skip_existing=True
    )
    """