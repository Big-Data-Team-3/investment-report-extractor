#!/usr/bin/env python3
"""
Test the new efficient IR crawler that uses ir_extractor_exact.py logic
"""

import asyncio
import sys
import os
import json
from datetime import datetime

# Add the pipelines directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'pipelines'))

from crawler import crawl_dow30_ir_pages, CrawlerConfig

async def test_efficient_crawler():
    """Test the new efficient IR crawler"""
    print("="*80)
    print("Testing Efficient DOW 30 IR Crawler")
    print("="*80)
    
    # Create config
    config = CrawlerConfig(
        scraping_mode="exact",
        state_file="data/test_crawler_state.json"
    )
    
    # Run the efficient crawler
    results = await crawl_dow30_ir_pages(
        seed_url="https://www.cnbc.com/dow-30/",
        config=config,
        debug=True
    )
    
    # Display results
    print("\n" + "="*80)
    print("RESULTS SUMMARY")
    print("="*80)
    
    companies = results.get('companies', [])
    ir_pages = results.get('ir_pages', [])
    stats = results.get('statistics', {})
    
    print(f"Total Companies: {len(companies)}")
    print(f"IR Pages Found: {len(ir_pages)}")
    print(f"Success Rate: {stats.get('success_rate', 0):.1%}")
    print(f"Duration: {stats.get('total_duration_seconds', 0):.2f} seconds")
    
    # Show sample results
    print("\nSample Companies with IR Pages:")
    for i, company in enumerate(companies[:5], 1):
        print(f"\n{i}. {company['ticker']} - {company.get('company_name', 'N/A')}")
        print(f"   CNBC: {company.get('cnbc_url', 'N/A')}")
        print(f"   Website: {company.get('company_website', 'N/A')}")
        print(f"   IR Page: {company.get('investor_relations_url', 'N/A')}")
    
    # Save test results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    test_file = f"data/test_efficient_crawler_{timestamp}.json"
    
    with open(test_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n✓ Test results saved to: {test_file}")
    
    return results

if __name__ == "__main__":
    asyncio.run(test_efficient_crawler())
