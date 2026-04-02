import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from google.cloud import bigquery
from google.oauth2 import service_account
from plotly.subplots import make_subplots

# ==========================================
# 1. AUTHENTICATION BLOCK
# ==========================================
def get_bq_client():
    """Replace this block if changing your GCP connection method."""
    if "gcp_service_account" in st.secrets:
        info = st.secrets["gcp_service_account"]
        credentials = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(credentials=credentials, project=info["project_id"])
    else:
        st.error("Secrets not found!")
        st.stop()

client = get_bq_client()

# ==========================================
# 2. MATH & TRANSFORMATION BLOCK
# ==========================================
def calculate_survey_path(df, origin_n, origin_e):
    """
    Replace this block to change coordinate math.
    Input: Dataframe with raw survey points.
    Output: Dataframe with n_rel and e_rel centered at 0,0.
    """
    df = df.sort_values('depth')
    rad_az = np.radians(df['azimuth'])
    rad_inc = np.radians(df['inclination'])
    dist = df['depth'].diff().fillna(0)
    
    # Standard survey math
    dn = dist * np.sin(rad_inc) * np.cos(rad_az)
    de = dist * np.sin(rad_inc) * np.sin(rad_az)
    
    df['n_rel'] = dn.cumsum()
    df['e_rel'] = de.cumsum()
    return df

# ==========================================
# 3. BIGQUERY INTERFACE BLOCK (DATABASE I/O)
# ==========================================
def run_query(query):
    """Generic wrapper to fetch data from BigQuery."""
    return client.query(query).to_dataframe()

def upload_to_bq(df, table_id):
    """Generic wrapper to push data to BigQuery."""
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    return job.result()

# ==========================================
# 4. USER INTERFACE (MAIN APP)
# ==========================================
st.set_page_config(page_title="SoilFreeze Survey", layout="wide")

# GLOBAL PROJECT SELECTOR
# We fetch projects from BigQuery to populate the sidebar menu
def get_project_list():
    query = "SELECT project_id, name, origin_north, origin_east FROM `sensorpush-export.survey.projects` ORDER BY name"
    return run_query(query)

df_projects = get_project_list()

with st.sidebar:
    st.title("🏗️ SoilFreeze Manager")
    
    if not df_projects.empty:
        # This is your "Menu of projects already created"
        selected_project_name = st.selectbox(
            "Current Project Context:", 
            options=df_projects['name'].tolist(),
            help="All uploads and charts will apply to this project."
        )
        # Store the active project details in a variable
        active_proj = df_projects[df_projects['name'] == selected_project_name].iloc[0]
        st.info(f"Active ID: {active_proj['project_id']}")
    else:
        st.warning("No projects found. Please create one below.")
        active_proj = None

    st.divider()
    menu = ["Project Dashboard", "1. Create New Project", "2. Upload Baseline", "3. Upload Top Survey", "4. Upload Downhole"]
    choice = st.selectbox("Navigation", menu)

# --- WORKFLOW PAGES ---

if choice == "Project Dashboard":
    if active_proj is not None:
        st.subheader(f"📊 Analysis: {active_proj['name']}")
        
        # 1. FETCH DATA: Only get holes that have matching survey records
        query = f"""
            SELECT 
                h.hole_id, h.design_n, h.design_e, h.design_z,
                s.depth, s.azimuth, s.inclination, s.survey_type
            FROM `sensorpush-export.survey.holes` h
            INNER JOIN `sensorpush-export.survey.surveys` s ON h.hole_id = s.hole_id
            WHERE h.project_id = '{active_proj['project_id']}'
            ORDER BY h.hole_id, s.depth
        """
        df_surveyed = run_query(query)

        if not df_surveyed.empty:
            # 2. SELECT HOLE: This list now only contains surveyed pipes
            surveyed_list = sorted(df_surveyed['hole_id'].unique())
            target_hole = st.selectbox("Select Surveyed Pipe", surveyed_list)
            
            # Filter for the specific pipe
            df_hole = df_surveyed[df_surveyed['hole_id'] == target_hole].copy()
            
            # 3. CALCULATE RELATIVE PATH (0,0 Shift)
            start_n = df_hole['design_n'].iloc[0] - active_proj['origin_north']
            start_e = df_hole['design_e'].iloc[0] - active_proj['origin_east']
            processed = calculate_survey_path(df_hole, start_n, start_e)
            
            # 4. INTERACTIVE PLOTS
            tab_plan, tab_profile = st.tabs(["Plan View", "Depth Profiles"])
            
            with tab_plan:
                fig_plan = go.Figure()
                # Design Start Point
                fig_plan.add_trace(go.Scatter(x=[start_e], y=[start_n], mode='markers', 
                                            marker=dict(size=12, symbol='x', color='red'), name='Collar'))
                # Surveyed Path
                fig_plan.add_trace(go.Scatter(x=processed['e_rel'], y=processed['n_rel'], 
                                            mode='lines+markers', name='Pipe Path'))
                
                fig_plan.update_layout(title=f"Pipe {target_hole}: Plan View (Relative to 0,0)",
                                      xaxis_title="East (ft)", yaxis_title="North (ft)",
                                      yaxis=dict(scaleanchor="x", scaleratio=1))
                st.plotly_chart(fig_plan, use_container_width=True)

            with tab_profile:
                fig_depth = make_subplots(rows=1, cols=2, subplot_titles=("East Dev", "North Dev"))
                fig_depth.add_trace(go.Scatter(x=processed['e_rel'], y=processed['depth'], name="East"), row=1, col=1)
                fig_depth.add_trace(go.Scatter(x=processed['n_rel'], y=processed['depth'], name="North"), row=1, col=2)
                fig_depth.update_yaxes(autorange="reversed", title="Depth (ft)")
                st.plotly_chart(fig_depth, use_container_width=True)

            # 5. DOWNLOAD DATA
            st.divider()
            csv = processed.to_csv(index=False).encode('utf-8')
            st.download_button(f"Download {target_hole} Transformed Data", data=csv, 
                             file_name=f"{target_hole}_centered.csv")
        else:
            st.info("No data found for this project. Please complete Steps 2-4.")
    else:
        st.error("Please select a project from the sidebar.")

#### Create New Project ####

elif choice == "1. Create New Project":
    tab1, tab2 = st.tabs(["Create New", "Edit Existing"])

    # --- SUB-BLOCK: CREATE ---
    with tab1:
        st.subheader("Setup New Project")
        with st.form("new_project_form", clear_on_submit=True):
            new_id = st.text_input("Project ID (Unique Code)")
            new_name = st.text_input("Project Name (Display Name)")
            new_on = st.number_input("Origin Northing (can be 0 for now)", format="%.3f", value=0.0)
            new_oe = st.number_input("Origin Easting (can be 0 for now)", format="%.3f", value=0.0)
            
            if st.form_submit_button("Save New Project"):
                new_df = pd.DataFrame([{
                    'project_id': new_id, 'name': new_name,
                    'origin_north': new_on, 'origin_east': new_oe
                }])
                upload_to_bq(new_df, "sensorpush-export.survey.projects")
                st.success("Project Created!")
                st.rerun()

    # --- SUB-BLOCK: EDIT ---
    with tab2:
        if active_proj is not None:
            st.subheader(f"Edit Details for: {active_proj['name']}")
            # Text inputs pre-filled with current BigQuery values
            upd_name = st.text_input("Edit Name", value=active_proj['name'])
            upd_on = st.number_input("Update Origin Northing", value=float(active_proj['origin_north']), format="%.3f")
            upd_oe = st.number_input("Update Origin Easting", value=float(active_proj['origin_east']), format="%.3f")
            
            if st.button("Update Project in Database"):
                # BigQuery UPDATE script
                update_query = f"""
                    UPDATE `sensorpush-export.survey.projects`
                    SET name = '{upd_name}', 
                        origin_north = {upd_on}, 
                        origin_east = {upd_oe}
                    WHERE project_id = '{active_proj['project_id']}'
                """
                client.query(update_query).result()
                st.success("Project coordinates updated!")
                st.rerun()
        else:
            st.warning("Select a project from the sidebar to edit it.")

#### Upload Baseline ####

elif choice == "2. Upload Baseline":
    if active_proj is not None:
        st.subheader(f"Step 2: Import Design Baseline for {active_proj['name']}")
        
        column_aliases = {
            'hole_id': ['hole_id', 'id', 'name', 'hole', 'point', 'station', 'label'],
            'design_n': ['design_n', 'north', 'northing', 'y', 'n', 'cady', 'pos_y'],
            'design_e': ['design_e', 'east', 'easting', 'x', 'e', 'cadx', 'pos_x'],
            'design_z': ['design_z', 'ele', 'elevation', 'z', 'rl', 'level', 'elev']
        }

        baseline_file = st.file_uploader("Upload Baseline CSV", type=['csv'])
        
        if baseline_file:
            # We read 'hole_id' specifically as a string from the start
            df_base = pd.read_csv(baseline_file)
            
            rename_map = {}
            for official_name, aliases in column_aliases.items():
                for col in df_base.columns:
                    if col.lower().strip() in aliases:
                        rename_map[col] = official_name
                        break

            df_base = df_base.rename(columns=rename_map)
            
            if 'hole_id' in df_base.columns:
                # CRITICAL: Strip whitespace but keep alphanumeric characters intact
                df_base['hole_id'] = df_base['hole_id'].astype(str).str.strip()
                
                required = ['hole_id', 'design_n', 'design_e']
                if all(col in df_base.columns for col in required):
                    df_base['project_id'] = str(active_proj['project_id'])
                    
                    if 'design_z' not in df_base.columns:
                        df_base['design_z'] = 0.0
                    
                    final_cols = ['project_id', 'hole_id', 'design_n', 'design_e', 'design_z']
                    df_to_upload = df_base[final_cols].copy()

                    st.write("✅ Label Check: IDs preserved exactly as strings.")
                    st.dataframe(df_to_upload.head())
                    
                    if st.button("Confirm & Upload to BigQuery"):
                        upload_to_bq(df_to_upload, "sensorpush-export.survey.holes")
                        st.success(f"Uploaded {len(df_to_upload)} holes with preserved labels.")
                else:
                    st.error("Could not find Northing/Easting columns. Check for CADX/CADY.")

#### Upload top survey ####

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
