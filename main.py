"""Scrape Fairfax FCPA activities for a given age range across every category.

The activity search at Fairfax County Park Authority is an ASP.NET WebForms page
that forces you to pick a single category, then paginates the results. This script
sweeps every category for one age range, follows the pagination postbacks, and
writes a self-contained HTML report with search and filters.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

_ROOT = Path(__file__).resolve().parent

BASE_URL = "https://fairfax.usedirect.com/FairfaxFCPAWeb/Activities/Search.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; fcpa-activity-scraper/1.0)"}

# Short slug -> exact ddlAgeRange option value (from Search.aspx).
AGE_RANGES = {
    "all": "All Ages",
    "infant": "Infant/Toddler (0 - 2 yrs)",
    "preschool": "Pre-school (3 - 5 yrs)",
    "child": "Children (6 - 12 yrs)",
    "teen": "Teen (13 - 17 yrs)",
    "adult": "Adult (18+ yrs)",
    "senior": "Senior (65+ yrs)",
}

# ddlCategoryList option value -> label (excluding the "All Categories" default).
CATEGORIES = {
    "1090": "ADAPTED RECREATION SERVICES",
    "1170": "AMUSEMENT TICKETS",
    "1087": "AQUATICS",
    "1171": "BOAT RENTALS",
    "1180": "CAMPS SCHOOL YEAR",
    "1092": "CAMPS SUMMER",
    "1093": "CAMPS EXTENDED CARE PROGRAMS",
    "72": "CHILDRENS CORNER",
    "1089": "DANCE",
    "1094": "EQUESTRIAN",
    "1160": "EVENTS",
    "1095": "EXERCISE AND PHYSICAL FITNESS",
    "1179": "FACILITY RENTALS",
    "1086": "FARM",
    "1178": "FARMERS MARKETS",
    "1096": "FINE ARTS",
    "1161": "GARDEN",
    "1162": "GOLF",
    "73": "HISTORY",
    "1098": "ICE SKATING",
    "1173": "ICE TICKETS",
    "1175": "INDOOR ARENA TICKETS",
    "1099": "MARTIAL ARTS",
    "83": "NATURE",
    "75": "OUTDOOR RECREATION",
    "1100": "PERFORMING ARTS",
    "1101": "PET PLACE",
    "1168": "PRESCHOOL",
    "1172": "RECENTER TICKETS",
    "1163": "SCIENCE AND TECHNOLOGY",
    "1083": "SCOUTS",
    "1181": "SPECIAL PROGRAMS",
    "1165": "SPORTS",
    "1177": "TICKETED EVENTS",
    "1174": "TOUR TICKETS",
    "1164": "TRIPS AND TOURS",
    "1176": "WATER MINE TICKETS",
    "1111": "XTRAS",
}

# ddlPlaceList option value -> label (excluding the "All Places" default).
PLACES = {
    "8039": "Audrey Moore Rec Center",
    "7770": "Bach to Rock McLean",
    "7773": "Belle View Elementary School",
    "7776": "Black Belt Academy Fairfax",
    "7786": "Bull Run Park",
    "7787": "Burke Lake Golf Course",
    "7788": "Burke Lake Park",
    "7806": "Clemyjontri Park",
    "7810": "Collingwood Park",
    "7813": "Colvin Run Mill",
    "8095": "Craftspace",
    "7818": "Cub Run Rec Center",
    "7819": "Cunningham Park Elementary School",
    "7821": "Deer Park Elementary School",
    "8050": "Ellanor C. Lawrence Park",
    "7838": "Fairfax Fencers",
    "7841": "Fairfax Ice Arena",
    "7921": "Franconia Rec Center",
    "8051": "Frying Pan Park",
    "7858": "George Washington Rec Center",
    "8052": "Green Spring Gardens Park",
    "7865": "Greenbriar Park",
    "7881": "Hidden Oaks Nature Center",
    "7882": "Hidden Pond Nature Center",
    "7883": "Historic Huntley",
    "8109": "Historic Oak Hill House",
    "7891": "Huntley Meadows Park",
    "7898": "Jefferson Golf Course",
    "7899": "Jhoon Rhee Falls Church",
    "7911": "Lake Accotink Park",
    "7931": "Lake Fairfax Park",
    "7920": "Lead By Example Fair Oaks",
    "8096": "Legacy Martial Arts",
    "7925": "Lemon Road Elementary School",
    "7929": "Little Run Elementary School",
    "7945": "McLean Central Park",
    "7949": "Mount Vernon Rec Center",
    "7953": "Navy Elementary School",
    "7955": "Nottoway Park",
    "7956": "NOVA Fencing Club",
    "7963": "Oak View Elementary School",
    "7962": "Oakmont Rec Center",
    "7964": "Oakton Elementary School",
    "7967": "Orange Hunt Elementary School",
    "8088": "Patriot Park North",
    "7970": "Pinecrest Golf Course",
    "7975": "Providence Rec Center",
    "7977": "Riverbend Park",
    "7978": "Roundtree Park",
    "7996": "South Run Rec Center",
    "8110": "Spindle Sears House",
    "7999": "Spring Hill Elementary School",
    "7998": "Spring Hill Rec Center",
    "8004": "Stone Mansion",
    "8066": "Stratton Woods Park",
    "8075": "Sully CommCtr",
    "8053": "Sully Historic Site",
    "8016": "Turner Farm Park",
    "8071": "Virtual FCPA",
    "8069": "Water Mine",
    "8045": "West Springfield Elementary School",
    "8044": "Woodson High School",
}

def _load_place_coords() -> dict[str, dict[str, float]]:
    path = _ROOT / "place_coords.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: {"lat": float(entry["lat"]), "lng": float(entry["lng"])}
        for name, entry in raw.items()
        if "lat" in entry and "lng" in entry
    }


PLACE_COORDS = _load_place_coords()

# Fixed, broadest default filters (place is configurable via --place).
DEFAULT_FILTERS = {
    "ctl01$mainContent$ddlMonths": "All Months",
    "ctl01$mainContent$hdnddlDayOfWeek": "Sunday,Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
    "ctl01$mainContent$txtInstructor": "",
    "ctl01$mainContent$hdnInstructorId": "0",
    "ctl01$mainContent$txtSearchActivity": "",
}

_HIDDEN_RE = {
    key: re.compile(rf'name="{re.escape(key)}"[^>]*value="(.*?)"', re.DOTALL)
    for key in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")
}
_PAGING_RE = re.compile(
    r'lblPagingRange">\s*page\s*(\d+)\s*</span>.*?lblResultsTotal">\s*(\d+)\s*</span>',
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class Session:
    session: str = ""
    place: str = ""
    schedule: str = ""
    ages: str = ""
    seats: str = ""
    signup: str = ""
    status: str = ""


@dataclass
class Activity:
    title: str
    description: str = ""
    sessions: list[Session] = field(default_factory=list)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "-a",
        "--age-range",
        default="infant",
        help=f"Age range slug ({', '.join(AGE_RANGES)}) or exact option string. Default: infant.",
    )
    parser.add_argument(
        "-p",
        "--place",
        help="Comma-separated place ids, exact names, or unique substrings "
        "(e.g. southrun,franconia). Default: All Places.",
    )
    parser.add_argument(
        "-x",
        "--exclude-category",
        help="Comma-separated category ids, exact names, or unique substrings to skip "
        "(e.g. golf,tickets).",
    )
    parser.add_argument("-o", "--output", type=Path, help="Output Markdown path.")
    parser.add_argument(
        "--delay", type=float, default=0.3, help="Seconds between requests (default 0.3)."
    )
    parser.add_argument(
        "--max-pages", type=int, default=50, help="Pagination safety cap per category."
    )
    parser.add_argument(
        "--list-age-ranges", action="store_true", help="Print age range choices and exit."
    )
    parser.add_argument(
        "--list-places", action="store_true", help="Print place choices and exit."
    )
    parser.add_argument(
        "--list-categories", action="store_true", help="Print category choices and exit."
    )
    return parser.parse_args(argv)


def resolve_age_range(value: str) -> tuple[str, str]:
    """Return (slug, exact option value) for a slug or exact option string."""
    key = value.strip().lower()
    if key in AGE_RANGES:
        return key, AGE_RANGES[key]
    for slug, option in AGE_RANGES.items():
        if option.lower() == key:
            return slug, option
    raise SystemExit(
        f"Unknown age range {value!r}. Choices: {', '.join(AGE_RANGES)}."
    )


def _resolve_one_place(token: str) -> tuple[str, str]:
    key = token.strip().lower()
    if key in PLACES:
        return key, PLACES[key]
    needle = re.sub(r"[^a-z0-9]", "", key)
    matches = {
        pid: name
        for pid, name in PLACES.items()
        if needle in re.sub(r"[^a-z0-9]", "", name.lower())
    }
    if len(matches) == 1:
        return next(iter(matches.items()))
    if not matches:
        raise SystemExit(f"Unknown place {token!r}. Use --list-places to see options.")
    raise SystemExit(
        f"Ambiguous place {token!r}; matches: {', '.join(matches.values())}."
    )


def resolve_places(value: str | None) -> list[tuple[str, str]]:
    """Resolve a comma-separated place list to [(ddlPlaceList value, label), ...]."""
    tokens = [t.strip() for t in (value or "").split(",") if t.strip()]
    if not tokens or {t.lower() for t in tokens} <= {"all", "all places"}:
        return [("All Places", "All Places")]
    resolved: dict[str, str] = {}
    for token in tokens:
        pid, name = _resolve_one_place(token)
        resolved.setdefault(pid, name)
    return list(resolved.items())


def _resolve_one_category(token: str) -> str:
    key = token.strip().lower()
    if key in CATEGORIES:
        return key
    needle = re.sub(r"[^a-z0-9]", "", key)
    matches = {
        cid: name
        for cid, name in CATEGORIES.items()
        if needle in re.sub(r"[^a-z0-9]", "", name.lower())
    }
    if len(matches) == 1:
        return next(iter(matches))
    if not matches:
        raise SystemExit(f"Unknown category {token!r}. Choices: {', '.join(CATEGORIES.values())}.")
    raise SystemExit(
        f"Ambiguous category {token!r}; matches: {', '.join(matches.values())}."
    )


def selected_categories(exclude: str | None) -> dict[str, str]:
    """Return CATEGORIES minus any matched by the comma-separated exclude list."""
    excluded = {
        _resolve_one_category(token)
        for token in (exclude or "").split(",")
        if token.strip()
    }
    return {cid: name for cid, name in CATEGORIES.items() if cid not in excluded}


def hidden_fields(page: str) -> dict[str, str]:
    fields = {}
    for key, pattern in _HIDDEN_RE.items():
        match = pattern.search(page)
        if not match:
            raise RuntimeError(f"Missing ASP.NET hidden field {key}")
        fields[key] = html.unescape(match.group(1))
    return fields


def common_payload(
    category_id: str, age_range: str, place: str, today: str
) -> dict[str, str]:
    return {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "ctl01$mainContent$ddlCategoryList": category_id,
        "ctl01$mainContent$ddlAgeRange": age_range,
        "ctl01$mainContent$ddlPlaceList": place,
        "ctl01$mainContent$txtStartDate": today,
        "ctl01$mainContent$hdnStartDate": today,
        **DEFAULT_FILTERS,
    }


def paging_state(page: str) -> tuple[int, int]:
    match = _PAGING_RE.search(page)
    return (int(match.group(1)), int(match.group(2))) if match else (1, 1)


def iter_pages(session, category_id, age_range, place, today, delay, max_pages):
    """Yield the HTML of every result page for one category."""
    seed = hidden_fields(session.get(BASE_URL, timeout=30).text)
    payload = common_payload(category_id, age_range, place, today)
    payload.update(seed)
    payload["ctl01$mainContent$LinkButton1"] = "Search Activities"
    page = session.post(BASE_URL, data=payload, timeout=60).text

    for _ in range(max_pages):
        yield page
        current, total = paging_state(page)
        if current >= total:
            break
        next_payload = common_payload(category_id, age_range, place, today)
        next_payload["__EVENTTARGET"] = "ctl01$mainContent$bNext"
        next_payload.update(hidden_fields(page))
        time.sleep(delay)
        page = session.post(BASE_URL, data=next_payload, timeout=60).text


def _clean(text: str) -> str:
    return " ".join(text.split())


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def parse_sessions(panel) -> list[Session]:
    sessions = []
    for row in panel.select("tbody tr"):
        cells = {
            cell["data-label"]: cell for cell in row.select("td[data-label]")
        }
        if "Session" not in cells:
            continue
        schedule = " / ".join(
            line.strip()
            for line in cells["Schedule"].get_text("\n").splitlines()
            if line.strip()
        ) if "Schedule" in cells else ""
        status = next(
            (cells[label].get_text(" ", strip=True) for label in ("Select", "Status") if label in cells),
            "",
        )
        sessions.append(
            Session(
                session=_clean(cells["Session"].get_text()),
                place=_clean(cells.get("Place", _empty()).get_text()),
                schedule=schedule,
                ages=_clean(cells.get("Restrictions", _empty()).get_text()),
                seats=_clean(cells.get("Seats", _empty()).get_text()),
                signup=_clean(cells.get("Sign Up", _empty()).get_text()),
                status=_clean(status),
            )
        )
    return sessions


class _Empty:
    def get_text(self, *_args, **_kwargs):
        return ""


def _empty() -> _Empty:
    return _EMPTY


_EMPTY = _Empty()


def parse_cards(page: str) -> list[Activity]:
    soup = BeautifulSoup(page, "html.parser")
    container = soup.find(id="activityList")
    if container is None:
        return []
    activities: list[Activity] = []
    for node in container.find_all(["h2", "div"]):
        classes = node.get("class") or []
        if node.name == "h2" and "FindTour" in classes:
            title = node.get("title") or node.get_text(strip=True)
            description = ""
            desc = node.find_next_sibling("div", class_="description")
            if desc is not None:
                description = _clean(desc.get_text(" "))
            activities.append(Activity(title=_clean(title), description=description))
        elif node.name == "div" and "sessions" in classes and activities:
            activities[-1].sessions = parse_sessions(node)
    return activities


def merge_activity(merged: dict[str, Activity], activity: Activity) -> None:
    """Add an activity to a title-keyed map, unioning sessions by session code."""
    existing = merged.get(activity.title)
    if existing is None:
        merged[activity.title] = activity
        return
    have = {s.session for s in existing.sessions}
    existing.sessions.extend(s for s in activity.sessions if s.session not in have)


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _activity_search_blob(activity: Activity) -> str:
    parts = [activity.title, activity.description]
    for s in activity.sessions:
        parts.extend((s.session, s.place, s.schedule, s.ages, s.seats, s.signup, s.status))
    return " ".join(parts).lower()


def to_html(
    results: dict[str, list[Activity]], age_option: str, place_label: str, today: str
) -> str:
    total = sum(len(items) for items in results.values())
    categories = [c for c, acts in results.items() if acts]
    places = sorted(
        {
            s.place
            for acts in results.values()
            for a in acts
            for s in a.sessions
            if s.place
        }
    )
    statuses = sorted(
        {
            s.status
            for acts in results.values()
            for a in acts
            for s in a.sessions
            if s.status
        }
    )

    category_options = "\n".join(
        f'<option value="{_escape(c)}">{_escape(c)}</option>' for c in categories
    )
    place_options = "\n".join(
        f'<option value="{_escape(p)}">{_escape(p)}</option>' for p in places
    )
    status_options = "\n".join(
        f'<option value="{_escape(s)}">{_escape(s)}</option>' for s in statuses
    )
    place_coords = {
        name: PLACE_COORDS[name]
        for name in places
        if name in PLACE_COORDS
    }
    place_coords_json = json.dumps(place_coords, separators=(",", ":"))

    sections: list[str] = []
    for category, activities in results.items():
        if not activities:
            continue
        cards: list[str] = []
        for activity in activities:
            session_rows = []
            for s in activity.sessions:
                session_rows.append(
                    "<tr"
                    f' data-place="{_escape(s.place)}"'
                    f' data-status="{_escape(s.status)}">'
                    f"<td>{_escape(s.session)}</td>"
                    f"<td>{_escape(s.place)}</td>"
                    f"<td>{_escape(s.schedule)}</td>"
                    f"<td>{_escape(s.ages)}</td>"
                    f"<td>{_escape(s.seats)}</td>"
                    f"<td>{_escape(s.signup)}</td>"
                    f"<td>{_escape(s.status)}</td>"
                    "</tr>"
                )
            places_attr = "|".join(sorted({s.place for s in activity.sessions if s.place}))
            statuses_attr = "|".join(sorted({s.status for s in activity.sessions if s.status}))
            desc = (
                f'<p class="desc">{_escape(activity.description)}</p>'
                if activity.description
                else ""
            )
            table = (
                "<div class='table-wrap'><table>"
                "<thead><tr>"
                "<th>Session</th><th>Place</th><th>Schedule</th><th>Ages</th>"
                "<th>Seats</th><th>Sign Up</th><th>Status</th>"
                "</tr></thead>"
                f"<tbody>{''.join(session_rows)}</tbody>"
                "</table></div>"
                if session_rows
                else "<p class='muted'>No session rows.</p>"
            )
            cards.append(
                "<article class='activity'"
                f' data-category="{_escape(category)}"'
                f' data-places="{_escape(places_attr)}"'
                f' data-statuses="{_escape(statuses_attr)}"'
                f' data-search="{_escape(_activity_search_blob(activity))}">'
                f"<h3>{_escape(activity.title)}</h3>"
                f"{desc}{table}"
                "</article>"
            )
        sections.append(
            f"<section class='category' data-category='{_escape(category)}'>"
            f"<h2>{_escape(category)} "
            f"<span class='count' data-count='{len(activities)}'>({len(activities)})</span></h2>"
            f"{''.join(cards)}"
            "</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fairfax FCPA Activities — {_escape(age_option)}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
<style>
:root {{
  --bg: #f4f6f4;
  --ink: #1c2420;
  --muted: #5c6a62;
  --line: #d5ddd7;
  --panel: #ffffff;
  --accent: #0f6a4f;
  --accent-soft: #e5f3ec;
  --warn: #8a5a00;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font: 16px/1.45 "Source Sans 3", "Segoe UI", sans-serif;
  color: var(--ink);
  background:
    radial-gradient(1200px 500px at 10% -10%, #dceee4 0%, transparent 55%),
    var(--bg);
}}
header {{
  padding: 1.5rem 1.25rem 1rem;
  max-width: 1100px;
  margin: 0 auto;
}}
h1 {{
  margin: 0 0 0.35rem;
  font: 700 1.75rem/1.2 "Fraunces", Georgia, serif;
  letter-spacing: -0.02em;
}}
.meta, .muted {{ color: var(--muted); }}
.meta {{ margin: 0 0 0.75rem; }}
.filters {{
  position: sticky;
  top: 0;
  z-index: 1000;
  background: color-mix(in srgb, var(--bg) 88%, white);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--line);
  padding: 0.75rem 1.25rem;
}}
.filters-inner {{
  max-width: 1100px;
  margin: 0 auto;
  display: grid;
  gap: 0.65rem;
  grid-template-columns: 2fr 1fr 1fr 1fr auto;
  align-items: end;
}}
label {{
  display: grid;
  gap: 0.25rem;
  font-size: 0.78rem;
  font-weight: 650;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
}}
input, select, button {{
  width: 100%;
  font: inherit;
  color: var(--ink);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0.55rem 0.7rem;
  background: var(--panel);
}}
input:focus, select:focus {{
  outline: 2px solid color-mix(in srgb, var(--accent) 45%, white);
  border-color: var(--accent);
}}
button {{
  cursor: pointer;
  background: var(--accent-soft);
  border-color: color-mix(in srgb, var(--accent) 35%, var(--line));
  color: var(--accent);
  font-weight: 650;
}}
#visible-count {{
  max-width: 1100px;
  margin: 0.75rem auto 0;
  padding: 0 1.25rem;
  font-size: 0.95rem;
  color: var(--muted);
}}
.map-panel {{
  max-width: 1100px;
  margin: 0.85rem auto 0;
  padding: 0 1.25rem;
}}
.map-panel h2 {{
  margin: 0 0 0.45rem;
  font: 650 1rem/1.3 "Fraunces", Georgia, serif;
}}
#map {{
  height: 320px;
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #dde5df;
  z-index: 1;
}}
.map-note {{
  margin: 0.4rem 0 0;
  font-size: 0.85rem;
  color: var(--muted);
}}
main {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 1rem 1.25rem 3rem;
}}
.category {{ margin: 1.75rem 0; }}
.category h2 {{
  margin: 0 0 0.85rem;
  font: 650 1.15rem/1.3 "Fraunces", Georgia, serif;
  border-bottom: 1px solid var(--line);
  padding-bottom: 0.4rem;
}}
.category h2 .count {{ color: var(--muted); font-weight: 500; }}
.activity {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 1rem 1.1rem;
  margin: 0 0 0.85rem;
}}
.activity h3 {{
  margin: 0 0 0.35rem;
  font-size: 1.05rem;
}}
.desc {{ margin: 0 0 0.75rem; color: var(--muted); }}
.table-wrap {{ overflow-x: auto; }}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92rem;
}}
th, td {{
  text-align: left;
  vertical-align: top;
  padding: 0.45rem 0.55rem;
  border-top: 1px solid var(--line);
}}
th {{
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
  border-top: 0;
}}
tr[hidden] {{ display: none; }}
.empty {{
  display: none;
  margin: 2rem 0;
  padding: 1.25rem;
  border: 1px dashed var(--line);
  border-radius: 12px;
  color: var(--warn);
  background: #fff9ef;
}}
.empty.show {{ display: block; }}
@media (max-width: 900px) {{
  .filters-inner {{ grid-template-columns: 1fr 1fr; }}
}}
@media (max-width: 560px) {{
  .filters-inner {{ grid-template-columns: 1fr; }}
  #map {{ height: 260px; }}
}}
</style>
</head>
<body>
<header>
  <h1>Fairfax FCPA Activities — {_escape(age_option)}</h1>
  <p class="meta">Generated {_escape(today)} · {total} activities across {len(results)} categories.</p>
  <p class="meta">Scraped filters: {_escape(place_label)}, All Months, all days, any instructor, starting on or after {_escape(today)}.</p>
</header>
<div class="filters">
  <div class="filters-inner">
    <label>Search
      <input id="q" type="search" placeholder="Title, description, session, place…" autocomplete="off">
    </label>
    <label>Category
      <select id="category"><option value="">All categories</option>{category_options}</select>
    </label>
    <label>Place
      <select id="place"><option value="">All places</option>{place_options}</select>
    </label>
    <label>Status
      <select id="status"><option value="">All statuses</option>{status_options}</select>
    </label>
    <label>&nbsp;<button type="button" id="reset">Reset</button></label>
  </div>
</div>
<p id="visible-count"></p>
<section class="map-panel">
  <h2>Locations</h2>
  <div id="map" role="img" aria-label="Map of activity locations"></div>
  <p class="map-note" id="map-note"></p>
</section>
<main>
  <div id="empty" class="empty">No activities match the current filters.</div>
  {''.join(sections)}
</main>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
  integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
(() => {{
  const q = document.getElementById("q");
  const category = document.getElementById("category");
  const place = document.getElementById("place");
  const status = document.getElementById("status");
  const reset = document.getElementById("reset");
  const empty = document.getElementById("empty");
  const visibleCount = document.getElementById("visible-count");
  const mapNote = document.getElementById("map-note");
  const activities = [...document.querySelectorAll(".activity")];
  const sections = [...document.querySelectorAll(".category")];
  const placeCoords = {place_coords_json};

  const map = L.map("map", {{ scrollWheelZoom: false }}).setView([38.85, -77.27], 10);
  L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
    maxZoom: 18,
    attribution: "&copy; OpenStreetMap",
  }}).addTo(map);
  const markers = L.layerGroup().addTo(map);

  function visiblePlaces() {{
    const counts = new Map();
    for (const activity of activities) {{
      if (activity.hidden) continue;
      for (const row of activity.querySelectorAll("tbody tr")) {{
        if (row.hidden) continue;
        const name = row.dataset.place;
        if (!name) continue;
        counts.set(name, (counts.get(name) || 0) + 1);
      }}
    }}
    return counts;
  }}

  function updateMap() {{
    markers.clearLayers();
    const counts = visiblePlaces();
    const bounds = [];
    for (const [name, count] of counts) {{
      const coord = placeCoords[name];
      if (!coord) continue;
      const marker = L.marker([coord.lat, coord.lng]);
      marker.bindPopup(
        `<strong>${{name}}</strong><br>${{count}} session${{count === 1 ? "" : "s"}} visible` +
        `<br><button type="button" data-filter-place="${{name.replace(/"/g, "&quot;")}}">Filter to this place</button>`
      );
      marker.on("popupopen", (e) => {{
        const btn = e.popup.getElement().querySelector("[data-filter-place]");
        if (!btn) return;
        btn.addEventListener("click", () => {{
          place.value = btn.getAttribute("data-filter-place");
          apply();
        }}, {{ once: true }});
      }});
      markers.addLayer(marker);
      bounds.push([coord.lat, coord.lng]);
    }}
    const mapped = bounds.length;
    const unmapped = [...counts.keys()].filter((n) => !placeCoords[n]).length;
    if (bounds.length === 1) {{
      map.setView(bounds[0], 13);
    }} else if (bounds.length > 1) {{
      map.fitBounds(bounds, {{ padding: [28, 28], maxZoom: 13 }});
    }}
    mapNote.textContent = mapped
      ? `${{mapped}} location${{mapped === 1 ? "" : "s"}} on map` +
        (unmapped ? ` · ${{unmapped}} without coordinates` : "")
      : "No mapped locations match the current filters.";
    setTimeout(() => map.invalidateSize(), 0);
  }}

  function apply() {{
    const needle = q.value.trim().toLowerCase();
    const cat = category.value;
    const pl = place.value;
    const st = status.value;
    let shown = 0;

    for (const activity of activities) {{
      const rows = [...activity.querySelectorAll("tbody tr")];
      let anyRow = false;
      for (const row of rows) {{
        const placeOk = !pl || row.dataset.place === pl;
        const statusOk = !st || row.dataset.status === st;
        const ok = placeOk && statusOk;
        row.hidden = !ok;
        if (ok) anyRow = true;
      }}
      const catOk = !cat || activity.dataset.category === cat;
      const searchOk = !needle || (activity.dataset.search || "").includes(needle);
      const placeOnCard = !pl || (activity.dataset.places || "").split("|").includes(pl);
      const statusOnCard = !st || (activity.dataset.statuses || "").split("|").includes(st);
      const visible = catOk && searchOk && placeOnCard && statusOnCard && (rows.length === 0 || anyRow);
      activity.hidden = !visible;
      if (visible) shown += 1;
    }}

    for (const section of sections) {{
      const kids = [...section.querySelectorAll(".activity")];
      const n = kids.filter(a => !a.hidden).length;
      section.hidden = n === 0;
      const count = section.querySelector("[data-count]");
      if (count) count.textContent = `(${{n}})`;
    }}

    empty.classList.toggle("show", shown === 0);
    visibleCount.textContent = `Showing ${{shown}} of ${{activities.length}} activities`;
    updateMap();
  }}

  for (const el of [q, category, place, status]) el.addEventListener("input", apply);
  reset.addEventListener("click", () => {{
    q.value = "";
    category.value = "";
    place.value = "";
    status.value = "";
    apply();
  }});
  apply();
}})();
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.list_age_ranges:
        for slug, option in AGE_RANGES.items():
            print(f"{slug:<10} {option}")
        return
    if args.list_places:
        for pid, name in PLACES.items():
            print(f"{pid:<6} {name}")
        return
    if args.list_categories:
        for cid, name in CATEGORIES.items():
            print(f"{cid:<6} {name}")
        return

    slug, age_option = resolve_age_range(args.age_range)
    categories = selected_categories(args.exclude_category)
    places = resolve_places(args.place)
    place_label = ", ".join(label for _, label in places)
    today = date.today().strftime("%m/%d/%Y")
    place_suffix = "" if places[0][0] == "All Places" else "_" + "_".join(
        _slugify(label) for _, label in places
    )
    output = args.output or (
        _ROOT / "outputs" / f"activities_{slug}{place_suffix}.html"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    results: dict[str, list[Activity]] = {}
    with requests.Session() as session:
        session.headers.update(HEADERS)
        for category_id, category_name in categories.items():
            merged: dict[str, Activity] = {}
            for place_value, _ in places:
                for page in iter_pages(
                    session, category_id, age_option, place_value, today, args.delay, args.max_pages
                ):
                    for activity in parse_cards(page):
                        merge_activity(merged, activity)
                time.sleep(args.delay)
            results[category_name] = list(merged.values())
            print(f"{category_name}: {len(merged)} activities", file=sys.stderr)

    output.write_text(
        to_html(results, age_option, place_label, today), encoding="utf-8"
    )
    total = sum(len(items) for items in results.values())
    print(f"Wrote {total} activities to {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
