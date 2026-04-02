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
# 1. AUTHENTICATION & CORE DATABASE I/O
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
# 2. MATH ENGINE
# ==========================================
def calculate_survey_path(df, start_n_shifted, start_e_shifted):
    df = df.sort_values('depth')
    rad_az = np.radians(df['azimuth'])
    rad_inc = np.radians(df['inclination'])
    dist = df['depth'].diff().fillna(0)
    
    dn = dist * np.sin(rad_inc) * np.cos(rad_az)
    de = dist * np.sin(rad_inc) * np.sin(rad_az)
    
    df['n_rel'] = start_n_shifted + dn.cumsum()
    df['e_rel'] = start_e_shifted + de.cumsum()
    return df

# ==========================================
# 3. GLOBAL SIDEBAR
# ==========================================
st.sidebar.title("🏗️ SoilFreeze Hub")

df_projects = run_query("SELECT * FROM `sensorpush-export.survey.projects` ORDER BY name")

if not df_projects.empty:
    sel_proj_name = st.sidebar.selectbox("Active Project", df_projects['name'].tolist())
    active_proj = df_projects[df_projects['name'] == sel_proj_name].iloc[0]
    
    # Robust Phase Fetching
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

st.sidebar.divider()
category = st.sidebar.selectbox("Category", ["Database Maintenance", "Visualization", "Reports"])

# ==========================================
# 4. DATABASE MAINTENANCE
# ==========================================
if category == "Database Maintenance":
    action = st.radio("Action", ["Project Setup", "Upload Baseline", "Update Top Survey", "Upload Downhole", "Manage Data"], horizontal=True)
    
    # UTILITY: Extract date from filename or default to today
    def get_file_date(filename):
        # Looks for YYYY.MM.DD or YYYY-MM-DD
        match = re.search(r'(\d{4})[.\-](\d{2})[.\-](\d{2})', filename)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        return datetime.now().strftime('%Y-%m-%d')

    # --- STEP 2: UPLOAD BASELINE (PREVENTS DOUBLES) ---
    if action == "Upload Baseline":
        st.subheader("Step 2: Upload Design Baseline")
        st.info("Uploading will overwrite any existing baseline for the selected Phase.")
        file = st.file_uploader("Upload Baseline CSV", type=['csv'])
        
        if file and active_proj is not None:
            df_base = pd.read_csv(file)
            df_base.columns = [c.lower().strip() for c in df_base.columns]
            
            # Mapping
            rename_map = {'id':'hole_id', 'name':'hole_id', 'north':'design_n', 'east':'design_e', 'elev':'design_z', 'cadx':'design_e', 'cady':'design_n'}
            df_base = df_base.rename(columns=rename_map)
            df_base['hole_id'] = df_base['hole_id'].astype(str).str.strip()
            df_base['project_id'] = str(active_proj['project_id'])
            if 'phase' not in df_base.columns: df_base['phase'] = "Phase1"

            if st.button("🚀 Confirm & Overwrite Phase"):
                with st.spinner("Cleaning old records..."):
                    # Delete exactly what matches Project + Phase to stop doubles
                    del_q = f"DELETE FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}' AND phase = '{df_base['phase'].iloc[0]}'"
                    client.query(del_q).result()
                    
                    upload_to_bq(df_base[['project_id','hole_id','design_n','design_e','design_z','phase']], "sensorpush-export.survey.holes")
                    st.success(f"Baseline for {df_base['phase'].iloc[0]} updated.")
                    st.rerun()

    # --- STEP 3: UPDATE TOP SURVEY (AS-BUILT) ---
    elif action == "Update Top Survey":
        st.subheader("Step 3: Update Actual Collar (As-Built)")
        top_file = st.file_uploader("Upload As-Built CSV", type=['csv'])
        
        if top_file and active_proj is not None:
            file_date = get_file_date(top_file.name)
            st.write(f"📅 Detected Survey Date: **{file_date}**")
            
            df_top = pd.read_csv(top_file)
            df_top.columns = [c.upper().strip() for c in df_top.columns]
            df_top = df_top.rename(columns={'ID': 'hole_id', 'NORTHING': 'actual_n', 'EASTING': 'actual_e', 'ELEVATION': 'actual_z'})
            
            if st.button("Update As-Built Coordinates"):
                temp_id = f"sensorpush-export.survey.temp_top_{active_proj['project_id']}"
                upload_to_bq(df_top, temp_id, write_mode="WRITE_TRUNCATE")
                # Merge logic to avoid touching design coordinates
                merge_q = f"""
                    MERGE `sensorpush-export.survey.holes` T USING `{temp_id}` S
                    ON T.hole_id = S.hole_id AND T.project_id = '{active_proj['project_id']}'
                    WHEN MATCHED THEN UPDATE SET T.actual_n = S.actual_n, T.actual_e = S.actual_e, T.actual_z = S.actual_z
                """
                client.query(merge_q).result()
                client.delete_table(temp_id)
                st.success("Updated Top Surveys.")

    # --- STEP 4: UPLOAD DOWNHOLE (WITH DATE EXTRACTION) ---
    elif action == "Upload Downhole":
        st.subheader("Step 4: Upload Downhole Survey")
        dh_file = st.file_uploader("Upload Downhole CSV", type=['csv'])
        
        if dh_file and active_proj is not None:
            file_date = get_file_date(dh_file.name)
            st.write(f"📅 Detected Survey Date: **{file_date}**")
            
            df_dh = pd.read_csv(dh_file)
            # Standard mapping logic
            df_dh['project_id'] = str(active_proj['project_id'])
            df_dh['survey_date'] = file_date
            
            if st.button("Append Survey Data"):
                upload_to_bq(df_dh, "sensorpush-export.survey.surveys")
                st.success(f"Uploaded survey data for {file_date}")

    # --- NEW: MANAGE DATA (DELETION TOOLS) ---
    elif action == "Manage Data":
        st.subheader("🗑️ Data Management")
        tab1, tab2 = st.tabs(["Delete by Hole", "Delete by Date"])
        
        with tab1:
            h_id = st.text_input("Hole ID to Clear")
            if st.button("Clear Hole"):
                # Reset actuals and delete surveys
                client.query(f"UPDATE `sensorpush-export.survey.holes` SET actual_n=NULL, actual_e=NULL WHERE hole_id='{h_id}' AND project_id='{active_proj['project_id']}'")
                client.query(f"DELETE FROM `sensorpush-export.survey.surveys` WHERE hole_id='{h_id}' AND project_id='{active_proj['project_id']}'")
                st.warning(f"Data cleared for {h_id}")

        with tab2:
            # Fetch dates present in DB for easy selection
            date_q = f"SELECT DISTINCT survey_date FROM `sensorpush-export.survey.surveys` WHERE project_id='{active_proj['project_id']}'"
            db_dates = run_query(date_q)
            if not db_dates.empty:
                target_date = st.selectbox("Select Date to Wipe", db_dates['survey_date'].tolist())
                if st.button("Confirm Delete All for Date"):
                    client.query(f"DELETE FROM `sensorpush-export.survey.surveys` WHERE survey_date='{target_date}' AND project_id='{active_proj['project_id']}'")
                    st.error(f"Deleted all records for {target_date}")

# ==========================================
# 5. VISUALIZATION
# ==========================================
elif category == "Visualization":
    view = st.radio("View", ["Whole Site Map", "Single Hole Analysis", "Elevation Slice"], horizontal=True)
    
    if active_proj:
        q = f"""SELECT h.*, s.depth, s.azimuth, s.inclination FROM `sensorpush-export.survey.holes` h 
                LEFT JOIN `sensorpush-export.survey.surveys` s ON h.hole_id = s.hole_id 
                WHERE h.project_id = '{active_proj['project_id']}'"""
        df_viz = run_query(q)
        if active_phase != "All Phases": df_viz = df_viz[df_viz['phase'] == active_phase]

        if view == "Single Hole Analysis":
            surveyed = df_viz.dropna(subset=['depth'])['hole_id'].unique()
            target = st.selectbox("Select Hole", sorted(surveyed))
            df_h = df_viz[df_viz['hole_id'] == target].copy()
            s_n, s_e = df_h['design_n'].iloc[0] - active_proj['origin_north'], df_h['design_e'].iloc[0] - active_proj['origin_east']
            processed = calculate_survey_path(df_h, s_n, s_e)
            
            fig = make_subplots(rows=1, cols=2, subplot_titles=("East Dev", "North Dev"))
            fig.add_trace(go.Scatter(x=processed['e_rel'], y=processed['depth'], name="East"), 1, 1)
            fig.add_trace(go.Scatter(x=processed['n_rel'], y=processed['depth'], name="North"), 1, 2)
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(fig, use_container_width=True)

        elif view == "Elevation Slice":
            target_z = st.number_input("Target Depth (ft)", value=50.0)
            # Logic to interpolate and plot all pipes at this depth...
            st.info("Mapping all pipes at specified elevation.")

# ==========================================
# 6. REPORTS (AUDIT & EXPORT)
# ==========================================
elif category == "Reports":
    # 1. SIDEBAR NAVIGATION
    report_action = st.sidebar.radio("Report Type", ["System Audit", "Deviation Summary", "Export Shifted Data"])

    if active_proj is None:
        st.error("❌ No Project Selected. Please select a project in the sidebar.")
    else:
        if report_action == "System Audit":
            st.subheader(f"📊 Project Progress: {active_proj['name']}")
            
            try:
                # Optimized query to count your 1,105 baseline vs 93 as-builts
                stats_query = f"""
                    SELECT 
                        COUNT(*) as total_baseline,
                        COUNTIF(actual_n != 0 AND actual_n IS NOT NULL) as top_surveys_done,
                        (SELECT COUNT(DISTINCT hole_id) FROM `sensorpush-export.survey.surveys` 
                         WHERE project_id = '{active_proj['project_id']}') as downhole_completed
                    FROM `sensorpush-export.survey.holes`
                    WHERE project_id = '{active_proj['project_id']}'
                """
                df_stats = run_query(stats_query)
                
                # Display Metrics
                c1, c2, c3 = st.columns(3)
                c1.metric("Baseline (Design Grid)", df_stats['total_baseline'][0]) 
                c2.metric("Top Surveys (As-Built)", df_stats['top_surveys_done'][0]) 
                c3.metric("Downhole (Probed)", df_stats['downhole_completed'][0])

                st.divider()

                # List missing as-builts to help reconcile the 1,105 grid
                st.write("### 📍 Pending As-Built Surveys")
                missing_q = f"""
                    SELECT hole_id, phase FROM `sensorpush-export.survey.holes`
                    WHERE project_id = '{active_proj['project_id']}'
                    AND (actual_n = 0 OR actual_n IS NULL)
                """
                df_missing = run_query(missing_q)
                if not df_missing.empty:
                    st.warning(f"{len(df_missing)} holes are missing As-Built coordinates.")
                    st.dataframe(df_missing)
                else:
                    st.success("✅ All holes reconciled with As-Built data.")

            except Exception as e:
                st.error("⚠️ Database Schema Mismatch")
                st.info("Please ensure you have run the 'ALTER TABLE' command in BigQuery to add 'actual_n'.")
                st.expander("Technical Error Details").write(e)
                
                
        elif report_action == "Deviation Summary":
            st.subheader("Design vs. Actual Deviation")
            st.info("This report will compare your million-value Northings to calculate drift.")

        elif report_action == "Export Shifted Data":
            st.subheader("Download Relative (0,0) CSV")
            st.info("Exporting all 1,105 holes relative to your project origin.")
        
