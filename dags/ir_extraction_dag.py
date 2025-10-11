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
    
    # ============ DUMMY TEST TASKS ============
    
    # Dummy Task 1: Test basic logging and context
    def test_basic_task(**context):
        """Simple test task to verify Airflow is working"""
        task_logger = logging.getLogger('airflow.task')
        task_logger.info("🧪 TEST 1: Basic task execution started")
        task_logger.info(f"Execution date: {context.get('ds')}")
        task_logger.info(f"Task instance: {context.get('task_instance')}")
        task_logger.info(f"DAG: {context.get('dag')}")
        
        # Test simple operations
        test_data = {
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'message': 'Airflow is working correctly!'
        }
        
        task_logger.info(f"Test data: {json.dumps(test_data, indent=2)}")
        task_logger.info("✅ TEST 1: Basic task completed successfully")
        
        return test_data
    
    test_basic_task_op = PythonOperator(
        task_id='test_basic_task',
        python_callable=test_basic_task
    )
    
    # Dummy Task 2: Test file system and module imports
    def test_filesystem_and_imports(**context):
        """Test file system access and pipeline module imports"""
        task_logger = logging.getLogger('airflow.task')
        task_logger.info("🧪 TEST 2: File system and imports test started")
        
        # Test file system access
        task_logger.info("Testing file system access...")
        current_dir = os.getcwd()
        task_logger.info(f"Current directory: {current_dir}")
        
        # Check if data directories exist
        data_dir = 'data'
        if os.path.exists(data_dir):
            task_logger.info(f"✅ Data directory exists: {data_dir}")
            # List contents
            contents = os.listdir(data_dir)
            task_logger.info(f"Contents: {contents[:10]}")  # Show first 10 items
        else:
            task_logger.warning(f"⚠️ Data directory does not exist: {data_dir}")
            task_logger.info("Creating data directory...")
            os.makedirs(data_dir, exist_ok=True)
            task_logger.info(f"✅ Created: {data_dir}")
        
        # Check pipelines directory
        pipelines_dir = 'pipelines'
        if os.path.exists(pipelines_dir):
            task_logger.info(f"✅ Pipelines directory exists: {pipelines_dir}")
            pipeline_files = [f for f in os.listdir(pipelines_dir) if f.endswith('.py')]
            task_logger.info(f"Pipeline files: {pipeline_files}")
        else:
            task_logger.error(f"❌ Pipelines directory not found: {pipelines_dir}")
        
        # Test module imports
        task_logger.info("Testing module imports...")
        try:
            import pipelines
            task_logger.info(f"✅ Pipelines module imported: {pipelines.__file__}")
            
            from pipelines import crawler
            task_logger.info(f"✅ Crawler module imported: {crawler.__file__}")
            
            from pipelines import ir_doc_extract
            task_logger.info(f"✅ IR extract module imported: {ir_doc_extract.__file__}")
            
        except Exception as e:
            task_logger.error(f"❌ Module import failed: {str(e)}", exc_info=True)
            raise
        
        # Test creating a dummy file
        task_logger.info("Testing file write capability...")
        test_file = 'data/test_write.json'
        test_content = {
            'test': 'successful',
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            with open(test_file, 'w') as f:
                json.dump(test_content, f, indent=2)
            task_logger.info(f"✅ Successfully wrote test file: {test_file}")
            
            # Read it back
            with open(test_file, 'r') as f:
                read_content = json.load(f)
            task_logger.info(f"✅ Successfully read test file: {read_content}")
            
            # Clean up
            os.remove(test_file)
            task_logger.info(f"✅ Cleaned up test file")
            
        except Exception as e:
            task_logger.error(f"❌ File operation failed: {str(e)}", exc_info=True)
            raise
        
        task_logger.info("✅ TEST 2: File system and imports test completed successfully")
        
        return {
            'status': 'success',
            'current_dir': current_dir,
            'data_dir_exists': os.path.exists(data_dir),
            'pipelines_dir_exists': os.path.exists(pipelines_dir)
        }
    
    test_filesystem_task_op = PythonOperator(
        task_id='test_filesystem_and_imports',
        python_callable=test_filesystem_and_imports
    )
    
    # ============ END OF DUMMY TEST TASKS ============
    
    # Task 1: Crawl DOW 30 IR pages
    def crawl_dow30(**context):
        """Crawl DOW 30 IR pages with Airflow logging"""
        import asyncio
        task_logger = logging.getLogger('airflow.task')
        task_logger.info("Starting DOW 30 IR pages crawl...")
        task_logger.info("🧪 Running in TEST MODE - processing only first 3 companies for quick testing")
        
        try:
            # Run the async crawler with test=True for quick testing
            result = asyncio.run(crawler.crawl_dow30_ir_pages(test=True, debug=True))
            task_logger.info(f"✅ Crawl completed successfully")
            task_logger.info(f"Companies processed: {len(result.get('companies', []))}")
            task_logger.info(f"IR pages found: {len(result.get('ir_pages', []))}")
            return result
        except Exception as e:
            task_logger.error(f"❌ Crawl failed: {str(e)}", exc_info=True)
            raise

    crawl_task = PythonOperator(
        task_id='crawl_dow30',
        python_callable=crawl_dow30
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
        python_callable=extract_documents
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
        python_callable=upload_to_gcs
    )

    # Define task dependencies
    # Test tasks run first in parallel, then the actual pipeline
    test_basic_task_op >> test_filesystem_task_op >> crawl_task >> extract_task >> upload_to_gcs_task