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
    
##### Function Junction #####
def physically_rename_and_export(uploaded_file):
    # 1. Open the CSV
    df = pd.read_csv(uploaded_file)
    
    # 2. Create the physical rename map
    rename_map = {}
    for col in df.columns:
        c_low = col.lower().strip()
        # The specific "Length to Depth" fix
        if 'length' in c_low:
            rename_map[col] = 'depth'
        elif 'hole' in c_low or 'pipe' in c_low:
            rename_map[col] = 'hole_id'
        elif 'azi' in c_low:
            rename_map[col] = 'azimuth'
        elif 'inc' in c_low:
            rename_map[col] = 'inclination'
            
    # 3. Apply the rename physically to the dataframe
    df_fixed = df.rename(columns=rename_map)
    
    # 4. Convert back to CSV string for the download button
    return df_fixed, df_fixed.to_csv(index=False).encode('utf-8')
    
# ==========================================
# 2. MATH ENGINE (Standardized to 'length')
# ==========================================
def calculate_survey_path(df, start_n, start_e):
    # Sort by length for the calculation
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
    action = st.radio(
        "Action", 
        ["Project Setup", "Upload Baseline", "Update Top Survey", "Upload Downhole", "Manage Data"], 
        horizontal=True,
        key="db_maint_v2"
    )

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
            rename_map = {
                'pipe':'hole_id', 'id':'hole_id', 'hole':'hole_id',
                'north':'design_n', 'east':'design_e', 'elev':'design_z',
                'inc':'design_inc', 'az':'design_az', 'len':'design_length', 'length':'design_length'
            }
            df_base = df_base.rename(columns=rename_map).dropna(subset=['hole_id'])
            db_schema = {
                'project_id': str(active_proj['project_id']), 'hole_id': None,
                'design_n': 0.0, 'design_e': 0.0, 'design_z': 0.0, 'phase': "Phase1",
                'pipe_type': "Freeze Pipe", 'design_inc': 0.0, 'design_az': 0.0, 
                'design_length': active_proj.get('default_length', 100.0)
            }
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

    elif action == "Upload Downhole":
        st.subheader("Step 4: Physical File Fixer")
        dh_file = st.file_uploader("Upload Downhole CSV", type=['csv'])
        
        if dh_file:
            # 1. RUN THE PHYSICAL RENAME
            df_processed, csv_data = physically_rename_and_export(dh_file)
            
            # 2. SAVE BACK TO COMPUTER
            # Since I cannot force-save to your drive, this button allows you to 
            # save the "Depth" version immediately.
            new_filename = dh_file.name.replace(".csv", "_FIXED_DEPTH.csv")
            st.download_button(
                label="💾 Save Fixed CSV to Computer",
                data=csv_data,
                file_name=new_filename,
                mime='text/csv',
            )
            
            st.divider()
            
            # 3. PROCEED WITH REST OF CODE
            # Now the "Internal" version is already named depth
            f_date = get_smart_date(dh_file.name)
            st.info(f"📅 Detected Survey Date: **{f_date}**")
            
            req_cols = ['hole_id', 'depth', 'azimuth', 'inclination']
            if all(c in df_processed.columns for c in req_cols):
                # Metadata
                df_processed['project_id'] = str(active_proj['project_id'])
                df_processed['survey_date'] = f_date
                
                st.success("✅ Headers physically changed to 'depth' in memory.")
                st.write("### New File Preview")
                st.dataframe(df_processed[req_cols].head())

                if st.button("🚀 Upload to BigQuery"):
                    try:
                        final_cols = ['project_id', 'hole_id', 'depth', 'azimuth', 'inclination', 'survey_date']
                        upload_to_bq(df_processed[final_cols], "sensorpush-export.survey.surveys")
                        st.success("BigQuery Upload Successful.")
                    except Exception as e:
                        st.error(f"Upload failed: {e}")
            else:
                st.error(f"Mapping failed. Still missing: {set(req_cols) - set(df_processed.columns)}")


# ==========================================
# 5. VISUALIZATION (FULL ORIGINAL LOGIC)
# ==========================================
elif category == "Visualization":
    view = st.radio("View Type", ["Whole Site Map", "Single Hole Analysis", "Elevation Slice"], horizontal=True)
    if active_proj is not None:
        q = f"""SELECT h.*, s.depth as length, s.azimuth, s.inclination 
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
            # Battered tails
            battered = df_viz[df_viz['design_inc'] > 0].drop_duplicates('hole_id')
            for _, row in battered.iterrows():
                rad_az = np.radians(row['design_az'])
                dn, de = 5 * np.cos(rad_az), 5 * np.sin(rad_az)
                fig.add_trace(go.Scatter(x=[row['e_rel'], row['e_rel']+de], y=[row['n_rel'], row['n_rel']+dn], mode='lines', line=dict(color='red', width=1), showlegend=False))

            # Status Symbology
            for p_type, shape in [("Freeze Pipe", "circle"), ("Battered Freeze Pipe", "circle"), ("Temperature Pipe", "square")]:
                type_mask = df_viz['pipe_type'] == p_type
                if type_mask.any():
                    for status, color in [(False, 'lightgrey'), (True, 'black')]:
                        mask = type_mask & (df_viz['has_top'] == status)
                        fig.add_trace(go.Scatter(x=df_viz.loc[mask, 'e_rel'], y=df_viz.loc[mask, 'n_rel'], mode='markers', name=f"{p_type} ({'As-Built' if status else 'Design'})", marker=dict(symbol=f"{shape}-open", color=color, size=14, line=dict(width=2.5)), text=df_viz.loc[mask, 'hole_id']))
                        mask_dh = type_mask & (df_viz['has_downhole'] == status)
                        fig.add_trace(go.Scatter(x=df_viz.loc[mask_dh, 'e_rel'], y=df_viz.loc[mask_dh, 'n_rel'], mode='markers', showlegend=False, marker=dict(symbol=shape, color=color, size=6), text=df_viz.loc[mask_dh, 'hole_id']))

            fig.update_layout(xaxis=dict(title="East (ft)", scaleanchor="y", scaleratio=1), yaxis=dict(title="North (ft)"), height=850, template="plotly_white")
            st.plotly_chart(fig, use_container_width=True)

        elif view == "Single Hole Analysis":
            surveyed_ids = df_viz.dropna(subset=['length'])['hole_id'].unique()
            if len(surveyed_ids) > 0:
                target = st.selectbox("Select Hole", sorted(surveyed_ids))
                df_h = df_viz[df_viz['hole_id'] == target].copy()
                start_n = df_h['actual_n'].iloc[0] if pd.notnull(df_h['actual_n'].iloc[0]) and df_h['actual_n'].iloc[0] != 0 else df_h['design_n'].iloc[0]
                start_e = df_h['actual_e'].iloc[0] if pd.notnull(df_h['actual_e'].iloc[0]) and df_h['actual_e'].iloc[0] != 0 else df_h['design_e'].iloc[0]
                processed = calculate_survey_path(df_h, start_n - active_proj['origin_north'], start_e - active_proj['origin_east'])
                fig = make_subplots(rows=1, cols=2, subplot_titles=("East Dev", "North Dev"))
                fig.add_trace(go.Scatter(x=processed['e_rel'], y=processed['length'], name="East", line=dict(color='blue')), row=1, col=1)
                fig.add_trace(go.Scatter(x=processed['n_rel'], y=processed['length'], name="North", line=dict(color='red')), row=1, col=2)
                fig.update_yaxes(autorange="reversed", title="Length (ft)")
                st.plotly_chart(fig, use_container_width=True)

# ==========================================
# 6. REPORTS
# ==========================================
elif category == "Reports":
    st.subheader("Project Status Report")
    if active_proj is not None:
        # Combined query to get all stats at once
        stats_query = f"""
            SELECT 
                (SELECT COUNT(*) FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}') as total_holes,
                (SELECT COUNTIF(actual_n != 0 AND actual_n IS NOT NULL) FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}') as as_built_complete,
                (SELECT COUNT(DISTINCT hole_id) FROM `sensorpush-export.survey.surveys` WHERE project_id = '{active_proj['project_id']}') as probe_complete
        """
        df_stats = run_query(stats_query)
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Grid Holes", df_stats['total_holes'][0])
        col2.metric("Surface As-Builts", df_stats['as_built_complete'][0])
        col3.metric("Downhole Surveys", df_stats['probe_complete'][0])
        
        # Simple progress bar
        progress = df_stats['probe_complete'][0] / df_stats['total_holes'][0] if df_stats['total_holes'][0] > 0 else 0
        st.write(f"**Overall Survey Progress: {progress*100:.1f}%**")
        st.progress(progress)
