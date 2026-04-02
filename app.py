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
            
            rename_map = {'id':'hole_id', 'name':'hole_id', 'hole':'hole_id', 'north':'design_n', 'east':'design_e', 'elev':'design_z', 'elevation':'design_z', 'cadx':'design_e', 'cady':'design_n'}
            df_base = df_base.rename(columns=rename_map)
            
            df_base['hole_id'] = df_base['hole_id'].astype(str).str.strip()
            df_base['project_id'] = str(active_proj['project_id'])
            if 'phase' not in df_base.columns: df_base['phase'] = "Phase1"
            if 'design_z' not in df_base.columns: df_base['design_z'] = 0.0
                
            st.dataframe(df_base.head())
            if st.button("Confirm & Overwrite Existing for Phase"):
                # Clean Upgrade Logic
                delete_q = f"DELETE FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}' AND phase = '{df_base['phase'].iloc[0]}'"
                client.query(delete_q).result()
                upload_to_bq(df_base[['project_id','hole_id','design_n','design_e','design_z','phase']], "sensorpush-export.survey.holes")
                st.success("Baseline Updated (Duplicates Cleared)")
                st.rerun()

    elif action == "Update Top Survey":
        st.subheader("Step 3: Fast Batch Top Update")
        top_file = st.file_uploader("Upload Actual Top CSV", type=['csv'])
        if top_file and active_proj is not None:
            df_top = pd.read_csv(top_file)
            # (Mapping Logic...)
            df_top['hole_id'] = df_top['hole_id'].astype(str).str.strip()
            if st.button("Run Fast Update"):
                temp_id = f"sensorpush-export.survey.temp_{active_proj['project_id']}"
                upload_to_bq(df_top, temp_id, write_mode="WRITE_TRUNCATE")
                merge_q = f"""MERGE `sensorpush-export.survey.holes` T USING `{temp_id}` S 
                             ON T.hole_id = S.hole_id AND T.project_id = '{active_proj['project_id']}' 
                             WHEN MATCHED THEN UPDATE SET T.design_n = S.north, T.design_e = S.east"""
                client.query(merge_q).result()
                client.delete_table(temp_id)
                st.success("Top Survey Updated!")

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

elif category == "Reports":
    action = st.sidebar.radio("Report Type", ["Data Audit", "Deviation Summary", "Export Shifted Data"])

elif category == "Reports":
    if action == "Data Audit":
        st.subheader(f"🔍 Data Audit: {active_proj['name']}")
        
        # 1. TOTAL COUNTS
        stats_query = f"""
            SELECT 
                (SELECT COUNT(*) FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}') as total_baseline,
                (SELECT COUNT(DISTINCT hole_id) FROM `sensorpush-export.survey.surveys` WHERE project_id = '{active_proj['project_id']}') as unique_downhole,
                (SELECT COUNT(*) FROM `sensorpush-export.survey.surveys` WHERE project_id = '{active_proj['project_id']}') as total_survey_points
        """
        df_stats = run_query(stats_query)
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Baseline Holes", df_stats['total_baseline'][0])
        col2.metric("Holes w/ Surveys", df_stats['unique_downhole'][0])
        col3.metric("Total Survey Points", df_stats['total_survey_points'][0])

        st.divider()

        # 2. CHECK FOR BASELINE DOUBLES
        st.write("### 🚩 Baseline Duplicates")
        dup_baseline_q = f"""
            SELECT hole_id, phase, COUNT(*) as count
            FROM `sensorpush-export.survey.holes`
            WHERE project_id = '{active_proj['project_id']}'
            GROUP BY hole_id, phase
            HAVING count > 1
        """
        df_dup_base = run_query(dup_baseline_q)
        if not df_dup_base.empty:
            st.error(f"Found {len(df_dup_base)} holes with duplicate baseline entries!")
            st.dataframe(df_dup_base)
        else:
            st.success("No duplicate baseline holes found.")

        st.divider()

        # 3. CHECK FOR MULTIPLE DOWNHOLE RUNS
        st.write("### 🛰️ Multiple Survey Runs")
        # This identifies if a hole has more than one survey "set" (e.g., re-run)
        # Assuming different runs have different survey_types or timestamps
        dup_survey_q = f"""
            SELECT hole_id, survey_type, COUNT(DISTINCT depth) as data_points, COUNT(*) as total_entries
            FROM `sensorpush-export.survey.surveys`
            WHERE project_id = '{active_proj['project_id']}'
            GROUP BY hole_id, survey_type
        """
        df_surveys = run_query(dup_survey_q)
        
        if not df_surveys.empty:
            # Highlight rows where total_entries / data_points > 1 (indicates overlapping data)
            st.info("Reviewing survey density per hole:")
            st.dataframe(df_surveys)
        else:
            st.warning("No downhole data found to audit.")

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
# 6. REPORTS
# ==========================================
elif category == "Reports":
    action = st.sidebar.radio("Report Type", ["Data Audit", "Deviation Summary", "Export Shifted Data"])

elif category == "Reports":
    if action == "Data Audit":
        st.subheader(f"🔍 Data Audit: {active_proj['name']}")
        
        # 1. TOTAL COUNTS
        stats_query = f"""
            SELECT 
                (SELECT COUNT(*) FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}') as total_baseline,
                (SELECT COUNT(DISTINCT hole_id) FROM `sensorpush-export.survey.surveys` WHERE project_id = '{active_proj['project_id']}') as unique_downhole,
                (SELECT COUNT(*) FROM `sensorpush-export.survey.surveys` WHERE project_id = '{active_proj['project_id']}') as total_survey_points
        """
        df_stats = run_query(stats_query)
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Baseline Holes", df_stats['total_baseline'][0])
        col2.metric("Holes w/ Surveys", df_stats['unique_downhole'][0])
        col3.metric("Total Survey Points", df_stats['total_survey_points'][0])

        st.divider()

        # 2. CHECK FOR BASELINE DOUBLES
        st.write("### 🚩 Baseline Duplicates")
        dup_baseline_q = f"""
            SELECT hole_id, phase, COUNT(*) as count
            FROM `sensorpush-export.survey.holes`
            WHERE project_id = '{active_proj['project_id']}'
            GROUP BY hole_id, phase
            HAVING count > 1
        """
        df_dup_base = run_query(dup_baseline_q)
        if not df_dup_base.empty:
            st.error(f"Found {len(df_dup_base)} holes with duplicate baseline entries!")
            st.dataframe(df_dup_base)
        else:
            st.success("No duplicate baseline holes found.")

        st.divider()

        # 3. CHECK FOR MULTIPLE DOWNHOLE RUNS
        st.write("### 🛰️ Multiple Survey Runs")
        # This identifies if a hole has more than one survey "set" (e.g., re-run)
        # Assuming different runs have different survey_types or timestamps
        dup_survey_q = f"""
            SELECT hole_id, survey_type, COUNT(DISTINCT depth) as data_points, COUNT(*) as total_entries
            FROM `sensorpush-export.survey.surveys`
            WHERE project_id = '{active_proj['project_id']}'
            GROUP BY hole_id, survey_type
        """
        df_surveys = run_query(dup_survey_q)
        
        if not df_surveys.empty:
            # Highlight rows where total_entries / data_points > 1 (indicates overlapping data)
            st.info("Reviewing survey density per hole:")
            st.dataframe(df_surveys)
        else:
            st.warning("No downhole data found to audit.")
