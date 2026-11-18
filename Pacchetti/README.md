# Moduli audio per Daphile RC6

**Hai già la RC6?** Dal pannello del player puoi aprire **CamillaDSP** per regolare il suono e **RME · EQ Studio** per controllare il DAC ADI-2 compatibile. RAM, AutoTune e Audio Host sono già integrati nella distribuzione. Per usarli sulla RC6 non occorre scaricare file.

## Quale versione scegliere

| Cosa cerchi | Versione | Dove si trova |
| --- | --- | --- |
| Equalizzatore fornito con la RC6 | Camilla 0.4.47 | [Plugin originale](plugin/DaphileCamillaDSP-0.4.47.zip) |
| Pannello del DAC RME fornito con la RC6 | RME 0.5.7 | [Plugin originale](plugin/DaphileRME-0.5.7.zip) |
| Nuova interfaccia Ascolto e Taratura, confronto A/B e importazione REW stereo | Camilla 0.4.48, candidata successiva alla RC6 | [Plugin collaudato in laboratorio](plugin/DaphileCamillaDSP-0.4.48.zip) |
| Gestione della riproduzione da RAM | RAM A7 | [Sorgenti di integrazione](sistema/RAM-A7-sorgenti.zip) |
| Regolazioni e profili di sistema | AutoTune B4e | [Sorgenti di integrazione](sistema/AutoTune-B4e-sorgenti.zip) |
| Servizi e catalogo locale dei plugin | Audio Host RC6 | [Sorgenti di integrazione](sistema/AudioHost-RC6-sorgenti.zip) |

Le due versioni del plugin Camilla utilizzano il medesimo motore **CamillaDSP 4.1.3**. I tre ZIP di plugin sono nel formato del catalogo Audio Host per **Daphile Linux x86-64 con LMS 9.1.1**. Il numero 0.4.47 o 0.4.48 identifica l’integrazione per Daphile.

## Usare la candidata 0.4.48

La RC6 distribuita contiene la 0.4.47. Il semplice download della candidata lascia il lettore sulla versione installata. Un aggiornamento gestito deve includere lo ZIP 0.4.48 e il suo inventario nel catalogo approvato dell’Audio Host, quindi passare attraverso il consolidamento della distribuzione. La guida utente descrive la nuova schermata e le operazioni di ascolto. [Guida Ascolto e Taratura](GUIDA-ASCOLTO.md) · [Inventario della candidata](inventari/DaphileCamillaDSP-0.4.48.json)

La 0.4.48 è stata verificata su desktop e mobile con il controller e il motore reali. La prova audio ha usato file PCM stereo a 48 kHz in laboratorio. Il percorso fisico LMS–USB–DAC richiede una prova separata.

## Per chi prepara la distribuzione

I tre archivi in `sistema/` raccolgono i sorgenti per chi integra una ISO compatibile. RAM include i servizi, le modifiche a LMS, la descrizione degli interventi sugli script di avvio e i test. AutoTune include il runtime e i profili forniti dal progetto. Audio Host include i servizi, il catalogo software e gli script per inizializzare e aggiornare i moduli. I percorsi `etc/`, `opt/`, `usr/` e `var/` rappresentano la posizione dei file nel sistema di destinazione. Le dipendenze di base provengono dalla distribuzione compatibile. [Sito ufficiale Daphile](https://www.daphile.com/)

Ogni archivio ha un inventario con l’impronta SHA-256 dei singoli file. Con la cartella completa su Linux, `sha256sum -c SHA256SUMS.txt` verifica i download. Con Python 3, `python3 verifica.py` verifica anche il contenuto degli ZIP e la versione dichiarata da ciascun plugin. Entrambi i controlli leggono solamente i file scaricati. [Manifest delle versioni](manifest.json) · [Condizioni di distribuzione e riferimenti](LICENZE.md)

Tino Zappala, Milano - 2026

## AutoTune B4f — aggiornamento successivo alla RC6

[Scarica lo ZIP originale: aggiornamento e sorgenti](Daphile-AutoTune-B4f-SORGENTI-E-AGGIORNAMENTO-20261118.zip?raw=1) · [Guida](../AutoTune/B4f-20261118/LEGGIMI_AUTOTUNE_B4F_IT.md) · [Rapporto di collaudo](../AutoTune/B4f-20261118/AUTOTUNE_B4F_REPORT.json) · [Metadati del bundle](Daphile-AutoTune-B4f-SORGENTI-E-AGGIORNAMENTO-20261118.BUNDLE.json)

L’archivio conserva i byte della consegna finale. La guida descrive l’installazione verificata e il recupero; il rapporto separa il risultato della prova nativa dalle fixture di portabilità. La ISO RC6 e il precedente ZIP B4e restano disponibili per la ricostruzione della distribuzione.

Dimensione: 3619190 byte. SHA-256: `71772ef58e03434c18ee540f3f10b9b9bfec4318a22b3cb399ecdcf50154db85`.
