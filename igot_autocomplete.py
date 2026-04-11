"""
iGOT Karmayogi Course Auto-Completer  v3
=========================================
Handles: Videos (with heartbeat simulation), PDFs, Quizzes (auto-answered),
         and the end-of-course Feedback Survey.

HOW TO USE
----------
1. Log in to portal.igotkarmayogi.gov.in in Chrome.
2. Open DevTools → Application → Cookies → copy the full cookie string.
3. Fill in the CONFIG block below and run:  python igot_autocomplete.py

PROXY SUPPORT (optional)
-------------------------
If you are behind an authenticated corporate/office proxy, set all four
environment variables below before running. If any are missing the script
runs without a proxy (direct connection).

  Windows (Command Prompt):
      set PROXY_USER=your_username
      set PROXY_PASSWORD=your_password
      set PROXY_HOST=proxy.company.com
      set PROXY_PORT=8080
      python igot_autocomplete.py

  Windows (PowerShell):
      $env:PROXY_USER="your_username"
      $env:PROXY_PASSWORD="your_password"
      $env:PROXY_HOST="proxy.company.com"
      $env:PROXY_PORT="8080"
      python igot_autocomplete.py

  Linux / macOS:
      PROXY_USER=your_username PROXY_PASSWORD=your_password \
      PROXY_HOST=proxy.company.com PROXY_PORT=8080 \
      python igot_autocomplete.py

⚠️ Do not log out of the Chrome browser while the script is running,
or it will instantly invalidate the cookie the script is using!
"""

import time
import urllib.parse
import random
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import requests
import urllib3
import os
os.environ['no_proxy'] = '*' # REMOVE THIS IF NEEDED (PROXY BASED)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────────────────────────────────────
# ██  CONFIG  — only these need changing per session / course
# ─────────────────────────────────────────────────────────────────────────────

CONFIG = {
    # ── Identity ──────────────────────────────────────────────────────────────
    "user_id":   "--PASTE YOUR USER-ID HERE--",   # wid / userId
    "course_id": "--PASTE YOUR COURSE-ID HERE (Begins with do_)--",               # leave blank if using "all courses" mode | Example of Id: "do_114371136825573376161" 

    # Paste the FULL cookie string from DevTools (one long line, expires per session)
    "cookie": (
        "--PASTE YOUR COOKIE STRING HERE--"
    ),

    # ── Run mode ──────────────────────────────────────────────────────────────
    # "single"  → process only CONFIG["course_id"]
    # "all"     → fetch all In-Progress enrolled courses and process each
    "mode": "single",

    # Course IDs to always skip regardless of mode (POSH etc.)
    "skip_course_ids": [
        #"do_113569878939262976132",   # SCORM FORMAT COURSES
    ],

    # ── Feature flags ─────────────────────────────────────────────────────────
    "complete_videos":  True,
    "complete_pdfs":    True,
    "complete_quizzes": True,
    "submit_survey":    True,

    # ── Human-behaviour tuning ────────────────────────────────────────────────
    "human": {
        # Heartbeat interval while "watching" (~30 s like a real browser)
        "heartbeat_interval_base":   30,    # seconds
        "heartbeat_interval_jitter":  8,    # ± random seconds added each cycle

        # Pause between consecutive items within the same module
        "between_item_pause_min":  4,
        "between_item_pause_max": 15,

        # Extra pause when switching modules (simulates reading the TOC)
        "between_module_pause_min": 8,
        "between_module_pause_max": 30,

        # Extra pause between courses in "all" mode
        "between_course_pause_min": 5,
        "between_course_pause_max": 15,

        # The final "current" position sent in the completion PATCH (95–99 %)
        "completion_fraction_min": 0.95,
        "completion_fraction_max": 0.99,

        # Video watch mode — pick ONE of the three:
        #   "real_time" → actually sleeps the full video duration (safest, slow)
        #   "fast"      → periodic heartbeats with small jitter sleeps (balanced)
        #   "warp"      → two PATCHes only: started + completed, no heartbeat loop.
        #                 The reported watch-time equals the full content duration. 
        #                 ⚠️USE WARP MODE WITH CAUTION

        "watch_mode": "warp",

        # Delays used in fast mode (ignored in real_time and warp)
        "fast_mode_sleep_min": 1.5,
        "fast_mode_sleep_max": 4.0,

        # Warp mode: tiny pause between the start-PATCH and completion-PATCH
        # (simulates browser processing time between the two events)
        "warp_between_patches_min": 0.8,
        "warp_between_patches_max": 2.5,

        # Skip items the server already shows as completed (status == 2)
        "skip_if_completed": True,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PROXIES_BASE   = "https://portal.igotkarmayogi.gov.in/apis/proxies/v8"
PROTECTED_BASE = "https://portal.igotkarmayogi.gov.in/apis/protected/v8"
IST            = timezone(timedelta(hours=5, minutes=30))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("igot")

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_ist() -> str:
    now = datetime.now(IST)
    ms  = now.microsecond // 1000
    return now.strftime(f"%Y-%m-%d %H:%M:%S:{ms:03d}+0530")


def _jitter(base: float, j: float) -> float:
    return base + random.uniform(-j, j)


def _build_proxies() -> Optional[dict]:
    """
    Builds a proxy config entirely from environment variables.
    """
    user     = os.environ.get("PROXY_USER",     "").strip()
    password = os.environ.get("PROXY_PASSWORD",  "").strip()
    host     = os.environ.get("PROXY_HOST",      "").strip()
    port     = os.environ.get("PROXY_PORT",      "").strip()

    if not all([user, password, host, port]):
        missing = [k for k, v in {
            "PROXY_USER": user, "PROXY_PASSWORD": password,
            "PROXY_HOST": host, "PROXY_PORT": port,
        }.items() if not v]
        if any([user, password, host, port]):   # some but not all set → warn
            log.warning(
                "Proxy : incomplete config — missing %s. Running without proxy.",
                ", ".join(missing),
            )
        return None

    # --- THE FIX IS HERE ---
    # Safely encode special characters (like @, #, !) in the username/password
    safe_user = urllib.parse.quote_plus(user)
    safe_password = urllib.parse.quote_plus(password)

    proxy_url = f"http://{safe_user}:{safe_password}@{host}:{port}"
    # -----------------------

    # Only print the 'user' and 'host' to the logs so your password stays hidden!
    log.info("Proxy : %s@%s:%s", user, host, port)
    
    return {"http": proxy_url, "https": proxy_url}


def build_session() -> requests.Session:
    s = requests.Session()
    s.verify = False

    proxies = _build_proxies()
    if proxies:
        s.proxies.update(proxies)
    else:
        log.info("Proxy : none (PROXY_USER / PROXY_PASSWORD not set — direct connection)")

    s.headers.update({
        "Accept":             "application/json, text/plain, */*",
        "Accept-Language":    "en-US,en;q=0.9",
        "Authorization":      "",
        "Connection":         "keep-alive",
        "Content-Type":       "application/json",
        "Origin":             "https://portal.igotkarmayogi.gov.in",
        "Referer":            "https://portal.igotkarmayogi.gov.in/",
        "Sec-Fetch-Dest":     "empty",
        "Sec-Fetch-Mode":     "cors",
        "Sec-Fetch-Site":     "same-origin",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "cstoken":            "",
        "hostPath":           "portal.igotkarmayogi.gov.in",
        "locale":             "en",
        "org":                "dopt",
        "rootOrg":            "igot",
        "sec-ch-ua":          '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"Windows"',
        "wid":                CONFIG["user_id"],
        "Cookie":             CONFIG["cookie"],
    })
    return s

# ─────────────────────────────────────────────────────────────────────────────
# Enrolled courses (for "all" mode)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_enrolled_courses(session: requests.Session) -> list:
    """
    POST learner/course/v4/user/enrollment/list/{user_id}
    Returns all In-Progress enrolled courses.
    """
    url = f"{PROXIES_BASE}/learner/course/v4/user/enrollment/list/{CONFIG['user_id']}"
    try:
        r = session.post(
            url,
            json={"request": {"retiredCoursesEnabled": True, "status": "In-Progress"}},
            timeout=20,
        )
        r.raise_for_status()
        courses = r.json().get("result", {}).get("courses", [])
        log.info("Enrolled In-Progress courses: %d found", len(courses))
        return courses
    except Exception as e:
        log.error("Failed to fetch enrolled courses: %s", e)
        return []

# ─────────────────────────────────────────────────────────────────────────────
# Course structure discovery
# ─────────────────────────────────────────────────────────────────────────────

def fetch_course_structure(session: requests.Session, course_id: str) -> Optional[dict]:
    """GET extended/content/v1/read/{course_id} — full nested course tree."""
    url = f"{PROXIES_BASE}/extended/content/v1/read/{course_id}"
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        return r.json()["result"]["content"]
    except Exception as e:
        log.error("  Failed to fetch course structure: %s", e)
        return None


def extract_batch_id(course: dict) -> Optional[str]:
    """Pick the active (status=1) batch from the course metadata."""
    for b in course.get("batches", []):
        if b.get("status") == 1:
            return b["batchId"]
    batches = course.get("batches", [])
    return batches[0]["batchId"] if batches else None


def _make_item(child: dict, module_name: str, module_index: int) -> dict:
    try:
        duration = float(child["duration"]) if child.get("duration") else None
    except (ValueError, TypeError):
        duration = None

    return {
        "content_id":       child.get("identifier", ""),
        "name":             child.get("name", "Unnamed"),
        "mime_type":        child.get("mimeType", ""),
        "primary_category": child.get("primaryCategory", ""),
        "duration":         duration,
        "module_name":      module_name,
        "module_index":     module_index,
        "index":            child.get("index", 0),
        "_raw":             child,   # keep original for quiz/survey use
    }


def parse_content_tree(course: dict) -> list:
    """
    Recursively walk the children tree and return a flat ordered list.
    Mirrors exactly the order the browser processes content: module → item.
    """
    items = []

    def _walk(node: dict, mod_name: str, mod_index: int):
        pcat = node.get("primaryCategory", "")
        mime = node.get("mimeType", "")
        children = node.get("children")

        # Container node (CourseUnit) → recurse
        if pcat == "Course Unit" or mime == "application/vnd.ekstep.content-collection":
            name  = node.get("name", node.get("identifier", ""))
            idx   = node.get("index", mod_index)
            for child in (children or []):
                _walk(child, name, idx)
        else:
            # Leaf node
            items.append(_make_item(node, mod_name, mod_index))

    for top in course.get("children", []):
        _walk(top, course.get("name", "Course"), top.get("index", 0))

    items.sort(key=lambda x: (x["module_index"], x["index"]))
    return items


def classify_item(item: dict) -> str:
    mime = item["mime_type"]
    if mime == "video/mp4":
        return "video"
    if mime == "application/pdf":
        return "pdf"
    if mime == "application/vnd.sunbird.questionset":
        return "quiz"
    return "skip"

# ─────────────────────────────────────────────────────────────────────────────
# Progress
# ─────────────────────────────────────────────────────────────────────────────

def fetch_course_progress(session: requests.Session, course_id: str, batch_id: str) -> list:
    url = f"{PROXIES_BASE}/read/content-progres/{course_id}"
    payload = {
        "request": {
            "userId":     CONFIG["user_id"],
            "language":   "english",
            "batchId":    batch_id,
            "courseId":   course_id,
            "contentIds": [],
            "fields":     ["progressdetails"],
        }
    }
    try:
        r = session.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return r.json()["result"].get("contentList", [])
    except Exception as e:
        log.warning("  Progress fetch failed: %s", e)
        return []


def already_completed(progress_list: list, content_id: str) -> bool:
    return any(
        p.get("contentId") == content_id and p.get("status") == 2
        for p in progress_list
    )

# ─────────────────────────────────────────────────────────────────────────────
# Generic PATCH — used by video, PDF, and quiz completion
# ─────────────────────────────────────────────────────────────────────────────

def patch_progress(
    session:     requests.Session,
    content_id:  str,
    course_id:   str,
    batch_id:    str,
    mime_type:   str,
    duration:    float,
    current_pos: float,
    status:      int,
    label:       str = "",
) -> bool:
    pct = round(min((current_pos / duration) * 100, 100), 2) if duration else 100.0
    url = f"{PROXIES_BASE}/content-progres/{content_id}"
    payload = {
        "request": {
            "userId": CONFIG["user_id"],
            "contents": [{
                "contentId":          content_id,
                "batchId":            batch_id,
                "language":           "english",
                "status":             status,
                "courseId":           course_id,
                "lastAccessTime":     _now_ist(),
                "progressdetails": {
                    "max_size": duration or 100,
                    "current":  [str(current_pos)],
                    "mimeType": mime_type,
                },
                "completionPercentage": pct,
            }],
        }
    }
    try:
        r = session.patch(url, json=payload, timeout=15)
        r.raise_for_status()
        result = r.json()
        # Both "SUCCESS" result key and params.status == "success" are used across endpoints
        ok = (
            result.get("result", {}).get(content_id) == "SUCCESS"
            or result.get("params", {}).get("status") == "success"
        )
        tag = label or ("HEARTBEAT" if status == 1 else "COMPLETE ")
        log.info("      [%s] pos=%.1f  pct=%.1f%%  %s", tag, current_pos, pct, "✓" if ok else "✗")
        return ok
    except Exception as e:
        log.warning("      PATCH failed: %s", e)
        return False

# ─────────────────────────────────────────────────────────────────────────────
# VIDEO — heartbeat simulation
# ─────────────────────────────────────────────────────────────────────────────

def fetch_content_metadata(session: requests.Session, content_id: str) -> Optional[dict]:
    """Registers the 'opened' event with the server."""
    url = f"{PROXIES_BASE}/content/v2/read/{content_id}"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        meta = r.json()["result"]["content"]
        log.info("    Metadata → %s", meta.get("name", content_id))
        return meta
    except Exception as e:
        log.warning("    Metadata fetch failed: %s", e)
        return None


def simulate_video_watch(
    session:    requests.Session,
    item:       dict,
    course_id:  str,
    batch_id:   str,
):
    h        = CONFIG["human"]
    cid      = item["content_id"]
    duration = item["duration"]
    mode     = h.get("watch_mode", "fast")

    fetch_content_metadata(session, cid)
    time.sleep(random.uniform(1.5, 3.5))

    frac      = random.uniform(h["completion_fraction_min"], h["completion_fraction_max"])
    final_pos = round(duration * frac, 6)

    # ── WARP MODE ─────────────────────────────────────────────────────────────
    # Two PATCHes only: one marking the video as started (a few seconds in),
    # one marking it complete. The "current" position in the completion PATCH
    # equals the full duration so the server records 100 % watch time.
    if mode == "warp":
        log.info("    ▶ Watching  (%.0fs, warp mode)", duration)

        # PATCH 1 — started (a few seconds in, looks like the player buffered)
        start_pos = round(random.uniform(1.5, 5.0), 6)
        patch_progress(session, cid, course_id, batch_id,
                       "video/mp4", duration, start_pos, status=1)

        # Tiny pause between the two events
        time.sleep(random.uniform(
            h.get("warp_between_patches_min", 0.8),
            h.get("warp_between_patches_max", 2.5),
        ))

        # PATCH 2 — completed (position = full duration, pct = 100)
        patch_progress(session, cid, course_id, batch_id,
                       "video/mp4", duration, final_pos, status=2)

    # ── REAL-TIME MODE ────────────────────────────────────────────────────────
    elif mode == "real_time":
        log.info("    ▶ Watching  (%.0fs, real-time mode)", duration)
        pos = 0.0
        while pos < duration * 0.90:
            step = _jitter(h["heartbeat_interval_base"], h["heartbeat_interval_jitter"])
            pos  = min(pos + step, duration * 0.93)
            time.sleep(step + random.uniform(0, 0.5))
            patch_progress(session, cid, course_id, batch_id,
                           "video/mp4", duration, round(pos, 6), status=1)
        time.sleep(max(0, duration - pos) + random.uniform(0, 3))
        patch_progress(session, cid, course_id, batch_id,
                       "video/mp4", duration, final_pos, status=2)

    # ── FAST MODE (default) ───────────────────────────────────────────────────
    else:
        log.info("    ▶ Watching  (%.0fs, fast mode)", duration)
        pos = 0.0
        while pos < duration * 0.90:
            step = _jitter(h["heartbeat_interval_base"], h["heartbeat_interval_jitter"])
            pos  = min(pos + step, duration * 0.93)
            time.sleep(random.uniform(h["fast_mode_sleep_min"], h["fast_mode_sleep_max"]))
            patch_progress(session, cid, course_id, batch_id,
                           "video/mp4", duration, round(pos, 6), status=1)
        time.sleep(random.uniform(h["fast_mode_sleep_min"], h["fast_mode_sleep_max"]))
        patch_progress(session, cid, course_id, batch_id,
                       "video/mp4", duration, final_pos, status=2)

# ─────────────────────────────────────────────────────────────────────────────
# PDF — single completion PATCH (no heartbeats needed)
# ─────────────────────────────────────────────────────────────────────────────

def complete_pdf(
    session:   requests.Session,
    item:      dict,
    course_id: str,
    batch_id:  str,
):
    """
    PDFs are marked complete with a single PATCH.
    The server only needs status=2; no heartbeat loop is required.
    """
    cid      = item["content_id"]
    duration = item["duration"] or 100.0

    log.info("    📄 PDF — marking complete")
    time.sleep(random.uniform(2, 5))   # brief pause: human "opens" the PDF

    patch_progress(
        session, cid, course_id, batch_id,
        "application/pdf", duration, duration, status=2,
        label="PDF DONE",
    )

# ─────────────────────────────────────────────────────────────────────────────
# QUIZ — fetch questions, extract correct answers, submit
# ─────────────────────────────────────────────────────────────────────────────

def _get_correct_answer_index(question: dict) -> str:
    """
    The correct option has answer == True in editorState.options.
    Returns the option value (as string) or "0" as fallback.
    """
    for opt in question.get("editorState", {}).get("options", []):
        if opt.get("answer") is True:
            val = opt.get("value", {})
            # value can be a dict {"value": N} or a plain int/str
            if isinstance(val, dict):
                return str(val.get("value", 0))
            return str(val)
    return "0"


def submit_quiz(
    session:   requests.Session,
    item:      dict,
    course_id: str,
    batch_id:  str,
) -> bool:
    quiz_id = item["content_id"]
    raw     = item["_raw"]

    log.info("    🧩 Fetching quiz structure…")

    # Step A — read quiz/assessment metadata to get section + question IDs
    res = session.get(
        f"{PROXIES_BASE}/assessment/read/{quiz_id}?parentContextId={course_id}",
        timeout=15,
    )
    if res.status_code != 200:
        log.warning("      Could not read assessment (%d). Skipping.", res.status_code)
        return False

    adata    = res.json().get("result", {}).get("questionSet", {})
    sections = adata.get("children", [])
    if not sections:
        log.warning("      No sections found in quiz. Skipping.")
        return False

    section = sections[0]
    qids    = section.get("childNodes", [])
    if not qids:
        log.warning("      No question IDs in section. Skipping.")
        return False

    log.info("      %d question(s) found. Fetching with answers…", len(qids))

    # Step B — fetch questions (answers are embedded in the response)
    rr = session.post(
        f"{PROXIES_BASE}/question/read",
        json={"assessmentId": quiz_id, "request": {"search": {"identifier": qids}}},
        timeout=15,
    )
    if rr.status_code != 200:
        log.warning("      Could not fetch questions (%d). Skipping.", rr.status_code)
        return False

    questions = rr.json().get("result", {}).get("questions", [])
    if not questions:
        log.warning("      Empty question list returned. Skipping.")
        return False

    # Step C — build answer payload (pick correct option for each question)
    answers = []
    for q in questions:
        correct_idx = _get_correct_answer_index(q)
        time_taken  = str(random.randint(12000, 40000))   # ms, looks human
        answers.append({
            "identifier":       q.get("identifier"),
            "mimeType":         q.get("mimeType", "application/vnd.sunbird.question"),
            "objectType":       "Question",
            "question":         q.get("name", "Q"),
            "primaryCategory":  q.get("primaryCategory", "Single Choice Question"),
            "qType":            q.get("qType", "MCQ-SCA"),
            "questionLevel":    "",
            "timeTaken":        time_taken,
            "timeSpent":        time_taken,
            "editorState": {
                "options": [{"index": correct_idx, "selectedAnswer": True}]
            },
        })

    log.info("      Submitting %d answer(s)…", len(answers))
    time.sleep(random.uniform(3, 8))   # simulate reading + answering time

    # Step D — submit to the protected assessment endpoint
    sub = session.post(
        f"{PROTECTED_BASE}/user/evaluate/assessment/submit/v4",
        json={
            "language":      "english",
            "batchId":       batch_id,
            "identifier":    quiz_id,
            "primaryCategory": raw.get("primaryCategory", "Practice Question Set"),
            "courseId":      course_id,
            "isAssessment":  True,
            "objectType":    "QuestionSet",
            "timeLimit":     raw.get("expectedDuration", 300),
            "children": [{
                "identifier":      section.get("identifier"),
                "objectType":      "QuestionSet",
                "primaryCategory": section.get("primaryCategory", "Practice Question Set"),
                "scoreCutoffType": "AssessmentLevel",
                "children":        answers,
            }],
        },
        timeout=20,
    )

    ok = sub.status_code == 200
    log.info("      Quiz submission → %s", "✓ 100%!" if ok else f"✗ HTTP {sub.status_code}")
    return ok

# ─────────────────────────────────────────────────────────────────────────────
# SURVEY — end-of-course feedback form
# ─────────────────────────────────────────────────────────────────────────────

def submit_survey(session: requests.Session, course: dict) -> bool:
    """
    Reads the completionSurveyLink from the course metadata, fetches form fields,
    and auto-submits with positive responses (5-star / "Excellent" equivalents).
    """
    survey_link = course.get("completionSurveyLink", "")
    if not survey_link:
        log.info("  No survey link found for this course — skipping.")
        return True

    form_id = survey_link.rstrip("/").split("/")[-1]
    log.info("  📋 Survey form ID: %s", form_id)

    # Fetch form definition
    res = session.get(
        f"{PROXIES_BASE}/forms/v2/getFormById?formId={form_id}",
        timeout=15,
    )
    if res.status_code != 200:
        log.warning("  Could not fetch survey form (%d).", res.status_code)
        return False

    fields = res.json().get("result", {}).get("response", {}).get("fields", [])
    if not fields:
        log.warning("  Survey form has no fields.")
        return False

    # Build responses — pick the best available answer per field type
    responses = []
    for field in fields:
        ft = field.get("fieldType", "")
        if ft in ("heading", "separator"):
            continue    # layout-only elements, no answer needed

        if ft == "radio":
            answer = "5"            # highest rating
        elif ft == "text":
            answer = "Excellent course, highly recommended."
        elif ft == "checkbox":
            answer = "Yes"
        else:
            answer = "5"

        responses.append({
            "questionId": field.get("id"),
            "question":   field.get("name"),
            "answer":     answer,
            "answerType": ft,
        })

    log.info("  Submitting survey with %d response(s)…", len(responses))
    time.sleep(random.uniform(3, 7))   # simulate filling it out

    sr = session.post(
        f"{PROXIES_BASE}/forms/v2/saveFormSubmit",
        json={
            "formId":      form_id,
            "version":     4,
            "status":      "SUBMITTED",
            "responses":   responses,
            "contextType": "completionSurvey",
            "contextId":   course.get("identifier"),
            "contextName": course.get("name"),
            "contextOrgId": course.get("channel"),
        },
        timeout=15,
    )
    ok = sr.status_code == 200
    log.info("  Survey → %s", "✓ submitted" if ok else f"✗ HTTP {sr.status_code}")
    return ok

# ─────────────────────────────────────────────────────────────────────────────
# Course processor
# ─────────────────────────────────────────────────────────────────────────────

def process_course(session: requests.Session, course_id: str):
    if course_id in CONFIG.get("skip_course_ids", []):
        log.info("  ⏭  Course is in skip list — skipping.")
        return

    # Fetch full course tree (includes batchId, children, survey link)
    course = fetch_course_structure(session, course_id)
    if not course:
        return

    course_name = course.get("name", course_id)
    batch_id    = extract_batch_id(course)
    if not batch_id:
        log.warning("  No batch ID found — cannot submit progress. Skipping.")
        return

    log.info("  Batch ID : %s", batch_id)

    # Parse all leaf items
    items = parse_content_tree(course)
    n = {k: sum(1 for i in items if classify_item(i) == k)
         for k in ("video", "pdf", "quiz", "skip")}
    log.info("  Content  : %d video(s), %d PDF(s), %d quiz/assessment(s), %d other",
             n["video"], n["pdf"], n["quiz"], n["skip"])

    # Print execution plan
    log.info("")
    log.info("  ── Execution plan ───────────────────────────────────────")
    cur_mod = None
    for item in items:
        if item["module_name"] != cur_mod:
            cur_mod = item["module_name"]
            log.info("  [Module] %s", cur_mod)
        dur_str = f"{item['duration']:.0f}s" if item["duration"] else "—"
        log.info("    %-46s %-5s %s",
                 item["name"][:46], classify_item(item), dur_str)
    log.info("  ─────────────────────────────────────────────────────────")
    log.info("")

    # Fetch current progress once up-front
    progress = fetch_course_progress(session, course_id, batch_id)

    h           = CONFIG["human"]
    current_mod = None
    total       = len(items)

    for idx, item in enumerate(items, 1):
        kind = classify_item(item)
        cid  = item["content_id"]
        name = item["name"]
        mod  = item["module_name"]

        # Module boundary
        if mod != current_mod:
            current_mod = mod
            log.info("")
            log.info("  ┌─ Module: %s", mod)
            if idx > 1:
                pause = random.uniform(
                    h["between_module_pause_min"],
                    h["between_module_pause_max"],
                )
                log.info("  │  (pausing %.0fs between modules)", pause)
                time.sleep(pause)

        log.info("  │  [%d/%d] %s  (%s)", idx, total, name, kind)

        # ── VIDEO ────────────────────────────────────────────────────────────
        if kind == "video" and CONFIG["complete_videos"]:
            if h["skip_if_completed"] and already_completed(progress, cid):
                log.info("  │    Already completed — skipping ✓")
            elif not item["duration"] or item["duration"] <= 0:
                log.warning("  │    No valid duration — skipping")
            else:
                simulate_video_watch(session, item, course_id, batch_id)

        # ── PDF ──────────────────────────────────────────────────────────────
        elif kind == "pdf" and CONFIG["complete_pdfs"]:
            if h["skip_if_completed"] and already_completed(progress, cid):
                log.info("  │    Already completed — skipping ✓")
            else:
                complete_pdf(session, item, course_id, batch_id)

        # ── QUIZ ─────────────────────────────────────────────────────────────
        elif kind == "quiz" and CONFIG["complete_quizzes"]:
            if h["skip_if_completed"] and already_completed(progress, cid):
                log.info("  │    Already completed — skipping ✓")
            else:
                submit_quiz(session, item, course_id, batch_id)

        # ── OTHER ─────────────────────────────────────────────────────────────
        elif kind == "skip":
            log.info("  │    Skipping unsupported type: %s", item["mime_type"])

        # Inter-item pause
        if idx < total:
            pause = random.uniform(
                h["between_item_pause_min"],
                h["between_item_pause_max"],
            )
            log.info("  │    (next in %.0fs…)", pause)
            time.sleep(pause)

    # Survey
    if CONFIG["submit_survey"]:
        log.info("")
        submit_survey(session, course)

    # Final progress check
    log.info("")
    log.info("  Final progress check…")
    prog = fetch_course_progress(session, course_id, batch_id)
    done = sum(1 for p in prog if p.get("status") == 2)
    log.info("  %d/%d items marked complete on server", done, len(prog))

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("═" * 65)
    log.info("iGOT Karmayogi Auto-Completer  v3")
    log.info("User   : %s", CONFIG["user_id"])
    log.info("Mode   : %s", CONFIG["mode"])
    log.info("═" * 65)

    session = build_session()
    h = CONFIG["human"]

    if CONFIG["mode"] == "all":
        enrolled = fetch_enrolled_courses(session)
        if not enrolled:
            log.error("No In-Progress courses found. Aborting.")
            return

        for i, enroll in enumerate(enrolled, 1):
            cid  = enroll.get("courseId", "")
            name = enroll.get("content", {}).get("name", cid)
            
            # --- Extract completion percentage ---
            completion_pct = enroll.get("completionPercentage", 0)
            
            log.info("")
            log.info("━" * 65)
            log.info("[%d/%d] Course: %s (Progress: %s%%)", i, len(enrolled), name, completion_pct)
            log.info("ID: %s", cid)
            log.info("━" * 65)
            
            # --- Hard skip if the course is already done ---
            if completion_pct == 100:
                log.info("  ✅ Course is already 100%% complete. Skipping entirely.")
                continue

            # Process the course
            process_course(session, cid)

            # Pause before the next course (if not the last one)
            if i < len(enrolled):
                pause = random.uniform(
                    h["between_course_pause_min"],
                    h["between_course_pause_max"],
                )
                log.info("\n(pausing %.0fs before next course…)", pause)
                time.sleep(pause)

    else:  # "single"
        cid = CONFIG["course_id"]
        if not cid:
            log.error("No course_id set in CONFIG. Aborting.")
            return
        log.info("Course : %s", cid)
        process_course(session, cid)

    log.info("")
    log.info("═" * 65)
    log.info("🎉 All done! Have a great day!")
    log.info("═" * 65)


if __name__ == "__main__":
    main()