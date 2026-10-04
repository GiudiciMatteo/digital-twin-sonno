#!/usr/bin/env python3
"""
Controllore notturno - Digital Twin del sonno  (v3.1)
=====================================================
Storico versioni:
  2.0  base (clima + attuatori)
  2.1  log preciso (temperatura con decimali)
  2.2  spegnimento clima a fine notte
  2.3  attesa del primo dato BME prima di calcolare il target
  2.4  spegnimento clima sempre libero (vincolo solo su riaccensione)
  2.5  ciclo termico: ON max 30 min, riaccensione a target+1 con vincoli
  2.6  loop chiuso sull'addormentamento (basato sul respiro)
  2.7  rilevamento per immobilita' (distanza/presenza); gira e logga
       anche in controllo, respiro solo come conferma
  2.8  rilevatore robusto: gestione buchi di presenza (grazia),
       immobilita' con IQR + range robusto + shift della mediana
  3.0  monitoraggio CONTINUO per tutta la notte (attiva e controllo):
       - stato UNOBSERVABLE: la perdita del segnale radar NON e' piu'
         interpretata come risveglio (mantiene lo stato precedente);
       - stato POSSIBLE_AWAKE: un movimento diventa risveglio solo se
         PERSISTE >= WAKE_CONFIRM (90s); movimenti brevi = movimento_nel_sonno;
       - fallback timer sul rumore (--rumore): spegnimento al primo tra
         addormentamento rilevato o timeout;
       - timing dello sleep-onset della v2.8 preservato (nessun minuto perso).
       NB: AWAKE va inteso come "possibile risveglio/micro-risveglio
       rilevato dal radar", non come veglia clinicamente validata.
  3.1  correzioni:
       - FIX sleep-onset: il recupero da UNOBSERVABLE con movimento porta a
         POSSIBLE_AWAKE solo se si proveniva dal sonno; se si era svegli si
         torna ad AWAKE. ASLEEP si raggiunge SOLO tramite immobilita'
         prolungata (AWAKE -> POSSIBLE_SLEEP -> ASLEEP), mai come "ritorno"
         da un movimento (elimina il falso addormentamento iniziale);
       - tramonto interrompibile (threading.Event): l'addormentamento ferma
         il thread del tramonto (stop + join) prima di spegnere la luce,
         evitando che il tramonto la riaccenda (race condition risolta);
       - log del blocco max_azioni del clima una sola volta, non a ripetizione.

Due livelli (indicazione del relatore):
  LIVELLO 1 - PROTEZIONE: vincoli fissi, vincono sempre. Ogni azione
              (anche BLOCCATA) viene registrata con il motivo.
  LIVELLO 2 - POLITICA: fase pre-sonno (tramonto + rumore bianco +
              clima) e, di notte, termostato delicato sul BME al letto.

Logica clima (concordata):
  - target = temperatura BME all'avvio - 1.5 C  (floor 21 C), UNA volta.
  - termostato sul BME AL LETTO (non sulla sonda Daikin, imprecisa):
        BME <= target        -> spegni il Daikin
        BME >= target + 1 C   -> riaccendi al target
        BME che scende da solo sotto target -> nessun intervento
  - sotto 21 C servirebbe riscaldamento: NON implementato, solo annotato.

Vincoli: min 30 min tra due azioni clima, max 5 azioni/notte.
Tipi di notte:  --attiva  |  --controllo
"""

import argparse
import threading, time, json, subprocess, configparser, statistics
from datetime import datetime, timezone
from pathlib import Path
import requests
import paho.mqtt.client as mqtt

cfg = configparser.ConfigParser(); cfg.read(Path(__file__).with_name("config.ini"))
HUE_BRIDGE=cfg.get("hue","bridge"); HUE_KEY=cfg.get("hue","api_key")
HUE_GRUPPO=cfg.get("hue","gruppo",fallback="6")
DAIKIN=cfg.get("daikin","host")
AUDIO_FILE=cfg.get("audio","file",fallback=""); AUDIO_VOL=cfg.get("audio","volume",fallback="0.3")
MQTT_HOST=cfg.get("mqtt","host"); MQTT_PORT=cfg.getint("mqtt","port",fallback=1883)
MQTT_USER=cfg.get("mqtt","user"); MQTT_PASS=cfg.get("mqtt","password")

VERSIONE = "3.1"   # 3.0 monitoraggio continuo | 3.1 FIX sleep-onset (POSSIBLE_AWAKE solo da ASLEEP) + tramonto interrompibile (stop_event+join) + log max_azioni una volta

DELTA_TARGET=1.5; FLOOR_SETPOINT=21.0; ISTERESI=1.0; VENTOLA="4"
TEMP_MIN, TEMP_MAX = 19.0, 28.0
MIN_TRA_AZIONI=30*60; MAX_AZIONI=5

# tramonto: ambra -> arancio -> terracotta (0.64/0.34), con spegnimento vero
TAPPE_LUCE = [
    ([0.54,0.41],100), ([0.54,0.41],70),
    ([0.58,0.39],45),  ([0.60,0.38],25),
    ([0.64,0.34],10),  ([0.64,0.34],3),
]

# finestra di storia mantenuta per il rilevamento immobilita' (10 min)
FINESTRA_STORIA = 600
def _sfoltisci(lst):
    taglio=time.time()-FINESTRA_STORIA
    lst[:]=[(t,x) for (t,x) in lst if t>=taglio]

stato = {"temp_letto": None, "umid_letto": None,
         "resp": None, "presenza": None, "dist": None, "n_target": None,
         "resp_hist": [], "dist_hist": [], "pres_hist": []}
_mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="controllore-sonno")
_mq.username_pw_set(MQTT_USER, MQTT_PASS)

def _on_connect(c,u,f,rc,p=None):
    c.subscribe("sonno/nodo_letto/sensor/temperatura_letto/state")
    c.subscribe("sonno/nodo_letto/sensor/umidita_letto/state")
    c.subscribe("sonno/nodo_radar/sensor/frequenza_respiratoria/state")
    c.subscribe("sonno/nodo_radar/binary_sensor/presenza_rilevata/state")
    c.subscribe("sonno/nodo_radar/sensor/distanza_bersaglio/state")
    c.subscribe("sonno/nodo_radar/sensor/numero_bersagli/state")
def _on_message(c,u,msg):
    p=msg.payload.decode()
    try:
        v=float(p)
        if msg.topic.endswith("temperatura_letto/state"): stato["temp_letto"]=v
        elif msg.topic.endswith("umidita_letto/state"):    stato["umid_letto"]=v
        elif msg.topic.endswith("frequenza_respiratoria/state"):
            stato["resp"]=v; stato["resp_hist"].append((time.time(),v))
            _sfoltisci(stato["resp_hist"])
        elif msg.topic.endswith("distanza_bersaglio/state"):
            stato["dist"]=v; stato["dist_hist"].append((time.time(),v))
            _sfoltisci(stato["dist_hist"])
        elif msg.topic.endswith("numero_bersagli/state"):
            stato["n_target"]=v
    except ValueError:
        if msg.topic.endswith("presenza_rilevata/state"):
            val = 1 if p.upper()=="ON" else 0
            stato["presenza"]=val
            stato["pres_hist"].append((time.time(),val))
            _sfoltisci(stato["pres_hist"])
_mq.on_connect=_on_connect; _mq.on_message=_on_message
_mq.connect(MQTT_HOST,MQTT_PORT,keepalive=60); _mq.loop_start()

def log(att,az,val,motivo=""):
    _mq.publish(f"sonno/controllo/sensor/{att}_{az}/state",str(val))
    if motivo: _mq.publish(f"sonno/controllo/sensor/{att}_{az}_motivo/state",motivo)
    ts=datetime.now(timezone.utc).astimezone()
    print(f"  [{ts:%H:%M:%S}] {att}.{az}={val}  ({motivo})")
def evento(n):
    _mq.publish("sonno/controllo/sensor/fase/state",n)
    ts=datetime.now(timezone.utc).astimezone(); print(f"  [{ts:%H:%M:%S}] === {n} ===")

def temp_letto():
    if stato["temp_letto"] is not None: return stato["temp_letto"]
    try:
        r=requests.get(f"http://{DAIKIN}/aircon/get_sensor_info",timeout=5)
        for p in r.text.strip().split(","):
            if p.startswith("htemp="): return float(p.split("=")[1])
    except Exception: pass
    return None

def hue(payload):
    requests.put(f"http://{HUE_BRIDGE}/api/{HUE_KEY}/groups/{HUE_GRUPPO}/action",
                 data=json.dumps(payload),timeout=5)
def luci_spegni_davvero():
    hue({"bri":1,"transitiontime":20}); time.sleep(2.5)
    hue({"on":False,"transitiontime":10}); log("luci","spegni",0,"spegnimento reale")
def daikin_accendi(sp):
    requests.get(f"http://{DAIKIN}/aircon/set_control_info?pow=1&mode=3&stemp={sp:.1f}&shum=0&f_rate={VENTOLA}&f_dir=0",timeout=5)
def daikin_spegni():
    r=requests.get(f"http://{DAIKIN}/aircon/get_control_info",timeout=5); sp="24"
    for p in r.text.strip().split(","):
        if p.startswith("stemp="): sp=p.split("=")[1]
    requests.get(f"http://{DAIKIN}/aircon/set_control_info?pow=0&mode=3&stemp={sp}&shum=0&f_rate={VENTOLA}&f_dir=0",timeout=5)

_audio=None
def audio_avvia():
    global _audio
    if not AUDIO_FILE or not Path(AUDIO_FILE).exists():
        log("audio","errore","file_mancante",AUDIO_FILE); return
    _audio=subprocess.Popen(["afplay","-v",str(AUDIO_VOL),AUDIO_FILE],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    log("audio","avvia",1,"rumore bianco pre-sonno")
def audio_ferma():
    global _audio
    if _audio and _audio.poll() is None: _audio.terminate()
    log("audio","ferma",0,"fine pre-sonno")

class Protezione:
    def __init__(self): self.azioni=0; self.ultima=0.0
    def clampa(self,sp): return round(max(TEMP_MIN,min(TEMP_MAX,max(FLOOR_SETPOINT,sp)))*2)/2
    def puo_agire(self):
        if self.azioni>=MAX_AZIONI:
            log("protezione","blocco","max_azioni",f"raggiunte {MAX_AZIONI} azioni/notte"); return False
        dt=time.time()-self.ultima
        if dt<MIN_TRA_AZIONI:
            log("protezione","blocco","min_30_non_passati",f"{int(dt//60)} min < 30 dall'ultima"); return False
        return True
    def segna(self): self.azioni+=1; self.ultima=time.time()

def tramonto(minuti, stop_event=None):
    """
    Sequenza tramonto interrompibile.
    Se stop_event viene impostato durante la sequenza, il thread termina
    senza inviare ulteriori comandi Hue. Lo spegnimento viene gestito
    dalla callback di addormentamento.
    """
    evento("tramonto_inizio")
    passi=len(TAPPE_LUCE); dt=(minuti*60)/passi
    xy0,bri0=TAPPE_LUCE[0]

    if stop_event is not None and stop_event.is_set():
        evento("tramonto_interrotto")
        return

    hue({"on":True,"bri":bri0,"xy":xy0,"transitiontime":20})

    # Attesa iniziale interrompibile
    if stop_event is not None:
        if stop_event.wait(2):
            evento("tramonto_interrotto")
            return
    else:
        time.sleep(2)

    log("luci","tramonto_start",f"bri={bri0}","ambra 40%")

    for xy,bri in TAPPE_LUCE[1:]:
        if stop_event is not None and stop_event.is_set():
            evento("tramonto_interrotto")
            return

        hue({"bri":bri,"xy":xy,"transitiontime":int(dt*10)})

        # Al posto di time.sleep(dt), attesa interrompibile
        if stop_event is not None:
            if stop_event.wait(dt):
                evento("tramonto_interrotto")
                return
        else:
            time.sleep(dt)

    luci_spegni_davvero()
    evento("tramonto_fine")

def attendi_bme(secondi=15):
    """Aspetta che arrivi il primo valore reale dal BME via MQTT,
    per non calcolare il target sul fallback (sonda Daikin, interi)."""
    for i in range(secondi):
        if stato["temp_letto"] is not None:
            return True
        if i==0:
            print("  attendo il primo dato del BME dal letto...")
        time.sleep(1)
    print("  ATTENZIONE: BME non ha pubblicato entro il timeout, uso il fallback Daikin.")
    return False


# ============================================================
#  MONITORAGGIO CONTINUO SONNO/VEGLIA  (v3.1)
# ============================================================
#
# Criterio primario:
#   - presenza del soggetto
#   - immobilita' prolungata stimata dalla distanza
#
# Il battito NON viene usato.
# Il respiro e' solo una conferma opzionale: se manca o ha gap,
# il rilevamento puo' comunque proseguire.
#
# Stati:
#   AWAKE
#   POSSIBLE_SLEEP
#   ASLEEP
#   POSSIBLE_AWAKE
#   UNOBSERVABLE
#
# Regole:
#   - 3 min immobile -> POSSIBLE_SLEEP
#   - altri 3 min immobile -> ASLEEP
#   - movimento mentre ASLEEP -> POSSIBLE_AWAKE
#   - movimento persistente >= 90 s -> AWAKE
#   - perdita target / dati insufficienti -> UNOBSERVABLE
# ============================================================

IMMOB_T1 = 3*60
IMMOB_T2 = 3*60

DIST_WINDOW = 60
DIST_MIN_SAMPLES = 10
DIST_MIN_COVERAGE = 35
DIST_MAX_AGE = 12

DIST_IQR_MAX = 8.0
DIST_ROBUST_RANGE_MAX = 12.0
DIST_MEDIAN_SHIFT_MAX = 8.0

PRES_LOSS_GRACE = 10
CHECK_EVERY = 10
DIAG_EVERY = 60

RESP_MIN = 6
RESP_MAX = 25
RESP_MAX_AGE = 30

WAKE_CONFIRM = 90


def _percentile_sorted(vals, p):
    if not vals:
        return None
    i = int(round((len(vals)-1) * p))
    return vals[max(0, min(len(vals)-1, i))]


def _resp_info():
    """Solo diagnostica: il respiro NON decide lo sleep onset."""
    if not stato["resp_hist"]:
        return "RR assente"
    t, v = stato["resp_hist"][-1]
    age = time.time() - t
    if age > RESP_MAX_AGE:
        return f"RR non recente ({int(age)}s)"
    if RESP_MIN <= v <= RESP_MAX:
        return f"RR plausibile {v:.1f}"
    return f"RR non plausibile {v:.1f}"


def _presenza_ok(finestra_sec):
    """
    Valuta la presenza usando lo stato corrente e le transizioni recenti.
    Brevi perdite del target <= PRES_LOSS_GRACE non resettano il candidato.
    """
    if stato["presenza"] != 1:
        return False, "target assente"

    ora = time.time()
    da = ora - finestra_sec
    eventi = [(t, v) for (t, v) in stato["pres_hist"] if t >= da]

    for i, (t, v) in enumerate(eventi):
        if v != 0:
            continue
        t_on = None
        for t2, v2 in eventi[i+1:]:
            if v2 == 1:
                t_on = t2
                break
        if t_on is None:
            return False, "perdita target recente non quantificabile"
        perdita = t_on - t
        if perdita > PRES_LOSS_GRACE:
            return False, f"target perso per {perdita:.0f}s"

    return True, "presenza OK"


def _classifica_osservazione(finestra_sec=DIST_WINDOW):
    """
    Ritorna:
      ("IMMOBILE", dettaglio)
      ("MOVIMENTO", dettaglio)
      ("UNOBSERVABLE", dettaglio)
    """
    ora = time.time()

    pres_ok, pres_dett = _presenza_ok(finestra_sec)
    if not pres_ok:
        return "UNOBSERVABLE", pres_dett

    da = ora - finestra_sec
    campioni = [(t, v) for (t, v) in stato["dist_hist"] if t >= da]

    if len(campioni) < DIST_MIN_SAMPLES:
        return "UNOBSERVABLE", f"pochi campioni distanza ({len(campioni)})"

    copertura = campioni[-1][0] - campioni[0][0]
    if copertura < DIST_MIN_COVERAGE:
        return "UNOBSERVABLE", f"copertura distanza {copertura:.0f}s<{DIST_MIN_COVERAGE}s"

    eta = ora - campioni[-1][0]
    if eta > DIST_MAX_AGE:
        return "UNOBSERVABLE", f"distanza non recente ({eta:.0f}s)"

    vals = [v for _, v in campioni]
    s = sorted(vals)

    q1 = _percentile_sorted(s, 0.25)
    q3 = _percentile_sorted(s, 0.75)
    p10 = _percentile_sorted(s, 0.10)
    p90 = _percentile_sorted(s, 0.90)

    iqr = q3 - q1
    robust_range = p90 - p10

    if iqr > DIST_IQR_MAX:
        return "MOVIMENTO", f"IQR distanza alto ({iqr:.1f}cm)"

    if robust_range > DIST_ROBUST_RANGE_MAX:
        return "MOVIMENTO", f"range distanza p10-p90 alto ({robust_range:.1f}cm)"

    meta_t = campioni[0][0] + copertura / 2
    prima = [v for t, v in campioni if t <= meta_t]
    seconda = [v for t, v in campioni if t > meta_t]

    if len(prima) >= 3 and len(seconda) >= 3:
        m1 = statistics.median(prima)
        m2 = statistics.median(seconda)
        shift = abs(m2 - m1)
        if shift > DIST_MEDIAN_SHIFT_MAX:
            return "MOVIMENTO", f"cambio posizione persistente ({shift:.1f}cm)"
    else:
        shift = 0.0

    med = statistics.median(vals)

    return "IMMOBILE", (
        f"fermo: dist med={med:.1f}cm IQR={iqr:.1f}cm "
        f"p10-p90={robust_range:.1f}cm shift={shift:.1f}cm; {_resp_info()}"
    )


def monitora_sonno(durata_sec, on_addormentamento=None, stop_flag=None):
    """
    Monitoraggio continuo per tutta la notte.
    La callback on_addormentamento() viene invocata una sola volta,
    al PRIMO passaggio ad ASLEEP.
    """
    evento("monitoraggio_sonno_inizio")

    inizio = time.time()
    fase = "AWAKE"
    fase_pre_unobservable = "AWAKE"

    t_immobile_da = None
    t_movimento_da = None

    gia_addormentato = False
    ultimo_diag = 0.0

    while time.time() - inizio < durata_sec:
        if stop_flag is not None and stop_flag():
            break

        ora = time.time()
        osservazione, dett = _classifica_osservazione(DIST_WINDOW)

        if osservazione == "UNOBSERVABLE":
            if fase != "UNOBSERVABLE":
                fase_pre_unobservable = fase
                log("sonno", "non_osservabile", fase_pre_unobservable, dett)

            fase = "UNOBSERVABLE"
            t_movimento_da = None

        elif osservazione == "IMMOBILE":

            if fase == "UNOBSERVABLE":
                if fase_pre_unobservable in ("ASLEEP", "POSSIBLE_AWAKE"):
                    fase = "ASLEEP"
                    log("sonno", "segnale_recuperato", "ASLEEP",
                        f"radar recuperato senza evidenza di risveglio; {dett}")
                    t_immobile_da = None
                    t_movimento_da = None
                else:
                    fase = "AWAKE"
                    t_immobile_da = None
                    t_movimento_da = None
                    log("sonno", "segnale_recuperato", "AWAKE",
                        f"radar recuperato; riparto dalla veglia; {dett}")

            if fase == "POSSIBLE_AWAKE":
                log("sonno", "movimento_nel_sonno", "ASLEEP",
                    f"movimento terminato prima di {WAKE_CONFIRM}s; {dett}")
                fase = "ASLEEP"
                t_movimento_da = None

            if fase in ("AWAKE", "POSSIBLE_SLEEP"):
                if t_immobile_da is None:
                    t_immobile_da = max(inizio, ora - DIST_WINDOW)

                durata_imm = ora - t_immobile_da

                if fase == "AWAKE" and durata_imm >= IMMOB_T1:
                    fase = "POSSIBLE_SLEEP"
                    log("sonno", "possible_sleep",
                        f"{durata_imm/60:.1f}min",
                        f"candidato sonno: {dett}")

                if fase == "POSSIBLE_SLEEP" and durata_imm >= (IMMOB_T1 + IMMOB_T2):
                    fase = "ASLEEP"
                    log("sonno", "addormentamento_rilevato",
                        f"{durata_imm/60:.1f}min",
                        f"presenza + immobilita confermate; {dett}")

                    if (not gia_addormentato) and on_addormentamento is not None:
                        on_addormentamento()

                    gia_addormentato = True
                    t_movimento_da = None

        elif osservazione == "MOVIMENTO":

            if fase == "POSSIBLE_SLEEP":
                log("sonno", "reset_immobilita",
                    f"{(ora-(t_immobile_da or ora))/60:.1f}min",
                    dett)
                fase = "AWAKE"
                t_immobile_da = None
                t_movimento_da = None

            elif fase == "AWAKE":
                t_immobile_da = None

            elif fase == "ASLEEP":
                fase = "POSSIBLE_AWAKE"
                t_movimento_da = ora
                log("sonno", "possible_awake", "movimento",
                    f"movimento durante il sonno; {dett}")

            elif fase == "POSSIBLE_AWAKE":
                if t_movimento_da is None:
                    t_movimento_da = ora

                durata_mov = ora - t_movimento_da

                if durata_mov >= WAKE_CONFIRM:
                    fase = "AWAKE"
                    t_immobile_da = None
                    t_movimento_da = None
                    log("sonno", "risveglio_movimento",
                        f"{durata_mov:.0f}s",
                        f"movimento persistente; {dett}")

            elif fase == "UNOBSERVABLE":
                # Recupero con movimento: si entra in POSSIBLE_AWAKE (candidato
                # risveglio) SOLO se prima dell'UNOBSERVABLE si stava dormendo.
                # Se si era svegli (AWAKE/POSSIBLE_SLEEP), un movimento non e' un
                # risveglio: si riparte da AWAKE. Questo evita il falso ASLEEP
                # iniziale (avvio -> UNOBSERVABLE -> recupero-mov -> ... -> ASLEEP).
                if fase_pre_unobservable in ("ASLEEP", "POSSIBLE_AWAKE"):
                    fase = "POSSIBLE_AWAKE"
                    t_movimento_da = ora
                    t_immobile_da = None
                    log("sonno", "segnale_recuperato_movimento",
                        "POSSIBLE_AWAKE", dett)
                else:
                    fase = "AWAKE"
                    t_movimento_da = None
                    t_immobile_da = None
                    log("sonno", "segnale_recuperato_movimento",
                        "AWAKE", f"recupero in movimento dalla veglia; {dett}")

        if ora - ultimo_diag >= DIAG_EVERY:
            im2 = (ora - t_immobile_da)/60 if t_immobile_da else 0.0
            mov2 = (ora - t_movimento_da) if t_movimento_da else 0.0

            log("sonno", "stato_rilevatore", fase,
                f"immobile={im2:.1f}min; movimento={mov2:.0f}s; "
                f"osservazione={osservazione}; {dett}")

            ultimo_diag = ora

        time.sleep(CHECK_EVERY)

    evento("monitoraggio_sonno_fine")

def _spegni_rumore_luce(th_tramonto, stop_tramonto=None):
    """Azione del loop: spegne rumore e, se necessario, interrompe e spegne il tramonto."""
    audio_ferma()

    if th_tramonto is not None and th_tramonto.is_alive():
        # Prima blocco il thread del tramonto, cosi' non puo' inviare altri comandi Hue.
        if stop_tramonto is not None:
            stop_tramonto.set()
            # Attendo che il thread esca DAVVERO prima di spegnere. Uso join()
            # senza timeout: le richieste Hue hanno gia' un loro timeout (5s),
            # quindi il thread non resta bloccato indefinitamente, ma cosi' sono
            # certo che non invii piu' comandi dopo il mio spegnimento.
            th_tramonto.join()

        log("luci","spegni_anticipato","addormentamento",
            "tramonto interrotto: soggetto addormentato")

        luci_spegni_davvero()


def notte_attiva(tramonto_min,rumore_min,ore):
    prot=Protezione()
    evento("NOTTE_ATTIVA_inizio")

    inizio_notte = time.time()
    fine_notte = inizio_notte + ore*3600

    attendi_bme(15)

    t0=temp_letto()
    if t0 is None:
        log("clima","errore","temp_non_letta","target non calcolabile")
        target=None
    else:
        grezzo=t0-DELTA_TARGET
        target=prot.clampa(grezzo)
        daikin_accendi(target)
        prot.segna()
        log("clima","temp_partenza",f"{t0:.2f}","temperatura BME al letto all'avvio")
        log("clima","accendi",target,
            f"BME {t0:.2f} - {DELTA_TARGET} = {grezzo:.2f} -> "
            f"setpoint {target} (floor {FLOOR_SETPOINT}, vent {VENTOLA})")

    audio_avvia()

    stop_tramonto = threading.Event()

    th=threading.Thread(
        target=tramonto,
        args=(tramonto_min, stop_tramonto),
        daemon=True
    )
    th.start()

    pre_sonno_lock = threading.Lock()
    pre_sonno_chiuso = {"value": False}

    def _chiudi_pre_sonno(motivo):
        with pre_sonno_lock:
            if pre_sonno_chiuso["value"]:
                return
            pre_sonno_chiuso["value"] = True

        if motivo == "addormentamento":
            _spegni_rumore_luce(th, stop_tramonto)
        else:
            audio_ferma()
            log("audio","timeout",rumore_min,
                f"rumore bianco fermato dopo {rumore_min} min senza sleep onset")

    def _azione_addormentamento():
        _chiudi_pre_sonno("addormentamento")

    def _timeout_audio():
        time.sleep(rumore_min * 60)
        _chiudi_pre_sonno("timeout")

    th_audio = threading.Thread(target=_timeout_audio, daemon=True)
    th_audio.start()

    stop_monitor = threading.Event()

    durata_monitor = max(0, fine_notte - time.time())

    mon = threading.Thread(
        target=monitora_sonno,
        args=(durata_monitor,),
        kwargs={
            "on_addormentamento": _azione_addormentamento,
            "stop_flag": stop_monitor.is_set
        },
        daemon=True
    )
    mon.start()

    evento("fase_notte_termostato")

    if target is None:
        print("  target non disponibile: nessun controllo clima.")
        while time.time() < fine_notte:
            time.sleep(60)

        stop_monitor.set()
        evento("NOTTE_ATTIVA_fine")
        return

    acceso=True
    t_accensione=time.time()
    t_spegnimento=0.0
    MAX_ON=30*60
    tetto_azioni_loggato=False   # per loggare il tetto max_azioni una volta sola

    while time.time() < fine_notte:
        t=temp_letto()
        ora=time.time()

        if t is not None:
            if acceso:
                raggiunto = (t<=target)
                scaduto = (ora-t_accensione>=MAX_ON)

                if raggiunto or scaduto:
                    daikin_spegni()
                    acceso=False
                    t_spegnimento=ora

                    motivo = (
                        f"BME {t:.1f}<=target"
                        if raggiunto
                        else f"tetto {MAX_ON//60} min di accensione raggiunto "
                             f"(BME {t:.1f}, target non raggiunto)"
                    )

                    log("clima","spegni",target,motivo)

            else:
                sopra_soglia = (t>=target+ISTERESI)

                if sopra_soglia:
                    if (ora-t_spegnimento)>=MIN_TRA_AZIONI and prot.azioni<MAX_AZIONI:
                        daikin_accendi(target)
                        prot.segna()
                        acceso=True
                        t_accensione=ora

                        log("clima","riaccendi",target,
                            f"BME {t:.1f}>=target+{ISTERESI}, "
                            f"passati >=30 min dallo spegnimento")

                    elif prot.azioni>=MAX_AZIONI:
                        # logga il tetto UNA sola volta, non a ogni tentativo
                        if not tetto_azioni_loggato:
                            log("protezione","blocco","max_azioni",
                                f"BME {t:.1f}>=target+{ISTERESI}: raggiunte "
                                f"{MAX_AZIONI} accensioni/notte, clima non piu' riattivato")
                            tetto_azioni_loggato=True

                    else:
                        log("protezione","blocco","min_30_da_spegnimento",
                            f"BME {t:.1f}>=target+{ISTERESI} ma solo "
                            f"{int((ora-t_spegnimento)//60)} min dallo spegnimento")

        time.sleep(60)

    stop_monitor.set()

    daikin_spegni()
    log("clima","spegni_fine_notte",0,
        "fine sequenza notturna, spegnimento clima")

    evento("NOTTE_ATTIVA_fine")

def notte_controllo(ore):
    evento("NOTTE_CONTROLLO")
    log("sistema","stato","ambiente_fisso","controllo: nessuna attuazione, solo rilevamento")
    print("  Notte di CONTROLLO: il sistema NON attua. Monitora e logga le fasi per tutta la notte.")
    # Monitoraggio per l'intera notte, SENZA callback: osserva e logga
    # (POSSIBLE_SLEEP, ASLEEP, risvegli) ma non tocca mai nulla.
    monitora_sonno(ore*3600, on_addormentamento=None)
    evento("NOTTE_CONTROLLO_fine")


def main():
    ap=argparse.ArgumentParser()
    g=ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--attiva",action="store_true"); g.add_argument("--controllo",action="store_true")
    ap.add_argument("--tramonto",type=int,default=15)
    ap.add_argument("--rumore",type=int,default=30)
    ap.add_argument("--ore",type=float,default=12.0)
    a=ap.parse_args()
    print("="*60)
    print(f"  CONTROLLORE v{VERSIONE} - notte {'ATTIVA' if a.attiva else 'CONTROLLO'} - {datetime.now().astimezone():%Y-%m-%d %H:%M}")
    print("="*60)
    try:
        if a.attiva: notte_attiva(a.tramonto,a.rumore,a.ore)
        else: notte_controllo(a.ore)
    except KeyboardInterrupt:
        print("\n  Interruzione: spengo audio, luci e clima."); audio_ferma(); luci_spegni_davvero(); daikin_spegni()
    finally:
        _mq.loop_stop(); _mq.disconnect()

if __name__=="__main__": main()
