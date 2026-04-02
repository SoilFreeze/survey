import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from google.cloud import bigquery
from google.oauth2 import service_account

# ==========================================
# 1. AUTHENTICATION BLOCK
# ==========================================
def get_bq_client():
    """Replace this block if changing your GCP connection method."""
    if "gcp_service_account" in st.secrets:
        info = st.secrets["gcp_service_account"]
        credentials = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(credentials=credentials, project=info["project_id"])
    else:
        st.error("Secrets not found!")
        st.stop()

client = get_bq_client()

# ==========================================
# 2. MATH & TRANSFORMATION BLOCK
# ==========================================
def calculate_survey_path(df, origin_n, origin_e):
    """
    Replace this block to change coordinate math.
    Input: Dataframe with raw survey points.
    Output: Dataframe with n_rel and e_rel centered at 0,0.
    """
    df = df.sort_values('depth')
    rad_az = np.radians(df['azimuth'])
    rad_inc = np.radians(df['inclination'])
    dist = df['depth'].diff().fillna(0)
    
    # Standard survey math
    dn = dist * np.sin(rad_inc) * np.cos(rad_az)
    de = dist * np.sin(rad_inc) * np.sin(rad_az)
    
    df['n_rel'] = dn.cumsum()
    df['e_rel'] = de.cumsum()
    return df

# ==========================================
# 3. BIGQUERY INTERFACE BLOCK (DATABASE I/O)
# ==========================================
def run_query(query):
    """Generic wrapper to fetch data from BigQuery."""
    return client.query(query).to_dataframe()

def upload_to_bq(df, table_id):
    """Generic wrapper to push data to BigQuery."""
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    return job.result()

# ==========================================
# 4. USER INTERFACE (MAIN APP)
# ==========================================
st.set_page_config(page_title="SoilFreeze Survey", layout="wide")
st.title("🏗️ SoilFreeze Survey Manager")

# Sidebar Workflow Navigation
menu = ["Project Dashboard", "1. Create Project", "2. Upload Baseline", "3. Upload Top Survey", "4. Upload Downhole"]
choice = st.sidebar.selectbox("Navigation", menu)

if choice == "Project Dashboard":
    st.subheader("Project Analysis & Visualization")
    # Dashboard logic goes here
    
elif choice == "1. Create Project":
    st.subheader("Setup New Project Origin")
    # Form for survey.projects

elif choice == "2. Upload Baseline":
    st.subheader("Import Design Data")
    # Form for survey.holes

elif choice == "3. Upload Top Survey":
    st.subheader("Import Actual Collar Locations")
    # Update logic for survey.holes

elif choice == "4. Upload Downhole":
    st.subheader("Import Boretrak/Gyro Data")
    # Form for survey.surveys
