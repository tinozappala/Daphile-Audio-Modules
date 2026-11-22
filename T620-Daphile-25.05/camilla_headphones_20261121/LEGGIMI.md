# Profili cuffie per CamillaDSP sul t620

Il modulo DaphileCamillaDSP 0.4.50 protegge i due profili dalla cancellazione nel pannello e sul server. Si possono modificare e salvare entrambi. Il pacchetto installato è incluso insieme ai parametri originali in HEADPHONE_PROFILES.json. Nell'interfaccia Camilla, selezionare il modello nel menu dei preset. Per ascoltare la sola correzione Camilla, attivare il percorso Camilla e disattivare il PEQ RME sull'uscita usata.

| Cuffia | Filtri per canale | Preguadagno |
|---|---:|---:|
| Denon AH-D9200 | 10 | −7,2 dB |
| HiFiMAN Edition XS | 10 | −5,8 dB |

Le frequenze, i guadagni e i Q riprendono le elaborazioni parametriche AutoEq delle misure oratory1990. I due canali sono collegati. Il calcolo su 6.000 frequenze fra 20 Hz e 20 kHz per ognuna delle sette frequenze PCM dà circa 1 dB di margine per una sinusoide a regime. L'avviso del pannello usa un limite cautelativo che somma i massimi delle singole bande. I picchi musicali possono differire dalla stima sinusoidale. Le curve sono riferimenti di modello e la risposta sul singolo ascoltatore dipende anche dal posizionamento e dai cuscinetti.

Fonti: [Denon AH-D9200](https://github.com/jaakkopasanen/AutoEq/tree/master/results/oratory1990/over-ear/Denon%20AH-D9200) e [HiFiMAN Edition XS](https://github.com/jaakkopasanen/AutoEq/tree/master/results/oratory1990/over-ear/HIFIMAN%20Edition%20XS).

Le ricevute INSTALL_050.jsonl e PROFILI_VERIFICATI.jsonl registrano installazione, modifica e protezione dei preset sul t620. Alla fine delle prove il percorso audio era in bypass e il profilo precedentemente selezionato era conservato. La verifica del motore ha riguardato la validità delle configurazioni. L'ascolto e la risposta acustica delle cuffie dell'utente restano da verificare sul posto.
