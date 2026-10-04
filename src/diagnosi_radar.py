#!/usr/bin/env python3
"""
Diagnosi copertura radar - Digital Twin del sonno
=================================================
Analizza un intervallo di dati del radar da InfluxDB e riporta, in modo
oggettivo, quanto il sensore ha agganciato il bersaglio e con che stabilita'.
Serve a confrontare configurazioni diverse (es. con/senza throttle) sui NUMERI.

Uso:
    python3 diagnosi_radar.py                       # ultimi 15 minuti
    python3 diagnosi_radar.py --minuti 10           # ultimi 10 minuti
    python3 diagnosi_radar.py --da "2026-09-11 14:40" --a "2026-09-11 14:50"

Legge le credenziali da config.ini (sezione [influx]).
Dipendenze: pip install influxdb-client pandas
"""
import argparse, configparser, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
import pandas as pd
from influxdb_client import InfluxDBClient

cfg = configparser.ConfigParser(); cfg.read(Path(__file__).with_name("config.ini"))
URL=cfg.get("influx","url",fallback="http://localhost:8086")
TOKEN=cfg.get("influx","token"); ORG=cfg.get("influx","org",fallback="tesi")
BUCKET=cfg.get("influx","bucket",fallback="sonno")

ap=argparse.ArgumentParser()
ap.add_argument("--minuti",type=int,default=15)
ap.add_argument("--da",default=None); ap.add_argument("--a",default=None)
a=ap.parse_args()

# finestra temporale (in UTC per la query; --da/--a si intendono ora ITALIANA)
if a.da and a.a:
    start=(pd.Timestamp(a.da)-pd.Timedelta(hours=2)).tz_localize("UTC")
    stop =(pd.Timestamp(a.a )-pd.Timedelta(hours=2)).tz_localize("UTC")
else:
    stop=pd.Timestamp.now(tz="UTC"); start=stop-pd.Timedelta(minutes=a.minuti)

flux=f'''
from(bucket:"{BUCKET}")
 |> range(start: {start.isoformat()}, stop: {stop.isoformat()})
 |> filter(fn:(r)=> r.nodo=="nodo_radar")
 |> filter(fn:(r)=> r._measurement=="frequenza_respiratoria" or r._measurement=="numero_bersagli")
 |> keep(columns:["_time","_value","_measurement"])
'''
cli=InfluxDBClient(url=URL,token=TOKEN,org=ORG)
df=cli.query_api().query_data_frame(flux)
if isinstance(df,list): df=pd.concat(df,ignore_index=True) if df else pd.DataFrame()
if len(df)==0: sys.exit("Nessun dato nell'intervallo. Controlla orari e che il radar pubblichi.")

df["_time"]=pd.to_datetime(df["_time"],utc=True)
resp=df[df["_measurement"]=="frequenza_respiratoria"].set_index("_time")["_value"].sort_index()
bers=df[df["_measurement"]=="numero_bersagli"].set_index("_time")["_value"].sort_index()

durata=(stop-start).total_seconds()
def it(ts): return (ts+pd.Timedelta(hours=2)).strftime("%H:%M:%S")
print("="*58)
print(f"  DIAGNOSI RADAR  {it(start)} -> {it(stop)}  ({durata/60:.1f} min, ora IT)")
print("="*58)

# --- FREQUENZA DI PUBBLICAZIONE (per capire se il throttle agisce) ---
print(f"\n[Pubblicazione]")
print(f"  messaggi respiro ricevuti : {len(resp)}  (~1 ogni {durata/max(len(resp),1):.1f}s)")
print(f"  messaggi bersagli ricevuti: {len(bers)}  (~1 ogni {durata/max(len(bers),1):.1f}s)")

# --- COPERTURA: secondi con almeno un dato respiro ---
sec=resp.resample("1s").mean()
copertura=sec.notna().sum()/ (durata) *100
print(f"\n[Copertura respiro]")
print(f"  secondi con dato : {sec.notna().sum()} su ~{int(durata)}  ({copertura:.0f}%)")
if len(resp):
    print(f"  respiro valido (>4) : {(resp>4).mean()*100:.0f}%   near-zero (<=4): {(resp<=4).mean()*100:.0f}%")
    rv=resp[resp>4]
    if len(rv): print(f"  respiro valido: media {rv.mean():.1f}  std {rv.std():.1f}  range {rv.min():.0f}-{rv.max():.0f}")

# --- BUCHI: gap piu' lunghi tra due campioni ---
if len(resp)>1:
    gaps=resp.index.to_series().diff().dt.total_seconds().dropna()
    lunghi=gaps[gaps>5]
    print(f"\n[Buchi] gap tra campioni respiro > 5s: {len(lunghi)}")
    if len(lunghi): print(f"  gap massimo: {gaps.max():.0f}s   tempo totale nei buchi: {lunghi.sum():.0f}s ({lunghi.sum()/durata*100:.0f}%)")

# --- minuto per minuto ---
print(f"\n[Minuto per minuto]")
r1=resp.resample("1min").mean(); rv=resp.resample("1min").apply(lambda s:(s>4).mean())
for t in r1.index:
    m=r1[t]; f=rv[t]
    if pd.isna(m): print(f"  {it(t)[:5]}  (nessun dato)"); continue
    print(f"  {it(t)[:5]}  resp={m:5.1f}  valido={0 if pd.isna(f) else f*100:3.0f}%  {'#'*int((0 if pd.isna(f) else f)*20)}")
cli.close()
