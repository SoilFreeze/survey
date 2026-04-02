import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from google.cloud import bigquery
from google.oauth2 import service_account

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
        st.subheader(f"Analysis: {active_proj['name']}")
        # Visualizer logic goes here
    else:
        st.error("Select or create a project first.")

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
            'hole_id': ['hole_id', 'id', 'name', 'hole', 'point', 'station'],
            'design_n': ['design_n', 'north', 'northing', 'y', 'n'],
            'design_e': ['design_e', 'east', 'easting', 'x', 'e'],
            'design_z': ['design_z', 'ele', 'elevation', 'z', 'rl', 'level']
        }

        baseline_file = st.file_uploader("Upload Baseline CSV", type=['csv'])
        
        if baseline_file:
            df_base = pd.read_csv(baseline_file)
            
            # 1. Map columns
            rename_map = {}
            for official_name, aliases in column_aliases.items():
                for col in df_base.columns:
                    if col.lower().strip() in aliases:
                        rename_map[col] = official_name
                        break

            df_base = df_base.rename(columns=rename_map)
            
            required = ['hole_id', 'design_n', 'design_e']
            missing = [col for col in required if col not in df_base.columns]

            if not missing:
                # --- THE FIX STARTS HERE ---
                # Force ID columns to be strings to avoid ArrowTypeError
                df_base['hole_id'] = df_base['hole_id'].astype(str)
                df_base['project_id'] = str(active_proj['project_id']) 
                # ---------------------------

                if 'design_z' not in df_base.columns:
                    df_base['design_z'] = 0.0
                
                final_cols = ['project_id', 'hole_id', 'design_n', 'design_e', 'design_z']
                df_to_upload = df_base[final_cols].copy()

                st.write("✅ Data prepared for upload:")
                st.dataframe(df_to_upload.head())
                
                if st.button("Confirm & Upload to BigQuery"):
                    upload_to_bq(df_to_upload, "sensorpush-export.survey.holes")
                    st.success(f"Uploaded {len(df_to_upload)} holes.")
            else:
                st.error(f"Missing columns: {missing}")

#### Upload top survey ####

elif choice == "3. Upload Top Survey":
    if active_proj is not None:
        st.subheader(f"Step 3: Actual Collar Locations for {active_proj['name']}")
        
        # Robust aliases for field survey exports
        column_aliases = {
            'hole_id': ['hole_id', 'id', 'name', 'hole', 'point', 'station'],
            'actual_n': ['actual_n', 'north', 'northing', 'y', 'n', 'cady'],
            'actual_e': ['actual_e', 'east', 'easting', 'x', 'e', 'cadx'],
            'actual_z': ['actual_z', 'ele', 'elevation', 'z', 'rl', 'level']
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
            
            # Validation and String Casting
            if 'hole_id' in df_top.columns and 'actual_n' in df_top.columns:
                df_top['hole_id'] = df_top['hole_id'].astype(str)
                
                st.write("Ready to update collar locations:")
                st.dataframe(df_top[['hole_id', 'actual_n', 'actual_e']].head())

                if st.button("Update Holes in BigQuery"):
                    with st.spinner("Updating records..."):
                        # We use a loop or a MERGE statement to update specific holes
                        for _, row in df_top.iterrows():
                            z_val = row.get('actual_z', 0.0)
                            update_query = f"""
                                UPDATE `sensorpush-export.survey.holes`
                                SET design_n = {row['actual_n']}, 
                                    design_e = {row['actual_e']},
                                    design_z = {z_val}
                                WHERE hole_id = '{row['hole_id']}' 
                                AND project_id = '{active_proj['project_id']}'
                            """
                            client.query(update_query).result()
                        st.success(f"Updated {len(df_top)} collar locations!")
            else:
                st.error("CSV must contain at least Hole_ID and Northing/Easting.")
    else:
        st.warning("Select a project in the sidebar first.")
