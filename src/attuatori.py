#!/usr/bin/env python3
"""
Attuatori del Digital Twin del sonno
====================================

Modulo di basso livello: sa COME eseguire le azioni, non decide QUANDO.
La logica decisionale (strato di protezione + politica) sta altrove.

Attuatori gestiti:
  - Luci   : Philips Hue, gruppo "Soggiorno" (strisce L5 1 / L5 2)
  - Clima  : Daikin Emura, API locale
  - Audio  : file locale (rumore bianco / pioggia) via uscita AirPlay del Mac

Ogni azione eseguita viene pubblicata su MQTT, cosi' il collettore la
registra su InfluxDB: per ogni azione si deve poter ricostruire in
seguito che cosa e' stato fatto e perche'.

Dipendenze: pip install requests paho-mqtt
"""

import configparser
import json
import logging
import subprocess
import time
from pathlib import Path

import requests
import paho.mqtt.client as mqtt

# ---------------------------------------------------------------
cfg = configparser.ConfigParser()
cfg.read(Path(__file__).with_name("config.ini"))

HUE_BRIDGE = cfg.get("hue", "bridge")
HUE_KEY = cfg.get("hue", "api_key")
HUE_GRUPPO = cfg.get("hue", "gruppo", fallback="6")        # 6 = "Soggiorno"

DAIKIN_HOST = cfg.get("daikin", "host")

AUDIO_FILE = cfg.get("audio", "file", fallback="")
AUDIO_VOLUME = cfg.getfloat("audio", "volume", fallback=0.3)   # 0.0 - 1.0

MQTT_HOST = cfg.get("mqtt", "host")
MQTT_PORT = cfg.getint("mqtt", "port", fallback=1883)
MQTT_USER = cfg.get("mqtt", "user")
MQTT_PASS = cfg.get("mqtt", "password")

log = logging.getLogger("attuatori")

# --- Scala mired delle strisce: 153 (freddo, 6500K) .. 500 (caldo, 2000K)
CT_CALDO_MAX = 500
CT_NEUTRO = 350

# --- Limiti fisici di sicurezza (lo strato di protezione li usera')
TEMP_MIN = cfg.getfloat("vincoli", "temp_min", fallback=19.0)
TEMP_MAX = cfg.getfloat("vincoli", "temp_max", fallback=28.0)


# ===============================================================
#  Tracciamento delle azioni
# ===============================================================
_mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="attuatori-sonno")
_mqtt.username_pw_set(MQTT_USER, MQTT_PASS)
_mqtt_pronto = False


def _connetti_mqtt():
    global _mqtt_pronto
    if not _mqtt_pronto:
        _mqtt.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        _mqtt.loop_start()
        _mqtt_pronto = True


def registra_azione(attuatore: str, azione: str, valore, motivo: str = ""):
    """
    Pubblica l'azione su MQTT perche' venga salvata su InfluxDB.
    'motivo' serve a ricostruire a posteriori perche' l'azione e' avvenuta.
    """
    _connetti_mqtt()
    _mqtt.publish(f"sonno/azioni/sensor/{attuatore}_{azione}/state", str(valore))
    if motivo:
        _mqtt.publish(f"sonno/azioni/sensor/{attuatore}_{azione}_motivo/state", motivo)
    log.info("AZIONE %s.%s = %s  (%s)", attuatore, azione, valore, motivo or "-")


# ===============================================================
#  LUCI — Philips Hue
# ===============================================================
def _hue_gruppo(payload: dict):
    url = f"http://{HUE_BRIDGE}/api/{HUE_KEY}/groups/{HUE_GRUPPO}/action"
    r = requests.put(url, data=json.dumps(payload), timeout=5)
    r.raise_for_status()
    return r.json()


def luci_spegni(transizione_s: int = 5, motivo: str = ""):
    """Spegne il gruppo con una dissolvenza."""
    _hue_gruppo({"on": False, "transitiontime": transizione_s * 10})
    registra_azione("luci", "spegni", 0, motivo)


def luci_imposta(luminosita: int, ct: int = CT_CALDO_MAX,
                 transizione_s: int = 5, motivo: str = ""):
    """
    luminosita: 1-254   (le strisce scendono fino a valori molto bassi)
    ct        : 153-500 mired; piu' alto = piu' caldo = meno componente blu
    """
    luminosita = max(1, min(254, int(luminosita)))
    ct = max(153, min(500, int(ct)))
    _hue_gruppo({
        "on": True,
        "bri": luminosita,
        "ct": ct,
        "transitiontime": transizione_s * 10,      # in decimi di secondo
    })
    registra_azione("luci", "imposta", f"bri={luminosita},ct={ct}", motivo)


def luci_rampa_presonno(durata_min: int = 30, passi: int = 10,
                        bri_iniziale: int = 200, bri_finale: int = 3):
    """
    Rampa di preparazione al sonno: luce che si scalda e si abbassa
    progressivamente, per favorire la produzione di melatonina.
    Al termine le luci restano molto fioche (non spente): lo spegnimento
    e' un'azione separata, decisa dal controllore.
    """
    intervallo = (durata_min * 60) / passi
    for i in range(passi + 1):
        frazione = i / passi
        bri = int(bri_iniziale + (bri_finale - bri_iniziale) * frazione)
        ct = int(CT_NEUTRO + (CT_CALDO_MAX - CT_NEUTRO) * frazione)
        luci_imposta(bri, ct, transizione_s=int(intervallo / 2),
                     motivo=f"rampa_presonno {i}/{passi}")
        if i < passi:
            time.sleep(intervallo)


def luci_risveglio_dolce(durata_min: int = 10, bri_finale: int = 120):
    """
    Modalita' protezione: risveglio graduale su arousal anomalo.
    Luce calda che sale lentamente, non luce fredda improvvisa.
    """
    _hue_gruppo({
        "on": True, "bri": 1, "ct": CT_CALDO_MAX, "transitiontime": 10,
    })
    time.sleep(1)
    _hue_gruppo({
        "bri": max(1, min(254, bri_finale)),
        "ct": CT_CALDO_MAX,
        "transitiontime": durata_min * 60 * 10,
    })
    registra_azione("luci", "risveglio_dolce", bri_finale, "modalita_protezione")


# ===============================================================
#  CLIMA — Daikin
# ===============================================================
def _daikin_leggi(endpoint: str) -> dict:
    r = requests.get(f"http://{DAIKIN_HOST}{endpoint}", timeout=5)
    r.raise_for_status()
    out = {}
    for pezzo in r.text.strip().split(","):
        if "=" in pezzo:
            k, v = pezzo.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def clima_stato() -> dict:
    return _daikin_leggi("/aircon/get_control_info")


def clima_imposta(setpoint: float, acceso: bool = True, modo: int = 3,
                  ventola: str = "B", motivo: str = ""):
    """
    setpoint : temperatura desiderata (limitata dai vincoli di sicurezza)
    modo     : 3 = raffrescamento, 4 = riscaldamento, 6 = ventilazione
    ventola  : 'A' auto, 'B' silenzioso, '3'..'7' livelli
               Di notte si usa 'B' (silenzioso) per non introdurre rumore.

    Nota metodologica: il setpoint e' l'INGRESSO del modello termico,
    non la frequenza del compressore.
    """
    setpoint = max(TEMP_MIN, min(TEMP_MAX, float(setpoint)))

    # set_control_info richiede sempre tutti i parametri obbligatori
    params = {
        "pow": "1" if acceso else "0",
        "mode": str(modo),
        "stemp": f"{setpoint:.1f}",
        "shum": "0",
        "f_rate": ventola,
        "f_dir": "0",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    r = requests.get(f"http://{DAIKIN_HOST}/aircon/set_control_info?{query}",
                     timeout=5)
    r.raise_for_status()
    registra_azione("clima", "setpoint", setpoint, motivo)
    return r.text


def clima_spegni(motivo: str = ""):
    stato = clima_stato()
    setpoint = float(stato.get("stemp", 24.0))
    clima_imposta(setpoint, acceso=False, motivo=motivo)
    registra_azione("clima", "spegni", 0, motivo)


# ===============================================================
#  AUDIO — rumore bianco / pioggia
# ===============================================================
_processo_audio = None


def audio_avvia(motivo: str = ""):
    """
    Riproduce in loop il file configurato sull'uscita audio corrente
    del Mac (impostare la JBL come uscita AirPlay prima della notte).
    """
    global _processo_audio
    if not AUDIO_FILE or not Path(AUDIO_FILE).exists():
        log.warning("File audio non configurato o inesistente: %s", AUDIO_FILE)
        return
    if _processo_audio and _processo_audio.poll() is None:
        return                                    # gia' in riproduzione

    _processo_audio = subprocess.Popen(
        ["afplay", "-v", str(AUDIO_VOLUME), AUDIO_FILE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    registra_azione("audio", "avvia", 1, motivo)


def audio_ferma(motivo: str = ""):
    global _processo_audio
    if _processo_audio and _processo_audio.poll() is None:
        _processo_audio.terminate()
        try:
            _processo_audio.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _processo_audio.kill()
    _processo_audio = None
    registra_azione("audio", "ferma", 0, motivo)


def audio_in_riproduzione() -> bool:
    return _processo_audio is not None and _processo_audio.poll() is None


# ===============================================================
#  Prova manuale:  python3 attuatori.py
# ===============================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s %(message)s")

    print("\n--- Prova attuatori (Ctrl+C per interrompere) ---\n")

    print("1. Luci: accensione calda e fioca")
    luci_imposta(60, CT_CALDO_MAX, transizione_s=2, motivo="prova")
    time.sleep(4)

    print("2. Luci: spegnimento")
    luci_spegni(transizione_s=2, motivo="prova")
    time.sleep(2)

    print("3. Clima: lettura stato")
    print("   ", clima_stato())

    print("4. Audio: avvio per 5 secondi")
    audio_avvia(motivo="prova")
    time.sleep(5)
    audio_ferma(motivo="prova")

    print("\nProva completata.\n")
