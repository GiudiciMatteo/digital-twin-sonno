# Digital Twin del sonno a ciclo chiuso

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
data/       dati sperimentali + SCHEMA_influxdb.md (struttura del database)
sound/      posizione del file di rumore bianco (non incluso: copyright)
```

## Configurazione

Credenziali e segreti **non** sono versionati. Prima dell'uso:

```bash
# runtime
cp src/config.ini.example      src/config.ini      # e compilare i valori
# firmware
cp firmware/secrets.yaml.example firmware/secrets.yaml  # e compilare i valori
```

`config.ini` deve restare nella **stessa cartella** degli script (`src/`):
i programmi lo cercano accanto a sé stessi.

## Requisiti

```bash
pip install -r requirements.txt
```

(Vedi `requirements.txt`: prima di un rilascio citabile, fissare le versioni
esatte con `pip freeze`.)

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

Il diario del sonno è de-identificato (`data/diario_sonno.csv`). La struttura
del database (organization, bucket, measurements, tag, campi) e le istruzioni
per ricrearlo sono in `data/SCHEMA_influxdb.md`. Gli export InfluxDB di radar e
controllore possono essere aggiunti in `data/` (vedi `data/README.md`).

Gli script di analisi che generano le figure e le statistiche della tesi
**non sono inclusi** in questo repository: esso documenta il sistema (firmware,
raccolta dati, controllo a ciclo chiuso) e ne rende disponibili i dati e lo
schema, non la pipeline di elaborazione.

**Sicurezza.** Da questo repository sono stati esclusi credenziali, token,
password e identificativi di rete dei dispositivi. I file `config.ini` e
`secrets.yaml` reali non sono versionati (`.gitignore`); al loro posto ci sono
i rispettivi `*.example`.

## Licenze

- Codice (`src/`, `firmware/`): **MIT** — vedi `LICENSE`.
- Dati e figure (`data/`, `figures/`): **CC BY 4.0** — vedi `LICENSE-data`.

## Citazione

Vedi `CITATION.cff`. Al momento del rilascio, collegare il repository a Zenodo
per ottenere un DOI citabile e inserirlo qui e nella tesi.
