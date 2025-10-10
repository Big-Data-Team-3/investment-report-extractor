from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.models import Connection
from airflow.utils.db import provide_session
from datetime import datetime
import json
import os

def setup_gcs_connection():
    """Setup GCS connection in Airflow"""
    
    # Read the service account key
    key_path = '/opt/airflow/config/gcs-key.json'
    
    if not os.path.exists(key_path):
        raise FileNotFoundError(f"GCS key file not found at {key_path}")
    
    with open(key_path, 'r') as f:
        service_account_info = json.load(f)
    
    project_id = service_account_info.get('project_id')
    
    @provide_session
    def create_connection(session):
        conn_id = 'google_cloud_default'
        
        # Check if connection already exists
        existing_conn = session.query(Connection).filter(Connection.conn_id == conn_id).first()
        if existing_conn:
            session.delete(existing_conn)
            print(f"Deleted existing connection: {conn_id}")
        
        # Create new connection
        new_conn = Connection(
            conn_id=conn_id,
            conn_type='google_cloud_platform',
            description='GCS connection for investment report extractor',
            extra=json.dumps({
                "extra__google_cloud_platform__key_path": key_path,
                "extra__google_cloud_platform__project": project_id,
                "extra__google_cloud_platform__scope": "https://www.googleapis.com/auth/cloud-platform"
            })
        )
        
        session.add(new_conn)
        session.commit()
        print(f"Created GCS connection: {conn_id} for project: {project_id}")
    
    create_connection()

dag = DAG(
    'setup_gcs_connection',
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=['setup', 'gcs'],
    description='Setup GCS connection for Airflow'
)

setup_task = PythonOperator(
    task_id='setup_gcs_connection',
    python_callable=setup_gcs_connection,
    dag=dag
)