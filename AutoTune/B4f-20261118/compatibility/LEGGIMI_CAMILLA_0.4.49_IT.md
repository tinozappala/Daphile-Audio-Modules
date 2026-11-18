# Camilla 0.4.49 — candidata di manutenzione CPU

Questo pacchetto deriva dalla **0.4.47 esatta della RC6** e ne conserva l’interfaccia classica. La 0.4.48 sperimentale presente in GitHub rimane separata e non viene inclusa o modificata.

Cambia soltanto la lettura fresca dei file del monitor PCM in `backend/native_player.py`; `install.xml` e la versione esposta dall’API vengono aggiornati a 0.4.49. Gli altri 36 file sono identici alla base, inclusi GUI HTML/JS/CSS, motore CamillaDSP, routing e gate DoP. L’intervallo del guard rimane 2 ms; ogni ciclo apre e chiude nuovamente i file.

Le 45 fixture differenziali Linux sono PASS. In sei round da 1000 chiamate su file regolari, la mediana CPU per chiamata è scesa da 1,899 ms a 0,559 ms (−70,56%), con risultati/checksum identici. **La misura riguarda questa lettura, non la CPU totale del worker**, procfs dal vivo o un miglioramento sonoro.

La composizione ZIP e gli hash sono verificati in `BUILD_REPORT.json`; `APPROVED_ENTRY_0.4.49.json` mantiene lo schema e i limiti LMS dell’approvazione 0.4.47 effettiva. La provenienza storica interna della 0.4.47 è conservata byte per byte; il rapporto esterno descrive questa candidata.

Il semplice download non aggiorna il player. L’installazione deve creare una nuova release Audio Host, rendere coerenti catalogo e sorgenti LMS e preservare preset, dati utente e release precedenti. Questo builder non installa e non modifica produzione, approvazioni attive o GitHub.

Tino Zappala, Milano - 2026
