"""Scrape Fairfax FCPA activities for a given age range across every category.

The activity search at Fairfax County Park Authority is an ASP.NET WebForms page
that forces you to pick a single category, then paginates the results. This script
sweeps every category for one age range, follows the pagination postbacks, and
writes a single grouped Markdown report.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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


def to_markdown(
    results: dict[str, list[Activity]], age_option: str, place_label: str, today: str
) -> str:
    total = sum(len(items) for items in results.values())
    lines = [
        f"# Fairfax FCPA Activities \u2014 {age_option}",
        "",
        f"Generated {today} \u00b7 {total} activities across {len(results)} categories.",
        "",
        (
            f"Filters: {place_label}, All Months, all days of the week, any instructor, "
            f"starting on or after {today}."
        ),
        "",
    ]
    for category, activities in results.items():
        lines.append(f"## {category} ({len(activities)})")
        lines.append("")
        if not activities:
            lines.append("_(none)_")
            lines.append("")
            continue
        for activity in activities:
            lines.append(f"### {activity.title}")
            lines.append("")
            if activity.description:
                lines.append(activity.description)
                lines.append("")
            if activity.sessions:
                lines.append("| Session | Place | Schedule | Ages | Seats | Sign Up | Status |")
                lines.append("| --- | --- | --- | --- | --- | --- | --- |")
                for s in activity.sessions:
                    cells = (s.session, s.place, s.schedule, s.ages, s.seats, s.signup, s.status)
                    lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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
        Path(__file__).parent / "outputs" / f"activities_{slug}{place_suffix}.md"
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
        to_markdown(results, age_option, place_label, today), encoding="utf-8"
    )
    total = sum(len(items) for items in results.values())
    print(f"Wrote {total} activities to {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
