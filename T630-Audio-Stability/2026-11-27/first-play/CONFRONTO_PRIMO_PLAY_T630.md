# Quanto attende il t630 prima del primo audio

Il 27 novembre 2026 il t630 ha riprodotto «A Time For Love», FLAC stereo
44,1 kHz/16 bit. L'RME ADI-2 DAC FS ha restituito il segnale digitale
prima dei propri effetti. Il brano contiene 2,511 s di silenzio iniziale.
Il kernel installato era 6.12.30-daphile, il modulo CamillaDSP 0.4.72.
Per il percorso DSP, il buffer Squeezelite era di 80 ms in otto periodi
e Camilla usava chunk da 2048 frame con target di due chunk.

| Stato prima del PLAY | Primo blocco con segnale letto entro | Brano riconosciuto entro |
| --- | ---: | ---: |
| EQ spento da STOP, Squeezelite sul DAC | — | 3,043 s |
| EQ acceso dopo un normale STOP, Camilla ancora sul DAC | 3,384 s | **4,008 s** |
| EQ acceso da freddo, DSP armato in attesa del primo PCM | 7,868 s | **8,490 s** |

Dopo lo STOP di «Unstoppable», Camilla ha mantenuto l'uscita USB. Al
successivo PLAY la musica era riconoscibile entro l'obiettivo di 5 s.
Prima del comando il ritorno era interamente a zero. L'analisi ha cercato
anche la firma del brano precedente senza rilevarla.

Da freddo il ritorno era ancora a zero nel blocco letto a 5,168 s. Anche
nel controllo senza registratore il puntatore USB di Camilla ha iniziato
ad avanzare solo a 6,593–6,664 s. Pausa, riavvio e ripresa del player hanno
richiesto circa 4,5 s complessivi nella prova con cattura.

Ogni condizione musicale è stata misurata una volta. I numeri riportano
i tempi di lettura e riconoscimento digitali. I tempi analogici e la
variabilità su più ripetizioni restano da misurare.

## Confronto sulla live USB .72

Sul medesimo t630 il preset completo e i controlli funzionali RME coincidevano.
I due FLAC sono stati copiati e riletti dalla RAM e i loro SHA-256 coincidevano
con gli originali NAS. La live aveva due brani in libreria. Sul disco interno
LMS indicizzava 67.626 brani e conservava una coda di 1.160 voci.

| Condizione sulla live | Avanzamento del tempo LMS osservato entro | Prima risposta con Camilla Running a 44,1 kHz entro |
| --- | ---: | ---: |
| EQ attivo da freddo, due prove | 6,162 e 6,293 s | 7,092 e 7,140 s |
| EQ attivo dopo STOP, due prove | 1,915 e 1,759 s | Camilla già attiva prima del PLAY |
| EQ spento, bypass nativo completo | 1,804 s | Bypass confermato in 15 letture |

Il comando del player instradato indicava buffer 80 ms e otto periodi.
Nella prima prova da freddo tre risposte consecutive indicavano il riavvio
del player. La risposta terminata a 4,93 s indicava la ripresa, quella a
6,15 s l'apertura del percorso PCM. L'attesa da freddo si ripresenta anche
con i file in RAM e la libreria minima. I tempi della live sono letture
software. Il primo campione del DAC è stato acquisito solo sul disco interno.

È emerso un secondo difetto. A coda vuota e in STOP, LMS 9.0.3 omette le
righe della playlist e l'indice. Il modulo .72 è entrato in recupero e il
bypass ha restituito HTTP 503. È bastato aggiungere un brano fermo,
ripristinare il bypass, quindi togliere il brano. Il secondo ciclo completo
ha applicato questo ordine automaticamente e ha restituito coda, preset e
tutte le uscite RME al loro stato iniziale.
