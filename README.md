# 🎯 Internship Radar

A free, self-running tool that scans every morning at **8:00 AM IST** for tech internships, early-career roles, and competitions relevant to Summer 2027 — SDE, AI/ML, fintech, product — with a boost for Bangalore/India/remote roles. **No Claude or paid subscription needed** — it runs entirely on GitHub's free tier.

## What it scans

- **Greenhouse / Lever / Ashby job boards** — 50+ curated companies (Stripe, OpenAI, Databricks, Razorpay, CRED, Groww, Zerodha, Atlassian, Palantir, Perplexity...). Add any company in `config.json`.
- **LinkedIn** — guest job search (last 24h) for your keyword/location combos.
- **Unstop** — Indian competitions, hackathons, and internships (Flipkart GRiD, TATA challenges, etc.).
- **Devpost** — global hackathons with prize money.
- **Hacker News "Who's Hiring"** — startup intern mentions.
- **RemoteOK** — remote intern roles.

New listings since the last run get a green **NEW** badge. Results are scored: Bangalore +30, India +20, remote +15, "2027"/"intern"/ML/quant keyword boosts.

## One-time setup (~10 minutes)

1. **Create a GitHub account** (free) if you don't have one → github.com
2. **Create a new repository** named `internship-radar` (public — required for free Pages). Upload all files in this folder, including the hidden `.github` folder. Easiest: on the repo page, "uploading an existing file" → drag everything in. (If the `.github/workflows/daily.yml` upload is fiddly in the browser, create the file manually: Add file → Create new file → type `.github/workflows/daily.yml` as the name → paste contents.)
3. **Enable the workflow**: repo → Actions tab → enable workflows → select "Daily internship scan" → "Run workflow" to test it now.
4. **Enable the dashboard**: repo → Settings → Pages → Source: "Deploy from a branch" → Branch: `main`, folder: `/docs` → Save.
   Your dashboard will be live at `https://<your-username>.github.io/internship-radar/` — bookmark it. It updates itself every morning.

### Optional: AI daily brief (free, no Claude needed)

Get a free Gemini API key at https://aistudio.google.com/apikey, then:
repo → Settings → Secrets and variables → Actions → New repository secret → name `GEMINI_API_KEY`, paste the key.
Each morning the dashboard will include a short AI-written brief picking the 3–5 best new opportunities for your profile.

## Customizing

Everything lives in `config.json`:

- `greenhouse_boards` / `lever_companies` / `ashby_boards` — company slugs. To find a slug: open a company's careers page; if the URL is `boards.greenhouse.io/acme` or `jobs.lever.co/acme` or `jobs.ashbyhq.com/acme`, the slug is `acme`.
- `linkedin_searches` — keyword + location pairs.
- `location_boosts` / `title_boosts` — tune the scoring.
- `exclude_keywords` — filter out senior roles.

## Run locally (optional)

```
python3 radar.py        # needs Python 3.9+, no packages to install
open docs/index.html
```

## Notes & limits

- LinkedIn's guest endpoint occasionally rate-limits GitHub's servers; the script fails soft (other sources still work). LinkedIn results resume automatically.
- GitHub Actions free tier gives 2,000 min/month; this uses ~2 min/day (~60/month). Comfortable.
- Sources without public APIs (Internshala, company portals like Google/Microsoft careers) can't be scanned reliably — check those weekly by hand. Google STEP / Microsoft / Amazon intern applications for India typically open **July–September of the preceding year**, i.e. mid-2026 for Summer 2027 — set a reminder.
