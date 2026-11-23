# Risultati verifica della release 0.4.54

**Tino Zappala, Milano - 2026**

- Installazione con backup della release 0.4.53, del catalogo approvazioni e dei profili PEQ.
- Bypass nativo verificato prima dello scambio atomico della directory plugin.
- Nuova release riconosciuta dal gestore: `running=true`, `recovery_required=false`, nessun errore di caricamento.
- Profilo HiFiMAN Edition XS ripristinato, motore PCM `Running` e `connected=true` a 192 kHz.
- Carico CPU prima: [28.17, 28.17, 28.67] %; dopo: [21.83, 22.00, 22.33] %; miglioramento mediano 21.9%.
- Campioni clipped: 0; campo xrun: null (nessuna segnalazione di xrun).
- Affinità verificate: LMS 0–1, Squeezelite 3, CamillaDSP 3, IRQ USB xHCI 2.
- Una prova di riavvio reale successiva all'installazione è documentata separatamente; non inferire persistenza da questo documento.
- La gestione priorità scanner `nice +10`/`ionice -c2 -n7` è conservata nel guardiano CPU distribuito in `../CPU_Affinity/`.

Test non svolti: sessione di ascolto prolungata sotto scansione massiva, misura analogica di jitter/THD+N, stress automatico di coda DSD/DoP, confronto di `cpuset` su kernel alternativo.
