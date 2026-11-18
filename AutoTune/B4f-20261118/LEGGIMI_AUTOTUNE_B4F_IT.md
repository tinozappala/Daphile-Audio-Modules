# AutoTune B4f — aggiornamento e sorgenti

B4f corregge l'integrazione di AutoTune con Daphile RAM A7 e rivede il
riconoscimento hardware, il percorso di prova, le misure e il recupero.
Il rapporto `AUTOTUNE_B4F_REPORT.json` distingue i test software dal collaudo
fisico effettivamente completato. Gli algoritmi CamillaDSP/PEQ e il controllo
RME restano invariati. La consegna include Camilla 0.4.49 di manutenzione:
riduce il costo del monitor PCM mantenendone frequenza e verifiche di sicurezza.
Corregge anche il ricevitore della policy audio nativa: prima delle prove
verifica che abbia effettivamente sospeso le proprie regolazioni RT, mantenendo
attive le protezioni USB. Sono accettati i nomi legittimi contenenti spazi.

## Hardware e capacità

Il modulo non richiede t620, una marca di CPU o un modello di scheda madre.
Rileva uscite ALSA fisiche USB, PCI/HDA/HDMI e platform/SoC. Loopback e dispositivi
virtuali non vengono scambiati per il DAC. Un'uscita disabilitata non impedisce
di identificare quella realmente configurata; più uscite abilitate ambigue
richiedono una selezione nativa univoca.

Regolazioni e prove dipendono dalle capacità effettivamente rilevate.
Affinità e protezione USB si applicano solo quando esiste quel trasporto.
Se non è osservabile l'interrupt USB, non viene dichiarata una sua qualifica.
I thread di un processore DSP esterno vengono osservati senza cambiarne le
impostazioni. Sono disponibili identificazione alternativa al DMI e lettura
dei sensori CPU tramite hwmon e thermal-zone; valori mancanti restano ignoti.
Un DAC USB senza numero di serie viene vincolato alla connessione realmente
aperta, al suo inode e all'avvio corrente. Un cambio di dispositivo o un reboot
richiedono una nuova verifica; l'identità USB dotata di seriale resta compatibile.

Il modulo opera in Daphile/Linux con ALSA, LMS, tracefs e privilegi root.
Questa portabilità del codice non certifica ogni dispositivo o architettura.
La ISO Daphile RC6 già consegnata rimane un'immagine x86_64 con AutoTune B4e:
questo pacchetto è l'aggiornamento separato B4f e contiene i sorgenti completi.

## Installazione e recupero

Estrarre lo ZIP su DATA. Dalla directory `AutoTune-B4f`, eseguire sul sistema
Daphile con AutoTune abilitato e nessuna prova in corso:

```sh
python3 -I -B install_b4f.py --install
python3 -I -B install_b4f.py --verify
```

L'installatore riconosce i file della versione B4e di questa consegna e quelli
B4f già installati. Se trova sorgenti modificati, li conserva e interrompe
l'aggiornamento prima della prima sostituzione. Controlla concorrenza e
recuperi pendenti; riavvia soltanto il broker della GUI AutoTune. Non riavvia
LMS, CamillaDSP, RME o il computer, né avvia musica. Un errore di installazione
provoca il ripristino dei file appena sostituiti.

L'esito indica la directory privata di recupero in
`/mnt/hdd/system/autotune-updates/b4f-…`. Per annullare quella installazione,
usare il percorso esatto restituito:

```sh
python3 -I -B install_b4f.py --rollback /mnt/hdd/system/autotune-updates/b4f-PERCORSO_RESTITUITO
```

L'annullamento rifiuta file cambiati nel frattempo. I backup privati devono
restare separati dal pacchetto generico. Nessuna musica o configurazione
privata è inclusa nello ZIP.

## Camilla 0.4.49 e persistenza

`compatibility/` contiene il pacchetto Camilla 0.4.49 completo e l'approvazione
corrispondente. Deriva dalla 0.4.47 classica della RC6: cambia soltanto il
lettore del monitor PCM e due dichiarazioni di versione. Le altre 36 voci,
inclusi interfaccia, motore, routing e gate DoP, rimangono identiche.
La 0.4.48 con interfaccia sperimentale presente in GitHub è conservata separatamente.
Le fixture differenziali e il benchmark del solo lettore sono documentati
nella guida Camilla; la riduzione misurata non è una misura del carico totale.

Sul t620 l'attivazione di 0.4.49 ha aggiornato atomicamente sorgenti LMS,
catalogo e release Audio Host, osservando il nuovo processo pronto. Coda,
preset e token sono stati preservati; host globale e RME sono rimasti attivi.
Il precedente namespace 0.4.47 e il recupero privato restano disponibili.
`install_b4f.py` installa AutoTune e la policy nativa; l'aggiornamento Camilla
richiede l'integrazione coerente con Audio Host descritta nella sua guida.

L'installazione corrente usa l'overlay persistente su DATA della firmware
`fw261118b7rc6`. Il consolidamento di avvio conserva una versione Camilla più
recente della 0.4.47 inclusa nella RC6. Questi aggiornamenti non riscrivono la
ISO storica; passando a un'altra firmware occorre integrarli nuovamente.

## Uso e significato delle misure

Aprire Audio Setup / AutoTune e aggiornare l'analisi. Per la calibrazione:
RAM disattivata, player acceso in STOP con posizione inferiore a 0,25 secondi,
shuffle disattivato, nessun gruppo sincronizzato e nessuna scansione LMS.
PEQ, preset e impostazioni audio devono restare stabili durante il confronto.
Se cambiano il processore audio o la sua configurazione, la prova si interrompe
e il pannello spiega la necessità di ripeterla con impostazioni stabili.
Il pannello espone la causa effettiva di un rifiuto prima della riproduzione.
Il ripristino conserva ordine, duplicati e impostazioni della coda.

Il test genera silenzio PCM locale, mantiene la ripetizione del solo campione
per evitare l'intervento di Don't Stop The Music e poi recupera la coda.
Il confronto osserva riferimento, alternativa e nuovo riferimento ripristinato;
una proposta richiede verifiche ripetute e una conferma finale indipendente.
La durata massima è 30 minuti, inclusi 5 minuti per il recupero. Un risultato
che conserva la configurazione attuale è valido quando non emerge un beneficio.
Se nessuna alternativa è stata provata per mancanza di tempo, il risultato è
esplicitamente inconclusivo. Un solo picco CPU isolato nella prima finestra di
riferimento può consentire un secondo tentativo dopo due secondi di assestamento,
prima delle scritture. La finestra scartata è registrata; il limite dell'85%,
il budget iniziale e tutti i controlli restano invariati. Le finestre successive
alle modifiche non vengono ripetute con questa procedura.
Se, durante il confronto di un'alternativa, solo la finestra dopo il ripristino
supera il margine CPU, il confronto viene scartato dopo aver verificato
nuovamente configurazione, controlli e sorgente. La finestra resta registrata
e non viene ritentata; la ricerca può continuare. Un risultato con sole
alternative prive di un confronto valido resta esplicitamente inconclusivo.

Le metriche CPU usano campioni uniformi. Il decoder scheduler v5 verifica
prima l'ordine dei timestamp su ciascuna CPU e poi unisce i flussi verificati
in ordine cronologico. Questo corregge consegne fuori ordine tra CPU senza
modificare i timestamp o la traccia originale. Regressioni sulla stessa CPU,
eventi persi o righe non interpretabili invalidano comunque la misura.
Gli indici risiedono su file temporanei e la memoria cresce con il numero
di CPU, senza caricare in RAM l'intera traccia o duplicarne i byte grezzi.
La traccia misura la stessa finestra; può completare entro 100 ms solo un'attesa già aperta alla fine,
registrandone la latenza intera. Attese residue, perdita di eventi, identità
incerte, sovraccarico o dati incompleti impediscono la qualifica. Nuove attività
successive alla finestra e picchi del riscaldamento non alterano le metriche.

La calibrazione PCM locale non certifica streaming NAS, codec, DSD, tutti i
sample rate, trasparenza bit per bit o qualità analogica. I contatori di rete
restano osservati; le prove NAS attive sono separate. Non viene dichiarato
un ottimo globale né un miglioramento udibile.

## Riproducibilità

Il collaudo fisico conclusivo sul t620/RME è terminato in circa 11 minuti:
18 finestre qualificate, tre confronti completi (governor, priorità scheduler,
affinità CPU/interrupt), nessuna finestra respinta e nessun errore di recupero.
Il massimo carico per core nelle finestre è stato 71,72%, temperatura 54,125 °C.
L'esito è `retained-current`: le alternative non hanno dimostrato un beneficio
ripetibile e le impostazioni attuali sono state conservate. La ricerca non è
esaustiva: 36 alternative restano differite. Coda di 17 elementi, preset e
configurazioni native sono stati verificati invariati; fixture e tracce
temporanee sono state rimosse e i lock rilasciati. Camilla 0.4.49 è pronto e i
39 sorgenti installati corrispondono al suo pacchetto.

`payload/opt/daphile-autotune` contiene il runtime completo; `payload/var/www`
contiene il JavaScript aggiornato. `SOURCE_MANIFEST.json` e
`INSTALL_MANIFEST.json` descrivono i byte e i modi dei file. Gli hash sono
controlli di distribuzione/installazione e non vincoli di ammissione runtime
legati a una macchina o a vecchi sorgenti.

Le fixture si trovano in `tests/`. Per esempio:

```sh
python3 -I -B tests/measurement_window_tests.py --lib payload/opt/daphile-autotune/lib
python3 -I -B tests/generic_hardware_tests.py --lib payload/opt/daphile-autotune/lib
```

Le fixture non avviano l'audio. I test filesystem e lock richiedono Linux;
le prove carattere/dispositivo richiedono root. Le evidenze private complete
sono conservate nell'archivio di recupero, non nel pacchetto generico.
Gli operatori usati per interrogare il t620 reale restano nelle evidenze private;
le fixture distribuite non richiedono il suo indirizzo, la sua coda o i suoi accessi.
