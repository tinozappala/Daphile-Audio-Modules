# Daphile T630 — indice release 0.4.56

**Tino Zappala, Milano — 24 novembre 2026**

## Ultima release operativa
**CamillaDSP 0.4.56**, motore 4.1.3, SHA-256 pacchetto `71b783d1d86cd16d4e0a91b206c3f1a4f3788550f8f87fc005df3823146f309e`.

- [Sorgenti 0.4.56 e manifest](CamillaDSP_0.4.56/)
- [Collaudo sul DAC reale e tempi di handoff](CamillaDSP_0.4.56/VALIDAZIONE_DAC_REALE_20261124.md)
- [Analisi della causa dei reset e consolidamento](CamillaDSP_0.4.56/CONSOLIDAMENTO_20261124.md)
- [Procedure di rollback](CamillaDSP_0.4.56/ROLLBACK.md)
- [Versione 0.4.55 precedente](../2026-11-23/CamillaDSP_0.4.55/)
- [Moduli di affinità CPU](../2026-11-23/CPU_Affinity/)
- [Cartella Drive dei sorgenti e della documentazione](https://drive.google.com/drive/folders/1Y1hmITK4or-XvmhDFNWlzns66CYYs796)

## Stato validazione

PCM→PCM era stato verificato con 0.4.55: CamillaDSP persistente tra sei frequenze. La 0.4.56 aggiunge l'handoff player-scoped PCM↔DSD, con quattro transizioni reali senza reset USB registrati. L'eventuale fallback USB di Daphile su errori di startup rimane disponibile. Tempi di stabilizzazione e qualità analogica sono grandezze distinte.

Le credenziali e i profili personali sono esclusi dal repository e dai backup pubblici.
