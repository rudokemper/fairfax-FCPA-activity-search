# Fairfax FCPA Activity Scraper

The [Fairfax County Park Authority activity search](https://fairfax.usedirect.com/FairfaxFCPAWeb/ACTIVITIES/Search.aspx)
forces you to pick a **single** activity category before it will show results,
and then paginates them. Browsing everything for one age group means clicking
through 37 categories and their pages by hand.

`main.py` does that sweep for you: it queries every category for one age range,
follows the pagination, and writes a self-contained HTML report with search and
filters.

## How it works

The page is ASP.NET WebForms with server-rendered results, so the script:

1. `GET`s the search page to obtain the `__VIEWSTATE` / `__VIEWSTATEGENERATOR` /
   `__EVENTVALIDATION` hidden fields and a session cookie.
2. `POST`s the search form for a category with the age range set and all other
   filters pinned to their broadest defaults (see below).
3. Follows the **Next** button, which is a postback (`__EVENTTARGET=...bNext`)
   that reuses the view-state returned by the previous page, until the last page
   (`Now Displaying page X Of N`).
4. Parses each activity card (title, description, and its session table) and
   emits HTML.

### Filters

Age range (and optionally place) narrow the search. Everything else is left at
its widest:

| Filter | Value |
| --- | --- |
| Age range | `--age-range` (default Infant/Toddler) |
| Place | `--place` (comma-separated; default All Places) |
| Month | All Months |
| Day of the week | All 7 days |
| Instructor | None |
| Starting On or After | Today |
| Search Text | Empty |

## Usage

```bash
pip install -r requirements.txt

# Default: Infant/Toddler (0 - 2 yrs) -> outputs/activities_infant.html
python main.py

# Another age range by slug or exact label
python main.py --age-range preschool
python main.py --age-range "Children (6 - 12 yrs)"

# Restrict to one or more places (comma-separated; id, exact name, or substring)
python main.py --place "Riverbend Park"
python main.py --age-range child --place southrun,franconia

# Skip categories (comma-separated; id, exact name, or unique substring)
python main.py --exclude-category golf,"childrens corner"

# List the available choices
python main.py --list-age-ranges
python main.py --list-places
python main.py --list-categories
```

### Options

| Flag | Description | Default |
| --- | --- | --- |
| `-a`, `--age-range` | Age range slug or exact option string | `infant` |
| `-p`, `--place` | Comma-separated place ids, exact names, or substrings | All Places |
| `-x`, `--exclude-category` | Comma-separated category ids, names, or substrings to skip | none |
| `-o`, `--output` | Output HTML path | `outputs/activities_<slug>[_<place>].html` |
| `--delay` | Seconds between requests | `0.3` |
| `--max-pages` | Pagination safety cap per category | `50` |
| `--list-age-ranges` | Print age range choices and exit | |
| `--list-places` | Print place choices and exit | |
| `--list-categories` | Print category choices and exit | |

Age range slugs: `all`, `infant`, `preschool`, `child`, `teen`, `adult`, `senior`.

## Output

A single HTML file in `outputs/` (git-ignored). Open it in a browser — no server
required (Leaflet/OSM load from CDN). The sticky toolbar includes:

- **Search** across title, description, session, place, schedule, ages, status
- **Category**, **Place**, and **Status** dropdowns
- A live “Showing X of Y” count and Reset
- A **Leaflet map** of locations that match the current filters (all places in
  the result set by default; narrows when you filter Place/search/etc.). Click a
  marker → **Filter to this place**.

Empty categories are omitted. Session rows that don’t match Place/Status are
hidden within an activity; the activity itself disappears when no rows remain.

Coordinates live in `place_coords.json` (keyed by place name).
