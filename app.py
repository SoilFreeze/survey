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
                    new_df = pd.DataFrame([{'project_id':n_id, 'name':n_name, 'origin_north':n_on, 'origin_east':n_oe}])
                    upload_to_bq(new_df, "sensorpush-export.survey.projects")
                    st.success("Project Created!")
                    st.rerun()
        with tab2:
            if active_proj is not None:
                u_on = st.number_input("Update Origin North", value=float(active_proj['origin_north']), format="%.3f")
                u_oe = st.number_input("Update Origin East", value=float(active_proj['origin_east']), format="%.3f")
                if st.button("Update Origin"):
                    client.query(f"UPDATE `sensorpush-export.survey.projects` SET origin_north={u_on}, origin_east={u_oe} WHERE project_id='{active_proj['project_id']}'")
                    st.success("Origin Updated!")
                    st.rerun()

    elif action == "Upload Baseline":
        st.subheader("Step 2: Upload Design Baseline")
        file = st.file_uploader("Upload Baseline CSV", type=['csv'])
        if file and active_proj is not None:
            df_base = pd.read_csv(file)
            # Standardizing headers
            df_base.columns = [c.lower().strip() for c in df_base.columns]
            
            # Robust mapping
            rename_map = {'id':'hole_id', 'name':'hole_id', 'hole':'hole_id', 'north':'design_n', 'east':'design_e', 'elev':'design_z', 'elevation':'design_z', 'cadx':'design_e', 'cady':'design_n'}
            df_base = df_base.rename(columns=rename_map)
            
            # Defaults & Logic
            df_base['hole_id'] = df_base['hole_id'].astype(str).str.strip()
            df_base['project_id'] = str(active_proj['project_id'])
            if 'phase' not in df_base.columns:
                df_base['phase'] = "Phase1"
            if 'design_z' not in df_base.columns:
                df_base['design_z'] = 0.0
                
            st.dataframe(df_base.head())
            if st.button("Confirm Upload"):
                upload_to_bq(df_base[['project_id','hole_id','design_n','design_e','design_z','phase']], "sensorpush-export.survey.holes")
                st.success("Baseline Uploaded!")
                st.rerun()

#### Update Top Survey ####

elif choice == "3. Upload Top Survey":
    if active_proj is not None:
        st.subheader(f"Step 3: Fast Batch Update for {active_proj['name']}")
        
        column_aliases = {
            'hole_id': ['hole_id', 'id', 'name', 'hole', 'point', 'station', 'label'],
            'actual_n': ['actual_n', 'north', 'northing', 'y', 'n', 'cady', 'pos_y', 'cady'],
            'actual_e': ['actual_e', 'east', 'easting', 'x', 'e', 'cadx', 'pos_x', 'cadx'],
            'actual_z': ['actual_z', 'ele', 'elevation', 'z', 'rl', 'level', 'elev', 'height']
        }

        top_file = st.file_uploader("Upload Actual Top Survey CSV", type=['csv'])
        
        if top_file:
            df_top = pd.read_csv(top_file)
            rename_map = {}
            for official_name, aliases in column_aliases.items():
                for col in df_top.columns:
                    if col.lower().strip() in aliases:
                        rename_map[col] = official_name
                        break
            
            df_top = df_top.rename(columns=rename_map)
            
            if 'hole_id' in df_top.columns and 'actual_n' in df_top.columns:
                # Preserve exact labels and project ID
                df_top['hole_id'] = df_top['hole_id'].astype(str).str.strip()
                df_top['project_id'] = str(active_proj['project_id'])
                if 'actual_z' not in df_top.columns: df_top['actual_z'] = 0.0

                st.write(f"Prepared {len(df_top)} holes for batch update.")

                if st.button("🚀 Run Fast Update"):
                    with st.spinner("Processing batch update in BigQuery..."):
                        # 1. Upload to a temporary "staging" table
                        temp_table_id = f"sensorpush-export.survey.temp_top_{active_proj['project_id']}"
                        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
                        client.load_table_from_dataframe(df_top, temp_table_id, job_config=job_config).result()

                        # 2. Execute a single MERGE command (The "Fast" part)
                        merge_query = f"""
                            MERGE `sensorpush-export.survey.holes` T
                            USING `{temp_table_id}` S
                            ON T.hole_id = S.hole_id AND T.project_id = S.project_id
                            WHEN MATCHED THEN
                              UPDATE SET 
                                T.design_n = S.actual_n, 
                                T.design_e = S.actual_e, 
                                T.design_z = S.actual_z
                        """
                        client.query(merge_query).result()
                        
                        # 3. Clean up
                        client.delete_table(temp_table_id, not_found_ok=True)
                        
                        st.success(f"Successfully updated {len(df_top)} holes in seconds!")
            else:
                st.error("Missing required columns (ID, North, East).")
             
#### Upload Downhole ####

elif choice == "4. Upload Downhole":
    if active_proj is not None:
        st.subheader(f"Step 4: Import Downhole Survey for {active_proj['name']}")
        
        # Robust aliases for Boretrak, Gyro, or Deviometer files
        column_aliases = {
            'hole_id': ['hole_id', 'id', 'name', 'hole', 'point', 'station', 'label'],
            'depth': ['depth', 'md', 'length', 'dist', 'distance', 'measured_depth'],
            'azimuth': ['azimuth', 'azi', 'az', 'dir', 'direction', 'bearing'],
            'inclination': ['inclination', 'inc', 'dip', 'angle', 'vertical_angle']
        }

        # Optional: Add a 'Survey Type' selector for the database
        s_type = st.selectbox("Survey Type", ["Pipe", "Casing", "Pre-Freeze", "Post-Freeze"])
        
        downhole_file = st.file_uploader("Upload Downhole CSV (Depth, Azi, Inc)", type=['csv'])
        
        if downhole_file:
            df_dh = pd.read_csv(downhole_file)
            
            # Robust mapping logic
            rename_map = {}
            for official_name, aliases in column_aliases.items():
                for col in df_dh.columns:
                    if col.lower().strip() in aliases:
                        rename_map[col] = official_name
                        break
            
            df_dh = df_dh.rename(columns=rename_map)
            
            # Ensure IDs are strings to match BigQuery schema
            if 'hole_id' in df_dh.columns:
                df_dh['hole_id'] = df_dh['hole_id'].astype(str).str.strip()
                df_dh['project_id'] = str(active_proj['project_id'])
                df_dh['survey_type'] = s_type
                
                # Check for minimum required survey data
                required = ['depth', 'azimuth', 'inclination']
                missing = [c for c in required if c not in df_dh.columns]
                
                if not missing:
                    st.write(f"✅ Found {len(df_dh)} survey points.")
                    st.dataframe(df_dh[['hole_id', 'depth', 'azimuth', 'inclination']].head())
                    
                    if st.button("Confirm & Append to BigQuery"):
                        with st.spinner("Uploading survey data..."):
                            # We append here so you don't lose old survey runs
                            final_cols = ['project_id', 'hole_id', 'depth', 'azimuth', 'inclination', 'survey_type']
                            df_to_push = df_dh[final_cols].copy()
                            
                            try:
                                upload_to_bq(df_to_push, "sensorpush-export.survey.surveys")
                                st.success(f"Added {len(df_to_push)} points to {active_proj['name']}.")
                            except Exception as e:
                                st.error(f"Upload failed: {e}")
                else:
                    st.error(f"Missing survey columns: {missing}")
            else:
                st.error("Could not find Hole ID column.")
    else:
        st.warning("Select a project in the sidebar first.")

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
