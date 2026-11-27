# Daphile .72 — immagine live isolata di prova

Questa immagine avvia Daphile 25.05-b251550 con LMS 9.0.3, il modulo
CamillaDSP 0.4.72 e il modulo RME 0.5.23. È una **live temporanea di prova**;
non è un aggiornamento automatico dell'installazione esistente.

- File: `Daphile-0.4.72-isolated-live-v2-trial.iso`
- Dimensione: 808.286.208 byte.
- SHA-256: `d5e4c2d90b722a15be0de5814f5a6d71543d36a52dcc407316eb586126bc8c47`
- Archivio Camilla 0.4.72: SHA-256 `acab2f0797c8bc871a2454a411ba68a506a61fd986730889ed00962d0c968b52`.
- Archivio RME 0.5.23: SHA-256 `2c778d58b5547282c6558650faa7e78c09b1b05170dff245f1a8c07099eea601`.

Il builder deposita l'immagine accanto a `build_iso.py`, nella cartella
`next_audio_20261127/iso_consolidata/`. La build salvata ha riletto tutti i
30.038 nodi della rootfs e 113 gruppi di hardlink; il kernel stock è conservato
e l'initrd protetto è distinto da quello stock mantenuto nell'immagine.

## Origine e ricostruzione

La base corrisponde alla [Daphile beta ufficiale 25.05-b251550 x86_64-rt](https://www.daphile.com/firmware/beta/daphile-25.05-b251550-x86_64-rt.iso).
Il [manifest ufficiale beta](https://www.daphile.com/firmware/beta/latest-x86_64-rt.inf)
riporta 391.118.848 byte e MD5 `fff253a66bfa5fdba7e6a13e6f9204e8`, verificati
sul file locale. Il suo SHA-256 misurato localmente è
`623dd475a6cb3f8528bbdb7d2234dfb1fbd582becdd1458d97ec845eeb6b0c15`.
Il kernel di questa base coincide con il kernel stock letto sul t630:
`225f6522feb3d49aede7da41e04aec288774e5efedc0eaca39e7bb6494b7e312`.

I componenti RAM, AutoTune e Audio Host provengono dalla RC6. La copia locale
dei sorgenti RAM e delle patch usate corrisponde al commit
`c10404848e02bc9eb14b5017296d1e8480361f17` del repository
[`tinozappala/Daphile-Audio-Modules`](https://github.com/tinozappala/Daphile-Audio-Modules/tree/c10404848e02bc9eb14b5017296d1e8480361f17).
Il collegamento è ricavato dal remote Git locale; la sua accessibilità pubblica
non è stata verificata durante questa prova. La rootfs RC6 utilizzata ha
SHA-256 `8e101c92c9161f5975f2e4c8ae22a85303270e965c0d11a8bbd60309a2b6253e`.

Per ricostruire non basta copiare il solo script: occorrono la base e la
rootfs RC6 esatte, gli archivi Camilla e RME indicati sopra, i sorgenti RC6,
`pseudo_fs.py`, pycdlib, squashfs-tools e xorriso. Il builder corrente usa
percorsi del workspace Windows riportati nella sezione delle costanti di
`build_iso.py`; vanno predisposti o adattati prima dell'esecuzione. Anche
`test_builder.py` richiede i corrispondenti input e pseudo-file di fixture.
Le versioni degli strumenti possono cambiare l'hash dell'immagine ricostruita.

## Prove effettuate

Sul computer MATTEO, in QEMU/KVM, sono stati completati quattro avvii della
**stessa ISO**. La colonna di destra è il tempo totale della prova, compresa
la rilettura finale; i tempi non sono misure del t630.

| Firmware | Supporto virtuale | GUI, LMS e due API disponibili | Totale con verifica |
| --- | --- | ---: | ---: |
| BIOS | CD | 35,4 s | 50,0 s |
| UEFI | CD | 71,6 s | 82,3 s |
| BIOS | USB | 39,4 s | 50,7 s |
| UEFI | USB | 61,2 s | 72,9 s |

In ogni caso LMS ha riportato 9.0.3, il catalogo e l'API Camilla 0.4.72;
l'API RME ha risposto. Due dischi ext4 sintetici scrivibili, uno interno e uno
USB, avevano lo stesso contenuto sentinella e lo stesso SHA-256 prima e dopo
ogni avvio. L'ISO stessa è stata riletta senza variazioni. Le quattro ricevute
`*_KVM_RESULT.json` accompagnano questa nota. I 13 test locali del builder e
delle fixture passano; il test delle identità USB usa sysfs sintetico e, su
Windows, simula i controlli di proprietario e permessi Linux.

Una precedente prova TCG su Windows aveva avviato GUI, LMS e catalogo ma il
broker audio non era partito entro il limite. La causa precisa non è stata
dimostrata. La successiva ISO v2 è partita in KVM; questo non dimostra la causa
del guasto TCG. La prova non comprende un DAC fisico, riproduzione audio,
qualità sonora, prestazioni real-time o tutti i tipi di filesystem/disco.

## Avvio fisico sul t630, 27 novembre 2026

Scritti e riletti direttamente sul supporto tutti gli 808.286.208 byte della
ISO. Lo SHA-256 coincide con quello indicato sopra. Dopo il riavvio normale,
fra i parametri del kernel del t630 figuravano `daphile=fw2611270720`,
`live` e `uefi=64`. La root SquashFS proveniva dalla copia in RAM di
questa release. DATA era montato in tmpfs e nell'elenco dei filesystem
montati il disco interno era assente.

La GUI e LMS 9.0.3 rispondevano. Il catalogo e l'API Camilla riportavano
0.4.72. LMS vedeva il player RME ADI-2 DAC connesso e il modulo RME ha
letto i controlli del DAC fisico. La live appena inizializzata aveva Camilla
in bypass nativo.

## Avvio da USB

1. Verifica lo SHA-256 della ISO. Scrivila come immagine grezza/DD su una
   chiavetta dedicata: **la scrittura cancella il contenuto della chiavetta**.
2. Scegli la chiavetta dal menu di avvio una tantum del computer. Il default di
   boot dell'installazione precedente non deve essere cambiato per la prova.
3. Accedi alla normale interfaccia Daphile dalla rete locale, usando l'indirizzo
   mostrato dal sistema o `http://daphile.local`. Seleziona esplicitamente il
   DAC e verifica la versione del modulo prima di attivare il DSP.
4. Per tornare all'installazione precedente, spegni la live, rimuovi il
   supporto e avvia normalmente il computer.

La live usa DATA in tmpfs, ignora un eventuale `data_uuid` e non monta
automaticamente i dischi locali né attiva LVM/RAID. Le sentinelle QEMU
confermano l'integrità dei due dischi virtuali. Sul t630 si è verificata
l'assenza del disco interno nell'elenco dei mount dopo l'avvio. Impostazioni e modifiche fatte
nella live sono temporanee e scompaiono allo spegnimento. Non sono inclusi
musica, profili personali, chiavi SSH o credenziali del t630. Il campo password
root della base pubblica resta quello della distribuzione; gli accessi SSH con
password e tastiera interattiva sono disabilitati. Senza una chiave aggiunta
in modo esplicito non è disponibile un accesso SSH amministrativo. Il getty
interattivo stock non è abilitato nella configurazione esaminata: non fare
affidamento su una shell locale come via di recupero di questa live.

Il DATA nuovo inizializza Camilla a `chunksize=2048` e `target_chunks=2`. Il
default selettivo usa il controllo di identità del router .72 e, sul DAC PCM
instradato, passa 40 ms/4 periodi al router, che li converte in 80 ms/8.
Un'impostazione nativa specifica del DAC può prevalere sul default: la
geometria effettiva e il percorso audio richiedono un readback e una prova
con il DAC selezionato. Le politiche IRQ e affinità della macchina usata nelle
precedenti prove fisiche non sono automaticamente replicate da questa ISO.

## Materiale generico per il repository

I seguenti file sono stati controllati come sorgenti e ricevute senza musica,
profili utente, chiavi o indirizzi di rete privati. Gli indirizzi `127.0.0.1`
nei test indicano soltanto il loopback della VM.

- `build_iso.py`, `protect_live_initrd.py`, `routed_default_probe.py` e
  `verify_pseudo.py`.
- `kvm_isolated_boot.py`, `test_builder.py`, `test_kvm_status.py` e, se utile,
  `test_iso_boot.py` per la prova TCG Windows.
- `ram_continuous/support/pseudo_fs.py`, necessario al builder.
- `PUBLIC_MANIFEST.json` e i quattro `*_KVM_RESULT.json` sintetici.

Conservare anche `LICENSE` e `THIRD_PARTY.md` della sorgente RC6 e gli avvisi
di licenza presenti negli archivi dei moduli e negli strumenti utilizzati.

Non pubblicare l'intera cartella `work`: contiene copie di lavoro molto grandi,
percorsi locali e ricevute intermedie. Il report di build originale contiene
un percorso assoluto del computer di sviluppo; per la pubblicazione usare il
manifest sintetico allegato. Il manifest distingue gli input esterni e non
include alcun dato dell'installazione fisica.
