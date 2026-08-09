# Fairfax FCPA Activity Scraper

Scrapes the full [Fairfax County Park Authority activity catalog](https://fairfax.usedirect.com/FairfaxFCPAWeb/ACTIVITIES/Search.aspx)
(every age range × every category, All Places) into `data.js`, with a
filterable `index.html` (search, age/category/place/status, Leaflet map).

Published via GitHub Pages.

## Local usage

```bash
pip install -r requirements.txt
python main.py   # writes data.js
git add data.js index.html
git commit -m "Update activity catalog"
git push         # deploys to GitHub Pages
```

Scrape locally — FCPA/UseDirect returns **HTTP 403** from GitHub-hosted runners.

Open `index.html` in a browser (loads `data.js` beside it). Filters sync to the
URL; category/place/status are multi-select (repeatable query params).

Source: https://github.com/rudokemper/fairfax-FCPA-activity-search/

## GitHub Pages

[`.github/workflows/publish.yml`](.github/workflows/publish.yml) deploys
`index.html` + `data.js` on every push to `main`.

One-time: Settings → Pages → **Source: GitHub Actions**.

## Files

| Path | Purpose |
| --- | --- |
| `main.py` | Scraper |
| `data.js` | Catalog data (`window.__FCPA_DATA__`) |
| `index.html` | Static UI |
| `place_coords.json` | Lat/lng for map markers |
| `requirements.txt` | `requests`, `beautifulsoup4` |
| `.github/workflows/publish.yml` | GitHub Pages deploy |
