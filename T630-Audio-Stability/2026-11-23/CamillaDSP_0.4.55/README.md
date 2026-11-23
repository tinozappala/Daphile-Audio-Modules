# CamillaDSP 0.4.55 — motore PCM residente

**Tino Zappala, Milano - 2026**

Release realmente installata sul Daphile HP t630 (23 novembre 2026). Il motore CamillaDSP v4.1.3 rimane in esecuzione quando cambia il sample rate PCM; un nuovo processo arecord e un nuovo controllo PCM Gate DoP vengono avviati per ogni sorgente. Per ogni riconfigurazione: mute, stop del produttore precedente, attesa dello stato Stalled, scarico vecchi frame, stato Inactive, nuova configurazione con SetConfigJson e riavvio dei soli endpoint ALSA.

- Pacchetto originale incluso il binario terze parti: **SHA-256 2271a32f1bb271cc74776042bcf5b0fc889df6edbf110009d5bfe57357937c95**, 3014197 byte, 38 file; il binario `backend/bin/camilladsp` è escluso da GitHub per rispettare la diversa provenienza e licenza.
- Questa cartella contiene gli altri **37 file testuali**, compresi tutti i sorgenti originali modificati, licenze upstream, manifest e install.xml.
- Il bypass DSD/DoP resta nativo, senza DSD→PCM.
- La prima attivazione dopo il boot può ancora richiedere il tradizionale handoff del routing con riavvio del singolo Squeezelite: **non è stato eliminato** da questa release.
- Non sono presenti profili personali, playlist, token o password.
- Le licenze upstream GPL/MPL restano applicabili; la firma Tino Zappala riguarda le modifiche originali.

Sono presenti `MANIFEST.sha256`, `release.json`, `VALIDAZIONE.md` e `ROLLBACK.md`.
