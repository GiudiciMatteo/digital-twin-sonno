# Digital Twin del sonno a ciclo chiuso

<!-- Dopo la pubblicazione su Zenodo, incolla qui il badge del CONCEPT DOI:
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX) -->

Prototipo di ricerca per il monitoraggio del sonno con sensoristica
*contactless* (radar mmWave 60 GHz) e attuazione automatica dell'ambiente
(luci, clima, audio) in un ciclo chiuso fisico → digitale → fisico.

Repository a corredo della tesi di laurea triennale. Il sistema osserva lo
stato del soggetto, lo rappresenta digitalmente — rendendo **esplicita** anche
l'incertezza osservativa (stato `UNOBSERVABLE`) — decide entro vincoli di
sicurezza e modifica l'ambiente, senza intervento manuale durante la notte.

> **Natura del progetto.** È un prototipo sperimentale, non un dispositivo
> clinico. Lo stimatore sonno/veglia non è uno stadiatore clinico e non
> ricostruisce gli stadi del sonno.

## Architettura

- **Nodi di sensing (ESPHome)** — `firmware/`
  - `nodo-radar.yaml`: radar Seeed MR60BHA2 su XIAO ESP32C6 (presenza, distanza,
    frequenze cardio-respiratorie, numero bersagli). Pubblica via MQTT.
  - `nodo-letto.yaml`: BME280 (temperatura/umidità/pressione al letto).
  - `nodo-radar-debug.yaml` / `nodo-radar-versione.yaml`: varianti diagnostiche
    (debug UART RX; query di versione firmware del modulo radar).
- **Raccolta dati** — `src/collettore.py`: sottoscrive i topic MQTT e scrive i
  valori grezzi su InfluxDB.
- **Poller attuatori** — `src/poller.py`: interroga Daikin e Philips Hue e
  pubblica il loro stato con lo stesso schema MQTT.
- **Controllore notturno** — `src/controllore.py`: cuore del sistema. Due livelli,
  come da impostazione metodologica della tesi:
  - **Protezione** (vincoli fissi, vincono sempre): setpoint entro limiti e
    quantizzato, intervallo minimo tra azioni, numero massimo di attivazioni/notte;
  - **Politica** (decide le azioni): rampa luci pre-sonno, rumore bianco,
    termostato delicato sul BME al letto, e chiusura del loop
    sull'addormentamento rilevato dalla macchina a stati.
- **Attuatori** — `src/attuatori.py`: come eseguire le singole azioni (Hue, Daikin, audio).
- **Utility** — `src/diagnosi_radar.py` (copertura radar), `src/prova_gradino.py`
  (identificazione del modello termico), `src/test_luce.py` (rampa luce).

## Struttura del repository

```
firmware/   configurazioni ESPHome dei nodi + secrets.yaml.example
src/        codice Python di runtime + config.ini.example
data/       dati sperimentali (diario + export per-notte) + schema del database
analysis/   script di analisi riproducibile sui dati pubblici (vedi analysis/README.md)
sound/      posizione del file di rumore bianco (non incluso: copyright)
```

## Dati personali e consenso alla pubblicazione

I dati fisiologici (frequenza cardiaca e respiratoria, distanza e presenza
rilevate dal radar) e gli orari di sonno contenuti in questo repository si
riferiscono all'autore, Matteo Giudici, che è anche il soggetto dello studio
("SOGGETTO 1"). In quanto interessato e titolare di tali dati, l'autore ne
autorizza espressamente la pubblicazione e il riuso secondo la licenza
indicata (CC BY 4.0 per i dati). I dati non riguardano terzi.

## Configurazione

Credenziali e segreti **non** sono versionati. Prima dell'uso:

```bash
# runtime
cp src/config.ini.example      src/config.ini           # e compilare i valori
# firmware
cp firmware/secrets.yaml.example firmware/secrets.yaml   # e compilare i valori
```

`config.ini` deve restare nella **stessa cartella** degli script (`src/`):
i programmi lo cercano accanto a sé stessi.

## Requisiti

```bash
pip install -r requirements.txt
```

## Esecuzione

```bash
# 1. flash dei nodi con ESPHome (richiede firmware/secrets.yaml)
# 2. avvio della raccolta dati
python3 src/collettore.py
# 3. avvio del poller attuatori (Daikin + Hue)
python3 src/poller.py
# 4. notte sperimentale
python3 src/controllore.py --attiva      # notte attiva (sistema attua)
python3 src/controllore.py --controllo   # notte di controllo (solo monitoraggio)
```

## Dati e riproducibilità

La cartella `data/` contiene il diario del sonno, gli export per-notte di radar,
BME280 e controllore, e lo schema del database (`data/SCHEMA_influxdb.md`).
I dettagli dei file e del formato sono in `data/README.md`. I valori notte per
notte permettono di ricostruire le statistiche aggregate del Capitolo 4.

Il repository include lo script di analisi `analysis/01_radar_quality_and_position.py`,
che riproduce dai soli dati pubblici la caratterizzazione di continuità e
posizionamento del radar (Capitolo 4). I restanti passaggi della pipeline si
appoggiano a riferimenti fisiologici (Withings Sleep Analyzer, Apple Health)
che costituiscono dati personali relativi alla salute e non sono pertanto
pubblicati; i valori notte per notte necessari all'audit dei risultati sono
riportati nelle tabelle supplementari dell'Appendice A della tesi.

**Sicurezza e privacy dei dati.** Gli export pubblicati sono filtrati per
contenere solo le misure usate nella tesi (radar, BME280, decisioni del
controllore, azioni sugli attuatori); sono esclusi diagnostica dei nodi,
indirizzi IP, nomi delle luci Hue e altri parametri della casa, oltre a
credenziali e token. I file `config.ini` e `secrets.yaml` reali non sono
versionati (`.gitignore`); al loro posto ci sono i rispettivi `*.example`.

## Licenze

- Codice (`src/`, `firmware/`): **MIT** — vedi `LICENSE`.
- Dati (`data/`): **CC BY 4.0** — vedi `LICENSE-data`.

## Citazione

Vedi `CITATION.cff`. Il repository è archiviato su Zenodo: cita il **concept
DOI** (quello indicato come *"Cite all versions"*, che punta sempre all'ultima
versione) insieme al link di questo repository.
