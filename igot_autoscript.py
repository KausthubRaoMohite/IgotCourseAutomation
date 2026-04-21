"""
iGOT Karmayogi Toolkit  v5
==========================
Handles: Auto-Enrollment, Videos (heartbeat / fast / warp), PDFs, Quizzes, and Feedback Surveys.

HOW TO USE
----------
Just run:   python igot_toolkit.py

The script walks you through everything interactively:
  • Credentials  — User ID and Cookie (saved locally so you only paste once)
  • Run mode     — Single completion, All In-Progress completion, or New Enrollment
  • Settings     — review and tweak every option before starting
  • Mid-run menu — pause between courses to change settings or stop

CREDENTIALS FILE
----------------
Credentials are saved to  .igot_session.json  in the same folder.
Delete that file to force a fresh login prompt.

PROXY SUPPORT (optional)
-------------------------
Set env vars before running — no code changes needed:

  Windows CMD:       set PROXY_USER=u & set PROXY_PASSWORD=p &
                     set PROXY_HOST=proxy.co & set PROXY_PORT=8080
  PowerShell:        $env:PROXY_USER="u"; $env:PROXY_PASSWORD="p"
                     $env:PROXY_HOST="proxy.co"; $env:PROXY_PORT="8080"
  Linux / macOS:     PROXY_USER=u PROXY_PASSWORD=p PROXY_HOST=h PROXY_PORT=8080

If any of the four is missing the script connects directly.

⚠️  Do not log out of Chrome while running — it invalidates the cookie.
"""

import time
import json
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
# ██  DEFAULTS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULTS = {
    "mode":             "single",    # "single" | "all" | "enroll"
    "complete_videos":  True,
    "complete_pdfs":    True,
    "complete_quizzes": True,
    "submit_survey":    True,
    "skip_course_ids":  [],
    "confirm_search_results": True,
    "search_limit": 10,
    "human": {
        "heartbeat_interval_base":   30,
        "heartbeat_interval_jitter":  8,
        "between_item_pause_min":     4,
        "between_item_pause_max":    15,
        "between_module_pause_min":   8,
        "between_module_pause_max":  30,
        "between_course_pause_min":   5,
        "between_course_pause_max":  15,
        "completion_fraction_min":   0.95,
        "completion_fraction_max":   0.99,
        "watch_mode":                "warp",   # "warp" | "fast" | "real_time"
        "fast_mode_sleep_min":        1.5,
        "fast_mode_sleep_max":        4.0,
        "warp_between_patches_min":   0.8,
        "warp_between_patches_max":   2.5,
        "skip_if_completed":         True,
        "between_enroll_pause_min":   3.0,
        "between_enroll_pause_max":   7.0,
        "verify_delay_min":           1.0,
        "verify_delay_max":           2.5,
        "between_search_pause_min":   1.0,
        "between_search_pause_max":   3.0,
    },
}

CONFIG: dict = {}

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PROXIES_BASE   = "https://portal.igotkarmayogi.gov.in/apis/proxies/v8"
PROTECTED_BASE = "https://portal.igotkarmayogi.gov.in/apis/protected/v8"
SEARCH_URL     = f"{PROXIES_BASE}/sunbirdigot/v4/search"
IST            = timezone(timedelta(hours=5, minutes=30))
_W             = 65   # banner width

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
    user     = os.environ.get("PROXY_USER",     "").strip()
    password = os.environ.get("PROXY_PASSWORD",  "").strip()
    host     = os.environ.get("PROXY_HOST",      "").strip()
    port     = os.environ.get("PROXY_PORT",      "").strip()

    if not all([user, password, host, port]):
        missing = [k for k, v in {
            "PROXY_USER": user, "PROXY_PASSWORD": password,
            "PROXY_HOST": host, "PROXY_PORT": port,
        }.items() if not v]
        if any([user, password, host, port]):
            log.warning("Proxy : incomplete config — missing %s. Running without proxy.", ", ".join(missing))
        return None

    safe_user = urllib.parse.quote_plus(user)
    safe_password = urllib.parse.quote_plus(password)
    proxy_url = f"http://{safe_user}:{safe_password}@{host}:{port}"

    log.info("Proxy : %s@%s:%s", user, host, port)
    return {"http": proxy_url, "https": proxy_url}


def build_session() -> requests.Session:
    s = requests.Session()
    s.verify = False

    proxies = _build_proxies()
    if proxies:
        s.proxies.update(proxies)
    else:
        log.info("Proxy : none (direct connection)")

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
# Enrolled courses & Autocomplete Logic
# ─────────────────────────────────────────────────────────────────────────────

def fetch_enrolled_courses(session: requests.Session) -> list:
    url = f"{PROXIES_BASE}/learner/course/v4/user/enrollment/list/{CONFIG['user_id']}"
    try:
        r = session.post(url, json={"request": {"retiredCoursesEnabled": True, "status": "In-Progress"}}, timeout=20)
        r.raise_for_status()
        courses = r.json().get("result", {}).get("courses", [])
        log.info("Enrolled In-Progress courses: %d found", len(courses))
        return courses
    except Exception as e:
        log.error("Failed to fetch enrolled courses: %s", e)
        return []

def fetch_course_structure(session: requests.Session, course_id: str) -> Optional[dict]:
    url = f"{PROXIES_BASE}/extended/content/v1/read/{course_id}"
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        return r.json()["result"]["content"]
    except Exception as e:
        log.error("  Failed to fetch course structure: %s", e)
        return None

def extract_batch_id(course: dict) -> Optional[str]:
    for b in course.get("batches", []):
        if b.get("status") == 1: return b["batchId"]
    batches = course.get("batches", [])
    return batches[0]["batchId"] if batches else None

def _make_item(child: dict, module_name: str, module_index: int) -> dict:
    try: duration = float(child["duration"]) if child.get("duration") else None
    except (ValueError, TypeError): duration = None
    return {
        "content_id":       child.get("identifier", ""),
        "name":             child.get("name", "Unnamed"),
        "mime_type":        child.get("mimeType", ""),
        "primary_category": child.get("primaryCategory", ""),
        "duration":         duration,
        "module_name":      module_name,
        "module_index":     module_index,
        "index":            child.get("index", 0),
        "_raw":             child,
    }

def parse_content_tree(course: dict) -> list:
    items = []
    def _walk(node: dict, mod_name: str, mod_index: int):
        pcat, mime, children = node.get("primaryCategory", ""), node.get("mimeType", ""), node.get("children")
        if pcat == "Course Unit" or mime == "application/vnd.ekstep.content-collection":
            name, idx = node.get("name", node.get("identifier", "")), node.get("index", mod_index)
            for child in (children or []): _walk(child, name, idx)
        else:
            items.append(_make_item(node, mod_name, mod_index))
    for top in course.get("children", []): _walk(top, course.get("name", "Course"), top.get("index", 0))
    items.sort(key=lambda x: (x["module_index"], x["index"]))
    return items

def classify_item(item: dict) -> str:
    mime = item["mime_type"]
    if mime == "video/mp4": return "video"
    if mime == "application/pdf": return "pdf"
    if mime == "application/vnd.sunbird.questionset": return "quiz"
    return "skip"

def fetch_course_progress(session: requests.Session, course_id: str, batch_id: str) -> list:
    url = f"{PROXIES_BASE}/read/content-progres/{course_id}"
    payload = {
        "request": {
            "userId": CONFIG["user_id"], "language": "english", "batchId": batch_id,
            "courseId": course_id, "contentIds": [], "fields": ["progressdetails"],
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
    return any(p.get("contentId") == content_id and p.get("status") == 2 for p in progress_list)

def patch_progress(session: requests.Session, content_id: str, course_id: str, batch_id: str, mime_type: str, duration: float, current_pos: float, status: int, label: str = "") -> bool:
    pct = round(min((current_pos / duration) * 100, 100), 2) if duration else 100.0
    url = f"{PROXIES_BASE}/content-progres/{content_id}"
    payload = {
        "request": {
            "userId": CONFIG["user_id"],
            "contents": [{
                "contentId": content_id, "batchId": batch_id, "language": "english", "status": status,
                "courseId": course_id, "lastAccessTime": _now_ist(),
                "progressdetails": {"max_size": duration or 100, "current": [str(current_pos)], "mimeType": mime_type},
                "completionPercentage": pct,
            }],
        }
    }
    try:
        r = session.patch(url, json=payload, timeout=15)
        r.raise_for_status()
        result = r.json()
        ok = (result.get("result", {}).get(content_id) == "SUCCESS" or result.get("params", {}).get("status") == "success")
        log.info("      [%s] pos=%.1f  pct=%.1f%%  %s", label or ("HEARTBEAT" if status == 1 else "COMPLETE "), current_pos, pct, "✓" if ok else "✗")
        return ok
    except Exception as e:
        log.warning("      PATCH failed: %s", e)
        return False

def fetch_content_metadata(session: requests.Session, content_id: str) -> Optional[dict]:
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

def simulate_video_watch(session: requests.Session, item: dict, course_id: str, batch_id: str):
    h, cid, duration, mode = CONFIG["human"], item["content_id"], item["duration"], CONFIG["human"].get("watch_mode", "fast")
    fetch_content_metadata(session, cid)
    time.sleep(random.uniform(1.5, 3.5))
    final_pos = round(duration * random.uniform(h["completion_fraction_min"], h["completion_fraction_max"]), 6)

    if mode == "warp":
        log.info("    ▶ Watching  (%.0fs, warp mode)", duration)
        patch_progress(session, cid, course_id, batch_id, "video/mp4", duration, round(random.uniform(1.5, 5.0), 6), status=1)
        time.sleep(random.uniform(h.get("warp_between_patches_min", 0.8), h.get("warp_between_patches_max", 2.5)))
        patch_progress(session, cid, course_id, batch_id, "video/mp4", duration, final_pos, status=2)
    elif mode == "real_time":
        log.info("    ▶ Watching  (%.0fs, real-time mode)", duration)
        pos = 0.0
        while pos < duration * 0.90:
            step = _jitter(h["heartbeat_interval_base"], h["heartbeat_interval_jitter"])
            pos  = min(pos + step, duration * 0.93)
            time.sleep(step + random.uniform(0, 0.5))
            patch_progress(session, cid, course_id, batch_id, "video/mp4", duration, round(pos, 6), status=1)
        time.sleep(max(0, duration - pos) + random.uniform(0, 3))
        patch_progress(session, cid, course_id, batch_id, "video/mp4", duration, final_pos, status=2)
    else:
        log.info("    ▶ Watching  (%.0fs, fast mode)", duration)
        pos = 0.0
        while pos < duration * 0.90:
            step = _jitter(h["heartbeat_interval_base"], h["heartbeat_interval_jitter"])
            pos  = min(pos + step, duration * 0.93)
            time.sleep(random.uniform(h["fast_mode_sleep_min"], h["fast_mode_sleep_max"]))
            patch_progress(session, cid, course_id, batch_id, "video/mp4", duration, round(pos, 6), status=1)
        time.sleep(random.uniform(h["fast_mode_sleep_min"], h["fast_mode_sleep_max"]))
        patch_progress(session, cid, course_id, batch_id, "video/mp4", duration, final_pos, status=2)

def complete_pdf(session: requests.Session, item: dict, course_id: str, batch_id: str):
    cid, duration = item["content_id"], item["duration"] or 100.0
    log.info("    📄 PDF — marking complete")
    time.sleep(random.uniform(2, 5))
    patch_progress(session, cid, course_id, batch_id, "application/pdf", duration, duration, status=2, label="PDF DONE")

def _get_correct_answer_index(question: dict) -> str:
    for opt in question.get("editorState", {}).get("options", []):
        if opt.get("answer") is True:
            val = opt.get("value", {})
            return str(val.get("value", 0)) if isinstance(val, dict) else str(val)
    return "0"

def submit_quiz(session: requests.Session, item: dict, course_id: str, batch_id: str) -> bool:
    quiz_id, raw = item["content_id"], item["_raw"]
    log.info("    🧩 Fetching quiz structure…")

    res = session.get(f"{PROXIES_BASE}/assessment/read/{quiz_id}?parentContextId={course_id}", timeout=15)
    if res.status_code != 200: return False
    sections = res.json().get("result", {}).get("questionSet", {}).get("children", [])
    if not sections: return False
    section = sections[0]
    qids = section.get("childNodes", [])
    if not qids: return False

    log.info("      %d question(s) found. Fetching with answers…", len(qids))
    rr = session.post(f"{PROXIES_BASE}/question/read", json={"assessmentId": quiz_id, "request": {"search": {"identifier": qids}}}, timeout=15)
    if rr.status_code != 200: return False
    questions = rr.json().get("result", {}).get("questions", [])
    if not questions: return False

    answers = []
    for q in questions:
        correct_idx, time_taken = _get_correct_answer_index(q), str(random.randint(12000, 40000))
        answers.append({
            "identifier": q.get("identifier"), "mimeType": q.get("mimeType", "application/vnd.sunbird.question"),
            "objectType": "Question", "question": q.get("name", "Q"),
            "primaryCategory": q.get("primaryCategory", "Single Choice Question"), "qType": q.get("qType", "MCQ-SCA"),
            "questionLevel": "", "timeTaken": time_taken, "timeSpent": time_taken,
            "editorState": {"options": [{"index": correct_idx, "selectedAnswer": True}]},
        })

    log.info("      Submitting %d answer(s)…", len(answers))
    time.sleep(random.uniform(3, 8))
    sub = session.post(
        f"{PROTECTED_BASE}/user/evaluate/assessment/submit/v4",
        json={
            "language": "english", "batchId": batch_id, "identifier": quiz_id, "primaryCategory": raw.get("primaryCategory", "Practice Question Set"),
            "courseId": course_id, "isAssessment": True, "objectType": "QuestionSet", "timeLimit": raw.get("expectedDuration", 300),
            "children": [{"identifier": section.get("identifier"), "objectType": "QuestionSet", "primaryCategory": section.get("primaryCategory", "Practice Question Set"), "scoreCutoffType": "AssessmentLevel", "children": answers}],
        }, timeout=20,
    )
    ok = sub.status_code == 200
    log.info("      Quiz submission → %s", "✓ 100%!" if ok else f"✗ HTTP {sub.status_code}")
    return ok

def submit_survey(session: requests.Session, course: dict) -> bool:
    survey_link = course.get("completionSurveyLink", "")
    if not survey_link: return True
    form_id = survey_link.rstrip("/").split("/")[-1]
    log.info("  📋 Survey form ID: %s", form_id)

    res = session.get(f"{PROXIES_BASE}/forms/v2/getFormById?formId={form_id}", timeout=15)
    if res.status_code != 200: return False
    fields = res.json().get("result", {}).get("response", {}).get("fields", [])
    if not fields: return False

    responses = []
    for field in fields:
        ft = field.get("fieldType", "")
        if ft in ("heading", "separator"): continue
        answer = "5" if ft in ("radio", "star") else "Amazing course, highly recommended. Learnt a lot!" if ft == "text" else "Yes" if ft == "checkbox" else "5"
        responses.append({"questionId": field.get("id"), "question": field.get("name"), "answer": answer, "answerType": ft})

    log.info("  Submitting survey with %d response(s)…", len(responses))
    time.sleep(random.uniform(3, 7))
    sr = session.post(
        f"{PROXIES_BASE}/forms/v2/saveFormSubmit",
        json={"formId": form_id, "version": 4, "status": "SUBMITTED", "responses": responses, "contextType": "completionSurvey", "contextId": course.get("identifier"), "contextName": course.get("name"), "contextOrgId": course.get("channel")},
        timeout=15,
    )
    ok = sr.status_code == 200
    log.info("  Survey → %s", "✓ submitted" if ok else f"✗ HTTP {sr.status_code}")
    return ok

def process_course(session: requests.Session, course_id: str):
    if course_id in CONFIG.get("skip_course_ids", []):
        log.info("  ⏭  Course is in skip list — skipping.")
        return

    course = fetch_course_structure(session, course_id)
    if not course: return

    batch_id = extract_batch_id(course)
    if not batch_id:
        log.warning("  No batch ID found — cannot submit progress. Skipping.")
        return

    log.info("  Batch ID : %s", batch_id)
    items = parse_content_tree(course)
    n = {k: sum(1 for i in items if classify_item(i) == k) for k in ("video", "pdf", "quiz", "skip")}
    log.info("  Content  : %d video(s), %d PDF(s), %d quiz/assessment(s), %d other", n["video"], n["pdf"], n["quiz"], n["skip"])

    log.info("\n  ── Execution plan ───────────────────────────────────────")
    cur_mod = None
    for item in items:
        if item["module_name"] != cur_mod:
            cur_mod = item["module_name"]
            log.info("  [Module] %s", cur_mod)
        dur_str = f"{item['duration']:.0f}s" if item["duration"] else "—"
        log.info("    %-46s %-5s %s", item["name"][:46], classify_item(item), dur_str)
    log.info("  ─────────────────────────────────────────────────────────\n")

    progress = fetch_course_progress(session, course_id, batch_id)
    h, current_mod, total = CONFIG["human"], None, len(items)

    for idx, item in enumerate(items, 1):
        kind, cid, name, mod = classify_item(item), item["content_id"], item["name"], item["module_name"]
        if mod != current_mod:
            current_mod = mod
            log.info("\n  ┌─ Module: %s", mod)
            if idx > 1:
                pause = random.uniform(h["between_module_pause_min"], h["between_module_pause_max"])
                log.info("  │  (pausing %.0fs between modules)", pause)
                time.sleep(pause)

        log.info("  │  [%d/%d] %s  (%s)", idx, total, name, kind)

        if kind == "video" and CONFIG["complete_videos"]:
            if h["skip_if_completed"] and already_completed(progress, cid): log.info("  │    Already completed — skipping ✓")
            elif not item["duration"] or item["duration"] <= 0: log.warning("  │    No valid duration — skipping")
            else: simulate_video_watch(session, item, course_id, batch_id)
        elif kind == "pdf" and CONFIG["complete_pdfs"]:
            if h["skip_if_completed"] and already_completed(progress, cid): log.info("  │    Already completed — skipping ✓")
            else: complete_pdf(session, item, course_id, batch_id)
        elif kind == "quiz" and CONFIG["complete_quizzes"]:
            if h["skip_if_completed"] and already_completed(progress, cid): log.info("  │    Already completed — skipping ✓")
            else: submit_quiz(session, item, course_id, batch_id)
        elif kind == "skip":
            log.info("  │    Skipping unsupported type: %s", item["mime_type"])

        if idx < total:
            pause = random.uniform(h["between_item_pause_min"], h["between_item_pause_max"])
            log.info("  │    (next in %.0fs…)", pause)
            time.sleep(pause)

    if CONFIG["submit_survey"]:
        log.info("")
        submit_survey(session, course)

    log.info("\n  Final progress check…")
    prog = fetch_course_progress(session, course_id, batch_id)
    log.info("  %d/%d items marked complete on server", sum(1 for p in prog if p.get("status") == 2), len(prog))


# ─────────────────────────────────────────────────────────────────────────────
# Auto-Enrollment Module
# ─────────────────────────────────────────────────────────────────────────────

def _normalise(s: str) -> str:
    return s.strip().lower()

def search_course(session: requests.Session, name: str, org: str = "") -> list:
    payload = {
        "request": {
            "filters": {"contentType": ["Course"], "courseCategory": {"!=": ["pre enrolment assessment"]}, "status": ["Live"]},
            "fields": ["identifier", "name", "organisation", "source", "duration", "avgRating", "difficultyLevel", "primaryCategory", "language"],
            "query": name, "limit": CONFIG["search_limit"], "offset": 0, "sort_by": {},
        }
    }
    if org: payload["request"]["filters"]["organisation"] = [org]
    try:
        r = session.post(SEARCH_URL, json=payload, timeout=20)
        r.raise_for_status()
        return r.json().get("result", {}).get("content", [])
    except Exception as e:
        log.error("  Search request failed: %s", e)
        return []

def _best_match(candidates: list, name: str, org: str) -> Optional[dict]:
    norm_name, norm_org = _normalise(name), _normalise(org)
    exact_name_org, exact_name = None, None
    for c in candidates:
        c_name = _normalise(c.get("name", ""))
        c_orgs = [_normalise(o) for o in c.get("organisation", [])]
        name_match = (c_name == norm_name)
        org_match  = any(norm_org in o or o in norm_org for o in c_orgs) if norm_org else True

        if name_match and org_match and exact_name_org is None: exact_name_org = c
        if name_match and exact_name is None: exact_name = c
    return exact_name_org or exact_name or (candidates[0] if candidates else None)

def _fmt_duration(secs) -> str:
    try:
        s = int(float(secs))
        h, m = divmod(s, 3600)
        m, s = divmod(m, 60)
        return f"{h}h {m}m" if h else f"{m}m {s}s"
    except Exception:
        return str(secs)

def resolve_course_id(session: requests.Session, name: str, org: str = "") -> Optional[str]:
    log.info("  🔍 Searching: \"%s\"%s", name, f" [{org}]" if org else "")
    candidates = search_course(session, name, org)
    if not candidates:
        log.warning("  ✗  No results returned for this query.")
        return None

    match = _best_match(candidates, name, org)
    if not match:
        log.warning("  ✗  Could not determine a best match.")
        return None

    print(f"\n  ┌─ Best match ──────────────────────────────────────────")
    print(f"  │  Name       : {match.get('name', '?')}")
    print(f"  │  ID         : {match.get('identifier', '?')}")
    print(f"  │  Org        : {', '.join(match.get('organisation', []))}")
    print(f"  │  Duration   : {_fmt_duration(match.get('duration', 0))}   Rating: {match.get('avgRating', '—')}   Level: {match.get('difficultyLevel', '—')}")
    print(f"  └───────────────────────────────────────────────────────")

    if CONFIG["confirm_search_results"]:
        if not _confirm("  Add this course to enrollment list? [Y/n]: "):
            log.info("  Skipped by user.")
            return None
    return match.get("identifier")

def enroll_in_course(session: requests.Session, course_id: str) -> bool:
    url = f"{PROTECTED_BASE}/cohorts/user/autoenrollment/{course_id}?language=english"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        content = r.json().get("result", {}).get("response", {}).get("content", [])
        if not content:
            log.warning("  ⚠  No open batches found for this course.")
            return False
        log.info("  ✓  Enrolled — batch: %s", content[0].get("batchId", "?"))
        return True
    except Exception as e:
        log.error("  ✗  Enrollment request failed: %s", e)
        return False

def verify_enrollment(session: requests.Session, course_id: str):
    url = f"{PROXIES_BASE}/learner/course/v4/user/enrollment/details/{CONFIG['user_id']}"
    try:
        r = session.post(url, json={"request": {"retiredCoursesEnabled": True, "courseId": [course_id]}}, timeout=15)
        r.raise_for_status()
        courses = r.json().get("result", {}).get("courses", [])
        if courses: log.info("  ✓  Verified in database: %s", courses[0].get("courseName", course_id))
        else: log.warning("  ⚠  Could not verify — may still be syncing.")
    except Exception as e:
        log.error("  ✗  Verification failed: %s", e)


def collect_enroll_inputs(session: requests.Session) -> list:
    ids, seen = [], set()
    def _add(cid: str, label: str = ""):
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)
            log.info("  + Added %s%s", cid, f"  ({label})" if label else "")

    print("\n┌─ Enter courses to enroll in ──────────────────────────────────────")
    print("│  Enter one item per line. Blank line when done.")
    print("│")
    print("│  Accepted formats:")
    print("│    do_1234567890...          ← paste a direct course ID")
    print("│    Course Name | Org Name    ← search by name + org (org optional)")
    print("│")
    print("│  Examples:")
    print("│    do_1145242891094835201284")
    print("│    Artificial Intelligence for Karmayogis | Karmayogi Bharat")
    print("└───────────────────────────────────────────────────────────────────")

    while True:
        try: line = input("\n  → ").strip()
        except (EOFError, KeyboardInterrupt): break
        if not line: break

        line = line.strip().strip("\"'").rstrip("/")
        if line.startswith("do_"):
            _add(line)
        else:
            n, o = (line.split("|", 1)[0].strip(), line.split("|", 1)[1].strip()) if "|" in line else (line, "")
            if not n: continue
            cid = resolve_course_id(session, n, o)
            if cid: _add(cid, f"searched: {n}")
            time.sleep(random.uniform(CONFIG["human"]["between_search_pause_min"], CONFIG["human"]["between_search_pause_max"]))
    print()
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# ██  INTERACTIVE UI
# ─────────────────────────────────────────────────────────────────────────────

CREDS_FILE = ".igot_session.json"

def _clear(): os.system("cls" if os.name == "nt" else "clear")

def _banner(subtitle: str = ""):
    print("═" * _W)
    print("  iGOT Karmayogi — Toolkit v5")
    if subtitle: print(f"  {subtitle}")
    print("═" * _W)

def _ask(prompt: str, default: str = "", secret: bool = False) -> str:
    import getpass
    display = f" [{default[:40] + '…' if len(default) > 40 else default}]" if default else ""
    full_prompt = f"  {prompt}{display}: "
    try: val = (getpass.getpass(full_prompt) if secret else input(full_prompt)).strip()
    except (EOFError, KeyboardInterrupt): return default
    return val if val else default

def _yn(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try: ans = input(f"  {prompt} {hint}: ").strip().lower()
    except (EOFError, KeyboardInterrupt): return default
    if not ans: return default
    return ans in ("y", "yes")

def _confirm(prompt: str) -> bool:
    try: return input(prompt).strip().lower() in ("y", "yes", "")
    except (EOFError, KeyboardInterrupt): return False

def _pick(prompt: str, options: list, default_idx: int = 0) -> int:
    print(f"\n  {prompt}")
    for i, opt in enumerate(options):
        print(f"    {'●' if i == default_idx else '○'} {i + 1}. {opt}")
    while True:
        try: raw = input(f"  Choice [1-{len(options)}] (Enter = {default_idx + 1}): ").strip()
        except (EOFError, KeyboardInterrupt): return default_idx
        if not raw: return default_idx
        if raw.isdigit() and 1 <= int(raw) <= len(options): return int(raw) - 1
        print("  ⚠  Invalid choice, try again.")

def _load_creds() -> dict:
    try:
        with open(CREDS_FILE) as f: return json.load(f)
    except Exception: return {}

def _save_creds(user_id: str, cookie: str):
    try:
        with open(CREDS_FILE, "w") as f: json.dump({"user_id": user_id, "cookie": cookie}, f)
    except Exception: pass

def _credentials_wizard() -> tuple:
    saved = _load_creds()
    _clear()
    _banner("Step 1 of 3 — Credentials")
    print("\n  How to get these values:")
    print("  1. Log in to portal.igotkarmayogi.gov.in in Chrome")
    print("  2. Press F12 → Network tab → pick any API request")
    print("  3. Copy 'wid' header value  →  User ID")
    print("  4. Copy 'Cookie' header value  →  Cookie string")
    if saved:
        print(f"\n  ℹ  Saved session found for user: {saved.get('user_id', '?')}")
        if _yn("  Use saved credentials?", default=True): return saved["user_id"], saved["cookie"]
    print()
    user_id = _ask("User ID (wid)", saved.get("user_id", ""))
    print("\n  Paste the FULL Cookie string below (one long line):")
    cookie  = _ask("Cookie", saved.get("cookie", ""))
    if user_id and cookie:
        _save_creds(user_id, cookie)
        print("  ✓  Credentials saved to", CREDS_FILE)
    return user_id, cookie

def _mode_wizard() -> tuple:
    _clear()
    _banner("Step 2 of 3 — What to run")
    mode_idx = _pick(
        "Select run mode:",
        [
            "Single course  — complete one course by ID",
            "All courses    — process every In-Progress enrollment",
            "Enroll         — search and auto-enroll in new courses"
        ],
        default_idx=0,
    )
    mode = ["single", "all", "enroll"][mode_idx]
    course_id = ""
    if mode == "single":
        print()
        course_id = _ask("Course ID  (e.g. do_114371136825573376161)", "")
        while not course_id.startswith("do_"):
            print("  ⚠  Course ID must start with 'do_'")
            course_id = _ask("Course ID", "")
    return mode, course_id

def _fmt_bool(v: bool) -> str: return "✓ ON " if v else "✗ OFF"

def _print_settings():
    h = CONFIG["human"]
    print("\n  ┌─ Current settings ─────────────────────────────────────────")
    print(f"  │  [1] Mode          : {CONFIG['mode'].upper()}" + (f"  →  {CONFIG.get('course_id', '')}" if CONFIG["mode"] == "single" else ""))
    print(f"  │  [2] Watch mode    : {h['watch_mode']}")
    print(f"  │  [3] Videos        : {_fmt_bool(CONFIG['complete_videos'])}")
    print(f"  │  [4] PDFs          : {_fmt_bool(CONFIG['complete_pdfs'])}")
    print(f"  │  [5] Quizzes       : {_fmt_bool(CONFIG['complete_quizzes'])}")
    print(f"  │  [6] Survey        : {_fmt_bool(CONFIG['submit_survey'])}")
    print(f"  │  [7] Skip done     : {_fmt_bool(h['skip_if_completed'])}")
    print(f"  │  [8] Item pause    : {h['between_item_pause_min']}–{h['between_item_pause_max']} s")
    print(f"  │  [9] Module pause  : {h['between_module_pause_min']}–{h['between_module_pause_max']} s")
    if CONFIG["mode"] == "all":
        print(f"  │  [10] Course pause : {h['between_course_pause_min']}–{h['between_course_pause_max']} s")
        skips = CONFIG.get("skip_course_ids", [])
        print(f"  │  [11] Skip list    : {len(skips)} course(s)" + (f" — {skips[0]}…" if skips else ""))
    print("  └────────────────────────────────────────────────────────────")

def _edit_pause_range(label: str, key_min: str, key_max: str):
    h = CONFIG["human"]
    print(f"\n  Current: {h[key_min]}–{h[key_max]} seconds")
    try:
        lo = input(f"  New minimum (Enter = {h[key_min]}): ").strip()
        hi = input(f"  New maximum (Enter = {h[key_max]}): ").strip()
        if lo: h[key_min] = float(lo)
        if hi: h[key_max] = float(hi)
    except ValueError:
        print("  ⚠  Invalid number — keeping previous values.")

def _edit_skip_list():
    skips = CONFIG.get("skip_course_ids", [])
    print(f"\n  Current skip list: {skips or '(empty)'}")
    print("  Enter course IDs to skip, one per line. Blank line to finish.")
    new_ids = []
    while True:
        try: line = input("  do_...: ").strip()
        except (EOFError, KeyboardInterrupt): break
        if not line: break
        if line.startswith("do_"): new_ids.append(line)
    if new_ids:
        CONFIG["skip_course_ids"] = new_ids
        print(f"  ✓  Skip list updated: {new_ids}")

def _settings_menu():
    if CONFIG["mode"] == "enroll":
        return # Skip detailed configuration if just enrolling
    while True:
        _clear()
        _banner("Step 3 of 3 — Review settings")
        _print_settings()
        print("\n  Enter a setting number to change it, or press Enter to start.")
        try: choice = input("\n  → ").strip()
        except (EOFError, KeyboardInterrupt): break
        if not choice: break
        
        if choice == "1":
            mode, cid = _mode_wizard()
            CONFIG["mode"], CONFIG["course_id"] = mode, cid
            if mode == "enroll": break
        elif choice == "2":
            idx = _pick("Watch mode:", ["Warp (instant)", "Fast (balanced)", "Real-time (slowest)"], default_idx=["warp", "fast", "real_time"].index(CONFIG["human"]["watch_mode"]))
            CONFIG["human"]["watch_mode"] = ["warp", "fast", "real_time"][idx]
        elif choice == "3": CONFIG["complete_videos"]  = not CONFIG["complete_videos"]
        elif choice == "4": CONFIG["complete_pdfs"]    = not CONFIG["complete_pdfs"]
        elif choice == "5": CONFIG["complete_quizzes"] = not CONFIG["complete_quizzes"]
        elif choice == "6": CONFIG["submit_survey"]    = not CONFIG["submit_survey"]
        elif choice == "7": CONFIG["human"]["skip_if_completed"] = not CONFIG["human"]["skip_if_completed"]
        elif choice == "8": _edit_pause_range("Item pause", "between_item_pause_min", "between_item_pause_max")
        elif choice == "9": _edit_pause_range("Module pause", "between_module_pause_min", "between_module_pause_max")
        elif choice == "10" and CONFIG["mode"] == "all": _edit_pause_range("Course pause", "between_course_pause_min", "between_course_pause_max")
        elif choice == "11" and CONFIG["mode"] == "all": _edit_skip_list()
        else:
            print("  ⚠  Unknown option.")
            time.sleep(0.8)

def _between_course_menu(next_name: str, remaining: int, timeout: float) -> bool:
    import sys
    import time
    
    print(f"\n{'─' * _W}")
    print(f"  ✓  Course done.  {remaining} course(s) remaining.")
    print(f"  ↳  Next: {next_name}")
    print()
    print("  Options:")
    print("    Enter  — continue immediately")
    print("    s      — skip next course")
    print("    q      — quit after this course")
    print(f"{'─' * _W}")

    start_time = time.time()
    choice = ""

    if sys.platform == "win32":
        import msvcrt
        while True:
            rem = int(timeout - (time.time() - start_time))
            if rem < 0:
                print(f"\r  [ Auto-proceeding to next course... ]{' '*20}")
                return True
            
            print(f"\r  → (Auto-proceed in {rem}s) Press key: ", end="", flush=True)
            
            if msvcrt.kbhit():
                char = msvcrt.getwch() # Reads a single keypress instantly
                if char in ('\r', '\n'):
                    print()
                    return True
                else:
                    print(char)
                    choice = char.lower()
                    break
            time.sleep(0.1)
    else:
        # Fallback for Linux/macOS
        import select
        while True:
            rem = int(timeout - (time.time() - start_time))
            if rem < 0:
                print(f"\r  [ Auto-proceeding to next course... ]{' '*20}")
                return True
            
            print(f"\r  → (Auto-proceed in {rem}s) Type choice + Enter: ", end="", flush=True)
            
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            if ready:
                choice = sys.stdin.readline().strip().lower()
                break

    if choice == "q":
        return False
    if choice == "s":
        CONFIG.setdefault("_skip_next", True)
        return True

    return True

def _bootstrap():
    import copy
    user_id, cookie = _credentials_wizard()
    if not user_id or not cookie: raise SystemExit(1)
    mode, course_id = _mode_wizard()
    CONFIG.update(copy.deepcopy(DEFAULTS))
    CONFIG["user_id"], CONFIG["cookie"], CONFIG["mode"], CONFIG["course_id"] = user_id, cookie, mode, course_id
    _settings_menu()
    _clear()
    _banner("Running…\n")

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    _bootstrap()

    log.info("iGOT Karmayogi Toolkit v5")
    log.info("User   : %s", CONFIG["user_id"])
    if CONFIG["mode"] != "enroll":
        log.info("Mode   : %s  |  Watch: %s", CONFIG["mode"], CONFIG["human"]["watch_mode"])
    else:
        log.info("Mode   : %s", CONFIG["mode"])
    log.info("═" * _W)

    session = build_session()
    h = CONFIG["human"]

    if CONFIG["mode"] == "enroll":
        courses = collect_enroll_inputs(session)
        if not courses:
            log.warning("No course IDs collected. Exiting.")
            return

        log.info("\n%s\nEnrollment queue (%d course(s)):", "━" * _W, len(courses))
        for i, cid in enumerate(courses, 1): log.info("  %d. %s", i, cid)
        log.info("━" * _W)

        if CONFIG["confirm_search_results"] and not _confirm("\nProceed with enrolling all of the above? [Y/n]: "):
            log.info("Aborted by user.")
            return

        results = {"ok": 0, "fail": 0}
        for i, cid in enumerate(courses, 1):
            log.info("\n[%d/%d] %s", i, len(courses), cid)
            if enroll_in_course(session, cid):
                results["ok"] += 1
                time.sleep(random.uniform(h["verify_delay_min"], h["verify_delay_max"]))
                verify_enrollment(session, cid)
            else:
                results["fail"] += 1

            if i < len(courses):
                pause = random.uniform(h["between_enroll_pause_min"], h["between_enroll_pause_max"])
                log.info("  (next in %.1fs…)", pause)
                time.sleep(pause)

        log.info("\n%s\nDone — %d enrolled, %d failed.\n%s", "═" * _W, results["ok"], results["fail"], "═" * _W)
        return

    # Completion Mode execution
    if CONFIG["mode"] == "all":
        enrolled = fetch_enrolled_courses(session)
        if not enrolled: return log.error("No In-Progress courses found.")
        todo = [e for e in enrolled if e.get("completionPercentage", 0) != 100]
        if len(enrolled) - len(todo): log.info("Skipping %d already-complete course(s).", len(enrolled) - len(todo))

        for i, enroll in enumerate(todo, 1):
            cid, name, pct = enroll.get("courseId", ""), enroll.get("content", {}).get("name", ""), enroll.get("completionPercentage", 0)
            log.info("\n%s\n[%d/%d] %s  (%s%%)\nID: %s\n%s", "━" * _W, i, len(todo), name, pct, cid, "━" * _W)

            if CONFIG.pop("_skip_next", False):
                log.info("  ⏭  Skipped by user request.")
                continue

            process_course(session, cid)

            if i < len(todo):
                next_name = todo[i].get("content", {}).get("name", todo[i].get("courseId", ""))
                pre_sleep = random.uniform(h["between_course_pause_min"], h["between_course_pause_max"])
                
                # The menu now handles the sleeping/countdown directly!
                if not _between_course_menu(next_name, len(todo) - i, timeout=pre_sleep): 
                    break

    else:   # single completion
        if not CONFIG.get("course_id"): return log.error("No course_id set.")
        log.info("Course : %s", CONFIG["course_id"])
        process_course(session, CONFIG["course_id"])

    log.info("\n%s\n🎉 All done! Have a great day!\n%s", "═" * _W, "═" * _W)

if __name__ == "__main__":
    main()