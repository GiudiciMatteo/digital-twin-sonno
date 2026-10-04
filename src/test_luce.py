#!/usr/bin/env python3
"""
Test della rampa luce pre-sonno
===============================
Rampa fluida ambra -> arancio -> rosso caldo (mai fuoco), con la
luminosita' che scende dal 40% quasi a zero, poi SPEGNIMENTO VERO.

Percorso in coordinate colore xy (spazio CIE), che evita del tutto il
blu. I punti sono scelti nella zona calda del gamut delle strisce.
Il fade e' liscio perche' si usano pochi passi con transizioni lunghe
(niente "scalini").

Uso:
    python3 test_luce.py                 # rampa realistica 15 min
    python3 test_luce.py --minuti 2      # rampa veloce per provare al volo
    python3 test_luce.py --solo-spegni   # prova solo lo spegnimento corretto
    python3 test_luce.py --tieni ambra   # accende fisso un colore per guardarlo
"""

import argparse, time, json, sys, configparser
from pathlib import Path
import requests

cfg = configparser.ConfigParser()
cfg.read(Path(__file__).with_name("config.ini"))
BRIDGE = cfg.get("hue", "bridge")
KEY    = cfg.get("hue", "api_key")
GRUPPO = cfg.get("hue", "gruppo", fallback="6")

# ---- Punti colore (xy CIE) nella zona calda, dal piu' "aperto" al piu' rosso ----
# ambra caldo -> arancio -> rosso terracotta (NON rosso fuoco puro)
COLORI = {
    "ambra":      [0.54, 0.41],   # ambra/oro caldo
    "arancio":    [0.58, 0.39],   # arancio caldo
    "terracotta": [0.64, 0.34],   # rosso morbido
}
# sequenza della rampa: (posizione_xy, luminosita 1-254)
# parte ambra al ~40% (bri 100), finisce terracotta molto fioco
TAPPE = [
    (COLORI["ambra"],      100),
    (COLORI["ambra"],       70),
    (COLORI["arancio"],     45),
    (COLORI["arancio"],     25),
    (COLORI["terracotta"],  10),
    (COLORI["terracotta"],   3),
]

def hue_put(payload):
    url = f"http://{BRIDGE}/api/{KEY}/groups/{GRUPPO}/action"
    r = requests.put(url, data=json.dumps(payload), timeout=5)
    r.raise_for_status()
    return r.json()

def spegni_davvero():
    """Prima porta a 1, poi spegne DAVVERO (fix: bri basso != off)."""
    hue_put({"bri": 1, "transitiontime": 20})
    time.sleep(2.5)
    hue_put({"on": False, "transitiontime": 10})
    print("  luce spenta (on=false)")

def tieni(colore):
    xy = COLORI.get(colore)
    if not xy:
        sys.exit(f"colore non valido. Scegli: {', '.join(COLORI)}")
    hue_put({"on": True, "bri": 100, "xy": xy, "transitiontime": 20})
    print(f"  acceso fisso: {colore} @ 40%  (Ctrl+C per uscire, resta acceso)")

def rampa(minuti):
    passi = len(TAPPE)
    dt = (minuti*60) / passi          # durata di ciascun tratto
    print(f"  rampa {minuti} min · {passi} tappe · {dt:.0f}s per tappa")
    # accensione iniziale
    xy0, bri0 = TAPPE[0]
    hue_put({"on": True, "bri": bri0, "xy": xy0, "transitiontime": 20})
    time.sleep(2)
    for i,(xy,bri) in enumerate(TAPPE[1:], start=1):
        # transitiontime lungo = fade liscio, niente scalini
        hue_put({"bri": bri, "xy": xy, "transitiontime": int(dt*10)})
        print(f"  tappa {i}/{passi-1}: xy={xy} bri={bri}")
        time.sleep(dt)
    print("  fine rampa -> spegnimento")
    spegni_davvero()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minuti", type=float, default=15)
    ap.add_argument("--solo-spegni", action="store_true")
    ap.add_argument("--tieni", default=None)
    a = ap.parse_args()
    try:
        if a.solo_spegni:
            spegni_davvero()
        elif a.tieni:
            tieni(a.tieni)
            while True: time.sleep(1)
        else:
            rampa(a.minuti)
    except KeyboardInterrupt:
        print("\n  interrotto")
