# Audio

Il file di rumore bianco usato nella fase pre-sonno **non è incluso** nel
repository per ragioni di copyright.

Per usare il sistema:

1. procurati un tuo file audio di rumore bianco / pioggia (es. `white_noise.mp3`);
2. collocalo in questa cartella;
3. imposta in `src/config.ini` la voce `[audio] file` con il **percorso assoluto**
   del file.

Il codice (`attuatori.py`, `controllore.py`) riproduce il file indicato in
configurazione tramite `afplay` (macOS); non dipende dal nome né dalla
posizione all'interno del repository.
