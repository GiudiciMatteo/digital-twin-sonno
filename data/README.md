# Dati

Questa cartella raccoglie i dati sperimentali del progetto.

## Contenuto

- `diario_sonno.csv` — diario del sonno de-identificato (SOGGETTO 1), un record
  per notte. Separatore `;` (formato Excel italiano). Colonne:
  `Data notte`, `Ora letto`, `Ora sveglia`, `Voto sonno (1-5)`, `Condizione`
  (`baseline`/`attiva`/`controllo`), `Ora inizio attuazione`,
  `Risvegli percepiti`, `Min. per addormentarti`, `Fattori`, `Note`.
  La notte del 19/09 è assente perché esclusa a priori dal protocollo.

## Dati da aggiungere

I seguenti export sono necessari per riprodurre integralmente le analisi del
Capitolo 4 e vanno collocati qui (sono esportazioni InfluxDB in formato Flux CSV):

- export per-notte del radar (distanza, presenza, frequenze cardio-respiratorie,
  numero bersagli);
- log completo del controllore (stati FSM, blocchi di protezione, eventi clima/luci/audio);
- export temperatura al letto (BME280).

Questi file non contengono credenziali: sono risultati di query. Prima di
aggiungerli, verificare comunque che non includano colonne con host o token.
