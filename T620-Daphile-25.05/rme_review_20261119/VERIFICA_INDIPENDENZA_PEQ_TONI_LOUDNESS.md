# Verifica PEQ, toni e loudness

Il 21 novembre 2026 sono state lette le tre uscite. Load B/T with Preset era OFF su Line, Phones e IEM.

Prove fisiche via CGI sull'uscita Phones, con Load B/T OFF e ripristino delle impostazioni iniziali dopo ciascuna modifica:
- accensione/spegnimento EQ e variazione del guadagno di una banda mantengono invariati i valori e gli interruttori dei toni e del loudness;
- variazione di Tone Enable, Loudness Enable, guadagno bassi e guadagno bassi del loudness mantiene invariata la curva e l'interruttore EQ;
- richiamo del preset EQ 5 HiFiMAN conserva le impostazioni di toni e loudness;
- richiamo Manual e confronto finale con l'istantanea iniziale completati.

Un controllo ha ricevuto HTTP 409 perché il controller era occupato. Dopo una nuova lettura dello stato e nessuna scrittura per quella richiesta, le prove mancanti sono state completate. Le prove dirette riguardano Phones, non tutte le combinazioni delle tre uscite. Il comportamento con Load B/T ON è stato osservato in una prova precedente: il DAC può attivare o disattivare i toni quando cambia EQ. Il manuale RME dichiara che il richiamo dei preset carica i toni quando Load B/T è ON.

Il loudness agisce sul segnale insieme a PEQ e toni e la sua intensità dipende dal volume e dal riferimento. I valori di questi controlli sono separati, ma le loro curve contribuiscono insieme alla risposta complessiva.

Fonte: https://rme-audio.de/downloads/adi2dacr_e.pdf
