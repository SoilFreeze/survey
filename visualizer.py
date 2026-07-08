import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import matplotlib.colors as mcolors
import pandas as pd
import numpy as np
from scipy.spatial import KDTree, distance_matrix
import configparser
from matplotlib.colors import ListedColormap, BoundaryNorm, LinearSegmentedColormap
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

class SurveyVisualizer:
    def __init__(self):
        # Define colors directly here
        self.colors = {
            'step_1': '#006400', 'step_2': '#008000', 'step_3': '#228B22',
            'step_4': '#32CD32', 'step_5': '#90EE90', 'step_6': '#FFFFE0',
            'step_7': '#FFFF00', 'step_8': '#FFD700', 'step_9': '#FFA500',
            'step_10': '#FF8C00', 'step_11': '#FF4500', 'step_12': '#FF0000',
            'step_13': '#800080'
        }

    def read_config(self, config_file):
        import sys, os
        if getattr(sys, 'frozen', False):
            app_path = os.path.dirname(sys.executable)
        else:
            app_path = os.path.dirname(os.path.abspath(__file__))
            
        config_path = os.path.join(app_path, config_file)
        read_files = self.config.read(config_path)
        
        # Check if the file was successfully read
        if read_files and 'HeatMapColors' in self.config:
            self.colors = self.config['HeatMapColors']
        else:
            # Fallback dictionary if config.ini is missing
            self.colors = {
                'step_1': '#006400', 'step_2': '#008000', 'step_3': '#228B22',
                'step_4': '#32CD32', 'step_5': '#90EE90', 'step_6': '#FFFFE0',
                'step_7': '#FFFF00', 'step_8': '#FFD700', 'step_9': '#FFA500',
                'step_10': '#FF8C00', 'step_11': '#FF4500', 'step_12': '#FF0000',
                'step_13': '#800080'
            }

    def fix_color(self, color_name):
        c = str(color_name).split(';')[0].strip()
        if c.startswith('#'): return c
        mapping = {
            'lightorange': '#FFCC80', 'lightgreen': '#90EE90', 'pink': '#FFC0CB',
            'orange': '#FFA500', 'yellow': '#FFFF00', 'green': '#008000',
            'red': '#FF0000', 'darkgreen': '#006400', 'lightyellow': '#FFFFE0',
            'purple': '#800080', 'gold': '#FFCC00', 'darkred': '#8B0000'
        }
        return mapping.get(c.lower(), c)

    def get_11_step_cmap(self):
        colors = []
        for i in range(1, 14):
            key = f'step_{i}'
            # Use .get() to access the dictionary safely
            # If self.colors is a configparser object, this works; 
            # if it's a dict, this also works.
            val = self.colors.get(key, '#808080')
            
            # If val is a SectionProxy (from configparser), convert to string
            if hasattr(val, '__getitem__'): 
                val = str(val)
                
            colors.append(self.fix_color(val))
        return ListedColormap(colors)

    def _get_percent_cmap(self):
        cdict = {
            'red':   [(0.0, 0.0, 0.0), (0.5, 1.0, 1.0), (1.0, 1.0, 1.0)],
            'green': [(0.0, 0.5, 0.5), (0.5, 1.0, 1.0), (1.0, 0.65, 0.65)],
            'blue':  [(0.0, 0.0, 0.0), (0.5, 0.0, 0.0), (1.0, 0.0, 0.0)]
        }
        cmap = LinearSegmentedColormap('PercentScale', cdict)
        cmap.set_over('#FF0000') 
        norm = mcolors.Normalize(vmin=0, vmax=3.0)
        return cmap, norm

    def _get_coords(self, df):
        if 'North_Shifted' in df.columns:
            return df['North_Shifted'].values, df['East_Shifted'].values
        elif 'North_New' in df.columns:
            return df['North_New'].values, df['East_New'].values
        return df['North'].values, df['East'].values

    def _get_norm_cmap(self):
        levels = [0, 1.5, 2.5, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 9999]
        cmap = self.get_11_step_cmap()
        norm = BoundaryNorm(levels, ncolors=cmap.N, clip=True)
        return cmap, norm, levels

    def _plot_markers(self, ax, df_at_depth, cmap, norm, marker_size, color_col='min_dist'):
        legend_elements = []
        pipe = df_at_depth[df_at_depth['Survey_Status'] == 'Pipe']
        if not pipe.empty:
            n, e = self._get_coords(pipe)
            c_vals = pipe[color_col] if color_col in pipe.columns else pipe['min_dist']
            ax.scatter(e, n, c=c_vals, cmap=cmap, norm=norm, s=marker_size, marker='o', edgecolors='black', linewidth=1.0, label='Pipe', zorder=10)
            legend_elements.append(Line2D([0], [0], marker='o', color='w', markerfacecolor='black', markersize=8, label='Pipe'))
        casing = df_at_depth[df_at_depth['Survey_Status'] == 'Casing']
        if not casing.empty:
            n, e = self._get_coords(casing)
            c_vals = casing[color_col] if color_col in casing.columns else casing['min_dist']
            ax.scatter(e, n, c=c_vals, cmap=cmap, norm=norm, s=marker_size, marker='s', edgecolors='blue', linewidth=1.0, label='Casing', zorder=10)
            legend_elements.append(Line2D([0], [0], marker='s', color='w', markerfacecolor='blue', markersize=8, label='Casing'))
        return legend_elements

    def _add_labels(self, ax, df, n_coords, e_coords):
        df = df.reset_index(drop=True)
        for i, txt in enumerate(df['ID']):
            ax.annotate(str(txt), (e_coords[i], n_coords[i]), fontsize=8, xytext=(3, 3), textcoords='offset points', color='black', weight='normal', zorder=12)

    def plot_hole_comparison(self, comparison_df, hole_id, neighbors_dict=None, show_neighbor=False, hide_baseline=False, show_casing=True, plan_view_only=False):
        if comparison_df.empty: return
        self._create_comparison_plot(comparison_df, hole_id, neighbors_dict, show_neighbor, hide_baseline, show_casing, plan_view_only, save_path=None)

    def save_static_graph_to_file(self, comparison_df, hole_id, filepath, show_casing=True):
        if comparison_df.empty: return
        self._create_comparison_plot(comparison_df, hole_id, neighbors_dict=None, show_neighbor=False, hide_baseline=False, show_casing=show_casing, plan_view_only=False, save_path=filepath)

    def _create_comparison_plot(self, comparison_df, hole_id, neighbors_dict, show_neighbor, hide_baseline, show_casing, plan_view_only, save_path):
        max_n = comparison_df['dev_north'].abs().max(); max_e = comparison_df['dev_east'].abs().max()
        if show_neighbor and neighbors_dict:
            for nid, data in neighbors_dict.items():
                ndf = data['df']
                max_n = max(max_n, ndf['rel_north'].abs().max())
                max_e = max(max_e, ndf['rel_east'].abs().max())
        max_dev = max(max_n, max_e); limit = 2.0
        if max_dev > 2.0: limit = 2.0 + (np.ceil((max_dev - 2.0) / 0.25) * 0.25)
        dev_interval = 0.5; 
        if limit > 3.0: dev_interval = 1.0
        if limit > 8.0: dev_interval = 2.0
        max_depth = comparison_df['depth'].max(); depth_interval = 20.0
        
        if plan_view_only: fig, ax_plan = plt.subplots(figsize=(10, 10)); axs = [ax_plan]
        else: fig, axs = plt.subplots(1, 3, figsize=(18, 6))
        fig.suptitle(f"Comparison: Hole {hole_id}", fontsize=16)
        
        def format_ax(ax, title, xlabel, ylabel, invert_y=False):
            ax.set_title(title, fontsize=12, pad=10); ax.set_xlabel(xlabel); ax.set_ylabel(ylabel)
            ax.grid(True, which='major', color='#D9D9D9', linestyle='-'); ax.xaxis.set_major_locator(ticker.MultipleLocator(dev_interval))
            if invert_y: ax.invert_yaxis(); ax.set_xlim(-limit, limit); ax.yaxis.set_major_locator(ticker.MultipleLocator(depth_interval))
            else: ax.set_xlim(-limit, limit); ax.set_ylim(-limit, limit); ax.yaxis.set_major_locator(ticker.MultipleLocator(dev_interval))
            ax.axhline(0, color='black', linewidth=1.5, zorder=2); ax.axvline(0, color='black', linewidth=1.5, zorder=2)

        if show_neighbor and neighbors_dict:
            colors = plt.cm.viridis(np.linspace(0, 0.8, len(neighbors_dict)))
            for idx, (nid, data) in enumerate(neighbors_dict.items()):
                ndf = data['df']; c = colors[idx]
                axs[0].plot(ndf['rel_east'], ndf['rel_north'], color=c, lw=1.5, alpha=0.7)
                axs[0].annotate(str(nid), (ndf['rel_east'].iloc[0], ndf['rel_north'].iloc[0]), color=c, fontsize=9, fontweight='bold')
                if not plan_view_only:
                    axs[1].plot(ndf['rel_east'], ndf['depth'], color=c, lw=1.5, alpha=0.7)
                    axs[2].plot(ndf['rel_north'], ndf['depth'], color=c, lw=1.5, alpha=0.7)

        axs[0].annotate(str(hole_id), (0, 0), color='black', fontsize=10, fontweight='bold', bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="black"))
        
        all_statuses = sorted(comparison_df['status'].unique())
        pipe_statuses = [s for s in all_statuses if str(s).startswith('Pipe')]
        try: pipe_statuses.sort(key=lambda x: x.split('(')[1] if '(' in x else x)
        except: pipe_statuses.sort()
        pipe_colors = ['#90EE90', '#8B0000', '#FFA500', '#800080', '#0000FF']
        pipe_map = {status: pipe_colors[i % len(pipe_colors)] for i, status in enumerate(pipe_statuses)}

        for status in all_statuses:
            if status == 'Baseline':
                if hide_baseline: continue
                c = 'grey'; ls = '--'; marker = None; lw = 1.5; z = 10
            elif str(status).startswith('Casing'):
                if not show_casing: continue
                c = 'blue'; ls = '-.'; marker = 's'; lw = 2.5; z = 25
            else:
                c = pipe_map.get(status, 'black'); ls = '-'; marker = '.'; lw = 1.5; z = 15
            
            data = comparison_df[comparison_df['status'] == status]
            if data.empty: continue
            axs[0].plot(data['dev_east'], data['dev_north'], color=c, linestyle=ls, marker=marker, markersize=4, linewidth=lw, label=status, zorder=z)
            if not plan_view_only:
                axs[1].plot(data['dev_east'], data['depth'], color=c, linestyle=ls, marker=marker, markersize=4, linewidth=lw, zorder=z)
                axs[2].plot(data['dev_north'], data['depth'], color=c, linestyle=ls, marker=marker, markersize=4, linewidth=lw, zorder=z)

        format_ax(axs[0], "Plan View", "Dev East", "Dev North"); axs[0].set_aspect('equal')
        if not plan_view_only:
            format_ax(axs[1], "East Dev", "Dev (ft)", "Depth", True)
            format_ax(axs[2], "North Dev", "Dev (ft)", "Depth", True)
            axs[2].legend(loc='center right', bbox_to_anchor=(1.4, 0.5))
        else: axs[0].legend(loc='upper right')
        
        plt.tight_layout()
        if save_path: plt.savefig(save_path); plt.close(fig)
        else: plt.show()

    def plot_batch_date_comparison(self, batch_df, date_str):
        if batch_df.empty: return
        max_dev = max(batch_df['dev_north'].abs().max(), batch_df['dev_east'].abs().max())
        limit = 2.0 if max_dev < 2 else 2.0 + (np.ceil((max_dev - 2.0) / 0.25) * 0.25)
        fig, axs = plt.subplots(1, 3, figsize=(18, 6))
        fig.suptitle(f"Batch Analysis: {date_str}", fontsize=16)
        holes = batch_df['id'].unique()
        colors = plt.cm.jet(np.linspace(0, 1, len(holes)))
        for idx, hid in enumerate(holes):
            sub = batch_df[batch_df['id'] == hid]
            c = colors[idx]
            axs[0].plot(sub['dev_east'], sub['dev_north'], color=c, lw=1.5, alpha=0.8, label=str(hid))
            axs[1].plot(sub['dev_east'], sub['depth'], color=c, lw=1.5, alpha=0.8)
            axs[2].plot(sub['dev_north'], sub['depth'], color=c, lw=1.5, alpha=0.8)
        
        for ax in axs:
            ax.grid(True); ax.axhline(0, c='k'); ax.axvline(0, c='k')
            if ax != axs[0]: ax.invert_yaxis()
        
        axs[0].set_title("Plan View"); axs[0].set_aspect('equal'); axs[0].set_xlim(-limit, limit); axs[0].set_ylim(-limit, limit)
        axs[1].set_title("East Dev"); axs[1].set_xlim(-limit, limit)
        axs[2].set_title("North Dev"); axs[2].set_xlim(-limit, limit)
        if len(holes) <= 20: axs[0].legend(loc='upper right')
        plt.tight_layout(); plt.show()

    def generate_grid_heatmap(self, df_at_depth, depth_label, grid_res=1.0, filter_surveyed=False, show_labels=False):
        if filter_surveyed: df_at_depth = df_at_depth[df_at_depth['Survey_Status'].isin(['Casing', 'Pipe'])]
        if df_at_depth.empty: return
        n, e = self._get_coords(df_at_depth)
        coords = np.column_stack((n, e))
        if len(coords) >= 2:
            dist_mat = distance_matrix(coords, coords)
            np.fill_diagonal(dist_mat, np.inf)
            df_at_depth['min_dist'] = dist_mat.min(axis=1)
        
        cmap, norm, levels = self._get_norm_cmap()
        fig, ax = plt.subplots(figsize=(12, 10))
        self._plot_markers(ax, df_at_depth, cmap, norm, 10, 'min_dist')
        if show_labels: self._add_labels(ax, df_at_depth, n, e)
        ax.set_title(f"Grid Map {depth_label}"); ax.axis('equal'); plt.show()

    def generate_pipe_heatmap(self, df_at_depth, depth_label, filter_surveyed=False, show_labels=False, mode="Deviation (ft)"):
        if filter_surveyed: df_at_depth = df_at_depth[df_at_depth['Survey_Status'].isin(['Casing', 'Pipe'])]
        if df_at_depth.empty: return
        n, e = self._get_coords(df_at_depth)
        
        if mode == "Deviation (%)":
            col = 'Deviation_Percent'; label = "Dev %"; cmap, norm = self._get_percent_cmap()
        else:
            col = 'Deviation' if 'Deviation' in df_at_depth.columns else 'min_dist'
            label = "Dev (ft)"; cmap, norm, levels = self._get_norm_cmap()

        fig, ax = plt.subplots(figsize=(12, 10))
        self._plot_markers(ax, df_at_depth, cmap, norm, 33, col)
        if show_labels: self._add_labels(ax, df_at_depth, n, e)
        
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm); sm.set_array([])
        cbar = fig.colorbar(sm, ax=ax, label=label, extend='max')
        ax.set_title(f"Pipe Map {depth_label} [{mode}]"); ax.axis('equal'); plt.show()

    def plot_deviation_needles(self, vectors_df, surveyed_only=False):
        if vectors_df.empty: return
        if surveyed_only:
            vectors_df = vectors_df[vectors_df['Status'] != 'Baseline']
            if vectors_df.empty: return

        colors = {268: 'red', 256: '#FFA500', 244: 'yellow', 232: 'green', 220: 'blue'}
        fig, ax = plt.subplots(figsize=(12, 12))
        fig.suptitle("Multi-Level Deviation Map", fontsize=16)
        
        unique_holes = vectors_df.drop_duplicates(subset=['ID'])
        temps = unique_holes[unique_holes['ID'].astype(str).str.startswith('T')]
        freeze = unique_holes[~unique_holes['ID'].astype(str).str.startswith('T')]
        
        # Design Centers - ZORDER 100 to stay on TOP
        if not freeze.empty: ax.scatter(freeze['Start_E'], freeze['Start_N'], marker='+', color='grey', s=80, label='Design (Freeze)', zorder=100)
        if not temps.empty: ax.scatter(temps['Start_E'], temps['Start_N'], marker='^', color='black', facecolors='none', s=60, label='Design (Temp)', zorder=100)
        
        for hid, group in vectors_df.groupby('ID'):
            group = group.sort_values('Elevation', ascending=False)
            path_x = [group.iloc[0]['Collar_E']] + group['End_E'].tolist()
            path_y = [group.iloc[0]['Collar_N']] + group['End_N'].tolist()
            ax.plot(path_x, path_y, color='black', linewidth=0.5, alpha=0.5, zorder=2)

        legend_added = set()
        for _, row in vectors_df.iterrows():
            elev = row['Elevation']
            closest_k = min(colors.keys(), key=lambda k: abs(k - elev))
            c = colors.get(closest_k, 'black')
            ax.plot([row['Start_E'], row['End_E']], [row['Start_N'], row['End_N']], color=c, lw=1, alpha=0.6, zorder=3)
            lbl = f"Elev {closest_k}" if closest_k not in legend_added else None
            ax.scatter(row['End_E'], row['End_N'], color=c, s=20, zorder=4, label=lbl, edgecolors='black', linewidth=0.5)
            if lbl: legend_added.add(closest_k)
            
        center_n = unique_holes['Start_N'].mean(); center_e = unique_holes['Start_E'].mean()
        ax.axhline(center_n, c='k', ls='--', lw=1, alpha=0.5); ax.axvline(center_e, c='k', ls='--', lw=1, alpha=0.5)
        ax.set_xlabel("Easting"); ax.set_ylabel("Northing"); ax.axis('equal'); ax.grid(True, ls=':', alpha=0.3)
        ax.legend(loc='upper right', title="Legend")
        plt.tight_layout(); plt.show()

    def plot_top_deviation_map(self, top_vectors_df):
        if top_vectors_df.empty: return
        fig, ax = plt.subplots(figsize=(12, 12))
        fig.suptitle("Top Deviation Map (Actual vs Design)", fontsize=16)
        
        temps = top_vectors_df[top_vectors_df['ID'].astype(str).str.startswith('T')]
        freeze = top_vectors_df[~top_vectors_df['ID'].astype(str).str.startswith('T')]
        
        for _, row in top_vectors_df.iterrows():
            ax.plot([row['Design_E'], row['Actual_E']], [row['Design_N'], row['Actual_N']], color='black', lw=1, alpha=0.6, zorder=1)
            
        if not freeze.empty:
            ax.scatter(freeze['Actual_E'], freeze['Actual_N'], s=80, facecolors='none', edgecolors='red', linewidth=1.5, label='Actual Top (Freeze)', zorder=2)
        if not temps.empty:
            ax.scatter(temps['Actual_E'], temps['Actual_N'], s=80, facecolors='none', edgecolors='blue', linewidth=1.5, label='Actual Top (Temp)', zorder=2)
            
        # Design on TOP (zorder=100)
        ax.scatter(top_vectors_df['Design_E'], top_vectors_df['Design_N'], marker='+', color='black', s=80, label='Design Top', zorder=100)
            
        for _, row in top_vectors_df.iterrows():
            ax.annotate(str(row['ID']), (row['Actual_E'], row['Actual_N']), fontsize=8, xytext=(4, 4), textcoords='offset points', zorder=101)
            
        ax.set_xlabel("Easting"); ax.set_ylabel("Northing")
        ax.axis('equal'); ax.grid(True, linestyle=':', alpha=0.3)
        ax.legend(loc='upper right')
        plt.tight_layout()
        return fig
