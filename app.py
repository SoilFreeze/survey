import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots  # Ensure the 'p' is here!
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

# --- GLOBAL UTILITIES (Top of file) ---
def get_smart_date(name):
    """Extracts date from filename in various formats."""
    pattern = r'(\d{1,4})[.\-](\d{1,2})[.\-](\d{2,4})'
    m = re.search(pattern, name)
    if m:
        g = m.groups()
        if len(g[0]) == 4: # YYYY.MM.DD
            return f"{g[0]}-{g[1].zfill(2)}-{g[2].zfill(2)}"
        else: # MM.DD.YY or MM.DD.YYYY
            yr = "20" + g[2] if len(g[2]) == 2 else g[2]
            return f"{yr}-{g[0].zfill(2)}-{g[1].zfill(2)}"
    return datetime.now().strftime('%Y-%m-%d')
    
def run_query(query):
    return client.query(query).to_dataframe()

def upload_to_bq(df, table_id, write_mode="WRITE_APPEND"):
    job_config = bigquery.LoadJobConfig(write_disposition=write_mode)
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    return job.result()

def get_file_date(filename):
    match = re.search(r'(\d{4})[.\-](\d{2})[.\-](\d{2})', filename)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return datetime.now().strftime('%Y-%m-%d')

# ==========================================
# 2. MATH ENGINE (Battered Support)
# ==========================================
def calculate_survey_path(df, start_n, start_e):
    df = df.sort_values('length') 
    rad_az = np.radians(df['azimuth'])
    rad_inc = np.radians(df['inclination'])
    
    # Calculate distance based on 'length'
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
# 4. DATABASE MAINTENANCE (MATCHED TO STANDARDS)
# ==========================================
if category == "Database Maintenance":
    action = st.radio(
        "Action", 
        ["Project Setup", "Upload Baseline", "Update Top Survey", "Upload Downhole", "Manage Data"], 
        horizontal=True
    )

    # --- STEP 1: PROJECT SETUP ---
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

    # --- STEP 2: UPLOAD BASELINE (1,105 GRID) ---
    elif action == "Upload Baseline":
        st.subheader("2. Upload Baseline Grid")
        file = st.file_uploader("Upload Baseline CSV", type=['csv'])
        if file and active_proj is not None:
            df_base = pd.read_csv(file)
            df_base.columns = [c.lower().strip() for c in df_base.columns]
            
            # Map 'pipe' or 'id' to 'hole_id'
            rename_map = {
                'pipe':'hole_id', 'id':'hole_id', 'hole':'hole_id',
                'north':'design_n', 'east':'design_e', 'elev':'design_z',
                'inc':'design_inc', 'az':'design_az', 'len':'design_length', 'length':'design_length'
            }
            df_base = df_base.rename(columns=rename_map)
            
            # Clean nulls
            df_base = df_base.dropna(subset=['hole_id'])
            
            # Force Schema
            db_schema = {
                'project_id': str(active_proj['project_id']), 'hole_id': None,
                'design_n': 0.0, 'design_e': 0.0, 'design_z': 0.0, 'phase': "Phase1",
                'pipe_type': "Freeze Pipe", 'design_inc': 0.0, 'design_az': 0.0, 
                'design_length': active_proj.get('default_length', 100.0)
            }
            for col, default in db_schema.items():
                if col not in df_base.columns: df_base[col] = default

            st.dataframe(df_base[['hole_id', 'design_n', 'design_e', 'pipe_type']].head())
            if st.button("🚀 Confirm Overwrite"):
                p_id, ph = str(active_proj['project_id']), df_base['phase'].iloc[0]
                client.query(f"DELETE FROM `sensorpush-export.survey.holes` WHERE project_id='{p_id}' AND phase='{ph}'").result()
                upload_to_bq(df_base[list(db_schema.keys())], "sensorpush-export.survey.holes")
                st.success("Grid Updated.")

    # --- STEP 3: UPDATE TOP SURVEY (AS-BUILT) ---
    elif action == "Update Top Survey":
        st.subheader("3. Sync As-Built Surface Surveys")
        top_file = st.file_uploader("Upload As-Built CSV", type=['csv'])
        if top_file and active_proj is not None:
            df_top = pd.read_csv(top_file)
            df_top.columns = [c.upper().strip() for c in df_top.columns]
            # Mapping based on your 'As Built Survey.csv'
            df_top = df_top.rename(columns={'ID': 'hole_id', 'NORTHING': 'actual_n', 'EASTING': 'actual_e', 'ELEVATION': 'actual_z'})
            
            if st.button("Apply Surface Coordinates"):
                temp_id = f"sensorpush-export.survey.temp_top_{active_proj['project_id']}"
                upload_to_bq(df_top, temp_id, write_mode="WRITE_TRUNCATE")
                merge_q = f"""
                    MERGE `sensorpush-export.survey.holes` T USING `{temp_id}` S 
                    ON T.hole_id = S.hole_id AND T.project_id = '{active_proj['project_id']}' 
                    WHEN MATCHED THEN UPDATE SET T.actual_n = S.actual_n, T.actual_e = S.actual_e, T.actual_z = S.actual_z
                """
                client.query(merge_q).result()
                client.delete_table(temp_id)
                st.success("Surface As-Builts updated.")

    # --- STEP 4: UPLOAD DOWNHOLE (PROBE DATA) ---
    elif action == "Upload Downhole":
        st.subheader("Step 4: Upload Probe Data")
        dh_file = st.file_uploader("Upload Downhole CSV", type=['csv'])
        
        if dh_file and active_proj is not None:
            # This call will now succeed because the function is at the top
            f_date = get_smart_date(dh_file.name)
            st.info(f"📅 Detected Survey Date: **{f_date}**")
            
            # Load with BOM handling to prevent hidden character errors
            df_dh = pd.read_csv(dh_file, encoding='utf-8-sig')
            
            # 3. BUILD THE TRANSLATION DICTIONARY
            mapping = {}
            for col in df_dh.columns:
                c_low = col.lower().strip()
                if any(kw in c_low for kw in ['hole', 'pipe']):
                    mapping[col] = 'hole_id'
                elif any(kw in c_low for kw in ['length', 'depth', 'md']):
                    mapping[col] = 'length'  # Renaming everything to length
                elif 'azi' in c_low:
                    mapping[col] = 'azimuth'
                elif 'inc' in c_low:
                    mapping[col] = 'inclination'

            df_dh = df_dh.rename(columns=mapping)

            # 4. VALIDATION: Looking for 'length', NOT 'depth'
            req_cols = ['hole_id', 'length', 'azimuth', 'inclination']
            missing = [c for c in req_cols if c not in df_dh.columns]
            
            if not missing:
                df_dh['project_id'] = str(active_proj['project_id'])
                df_dh['survey_date'] = f_date
                
                # Cleanup Data Types
                df_dh['length'] = pd.to_numeric(df_dh['length'], errors='coerce')
                df_dh['azimuth'] = pd.to_numeric(df_dh['azimuth'], errors='coerce').fillna(0.0)
                df_dh['inclination'] = pd.to_numeric(df_dh['inclination'], errors='coerce').fillna(0.0)
                
                # Drop rows where 'length' is missing (prevents BQ schema errors)
                df_dh = df_dh.dropna(subset=['length', 'hole_id'])

                st.write("### ✅ Mapping Success")
                st.dataframe(df_dh[req_cols].head())

                if st.button("🚀 Upload to BigQuery"):
                    with st.spinner("Uploading..."):
                        try:
                            final_cols = ['project_id', 'hole_id', 'length', 'azimuth', 'inclination', 'survey_date']
                            upload_to_bq(df_dh[final_cols], "sensorpush-export.survey.surveys")
                            st.success(f"Successfully uploaded {len(df_dh)} points.")
                        except Exception as e:
                            st.error(f"BigQuery Error: {e}")
            else:
                st.error(f"CSV is missing required columns: {', '.join(missing)}")
                st.write("Found headers:", list(df_dh.columns))
                
    # --- STEP 5: MANAGE DATA ---
    elif action == "Manage Data":
        st.subheader("5. Data Cleanup")
        with st.expander("⚠️ DANGER ZONE: Delete Project"):
            confirm = st.text_input(f"Type '{active_proj['name']}' to confirm deletion")
            if st.button("DELETE PROJECT PERMANENTLY"):
                if confirm == active_proj['name']:
                    p_id = active_proj['project_id']
                    client.query(f"DELETE FROM `sensorpush-export.survey.surveys` WHERE project_id='{p_id}'").result()
                    client.query(f"DELETE FROM `sensorpush-export.survey.holes` WHERE project_id='{p_id}'").result()
                    client.query(f"DELETE FROM `sensorpush-export.survey.projects` WHERE project_id='{p_id}'").result()
                    st.rerun()


# ==========================================
# 5. VISUALIZATION (Fully Scrubbed for 'length')
# ==========================================
elif category == "Visualization":
    view = st.radio("View Type", ["Whole Site Map", "Single Hole Analysis", "Elevation Slice"], horizontal=True)
    
    if active_proj is not None:
        # Pull data using the 'length' column name
        q = f"""SELECT h.*, s.length, s.azimuth, s.inclination 
                FROM `sensorpush-export.survey.holes` h 
                LEFT JOIN `sensorpush-export.survey.surveys` s ON h.hole_id = s.hole_id 
                WHERE h.project_id = '{active_proj['project_id']}'"""
        df_viz = run_query(q)
        
        if active_phase != "All Phases":
            df_viz = df_viz[df_viz['phase'] == active_phase]

        if view == "Whole Site Map":
            st.subheader(f"Project Grid: {active_proj['name']}")
            df_viz['n_rel'] = df_viz['design_n'] - active_proj['origin_north']
            df_viz['e_rel'] = df_viz['design_e'] - active_proj['origin_east']
            df_viz['has_top'] = df_viz['actual_n'].notnull() & (df_viz['actual_n'] != 0)
            df_viz['has_downhole'] = df_viz['length'].notnull()
            
            fig = go.Figure()
            # Plotting logic for hole status...
            for status, color in [(False, 'lightgrey'), (True, 'black')]:
                mask = df_viz['has_downhole'] == status
                fig.add_trace(go.Scatter(x=df_viz.loc[mask, 'e_rel'], y=df_viz.loc[mask, 'n_rel'],
                                         mode='markers', name=f"Downhole: {status}", marker=dict(color=color)))
            st.plotly_chart(fig, use_container_width=True)

        elif view == "Single Hole Analysis":
            surveyed_ids = df_viz.dropna(subset=['length'])['hole_id'].unique()
            if len(surveyed_ids) > 0:
                target = st.selectbox("Select Hole", sorted(surveyed_ids))
                df_h = df_viz[df_viz['hole_id'] == target].copy()
                
                # Pivot anchor point logic
                start_n = (df_h['actual_n'].iloc[0] or df_h['design_n'].iloc[0]) - active_proj['origin_north']
                start_e = (df_h['actual_e'].iloc[0] or df_h['design_e'].iloc[0]) - active_proj['origin_east']
                
                processed = calculate_survey_path(df_h, start_n, start_e)
                
                fig = make_subplots(rows=1, cols=2, subplot_titles=("East Dev (ft)", "North Dev (ft)"))
                fig.add_trace(go.Scatter(x=processed['e_rel'], y=processed['length'], name="East"), row=1, col=1)
                fig.add_trace(go.Scatter(x=processed['n_rel'], y=processed['length'], name="North"), row=1, col=2)
                fig.update_yaxes(autorange="reversed", title="Length (ft)")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("No survey data (length) found for this project.")

        elif view == "Elevation Slice":
            slice_val = st.slider("Target Length (ft)", 0, 250, 50)
            st.info(f"Visualizing intersections at {slice_val}ft.")
            
# ==========================================
# 6. REPORTS
# ==========================================
elif category == "Reports":
    report_action = st.sidebar.radio("Report Type", ["System Audit", "Deviation Summary"])
    if report_action == "System Audit":
        stats_query = f"""SELECT COUNT(*) as total_baseline, COUNTIF(actual_n != 0 AND actual_n IS NOT NULL) as top_surveys_done,
                        (SELECT COUNT(DISTINCT hole_id) FROM `sensorpush-export.survey.surveys` WHERE project_id = '{active_proj['project_id']}') as downhole_completed
                        FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}'"""
        df_stats = run_query(stats_query)
        st.metric("Baseline (Grid)", df_stats['total_baseline'][0])
        st.metric("Top Surveys (As-Built)", df_stats['top_surveys_done'][0])
        st.metric("Downhole Surveys", df_stats['downhole_completed'][0])
