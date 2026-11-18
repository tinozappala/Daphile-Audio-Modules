AutoTune B4f — integrazione con Daphile RAM A7, 18 novembre 2026.

Portabilità: nessun modello di computer, produttore CPU o scheda madre è
richiesto. Uscite fisiche ALSA USB, PCI/HDA/HDMI e platform/SoC sono identificate
dalla connessione effettiva. Le regolazioni disponibili dipendono dalle
capacità pubblicate dal sistema. Interrupt e protezioni USB si applicano solo
a USB; un DSP esterno viene osservato senza modificarne i thread. Il codice
Python non aggiunge binari specifici per CPU. L'esecuzione resta nel sistema
Daphile/Linux e richiede le API native LMS, ALSA, tracefs e privilegi root.
Il supporto logico con fixture non certifica hardware fisico non collaudato.
USB senza seriale: identificazione tramite descrittori e directory sysfs
effettivamente aperta, inode e boot. Hotplug o reboot invalidano il vecchio
collegamento e richiedono una nuova verifica; nessuna vecchia policy viene
trasferita automaticamente a un dispositivo non identificabile come lo stesso.

La sorgente controllata è PCM locale e silenzio digitale. Non qualifica
streaming NAS, codec, DSD, tutti i sample rate, conversione bit-perfect o resa
analogica. I contatori di rete restano osservati; i test NAS attivi sono
separati e non fanno parte di questa calibrazione locale. Il limite CPU
85%, la riserva memoria e le soglie termiche restano obbligatori. Sensori o
tracce mancanti producono un esito esplicito, mai una qualifica inventata.

Le operazioni e i relativi processi di recupero condividono il blocco nativo della coda RAM. Non servono le vecchie directory di activity, i relativi registri di owner o gli hash dei sorgenti come condizione per avviare il test. Restano i controlli sul dispositivo, sullo stato reale del player, sulle modifiche concorrenti e sul recupero delle transazioni.

Prima del collaudo: RAM disattivata, player acceso in STOP con posizione inferiore a 0,25 secondi, shuffle disattivato, nessun gruppo sincronizzato e nessuna scansione LMS. Una condizione non soddisfatta viene segnalata prima di creare una sessione o modificare la riproduzione. Coda, ordine e duplicati vengono ripristinati dopo la sorgente PCM controllata.

Durata massima: 30 minuti, inclusi 5 minuti riservati al recupero. Il test conserva le impostazioni attuali se non dimostra un miglioramento ripetibile. Non costituisce una misura di qualità sonora.

Le impostazioni misurate salvate vengono ricontrollate sul sistema e sulla configurazione correnti. Una configurazione nativa diversa da quella salvata richiede una nuova calibrazione; non viene accettata tramite hash di vecchi programmi o timestamp presunti.

Audio Setup / AutoTune base. No signal processing or DAC control packages are included. Apply measured changes only; restore through the native Audio Setup panel. Persisted transaction journals require verified recovery before a new run.

This candidate supports the canonical V2 workflow: `dat automatic analyze`, `dat automatic run --confirm-audio-test`, `dat automatic status`, `dat automatic restore`, and explicit `dat restore pending` for interrupted operations. The shipped panel's Restore button invokes `automatic restore`.

Direct non-default native tune profiles remain guarded by the common kernel lease and original transaction write-ahead protocol. Legacy `tune default`, `tune experimental`, and non-pending `restore previous/baseline/default/last-known-good` refuse before effects. The historical REQUEST.txt records earlier requirements and examples; it is not a list of admitted candidate commands. The CLI --experimental option is retained for compatibility; in V2 it does not expand admissible controls or bypass capability, measurement or recovery checks. The 1500/1800/300-second limits remain unchanged.
