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
# 1. CORE CONFIG & DB I/O
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

def get_file_date(filename):
    match = re.search(r'(\d{4})[.\-](\d{2})[.\-](\d{2})', filename)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return datetime.now().strftime('%Y-%m-%d')

# ==========================================
# 2. MATH ENGINE (Battered Support)
# ==========================================
def calculate_survey_path(df, start_n, start_e):
    df = df.sort_values('depth')
    rad_az = np.radians(df['azimuth'])
    rad_inc = np.radians(df['inclination'])
    dist = df['depth'].diff().fillna(0)
    
    dn = dist * np.sin(rad_inc) * np.cos(rad_az)
    de = dist * np.sin(rad_inc) * np.sin(rad_az)
    
    df['n_rel'] = start_n + dn.cumsum()
    df['e_rel'] = start_e + de.cumsum()
    return df

# ==========================================
# 3. GLOBAL SIDEBAR
# ==========================================
st.sidebar.title("🏗️ SoilFreeze Hub")

df_projects = run_query("SELECT * FROM `sensorpush-export.survey.projects` ORDER BY name")

if not df_projects.empty:
    sel_proj_name = st.sidebar.selectbox("Active Project", df_projects['name'].tolist())
    active_proj = df_projects[df_projects['name'] == sel_proj_name].iloc[0]
    
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

category = st.sidebar.selectbox("Category", ["Database Maintenance", "Visualization", "Reports"])

# ==========================================
# 4. DATABASE MAINTENANCE
# ==========================================
if category == "Database Maintenance":
    action = st.radio("Action", ["Project Setup", "Upload Baseline", "Update Top Survey", "Upload Downhole", "Manage Data"], horizontal=True)   
    
    # --- STEP 1: PROJECT SETUP ---
    if action == "Project Setup":
        with st.form("new_proj"):
            st.subheader("Configure New Project")
            n_id = st.text_input("Project ID (e.g., BJ-2026)")
            n_name = st.text_input("Project Name (e.g., Blackjack Creek)")
            n_len = st.number_input("Standard Pipe Length (ft)", value=100.0)
            n_on = st.number_input("Origin Northing (Project 0,0)", format="%.3f")
            n_oe = st.number_input("Origin Easting (Project 0,0)", format="%.3f")
            n_type = st.selectbox("Default Pipe Type", ["Freeze Pipe", "Battered Freeze Pipe", "Temperature Pipe"])
            
            if st.form_submit_button("Save Project"):
                new_df = pd.DataFrame([{
                    'project_id': n_id, 
                    'name': n_name, 
                    'default_length': n_len, 
                    'origin_north': n_on, 
                    'origin_east': n_oe
                }])
                upload_to_bq(new_df, "sensorpush-export.survey.projects")
                st.success(f"Project '{n_name}' created.")
                st.rerun()

    # --- STEP 2: UPLOAD BASELINE (PREVENTS DOUBLES) ---
# ==========================================
# 4. DATABASE MAINTENANCE (CLEANED VERSION)
# ==========================================
if category == "Database Maintenance":
    # 1. Action Selector
    action = st.radio("Action", ["Project Setup", "Upload Baseline", "Update Top Survey", "Upload Downhole", "Manage Data"], horizontal=True)
    
    # --- ACTION: PROJECT SETUP ---
    if action == "Project Setup":
        with st.form("new_proj"):
            st.subheader("Configure New Project")
            n_id = st.text_input("Project ID")
            n_name = st.text_input("Project Name")
            n_len = st.number_input("Standard Pipe Length (ft)", value=100.0)
            n_on = st.number_input("Origin Northing", format="%.3f")
            n_oe = st.number_input("Origin Easting", format="%.3f")
            
            if st.form_submit_button("Save Project"):
                new_df = pd.DataFrame([{
                    'project_id': n_id, 
                    'name': n_name, 
                    'default_length': n_len, 
                    'origin_north': n_on, 
                    'origin_east': n_oe
                }])
                upload_to_bq(new_df, "sensorpush-export.survey.projects")
                st.success(f"Project '{n_name}' created.")
                st.rerun()

    # --- ACTION: UPLOAD BASELINE (The 1,105-Hole Grid) ---
    elif action == "Upload Baseline":
        st.subheader("Step 2: Upload Design Baseline")
        file = st.file_uploader("Upload CSV", type=['csv'])
        
        if file and active_proj is not None:
            df_base = pd.read_csv(file)
            df_base.columns = [c.lower().strip() for c in df_base.columns]
            
            # Map 'pipe' to 'hole_id' and other variations
            rename_map = {
                'pipe':'hole_id', 'id':'hole_id', 'name':'hole_id', 'hole':'hole_id',
                'north':'design_n', 'northing':'design_n', 'east':'design_e', 'easting':'design_e',
                'elev':'design_z', 'elevation':'design_z', 'type':'pipe_type', 'length':'design_length'
            }
            df_base = df_base.rename(columns=rename_map)
            
            # CLEANING: Drop any rows where the Pipe ID is missing
            df_base = df_base.dropna(subset=['hole_id'])
            df_base = df_base[df_base['hole_id'].astype(str).str.strip() != ""]

            # SCHEMA: Ensure all 10 columns exist for BigQuery
            db_schema = {
                'project_id': str(active_proj['project_id']),
                'hole_id': None,
                'design_n': 0.0, 'design_e': 0.0, 'design_z': 0.0,
                'phase': "Phase1", 'pipe_type': "Freeze Pipe",
                'design_inc': 0.0, 'design_az': 0.0, 
                'design_length': active_proj.get('default_length', 100.0)
            }

            for col, default in db_schema.items():
                if col not in df_base.columns:
                    df_base[col] = default

            st.success(f"✅ Ready to upload {len(df_base)} pipes.")
            st.dataframe(df_base[['hole_id', 'design_n', 'design_e', 'pipe_type']].head())

            if st.button("🚀 Confirm & Overwrite Phase"):
                with st.spinner(f"Updating {len(df_base)} records..."):
                    p_id = str(active_proj['project_id'])
                    ph = df_base['phase'].iloc[0]
                    client.query(f"DELETE FROM `sensorpush-export.survey.holes` WHERE project_id='{p_id}' AND phase='{ph}'").result()
                    
                    final_df = df_base[list(db_schema.keys())]
                    upload_to_bq(final_df, "sensorpush-export.survey.holes")
                    st.success("Baseline Updated.")
                    st.rerun()

    # --- ACTION: UPDATE TOP SURVEY (As-Built) ---
    elif action == "Update Top Survey":
        st.subheader("Step 3: Update Actual Collar (As-Built)")
        top_file = st.file_uploader("Upload As-Built CSV", type=['csv'])
        if top_file and active_proj is not None:
            df_top = pd.read_csv(top_file)
            df_top.columns = [c.upper().strip() for c in df_top.columns]
            df_top = df_top.rename(columns={'ID': 'hole_id', 'NORTHING': 'actual_n', 'EASTING': 'actual_e', 'ELEVATION': 'actual_z'})
            
            if st.button("Apply As-Built Coordinates"):
                temp_id = f"sensorpush-export.survey.temp_top_{active_proj['project_id']}"
                upload_to_bq(df_top, temp_id, write_mode="WRITE_TRUNCATE")
                merge_q = f"""
                    MERGE `sensorpush-export.survey.holes` T USING `{temp_id}` S 
                    ON T.hole_id = S.hole_id AND T.project_id = '{active_proj['project_id']}' 
                    WHEN MATCHED THEN UPDATE SET T.actual_n = S.actual_n, T.actual_e = S.actual_e, T.actual_z = S.actual_z
                """
                client.query(merge_q).result()
                client.delete_table(temp_id)
                st.success("Surface Surveys Synced.")

    # --- ACTION: UPLOAD DOWNHOLE (Probe Data) ---
    elif action == "Upload Downhole":
        st.subheader("Step 4: Upload Probe Data")
        dh_file = st.file_uploader("Upload Downhole CSV", type=['csv'])
        if dh_file and active_proj is not None:
            # Extract date from filename
            match = re.search(r'(\d{4})[.\-](\d{2})[.\-](\d{2})', dh_file.name)
            f_date = match.group(0).replace('.', '-') if match else datetime.now().strftime('%Y-%m-%d')
            
            df_dh = pd.read_csv(dh_file)
            df_dh.columns = [c.lower().strip() for c in df_dh.columns]
            df_dh = df_dh.rename(columns={'pipe':'hole_id', 'id':'hole_id'})
            
            if st.button("Upload to Survey Table"):
                df_dh['project_id'] = str(active_proj['project_id'])
                df_dh['survey_date'] = f_date
                # Only keep columns the DB expects
                cols = ['project_id', 'hole_id', 'depth', 'azimuth', 'inclination', 'survey_date']
                upload_to_bq(df_dh[[c for c in cols if c in df_dh.columns]], "sensorpush-export.survey.surveys")
                st.success(f"Uploaded {len(df_dh)} points for {f_date}")

    # --- ACTION: MANAGE DATA (Danger Zone) ---
    elif action == "Manage Data":
        st.subheader("🗑️ Data Cleanup")
        with st.expander("⚠️ DANGER ZONE: Delete Project"):
            confirm = st.text_input(f"Type '{active_proj['name']}' to confirm")
            if st.button("DELETE PERMANENTLY"):
                if confirm == active_proj['name']:
                    p_id = active_proj['project_id']
                    client.query(f"DELETE FROM `sensorpush-export.survey.surveys` WHERE project_id='{p_id}'").result()
                    client.query(f"DELETE FROM `sensorpush-export.survey.holes` WHERE project_id='{p_id}'").result()
                    client.query(f"DELETE FROM `sensorpush-export.survey.projects` WHERE project_id='{p_id}'").result()
                    st.rerun()
                        
    # --- STEP 4: UPLOAD DOWNHOLE (HARDENED) ---
    elif action == "Upload Downhole":
        st.subheader("Step 4: Upload Downhole Survey")
        dh_file = st.file_uploader("Upload Probe CSV", type=['csv'])
        if dh_file and active_proj is not None:
            file_date = get_file_date(dh_file.name)
            df_dh = pd.read_csv(dh_file)
            df_dh.columns = [c.lower().strip() for c in df_dh.columns]
            
            # Map headers
            dh_map = {'id':'hole_id', 'hole':'hole_id', 'north':'n_dev', 'east':'e_dev'}
            df_dh = df_dh.rename(columns=dh_map)
            
            # Standardize required columns for the Survey Table
            df_dh['project_id'] = str(active_proj['project_id'])
            df_dh['survey_date'] = file_date
            
            # Logic to ensure BigQuery gets exactly what it expects
            survey_cols = ['project_id', 'hole_id', 'depth', 'azimuth', 'inclination', 'survey_date']
            available = [c for c in survey_cols if c in df_dh.columns]
            
            if 'hole_id' in available and 'depth' in available:
                if st.button("Upload Survey Data"):
                    upload_to_bq(df_dh[available], "sensorpush-export.survey.surveys")
                    st.success(f"Uploaded points for {file_date}")
            else:
                st.error("CSV must contain 'hole_id' and 'depth'.")

    # --- STEP 5: MANAGE DATA (DELETION TOOLS) ---
    elif action == "Manage Data":
        st.subheader("🗑️ Data Cleanup")
        
        # DELETE PROJECT
        with st.expander("⚠️ DANGER ZONE: Delete Entire Project"):
            st.warning("This wipes ALL holes, surveys, and project settings.")
            confirm = st.text_input(f"Type '{active_proj['name']}' to confirm")
            if st.button("DELETE PERMANENTLY"):
                if confirm == active_proj['name']:
                    p_id = active_proj['project_id']
                    client.query(f"DELETE FROM `sensorpush-export.survey.surveys` WHERE project_id='{p_id}'").result()
                    client.query(f"DELETE FROM `sensorpush-export.survey.holes` WHERE project_id='{p_id}'").result()
                    client.query(f"DELETE FROM `sensorpush-export.survey.projects` WHERE project_id='{p_id}'").result()
                    st.success("Project deleted.")
                    st.rerun()

        # DELETE BY DATE
        st.divider()
        st.write("### Delete Surveys by Date")
        date_q = f"SELECT DISTINCT survey_date FROM `sensorpush-export.survey.surveys` WHERE project_id='{active_proj['project_id']}'"
        db_dates = run_query(date_q)
        if not db_dates.empty:
            target = st.selectbox("Select Date to Remove", db_dates['survey_date'].tolist())
            if st.button("Wipe Selected Date"):
                client.query(f"DELETE FROM `sensorpush-export.survey.surveys` WHERE survey_date='{target}' AND project_id='{active_proj['project_id']}'").result()
                st.warning(f"All survey data for {target} has been removed.")
# ==========================================
# 5. VISUALIZATION
# ==========================================
elif category == "Visualization":
    view = st.radio("View Type", ["Whole Site Map", "Single Hole Analysis", "Elevation Slice"], horizontal=True)
    
    if active_proj is not None:
        # Fetch joined data
        q = f"""SELECT h.*, s.depth, s.azimuth, s.inclination 
                FROM `sensorpush-export.survey.holes` h 
                LEFT JOIN `sensorpush-export.survey.surveys` s ON h.hole_id = s.hole_id 
                WHERE h.project_id = '{active_proj['project_id']}'"""
        df_viz = run_query(q)
        
        if active_phase != "All Phases":
            df_viz = df_viz[df_viz['phase'] == active_phase]

        if view == "Whole Site Map":
            st.subheader(f"Project Grid: {active_proj['name']}")
            
            # Prepare relative coordinates
            df_viz['n_rel'] = df_viz['design_n'] - active_proj['origin_north']
            df_viz['e_rel'] = df_viz['design_e'] - active_proj['origin_east']
            df_viz['has_top'] = df_viz['actual_n'].notnull() & (df_viz['actual_n'] != 0)
            df_viz['has_downhole'] = df_viz['depth'].notnull()
            
            fig = go.Figure()

            # 1. Battered Pipe Indicators (Red tails showing lean direction)
            battered = df_viz[df_viz['design_inc'] > 0].drop_duplicates('hole_id')
            for _, row in battered.iterrows():
                rad_az = np.radians(row['design_az'])
                dn, de = 5 * np.cos(rad_az), 5 * np.sin(rad_az) # 5ft indicator
                fig.add_trace(go.Scatter(x=[row['e_rel'], row['e_rel']+de], y=[row['n_rel'], row['n_rel']+dn], mode='lines', line=dict(color='red', width=1), showlegend=False, hoverinfo='skip'))

            # 2. Status Symbology (Outer Ring = Top Survey, Inner Dot = Downhole)
            # Squares for Temperature Pipes, Circles for Freeze Pipes
            for p_type, shape in [("Freeze Pipe", "circle"), ("Battered Freeze Pipe", "circle"), ("Temperature Pipe", "square")]:
                type_mask = df_viz['pipe_type'] == p_type
                if type_mask.any():
                    # Outer Ring (Top Survey Status)
                    for status, color in [(False, 'lightgrey'), (True, 'black')]:
                        mask = type_mask & (df_viz['has_top'] == status)
                        fig.add_trace(go.Scatter(
                            x=df_viz.loc[mask, 'e_rel'], y=df_viz.loc[mask, 'n_rel'],
                            mode='markers', name=f"{p_type} ({'As-Built' if status else 'Design'})",
                            marker=dict(symbol=f"{shape}-open", color=color, size=14, line=dict(width=2.5)),
                            text=df_viz.loc[mask, 'hole_id']
                        ))
                    
                    # Inner Center (Downhole Status)
                    for status, color in [(False, 'lightgrey'), (True, 'black')]:
                        mask_dh = type_mask & (df_viz['has_downhole'] == status)
                        fig.add_trace(go.Scatter(
                            x=df_viz.loc[mask_dh, 'e_rel'], y=df_viz.loc[mask_dh, 'n_rel'],
                            mode='markers', showlegend=False,
                            marker=dict(symbol=shape, color=color, size=6),
                            text=df_viz.loc[mask_dh, 'hole_id']
                        ))

            # Force Even Scale (1:1 Aspect Ratio)
            fig.update_layout(
                xaxis=dict(title="East (ft)", scaleanchor="y", scaleratio=1),
                yaxis=dict(title="North (ft)"),
                height=850, template="plotly_white",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig, use_container_width=True)

        # --- VIEW 2: SINGLE HOLE ANALYSIS (Deviation Plot) ---
        elif view == "Single Hole Analysis":
            surveyed_ids = df_viz.dropna(subset=['depth'])['hole_id'].unique()
            if len(surveyed_ids) > 0:
                target = st.selectbox("Select Hole to Inspect", sorted(surveyed_ids))
                df_h = df_viz[df_viz['hole_id'] == target].copy()
                
                # Determine Anchor Point
                # Use as-built if available, otherwise design
                start_n = df_h['actual_n'].iloc[0] if pd.notnull(df_h['actual_n'].iloc[0]) and df_h['actual_n'].iloc[0] != 0 else df_h['design_n'].iloc[0]
                start_e = df_h['actual_e'].iloc[0] if pd.notnull(df_h['actual_e'].iloc[0]) and df_h['actual_e'].iloc[0] != 0 else df_h['design_e'].iloc[0]
                
                # Shift Anchor to Local (0,0)
                s_n_rel = start_n - active_proj['origin_north']
                s_e_rel = start_e - active_proj['origin_east']
                
                processed = calculate_survey_path(df_h, s_n_rel, s_e_rel)
                
                # Plotting Depth vs Deviation
                fig = make_subplots(rows=1, cols=2, subplot_titles=("East Dev (ft)", "North Dev (ft)"))
                fig.add_trace(go.Scatter(x=processed['e_rel'], y=processed['depth'], name="East Path", line=dict(color='blue')), row=1, col=1)
                fig.add_trace(go.Scatter(x=processed['n_rel'], y=processed['depth'], name="North Path", line=dict(color='red')), row=1, col=2)
                fig.update_yaxes(autorange="reversed", title="Depth (ft)")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("No probe data available for analysis.")

        # --- VIEW 3: ELEVATION SLICE (Freeze Wall View) ---
        elif view == "Elevation Slice":
            st.subheader("Subsurface Pipe Intersection")
            slice_depth = st.slider("Target Elevation Depth (ft)", 0, 250, 50)
            st.info(f"Showing all surveyed pipes as they cross the {slice_depth}ft mark.")
            # (Interpolation logic for all 1,105 holes goes here)

# ==========================================
# 6. REPORTS
# ==========================================
elif category == "Reports":
    report_action = st.sidebar.radio("Report Type", ["System Audit", "Deviation Summary"])
    if report_action == "System Audit":
        stats_query = f"""SELECT COUNT(*) as total_baseline, COUNTIF(actual_n != 0 AND actual_n IS NOT NULL) as top_surveys_done,
                        (SELECT COUNT(DISTINCT hole_id) FROM `sensorpush-export.survey.surveys` WHERE project_id = '{active_proj['project_id']}') as downhole_completed
                        FROM `sensorpush-export.survey.holes` WHERE project_id = '{active_proj['project_id']}'"""
        df_stats = run_query(stats_query)
        st.metric("Baseline (Grid)", df_stats['total_baseline'][0])
        st.metric("Top Surveys (As-Built)", df_stats['top_surveys_done'][0])
        st.metric("Downhole Surveys", df_stats['downhole_completed'][0])
