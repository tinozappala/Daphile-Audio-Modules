# CamillaDSP 0.4.54 — candidata NON INSTALLATA

**Tino Zappala, Milano - 2026**

Questa directory contiene il **delta esatto** tra `native_audio.py` della release **0.4.53 installata** sul t630 e la candidata **0.4.54**, preparata e verificata offline. La modifica aggiorna il polling locale ALSA; NON modifica la logica di timeout LMS o la gestione del bypass DSD/DoP.

## Riproduzione della modifica

1. Partire dalla snapshot `CamillaDSP_0.4.53/` del 23 novembre 2026, senza includere nella pubblicazione il motore binario di terze parti.
2. Applicare `native_audio.py.patch` al file `native_audio.py`.
3. Incrementare `__version__ = '0.4.53'` a `'0.4.54'` in `peq/__init__.py`.
4. Nel manifest del pacchetto originale `install.xml`, sostituire `<version>0.4.53</version>` con `<version>0.4.54</version>`.
5. Ricostruire un archivio approvato, verificando hash di ogni file e conservando la release precedente per il rollback.

Pacchetto candidato **presente sul t630, non pubblicato qui**: `/mnt/hdd/system/daphile-maintenance/20261123-controller-poll/DaphileCamillaDSP-0.4.54.zip`.

SHA-256 candidato: `d1ea771e66ba4844e141c5eae81b6c7b0bf1b3c52e69998236883669a0d9169d`; 3.033.660 byte.

## Test e stato

Test offline: compilazione Python, 11 casi di intervallo per sample-rate, integrità dell'archivio, regressioni su timeout LMS e playlist. Benchmark del solo guard ALSA a 192 kHz: circa 19,5% CPU a 2 ms contro 12,6% a 3 ms, senza divergenze del flusso durante la misura. Baseline controller in produzione 0.4.53: 27,8–28,0% di un core. **Non sono ancora disponibili benchmark dell'intero controller 0.4.54 né un collaudo live del nuovo polling.**

La richiesta di bypass controllato per il deployment è stata inviata al pannello di approvazione; fino alla sua esecuzione la **release attiva è 0.4.53**.
