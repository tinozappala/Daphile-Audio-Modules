# Rollback conservativo

**Tino Zappala, Milano - 2026**

Release precedente verificata: 0.4.54, **SHA-256 d1ea771e66ba4844e141c5eae81b6c7b0bf1b3c52e69998236883669a0d9169d**.

Sul t630 i backup integrali del plugin, delle approvazioni e delle preferenze sono conservati privatamente nella cartella di manutenzione 20261123-warm-engine-candidate/sync-attempt. Non pubblicare access.token, active_profile.json o peq-intent.json.

In caso di regressione: bypass nativo confermato, swap atomico verso sorgenti 0.4.54, ripristino del catalogo, chiamata sincrona host action sync, verifica Running e recupero della preferenza PEQ. Non sostituire singoli processi mentre PCM o DSD sono attivi.
