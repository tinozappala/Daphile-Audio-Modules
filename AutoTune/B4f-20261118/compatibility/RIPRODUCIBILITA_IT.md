# Camilla 0.4.49: sorgenti e riproducibilità

Lo ZIP contiene i 39 file completi del modulo, compresi sorgenti e motore
preesistente. `APPROVED_ENTRY_0.4.49.json` ne elenca gli hash. Il builder
controlla la base 0.4.47 esatta e consente soltanto tre differenze: lettore PCM,
versione del plugin e versione dell'API. Il rapporto di composizione è distinto
dall'esito d'installazione riportato in `../AUTOTUNE_B4F_REPORT.json`.

Le fixture distribuite in `guard-tests/` non richiedono il t620 o credenziali:

```sh
python3 -I -B guard-tests/guard_fixture.py --original guard-tests/original --working guard-tests/working --iterations 1000 --rounds 6 --output misura-locale.json
```

I risultati numerici dipendono dal sistema; la semantica deve restare identica.
Il benchmark usa file regolari e non costituisce un collaudo di procfs/audio.

Per ricostruire lo stesso archivio, usare la base immutabile
`Pacchetti/plugin/DaphileCamillaDSP-0.4.47.zip` del repository
`tinozappala/Daphile-Audio-Modules` e un'uscita nuova:

```sh
python3 -I -B build_camilla_guard49.py --base /percorso/DaphileCamillaDSP-0.4.47.zip --native-player guard-tests/working/native_player.py --original-player guard-tests/original/native_player.py --approval47 APPROVED_ENTRY_0.4.47.json --benchmark LINUX_FIXTURE_BENCHMARK.json --out ricostruzione
```

SHA-256 dello ZIP 0.4.49:
`64427d58da63f8383148fd8c5742fcb559186d9fdbce56806b5ba061096dfb27`.
Il builder usa la ricevuta Linux conservata per documentare la prova originale;
il comando delle fixture permette di verificarla nuovamente sul proprio host.

L'installazione sul t620 ha mantenuto i namespace vecchio e nuovo, pubblicato
catalogo e sorgenti LMS coerenti e osservato il processo pronto. Il rollback
privato conserva preimmagini e postcondizioni; rifiuta di sovrascrivere cambi
successivi. Non copiare soltanto `native_player.py` dentro una release attiva:
Audio Host deve attivare una nuova release completa con la relativa approvazione.
La guida `LEGGIMI_CAMILLA_0.4.49_IT.md` generata dal builder documenta il perimetro
della candidata, mentre il rapporto finale attesta l'installazione effettiva.

La 0.4.48 sperimentale rimane disponibile nel repository con la propria storia.
Questa manutenzione conserva l'interfaccia classica della RC6.
