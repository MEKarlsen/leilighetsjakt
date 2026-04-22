# leilighetsjakt

> **Kun til personlig bruk.** Dette verktøyet er laget for privat boligjakting og er ikke ment for distribusjon, kommersiell bruk eller videredeling.

Et personlig webverktøy for å samle, organisere og analysere boligannonser fra finn.no og vising.ai.

## Funksjoner

- Hent og lagre leiligheter fra finn.no ved å lime inn en annonselenke
- Synkroniser en delt favorittliste fra finn.no (krever innlogging via e-post + engangskode)
- Hent Tilstandsrapport (TG-grader), høydepunkter og risikoer fra visning.ai
- Vis leilighetene på et interaktivt kart
- Eksporter/importer data til Excel
- Se visningsdato, solgt-status, balkong, etasje og mer i en oversiktlig tabell

## Oppsett

### 1. Installer avhengigheter

```bash
pip install flask requests beautifulsoup4 lxml openpyxl playwright
playwright install chromium
```

### 2. Start appen

```bash
python3 app.py
```

Åpne [http://localhost:5000](http://localhost:5000) i nettleseren.
