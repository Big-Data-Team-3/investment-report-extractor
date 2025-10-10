#!/usr/bin/env python3
"""
Investment Report Extractor Architecture Diagram

This script creates a comprehensive architecture diagram showing the data flow
from ticker extraction through document parsing and storage.
"""

from diagrams import Diagram, Cluster, Edge
from diagrams.gcp.storage import GCS
from diagrams.gcp.compute import ComputeEngine
from diagrams.programming.language import Python
from diagrams.onprem.workflow import Airflow
from diagrams.onprem.client import Users
from diagrams.onprem.network import Internet
from diagrams.onprem.database import MongoDB
from diagrams.onprem.analytics import Spark
from diagrams.generic.compute import Rack
from diagrams.generic.storage import Storage
from diagrams.generic.database import SQL
from diagrams.generic.blank import Blank

def create_architecture_diagram():
    """Create the investment report extractor architecture diagram"""
    
    with Diagram("Investment Report Extractor Architecture", 
                 filename="investment_report_extractor_architecture",
                 show=False,
                 direction="TB"):
        
        # External Data Sources
        with Cluster("External Data Sources"):
            cnbc = Internet("CNBC DOW 30\nIndex")
            company_sites = Internet("Company\nIR Websites")
        
        # Data Processing Components
        with Cluster("Data Processing Pipeline"):
            # Crawler Component
            crawler = Python("crawler.py\n• Extract tickers\n• Find IR URLs\n• Queue management\n• BFS navigation")
            
            # Document Extractor
            doc_extractor = Python("ir_doc_extract.py\n• Download documents\n• Extract metadata\n• Classify documents\n• Deduplicate files")
            
            # PDF Parser
            pdf_parser = Python("docling_pdf_parser.py\n• Parse PDFs\n• Extract text\n• Extract tables\n• Structure data\n• OCR processing")
        
        # Orchestration
        with Cluster("Orchestration"):
            airflow = Airflow("Apache Airflow\n• Schedule jobs\n• Monitor pipeline\n• Handle failures\n• Retry logic")
        
        # Storage Layer
        with Cluster("Storage Layer"):
            # GCS Buckets
            raw_docs_bucket = GCS("Raw Documents\nBucket\n• PDF files\n• Excel files\n• Audio files")
            structured_data_bucket = GCS("Structured Data\nBucket\n• JSON metadata\n• CSV summaries\n• Markdown content")
        
        # Output Formats
        with Cluster("Output Formats"):
            json_output = Storage("JSON Files\n• Document metadata\n• Financial metrics\n• Company info")
            csv_output = Storage("CSV Files\n• Summary data\n• Financial tables\n• Processing stats")
            markdown_output = Storage("Markdown Files\n• Structured text\n• Document content\n• Tables")
        
        # Data Flow Connections
        cnbc >> Edge(label="DOW 30 companies\nlist", color="blue") >> crawler
        company_sites >> Edge(label="IR pages\n& documents", color="blue") >> crawler
        
        crawler >> Edge(label="Tickers & URLs\nmetadata", color="green") >> doc_extractor
        
        # Airflow DAG orchestrates all stages
        #airflow >> Edge(label="DAG Stage 1\nOrchestrates", color="red") >> crawler
        #airflow >> Edge(label="DAG Stage 2\nOrchestrates", color="red") >> doc_extractor
        #airflow >> Edge(label="DAG Stage 3\nOrchestrates", color="red") >> pdf_parser
        
        # Stage 2: Airflow DAG stores files to GCS
        doc_extractor >> Edge(label="Downloaded files\n(PDF, Excel, etc.)", color="green") >> airflow
        airflow >> Edge(label="DAG stores files\nin GCS", color="green") >> raw_docs_bucket
        
        # Stage 3: Airflow DAG retrieves files from GCS for processing
        raw_docs_bucket >> Edge(label="DAG retrieves\nPDF files", color="orange") >> airflow
        airflow >> Edge(label="Files to\nPDF Parser", color="orange") >> pdf_parser
        
        # Stage 4: Airflow DAG stores processed data back to GCS
        pdf_parser >> Edge(label="Structured data\nvia DAG", color="purple") >> airflow
        airflow >> Edge(label="DAG stores processed\ndata in GCS", color="purple") >> structured_data_bucket
        
        # Final output formats
        pdf_parser >> Edge(label="Processed data", color="purple") >> json_output
        pdf_parser >> Edge(label="Summary data", color="purple") >> csv_output
        pdf_parser >> Edge(label="Text content", color="purple") >> markdown_output

if __name__ == "__main__":
    create_architecture_diagram()
    print("Architecture diagram created successfully!")
