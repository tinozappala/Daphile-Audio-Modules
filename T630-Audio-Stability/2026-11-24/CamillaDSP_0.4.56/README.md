# DaphileCamillaDSP 0.4.56 — handoff senza reset USB obbligatorio

**Tino Zappala, Milano — 2026**

## Obiettivo e causa individuata

La 0.4.55 mantiene CamillaDSP residente durante i cambi PCM→PCM, ma nell'handoff PCM↔DSD eseguiva `spac-msg restart cardN`. Il daemon Daphile `spacd` gestisce quel comando tramite `reset_usb_audio_device`, che usa esplicitamente `usbreset` sull'intera periferica USB. I due reset registrati il 24 novembre erano coerenti con tale chiamata.

Questa release cambia **soltanto il comando di riavvio del player** nel sorgente `native_player.py`, usando il percorso Daphile `restart pcmCND0p` dopo tutte le verifiche già presenti di identità USB e topologia LMS. Questo ramo termina e riavvia Squeezelite per il solo dispositivo ALSA 0, senza il reset USB preventivo di `restart cardN`.

Le protezioni native **DSD/DoP** sono conservate; i flussi DSF/DFF non attraversano CamillaDSP e non viene imposto alcun ricampionamento PCM. Il motore CamillaDSP PCM e il codice del gate DoP rimangono identici alla 0.4.55.

## Provenienza

- Versione: **0.4.56**, pacchetto approvato con 38 componenti e SHA-256 `71b783d1d86cd16d4e0a91b206c3f1a4f3788550f8f87fc005df3823146f309e`, 3.014.336 byte.
- Motore CamillaDSP upstream: 4.1.3, non incluso nel repository sorgenti.
- Il repository contiene **37 file testuali** verificati dal `MANIFEST.sha256`, interfaccia e backend, licenze, prove e documentazione.
- Versione precedente disponibile: 0.4.55, SHA-256 `2271a32f1bb271cc74776042bcf5b0fc889df6edbf110009d5bfe57357937c95`.
- Identità del daemon Daphile esaminato: SHA-256 `530f4940d49f6ca2baa91348cf07e698ae631e42eebe8dd845280e49e4274969`. Il codice proprietario del daemon **non** è incluso.

## Verifica reale

Due sequenze RME ADI-2 DAC FS, DSD64→PCM→DSD64, una a 96 kHz e una a 44,1 kHz, con **zero nuovi reset USB in quattro transizioni**. Coda LMS di 15 brani conservata e posizione DSD ripristinata in entrambi i cicli.

Consultare [VALIDAZIONE_DAC_REALE_20261124.md](VALIDAZIONE_DAC_REALE_20261124.md) per i tempi e limiti, [ROLLBACK.md](ROLLBACK.md) per recupero, [CONSOLIDAMENTO_20261124.md](CONSOLIDAMENTO_20261124.md) per archivi.

**Limite importante:** in caso di errore di avvio del player, il servizio proprietario Daphile dispone ancora di un *fallback* con `usbreset`. La modifica elimina il reset **obbligatorio nel percorso normale**, non disabilita il recupero d'emergenza del daemon.

Non includere credenziali, token, profili PEQ privati, metadati delle playlist o il binario upstream nell'archivio pubblico. Le licenze dei file di terze parti restano valide.
