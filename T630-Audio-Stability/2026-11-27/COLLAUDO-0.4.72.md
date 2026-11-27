# Collaudo DaphileCamillaDSP 0.4.72 sul t630

RME ADI-2 DAC FS, Squeezelite Loopback a 80 ms in otto periodi, CamillaDSP 5.0.0-beta.2 con blocchi da 2048 frame e obiettivo di 4096. La .72 assegna a ogni flusso PCM il proprio FIFO e attende il rilascio effettivo del vecchio ingresso e del playback ALSA prima di ritirare la cattura.

Sul kernel 6.12.117-rt23, la .71 aveva prodotto XRUN USB in 10/12 cambi 96→48 kHz, in 4/11 inversi e nello STOP finale. Nella .72 entrambi i tracepoint hanno contato zero XRUN nelle 40 selezioni (20 discendenti, 19 ascendenti a caldo, più l'avvio) e nello STOP. Nessuna perdita nelle statistiche per CPU di entrambi gli esperimenti. IRQ xHCI osservato FIFO40 su CPU2.

Anche nella successiva serie di dieci FLAC con equalizzatore attivo entrambi i tracepoint hanno contato zero XRUN, compresi cinque minuti a 192 kHz. Le nove selezioni a caldo hanno impiegato da 2,731 a 3,252 s fino alla terza conferma software. La prima selezione da fermo ha impiegato 8,609 s fino alla stessa conferma. Coda, posizione, profilo e DAC ripristinati.

Sul kernel stock 6.12.30 il ritorno digitale pre-FX ha riconosciuto il brano a 352,8 kHz entro 1,980 s durante un cambio manuale da 176,4 kHz. I precedenti limiti erano 1,856 s sulla .71 e 0,565 s su Daphile puro. Su questa coppia Squeezelite e Camilla hanno conservato il proprio processo. Il primo audio entro 5 s da fermo resta da qualificare.

SHA-256 dell'archivio del modulo .72: `acab2f0797c8bc871a2454a411ba68a506a61fd986730889ed00962d0c968b52`.
