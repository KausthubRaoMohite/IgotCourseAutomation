"""
iGOT Karmayogi Bulk Auto-Enroller  v2
======================================
Supports three input modes (evaluated in priority order):

  1. Hardcoded IDs   — fill CONFIG["courses_to_enroll"] with do_... IDs.
  2. CLI args        — python igot_autoenroll.py do_123 do_456
  3. Interactive     — leave both empty; the script prompts you at runtime.

For the interactive prompt you can EITHER:
  a) Type / paste do_... IDs directly, OR
  b) Type course name + organisation pairs and let the script search for
     the correct course ID automatically via the iGOT search API.

INPUT FORMAT FOR NAME+ORG SEARCH
---------------------------------
  At the interactive prompt, instead of a do_... ID enter:
      "Course Name" | Organisation Name

  Examples:
      Artificial Intelligence for Karmayogis | Karmayogi Bharat
      Governing Artificial Intelligence | National E-Governance Division (NEGD) MeitY Govt of India

  The script searches, shows you the top match (name, org, rating, duration),
  asks you to confirm, and only adds it if you say yes.
  Organisation is optional — if omitted the best name match is used.

You can also hardcode name+org pairs in CONFIG["courses_to_search"] below.

PROXY SUPPORT (optional)
--------------------------
Set all four env vars before running:

  Windows (Command Prompt):
      set PROXY_USER=your_username
      set PROXY_PASSWORD=your_password
      set PROXY_HOST=proxy.company.com
      set PROXY_PORT=8080
      python igot_autoenroll.py

  Windows (PowerShell):
      $env:PROXY_USER="your_username" ; $env:PROXY_PASSWORD="your_password"
      $env:PROXY_HOST="proxy.company.com" ; $env:PROXY_PORT="8080"
      python igot_autoenroll.py

  Linux / macOS:
      PROXY_USER=u PROXY_PASSWORD=p PROXY_HOST=proxy.co PROXY_PORT=8080 python igot_autoenroll.py

If any proxy variable is missing the script runs without a proxy.

⚠️  Do not log out of Chrome while the script runs — it invalidates the cookie.
"""

import sys
import time
import urllib.parse
import random
import logging
from typing import Optional
import requests
import urllib3
import os
os.environ['no_proxy'] = '*' # REMOVE THIS IF NEEDED (PROXY BASED)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────────────────────────────────────
# ██  CONFIG
# ─────────────────────────────────────────────────────────────────────────────

CONFIG = {
    "user_id": "--PASTE YOUR USER ID--",

    # Paste the FULL cookie string from DevTools (expires per session)
    "cookie": (
        "--PASTE YOUR COOKIE STRING--"
    ),

    # ── Option A: hardcode do_... IDs ─────────────────────────────────────────
    "courses_to_enroll": [
        # "do_1143613347908812801129",
    ],

    # ── Option B: hardcode name + org pairs to search ─────────────────────────
    # Each entry is {"name": "...", "org": "..."}  (org is optional)
    "courses_to_search": [
        # {"name": "Artificial Intelligence for Karmayogis", "org": "Karmayogi Bharat"},
        # {"name": "Governing Artificial Intelligence"},
    ],

    # ── Behaviour ─────────────────────────────────────────────────────────────
    "human": {
        "between_enroll_pause_min": 3.0,
        "between_enroll_pause_max": 7.0,
        "verify_delay_min":         1.0,
        "verify_delay_max":         2.5,
        "between_search_pause_min": 1.0,
        "between_search_pause_max": 3.0,
    },

    # Ask for confirmation before enrolling a search result (True recommended)
    "confirm_search_results": True,

    # Max candidates returned from the search API per query
    "search_limit": 10,
}

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PROTECTED_BASE = "https://portal.igotkarmayogi.gov.in/apis/protected/v8"
PROXIES_BASE   = "https://portal.igotkarmayogi.gov.in/apis/proxies/v8"
SEARCH_URL     = f"{PROXIES_BASE}/sunbirdigot/v4/search"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("igot_enroller")

# ─────────────────────────────────────────────────────────────────────────────
# Proxy
# ─────────────────────────────────────────────────────────────────────────────

def _build_proxies() -> Optional[dict]:
    user     = os.environ.get("PROXY_USER",    "").strip()
    password = os.environ.get("PROXY_PASSWORD", "").strip()
    host     = os.environ.get("PROXY_HOST",    "").strip()
    port     = os.environ.get("PROXY_PORT",    "").strip()

    if not all([user, password, host, port]):
        missing = [k for k, v in {
            "PROXY_USER": user, "PROXY_PASSWORD": password,
            "PROXY_HOST": host, "PROXY_PORT": port,
        }.items() if not v]
        if any([user, password, host, port]):
            log.warning("Proxy : incomplete — missing %s. Running without proxy.",
                        ", ".join(missing))
        return None

    # --- THE FIX: URL Encode the username and password ---
    safe_user = urllib.parse.quote_plus(user)
    safe_password = urllib.parse.quote_plus(password)

    proxy_url = f"http://{safe_user}:{safe_password}@{host}:{port}"
    # -----------------------------------------------------
    
    log.info("Proxy : %s@%s:%s", user, host, port)
    return {"http": proxy_url, "https": proxy_url}
    user     = os.environ.get("PROXY_USER",    "").strip()
    password = os.environ.get("PROXY_PASSWORD", "").strip()
    host     = os.environ.get("PROXY_HOST",    "").strip()
    port     = os.environ.get("PROXY_PORT",    "").strip()

    if not all([user, password, host, port]):
        missing = [k for k, v in {
            "PROXY_USER": user, "PROXY_PASSWORD": password,
            "PROXY_HOST": host, "PROXY_PORT": port,
        }.items() if not v]
        if any([user, password, host, port]):
            log.warning("Proxy : incomplete — missing %s. Running without proxy.",
                        ", ".join(missing))
        return None

    proxy_url = f"http://{user}:{password}@{host}:{port}"
    log.info("Proxy : %s@%s:%s", user, host, port)
    return {"http": proxy_url, "https": proxy_url}


# ─────────────────────────────────────────────────────────────────────────────
# Session
# ─────────────────────────────────────────────────────────────────────────────

def build_session() -> requests.Session:
    s = requests.Session()
    s.verify = False

    proxies = _build_proxies()
    if proxies:
        s.proxies.update(proxies)
    else:
        log.info("Proxy : none — direct connection")

    s.headers.update({
        "Accept":           "application/json, text/plain, */*",
        "Accept-Language":  "en-US,en;q=0.9",
        "Content-Type":     "application/json",
        "Connection":       "keep-alive",
        "Origin":           "https://portal.igotkarmayogi.gov.in",
        "Referer":          "https://portal.igotkarmayogi.gov.in/",
        "Sec-Fetch-Dest":   "empty",
        "Sec-Fetch-Mode":   "cors",
        "Sec-Fetch-Site":   "same-origin",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "cstoken":          "",
        "hostPath":         "portal.igotkarmayogi.gov.in",
        "locale":           "en",
        "org":              "dopt",
        "rootOrg":          "igot",
        "sec-ch-ua":        '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "wid":              CONFIG["user_id"],
        "Cookie":           CONFIG["cookie"],
    })
    return s

# ─────────────────────────────────────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────────────────────────────────────

def _normalise(s: str) -> str:
    """Lowercase + strip for loose comparison."""
    return s.strip().lower()


def search_course(
    session: requests.Session,
    name: str,
    org: str = "",
) -> list:
    """
    POST to the search API with the course name as query.
    Returns a list of candidate dicts, each with:
        identifier, name, organisation, duration, avgRating, difficultyLevel
    """
    payload = {
        "request": {
            "filters": {
                "contentType": ["Course"],
                "courseCategory": {"!=": ["pre enrolment assessment"]},
                "status": ["Live"],
            },
            "fields": [
                "identifier", "name", "organisation", "source",
                "duration", "avgRating", "difficultyLevel",
                "primaryCategory", "language",
            ],
            "query":  name,
            "limit":  CONFIG["search_limit"],
            "offset": 0,
            "sort_by": {},
        }
    }

    # If an org is given, add it as a filter to narrow results
    if org:
        payload["request"]["filters"]["organisation"] = [org]

    try:
        r = session.post(SEARCH_URL, json=payload, timeout=20)
        r.raise_for_status()
        return r.json().get("result", {}).get("content", [])
    except Exception as e:
        log.error("  Search request failed: %s", e)
        return []


def _best_match(candidates: list, name: str, org: str) -> Optional[dict]:
    """
    Pick the best candidate from search results.
    Priority:
      1. Exact name match (case-insensitive) AND org match (if provided)
      2. Exact name match only
      3. First result (closest search-engine match)
    """
    norm_name = _normalise(name)
    norm_org  = _normalise(org)

    exact_name_org, exact_name = None, None

    for c in candidates:
        c_name = _normalise(c.get("name", ""))
        c_orgs = [_normalise(o) for o in c.get("organisation", [])]

        name_match = (c_name == norm_name)
        org_match  = any(norm_org in o or o in norm_org for o in c_orgs) if norm_org else True

        if name_match and org_match and exact_name_org is None:
            exact_name_org = c
        if name_match and exact_name is None:
            exact_name = c

    return exact_name_org or exact_name or (candidates[0] if candidates else None)


def _fmt_duration(secs) -> str:
    try:
        s = int(float(secs))
        h, m = divmod(s, 3600)
        m, s = divmod(m, 60)
        return f"{h}h {m}m" if h else f"{m}m {s}s"
    except Exception:
        return str(secs)


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes", "")
    except (EOFError, KeyboardInterrupt):
        return False


def resolve_course_id(
    session: requests.Session,
    name: str,
    org: str = "",
) -> Optional[str]:
    """
    Search for a course by name (+ optional org), pick the best match,
    optionally confirm with the user, and return its identifier.
    Returns None if nothing suitable is found or user rejects it.
    """
    org_label = f" [{org}]" if org else ""
    log.info("  🔍 Searching: \"%s\"%s", name, org_label)

    candidates = search_course(session, name, org)
    if not candidates:
        log.warning("  ✗  No results returned for this query.")
        return None

    match = _best_match(candidates, name, org)
    if not match:
        log.warning("  ✗  Could not determine a best match.")
        return None

    m_name  = match.get("name", "?")
    m_id    = match.get("identifier", "?")
    m_orgs  = ", ".join(match.get("organisation", []))
    m_dur   = _fmt_duration(match.get("duration", 0))
    m_rat   = match.get("avgRating", "—")
    m_diff  = match.get("difficultyLevel", "—")

    print()
    print(f"  ┌─ Best match ──────────────────────────────────────────")
    print(f"  │  Name       : {m_name}")
    print(f"  │  ID         : {m_id}")
    print(f"  │  Org        : {m_orgs}")
    print(f"  │  Duration   : {m_dur}   Rating: {m_rat}   Level: {m_diff}")
    print(f"  └───────────────────────────────────────────────────────")

    if CONFIG["confirm_search_results"]:
        if not _confirm("  Add this course to enrollment list? [Y/n]: "):
            log.info("  Skipped by user.")
            return None

    return m_id


# ─────────────────────────────────────────────────────────────────────────────
# Input collection
# ─────────────────────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    return s.strip().strip("\"'").rstrip("/")


def collect_inputs(session: requests.Session) -> list:
    """
    Returns a flat list of do_... course IDs, resolved from all three sources
    in priority order:
      1. CONFIG["courses_to_enroll"]  (already IDs)
      2. CONFIG["courses_to_search"]  (name+org pairs → search → ID)
      3. CLI args                     (do_... IDs or "Name | Org" strings)
      4. Interactive prompt           (do_... IDs or "Name | Org" strings)
    """
    ids = []
    seen = set()

    def _add(cid: str, label: str = ""):
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)
            log.info("  + Added %s%s", cid, f"  ({label})" if label else "")

    # ── 1: Hardcoded IDs ─────────────────────────────────────────────────────
    for cid in CONFIG.get("courses_to_enroll", []):
        cid = _clean(cid)
        if cid.startswith("do_"):
            _add(cid, "from CONFIG[courses_to_enroll]")

    # ── 2: Hardcoded name+org pairs ───────────────────────────────────────────
    for entry in CONFIG.get("courses_to_search", []):
        n = entry.get("name", "").strip()
        o = entry.get("org", "").strip()
        if n:
            cid = resolve_course_id(session, n, o)
            if cid:
                _add(cid, f"searched: {n}")
            time.sleep(random.uniform(
                CONFIG["human"]["between_search_pause_min"],
                CONFIG["human"]["between_search_pause_max"],
            ))

    # If either config source gave us IDs, stop here (don't prompt)
    if ids:
        return ids

    # ── 3: CLI args ───────────────────────────────────────────────────────────
    cli_args = [a for a in sys.argv[1:] if _clean(a)]
    if cli_args:
        log.info("Reading course inputs from command-line arguments…")
        for arg in cli_args:
            arg = _clean(arg)
            if arg.startswith("do_"):
                _add(arg, "CLI")
            elif "|" in arg:
                parts = arg.split("|", 1)
                n, o  = parts[0].strip(), parts[1].strip()
                cid   = resolve_course_id(session, n, o)
                if cid:
                    _add(cid, f"searched: {n}")
            else:
                log.warning("  Unrecognised CLI arg (not a do_... ID or Name|Org): %s", arg)
        return ids

    # ── 4: Interactive prompt ─────────────────────────────────────────────────
    print()
    print("┌─ Enter courses to enroll in ──────────────────────────────────────")
    print("│  Enter one item per line. Blank line when done.")
    print("│")
    print("│  Accepted formats:")
    print("│    do_1234567890...          ← paste a direct course ID")
    print("│    Course Name | Org Name    ← search by name + org (org optional)")
    print("│")
    print("│  Examples:")
    print("│    do_1145242891094835201284")
    print("│    Artificial Intelligence for Karmayogis | Karmayogi Bharat")
    print("│    Governing Artificial Intelligence")
    print("└───────────────────────────────────────────────────────────────────")

    while True:
        try:
            line = input("\n  → ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break

        line = _clean(line)

        # Direct ID
        if line.startswith("do_"):
            _add(line)

        # Name | Org  or just  Name
        else:
            if "|" in line:
                parts = line.split("|", 1)
                n, o  = parts[0].strip(), parts[1].strip()
            else:
                n, o = line, ""

            if not n:
                continue

            cid = resolve_course_id(session, n, o)
            if cid:
                _add(cid, f"searched: {n}")

            time.sleep(random.uniform(
                CONFIG["human"]["between_search_pause_min"],
                CONFIG["human"]["between_search_pause_max"],
            ))

    print()
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# Enrollment
# ─────────────────────────────────────────────────────────────────────────────

def enroll_in_course(session: requests.Session, course_id: str) -> bool:
    url = f"{PROTECTED_BASE}/cohorts/user/autoenrollment/{course_id}?language=english"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        content = r.json().get("result", {}).get("response", {}).get("content", [])
        if not content:
            log.warning("  ⚠  No open batches found for this course.")
            return False
        batch_id = content[0].get("batchId", "?")
        log.info("  ✓  Enrolled — batch: %s", batch_id)
        return True
    except Exception as e:
        log.error("  ✗  Enrollment request failed: %s", e)
        return False


def verify_enrollment(session: requests.Session, course_id: str):
    url = f"{PROXIES_BASE}/learner/course/v4/user/enrollment/details/{CONFIG['user_id']}"
    payload = {"request": {"retiredCoursesEnabled": True, "courseId": [course_id]}}
    try:
        r = session.post(url, json=payload, timeout=15)
        r.raise_for_status()
        courses = r.json().get("result", {}).get("courses", [])
        if courses:
            log.info("  ✓  Verified in database: %s", courses[0].get("courseName", course_id))
        else:
            log.warning("  ⚠  Could not verify — may still be syncing.")
    except Exception as e:
        log.error("  ✗  Verification failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("═" * 65)
    log.info("iGOT Bulk Auto-Enroller  v2")
    log.info("User : %s", CONFIG["user_id"])
    log.info("═" * 65)

    session = build_session()
    h       = CONFIG["human"]

    # Collect all course IDs (from config, search, CLI, or prompt)
    courses = collect_inputs(session)

    if not courses:
        log.warning("No course IDs collected. Exiting.")
        return

    log.info("")
    log.info("━" * 65)
    log.info("Enrollment queue (%d course(s)):", len(courses))
    for i, cid in enumerate(courses, 1):
        log.info("  %d. %s", i, cid)
    log.info("━" * 65)

    if CONFIG["confirm_search_results"]:
        if not _confirm("\nProceed with enrolling all of the above? [Y/n]: "):
            log.info("Aborted by user.")
            return

    results = {"ok": 0, "fail": 0}

    for i, cid in enumerate(courses, 1):
        log.info("")
        log.info("[%d/%d] %s", i, len(courses), cid)

        success = enroll_in_course(session, cid)

        if success:
            results["ok"] += 1
            time.sleep(random.uniform(h["verify_delay_min"], h["verify_delay_max"]))
            verify_enrollment(session, cid)
        else:
            results["fail"] += 1

        if i < len(courses):
            pause = random.uniform(
                h["between_enroll_pause_min"],
                h["between_enroll_pause_max"],
            )
            log.info("  (next in %.1fs…)", pause)
            time.sleep(pause)

    log.info("")
    log.info("═" * 65)
    log.info("Done — %d enrolled, %d failed.", results["ok"], results["fail"])
    log.info("═" * 65)


if __name__ == "__main__":
    main()