import streamlit as st
import pandas as pd
from google.cloud import bigquery

# Navigation for your flow
menu = ["Project Setup", "Upload Baseline", "Upload Top Survey", "Upload Downhole"]
choice = st.sidebar.selectbox("Workflow Step", menu)

# --- STEP 1: CREATE PROJECT ---
if choice == "Project Setup":
    st.header("Step 1: Create or Select Project")
    with st.form("new_project"):
        p_id = st.text_input("Project ID (e.g., SITE-2024)")
        p_name = st.text_input("Project Name")
        o_n = st.number_input("Origin Northing (for 0,0 shift)", format="%.3f")
        o_e = st.number_input("Origin Easting (for 0,0 shift)", format="%.3f")
        if st.form_submit_button("Save Project"):
            # Insert into BQ 'projects' table
            st.success(f"Project {p_name} created.")

# --- STEP 2: BASELINE ---
elif choice == "Upload Baseline":
    st.header("Step 2: Upload Design Baseline")
    # CSV should have: hole_id, design_n, design_e, design_z
    file = st.file_uploader("Upload Baseline CSV", type=['csv'])
    if file:
        df = pd.read_csv(file)
        # Push to BQ 'holes' table

# --- STEP 4: DOWNHOLE ---
elif choice == "Upload Downhole":
    st.header("Step 4: Upload Downhole Survey")
    # CSV should have: hole_id, depth, azimuth, inclination
    file = st.file_uploader("Upload Boretrak/Downhole CSV", type=['csv'])
    if file:
        # 1. Ask user which project this belongs to
        # 2. Append to BQ 'surveys' table
