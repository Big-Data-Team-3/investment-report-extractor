# dags/ir_extraction_tasks.py
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from google.cloud import storage
from google.oauth2 import service_account

# Import your existing classes
from playwright_ir_doc import PlaywrightIRExtractor, DocumentDownloader, DocumentInfo

def extract_company_documents(ticker: str, company_name: str, ir_url: str, **context):
    """Extract document metadata for a specific company"""
    print(f"Starting extraction for {ticker} - {company_name}")
    print(f"IR URL: {ir_url}")
    
    async def _extract():
        async with PlaywrightIRExtractor(debug=True) as extractor:
            documents = await extractor.extract_documents(ir_url, ticker, company_name)
            
            # Add metadata
            for doc in documents:
                doc.metadata['ticker'] = ticker
                doc.metadata['company'] = company_name
                doc.metadata['extraction_date'] = datetime.now().isoformat()
            
            return documents
    
    # Run the async extraction
    documents = asyncio.run(_extract())
    
    # Save results to Airflow's temp directory
    output_dir = Path(f"/tmp/airflow/{ticker.lower()}")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save document metadata
    metadata_file = output_dir / "documents_metadata.json"
    with open(metadata_file, 'w') as f:
        json.dump([doc.__dict__ for doc in documents], f, indent=2, default=str)
    
    print(f"Extracted {len(documents)} documents for {ticker}")
    print(f"Metadata saved to: {metadata_file}")
    
    return {
        'ticker': ticker,
        'company_name': company_name,
        'documents_count': len(documents),
        'metadata_file': str(metadata_file),
        'status': 'success'
    }

def download_company_documents(ticker: str, company_name: str, **context):
    """Download documents for a specific company"""
    print(f"Starting download for {ticker} - {company_name}")
    
    # Load document metadata from previous task
    metadata_file = Path(f"/tmp/airflow/{ticker.lower()}/documents_metadata.json")
    
    if not metadata_file.exists():
        raise FileNotFoundError(f"Metadata file not found: {metadata_file}")
    
    with open(metadata_file, 'r') as f:
        documents_data = json.load(f)
    
    # Convert back to DocumentInfo objects
    documents = []
    for doc_data in documents_data:
        doc = DocumentInfo(**doc_data)
        documents.append(doc)
    
    # Download documents
    downloader = DocumentDownloader(base_output_dir=f"/tmp/airflow/{ticker.lower()}/downloads")
    stats = downloader.download_company_documents(ticker, company_name, documents)
    
    print(f"Download completed for {ticker}")
    print(f"Downloaded: {stats['downloaded']}, Failed: {stats['failed']}")
    
    return {
        'ticker': ticker,
        'company_name': company_name,
        'download_stats': stats,
        'status': 'success'
    }

def upload_company_documents_to_gcs(ticker: str, company_name: str, **context):
    """Upload downloaded documents to Google Cloud Storage"""
    print(f"Starting GCS upload for {ticker} - {company_name}")
    
    # Initialize GCS client
    key_file_path = '/opt/airflow/config/gcs-key.json'
    credentials = service_account.Credentials.from_service_account_file(key_file_path)
    client = storage.Client(credentials=credentials)
    
    # Define bucket and paths
    bucket_name = 'investment-docs-7245-03'  # Replace with your bucket name
    bucket = client.bucket(bucket_name)
    
    # Upload files
    downloads_dir = Path(f"/tmp/airflow/{ticker.lower()}/downloads")
    company_folder = f"{ticker} - {company_name}"
    
    uploaded_files = []
    
    if downloads_dir.exists():
        for file_path in downloads_dir.rglob('*'):
            if file_path.is_file() and file_path.name != '_metadata.json':
                # Create GCS path
                gcs_path = f"companies/{company_folder}/{file_path.name}"
                blob = bucket.blob(gcs_path)
                
                # Upload file
                blob.upload_from_filename(str(file_path))
                uploaded_files.append(gcs_path)
                print(f"Uploaded: {gcs_path}")
    
    # Upload metadata
    metadata_file = Path(f"/tmp/airflow/{ticker.lower()}/documents_metadata.json")
    if metadata_file.exists():
        gcs_metadata_path = f"companies/{company_folder}/_metadata.json"
        blob = bucket.blob(gcs_metadata_path)
        blob.upload_from_filename(str(metadata_file))
        uploaded_files.append(gcs_metadata_path)
    
    print(f"GCS upload completed for {ticker}")
    print(f"Uploaded {len(uploaded_files)} files")
    
    return {
        'ticker': ticker,
        'company_name': company_name,
        'uploaded_files': uploaded_files,
        'status': 'success'
    }