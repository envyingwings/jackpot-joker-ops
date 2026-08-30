"""
core.py

Consolidated scraping + status + scheduling logic for the Jackpot Joker site
generator. This replaces the separate Obsidian-oriented scripts
(scrape_competitions.py, update_competition_status.py,
generate_ad_calendar.py) with one shared data model that the site builder
renders directly to HTML - no markdown notes, no Obsidian, no dataviewjs.

This is an experimental read-only mirror: it does its own scrape and keeps
its own JSON cache (site_data.json) in this folder. It never touches your
Obsidian vault.
"""

import hashlib
import html
import json
import os
import re
import time
from datetime import datetime, date, timedelta
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config (mirrors the constants from the original scripts)
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
REQUEST_DELAY_SECONDS = 0.4

POSTS_PER_DAY = 4
MIN_GAP_DAYS = 5
NOT_LIVE_STATUSES = {"inactive", "drawn"}

WEEKDAY_SLOT_TIMES = ["9:30am", "12:00pm", "7:00pm", "7:00pm"]
WEEKEND_SLOT_TIMES = ["11:00am", "11:00am", "5:00pm", "5:00pm"]
WEEKEND_DAYS = {5, 6}
SATURDAY_WEEKDAY_INDEX = 5

ENGAGEMENT_POST_INTERVAL_DAYS = 2
ENGAGEMENT_POST_SLOT_INDEX = 1
ENGAGEMENT_POST_IDEAS = [
    "Premier League predictions - who's your pick this week?",
    "What would you spend £1000 tax-free cash on?",
    "Staycation or abroad - what are your summer plans?",
    "Tag someone who'd win this and never tell you",
    "Guess the prize value - closest guess gets a shoutout",
    "Caption competition - best caption wins a shoutout",
    "Tell us about the best win you've ever had",
    "This or that: BBQ or takeaway?",
]

MONDAY_WINNERS_SLOT_INDEX = 0
MONDAY_WINNERS_TEXT = "Announce Winners of Sunday Draws"

INSTANT_WIN_TAG = "Instant-Wins"
NO_INSTANT_WIN_TEXT = "No instant win available today"

DATA_FILE = os.path.join(os.path.dirname(__file__), "site_data.json")


# ---------------------------------------------------------------------------
# Scraping (from scrape_competitions.py)
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-") or "untitled"


def strip_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    raw_html = html.unescape(raw_html)
    raw_html = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.I)
    raw_html = re.sub(r"</p>", "\n\n", raw_html, flags=re.I)
    raw_html = re.sub(r"</h[1-6]>", "\n\n", raw_html, flags=re.I)
    raw_html = re.sub(r"</li>", "\n", raw_html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", raw_html)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_page_json(page_html: str) -> dict:
    soup = BeautifulSoup(page_html, "html.parser")
    app_div = soup.find("div", id="app")
    if not app_div or not app_div.get("data-page"):
        raise ValueError(
            "Could not find <div id=\"app\" data-page=\"...\"> in the page HTML. "
            "The site structure may have changed."
        )
    raw = app_div["data-page"]
    return json.loads(raw)


def get_competitions_list(page_json: dict) -> list:
    try:
        return page_json["props"]["competitions"]["data"]
    except (KeyError, TypeError) as e:
        raise ValueError(f"Unexpected JSON shape, couldn't find props.competitions.data: {e}")


def parse_draw_date(draw_str) -> "date | None":
    if not draw_str or not isinstance(draw_str, str):
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(draw_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def scrape_live_competitions(url: str, existing_tags: dict) -> list:
    """
    Scrapes the listing page and returns a list of competition dicts in our
    normalized internal shape. existing_tags is a dict of {note_filename:
    [tags]} carried over from the previous run, so manually-added tags (like
    Instant-Wins) survive a re-scrape rather than being wiped each time.
    """
    session = requests.Session()
    resp = session.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    page_json = extract_page_json(resp.text)
    raw_competitions = get_competitions_list(page_json)

    results = []
    for comp in raw_competitions:
        name = comp.get("name", "Untitled Competition")
        slug = comp.get("slug")
        comp_url = urljoin(url, f"/competitions/{slug}")
        note_filename = slugify(name)

        categories = [c.get("name") for c in (comp.get("categories") or []) if c.get("name")]
        category_tags = [slugify(c).replace("-", " ").title().replace(" ", "-") for c in categories]
        tags = ["competition"] + category_tags
        # Carry over any manually-added tags (e.g. Instant-Wins) from the previous run
        for t in existing_tags.get(note_filename, []):
            if t not in tags:
                tags.append(t)

        results.append({
            "title": name,
            "note_filename": note_filename,
            "source": comp_url,
            "draw_date": comp.get("draw_clean"),  # stored as string, parsed on demand
            "status": comp.get("status"),
            "ticket_price": comp.get("price"),
            "prize_value": comp.get("end_prize_value"),
            "categories": categories,
            "tags": tags,
            "description": strip_html(comp.get("description", "")),
            "image_path": comp.get("image_path"),
        })

    return results


# ---------------------------------------------------------------------------
# Data persistence (replaces markdown notes as the source of truth)
# ---------------------------------------------------------------------------

def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {"competitions": [], "last_scraped": None}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def refresh_from_site(url: str) -> dict:
    """Scrapes fresh, merges with existing tags, marks missing competitions Inactive,
    and updates status for any competition whose draw_date has passed. Saves and
    returns the updated data dict."""
    data = load_data()
    existing_tags = {c["note_filename"]: c.get("tags", []) for c in data["competitions"]}
    existing_by_filename = {c["note_filename"]: c for c in data["competitions"]}

    scraped = scrape_live_competitions(url, existing_tags)
    scraped_filenames = {c["note_filename"] for c in scraped}

    # Mark anything that dropped off the site as Inactive, but keep it in our records
    for filename, comp in existing_by_filename.items():
        if filename not in scraped_filenames:
            comp["status"] = "Inactive"

    # Recompute status locally for early-drawn competitions the site hasn't flagged yet
    today = date.today()
    for comp in scraped:
        d = parse_draw_date(comp["draw_date"])
        if d and d < today and (comp.get("status") or "").strip().lower() not in NOT_LIVE_STATUSES:
            comp["status"] = "Drawn"

    # Merge: scraped competitions replace/refresh their entries; untouched existing
    # (now-inactive) ones are kept as historical records.
    merged = {c["note_filename"]: c for c in existing_by_filename.values()}
    for c in scraped:
        merged[c["note_filename"]] = c

    data["competitions"] = list(merged.values())
    data["last_scraped"] = datetime.now().isoformat()
    save_data(data)
    return data


# ---------------------------------------------------------------------------
# Scheduling (from generate_ad_calendar.py)
# ---------------------------------------------------------------------------

def _upcoming_sunday(current_day: date) -> date:
    days_until_sunday = (6 - current_day.weekday()) % 7
    return current_day + timedelta(days=days_until_sunday)


def build_schedule(competitions: list, start_date: date, num_days: int) -> list:
    """Same priority-ordered scheduling logic as generate_ad_calendar.py's build_schedule."""
    # Normalize: parse draw_date strings into date objects for internal use
    comps = []
    for c in competitions:
        d = dict(c)
        d["_draw_date"] = parse_draw_date(c.get("draw_date"))
        d["_is_instant_win"] = any(
            str(t).strip().lower() == INSTANT_WIN_TAG.lower() for t in c.get("tags", [])
        )
        comps.append(d)

    last_posted = {}
    post_count = {}

    def eligible_pool(current_day, ignore_gap=False):
        pool = []
        for comp in comps:
            status = (comp.get("status") or "").strip().lower()
            if status in NOT_LIVE_STATUSES:
                continue
            if comp["_draw_date"] and comp["_draw_date"] < current_day:
                continue
            if not ignore_gap:
                last = last_posted.get(comp["title"])
                if last and (current_day - last).days < MIN_GAP_DAYS:
                    continue
            pool.append(comp)
        return pool

    def fairness_key(comp):
        d = comp["_draw_date"] or date.max
        return (post_count.get(comp["title"], 0), d)

    def take(comp, day_picks):
        day_picks.append(comp)
        last_posted[comp["title"]] = current_day
        post_count[comp["title"]] = post_count.get(comp["title"], 0) + 1

    schedule = []
    current_day = start_date

    for day_offset in range(num_days):
        is_monday = current_day.weekday() == 0
        is_saturday = current_day.weekday() == SATURDAY_WEEKDAY_INDEX
        is_engagement_day = (
            not is_monday
            and ENGAGEMENT_POST_INTERVAL_DAYS > 0
            and day_offset % ENGAGEMENT_POST_INTERVAL_DAYS == 0
        )
        upcoming_sunday = _upcoming_sunday(current_day)

        reserved_slots = 0
        if is_monday:
            reserved_slots += 1
        if is_engagement_day:
            reserved_slots += 1
        slots_today = max(POSTS_PER_DAY - reserved_slots, 0)

        day_picks = []
        no_instant_win_flag = False
        already_picked_titles = set()

        if is_saturday:
            pool = [c for c in eligible_pool(current_day, ignore_gap=True) if c["_draw_date"] == upcoming_sunday]
            pool.sort(key=fairness_key)
            for comp in pool:
                if len(day_picks) >= slots_today:
                    break
                take(comp, day_picks)
        else:
            sunday_pool = [c for c in eligible_pool(current_day) if c["_draw_date"] == upcoming_sunday]
            sunday_pool.sort(key=fairness_key)
            for comp in sunday_pool:
                if len(day_picks) >= slots_today:
                    break
                take(comp, day_picks)
                already_picked_titles.add(comp["title"])

            if len(day_picks) < slots_today:
                instant_win_pool = [
                    c for c in eligible_pool(current_day)
                    if c["_is_instant_win"] and c["title"] not in already_picked_titles
                ]
                instant_win_pool.sort(key=fairness_key)
                if instant_win_pool:
                    take(instant_win_pool[0], day_picks)
                    already_picked_titles.add(instant_win_pool[0]["title"])
                else:
                    no_instant_win_flag = True

            remaining_slots = slots_today - len(day_picks)
            if no_instant_win_flag:
                remaining_slots -= 1
            if remaining_slots > 0:
                normal_pool = [
                    c for c in eligible_pool(current_day)
                    if c["title"] not in already_picked_titles
                ]
                normal_pool.sort(key=fairness_key)
                for comp in normal_pool:
                    if len(day_picks) >= slots_today - (1 if no_instant_win_flag else 0):
                        break
                    take(comp, day_picks)

        schedule.append({
            "date": current_day,
            "competitions": day_picks,
            "engagement_post": is_engagement_day,
            "is_monday": is_monday,
            "no_instant_win": no_instant_win_flag,
        })
        current_day += timedelta(days=1)

    return schedule


def get_calendar_start_date(reference: "date | None" = None) -> date:
    """Always the Monday of the current (or given) week."""
    d = reference or date.today()
    return d - timedelta(days=d.weekday())


# ---------------------------------------------------------------------------
# Sunday-draws-this-month report (from the folded-in sunday_draws script)
# ---------------------------------------------------------------------------

def find_sunday_draws_this_month(competitions: list, today: "date | None" = None) -> list:
    today = today or date.today()
    month_start = today.replace(day=1)
    results = []
    for c in competitions:
        d = parse_draw_date(c.get("draw_date"))
        if not d or not (month_start <= d <= today) or d.weekday() != 6:
            continue
        is_instant_win = any(
            str(t).strip().lower() == INSTANT_WIN_TAG.lower() for t in c.get("tags", [])
        )
        if is_instant_win:
            continue
        results.append({**c, "_draw_date": d})
    results.sort(key=lambda c: c["_draw_date"])
    return results
