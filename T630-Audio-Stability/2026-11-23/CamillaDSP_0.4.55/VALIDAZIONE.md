# Validazione della release 0.4.55

**Tino Zappala, Milano - 2026**

## Banco prova isolato dal DAC
CamillaDSP 4.1.3, stato Running a 44.100, 96.000 e 192.000 Hz **mantenendo lo stesso PID**. Durate prove: circa 0,94 / 0,74 / 0,83 secondi, non equivalenti alla durata del silenzio in cuffia. PCM Gate respinge la sequenza DoP 0x05/0xFA. La prima implementazione è stata scartata perché faceva trapelare vecchi frame; la versione definitiva attende lo stato Stalled e svuota i buffer, e supera la verifica dei campioni sentinella.

## Produzione HP t630
Il plugin è stato avviato mediante sincronizzazione esplicita dell'Audio Host dopo che due tentativi di osservazione durante lo stop/start avevano prodotto falsi errori transitori e rollback automatici. La procedura finale ha verificato la release 0.4.55, profilo PEQ ripristinato, DAC RME hw:DAC57850059,0 a 192 kHz, Camilla Running, clipped_samples 0, xrun null e nessun recovery.

Test reale di due brani entrambi a 192 kHz: CamillaDSP **PID 20445** invariato; Squeezelite **PID 20174** invariato. Playlist con 1.160 elementi conservata, indice e posizione ripristinati. Test superato.

## Limiti espliciti
La playlist live conteneva esclusivamente brani a 192 kHz: **non è ancora stato effettuato un cambio reale di frequenza sul DAC in produzione**. Non sono state eseguite in produzione transizioni DSD/DoP, ascolto prolungato né misurazioni analogiche THD+N. Non equiparare assenza di segnalazioni xrun a una prova di assenza assoluta di drop.
