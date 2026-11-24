# Indice consolidato — Daphile HP t630

**Tino Zappala, Milano — 24 novembre 2026**

## Versione attiva

**DaphileCamillaDSP 0.4.55** — plugin integrato con motore CamillaDSP 4.1.3, SHA-256 del pacchetto approvato:

`2271a32f1bb271cc74776042bcf5b0fc889df6edbf110009d5bfe57357937c95`

- [Sorgenti completi testuali 0.4.55](CamillaDSP_0.4.55/) e [manifest 37 SHA-256](CamillaDSP_0.4.55/MANIFEST.sha256).
- [Consolidamento tecnico e archivio Drive](CamillaDSP_0.4.55/CONSOLIDAMENTO_20261124.md).
- [Collaudo effettivo sul DAC RME](CamillaDSP_0.4.55/VALIDAZIONE_DAC_REALE_20261124.md).
- [Test isolato](CamillaDSP_0.4.55/VALIDAZIONE.md), [codice di prova](CamillaDSP_0.4.55/tests/offline_test.py), [rollback](CamillaDSP_0.4.55/ROLLBACK.md).

## Versione precedente

**0.4.54** — sorgenti e documentazione conservati in [CamillaDSP_0.4.54](CamillaDSP_0.4.54/), SHA-256 pacchetto `d1ea771e66ba4844e141c5eae81b6c7b0bf1b3c52e69998236883669a0d9169d`. Backup e profili privati rimangono esclusivamente sul t630.

## Moduli complementari

- [Guardiano affinità CPU / OpenRC](CPU_Affinity/).
- Altri documenti di ottimizzazione nella cartella di progetto.
- [Archivio documentale Google Drive](https://drive.google.com/drive/folders/1Y1hmITK4or-XvmhDFNWlzns66CYYs796).

## Esito e limiti

I sei cambi PCM a frequenze native **44,1, 48, 88,2, 96, 176,4 e 192 kHz** sono stati verificati sul DAC reale, senza cambiare PID di CamillaDSP/Squeezelite. Il bypass DSD64 per file DSF e DFF funziona, ma negli handoff PCM↔DSD sono stati registrati **due reset USB** e la riconfigurazione è più lenta: **miglioramento ancora da completare**. Non è stata misurata l'assenza totale di xrun né la qualità analogica.

Nessun archivio pubblico deve contenere credenziali, token, profili privati, playlist, registrazioni o dati personali.
