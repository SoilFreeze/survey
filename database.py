import pandas as pd
from datetime import datetime
from google.cloud import bigquery
import streamlit as st

class ProjectDB:
    def __init__(self):
        # Authenticate using Streamlit Secrets
        self.client = bigquery.Client.from_service_account_info(st.secrets["gcp_service_account"])
        
        # Define your BigQuery Project and Dataset here
        self.project_id = st.secrets["gcp_service_account"]["project_id"]
        self.dataset_id = st.secrets["bq"]["dataset"]
        self.dataset_ref = f"{self.project_id}.{self.dataset_id}"
        
        self.current_project = None
        self._ensure_tables()

    def _ensure_tables(self):
        """Creates the tables exactly as your code expects them."""
        # 1. Create Holes Table
        holes_query = f"""
        CREATE TABLE IF NOT EXISTS `{self.dataset_ref}.holes` (
            id STRING, clean_id STRING, 
            n_base FLOAT64, e_base FLOAT64, z_base FLOAT64,
            n_top FLOAT64, e_top FLOAT64, z_top FLOAT64,
            has_top_survey INT64, design_az FLOAT64, 
            design_inc FLOAT64, design_len FLOAT64
        )
        """
        self.client.query(holes_query).result()

        # 2. Create Downhole Table (matching your Python logic)
        downhole_query = f"""
        CREATE TABLE IF NOT EXISTS `{self.dataset_ref}.downhole` (
            hole_id STRING, depth FLOAT64, 
            azimuth FLOAT64, inclination FLOAT64, 
            survey_type STRING, upload_date STRING
        )
        """
        self.client.query(downhole_query).result()

    def get_project_origin(self, project_id):
        """Fetches the shift/origin for a specific project."""
        query = f"""
            SELECT origin_north, origin_east 
            FROM `{self.dataset_ref}.projects` 
            WHERE project_id = @project_id
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("project_id", "STRING", project_id)]
        )
        
        # Execute the query and wait for the results
        query_job = self.client.query(query, job_config=job_config)
        rows = list(query_job.result())
        
        # Check if the list contains any rows
        if rows:
            return rows[0].origin_north, rows[0].origin_east
            
        return 0.0, 0.0 # Default origin if none specified
        
    def create_new_project(self, project_id, folder_path=None):
        """Sets the active project context (folder_path is ignored in BQ)"""
        self.current_project = project_id
        return project_id

    def open_project(self, project_id):
        """Sets the active project context."""
        self.current_project = project_id
        return True

    def get_available_projects(self):
        """Fetches a list of all unique projects."""
        # Ensure your table name is correct. If you created it as 'holes', 
        # ensure it's referenced as `your_project.survey.holes`
        query = f"SELECT DISTINCT project_id FROM `{self.dataset_ref}.holes` ORDER BY project_id"
        try:
            df = self.client.query(query).to_dataframe()
            return df['project_id'].tolist()
        except Exception as e:
            # If 'project_id' is not recognized, check your table schema in the GCP Console
            st.error(f"BigQuery Query Error: {e}")
            return []

    def import_baseline(self, df):
        return self.update_baseline_safely(df)

    def update_baseline_safely(self, df):
        if not self.current_project: return 0
        count = 0
        # Assuming your CSV columns are North/East/Elev/ID
        for _, row in df.iterrows():
            # Standardize defaults from your CSV
            d_az = float(row.get('Azimuth', 0.0))
            d_inc = float(row.get('Inclination', 0.0))
            d_len = float(row.get('Length', 0.0))

            query = f"""
                MERGE `{self.dataset_ref}.holes` T
                USING (SELECT @project_id AS project_id, @hole_id AS hole_id, @clean_id AS clean_id, 
                              @n AS design_n, @e AS design_e, @z AS design_z, 
                              @n AS actual_n, @e AS actual_e, @z AS actual_z, 
                              0 AS has_top_survey, @az AS design_az, @inc AS design_inc, @len AS design_length) S
                ON T.project_id = S.project_id AND T.hole_id = S.hole_id
                WHEN MATCHED THEN
                    UPDATE SET design_n=S.design_n, design_e=S.design_e, design_z=S.design_z,
                               actual_n=S.actual_n, actual_e=S.actual_e, actual_z=S.actual_z,
                               design_az=S.design_az, design_inc=S.design_inc, design_length=S.design_length
                WHEN NOT MATCHED THEN
                    INSERT (project_id, hole_id, clean_id, design_n, design_e, design_z, 
                            actual_n, actual_e, actual_z, has_top_survey, design_az, design_inc, design_length)
                    VALUES (S.project_id, S.hole_id, S.clean_id, S.design_n, S.design_e, S.design_z, 
                            S.actual_n, S.actual_e, S.actual_z, S.has_top_survey, S.design_az, S.design_inc, S.design_length)
            """
            
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("project_id", "STRING", self.current_project),
                    bigquery.ScalarQueryParameter("hole_id", "STRING", str(row['ID'])),
                    bigquery.ScalarQueryParameter("clean_id", "STRING", str(row['clean_ID'])),
                    bigquery.ScalarQueryParameter("n", "FLOAT64", float(row['North'])),
                    bigquery.ScalarQueryParameter("e", "FLOAT64", float(row['East'])),
                    bigquery.ScalarQueryParameter("z", "FLOAT64", float(row['Elev'])),
                    bigquery.ScalarQueryParameter("az", "FLOAT64", d_az),
                    bigquery.ScalarQueryParameter("inc", "FLOAT64", d_inc),
                    bigquery.ScalarQueryParameter("len", "FLOAT64", d_len),
                ]
            )
            self.client.query(query, job_config=job_config).result()
            count += 1
        return count

    def update_top_survey(self, df):
        if not self.current_project: return 0
        updated = 0
        query = f"""
            UPDATE `{self.dataset_ref}.holes`
            SET actual_n = @n, actual_e = @e, actual_z = @z
            WHERE project_id = @project_id AND hole_id = @clean_id
        """
        for _, row in df.iterrows():
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("n", "FLOAT64", float(row['North'])),
                    bigquery.ScalarQueryParameter("e", "FLOAT64", float(row['East'])),
                    bigquery.ScalarQueryParameter("z", "FLOAT64", float(row['Elev'])),
                    bigquery.ScalarQueryParameter("project_id", "STRING", self.current_project),
                    bigquery.ScalarQueryParameter("clean_id", "STRING", str(row['clean_ID'])),
                ]
            )
            res = self.client.query(query, job_config=job_config).result()
            if res.num_dml_affected_rows > 0: updated += 1
        return updated

    def import_downhole(self, df, survey_type, date_override=None):
        if not self.current_project: return 0
        final_date = date_override if date_override else datetime.now().strftime("%Y-%m-%d")
        ids = df['clean_ID'].unique().tolist()
        
        # 1. Clear existing data to prevent duplicates in 'surveys'
        delete_query = f"""
            DELETE FROM `{self.dataset_ref}.surveys`
            WHERE project_id = @project_id 
            AND survey_type = @survey_type 
            AND upload_date = CAST(@final_date AS DATE)
            AND hole_id IN UNNEST(@ids)
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("project_id", "STRING", self.current_project),
                bigquery.ScalarQueryParameter("survey_type", "STRING", survey_type),
                bigquery.ScalarQueryParameter("final_date", "STRING", final_date),
                bigquery.ArrayQueryParameter("ids", "STRING", ids),
            ]
        )
        self.client.query(delete_query, job_config=job_config).result()

        # 3. Prepare upload dataframe matching 'surveys' schema exactly
        upload_rows = []
        for _, row in df.iterrows():
            upload_rows.append({
                "project_id": self.current_project,
                "hole_id": str(row['clean_ID']),
                "length": float(row['Length']),
                "azimuth": float(row['Azimuth']),
                "inclination": float(row['Inclination']),
                "survey_type": survey_type,
                "upload_date": final_date
            })
        
        if upload_rows:
            upload_df = pd.DataFrame(upload_rows)
            # Ensure dates are properly cast for the dataframe before upload
            upload_df['upload_date'] = pd.to_datetime(upload_df['upload_date']).dt.date
            
            job = self.client.load_table_from_dataframe(upload_df, f"{self.dataset_ref}.surveys")
            job.result()
            return len(upload_rows)
        return 0

    def get_all_data(self):
        if not self.current_project: return pd.DataFrame(), pd.DataFrame()
        
        # Translate BQ 'holes' schema to what math_engine expects
        holes_query = f"""
            SELECT 
                hole_id AS id, 
                hole_id AS clean_id, /* Using hole_id as fallback since clean_id isn't in BQ */
                design_n AS n_base, design_e AS e_base, design_z AS z_base,
                actual_n AS n_top, actual_e AS e_top, actual_z AS z_top,
                CASE WHEN actual_n IS NOT NULL THEN 1 ELSE 0 END AS has_top_survey,
                design_inc, design_az, design_length AS design_len
            FROM `{self.dataset_ref}.holes` 
            WHERE project_id = '{self.current_project}'
        """
        
        # Pulling from 'surveys' instead of 'downhole'
        surveys_query = f"""
            SELECT 
                hole_id, length AS depth, azimuth, inclination, survey_type, CAST(upload_date AS STRING) AS upload_date
            FROM `{self.dataset_ref}.surveys` 
            WHERE project_id = '{self.current_project}'
        """
        
        holes = self.client.query(holes_query).to_dataframe()
        surveys = self.client.query(surveys_query).to_dataframe()
        return holes, surveys

    def get_surveyed_ids(self):
        if not self.current_project: return []
        query = f"""
            SELECT DISTINCT h.clean_id 
            FROM `{self.dataset_ref}.holes` h 
            JOIN `{self.dataset_ref}.downhole` d ON h.id = d.hole_id 
            WHERE h.project_id = @project_id AND d.project_id = @project_id
            ORDER BY h.clean_id
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("project_id", "STRING", self.current_project)])
        return [row[0] for row in self.client.query(query, job_config=job_config).result()]

    def get_available_dates(self):
        if not self.current_project: return []
        query = f"SELECT DISTINCT upload_date FROM `{self.dataset_ref}.downhole` WHERE project_id = '{self.current_project}' AND upload_date IS NOT NULL ORDER BY upload_date DESC"
        return [row[0] for row in self.client.query(query).result()]

    def get_holes_by_date(self, date_str):
        if not self.current_project: return []
        query = f"""
            SELECT DISTINCT h.clean_id 
            FROM `{self.dataset_ref}.holes` h 
            JOIN `{self.dataset_ref}.downhole` d ON h.id = d.hole_id 
            WHERE h.project_id = @project_id AND d.project_id = @project_id AND d.upload_date = @date_str
            ORDER BY h.clean_id
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("project_id", "STRING", self.current_project),
            bigquery.ScalarQueryParameter("date_str", "STRING", date_str)
        ])
        return [row[0] for row in self.client.query(query, job_config=job_config).result()]
