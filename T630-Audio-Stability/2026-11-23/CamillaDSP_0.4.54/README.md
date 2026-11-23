# DaphileCamillaDSP 0.4.54 — sorgente effettivamente installato

**Tino Zappala, Milano - 2026**

Release attiva sul t630 il **23 novembre 2026**, integrata nell'Audio Host di Daphile, mantenendo bypass nativo DSD/DoP e protezione LMS ai timeout con verifica locale del PCM. Include il fix delle playlist oltre 1.000 elementi (test reale: 1.160).

## Ottimizzazione del controller

Il controllo ALSA `monitor_pcm` usa un polling dipendente dalla frequenza:
- 44,1/48 kHz: 6 ms
- 88,2/96 kHz: 4 ms
- 176,4/192 kHz: 3 ms
- ≥352,8 kHz: 2 ms

Il controllo locale non esegue chiamate LMS ed è progettato per rilevare variazioni dello stream o dell'identità ALSA. Le frequenze elevate mantengono il ritardo di 2 ms precedente.

## Misura in esecuzione sul t630 (PCM a 192 kHz)

| Misura | 0.4.53 | 0.4.54 |
|---|---:|---:|
| CPU controller, mediana di tre finestre da 6 s | 28,17% | 22,00% |
| Miglioramento | — | **21,9%** |
| DSP | Running | Running |
| Campioni in clipping | 0 | 0 |
| Xrun comunicati | Nessuno | Nessuno |

Valori raccolti dall'auditor durante il rollout. Il test non misura rumore o jitter analogico e non equivale a un benchmark statistico di lungo periodo.

## Integrità e ripristino

Pacchetto approvato integrale (include componenti terze NON pubblicate qui): **SHA-256 `d1ea771e66ba4844e141c5eae81b6c7b0bf1b3c52e69998236883669a0d9169d`**, dimensione **3033660 byte**.

Questa cartella contiene tutti i 37 file di testo del pacchetto, compresi manifest di installazione, frontend e registrazione del plugin. Il binario CamillaDSP `backend/bin/camilladsp` è escluso: il suo SHA-256 è `e69090287d9af87eeb5720d59cf8ebf8cab1ed3adbe24de1dd60b66a47f5385b`.

Vedi `MANIFEST.sha256` per i 37 file testuali. Il sorgente è conservato come snapshot fedele, senza modifiche per aggiungere intestazioni, così gli hash rimangono validi. Questa cartella non include preferenze, profili privati, token o dati della libreria.

Rollback locale: backup della release **0.4.53** su t630 e catalogo approvazioni salvato prima del rollout. Per recuperare, usare il gestore Audio Host con verifica di routing e hash; non sostituire file isolati di una release in esercizio.

Le licenze dei singoli componenti sono quelle applicabili a ciascun file; l'attribuzione Tino Zappala riguarda gli apporti originali e non rilascia sotto MIT componenti upstream GPL/MPL.
