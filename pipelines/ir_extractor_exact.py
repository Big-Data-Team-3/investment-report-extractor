# pipelines/ir_extractor_exact.py
"""
Enhanced IR (Investor Relations) extractor using the efficient logic from the notebook.
Implements a fast, rule-based approach with minimal LLM usage.
"""

import asyncio
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin
from playwright.async_api import async_playwright
import logging

logger = logging.getLogger(__name__)

class IRExtractorExact:
    """Fast, rule-based IR page extractor based on notebook logic"""
    
    def __init__(self):
        # Company-specific IR patterns (from notebook)
        self.company_specific_patterns = {
            'amazon': 'https://ir.aboutamazon.com',
            '3m': 'https://investors.3m.com',
            'chevron': 'https://www.chevron.com/investors',
            'caterpillar': 'https://www.caterpillar.com/en/investors.html',
            'salesforce': 'https://investor.salesforce.com',
            'pg.com': 'https://www.pginvestor.com',
            'proctergamble': 'https://www.pginvestor.com',
            'visa': 'https://investor.visa.com',
            'verizon': 'https://www.verizon.com/about/investors',
            'honeywell': 'https://investor.honeywell.com'
        }
        
        # IR-related keywords for scoring
        self.ir_url_patterns = {
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
        
        self.ir_text_patterns = {
            'investor relations': 40,
            'investors': 30,
            'investor': 25,
            'shareholder': 20,
            'financial information': 20,
            'stock information': 20,
            'ir': 15,
        }
        
        # Domains to ignore (third-party sites)
        self.ignore_domains = [
            'cnbc.com', 'facebook', 'twitter', 'linkedin', 'youtube', 'instagram',
            'tipranks', 'seekingalpha', 'marketwatch', 'yahoo', 'google', 'reuters',
            'bloomberg', 'morningstar', 'zacks', 'fool.com', 'investopedia'
        ]

    def extract_domain_info(self, url: str) -> Tuple[str, str]:
        """Extract full domain and root domain from URL"""
        match = re.search(r'https?://(?:www\.)?([^/]+)', url)
        if not match:
            return "", ""
        
        full_domain = match.group(1)
        domain_parts = full_domain.split('.')
        
        if len(domain_parts) >= 2:
            root_domain = '.'.join(domain_parts[-2:])
        else:
            root_domain = full_domain
            
        return full_domain, root_domain

    def generate_ir_subdomain_candidates(self, root_domain: str, ticker: str) -> List[str]:
        """Generate IR subdomain candidates based on notebook logic"""
        candidates = []
        
        # Check company-specific patterns first
        for pattern_key, ir_url in self.company_specific_patterns.items():
            if pattern_key in root_domain.lower():
                candidates.append(ir_url)
                break
        
        # Common IR subdomain patterns
        common_patterns = [
            f"https://investor.{root_domain}",
            f"https://investors.{root_domain}",
            f"https://ir.{root_domain}",
            f"https://investorrelations.{root_domain}",
            f"https://s2.q4cdn.com/{ticker.lower()}/",  # Common IR hosting
        ]
        
        candidates.extend(common_patterns)
        return candidates

    async def test_ir_subdomain(self, url: str, timeout: int = 15000) -> bool:
        """Test if an IR subdomain exists and contains IR content"""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                
                response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                
                if not response or response.status != 200:
                    await browser.close()
                    return False
                
                # Wait for content to load
                await page.wait_for_timeout(1000)
                content = await page.content()
                content_lower = content.lower()
                
                # Check for IR-related keywords
                ir_keywords = ['investor relations', 'investor', 'sec filings', 
                              'financial results', 'earnings', 'stock information']
                keyword_count = sum(1 for keyword in ir_keywords if keyword in content_lower)
                
                await browser.close()
                return keyword_count >= 2  # At least 2 IR keywords found
                
        except Exception as e:
            logger.debug(f"Failed to test {url}: {e}")
            return False

    def score_link_for_ir(self, url: str, text: str, title: str = "") -> int:
        """Score a link for IR relevance using notebook logic"""
        score = 0
        url_lower = url.lower()
        combined_text = f"{text} {title}".lower()
        
        # URL pattern scoring
        for pattern, points in self.ir_url_patterns.items():
            if pattern in url_lower:
                score += points
        
        # Text pattern scoring
        for pattern, points in self.ir_text_patterns.items():
            if pattern in combined_text:
                score += points
        
        return score

    def is_valid_ir_domain(self, url: str, root_domain: str) -> bool:
        """Check if URL is on valid domain for IR links"""
        url_lower = url.lower()
        
        # Check if it's on the same root domain
        if root_domain in url_lower:
            return True
        
        # Check for known IR subdomains
        ir_subdomains = ['investor.', 'investors.', 'ir.', 'q4cdn.com']
        if any(subdomain in url_lower for subdomain in ir_subdomains):
            return True
        
        return False

    async def scrape_main_website_for_ir(self, company_url: str, root_domain: str, 
                                       timeout: int = 30000) -> Optional[str]:
        """Scrape main website to find IR links (Stage 2 from notebook)"""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                
                await page.goto(company_url, wait_until="domcontentloaded", timeout=timeout)
                await page.wait_for_timeout(2000)
                
                # Get all links
                all_links = await page.query_selector_all("a[href]")
                
                candidates = []
                
                for link in all_links:
                    try:
                        href = await link.get_attribute("href")
                        if not href:
                            continue
                        
                        link_text = await link.inner_text()
                        title_attr = await link.get_attribute("title") or ""
                        
                        # Convert relative URLs to absolute
                        if href.startswith('/'):
                            href = company_url.rstrip('/') + href
                        elif not href.startswith('http'):
                            href = company_url.rstrip('/') + '/' + href
                        
                        # Score the link
                        score = self.score_link_for_ir(href, link_text, title_attr)
                        
                        # Only consider links with positive scores on valid domains
                        if score > 0 and self.is_valid_ir_domain(href, root_domain):
                            candidates.append({
                                'url': href,
                                'text': link_text.strip(),
                                'score': score
                            })
                    
                    except Exception:
                        continue
                
                await browser.close()
                
                # Return highest scoring candidate
                if candidates:
                    candidates.sort(key=lambda x: x['score'], reverse=True)
                    return candidates[0]['url']
                
                return None
                
        except Exception as e:
            logger.error(f"Error scraping main website {company_url}: {e}")
            return None

    async def find_ir_page(self, company_url: str, ticker: str, 
                          company_name: str, debug: bool = False) -> str:
        """
        Main method to find IR page using notebook's two-stage approach
        
        Args:
            company_url: Company's main website URL
            ticker: Company ticker symbol
            company_name: Company name
            debug: Enable debug logging
            
        Returns:
            IR page URL or empty string if not found
        """
        if not company_url:
            return ""
        
        full_domain, root_domain = self.extract_domain_info(company_url)
        if not root_domain:
            return ""
        
        if debug:
            logger.info(f"Finding IR page for {ticker} - {company_name}")
            logger.info(f"Base domain: {full_domain}, Root: {root_domain}")
        
        # STAGE 1: Try common IR subdomain patterns (fast!)
        if debug:
            logger.info("Stage 1: Testing IR subdomain patterns...")
        
        candidates = self.generate_ir_subdomain_candidates(root_domain, ticker)
        
        for candidate_url in candidates:
            if debug:
                logger.info(f"  Testing: {candidate_url}")
            
            if await self.test_ir_subdomain(candidate_url):
                if debug:
                    logger.info(f"  ✓ Found valid IR page: {candidate_url}")
                return candidate_url
        
        # STAGE 2: Scrape main website for IR links
        if debug:
            logger.info("Stage 2: Scraping main website for IR links...")
        
        ir_url = await self.scrape_main_website_for_ir(company_url, root_domain)
        
        if ir_url:
            if debug:
                logger.info(f"  ✓ Found IR page: {ir_url}")
            return ir_url
        else:
            if debug:
                logger.info("  ✗ No IR page found")
            return company_url  # Fallback to main website

    async def extract_company_website_from_cnbc(self, cnbc_url: str, ticker: str, 
                                              company_name: str, debug: bool = False) -> Dict[str, str]:
        """
        Extract company website and IR URL from CNBC profile page.
        Based on the notebook's extract_company_website_from_cnbc function.
        """
        result = {
            "company_website": "",
            "investor_relations_url": ""
        }
        
        # Prepare company name variants for matching
        company_words = re.sub(r'[^\w\s]', '', company_name.lower()).split()
        company_words = [w for w in company_words if w not in ['inc', 'corp', 'company', 'corporation', 'ltd', 'llc', 'the', 'group', 'co']]
        ticker_lower = ticker.lower()
        
        def extract_domain(url: str) -> str:
            """Extract the main domain from a URL."""
            match = re.search(r'https?://(?:www\.)?([^/]+)', url)
            return match.group(1).lower() if match else ""
        
        def score_url(url: str, link_text: str = "") -> int:
            """Score a URL based on how likely it is to be the company's website."""
            score = 0
            domain = extract_domain(url)
            
            if not domain or any(ignore in domain for ignore in self.ignore_domains):
                return -1000  # Definitely not the company site
            
            # Check if ticker is in domain
            if ticker_lower in domain:
                score += 100
            
            # Check if company name words are in domain
            for word in company_words:
                if len(word) > 3 and word in domain:  # Only match meaningful words
                    score += 50
            
            # Check for IR patterns in URL
            if any(pattern in url.lower() for pattern in ['investor', 'ir.', '/investors', '/investor-relations']):
                score += 30
            
            # Check for IR patterns in link text
            if any(pattern in link_text.lower() for pattern in ['investor', 'ir', 'relations']):
                score += 20
            
            # Prefer cleaner domains (likely official sites)
            if domain.count('.') <= 2:  # e.g., apple.com or investor.apple.com
                score += 10
            
            return score
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                
                if debug:
                    logger.info(f"  Visiting CNBC profile: {cnbc_url}")
                    logger.info(f"  Looking for domains containing: {company_words} or {ticker_lower}")
                
                await page.goto(cnbc_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)  # Wait for JS
                
                # Collect all external links
                all_links = await page.query_selector_all("a[href^='http']")
                
                candidates = []
                
                for link in all_links:
                    href = await link.get_attribute("href")
                    if not href:
                        continue
                    
                    try:
                        link_text = await link.inner_text()
                    except:
                        link_text = ""
                    
                    score = score_url(href, link_text)
                    
                    if score > 0:  # Only consider positive scores
                        is_ir = any(pattern in href.lower() for pattern in ['investor', 'ir.', '/investors', '/investor-relations'])
                        candidates.append({
                            "url": href,
                            "score": score,
                            "is_ir": is_ir,
                            "text": link_text.strip(),
                            "domain": extract_domain(href)
                        })
                        
                        if debug:
                            logger.info(f"    Candidate: {extract_domain(href)} (score: {score}, IR: {is_ir})")
                
                # Sort by score (highest first)
                candidates.sort(key=lambda x: x['score'], reverse=True)
                
                # Extract best matches
                for candidate in candidates:
                    if candidate['is_ir'] and not result["investor_relations_url"]:
                        result["investor_relations_url"] = candidate['url']
                    elif not candidate['is_ir'] and not result["company_website"]:
                        result["company_website"] = candidate['url']
                    
                    # Stop if we found both
                    if result["company_website"] and result["investor_relations_url"]:
                        break
                
                # If we only found IR, use it as company website too
                if not result["company_website"] and result["investor_relations_url"]:
                    # Try to get the main domain from IR URL
                    ir_domain = extract_domain(result["investor_relations_url"])
                    # Convert investor.apple.com -> apple.com
                    base_domain = '.'.join(ir_domain.split('.')[-2:])
                    result["company_website"] = f"https://{base_domain}"
                
                if debug:
                    logger.info(f"    ✓ Website: {result['company_website']}")
                    logger.info(f"    ✓ IR: {result['investor_relations_url']}")
                
                await browser.close()
                
        except Exception as e:
            if debug:
                logger.error(f"  Error extracting from {cnbc_url}: {e}")
        
        return result

    async def extract_ir_pages_for_companies(self, companies: List[Dict], 
                                           debug: bool = True) -> List[Dict]:
        """Extract IR pages for a list of companies"""
        updated_companies = []
        
        logger.info(f"Extracting IR pages for {len(companies)} companies using exact mode...")
        
        for i, company in enumerate(companies, 1):
            logger.info(f"[{i}/{len(companies)}] {company['ticker']} - {company.get('company_name', 'N/A')}")
            
            # Step 1: If we have a CNBC URL but no company website, extract it first
            cnbc_url = company.get('cnbc_url', '')
            company_website = company.get('company_website', '')
            
            if cnbc_url and not company_website:
                logger.info(f"  Extracting company website from CNBC profile...")
                website_info = await self.extract_company_website_from_cnbc(
                    cnbc_url, 
                    company['ticker'],
                    company.get('company_name', ''),
                    debug=debug
                )
                
                # Update company with extracted website info
                if website_info['company_website']:
                    company['company_website'] = website_info['company_website']
                    company_website = website_info['company_website']
                
                # If we found IR URL directly from CNBC, use it
                if website_info['investor_relations_url']:
                    company['investor_relations_url'] = website_info['investor_relations_url']
                    logger.info(f"  ✓ Found IR URL from CNBC: {website_info['investor_relations_url']}")
                    updated_companies.append(company)
                    continue
            
            # Skip if IR URL already exists and is different from company website
            existing_ir = company.get('investor_relations_url', '')
            
            if existing_ir and existing_ir != company_website:
                logger.info(f"  ✓ Already have IR URL: {existing_ir}")
                updated_companies.append(company)
                continue
            
            # Step 2: Find IR page using the two-stage approach
            if company_website:
                ir_url = await self.find_ir_page(
                    company_website,
                    company['ticker'],
                    company.get('company_name', ''),
                    debug=debug
                )
                
                # Update company data
                updated_company = {**company}
                updated_company['investor_relations_url'] = ir_url
                updated_companies.append(updated_company)
            else:
                logger.warning(f"  ⚠ No company website found for {company['ticker']}")
                updated_companies.append(company)
            
            # Be polite to servers
            if i < len(companies):
                await asyncio.sleep(1.0)
        
        return updated_companies


# Enhanced filter function for exact mode
def filter_links_exact_enhanced(links: dict, intent_understanding: dict) -> dict:
    """Enhanced exact mode link filtering using notebook logic"""
    
    keywords = intent_understanding.get('keywords', [])
    
    # If looking for IR pages, use specialized IR scoring
    if any(kw.lower() in ['investor', 'relations', 'ir', 'financial'] for kw in keywords):
        return _filter_links_for_ir(links)
    
    # Otherwise use existing exact filtering
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


def _filter_links_for_ir(links: dict) -> dict:
    """Specialized IR link filtering using notebook scoring logic"""
    extractor = IRExtractorExact()
    filtered_links = {'internal': [], 'external': [], 'navigation': []}
    
    for category in ['internal', 'external', 'navigation']:
        for link in links.get(category, []):
            score = extractor.score_link_for_ir(
                link['url'], 
                link.get('text', ''), 
                link.get('title', '')
            )
            
            if score > 15:  # Minimum threshold for IR links
                filtered_links[category].append({
                    **link,
                    'relevance_score': score,
                    'ir_score': score
                })
        
        filtered_links[category].sort(key=lambda x: x['relevance_score'], reverse=True)
    
    return filtered_links


# Usage example and integration
async def main_example():
    """Example usage of the enhanced IR extractor with CNBC profile extraction"""
    
    # Sample companies data (like from notebook) - with CNBC URLs
    companies = [
        {
            'ticker': 'AAPL',
            'company_name': 'Apple Inc.',
            'cnbc_url': 'https://www.cnbc.com/quotes/AAPL'
        },
        {
            'ticker': 'MSFT',
            'company_name': 'Microsoft Corporation',
            'cnbc_url': 'https://www.cnbc.com/quotes/MSFT'
        },
        {
            'ticker': 'AMZN',
            'company_name': 'Amazon.com Inc.',
            'cnbc_url': 'https://www.cnbc.com/quotes/AMZN'
        }
    ]
    
    extractor = IRExtractorExact()
    
    # This will:
    # 1. Extract company websites from CNBC profile pages
    # 2. Find IR pages using the two-stage approach
    # 3. Return enriched company data
    enriched_companies = await extractor.extract_ir_pages_for_companies(companies, debug=True)
    
    print("\n" + "="*80)
    print("ENHANCED IR EXTRACTION RESULTS")
    print("="*80)
    
    for company in enriched_companies:
        print(f"\n{company['ticker']} - {company.get('company_name', 'N/A')}")
        print(f"  CNBC URL: {company.get('cnbc_url', 'N/A')}")
        print(f"  Company Website: {company.get('company_website', 'N/A')}")
        print(f"  IR Page: {company.get('investor_relations_url', 'N/A')}")


async def extract_from_cnbc_dow30():
    """
    Extract IR pages for all DOW 30 companies from CNBC.
    This function can be used with the crawler's output.
    """
    # This would typically be called with the DOW 30 companies
    # extracted by the crawler from https://www.cnbc.com/dow-30/
    
    # Example: Load DOW 30 companies from crawler output
    import json
    
    try:
        # Try to load from the data directory
        with open('/Users/smatcha/Documents/BigData/investment-report-extractor/data/catalogue/cnbc_companies_full.json', 'r') as f:
            companies = json.load(f)
        
        print(f"Loaded {len(companies)} companies from catalogue")
        
        extractor = IRExtractorExact()
        enriched_companies = await extractor.extract_ir_pages_for_companies(companies, debug=True)
        
        # Save the results
        output_path = '/Users/smatcha/Documents/BigData/investment-report-extractor/data/catalogue/cnbc_companies_with_ir_exact.json'
        with open(output_path, 'w') as f:
            json.dump(enriched_companies, f, indent=2)
        
        print(f"\n✓ Saved {len(enriched_companies)} companies with IR pages to {output_path}")
        
        return enriched_companies
        
    except FileNotFoundError:
        print("No company catalogue found. Run the crawler first to extract DOW 30 companies.")
        return []


if __name__ == "__main__":
    asyncio.run(main_example())