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

def get_file_date(filename):
    match = re.search(r'(\d{4})[.\-](\d{2})[.\-](\d{2})', filename)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return datetime.now().strftime('%Y-%m-%d')

# ==========================================
# 2. MATH ENGINE (Battered Support)
# ==========================================
def calculate_survey_path(df, start_n, start_e):
    df = df.sort_values('depth')
    rad_az = np.radians(df['azimuth'])
    rad_inc = np.radians(df['inclination'])
    dist = df['depth'].diff().fillna(0)
    
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
        with st.form("new_proj"):
            n_id = st.text_input("Project ID")
            n_name = st.text_input("Project Name")
            n_len = st.number_input("Default Pipe Length (ft)", value=100.0)
            n_on = st.number_input("Origin Northing", format="%.3f")
            n_oe = st.number_input("Origin Easting", format="%.3f")
            if st.form_submit_button("Save"):
                new_df = pd.DataFrame([{'project_id':n_id, 'name':n_name, 'default_length':n_len, 'origin_north':n_on, 'origin_east':n_oe}])
                upload_to_bq(new_df, "sensorpush-export.survey.projects")
                st.success("Project Created!")
                st.rerun()

    elif action == "Upload Baseline":
        st.subheader("Step 2: Upload Design Baseline")
        file = st.file_uploader("Upload CSV", type=['csv'])
        if file and active_proj is not None:
            df_base = pd.read_csv(file)
            df_base.columns = [c.lower().strip() for c in df_base.columns]
            
            # Expanded mapping for Battered pipes
            rename_map = {
                'id':'hole_id', 'name':'hole_id', 'north':'design_n', 'east':'design_e', 
                'elev':'design_z', 'inc':'design_inc', 'az':'design_az', 'length':'design_length'
            }
            df_base = df_base.rename(columns=rename_map)
            df_base['project_id'] = str(active_proj['project_id'])
            
            if 'phase' not in df_base.columns: df_base['phase'] = "Phase1"
            if 'design_length' not in df_base.columns: df_base['design_length'] = active_proj['default_length']
            if 'design_inc' not in df_base.columns: df_base['design_inc'] = 0.0 # Vertical default
            if 'design_az' not in df_base.columns: df_base['design_az'] = 0.0

            if st.button("Confirm & Overwrite Phase"):
                del_q = f"DELETE FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}' AND phase = '{df_base['phase'].iloc[0]}'"
                client.query(del_q).result()
                upload_to_bq(df_base, "sensorpush-export.survey.holes")
                st.success("Baseline Updated.")

    elif action == "Update Top Survey":
        st.subheader("Step 3: Update Actual Collar (As-Built)")
        top_file = st.file_uploader("Upload As-Built CSV", type=['csv'])
        if top_file and active_proj is not None:
            df_top = pd.read_csv(top_file)
            df_top.columns = [c.upper().strip() for c in df_top.columns]
            df_top = df_top.rename(columns={'ID': 'hole_id', 'NORTHING': 'actual_n', 'EASTING': 'actual_e', 'ELEVATION': 'actual_z'})
            if st.button("Update As-Built"):
                temp_id = f"sensorpush-export.survey.temp_top_{active_proj['project_id']}"
                upload_to_bq(df_top, temp_id, write_mode="WRITE_TRUNCATE")
                merge_q = f"""MERGE `sensorpush-export.survey.holes` T USING `{temp_id}` S 
                             ON T.hole_id = S.hole_id AND T.project_id = '{active_proj['project_id']}' 
                             WHEN MATCHED THEN UPDATE SET T.actual_n = S.actual_n, T.actual_e = S.actual_e, T.actual_z = S.actual_z"""
                client.query(merge_q).result()
                client.delete_table(temp_id)
                st.success("Top Surveys Updated.")

    elif action == "Manage Data":
        st.subheader("🗑️ Data & Project Management")
        with st.expander("⚠️ DANGER ZONE: Delete Project"):
            confirm = st.text_input(f"Type '{active_proj['name']}' to confirm deletion")
            if st.button("DELETE PROJECT PERMANENTLY"):
                if confirm == active_proj['name']:
                    p_id = active_proj['project_id']
                    client.query(f"DELETE FROM `sensorpush-export.survey.surveys` WHERE project_id='{p_id}'").result()
                    client.query(f"DELETE FROM `sensorpush-export.survey.holes` WHERE project_id='{p_id}'").result()
                    client.query(f"DELETE FROM `sensorpush-export.survey.projects` WHERE project_id='{p_id}'").result()
                    st.rerun()
elif action == "Upload Downhole":
    st.subheader("Step 4: Upload Downhole Survey")
    dh_file = st.file_uploader("Upload Downhole CSV", type=['csv'])
    
    if dh_file and active_proj is not None:
        file_date = get_file_date(dh_file.name)
        st.info(f"📅 Extracting data for: {file_date}")
        
        # Read and Clean
        df_dh = pd.read_csv(dh_file)
        df_dh.columns = [c.lower().strip() for c in df_dh.columns]
        
        # Ensure critical columns exist
        required = ['hole_id', 'depth', 'azimuth', 'inclination']
        missing = [col for col in required if col not in df_dh.columns]
        
        if not missing:
            df_dh['project_id'] = str(active_proj['project_id'])
            df_dh['survey_date'] = file_date
            # Ensure Hole ID is a clean string
            df_dh['hole_id'] = df_dh['hole_id'].astype(str).str.strip()

            if st.button("🚀 Upload Survey Data"):
                try:
                    # Select only the columns that match your BigQuery table
                    final_cols = ['project_id', 'hole_id', 'depth', 'azimuth', 'inclination', 'survey_date']
                    upload_to_bq(df_dh[final_cols], "sensorpush-export.survey.surveys")
                    st.success(f"Successfully uploaded {len(df_dh)} data points.")
                except Exception as e:
                    st.error(f"BigQuery Error: {e}")
        else:
            st.error(f"CSV is missing columns: {', '.join(missing)}")
# ==========================================
# 5. VISUALIZATION
# ==========================================
elif category == "Visualization":
    view = st.radio("View Type", ["Whole Site Map", "Single Hole Analysis", "Elevation Slice"], horizontal=True)
    
    if active_proj is not None:
        q = f"""SELECT h.*, s.depth, s.azimuth, s.inclination 
                FROM `sensorpush-export.survey.holes` h 
                LEFT JOIN `sensorpush-export.survey.surveys` s ON h.hole_id = s.hole_id 
                WHERE h.project_id = '{active_proj['project_id']}'"""
        df_viz = run_query(q)
        
        if active_phase != "All Phases":
            df_viz = df_viz[df_viz['phase'] == active_phase]

        if view == "Whole Site Map":
            df_viz['n_rel'] = df_viz['design_n'] - active_proj['origin_north']
            df_viz['e_rel'] = df_viz['design_e'] - active_proj['origin_east']
            df_viz['has_top'] = df_viz['actual_n'].notnull() & (df_viz['actual_n'] != 0)
            df_viz['has_downhole'] = df_viz['depth'].notnull()
            
            fig = go.Figure()

            # Battered Pipe Indicators (Red Tails)
            battered = df_viz[df_viz['design_inc'] > 0].drop_duplicates('hole_id')
            for _, row in battered.iterrows():
                rad_az = np.radians(row['design_az'])
                # Show direction of lean (5ft indicator)
                dn = 5 * np.cos(rad_az)
                de = 5 * np.sin(rad_az)
                fig.add_trace(go.Scatter(x=[row['e_rel'], row['e_rel']+de], y=[row['n_rel'], row['n_rel']+dn], mode='lines', line=dict(color='red', width=1), showlegend=False))

            # Symbology: Outer Ring (Top) and Inner Dot (Downhole)
            for status, color in [(False, 'lightgrey'), (True, 'black')]:
                mask = df_viz['has_top'] == status
                fig.add_trace(go.Scatter(x=df_viz.loc[mask, 'e_rel'], y=df_viz.loc[mask, 'n_rel'], mode='markers', name=f"{'Actual' if status else 'Design'} Top", marker=dict(symbol='circle-open', color=color, size=12, line=dict(width=2)), text=df_viz.loc[mask, 'hole_id']))
                
                mask_dh = df_viz['has_downhole'] == status
                fig.add_trace(go.Scatter(x=df_viz.loc[mask_dh, 'e_rel'], y=df_viz.loc[mask_dh, 'n_rel'], mode='markers', name=f"{'Probed' if status else 'Pending'} DH", marker=dict(symbol='circle', color=color, size=5), text=df_viz.loc[mask_dh, 'hole_id']))

            fig.update_layout(xaxis=dict(scaleanchor="y", scaleratio=1), height=800)
            st.plotly_chart(fig, use_container_width=True)

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
