#!/usr/bin/env python3
"""
Docling PDF Parser for IR Documents

This script processes PDF documents from company reports using Docling for intelligent 
document parsing and content extraction. Designed for efficient processing of a small 
sample of documents for testing and validation.

Usage:
    python docling_pdf_parser.py [options]

Examples:
    # Process 2 documents total across all companies
    python docling_pdf_parser.py --sample-size 2
    
    # Process specific company (2 documents from that company)
    python docling_pdf_parser.py --ticker JPM --sample-size 2
    
    # Process specific document types (2 documents total)
    python docling_pdf_parser.py --doc-types "10-K Annual Report,10-Q Quarterly Report" --sample-size 2
"""

import os
import json
import argparse
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
import re

# Data processing
import pandas as pd
import numpy as np

# Docling imports
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class DoclingPDFParser:
    """Efficient PDF parser using Docling for IR documents"""
    
    def __init__(self, reports_dir: str, output_dir: str):
        """Initialize the parser with directories"""
        self.reports_dir = Path(reports_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        # Initialize Docling converter with fallback
        self.converter = self._initialize_converter()
        
        logger.info(f"📁 Reports directory: {self.reports_dir.absolute()}")
        logger.info(f"📁 Output directory: {self.output_dir.absolute()}")
        logger.info("🔧 Docling converter initialized")
    
    def _initialize_converter(self) -> DocumentConverter:
        """Initialize Docling converter with fallback options"""
        try:
            # Try advanced configuration first
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = True
            pipeline_options.do_table_structure = True
            pipeline_options.table_structure_options.do_cell_matching = True
            
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: pipeline_options
                }
            )
            logger.info("✅ Using advanced pipeline options")
            return converter
            
        except Exception as e:
            logger.warning(f"⚠️ Advanced options failed: {e}")
            logger.info("🔄 Falling back to basic configuration...")
            
            # Fallback to basic configuration
            converter = DocumentConverter()
            logger.info("✅ Using basic configuration")
            return converter
    
    def discover_documents(self) -> Dict[str, List[Dict[str, Any]]]:
        """Discover all PDF documents in company folders"""
        documents = {}
        
        for company_dir in self.reports_dir.iterdir():
            if not company_dir.is_dir():
                continue
                
            company_name = company_dir.name
            ticker = company_name.split(' - ')[0]
            
            company_docs = []
            
            # Load metadata if available
            metadata_file = company_dir / "_metadata.json"
            metadata = {}
            if metadata_file.exists():
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
            
            # Find all PDF files
            for file_path in company_dir.glob("*.pdf"):
                doc_info = {
                    'file_path': str(file_path),
                    'file_name': file_path.name,
                    'file_size_mb': file_path.stat().st_size / (1024 * 1024),
                    'ticker': ticker,
                    'company_name': company_name,
                    'metadata': metadata
                }
                
                # Extract document type from filename
                filename_lower = file_path.name.lower()
                if '10-k' in filename_lower or 'annual' in filename_lower:
                    doc_info['document_type'] = '10-K Annual Report'
                elif '10-q' in filename_lower or 'quarterly' in filename_lower:
                    doc_info['document_type'] = '10-Q Quarterly Report'
                elif 'proxy' in filename_lower:
                    doc_info['document_type'] = 'Proxy Statement'
                elif 'press' in filename_lower or 'earnings' in filename_lower:
                    doc_info['document_type'] = 'Press Release'
                elif 'presentation' in filename_lower:
                    doc_info['document_type'] = 'Presentation'
                else:
                    doc_info['document_type'] = 'Other PDF Document'
                
                # Extract year and quarter from filename
                year_match = re.search(r'20(\d{2})', file_path.name)
                quarter_match = re.search(r'[Qq]([1-4])', file_path.name)
                
                doc_info['extracted_year'] = int(year_match.group(1)) + 2000 if year_match else None
                doc_info['extracted_quarter'] = int(quarter_match.group(1)) if quarter_match else None
                
                company_docs.append(doc_info)
            
            if company_docs:
                documents[ticker] = company_docs
        
        return documents
    
    def extract_document_content(self, doc_path: str) -> Dict[str, Any]:
        """Extract content from PDF using Docling with fallback options"""
        try:
            # Try with converter first
            try:
                result = self.converter.convert(doc_path)
            except Exception as e:
                if "backend" in str(e).lower():
                    # Fallback: create a new basic converter
                    basic_converter = DocumentConverter()
                    result = basic_converter.convert(doc_path)
                else:
                    raise e
            
            document = result.document
            
            # Extract text content
            text_content = document.export_to_markdown()
            
            # Extract tables
            tables = []
            try:
                for element in document.iterate_items():
                    if hasattr(element, 'label') and element.label == 'table':
                        table_data = {
                            'caption': getattr(element, 'caption', ''),
                            'content': str(element),
                            'bbox': getattr(element, 'bbox', None)
                        }
                        tables.append(table_data)
            except Exception as e:
                logger.warning(f"Could not extract tables: {e}")
            
            # Extract metadata
            metadata = {
                'title': getattr(document, 'title', ''),
                'page_count': len(document.pages) if hasattr(document, 'pages') else 0,
                'language': getattr(document, 'language', 'en'),
                'creation_date': getattr(document, 'creation_date', None)
            }
            
            # Extract structured elements
            elements = []
            try:
                for element in document.iterate_items():
                    element_info = {
                        'type': type(element).__name__,
                        'label': getattr(element, 'label', ''),
                        'text': str(element)[:200] if hasattr(element, '__str__') else '',
                        'bbox': getattr(element, 'bbox', None)
                    }
                    elements.append(element_info)
            except Exception as e:
                logger.warning(f"Could not extract elements: {e}")
            
            return {
                'success': True,
                'text_content': text_content,
                'tables': tables,
                'metadata': metadata,
                'elements': elements,
                'error': None
            }
            
        except Exception as e:
            logger.error(f"Error processing {doc_path}: {str(e)}")
            return {
                'success': False,
                'text_content': '',
                'tables': [],
                'metadata': {},
                'elements': [],
                'error': str(e)
            }
    
    def extract_financial_metrics(self, text_content: str) -> Dict[str, Any]:
        """Extract financial metrics from text content"""
        metrics = {}
        
        # Revenue patterns
        revenue_patterns = [
            r'revenue[\s:]*\$?([\d,]+(?:\.[\d]+)?)\s*(?:million|billion|m|b)?',
            r'total revenue[\s:]*\$?([\d,]+(?:\.[\d]+)?)\s*(?:million|billion|m|b)?',
            r'net revenue[\s:]*\$?([\d,]+(?:\.[\d]+)?)\s*(?:million|billion|m|b)?'
        ]
        
        # Profit patterns
        profit_patterns = [
            r'net income[\s:]*\$?([\d,]+(?:\.[\d]+)?)\s*(?:million|billion|m|b)?',
            r'profit[\s:]*\$?([\d,]+(?:\.[\d]+)?)\s*(?:million|billion|m|b)?',
            r'earnings[\s:]*\$?([\d,]+(?:\.[\d]+)?)\s*(?:million|billion|m|b)?'
        ]
        
        # Extract revenue
        for pattern in revenue_patterns:
            matches = re.findall(pattern, text_content, re.IGNORECASE)
            if matches:
                metrics['revenue'] = matches[0]
                break
        
        # Extract profit
        for pattern in profit_patterns:
            matches = re.findall(pattern, text_content, re.IGNORECASE)
            if matches:
                metrics['profit'] = matches[0]
                break
        
        return metrics
    
    def filter_documents(self, documents: Dict[str, List[Dict[str, Any]]], 
                        ticker: Optional[str] = None,
                        doc_types: Optional[List[str]] = None,
                        sample_size: int = 2) -> Dict[str, List[Dict[str, Any]]]:
        """Filter documents based on criteria - sample_size is total documents across all companies"""
        filtered = {}
        
        # Filter by ticker
        if ticker:
            if ticker in documents:
                filtered[ticker] = documents[ticker]
            else:
                logger.warning(f"Ticker {ticker} not found in documents")
                return {}
        else:
            filtered = documents
        
        # Filter by document types
        if doc_types:
            for ticker_key, docs in filtered.items():
                filtered[ticker_key] = [
                    doc for doc in docs 
                    if doc['document_type'] in doc_types
                ]
        
        # Collect all documents from all companies
        all_docs = []
        for ticker_key, docs in filtered.items():
            for doc in docs:
                doc['source_ticker'] = ticker_key
                all_docs.append(doc)
        
        # Sort by ticker and document type for consistent selection
        all_docs.sort(key=lambda x: (x['source_ticker'], x['document_type'], x['file_name']))
        
        # Take only the first sample_size documents
        selected_docs = all_docs[:sample_size]
        
        # Group back by ticker
        result = {}
        for doc in selected_docs:
            ticker_key = doc['source_ticker']
            if ticker_key not in result:
                result[ticker_key] = []
            # Remove the temporary source_ticker field
            doc_copy = doc.copy()
            doc_copy.pop('source_ticker', None)
            result[ticker_key].append(doc_copy)
        
        return result
    
    def process_documents(self, documents: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
        """Process documents and extract content"""
        processed_results = {}
        
        for ticker, docs in documents.items():
            logger.info(f"\n🔄 Processing {ticker}: {len(docs)} documents")
            company_results = []
            
            for doc in docs:
                logger.info(f"  📄 Processing: {doc['file_name']}")
                
                # Extract content
                result = self.extract_document_content(doc['file_path'])
                
                # Combine with document info
                doc_result = {
                    'document_info': doc,
                    'extraction_result': result
                }
                
                company_results.append(doc_result)
                
                # Show progress
                if result['success']:
                    logger.info(f"    ✅ Success: {len(result['text_content'])} chars, {len(result['tables'])} tables")
                else:
                    logger.error(f"    ❌ Failed: {result['error']}")
            
            processed_results[ticker] = company_results
        
        return processed_results
    
    def export_results(self, processed_results: Dict[str, List[Dict[str, Any]]]) -> Dict[str, str]:
        """Export processed results to files"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Export full results
        results_file = self.output_dir / f"docling_results_{timestamp}.json"
        with open(results_file, 'w') as f:
            json.dump(processed_results, f, indent=2, default=str)
        
        # Create summary DataFrame
        summary_data = []
        for ticker, docs in processed_results.items():
            for doc_result in docs:
                doc_info = doc_result['document_info']
                extraction = doc_result['extraction_result']
                
                summary_data.append({
                    'ticker': ticker,
                    'document_type': doc_info['document_type'],
                    'year': doc_info['extracted_year'],
                    'quarter': doc_info['extracted_quarter'],
                    'file_name': doc_info['file_name'],
                    'file_size_mb': doc_info['file_size_mb'],
                    'extraction_success': extraction['success'],
                    'text_length': len(extraction['text_content']) if extraction['success'] else 0,
                    'table_count': len(extraction['tables']) if extraction['success'] else 0,
                    'error': extraction['error'] if not extraction['success'] else None
                })
        
        summary_df = pd.DataFrame(summary_data)
        summary_csv = self.output_dir / f"document_summary_{timestamp}.csv"
        summary_df.to_csv(summary_csv, index=False)
        
        # Extract financial metrics
        financial_data = []
        for ticker, docs in processed_results.items():
            for doc_result in docs:
                doc_info = doc_result['document_info']
                extraction = doc_result['extraction_result']
                
                if extraction['success']:
                    metrics = self.extract_financial_metrics(extraction['text_content'])
                    
                    financial_data.append({
                        'ticker': ticker,
                        'document_type': doc_info['document_type'],
                        'year': doc_info['extracted_year'],
                        'quarter': doc_info['extracted_quarter'],
                        'revenue': metrics.get('revenue'),
                        'profit': metrics.get('profit'),
                        'text_length': len(extraction['text_content'])
                    })
        
        financial_df = pd.DataFrame(financial_data)
        financial_csv = self.output_dir / f"financial_metrics_{timestamp}.csv"
        financial_df.to_csv(financial_csv, index=False)
        
        # Log summary
        logger.info(f"\n📋 Document Processing Summary:")
        logger.info(f"Total documents processed: {len(summary_df)}")
        logger.info(f"Successful extractions: {summary_df['extraction_success'].sum()}")
        logger.info(f"Total text extracted: {summary_df['text_length'].sum():,} characters")
        logger.info(f"Total tables extracted: {summary_df['table_count'].sum()}")
        
        logger.info(f"\n✅ Results exported:")
        logger.info(f"  📄 Full results: {results_file}")
        logger.info(f"  📋 Summary CSV: {summary_csv}")
        logger.info(f"  💰 Financial metrics: {financial_csv}")
        
        return {
            'results_file': str(results_file),
            'summary_csv': str(summary_csv),
            'financial_csv': str(financial_csv)
        }


def main():
    """Main function for CLI usage"""
    parser = argparse.ArgumentParser(
        description="Docling PDF Parser for IR Documents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Process 2 documents total across all companies
    python docling_pdf_parser.py --sample-size 2
    
    # Process specific company (2 documents from that company)
    python docling_pdf_parser.py --ticker JPM --sample-size 2
    
    # Process specific document types (2 documents total)
    python docling_pdf_parser.py --doc-types "10-K Annual Report,10-Q Quarterly Report" --sample-size 2
    
    # Process with custom directories
    python docling_pdf_parser.py --reports-dir ../data/reports --output-dir ../data/docling_output --sample-size 2
        """
    )
    
    parser.add_argument(
        '--reports-dir',
        type=str,
        default='../data/reports',
        help='Directory containing company report folders (default: ../data/reports)'
    )
    
    parser.add_argument(
        '--output-dir',
        type=str,
        default='../data/docling_output',
        help='Directory to save output files (default: ../data/docling_output)'
    )
    
    parser.add_argument(
        '--ticker',
        type=str,
        help='Specific ticker symbol to process (e.g., JPM, AAPL)'
    )
    
    parser.add_argument(
        '--doc-types',
        type=str,
        help='Comma-separated document types to process (e.g., "10-K Annual Report,10-Q Quarterly Report")'
    )
    
    parser.add_argument(
        '--sample-size',
        type=int,
        default=2,
        help='Total number of documents to process across all companies (default: 2)'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Parse document types
    doc_types = None
    if args.doc_types:
        doc_types = [dt.strip() for dt in args.doc_types.split(',')]
    
    try:
        # Initialize parser
        parser_instance = DoclingPDFParser(args.reports_dir, args.output_dir)
        
        # Discover documents
        logger.info("🔍 Discovering documents...")
        all_documents = parser_instance.discover_documents()
        
        logger.info(f"📊 Discovered {len(all_documents)} companies with PDF documents")
        total_pdfs = sum(len(docs) for docs in all_documents.values())
        logger.info(f"📄 Total PDF documents: {total_pdfs}")
        
        # Filter documents
        filtered_documents = parser_instance.filter_documents(
            all_documents,
            ticker=args.ticker,
            doc_types=doc_types,
            sample_size=args.sample_size
        )
        
        if not filtered_documents:
            logger.error("❌ No documents found matching criteria")
            return
        
        logger.info(f"📄 Processing {len(filtered_documents)} companies with filtered documents")
        
        # Process documents
        processed_results = parser_instance.process_documents(filtered_documents)
        
        # Export results
        output_files = parser_instance.export_results(processed_results)
        
        logger.info("\n🚀 Docling PDF Parser Complete!")
        logger.info(f"✅ Processed documents from {len(processed_results)} companies")
        
    except Exception as e:
        logger.error(f"❌ Error: {str(e)}")
        raise


if __name__ == "__main__":
    main()
