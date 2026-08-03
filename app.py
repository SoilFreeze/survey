import streamlit as st
import pandas as pd
import io
import re

# Import your existing engines and the new BigQuery DB class
from math_engine import SurveyMath
from visualizer import SurveyVisualizer
from bigquery_db import BigQueryDB  # The file we created in the previous step
from google.cloud import bigquery

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
        
        # Display and manage current project info
        proj_info = db.get_project_info()
        if proj_info:
            with st.expander("⚙️ Manage Project Settings"):
                with st.form("update_project_form"):
                    st.write(f"**Project Name:** {proj_info['name']}")
                    
                    # Pre-fill inputs with the current database values
                    upd_n = st.number_input("Origin North (Y)", value=float(proj_info['origin_north']), format="%.3f")
                    upd_e = st.number_input("Origin East (X)", value=float(proj_info['origin_east']), format="%.3f")
                    
                    if st.form_submit_button("Update Origin"):
                        if db.update_project_origin(upd_n, upd_e):
                            st.success("Project origin updated successfully!")
                            st.rerun() # Refresh to show new coordinates
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
                        st.rerun()
                else:
                    st.error("Project ID and Name are required.")

    st.divider()
    
    # 4. Sidebar - Data Import
    st.header("2. Import Data")
    
    # Removed "Casing" from the selectbox options
    import_type = st.selectbox("Select Data Type to Import:", 
                               ["Baseline", "Top Survey", "Pipe", "Pipe Details"])
    
    uploaded_file = st.file_uploader(f"Upload {import_type} CSV", type=["csv"])
    
    if uploaded_file is not None:
        try:
            df = pd.read_csv(uploaded_file)
            
            # Expanded mapping to include CADX, CADY, and Pipe words
            col_map = {
                'id': ["id", "hole_id", "holeid", "hole", "point", "name", "station", "loc", "pipe"],
                'n': ["north", "northing", "n", "y", "northings", "cady"],
                'e': ["east", "easting", "e", "x", "eastings", "cadx"],
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
                
                # Function to grab date from filename (YYYY-MM-DD or MM-DD-YYYY)
                def extract_date_from_filename(filename):
                    match = re.search(r'(\d{4})[._-](\d{2})[._-](\d{2})', filename)
                    if match: return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
                    match = re.search(r'(\d{2})[._-](\d{2})[._-](\d{4})', filename)
                    if match: return f"{match.group(3)}-{match.group(1)}-{match.group(2)}"
                    return pd.Timestamp.now().strftime("%Y-%m-%d")
                    
                file_date = extract_date_from_filename(uploaded_file.name)
                
                if st.button(f"Confirm & Upload {import_type} to BigQuery"):
                    with st.spinner("Uploading to BigQuery..."):
                        
                        # Updated to only look for "Pipe"
                        if import_type == "Pipe":
                            # We now catch both the row count and the unique hole list
                            rows_inserted, unique_holes = db.import_downhole(df, import_type, file_date)
                            st.success(f"Successfully processed {len(unique_holes)} specific holes ({rows_inserted} rows) for {import_type}!")
                            st.info(f"The rest of your holes remain untouched. Holes updated: {', '.join(unique_holes)}")
                            
                        elif import_type == "Baseline":
                            rows_inserted = db.import_baseline(df)
                            st.success(f"Successfully uploaded {rows_inserted} Baseline records!")
                            
                        elif import_type == "Top Survey":
                            rows_inserted = db.update_top_survey(df, file_date)
                            st.success(f"Successfully updated {rows_inserted} Top Survey records (Date: {file_date})!")
                            
                        elif import_type == "Pipe Details":
                            df.rename(columns=lambda x: 'pipe_type' if x.strip().lower() == 'type' else x, inplace=True)
                            rows_inserted = db.import_pipe_details(df)
                            st.success(f"Successfully updated {rows_inserted} Pipe Details!")
                            
        except Exception as e:
            st.error(f"Error processing file: {e}")
# 5. Main App Tabs
tab_data, tab_maps, tab_qc = st.tabs(["Data & Analysis", "Map Visualizations", "QC & Single Hole"])

with tab_data:
    st.subheader("Project Data Overview")
    
    # 1. Fetch and display stats automatically (no button required)
    stats = db.get_project_stats()
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Holes", stats["total"])
    col2.metric("Holes w/ Top Survey", stats["top"])
    col3.metric("Holes w/ Downhole Survey", stats["downhole"])
    
    st.divider()
    
    # 2. Fetch data tables only when requested
    if st.button("Fetch Latest Data (View Tables)"):
        with st.spinner("Querying BigQuery..."):
            holes_df, surveys_df = db.get_all_data()
            
            # Stacked vertically on their own lines
            st.write(f"### Holes Data ({len(holes_df)} records)")
            st.dataframe(holes_df, use_container_width=True)
            
            st.write(f"### Surveys Data ({len(surveys_df)} records)")
            st.dataframe(surveys_df, use_container_width=True)
            
with tab_maps:
    st.subheader("Map & Spatial Visualizations")
    
    vis = SurveyVisualizer()
    
    map_type = st.selectbox(
        "Select Map Type:",
        ["Plan View (Design vs Actual)", "Grid Heatmap", "Pipe Heatmap", "Deviation Needles"]
    )
    
    if st.button("Generate Map"):
        with st.spinner("Fetching data and rendering plots..."):
            holes_df, surveys_df = db.get_all_data()
            
            if holes_df.empty:
                st.warning("No hole data found for this project. Please import a Baseline first.")
            else:
                try:
                    # 1. Fetch origin from the database universally
                    proj_query = f"SELECT origin_north, origin_east FROM `{db.dataset}.projects` WHERE project_id = @pid"
                    params = [bigquery.ScalarQueryParameter("pid", "STRING", db.active_project_id)]
                    proj_res = list(db.client.query(proj_query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result())
                    
                    db_orig_n = proj_res[0]['origin_north'] if proj_res else 0.0
                    db_orig_e = proj_res[0]['origin_east'] if proj_res else 0.0
                    
                    orig_n = db_orig_n if db_orig_n != 0.0 else holes_df['n_base'].mean()
                    orig_e = db_orig_e if db_orig_e != 0.0 else holes_df['e_base'].mean()
                    
                    # 2. Safely fallback any accidental 0.0 actuals to the design base so they don't draw 5-million-foot lines
                    holes_df.loc[holes_df['n_top'] == 0.0, 'n_top'] = holes_df['n_base']
                    holes_df.loc[holes_df['e_top'] == 0.0, 'e_top'] = holes_df['e_base']
                    
                    # 3. Shift ALL coordinates universally before ANY map draws
                    holes_df['n_base'] = holes_df['n_base'] - orig_n
                    holes_df['e_base'] = holes_df['e_base'] - orig_e
                    holes_df['n_top'] = holes_df['n_top'] - orig_n
                    holes_df['e_top'] = holes_df['e_top'] - orig_e
                    
                    fig = None
                    
                    # 4. Render the chosen map
                    if map_type == "Plan View (Design vs Actual)":
                        top_df = holes_df.copy()
                        top_df = top_df.rename(columns={'clean_id': 'ID', 'n_base': 'Design_N', 'e_base': 'Design_E', 'n_top': 'Actual_N', 'e_top': 'Actual_E'})
                        fig = vis.plot_top_deviation_map(top_df)
                        
                    elif map_type == "Grid Heatmap":
                        grid_df = holes_df.rename(columns={'clean_id': 'ID', 'n_base': 'North', 'e_base': 'East'})
                        grid_df['Survey_Status'] = grid_df['has_top_survey'].apply(lambda x: 'Pipe' if x == 1 else 'Baseline')
                        fig = vis.generate_grid_heatmap(grid_df, depth_label="Surface", show_labels=True)
                        
                    elif map_type == "Pipe Heatmap":
                        pipe_df = holes_df.rename(columns={'clean_id': 'ID', 'n_base': 'North', 'e_base': 'East'})
                        pipe_df['Survey_Status'] = 'Pipe'
                        fig = vis.generate_pipe_heatmap(pipe_df, depth_label="Surface", show_labels=True)
                        
                    elif map_type == "Deviation Needles":
                        if surveys_df.empty:
                            st.warning("Deviation needles require downhole survey data. Please import Pipe surveys first.")
                        else:
                            needles_df = surveys_df.merge(holes_df[['id', 'n_base', 'e_base']], left_on='hole_id', right_on='id')
                            needles_df = needles_df.rename(columns={
                                'hole_id': 'ID', 'n_base': 'Start_N', 'e_base': 'Start_E',
                                'depth': 'Elevation'
                            })
                            # Polyfill for visualizer requirements
                            needles_df['Collar_N'] = needles_df['Start_N']
                            needles_df['Collar_E'] = needles_df['Start_E']
                            needles_df['End_N'] = needles_df['Start_N'] 
                            needles_df['End_E'] = needles_df['Start_E']
                            fig = vis.plot_deviation_needles(needles_df)
                            
                    if fig:
                        st.pyplot(fig)
                    else:
                        st.info("The selected map type returned no plot data.")
                        
                except Exception as e:
                    st.error(f"Error rendering visualization: {e}")
            
with tab_qc:
    st.subheader("QC & Single Hole Analysis")
    st.info("Single hole graph integration coming next!")
