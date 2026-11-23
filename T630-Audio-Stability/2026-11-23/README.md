# Daphile t630 — snapshot audio e CPU del 23 novembre 2026

**Tino Zappala, Milano - 2026**

Snapshot dei **33 file testuali effettivamente acquisiti** dal dispositivo HP t630, firmware Daphile 25.05-b251550, kernel PREEMPT_RT 6.12.30. I file `CamillaDSP_0.4.53/` sono il backend Python e gli asset statici della release attiva **DaphileCamillaDSP 0.4.53**, non il firmware o un pacchetto Daphile completo. `CPU_Affinity/` contiene il servizio OpenRC e il supervisore di affinità persistente.

La release 0.4.53 mantiene la tolleranza **massimo 12 s** ai timeout LMS della 0.4.52, applicabile solo quando identità DAC/player, processo DSP e parametri ALSA locali rimangono coerenti. Corregge inoltre il limite della playlist: acquisizione integrale fino a 8192 elementi, con rifiuto delle risposte incomplete; è stata verificata su una coda di **1160 brani**. Il passaggio DSD/DoP usa bypass nativo senza conversione.

La topologia attuale usa CPU 0–1 per LMS e servizi, CPU 2 per l'IRQ xHCI, CPU 3 per Squeezelite e pipeline PCM. Il guardiano gira su CPU 1, con nice +10. Non usa `isolcpus`/`nohz_full` e quindi **non equivale a isolamento completo del kernel**. Non è previsto alcun aumento arbitrario della priorità dell'IRQ.

## Verifiche registrate
- Reboot OpenRC effettivo e ritorno dei servizi audio con identificativo di boot cambiato.
- Servizio `daphile-audio-affinity` registrato nel runlevel `default` e riavviato senza riavviare il player.
- LMS avviato via Perl correttamente vincolato alle CPU 0–1.
- Interrupt USB xHCI IRQ/FIFO 40 vincolato alla CPU 2; Squeezelite FIFO 45 sulla CPU 3; pipeline CamillaDSP/ALSA FIFO 10 sulla CPU 3.
- API CamillaDSP in stato `Running`, 192 kHz, 0 clipped samples e nessun xrun riportato nel controllo finale.
- Precedente release 0.4.52 conservata per rollback; backup precedenti mantenuti sul dispositivo.

## Ambito del deposito e sicurezza

Questa cartella contiene sorgenti testuali e documentazione; **non** include credenziali, token, configurazioni private, profili utente salvati, librerie musicali, file di stato/diagnostica privata, firmware Daphile o il binario terzo di CamillaDSP. Non è un installer eseguibile: la ricostruzione del pacchetto deve rispettare gli hash del motore upstream e il sistema di release approvata dell'Audio Host. Vedi `MANIFEST.sha256`, `VALIDAZIONE.md`, `ROLLBACK.md` e `LICENZE.md`.

**Stato di questa snapshot:** base funzionante; la riduzione delle priorità dello scanner, l'ottimizzazione dei controller e i test comparativi di `cpuset` sono attività separate. Ogni modifica successiva richiede nuova prova e nuovo snapshot.
