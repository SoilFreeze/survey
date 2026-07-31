import pandas as pd
import numpy as np
import re
from scipy.spatial import KDTree, cKDTree

class SurveyMath:
    def __init__(self):
        pass

    def standardize_id(self, raw_id):
        s = str(raw_id).strip().upper()
        prefix = "T" if s.startswith("T") else ""
        match = re.search(r'(\d+)', s)
        if not match: return s
        number = match.group(1)
        suffix = ""
        parts = s.split(number)
        if len(parts) > 1 and 'A' in parts[-1]: suffix = "a"
        return f"{prefix}{number}{suffix}"

    def normalize_columns(self, df, col_map):
        new_cols = {}
        for col in df.columns:
            c = col.lower().strip()
            if c in [x.lower() for x in col_map['id']]: new_cols[col] = 'ID'
            elif c in [x.lower() for x in col_map['n']]: new_cols[col] = 'North'
            elif c in [x.lower() for x in col_map['e']]: new_cols[col] = 'East'
            elif c in [x.lower() for x in col_map['z']]: new_cols[col] = 'Elev'
            elif c in [x.lower() for x in col_map['az']]: new_cols[col] = 'Azimuth'
            elif c in [x.lower() for x in col_map['inc']]: new_cols[col] = 'Inclination'
            elif c in [x.lower() for x in col_map['depth']]: new_cols[col] = 'Length'
        df = df.rename(columns=new_cols)
        if 'Elev' not in df.columns: df['Elev'] = 0.0
        return df

    def calculate_tangential(self, start_n, start_e, start_z, depths, azis, incs, status, hole_id, clean_id, d_az=0.0, d_inc=0.0):
        intervals = np.diff(depths, prepend=0)
        vert_drops = intervals * np.cos(incs)
        horiz_moves = intervals * np.sin(incs)
        delta_n = horiz_moves * np.cos(azis)
        delta_e = horiz_moves * np.sin(azis)
        
        path_n = start_n + np.cumsum(delta_n)
        path_e = start_e + np.cumsum(delta_e)
        path_z = start_z - np.cumsum(vert_drops)
        
        dev_n = path_n - start_n
        dev_e = path_e - start_e
        
        path_df = pd.DataFrame({
            'id': hole_id, 'clean_id': clean_id,
            'n_top': start_n, 'e_top': start_e, 'z_top': start_z,
            'north': path_n, 'east': path_e, 'elev': path_z, 'depth': depths,
            'dev_north': dev_n, 'dev_east': dev_e,
            'status': status,
            'design_az': d_az, 'design_inc': d_inc
        })
        top_row = pd.DataFrame([{
            'id': hole_id, 'clean_id': clean_id,
            'n_top': start_n, 'e_top': start_e, 'z_top': start_z,
            'north': start_n, 'east': start_e, 'elev': start_z, 'depth': 0.0,
            'dev_north': 0.0, 'dev_east': 0.0,
            'status': status,
            'design_az': d_az, 'design_inc': d_inc
        }])
        return pd.concat([top_row, path_df], ignore_index=True)

    # --- SIMPLIFIED: Returns ALL versions for plotting history ---
    def calculate_single_hole_all_versions(self, hole_row, hole_surveys):
        paths = []
        hole_id = hole_row['id']
        clean_id = hole_row['clean_id']
        sn, se, sz = hole_row['n_top'], hole_row['e_top'], hole_row['z_top']

        d_az = hole_row.get('design_az', 0.0)
        d_inc = hole_row.get('design_inc', 0.0)
        d_len = hole_row.get('design_len', 200.0) or 200.0
        
        # Baseline
        b_depths = np.arange(10, d_len + 10, 10)
        b_azis = np.full(len(b_depths), np.radians(d_az))
        b_incs = np.full(len(b_depths), np.radians(d_inc))
        paths.append(self.calculate_tangential(sn, se, sz, b_depths, b_azis, b_incs, 'Baseline', hole_id, clean_id, d_az, d_inc))

        if not hole_surveys.empty:
            casing = hole_surveys[hole_surveys['survey_type'] == 'Casing']
            if not casing.empty:
                casing = casing.copy()
                casing['upload_date'] = casing['upload_date'].fillna('Unknown')
                dates = casing['upload_date'].unique()
                for d in dates:
                    sub = casing[casing['upload_date'] == d].sort_values('depth')
                    label = f"Casing ({d})" if d != 'Unknown' else "Casing"
                    paths.append(self.calculate_tangential(sn, se, sz, sub['depth'].values, np.radians(sub['azimuth'].values), np.radians(sub['inclination'].values), label, hole_id, clean_id, d_az, d_inc))

            pipe = hole_surveys[hole_surveys['survey_type'] == 'Pipe']
            if not pipe.empty:
                pipe = pipe.copy()
                pipe['upload_date'] = pipe['upload_date'].fillna('Unknown')
                dates = pipe['upload_date'].unique()
                for d in dates:
                    sub = pipe[pipe['upload_date'] == d].sort_values('depth')
                    label = f"Pipe ({d})" if d != 'Unknown' else "Pipe"
                    paths.append(self.calculate_tangential(sn, se, sz, sub['depth'].values, np.radians(sub['azimuth'].values), np.radians(sub['inclination'].values), label, hole_id, clean_id, d_az, d_inc))

        return pd.concat(paths, ignore_index=True)

    # --- SIMPLIFIED: Uses LATEST Pipe Survey for Calculations ---
    def calculate_trajectory(self, holes_df, surveys_df, origin_north=0.0, origin_east=0.0):
        full_paths = []
        for _, hole_row in holes_df.iterrows():
            hole_id = hole_row['id']
            status = 'Baseline'
            d_az = hole_row.get('design_az', 0.0)
            d_inc = hole_row.get('design_inc', 0.0)
            
            # Filter surveys for this specific hole
            hole_surveys = pd.DataFrame()
            if not surveys_df.empty:
                hole_surveys = surveys_df[surveys_df['hole_id'] == hole_id]
                
            active_survey = pd.DataFrame()

            if not hole_surveys.empty:
                pipe = hole_surveys[hole_surveys['survey_type'] == 'Pipe']
                casing = hole_surveys[hole_surveys['survey_type'] == 'Casing']
                
                # Priority: Pipe (Latest) -> Casing (Latest) -> Baseline
                if not pipe.empty:
                    pipe = pipe.copy()
                    pipe['upload_date'] = pipe['upload_date'].fillna('Unknown')
                    dates = sorted(pipe['upload_date'].unique())
                    active_survey = pipe[pipe['upload_date'] == dates[-1]]
                    status = 'Pipe'
                elif not casing.empty:
                    casing = casing.copy()
                    casing['upload_date'] = casing['upload_date'].fillna('Unknown')
                    dates = sorted(casing['upload_date'].unique())
                    active_survey = casing[casing['upload_date'] == dates[-1]]
                    status = 'Casing'
            
            if not active_survey.empty:
                group = active_survey.sort_values('depth')
                path = self.calculate_tangential(
                    hole_row['n_top'], hole_row['e_top'], hole_row['z_top'],
                    group['depth'].values, np.radians(group['azimuth'].values), np.radians(group['inclination'].values),
                    status, hole_id, hole_row['clean_id'], d_az, d_inc
                )
                full_paths.append(path)
            else:
                d_len = hole_row.get('design_len', 200.0) or 200.0
                path = self.calculate_tangential(
                    hole_row['n_top'], hole_row['e_top'], hole_row['z_top'],
                    np.array([d_len]), np.array([np.radians(d_az)]), np.array([np.radians(d_inc)]),
                    'Baseline', hole_id, hole_row['clean_id'], d_az, d_inc
                )
                full_paths.append(path)
                
        if not full_paths: return pd.DataFrame()
        traj_df = pd.concat(full_paths, ignore_index=True)
        
        # Apply origin shifts using the passed parameters
        traj_df['north'] = traj_df['north'] - origin_north
        traj_df['east'] = traj_df['east'] - origin_east
        
        return traj_df

    def get_slice_at_elevation(self, trajectory_df, target_z):
        results = []
        grouped = trajectory_df.groupby('id')
        for hole_id, group in grouped:
            group = group.sort_values('elev', ascending=False)
            zs, ns, es = group['elev'].values, group['north'].values, group['east'].values
            
            top_z = group['elev'].max()
            btm_z = group['elev'].min()
            
            if target_z <= (top_z + 0.1) and target_z >= (btm_z - 0.1):
                n_new = np.interp(target_z, zs[::-1], ns[::-1]) 
                e_new = np.interp(target_z, zs[::-1], es[::-1])
                
                d_az_deg = group['design_az'].iloc[0]
                d_inc_deg = group['design_inc'].iloc[0]
                n_start = group['n_top'].iloc[0]
                e_start = group['e_top'].iloc[0]
                z_start = group['z_top'].iloc[0]
                
                vert_drop = z_start - target_z
                rad_inc = np.radians(d_inc_deg)
                rad_az = np.radians(d_az_deg)
                h_dist = vert_drop * np.tan(rad_inc)
                
                des_n = n_start + (h_dist * np.cos(rad_az))
                des_e = e_start + (h_dist * np.sin(rad_az))
                
                delta_n = n_new - des_n
                delta_e = e_new - des_e
                dev = np.sqrt(delta_n**2 + delta_e**2)
                
                perc_dev = 0.0
                if vert_drop > 0:
                    perc_dev = (dev / vert_drop) * 100

                results.append({
                    'ID': group.iloc[0]['clean_id'], 
                    'Target_Elev': target_z,
                    'East_New': e_new, 'North_New': n_new,
                    'East_Top': e_start, 'North_Top': n_start,
                    'Survey_Status': group['status'].iloc[0],
                    'Deviation': dev,
                    'Deviation_Percent': perc_dev
                })
        return pd.DataFrame(results)

    def get_multi_level_vectors(self, holes_df, surveys_df, target_elevations):
        all_traj = self.calculate_trajectory(holes_df, surveys_df)
        if all_traj.empty: return pd.DataFrame()
        
        vectors = []
        
        grouped = all_traj.groupby('id')
        for hole_id, group in grouped:
            group = group.sort_values('elev', ascending=False)
            zs = group['elev'].values
            ns = group['north'].values
            es = group['east'].values
            
            top_z = group['elev'].max()
            btm_z = group['elev'].min()
            
            d_az_deg = group['design_az'].iloc[0]
            d_inc_deg = group['design_inc'].iloc[0]
            n_start = group['n_top'].iloc[0]
            e_start = group['e_top'].iloc[0]
            z_start = group['z_top'].iloc[0]
            
            status_val = group['status'].iloc[0]
            rad_inc = np.radians(d_inc_deg)
            rad_az = np.radians(d_az_deg)

            for target_z in target_elevations:
                if target_z <= (top_z + 0.5) and target_z >= (btm_z - 0.5):
                    n_act = np.interp(target_z, zs[::-1], ns[::-1]) 
                    e_act = np.interp(target_z, zs[::-1], es[::-1])
                    vert_drop = z_start - target_z
                    h_dist = vert_drop * np.tan(rad_inc)
                    n_des = n_start + (h_dist * np.cos(rad_az))
                    e_des = e_start + (h_dist * np.sin(rad_az))
                    
                    vectors.append({
                        'ID': group.iloc[0]['clean_id'],
                        'Elevation': target_z,
                        'Start_N': n_des, 'Start_E': e_des, 
                        'End_N': n_act,   'End_E': e_act,   
                        'Collar_N': n_start, 'Collar_E': e_start,
                        'Dev_N': n_act - n_des,
                        'Dev_E': e_act - e_des,
                        'Status': status_val
                    })
                    
        return pd.DataFrame(vectors)

    def get_top_deviation_vectors(self, holes_df):
        surveyed_holes = holes_df[holes_df['has_top_survey'] == 1].copy()
        vectors = []
        for _, row in surveyed_holes.iterrows():
            dn, de = row['n_base'], row['e_base']
            an, ae = row['n_top'], row['e_top']
            dev_n = an - dn
            dev_e = ae - de
            total_dev = np.sqrt(dev_n**2 + dev_e**2)
            vectors.append({
                'ID': row['clean_id'],
                'Design_N': dn, 'Design_E': de,
                'Actual_N': an, 'Actual_E': ae,
                'Dev_North (ft)': round(dev_n, 3),
                'Dev_East (ft)': round(dev_e, 3),
                'Total_Deviation (ft)': round(total_dev, 3)
            })
        return pd.DataFrame(vectors)

    def get_nearby_pipes_data(self, target_clean_id, holes_df, surveys_df, search_radius=10.0):
        all_traj = self.calculate_trajectory(holes_df, surveys_df)
        if all_traj.empty: return {}
        target_traj = all_traj[all_traj['clean_id'] == str(target_clean_id)].copy()
        if target_traj.empty: return {}
        t_start_n = float(target_traj.iloc[0]['n_top'])
        t_start_e = float(target_traj.iloc[0]['e_top'])
        target_points = target_traj[['north', 'east', 'elev']].values
        target_tree = cKDTree(target_points)
        nearby_pipes = {}
        other_groups = all_traj[all_traj['clean_id'] != str(target_clean_id)].groupby('clean_id')
        for nid, group in other_groups:
            neighbor_points = group[['north', 'east', 'elev']].values
            dists, _ = target_tree.query(neighbor_points, k=1)
            min_dist = np.min(dists)
            if min_dist <= search_radius:
                group = group.copy()
                group['rel_north'] = group['north'] - t_start_n
                group['rel_east'] = group['east'] - t_start_e
                nearby_pipes[nid] = {'df': group, 'dist': min_dist}
        return nearby_pipes

    def _calc_deviation_for_survey(self, row, survey_data, status_label):
        survey_path = self.calculate_tangential(
            row['n_top'], row['e_top'], row['z_top'], 
            survey_data['depth'].values, 
            np.radians(survey_data['azimuth'].values), 
            np.radians(survey_data['inclination'].values), 
            status_label, row['id'], row['clean_id'],
            row.get('design_az', 0.0), row.get('design_inc', 0.0)
        )
        btm = survey_path.iloc[-1]
        final_depth = btm['depth']
        
        rad_inc = np.radians(row.get('design_inc', 0.0))
        rad_az = np.radians(row.get('design_az', 0.0))
        
        h_dist = final_depth * np.sin(rad_inc)
        v_dist = final_depth * np.cos(rad_inc)
        
        base_n = row['n_base'] + (h_dist * np.cos(rad_az))
        base_e = row['e_base'] + (h_dist * np.sin(rad_az))
        base_z = row['z_base'] - v_dist
        
        delta_n = btm['north'] - base_n
        delta_e = btm['east'] - base_e
        delta_z = btm['elev'] - base_z
        total_dev = np.sqrt(delta_n**2 + delta_e**2)
        
        return total_dev, final_depth, delta_n, delta_e, delta_z

    def calculate_deviation_stats(self, row, hole_surveys, force_date=None):
        if hole_surveys.empty: return None

        target_survey = pd.DataFrame()
        
        if force_date:
            pipe = hole_surveys[(hole_surveys['survey_type'] == 'Pipe') & (hole_surveys['upload_date'] == force_date)]
            if not pipe.empty: target_survey = pipe.sort_values('depth')
            else: return None
        else:
            # Default to Latest Pipe
            pipe = hole_surveys[hole_surveys['survey_type'] == 'Pipe']
            if not pipe.empty:
                pipe = pipe.copy()
                pipe['upload_date'] = pipe['upload_date'].fillna('Unknown')
                dates = sorted(pipe['upload_date'].unique())
                target_survey = pipe[pipe['upload_date'] == dates[-1]].sort_values('depth')
            else:
                casing = hole_surveys[hole_surveys['survey_type'] == 'Casing']
                if not casing.empty:
                    target_survey = casing.sort_values('depth')
        
        if target_survey.empty: return None

        curr_dev, depth, dn, de, dz = self._calc_deviation_for_survey(row, target_survey, 'Latest')
        upload_date = target_survey.iloc[0]['upload_date'] if 'upload_date' in target_survey.columns else "Unknown"
        
        percent_dev = 0.0
        if depth > 0:
            percent_dev = (curr_dev / depth) * 100

        return {
            "ID": row['clean_id'],
            "Upload_Date": upload_date,
            "Top_Surveyed": "Yes" if row['has_top_survey'] else "No",
            "Bottom_Depth (ft)": round(depth, 2),
            "Dev_North (ft)": round(dn, 2),
            "Dev_East (ft)": round(de, 2),
            "Total_Deviation (ft)": round(curr_dev, 2),
            "Percent Dev": round(percent_dev, 2)
        }
