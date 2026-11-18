# Moduli audio per Daphile

Estensioni audio mantenute da Tino Zappala per Daphile. La versione RC6 comprende **RME 0.5.7** e **Camilla 0.4.47**. La successiva **Camilla 0.4.48** introduce le viste Ascolto e Taratura, il confronto fra due regolazioni e l’importazione guidata dei filtri REW.

## Scarica i moduli

| Scelta | Scaricamento |
| --- | --- |
| RME presente nella RC6 | [RME 0.5.7 in formato ZIP](Pacchetti/plugin/DaphileRME-0.5.7.zip?raw=1) |
| Camilla presente nella RC6 | [Camilla 0.4.47 in formato ZIP](Pacchetti/plugin/DaphileCamillaDSP-0.4.47.zip?raw=1) |
| Nuova interfaccia Camilla, collaudata in laboratorio | [Camilla 0.4.48 in formato ZIP](Pacchetti/plugin/DaphileCamillaDSP-0.4.48.zip?raw=1) · [Guida Ascolto e Taratura](Pacchetti/GUIDA-ASCOLTO.md) |
| AutoTune aggiornato dopo la RC6 | [AutoTune B4f: aggiornamento e sorgenti](Pacchetti/Daphile-AutoTune-B4f-SORGENTI-E-AGGIORNAMENTO-20261118.zip?raw=1) · [Guida](AutoTune/B4f-20261118/LEGGIMI_AUTOTUNE_B4F_IT.md) |

Trovi le istruzioni e gli archivi di RAM, AutoTune e Audio Host nella [raccolta dei pacchetti](Pacchetti/).

## Sorgenti

Le directory di AutoTune, AudioHost e RAM riproducono i percorsi dei componenti nel sistema di destinazione. Le directory dei plugin seguono il formato di Lyrion Music Server.

Per ricostruire la distribuzione, apri le [ricette della ISO RC6](ISO-RC6/README.md). La guida indica le versioni dei componenti e la base necessaria.

| Componente | Funzione | Sorgenti |
| --- | --- | --- |
| Camilla 0.4.48 | Profili di ascolto, confronto A/B a volume allineabile, filtri separati per canale e importazione REW. | [DaphileCamillaDSP](DaphileCamillaDSP/) |
| RME 0.5.7 | Controlli, equalizzazione e memorie dei dispositivi ADI-2 compatibili. | [DaphileRME](DaphileRME/) |
| AutoTune B4f | Prova controllata, misure verificate, riconoscimento delle capacità hardware e recupero dello stato. | [AutoTune B4f](AutoTune/B4f-20261118/) |
| RAM A7 | Caricamento progressivo della musica in RAM e integrazione con la coda di LMS. | [RAM](RAM/) |
| Audio Host RC6 | Avvio dei servizi, catalogo locale dei plugin e inizializzazione dei moduli. | [AudioHost](AudioHost/) |
| Adattamenti a monte | Hook per LMS e correzione lighttpd per il ramo OpenSSL 3.5. | [LMS](RAM/patches-lms/) · [lighttpd](patches/lighttpd/) |

Le prove hardware della distribuzione precedente riguardano Daphile RC6 e LMS 9.1.1. Questa raccolta è destinata allo sviluppo. Per aggiornare un lettore occorrono il pacchetto della versione scelta e l’integrazione compatibile dell’Audio Host.

## Provenienza e licenze

La [licenza MIT](LICENSE) copre il codice originale del progetto. Gli adattamenti di sorgenti a monte conservano i rispettivi avvisi, indicati in [THIRD_PARTY.md](THIRD_PARTY.md). CamillaDSP è una dipendenza esterna eseguibile. L’inventario delle versioni e delle basi è in [SOURCES.md](SOURCES.md).

## Firma di pubblicazione

**Tino Zappala, Milano - 2026**

Nel [catalogo delle firme](FIRME.json) puoi cercare il nome di ogni file, anche all’interno dei pacchetti ZIP. Ogni voce associa quella dicitura all’impronta SHA-256 e a una firma Ed25519. Anche il catalogo è firmato. Le firme accompagnano i pacchetti originali e ne preservano i byte collaudati.

Per verificare una copia scaricata, esegui `python3 tools/firme.py` dalla cartella del progetto con Python 3.9 o successivo e il pacchetto `cryptography`. La prova confronta anche le versioni storiche indicate se eseguita in un clone Git completo. La chiave pubblica di questa edizione è nel catalogo. La chiave privata è generata in memoria al momento della firma.
