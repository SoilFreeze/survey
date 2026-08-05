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
                
                def extract_date_from_filename(filename):
                    match = re.search(r'(\d{4})[._-](\d{2})[._-](\d{2})', filename)
                    if match: return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
                    match = re.search(r'(\d{2})[._-](\d{2})[._-](\d{4})', filename)
                    if match: return f"{match.group(3)}-{match.group(1)}-{match.group(2)}"
                    return pd.Timestamp.now().strftime("%Y-%m-%d")
                    
                file_date = extract_date_from_filename(uploaded_file.name)
                
                # ---------------------------------------------------------
                # NEW LOGIC: TOP SURVEY INTERACTIVE CONFLICT RESOLUTION
                # ---------------------------------------------------------
                if import_type == "Top Survey":
                    # Step 1: Initial Check Button
                    if 'conflict_df' not in st.session_state:
                        if st.button("Check & Upload Top Survey"):
                            with st.spinner("Checking database for coordinate conflicts..."):
                                
                                # Query BigQuery for existing raw actual coordinates
                                query = f"""
                                    SELECT hole_id, actual_n as db_n, actual_e as db_e, actual_z as db_z
                                    FROM `{db.dataset}.holes`
                                    WHERE project_id = @pid AND hole_id IN UNNEST(@ids)
                                """
                                params = [
                                    bigquery.ScalarQueryParameter("pid", "STRING", db.active_project_id),
                                    bigquery.ArrayQueryParameter("ids", "STRING", df['clean_ID'].astype(str).tolist())
                                ]
                                db_df = db.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).to_dataframe()
                                
                                # Merge the new upload with the database data
                                merged = df.merge(db_df, left_on='clean_ID', right_on='hole_id', how='left')
                                conflicts = []
                                clean_inserts = []
                                
                                # Sort holes into conflicts vs clean updates
                                for _, row in merged.iterrows():
                                    # If DB is empty, NULL, or 0.0, it's a clean insert
                                    if pd.isna(row['db_n']) or row['db_n'] == 0.0:
                                        clean_inserts.append(row['clean_ID'])
                                    else:
                                        # Calculate absolute differences (allowing 0.01ft floating point variance)
                                        n_diff = abs(row['North'] - row['db_n'])
                                        e_diff = abs(row['East'] - row['db_e'])
                                        z_diff = abs(row['Elev'] - row['db_z'])
                                        
                                        if n_diff > 0.01 or e_diff > 0.01 or z_diff > 0.01:
                                            conflicts.append(row)
                                        else:
                                            # Identical to database, overwrite silently
                                            clean_inserts.append(row['clean_ID']) 
                                            
                                st.session_state.clean_inserts_df = df[df['clean_ID'].isin(clean_inserts)].copy()
                                
                                # Step 2: Trigger UI if conflicts exist
                                if conflicts:
                                    conflicts_df = pd.DataFrame(conflicts)
                                    conflicts_df['Keep_New_Upload'] = True # Default to the new spreadsheet data
                                    st.session_state.conflict_df = conflicts_df
                                    st.rerun() 
                                else:
                                    # No conflicts detected, bypass the UI and update directly
                                    rows = db.update_top_survey(df, file_date)
                                    st.success(f"Successfully updated {rows} records silently!")
                                    
                    # Step 3: Render Conflict Resolution UI
                    if 'conflict_df' in st.session_state:
                        st.warning(f"⚠️ Found {len(st.session_state.conflict_df)} conflicting coordinates!")
                        st.write("Existing database values differ from your upload. Uncheck **'Keep_New_Upload'** to discard the upload and keep the existing database values.")
                        
                        # Format the dataframe for the interactive data editor
                        disp_df = st.session_state.conflict_df[['clean_ID', 'North', 'db_n', 'East', 'db_e', 'Keep_New_Upload']].copy()
                        disp_df = disp_df.rename(columns={'North': 'New_North', 'db_n': 'Old_North', 'East': 'New_East', 'db_e': 'Old_East'})
                        
                        # Render the interactive grid
                        edited_conflicts = st.data_editor(disp_df, use_container_width=True, hide_index=True)
                        
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("Finalize Upload", type="primary"):
                                # Grab the IDs the user decided to keep from the new upload
                                keep_new_ids = edited_conflicts[edited_conflicts['Keep_New_Upload'] == True]['clean_ID'].tolist()
                                
                                # Combine the conflict resolutions with the clean inserts
                                final_df = df[
                                    (df['clean_ID'].isin(st.session_state.clean_inserts_df['clean_ID'])) | 
                                    (df['clean_ID'].isin(keep_new_ids))
                                ]
                                
                                if not final_df.empty:
                                    rows = db.update_top_survey(final_df, file_date)
                                    st.success(f"Successfully resolved conflicts and updated {rows} total records!")
                                else:
                                    st.info("No records were selected for update.")
                                    
                                # Clean up session state
                                del st.session_state.conflict_df
                                del st.session_state.clean_inserts_df
                                st.rerun()
                                
                        with col2:
                            if st.button("Cancel Upload"):
                                del st.session_state.conflict_df
                                del st.session_state.clean_inserts_df
                                st.rerun()
                                
                # ---------------------------------------------------------
                # STANDARD LOGIC: ALL OTHER IMPORT TYPES
                # ---------------------------------------------------------
                else:
                    # Clear top survey session state if user switches dropdown menus
                    if 'conflict_df' in st.session_state: del st.session_state.conflict_df
                    if 'clean_inserts_df' in st.session_state: del st.session_state.clean_inserts_df
                    
                    # NEW UI: Ask for overwrite mode if it is a Pipe import
                    pipe_overwrite_mode = "Append"
                    if import_type == "Pipe":
                        st.markdown("### Import Settings")
                        mode_choice = st.radio(
                            "Survey History Action:",
                            ["Append (Keep History)", "Overwrite (Replace All Previous)"],
                            help="Append will add this as a new survey. Overwrite will delete all older downhole surveys for the specific pipes in this file."
                        )
                        # Extract just the first word ('Append' or 'Overwrite') for the database function
                        pipe_overwrite_mode = mode_choice.split()[0]
                    
                    if st.button(f"Confirm & Upload {import_type} to BigQuery"):
                        with st.spinner("Uploading to BigQuery..."):
                            if import_type == "Pipe":
                                rows_inserted, unique_holes = db.import_downhole(df, import_type, file_date, overwrite_mode=pipe_overwrite_mode)
                                st.success(f"Successfully processed {len(unique_holes)} specific holes ({rows_inserted} rows) for {import_type}!")
                                st.info(f"Holes updated: {', '.join(unique_holes)}")
                                
                            elif import_type == "Baseline":
                                rows_inserted = db.import_baseline(df)
                                st.success(f"Successfully uploaded {rows_inserted} Baseline records!")
                                
                            elif import_type == "Pipe Details":
                                df.rename(columns=lambda x: 'pipe_type' if x.strip().lower() == 'type' else x, inplace=True)
                                rows_inserted = db.import_pipe_details(df)
                                st.success(f"Successfully updated {rows_inserted} Pipe Details!")
            
# 5. Main App Tabs
tab_data, tab_maps, tab_qc, tab_explorer = st.tabs(["Data & Analysis", "Map Visualizations", "QC & Single Hole", "Survey Explorer"])

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

    # -----------------------------------------
    # ELEVATION SLICE EXPORT
    # -----------------------------------------
    st.divider()
    st.subheader("📥 Export Elevation Slice")
    st.write("Generate a CSV of pipe coordinates at a specific target elevation. Angled pipes without downhole surveys will follow their design inclination and azimuth from the collar.")

    col1, col2 = st.columns([1, 2])
    
    with col1:
        export_elev = st.number_input("Target Elevation (Z)", value=250.0, step=10.0)
        
    with col2:
        st.write("") # Spacer to align button with input
        st.write("")
        if st.button("Calculate Slice Coordinates", type="primary"):
            with st.spinner(f"Calculating trajectories down to Elev {export_elev}..."):
                holes_df, surveys_df = db.get_all_data()

                if not holes_df.empty:
                    # 1. Fetch origin for coordinate shift calculations
                    proj_query = f"SELECT origin_north, origin_east FROM `{db.dataset}.projects` WHERE project_id = @pid"
                    params = [bigquery.ScalarQueryParameter("pid", "STRING", db.active_project_id)]
                    proj_res = list(db.client.query(proj_query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result())

                    orig_n = proj_res[0]['origin_north'] if proj_res and proj_res[0]['origin_north'] != 0.0 else holes_df['n_base'].mean()
                    orig_e = proj_res[0]['origin_east'] if proj_res and proj_res[0]['origin_east'] != 0.0 else holes_df['e_base'].mean()

                    # 2. Filter out 0.0 bug coordinates and safely fallback to design base
                    holes_df = holes_df[(holes_df['n_base'] != 0.0) & (holes_df['e_base'] != 0.0)].copy()
                    holes_df.loc[holes_df['n_top'] == 0.0, 'n_top'] = holes_df['n_base']
                    holes_df.loc[holes_df['e_top'] == 0.0, 'e_top'] = holes_df['e_base']

                    # 3. Shift coordinates to local 0,0 for accurate math engine geometry
                    holes_df['n_base'] = holes_df['n_base'] - orig_n
                    holes_df['e_base'] = holes_df['e_base'] - orig_e
                    holes_df['n_top'] = holes_df['n_top'] - orig_n
                    holes_df['e_top'] = holes_df['e_top'] - orig_e

                    # 4. Run the full trajectory and slice calculations
                    traj_df = math.calculate_trajectory(holes_df, surveys_df)
                    slice_df = math.get_slice_at_elevation(traj_df, export_elev)

                    if not slice_df.empty:
                        # 5. Shift back to absolute State Plane coordinates for the export
                        slice_df['Absolute_East'] = slice_df['East_New'] + orig_e
                        slice_df['Absolute_North'] = slice_df['North_New'] + orig_n
                        
                        # 6. Clean up the dataframe for the user
                        export_df = slice_df[['ID', 'Target_Elev', 'Absolute_North', 'Absolute_East', 'Survey_Status', 'Deviation', 'Deviation_Percent']].copy()
                        export_df = export_df.rename(columns={
                            'Target_Elev': 'Elevation',
                            'Absolute_North': 'Northing',
                            'Absolute_East': 'Easting',
                            'Survey_Status': 'Data_Source',
                            'Deviation': 'Deviation_From_Design_ft'
                        })
                        
                        # Round coordinates for cleaner output
                        export_df['Northing'] = export_df['Northing'].round(3)
                        export_df['Easting'] = export_df['Easting'].round(3)
                        export_df['Deviation_From_Design_ft'] = export_df['Deviation_From_Design_ft'].round(3)

                        st.success(f"Successfully calculated coordinates for {len(export_df)} pipes!")
                        st.dataframe(export_df.head())

                        # 7. Create the file download button
                        csv = export_df.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label="⬇️ Download CSV File",
                            data=csv,
                            file_name=f"pipe_slice_elev_{export_elev}.csv",
                            mime="text/csv",
                            type="primary"
                        )
                    else:
                        st.warning(f"No pipes intersect elevation {export_elev}. (Check if the elevation is deeper than your design lengths).")
                else:
                    st.error("No project data found.")
            
with tab_maps:
    st.subheader("Map & Spatial Visualizations")
    
    vis = SurveyVisualizer()
    
    map_type = st.selectbox(
        "Select Map Type:",
        ["Plan View (Design vs Actual)", "Grid Heatmap", "Pipe Heatmap", "Deviation Needles"]
    )
    
    # --- NEW: Add dynamic depth/elevation selectors OUTSIDE the button ---
    target_elev = 0.0
    if map_type in ["Grid Heatmap", "Pipe Heatmap"]:
        target_elev = st.number_input("Target Elevation (Z)", value=-20.0, step=10.0, help="Enter the elevation slice (e.g., -20.0 for 20ft depth if collar is 0)")
        
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
                    
                    # 2. Safely fallback any accidental 0.0 actuals to the design base
                    holes_df.loc[holes_df['n_top'] == 0.0, 'n_top'] = holes_df['n_base']
                    holes_df.loc[holes_df['e_top'] == 0.0, 'e_top'] = holes_df['e_base']
                    
                    # --- NEW: Drop invalid (0.0) coordinates so they don't get shifted into the negative millions ---
                    holes_df = holes_df[(holes_df['n_base'] != 0.0) & (holes_df['e_base'] != 0.0)].copy()
                    
                    # 3. Shift ALL coordinates universally before ANY map draws
                    holes_df['n_base'] = holes_df['n_base'] - orig_n
                    holes_df['e_base'] = holes_df['e_base'] - orig_e
                    holes_df['n_top'] = holes_df['n_top'] - orig_n
                    holes_df['e_top'] = holes_df['e_top'] - orig_e
                    
                    fig = None
                    
                    # --- NEW: Calculate True Trajectories instead of polyfilling ---
                    traj_df = pd.DataFrame()
                    if map_type in ["Grid Heatmap", "Pipe Heatmap", "Deviation Needles"]:
                        traj_df = math.calculate_trajectory(holes_df, surveys_df)
                    
                    # 4. Render the chosen map
                    if map_type == "Plan View (Design vs Actual)":
                        top_df = holes_df.copy()
                        top_df = top_df.rename(columns={'clean_id': 'ID', 'n_base': 'Design_N', 'e_base': 'Design_E', 'n_top': 'Actual_N', 'e_top': 'Actual_E'})
                        fig = vis.plot_top_deviation_map(top_df)
                        
                    elif map_type == "Grid Heatmap":
                        if not traj_df.empty:
                            slice_df = math.get_slice_at_elevation(traj_df, target_elev)
                            if not slice_df.empty:
                                fig = vis.generate_grid_heatmap(slice_df, depth_label=f"Elev {target_elev}", show_labels=True)
                            else:
                                st.warning(f"No trajectory data crosses elevation {target_elev}.")
                        else:
                            st.warning("No trajectory data calculated.")
                        
                    elif map_type == "Pipe Heatmap":
                        if not traj_df.empty:
                            slice_df = math.get_slice_at_elevation(traj_df, target_elev)
                            if not slice_df.empty:
                                fig = vis.generate_pipe_heatmap(slice_df, depth_label=f"Elev {target_elev}", show_labels=True)
                            else:
                                st.warning(f"No trajectory data crosses elevation {target_elev}.")
                        else:
                            st.warning("No trajectory data calculated.")
                        
                    elif map_type == "Deviation Needles":
                        if surveys_df.empty:
                            st.warning("Deviation needles require downhole survey data. Please import Pipe surveys first.")
                        else:
                            # Generate deviation vectors at 10-foot intervals based on the max depth
                            max_depth = int(surveys_df['depth'].max()) if not surveys_df.empty else 100
                            # Assumes downward trajectory (negative elevations)
                            target_elevations = [float(-z) for z in range(10, max_depth + 10, 10)]
                            
                            needles_df = math.get_multi_level_vectors(holes_df, surveys_df, target_elevations)
                            if not needles_df.empty:
                                fig = vis.plot_deviation_needles(needles_df)
                            else:
                                st.warning("No deviation vectors could be calculated.")
                                
                    if fig:
                        st.pyplot(fig)
                        
                except Exception as e:
                    st.error(f"Error rendering visualization: {e}")
            
with tab_qc:
    st.subheader("Data Management & QA/QC")
    
    # Fetch fresh data for QC analysis
    holes_df, surveys_df = db.get_all_data()
    
    if holes_df.empty:
        st.info("No data available for QC analysis.")
    else:
        # -----------------------------------------
        # TOOL 1: DATA ANOMALY FLAGS
        # -----------------------------------------
        st.markdown("### 🚩 Automated Data Flags")
        
        qc_col1, qc_col2 = st.columns(2)
        
        with qc_col1:
            st.write("**Missing Coordinates or Incomplete Top Surveys**")
            # Flag rows where coordinates are 0.0 or missing top surveys
            missing_coords = holes_df[
                (holes_df['n_base'] == 0.0) | 
                (holes_df['e_base'] == 0.0) | 
                (holes_df['has_top_survey'] == 0)
            ].copy()
            
            if not missing_coords.empty:
                st.dataframe(missing_coords[['id', 'n_base', 'e_base', 'n_top', 'e_top', 'has_top_survey']], use_container_width=True)
            else:
                st.success("All holes have valid base coordinates and top surveys!")

        with qc_col2:
            st.write("**Built vs Planned: High Collar Deviation (> 3 ft)**")
            # Utilize the math engine to find massive actual vs design discrepancies
            dev_df = math.get_top_deviation_vectors(holes_df)
            if not dev_df.empty:
                high_dev = dev_df[dev_df['Total_Deviation (ft)'] > 3.0].copy()
                
                # Add a tracking column for the backend schema mapping
                high_dev['approve'] = "Pending Review"
                
                if not high_dev.empty:
                    st.dataframe(high_dev, use_container_width=True)
                else:
                    st.success("No significant collar deviations detected.")
            else:
                st.info("Could not calculate deviation vectors.")

        st.divider()

        # -----------------------------------------
        # TOOL 2: DATA DELETION MANAGEMENT
        # -----------------------------------------
        st.markdown("### 🗑️ Delete Bad Data")
        
        del_col1, del_col2 = st.columns(2)
        
        with del_col1:
            st.write("**Delete Specific Survey Record**")
            surveyed_holes = db.get_surveyed_ids()
            
            if surveyed_holes:
                del_hole = st.selectbox("Select Hole:", options=surveyed_holes, key="del_hole")
                if del_hole:
                    details = db.get_hole_survey_details(del_hole)
                    if details:
                        # Format the dropdown to show Date, Type, and Point Count
                        detail_opts = {f"{d['date']} - {d['type']} ({d['pts']} pts)": d for d in details}
                        selected_key = st.selectbox("Select Survey to Delete:", options=list(detail_opts.keys()))
                        selected_detail = detail_opts[selected_key]
                        
                        if st.button(f"Delete {selected_detail['type']} from {selected_detail['date']}", type="primary"):
                            db.delete_survey_entry(del_hole, selected_detail['date'], selected_detail['type'])
                            st.success(f"Survey for {del_hole} deleted successfully!")
                            st.rerun()
                    else:
                        st.info("No survey data found for this specific hole.")
            else:
                st.info("No survey records available to delete.")
                    
        with del_col2:
            st.write("**Delete Entire Upload Batch by Date**")
            avail_dates = db.get_available_dates()
            
            if avail_dates:
                del_date = st.selectbox("Select Upload Date:", options=avail_dates, key="del_date")
                if st.button(f"Delete Entire Batch ({del_date})", type="primary"):
                    db.delete_batch_by_date(del_date)
                    st.success(f"All surveys from {del_date} have been removed!")
                    st.rerun()
            else:
                st.info("No upload batches available to delete.")

with tab_explorer:
    st.subheader("📅 Date-Based Survey Explorer")
    
    # 1. Fetch available dates from the database
    avail_dates = db.get_available_dates()
    
    if not avail_dates:
        st.info("No downhole survey dates found in the database.")
    else:
        col1, col2 = st.columns([1, 2])
        
        with col1:
            # 2. Select a date
            selected_date = st.selectbox("Select Survey Date:", options=avail_dates)
            
            # 3. Fetch and display the holes surveyed on that date
            date_holes = db.get_holes_by_date(selected_date)
            
            st.success(f"Found {len(date_holes)} surveys on this date.")
            with st.expander("View Included Holes", expanded=True):
                # Display as a clean comma-separated list
                st.write(", ".join(date_holes))
                
        with col2:
            if date_holes:
                # 4. Select a specific hole to visualize
                selected_hole = st.selectbox("Select a Hole to generate its deviation graph:", options=date_holes)
                
                if st.button("Generate 3-Panel Graph", type="primary"):
                    with st.spinner("Calculating trajectories and rendering graph..."):
                        holes_df, surveys_df = db.get_all_data()
                        
                        # Grab the base design data for this specific hole
                        hole_row = holes_df[holes_df['clean_id'] == selected_hole]
                        
                        if not hole_row.empty:
                            hole_row = hole_row.iloc[0]
                            
                            # Filter the survey data down to just this hole
                            hole_surveys = surveys_df[surveys_df['hole_id'] == selected_hole]
                            
                            # Calculate the full trajectory paths (Baseline + All Survey Dates)
                            comparison_df = math.calculate_single_hole_all_versions(hole_row, hole_surveys)
                            
                            # Generate the 3-panel plot using the visualizer
                            fig = vis.plot_hole_comparison(
                                comparison_df=comparison_df, 
                                hole_id=selected_hole,
                                show_casing=True
                            )
                            
                            if fig:
                                st.pyplot(fig)
                            else:
                                st.error("Failed to generate plot data.")
                        else:
                            st.error(f"Hole {selected_hole} not found in the primary design table.")
