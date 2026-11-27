# Daphile Next Audio sul t630

## Riferimento fisico del 27 novembre 2026

Modulo DaphileCamillaDSP **0.4.71**, motore CamillaDSP **5.0.0-beta.2**,
kernel stock **6.12.30-daphile**, Squeezelite Loopback a **80 ms** in otto
periodi. Camilla usa blocchi da 2.048 frame e un livello obiettivo di 4.096.
L'archivio del modulo ha SHA-256
`c6add62c058907f27c40de0f6af2f599ad61ade645dd02a5c5ee390baf944847`.

Sul ritorno digitale pre-FX dell'RME ADI-2 DAC FS il brano a 352,8 kHz
è stato riconosciuto entro **1,856 s** dal comando LMS di cambio manuale
da 176,4 kHz. Con Daphile puro lo stesso limite superiore era **0,565 s**.
La serie di dieci FLAC a 44,1–352,8 kHz ha completato nove cambi entro
**3,472 s** alla terza conferma dello stato Running, con PID di Squeezelite
e CamillaDSP invariati.

Nel primo avvio da DSP armato il puntatore USB ha iniziato ad avanzare
fra **7,06 e 7,73 s** dal comando di selezione che avvia la riproduzione.
Il criterio di accettazione per il primo audio da fermo è **5 s**.

Sul kernel di laboratorio 6.12.117-rt23 le tracce ALSA della .71 hanno
contato un XRUN USB in 10 dei 12 cambi 96→48 kHz, in 4 degli 11 cambi
inversi a caldo e nello STOP finale. Il Loopback ha totalizzato zero XRUN
in questa prova e il trace non ha segnalato perdite. Lo stock non forniva
un contatore con copertura equivalente.

Nei segmenti catturati con profilo flat, 529.200 frame a 176,4 kHz e
1.058.400 a 352,8 kHz corrispondono alla sorgente sui 24 bit utili. Per
DoP64 sono stati verificati marcatori e payload di 529.200 frame.
Le finestre fra catture a rate diversi restano scoperte.

Il pacchetto di riferimento e i sorgenti provengono dal modulo esatto
usato per queste misure. Il binario Linux amd64 del motore corrisponde
allo SHA-256 `e8ab3b51ed01e40b86f3cff1a04cf3087ba938c22947732a60b36403ac95670f`.
[Release del motore e relativo sorgente](https://github.com/HEnquist/camilladsp/releases/tag/v5.0.0-beta.2)
