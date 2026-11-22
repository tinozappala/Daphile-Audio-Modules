# Revisione RME 0.5.7

19 novembre 2026 · RME ADI-2 DAC del t620 · progetto Daphile

## Confronto RC6 e Daphile originale sul T620, 21 novembre 2026

La 0.5.15 attiva sul T620 corregge due transizioni del DAC osservate con
il preset hardware 1 su Line e `Load B/T with Preset` attivo. Il comando
EQ scrive `4:2`. Il DAC replica lo stato su `5:2` e commuta anche `4:20`
(abilitazione bassi e alti). Le letture prima e dopo contenevano 220
registri coerenti. Il controllo precedente rifiutava la variazione di
`4:20` e anche il tentativo di recupero la classificava come modifica
esterna. Le due transazioni sono state chiuse dopo doppia rilettura senza
inviare altri SET. [Nota firmware RME su EQ Off](https://forum.rme-audio.de/viewtopic.php?id=34364)

| Componente | RC6 precedente | Daphile 25.05-b251550 RT |
| --- | --- | --- |
| RME al momento del passaggio | 0.5.12 | Stesso archivio 0.5.12 sullo stesso volume dati |
| Kernel | 6.12.117-rt23-daphile-v6 | 6.12.30-daphile |
| ALSA e MIDI | `amidi` 1.2.16 | `amidi` 1.2.13 |
| Python | 3.13.18 | 3.13.3 |
| lighttpd | 1.4.85 | 1.4.78 |
| Avvio del broker audio | Dipendenza da `daphile-modules` | Dipendenza da `diskmount` con lo stesso broker sul volume dati |

L'archivio RME 0.5.12 verificato prima del passaggio e conservato sul
volume dati ha SHA-256 `868cc26a3b04dd9b422741f49f344e471949a1246fbdc2375a5dab2358950ce8`.
Il suo controllo, rieseguito offline sui due dump del DAC, rifiuta
entrambi proprio su `4:20`. La 0.5.15 accetta entrambi i dump e continua
a segnalare variazioni estranee alla transizione. Il journal Line che
precede la migrazione documenta una selezione preset (`4:28`), come il
collaudo storico cronometrato. Il motivo per cui la specifica combinazione
si sia presentata dopo il passaggio resta indeterminato.
[Collaudo 0.5.12](t620_preset_latency_candidate.jsonl)

Sul kernel della nuova ISO, le due build di `amidi` hanno letto in
alternanza lo stesso DAC. Due letture per build hanno restituito gli stessi
220 registri senza differenze nei valori. I circa quattro secondi per
campione comprendono la finestra di ricezione fissata dalla prova. Con
il percorso di produzione `alsa-rawmidi-get`, tre letture stock hanno
restituito 220 registri in 4,160, 1,449 e 1,387 s. Il confronto live
isola gli strumenti utente e usa il kernel della nuova ISO per tutti i
campioni. RC6 è stata ispezionata in sola lettura dal suo slot di recupero.

In 0.5.15 la GUI mostra il recupero accanto all'errore e permette di
avviarlo mentre blocca gli altri comandi. Il journal registra la fine
della richiesta per consentire il recupero con il controller in esecuzione.
Verificati sette test backend, il flusso di errore e recupero in Chromium,
la risposta del CGI installato, il preset Line 1 attivo e la lettura della
memoria 20. Camilla è rimasta in esecuzione.
[Ricevuta dell’installazione](t620_install_0.5.15_receipt.jsonl)

## Aggiornamento installato sul t620

La versione **0.5.9** mantiene le 20 memorie EQ nella pagina Equalizzatore,
con attivazione evidente dell’EQ e salvataggio direttamente nel DAC. I nove
Setup hanno una pagina dedicata in italiano. Le operazioni sui Setup restano
disponibili dal DAC e da ADI-2 Remote. Le sezioni usano icone e colori distinti.

La prova fisica ha scritto la copia Line nella sola memoria 20, prima vuota.
La trasmissione con flag a indice 2 ha aggiornato quel banco, mentre la
risposta è stata letta con il flag a indice 1. Il riscontro iniziale ha
rilevato una sola differenza sui toni, 7.960 → 7.900 Hz sugli alti, coerente
con i passi da 100 Hz del manuale. La 0.5.9 usa tale risoluzione. Il CGI
installato ha riletto e confermato la corrispondenza senza ripetere il SET.
Lo slot 1 precedente e la configurazione attiva sono stati conservati.
Il broker ha avviato il solo controller RME e ha tenuto disponibile il
recupero del pacchetto precedente. [Prova di scrittura](t620_store_proof.jsonl) · [Ricevuta d’installazione](t620_install_receipt.jsonl)

Il resto del documento descrive la revisione della 0.5.7 e la prima
candidata in sola lettura.

## Esito

La candidata concentra i preset in **Preset del DAC**. La pagina Equalizzatore rimanda alla stessa sezione. Il selettore mostra Manual e le 20 memorie EQ. Si può leggere una memoria senza applicarla, vedere le cinque bande e i toni, oppure richiamarla per Line, Phones o IEM. La sezione Setup mostra i nove nomi e l’opzione Load Volume. Salvataggio, nomi e operazioni sui Setup si gestiscono dal DAC o da ADI-2 Remote.

I quattro slot del plugin, il loro import e il loro richiamo sono rimossi dalla GUI. Il backend candidato ritira i mutatori degli slot e le API di backup del pannello RME. Mantiene `GET /api/slots` autenticata in sola lettura per recuperare dati preesistenti, senza consultare il DAC. Il file `preset-slots.json` resta recuperabile con la versione precedente. Sul t620 i quattro slot software risultavano vuoti al momento della lettura.

Il sorgente candidato è il delta in [DaphileRME](DaphileRME) sul pacchetto 0.5.7. I due ZIP RC4 e RC7 coincidono, SHA-256 `6593f7d98dcf1d38aba28ecbbce310f9fb160e28a6d09ccffd3a80f42f23fa88`. I 31 file eseguibili e di interfaccia del riferimento leggibile in [RC7](../iso_rc7_20261118/github_release/DaphileRME) coincidono con la RC4. Il delta cambia la pagina, il suo controller JavaScript, l’API RME e il controllo del richiamo. Il pacchetto Camilla è indipendente.

## Memorie del dispositivo

| Funzione | Semantica RME | Copertura Daphile |
| --- | --- | --- |
| EQ 1–20 | Banco persistente condiviso dai Setup. Il richiamo si riferisce all’uscita scelta e può caricare anche i toni secondo Load B/T. Il preset può avere curve L/R distinte. | Lettura qualificata storicamente per tutti i banchi. Richiamo e ripristino fisici storici su Line con Load B/T acceso e spento. La candidata usa lo stesso percorso di richiamo e corregge la verifica del caso già selezionato. |
| Manual, Temp, Clear | Manual è l’EQ modificabile. Temp conserva la modifica di un preset richiamato. Clear è una selezione lineare. Il protocollo corrente usa i valori 0, 1 e 22. | La candidata permette il richiamo di Manual e identifica Temp/Clear nello stato letto. La scrittura di Temp/Clear richiede una prova dedicata. |
| Setup 1–9 | Configurazioni complessive del DAC. Al richiamo l’EQ corrente salvato viene copiato in Manual. I 20 preset EQ sono condivisi. Load Volume controlla il richiamo del volume del Setup. | Nove nomi letti sul DAC. Load Volume è stato verificato fisicamente. Il protocollo documenta richiamo, salvataggio e nomi ma manca una lettura passiva del contenuto completo. La candidata lascia i comandi di Setup al DAC o all’app ufficiale. |
| Scrittura e nome EQ | Il DAC e ADI-2 Remote permettono salvataggio e rinomina. | Il foglio MIDI corrente indica indice 2 nel prospetto del flag di commit del banco temporaneo. Gli esempi di risposta e le catture fisiche leggono il flag all’indice 1. Serve una cattura della trasmissione ufficiale e una prova di scrittura con ripristino su un banco concordato. |
| File ufficiali | `.adieqpr` conserva un EQ. `.adistp` comprende lo stato corrente e il banco EQ secondo il manuale dell’app. Per acquisire i nove Setup a file occorre richiamarli singolarmente. | I file JSON dei vecchi slot contengono profili del plugin. Alcuni possono includere livelli, instradamento o impostazioni globali. Una conversione richiede decidere se salvare un EQ o un Setup e controllare i campi effettivamente trasferiti. |

Riferimenti primari RME. [Manuale ADI-2 DAC FS, §§13 e 14.2](https://rme-audio.de/downloads/adi2dacr_e.pdf) · [Manuale ADI-2 Remote, §§4 e 8](https://rme-audio.de/downloads/adi2remote_e.pdf) · [Protocollo MIDI ufficiale](https://rme-audio.de/downloads/adi2remote_midi_protocol.zip)

Evidenze storiche di registri, banchi e richiamo. [Lettura dei 20 banchi](../reports/rme_extension_readonly.json) · [Richiamo fisico e ripristino](../reports/rme052_final_all_groups_physical.json) · [Load Volume](../reports/rme55_load_volume_mapping.json)

## Flussi esaminati

1. **Avvio e accesso.** LMS espone la pagina di impostazioni. Il gestore audio genera il CGI RME, avvia il controller con stato persistente dedicato e usa un journal DAC condiviso con le operazioni che devono coordinarsi sul dispositivo. Il CGI inoltra a loopback con controllo di origine e cookie limitato al percorso. `/api/status` indica disponibilità HTTP del controller. La prontezza del DAC richiede uno snapshot valido.
2. **Lettura e stato della GUI.** La pagina apre la sessione, legge lo snapshot e visualizza capacità, stato osservato e selezione EQ per uscita. Il polling usa la cache di snapshot e scarta una risposta iniziata prima di un’operazione esplicita. La pagina candidata apre con due richieste e consulta i banchi solo su richiesta.
3. **Modifica dei controlli.** Filtri, volume, toni, percorso, display e impostazioni globali usano capacità per uscita e revisione dello snapshot. Il controller ricontrolla la revisione sotto lock, calcola scritture e dipendenze ed esegue il SET con journal e watchdog. Le modifiche che incidono su livello, mute, riferimento o EQ separato richiedono il readback dei registri interessati e degli effetti derivati.
4. **Richiamo e recupero.** Il richiamo nativo proietta banco e toni sullo stato dell’uscita, invia il selettore e verifica l’effetto. Undo usa l’ultimo journal per uscita. Il recupero riconcilia il precedente stato con quello fisico e deve rispettare cambiamenti esterni intercorsi.
5. **Collaudo e manutenzione.** Le prove RC4 collegano coda, flusso PCM, servizi e pagine HTTP. La lettura fisica dei preset va osservata a parte. Un’installazione successiva deve usare l’ingresso LMS/CGI con versione e archivio di recupero coerenti, senza riusare le procedure di rilascio storico come precondizione.

## Difetti e priorità

| Priorità | Rilievo e prova | Intervento |
| --- | --- | --- |
| Alta | **Commit senza undo.** Un `ENOSPC` persistente dopo il SET può lasciare `transaction.json` terminale, DAC modificato e salvataggio dell’undo assente. La fixture riproduce il mancato recupero dopo la ripresa. [Commit in 0.5.7](../iso_rc7_20261118/github_release/DaphileRME/backend/rme/lib/rme_control.py#L389) | Scrivere e rendere persistenti stato finale e undo prima del commit terminale. Recuperare un arresto in ciascun punto e verificare anche spazio esaurito persistente. |
| Alta | **Rollback prematuro.** Nella fixture asincrona un SET compare alla seconda lettura. La 0.5.7 ha già iniziato il rollback alla prima discordanza e termina `failed-restored`. [Verifica in 0.5.7](../iso_rc7_20261118/github_release/DaphileRME/backend/rme/lib/rme_control.py#L385) | Rileggere entro un budget complessivo che tenga conto del tempo di assestamento del DAC prima del rollback. Usare il verificatore già presente dove compatibile. Evitare ripetizioni del SET. |
| Alta | **Recupero legato alla vita del servizio.** Il controller prova il recupero prima di aprire il socket. Dopo `rollback-incomplete` il PID del server può far sembrare ancora attiva una richiesta conclusa. Il watchdog può poi terminare il processo intero. Riscontro nel codice. [Avvio](../iso_rc7_20261118/github_release/DaphileRME/backend/worker.py#L53) · [Recupero](../iso_rc7_20261118/github_release/DaphileRME/backend/rme/lib/rme_control.py#L439) | Separare lo stato della richiesta da quello del processo. Esporre diagnosi in sola lettura e bloccare nuove mutazioni fino a recupero osservato. Usare il lock per sincronizzare il recupero esplicito. |
| Alta | **Falso richiamo verificato.** Con il selettore già sullo slot e banca discordante, l’esecuzione precedente scartava il solo SET e dichiarava `no-change`. Riprodotto su cattura reale con 30 valori discordanti. [Correzione](DaphileRME/backend/rme/lib/rme_control.py#L218) | Corretto nella candidata. Restituisce no-op solo con tutta la proiezione coerente e rifiuta il caso discordante prima del SET. |
| Alta | **Richiamo software parziale.** La vecchia GUI suddivide un profilo software in più transazioni indipendenti per EQ e, se inclusi, livelli e stato del dispositivo. Un errore a metà lascia applicati i passi precedenti e Undo vede solo l’ultima transazione. [Richiamo precedente](../iso_rc7_20261118/github_release/DaphileRME/backend/rme/ui/studio.js#L298) | Flusso ritirato nella candidata. Un eventuale trasferimento di vecchi dati deve operare sui soli campi rappresentabili in una memoria nativa. |
| Media | **Bozze e risposte incerte.** Rileggi DAC svuota la coda locale prima di sapere se la nuova lettura riesce. Il retry esplicito dopo errore di rete usa ancora la revisione precedente, che il server protegge ma può costringere a una riconciliazione manuale. La banca letta in anteprima può essere modificata da un altro controller prima del richiamo. [Coda e lettura](DaphileRME/backend/rme/ui/studio.js#L17) · [Richiamo](DaphileRME/backend/rme/ui/studio.js#L369) | Conservare la bozza fino a lettura riuscita, riconciliare prima del retry e includere un identificatore del contenuto quando l’utente richiama una banca già letta. |
| Media | **Conflitti su registri volatili.** La revisione include parametri grezzi, compresi clock/frequenza, e Undo confronta l’intera proiezione. Un cambiamento indipendente può rendere impossibile l’annullamento. [Revisione](../iso_rc7_20261118/github_release/DaphileRME/backend/rme/lib/rme_peq.py#L66) · [Undo](../iso_rc7_20261118/github_release/DaphileRME/backend/rme/lib/rme_control.py#L421) | Qualificare quali registri sono volatili e confrontare quelli rilevanti per la mutazione e per le sue dipendenze. |
| Media | **Avvio seriale e lavoro periodico del broker.** Il broker sincronizza RME e Camilla prima di accettare richieste. Una readiness fallita può occupare 8 s. Con i due moduli RC4 abilitati calcola quattro `lstat` per 73 file a ogni ciclo di circa 2 s e pubblica lo stato anche identico con due `fsync`. Dopo il ritiro del RME duplicato un singolo campione a riposo mostra broker 7,130% di un core e RME 0,155%. | Fare un solo `lstat` per file quando serve, pubblicare cambiamenti reali e distinguere installazione da controllo periodico. Misurare prima e dopo con gli stessi moduli e coda. |
| Media | **Trasporto avviato da un server multithread.** Il SET usa `Popen(preexec_fn=…)`, che Python documenta come non sicuro con più thread. Il difetto potenziale è confermato dal codice. [Invio del SET](../iso_rc7_20261118/github_release/DaphileRME/backend/rme/lib/rme_control.py#L82) · [Documentazione Python](https://docs.python.org/3/library/subprocess.html#subprocess.Popen) | Conservare il limite di vita dei processi e impostare la protezione nel processo helper dopo exec. |
| Media | **Misuratore e operatore storici.** Il misuratore cerca sottostringhe nella riga di comando e registra anche se stesso. Usa inoltre mtime e un’epoca derivata dall’orologio civile. Le procedure consolidate si aspettano un trasporto storico assente. La prova fisica riapplica la fotografia precedente senza controllo di cambiamenti concorrenti della coda. [Misuratore](../ram_continuous/boot_timing_body.py#L5) · [Operatore](../DAPHILE_FINALE_RC4_20261116/operatori/test_corrected_native_state_on_RME.py#L22) | Misurare con clock monotono, identificare servizi per argv e PID con istante di nascita, osservare postcondizioni e abbandonare il ripristino se l’utente ha aggiornato la baseline. |

## Riavvio del 17 novembre

Nel reboot personale RC4 `c9abf832-a1b0-4089-bb17-96a7bb4f06d1` i tempi partono dall’avvio Linux.

| Fase | Secondi |
| --- | ---: |
| Cache RAM pronta per 17 file e 471.916.945 byte | 62,249 |
| Processo LMS avviato | 81,230 |
| Processo squeezelite avviato | 101,290 |
| Coda ripristinata | 112,793 |

La preparazione RAM era sul percorso sincrono prima di LMS. Fra cache pronta e coda pronta sono passati 50,544 s. RC5 A7 ha poi spostato la preparazione sul brano richiesto. Le misure fisiche di coda pronta sono **89,612 s in RC5** e **86,319 s in una successiva RC6**. Cambiano versione e condizioni. Mancano spegnimento, firmware/GRUB, riferimento omogeneo precedente e prima lettura HID RME, quindi il raddoppio percepito non ha una causa isolata. [Traccia RC4](../ram_continuous/BOOT_TIMING_FIRST.stdout) · [Rapporto RC5](../DAPHILE_RAM_A7_RC5_20261117/RAPPORTO_FINALE_RC5.json) · [Evidenze RC6](../ram_continuous/history/RC6_MANUTENZIONE_CONCLUSA_20261118.md)

Un altro reboot RC6, arrivato a 326,381 s, contiene 246,905 s di FullTextSearch sincrono LMS. Il RME duplicato che occupò quasi un core fu osservato durante AutoTune e poi ritirato. Entrambi sono eventi separati dal reboot personale RC4. Nello stesso trasporto RME della 0.5.7 le letture fisiche storiche richiesero 4,11 s a freddo e 1,11–1,39 s a caldo. Il controller senza recupero pendente espone HTTP prima della prima lettura HID. [Misure trasporto](../reports/rme_receive_speed_probe.json) · [Cronologia corrente](../ram_continuous/CURRENT.md)

Per attribuire il ritardo serve una traccia di spegnimento e avvio con la stessa coda e lo stesso stato del DAC, registrando rete, HTTP, LMS pronto per RPC, prima lettura HID valida e coda pronta. I tempi di boot disponibili e le letture di questo incarico sono stati raccolti senza un ulteriore reboot.

## Verifiche della candidata

- `python -B -X utf8 rme_review_20261119/test_backend.py`. Due casi con server HTTP reale e risposte MIDI da catture del DAC. Verificati gli slot software in sola lettura, i mutatori ritirati, lo snapshot, la lettura dei banchi, Manual e il rifiuto prima di qualsiasi SET in caso di banca discordante.
- `node rme_review_20261119/test_presets.mjs`. Chromium su loopback con DAC simulato. Verificati navigazione, 20 slot e Manual, nove Setup, memoria vuota, anteprima senza applicazione, uscita Phones, due richiami e rifiuto di una risposta priva di conferma. In questo ambiente il test ha richiesto `RME_TEST_CHROME_NO_SANDBOX=1` per il processo Chromium della fixture.
- [Lettura t620 con versione installata](t620_readonly.jsonl). CGI 200 in 0,512 s, snapshot in 1,350 e 1,429 s, nove nomi Setup, quattro slot software vuoti. Slot EQ 1 popolato e attivo su Line, 2 e 20 vuoti. Snapshot prima e dopo con stesso dispositivo e stessi controlli, EQ e impostazioni globali. La scansione iniziale di tutti i banchi ha esaurito il timeout. La prova attuale copre quindi tre banchi e la prova storica tutti e venti.
- [Lettura candidata sul DAC fisico](t620_candidate_readonly.json). I quattro file candidati sono stati sovrapposti al controller reale in una cartella temporanea del t620, con server limitato a loopback. Verificati pagina, sessione, JavaScript, nove nomi Setup, banchi 1 e 20 e due snapshot equivalenti. Zero SET. PID di produzione invariato, controller di prova terminato e cartella rimossa. Il CGI installato è stato verificato separatamente con la 0.5.7.

La candidata resta separata dalla release installata. Scrittura e rinomina di preset EQ, richiamo e salvataggio dei Setup, nuovo reboot e pubblicazione di un nuovo ZIP/ISO richiedono collaudi distinti. La prima fase di eventuale rilascio è la verifica di recupero dopo interruzione e del percorso CGI con il pacchetto candidato. I dati letti sul t620 rendono superflua una migrazione dei quattro slot RME al momento della rilevazione.
