"""Scrape all Fairfax FCPA activities and write data.js.

Sweeps every official age range × every category (All Places), merges results,
and emits catalog data for the static index.html UI.
"""

from __future__ import annotations

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
DATA_JS = _ROOT / "data.js"

BASE_URL = "https://fairfax.usedirect.com/FairfaxFCPAWeb/Activities/Search.aspx"
HOME_URL = "https://fairfax.usedirect.com/FairfaxFCPAWeb/Default.aspx"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}
DELAY = 0.35
MAX_PAGES = 50
MAX_RETRIES = 5
RETRY_BACKOFF = 3.0

# Official age buckets used for scraping + the HTML age filter (not "All Ages").
AGE_RANGES = [
    "Infant/Toddler (0 - 2 yrs)",
    "Pre-school (3 - 5 yrs)",
    "Children (6 - 12 yrs)",
    "Teen (13 - 17 yrs)",
    "Adult (18+ yrs)",
    "Senior (65+ yrs)",
]

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

DEFAULT_FILTERS = {
    "ctl01$mainContent$ddlPlaceList": "All Places",
    "ctl01$mainContent$ddlMonths": "All Months",
    "ctl01$mainContent$hdnddlDayOfWeek": "Sunday,Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
    "ctl01$mainContent$txtInstructor": "",
    "ctl01$mainContent$hdnInstructorId": "0",
    "ctl01$mainContent$txtSearchActivity": "",
}

_HIDDEN_KEYS = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")
_PAGING_RE = re.compile(
    r'lblPagingRange">\s*page\s*(\d+)\s*</span>.*?lblResultsTotal">\s*(\d+)\s*</span>',
    re.DOTALL | re.IGNORECASE,
)


def _load_place_coords() -> dict[str, dict]:
    path = _ROOT / "place_coords.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for name, entry in raw.items():
        if "lat" not in entry or "lng" not in entry:
            continue
        kind = entry.get("kind")
        if not kind:
            if "Rec Center" in name:
                kind = "rec_center"
            elif name == "Virtual FCPA":
                kind = "virtual"
            else:
                kind = "other"
        out[name] = {"lat": float(entry["lat"]), "lng": float(entry["lng"]), "kind": kind}
    return out


PLACE_COORDS = _load_place_coords()


@dataclass
class Session:
    session: str = ""
    place: str = ""
    schedule: str = ""
    ages: str = ""
    seats: str = ""
    signup: str = ""
    status: str = ""
    age_ranges: set[str] = field(default_factory=set)


@dataclass
class Activity:
    title: str
    description: str = ""
    sessions: list[Session] = field(default_factory=list)


def hidden_fields(page: str) -> dict[str, str]:
    """Extract ASP.NET hidden fields via BeautifulSoup (regex is unsafe here)."""
    soup = BeautifulSoup(page, "html.parser")
    fields: dict[str, str] = {}
    for key in _HIDDEN_KEYS:
        node = soup.find("input", attrs={"name": key}) or soup.find(
            "input", attrs={"id": key}
        )
        if node is None or node.get("value") is None:
            snippet = re.sub(r"\s+", " ", page[:400]).strip()
            raise RuntimeError(
                f"Missing ASP.NET hidden field {key} "
                f"(page_len={len(page)}; snippet={snippet!r})"
            )
        fields[key] = html.unescape(node["value"])
    return fields


def common_payload(category_id: str, age_range: str, today: str) -> dict[str, str]:
    return {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "ctl01$mainContent$ddlCategoryList": category_id,
        "ctl01$mainContent$ddlAgeRange": age_range,
        "ctl01$mainContent$txtStartDate": today,
        "ctl01$mainContent$hdnStartDate": today,
        **DEFAULT_FILTERS,
    }


def paging_state(page: str) -> tuple[int, int]:
    match = _PAGING_RE.search(page)
    return (int(match.group(1)), int(match.group(2))) if match else (1, 1)


def _request(session: requests.Session, method: str, url: str, **kwargs) -> str:
    """HTTP helper with retries for transient blocks / empty ASP.NET pages."""
    kwargs.setdefault("timeout", 60)
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.request(method, url, **kwargs)
            response.raise_for_status()
            text = response.text
            if "__VIEWSTATE" in text:
                return text
            last_error = RuntimeError(
                f"{method} {url} returned {response.status_code} without __VIEWSTATE "
                f"(len={len(text)}; attempt {attempt}/{MAX_RETRIES})"
            )
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
        sleep_for = RETRY_BACKOFF * attempt
        print(f"retry {attempt}/{MAX_RETRIES} after {sleep_for:.0f}s: {last_error}", file=sys.stderr)
        time.sleep(sleep_for)
        # Re-warm cookies between retries.
        try:
            session.get(HOME_URL, timeout=30)
        except requests.RequestException:
            pass
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {last_error}")


def warm_session(session: requests.Session) -> None:
    session.get(HOME_URL, timeout=30)
    time.sleep(DELAY)
    page = _request(session, "GET", BASE_URL)
    hidden_fields(page)  # fail fast if the search form is unreachable


def iter_pages(session, category_id, age_range, today):
    seed = hidden_fields(_request(session, "GET", BASE_URL))
    payload = common_payload(category_id, age_range, today)
    payload.update(seed)
    payload["ctl01$mainContent$LinkButton1"] = "Search Activities"
    page = _request(
        session,
        "POST",
        BASE_URL,
        data=payload,
        headers={"Referer": BASE_URL, "Origin": "https://fairfax.usedirect.com"},
    )

    for _ in range(MAX_PAGES):
        yield page
        current, total = paging_state(page)
        if current >= total:
            break
        next_payload = common_payload(category_id, age_range, today)
        next_payload["__EVENTTARGET"] = "ctl01$mainContent$bNext"
        next_payload.update(hidden_fields(page))
        time.sleep(DELAY)
        page = _request(
            session,
            "POST",
            BASE_URL,
            data=next_payload,
            headers={"Referer": BASE_URL, "Origin": "https://fairfax.usedirect.com"},
        )


def _clean(text: str) -> str:
    return " ".join(text.split())


class _Empty:
    def get_text(self, *_args, **_kwargs):
        return ""


_EMPTY = _Empty()


def parse_sessions(panel) -> list[Session]:
    sessions = []
    for row in panel.select("tbody tr"):
        cells = {cell["data-label"]: cell for cell in row.select("td[data-label]")}
        if "Session" not in cells:
            continue
        schedule = (
            " / ".join(
                line.strip()
                for line in cells["Schedule"].get_text("\n").splitlines()
                if line.strip()
            )
            if "Schedule" in cells
            else ""
        )
        status = next(
            (
                cells[label].get_text(" ", strip=True)
                for label in ("Select", "Status")
                if label in cells
            ),
            "",
        )
        sessions.append(
            Session(
                session=_clean(cells["Session"].get_text()),
                place=_clean(cells.get("Place", _EMPTY).get_text()),
                schedule=schedule,
                ages=_clean(cells.get("Restrictions", _EMPTY).get_text()),
                seats=_clean(cells.get("Seats", _EMPTY).get_text()),
                signup=_clean(cells.get("Sign Up", _EMPTY).get_text()),
                status=_clean(status),
            )
        )
    return sessions


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
    existing = merged.get(activity.title)
    if existing is None:
        merged[activity.title] = activity
        return
    if activity.description and not existing.description:
        existing.description = activity.description
    by_code = {s.session: s for s in existing.sessions}
    for session in activity.sessions:
        have = by_code.get(session.session)
        if have is None:
            existing.sessions.append(session)
            by_code[session.session] = session
        else:
            have.age_ranges.update(session.age_ranges)


def _coords_for_places(places: list[str]) -> dict[str, dict]:
    aliases = {
        "Sully Community Center": "Sully CommCtr",
    }
    out: dict[str, dict] = {}
    for name in places:
        entry = PLACE_COORDS.get(name) or PLACE_COORDS.get(aliases.get(name, ""))
        if entry is None:
            continue
        out[name] = {
            "lat": float(entry["lat"]),
            "lng": float(entry["lng"]),
            "kind": entry.get("kind")
            or ("rec_center" if "Rec Center" in name else "other"),
        }
    return out


def build_catalog(results: dict[str, list[Activity]], today: str) -> dict:
    activities = []
    for category, items in results.items():
        for activity in items:
            activities.append(
                {
                    "category": category,
                    "title": activity.title,
                    "description": activity.description,
                    "sessions": [
                        {
                            "session": s.session,
                            "place": s.place,
                            "schedule": s.schedule,
                            "ages": s.ages,
                            "seats": s.seats,
                            "signup": s.signup,
                            "status": s.status,
                            "age_ranges": sorted(s.age_ranges),
                        }
                        for s in activity.sessions
                    ],
                }
            )
    places = sorted(
        {s["place"] for a in activities for s in a["sessions"] if s["place"]}
    )
    statuses = sorted(
        {s["status"] for a in activities for s in a["sessions"] if s["status"]}
    )
    categories = [c for c, acts in results.items() if acts]
    return {
        "generated": today,
        "search_url": BASE_URL,
        "age_ranges": list(AGE_RANGES),
        "categories": categories,
        "places": places,
        "statuses": statuses,
        "place_coords": _coords_for_places(places),
        "activities": activities,
    }


def write_catalog(catalog: dict) -> None:
    payload = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    DATA_JS.write_text(f"window.__FCPA_DATA__ = {payload};\n", encoding="utf-8")


def scrape(today: str) -> dict[str, list[Activity]]:
    merged: dict[str, dict[str, Activity]] = {name: {} for name in CATEGORIES.values()}
    total_jobs = len(AGE_RANGES) * len(CATEGORIES)
    done = 0

    with requests.Session() as session:
        session.headers.update(HEADERS)
        print("Warming session…", file=sys.stderr)
        warm_session(session)
        for age_range in AGE_RANGES:
            for category_id, category_name in CATEGORIES.items():
                done += 1
                before = len(merged[category_name])
                for page in iter_pages(session, category_id, age_range, today):
                    for activity in parse_cards(page):
                        for s in activity.sessions:
                            s.age_ranges.add(age_range)
                        merge_activity(merged[category_name], activity)
                added = len(merged[category_name]) - before
                print(
                    f"[{done}/{total_jobs}] {age_range} · {category_name}: "
                    f"+{added} (category total {len(merged[category_name])})",
                    file=sys.stderr,
                )
                time.sleep(DELAY)

    return {name: list(acts.values()) for name, acts in merged.items()}


def main() -> None:
    today = date.today().strftime("%m/%d/%Y")
    results = scrape(today)
    catalog = build_catalog(results, today)
    write_catalog(catalog)
    print(
        f"Wrote {len(catalog['activities'])} activities to {DATA_JS}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
