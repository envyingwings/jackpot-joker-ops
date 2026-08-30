#!/usr/bin/env python3
"""
build_site.py

Generates the Jackpot Joker ops mini-site: three static HTML pages (Ad
Calendar, Sunday Draws This Month, All Competitions), built directly from a
fresh scrape - no Obsidian, no markdown notes, no dataviewjs.

This is a standalone experiment: it scrapes on its own and keeps its own
JSON cache (site_data.json) in this folder. It never reads or writes
anything in your Obsidian vault.

Usage:
    python build_site.py
    python build_site.py --no-scrape     # rebuild pages from the last cached scrape
    python build_site.py --days 7        # how many days the ad calendar covers (default: one week)
"""

import argparse
import html
import os
import webbrowser
from datetime import datetime, date

from jinja2 import Environment, FileSystemLoader

import core

BASE_DIR = os.path.dirname(__file__)
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
SITE_URL = "https://jackpotjoker.co.uk/competitions"

env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=False)


def render_page(page_title: str, active: str, content_html: str) -> str:
    template = env.get_template("base.html")
    return template.render(
        page_title=page_title,
        active=active,
        content=content_html,
        generated_at=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )


def status_badge(status: str) -> str:
    s = (status or "").strip().lower()
    if s == "active":
        return '<span class="status-badge status-active">Active</span>'
    if s == "drawn":
        return '<span class="status-badge status-drawn">Drawn</span>'
    if s == "inactive":
        return '<span class="status-badge status-inactive">Inactive</span>'
    return f'<span class="status-badge">{status or "—"}</span>'


def date_stub(d: date, day_name: str) -> str:
    is_weekend = d.weekday() in core.WEEKEND_DAYS
    cls = "date-stub weekend" if is_weekend else "date-stub"
    return (
        f'<span class="{cls}"><span class="date">{d.strftime("%d/%m/%Y")}</span>'
        f'<span class="day">{day_name}</span></span>'
    )


def comp_link(comp: dict) -> str:
    title = html.escape(comp["title"])
    return f'<a href="competitions.html#{comp["note_filename"]}">{title}</a>'


# ---------------------------------------------------------------------------
# Page: Ad Calendar
# ---------------------------------------------------------------------------

def build_calendar_page(data: dict, days: int) -> str:
    competitions = data["competitions"]
    start_date = core.get_calendar_start_date()
    schedule = core.build_schedule(competitions, start_date, days)

    rows_html = []
    for entry in schedule:
        d = entry["date"]
        day_name = d.strftime("%A")
        picks = entry["competitions"]
        is_monday = entry["is_monday"]
        is_engagement = entry["engagement_post"]
        no_instant_win = entry["no_instant_win"]

        reserved = set()
        if is_monday:
            reserved.add(core.MONDAY_WINNERS_SLOT_INDEX)
        if is_engagement:
            reserved.add(core.ENGAGEMENT_POST_SLOT_INDEX)
        comp_slot_indices = [i for i in range(core.POSTS_PER_DAY) if i not in reserved]

        slot_content = {}
        for comp, idx in zip(picks, comp_slot_indices):
            slot_content[idx] = ("comp", comp)
        if is_monday:
            slot_content[core.MONDAY_WINNERS_SLOT_INDEX] = ("winners", None)
        if is_engagement:
            slot_content[core.ENGAGEMENT_POST_SLOT_INDEX] = ("engagement", None)
        if no_instant_win:
            leftover = next((i for i in comp_slot_indices if i not in slot_content), None)
            if leftover is not None:
                slot_content[leftover] = ("noinstant", None)

        slot_cells = []
        for i in range(core.POSTS_PER_DAY):
            if i not in slot_content:
                slot_cells.append('<span class="slot-empty">—</span>')
                continue
            kind, payload = slot_content[i]
            if kind == "comp":
                slot_cells.append(comp_link(payload))
            elif kind == "winners":
                slot_cells.append(f'<span class="tag-winners">🏆 {core.MONDAY_WINNERS_TEXT}</span>')
            elif kind == "engagement":
                slot_cells.append('<span class="tag-engagement">📣 Engagement post</span>')
            elif kind == "noinstant":
                slot_cells.append(f'<span class="tag-noinstant">⚡ {core.NO_INSTANT_WIN_TEXT}</span>')

        times = core.WEEKEND_SLOT_TIMES if d.weekday() in core.WEEKEND_DAYS else core.WEEKDAY_SLOT_TIMES
        rows_html.append(
            f'<tr><td class="day-cell">{date_stub(d, day_name)}</td>'
            + "".join(f"<td>{cell}</td>" for cell in slot_cells)
            + "</tr>"
        )

    weekday_headers = "".join(f"<th>{t}</th>" for t in core.WEEKDAY_SLOT_TIMES)

    content = f"""
    <h1>Advertising Calendar</h1>
    <p class="subtitle">Week starting <span class="mono">{start_date.strftime('%d/%m/%Y')}</span> ·
       {core.POSTS_PER_DAY} slots/day · no repeats within {core.MIN_GAP_DAYS} days</p>

    <dl class="legend">
      <div><dt>Weekday times</dt><dd>{', '.join(core.WEEKDAY_SLOT_TIMES)} (slots 3 &amp; 4 together)</dd></div>
      <div><dt>Weekend times</dt><dd>{', '.join(core.WEEKEND_SLOT_TIMES)} (1&amp;2, and 3&amp;4, together)</dd></div>
      <div><dt>Engagement post</dt><dd>every {core.ENGAGEMENT_POST_INTERVAL_DAYS} days, Slot 2 (skipped on Mondays)</dd></div>
      <div><dt>Mondays</dt><dd>Slot 1 fixed to winners announcement; remaining slots prioritise Sunday draws</dd></div>
      <div><dt>Saturdays</dt><dd>all slots are competitions drawing tomorrow (Sunday)</dd></div>
    </dl>

    <table>
      <thead>
        <tr><th>Date</th>{weekday_headers}</tr>
      </thead>
      <tbody>
        {''.join(rows_html)}
      </tbody>
    </table>
    """
    return render_page("Ad Calendar", "calendar", content)


# ---------------------------------------------------------------------------
# Page: Sunday Draws This Month
# ---------------------------------------------------------------------------

def build_sunday_draws_page(data: dict) -> str:
    competitions = data["competitions"]
    today = date.today()
    results = core.find_sunday_draws_this_month(competitions, today)
    month_label = today.strftime("%B %Y")

    if results:
        rows = "".join(
            f'<tr><td class="mono">{c["_draw_date"].strftime("%d/%m/%Y")}</td>'
            f'<td>{comp_link(c)}</td></tr>'
            for c in results
        )
        table = f"""
        <table>
          <thead><tr><th>Draw Date</th><th>Competition</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
        """
    else:
        table = '<div class="empty-state">No Sunday draws found yet this month.</div>'

    content = f"""
    <h1>Sunday Draws — {month_label}</h1>
    <p class="subtitle">Found <span class="mono">{len(results)}</span> competition(s) drawn on a Sunday
       this month, through <span class="mono">{today.strftime('%d/%m/%Y')}</span>.
       Instant-Win competitions are excluded.</p>
    {table}
    """
    return render_page("Sunday Draws", "sunday", content)


# ---------------------------------------------------------------------------
# Page: All Competitions
# ---------------------------------------------------------------------------

def build_competitions_page(data: dict) -> str:
    competitions = sorted(
        data["competitions"],
        key=lambda c: (core.parse_draw_date(c.get("draw_date")) or date.max)
    )

    rows = []
    for c in competitions:
        d = core.parse_draw_date(c.get("draw_date"))
        draw_str = d.strftime("%d/%m/%Y") if d else "—"
        is_instant_win = any(
            str(t).strip().lower() == core.INSTANT_WIN_TAG.lower() for t in c.get("tags", [])
        )
        iw_flag = '<span class="instant-win-flag">⚡ Instant Win</span>' if is_instant_win else ""
        price = c.get("ticket_price")
        price_str = f"£{price}" if price else "—"

        rows.append(
            f'<tr id="{c["note_filename"]}">'
            f'<td><a href="{html.escape(c.get("source", "#"))}" target="_blank" rel="noopener">{html.escape(c["title"])}</a> {iw_flag}</td>'
            f'<td class="mono">{draw_str}</td>'
            f'<td class="mono">{price_str}</td>'
            f'<td>{status_badge(c.get("status"))}</td>'
            f'</tr>'
        )

    if rows:
        table = f"""
        <table>
          <thead><tr><th>Competition</th><th>Draw Date</th><th>Price</th><th>Status</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
        """
    else:
        table = '<div class="empty-state">No competitions scraped yet. Run without --no-scrape first.</div>'

    content = f"""
    <h1>All Competitions</h1>
    <p class="subtitle"><span class="mono">{len(competitions)}</span> competitions on record
       (last scraped <span class="mono">{data.get('last_scraped', 'never')}</span>).</p>
    {table}
    """
    return render_page("All Competitions", "competitions", content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build the Jackpot Joker ops mini-site.")
    parser.add_argument("--no-scrape", action="store_true", help="Rebuild pages from the last cached scrape instead of hitting the site")
    parser.add_argument("--days", type=int, default=7, help="How many days the ad calendar covers (default 7 - one week)")
    parser.add_argument("--url", default=SITE_URL, help="Competitions listing page URL")
    parser.add_argument("--open", action="store_true", help="Open the calendar page in your browser when done")
    parser.add_argument("--ci", action="store_true", help="Non-interactive mode for automated runs (e.g. GitHub Actions) - skips the browser launch and the 'press enter' pause")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if args.no_scrape:
        print("Skipping scrape, using cached data...")
        data = core.load_data()
        if not data["competitions"]:
            print("No cached data found - run once without --no-scrape first.")
            return
    else:
        print(f"Scraping {args.url} ...")
        data = core.refresh_from_site(args.url)
        print(f"Scraped {len(data['competitions'])} competitions.")

    pages = {
        "calendar.html": build_calendar_page(data, args.days),
        "sunday-draws.html": build_sunday_draws_page(data),
        "competitions.html": build_competitions_page(data),
        "index.html": (
            '<!DOCTYPE html><html><head><meta charset="UTF-8">'
            '<meta http-equiv="refresh" content="0; url=calendar.html">'
            '<title>Redirecting...</title></head>'
            '<body>Redirecting to <a href="calendar.html">the calendar</a>...</body></html>'
        ),
    }

    for filename, html_content in pages.items():
        path = os.path.join(OUTPUT_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"Wrote {path}")

    print(f"\nDone. Open {os.path.join(OUTPUT_DIR, 'calendar.html')} in your browser.")

    if args.open and not args.ci:
        webbrowser.open(f"file://{os.path.join(OUTPUT_DIR, 'calendar.html')}")

    if not args.ci:
        input("\nPress Enter to close...")


if __name__ == "__main__":
    main()
