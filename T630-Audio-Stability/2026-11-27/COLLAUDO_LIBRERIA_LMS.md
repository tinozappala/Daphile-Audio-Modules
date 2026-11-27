# Selezione manuale dalla libreria sul t630

Il t630 ha avviato la .72 da USB. Il DAC è un RME ADI-2 DAC FS. La live dispone in RAM dei dieci FLAC originali del collaudo e di due DSF musicali. I dodici file sono stati riletti integralmente dopo il trasferimento e i loro SHA-256 coincidono con gli originali. LMS ha indicizzato tutti i dodici con normali URL di libreria. I FLAC totalizzano 992.685.358 byte e i due DSF altri 813.221.508 byte.

## Dieci brani con EQ spento

Tutti e dieci hanno superato la prova sul percorso nativo della live. Il DAC ha negoziato a ogni selezione la frequenza sorgente, da 44,1 a 352,8 kHz, con due canali e buffer di 80 ms. Squeezelite ha conservato il proprio PID. Nei nove cambi mentre il brano precedente suonava, il limite superiore osservato per l'avanzamento del tempo di LMS è stato 1,909–2,258 s, mediana 2,010 s. È una misura del trasporto software. Al termine, STOP, coda, preset e controlli RME sono stati verificati rispetto ai valori iniziali.

## Dieci brani con il preset Hifiman armato e due DSF

Con il convertitore LMS 0.3 armato, tutti i dieci FLAC sono avanzati sul player selezionato. La frequenza effettiva del DAC coincideva ogni volta con quella del file, il buffer era di 80 ms e il PID Squeezelite è rimasto stabile. Nei nove cambi l'avanzamento di LMS è stato osservato entro 2,338–5,368 s, mediana 3,267 s. Il massimo appartiene a «Hells Bells» a 192 kHz; la sua latenza audio precisa richiede una cattura dedicata.

«In a Mellow Tone» (DSF64) e «The Pink Panther» (DSF128) sono avanzati entrambi. L'uscita USB ha negoziato 705,6 kHz, stereo S32_LE e 80 ms in entrambi i casi. Il successivo «Unstoppable» ha riportato il DAC a 44,1 kHz. Il controllo diagnostico del selettore ha restituito FLAC anche durante i DSF, determinando due esiti incompleti nella matrice. L'RPC di ispezione della .03 costruisce `Song->new($client,0)`, che in LMS punta alla posizione assoluta zero. Il codice della conversione usa invece il brano effettivo passato da LMS. La misura del DAC e l'avanzamento di coda restano validi. Il filtro di ciascuno dei dieci FLAC e il bypass effettivo dei due DSF non sono certificati dal solo RPC. Per «Daddy» l'EQ è verificato dal confronto fisico indipendente riportato sotto.

Una verifica separata in STOP ha isolato a turno ciascuno dei dodici URL come unico elemento della coda. Nella posizione realmente letta dall'RPC, tutti i dieci FLAC hanno selezionato il convertitore WAV corretto e i due DSF sono stati esclusi. Tutti i dodici controlli sono riusciti, con ripristino della coda dopo ciascuno. Questo verifica la selezione del convertitore e l'esclusione dei DSF. Per distinguere il payload DSD/DoP dalla conversione PCM sul cavo serve un'acquisizione dedicata.

## Cambio da «Luv N’ Haight» a «Daddy»

La selezione porta l'uscita da 176,4 a 352,8 kHz. Il misuratore osserva dapprima il playback alla frequenza precedente, poi apre l'ingresso RME pre-FX solo quando il nuovo playback risulta attivo a 352,8 kHz. I timestamp sul t630 delimitano il comando manuale. Il tempo riportato riguarda il primo gruppo di 65.536 frame che identifica «Daddy», con conferma in una seconda finestra. L'attacco eventualmente precedente all'apertura dell'ingresso resta fuori dalla misura.

| Configurazione | Primo gruppo musicale riconosciuto entro | Scarto di allineamento fra le finestre |
| --- | ---: | ---: |
| EQ spento, percorso nativo della .72 live | 0,788 s | 0 frame |
| EQ acceso, convertitore LMS 0.3 su URL ordinari | 3,535 s | 0 frame rispetto al riferimento Hifiman |

Le correlazioni del nativo sono 0,99929 e 0,99969. Il processo Squeezelite resta lo stesso e l'uscita mantiene il buffer a 80 ms. Dopo la prova il trasporto e i controlli RME sono tornati allo stato iniziale. Le precedenti misure sull'installazione interna hanno identificato la stessa transizione entro 0,565 s in Daphile puro, 1,775 s con DSP flat e 1,980 s con EQ. Le acquisizioni usano finestre di durata diversa.

Con il preset attivo, la prima osservazione del playback USB a 352,8 kHz è arrivata entro 3,185 s. Il primo riconoscimento di «Daddy» contro la sorgente nativa ha dato due correlazioni di 0,90034 e 0,90819. Il confronto con un estratto storico filtrato ha dato una prima finestra entro 4,576 s. Il selettore LMS ha confermato il convertitore dedicato, il PID è rimasto stabile e il buffer effettivo era di 80 ms. La soglia richiesta di 5 s è rispettata in questa acquisizione. Sul cambio manuale la precedente misura con EQ sull'installazione interna è più breve. La misura del nuovo percorso è stata effettuata su due copie intere dei FLAC con verifica SHA a ogni apertura.

Per distinguere il preset dal segnale nativo sono stati calcolati separatamente i primi 12 s di «Daddy» con lo stesso profilo Hifiman a 352,8 kHz. Questo riferimento identifica il segnale filtrato già entro 3,535 s, con correlazioni 0,99981 e 0,99997. Sul medesimo tratto di 743 ms, dopo la stima di un solo guadagno comune, gli errori RMS energetici normalizzati sono 0,00315 rispetto al filtrato e 0,40301 rispetto al nativo. Nella cattura con EQ spento si osserva il comportamento inverso: 0,00350 verso il nativo e 0,39899 verso il filtrato. È un confronto dell'energia digitale per blocchi.

## Primo PLAY

Su «A Time For Love» il percorso originale Camilla da freddo ha richiesto fino a 8,398 s. Il pilota 0.2 ha avviato Camilla da LMS per due URL diagnostici e restituito il brano equalizzato entro 4,681 e 4,584 s. Le due letture e il controllo con EQ spento sono documentati in [Primo PLAY sul ritorno digitale RME](PRIMO_PLAY_DIGITALE_LIVE.md).

Con la .03, usando il normale URL FLAC selezionato in STOP e un nuovo processo Camilla al PLAY, il primo blocco con segnale è stato letto entro **4,295 s**. La stessa finestra di riconoscimento usata per il confronto col vecchio percorso è stata letta entro **5,043 s**. Il preset Hifiman coincide con il riferimento digitale e le due finestre mantengono lo stesso allineamento. Squeezelite mantiene lo stesso PID. Il miglioramento rispetto a 8,398 s è di circa 3,36 s; il limite superiore osservato supera di 43 ms l'obiettivo dei 5 s. Il FLAC comprende circa 2,51 s di silenzio musicale iniziale. Un tentativo precedente era stato interrotto prima del PLAY perché dopo «Daddy» il DAC era rimasto a 352,8 kHz e il misuratore richiedeva 44,1 kHz. L'acquisizione valida è stata fatta dopo il normale ritorno a un FLAC a 44,1 kHz.

La candidata .04 conserva l'identità verificata nel preflight ed evita di leggere tutto il FLAC prima di ciascun PLAY quando il file è immutato. Su Matteo, per una sorgente di 54.958.890 byte, l'apertura successiva ha letto 42 byte di intestazione invece dell'intero file. L'impatto sul t630 e la regressione fisica rimangono da misurare. La ISO .75 pubblicata contiene il modulo audio .74; entrambe le revisioni del convertitore LMS sono distribuite a parte.
