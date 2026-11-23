# Validazione — 23 novembre 2026

**Tino Zappala, Milano - 2026**

Ambiente: HP t630, kernel 6.12.30-daphile PREEMPT_RT, RME ADI-2 DAC collegato via USB.

## Test della release
- 0.4.52: 9 verifiche offline (grace dei timeout LMS, scadenza, fail-closed se PCM cambia/DAC non coincide, ripresa delle osservazioni, compilazione, hash).
- 0.4.53: 8 verifiche di regressione, inclusa acquisizione reale in sola lettura di una playlist a 1160 tracce, rifiuto di risposte incomplete e persistenza della protezione LMS.
- Live dopo fix playlist: 6 osservazioni consecutive (2 s ciascuna), `pcm-active`, motore `Running`, 192000 Hz, zero campioni clipped, nessun xrun registrato.
- Thread attivi: Squeezelite FIFO45/core3; IRQ xHCI FIFO40/core2; CamillaDSP, PCM Gate e arecord FIFO10/core3.
- LMS via `/usr/bin/perl`: il riconoscimento del processo è stato corretto e l'affinità verificata {0,1}.
- OpenRC attivo al riavvio. L'ultima modifica al guardiano (identificazione Perl e self-pin core1) è stata testata a caldo, senza un ulteriore reboot.

## Limiti dei test

Il report non certifica assenza di jitter analogico, né dimostra un miglioramento misurato della qualità sonora. I timeout LMS e la gestione PCM sono stati testati per casi simulati; non è stato svolto un fault injection durante streaming reale continuativo. Non è stata misurata una sessione con scanner sotto carico dopo il fix. Il modulo CamillaDSP **non** è attivo su DSD/DoP, che resta bypass nativo. L'appartenenza di kernel thread e IRQ non audio a CPU 2/3 non è ancora completamente isolata.

## Riferimenti di release
- DaphileCamillaDSP **0.4.53**, SHA-256 del pacchetto approvato: `682a881f8353b81092b2e8f2deb01f5d05baab218a4bc80beeba0b3ef3eda2fe`.
- Release precedente **0.4.52**: `8b291abe2b09dbc4ca1f369fafff52bf0943e58144ccdb31e8b01dbd1f33cc8a`.
- Sorgenti e servizi del dispositivo: vedi `MANIFEST.sha256`.
