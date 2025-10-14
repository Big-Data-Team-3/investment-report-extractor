# dags/ir_extraction_dag_factory.py
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import json
import os
from pathlib import Path

# Import task functions
from ir_extraction_tasks import extract_company_documents, download_company_documents, upload_company_documents_to_gcs

def load_companies_data():
    """Load company data from JSON file"""
    companies_file = "/opt/airflow/dags/dow30_ir_pages.json"
    with open(companies_file, 'r') as f:
        return json.load(f)

def create_company_dag(company_data):
    """Create a DAG for a specific company"""
    ticker = company_data['metadata']['ticker']
    company_name = company_data['metadata']['company_name']
    ir_url = company_data['url']
    
    dag_id = f"ir_extraction_{ticker.lower()}"
    
    default_args = {
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': False,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    }
    
    dag = DAG(
        dag_id=dag_id,
        default_args=default_args,
        description=f'IR Document Extraction for {ticker} - {company_name}',
        schedule_interval=timedelta(days=7),  # Run weekly
        start_date=datetime(2024, 1, 1),
        catchup=False,
        tags=['ir_extraction', ticker.lower(), 'documents'],
    )
    
    # Task 1: Extract Documents
    extract_task = PythonOperator(
        task_id=f'extract_documents_{ticker.lower()}',
        python_callable=extract_company_documents,
        op_kwargs={
            'ticker': ticker,
            'company_name': company_name,
            'ir_url': ir_url
        },
        dag=dag,
    )
    
    # Task 2: Download Documents
    download_task = PythonOperator(
        task_id=f'download_documents_{ticker.lower()}',
        python_callable=download_company_documents,
        op_kwargs={
            'ticker': ticker,
            'company_name': company_name
        },
        dag=dag,
    )
    
    # Task 3: Upload to GCS
    upload_task = PythonOperator(
        task_id=f'upload_to_gcs_{ticker.lower()}',
        python_callable=upload_company_documents_to_gcs,
        op_kwargs={
            'ticker': ticker,
            'company_name': company_name
        },
        dag=dag,
    )
    
    # Set task dependencies
    extract_task >> download_task >> upload_task
    
    return dag

# Create DAGs for all companies
companies_data = load_companies_data()
for company_data in companies_data:
    dag = create_company_dag(company_data)
    globals()[dag.dag_id] = dag