# Recupero / rollback — 0.4.56

**Tino Zappala, Milano — 2026**

- Release corrente: 0.4.56, SHA-256 `71b783d1d86cd16d4e0a91b206c3f1a4f3788550f8f87fc005df3823146f309e`.
- Rollback: release precedente 0.4.55, SHA-256 `2271a32f1bb271cc74776042bcf5b0fc889df6edbf110009d5bfe57357937c95`.

I backup byte-verificati del precedente plugin, del catalogo di approvazioni e della preferenza PEQ si trovano **soltanto** nella cartella privata di manutenzione del t630 `/mnt/hdd/system/daphile-maintenance/20261124-playeronly-candidate/`. Non pubblicarli.

Procedura conservativa: verificare identità del DAC e stato Audio Host, acquisire playlist e cursore, abilitare bypass nativo con conferma; effettuare swap atomico dei sorgenti 0.4.55, ripristinare `approvals.json`, richiedere `sync` via socket Unix all'Audio Host, verificare `running=true` e la release precedente, ripristinare il profilo PEQ originale. Non sostituire file a caldo sotto una sessione PCM o DSD attiva.

In caso di fallimento del riavvio `pcmCND0p`, interrompere l'handoff e conservare i registri; **non chiamare automaticamente `restart cardN`** soltanto per ridurre i tempi, perché comporta il reset hardware USB. La gestione d'emergenza del daemon rimane invariata.
