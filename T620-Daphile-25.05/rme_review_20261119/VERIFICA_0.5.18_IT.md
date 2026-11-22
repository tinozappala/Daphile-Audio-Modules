RME 0.5.18, 21 novembre 2026

Corretto il richiamo dei preset EQ quando Load B/T è attivo e i toni restano spenti dopo la selezione. In precedenza il modulo presumeva che i toni si attivassero e annullava il richiamo. La nuova versione verifica bande e stato del DAC senza imporre quello stato ai toni.

Sul t620, dopo l'installazione con backup e verifica, il preset 2 prova due è stato richiamato su Line. La lettura del DAC conferma tutte le bande sinistra e destra della memoria, con toni OFF e nessuna operazione in sospeso. La memoria è rimasta invariata. Nove test backend locali superati.

I nove Setup del DAC non sono gestibili dal modulo. La tabella MIDI ufficiale RME pubblica i comandi di richiamo e scrittura ma non include una lettura del contenuto dei Setup già salvati. L'implementazione e le prove rimangono aperte per rispettare i Setup esistenti. Il salvataggio dei preset EQ è stato confermato funzionante dall'utente, senza una nuova prova di scrittura.
