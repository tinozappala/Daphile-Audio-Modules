# Ottimizzazione scanner e guardiano — 23 novembre 2026

**Tino Zappala, Milano - 2026**

Il file `CPU_Affinity/audio_affinity_guard.py` di questa revisione sostituisce la prima snapshot del guardiano, mantenendo l'avvio automatico tramite OpenRC.

- LMS e controller: CPU 0–1; guardiano CPU 1 con `nice +10`.
- Scanner `scanner.pl`, anche se avviato da Perl: CPU 0, `nice +10`, `ionice -c2 -n7` (best-effort classe 2, priorità 7).
- SoX/FFmpeg di riproduzione: CPU 0–1, nessuna variazione del loro `nice -10` o I/O.
- Squeezelite e pipeline PCM: CPU 3, priorità FIFO preesistenti conservate.
- IRQ xHCI del DAC: CPU 2, FIFO 40 conservato.

Test eseguiti: simulazione della policy su processo innocuo, verifica readback `nice=10` e `ionice=best-effort/7`, compilazione Python, riavvio del solo servizio OpenRC, verifica di affinità di LMS/Squeezelite/CamillaDSP/IRQ. SHA-256 del file nuovo sul dispositivo: `f7b36b2e3424dde7780b0ff8bd8d5ae97da150711e25fcb5b3fb0d8ba35fed5b`.

**Limite misurato:** lo scheduler di `/dev/sda` è `none` e la libreria è su NAS via SMB. `ionice` è configurato e verificabile, ma non garantisce di limitare effettivamente le letture di rete SMB. Non è stato effettuato un test scanner reale mentre un rescanning era in corso.

## CamillaDSP 0.4.54 — solo candidata

La versione **0.4.54** con polling PCM adattivo (2 ms ai rate elevati, 3 ms a 176,4/192 kHz, 4 ms a 88,2/96 kHz e 6 ms ai rate inferiori) è stata preparata e ha superato i test offline. SHA-256 del pacchetto candidato: `d1ea771e66ba4844e141c5eae81b6c7b0bf1b3c52e69998236883669a0d9169d`. **Non risulta installata**, perché l'operazione di bypass e cambio release richiede autorizzazione e non è stata ancora eseguita.

Benchmark micro-loop guard locale @192 kHz: intervallo 2 ms ~19,5% CPU, 3 ms ~12,6%, 4 ms ~10,2%, 6 ms ~7,5%. Baseline complessiva reale del controller prima dell'ottimizzazione: 27,8–28,0% di un core, misurata in tre finestre di 6 s. Nessun risultato post-installazione è ancora disponibile.

## cpuset e isolamento rigido

Il kernel runtime registra i tipi filesystem `cgroup` e `cgroup2`, ma `/proc/cgroups` non elenca controller e non esiste alcun mount cgroup o `cpuset.cpus.effective`. `isolcpus` e `nohz_full` non sono attivati dalla riga di avvio. Nessun mount o cambio di parametri kernel è stato eseguito; serviranno verifiche di supporto e benchmark prima di proporre un isolamento più rigido.
