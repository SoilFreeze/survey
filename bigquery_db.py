import pandas as pd
import streamlit as st
from google.cloud import bigquery
from google.oauth2 import service_account
from datetime import datetime

class BigQueryDB:
    def __init__(self, project_id):
        # 1. Load credentials from Streamlit Secrets
        try:
            gcp_secrets = st.secrets["gcp_service_account"]
            credentials = service_account.Credentials.from_service_account_info(gcp_secrets)
            
            # 2. Initialize the BigQuery client explicitly using the secrets
            self.client = bigquery.Client(
                credentials=credentials,
                project=credentials.project_id
            )
            
            # 3. Dynamically set the dataset from secrets
            dataset_name = st.secrets["bq"]["dataset"]
            self.dataset = f"{credentials.project_id}.{dataset_name}"
            
        except Exception as e:
            st.error(f"Failed to authenticate with BigQuery. Please check your Streamlit Secrets. Error: {e}")
            self.client = None
            
        self.active_project_id = str(project_id)

    def _execute_query(self, query, params=None):
        """Helper to run DML/SQL queries safely."""
        job_config = bigquery.QueryJobConfig()
        if params:
            job_config.query_parameters = params
        job = self.client.query(query, job_config=job_config)
        return job.result()

    def get_all_projects(self):
        """Fetches a list of all existing project IDs."""
        if not self.client: return []
        query = f"SELECT project_id FROM `{self.dataset}.projects` ORDER BY created_at DESC"
        try:
            res = self.client.query(query).result()
            return [row['project_id'] for row in res]
        except Exception as e:
            st.error(f"Failed to fetch projects: {e}")
            return []

    def create_new_project(self, new_id, name, origin_north=0.0, origin_east=0.0, default_length=200.0):
        """Inserts a new project into the BigQuery projects table."""
        if not self.client: return False
        
        query = f"""
            INSERT INTO `{self.dataset}.projects` 
            (project_id, name, origin_north, origin_east, created_at, default_length)
            VALUES (@project_id, @name, @origin_north, @origin_east, CURRENT_TIMESTAMP(), @default_length)
        """
        params = [
            bigquery.ScalarQueryParameter("project_id", "STRING", str(new_id)),
            bigquery.ScalarQueryParameter("name", "STRING", str(name)),
            bigquery.ScalarQueryParameter("origin_north", "FLOAT64", float(origin_north)),
            bigquery.ScalarQueryParameter("origin_east", "FLOAT64", float(origin_east)),
            bigquery.ScalarQueryParameter("default_length", "FLOAT64", float(default_length))
        ]
        try:
            self._execute_query(query, params)
            return True
        except Exception as e:
            st.error(f"Failed to create project. It may already exist. Error: {e}")
            return False
    
    def get_all_data(self):
        """Fetches holes and surveys, aliasing BQ schema back to legacy math engine schema."""
        holes_query = f"""
            SELECT 
                hole_id AS id,
                hole_id AS clean_id,
                COALESCE(design_n, 0.0) AS n_base,
                COALESCE(design_e, 0.0) AS e_base,
                COALESCE(design_z, 0.0) AS z_base,
                COALESCE(actual_n, design_n, 0.0) AS n_top,
                COALESCE(actual_e, design_e, 0.0) AS e_top,
                COALESCE(actual_z, design_z, 0.0) AS z_top,
                CASE WHEN actual_n IS NOT NULL THEN 1 ELSE 0 END AS has_top_survey,
                COALESCE(design_az, 0.0) AS design_az,
                COALESCE(design_inc, 0.0) AS design_inc,
                COALESCE(design_length, 0.0) AS design_len
            FROM `{self.dataset}.holes`
            WHERE project_id = @project_id
        """
        
        surveys_query = f"""
            SELECT 
                hole_id,
                length AS depth,
                azimuth,
                inclination,
                survey_type,
                CAST(upload_date AS STRING) AS upload_date
            FROM `{self.dataset}.surveys`
            WHERE project_id = @project_id
        """
        
        params = [bigquery.ScalarQueryParameter("project_id", "STRING", self.active_project_id)]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        
        try:
            holes_df = self.client.query(holes_query, job_config=job_config).to_dataframe()
            surveys_df = self.client.query(surveys_query, job_config=job_config).to_dataframe()
            return holes_df, surveys_df
        except Exception as e:
            print(f"BigQuery fetch failed: {e}")
            return pd.DataFrame(), pd.DataFrame()

    def get_project_stats(self):
        """Fetches quick summary statistics for the active project."""
        if not self.client: return {"total": 0, "top": 0, "downhole": 0}
        
        query = f"""
            SELECT 
                (SELECT COUNT(*) FROM `{self.dataset}.holes` WHERE project_id = @pid) as total_holes,
                (SELECT COUNT(*) FROM `{self.dataset}.holes` WHERE project_id = @pid AND actual_n IS NOT NULL) as top_survey_holes,
                (SELECT COUNT(DISTINCT hole_id) FROM `{self.dataset}.surveys` WHERE project_id = @pid) as downhole_holes
        """
        params = [bigquery.ScalarQueryParameter("pid", "STRING", self.active_project_id)]
        try:
            res = self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
            row = next(res)
            return {
                "total": row['total_holes'],
                "top": row['top_survey_holes'],
                "downhole": row['downhole_holes']
            }
        except Exception as e:
            st.error(f"Failed to fetch stats: {e}")
            return {"total": 0, "top": 0, "downhole": 0}
    
    def import_baseline(self, df):
        """Replaces existing holes with new baseline design data."""
        if df.empty or not self.client: return 0
        
        df['project_id'] = self.active_project_id
        
        # FAILSAFE: Ensure required coordinate columns exist before renaming
        for col in ['North', 'East', 'Elev', 'Azimuth', 'Inclination', 'Length']:
            if col not in df.columns: 
                df[col] = 0.0
                
        df = df.rename(columns={
            'clean_ID': 'hole_id', 'North': 'design_n', 'East': 'design_e', 'Elev': 'design_z',
            'Azimuth': 'design_az', 'Inclination': 'design_inc', 'Length': 'design_length'
        })
        
        bq_df = df[['hole_id', 'project_id', 'design_n', 'design_e', 'design_z', 'design_az', 'design_inc', 'design_length']]
        bq_df = bq_df.drop_duplicates(subset=['hole_id'], keep='last')
        
        temp_table = f"{self.dataset}.temp_holes_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        job = self.client.load_table_from_dataframe(bq_df, temp_table)
        job.result()
        
        merge_query = f"""
            MERGE `{self.dataset}.holes` T
            USING `{temp_table}` S
            ON T.hole_id = S.hole_id AND T.project_id = S.project_id
            WHEN MATCHED THEN
              UPDATE SET design_n = S.design_n, design_e = S.design_e, design_z = S.design_z,
                         design_az = S.design_az, design_inc = S.design_inc, design_length = S.design_length
            WHEN NOT MATCHED THEN
              INSERT (hole_id, project_id, design_n, design_e, design_z, design_az, design_inc, design_length)
              VALUES (S.hole_id, S.project_id, S.design_n, S.design_e, S.design_z, S.design_az, S.design_inc, S.design_length)
        """
        self._execute_query(merge_query)
        self.client.delete_table(temp_table, not_found_ok=True)
        return len(bq_df)
        
    def update_baseline_safely(self, df):
        """In BigQuery, our MERGE query in import_baseline handles safe upserts automatically."""
        return self.import_baseline(df)

    def update_top_survey(self, df, date_str):
        """Updates the 'actual' top coordinates and survey date for existing holes."""
        if df.empty or not self.client: return 0
        df['project_id'] = self.active_project_id
        df['top_survey_date'] = pd.to_datetime(date_str).strftime('%Y-%m-%d')
        
        # FAILSAFE: Ensure coordinate columns exist before renaming
        for col in ['North', 'East', 'Elev']:
            if col not in df.columns: 
                df[col] = 0.0
                
        df = df.rename(columns={'clean_ID': 'hole_id', 'North': 'actual_n', 'East': 'actual_e', 'Elev': 'actual_z'})
        
        bq_df = df[['hole_id', 'project_id', 'actual_n', 'actual_e', 'actual_z', 'top_survey_date']]
        bq_df = bq_df.drop_duplicates(subset=['hole_id'], keep='last')
        
        temp_table = f"{self.dataset}.temp_top_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        job = self.client.load_table_from_dataframe(bq_df, temp_table)
        job.result()
        
        merge_query = f"""
            MERGE `{self.dataset}.holes` T
            USING `{temp_table}` S
            ON T.hole_id = S.hole_id AND T.project_id = S.project_id
            WHEN MATCHED THEN
              UPDATE SET actual_n = S.actual_n, actual_e = S.actual_e, actual_z = S.actual_z, top_survey_date = S.top_survey_date
        """
        self._execute_query(merge_query)
        self.client.delete_table(temp_table, not_found_ok=True)
        return len(bq_df)
        
    def import_pipe_details(self, df):
        """Updates the pipe_type and design_length for existing holes."""
        if df.empty or not self.client: return 0
        
        df['project_id'] = self.active_project_id
        df = df.rename(columns={'clean_ID': 'hole_id', 'Length': 'design_length'})
        
        # Ensure columns exist to prevent crashes
        if 'pipe_type' not in df.columns: df['pipe_type'] = "Unknown"
        if 'design_length' not in df.columns: df['design_length'] = 200.0
            
        bq_df = df[['hole_id', 'project_id', 'pipe_type', 'design_length']]
        bq_df = bq_df.drop_duplicates(subset=['hole_id'], keep='last')
        
        temp_table = f"{self.dataset}.temp_pipedetails_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        job = self.client.load_table_from_dataframe(bq_df, temp_table)
        job.result()
        
        merge_query = f"""
            MERGE `{self.dataset}.holes` T
            USING `{temp_table}` S
            ON T.hole_id = S.hole_id AND T.project_id = S.project_id
            WHEN MATCHED THEN
              UPDATE SET pipe_type = S.pipe_type, design_length = S.design_length
            WHEN NOT MATCHED THEN
              INSERT (hole_id, project_id, pipe_type, design_length)
              VALUES (S.hole_id, S.project_id, S.pipe_type, S.design_length)
        """
        self._execute_query(merge_query)
        self.client.delete_table(temp_table, not_found_ok=True)
        return len(bq_df)

    def import_downhole(self, df, survey_type, date_override=None):
        """Deletes existing survey data ONLY for the holes in this file, then uploads new points."""
        if df.empty or not self.client: return 0, []
        
        final_date = date_override if date_override else datetime.now().strftime("%Y-%m-%d")
        
        # 1. Append metadata
        df['project_id'] = self.active_project_id
        df['survey_type'] = survey_type
        df['upload_date'] = pd.to_datetime(final_date).date() 
        
        # 2. Map ID correctly
        if 'clean_ID' in df.columns:
            df['hole_id'] = df['clean_ID']
        elif 'ID' in df.columns:
            df['hole_id'] = df['ID']
            
        # Ensure it is a string and drop any empty/NaN IDs from the CSV
        df['hole_id'] = df['hole_id'].astype(str).str.strip()
        df = df[df['hole_id'] != 'nan']
        df = df[df['hole_id'] != '']
        
        unique_holes = df['hole_id'].unique().tolist()
        
        if not unique_holes:
            return 0, []
            
        # 3. Clear out old data ONLY for the specific holes in this file on this date
        delete_query = f"""
            DELETE FROM `{self.dataset}.surveys`
            WHERE project_id = @project_id 
              AND survey_type = @survey_type 
              AND upload_date = CAST(@upload_date AS DATE)
              AND hole_id IN UNNEST(@hole_ids)
        """
        params = [
            bigquery.ScalarQueryParameter("project_id", "STRING", self.active_project_id),
            bigquery.ScalarQueryParameter("survey_type", "STRING", survey_type),
            bigquery.ScalarQueryParameter("upload_date", "STRING", final_date),
            bigquery.ArrayQueryParameter("hole_ids", "STRING", unique_holes)
        ]
        self._execute_query(delete_query, params)

        # 4. Standardize required columns
        rename_map = {'Length': 'length', 'Azimuth': 'azimuth', 'Inclination': 'inclination'}
        df = df.rename(columns=rename_map)
        
        # 5. Failsafe for missing columns & blank cells
        for col in ['length', 'azimuth', 'inclination']:
            if col not in df.columns:
                df[col] = 0.0
            # Force to numeric, replacing any random text with 0.0
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0) 
            
        # 6. Extract only the columns BigQuery wants
        bq_df = df[['hole_id', 'project_id', 'length', 'azimuth', 'inclination', 'survey_type', 'upload_date']]
        bq_df = bq_df.loc[:, ~bq_df.columns.duplicated()]
        
        # 7. Upload
        job = self.client.load_table_from_dataframe(bq_df, f"{self.dataset}.surveys")
        job.result()
        
        # Return the row count AND the list of unique holes so the UI can prove it
        return len(bq_df), unique_holes

    def get_surveyed_ids(self):
        query = f"SELECT DISTINCT hole_id FROM `{self.dataset}.surveys` WHERE project_id = @pid ORDER BY hole_id"
        params = [bigquery.ScalarQueryParameter("pid", "STRING", self.active_project_id)]
        res = self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
        return [row[0] for row in res]

    def get_available_dates(self):
        query = f"SELECT DISTINCT CAST(upload_date AS STRING) FROM `{self.dataset}.surveys` WHERE project_id = @pid AND upload_date IS NOT NULL ORDER BY 1 DESC"
        params = [bigquery.ScalarQueryParameter("pid", "STRING", self.active_project_id)]
        res = self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
        return [row[0] for row in res]

    def get_holes_by_date(self, date_str):
        query = f"""
            SELECT DISTINCT hole_id 
            FROM `{self.dataset}.surveys` 
            WHERE project_id = @pid AND CAST(upload_date AS STRING) = @date 
            ORDER BY hole_id
        """
        params = [
            bigquery.ScalarQueryParameter("pid", "STRING", self.active_project_id),
            bigquery.ScalarQueryParameter("date", "STRING", date_str)
        ]
        res = self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
        return [row[0] for row in res]

    def get_hole_survey_details(self, clean_id):
        query = f"""
            SELECT CAST(upload_date AS STRING) as date, survey_type as type, COUNT(*) as pts
            FROM `{self.dataset}.surveys`
            WHERE project_id = @pid AND hole_id = @hid
            GROUP BY upload_date, survey_type
            ORDER BY upload_date DESC
        """
        params = [
            bigquery.ScalarQueryParameter("pid", "STRING", self.active_project_id),
            bigquery.ScalarQueryParameter("hid", "STRING", clean_id)
        ]
        res = self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
        return [{'date': r['date'], 'type': r['type'], 'pts': r['pts']} for r in res]

    def delete_survey_entry(self, clean_id, date_str, survey_type):
        query = f"""
            DELETE FROM `{self.dataset}.surveys` 
            WHERE project_id = @pid AND hole_id = @hid AND CAST(upload_date AS STRING) = @date AND survey_type = @stype
        """
        params = [
            bigquery.ScalarQueryParameter("pid", "STRING", self.active_project_id),
            bigquery.ScalarQueryParameter("hid", "STRING", clean_id),
            bigquery.ScalarQueryParameter("date", "STRING", date_str),
            bigquery.ScalarQueryParameter("stype", "STRING", survey_type)
        ]
        self._execute_query(query, params)

    def delete_batch_by_date(self, date_str):
        query = f"""
            DELETE FROM `{self.dataset}.surveys` 
            WHERE project_id = @pid AND CAST(upload_date AS STRING) = @date
        """
        params = [
            bigquery.ScalarQueryParameter("pid", "STRING", self.active_project_id),
            bigquery.ScalarQueryParameter("date", "STRING", date_str)
        ]
        self._execute_query(query, params)
