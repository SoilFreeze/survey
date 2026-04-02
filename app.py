import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from google.cloud import bigquery
from google.oauth2 import service_account
import re
from datetime import datetime

# ==========================================
# 1. CORE CONFIG & DB I/O
# ==========================================
def get_bq_client():
    if "gcp_service_account" in st.secrets:
        info = st.secrets["gcp_service_account"]
        credentials = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(credentials=credentials, project=info["project_id"])
    else:
        st.error("GCP Secrets not found.")
        st.stop()

client = get_bq_client()

def run_query(query):
    return client.query(query).to_dataframe()

def upload_to_bq(df, table_id, write_mode="WRITE_APPEND"):
    job_config = bigquery.LoadJobConfig(write_disposition=write_mode)
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    return job.result()

# ==========================================
# 2. MATH ENGINE (Battered Support)
# ==========================================
def calculate_survey_path(df, start_n, start_e):
    # Uses 'length' for sorting and distance calculation
    df = df.sort_values('length')
    rad_az = np.radians(df['azimuth'])
    rad_inc = np.radians(df['inclination'])
    dist = df['length'].diff().fillna(0)
    
    dn = dist * np.sin(rad_inc) * np.cos(rad_az)
    de = dist * np.sin(rad_inc) * np.sin(rad_az)
    
    df['n_rel'] = start_n + dn.cumsum()
    df['e_rel'] = start_e + de.cumsum()
    return df

# ==========================================
# 3. GLOBAL SIDEBAR
# ==========================================

st.sidebar.title("🏗️ SoilFreeze Hub")
df_projects = run_query("SELECT * FROM `sensorpush-export.survey.projects` ORDER BY name")

if not df_projects.empty:
    sel_proj_name = st.sidebar.selectbox("Active Project", df_projects['name'].tolist())
    active_proj = df_projects[df_projects['name'] == sel_proj_name].iloc[0]
    
    try:
        phase_q = f"SELECT DISTINCT phase FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}'"
        df_phases = run_query(phase_q)
        phases = ["All Phases"] + sorted(df_phases['phase'].dropna().unique().tolist())
    except:
        phases = ["All Phases"]
    active_phase = st.sidebar.selectbox("Filter Phase", phases)
else:
    st.sidebar.warning("No projects found.")
    active_proj = None

category = st.sidebar.selectbox("Category", ["Database Maintenance", "Visualization", "Reports"])

# ==========================================
# 4. DATABASE MAINTENANCE
# ==========================================
if category == "Database Maintenance":
    action = st.radio("Action", ["Project Setup", "Upload Baseline", "Update Top Survey", "Upload Downhole", "Manage Data"], horizontal=True)

    if action == "Project Setup":
        with st.form("new_proj_form"):
            st.subheader("1. Setup New Project")
            n_id = st.text_input("Project ID")
            n_name = st.text_input("Project Name")
            n_len = st.number_input("Standard Pipe Length (ft)", value=100.0)
            n_on = st.number_input("Origin Northing", format="%.3f")
            n_oe = st.number_input("Origin Easting", format="%.3f")
            if st.form_submit_button("Save Project"):
                df_new = pd.DataFrame([{'project_id':n_id, 'name':n_name, 'default_length':n_len, 'origin_north':n_on, 'origin_east':n_oe}])
                upload_to_bq(df_new, "sensorpush-export.survey.projects")
                st.success("Project Saved.")
                st.rerun()

    elif action == "Upload Baseline":
        st.subheader("2. Upload Baseline Grid")
        file = st.file_uploader("Upload Baseline CSV", type=['csv'])
        if file and active_proj is not None:
            df_base = pd.read_csv(file)
            df_base.columns = [c.lower().strip() for c in df_base.columns]
            rename_map = {'pipe':'hole_id', 'id':'hole_id', 'hole':'hole_id', 'north':'design_n', 'east':'design_e', 'len':'design_length', 'length':'design_length'}
            df_base = df_base.rename(columns=rename_map).dropna(subset=['hole_id'])
            db_schema = {'project_id': str(active_proj['project_id']), 'hole_id': None, 'design_n': 0.0, 'design_e': 0.0, 'design_z': 0.0, 'phase': "Phase1", 'pipe_type': "Freeze Pipe", 'design_inc': 0.0, 'design_az': 0.0, 'design_length': active_proj.get('default_length', 100.0)}
            for col, default in db_schema.items():
                if col not in df_base.columns: df_base[col] = default
            if st.button("🚀 Confirm Overwrite"):
                p_id, ph = str(active_proj['project_id']), df_base['phase'].iloc[0]
                client.query(f"DELETE FROM `sensorpush-export.survey.holes` WHERE project_id='{p_id}' AND phase='{ph}'").result()
                upload_to_bq(df_base[list(db_schema.keys())], "sensorpush-export.survey.holes")
                st.success("Grid Updated.")

    elif action == "Update Top Survey":
        st.subheader("3. Sync As-Built Surface Surveys")
        top_file = st.file_uploader("Upload As-Built CSV", type=['csv'])
        if top_file and active_proj is not None:
            df_top = pd.read_csv(top_file)
            df_top.columns = [c.upper().strip() for c in df_top.columns]
            df_top = df_top.rename(columns={'ID': 'hole_id', 'NORTHING': 'actual_n', 'EASTING': 'actual_e', 'ELEVATION': 'actual_z'})
            if st.button("Apply Surface Coordinates"):
                temp_id = f"sensorpush-export.survey.temp_top_{active_proj['project_id']}"
                upload_to_bq(df_top, temp_id, write_mode="WRITE_TRUNCATE")
                merge_q = f"MERGE `sensorpush-export.survey.holes` T USING `{temp_id}` S ON T.hole_id = S.hole_id AND T.project_id = '{active_proj['project_id']}' WHEN MATCHED THEN UPDATE SET T.actual_n = S.actual_n, T.actual_e = S.actual_e, T.actual_z = S.actual_z"
                client.query(merge_q).result()
                client.delete_table(temp_id)
                st.success("Surface As-Builts updated.")

    
    # --- UPLOAD DOWNHOLE (THE FIX IS HERE) ---
    if action == "Upload Downhole":
        st.subheader("Step 4: Upload Probe Data")
        dh_file = st.file_uploader("Upload Downhole CSV", type=['csv'])
        
        if dh_file and active_proj is not None:
            # 1. Precise Date Logic (Fixes the 2026-04-02 issue)
            def get_smart_date(name):
                pattern = r'(\d{1,2})[.\-](\d{1,2})[.\-](\d{2,4})'
                m = re.search(pattern, name)
                if m:
                    month, day, year = m.groups()
                    full_yr = "20" + year if len(year) == 2 else year
                    return f"{full_yr}-{month.zfill(2)}-{day.zfill(2)}"
                return datetime.now().strftime('%Y-%m-%d')

            f_date = get_smart_date(dh_file.name)
            st.info(f"📅 Survey Date: **{f_date}**")
            
            df_dh = pd.read_csv(dh_file)
            
            # 2. Flexible Keyword Harvester
            # This maps YOUR CSV headers to OUR logic
            rename_map = {}
            for col in df_dh.columns:
                c_low = col.lower().strip()
                if any(k in c_low for k in ['hole', 'pipe', 'id']): rename_map[col] = 'hole_id'
                elif any(k in c_low for k in ['length', 'depth', 'dist']): rename_map[col] = 'length'
                elif 'azi' in c_low: rename_map[col] = 'azimuth'
                elif 'inc' in c_low: rename_map[col] = 'inclination'

            df_dh = df_dh.rename(columns=rename_map)
            
            # 3. Clean and Upload
            req = ['hole_id', 'length', 'azimuth', 'inclination']
            if all(c in df_dh.columns for c in req):
                df_dh['project_id'] = str(active_proj['project_id'])
                df_dh['survey_date'] = f_date
                
                for c in ['length', 'azimuth', 'inclination']:
                    df_dh[c] = pd.to_numeric(df_dh[c], errors='coerce').fillna(0.0)

                st.write("### Data Preview")
                st.dataframe(df_dh[req].head())

                if st.button("🚀 Upload to BigQuery"):
                    with st.spinner("Translating Length to Depth for BigQuery..."):
                        try:
                            # CRITICAL MAPPING: 
                            # We use 'length' everywhere, but BQ table uses 'depth'
                            upload_df = df_dh.copy()
                            upload_df = upload_df.rename(columns={'length': 'depth'})
                            
                            final_cols = ['project_id', 'hole_id', 'depth', 'azimuth', 'inclination', 'survey_date']
                            upload_to_bq(upload_df[final_cols], "sensorpush-export.survey.surveys")
                            st.success(f"Successfully uploaded {len(df_dh)} rows.")
                        except Exception as e:
                            st.error(f"BigQuery Error: {e}")
            else:
                st.error("Could not find required columns (Hole ID, Length, Azimuth, Inclination)")

# ==========================================
# 5. VISUALIZATION
# ==========================================
elif category == "Visualization":
    view = st.radio("View Type", ["Whole Site Map", "Single Hole Analysis"], horizontal=True)
    if active_proj is not None:
        # Fetching 'depth' from DB but renaming to 'length' immediately for the app
        q = f"""SELECT h.*, s.depth as length, s.azimuth, s.inclination 
                FROM `sensorpush-export.survey.holes` h 
                LEFT JOIN `sensorpush-export.survey.surveys` s ON h.hole_id = s.hole_id 
                WHERE h.project_id = '{active_proj['project_id']}'"""
        df_viz = run_query(q)

        if view == "Single Hole Analysis":
            surveyed_ids = df_viz.dropna(subset=['length'])['hole_id'].unique()
            if len(surveyed_ids) > 0:
                target = st.selectbox("Select Hole", sorted(surveyed_ids))
                df_h = df_viz[df_viz['hole_id'] == target].copy()
                processed = calculate_survey_path(df_h, df_h['design_n'].iloc[0], df_h['design_e'].iloc[0])
                
                fig = make_subplots(rows=1, cols=2, subplot_titles=("East Dev", "North Dev"))
                fig.add_trace(go.Scatter(x=processed['e_rel'], y=processed['length'], name="East"), row=1, col=1)
                fig.add_trace(go.Scatter(x=processed['n_rel'], y=processed['length'], name="North"), row=1, col=2)
                fig.update_yaxes(autorange="reversed", title="Length (ft)")
                st.plotly_chart(fig, use_container_width=True)

        elif view == "Single Hole Analysis":
            surveyed_ids = df_viz.dropna(subset=['length'])['hole_id'].unique()
            if len(surveyed_ids) > 0:
                target = st.selectbox("Select Hole", sorted(surveyed_ids))
                df_h = df_viz[df_viz['hole_id'] == target].copy()
                processed = calculate_survey_path(df_h, df_h['design_n'].iloc[0], df_h['design_e'].iloc[0])
                fig = make_subplots(rows=1, cols=2, subplot_titles=("East Dev", "North Dev"))
                fig.add_trace(go.Scatter(x=processed['e_rel'], y=processed['length'], name="East"), row=1, col=1)
                fig.add_trace(go.Scatter(x=processed['n_rel'], y=processed['length'], name="North"), row=1, col=2)
                fig.update_yaxes(autorange="reversed", title="Length (ft)")
                st.plotly_chart(fig, use_container_width=True)
