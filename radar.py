#!/usr/bin/env python3
"""Internship Radar — daily scanner for internships, early-career roles & competitions.

Zero external dependencies (stdlib only). Designed to run free on GitHub Actions.
Sources: Greenhouse / Lever / Ashby public job-board APIs, RemoteOK, Devpost hackathons,
Unstop competitions.

Quality controls:
  - Every role is tagged AI/ML or Tech (anything else, e.g. content/marketing/HR/BD, is dropped).
  - Every role is typed: internship / fellowship / early-career — UI filters on this.
  - Term year is extracted from titles ("Summer 2026" vs "2027") so past-term postings
    can be hidden by default.
  - Company-tier boost so top-tier companies always rank above unknown ones.
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

# Word-boundary role matching — plain substring "intern" also matches
# "internal"/"international", which is how full-time roles snuck in before.
INTERN_RE = re.compile(r"\b(intern(?:ship)?s?|co-?op)\b", re.I)
FELLOW_RE = re.compile(r"\b(fellowships?|fellows?)\b", re.I)
GRAD_RE = re.compile(
    r"\b(new\s*grad(?:uate)?|university\s*grad(?:uate)?|campus\s*hire|"
    r"apprentice(?:ship)?|early\s*career|graduate\s*(?:engineer|program|trainee))\b", re.I)

# Explicit experience requirements ("5+ years experience") = full-time signal.
EXPERIENCE_RE = re.compile(
    r"\b(\d+)\+?\s*(?:-\s*\d+\s*)?\s*years?\s*(?:of\s*)?(?:experience|exp\.?)\b", re.I)

TERM_RE = re.compile(r"\b(20\d{2})\b")


def role_type(title, text=""):
    blob = f"{title or ''} {text or ''}"
    if INTERN_RE.search(blob):
        return "internship"
    if FELLOW_RE.search(blob):
        return "fellowship"
    if GRAD_RE.search(blob):
        return "grad"
    return None


def term_year(title):
    years = [int(y) for y in TERM_RE.findall(title or "")]
    return max(years) if years else None


UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
]


def _fetch(url, timeout=20, retries=2):
    """Robust GET: retries with exponential backoff + UA rotation.
    404 = board doesn't exist on this ATS → no retry, fail silent."""
    import time, random
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": random.choice(UAS),
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # board not on this ATS — expected, skip quietly
            last = e
        except Exception as e:
            last = e
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1) + random.random())
    print(f"  ! {url.split('/')[2]}: {last}", file=sys.stderr)
    return None


def get_json(url, timeout=20):
    raw = _fetch(url, timeout)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def get_text(url, timeout=20):
    return _fetch(url, timeout) or ""


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
    """Return 'AI/ML', 'Tech', or None (excluded)."""
    blob = f"{title or ''} {text or ''}".lower()
    if any(p.search(blob) for p in AI_ML_RE):
        return "AI/ML"
    if any(p.search(blob) for p in TECH_RE):
        return "Tech"
    return None


def matches(title, text=""):
    t = (title or "").lower()
    x = (text or "").lower()
    blob = f"{t} {x}"
    if any(k in blob for k in CONFIG["exclude_keywords"]):
        return False
    exp = EXPERIENCE_RE.search(blob)
    if exp and int(exp.group(1)) >= 2:
        return False
    if role_type(title, text) is None:
        return False
    yr = term_year(title)
    if yr and yr < CONFIG.get("min_term_year", 2026):
        return False  # stale posting for a past cycle
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
                category=categorize(title, text) or "Tech",
                role_type=("competition" if kind == "competition"
                           else role_type(title, text) or "internship"),
                term=term_year(title))


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


COMP_ALLOW_RE = [re.compile(r"\b" + re.escape(k.strip().lower()) + r"\b")
                 for k in CONFIG.get("competition_orgs_allow", [])]


def ppo_track(org, title=""):
    """True if the competition is run by a reputed company (PPI/PPO potential),
    not a random college fest. Word-boundary match against the config whitelist."""
    blob = f"{org or ''} {title or ''}".lower()
    return any(p.search(blob) for p in COMP_ALLOW_RE)


def fetch_devpost():
    out = []
    data = get_json("https://devpost.com/api/hackathons?status[]=upcoming&status[]=open")
    if not data:
        return out
    for h in data.get("hackathons", [])[:25]:
        title = h.get("title", "")
        prize = re.sub(r"<[^>]+>", "", str(h.get("prize_amount", "")))
        loc = (h.get("displayed_location") or {}).get("location", "")
        orgs = " ".join(str(o) for o in (h.get("organization_name"), title))
        # Devpost is corporate-hackathon-heavy but still gate on the whitelist,
        # with big prize pools (>= $20k) as an alternate quality signal.
        prize_num = int(re.sub(r"[^\d]", "", prize) or 0)
        if not (ppo_track(orgs) or prize_num >= 20000):
            continue
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
                        f"opportunity={opp}&per_page=50&oppstatus=open")
        items = (((data or {}).get("data") or {}).get("data") or [])
        for h in items:
            title = h.get("title", "")
            org = ((h.get("organisation") or {}).get("name") or "Unstop")
            if not ppo_track(org, title):
                continue  # skip college fests / unknown organizers
            slug = h.get("public_url") or h.get("seo_url") or ""
            url = slug if slug.startswith("http") else f"https://unstop.com/{slug.lstrip('/')}"
            item = make_item(f"{org} (Unstop)", title, "India", url, "competition")
            item["score"] += 40  # corporate PPI/PPO-track competition
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
               "&f_TPR=r604800&start=0")  # last 7 days — better India coverage
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
    """Intern mentions in the latest HN Who's Hiring thread. Off by default."""
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


def _gemini(prompt, timeout=60, json_mode=False):
    """One Gemini call (free tier). Returns response text or None."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    try:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.0-flash:generateContent?key=" + key,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        return d["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"  ! gemini: {e}", file=sys.stderr)
        return None


AI_FILTER_PROMPT = """You are vetting opportunities for a 3rd-year Chemical Engineering
student at IIT Bombay targeting SUMMER 2027 internships in SDE / AI-ML / fintech /
product, preferably Bangalore/India, open to remote and abroad.

For each numbered item, judge from the title/company/location:
- drop full-time or experienced-hire roles, staffing agencies, body shops,
  training-institute "internships" that charge fees, college-fest competitions,
  and roles for past terms (Summer/Fall 2026 or earlier).
- Keep genuine internships/co-ops/fellowships at real companies, PPO-track
  corporate competitions, and roles plausibly open for Summer 2027 applicants.
- fit = 0-100: how valuable this is for THIS student (company reputation,
  role relevance, India/Bangalore accessibility, career impact).

Return ONLY a JSON array: [{"i": <item number>, "keep": true/false, "fit": <0-100>,
"why": "<max 8 words>"}] — one entry per item, no other text.

Items:
"""


def ai_filter(items):
    """LLM quality pass: drops junk regex can't catch, adds fit score + reason.
    No API key or any failure → items returned unchanged (rule-based fallback)."""
    if not os.environ.get("GEMINI_API_KEY") or not items:
        return items
    out, chunk_size = [], 40
    for start in range(0, len(items), chunk_size):
        chunk = items[start:start + chunk_size]
        listing = "\n".join(
            f"{n}. [{i['kind']}] {i['title']} @ {i['company']} "
            f"({i['location']}, via {i['source']})"
            for n, i in enumerate(chunk))
        resp = _gemini(AI_FILTER_PROMPT + listing, json_mode=True)
        verdicts = {}
        try:
            for v in json.loads(resp or "[]"):
                verdicts[int(v["i"])] = v
        except Exception as e:
            print(f"  ! ai_filter parse: {e}", file=sys.stderr)
        if not verdicts:          # this chunk failed — keep it untouched
            out += chunk
            continue
        for n, i in enumerate(chunk):
            v = verdicts.get(n)
            if v is None:
                out.append(i)
                continue
            if not v.get("keep", True):
                continue
            fit = max(0, min(100, int(v.get("fit", 50))))
            i["ai_fit"] = fit
            i["ai_why"] = str(v.get("why", ""))[:60]
            i["score"] += fit      # blend rule score + AI fit
            out.append(i)
    dropped = len(items) - len(out)
    print(f"AI filter: kept {len(out)}, dropped {dropped}")
    return out


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
                   "role_type": i["role_type"], "term": i["term"],
                   "ai_fit": i.get("ai_fit"), "ai_why": i.get("ai_why", ""),
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
        try:
            got = fn()
        except Exception as e:  # one broken source must never kill the run
            print(f"  ! {name} crashed: {e}", file=sys.stderr)
            got = []
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

    # quality floor: tier3 companies need a stronger combined signal to qualify
    min_score = CONFIG.get("min_score", 0)
    min_score_tier3 = CONFIG.get("min_score_tier3", min_score)
    uniq = [i for i in uniq
            if i["score"] >= (min_score_tier3 if i["tier"] == "tier3" else min_score)]

    uniq = ai_filter(uniq)  # LLM vetting pass (no-op without GEMINI_API_KEY)

    uniq.sort(key=lambda i: -i["score"])
    uniq = uniq[:CONFIG.get("max_items", 200)]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    old = json.load(open(SEEN_PATH)) if os.path.exists(SEEN_PATH) else {}
    if isinstance(old, list):
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
