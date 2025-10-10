#!/usr/bin/env python3
"""
Test script for the enhanced IR extractor.
Demonstrates the complete workflow from CNBC profile to IR page.
"""

import asyncio
import sys
import os

# Add the pipelines directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'pipelines'))

from ir_extractor_exact import IRExtractorExact

async def test_single_company():
    """Test IR extraction for a single company"""
    print("="*80)
    print("Testing IR Extraction for Apple Inc.")
    print("="*80)
    
    # Test with just CNBC URL (like the crawler would provide)
    company = {
        'ticker': 'AAPL',
        'company_name': 'Apple Inc.',
        'cnbc_url': 'https://www.cnbc.com/quotes/AAPL'
    }
    
    extractor = IRExtractorExact()
    
    # Step 1: Extract company website from CNBC profile
    print("\nStep 1: Extracting company website from CNBC profile...")
    website_info = await extractor.extract_company_website_from_cnbc(
        company['cnbc_url'],
        company['ticker'],
        company['company_name'],
        debug=True
    )
    
    print(f"\nResults from CNBC profile:")
    print(f"  Company Website: {website_info['company_website']}")
    print(f"  IR URL: {website_info['investor_relations_url']}")
    
    # Step 2: If no IR URL found, use the two-stage approach
    if not website_info['investor_relations_url'] and website_info['company_website']:
        print(f"\nStep 2: Using two-stage approach to find IR page...")
        ir_url = await extractor.find_ir_page(
            website_info['company_website'],
            company['ticker'],
            company['company_name'],
            debug=True
        )
        print(f"\nFinal IR URL: {ir_url}")
    else:
        print(f"\nIR URL found directly from CNBC profile!")

async def test_multiple_companies():
    """Test IR extraction for multiple companies"""
    print("\n" + "="*80)
    print("Testing IR Extraction for Multiple Companies")
    print("="*80)
    
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
        }
    ]
    
    extractor = IRExtractorExact()
    enriched_companies = await extractor.extract_ir_pages_for_companies(companies, debug=True)
    
    print("\n" + "="*80)
    print("FINAL RESULTS")
    print("="*80)
    
    for company in enriched_companies:
        print(f"\n{company['ticker']} - {company.get('company_name', 'N/A')}")
        print(f"  CNBC URL: {company.get('cnbc_url', 'N/A')}")
        print(f"  Company Website: {company.get('company_website', 'N/A')}")
        print(f"  IR Page: {company.get('investor_relations_url', 'N/A')}")

async def main():
    """Main test function"""
    print("Enhanced IR Extractor Test")
    print("This demonstrates the complete workflow from the notebook:")
    print("1. Extract company website from CNBC profile page")
    print("2. Find IR page using two-stage approach (subdomain probing + main site scraping)")
    
    # Test single company first
    await test_single_company()
    
    # Test multiple companies
    await test_multiple_companies()

if __name__ == "__main__":
    asyncio.run(main())
