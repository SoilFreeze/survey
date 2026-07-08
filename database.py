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
        result = self.client.query(query, job_config=job_config).fetchone()
        if result:
            return result.origin_north, result.origin_east
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
        has_az, has_inc, has_len = 'Azimuth' in df.columns, 'Inclination' in df.columns, 'Length' in df.columns

        # Using a MERGE statement to UPSERT data into BigQuery
        query = f"""
            MERGE `{self.dataset_ref}.holes` T
            USING (SELECT @project_id AS project_id, @id AS id, @clean_id AS clean_id, 
                          @n AS n_base, @e AS e_base, @z AS z_base, 
                          @n AS n_top, @e AS e_top, @z AS z_top, 
                          0 AS has_top_survey, @az AS design_az, @inc AS design_inc, @len AS design_len) S
            ON T.project_id = S.project_id AND T.id = S.id
            WHEN MATCHED THEN
                UPDATE SET n_base=S.n_base, e_base=S.e_base, z_base=S.z_base,
                           n_top=S.n_top, e_top=S.e_top, z_top=S.z_top,
                           design_az=S.design_az, design_inc=S.design_inc, design_len=S.design_len
            WHEN NOT MATCHED THEN
                INSERT (project_id, id, clean_id, n_base, e_base, z_base, n_top, e_top, z_top, has_top_survey, design_az, design_inc, design_len)
                VALUES (S.project_id, S.id, S.clean_id, S.n_base, S.e_base, S.z_base, S.n_top, S.e_top, S.z_top, S.has_top_survey, S.design_az, S.design_inc, S.design_len)
        """

        for _, row in df.iterrows():
            d_az = float(row['Azimuth']) if has_az and pd.notna(row['Azimuth']) else 0.0
            d_inc = float(row['Inclination']) if has_inc and pd.notna(row['Inclination']) else 0.0
            d_len = float(row['Length']) if has_len and pd.notna(row['Length']) else 0.0

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("project_id", "STRING", self.current_project),
                    bigquery.ScalarQueryParameter("id", "STRING", str(row['ID'])),
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
            SET n_top = @n, e_top = @e, z_top = @z, has_top_survey = 1
            WHERE project_id = @project_id AND clean_id = @clean_id
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
        
        # 1. Clear existing data to prevent duplicates
        delete_query = f"""
            DELETE FROM `{self.dataset_ref}.downhole`
            WHERE project_id = @project_id 
            AND survey_type = @survey_type 
            AND upload_date = @final_date
            AND hole_id IN (SELECT id FROM `{self.dataset_ref}.holes` WHERE project_id = @project_id AND clean_id IN UNNEST(@ids))
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

        # 2. Fetch real IDs mapping
        mapping_query = f"SELECT clean_id, id FROM `{self.dataset_ref}.holes` WHERE project_id = @project_id"
        mapping_config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("project_id", "STRING", self.current_project)])
        mapping_df = self.client.query(mapping_query, job_config=mapping_config).to_dataframe()
        id_map = dict(zip(mapping_df['clean_id'], mapping_df['id']))

        # 3. Prepare upload dataframe and push in bulk (much faster than row-by-row)
        upload_rows = []
        for _, row in df.iterrows():
            real_id = id_map.get(str(row['clean_ID']))
            if real_id:
                upload_rows.append({
                    "project_id": self.current_project,
                    "hole_id": real_id,
                    "depth": float(row['Length']),
                    "azimuth": float(row['Azimuth']),
                    "inclination": float(row['Inclination']),
                    "survey_type": survey_type,
                    "upload_date": final_date
                })
        
        if upload_rows:
            upload_df = pd.DataFrame(upload_rows)
            # Load straight to BigQuery
            job = self.client.load_table_from_dataframe(upload_df, f"{self.dataset_ref}.downhole")
            job.result()
            return len(upload_rows)
        return 0

    def get_all_data(self):
        if not self.current_project: return pd.DataFrame(), pd.DataFrame()
        
        # Mapping your schema to what your math_engine expects
        holes_query = f"""
            SELECT 
                hole_id AS id, 
                hole_id AS clean_id, 
                design_n AS n_base, design_e AS e_base, design_z AS z_base,
                actual_n AS n_top, actual_e AS e_top, actual_z AS z_top,
                design_inc, design_az, design_length AS design_len
            FROM `{self.dataset_ref}.holes` 
            WHERE project_id = '{self.current_project}'
        """
        
        # Use your 'surveys' table for survey data
        surveys_query = f"""
            SELECT 
                hole_id, length AS depth, azimuth, inclination, survey_type, upload_date
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
