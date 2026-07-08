import sqlite3
import pandas as pd
import os
from datetime import datetime

class ProjectDB:
    def __init__(self):
        self.conn = None
        self.db_path = None

    def _ensure_connection(self):
        if self.conn is None:
            if self.db_path and os.path.exists(self.db_path):
                try:
                    self.conn = sqlite3.connect(self.db_path)
                    self._check_schema()
                except Exception as e:
                    print(f"Database reconnection failed: {e}")
            else:
                return False
        return True

    def _check_schema(self):
        try:
            cursor = self.conn.cursor()
            # Ensure upload_date exists
            cursor.execute("PRAGMA table_info(downhole)")
            cols = [info[1] for info in cursor.fetchall()]
            if 'upload_date' not in cols:
                cursor.execute("ALTER TABLE downhole ADD COLUMN upload_date TEXT")
            self.conn.commit()
        except:
            pass

    def create_new_project(self, project_name, folder_path):
        filename = f"{project_name.replace(' ', '_')}.db"
        self.db_path = os.path.join(folder_path, filename)
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS holes (
                id TEXT PRIMARY KEY,
                clean_id TEXT,
                n_base REAL, e_base REAL, z_base REAL,
                n_top REAL, e_top REAL, z_top REAL,
                has_top_survey INTEGER DEFAULT 0,
                design_az REAL DEFAULT 0,
                design_inc REAL DEFAULT 0,
                design_len REAL DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS downhole (
                hole_id TEXT,
                depth REAL,
                azimuth REAL,
                inclination REAL,
                survey_type TEXT,
                upload_date TEXT,
                FOREIGN KEY(hole_id) REFERENCES holes(id)
            )
        ''')
        self.conn.commit()
        return self.db_path

    def open_project(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self._check_schema()
        return True

    def import_baseline(self, df):
        if not self._ensure_connection(): return 0
        cursor = self.conn.cursor()
        count = 0
        has_az, has_inc, has_len = 'Azimuth' in df.columns, 'Inclination' in df.columns, 'Length' in df.columns

        for _, row in df.iterrows():
            d_az = row['Azimuth'] if has_az and pd.notna(row['Azimuth']) else 0.0
            d_inc = row['Inclination'] if has_inc and pd.notna(row['Inclination']) else 0.0
            d_len = row['Length'] if has_len and pd.notna(row['Length']) else 0.0

            cursor.execute('''
                INSERT OR REPLACE INTO holes 
                (id, clean_id, n_base, e_base, z_base, n_top, e_top, z_top, has_top_survey, design_az, design_inc, design_len)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            ''', (row['ID'], row['clean_ID'], row['North'], row['East'], row['Elev'], 
                  row['North'], row['East'], row['Elev'],
                  d_az, d_inc, d_len))
            count += 1
        self.conn.commit()
        return count

    def update_baseline_safely(self, df):
        if not self._ensure_connection(): return 0
        cursor = self.conn.cursor()
        count = 0
        has_az, has_inc, has_len = 'Azimuth' in df.columns, 'Inclination' in df.columns, 'Length' in df.columns

        for _, row in df.iterrows():
            d_az = row['Azimuth'] if has_az and pd.notna(row['Azimuth']) else 0.0
            d_inc = row['Inclination'] if has_inc and pd.notna(row['Inclination']) else 0.0
            d_len = row['Length'] if has_len and pd.notna(row['Length']) else 0.0
            
            cursor.execute("SELECT 1 FROM holes WHERE id=?", (row['ID'],))
            exists = cursor.fetchone()

            if exists:
                cursor.execute('''
                    UPDATE holes 
                    SET n_base=?, e_base=?, z_base=?, 
                        n_top=?, e_top=?, z_top=?, 
                        design_az=?, design_inc=?, design_len=?
                    WHERE id=?
                ''', (row['North'], row['East'], row['Elev'],
                      row['North'], row['East'], row['Elev'], 
                      d_az, d_inc, d_len, row['ID']))
            else:
                cursor.execute('''
                    INSERT INTO holes 
                    (id, clean_id, n_base, e_base, z_base, n_top, e_top, z_top, has_top_survey, design_az, design_inc, design_len)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ''', (row['ID'], row['clean_ID'], row['North'], row['East'], row['Elev'], 
                      row['North'], row['East'], row['Elev'],
                      d_az, d_inc, d_len))
            count += 1
        self.conn.commit()
        return count

    def update_top_survey(self, df):
        if not self._ensure_connection(): return 0
        cursor = self.conn.cursor()
        updated = 0
        for _, row in df.iterrows():
            cursor.execute('''
                UPDATE holes SET n_top=?, e_top=?, z_top=?, has_top_survey=1 WHERE clean_id=?
            ''', (row['North'], row['East'], row['Elev'], row['clean_ID']))
            if cursor.rowcount > 0: updated += 1
        self.conn.commit()
        return updated

    def import_downhole(self, df, survey_type, date_override=None):
        if not self._ensure_connection(): return 0
        cursor = self.conn.cursor()
        ids = df['clean_ID'].unique()
        final_date = date_override if date_override else datetime.now().strftime("%Y-%m-%d")
        
        # Clear existing data for this specific batch to prevent duplicates
        for hid in ids:
            cursor.execute("""
                DELETE FROM downhole 
                WHERE hole_id IN (SELECT id FROM holes WHERE clean_id=?) 
                AND survey_type=? AND upload_date=?
            """, (hid, survey_type, final_date))
            
        inserted = 0
        for _, row in df.iterrows():
            cursor.execute("SELECT id FROM holes WHERE clean_id=?", (row['clean_ID'],))
            res = cursor.fetchone()
            if res:
                real_id = res[0]
                cursor.execute('''
                    INSERT INTO downhole (hole_id, depth, azimuth, inclination, survey_type, upload_date)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (real_id, row['Length'], row['Azimuth'], row['Inclination'], survey_type, final_date))
                inserted += 1
        self.conn.commit()
        return inserted

    def get_all_data(self):
        if not self._ensure_connection(): return pd.DataFrame(), pd.DataFrame()
        try:
            holes = pd.read_sql("SELECT * FROM holes", self.conn)
            surveys = pd.read_sql("SELECT * FROM downhole", self.conn)
            return holes, surveys
        except: return pd.DataFrame(), pd.DataFrame()

    def get_surveyed_ids(self):
        if not self._ensure_connection(): return []
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT h.clean_id FROM holes h JOIN downhole d ON h.id = d.hole_id ORDER BY h.clean_id")
        return [row[0] for row in cursor.fetchall()]

    def get_available_dates(self):
        if not self._ensure_connection(): return []
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT upload_date FROM downhole WHERE upload_date IS NOT NULL ORDER BY upload_date DESC")
        return [row[0] for row in cursor.fetchall()]

    def get_holes_by_date(self, date_str):
        if not self._ensure_connection(): return []
        query = """
            SELECT DISTINCT h.clean_id 
            FROM holes h 
            JOIN downhole d ON h.id = d.hole_id 
            WHERE d.upload_date = ?
            ORDER BY h.clean_id
        """
        cursor = self.conn.cursor()
        cursor.execute(query, (date_str,))
        return [row[0] for row in cursor.fetchall()]

    # --- NEW MANAGEMENT METHODS ---
    def get_hole_survey_details(self, clean_id):
        """Returns list of unique surveys [date, type, count] for a hole."""
        if not self._ensure_connection(): return []
        cursor = self.conn.cursor()
        query = """
            SELECT d.upload_date, d.survey_type, COUNT(*) 
            FROM downhole d
            JOIN holes h ON d.hole_id = h.id
            WHERE h.clean_id = ?
            GROUP BY d.upload_date, d.survey_type
            ORDER BY d.upload_date DESC
        """
        cursor.execute(query, (clean_id,))
        return [{'date': r[0], 'type': r[1], 'pts': r[2]} for r in cursor.fetchall()]

    def delete_survey_entry(self, clean_id, date, survey_type):
        """Deletes a specific survey run for a hole."""
        if not self._ensure_connection(): return
        cursor = self.conn.cursor()
        cursor.execute("""
            DELETE FROM downhole 
            WHERE hole_id IN (SELECT id FROM holes WHERE clean_id=?) 
            AND upload_date=? AND survey_type=?
        """, (clean_id, date, survey_type))
        self.conn.commit()

    def delete_batch_by_date(self, date_str):
        """Deletes ALL surveys uploaded on a specific date."""
        if not self._ensure_connection(): return 0
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM downhole WHERE upload_date=?", (date_str,))
        self.conn.commit()
        return cursor.rowcount