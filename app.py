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
    # This line MUST run first so 'action' is defined
    action = st.radio("Action", ["Project Setup", "Upload Baseline", "Update Top Survey", "Upload Downhole"], horizontal=True)
    
    if action == "Project Setup":
        tab1, tab2 = st.tabs(["Create New", "Edit Origin"])
        with tab1:
            with st.form("new_proj"):
                n_id = st.text_input("Project ID")
                n_name = st.text_input("Project Name")
                n_on = st.number_input("Origin Northing", format="%.3f")
                n_oe = st.number_input("Origin Easting", format="%.3f")
                if st.form_submit_button("Save"):
                    upload_to_bq(pd.DataFrame([{'project_id':n_id, 'name':n_name, 'origin_north':n_on, 'origin_east':n_oe}]), "sensorpush-export.survey.projects")
                    st.rerun()
        with tab2:
            if active_proj is not None:
                u_on = st.number_input("Update Origin North", value=float(active_proj['origin_north']), format="%.3f")
                u_oe = st.number_input("Update Origin East", value=float(active_proj['origin_east']), format="%.3f")
                if st.button("Update Origin"):
                    client.query(f"UPDATE `sensorpush-export.survey.projects` SET origin_north={u_on}, origin_east={u_oe} WHERE project_id='{active_proj['project_id']}'")
                    st.rerun()

    elif action == "Upload Baseline":
        st.subheader("Step 2: Upload Design Baseline")
        file = st.file_uploader("Upload Baseline CSV", type=['csv'])
        if file and active_proj is not None:
            df_base = pd.read_csv(file)
            df_base.columns = [c.lower().strip() for c in df_base.columns]
            # Mapping and Defaults
            df_base['hole_id'] = df_base['hole_id'].astype(str).str.strip()
            df_base['project_id'] = str(active_proj['project_id'])
            if 'phase' not in df_base.columns: df_base['phase'] = "Phase1"
            
            if st.button("Confirm Baseline Upload"):
                # Clean Upgrade logic to prevent duplicates
                delete_q = f"DELETE FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}' AND phase = '{df_base['phase'].iloc[0]}'"
                client.query(delete_q).result()
                upload_to_bq(df_base, "sensorpush-export.survey.holes")
                st.success("Baseline uploaded and old phase data cleared.")

    elif action == "Update Top Survey":
        st.subheader("Step 3: Update Actual Collar (As-Built)")
        # This now handles your 93-hole survey file
        top_file = st.file_uploader("Upload As-Built CSV", type=['csv'])
        if top_file and active_proj is not None:
            df_top = pd.read_csv(top_file)
            df_top.columns = [c.upper().strip() for c in df_top.columns]
            df_top = df_top.rename(columns={'ID': 'hole_id', 'NORTHING': 'actual_n', 'EASTING': 'actual_e', 'ELEVATION': 'actual_z'})
            df_top['hole_id'] = df_top['hole_id'].astype(str).str.strip()
            df_top['project_id'] = str(active_proj['project_id'])

            if st.button("🚀 Run As-Built Update"):
                temp_id = f"sensorpush-export.survey.temp_top_{active_proj['project_id']}"
                upload_to_bq(df_top, temp_id, write_mode="WRITE_TRUNCATE")
                # Update 'actual' columns only to keep design 1105 grid intact
                merge_q = f"""
                    MERGE `sensorpush-export.survey.holes` T USING `{temp_id}` S
                    ON T.hole_id = S.hole_id AND T.project_id = S.project_id
                    WHEN MATCHED THEN UPDATE SET T.actual_n = S.actual_n, T.actual_e = S.actual_e, T.actual_z = S.actual_z
                """
                client.query(merge_q).result()
                client.delete_table(temp_id)
                st.success("Updated Top Survey coordinates for matching holes.")

    elif action == "Upload Downhole":
        st.subheader("Step 4: Upload Downhole Survey")
        dh_file = st.file_uploader("Upload Downhole CSV", type=['csv'])
        if dh_file and active_proj is not None:
            df_dh = pd.read_csv(dh_file)
            df_dh['hole_id'] = df_dh['hole_id'].astype(str).str.strip()
            df_dh['project_id'] = str(active_proj['project_id'])
            if st.button("Append Survey Data"):
                upload_to_bq(df_dh, "sensorpush-export.survey.surveys")
                st.success("Data Appended!")

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
            
            # 2. RUN INTEGRATED STATS QUERY
            # 'top_surveys_done' now specifically counts the 'actual_n' column
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
            
            # 3. DISPLAY PROGRESS DASHBOARD
            c1, c2, c3 = st.columns(3)
            c1.metric("Baseline (Design Grid)", df_stats['total_baseline'][0]) # Should be 1105
            c2.metric("Top Surveys (As-Built)", df_stats['top_surveys_done'][0]) # Should be 93
            c3.metric("Downhole (Probed)", df_stats['downhole_completed'][0])

            st.divider()

            # 4. GAP ANALYSIS: IDENTIFY HOLES WITHOUT AS-BUILTS
            col_left, col_right = st.columns(2)

            with col_left:
                st.write("### 📍 Missing Top Surveys")
                # Lists holes that have design coordinates but no 'actual' survey yet
                missing_q = f"""
                    SELECT hole_id, phase 
                    FROM `sensorpush-export.survey.holes`
                    WHERE project_id = '{active_proj['project_id']}'
                    AND (actual_n = 0 OR actual_n IS NULL)
                """
                df_missing = run_query(missing_q)
                if not df_missing.empty:
                    st.warning(f"{len(df_missing)} holes are missing As-Built coordinates.")
                    st.dataframe(df_missing)
                else:
                    st.success("✅ All 1,105 holes have been surveyed at the surface.")

            with col_right:
                st.write("### 🛰️ Survey Run Audit (Doubles)")
                # Detects if any hole has multiple sets of data for the same depth
                dup_survey_q = f"""
                    SELECT hole_id, survey_type, COUNT(depth) as pts, COUNT(DISTINCT depth) as unique_depths
                    FROM `sensorpush-export.survey.surveys`
                    WHERE project_id = '{active_proj['project_id']}'
                    GROUP BY hole_id, survey_type
                    HAVING pts > unique_depths
                """
                df_dups = run_query(dup_survey_q)
                if not df_dups.empty:
                    st.error("Duplicate data points detected in Downhole surveys!")
                    st.dataframe(df_dups)
                else:
                    st.success("No overlapping survey points detected.")

        elif report_action == "Deviation Summary":
            st.subheader("Design vs. Actual Deviation")
            st.info("This report will compare your million-value Northings to calculate drift.")

        elif report_action == "Export Shifted Data":
            st.subheader("Download Relative (0,0) CSV")
            st.info("Exporting all 1,105 holes relative to your project origin.")
        
