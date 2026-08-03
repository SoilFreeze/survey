import streamlit as st
import pandas as pd
import io

# Import your existing engines and the new BigQuery DB class
from math_engine import SurveyMath
from visualizer import SurveyVisualizer
from bigquery_db import BigQueryDB  # The file we created in the previous step

# 1. Page Configuration
st.set_page_config(page_title="Survey Management System", layout="wide")
st.title("Survey Management System (BigQuery Edition)")

# 2. Initialize Engines
@st.cache_resource
def get_engines():
    return SurveyMath(), SurveyVisualizer()

math, vis = get_engines()

# 3. Sidebar - Project & Database Connection
# Initialize DB with a dummy project ID first just to connect
db = BigQueryDB(project_id="temp") 

with st.sidebar:
    st.header("1. Project Settings")
    
    # Fetch existing projects
    existing_projects = db.get_all_projects()
    
    # Use a selectbox instead of text input
    active_project = st.selectbox("Active Project ID:", options=existing_projects, index=0 if existing_projects else None)
    
    if active_project:
        db.active_project_id = active_project
        st.success(f"Connected to project: {active_project}")
    else:
        st.warning("No projects found. Please create one below.")
        
    # Form to create a new project
    with st.expander("➕ Create New Project"):
        with st.form("new_project_form", clear_on_submit=True):
            new_pid = st.text_input("Project ID (e.g., 2329a)")
            new_pname = st.text_input("Project Name")
            new_n = st.number_input("Origin North (Y)", value=0.0)
            new_e = st.number_input("Origin East (X)", value=0.0)
            submit_btn = st.form_submit_button("Create Project")
            
            if submit_btn:
                if new_pid and new_pname:
                    success = db.create_new_project(new_pid, new_pname, new_n, new_e)
                    if success:
                        st.success(f"Project '{new_pid}' created successfully!")
                        st.rerun() # Refresh the app to update the dropdown list
                else:
                    st.error("Project ID and Name are required.")

    st.divider()
    
    # 4. Sidebar - Data Import
    st.header("2. Import Data")
    import_type = st.selectbox("Select Data Type to Import:", 
                               ["Top Survey", "Casing", "Pipe", "Baseline"])
    
    uploaded_file = st.file_uploader(f"Upload {import_type} CSV", type=["csv"])
    
    if uploaded_file is not None:
        try:
            # Read and normalize the CSV
            df = pd.read_csv(uploaded_file)
            
            # Use the existing mapping logic from your original GUI
            col_map = {
                'id': ["id", "hole_id", "holeid", "hole", "point", "name", "station", "loc"],
                'n': ["north", "northing", "n", "y", "northings"],
                'e': ["east", "easting", "e", "x", "eastings"],
                'z': ["elev", "elevation", "z", "rl", "level", "height"],
                'az': ['azimuth', 'azi', 'az', 'dir', 'direction'],
                'inc': ['inclination', 'inc', 'dip', 'angle'],
                'depth': ['length', 'depth', 'dist', 'distance', 'md']
            }
            
            df = math.normalize_columns(df, col_map)
            
            if 'ID' not in df.columns:
                st.error(f"Could not find 'ID' column. Found: {list(df.columns)}")
            else:
                df['clean_ID'] = df['ID'].apply(math.standardize_id)
                st.write("Preview of Normalized Data:")
                st.dataframe(df.head())
                
                # Import Logic based on selection
                if st.button(f"Confirm & Upload {import_type} to BigQuery"):
                    with st.spinner("Uploading to BigQuery..."):
                        if import_type in ["Casing", "Pipe"]:
                            upload_date = pd.Timestamp.now().strftime("%Y-%m-%d")
                            df = df.rename(columns={'ID': 'hole_id', 'Length': 'length', 'Azimuth': 'azimuth', 'Inclination': 'inclination'})
                            rows_inserted = db.import_downhole(df, import_type, upload_date)
                            st.success(f"Successfully uploaded {rows_inserted} rows to {import_type}!")
                            
                        elif import_type == "Baseline":
                            rows_inserted = db.import_baseline(df)
                            st.success(f"Successfully uploaded {rows_inserted} Baseline records!")
                            
                        elif import_type == "Top Survey":
                            rows_inserted = db.update_top_survey(df)
                            st.success(f"Successfully updated {rows_inserted} Top Survey records!")
                            
        except Exception as e:
            st.error(f"Error processing file: {e}")

# 5. Main App Tabs
tab_data, tab_maps, tab_qc = st.tabs(["Data & Analysis", "Map Visualizations", "QC & Single Hole"])

with tab_data:
    st.subheader("Project Data Overview")
    if st.button("Fetch Latest Data"):
        with st.spinner("Querying BigQuery..."):
            holes_df, surveys_df = db.get_all_data()
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**Holes:** {len(holes_df)} records")
                st.dataframe(holes_df)
            with col2:
                st.write(f"**Surveys:** {len(surveys_df)} records")
                st.dataframe(surveys_df)

with tab_maps:
    st.subheader("Generate Map Visualizations")
    col1, col2 = st.columns(2)
    with col1:
        target_elev = st.number_input("Target Elevation:", value=220.0, step=1.0)
        if st.button("Generate Pipe Map"):
            st.info("Visualizer integration coming next!")
            
with tab_qc:
    st.subheader("QC & Single Hole Analysis")
    st.info("Single hole graph integration coming next!")
