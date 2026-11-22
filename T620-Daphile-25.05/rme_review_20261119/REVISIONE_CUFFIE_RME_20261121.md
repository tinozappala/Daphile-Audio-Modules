# Revisione delle curve cuffie RME

Verifica del 21 novembre 2026 sul t620. La versione del modulo resta 0.5.22. Le memorie 4 Denon AH-D9200 e 5 HiFiMAN Ed XS sono state aggiornate, rilette su entrambi i canali e restano protette nel modulo da sovrascrittura. Il DAC e altri programmi possono modificare le memorie.

## Fonti

- Dati AutoEq calcolati dalla misura di oratory1990, [Denon](https://github.com/jaakkopasanen/AutoEq/tree/master/results/oratory1990/over-ear/Denon%20AH-D9200) e [HiFiMAN](https://github.com/jaakkopasanen/AutoEq/tree/master/results/oratory1990/over-ear/HIFIMAN%20Edition%20XS).
- [Indice dei profili RME di oratory1990](https://www.reddit.com/r/oratory1990/wiki/index/list_of_presets_rme_adi-2/), con le schede specifiche [Denon](https://www.dropbox.com/scl/fi/jx82thj7c5106re5r1j8z/Denon-AH-D9200-RME-ADI-2.pdf?rlkey=56wk31wk68cgtvac6mdhvncfo&dl=0) e [HiFiMAN](https://www.dropbox.com/scl/fi/j1qjys2mji5on935i320p/Hifiman-Edition-XS-RME-ADI-2.pdf?rlkey=mcnym15bte8w2j79avy9z44zf&dl=0).
- [Manuale RME ADI-2 DAC FS](https://rme-audio.de/downloads/adi2dacr_e.pdf), limiti di frequenza, passo e Q delle bande.

I profili RME di oratory1990 usano anche bassi e alti. Per rispettare la richiesta dell'utente di mantenere toni e loudness indipendenti, la revisione usa soltanto le cinque bande PEQ. Il nome e il contenuto dei toni memorizzati sono rimasti invariati.

## Metodo

L'adattamento a cinque bande è stato calcolato sulla colonna equalization dei CSV AutoEq delle misure di oratory1990. Ha usato un modello standard RBJ a 48 kHz, 20–16.000 Hz, campioni equidistanti in scala logaritmica, peso 1 fino a 8 kHz e 0,4 sopra 8 kHz. Il peso minore limita la dipendenza dalle alte frequenze misurate con un manichino. Le frequenze, i guadagni e i Q sono stati arrotondati ai passi e agli intervalli del DAC. Per la banda 1 è consentito il low shelf, per la 5 un peak oppure high shelf, le altre sono peak.

Lo scostamento RMS non ponderato rispetto alla curva di correzione AutoEq nella simulazione passa da 0,93 a 0,80 dB per Denon e da 1,14 a 0,36 dB per HiFiMAN. È un confronto fra curve numeriche, non una misurazione delle cuffie dell'utente. Il modello grafico RBJ approssima i filtri del DAC e non prova i coefficienti interni RME. Pad, posizione e variabilità fra esemplari richiedono comunque ascolto e possibili regolazioni.

## Nuovi valori

| Cuffia | Banda | Tipo | Frequenza Hz | Guadagno dB | Q |
| --- | --- | --- | ---: | ---: | ---: |
| Denon | 1 | Low shelf | 35 | 2,5 | 0,5 |
| Denon | 2 | Peak | 184 | -4,0 | 1,3 |
| Denon | 3 | Peak | 1098 | -3,0 | 1,5 |
| Denon | 4 | Peak | 2180 | 6,5 | 1,5 |
| Denon | 5 | Peak | 5320 | -4,0 | 5,0 |
| HiFiMAN | 1 | Low shelf | 62 | 5,5 | 0,5 |
| HiFiMAN | 2 | Peak | 204 | -2,0 | 0,5 |
| HiFiMAN | 3 | Peak | 1804 | 4,0 | 1,6 |
| HiFiMAN | 4 | Peak | 2910 | -4,0 | 3,2 |
| HiFiMAN | 5 | High shelf | 12550 | -7,0 | 0,8 |

Le curve simulate raggiungono rispettivamente +5,95 e +4,90 dB. Come margine iniziale usare una riduzione del segnale digitale a monte di circa 7 dB con Denon o 6 dB con HiFiMAN. Il volume utente non è stato modificato e l'eventuale margine dipende dall'intera catena e dagli altri effetti attivi.

## Verifiche e ripristino

Prima della scrittura sono stati confrontati i fingerprint delle due memorie con le copie precedenti e salvati i dati originali nell'area privata di recupero del t620. La memoria 5 è stata aggiornata mentre non era selezionata. Per la 4, attiva su Line, l'uscita è stata portata temporaneamente su Manual, quindi il nuovo preset è stato richiamato. Riletti valori, nomi e protezioni, con toni e loudness invariati.

Il profilo HiFiMAN è stato richiamato su Phones, con confronto delle cinque bande dei due canali e ripristino di Manual. Entrambi i tentativi di sovrascrittura tramite API normale sono stati respinti dalla protezione. Il calcolo dopo arrotondamento è stato ricontrollato usando la funzione grafica JavaScript già presente nel progetto. Non sono state fatte misure acustiche o prove di ascolto sulle cuffie reali.

L'archivio comprende le copie delle memorie prima e dopo, i dati AutoEq, la loro licenza, il calcolo e la verifica indipendente. Per riprodurre il calcolo servono Python 3.12, numpy e scipy e i percorsi relativi presenti nell'archivio. Le copie prima e dopo non contengono credenziali.
