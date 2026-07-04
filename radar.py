#!/usr/bin/env python3
"""Internship Radar — daily scanner for internships, early-career roles & competitions.

Zero external dependencies (stdlib only). Designed to run free on GitHub Actions.
Sources: Greenhouse / Lever / Ashby public job-board APIs, RemoteOK, Devpost hackathons,
Unstop competitions.

Quality controls:
  - Every role is tagged AI/ML or Tech (anything else, e.g. content/marketing/HR/BD, is dropped).
  - Company-tier boost so top-tier companies (OpenAI, Anthropic, Scale AI, Palantir, Stripe...)
    always rank above random/unknown companies, regardless of keyword overlap.
  - min_score floor + max_items cap keep the feed from drowning in long-tail noise.

Optional: set GEMINI_API_KEY env var to get an AI-written daily brief
(Google Gemini free tier — no Claude subscription needed).
"""

import json, os, re, sys, urllib.request, urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, "config.json")))
SEEN_PATH = os.path.join(HERE, "seen.json")
DOCS = os.path.join(HERE, "docs")
UA = {"User-Agent": "Mozilla/5.0 (internship-radar; personal student project)"}

TIER1 = set(CONFIG["company_tiers"].get("tier1", []))
TIER2 = set(CONFIG["company_tiers"].get("tier2", []))
TIER_BOOSTS = CONFIG["tier_boosts"]
DISPLAY_NAMES = CONFIG.get("company_display_names", {})
AI_ML_RE = [re.compile(k) for k in CONFIG["ai_ml_keywords"]]
TECH_RE = [re.compile(k) for k in CONFIG["tech_keywords"]]


def get_json(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"  ! {url.split('/')[2]}: {e}", file=sys.stderr)
        return None


def get_text(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"  ! {url.split('/')[2]}: {e}", file=sys.stderr)
        return ""


def display_name(slug_or_name):
    return DISPLAY_NAMES.get(slug_or_name, slug_or_name)


def company_tier(slug_or_name):
    key = (slug_or_name or "").lower()
    if key in TIER1:
        return "tier1"
    if key in TIER2:
        return "tier2"
    return "tier3"


def categorize(title, text=""):
    """Return 'AI/ML', 'Tech', or None (i.e. excluded — not a role we care about)."""
    blob = f"{title or ''} {text or ''}".lower()
    if any(p.search(blob) for p in AI_ML_RE):
        return "AI/ML"
    if any(p.search(blob) for p in TECH_RE):
        return "Tech"
    return None


def matches(title, text=""):
    t = (title or "").lower()
    x = (text or "").lower()
    if any(k in t for k in CONFIG["exclude_keywords"]):
        return False
    role = any(k in t or k in x for k in CONFIG["role_keywords"])
    if not role:
        return False
    return categorize(title, text) is not None


def score(title, location, company_slug=None):
    s, t, loc = 0, (title or "").lower(), (location or "").lower()
    for k, v in CONFIG["title_boosts"].items():
        hit = re.search(r"\b" + re.escape(k) + r"\b", t) if k in ("ai", "ml") else k in t
        if hit:
            s += v
    for k, v in CONFIG["location_boosts"].items():
        if k in loc:
            s += v
    if company_slug:
        s += TIER_BOOSTS.get(company_tier(company_slug), 0)
    return s


def make_item(source, title, location, url, kind, company_slug=None, text=""):
    return dict(source=source, title=title, location=location, url=url,
                score=score(title, location, company_slug), kind=kind,
                company=display_name(company_slug) if company_slug else source.split(" (")[0],
                tier=company_tier(company_slug) if company_slug else "tier3",
                category=categorize(title, text) or "Tech")


def fetch_greenhouse():
    out = []
    for board in CONFIG["greenhouse_boards"]:
        data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs")
        if not data:
            continue
        for j in data.get("jobs", []):
            title = j.get("title", "")
            loc = (j.get("location") or {}).get("name", "")
            if matches(title):
                out.append(make_item(f"{display_name(board)} (Greenhouse)", title, loc,
                                      j.get("absolute_url", ""), "job", board))
    return out


def fetch_lever():
    out = []
    for c in CONFIG["lever_companies"]:
        data = get_json(f"https://api.lever.co/v0/postings/{c}?mode=json")
        if not isinstance(data, list):
            continue
        for j in data:
            title = j.get("text", "")
            loc = (j.get("categories") or {}).get("location", "") or ""
            if matches(title):
                out.append(make_item(f"{display_name(c)} (Lever)", title, loc,
                                      j.get("hostedUrl", ""), "job", c))
    return out


def fetch_ashby():
    out = []
    for b in CONFIG["ashby_boards"]:
        data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{b}")
        if not data:
            continue
        for j in data.get("jobs", []):
            title = j.get("title", "")
            loc = j.get("location", "") or ""
            if matches(title):
                out.append(make_item(f"{display_name(b)} (Ashby)", title, loc,
                                      j.get("jobUrl", "") or j.get("applyUrl", ""), "job", b))
    return out


def fetch_devpost():
    out = []
    data = get_json("https://devpost.com/api/hackathons?status[]=upcoming&status[]=open")
    if not data:
        return out
    for h in data.get("hackathons", [])[:25]:
        title = h.get("title", "")
        prize = h.get("prize_amount", "")
        prize = re.sub(r"<[^>]+>", "", str(prize))
        loc = (h.get("displayed_location") or {}).get("location", "")
        full_title = f"{title} ({prize})" if prize else title
        item = make_item("Devpost", full_title, loc, h.get("url", ""), "competition")
        item["score"] += 15
        out.append(item)
    return out


def fetch_unstop():
    """Unstop — hackathons/competitions only. The 'internships' feed there is mostly
    HR/BD/campus-ambassador roles from unvetted small companies, so it's excluded."""
    out = []
    for opp in ("hackathons", "competitions"):
        data = get_json("https://unstop.com/api/public/opportunity/search-result?"
                        f"opportunity={opp}&per_page=15&oppstatus=open")
        items = (((data or {}).get("data") or {}).get("data") or [])
        for h in items:
            title = h.get("title", "")
            org = ((h.get("organisation") or {}).get("name") or "Unstop")
            slug = h.get("public_url") or h.get("seo_url") or ""
            url = slug if slug.startswith("http") else f"https://unstop.com/{slug.lstrip('/')}"
            item = make_item(f"{org} (Unstop)", title, "India", url, "competition")
            item["score"] += 15
            out.append(item)
    return out


def fetch_remoteok():
    out = []
    data = get_json("https://remoteok.com/api")
    if not isinstance(data, list):
        return out
    for j in data[1:]:
        title = j.get("position", "") or ""
        company = j.get("company", "") or ""
        if matches(title):
            out.append(make_item(f"{company} (RemoteOK)", title, "Remote",
                                  j.get("url", ""), "job", company.lower()))
    return out


def fetch_linkedin():
    """LinkedIn guest job-search endpoint (no login). Noisy/rate-limited — off by default,
    enable via enabled_sources in config.json."""
    from urllib.parse import quote
    out = []
    for q in CONFIG.get("linkedin_searches", []):
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/"
               f"search?keywords={quote(q['keywords'])}&location={quote(q['location'])}"
               "&f_TPR=r86400&start=0")
        html_txt = get_text(url)
        cards = re.findall(
            r'<a[^>]*base-card__full-link[^>]*href="([^"]+)"[^>]*>.*?'
            r'<span class="sr-only">\s*(.*?)\s*</span>', html_txt, re.S)
        locs = re.findall(r'job-search-card__location">\s*(.*?)\s*<', html_txt)
        for i, (link, title) in enumerate(cards[:15]):
            title = re.sub(r"\s+", " ", title).strip()
            loc = locs[i] if i < len(locs) else q["location"]
            if not matches(title, title):
                continue
            out.append(make_item("LinkedIn", title, loc, link.split("?")[0], "job"))
    return out


def fetch_hn_whoishiring():
    """Intern mentions in the latest HN Who's Hiring thread. Off by default — free-text
    comments are hard to vet for company quality."""
    out = []
    data = get_json("https://hn.algolia.com/api/v1/search_by_date?"
                    "tags=story,author_whoishiring&query=hiring&hitsPerPage=1")
    if not data or not data.get("hits"):
        return out
    story_id = data["hits"][0]["objectID"]
    for q in ("intern", "internship"):
        d = get_json(f"https://hn.algolia.com/api/v1/search?tags=comment,"
                     f"story_{story_id}&query={q}&hitsPerPage=15")
        if not d:
            continue
        for h in d.get("hits", []):
            text = re.sub(r"<[^>]+>", " ", h.get("comment_text") or "")
            first = text.strip().split("|")[0].strip()[:80]
            if not first or not matches(text[:300], text[:300]):
                continue
            out.append(make_item("HN Who's Hiring", f"{first} — intern mention", "see post",
                                  f"https://news.ycombinator.com/item?id={h['objectID']}",
                                  "job", text=text[:300]))
    return out


SOURCE_FNS = {
    "greenhouse": ("Greenhouse", fetch_greenhouse),
    "lever": ("Lever", fetch_lever),
    "ashby": ("Ashby", fetch_ashby),
    "remoteok": ("RemoteOK", fetch_remoteok),
    "devpost": ("Devpost", fetch_devpost),
    "unstop": ("Unstop", fetch_unstop),
    "linkedin": ("LinkedIn", fetch_linkedin),
    "hn": ("HN", fetch_hn_whoishiring),
}


def ai_brief(items):
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not items:
        return ""
    top = "\n".join(f"- {i['title']} @ {i['company']} ({i['location']}) [score {i['score']}]"
                    for i in items[:25])
    body = json.dumps({"contents": [{"parts": [{"text":
        "You are advising a 3rd-year Chemical Engineering student at IIT Bombay "
        "targeting Summer 2027 tech internships (SWE, AI/ML), preferably Bangalore. "
        "In under 150 words, pick the 3-5 most valuable NEW opportunities below — "
        "favor well-known/top-tier companies over obscure ones — and say why + any "
        "deadlines to note:\n" + top}]}]})
    try:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.0-flash:generateContent?key=" + key,
            data=body.encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.loads(r.read())
        return d["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"  ! gemini: {e}", file=sys.stderr)
        return ""


def render(items, new_keys, brief):
    """Write docs/data.json — the static docs/index.html app renders it."""
    now = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "updated": now,
        "brief": brief,
        "items": [{"source": i["source"], "title": i["title"], "company": i["company"],
                   "tier": i["tier"], "category": i["category"],
                   "location": i["location"], "url": i["url"],
                   "score": i["score"], "kind": i["kind"],
                   "new": i["key"] in new_keys,
                   "first_seen": i.get("first_seen", today)}
                  for i in items],
    }
    os.makedirs(DOCS, exist_ok=True)
    json.dump(payload, open(os.path.join(DOCS, "data.json"), "w"))


def main():
    enabled = CONFIG.get("enabled_sources", list(SOURCE_FNS.keys()))
    items = []
    for key in enabled:
        if key not in SOURCE_FNS:
            continue
        name, fn = SOURCE_FNS[key]
        got = fn()
        print(f"{name}: {len(got)} matches")
        items += got

    # dedupe + keys
    seen_urls = set()
    uniq = []
    for i in items:
        if i["url"] in seen_urls:
            continue
        seen_urls.add(i["url"])
        i["key"] = i["url"] or (i["source"] + i["title"])
        uniq.append(i)

    # quality floor: drop long-tail junk below min_score. Unknown/tier3
    # companies need a stronger combined signal (location+keywords) to
    # qualify, since there's no company-reputation signal backing them up —
    # this is what keeps random small companies from drowning out
    # top-tier ones in the feed.
    min_score = CONFIG.get("min_score", 0)
    min_score_tier3 = CONFIG.get("min_score_tier3", min_score)
    uniq = [i for i in uniq
            if i["score"] >= (min_score_tier3 if i["tier"] == "tier3" else min_score)]

    uniq.sort(key=lambda i: -i["score"])

    # cap total size so the feed stays readable
    max_items = CONFIG.get("max_items", 200)
    uniq = uniq[:max_items]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    old = json.load(open(SEEN_PATH)) if os.path.exists(SEEN_PATH) else {}
    if isinstance(old, list):  # migrate old format
        old = {k: today for k in old}
    new_keys = {i["key"] for i in uniq} - set(old)
    for i in uniq:
        i["first_seen"] = old.get(i["key"], today)
    old.update({i["key"]: i["first_seen"] for i in uniq})
    json.dump(old, open(SEEN_PATH, "w"))

    new_items = [i for i in uniq if i["key"] in new_keys]
    brief = ai_brief(new_items or uniq)
    render(uniq, new_keys, brief)
    print(f"\nTotal {len(uniq)} listings, {len(new_keys)} new. Dashboard → docs/index.html")


if __name__ == "__main__":
    main()
