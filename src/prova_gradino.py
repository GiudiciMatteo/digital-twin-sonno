#!/usr/bin/env python3
"""
Prova a gradino per l'identificazione del modello termico della stanza
=====================================================================
Progetto: Digital Twin del sonno

Sequenza automatica:
  1) registra la temperatura di partenza (stanza a regime)
  2) accende il Daikin in raffrescamento a setpoint basso, ventola silenziosa
  3) attende 30 min (gradino ON) -> osserva come scende la temperatura al letto
  4) spegne il Daikin
  5) attende 30 min (gradino OFF) -> osserva la risalita naturale
  6) marca inizio/fine di ogni fase su MQTT (finiscono in InfluxDB)

Tutti i comandi passano dal modulo attuatori, quindi ora e setpoint di
ogni azione vengono registrati automaticamente. Gli istanti delle fasi
vengono anche marcati come eventi dedicati per ritrovarli facilmente.

Uso:
    python3 prova_gradino.py
    python3 prova_gradino.py --setpoint 20 --ventola B --minuti 30

NB: eseguire di giorno, stanza chiusa, senza sostare in mezzo alla stanza.
"""

import argparse, time, sys
from datetime import datetime, timezone

import attuatori   # riusa il modulo gia' scritto (clima_imposta, clima_spegni, registra_azione)

def marca(fase):
    """Marca l'inizio di una fase come evento su MQTT/InfluxDB."""
    attuatori.registra_azione("prova_termica", "fase", fase, f"prova a gradino")
    ts = datetime.now(timezone.utc).astimezone()
    print(f"  [{ts:%H:%M:%S}]  --> {fase}")

def leggi_temp_letto():
    """Legge l'ultima temperatura dal BME280 via MQTT (best effort)."""
    # semplice: la temperatura la vediamo poi in InfluxDB; qui stampiamo lo stato Daikin
    st = attuatori.clima_stato()
    return st

def attendi(minuti, etichetta):
    tot = int(minuti*60)
    for s in range(tot):
        if s % 60 == 0:
            print(f"    {etichetta}: {s//60}/{minuti} min", end="\r", flush=True)
        time.sleep(1)
    print(f"    {etichetta}: {minuti}/{minuti} min  (completato)      ")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setpoint", type=float, default=20.0)
    ap.add_argument("--ventola", default="B")     # B = silenzioso
    ap.add_argument("--minuti", type=int, default=30)
    a = ap.parse_args()

    print("="*60)
    print("  PROVA A GRADINO - modello termico")
    print(f"  setpoint {a.setpoint}°C · ventola {a.ventola} · fasi da {a.minuti} min")
    print("="*60)
    print("  Assicurarsi: stanza chiusa, nessuno in mezzo alla stanza.")
    print("  Ctrl+C per interrompere in sicurezza (spegne il clima).\n")

    try:
        # --- FASE 0: baseline a clima spento ---
        marca("inizio_baseline_pre")
        print("  Registro 5 min di temperatura a regime (clima spento)...")
        attuatori.clima_spegni(motivo="prova gradino - baseline pre")
        attendi(5, "baseline")

        # --- FASE 1: gradino ON (raffrescamento) ---
        marca("gradino_ON")
        print(f"  Accendo Daikin: raffrescamento, setpoint {a.setpoint}°C, ventola {a.ventola}")
        attuatori.clima_imposta(a.setpoint, acceso=True, modo=3,
                                ventola=a.ventola, motivo="prova gradino - ON")
        attendi(a.minuti, "raffrescamento")

        # stato a fine raffrescamento
        st = attuatori.clima_stato()
        print(f"    stato Daikin: htemp={st.get('htemp')}  otemp={st.get('otemp')}  "
              f"cmpfreq={st.get('cmpfreq')}")

        # --- FASE 2: gradino OFF (risalita) ---
        marca("gradino_OFF")
        print("  Spengo Daikin: osservo la risalita naturale")
        attuatori.clima_spegni(motivo="prova gradino - OFF")
        attendi(a.minuti, "risalita")

        marca("fine_prova")
        print("\n  PROVA COMPLETATA.")
        print("  I dati sono in InfluxDB. Per l'analisi useremo:")
        print("   - temperatura_letto (BME280)")
        print("   - temperatura_interna_split e temperatura_esterna (Daikin)")
        print("   - gli eventi 'prova_termica' per delimitare le fasi")

    except KeyboardInterrupt:
        print("\n\n  INTERRUZIONE - spengo il clima per sicurezza.")
        attuatori.clima_spegni(motivo="prova gradino - interrotta")
        sys.exit(0)

if __name__ == "__main__":
    main()
