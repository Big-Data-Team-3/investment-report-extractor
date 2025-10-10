from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime
import sys
from pathlib import Path
import json
import os
import logging
# Add parent directory to path to import pipelines module
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import crawler, ir_doc_extract
from airflow.providers.google.cloud.hooks.gcs import GCSHook

# Define default arguments
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2025, 10, 10),
    'retries': 1,
}

# Define the DAG
with DAG(
    'ir_extraction_dag',
    default_args=default_args,
    description='An ETL pipeline for IR document extraction',
    schedule=None,
    catchup=False,
) as dag:
    
    # Task 1: Crawl DOW 30 IR pages
    def crawl_dow30(**context):
        """Crawl DOW 30 IR pages with Airflow logging"""
        task_logger = logging.getLogger('airflow.task')
        task_logger.info("Starting DOW 30 IR pages crawl...")
        
        try:
            result = crawler.crawl_dow30_ir_pages()
            task_logger.info(f"✅ Crawl completed successfully: {result}")
            return result
        except Exception as e:
            task_logger.error(f"❌ Crawl failed: {str(e)}", exc_info=True)
            raise

    crawl_task = PythonOperator(
        task_id='crawl_dow30',
        python_callable=crawl_dow30,
        provide_context=True
    )
    
    # Task 2: Extract documents
    def extract_documents(**context):
        """Extract documents with Airflow logging"""
        task_logger = logging.getLogger('airflow.task')
        task_logger.info("Starting document extraction...")
        
        input_path = 'data/dow30_ir_pages_latest.json'
        output_path = 'data/documents/all_companies.json'
        
        task_logger.info(f"Input: {input_path}")
        task_logger.info(f"Output: {output_path}")

        try:
            result = ir_doc_extract.extract_all_companies(
                input_json_path=input_path,
                output_json_path=output_path
            )
            task_logger.info(f"✅ Extraction completed successfully: {result}")
            return result
        except Exception as e:
            task_logger.error(f"❌ Extraction failed: {str(e)}", exc_info=True)
            raise
        

    extract_task = PythonOperator(
        task_id='extract_documents',
        python_callable=extract_documents,
        provide_context=True
    )

    # Task 3: Upload to GCS using GCSHook
    def upload_to_gcs(**context):
        """Upload extracted documents to GCS bucket with Airflow logging"""
        task_logger = logging.getLogger('airflow.task')
        task_logger.info("Starting document upload to GCS...")
        
        """Upload extracted documents to GCS bucket"""
        # Configuration
        bucket_name = 'investment-docs-7245-03'
        source_file = 'data/documents/all_companies.json'
        destination_blob = f'documents/{datetime.now().strftime("%Y%m%d_%H%M%S")}/all_companies.json'
        gcp_conn_id = 'google_cloud_default'  # Your GCP connection ID in Airflow
        
        # Check if source file exists
        if not os.path.exists(source_file):
            raise FileNotFoundError(f"Source file not found: {source_file}")
        
        task_logger.info(f"Source file: {source_file}")
        task_logger.info(f"Destination blob: {destination_blob}")
        task_logger.info(f"GCP connection ID: {gcp_conn_id}")

        # Initialize GCS Hook
        gcs_hook = GCSHook(gcp_conn_id=gcp_conn_id)
        
        # Upload file to GCS
        gcs_hook.upload(
            bucket_name=bucket_name,
            object_name=destination_blob,
            filename=source_file,
            mime_type='application/json'
        )
        
        task_logger.info(f"✅ Successfully uploaded {source_file} to gs://{bucket_name}/{destination_blob}")
        
        # Optionally, also upload with a 'latest' version for easy access
        latest_blob = 'documents/latest/all_companies.json'
        gcs_hook.upload(
            bucket_name=bucket_name,
            object_name=latest_blob,
            filename=source_file,
            mime_type='application/json'
        )
        
        task_logger.info(f"✅ Successfully uploaded latest version to gs://{bucket_name}/{latest_blob}")
        
        return {
            'bucket': bucket_name,
            'timestamped_path': destination_blob,
            'latest_path': latest_blob
        }

    upload_to_gcs_task = PythonOperator(
        task_id='upload_to_gcs',
        python_callable=upload_to_gcs,
        provide_context=True
    )

    # Define task dependencies
    crawl_task >> extract_task >> upload_to_gcs_task
