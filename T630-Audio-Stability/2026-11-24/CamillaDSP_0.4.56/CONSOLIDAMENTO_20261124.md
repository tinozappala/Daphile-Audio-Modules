# Consolidamento completo 0.4.56

**Tino Zappala, Milano — 24 novembre 2026**

Questo snapshot comprende i **37 file sorgente testuali del plugin** (interfaccia, controller, codec PCM, gate DoP, metadati e licenze), il `MANIFEST.sha256`, il `release.json`, test offline, la documentazione del DAC RME e il piano di rollback. Esclude il binario upstream CamillaDSP, segreti, token, configurazioni private e la libreria musicale.

**SHA-256 pacchetto completo distribuito sul t630:** `71b783d1d86cd16d4e0a91b206c3f1a4f3788550f8f87fc005df3823146f309e` (38 componenti, 3.014.336 byte).

**Differenza essenziale rispetto a 0.4.55:** `native_player.py` passa dal comando `restart cardN` (che esegue esplicitamente `usbreset` sulla periferica USB) al riavvio per singolo player `restart pcmCND0p`. Il motore PCM persistente e la salvaguardia DSD/DoP della 0.4.55 sono invariati.

**Collaudo effettivo sul DAC RME:** due cicli di transizione DSD64→PCM→DSD64 (44,1/96 kHz), quattro handoff, **zero nuovi reset USB**. Vedere `VALIDAZIONE_DAC_REALE_20261124.md` per risultati, limiti e confronto storico.

**Github:** https://github.com/tinozappala/Daphile-Audio-Modules  
**Google Drive:** https://drive.google.com/drive/folders/1Y1hmITK4or-XvmhDFNWlzns66CYYs796

L'archivio ZIP finale e i checksum sono generati da GitHub Actions dopo la verifica della `MANIFEST.sha256`. Per l'esatta provenienza utilizzare `SOURCE_GITHUB_COMMIT.txt` incluso nell'archivio, insieme al file SHA256SUMS esterno.
