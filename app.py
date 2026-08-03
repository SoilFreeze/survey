import streamlit as st
import pandas as pd
import io

# Import your existing engines and the new BigQuery DB class
from math_engine_2 import SurveyMath
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
with st.sidebar:
    st.header("1. Project Settings")
    # For now, manually type the project ID (e.g., "2329a"). 
    # Later, we can fetch this list dynamically from the 'projects' table.
    active_project = st.text_input("Active Project ID:", value="2329a")
    
    if active_project:
        db = BigQueryDB(project_id=active_project)
        st.success(f"Connected to project: {active_project}")
    else:
        st.warning("Please enter a Project ID.")
        st.stop()

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
                            # We extract the date, or use today's date, and push via our new BigQueryDB class
                            upload_date = pd.Timestamp.now().strftime("%Y-%m-%d")
                            # Alias the columns to match BigQuery schema expectations
                            df = df.rename(columns={'ID': 'hole_id', 'Length': 'length', 'Azimuth': 'azimuth', 'Inclination': 'inclination'})
                            rows_inserted = db.import_downhole(df, import_type, upload_date)
                            st.success(f"Successfully uploaded {rows_inserted} rows to {import_type}!")
                        else:
                            st.info("Baseline/Top Survey logic needs to be mapped to the new holes table schema.")
                            
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
