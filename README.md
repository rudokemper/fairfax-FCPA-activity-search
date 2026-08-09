# Fairfax FCPA Activity Search

Compiles the full [Fairfax County Park Authority activity catalog](https://fairfax.usedirect.com/FairfaxFCPAWeb/ACTIVITIES/Search.aspx) into `data.js` and provides a
filterable `index.html` (search, age/category/place/status, Leaflet map, shareable URL with query params).

Published via GitHub Pages (`publish.yml` deploys `index.html` + `data.js` on push to `main`).

## Why the catalog is updated locally

UseDirect sits behind CloudFront and returns **HTTP 403** to GitHub-hosted Actions runners (datacenter IPs), even with a normal browser User-Agent and Chrome TLS impersonation. The scrape works from a regular residential network, so this repo keeps a simple workflow: run `main.py` locally, commit the new `data.js`, and push.

CI is only used to publish the static site, not to refresh the catalog.

## Updating the catalog

```bash
pip install -r requirements.txt
python main.py   # writes data.js
git add data.js
git commit -m "Update activity catalog"
git push         # deploys to GitHub Pages
```

If you’d like fresher data: run `main.py` and open a PR with the updated `data.js`, or just ask me and I’ll refresh it.
