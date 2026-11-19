# DaphileRME 0.5.12

Aggiornamento installato e verificato sul t620 con ADI-2 DAC e LMS 9.1.1. La [ISO RC7](../ISO-RC7/) resta la base di distribuzione e include RME 0.5.7.

- [Pacchetto installato](DaphileRME-0.5.12.zip)
- [Sorgenti completi](SORGENTI.zip)
- [Collaudo](COLLAUDO.json)
- [Pacchetto precedente per il ripristino](DaphileRME-0.5.11.zip)
- [Impronte SHA-256](SHA256SUMS.txt)

L’equalizzatore richiama e disegna i preset hardware con una sola selezione. Salva la curva con nome nel DAC. I nove Setup hanno una sezione separata. Il pannello mantiene la navigazione durante l’attesa e permette di rileggere il DAC se la risposta a un comando si interrompe.

Sul DAC reale il richiamo dello stesso preset su IEM scollegata è passato da 3,755 a 3,021 s. La prova ha ripristinato tutti i registri e preservato il preset. La conferma del richiamo richiede ancora la rilettura delle impostazioni prima e dopo il comando.

## Installazione e ripristino

Usare Audio Host compatibile e il suo flusso di integrazione con LMS. Conservare il pacchetto e la copia della distribuzione attiva prima della sostituzione. Verificare versione attiva, apertura del pannello e prima lettura DAC. Per il ripristino usare il precedente pacchetto 0.5.11 con la configurazione Audio Host salvata e verificare nuovamente il pannello. Le memorie hardware e lo stato utente sono esterni al pacchetto.

[Consegna su Drive](https://drive.google.com/drive/folders/1tAarABv13XcqmZdMMAwJB866ALkFZJDD)
