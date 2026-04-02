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

# GLOBAL PROJECT SELECTOR
# We fetch projects from BigQuery to populate the sidebar menu
def get_project_list():
    query = "SELECT project_id, name, origin_north, origin_east FROM `sensorpush-export.survey.projects` ORDER BY name"
    return run_query(query)

df_projects = get_project_list()

with st.sidebar:
    st.title("🏗️ SoilFreeze Manager")
    
    if not df_projects.empty:
        # This is your "Menu of projects already created"
        selected_project_name = st.selectbox(
            "Current Project Context:", 
            options=df_projects['name'].tolist(),
            help="All uploads and charts will apply to this project."
        )
        # Store the active project details in a variable
        active_proj = df_projects[df_projects['name'] == selected_project_name].iloc[0]
        st.info(f"Active ID: {active_proj['project_id']}")
    else:
        st.warning("No projects found. Please create one below.")
        active_proj = None

    st.divider()
    menu = ["Project Dashboard", "1. Create New Project", "2. Upload Baseline", "3. Upload Top Survey", "4. Upload Downhole"]
    choice = st.selectbox("Navigation", menu)

# --- WORKFLOW PAGES ---

if choice == "Project Dashboard":
    if active_proj is not None:
        st.subheader(f"Analysis: {active_proj['name']}")
        # Visualizer logic goes here
    else:
        st.error("Select or create a project first.")

elif choice == "1. Create New Project":
    tab1, tab2 = st.tabs(["Create New", "Edit Existing"])

    # --- SUB-BLOCK: CREATE ---
    with tab1:
        st.subheader("Setup New Project")
        with st.form("new_project_form", clear_on_submit=True):
            new_id = st.text_input("Project ID (Unique Code)")
            new_name = st.text_input("Project Name (Display Name)")
            new_on = st.number_input("Origin Northing (can be 0 for now)", format="%.3f", value=0.0)
            new_oe = st.number_input("Origin Easting (can be 0 for now)", format="%.3f", value=0.0)
            
            if st.form_submit_button("Save New Project"):
                new_df = pd.DataFrame([{
                    'project_id': new_id, 'name': new_name,
                    'origin_north': new_on, 'origin_east': new_oe
                }])
                upload_to_bq(new_df, "sensorpush-export.survey.projects")
                st.success("Project Created!")
                st.rerun()

    # --- SUB-BLOCK: EDIT ---
    with tab2:
        if active_proj is not None:
            st.subheader(f"Edit Details for: {active_proj['name']}")
            # Text inputs pre-filled with current BigQuery values
            upd_name = st.text_input("Edit Name", value=active_proj['name'])
            upd_on = st.number_input("Update Origin Northing", value=float(active_proj['origin_north']), format="%.3f")
            upd_oe = st.number_input("Update Origin Easting", value=float(active_proj['origin_east']), format="%.3f")
            
            if st.button("Update Project in Database"):
                # BigQuery UPDATE script
                update_query = f"""
                    UPDATE `sensorpush-export.survey.projects`
                    SET name = '{upd_name}', 
                        origin_north = {upd_on}, 
                        origin_east = {upd_oe}
                    WHERE project_id = '{active_proj['project_id']}'
                """
                client.query(update_query).result()
                st.success("Project coordinates updated!")
                st.rerun()
        else:
            st.warning("Select a project from the sidebar to edit it.")

elif choice == "2. Upload Baseline":
    if active_proj is not None:
        st.subheader(f"Step 2: Import Design Baseline for {active_proj['name']}")
        
        # 1. Instructions and Template
        st.info("Upload a CSV with these headers: hole_id, design_n, design_e, design_z")
        
        # 2. File Uploader
        baseline_file = st.file_uploader("Choose Baseline CSV", type=['csv'])
        
        if baseline_file:
            df_base = pd.read_csv(baseline_file)
            
            # Data Cleaning: Ensure column names match BigQuery schema
            df_base.columns = [c.lower().strip() for c in df_base.columns]
            
            # Validation
            required_cols = ['hole_id', 'design_n', 'design_e', 'design_z']
            if all(col in df_base.columns for col in required_cols):
                # Add the project_id to link these holes to the active project
                df_base['project_id'] = active_proj['project_id']
                
                # Show preview
                st.write("Preview of Design Data:", df_base.head())
                
                if st.button("Confirm & Upload to BigQuery"):
                    with st.spinner("Writing to database..."):
                        # We use WRITE_TRUNCATE if you want to overwrite, 
                        # or WRITE_APPEND to add to existing
                        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
                        
                        try:
                            upload_to_bq(df_base, "sensorpush-export.survey.holes")
                            st.success(f"Successfully loaded {len(df_base)} holes for {active_proj['name']}.")
                        except Exception as e:
                            st.error(f"Error uploading: {e}")
            else:
                st.error(f"CSV missing columns. Required: {required_cols}")
    else:
        st.warning("Please select or create a project in the sidebar first.")
