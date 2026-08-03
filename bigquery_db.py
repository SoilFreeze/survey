import pandas as pd
from google.cloud import bigquery

class BigQueryDB:
    def __init__(self, project_id):
        # Initialize the BigQuery client
        self.client = bigquery.Client()
        self.dataset = "sensorpush-export.survey"
        
        # We need to know which project we are looking at (e.g., "2329a")
        self.active_project_id = project_id 

    def get_all_data(self):
        """
        Fetches holes and surveys for the active project and aliases the 
        BigQuery columns to match the legacy math_engine expectations.
        """
        
        # Map BigQuery 'holes' schema to math_engine columns
        holes_query = f"""
            SELECT 
                hole_id AS id,
                hole_id AS clean_id,
                design_n AS n_base,
                design_e AS e_base,
                design_z AS z_base,
                actual_n AS n_top,
                actual_e AS e_top,
                actual_z AS z_top,
                CASE WHEN actual_n IS NOT NULL THEN 1 ELSE 0 END AS has_top_survey,
                design_az,
                design_inc,
                design_length AS design_len
            FROM `{self.dataset}.holes`
            WHERE project_id = @project_id
        """
        
        # Map BigQuery 'surveys' schema to math_engine columns
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
        
        # Safely parameterize the query to prevent SQL injection
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("project_id", "STRING", self.active_project_id)
            ]
        )
        
        try:
            holes_df = self.client.query(holes_query, job_config=job_config).to_dataframe()
            surveys_df = self.client.query(surveys_query, job_config=job_config).to_dataframe()
            return holes_df, surveys_df
            
        except Exception as e:
            print(f"BigQuery fetch failed: {e}")
            return pd.DataFrame(), pd.DataFrame()

    def import_downhole(self, df, survey_type, upload_date):
        """
        Example of how we will push data back to BigQuery using 
        the load_table_from_dataframe method.
        """
        # Ensure the dataframe matches the BigQuery 'surveys' schema exactly
        df['project_id'] = self.active_project_id
        df['survey_type'] = survey_type
        df['upload_date'] = upload_date
        
        # We must align the DataFrame columns with BigQuery before uploading
        bq_df = df[['hole_id', 'project_id', 'length', 'azimuth', 'inclination', 'survey_type', 'upload_date']]
        
        try:
            job = self.client.load_table_from_dataframe(bq_df, f"{self.dataset}.surveys")
            job.result() # Wait for the job to complete
            return len(bq_df)
        except Exception as e:
            print(f"Upload failed: {e}")
            return 0
