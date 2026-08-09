# Fairfax FCPA Activity Search

Compiles the full [Fairfax County Park Authority activity catalog](https://fairfax.usedirect.com/FairfaxFCPAWeb/ACTIVITIES/Search.aspx) into `data.js` and provides a
filterable `index.html` (search, age/category/place/status, Leaflet map, shareable URL with query params).

## Local usage

```bash
pip install -r requirements.txt
python main.py   # writes data.js
git add data.js index.html
git commit -m "Update activity catalog"
git push         # deploys to GitHub Pages
```