# Equalizzatore acceso e spento sul t630

Il 27 novembre 2026 il cambio manuale da «Luv N’ Haight» a «Daddy»
(176,4 → 352,8 kHz) ha prodotto il nuovo brano sul ritorno digitale
pre-FX dell’RME ADI-2 DAC FS entro i tempi seguenti.

| Configurazione su kernel stock | Nuovo brano riconosciuto entro | Come è stato riconosciuto |
| --- | ---: | --- |
| Daphile puro | 0,565 s | Cattura digitale nativa confrontata con il brano |
| Modulo .72 con EQ spento | 1,775 s | 1.024 frame stereo consecutivi identici al riferimento digitale nativo |
| Modulo .72 con EQ acceso | 1,980 s | Firma del brano dopo l’equalizzazione |

Sono limiti superiori ottenuti in acquisizioni distinte. Sulla .72 con
EQ spento il nuovo playback USB avanzava fra 1,499 e 1,603 s e con EQ
acceso fra 1,601 e 1,704 s. Squeezelite e Camilla hanno conservato il
proprio processo in entrambi i cambi. Il confronto riguarda una selezione
manuale durante la riproduzione, su kernel stock 6.12.30.

Sul medesimo boot RT 6.12.117-rt23 sono stati riprodotti gli stessi dieci
FLAC in due serie, con Squeezelite a 80 ms/8 periodi e Camilla a 2048
frame per blocco, obiettivo due blocchi. Nella serie EQ spento il primo
PLAY ha preparato il percorso PCM. Il comando di disattivazione è stato
inviato mentre suonava il primo brano, prima della sua finestra di misura.
Camilla ha restituito la configurazione flat su tutte le venti letture
prima e dopo le dieci finestre. Entrambi i canali e il fader erano a
0 dB e l’uscita ALSA USB apparteneva al medesimo motore interrogato.

| Misura nelle due serie | EQ acceso | EQ spento |
| --- | ---: | ---: |
| Mediana dei nove cambi fino alla terza conferma software | 2,946 s | 2,867 s |
| Intervallo degli stessi nove cambi | 2,731–3,252 s | 2,663–2,993 s |
| Osservazioni Running | 843/843 | 849/849 |
| Osservazioni Running nei 300 s a 192 kHz | 516/516 | 520/520 |
| XRUN ALSA con tracepoint disponibili | 0 | 0 |
| Eventi persi nel tracing | 0 | 0 |
| Contatore clipping nelle finestre | 0 | 0 |
| Carico mediano del solo processo Camilla, % di un core | 2,67% | 1,84% |

Lo STOP → PLAY è una prova separata. In una prima acquisizione con EQ
spento mentre il player era già fermo, il modulo ha mantenuto il percorso
nativo e Squeezelite ha continuato a inviare frame USB a 44,1 kHz anche
prima del comando PLAY. Il puntatore del DAC non identifica l'inizio
del brano in questa condizione. La successiva prova con registrazione
RME continua ha riconosciuto «A Time For Love» entro **3,043 s** dal
marcatore immediatamente precedente all'unico PLAY, con 73 finestre
stereo identiche al FLAC. Nessuna delle finestre identificate precedeva
il comando. Il FLAC contiene già 110.720 frame stereo iniziali a zero,
pari a **2,511 s** a 44,1 kHz. Coda, profilo, mute, loopback RME e
buffer ripristinati. Con EQ inizialmente acceso e percorso
ancora da attivare, il primo avanzamento USB di Camilla è stato osservato
circa 7,4 s dopo il marcatore iniziale. Il target concordato per il primo
audio riconoscibile da STOP è 5 s.

Lo ZIP del modulo .72 ha SHA-256
`acab2f0797c8bc871a2454a411ba68a506a61fd986730889ed00962d0c968b52`.
