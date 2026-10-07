#!/usr/bin/env python3
"""Chapter 4.1 - radar continuity and sensor-position characterization.

Input: public data/radar_bme_YYYY-MM-DD.csv files and data/diario_sonno.csv.
Output: nightly CSV with distance validity, target presence and dropout metrics,
plus descriptive comparison between the 71 cm and 100 cm configurations.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from common import read_repo_export, read_diary, night_window, parse_night_from_filename, med_iqr


def run_length_seconds(ts: pd.Series, valid: pd.Series):
    if len(ts) == 0:
        return []
    t = ts.reset_index(drop=True)
    v = valid.reset_index(drop=True).astype(bool)
    runs = []
    start = None
    last = None
    for ti, vi in zip(t, v):
        if vi and start is None:
            start = ti
        if vi:
            last = ti
        elif start is not None:
            runs.append(max(0.0, (last - start).total_seconds()) if last is not None else 0.0)
            start = last = None
    if start is not None:
        runs.append(max(0.0, (last - start).total_seconds()) if last is not None else 0.0)
    return runs


def analyze_file(path: Path, diary: pd.DataFrame):
    night = parse_night_from_filename(path)
    drow = diary.loc[diary['Data notte'] == night]
    if drow.empty:
        return None
    bed, wake = night_window(drow.iloc[0])
    df = read_repo_export(path)
    df = df[(df['timestamp_local'] >= bed) & (df['timestamp_local'] <= wake)].copy()
    dist = df[df.measurement.eq('distanza_bersaglio')].copy()
    tgt = df[df.measurement.eq('numero_bersagli')].copy()
    dist['value_num'] = pd.to_numeric(dist.value, errors='coerce')
    tgt['value_num'] = pd.to_numeric(tgt.value, errors='coerce')
    valid = dist['value_num'].notna() & dist['value_num'].gt(0)
    zero = dist['value_num'].fillna(0).le(0)
    dt = dist.timestamp_utc.sort_values().diff().dt.total_seconds().dropna()
    zero_runs = run_length_seconds(dist.timestamp_utc, zero)
    vals = dist.loc[valid, 'value_num']
    position = 'low_71cm' if night <= pd.Timestamp('2026-09-17').date() else 'high_100cm'
    return {
        'date': night,
        'position': position,
        'hours': (wake-bed).total_seconds()/3600,
        'dist_n': len(dist),
        'dist_valid_pct': 100*valid.mean() if len(dist) else np.nan,
        'dist_zero_pct': 100*zero.mean() if len(dist) else np.nan,
        'target_present_pct': 100*tgt['value_num'].gt(0).mean() if len(tgt) else np.nan,
        'dist_median_valid_cm': vals.median() if len(vals) else np.nan,
        'dist_p10_valid_cm': vals.quantile(.10) if len(vals) else np.nan,
        'dist_p90_valid_cm': vals.quantile(.90) if len(vals) else np.nan,
        'med_sample_dt_s': dt.median() if len(dt) else np.nan,
        'timestamp_gaps_gt4_5_n': int((dt > 4.5).sum()),
        'timestamp_gap_max_s': dt.max() if len(dt) else np.nan,
        'zero_runs_n': len(zero_runs),
        'zero_run_median_s': np.median(zero_runs) if zero_runs else 0.0,
        'zero_run_p90_s': np.percentile(zero_runs,90) if zero_runs else 0.0,
        'zero_run_max_s': max(zero_runs) if zero_runs else 0.0,
        'zero_runs_gt10_n': sum(x > 10 for x in zero_runs),
        'zero_runs_gt30_n': sum(x > 30 for x in zero_runs),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', type=Path, default=Path('../data'))
    ap.add_argument('--diary', type=Path, default=None)
    ap.add_argument('--out', type=Path, default=Path('results/radar_height_nightly_metrics.csv'))
    args = ap.parse_args()
    diary_path = args.diary or args.data_dir/'diario_sonno.csv'
    diary = read_diary(diary_path)
    rows = [analyze_file(p, diary) for p in sorted(args.data_dir.glob('radar_bme_*.csv'))]
    rows = [r for r in rows if r and pd.Timestamp(r['date']) >= pd.Timestamp('2026-09-12')]
    out = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(out.to_string(index=False))
    for pos, g in out.groupby('position'):
        m, q1, q3 = med_iqr(g.dist_valid_pct)
        print(f'\n{pos}: n={len(g)}, valid distance median={m:.1f}% [IQR {q1:.1f}-{q3:.1f}]')

if __name__ == '__main__':
    main()
