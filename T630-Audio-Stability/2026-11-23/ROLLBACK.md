# Recupero e rollback (procedura conservativa)

**Tino Zappala, Milano - 2026**

Questa directory è uno snapshot di sorgenti **non installabile direttamente**. Non sostituire file singoli dentro una release con hash controllato: creare una nuova release approvata attraverso il workflow dell'Audio Host.

1. Prima di cambiare il routing audio, verificare che il PCM non sia in una sessione di handoff non risolta. Conservare volumi, coda, posizioni e configurazione USB.
2. Il broker sul t630 mantiene la release CamillaDSP precedente e il pacchetto di rollback. Usare l'operazione di rollback del gestore, rispettando i suoi controlli, non cancellare i registri per forzare l'avvio.
3. Il supervisore CPU è un servizio OpenRC indipendente: `rc-service daphile-audio-affinity status` e `rc-service daphile-audio-affinity stop` consentono diagnosi senza interrompere il servizio audio. Una modifica al suo script deve avere backup, compilazione Python, test in esecuzione e validazione del runlevel.
4. In caso di assenza DSP, usare l'API `/api/audio/status` e controllare `enabled`, `mode`, `recovery_required` prima di tentare un recupero, evitando ripetizioni cieche. Per DSD mantenere il bypass nativo.
5. Non alterare l'affinità IRQ o le priorità FIFO sulla base del numero numerico dell'IRQ: il numero può cambiare al reboot.

I backup di runtime sono conservati sul dispositivo; **non** fanno parte di questo repository pubblico. Testare sempre la riproduzione dopo modifiche alla pipeline.
