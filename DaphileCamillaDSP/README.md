# Daphile · Suono 0.4.48

**Ascolto** apre il profilo del lettore. Con **Confronta due regolazioni** si passa da A a B durante un brano PCM. Il lettore conferma ogni cambio. Alla chiusura riprende il profilo di partenza. **Usa questa regolazione** mantiene la scelta con il volume originale del profilo.

Per confrontare a volume simile si può scegliere un WAV stereo alla stessa frequenza del brano. Il browser misura il livello medio RMS delle due regolazioni sul medesimo estratto, fino a sei secondi, e trasmette al lettore soltanto la regolazione. Il risultato si riferisce a quel passaggio. L’attenuazione del confronto termina con la prova.

**Taratura** permette di trascinare i punti sulla curva, scegliere la forma dei filtri, usare fino a 24 bande per lato e importare un file di filtri REW per entrambi i canali o due file diversi. Prima della conferma si può controllare la curva prevista. I file supportati da questa versione sono le esportazioni dei filtri Generic in formato TXT. L’editor adatta colori, icone dei filtri e indicatori della larghezza di banda di CamillaEQ. La zona evidenziata si vede selezionando una campana con guadagno diverso da zero oppure un filtro a tacca.

## Sviluppo

L’Audio Host della RC6 serve gli asset locali; il controller Python usa la libreria standard. Il pacchetto di questa versione continua a usare il motore CamillaDSP 4.1.3 presente nella RC6. Per ricostruire il pacchetto dalla radice del repository basta Python 3 e il binario del motore della RC6, con SHA-256 `e69090287d9af87eeb5720d59cf8ebf8cab1ed3adbe24de1dd60b66a47f5385b`.

```sh
python3 tools/camilla/build_camilla.py --source DaphileCamillaDSP --engine /percorso/al/camilladsp --out dist
python3 tools/camilla/check_release.py --package dist/DaphileCamillaDSP-0.4.48.zip
```

La build accetta in alternativa il pacchetto originale con `--base DaphileCamillaDSP-0.4.47.zip`. Produce il modulo e l’inventario `APPROVAL.json` per l’Audio Host. Il confronto temporaneo si trova in `backend/peq/comparison.py`; nel pacchetto viene incluso nel controller già ammesso dalla RC6. Il test usa il motore e il ponte HTTP su Linux, con un flusso PCM locale in luogo del lettore fisico.

L’istanza fisica, l’uscita USB e il trasporto AirPlay non sono stati collaudati per la versione 0.4.48. Questa versione gestisce l’equalizzazione parametrica PCM. Il gestore FIR, la selezione automatica per uscita e la cronologia estesa dei profili sono sviluppi successivi.

La build del 18 novembre 2026 ha SHA-256 `f2692711eaa8ce884ae7a66bc4ca50a7539f4e4ea4e6b0331715dedef42cf8f0`. Nel laboratorio Linux sono state provate l'apertura nelle viste desktop e telefono, la commutazione temporanea, l’allineamento del volume con un WAV stereo, il ripristino, l'importazione e il salvataggio di due correzioni differenti per canale.
