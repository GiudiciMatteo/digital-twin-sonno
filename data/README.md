# Dati

Questa cartella raccoglie i dati sperimentali del progetto. Tutti gli export
sono in formato CSV semplice con intestazione:

```
timestamp_utc,measurement,field,value
```

I timestamp sono in **UTC**. La **data nel nome del file** indica la notte
secondo la convenzione dello studio (data del risveglio): la notte 17→18
settembre è etichettata `2026-09-18`.

## Diario del sonno

- `diario_sonno.csv` — diario de-identificato (SOGGETTO 1), un record per notte.
  Separatore `;` (formato Excel italiano). Colonne: `Data notte`, `Ora letto`,
  `Ora sveglia`, `Voto sonno (1-5)`, `Condizione` (`baseline`/`attiva`/`controllo`),
  `Ora inizio attuazione`, `Risvegli percepiti`, `Min. per addormentarti`,
  `Fattori`, `Note`. La notte del 19/09 è assente perché esclusa a priori dal
  protocollo.

## Export radar e BME280 — `radar_bme_AAAA-MM-GG.csv`

Un file per notte (12–30 settembre, 18 notti). Contiene le misure del radar
mmWave e del sensore ambientale al letto, distinguibili dal campo
`measurement`:

- `distanza_bersaglio` — distanza del bersaglio (cm);
- `numero_bersagli` — numero di bersagli rilevati;
- `frequenza_respiratoria` — stima della frequenza respiratoria (atti/min);
- `frequenza_cardiaca` — stima della frequenza cardiaca (bpm);
- `presenza_rilevata` — presenza di un bersaglio (1/0);
- `temperatura_letto` — temperatura al letto dal BME280 (°C).

Le stime radar di frequenza cardiaca e respiratoria sono conservate nel
dataset, ma i risultati della Sezione 4.1 della tesi non ne supportano
l'impiego come variabili decisionali del controllore.

## Export del controllore — `controllo_AAAA-MM-GG.csv`

Un file per notte. Contiene gli eventi del ciclo di controllo e della macchina
a stati, nel campo `measurement`:

- **Stato e transizioni FSM**: `sonno_stato_rilevatore` (valori `AWAKE`,
  `POSSIBLE_SLEEP`, `ASLEEP`, `POSSIBLE_AWAKE`, `UNOBSERVABLE`),
  `sonno_non_osservabile`, `sonno_possible_sleep`, `sonno_possible_awake`,
  `sonno_addormentamento_rilevato`, `sonno_movimento_nel_sonno`,
  `sonno_segnale_recuperato(_movimento)`, `sonno_reset_immobilita`,
  `sonno_risveglio_movimento`;
- **Azioni sugli attuatori**: `clima_accendi`/`spegni`/`riaccendi`/`temp_partenza`,
  `luci_tramonto_start`/`spegni`/`spegni_anticipato`, `audio_avvia`/`ferma`/`timeout`;
- **Protezione**: `protezione_blocco`;
- **Fasi / sistema**: `fase`, `sistema_stato`.

Ogni evento `X` è in genere accompagnato da un measurement testuale `X_motivo`
che ne riporta la causa. Lo schema completo del database (organization, bucket,
tag, campi) è in `SCHEMA_influxdb.md`.

## Pulizia e privacy

Gli export contengono **solo** le misure usate nella tesi (radar, BME280,
decisioni del controllore, azioni sugli attuatori). Sono esclusi: diagnostica
dei nodi (RSSI, uptime, versione firmware), indirizzi IP, nomi delle luci Hue
e altri parametri della casa, oltre a credenziali e token.

I dati fisiologici e gli orari di sonno si riferiscono all'autore, che è anche
il soggetto dello studio e ne autorizza la pubblicazione (vedi la sezione
«Dati personali e consenso» nel README principale). Licenza dei dati:
CC BY 4.0 (`LICENSE-data`).
