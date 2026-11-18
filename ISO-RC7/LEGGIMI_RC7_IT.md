# Daphile RC7 del 18 novembre 2026

La ISO integra **AutoTune B4f con avvio a un clic** e **Camilla 0.4.49**
con l'interfaccia classica. Deriva dalla RC6 e include RAM A7, RME 0.5.7,
LMS 9.1.1, motore CamillaDSP 4.1.3 e Linux 6.12.117-rt23-daphile-v6.

## Usare AutoTune

1. Salva eventuali modifiche in Settings.
2. Apri AutoTune e premi **Avvia AutoTune**. Il lettore si ferma e il brano
   torna all'inizio. Il pannello indica quando comincia la prova.
3. Attendi il risultato. La prova può durare fino a 30 minuti, incluso
   il ripristino della coda. Se serve un intervento, il pannello indica
   cosa fare e quando riprovare.

Le istruzioni riguardano il lettore rilevato. Se la scansione della libreria,
un gruppo di lettori o la riproduzione in RAM impediscono la prova, AutoTune
attende la correzione indicata. I dettagli tecnici sono nei Controlli avanzati.
Il pacchetto separato **AutoTune-B4f-avvio-guidato.zip** permette di aggiornare
un'installazione B4f compatibile. Dopo aver estratto lo ZIP su Daphile, eseguire
come amministratore `python3 -I -B install_b4f.py --install` dalla cartella
estratta e verificare con `python3 -I -B install_b4f.py --verify`.
L'installatore indica la cartella privata per il recupero dei file precedenti.

## Scaricare e verificare la ISO

1. Scarica le cinque parti, `MANIFEST_PARTI.json` e `RICOMPONI.py`
   nella stessa cartella. [Cartella ISO sul Drive](https://drive.google.com/drive/folders/1exQNAqeMLCkTAwPOZLahYOCN7bMklKIh).
2. Nella cartella esegui `python RICOMPONI.py`. Lo script controlla
   dimensione e SHA-256 delle parti e produce la ISO originale.
3. Per controllare anche la firma, metti la ISO ricomposta accanto a
   `FIRME_CONSEGNA.json` e `firme_consegna.py`. Con Python 3.11 o successivo
   e il pacchetto `cryptography`, esegui questo comando.

```sh
python firme_consegna.py --solo Daphile-RAM-A7-Camilla049-RME057-AutoTuneB4f-RC7-20261118.iso
```

La ISO è di 438.140.928 byte. Il suo SHA-256 è
`62c907d438a2be83987f417faff73d98cc82373cf8339e7e42e0e7f3106d1dda`.
Il catalogo firma i file della consegna con Ed25519, comprese le parti
dell'immagine. Il rapporto `COLLAUDO.json` descrive le prove eseguite.
[Cartella della consegna](https://drive.google.com/drive/folders/1Fipqe61o2zUYsob39qau8sHvVtZviQ_9).

## Esito del collaudo

In VM, la ISO definitiva ha avviato UEFI, LMS e AutoTune. La verifica ha
confrontato il filesystem montato con quello della ISO, 125 file di AutoTune
e 39 file Camilla installati da LMS. Il servizio dei moduli audio è rimasto
fermo all'avvio. Avviato da comando standard, non ha portato Camilla e RME
allo stato pronto nella VM. Camilla 0.4.49 ha risposto correttamente sul t620
in una prova separata con configurazione nuova e rete isolata.

Il supporto contiene il catalogo BIOS e UEFI. La prova BIOS riguarda
la composizione precedente all'avvio guidato di AutoTune. Sul t620 è stato
installato e verificato il pannello con avvio a un clic. Le prove del gesto,
della preparazione del lettore e delle condizioni di blocco usano dati
simulati. La calibrazione fisica B4f precedente ha completato 18 misure
valide e tre confronti sul t620 con RME.

## Ricostruire la ISO

Usa l'immagine RC6 originale con SHA-256
`c11c16fe07466af293c437d7eaaaa5a514e586c913e4b64b1197f8f979f63ed3`.
[Base RC6 su Drive](https://drive.google.com/drive/folders/1yaFNytx6EWNnEb29c3AfCswuxMlOaC9X).

Scarica anche il pacchetto AutoTune B4f originale con SHA-256
`71772ef58e03434c18ee540f3f10b9b9bfec4318a22b3cb399ecdcf50154db85`.
Contiene il pacchetto Camilla 0.4.49 e il relativo catalogo.
[Archivio B4f](https://drive.google.com/file/d/13SZUVJBCzFm5HKYafFIAOHtgte93Iqox/view).

Estrai `RICETTA_RC7.zip` e tieni nello stesso percorso di lavoro i due
input e `AutoTune-B4f-avvio-guidato.zip`. Il compositore richiede Python
3.11 o successivo con pycdlib, SquashFS e xorriso. Su Windows, indica
le directory degli strumenti con `--tools-dir` e `--pycdlib-dir` se servono.

```sh
python -B ricetta/build_rc7.py --base Daphile-RAM-A7-Camilla047-RME057-AutoTuneB4e-RC6-20261118.iso --autotune Daphile-AutoTune-B4f-SORGENTI-E-AGGIORNAMENTO-20261118.zip --interface AutoTune-B4f-avvio-guidato.zip --work lavoro-rc7 --out rilascio-rc7
```

Il compositore verifica gli input, l'inventario del filesystem e la lettura
dei file dal supporto finale. Il supporto Live usa l'identificatore richiesto
dal menu originale Daphile. Le versioni degli strumenti di compressione
possono influenzare l'impronta dell'immagine ricostruita. Il materiale
completo dei componenti ereditati è nella consegna RC6.
[Sorgenti e ricette RC6](https://drive.google.com/drive/folders/17bH_JeEoBQ3Gf7HpAW8-FJPtlSQ-YYC5).

Tino Zappala, Milano - 2026
