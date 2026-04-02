import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from google.cloud import bigquery
from google.oauth2 import service_account

# ==========================================
# 1. AUTHENTICATION & CORE DATABASE I/O
# ==========================================
def get_bq_client():
    """Authenticates using Streamlit Secrets."""
    if "gcp_service_account" in st.secrets:
        info = st.secrets["gcp_service_account"]
        credentials = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(credentials=credentials, project=info["project_id"])
    else:
        st.error("GCP Secrets not found in .streamlit/secrets.toml")
        st.stop()

client = get_bq_client()

def run_query(query):
    """Generic wrapper to fetch data from BigQuery."""
    return client.query(query).to_dataframe()

def upload_to_bq(df, table_id, write_mode="WRITE_APPEND"):
    """Generic wrapper to push data to BigQuery."""
    job_config = bigquery.LoadJobConfig(write_disposition=write_mode)
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    return job.result()

# ==========================================
# 2. MATH & COORDINATE TRANSFORMATION
# ==========================================
def calculate_survey_path(df, start_n_shifted, start_e_shifted):
    """
    Calculates path and shifts coordinates to 0,0 center.
    Inputs: Raw survey dataframe, and the pre-calculated shifted collar start points.
    """
    df = df.sort_values('depth')
    rad_az = np.radians(df['azimuth'])
    rad_inc = np.radians(df['inclination'])
    dist = df['depth'].diff().fillna(0)
    
    # Standard survey math: N = dist * sin(inc) * cos(az)
    dn = dist * np.sin(rad_inc) * np.cos(rad_az)
    de = dist * np.sin(rad_inc) * np.sin(rad_az)
    
    # Cumulative position relative to the shifted collar
    df['n_rel'] = start_n_shifted + dn.cumsum()
    df['e_rel'] = start_e_shifted + de.cumsum()
    return df

# ==========================================
# 3. GLOBAL SIDEBAR (PROJECT & PHASE CONTEXT)
# ==========================================
st.sidebar.title("🏗️ SoilFreeze Hub")

# Fetch available projects
df_projects = run_query("SELECT * FROM `sensorpush-export.survey.projects` ORDER BY name")

if not df_projects.empty:
    sel_proj_name = st.sidebar.selectbox("Active Project", df_projects['name'].tolist())
    active_proj = df_projects[df_projects['name'] == sel_proj_name].iloc[0]
    
    # Fetch phases for this specific project
    phase_query = f"SELECT DISTINCT phase FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}'"
    df_phases = run_query(phase_query)
    phases = ["All Phases"] + sorted(df_phases['phase'].dropna().unique().tolist())
    active_phase = st.sidebar.selectbox("Filter Phase", phases)
else:
    st.sidebar.warning("No projects found. Create one in Maintenance.")
    active_proj = None

st.sidebar.divider()
category = st.sidebar.selectbox("Category", ["Database Maintenance", "Visualization", "Reports"])

# ==========================================
# 4. DATABASE MAINTENANCE (INGESTION)
# ==========================================
if category == "Database Maintenance":
    action = st.radio("Action", ["Project Setup", "Upload Baseline", "Update Top Survey", "Upload Downhole"], horizontal=True)
    
    if action == "Project Setup":
        tab1, tab2 = st.tabs(["Create New", "Edit Origin"])
        with tab1:
            with st.form("new_proj"):
                n_id = st.text_input("Project ID")
                n_name = st.text_input("Project Name")
                n_on = st.number_input("Origin Northing (0,0 point)", format="%.3f")
                n_oe = st.number_input("Origin Easting (0,0 point)", format="%.3f")
                if st.form_submit_button("Save"):
                    upload_to_bq(pd.DataFrame([{'project_id':n_id, 'name':n_name, 'origin_north':n_on, 'origin_east':n_oe}]), 
                                 "sensorpush-export.survey.projects")
                    st.rerun()
        with tab2:
            if active_proj is not None:
                u_on = st.number_input("Update Origin North", value=float(active_proj['origin_north']), format="%.3f")
                u_oe = st.number_input("Update Origin East", value=float(active_proj['origin_east']), format="%.3f")
                if st.button("Update Origin"):
                    client.query(f"UPDATE `sensorpush-export.survey.projects` SET origin_north={u_on}, origin_east={u_oe} WHERE project_id='{active_proj['project_id']}'")
                    st.rerun()

    elif action == "Upload Baseline":
        st.info("Required: hole_id, north, east, elev, phase")
        file = st.file_uploader("Upload Baseline CSV", type=['csv'])
        if file and active_proj is not None:
            df = pd.read_csv(file)
            # Add robust mapping and type casting here
            df['hole_id'] = df['hole_id'].astype(str).str.strip()
            df['project_id'] = active_proj['project_id']
            if st.button("Upload Baseline"):
                upload_to_bq(df, "sensorpush-export.survey.holes")
                st.success("Baseline uploaded.")

    # [Update Top Survey and Upload Downhole follow similar logic blocks]

# ==========================================
# 5. VISUALIZATION (ANALYSIS)
# ==========================================
elif category == "Visualization":
    view = st.radio("View Type", ["Site Map", "Single Hole Deviation"], horizontal=True)
    
    if active_proj is not None:
        # Load joined data
        q = f"""SELECT h.*, s.depth, s.azimuth, s.inclination FROM `sensorpush-export.survey.holes` h 
                LEFT JOIN `sensorpush-export.survey.surveys` s ON h.hole_id = s.hole_id 
                WHERE h.project_id = '{active_proj['project_id']}'"""
        df_viz = run_query(q)
        
        if active_phase != "All Phases":
            df_viz = df_viz[df_viz['phase'] == active_phase]

        if view == "Single Hole Deviation":
            # Filter for surveyed holes only
            surveyed = df_viz.dropna(subset=['depth'])['hole_id'].unique()
            target = st.selectbox("Select Hole", sorted(surveyed))
            
            df_h = df_viz[df_viz['hole_id'] == target].copy()
            # Apply 0,0 shift
            s_n = df_h['design_n'].iloc[0] - active_proj['origin_north']
            s_e = df_h['design_e'].iloc[0] - active_proj['origin_east']
            
            processed = calculate_survey_path(df_h, s_n, s_e)
            
            fig = make_subplots(rows=1, cols=2, subplot_titles=("East Dev", "North Dev"))
            fig.add_trace(go.Scatter(x=processed['e_rel'], y=processed['depth'], name="East"), row=1, col=1)
            fig.add_trace(go.Scatter(x=processed['n_rel'], y=processed['depth'], name="North"), row=1, col=2)
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(fig, use_container_width=True)

# ==========================================
# 6. REPORTS (EXPORT)
# ==========================================
elif category == "Reports":
    st.subheader("Data Export")
    # Add logic for target elevation filtering and CSV downloads
