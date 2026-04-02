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

def get_smart_date(filename):
    # Specifically targets Month-Day-Year (e.g., 2-18-26)
    match = re.search(r'(\d{1,2})[.\-](\d{1,2})[.\-](\d{2,4})', filename)
    if match:
        month, day, year = match.groups()
        # Convert '26' to '2026'
        full_year = f"20{year}" if len(year) == 2 else year
        return f"{full_year}-{month.zfill(2)}-{day.zfill(2)}"
    return datetime.now().strftime('%Y-%m-%d')

def standardize_survey_data(df):
    """
    Maps varied CSV headers (length, depth, dist, etc.) to a 
    standard internal 'length' column for calculations.
    """
    # 1. Create a map of potential variations to our standard internal names
    col_map = {}
    for col in df.columns:
        c_low = col.lower().strip()
        # Map Hole ID variations
        if any(k in c_low for k in ['hole', 'pipe', 'id']): 
            col_map[col] = 'hole_id'
        # Map Length/Depth variations to 'length' internally
        elif any(k in c_low for k in ['length', 'depth', 'dist']): 
            col_map[col] = 'length'
        # Map Azimuth variations
        elif 'azi' in c_low: 
            col_map[col] = 'azimuth'
        # Map Inclination variations
        elif 'inc' in c_low: 
            col_map[col] = 'inclination'

    # 2. Rename columns based on the harvester results
    df = df.rename(columns=col_map)
    
    # 3. Clean and convert to numeric to prevent math errors
    for c in ['length', 'azimuth', 'inclination']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)
            
    return df

def harmonize_probe_data(df):
    """Maps any variation of headers to standard internal names."""
    col_map = {}
    for col in df.columns:
        c_low = col.lower().strip()
        if any(k in c_low for k in ['hole', 'pipe', 'id']): col_map[col] = 'hole_id'
        elif any(k in c_low for k in ['length', 'depth', 'dist']): col_map[col] = 'length'
        elif 'azi' in c_low: col_map[col] = 'azimuth'
        elif 'inc' in c_low: col_map[col] = 'inclination'
    
    df = df.rename(columns=col_map)
    # Force numeric types to prevent BQ Schema errors
    for c in ['length', 'azimuth', 'inclination']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)
    return df


elif action == "Upload Downhole":
        st.subheader("Step 4: Upload Probe Data")
        dh_file = st.file_uploader("Upload Downhole CSV", type=['csv'])
        
        if dh_file and active_proj is not None:
            # --- 1. NEW ROBUST DATE PARSER ---
            def get_filename_date(name):
                # Target M-D-Y (e.g., 2-18-26)
                match = re.search(r'(\d{1,2})[.\-](\d{1,2})[.\-](\d{2,4})', name)
                if match:
                    m, d, y = match.groups()
                    full_yr = f"20{y}" if len(y) == 2 else y
                    return f"{full_yr}-{m.zfill(2)}-{d.zfill(2)}"
                return datetime.now().strftime('%Y-%m-%d')

            f_date = get_filename_date(dh_file.name)
            st.info(f"📅 Detected Survey Date: **{f_date}**")
            
            # --- 2. LOAD & POSITIONAL MAPPING ---
            df_dh = pd.read_csv(dh_file)
            
            # Show headers in the console for debugging
            # st.write("Original Headers:", list(df_dh.columns))

            # Forcefully map by column index if the names are being stubborn
            # Based on your previous output: Index 3=hole_id, 6=azimuth, 7=inclination, 8=length
            try:
                processed_df = pd.DataFrame({
                    'hole_id': df_dh.iloc[:, 3].astype(str),
                    'depth': pd.to_numeric(df_dh.iloc[:, 8], errors='coerce'),
                    'azimuth': pd.to_numeric(df_dh.iloc[:, 6], errors='coerce'),
                    'inclination': pd.to_numeric(df_dh.iloc[:, 7], errors='coerce')
                })
            except Exception as e:
                st.error(f"Positional mapping failed: {e}")
                st.stop()

            # --- 3. CLEANUP & PREVIEW ---
            processed_df['project_id'] = str(active_proj['project_id'])
            processed_df['survey_date'] = f_date
            processed_df = processed_df.dropna(subset=['depth']) # Remove empty rows

            st.write("### Data Preview (Positional Mapping Success)")
            st.dataframe(processed_df.head())

            # --- 4. UPLOAD ---
            if st.button("🚀 Upload to BigQuery"):
                with st.spinner("Uploading..."):
                    try:
                        # Table Schema matches 'depth'
                        final_cols = ['project_id', 'hole_id', 'depth', 'azimuth', 'inclination', 'survey_date']
                        upload_to_bq(processed_df[final_cols], "sensorpush-export.survey.surveys")
                        st.success(f"Uploaded {len(processed_df)} points for {f_date}")
                    except Exception as e:
                        st.error(f"BigQuery Error: {e}")

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
        st.subheader("Step 4: Upload Probe Data")
        dh_file = st.file_uploader("Upload Downhole CSV", type=['csv'])
        
        if dh_file and active_proj is not None:
            # --- 1. THE FOOLPROOF DATE PARSER ---
            # Forces 2-18-26 into 2026-02-18
            def get_filename_date(name):
                match = re.search(r'(\d{1,2})[.\-](\d{1,2})[.\-](\d{2,4})', name)
                if match:
                    m, d, y = match.groups()
                    year = f"20{y}" if len(y) == 2 else y
                    return f"{year}-{m.zfill(2)}-{d.zfill(2)}"
                return datetime.now().strftime('%Y-%m-%d')

            f_date = get_filename_date(dh_file.name)
            st.info(f"📅 Detected Survey Date: **{f_date}**")
            
            # --- 2. LOAD WITHOUT HEADERS ---
            # We skip the first row and manually assign names to columns by their order
            raw_data = pd.read_csv(dh_file, header=0) 
            
            # Create a clean dataframe by grabbing the exact column numbers
            # Based on your CSV: 3=HoleID, 6=Azimuth, 7=Inclination, 8=Length
            df_cleaned = pd.DataFrame()
            try:
                df_cleaned['hole_id'] = raw_data.iloc[:, 3].astype(str).str.strip()
                df_cleaned['azimuth'] = pd.to_numeric(raw_data.iloc[:, 6], errors='coerce').fillna(0.0)
                df_cleaned['inclination'] = pd.to_numeric(raw_data.iloc[:, 7], errors='coerce').fillna(0.0)
                # We label it 'depth' here so the rest of the script (and BQ) is happy
                df_cleaned['depth'] = pd.to_numeric(raw_data.iloc[:, 8], errors='coerce').fillna(0.0)
                
                df_cleaned['project_id'] = str(active_proj['project_id'])
                df_cleaned['survey_date'] = f_date
            except Exception as e:
                st.error(f"Logic Error: Could not find data at expected positions. {e}")
                st.stop()

            # --- 3. PREVIEW ---
            st.write("### Data Preview (Positional Extraction)")
            st.dataframe(df_cleaned[['hole_id', 'depth', 'azimuth', 'inclination']].head())

            # --- 4. THE UPLOAD ---
            if st.button("🚀 Upload to BigQuery"):
                with st.spinner("Pushing to BigQuery..."):
                    try:
                        # Direct upload using the names BigQuery expects
                        final_cols = ['project_id', 'hole_id', 'depth', 'azimuth', 'inclination', 'survey_date']
                        upload_to_bq(df_cleaned[final_cols], "sensorpush-export.survey.surveys")
                        st.success(f"Success! {len(df_cleaned)} rows uploaded.")
                    except Exception as e:
                        st.error(f"BigQuery Reject: {e}")

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
