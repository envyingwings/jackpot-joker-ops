# Jackpot Joker Ops Console (experimental)

A standalone mini-site that mirrors the ad calendar, Sunday draws report,
and competition list — without Obsidian. This is **experimental** and
separate from your Obsidian vault; it does its own scrape and keeps its own
data file (`site_data.json`). It never reads or writes anything in your
vault.

Two ways to run this: **locally** (manual, on your own PC) or **hosted**
(automatic daily rebuild via GitHub Actions + GitHub Pages, works even when
your PC is off).

## Option A: Run it locally

```
pip install requests beautifulsoup4 jinja2
python build_site.py --open
```

- `--no-scrape` rebuilds pages from the last scrape without hitting the site again.
- `--days 14` changes how many days the calendar covers (default 7 - one week).
- Output lands in `output/calendar.html`, `output/sunday-draws.html`, `output/competitions.html` - open any directly in your browser.

## Option B: Host it for free with GitHub Actions + GitHub Pages

This runs the scrape and rebuild once a day automatically, on GitHub's
servers, with no cost and no server of your own to maintain. **The
repository must be public** for GitHub Pages to be free on a personal
account.

### One-time setup

1. **Create a new GitHub repository** (github.com → New repository). Make it
   Public. Don't initialize it with a README (you already have one).

2. **Push this folder to it.** From inside this folder on your computer:
   ```
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo-name>.git
   git push -u origin main
   ```

3. **Enable GitHub Pages for Actions deployment.** On GitHub: go to your
   repo → Settings → Pages → under "Build and deployment", set Source to
   **GitHub Actions** (not "Deploy from a branch").

4. **That's it.** The workflow file at `.github/workflows/build.yml` will:
   - Run automatically every day at 07:00 UK time (adjust the `cron`/`timezone` lines in that file if you want a different time).
   - Can also be triggered manually any time from the repo's **Actions** tab → "Build and deploy Jackpot Joker site" → **Run workflow**.
   - Scrape the site, rebuild all three pages, commit the updated `site_data.json` back to the repo (so competition history persists across runs), and publish `output/` to GitHub Pages.

5. **Find your live URL** after the first run finishes: repo → Settings →
   Pages will show something like `https://<your-username>.github.io/<your-repo-name>/`.
   The calendar will be at `.../calendar.html` on that domain.

### Checking it worked

Repo → **Actions** tab shows every run, whether it succeeded, and full logs
if something failed (e.g. the site's HTML structure changed and scraping
broke - you'll see the Python traceback right there, same as running it
locally).

## What it produces

- **Ad Calendar** — 4 slots/day, Monday winners announcement, Saturday/Sunday
  draw-day push, rotating engagement posts, instant-win guarantee.
- **Sunday Draws This Month** — competitions drawn on a Sunday this
  calendar month, excluding Instant-Win-tagged ones.
- **All Competitions** — every competition ever scraped, including ones no
  longer live (marked Inactive) - nothing is ever deleted from
  `site_data.json`, only status-flagged.

## Notes

- `site_data.json` is the whole "database". Locally, delete it any time to
  start fresh (you'll lose all history). On GitHub, it's committed to the
  repo, so deleting it means deleting it from git too.
- Manually tagging a competition Instant-Wins isn't possible through this
  site yet (no edit UI) - that's still an Obsidian-only workflow for now.
- All logic (scraping, status recomputation, scheduling, Sunday-draws
  filtering) lives in `core.py`. `build_site.py` only turns that data into
  HTML.
- Since the repo is public, anyone can technically view your scraped
  competition data and the site's source - nothing sensitive is stored
  (no logins, no personal data), but worth knowing.

