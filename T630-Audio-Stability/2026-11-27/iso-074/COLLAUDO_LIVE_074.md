# Daphile live candidata 0.4.74

Questa immagine corregge due errori emersi sulla live USB 0.4.72 con un HP t630 e un RME ADI-2 DAC FS. Mantiene il kernel 6.12.30-daphile, LMS 9.0.3 e CamillaDSP 5.0.0-beta.2. Il numero 0.4.74 identifica il modulo di integrazione.

- A coda vuota e in STOP, LMS omette la lista dei brani e l'indice. La 0.4.72 poteva entrare in recupero e restituire HTTP 503 al bypass. La correzione introdotta nella 0.4.73 gestisce la risposta vuota mantenendo i controlli per coda non vuota, sincronizzazione e risposte contraddittorie.
- Su una scheda appena inizializzata Daphile può omettere `USEDOP` nelle preferenze specifiche del DAC. Il suo valore predefinito è `off`. La 0.4.74 accetta la conversione DSD prevista da Daphile quando riconosce quel default. Il controllo dedicato a DoP e DSD nativo resta rigoroso.

## Riscontri sul t630

Con la 0.4.72 e le preferenze iniziali, il passaggio da PCM già elaborato al DSF64 ha portato il modulo in recupero. Partendo dal percorso nativo, DSF64 e DSF128 sono avanzati e il FLAC successivo è tornato sotto Camilla. Dopo aver applicato il modulo completo della scheda RME su DATA in RAM, la stessa sequenza PCM → DSF64 → DSF128 → PCM è riuscita sia con `USEDOP=off` esplicito sia con DoP selezionato.

| Politica al momento del test | DAC con DSF64 | DAC con DSF128 | Ritorno al FLAC |
| --- | ---: | ---: | --- |
| Conversione PCM di Daphile | 705,6 kHz | 705,6 kHz | 44,1 kHz con Camilla |
| DSD-over-PCM | 176,4 kHz | 352,8 kHz | 44,1 kHz con Camilla |

AutoTune ha letto sul Loopback PCM 3.528 frame di buffer e 441 per periodo a 44.100 Hz, pari a **80 ms e otto periodi**. Il modulo completo della scheda ha anche portato il buffer nativo da 100 a 80 ms. Le frequenze sono letture ALSA. I bit del payload DoP128 non sono stati acquisiti nella sessione live. Prima del riavvio, controlli RME, preset, coda vuota, STOP e politica di conversione esplicita sono stati riletti.

## Costruzione e verifica della candidata

Su Matteo il modulo combinato ha superato 14 test mirati DSD, 9 per la coda e 103 regressioni della pipeline nativa. Dal filesystem dell'immagine sono stati controllati tutti i 51 membri del modulo e il default DSD effettivamente letto dal parser. In QEMU/KVM la .74 è partita da USB virtuale in UEFI. GUI, LMS e i due moduli risultavano disponibili dopo 66 s. I due dischi virtuali di controllo hanno conservato contenuto e hash.

| Artefatto | SHA-256 |
| --- | --- |
| ISO .72 v2 di partenza | `d5e4c2d90b722a15be0de5814f5a6d71543d36a52dcc407316eb586126bc8c47` |
| Delta diretto .72 → .74, 235.278 byte | `ce8dc8e5231a1ada9debe1a9eda526c2305634288d1b301203b0588a11ab2366` |
| ISO .74 completa, 808.286.208 byte | `171c0fba440e98b934bf4157374c5bbbd12f0961a085d270754bf441be581e4d` |
| Modulo Camilla 0.4.74 | `c2ef7df33a91df6b5888e13e150c784946f43e575876c922346e806c848fe508` |

La copia integrale ricostruita su WIN e quella di Matteo hanno lo stesso SHA-256. Da una copia verificata della .72 si può ricostruire la .74 con zstd.

```sh
zstd --long=30 -d --patch-from=Daphile-0.4.72-isolated-live-v2-trial.iso 0.4.72-to-0.4.74.iso.patch.zst -o Daphile-0.4.74-isolated-live-candidate.iso
```

La misura digitale del primo PLAY a freddo resta 8,490 s sull'installazione con il modulo .72. Le due modifiche della candidata riguardano i difetti di stato e configurazione.
