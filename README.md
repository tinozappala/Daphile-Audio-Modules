# Moduli audio per Daphile

Sorgenti delle estensioni mantenute da Tino Zappala per il progetto Daphile. Le directory di AutoTune, AudioHost e RAM riproducono i percorsi dei componenti nel sistema di destinazione. Le directory dei plugin seguono il formato di Lyrion Music Server.

| Componente | Funzione | Sorgenti |
| --- | --- | --- |
| Camilla 0.4.48 | Profili di ascolto, confronto A/B a volume allineabile, filtri separati per canale e importazione REW. | [DaphileCamillaDSP](DaphileCamillaDSP/) |
| RME 0.5.7 | Controlli, equalizzazione e memorie dei dispositivi ADI-2 compatibili. | [DaphileRME](DaphileRME/) |
| AutoTune B4e | Regolazione del player, gestione delle risorse e strumenti di misura. | [AutoTune](AutoTune/) |
| RAM A7 | Caricamento progressivo della musica in RAM e integrazione con la coda di LMS. | [RAM](RAM/) |
| Audio Host RC6 | Avvio dei servizi, catalogo locale dei plugin e inizializzazione dei moduli. | [AudioHost](AudioHost/) |
| Adattamenti a monte | Hook per LMS e correzione lighttpd per il ramo OpenSSL 3.5. | [LMS](RAM/patches-lms/) · [lighttpd](patches/lighttpd/) |

Le prove hardware della distribuzione precedente riguardano Daphile RC6 e LMS 9.1.1. Questa raccolta è destinata allo sviluppo. Per aggiornare un lettore occorrono il pacchetto della versione scelta e l’integrazione compatibile dell’Audio Host.

## Provenienza e licenze

La [licenza MIT](LICENSE) copre il codice originale del progetto. Gli adattamenti di sorgenti a monte conservano i rispettivi avvisi, indicati in [THIRD_PARTY.md](THIRD_PARTY.md). CamillaDSP è una dipendenza esterna eseguibile. L’inventario delle versioni e delle basi è in [SOURCES.md](SOURCES.md).
