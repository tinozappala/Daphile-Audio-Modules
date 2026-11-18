# Daphile RC6 del 18 novembre 2026

La RC6 è l’edizione dell’immagine Daphile che comprende **RAM A7, RME 0.5.7, Camilla 0.4.47 e AutoTune B4e**. Il pannello RME gestisce i convertitori ADI-2 compatibili. Il pannello Camilla usa il motore CamillaDSP 4.1.3. Per scaricare un modulo apri la [raccolta dei pacchetti](../Pacchetti/).

| Cerco | Dove trovarlo |
| --- | --- |
| Il modulo RME incluso nella RC6 | [Pacchetto RME 0.5.7](../Pacchetti/plugin/DaphileRME-0.5.7.zip?raw=1) · [Sorgenti RME](../DaphileRME/) |
| L’equalizzatore incluso nella RC6 | [Pacchetto Camilla 0.4.47](../Pacchetti/plugin/DaphileCamillaDSP-0.4.47.zip?raw=1) · [Sorgenti dell’edizione ISO](camilla_current/DaphileCamillaDSP/) |
| L’equalizzatore con l’interfaccia Ascolto e Taratura | [Guida alla versione 0.4.48](../Pacchetti/Guida-Camilla-0.4.48.md) |
| Gli altri moduli dell’immagine | [RAM A7](../RAM/) · [AutoTune B4e](../AutoTune/) · [Audio Host](../AudioHost/) |
| Le versioni e le impronte dei file di consegna | [Inventario della RC6](RELEASE.json) |

## Controllare un download

Chi vuole usare i pannelli può fermarsi alla raccolta dei pacchetti. Per controllare un file prima di usarlo, apri un terminale nella cartella `ISO-RC6`. Con Python 3.11 o successivo esegui questo comando indicando il file scaricato.

```sh
python gestione_iso.py verifica --rme ../Pacchetti/plugin/DaphileRME-0.5.7.zip
```

La scritta `OK` conferma che dimensione e impronta SHA-256 corrispondono alla consegna. Puoi usare anche `--iso` per la RC6, `--base` per la RC5, `--sorgenti` per l’archivio delle ricette o `--camilla` per il pacchetto 0.4.47.

## Ricostruire l’immagine

Occorrono **la ISO base RC5 e l’archivio sorgenti completo della RC6**. La base fornisce il sistema Daphile e il plugin RME. L’archivio sorgenti fornisce gli oggetti di compilazione, il pacchetto storico Camilla e gli strumenti dell’edizione. I nomi, le dimensioni e le impronte per riconoscerli sono in [RELEASE.json](RELEASE.json).

1. Metti i due file sul computer. Nell’esempio si chiamano `base.iso` e `sorgenti.zip`.
2. Dalla cartella `ISO-RC6` scegli una nuova cartella di lavoro accanto al repository. Il primo comando controlla i due file e copia gli elementi necessari. Il secondo costruisce l’immagine nella cartella di lavoro.

```sh
python gestione_iso.py prepara --sorgenti sorgenti.zip --base base.iso --cartella ../../lavoro-rc6
python -B ../../lavoro-rc6/iso_maintenance_20261117/build_iso.py
```

Il compositore usa Python 3.11 o successivo, pycdlib 1.21.0, SquashFS e xorriso. L’archivio contiene gli strumenti usati nella compilazione Windows. Su Linux servono i comandi `mksquashfs`, `unsquashfs` e `xorriso` nel PATH. Versioni differenti degli strumenti possono produrre un’immagine con un’impronta diversa. L’esecuzione opera su file locali. Per controllare l’immagine prodotta usa il nome del file presente nella cartella di uscita.

```sh
python gestione_iso.py verifica --iso ../../lavoro-rc6/DAPHILE_MANUTENZIONE_RC6_20261117/Daphile-RAM-A7-Camilla047-RME057-AutoTuneB4e-RC6-20261118.iso
```

## Ricette di compilazione

| Componente della RC6 | Edizione | Sorgenti di integrazione e licenze |
| --- | --- | --- |
| Linux con patch realtime | 6.12.117-rt23-daphile-v6 | [Ricetta, configurazioni e licenze Linux](iso_maintenance_20261117/recipes/kernel/) |
| Python | 3.13.18 | [Ricetta e licenza Python](iso_maintenance_20261117/recipes/python/) |
| Perl | 5.40.5, pacchetto R3 | [Ricetta e licenze Perl](iso_maintenance_20261117/recipes/perl/) |
| curl e libcurl | 8.23.0 | [Ricetta e licenze curl e ngtcp2](iso_maintenance_20261117/recipes/curl/) |
| OpenSSH Portable | 10.6p2 | [Ricetta e avvisi OpenSSH](iso_maintenance_20261117/recipes/openssh/) |
| lighttpd | 1.4.85+daphile1 | [Ricetta, patch e licenza BSD](iso_maintenance_20261117/recipes/lighttpd/) |
| FFmpeg | 6.1.6 | [Ricetta e licenze FFmpeg](iso_maintenance_20261117/recipes/ffmpeg/) |

Il [compositore](iso_maintenance_20261117/build_iso.py) parte dalla RC5, verifica l’identità dei componenti, crea il filesystem Linux e rilegge il risultato. La [selezione dei file](iso_maintenance_20261117/COMPONENTI_VERIFICATI.json) riporta percorsi di installazione, permessi e impronte. I [riferimenti ai sette runtime](iso_maintenance_20261117/RUNTIME_ARTIFACT_REFERENCES.json) identificano gli archivi compilati. L’[elenco dei quindici archivi upstream](iso_maintenance_20261117/UPSTREAM_SOURCES.json) riporta URL dei produttori, dimensioni e SHA-256. L’[indice degli input di compilazione](iso_maintenance_20261117/PUBLIC_SDK_INDEX.json) identifica gli SDK usati dalle ricette.

La [licenza MIT del progetto](../LICENSE) copre il codice originale. Le cartelle `licenses` di ogni componente conservano gli avvisi e le licenze delle rispettive distribuzioni. La ricetta FFmpeg abilita le opzioni GPL e versione 3, documentate nel [testo di FFmpeg](iso_maintenance_20261117/recipes/ffmpeg/licenses/LICENSE.md). Per le parti derivate di LMS, la patch di lighttpd e il motore CamillaDSP, vedi le [attribuzioni del repository](../THIRD_PARTY.md).

Tino Zappala, Milano - 2026
