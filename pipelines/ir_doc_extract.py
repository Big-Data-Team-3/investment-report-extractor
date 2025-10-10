# ============================================================================
# COMPLETE IR DOCUMENT EXTRACTION & VALIDATION PIPELINE
# Run this script end-to-end for full extraction and validation
# ============================================================================

import re
import random
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, asdict
from urllib.parse import urljoin, urlparse, unquote, urlunparse
import json
import os
from collections import defaultdict
from pathlib import Path
import requests
import logging

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================================================
# PART 1: DOCUMENT EXTRACTOR
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


class IRDocumentExtractor:
    """Extract IR documents with all fixes applied"""
    
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
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, '', ''))
        except Exception:
            return url
    
    def _is_valid_ir_url(self, url: str) -> bool:
        """Validate IR URL"""
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
    
    def extract_documents(self, ir_url: str, ticker: str, company_name: str) -> List[DocumentInfo]:
        """Extract all documents"""
        if not self._is_valid_ir_url(ir_url):
            logger.error(f"❌ {ticker}: Invalid IR URL: {ir_url}")
            return []
        
        if self.debug:
            logger.info(f"\n[{ticker}] {company_name}")
        
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
                    try:
                        self.driver.get(section_url)
                        self._wait_for_dynamic_content(section_url)
                        time.sleep(random.uniform(*self.request_delay))
                        
                        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
                        section_docs = self._extract_all_files_from_page(soup, section_url, section_name)
                        all_raw_documents.extend(section_docs)
                    except Exception:
                        continue
                
                if len(all_raw_documents) == 0:
                    if attempt < self.max_retries - 1:
                        time.sleep(5)
                        continue
                    else:
                        logger.error(f"❌ {ticker}: Failed after {self.max_retries} attempts")
                        return []
                break
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(5)
                else:
                    logger.error(f"❌ {ticker}: Failed: {e}")
                    return []
        
        all_raw_documents = [doc for doc in all_raw_documents if doc.is_direct_file]
        deduplicated = self._global_deduplicate_improved(all_raw_documents)
        
        latest_quarterly = self.identify_latest_quarterly_report(deduplicated)
        if latest_quarterly and self.debug:
            q_info = f"Q{latest_quarterly.extracted_quarter}" if latest_quarterly.extracted_quarter else ""
            logger.info(f"📊 LATEST QUARTERLY: {latest_quarterly.document_type} ({latest_quarterly.extracted_year} {q_info})")
        
        return deduplicated
    
    def _global_deduplicate_improved(self, documents: List[DocumentInfo]) -> List[DocumentInfo]:
        """Deduplicate documents, keeping latest per type"""
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
                    key=lambda d: (d.extracted_year, d.extracted_quarter if d.extracted_quarter else -1, d.relevance_score),
                    reverse=True
                )
                latest = sorted_docs[0]
                if latest.extracted_year < self.current_year - 1:
                    logger.error(f"🚨 {doc_type}: OLD from {latest.extracted_year}")
            else:
                if docs_without_year:
                    latest = docs_without_year[0]
                else:
                    continue
            latest_documents.append(latest)
        return latest_documents
    
    def identify_latest_quarterly_report(self, documents: List[DocumentInfo]) -> Optional[DocumentInfo]:
        """Identify single latest quarterly report"""
        if not documents:
            return None
        quarterly_types = ['Press Release', '10-Q Quarterly Report', 'Presentation', 'CFO Commentary', 'Transcript']
        quarterly_docs = [d for d in documents if d.document_type in quarterly_types and d.extracted_quarter]
        if not quarterly_docs:
            quarterly_docs = [d for d in documents if d.document_type in quarterly_types]
        if not quarterly_docs:
            return None
        type_priority = {'Press Release': 1, '10-Q Quarterly Report': 2, 'Presentation': 3, 'CFO Commentary': 4, 'Transcript': 5}
        sorted_docs = sorted(quarterly_docs, key=lambda d: (d.extracted_year if d.extracted_year else 0, d.extracted_quarter if d.extracted_quarter else 0, -type_priority.get(d.document_type, 99), d.relevance_score), reverse=True)
        return sorted_docs[0]
    
    def _discover_sections(self, base_url: str) -> List[Tuple[str, str]]:
        """Discover sections"""
        sections = []
        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
        all_links = soup.find_all('a', href=True)
        priority_keywords = ['sec filing', '10-k', '10-q', 'financial', 'annual report', 'quarterly', 'earnings', 'investor']
        
        for link in all_links:
            try:
                text = link.get_text(strip=True)
                href = link.get('href', '')
                if not href or href.startswith('#') or href.startswith('javascript:') or not text or len(text) < 3:
                    continue
                full_url = urljoin(base_url, href)
                if urlparse(full_url).netloc != urlparse(base_url).netloc:
                    continue
                if self._is_likely_section_link(text, href):
                    priority = sum(1 for kw in priority_keywords if kw in text.lower() or kw in href.lower())
                    sections.append((text, full_url, priority))
            except Exception:
                continue
        
        sections.sort(key=lambda x: x[2], reverse=True)
        unique_sections = {}
        for name, url, priority in sections:
            normalized = self._normalize_url(url)
            if normalized not in unique_sections and normalized != self._normalize_url(base_url):
                unique_sections[normalized] = (name, url)
        return list(unique_sections.values())[:self.max_sections]
    
    def _is_likely_section_link(self, text: str, href: str) -> bool:
        """Determine if link is main section"""
        text_lower, href_lower = text.lower(), href.lower()
        word_count = len(text.split())
        if word_count < 1 or word_count > 6:
            return False
        financial_indicators = ['financial', 'investor', 'earning', 'report', 'filing', 'sec', 'presentation', 'event', 'news', 'press', 'annual', 'quarterly', 'result', 'document', 'library', 'information']
        if not any(term in text_lower or term in href_lower for term in financial_indicators):
            return False
        exclude_terms = ['contact', 'email alert', 'subscribe', 'rss', 'faq', 'help', 'cookie', 'privacy', 'terms', 'accessibility', 'sitemap']
        return not any(term in text_lower for term in exclude_terms)
    
    def _extract_all_files_from_page(self, soup: BeautifulSoup, base_url: str, section_name: str) -> List[DocumentInfo]:
        """Extract all file links from page"""
        documents = []
        for link in soup.find_all('a', href=True):
            try:
                href = link.get('href', '')
                if not href or href.startswith('#') or href.startswith('javascript:'):
                    continue
                full_url = urljoin(base_url, href)
                normalized_url = self._normalize_url(full_url)
                if normalized_url in self.seen_urls or not self._is_direct_file(full_url):
                    continue
                self.seen_urls.add(normalized_url)
                
                text = link.get_text(strip=True)
                if not text or len(text) < 5:
                    text = self._extract_filename_from_url(full_url)
                
                doc_type = self._classify_document(full_url, text, link.get('title', ''))
                year = self._extract_year_improved(text + ' ' + full_url)
                quarter = self._extract_quarter(text + ' ' + full_url)
                
                doc = DocumentInfo(
                    title=text[:200], url=full_url, normalized_url=normalized_url,
                    document_type=doc_type, publication_date=self._extract_date(text),
                    file_extension=self._get_extension(full_url), relevance_score=100.0,
                    section=section_name, subsection=doc_type, content_preview=text[:150],
                    metadata={'ticker': '', 'company': ''}, is_direct_file=True,
                    extracted_year=year, extracted_quarter=quarter
                )
                documents.append(doc)
            except Exception:
                continue
        return documents
    
    def _classify_document(self, url: str, text: str, title: str) -> str:
        """Classify document"""
        combined = f"{url} {text} {title}".lower()
        classifications = [
            ('10-K Annual Report', ['10-k', 'form 10-k']),
            ('10-Q Quarterly Report', ['10-q', 'form 10-q']),
            ('8-K Current Report', ['8-k', 'form 8-k']),
            ('Proxy Statement', ['proxy statement', 'def 14a']),
            ('Press Release', ['press release', 'earnings release']),
            ('Transcript', ['transcript']),
            ('Presentation', ['presentation', 'slides']),
            ('Annual Report', ['annual report']),
            ('Excel Data', ['.xlsx', '.xls']),
        ]
        for doc_type, keywords in classifications:
            if any(keyword in combined for keyword in keywords):
                return doc_type
        return 'PDF Document' if '.pdf' in url else 'Financial Document'
    
    def _extract_filename_from_url(self, url: str) -> str:
        """Extract filename from URL"""
        try:
            filename = os.path.basename(unquote(urlparse(url).path))
            filename = re.sub(r'[-_]', ' ', filename)
            filename = re.sub(r'\.(pdf|xlsx?|pptx?|docx?)$', '', filename, flags=re.I)
            filename = re.sub(r'\b[a-f0-9]{32,}\b', '', filename, flags=re.I)
            return re.sub(r'\s+', ' ', filename).strip() or 'Financial Document'
        except Exception:
            return 'Financial Document'
    
    def _extract_date(self, text: str) -> Optional[str]:
        """Extract date"""
        patterns = [r'Q([1-4])\s*20([12]\d)', r'20([12]\d)\s*Q([1-4])']
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(0)
        return None
    
    def _is_valid_year_context(self, year: int, context: str) -> bool:
        """Check if year is valid (not hex string)"""
        if not (2015 <= year <= self.current_year):
            return False
        hex_chars = len(re.findall(r'[a-f]', context.lower()))
        total_chars = len(context)
        if total_chars > 0 and (hex_chars / total_chars) > 0.4:
            return False
        return True
    
    def _extract_year_improved(self, text: str) -> Optional[int]:
        """Extract year with hex detection"""
        text_clean = re.sub(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', '', text, flags=re.I)
        text_clean = re.sub(r'\b[a-f0-9]{8,}\b', '', text_clean, flags=re.I)
        
        year_match = re.search(r'20([12]\d)', text_clean)
        if year_match:
            year = int(year_match.group(0))
            context = text_clean[max(0, year_match.start()-10):year_match.end()+10]
            if self._is_valid_year_context(year, context):
                return year
        return None
    
    def _extract_quarter(self, text: str) -> Optional[int]:
        """Extract quarter"""
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
        """Close driver"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None


def extract_all_companies(input_json_path: str, output_json_path: str = None,
                         start_index: int = 0, end_index: int = None,
                         debug: bool = True) -> Dict[str, Any]:
    """Extract documents for all companies"""
    print("\n" + "="*80)
    print("IR DOCUMENT EXTRACTION")
    print("="*80)
    
    with open(input_json_path, 'r') as f:
        companies = json.load(f)
    
    if end_index is not None:
        companies = companies[start_index:end_index]
    else:
        companies = companies[start_index:]
    
    print(f"Processing {len(companies)} companies")
    
    extractor = IRDocumentExtractor(debug=debug)
    all_documents = {}
    latest_quarterly_reports = {}
    
    try:
        for i, company in enumerate(companies, 1):
            ticker = company.get('ticker', 'UNKNOWN')
            company_name = company.get('company_name', 'Unknown')
            ir_url = company.get('investor_relations_url', '')
            
            if not ir_url:
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
                logger.error(f"Error: {ticker}")
                all_documents[ticker] = []
    finally:
        extractor.close()
    
    total = sum(len(docs) for docs in all_documents.values())
    with_docs = sum(1 for docs in all_documents.values() if docs)
    
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"✅ Total documents: {total}")
    print(f"✅ Companies with docs: {with_docs}/{len(companies)}")
    print(f"📊 Latest quarterly: {len(latest_quarterly_reports)}")
    
    if output_json_path:
        export_data = {
            'all_documents': {ticker: [asdict(doc) for doc in docs] for ticker, docs in all_documents.items()},
            'latest_quarterly_reports': {ticker: asdict(doc) for ticker, doc in latest_quarterly_reports.items()}
        }
        with open(output_json_path, 'w') as f:
            json.dump(export_data, f, indent=2, default=str)
        print(f"\n✅ Saved: {output_json_path}")
    
    return {'all_documents': all_documents, 'latest_quarterly_reports': latest_quarterly_reports}


# ============================================================================
# PART 2: SEC VALIDATOR
# ============================================================================

class SimpleValidator:
    """Simple SEC EDGAR validator"""
    
    def __init__(self, user_email: str = "riyanshibnkedia@gmail.com"):
        self.sec_ticker_map = None
        self.current_year = datetime.now().year
        self.user_email = user_email
    
    def load_sec_map(self):
        """Load SEC ticker map"""
        url = "https://www.sec.gov/files/company_tickers.json"
        headers = {'User-Agent': f'Research {self.user_email}'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            self.sec_ticker_map = {v['ticker'].upper(): v['cik_str'] for v in data.values()}
            print(f"✅ Loaded {len(self.sec_ticker_map)} companies from SEC")
        else:
            print(f"❌ SEC API error: {response.status_code}")
            self.sec_ticker_map = {}
    
    def get_sec_filings(self, ticker: str) -> Dict[str, Optional[int]]:
        """Get latest SEC filing years"""
        if not self.sec_ticker_map:
            self.load_sec_map()
        cik = self.sec_ticker_map.get(ticker.upper())
        if not cik:
            return {}
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        headers = {'User-Agent': f'Research {self.user_email}'}
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                filings = data.get('filings', {}).get('recent', {})
                forms, dates = filings.get('form', []), filings.get('filingDate', [])
                result = {}
                for form_type in ['10-K', '10-Q', 'DEF 14A']:
                    for i, form in enumerate(forms):
                        if form == form_type:
                            result[form_type] = int(dates[i][:4])
                            break
                return result
        except Exception as e:
            return {}
    
    def validate(self, extraction_json: str, output_csv: str = 'validation.csv'):
        """Validate extraction"""
        print("\n" + "="*60)
        print("SEC VALIDATION")
        print("="*60)
        
        with open(extraction_json, 'r') as f:
            data = json.load(f)
        all_docs = data.get('all_documents', data)
        
        results = []
        for ticker, docs in all_docs.items():
            print(f"\n{ticker}:")
            sec_years = self.get_sec_filings(ticker)
            time.sleep(0.5)
            
            ext_10k = next((d for d in docs if d['document_type'] == '10-K Annual Report'), None)
            ext_10q = next((d for d in docs if d['document_type'] == '10-Q Quarterly Report'), None)
            ext_proxy = next((d for d in docs if d['document_type'] == 'Proxy Statement'), None)
            
            sec_10k, ext_10k_year = sec_years.get('10-K'), ext_10k.get('extracted_year') if ext_10k else None
            sec_10q, ext_10q_year = sec_years.get('10-Q'), ext_10q.get('extracted_year') if ext_10q else None
            sec_proxy, ext_proxy_year = sec_years.get('DEF 14A'), ext_proxy.get('extracted_year') if ext_proxy else None
            
            match_10k = self._check(sec_10k, ext_10k_year)
            match_10q = self._check(sec_10q, ext_10q_year)
            match_proxy = self._check(sec_proxy, ext_proxy_year)
            
            print(f"  10-K: SEC={sec_10k}, Extracted={ext_10k_year} → {match_10k}")
            print(f"  10-Q: SEC={sec_10q}, Extracted={ext_10q_year} → {match_10q}")
            print(f"  Proxy: SEC={sec_proxy}, Extracted={ext_proxy_year} → {match_proxy}")
            
            results.append({
                'Ticker': ticker, 'Total_Docs': len(docs),
                'SEC_10K': sec_10k, 'Ext_10K': ext_10k_year, '10K_Match': match_10k,
                'SEC_10Q': sec_10q, 'Ext_10Q': ext_10q_year, '10Q_Match': match_10q,
                'SEC_Proxy': sec_proxy, 'Ext_Proxy': ext_proxy_year, 'Proxy_Match': match_proxy
            })
        
        # Save CSV
        import csv
        with open(output_csv, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        exact = sum(1 for r in results if '✅' in f"{r['10K_Match']}{r['10Q_Match']}{r['Proxy_Match']}")
        print(f"✅ Companies with matches: {exact}/{len(results)}")
        print(f"📄 Saved: {output_csv}")
        return results
    
    def _check(self, sec: Optional[int], ext: Optional[int]) -> str:
        """Check match"""
        if sec is None or ext is None:
            return '❌'
        elif sec == ext:
            return '✅'
        elif abs(sec - ext) == 1:
            return '⚠️'
        return '❌'


# ============================================================================
# PART 3: DOCUMENT DOWNLOADER
# ============================================================================

class DocumentDownloader:
    """Download documents to company folders"""
    
    def __init__(self, base_dir: str = "data/reports", delay: tuple = (1, 3), timeout: int = 60):
        self.base_dir = Path(base_dir)
        self.delay = delay
        self.timeout = timeout
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {'downloaded': 0, 'failed': 0, 'skipped': 0, 'size_mb': 0}
    
    def sanitize(self, name: str, max_len: int = 150) -> str:
        """Sanitize filename"""
        for char in '<>:"/\\|?*':
            name = name.replace(char, '_')
        name = ' '.join(name.strip('. ').split())
        if len(name) > max_len:
            name, ext = os.path.splitext(name)
            name = name[:max_len - len(ext)] + ext
        return name or 'document'
    
    def generate_filename(self, doc: Dict) -> str:
        """Generate filename"""
        parts = [doc.get('document_type', 'Document')]
        if doc.get('extracted_year'):
            parts.append(str(doc['extracted_year']))
            if doc.get('extracted_quarter'):
                parts[-1] += f"_Q{doc['extracted_quarter']}"
        title = doc.get('title', '')[:50]
        if title and len(title) > 10:
            parts.append(self.sanitize(title))
        filename = ' - '.join(parts)
        ext = doc.get('file_extension', '.unknown')
        return self.sanitize(filename + ext)
    
    def download_file(self, url: str, output_path: Path) -> bool:
        """Download file"""
        try:
            headers = {'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}
            response = requests.get(url, headers=headers, timeout=self.timeout, stream=True)
            response.raise_for_status()
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            size_mb = output_path.stat().st_size / (1024 * 1024)
            self.stats['size_mb'] += size_mb
            logger.info(f"✅ Downloaded ({size_mb:.2f} MB)")
            return True
        except Exception as e:
            logger.error(f"❌ Failed: {str(e)[:50]}")
            return False
    
    def download_all(self, extraction_json: str, skip_existing: bool = True):
        """Download all documents"""
        print("\n" + "="*80)
        print("DOCUMENT DOWNLOADER")
        print("="*80)
        
        with open(extraction_json, 'r') as f:
            data = json.load(f)
        all_docs = data.get('all_documents', data)
        
        print(f"Processing {len(all_docs)} companies")
        
        for i, (ticker, docs) in enumerate(all_docs.items(), 1):
            if not docs:
                continue
            
            company_name = docs[0].get('metadata', {}).get('company', 'Unknown')
            company_dir = self.base_dir / f"{ticker} - {self.sanitize(company_name)}"
            company_dir.mkdir(parents=True, exist_ok=True)
            
            print(f"\n[{i}/{len(all_docs)}] {ticker}: {len(docs)} documents")
            
            for j, doc in enumerate(docs, 1):
                url = doc.get('url')
                if not url:
                    continue
                
                filename = self.generate_filename(doc)
                output_path = company_dir / filename
                
                if skip_existing and output_path.exists():
                    self.stats['skipped'] += 1
                    continue
                
                logger.info(f"  [{j}/{len(docs)}] {filename}")
                if self.download_file(url, output_path):
                    self.stats['downloaded'] += 1
                else:
                    self.stats['failed'] += 1
                
                time.sleep(random.uniform(*self.delay))
        
        print("\n" + "="*80)
        print("DOWNLOAD SUMMARY")
        print("="*80)
        print(f"✅ Downloaded: {self.stats['downloaded']}")
        print(f"⏭️  Skipped: {self.stats['skipped']}")
        print(f"❌ Failed: {self.stats['failed']}")
        print(f"💾 Total size: {self.stats['size_mb']:.2f} MB")
        print(f"📂 Location: {self.base_dir.absolute()}")


# ============================================================================
# MAIN END-TO-END PIPELINE
# ============================================================================

def run_complete_pipeline(
    companies_json: str = 'data/dow30_ir_pages.json',
    output_dir: str = 'output',
    user_email: str = 'riyanshibnkedia@gmail.com',
    start_index: int = 0,
    end_index: int = None,
    skip_download: bool = False,
    skip_validation: bool = False
):
    """
    Run complete end-to-end pipeline
    
    Args:
        companies_json: Input JSON with company info
        output_dir: Output directory for all files
        user_email: Your email for SEC API
        start_index: Start index for companies
        end_index: End index for companies (None = all)
        skip_download: Skip downloading files
        skip_validation: Skip SEC validation
    """
    os.makedirs(output_dir, exist_ok=True)
    
    print("\n" + "="*80)
    print("🚀 COMPLETE IR EXTRACTION PIPELINE")
    print("="*80)
    print(f"Input: {companies_json}")
    print(f"Output: {output_dir}")
    print(f"Companies: {start_index} to {end_index or 'end'}")
    print("="*80)
    
    # STEP 1: Extract documents
    print("\n📥 STEP 1: Extracting documents...")
    extraction_output = os.path.join(output_dir, 'all_documents.json')
    
    results = extract_all_companies(
        input_json_path=companies_json,
        output_json_path=extraction_output,
        start_index=start_index,
        end_index=end_index,
        debug=True
    )
    
    # STEP 2: Validate with SEC
    if not skip_validation:
        print("\n🔍 STEP 2: Validating with SEC...")
        validator = SimpleValidator(user_email=user_email)
        validation_csv = os.path.join(output_dir, 'sec_validation.csv')
        validator.validate(extraction_output, validation_csv)
    
    # STEP 3: Download files
    if not skip_download:
        print("\n📥 STEP 3: Downloading files...")
        reports_dir = os.path.join(output_dir, 'reports')
        downloader = DocumentDownloader(base_dir=reports_dir)
        downloader.download_all(extraction_output, skip_existing=True)
    
    print("\n" + "="*80)
    print("✅ PIPELINE COMPLETE")
    print("="*80)
    print(f"\nGenerated files:")
    print(f"  1. {extraction_output} - Extraction results")
    if not skip_validation:
        print(f"  2. {os.path.join(output_dir, 'sec_validation.csv')} - SEC validation")
    if not skip_download:
        print(f"  3. {os.path.join(output_dir, 'reports/')} - Downloaded files")
    print("\n")


# ============================================================================
# USAGE
# ============================================================================

if __name__ == "__main__":
    # QUICK TEST (5 companies)
    run_complete_pipeline(
        companies_json='data/dow30_ir_pages.json',
        output_dir='data/test_output',
        user_email='riyanshibnkedia@gmail.com',  # ← CHANGE THIS
        start_index=0,
        end_index=3,
        skip_download=False,  # Set True to skip downloading
        skip_validation=False  # Set True to skip validation
    )
    
    # FULL RUN (all companies)
    # run_complete_pipeline(
    #     companies_json='cnbc_companies_with_ir.json',
    #     output_dir='data/full_output',
    #     user_email='your.email@example.com',
    #     skip_download=False,
    #     skip_validation=False
    # )