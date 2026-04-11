"""
iGOT Karmayogi Bulk Auto-Enroller
=================================
Automatically enrolls your account into a list of Course IDs.

HOW TO USE
----------
1. Log in to portal.igotkarmayogi.gov.in in Chrome.
2. Open DevTools → Application → Cookies → copy the full cookie string.
3. Fill in the CONFIG block below (user_id + cookie).
4. Run:  python igot_autoenroll.py

   You can supply course IDs in three ways (evaluated in this order):
     a) Hardcode them in CONFIG["courses_to_enroll"] below.
     b) Pass them as command-line arguments:
           python igot_autoenroll.py do_123 do_456 do_789
     c) Leave both empty and the script will prompt you to type / paste
        them interactively at runtime (one per line, blank line to finish).

PROXY SUPPORT (optional)
-------------------------
If you are behind an authenticated corporate/office proxy, set all four
environment variables before running:

  Windows (Command Prompt):
      set PROXY_USER=your_username
      set PROXY_PASSWORD=your_password
      set PROXY_HOST=proxy.company.com
      set PROXY_PORT=8080
      python igot_autoenroll.py

  Windows (PowerShell):
      $env:PROXY_USER="your_username"
      $env:PROXY_PASSWORD="your_password"
      $env:PROXY_HOST="proxy.company.com"
      $env:PROXY_PORT="8080"
      python igot_autoenroll.py

  Linux / macOS:
      PROXY_USER=u PROXY_PASSWORD=p PROXY_HOST=proxy.co PROXY_PORT=8080 \\
      python igot_autoenroll.py

If any of the four proxy variables is missing the script runs without a
proxy (direct connection).

⚠️  Do not log out of Chrome while the script runs — it will invalidate
    the cookie immediately.
"""

import sys
import time
import random
import logging
import requests
import urllib3
import os
from typing import Optional

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────────────────────────────────────
# ██  CONFIG  — fill in user_id and cookie; course IDs are optional here
# ─────────────────────────────────────────────────────────────────────────────

CONFIG = {
    "user_id": "--PASTE YOUR USER-ID HERE--",

    # Paste the FULL cookie string from DevTools (one long line, expires per session)
    "cookie": (
         "--PASTE YOUR COOKIE STRING HERE--"
    ),

    # ── Course IDs (optional — see HOW TO USE above for other input methods) ──
    # Add IDs here to always enroll in the same set without being prompted.
    "courses_to_enroll": [
        # "do_1143613347908812801129",   # example: Mastering Feedback
        # "do_1234567890...",
    ],

    # ── Human-behaviour tuning ────────────────────────────────────────────────
    "human": {
        # Pause between enrollments (seconds)
        "between_enroll_pause_min": 3.0,
        "between_enroll_pause_max": 7.0,

        # Pause between the enrollment call and its verification check
        "verify_delay_min": 1.0,
        "verify_delay_max": 2.5,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PROTECTED_BASE = "https://portal.igotkarmayogi.gov.in/apis/protected/v8"
PROXIES_BASE   = "https://portal.igotkarmayogi.gov.in/apis/proxies/v8"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("igot_enroller")

# ─────────────────────────────────────────────────────────────────────────────
# Proxy helper (identical pattern to igot_autocomplete.py)
# ─────────────────────────────────────────────────────────────────────────────

def _build_proxies() -> Optional[dict]:
    """
    Builds a proxy config entirely from environment variables.

    Required (if using proxy):
        PROXY_USER      your proxy username
        PROXY_PASSWORD  your proxy password
        PROXY_HOST      proxy hostname or IP  (e.g. proxy.company.com)
        PROXY_PORT      proxy port number     (e.g. 8080)

    If any of the four is missing the script runs without a proxy.
    """
    user     = os.environ.get("PROXY_USER",    "").strip()
    password = os.environ.get("PROXY_PASSWORD", "").strip()
    host     = os.environ.get("PROXY_HOST",    "").strip()
    port     = os.environ.get("PROXY_PORT",    "").strip()

    if not all([user, password, host, port]):
        missing = [k for k, v in {
            "PROXY_USER": user, "PROXY_PASSWORD": password,
            "PROXY_HOST": host, "PROXY_PORT": port,
        }.items() if not v]
        if any([user, password, host, port]):   # partial config → warn
            log.warning(
                "Proxy : incomplete config — missing %s. Running without proxy.",
                ", ".join(missing),
            )
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
        "Accept":         "application/json, text/plain, */*",
        "Content-Type":   "application/json",
        "Connection":     "keep-alive",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "wid":    CONFIG["user_id"],
        "Cookie": CONFIG["cookie"],
    })
    return s

# ─────────────────────────────────────────────────────────────────────────────
# Course ID collection (three-way priority)
# ─────────────────────────────────────────────────────────────────────────────

def _clean(cid: str) -> str:
    """Strip whitespace, trailing slashes, accidental quotes."""
    return cid.strip().strip("\"'").rstrip("/")


def collect_course_ids() -> list:
    """
    Returns the list of course IDs to enroll in, using the first source
    that yields at least one valid ID:

      1. Hardcoded CONFIG["courses_to_enroll"]
      2. Command-line arguments (python igot_autoenroll.py do_123 do_456 …)
      3. Interactive prompt (type / paste IDs one per line, blank to finish)
    """
    # 1 — CONFIG
    hardcoded = [_clean(c) for c in CONFIG.get("courses_to_enroll", []) if _clean(c)]
    if hardcoded:
        log.info("Course IDs : loaded from CONFIG (%d)", len(hardcoded))
        return hardcoded

    # 2 — CLI args
    cli_args = [_clean(a) for a in sys.argv[1:] if _clean(a).startswith("do_")]
    if cli_args:
        log.info("Course IDs : loaded from command-line arguments (%d)", len(cli_args))
        return cli_args

    # 3 — Interactive prompt
    print()
    print("┌─ Enter Course IDs to enroll in ───────────────────────────────")
    print("│  Paste one do_... ID per line.")
    print("│  Press Enter on a blank line when done.")
    print("└───────────────────────────────────────────────────────────────")
    ids = []
    while True:
        try:
            line = input("  Course ID: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        cleaned = _clean(line)
        if not cleaned.startswith("do_"):
            print(f"  ⚠  Skipping '{cleaned}' — doesn't look like a valid do_... ID.")
            continue
        if cleaned in ids:
            print(f"  ⚠  '{cleaned}' already in list — skipping duplicate.")
            continue
        ids.append(cleaned)
        print(f"  ✓  Added ({len(ids)} total)")

    print()
    return ids

# ─────────────────────────────────────────────────────────────────────────────
# Core logic
# ─────────────────────────────────────────────────────────────────────────────

def enroll_in_course(session: requests.Session, course_id: str) -> bool:
    """Hits the autoenrollment endpoint to assign the user to a batch."""
    url = f"{PROTECTED_BASE}/cohorts/user/autoenrollment/{course_id}?language=english"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        data    = r.json()
        content = data.get("result", {}).get("response", {}).get("content", [])
        if not content:
            log.warning("  ⚠  Enrollment failed: no open batches found for this course.")
            return False
        batch_id = content[0].get("batchId", "?")
        log.info("  ✓  Enrolled — batch: %s", batch_id)
        return True
    except Exception as e:
        log.error("  ✗  Enrollment request failed: %s", e)
        return False


def verify_enrollment(session: requests.Session, course_id: str):
    """Confirms the enrollment record exists in the learner database."""
    url = f"{PROXIES_BASE}/learner/course/v4/user/enrollment/details/{CONFIG['user_id']}"
    payload = {"request": {"retiredCoursesEnabled": True, "courseId": [course_id]}}
    try:
        r = session.post(url, json=payload, timeout=15)
        r.raise_for_status()
        courses = r.json().get("result", {}).get("courses", [])
        if courses:
            name = courses[0].get("courseName", course_id)
            log.info("  ✓  Verified in database: %s", name)
        else:
            log.warning("  ⚠  Could not verify enrollment — may still be syncing.")
    except Exception as e:
        log.error("  ✗  Verification failed: %s", e)

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("═" * 60)
    log.info("iGOT Bulk Auto-Enroller")
    log.info("User : %s", CONFIG["user_id"])
    log.info("═" * 60)

    session = build_session()
    h       = CONFIG["human"]
    courses = collect_course_ids()

    if not courses:
        log.warning("No course IDs provided. Exiting.")
        return

    log.info("")
    log.info("Enrolling in %d course(s)…", len(courses))

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
    log.info("═" * 60)
    log.info("Done — %d enrolled, %d failed.", results["ok"], results["fail"])
    log.info("═" * 60)


if __name__ == "__main__":
    main()