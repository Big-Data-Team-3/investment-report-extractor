#!/usr/bin/env python3
"""
Complete Playwright IR Extraction + Download Script
Extracts metadata AND downloads files for 2 companies

Usage:
    python playwirght_ir_doc.py

Requirements:
    pip install playwright beautifulsoup4 requests
    python -m playwright install chromium
"""

import re
import asyncio
import random
import time
import requests
from datetime import datetime
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, asdict
from urllib.parse import urljoin, urlparse, unquote, urlunparse
import json
import os
from collections import defaultdict
from bs4 import BeautifulSoup
import logging
from pathlib import Path

from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION
# ============================================================================

INPUT_JSON = "/Users/RiyanshiKedia/Documents/GitHub/investment-report-extractor/data/dow30_ir_pages.json"
OUTPUT_JSON = "/Users/RiyanshiKedia/Documents/GitHub/investment-report-extractor/data/documents/test_2_companies.json"
DOWNLOAD_DIR = "/Users/RiyanshiKedia/Documents/GitHub/investment-report-extractor/data/reports"

# Number of companies to process
NUM_COMPANIES = 2

# ============================================================================
# DATA CLASSES
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
# PLAYWRIGHT EXTRACTOR CLASS
# ============================================================================

class PlaywrightIRExtractor:
    """Extract IR documents using Playwright"""
    
    def __init__(self, debug: bool = True, max_sections: int = 20, 
                 request_delay: Tuple[float, float] = (2, 4), max_retries: int = 2):
        self.debug = debug
        self.max_sections = max_sections
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.file_extensions = ['.pdf', '.xlsx', '.xls', '.pptx', '.ppt', 
                                '.docx', '.doc', '.csv', '.mp3', '.mp4']
        self.seen_urls: Set[str] = set()
        self.current_year = datetime.now().year
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
    
    async def __aenter__(self):
        await self._setup_playwright()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def _setup_playwright(self):
        if self.debug:
            logger.info("Setting up Playwright browser...")
        
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        self.page = await self.context.new_page()
        self.page.set_default_timeout(45000)
        
        if self.debug:
            logger.info("✅ Playwright browser ready")
    
    def _normalize_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, '', ''))
        except:
            return url
    
    def _is_valid_ir_url(self, url: str) -> bool:
        url_lower = url.lower()
        invalid = ['smartphone', 'iphone', 'product', 'shop', '/careers']
        if any(bad in url_lower for bad in invalid):
            return False
        valid = ['investor', 'ir.', 'shareholders', '/investor']
        return any(good in url_lower for good in valid)
    
    def _is_direct_file(self, url: str) -> bool:
        url_lower = url.lower()
        if any(ext in url_lower for ext in self.file_extensions):
            return True
        cdn_patterns = [
            r'\.q4cdn\.com/.*/files/', r'\.cloudfront\.net/', 
            r'/static-files/[a-f0-9\-]{30,}', r'/files/doc_'
        ]
        return any(re.search(p, url_lower) for p in cdn_patterns)
    
    async def _wait_for_dynamic_content(self, url: str):
        try:
            await self.page.wait_for_selector('body', timeout=10000)
            await asyncio.sleep(4)
            try:
                await self.page.wait_for_selector('a[href*="pdf"], a[href*="10-k"]', timeout=8000)
            except:
                await self.page.wait_for_function('document.querySelectorAll("a").length > 20', timeout=5000)
            await asyncio.sleep(2)
        except:
            pass
    
    async def extract_documents(self, ir_url: str, ticker: str, company_name: str) -> List[DocumentInfo]:
        if not self._is_valid_ir_url(ir_url):
            logger.error(f"❌ {ticker}: Invalid IR URL")
            return []
        
        if self.debug:
            logger.info(f"\n[{ticker}] {company_name}")
            logger.info(f"URL: {ir_url}")
        
        all_raw_documents = []
        
        for attempt in range(self.max_retries):
            try:
                await self.page.goto(ir_url, wait_until='domcontentloaded')
                await self._wait_for_dynamic_content(ir_url)
                
                content = await self.page.content()
                sections = await self._discover_sections(ir_url, content)
                
                if self.debug:
                    logger.info(f"Found {len(sections)} sections")
                
                for section_name, section_url in sections:
                    try:
                        await self.page.goto(section_url, wait_until='domcontentloaded')
                        await self._wait_for_dynamic_content(section_url)
                        await asyncio.sleep(random.uniform(*self.request_delay))
                        
                        section_content = await self.page.content()
                        soup = BeautifulSoup(section_content, 'html.parser')
                        docs = self._extract_all_files_from_page(soup, section_url, section_name)
                        
                        if self.debug:
                            logger.info(f"  • {section_name}: {len(docs)} docs")
                        
                        all_raw_documents.extend(docs)
                    except Exception as e:
                        logger.error(f"Error in section {section_name}: {e}")
                        continue
                
                if all_raw_documents:
                    break
                
            except Exception as e:
                if attempt < self.max_retries - 1:
                    logger.warning(f"Retry {attempt + 1}/{self.max_retries}")
                    await asyncio.sleep(5)
                else:
                    logger.error(f"❌ {ticker}: Failed")
                    return []
        
        all_raw_documents = [d for d in all_raw_documents if d.is_direct_file]
        deduplicated = self._global_deduplicate_improved(all_raw_documents)
        
        if self.debug:
            logger.info(f"Final: {len(deduplicated)} unique documents")
        
        return deduplicated
    
    async def _discover_sections(self, base_url: str, content: str) -> List[Tuple[str, str]]:
        soup = BeautifulSoup(content, 'html.parser')
        all_links = soup.find_all('a', href=True)
        
        priority_keywords = ['sec filing', '10-k', '10-q', 'financial', 'earnings']
        sections = []
        
        for link in all_links:
            try:
                text = link.get_text(strip=True)
                href = link.get('href', '')
                
                if not href or href.startswith('#') or not text or len(text) < 3:
                    continue
                
                full_url = urljoin(base_url, href)
                
                if urlparse(full_url).netloc != urlparse(base_url).netloc:
                    continue
                
                if self._is_likely_section_link(text, href, link):
                    priority = sum(1 for kw in priority_keywords if kw in text.lower())
                    sections.append((text, full_url, priority))
            except:
                continue
        
        sections.sort(key=lambda x: x[2], reverse=True)
        
        unique_sections = {}
        for name, url, priority in sections:
            normalized = self._normalize_url(url)
            if normalized not in unique_sections:
                unique_sections[normalized] = (name, url)
        
        return list(unique_sections.values())[:self.max_sections]
    
    def _is_likely_section_link(self, text: str, href: str, link) -> bool:
        text_lower = text.lower()
        href_lower = href.lower()
        
        word_count = len(text.split())
        if word_count < 1 or word_count > 6:
            return False
        
        financial_indicators = [
            'financial', 'investor', 'earning', 'report', 'filing', 'sec',
            'presentation', 'event', 'news', 'press', 'annual', 'quarterly'
        ]
        
        has_financial = any(t in text_lower or t in href_lower for t in financial_indicators)
        if not has_financial:
            return False
        
        exclude = ['contact', 'subscribe', 'rss', 'faq', 'cookie', 'privacy']
        return not any(t in text_lower for t in exclude)
    
    def _extract_all_files_from_page(self, soup: BeautifulSoup, 
                                     base_url: str, section_name: str) -> List[DocumentInfo]:
        documents = []
        all_links = soup.find_all('a', href=True)
        
        for link in all_links:
            try:
                href = link.get('href', '')
                if not href or href.startswith('#'):
                    continue
                
                full_url = urljoin(base_url, href)
                normalized_url = self._normalize_url(full_url)
                
                if normalized_url in self.seen_urls or not self._is_direct_file(full_url):
                    continue
                
                self.seen_urls.add(normalized_url)
                
                text = link.get_text(strip=True)
                title = link.get('title', '')
                
                if not text or len(text) < 5:
                    text = os.path.basename(unquote(urlparse(full_url).path))
                
                combined = text + ' ' + full_url + ' ' + title
                doc_type = self._classify_document(full_url, text, title)
                year = self._extract_year_improved(combined)
                quarter = self._extract_quarter(combined)
                
                doc = DocumentInfo(
                    title=text[:200],
                    url=full_url,
                    normalized_url=normalized_url,
                    document_type=doc_type,
                    publication_date=None,
                    file_extension=self._get_extension(full_url),
                    relevance_score=100.0,
                    section=section_name,
                    subsection=doc_type,
                    content_preview=text[:150],
                    metadata={},
                    is_direct_file=True,
                    extracted_year=year,
                    extracted_quarter=quarter
                )
                
                documents.append(doc)
            except:
                continue
        
        return documents
    
    def _global_deduplicate_improved(self, documents: List[DocumentInfo]) -> List[DocumentInfo]:
        by_type = defaultdict(list)
        for doc in documents:
            by_type[doc.document_type].append(doc)
        
        latest_documents = []
        
        for doc_type, docs in by_type.items():
            docs_with_year = [d for d in docs if d.extracted_year]
            
            if docs_with_year:
                latest = max(docs_with_year, key=lambda d: (
                    d.extracted_year,
                    d.extracted_quarter if d.extracted_quarter else -1
                ))
            elif docs:
                latest = docs[0]
            else:
                continue
            
            latest_documents.append(latest)
        
        return latest_documents
    
    def identify_latest_quarterly_report(self, documents: List[DocumentInfo]) -> Optional[DocumentInfo]:
        quarterly_types = ['Press Release', '10-Q Quarterly Report', 'Presentation', 'Transcript']
        quarterly_docs = [d for d in documents if d.document_type in quarterly_types]
        
        if not quarterly_docs:
            return None
        
        return max(quarterly_docs, key=lambda d: (
            d.extracted_year if d.extracted_year else 0,
            d.extracted_quarter if d.extracted_quarter else 0
        ))
    
    def _classify_document(self, url: str, text: str, title: str) -> str:
        combined = f"{url} {text} {title}".lower()
        
        if any(k in combined for k in ['10-k', 'form 10-k']):
            return '10-K Annual Report'
        if any(k in combined for k in ['10-q', 'form 10-q']):
            return '10-Q Quarterly Report'
        if 'proxy' in combined or 'def 14a' in combined:
            return 'Proxy Statement'
        if 'press release' in combined or 'earnings release' in combined:
            return 'Press Release'
        if 'transcript' in combined:
            return 'Transcript'
        if 'presentation' in combined or 'slides' in combined:
            return 'Presentation'
        if '.xls' in url:
            return 'Excel Data'
        
        return 'PDF Document' if '.pdf' in url else 'Financial Document'
    
    def _extract_year_improved(self, text: str) -> Optional[int]:
        text_clean = re.sub(r'[a-f0-9]{8,}', '', text, flags=re.I)
        
        year_match = re.search(r'20([12]\d)', text_clean)
        if year_match:
            year = int(year_match.group(0))
            if 2020 <= year <= self.current_year:
                return year
        
        fy_match = re.search(r'fy\s*(\d{2,4})', text_clean, re.I)
        if fy_match:
            fy = fy_match.group(1)
            if len(fy) == 2:
                year = 2000 + int(fy)
                if 2020 <= year <= self.current_year + 2:
                    return year
        
        return None
    
    def _extract_quarter(self, text: str) -> Optional[int]:
        match = re.search(r'[q\-]([1-4])(?:\s|q|$)', text, re.I)
        return int(match.group(1)) if match else None
    
    def _get_extension(self, url: str) -> str:
        for ext in self.file_extensions:
            if ext in url.lower():
                return ext
        return '.unknown'
    
    async def close(self):
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


# ============================================================================
# DOCUMENT DOWNLOADER CLASS
# ============================================================================

class DocumentDownloader:
    """Download documents to company-specific folders"""
    
    def __init__(self, base_output_dir: str = "data/reports", timeout: int = 60, max_retries: int = 3):
        self.base_output_dir = Path(base_output_dir)
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {
            'successful_downloads': 0,
            'failed_downloads': 0,
            'total_size_mb': 0
        }
    
    def sanitize_filename(self, filename: str, max_length: int = 150) -> str:
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        filename = filename.strip('. ')
        filename = ' '.join(filename.split())
        
        if len(filename) > max_length:
            name, ext = os.path.splitext(filename)
            filename = name[:max_length - len(ext)] + ext
        
        return filename or 'document'
    
    def generate_filename(self, doc: DocumentInfo, url: str) -> str:
        doc_type = doc.document_type
        title = doc.title
        year = doc.extracted_year
        quarter = doc.extracted_quarter
        
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
        ext = doc.file_extension
        if not filename.endswith(ext):
            filename += ext
        
        return self.sanitize_filename(filename)
    
    def download_file(self, url: str, output_path: Path) -> bool:
        for attempt in range(self.max_retries):
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': '*/*'
                }
                
                response = requests.get(url, headers=headers, timeout=self.timeout, stream=True)
                response.raise_for_status()
                
                with open(output_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                file_size_mb = output_path.stat().st_size / (1024 * 1024)
                self.stats['total_size_mb'] += file_size_mb
                
                logger.info(f"✅ Downloaded: {output_path.name} ({file_size_mb:.2f} MB)")
                return True
                
            except Exception as e:
                if attempt < self.max_retries - 1:
                    logger.warning(f"⚠️ Retry {attempt + 1}/{self.max_retries}: {str(e)[:50]}")
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"❌ Failed: {url[:100]}")
                    return False
        
        return False
    
    def download_company_documents(self, ticker: str, company_name: str, 
                                   documents: List[DocumentInfo]) -> Dict[str, int]:
        if not documents:
            logger.info(f"⭐ {ticker}: No documents to download")
            return {'downloaded': 0, 'failed': 0}
        
        company_folder_name = f"{ticker} - {company_name}"
        company_dir = self.base_output_dir / company_folder_name
        company_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"\n{'='*80}")
        logger.info(f"📁 {ticker}: {company_name}")
        logger.info(f"   Folder: {company_dir}")
        logger.info(f"   Documents: {len(documents)}")
        logger.info(f"{'='*80}")
        
        stats = {'downloaded': 0, 'failed': 0}
        
        for i, doc in enumerate(documents, 1):
            try:
                url = doc.url
                filename = self.generate_filename(doc, url)
                output_path = company_dir / filename
                
                logger.info(f"⬇️ [{i}/{len(documents)}] Downloading: {filename}")
                success = self.download_file(url, output_path)
                
                if success:
                    stats['downloaded'] += 1
                    self.stats['successful_downloads'] += 1
                else:
                    stats['failed'] += 1
                    self.stats['failed_downloads'] += 1
                
                if i < len(documents):
                    time.sleep(random.uniform(1, 2))
                    
            except Exception as e:
                logger.error(f"❌ Error: {e}")
                stats['failed'] += 1
                self.stats['failed_downloads'] += 1
        
        # Save metadata
        metadata_path = company_dir / "_metadata.json"
        with open(metadata_path, 'w') as f:
            json.dump({
                'ticker': ticker,
                'company_name': company_name,
                'total_documents': len(documents),
                'documents': [asdict(d) for d in documents],
                'download_stats': stats,
                'downloaded_at': time.strftime('%Y-%m-%d %H:%M:%S')
            }, f, indent=2)
        
        logger.info(f"\n✅ {ticker} Complete: {stats['downloaded']} downloaded, {stats['failed']} failed")
        
        return stats


# ============================================================================
# MAIN EXECUTION
# ============================================================================

async def main():
    print("\n" + "="*80)
    print("🚀 PLAYWRIGHT IR EXTRACTION + DOWNLOAD - 2 COMPANIES")
    print("="*80)
    print(f"📥 Input:  {INPUT_JSON}")
    print(f"📤 Output: {OUTPUT_JSON}")
    print(f"📁 Downloads: {DOWNLOAD_DIR}")
    print(f"🎯 Companies to process: {NUM_COMPANIES}")
    print("="*80 + "\n")
    
    # Load IR pages
    with open(INPUT_JSON, 'r') as f:
        ir_pages = json.load(f)
    
    companies = []
    for page in ir_pages:
        companies.append({
            'ticker': page['metadata']['ticker'],
            'company_name': page['metadata']['company_name'],
            'investor_relations_url': page['url']
        })
    
    # Limit to NUM_COMPANIES
    companies = companies[:NUM_COMPANIES]
    
    print(f"📊 Processing companies:")
    for i, c in enumerate(companies, 1):
        print(f"  {i}. {c['ticker']} - {c['investor_relations_url']}")
    
    # Create output directory
    Path(OUTPUT_JSON).parent.mkdir(parents=True, exist_ok=True)
    
    start_time = datetime.now()
    all_documents = {}
    latest_quarterly_reports = {}
    
    # PHASE 1: EXTRACT METADATA
    print("\n" + "="*80)
    print("PHASE 1: EXTRACTING DOCUMENT METADATA")
    print("="*80)
    
    async with PlaywrightIRExtractor(debug=True) as extractor:
        for i, company in enumerate(companies, 1):
            ticker = company['ticker']
            print(f"\n[{i}/{len(companies)}] {ticker}")
            
            try:
                docs = await extractor.extract_documents(
                    company['investor_relations_url'], 
                    ticker, 
                    company['company_name']
                )
                
                for doc in docs:
                    doc.metadata['ticker'] = ticker
                    doc.metadata['company'] = company['company_name']
                
                all_documents[ticker] = docs
                
                if docs:
                    latest_q = extractor.identify_latest_quarterly_report(docs)
                    if latest_q:
                        latest_quarterly_reports[ticker] = latest_q
                
                await asyncio.sleep(2)
                
            except Exception as e:
                print(f"❌ Error: {e}")
                all_documents[ticker] = []
    
    extraction_duration = (datetime.now() - start_time).total_seconds()
    total_docs = sum(len(docs) for docs in all_documents.values())
    
    print("\n" + "="*80)
    print("PHASE 1 COMPLETE - EXTRACTION SUMMARY")
    print("="*80)
    print(f"✅ Total documents found: {total_docs}")
    print(f"⏱️  Extraction time: {extraction_duration:.1f}s")
    
    # PHASE 2: DOWNLOAD FILES
    print("\n" + "="*80)
    print("PHASE 2: DOWNLOADING FILES")
    print("="*80)
    
    downloader = DocumentDownloader(base_output_dir=DOWNLOAD_DIR)
    
    for ticker, documents in all_documents.items():
        if documents:
            company_name = documents[0].metadata.get('company', ticker)
            downloader.download_company_documents(ticker, company_name, documents)
    
    # FINAL SUMMARY
    total_duration = (datetime.now() - start_time).total_seconds()
    
    print("\n" + "="*80)
    print("📊 FINAL SUMMARY")
    print("="*80)
    print(f"✅ Total documents extracted: {total_docs}")
    print(f"✅ Successfully downloaded: {downloader.stats['successful_downloads']}")
    print(f"❌ Failed downloads: {downloader.stats['failed_downloads']}")
    print(f"💾 Total size: {downloader.stats['total_size_mb']:.2f} MB")
    print(f"⏱️  Total time: {total_duration:.1f}s ({total_duration/60:.1f} min)")
    print(f"📁 Files saved to: {DOWNLOAD_DIR}")
    
    # Save metadata JSON
    export_data = {
        'extraction_metadata': {
            'total_companies': len(companies),
            'total_documents': total_docs,
            'extraction_date': datetime.now().isoformat(),
            'duration_seconds': total_duration,
            'download_stats': downloader.stats
        },
        'all_documents': {t: [asdict(d) for d in docs] for t, docs in all_documents.items()},
        'latest_quarterly_reports': {t: asdict(d) for t, d in latest_quarterly_reports.items()}
    }
    
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(export_data, f, indent=2, default=str)
    
    print(f"\n✅ Metadata saved to: {OUTPUT_JSON}")
    print("="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())