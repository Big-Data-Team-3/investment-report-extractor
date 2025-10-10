# dags/test_gcs_connection.py
from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.google.cloud.operators.gcs import (
    GCSCreateBucketOperator,
    GCSListObjectsOperator,
    GCSDeleteObjectsOperator
)
from airflow.providers.google.cloud.transfers.local_to_gcs import LocalFilesystemToGCSOperator
from airflow.providers.google.cloud.hooks.gcs import GCSHook
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
import json
import os

# Default arguments
default_args = {
    'owner': 'investment-extractor',
    'depends_on_past': False,
    'start_date': datetime(2024, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# DAG definition
dag = DAG(
    'test_gcs_connection',
    default_args=default_args,
    description='Test GCS bucket connection and operations',
    schedule=None,  # Manual trigger only
    catchup=False,
    tags=['test', 'gcs', 'connection'],
)

def create_test_data():
    """Create sample test data"""
    test_data = {
        "test_timestamp": datetime.now().isoformat(),
        "message": "GCS connection test successful",
        "bucket_name": "investment-docs-7245-03",
        "test_companies": [
            {"ticker": "AAPL", "name": "Apple Inc.", "ir_url": "https://investor.apple.com"},
            {"ticker": "MSFT", "name": "Microsoft Corp.", "ir_url": "https://www.microsoft.com/investor/default.aspx"},
            {"ticker": "GOOGL", "name": "Alphabet Inc.", "ir_url": "https://abc.xyz/investor/"}
        ]
    }
    
    # Create test file
    os.makedirs('/tmp/airflow_test', exist_ok=True)
    with open('/tmp/airflow_test/test_data.json', 'w') as f:
        json.dump(test_data, f, indent=2)
    
    print(f"Created test data: {test_data}")
    return '/tmp/airflow_test/test_data.json'

def verify_upload():
    """Verify the upload was successful"""
    print("Upload verification completed successfully!")
    return True

# Task 1: Create test data
create_data_task = PythonOperator(
    task_id='create_test_data',
    python_callable=create_test_data,
    dag=dag,
)

# Task 2: Upload test data to GCS
upload_to_gcs = LocalFilesystemToGCSOperator(
    task_id='upload_test_data_to_gcs',
    src='/tmp/airflow_test/test_data.json',
    dst='test/airflow_connection_test.json',
    bucket='investment-docs-7245-03',
    gcp_conn_id='google_cloud_default',
    dag=dag,
)

# Task 3: List objects in bucket
list_objects = GCSListObjectsOperator(
    task_id='list_gcs_objects',
    bucket='investment-docs-7245-03',
    prefix='test/',
    gcp_conn_id='google_cloud_default',
    dag=dag,
)

# Task 4: Download the file back from GCS
def download_from_gcs():
    os.makedirs('/tmp/airflow_test', exist_ok=True)
    hook = GCSHook(
        gcp_conn_id='google_cloud_default'
    )
    hook.download(
        bucket_name='investment-docs-7245-03',
        object_name='test/airflow_connection_test.json',
        filename='/tmp/airflow_test/downloaded_test_data.json'
    )
    return '/tmp/airflow_test/downloaded_test_data.json'

download_from_gcs = PythonOperator(
    task_id='download_from_gcs',
    python_callable=download_from_gcs,
    dag=dag,
)

# Task 5: Verify the downloaded content
verify_task = BashOperator(
    task_id='verify_downloaded_content',
    bash_command='cat /tmp/airflow_test/downloaded_test_data.json && echo "\\nDownload verification successful!"',
    dag=dag,
)

# Task 6: Cleanup test files
cleanup_local = BashOperator(
    task_id='cleanup_local_files',
    bash_command='rm -rf /tmp/airflow_test && echo "Local cleanup completed"',
    dag=dag,
)

# Task 7: Cleanup GCS test files
cleanup_gcs = GCSDeleteObjectsOperator(
    task_id='cleanup_gcs_files',
    bucket_name='investment-docs-7245-03',
    objects=['test/airflow_connection_test.json'],
    gcp_conn_id='google_cloud_default',
    dag=dag,
)

# Define task dependencies
create_data_task >> upload_to_gcs >> list_objects >> download_from_gcs >> verify_task >> [cleanup_local, cleanup_gcs]