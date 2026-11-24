# DaphileCamillaDSP 0.4.57 RC3 — consolidamento 24 novembre 2026

**Tino Zappala, Milano - 2026**

Release installata su HP t630, LMS 9.0.3, CamillaDSP upstream 4.1.3. Configurazione persistente chunksize 1024, target_chunks 4. Pacchetto installato SHA-256: b14886e70fc1dcc4e4aa5da3a5ce0859a739bfdef4545c1ae367156c04b050a7. Rollback 0.4.56: 71b783d1d86cd16d4e0a91b206c3f1a4f3788550f8f87fc005df3823146f309e.

Cinque file modificati: mapping ALSA S24_3LE, versione, frequenza 352800 nel modello e nell'interfaccia, compatibilità LMS nel manifest. I restanti 32 sorgenti sono identici alla 0.4.56. Conservati licenze originali e sorgenti esatti; attribuzione del consolidamento in questo documento per non alterare gli hash. Eseguibile upstream escluso.

## Qualificazione verificata
- Installazione e riconciliazione recovery completate; 29/29 controlli isolati, 16/16 regressione preset, 21/21 test del comparatore RME rate-aware.
- PCM sul DAC: 8/8 frequenze per 1024 e 2048, guadagno unitario. Nessun clipping o nuovo reset USB registrato; 0 osservazioni XRUN, conteggio cumulativo non disponibile.
- Il falso arresto a 88,2 kHz era dovuto alla frequenza di stato RME in sola lettura trattata come impostazione immutabile dal collaudatore. Correzione nel test, non nel motore.
- Packed24: 8/8 frequenze sul vero ALSA Loopback e PCM gate installato; 1.381.500 frame stereo confrontati byte per byte. Non qualificato il percorso produttivo completo in packed24, né il DSP con ingresso packed; USB RME espone S32_LE.
- DSD64: percorso DoP a 176400 Hz S32_LE e bypass Camilla osservati; ripristino RME completato con recupero dedicato. Marker DoP non catturati; DSD128/256 non eseguiti.
- A/B una osservazione per frequenza e buffer, circa 2 s di campionamento CPU; mediana transizioni 1,9799 vs 2,2561 s, CPU DSP mediana 2,9422 vs 1,9655% di un core. Non prova statistica né soak.
- Test offline: 120 PCM frammentati, 2452 sequenze DoP respinte, 22 frame incompleti respinti, 32 combinazioni formato/frequenza native source/file.
- Stato: P6 non integralmente chiusa. Rimangono packed24 produzione, DSD128/256, test prolungati/48 filtri, conteggio XRUN completo, integrità digitale all'uscita fisica.
- Nessuna credenziale, configurazione privata, identificativo DAC, file musicale, binario proprietario o executable upstream incluso. Per installazione/rollback utilizzare soltanto la procedura esistente supportata; non installare questo snapshot sorgente come ZIP LMS.
