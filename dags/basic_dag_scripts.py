"""
Airflow DAG Tutorial - Part 1: Basic Concepts
==============================================

A DAG (Directed Acyclic Graph) is a collection of tasks with dependencies.
Place this file in your dags/ folder.
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.providers.google.cloud.operators.gcs import (
    GCSCreateBucketOperator,
    GCSListObjectsOperator,
    GCSDeleteObjectsOperator
)
from airflow.providers.google.cloud.transfers.local_to_gcs import LocalFilesystemToGCSOperator
from airflow.providers.google.cloud.hooks.gcs import GCSHook 
from google.oauth2 import service_account
import os
import json
from google.cloud import storage
import time

# Define default arguments that apply to all tasks
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,  # Task doesn't depend on previous run's success
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,  # Retry once if task fails
    'retry_delay': timedelta(minutes=5),
}

# Create the DAG
dag = DAG(
    'tutorial_basic_dag',  # DAG ID - must be unique
    default_args=default_args,
    description='A simple tutorial DAG',
    schedule_interval=timedelta(days=1),  # Run daily
    start_date=datetime(2024, 1, 1),  # When DAG starts running
    catchup=False,  # Don't backfill past runs
    tags=['tutorial', 'basics'],
)

# region Define Python functions for tasks
def print_hello():
    """Simple function that prints hello"""
    print("Hello from Airflow!")
    return "Task completed successfully"

def print_date():
    """Function that prints current date"""
    from datetime import datetime
    current_date = datetime.now()
    print(f"Current date and time: {current_date}")
    return current_date

def process_data(**context):
    """Function with context - access to Airflow variables"""
    execution_date = context['execution_date']
    print(f"Processing data for execution date: {execution_date}")
    
    # You can return values that other tasks can use
    return {'status': 'success', 'records_processed': 100}

def test_gcs_connection(**context):
    """
    Function that tests GCS connection using your service account key
    This will verify:
    1. The key file exists and is readable
    2. Authentication works
    3. Can list buckets (requires Storage Admin or Viewer role)
    """
    import os
    import json
    from google.cloud import storage
    from google.oauth2 import service_account
    
    print("=" * 60)
    print("TESTING GCS CONNECTION")
    print("=" * 60)
    
    # Path to your service account key
    key_path = "/opt/airflow/config/gcs-key.json"
    
    try:
        # Step 1: Check if key file exists
        print(f"\n1. Checking if key file exists: {key_path}")
        if not os.path.exists(key_path):
            raise FileNotFoundError(f"Key file not found at {key_path}")
        print("   ✓ Key file found")
        
        # Step 2: Read and validate key file
        print("\n2. Reading and validating key file...")
        with open(key_path, 'r') as f:
            key_data = json.load(f)
        
        required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email']
        for field in required_fields:
            if field not in key_data:
                raise ValueError(f"Missing required field in key file: {field}")
        
        print(f"   ✓ Key file is valid")
        print(f"   ✓ Project ID: {key_data['project_id']}")
        print(f"   ✓ Service Account: {key_data['client_email']}")
        print(f"   ✓ Private Key ID: {key_data['private_key_id'][:20]}...")
        
        # Step 3: Create credentials
        print("\n3. Creating credentials from service account key...")
        credentials = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        print("   ✓ Credentials created successfully")
        
        # Step 4: Initialize GCS client
        print("\n4. Initializing Google Cloud Storage client...")
        client = storage.Client(
            credentials=credentials,
            project=key_data['project_id']
        )
        print("   ✓ GCS client initialized")
        
        # Step 5: Test authentication by listing buckets
        print("\n5. Testing authentication by listing buckets...")
        buckets = list(client.list_buckets())
        
        if buckets:
            print(f"   ✓ Successfully authenticated!")
            print(f"   ✓ Found {len(buckets)} bucket(s) in project:")
            for bucket in buckets[:5]:  # Show first 5 buckets
                print(f"      - {bucket.name}")
            if len(buckets) > 5:
                print(f"      ... and {len(buckets) - 5} more")
        else:
            print("   ✓ Authentication successful, but no buckets found")
            print("   ℹ️  This is normal if you haven't created any buckets yet")
        
        # Step 6: Test bucket creation permissions (optional - commented out by default)
        # Uncomment below to test if you can create buckets
        """
        print("\n6. Testing bucket creation permissions...")
        test_bucket_name = f"test-bucket-{key_data['project_id']}-{int(time.time())}"
        try:
            test_bucket = client.create_bucket(test_bucket_name)
            print(f"   ✓ Successfully created test bucket: {test_bucket_name}")
            # Clean up - delete the test bucket
            test_bucket.delete()
            print(f"   ✓ Successfully deleted test bucket")
        except Exception as e:
            print(f"   ⚠️  Cannot create buckets: {str(e)}")
            print("   ℹ️  This is OK if you only need read access")
        """
        
        # Summary
        print("\n" + "=" * 60)
        print("✅ GCS CONNECTION TEST SUCCESSFUL!")
        print("=" * 60)
        print("\nYour GCS connection is working properly.")
        print("You can now use GCS operators in your DAGs.")
        print("=" * 60)
        
        # Return summary for XCom
        return {
            'status': 'success',
            'project_id': key_data['project_id'],
            'service_account': key_data['client_email'],
            'buckets_found': len(buckets),
            'bucket_names': [b.name for b in buckets] if buckets else []
        }
        
    except FileNotFoundError as e:
        print(f"\n❌ ERROR: {str(e)}")
        print("\nTroubleshooting:")
        print("1. Make sure gcs-key.json is in your config/ folder")
        print("2. Verify the volume mount in docker-compose.yml:")
        print("   - ${AIRFLOW_PROJ_DIR:-.}/config:/opt/airflow/config")
        print("3. Restart containers if you just added the file:")
        print("   docker-compose restart")
        raise
        
    except json.JSONDecodeError as e:
        print(f"\n❌ ERROR: Invalid JSON in key file: {str(e)}")
        print("\nTroubleshooting:")
        print("1. Make sure gcs-key.json is valid JSON")
        print("2. Check if file was corrupted during copy")
        print("3. Re-download the key from GCP Console")
        raise
        
    except ValueError as e:
        print(f"\n❌ ERROR: {str(e)}")
        print("\nTroubleshooting:")
        print("1. Make sure you downloaded the correct service account key")
        print("2. The file should be in JSON format from GCP Console")
        print("3. Re-download the key if needed")
        raise
        
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        print(f"\nError type: {type(e).__name__}")
        print("\nCommon issues:")
        print("1. Service account doesn't have proper permissions")
        print("   - Need at least 'Storage Object Viewer' role")
        print("2. API not enabled: Enable Cloud Storage API in GCP Console")
        print("3. Network issues: Check if containers can reach GCP")
        print("4. Invalid credentials: Re-download the service account key")
        raise

def upload_to_gcs(bucket_name, source_file_path, destination_blob_name=None, **context):
    """
    Upload a file to Google Cloud Storage
    
    Args:
        bucket_name: Name of the GCS bucket
        source_file_path: Local file path to upload
        destination_blob_name: Name for the file in GCS (optional, uses filename if not provided)
    """
    print("=" * 60)
    print("UPLOADING TO GCS")
    print("=" * 60)
    
    try:
        # Setup credentials
        key_path = "/opt/airflow/config/gcs-key.json"
        credentials = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        
        # Read project ID
        with open(key_path, 'r') as f:
            key_data = json.load(f)
        
        # Initialize client
        client = storage.Client(
            credentials=credentials,
            project=key_data['project_id']
        )
        
        # Get bucket
        print(f"\n1. Accessing bucket: {bucket_name}")
        bucket = client.bucket(bucket_name)
        
        # Set destination name
        if destination_blob_name is None:
            destination_blob_name = os.path.basename(source_file_path)
        
        # Create blob (file object in GCS)
        blob = bucket.blob(destination_blob_name)
        
        # Check if source file exists
        if not os.path.exists(source_file_path):
            raise FileNotFoundError(f"Source file not found: {source_file_path}")
        
        # Get file size
        file_size = os.path.getsize(source_file_path)
        print(f"2. Source file: {source_file_path}")
        print(f"   Size: {file_size:,} bytes ({file_size / 1024:.2f} KB)")
        
        # Upload file
        print(f"3. Uploading to: gs://{bucket_name}/{destination_blob_name}")
        blob.upload_from_filename(source_file_path)
        
        # Get uploaded file info
        blob.reload()
        
        print(f"\n✅ Upload successful!")
        print(f"   GCS URI: gs://{bucket_name}/{destination_blob_name}")
        print(f"   Public URL: {blob.public_url}")
        print(f"   Content Type: {blob.content_type}")
        print(f"   Size: {blob.size:,} bytes")
        print(f"   Created: {blob.time_created}")
        print("=" * 60)
        
        # Return info for downstream tasks
        return {
            'status': 'success',
            'bucket': bucket_name,
            'blob_name': destination_blob_name,
            'size': blob.size,
            'uri': f"gs://{bucket_name}/{destination_blob_name}",
            'public_url': blob.public_url
        }
        
    except Exception as e:
        print(f"\n❌ Upload failed: {str(e)}")
        print("\nTroubleshooting:")
        print("1. Make sure the bucket exists")
        print("2. Check service account has 'Storage Object Creator' role")
        print("3. Verify the source file path is correct")
        raise


def list_gcs_objects(bucket_name, prefix=None, max_results=100, **context):
    """
    List objects in a GCS bucket
    
    Args:
        bucket_name: Name of the GCS bucket
        prefix: Filter objects by prefix (folder path)
        max_results: Maximum number of objects to list
    """
    print("=" * 60)
    print("LISTING GCS OBJECTS")
    print("=" * 60)
    
    try:
        # Setup credentials
        key_path = "/opt/airflow/config/gcs-key.json"
        credentials = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        
        with open(key_path, 'r') as f:
            key_data = json.load(f)
        
        # Initialize client
        client = storage.Client(
            credentials=credentials,
            project=key_data['project_id']
        )
        
        # Get bucket
        print(f"\n1. Accessing bucket: {bucket_name}")
        if prefix:
            print(f"   Filtering by prefix: {prefix}")
        
        bucket = client.bucket(bucket_name)
        
        # List blobs
        print(f"\n2. Listing objects...")
        blobs = list(client.list_blobs(
            bucket_name, 
            prefix=prefix,
            max_results=max_results
        ))
        
        if not blobs:
            print("\n   No objects found in bucket")
            print("=" * 60)
            return {
                'status': 'success',
                'bucket': bucket_name,
                'count': 0,
                'objects': []
            }
        
        # Display results
        print(f"\n✅ Found {len(blobs)} object(s):")
        print("-" * 60)
        
        total_size = 0
        object_list = []
        
        for i, blob in enumerate(blobs, 1):
            size_kb = blob.size / 1024
            size_mb = size_kb / 1024
            total_size += blob.size
            
            # Format size
            if size_mb > 1:
                size_str = f"{size_mb:.2f} MB"
            else:
                size_str = f"{size_kb:.2f} KB"
            
            print(f"\n{i}. {blob.name}")
            print(f"   Size: {size_str}")
            print(f"   Type: {blob.content_type}")
            print(f"   Created: {blob.time_created}")
            print(f"   Updated: {blob.updated}")
            print(f"   URI: gs://{bucket_name}/{blob.name}")
            
            object_list.append({
                'name': blob.name,
                'size': blob.size,
                'content_type': blob.content_type,
                'created': str(blob.time_created),
                'uri': f"gs://{bucket_name}/{blob.name}"
            })
        
        # Summary
        total_size_mb = total_size / (1024 * 1024)
        print("\n" + "-" * 60)
        print(f"Total objects: {len(blobs)}")
        print(f"Total size: {total_size_mb:.2f} MB")
        print("=" * 60)
        
        return {
            'status': 'success',
            'bucket': bucket_name,
            'count': len(blobs),
            'total_size_bytes': total_size,
            'objects': object_list
        }
        
    except Exception as e:
        print(f"\n❌ Listing failed: {str(e)}")
        print("\nTroubleshooting:")
        print("1. Make sure the bucket exists")
        print("2. Check service account has 'Storage Object Viewer' role")
        raise


def download_from_gcs(bucket_name, source_blob_name, destination_file_path, **context):
    """
    Download a file from Google Cloud Storage
    
    Args:
        bucket_name: Name of the GCS bucket
        source_blob_name: Name of the file in GCS
        destination_file_path: Local path where file will be saved
    """
    print("=" * 60)
    print("DOWNLOADING FROM GCS")
    print("=" * 60)
    
    try:
        # Setup credentials
        key_path = "/opt/airflow/config/gcs-key.json"
        credentials = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        
        with open(key_path, 'r') as f:
            key_data = json.load(f)
        
        # Initialize client
        client = storage.Client(
            credentials=credentials,
            project=key_data['project_id']
        )
        
        # Get bucket and blob
        print(f"\n1. Accessing bucket: {bucket_name}")
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(source_blob_name)
        
        # Check if blob exists
        if not blob.exists():
            raise FileNotFoundError(f"Object not found in GCS: {source_blob_name}")
        
        # Get file info
        blob.reload()
        print(f"2. Source object: gs://{bucket_name}/{source_blob_name}")
        print(f"   Size: {blob.size:,} bytes ({blob.size / 1024:.2f} KB)")
        print(f"   Type: {blob.content_type}")
        
        # Create destination directory if it doesn't exist
        dest_dir = os.path.dirname(destination_file_path)
        if dest_dir and not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
            print(f"3. Created directory: {dest_dir}")
        
        # Download file
        print(f"4. Downloading to: {destination_file_path}")
        blob.download_to_filename(destination_file_path)
        
        # Verify download
        if os.path.exists(destination_file_path):
            local_size = os.path.getsize(destination_file_path)
            print(f"\n✅ Download successful!")
            print(f"   Local file: {destination_file_path}")
            print(f"   Size: {local_size:,} bytes")
            
            if local_size == blob.size:
                print(f"   ✓ File size matches!")
            else:
                print(f"   ⚠️  Warning: Size mismatch (GCS: {blob.size}, Local: {local_size})")
        
        print("=" * 60)
        
        return {
            'status': 'success',
            'bucket': bucket_name,
            'blob_name': source_blob_name,
            'local_path': destination_file_path,
            'size': blob.size,
            'content_type': blob.content_type
        }
        
    except Exception as e:
        print(f"\n❌ Download failed: {str(e)}")
        print("\nTroubleshooting:")
        print("1. Make sure the object exists in GCS")
        print("2. Check service account has 'Storage Object Viewer' role")
        print("3. Verify destination path is writable")
        raise


def delete_from_gcs(bucket_name, blob_name, **context):
    """
    Delete an object from Google Cloud Storage
    
    Args:
        bucket_name: Name of the GCS bucket
        blob_name: Name of the file to delete in GCS
    """
    print("=" * 60)
    print("DELETING FROM GCS")
    print("=" * 60)
    
    try:
        # Setup credentials
        key_path = "/opt/airflow/config/gcs-key.json"
        credentials = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        
        with open(key_path, 'r') as f:
            key_data = json.load(f)
        
        # Initialize client
        client = storage.Client(
            credentials=credentials,
            project=key_data['project_id']
        )
        
        # Get bucket and blob
        print(f"\n1. Accessing bucket: {bucket_name}")
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        # Check if blob exists
        if not blob.exists():
            print(f"\n⚠️  Object does not exist: gs://{bucket_name}/{blob_name}")
            print("   Nothing to delete")
            print("=" * 60)
            return {
                'status': 'skipped',
                'message': 'Object does not exist',
                'bucket': bucket_name,
                'blob_name': blob_name
            }
        
        # Get info before deletion
        blob.reload()
        print(f"2. Object to delete: gs://{bucket_name}/{blob_name}")
        print(f"   Size: {blob.size:,} bytes ({blob.size / 1024:.2f} KB)")
        print(f"   Type: {blob.content_type}")
        print(f"   Created: {blob.time_created}")
        
        # Delete the blob
        print(f"\n3. Deleting object...")
        blob.delete()
        
        # Verify deletion
        if not blob.exists():
            print(f"\n✅ Delete successful!")
            print(f"   Deleted: gs://{bucket_name}/{blob_name}")
        else:
            print(f"\n⚠️  Warning: Object may still exist")
        
        print("=" * 60)
        
        return {
            'status': 'success',
            'bucket': bucket_name,
            'blob_name': blob_name,
            'deleted_size': blob.size
        }
        
    except Exception as e:
        print(f"\n❌ Delete failed: {str(e)}")
        print("\nTroubleshooting:")
        print("1. Make sure the object exists in GCS")
        print("2. Check service account has 'Storage Object Admin' role")
        print("3. Verify you have delete permissions on the bucket")
        raise


# Example: Create a test file to upload
def create_test_file(file_path="/tmp/test_upload.txt", **context):
    """Create a test file for uploading to GCS"""
    from datetime import datetime
    
    content = f"""
    This is a test file created by Airflow
    ========================================
    
    Timestamp: {datetime.now()}
    Execution Date: {context.get('execution_date', 'N/A')}
    Task Instance: {context.get('task_instance', 'N/A')}
    
    This file will be uploaded to Google Cloud Storage.
    """
    
    with open(file_path, 'w') as f:
        f.write(content)
    
    print(f"✓ Created test file: {file_path}")
    return file_path

# endregion Define Python functions for tasks


# region Task Calls

# Task 1: Simple Python task
task1 = PythonOperator(
    task_id='print_hello_task',
    python_callable=print_hello,
    dag=dag,
)

# Task 2: Another Python task
task2 = PythonOperator(
    task_id='print_date_task',
    python_callable=print_date,
    dag=dag,
)


# Task 3: Python task with context
task3 = PythonOperator(
    task_id='process_data_task',
    python_callable=process_data,
    provide_context=True,  # Pass Airflow context to function
    dag=dag,
)

# Task 4: Bash command task
task4 = BashOperator(
    task_id='bash_task',
    bash_command='echo "This is a bash command" && date',
    dag=dag,
)

# Task 5: Test GCS connection
test_gcs_connection = PythonOperator(
    task_id='test_gcs_connection_task',
    python_callable=test_gcs_connection,
    dag=dag
)

# Create test file
create_file = PythonOperator(
    task_id='create_test_file',
    python_callable=create_test_file,
    op_kwargs={'file_path': '/tmp/test_upload.txt'},
)

# Upload to GCS
upload = PythonOperator(
    task_id='upload_to_gcs',
    python_callable=upload_to_gcs,
    op_kwargs={
        'bucket_name': 'your-bucket-name',
        'source_file_path': '/tmp/test_upload.txt',
        'destination_blob_name': 'uploads/test_file.txt'
    },
)

# List objects in bucket
list_objects = PythonOperator(
    task_id='list_gcs_objects',
    python_callable=list_gcs_objects,
    op_kwargs={
        'bucket_name': 'your-bucket-name',
        'prefix': 'uploads/'
    },
)

# Download from GCS
download = PythonOperator(
    task_id='download_from_gcs',
    python_callable=download_from_gcs,
    op_kwargs={
        'bucket_name': 'your-bucket-name',
        'source_blob_name': 'uploads/test_file.txt',
        'destination_file_path': '/tmp/downloaded_file.txt'
    },
)

# Delete from GCS
delete = PythonOperator(
    task_id='delete_from_gcs',
    python_callable=delete_from_gcs,
    op_kwargs={
        'bucket_name': 'your-bucket-name',
        'blob_name': 'uploads/test_file.txt'
    },
)

# endregion Task Calls

# region Task Dependencies
# Define task dependencies (execution order)
# Method 1: Using >> operator (recommended)
task1 >> task2 >> task3 >> task4 >> test_gcs_connection >> create_file >> upload >> list_objects >> download >> delete
# endregion Task Dependencies