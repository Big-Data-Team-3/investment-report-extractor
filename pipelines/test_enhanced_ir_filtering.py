#!/usr/bin/env python3
"""
Test script to verify the enhanced IR filtering is working correctly.
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'pipelines'))

from _filters import (
    _is_ir_related_intent, 
    filter_exact_mode_enhanced_ir,
    filter_links_exact_enhanced_ir,
    filter_documents_exact_ir_focused
)

def test_ir_intent_detection():
    """Test IR intent detection"""
    print("Testing IR intent detection...")
    
    # Test cases
    test_cases = [
        {
            'intent': {'keywords': ['investor', 'relations'], 'expanded_intent': 'Find investor relations pages'},
            'expected': True,
            'description': 'Direct IR keywords'
        },
        {
            'intent': {'keywords': ['financial', 'documents'], 'expanded_intent': 'Get financial reports'},
            'expected': True,
            'description': 'Financial keywords'
        },
        {
            'intent': {'keywords': ['product', 'catalog'], 'expanded_intent': 'Find product information'},
            'expected': False,
            'description': 'Non-IR keywords'
        },
        {
            'intent': {'keywords': [], 'expanded_intent': 'Looking for SEC filings and earnings reports'},
            'expected': True,
            'description': 'IR keywords in expanded intent'
        }
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        result = _is_ir_related_intent(test_case['intent'])
        status = "✓" if result == test_case['expected'] else "✗"
        print(f"  Test {i}: {status} {test_case['description']} - Expected: {test_case['expected']}, Got: {result}")

def test_ir_link_filtering():
    """Test enhanced IR link filtering"""
    print("\nTesting IR link filtering...")
    
    # Sample links data
    sample_links = {
        'internal': [
            {'url': 'https://investor.apple.com/financials', 'text': 'Investor Relations', 'title': ''},
            {'url': 'https://apple.com/about', 'text': 'About Us', 'title': ''},
            {'url': 'https://apple.com/investors/earnings', 'text': 'Quarterly Earnings', 'title': ''},
            {'url': 'https://apple.com/products', 'text': 'Products', 'title': ''},
            {'url': 'https://ir.apple.com/sec-filings', 'text': '10-K Annual Report', 'title': ''},
        ],
        'external': [],
        'navigation': []
    }
    
    # Test filtering
    filtered = filter_links_exact_enhanced_ir(sample_links, ['financial'])
    
    print(f"  Original links: {len(sample_links['internal'])}")
    print(f"  Filtered links: {len(filtered['internal'])}")
    
    print("  Top filtered links:")
    for i, link in enumerate(filtered['internal'][:3], 1):
        print(f"    {i}. {link['text']} (Score: {link['relevance_score']}, IR: {link['ir_score']})")
        print(f"       URL: {link['url']}")
        print(f"       Patterns: {link.get('matched_patterns', [])}")

def test_ir_document_filtering():
    """Test IR document filtering"""
    print("\nTesting IR document filtering...")
    
    # Sample documents
    sample_docs = [
        {'url': 'https://example.com/annual-report-2023.pdf', 'text': 'Annual Report 2023', 'type': 'PDF'},
        {'url': 'https://example.com/product-catalog.pdf', 'text': 'Product Catalog', 'type': 'PDF'},
        {'url': 'https://example.com/10-k-filing.pdf', 'text': '10-K SEC Filing', 'type': 'PDF'},
        {'url': 'https://example.com/earnings-transcript.pdf', 'text': 'Q3 Earnings Call Transcript', 'type': 'PDF'},
        {'url': 'https://example.com/marketing-brochure.pdf', 'text': 'Marketing Brochure', 'type': 'PDF'},
    ]
    
    # Test filtering
    filtered = filter_documents_exact_ir_focused(sample_docs, ['quarterly'])
    
    print(f"  Original documents: {len(sample_docs)}")
    print(f"  Filtered documents: {len(filtered)}")
    
    print("  Top filtered documents:")
    for i, doc in enumerate(filtered[:3], 1):
        print(f"    {i}. {doc['text']} (Score: {doc['relevance_score']}, IR: {doc['ir_score']})")
        print(f"       IR Keywords: {doc.get('matched_ir_keywords', [])}")

def test_full_enhanced_mode():
    """Test the full enhanced IR mode"""
    print("\nTesting full enhanced IR mode...")
    
    # Sample parsed content
    sample_content = {
        'title': 'Apple Inc. - Investor Relations',
        'links': {
            'internal': [
                {'url': 'https://investor.apple.com/financials', 'text': 'Financial Information', 'title': ''},
                {'url': 'https://apple.com/products/iphone', 'text': 'iPhone', 'title': ''},
            ]
        },
        'documents': [
            {'url': 'https://example.com/10-q.pdf', 'text': '10-Q Report', 'type': 'PDF'},
            {'url': 'https://example.com/brochure.pdf', 'text': 'Company Brochure', 'type': 'PDF'},
        ],
        'tables': [],
        'content': {},
        'structure': {},
        'images': []
    }
    
    # Sample intent understanding
    intent_understanding = {
        'keywords': ['investor', 'financial'],
        'target_content_types': ['links', 'documents'],
        'expanded_intent': 'Find investor relations documents and financial information'
    }
    
    # Test full filtering
    result = filter_exact_mode_enhanced_ir(sample_content, intent_understanding)
    
    print(f"  Filtered links: {len(result['links']['internal'])}")
    print(f"  Filtered documents: {len(result['documents'])}")
    
    if result['links']['internal']:
        top_link = result['links']['internal'][0]
        print(f"  Top link: {top_link['text']} (Score: {top_link['relevance_score']})")
    
    if result['documents']:
        top_doc = result['documents'][0]
        print(f"  Top document: {top_doc['text']} (Score: {top_doc['relevance_score']})")

if __name__ == "__main__":
    print("Enhanced IR Filtering Test Suite")
    print("=" * 50)
    
    test_ir_intent_detection()
    test_ir_link_filtering()
    test_ir_document_filtering()
    test_full_enhanced_mode()
    
    print("\n" + "=" * 50)
    print("Test suite completed!")
