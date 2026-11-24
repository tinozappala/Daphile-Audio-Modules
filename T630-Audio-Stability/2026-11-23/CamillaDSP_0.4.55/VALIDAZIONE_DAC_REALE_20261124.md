# Collaudo sul DAC reale — CamillaDSP 0.4.55

**Tino Zappala, Milano — 2026**  
**Data:** 24 novembre 2026, 01:38–01:45 (Europe/Rome)

## Hardware e metodo

- HP t630, DAC USB **RME ADI-2 DAC FS**, ALSA card3, VID:PID `2a39:3fd3`.
- Lyrion Music Server + Squeezelite + DaphileCamillaDSP 0.4.55; motore CamillaDSP 4.1.3.
- PCM: `RESAMPLE=exception`, `RATELIMIT=0`; DSD64 nativo via `USEDOP=on`.
- File FLAC, DSF e DFF **reali** della libreria. Misurata la frequenza dell'endpoint ALSA del DAC; registrati PID e stato audio. L'API non fornisce una misura quantitativa degli xrun (`xrun=null`).

## Risultati PCM→PCM sul DAC

| Frequenza | ALSA confermata | Tempo al rilevamento stabile |
|---|---:|---:|
| 44,1 kHz | 44100 Hz | 2,371 s |
| 96 kHz | 96000 Hz | 2,512 s |
| 192 kHz | 192000 Hz | 2,041 s |
| 48 kHz | 48000 Hz | 1,983 s |
| 88,2 kHz | 88200 Hz | 2,094 s |
| 176,4 kHz | 176400 Hz | 2,106 s |

**Tutti e sei superati**; CamillaDSP PID **20445** e Squeezelite PID **20174** invariati durante l'intera sequenza. `pcm-active`, engine `Running` e clipping segnalato zero. I secondi riportati non misurano il silenzio udibile.

## DSD64 e ritorno a PCM

- DSF DSD64: `dsd-bypass`, ALSA 176400 Hz (DoP64), stabilizzazione **5,761 s**.
- DFF DSD64: `dsd-bypass`, ALSA 176400 Hz, stabilizzazione **0,734 s**.
- Ritorno a FLAC PCM 44,1 kHz: `pcm-active`, ALSA 44100 Hz, stabilizzazione **7,954 s**.
- Il passaggio DSD richiede attualmente il riavvio del percorso nativo di Squeezelite; il processo CamillaDSP dopo il ritorno è nuovo. Il DSP non è stato impiegato per il DSD/DoP.
- **Due reset USB** del DAC nei log del kernel (01:38:33 e 01:38:41), associati temporalmente agli handoff PCM↔DSD. Nessun reset nuovo nel primo gruppo PCM→PCM; nessuna disconnessione permanente. La causa precisa richiede ulteriore indagine.

## Conservazione del player

La coda originaria di **1160 brani** è stata conservata integralmente. Il brano era vicino alla fine quando è stato salvato il cursore; il primo seek di ripristino è stato seguito da un passaggio indesiderato a `stop`. Un secondo ripristino con pausa esplicita dopo il seek ha superato una verifica di **12 secondi**, e alle 01:45 sono stati confermati `pause`, stessa posizione e indice. Audio Host attivo, `recovery_required=false`, CamillaDSP 0.4.55 `Running` a 192 kHz.

## Verdetto

**PCM→PCM: superato sul DAC reale.**  
**DSD/DoP: bypass funzionale, ma transizioni PCM↔DSD non ancora promosse senza riserve** a causa dei due reset USB e dei tempi di commutazione. Nessuna misura analogica, nessuna conferma dal display RME e nessuna prova di zero xrun; non dichiarare perfezione bit per bit sulla sola base del sample rate ALSA.

Il rapporto completo si trova nel workspace Google Drive: [VALIDAZIONE_DAC_REALE_CAMILLA_0455_20261124.md](https://drive.google.com/file/d/18QXRskqeLqpNGhm4-jYH3KFyuyWecCu2/view).

**Release installata SHA-256** `2271a32f1bb271cc74776042bcf5b0fc889df6edbf110009d5bfe57357937c95`; rollback disponibile alla 0.4.54 (`d1ea771e66ba4844e141c5eae81b6c7b0bf1b3c52e69998236883669a0d9169d`).

Non pubblicare token, URI dei brani e profili personali.
