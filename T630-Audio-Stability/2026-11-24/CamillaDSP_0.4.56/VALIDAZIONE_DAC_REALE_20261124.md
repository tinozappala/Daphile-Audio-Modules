# Collaudo reale DAC RME — CamillaDSP 0.4.56

**Tino Zappala, Milano — 24 novembre 2026**

## Hardware e protocollo

HP t630, RME ADI-2 DAC FS via USB 2-1, Daphile/Lyrion, CamillaDSP 4.1.3, codec DSF DSD64 in bypass DoP (ALSA 176.400 Hz). Il cambio a PCM mantiene la frequenza nativa del brano. `spacd` usa il nuovo comando `restart pcmC3D0p`.

Il collaudo è stato eseguito con file della libreria realmente riprodotti sul DAC, con un solo brano PCM aggiunto temporaneamente ai 15 brani DSF della playlist corrente; al termine di ciascun ciclo la coda originaria e il cursore di riproduzione sono stati ripristinati.

| Ciclo | Transizione | Stabilizzazione del percorso | Nuovi reset USB |
|---|---|---:|---:|
| 1 | DSD64 → PCM 96 kHz | 5,666 s | 0 |
| 1 | PCM 96 kHz → DSD64/DoP | 4,783 s | 0 |
| 2 | DSD64 → PCM 44,1 kHz | 6,074 s | 0 |
| 2 | PCM 44,1 kHz → DSD64/DoP | 5,006 s | 0 |

Per il PCM è stato confermato `pcm-active`, CamillaDSP `Running` e ALSA a frequenza nominale; per DSD `dsd-bypass` e ALSA a 176.400 Hz. Nessun campione DSD è stato trattato come PCM.

### Confronto con la 0.4.55

Il 24 novembre alle 01:38, la vecchia sequenza 0.4.55 aveva prodotto **due reset USB** durante gli handoff, con tempi di stabilizzazione riportati di **5,761 s** (PCM→DSF) e **7,954 s** (DSD→PCM 44,1). I due cicli 0.4.56 non hanno prodotto nuovi reset. I tempi risultano più favorevoli, ma le condizioni di prova non sono identiche: non si dichiara una riduzione percentuale misurata in A/B.

Il reset USB precedente era **intenzionale nel ramo card-wide del daemon Daphile**, non sintomo provato di degrado hardware del DAC. È stato evitato scegliendo il ramo player-scoped, lasciando intatto il fallback di recupero in caso di errore del player.

### Controlli e limiti

- Audio Host `running=true`, `recovery_required=false`, nessun errore dopo le prove.
- Nessuna variazione al kernel, alle affinità o ai profili di equalizzazione.
- Playlist di 15 brani e posizione originale ripristinate in entrambi i cicli.
- Il controllo di reset è ottenuto dal log kernel `usb 2-1: reset`, valore stabile a **10** durante i test; non esclude altre tipologie di interruzione che il kernel non abbia registrato.
- I tempi sono dalla selezione del brano alla conferma stabile del percorso, **non** una misura strumentale della durata del silenzio udibile.
- `xrun=null` non è prova di zero xrun. Non sono state eseguite misurazioni analogiche THD+N né test d'ascolto ciechi.

**Esito:** passano entrambi gli handoff PCM↔DSD sulla release 0.4.56 senza reset USB obbligatori. Resta opportuno monitorare la stabilità durante l'uso continuativo.
