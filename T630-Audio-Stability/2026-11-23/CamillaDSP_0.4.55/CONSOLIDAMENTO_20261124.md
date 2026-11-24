# Consolidamento finale — Daphile T630 / CamillaDSP 0.4.55

**Tino Zappala, Milano — 2026**  
**Data di consolidamento:** 24 novembre 2026  
**Stato:** sorgenti e documentazione consolidati; collaudo PCM completato; handoff PCM↔DSD ancora da ottimizzare.

## Release e provenienza

- Plugin DaphileCamillaDSP **0.4.55**, motore CamillaDSP **4.1.3**, installato su HP t630.
- Pacchetto plugin approvato in produzione (include un binario CamillaDSP di terze parti): SHA-256 `2271a32f1bb271cc74776042bcf5b0fc889df6edbf110009d5bfe57357937c95`, 3.014.197 byte, 38 componenti.
- **37 file testuali** del plugin versionati e verificabili mediante `MANIFEST.sha256`; il binario terze parti `backend/bin/camilladsp` non è incluso nell'archivio dei sorgenti, né nel repository pubblico.
- Versione precedente per rollback: **0.4.54**, SHA-256 `d1ea771e66ba4844e141c5eae81b6c7b0bf1b3c52e69998236883669a0d9169d`.
- Repository pubblico: https://github.com/tinozappala/Daphile-Audio-Modules
- Cartella Drive di progetto: https://drive.google.com/drive/folders/1Y1hmITK4or-XvmhDFNWlzns66CYYs796

## Contenuto consolidato

- Sorgenti completi del plugin 0.4.55, interfaccia, controller e backend PEQ, `install.xml`, licenze di terzi e `MANIFEST.sha256`.
- Codice e documentazione della versione precedente 0.4.54 e della segregazione CPU già pubblicati nel percorso `T630-Audio-Stability/2026-11-23/`.
- `README.md`, `VALIDAZIONE.md`, `VALIDAZIONE_DAC_REALE_20261124.md`, `ROLLBACK.md`, `release.json`, `tests/offline_test.py`.
- Il report della validazione reale è presente anche separatamente in Drive, oltre che nello snapshot ZIP.

## Risultati verificati sul DAC RME

Sei cambi di sample rate **PCM→PCM** sul RME ADI-2 DAC FS: 44,1, 96, 192, 48, 88,2 e 176,4 kHz. Frequenza ALSA confermata al DAC e PID del motore CamillaDSP invariato durante i sei cambi; anche Squeezelite ha mantenuto il proprio PID. Clipping segnalato zero.

**DSD64 DSF e DFF:** bypass DoP verificato (`dsd-bypass`, ALSA 176,4 kHz). Il successivo ritorno a PCM funziona, **ma sono stati osservati due reset USB** durante gli handoff PCM↔DSD, alle 01:38:33 e 01:38:41. Questi reset vanno approfonditi: non descrivere la commutazione PCM↔DSD come ottimizzata o priva di difetti.

La durata rilevata per arrivare a una configurazione stabile è 1,98–2,51 s nei sei cambi PCM, 5,761 s alla prima entrata DSF, 0,734 s nel passaggio DFF e 7,954 s nel ritorno PCM. **Questi tempi non misurano direttamente il silenzio udibile.** L'API riporta `xrun=null`, non una prova strumentale di zero xrun.

Il sistema è stato lasciato con la playlist originaria conservata e lo stesso brano in pausa, dopo verifica di stabilità della pausa di 12 secondi.

## Limiti della pubblicazione

L'archivio di sorgenti è una copia dei sorgenti versionati su GitHub, **non** una copia del binario di terze parti, dei segreti, dei profili PEQ privati, dei dati musicali o dell'intera immagine ISO Daphile. Non contiene credenziali, token d'accesso, URL della libreria, backup di configurazioni private o dati dei brani. I file di licenza upstream conservano la rispettiva paternità e i relativi termini.

La generazione dell'archivio esegue verifica dei 37 SHA-256 e controllo di presenza della documentazione e dei componenti di affinità CPU. Il nome e il checksum dello ZIP sono pubblicati nel file `SHA256SUMS.txt` che accompagna l'archivio esportato.

## Prossimo intervento tecnico

Indagare i due reset USB nel passaggio PCM↔DSD prima di modificare l'handoff, mantenendo il bypass DSD/DoP e il rollback conservativo della 0.4.54.
