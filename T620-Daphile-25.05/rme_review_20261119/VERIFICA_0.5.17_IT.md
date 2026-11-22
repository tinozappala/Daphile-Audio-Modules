RME 0.5.17 installata e verificata sul t620 il 21 novembre 2026

Questo aggiornamento sostituisce la RME 0.5.16 nella consegna Daphile originale. Camilla e la ISO sono quelle della consegna precedente.
Pacchetto RME SHA-256 70123905255ace891361574a07dbff610d0cb901d46a32b67fc643cf2832f351.

Sul t620 il controller 0.5.16 non partiva. Il journal di EQ ON in Manual considerava imprevista la riattivazione dei toni da parte del DAC con Load B/T with Preset attivo. Due riletture hanno confermato il comando su entrambi i canali e l'effetto sui toni. La frequenza di campionamento era cambiata nel frattempo. Riconciliazione sotto lock con copia privata del journal, senza ulteriori scritture MIDI. Avvio del solo controller RME.

La 0.5.17 permette di regolare le cinque bande da Manual, Temp e memorie 1–20. Le modifiche a un preset possono spostare l'ascolto sulla curva Temp. Salva EQ nel DAC conserva la curva nella memoria scelta. Regolazioni dei toni e Dual EQ abilitate anche su preset. Un campo numerico incompleto non sospende gli altri comandi. La rilettura aggiorna le abilitazioni.

Prove sulla macchina fisica via CGI
- Installazione con backup, verifica dei file e delle impostazioni pre/post. Processo Camilla conservato.
- Phones, preset 1: modifica del Q in tutte le cinque bande, rilettura sinistra e destra, selettore Temp osservato, fingerprint della memoria invariata. Annullamento verificato.
- Phones: commutazione toni, Dual EQ e EQ con rilettura e annullamento dopo ogni comando. Ritorno a Manual e confronto delle impostazioni di tutte le uscite, dispositivo e curve.
- Line, Manual con Load B/T attivo: EQ OFF e ON. Entrambi i canali e lo stato dei toni hanno seguito il comando. Rilettura finale identica alle impostazioni iniziali; nessun journal pendente.
- Nove test backend locali. Chromium con DAC simulato per grafico su preset, lettura, recupero e interruttore.
- Il clock e la frequenza del segnale sono letture del DAC. Restano i vincoli fisici o di modalità, ad esempio riferimento fisso IEM e blocco volume. La prova fisica dei cinque filtri riguarda il Q; non è un collaudo esaustivo di tutte le combinazioni.

Il file ZIP comprende pacchetto installato, sorgenti completi nel pacchetto, delta di build, test e operatori. Estrarre insieme ai sorgenti della consegna precedente e usare python -B rme_review_20261119/deploy_t620.py per ricostruire il pacchetto.
