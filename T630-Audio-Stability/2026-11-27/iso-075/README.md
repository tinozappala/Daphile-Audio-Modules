# ISO live e installabile 0.4.75

La candidata è derivata dalla ISO .74 esatta. Conserva senza modifiche il
modulo audio **0.4.74**; corregge la costruzione dell'immagine e dell'initrd
necessaria all'installazione dalla stessa live. La base Daphile e il kernel
sono quelli della 25.05: l'identificativo sintetico `fw2611270750` è mostrato
dalla GUI come `26.11-b270750`, ma non indica un nuovo firmware del produttore.
Il comportamento degli aggiornamenti online con tale identificativo non è
stato qualificato.

Nella prova .72 la GUI offriva la USB live come destinazione, la ripartizionava
e falliva poi la copia. Il rootfs indicava `/version = fw2505251550`, mentre
l'initrd aveva copiato in RAM l'albero `fw2611270720` e smontato la sorgente.
Il controllo `Error#101` di fwtoold verifica sia sorgente sia destinazione:
il disallineamento spiega la failure osservata, pur non essendo una lettura
diretta dei due percorsi nel guest.

La .75 allinea `/version` a `fw2611270750`. Inoltre l'initrd protetto della
.74 accettava soltanto ISO9660 e DATA temporaneo: modificare il solo file
`/version` non avrebbe permesso un avvio installato. La .75 mantiene come
predefinito il boot ISO in sola lettura con DATA tmpfs. Il menu generato per
l'installazione aggiunge un marker esplicito; solo in sua presenza, senza
`live`, l'initrd cerca gli UUID di boot e dati, richiede che siano univoci e
che le due partizioni appartengano allo stesso disco, quindi controlla
`disk.uuid` prima di usare DATA persistente.

Il rootfs della .75 differisce dalla .74 solo nel file `/version`; il confronto
ha riguardato tutti i nodi e i gruppi di hardlink. Il pacchetto Camilla .74 è
identico allo ZIP SHA-256
`c2ef7df33a91df6b5888e13e150c784946f43e575876c922346e806c848fe508`.
L'initrd, il menu di installazione, il nome firmware e i riferimenti di avvio
sono aggiornati nella ISO candidata.

Le prove hanno usato esclusivamente immagini sintetiche su MATTEO:

- **Installazione e boot UEFI:** la GUI ha installato sulla stessa USB live
  virtuale, dichiarata removibile, con BIOS e UEFI selezionati. Il disco
  interno sentinella è rimasto invariato; BootOrder e tutte le voci UEFI sono
  identici prima, dopo l'installazione e dopo il riavvio. La USB risultante ha
  ESP FAT32 da 100 MiB, DaphileBoot ext4 da 1 GiB e DaphileData ext4 da circa
  2,90 GiB. Entrambi i file EFI di fallback sono presenti.
- **Avvio installato:** dopo il riavvio, `/boot` e `/mnt/hdd` risultano sulle
  partizioni della USB scelta; la GUI riporta il modulo 0.4.74 e lo ZIP servito
  ha l'hash atteso. Rootfs e initrd letti dalla USB coincidono byte per byte
  con quelli della ISO. Anche un avvio SeaBIOS di un overlay separato della
  stessa USB installata è passato, con un'altra sentinella invariata.
- **Protezione live:** avvio UEFI di una copia di sola prova senza il parametro
  `live` e senza marker installato: DATA tmpfs, nessun disco sentinella montato
  e hash invariati. Un secondo test aggiunge UUID residui riferiti ai due
  dischi sentinella: stesso risultato. Le copie modificate servono soltanto ai
  test e non sono la ISO da distribuire.
- **Fixture initrd:** 5/5 passate, inclusi selezione fail-closed, verifica
  della stessa unità per boot e DATA e confronto del contenuto del cpio.

Queste sono prove virtuali, non una qualifica sul t630 o sul DAC. La prova
UEFI riuscita ha selezionato UEFI e usato un dispositivo che il guest vedeva
come removibile. Per il codice nativo, un dispositivo classificato come non
removibile può provocare modifiche alle voci firmware; il comportamento della
chiavetta e del firmware fisici non è stato verificato qui. La disattivazione
di UEFI non fornisce una ESP avviabile su una macchina solo UEFI. Nessun
miglioramento di latenza o trasporto DSD nativo è rivendicato.

Nel workspace locale:

| File | SHA-256 |
| --- | --- |
| `Daphile-0.4.75-live-install-candidate.iso` | `76081f0321226e3dac8a8d6fd7f821a340a640c7e35ff2a24497f12742c5abd8` |
| `0.4.74-to-0.4.75.iso.patch.zst` | `28dc78209e07b57888cfcb8d49fa9baa5282d815f6d9cc9e2f5c463723e238dd` |
| Base `Daphile-0.4.74-isolated-live-candidate.iso` | `171c0fba440e98b934bf4157374c5bbbd12f0961a085d270754bf441be581e4d` |

Il delta occupa 1.297.422 byte. La ricostruzione richiede zstd con
`-d --long=30 --patch-from=<ISO .74 esatta>`; è stata eseguita e verificata sia
su MATTEO sia nel workspace Windows. Il [manifest](PUBLIC_MANIFEST_075.json)
elenca gli hash di sorgenti e ricevute. La candidata completa è disponibile
anche in otto parti. Matteo le ha riscaricate da Drive e ha verificato i singoli
SHA-256 e quello dell'intera immagine ricomposta.
[Cartella Drive](https://drive.google.com/drive/folders/1sAqQO0LvTs3oIX8eXwOoJ0Q97sljwVGJ).
[Ricevuta della rilettura](DRIVE_ROUNDTRIP_075.json).
