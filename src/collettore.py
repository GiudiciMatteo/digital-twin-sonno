#!/usr/bin/env python3
"""
Collettore MQTT -> InfluxDB
Progetto: Digital Twin del sonno

Sottoscrive i topic dei nodi ESPHome e scrive i valori su InfluxDB.
Nessun filtraggio: i dati vengono salvati grezzi, cosi' come arrivano.
Il filtraggio si fa a valle, in fase di analisi.

Dipendenze:
    pip install paho-mqtt influxdb-client

Configurazione: file config.ini accanto a questo script.
"""

import configparser
import logging
import signal
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# ---------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------
CONFIG_PATH = Path(__file__).with_name("config.ini")

if not CONFIG_PATH.exists():
    sys.exit(f"File di configurazione mancante: {CONFIG_PATH}")

cfg = configparser.ConfigParser()
cfg.read(CONFIG_PATH)

MQTT_HOST = cfg.get("mqtt", "host")
MQTT_PORT = cfg.getint("mqtt", "port", fallback=1883)
MQTT_USER = cfg.get("mqtt", "user")
MQTT_PASS = cfg.get("mqtt", "password")
MQTT_TOPIC = cfg.get("mqtt", "topic", fallback="sonno/#")

INFLUX_URL = cfg.get("influx", "url", fallback="http://localhost:8086")
INFLUX_TOKEN = cfg.get("influx", "token")
INFLUX_ORG = cfg.get("influx", "org", fallback="tesi")
INFLUX_BUCKET = cfg.get("influx", "bucket", fallback="sonno")

# Ora locale in cui si considera iniziata una "nuova notte".
# Con cutoff = 12, tutto cio' che arriva dopo mezzogiorno appartiene
# alla notte etichettata con la data del GIORNO SUCCESSIVO (= risveglio).
NIGHT_CUTOFF_HOUR = cfg.getint("studio", "night_cutoff_hour", fallback=12)
LOCAL_TZ_OFFSET = cfg.getint("studio", "local_tz_offset_hours", fallback=2)

# Condizione sperimentale corrente: baseline | attiva | controllo
CONDIZIONE = cfg.get("studio", "condizione", fallback="baseline")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("collettore")


# ---------------------------------------------------------------
# Etichetta della notte (convenzione: data del RISVEGLIO)
# ---------------------------------------------------------------
def etichetta_notte(ts_utc: datetime) -> str:
    """
    Restituisce la data della notte secondo la convenzione Withings:
    la notte 25 -> 26 agosto si chiama '2026-08-26'.

    Regola: si converte in ora locale; se sono passate le NIGHT_CUTOFF_HOUR
    (mezzogiorno), la notte in corso e' quella del giorno successivo.
    """
    locale = ts_utc + timedelta(hours=LOCAL_TZ_OFFSET)
    if locale.hour >= NIGHT_CUTOFF_HOUR:
        locale += timedelta(days=1)
    return locale.strftime("%Y-%m-%d")


# ---------------------------------------------------------------
# Parsing del topic ESPHome
#   sonno/nodo_radar/sensor/frequenza_respiratoria/state
#   -> nodo = nodo_radar, tipo = sensor, grandezza = frequenza_respiratoria
# ---------------------------------------------------------------
def analizza_topic(topic: str):
    parti = topic.split("/")
    if len(parti) < 5 or parti[-1] != "state":
        return None
    return {"nodo": parti[1], "tipo": parti[2], "grandezza": parti[3]}


def converti_valore(raw: str):
    """Numerico se possibile; ON/OFF -> 1/0; altrimenti stringa."""
    testo = raw.strip()
    if testo.upper() in ("ON", "TRUE"):
        return 1.0
    if testo.upper() in ("OFF", "FALSE"):
        return 0.0
    try:
        return float(testo)
    except ValueError:
        return testo


# ---------------------------------------------------------------
# InfluxDB
# ---------------------------------------------------------------
influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api = influx.write_api(write_options=SYNCHRONOUS)

contatore = {"scritti": 0, "ignorati": 0, "errori": 0}


def scrivi(info, valore, ts_utc):
    punto = (
        Point(info["grandezza"])
        .tag("nodo", info["nodo"])
        .tag("tipo", info["tipo"])
        .tag("notte", etichetta_notte(ts_utc))
        .tag("condizione", CONDIZIONE)
        .time(ts_utc, WritePrecision.NS)
    )
    if isinstance(valore, float):
        punto = punto.field("value", valore)
    else:
        punto = punto.field("value_text", str(valore))

    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=punto)
    contatore["scritti"] += 1


# ---------------------------------------------------------------
# Callback MQTT
# ---------------------------------------------------------------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info("Connesso al broker MQTT %s:%s", MQTT_HOST, MQTT_PORT)
        client.subscribe(MQTT_TOPIC)
        log.info("Sottoscritto a '%s'", MQTT_TOPIC)
        log.info("Condizione corrente: %s", CONDIZIONE)
    else:
        log.error("Connessione MQTT rifiutata (codice %s)", rc)


def on_message(client, userdata, msg):
    ts = datetime.now(timezone.utc)
    info = analizza_topic(msg.topic)
    if info is None:
        contatore["ignorati"] += 1
        return
    try:
        valore = converti_valore(msg.payload.decode("utf-8"))
        scrivi(info, valore, ts)
        if contatore["scritti"] % 500 == 0:
            log.info(
                "scritti=%d ignorati=%d errori=%d  (ultimo: %s/%s = %s)",
                contatore["scritti"], contatore["ignorati"], contatore["errori"],
                info["nodo"], info["grandezza"], valore,
            )
    except Exception as exc:                     # noqa: BLE001
        contatore["errori"] += 1
        log.error("Errore su '%s': %s", msg.topic, exc)


def on_disconnect(client, userdata, rc, properties=None, reason=None):
    log.warning("Disconnesso dal broker (codice %s) - riconnessione automatica", rc)


# ---------------------------------------------------------------
# Avvio
# ---------------------------------------------------------------
def chiusura(signum, frame):                     # noqa: ARG001
    log.info("Chiusura richiesta. Totale scritti: %d", contatore["scritti"])
    try:
        write_api.close()
        influx.close()
    finally:
        sys.exit(0)


signal.signal(signal.SIGINT, chiusura)
signal.signal(signal.SIGTERM, chiusura)

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="collettore-sonno")
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect

log.info("Avvio collettore -> InfluxDB %s (org=%s bucket=%s)",
         INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET)
client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
client.loop_forever()
