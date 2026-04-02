import streamlit as st
import pandas as pd
from google.cloud import bigquery

# Assuming 'client' and 'proj' are already defined from the previous step
def upload_survey_data(uploaded_file, project_id):
    """Processes CSV and uploads to BigQuery survey table."""
    df = pd.read_csv(uploaded_file)
    
    # Standardize columns to match BigQuery schema
    # Expecting: Hole_ID, Depth, Azimuth, Inclination
    df.columns = [c.lower().strip() for c in df.columns]
    
    required = {'hole_id', 'depth', 'azimuth', 'inclination'}
    if not required.issubset(df.columns):
        st.error(f"Missing columns. CSV must have: {required}")
        return

    # Add metadata required by the database
    df['project_id'] = project_id
    df['survey_type'] = 'Pipe' # Defaulting to Pipe for this paired-down version
    
    # Push to BigQuery
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    job = client.load_table_from_dataframe(
        df, "sensorpush-export.survey.surveys", job_config=job_config
    )
    job.result() # Wait for table load to complete
    st.success(f"Uploaded {len(df)} points to Project: {project_id}")

# UI for Upload
with st.expander("📤 Upload New Survey Data"):
    survey_file = st.file_uploader("Upload Downhole CSV", type=['csv'])
    if survey_file and st.button("Confirm Upload to BigQuery"):
        upload_survey_data(survey_file, proj['project_id'])
