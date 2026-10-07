# Script di analisi

Questa cartella include lo script `01_radar_quality_and_position.py` (con il
modulo di supporto `common.py`), che riproduce **interamente a partire dai
soli dati pubblici del repository** la caratterizzazione di continuità e di
posizionamento del radar discussa nel Capitolo 4 della tesi (mediana della
distanza valida pari a circa 88,6% nella configurazione a 71 cm e circa 77,3%
in quella a 100 cm).

## Esecuzione

```bash
pip install -r analysis/requirements-analysis.txt
python analysis/01_radar_quality_and_position.py --data-dir data
```

Lo script legge gli export pubblici `data/radar_bme_*.csv` e
`data/diario_sonno.csv` e produce le metriche di continuità notte per notte.

## Perché gli altri passaggi non sono inclusi

La pipeline di analisi completa della tesi comprende ulteriori passaggi che si
appoggiano a dati **non inclusi** in questo repository: in particolare i
riferimenti fisiologici del *Withings Sleep Analyzer* e di *Apple Health*, che
costituiscono **dati personali relativi alla salute** e non vengono pertanto
condivisi. Per questi passaggi la tesi documenta i metodi e i formati di input
richiesti. I conteggi degli eventi del sistema di controllo sono inoltre
riportati notte per notte nelle tabelle supplementari della tesi (Appendice A),
direttamente verificabili.

## Nota di tracciabilità

Lo script qui incluso riproduce fedelmente, a partire dai dati e dai metodi
documentati nella tesi, l'analisi descritta nel testo. Non è presentato come
copia bit-per-bit di eventuali script temporanei usati durante l'esplorazione.
