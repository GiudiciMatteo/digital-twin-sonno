# Struttura dati InfluxDB

Tutti i dati del progetto sono salvati in **InfluxDB 2.x**. Questo documento
descrive l'organizzazione del database, così da poter interpretare gli export
CSV e, volendo, ricreare l'intera pipeline di raccolta.

## Organizzazione generale

- **Organization**: `tesi`
- **Bucket**: `sonno`
- **Timestamp**: tutti in **UTC**. L'ora locale serve solo a calcolare
  l'etichetta della notte.

## Pipeline di scrittura

```
nodi ESPHome  ─┐
poller (Daikin/Hue) ─┤→  MQTT  →  collettore.py  →  InfluxDB (bucket "sonno")
controllore.py ─┘
```

Il collettore sottoscrive `sonno/#` e scrive ogni messaggio senza filtrarlo
(i dati sono grezzi; il filtraggio avviene a valle, in analisi).

## Convenzione dei topic MQTT

```
sonno/<nodo>/<tipo>/<grandezza>/state
```

Il collettore ne ricava: `measurement = <grandezza>`, tag `nodo = <nodo>`,
tag `tipo = <tipo>` (es. `sensor`, `binary_sensor`).

## Tag presenti su OGNI punto

| Tag          | Valori / significato |
|--------------|----------------------|
| `nodo`       | sorgente: `nodo_radar`, `nodo_letto`, `daikin`, `hue`, `controllo`, `azioni` |
| `tipo`       | tipo di componente (`sensor`, `binary_sensor`) |
| `notte`      | `YYYY-MM-DD` = **data del risveglio** (convenzione Withings). Cutoff: i dati dopo le 12:00 locali appartengono alla notte del giorno successivo |
| `condizione` | `baseline` \| `attiva` \| `controllo` |

## Campi (fields)

| Field        | Tipo   | Quando |
|--------------|--------|--------|
| `value`      | float  | grandezze numeriche; `ON/TRUE→1.0`, `OFF/FALSE→0.0` |
| `value_text` | string | valori testuali (es. stati FSM, motivazioni degli eventi) |

## Measurements per sorgente

### `nodo_radar` (Seeed MR60BHA2)
| measurement | note |
|---|---|
| `frequenza_respiratoria` | atti/min |
| `frequenza_cardiaca` | bpm |
| `distanza_bersaglio` | distanza del bersaglio (unità come fornita dal modulo) |
| `numero_bersagli` | conteggio |
| `presenza_rilevata` | 1/0 (binary_sensor) |

### `nodo_letto` (BME280)
| measurement | note |
|---|---|
| `temperatura_letto` | °C |
| `umidita_letto` | % |
| `pressione` | hPa |

(Entrambi i nodi pubblicano anche diagnostica ancillare: `wifi_rssi`, `uptime`,
versione ESPHome, indirizzo IP.)

### `daikin` (via `poller.py`)
| measurement | note |
|---|---|
| `temperatura_interna_split` | °C (sonda dello split, in quota) |
| `temperatura_esterna` | °C |
| `umidita_interna_split` | % |
| `frequenza_compressore` | Hz — cosa fa davvero la macchina |
| `stato_acceso` | 1/0 |
| `modalita` | 2=deumid, 3=freddo, 4=caldo, 6=vent |
| `setpoint` | °C — **ingresso del modello termico** |
| `velocita_ventola` | testo: `A` auto, `B` silenzioso, `3`..`7` livelli |

### `hue` (via `poller.py`)
| measurement | note |
|---|---|
| `<nome_luce>_accesa` | 1/0 |
| `<nome_luce>_luminosita` | 1–254 |
| `<nome_luce>_temp_colore_mired` | mired |
| `luci_accese_totale` | conteggio |

### `controllo` (via `controllore.py`)
Eventi del ciclo di controllo e della macchina a stati. Ogni evento `X` è in
genere accompagnato da un measurement testuale `X_motivo` che ne spiega la causa.

- **Stato FSM**: `sonno_stato_rilevatore` (valori: `AWAKE`, `POSSIBLE_SLEEP`,
  `ASLEEP`, `POSSIBLE_AWAKE`, `UNOBSERVABLE`)
- **Transizioni**: `sonno_non_osservabile`, `sonno_segnale_recuperato`,
  `sonno_segnale_recuperato_movimento`, `sonno_movimento_nel_sonno`,
  `sonno_possible_sleep`, `sonno_possible_awake`, `sonno_addormentamento_rilevato`,
  `sonno_reset_immobilita`, `sonno_risveglio_movimento`
- **Clima**: `clima_temp_partenza`, `clima_accendi`, `clima_spegni`,
  `clima_riaccendi`, `clima_spegni_fine_notte`
- **Luci**: `luci_tramonto_start`, `luci_spegni`, `luci_spegni_anticipato`
- **Audio**: `audio_avvia`, `audio_ferma`, `audio_timeout`
- **Protezione**: `protezione_blocco` (+ `_motivo`: es. max azioni/notte,
  intervallo minimo non trascorso)
- **Fasi / sistema**: `fase`, `sistema_stato`

### `azioni` (via `attuatori.py`)
Azioni di basso livello registrate quando gli attuatori vengono comandati
direttamente (es. durante `prova_gradino.py`: `prova_termica_fase`).

## Ricreare il database

1. Installare InfluxDB 2.x; creare org `tesi` e bucket `sonno`; generare un token.
2. Inserire URL/token/org/bucket in `src/config.ini` (sezione `[influx]`).
3. Avviare `collettore.py`, quindi i nodi ESPHome e `poller.py`; per le notti
   sperimentali, `controllore.py`.

## Formato degli export

Gli export CSV sono in formato **Flux annotato** (righe iniziali `#datatype`,
`#group`, `#default`, poi l'header con `_time`, `_value`, `_field`,
`_measurement` e i tag `condizione`, `nodo`, `notte`, `tipo`).
