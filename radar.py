#!/usr/bin/env python3
"""Internship Radar — daily scanner for internships, early-career roles & competitions.

Zero external dependencies (stdlib only). Designed to run free on GitHub Actions.
Sources: Greenhouse / Lever / Ashby public job-board APIs, Hacker News Who's
Hiring (Algolia API), Devpost hackathons.

Optional: set GEMINI_API_KEY env var to get an AI-written daily brief
(Google Gemini free tier — no Claude subscription needed).
"""

import json, os, re, sys, urllib.request, urllib.error
from datetime import datetime, timezone
from html import escape

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, "config.json")))
SEEN_PATH = os.path.join(HERE, "seen.json")
DOCS = os.path.join(HERE, "docs")
UA = {"User-Agent": "Mozilla/5.0 (internship-radar; personal student project)"}


def get_json(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"  ! {url.split('/')[2]}: {e}", file=sys.stderr)
        return None


def matches(title, text=""):
    t = (title or "").lower()
    x = (text or "").lower()
    if any(k in t for k in CONFIG["exclude_keywords"]):
        return False
    role = any(k in t or k in x for k in CONFIG["role_keywords"])
    domain = any(k in t for k in CONFIG["domain_keywords"])
    return role and (domain or "intern" in t)


def score(title, location):
    s, t, loc = 0, (title or "").lower(), (location or "").lower()
    for k, v in CONFIG["title_boosts"].items():
        if k in t:
            s += v
    for k, v in CONFIG["location_boosts"].items():
        if k in loc:
            s += v
    return s


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
                out.append(dict(source=f"{board} (Greenhouse)", title=title,
                                location=loc, url=j.get("absolute_url", ""),
                                score=score(title, loc), kind="job"))
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
                out.append(dict(source=f"{c} (Lever)", title=title, location=loc,
                                url=j.get("hostedUrl", ""), score=score(title, loc),
                                kind="job"))
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
                out.append(dict(source=f"{b} (Ashby)", title=title, location=loc,
                                url=j.get("jobUrl", "") or j.get("applyUrl", ""),
                                score=score(title, loc), kind="job"))
    return out


def fetch_hn_whoishiring():
    """Intern mentions in the latest HN Who's Hiring thread via Algolia."""
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
            if not first:
                continue
            out.append(dict(source="HN Who's Hiring", title=f"{first} — intern mention",
                            location="see post",
                            url=f"https://news.ycombinator.com/item?id={h['objectID']}",
                            score=10 + score(text[:300], text[:300]), kind="job"))
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
        out.append(dict(source="Devpost", title=f"{title} ({prize})" if prize else title,
                        location=loc, url=h.get("url", ""),
                        score=15 + score(title, loc), kind="competition"))
    return out


def get_text(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"  ! {url.split('/')[2]}: {e}", file=sys.stderr)
        return ""


def fetch_linkedin():
    """LinkedIn guest job-search endpoint (no login). May rate-limit; fails soft."""
    from urllib.parse import quote
    out = []
    for q in CONFIG.get("linkedin_searches", []):
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/"
               f"search?keywords={quote(q['keywords'])}&location={quote(q['location'])}"
               "&f_TPR=r86400&start=0")  # posted in last 24h
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
            out.append(dict(source="LinkedIn", title=title, location=loc,
                            url=link.split("?")[0], score=5 + score(title, loc),
                            kind="job"))
    return out


def fetch_unstop():
    """Unstop (formerly Dare2Compete) — competitions, hackathons, internships in India."""
    out = []
    for opp, kind in [("hackathons", "competition"), ("competitions", "competition"),
                      ("internships", "job")]:
        data = get_json("https://unstop.com/api/public/opportunity/search-result?"
                        f"opportunity={opp}&per_page=15&oppstatus=open")
        items = (((data or {}).get("data") or {}).get("data") or [])
        for h in items:
            title = h.get("title", "")
            org = ((h.get("organisation") or {}).get("name") or "Unstop")
            slug = h.get("public_url") or h.get("seo_url") or ""
            url = slug if slug.startswith("http") else f"https://unstop.com/{slug.lstrip('/')}"
            if kind == "job" and not matches(title, title):
                continue
            out.append(dict(source=f"{org} (Unstop)", title=title, location="India",
                            url=url, score=(15 if kind == "competition" else 20) +
                            score(title, "india"), kind=kind))
    return out


def fetch_remoteok():
    out = []
    data = get_json("https://remoteok.com/api")
    if not isinstance(data, list):
        return out
    for j in data[1:]:
        title = j.get("position", "") or ""
        if matches(title):
            out.append(dict(source=f"{j.get('company','?')} (RemoteOK)", title=title,
                            location="Remote", url=j.get("url", ""),
                            score=score(title, "remote"), kind="job"))
    return out


def ai_brief(items):
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not items:
        return ""
    top = "\n".join(f"- {i['title']} @ {i['source']} ({i['location']})"
                    for i in items[:25])
    body = json.dumps({"contents": [{"parts": [{"text":
        "You are advising a 3rd-year Chemical Engineering student at IIT Bombay "
        "targeting Summer 2027 tech internships (SDE, AI/ML, fintech, product), "
        "preferably Bangalore. In under 150 words, pick the 3-5 most valuable "
        "NEW opportunities below and say why + any deadlines to note:\n" + top}]}]})
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
        "items": [{"source": i["source"], "title": i["title"],
                   "location": i["location"], "url": i["url"],
                   "score": i["score"], "kind": i["kind"],
                   "new": i["key"] in new_keys,
                   "first_seen": i.get("first_seen", today)}
                  for i in items],
    }
    os.makedirs(DOCS, exist_ok=True)
    json.dump(payload, open(os.path.join(DOCS, "data.json"), "w"))


def main():
    items = []
    for name, fn in [("Greenhouse", fetch_greenhouse), ("Lever", fetch_lever),
                     ("Ashby", fetch_ashby), ("HN", fetch_hn_whoishiring),
                     ("Devpost", fetch_devpost), ("LinkedIn", fetch_linkedin),
                     ("Unstop", fetch_unstop), ("RemoteOK", fetch_remoteok)]:
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
    uniq.sort(key=lambda i: -i["score"])

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
