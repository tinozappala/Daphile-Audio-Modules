# ISO live .72 per HP t630

L’immagine di prova avvia il kernel stock della beta 25.05-b251550, LMS 9.0.3, CamillaDSP 0.4.72 e il modulo RME 0.5.23. Il SHA-256 della ISO da 808.286.208 byte è `d5e4c2d90b722a15be0de5814f5a6d71543d36a52dcc407316eb586126bc8c47`.

In QEMU/KVM su Matteo sono passati BIOS e UEFI sia da CD che da USB. GUI, LMS, catalogo e API dei due moduli hanno risposto e i dati dei due dischi ext4 di controllo sono rimasti integri dopo ciascun boot. La live conserva i propri dati in RAM. La riproduzione fisica del modulo installato sul t630 è documentata separatamente. [Nota tecnica, avvio e sorgenti](next_audio_20261127/iso_consolidata/NOTA_TECNICA_PUBBLICA_IT.md) · [Manifest di build](next_audio_20261127/iso_consolidata/PUBLIC_MANIFEST.json) · [Confronto fisico con l’equalizzatore spento](../CONFRONTO_EQ_SUL_T630.md)

La cartella Drive privata di distribuzione contiene otto parti, il manifesto e `ricostruisci_iso.py`. Dopo il download nella stessa cartella, `py -3 ricostruisci_iso.py` su WIN (Python 3.11 o successivo) ricostruisce l’immagine e verifica ciascuna parte e il digest della ISO. La ricostruzione delle otto parti scaricate da Drive è stata verificata su Matteo.
