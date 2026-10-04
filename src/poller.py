#!/usr/bin/env python3
"""
Poller Daikin + Philips Hue -> MQTT
Progetto: Digital Twin del sonno

Interroga periodicamente il condizionatore Daikin e il bridge Hue e
pubblica i valori su MQTT con lo stesso schema dei nodi ESPHome:

    sonno/<nodo>/sensor/<grandezza>/state

Il collettore gia' attivo li raccoglie e li scrive su InfluxDB senza
alcuna modifica.

Dipendenze:
    pip install requests paho-mqtt

Configurazione: stesso config.ini del collettore, con le sezioni
[daikin] e [hue] aggiunte.
"""

import configparser
import logging
import signal
import sys
import time
from pathlib import Path

import requests
import paho.mqtt.client as mqtt

CONFIG_PATH = Path(__file__).with_name("config.ini")
if not CONFIG_PATH.exists():
    sys.exit(f"File di configurazione mancante: {CONFIG_PATH}")

cfg = configparser.ConfigParser()
cfg.read(CONFIG_PATH)

MQTT_HOST = cfg.get("mqtt", "host")
MQTT_PORT = cfg.getint("mqtt", "port", fallback=1883)
MQTT_USER = cfg.get("mqtt", "user")
MQTT_PASS = cfg.get("mqtt", "password")

DAIKIN_HOST = cfg.get("daikin", "host", fallback="")
HUE_BRIDGE = cfg.get("hue", "bridge", fallback="")
HUE_KEY = cfg.get("hue", "api_key", fallback="")
INTERVALLO = cfg.getint("poller", "intervallo_secondi", fallback=10)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("poller")


# ---------------------------------------------------------------
# DAIKIN
# ---------------------------------------------------------------
def leggi_daikin_risposta(url: str) -> dict:
    """
    Le risposte Daikin sono nel formato:
        ret=OK,htemp=28.0,hhum=-,otemp=25.0,err=0,cmpfreq=0
    """
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    risultato = {}
    for pezzo in r.text.strip().split(","):
        if "=" in pezzo:
            k, v = pezzo.split("=", 1)
            risultato[k.strip()] = v.strip()
    return risultato


def numerico(v):
    """Converte in float; il Daikin usa '-' per i valori non disponibili."""
    if v in ("-", "--", "", None):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def poll_daikin(pubblica):
    if not DAIKIN_HOST:
        return

    # --- sensori: temperature, umidita', compressore ---
    try:
        s = leggi_daikin_risposta(f"http://{DAIKIN_HOST}/aircon/get_sensor_info")
        mappa = {
            "htemp": "temperatura_interna_split",   # sonda dello split (in quota)
            "otemp": "temperatura_esterna",         # richiesta dal relatore
            "hhum": "umidita_interna_split",
            "cmpfreq": "frequenza_compressore",     # cosa fa davvero la macchina
        }
        for chiave, nome in mappa.items():
            val = numerico(s.get(chiave))
            if val is not None:
                pubblica("daikin", nome, val)
    except Exception as exc:                        # noqa: BLE001
        log.warning("Daikin get_sensor_info: %s", exc)

    # --- stato di controllo: e' l'INGRESSO del modello termico ---
    try:
        c = leggi_daikin_risposta(f"http://{DAIKIN_HOST}/aircon/get_control_info")
        # pow: 0/1 | mode: 2=deumid 3=freddo 4=caldo 6=vent 0/1/7=auto
        # stemp: temperatura impostata (setpoint) -> variabile di comando
        for chiave, nome in {
            "pow": "stato_acceso",
            "mode": "modalita",
            "stemp": "setpoint",
        }.items():
            val = numerico(c.get(chiave))
            if val is not None:
                pubblica("daikin", nome, val)
        # f_rate e' alfanumerico (A=auto, B=silence, 3..7=livelli)
        if c.get("f_rate"):
            pubblica("daikin", "velocita_ventola", c["f_rate"])
    except Exception as exc:                        # noqa: BLE001
        log.warning("Daikin get_control_info: %s", exc)


# ---------------------------------------------------------------
# PHILIPS HUE  (API locale v1)
# ---------------------------------------------------------------
def poll_hue(pubblica):
    if not HUE_BRIDGE or not HUE_KEY:
        return
    try:
        r = requests.get(f"http://{HUE_BRIDGE}/api/{HUE_KEY}/lights", timeout=5)
        r.raise_for_status()
        luci = r.json()
        accese = 0
        for _id, luce in luci.items():
            nome = luce.get("name", f"luce_{_id}").lower().replace(" ", "_")
            st = luce.get("state", {})
            acceso = 1.0 if st.get("on") else 0.0
            pubblica("hue", f"{nome}_accesa", acceso)
            if st.get("on"):
                accese += 1
                if "bri" in st:                     # luminosita' 1-254
                    pubblica("hue", f"{nome}_luminosita", float(st["bri"]))
                if "ct" in st:                      # temperatura colore (mired)
                    pubblica("hue", f"{nome}_temp_colore_mired", float(st["ct"]))
        pubblica("hue", "luci_accese_totale", float(accese))
    except Exception as exc:                        # noqa: BLE001
        log.warning("Hue: %s", exc)


# ---------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="poller-sonno")
client.username_pw_set(MQTT_USER, MQTT_PASS)

contatore = {"pubblicati": 0}


def pubblica(nodo: str, grandezza: str, valore):
    topic = f"sonno/{nodo}/sensor/{grandezza}/state"
    client.publish(topic, str(valore), qos=0, retain=False)
    contatore["pubblicati"] += 1


def chiusura(signum, frame):                        # noqa: ARG001
    log.info("Chiusura. Totale pubblicati: %d", contatore["pubblicati"])
    client.loop_stop()
    client.disconnect()
    sys.exit(0)


signal.signal(signal.SIGINT, chiusura)
signal.signal(signal.SIGTERM, chiusura)

log.info("Avvio poller (intervallo %ds)", INTERVALLO)
log.info("Daikin: %s | Hue: %s", DAIKIN_HOST or "non configurato",
         HUE_BRIDGE or "non configurato")

client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
client.loop_start()

giro = 0
while True:
    inizio = time.time()
    poll_daikin(pubblica)
    poll_hue(pubblica)
    giro += 1
    if giro % 30 == 0:
        log.info("giri=%d  valori pubblicati=%d", giro, contatore["pubblicati"])
    attesa = INTERVALLO - (time.time() - inizio)
    if attesa > 0:
        time.sleep(attesa)
