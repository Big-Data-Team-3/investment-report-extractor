# Investment Report Extractor Architecture

This document describes the architecture of the Investment Report Extractor system, which automates the process of extracting, processing, and storing financial documents from company investor relations websites.

## Architecture Overview

The system follows a multi-stage pipeline architecture with the following key components:

### 1. Data Sources
- **CNBC DOW 30 Index**: Provides the initial list of companies to process
- **Company IR Websites**: Source of financial documents (PDFs, Excel files, presentations, etc.)

### 2. Data Processing Pipeline

#### Stage 1: Crawler (`crawler.py`)
- Extracts ticker symbols and company information from CNBC DOW 30 index
- Discovers investor relations URLs for each company
- Implements queue-based crawling with BFS navigation
- Manages priority-based URL processing
- Handles rate limiting and retry logic

#### Stage 2: Document Extractor (`ir_doc_extract.py`)
- Downloads financial documents from company IR websites
- Extracts metadata (document type, publication date, etc.)
- Classifies documents (10-K, 10-Q, Press Releases, etc.)
- Deduplicates files to keep only the latest versions
- Organizes files in company-wise folder structure

#### Stage 3: PDF Parser (`docling_pdf_parser.py`)
- Parses PDF documents using Docling library
- Extracts structured text content
- Identifies and extracts tables
- Performs OCR processing for scanned documents
- Generates structured data outputs

### 3. Orchestration
- **Apache Airflow**: Manages the entire pipeline
- Schedules jobs and monitors execution
- Handles failures and implements retry logic
- Provides observability and logging

### 4. Storage Layer

#### Google Cloud Storage (GCS)
- **Raw Documents Bucket**: Stores original downloaded files from ir_doc_extract
- **Structured Data Bucket**: Stores processed and structured data from docling_pdf_parser

### 5. Output Formats
- **JSON Files**: Document metadata, financial metrics, and company information
- **CSV Files**: Summary data, financial tables, and processing statistics
- **Markdown Files**: Structured text content and document tables

## Data Flow

1. **Stage 1 - Discovery**: Airflow DAG orchestrates crawler to extract company list from CNBC and find IR URLs
2. **Stage 2 - Extraction**: Airflow DAG orchestrates document extractor to download files and store them in GCS Raw Documents Bucket
3. **Stage 3 - Processing**: Airflow DAG retrieves files from GCS Raw Documents Bucket, orchestrates PDF parser to process files and generate structured data
4. **Stage 4 - Storage**: Airflow DAG stores processed data back to GCS Structured Data Bucket in multiple formats

**Note**: All storage operations are handled by Airflow DAGs - no local storage is involved in the pipeline.

## Key Features

- **Scalable Architecture**: Queue-based processing with configurable concurrency
- **Fault Tolerance**: Retry logic and error handling at each stage
- **Data Quality**: Deduplication and validation of extracted content
- **Multiple Output Formats**: JSON, CSV, and Markdown for different analytical needs
- **Cloud Integration**: GCS for scalable storage and Airflow for orchestration

## Technology Stack

- **Python**: Core processing language
- **Docling**: PDF parsing and content extraction
- **Apache Airflow**: Workflow orchestration
- **Google Cloud Storage**: Scalable file storage
- **Selenium**: Web scraping and dynamic content handling
- **BeautifulSoup**: HTML parsing
- **Pandas**: Data manipulation and analysis

## Usage

To generate the architecture diagram:

```bash
cd docs
python diagram.py
```

This will create `investment_report_extractor_architecture.png` showing the complete system architecture with data flow connections.
