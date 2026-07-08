import sqlite3
import pandas as pd
from google.cloud import bigquery

# 1. Setup local connection
sqlite_path = '2329a.db' # Replace with your file
conn = sqlite3.connect(sqlite_path)

# 2. Setup BigQuery client (ensure you have your JSON key authenticated)
client = bigquery.Client()
dataset_id = "your_project_id.your_dataset_name"

# 3. Migrate 'holes' table
holes_df = pd.read_sql("SELECT * FROM holes", conn)
holes_df['project_name'] = '2329a' # Assign a project name
job = client.load_table_from_dataframe(holes_df, f"{dataset_id}.holes")
job.result()
print("Holes table migrated.")

# 4. Migrate 'downhole' table
surveys_df = pd.read_sql("SELECT * FROM downhole", conn)
surveys_df['project_name'] = '2329a' # Must match the project name used above
job = client.load_table_from_dataframe(surveys_df, f"{dataset_id}.downhole")
job.result()
print("Downhole table migrated.")
