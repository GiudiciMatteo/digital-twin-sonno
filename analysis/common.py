from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo
import re
import pandas as pd
import numpy as np

TZ = ZoneInfo('Europe/Rome')
STABILIZED_NIGHTS = pd.date_range('2026-09-12', '2026-09-30', freq='D').difference(pd.DatetimeIndex(['2026-09-19'])).date


def read_repo_export(path: str | Path) -> pd.DataFrame:
    """Read repository CSV exports: timestamp_utc,measurement,field,value."""
    df = pd.read_csv(path)
    needed = {'timestamp_utc', 'measurement', 'value'}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f'{path}: missing columns {sorted(missing)}')
    df['timestamp_utc'] = pd.to_datetime(df['timestamp_utc'], utc=True, errors='coerce')
    df = df.dropna(subset=['timestamp_utc']).copy()
    df['timestamp_local'] = df['timestamp_utc'].dt.tz_convert(TZ)
    return df


def read_diary(path: str | Path) -> pd.DataFrame:
    """Read the de-identified semicolon diary currently published in data/."""
    # The public file has three preamble rows before the actual header.
    df = pd.read_csv(path, sep=';', skiprows=3, dtype=str)
    df = df.loc[:, ~df.columns.str.startswith('Unnamed')]
    df['Data notte'] = pd.to_datetime(df['Data notte'], errors='coerce').dt.date
    for col in ['Voto sonno (1-5)', 'Risvegli percepiti', 'Min. per addormentarti']:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def night_window(diary_row: pd.Series) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return local bedtime/wake window for a diary row.

    Study convention: Data notte is the waking date. If Ora letto is later
    than Ora sveglia, bedtime belongs to the preceding civil date.
    """
    date = pd.Timestamp(diary_row['Data notte'])
    bed_t = pd.to_datetime(str(diary_row['Ora letto']), format='%H:%M').time()
    wake_t = pd.to_datetime(str(diary_row['Ora sveglia']), format='%H:%M').time()
    bed = pd.Timestamp.combine(date, bed_t).tz_localize(TZ)
    wake = pd.Timestamp.combine(date, wake_t).tz_localize(TZ)
    if bed >= wake:
        bed -= pd.Timedelta(days=1)
    return bed, wake


def parse_night_from_filename(path: str | Path):
    m = re.search(r'(20\d\d-\d\d-\d\d)', Path(path).name)
    if not m:
        raise ValueError(f'Cannot infer night date from {path}')
    return pd.Timestamp(m.group(1)).date()


def q25(x):
    return np.nanpercentile(np.asarray(x, dtype=float), 25)


def q75(x):
    return np.nanpercentile(np.asarray(x, dtype=float), 75)


def med_iqr(x) -> tuple[float, float, float]:
    a = np.asarray(pd.Series(x).dropna(), dtype=float)
    if len(a) == 0:
        return np.nan, np.nan, np.nan
    return float(np.median(a)), float(np.percentile(a, 25)), float(np.percentile(a, 75))


def binary_withings(stage):
    s = str(stage).strip().upper()
    if s in {'LIGHT', 'DEEP', 'REM', 'SLEEP', 'ASLEEP'}:
        return 'Sleep'
    if s in {'AWAKE', 'WAKE'}:
        return 'Wake'
    return np.nan


def binary_apple(stage):
    s = str(stage).strip().upper()
    if s in {'CORE', 'DEEP', 'REM', 'ASLEEP', 'ASLEEP_UNSPECIFIED', 'SLEEP'}:
        return 'Sleep'
    if s in {'AWAKE', 'WAKE'}:
        return 'Wake'
    return np.nan


def binary_dt(stage, exclude_transients: bool = False):
    s = str(stage).strip().upper()
    if s == 'UNOBSERVABLE':
        return np.nan
    if exclude_transients and s in {'POSSIBLE_SLEEP', 'POSSIBLE_AWAKE'}:
        return np.nan
    if s in {'AWAKE', 'POSSIBLE_SLEEP'}:
        return 'Wake'
    if s in {'ASLEEP', 'POSSIBLE_AWAKE'}:
        return 'Sleep'
    return np.nan
