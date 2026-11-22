# HP t620 con Daphile 25.05 RT

Questa edizione raccoglie il codice e le verifiche della configurazione con **RME 0.5.22** e **Camilla 0.4.51**. Per ricostruire entrambi gli ZIP con Python, eseguire `python -B build.py` da questa cartella. Lo script confronta ogni risultato con lo SHA-256 della versione installata e scrive i pacchetti in `dist/`.

- [Modulo RME](DaphileRME/) e [documentazione e verifiche](rme_review_20261119/). Le curve correnti sono nelle memorie EQ 4 (Denon) e 5 (HiFiMAN). La [revisione delle curve](rme_review_20261119/REVISIONE_CUFFIE_RME_20261121.md) riporta valori, fonti e limiti. La [prova dei controlli di tono](rme_review_20261119/VERIFICA_INDIPENDENZA_PEQ_TONI_LOUDNESS.md) documenta l'indipendenza dal PEQ.
- [Modulo Camilla](DaphileCamillaDSP/) e [guida 0.4.51](camilla_cuffie_051/LEGGIMI.md). I due predefiniti sono modificabili durante l'ascolto e protetti da salvataggio e cancellazione. Duplica permette di conservare le modifiche in una copia personale.
- [Broker e integrazione con Daphile](t620_installed/), [operatori del t620](operatori_t620/) e [prove sulla macchina](prove/).
- [Consegne versionate](consegne/). Gli archivi conservano anche il codice delle revisioni precedenti. Gli operatori di installazione storici presuppongono la configurazione privata e lo stato di partenza indicato per ciascuna revisione.
