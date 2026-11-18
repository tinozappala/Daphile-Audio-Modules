# Inventario dei sorgenti

| Raccolta | Versione di riferimento | Origine |
| --- | --- | --- |
| RAM | A7 | File di runtime della RC5 ripresi dalla RC6. Servizi, moduli Perl del progetto e interfaccia web provengono dai pacchetti di ricostruzione RC4/RC5. |
| Audio Host | RC6 | Codice dell’host con RME 0.5.7, script OpenRC e integrazione di bootstrap/consolidamento della ricetta RC6. |
| AutoTune | B4e | Candidato sorgente B4e con codice, regole, profili di fabbrica e pagina di controllo. |
| RME | 0.5.7 | Componenti testuali del pacchetto del catalogo RC6. Il relativo archivio proviene dal bundle dell’Audio Host. |
| Camilla | 0.4.48, derivata dalla 0.4.47 della RC6 | Interfaccia Ascolto e Taratura, importazione REW e confronto A/B sul motore esterno CamillaDSP 4.1.3. |
| Distribuzione Daphile | fw261118b7rc6 | [Sorgenti, ricette e impronte ISO RC6](ISO-RC6/README.md). La base di costruzione è la RC5. I sorgenti storici di Camilla 0.4.47 accompagnano le ricette. |
| Pacchetti dei moduli | RC6 e candidata Camilla 0.4.48 | [Pacchetti e inventari](Pacchetti/README.md). I plugin RME e Camilla si possono scaricare separatamente. |
| lighttpd | 1.4.85+daphile1 | Patch originale del progetto sul modulo OpenSSL e avviso di licenza a monte. |

I file ereditati sono stati confrontati con gli hash della tabella completa del filesystem RC5, che la ricetta RC6 verifica prima di applicare le modifiche. Per i due script di bootstrap modificati dalla RC6 è stata usata la copia nella ricetta RC6. L’archivio completo di provenienza del progetto ha SHA-256 `c9c5c7486459ff565c015d37749b834928c077b21c4bea497781cf08ba61ce20`.

Le tre patch LMS sono per `Slim/Player/Song.pm`, `Slim/Player/StreamingController.pm` e `slimserver.pl`. Il file [boot-changes.json](RAM/platform/boot-changes.json) descrive le aggiunte locali ai tre script di avvio Daphile usando l’impronta del file d’ingresso e di uscita e intervalli di righe numerati da 1. I contenuti originali di quegli script provengono dal firmware compatibile. I runtime Linux, Python, Perl, curl, OpenSSH e FFmpeg aggiornati nella distribuzione restano pacchetti dei relativi progetti.

Per eseguire le prove isolate su cache e log della RAM dalla radice del repository:

```sh
PYTHONPATH=RAM/opt/daphile-ram python3 -B -m unittest discover -s RAM/tests
```
