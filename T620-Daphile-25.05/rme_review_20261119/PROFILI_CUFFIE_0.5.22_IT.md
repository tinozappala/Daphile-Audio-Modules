PROFILI CUFFIE RME 0.5.22

Due memorie EQ del DAC, salvate e rilette il 21 novembre 2026:
4 Denon AH-D9200
5 HiFiMAN Ed XS

I file JSON riportano i parametri letti dal DAC, su entrambi i canali. La base sono le prime cinque bande dei profili AutoEq da misure oratory1990, verso la curva di riferimento Harman per cuffie. Fonti:
https://github.com/jaakkopasanen/AutoEq/blob/master/results/oratory1990/over-ear/Denon%20AH-D9200/README.md
https://github.com/jaakkopasanen/AutoEq/blob/master/results/oratory1990/over-ear/HIFIMAN%20Edition%20XS/README.md
https://assets.denon.com/DocumentMaster/UK/AH-D9200_EN01.pdf
https://www.hifiman.com/products/detail/315

Le bande sono arrotondate ai passi del DAC. Per XS Q 0,33 è stato portato al minimo disponibile 0,5: la risposta risultante è solo un'approssimazione e non è stata misurata sulle cuffie dell'utente. Entrambe le curve lasciano a zero i guadagni bassi e alti della memoria. Non includono la riduzione del preamplificatore raccomandata dai profili AutoEq. Ridurre il livello digitale a monte di circa 7 dB per la Denon e 5 dB per XS è un punto di partenza per ottenere margine. Il comando volume del DAC non è stato cambiato, e il margine va valutato in base alla catena audio.

Il modulo impedisce di salvare sopra le memorie 4 e 5, e le disabilita nella lista di destinazione. Resta possibile modificarle dal DAC o da altri programmi. Le copie JSON contengono i valori completi riletti per il ripristino. Controllo fisico: valori e nomi riletti dalle due memorie, stato DAC iniziale invariato, due richieste di sovrascrittura rifiutate dal modulo. Richiamo e ascolto con le cuffie non sono stati provati.
