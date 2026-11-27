# Primo PLAY sul t630 con ritorno digitale RME

Il 27 novembre 2026 il t630 dalla live USB .72 ha riprodotto «A Time For Love», FLAC stereo 44,1 kHz con circa 2,51 s di silenzio iniziale. Il preset cuffie e i due FLAC di riferimento coincidevano con quelli della prova su disco interno. Il buffer richiesto è di 80 ms in otto periodi; in riproduzione nativa il DAC ha negoziato 3.528 frame e periodi di 441 frame.

| Condizione nella live .72 | Primo blocco con segnale letto entro | Prima finestra che identifica il brano letta entro |
| --- | ---: | ---: |
| EQ spento, FLAC locale da STOP | 3,245 s | 3,995 s |
| EQ acceso, Camilla locale armata da freddo | 7,649 s | 8,398 s |
| EQ acceso, Camilla locale dopo STOP caldo | 3,277 s | 4,030 s |
| WAV locale con lo stesso EQ già applicato, da STOP | 3,115 s | 3,749 s |

I numeri sono limiti superiori alla lettura del segnale digitale pre-FX. Il controllo nativo parte dalla coda vuota con una singola selezione e PLAY. Per il DSP locale il brano è già selezionato in STOP e il comando misurato è PLAY. Ogni riga riporta una singola acquisizione. I confronti precedenti sul disco interno hanno misurato 3,043 s, 8,490 s e 4,008 s al riconoscimento, con una procedura di cattura differente.

Il misuratore LMS temporaneo gira con l'utente ordinario del media server e legge per 16 s l'ingresso del DAC con loopback pre-FX. Conserva l'energia di ciascun canale per blocchi da 256 frame. I timestamp provengono dal clock monotono del t630 e indicano quando termina la lettura di un gruppo di blocchi. Richieste temporizzate prima e dopo il PLAY delimitano lo scarto rispetto al clock del controllore. Il riconoscimento mette a confronto due finestre stereo distanti 2,322 s con il riferimento nativo e con quello prodotto dallo stesso preset su Matteo.

Nel controllo nativo le due correlazioni con il brano sono 0,99988 e 0,99890. Le due finestre presentano lo stesso allineamento. Con Camilla dopo STOP anche l'allineamento del riferimento filtrato coincide. Nel primo PLAY freddo il riferimento filtrato è riconoscibile in entrambe le finestre (0,9993 e 0,9930), ma l'allineamento differisce di 512 frame, circa 11,6 ms. La misura non identifica la causa di questo scarto. L'API Camilla non ha fornito un contatore XRUN in queste prove.

Per il controllo con EQ precalcolato, i 18 s di PCM prodotti su WIN e su Matteo coincidono byte per byte. Il WAV è stato riletto dalla RAM del t630 prima del PLAY. Le finestre del ritorno pre-FX sono allineate al riferimento filtrato e la prima presenta correlazione 0,99995, con guadagno comune di circa 0 dB. In questa prova l'elaborazione era già terminata alla partenza del cronometro.

In tutte le condizioni i blocchi sicuramente letti prima del comando erano silenziosi. Al termine di ciascuna procedura sono stati verificati STOP, coda vuota, preset, mute e loopback originari. La prova del cambio manuale «Luv N’ Haight» → «Daddy» sul disco interno aveva già misurato 1,980 s con EQ acceso, 1,775 s con DSP piatto e 0,565 s con Daphile puro.

La candidata ISO .75 corregge l'installazione USB ed è stata verificata in UEFI e BIOS virtuali. Contiene il modulo audio .74. La correzione per il primo PLAY richiede una prova separata sul t630.
