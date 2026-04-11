"""
iGOT Karmayogi Bulk Auto-Enroller
=================================
Automatically enrolls your account into a list of Course IDs.
"""

import time
import random
import logging
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

CONFIG = {
    "user_id": "4ce33233-8498-456b-81e8-b825792927cd", 
    
    # Paste the FULL cookie string from DevTools here
    "cookie": (
        "unbxd.netcoreId=IjMyZTA0ZmJlODEyOTE2MWIwNDNhMmEwZGY4MzU1NTVhZTE0YzkyM2QzNjA0N2Q4YTE5ZjliODMyMjhmMGEwZGUi; "
        "connect.sid=s%3AyYjeqYT_MW7un59XcIB0vX6QHi15-8gs.VK7D1mHucSf9Sf9WUf7DRX%2BR%2FD2cTLEFzX9QeX4QLck; "
        # ... paste the rest of your cookie ...
    ),

    # Add as many course IDs here as you want to enroll in
    "courses_to_enroll": [
        "do_1143613347908812801129",  # Mastering Feedback: A Leadership Tool
        # "do_1234567890...",
    ]
}

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS & SETUP
# ─────────────────────────────────────────────────────────────────────────────

PROTECTED_BASE = "https://portal.igotkarmayogi.gov.in/apis/protected/v8"
PROXIES_BASE   = "https://portal.igotkarmayogi.gov.in/apis/proxies/v8"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("igot_enroller")

def build_session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Connection": "keep-alive",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        "wid": CONFIG["user_id"],
        "Cookie": CONFIG["cookie"],
    })
    return s

# ─────────────────────────────────────────────────────────────────────────────
# CORE LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def enroll_in_course(session: requests.Session, course_id: str) -> bool:
    """Hits the autoenrollment endpoint to assign the user to a batch."""
    url = f"{PROTECTED_BASE}/cohorts/user/autoenrollment/{course_id}?language=english"
    
    try:
        # API 1: Trigger Auto-Enrollment
        r = session.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        # Check if the backend successfully returned a batch
        content = data.get("result", {}).get("response", {}).get("content", [])
        if not content:
            log.warning("  ⚠️ Enrollment failed: No open batches found for this course.")
            return False
            
        batch_id = content[0].get("batchId")
        course_name = content[0].get("name", "Unknown Course")
        log.info("  ✅ Enrolled in batch: %s", batch_id)
        return True

    except Exception as e:
        log.error("  ❌ Enrollment request failed: %s", e)
        return False

def verify_enrollment(session: requests.Session, course_id: str):
    """Verifies the enrollment record exists."""
    url = f"{PROXIES_BASE}/learner/course/v4/user/enrollment/details/{CONFIG['user_id']}"
    payload = {
        "request": {
            "retiredCoursesEnabled": True,
            "courseId": [course_id]
        }
    }
    
    try:
        # API 3: Verify Enrollment State
        r = session.post(url, json=payload, timeout=15)
        r.raise_for_status()
        courses = r.json().get("result", {}).get("courses", [])
        
        if courses:
            name = courses[0].get("courseName", course_id)
            log.info("  🎯 Verified! Successfully registered for: %s", name)
        else:
            log.warning("  ⚠️ Could not verify enrollment in database.")
            
    except Exception as e:
        log.error("  ❌ Verification failed: %s", e)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("═" * 60)
    log.info("iGOT Bulk Auto-Enroller")
    log.info("═" * 60)
    
    session = build_session()
    courses = CONFIG["courses_to_enroll"]
    
    if not courses:
        log.warning("No courses added to 'courses_to_enroll' list. Exiting.")
        return

    for i, cid in enumerate(courses, 1):
        log.info("")
        log.info("[%d/%d] Attempting to enroll in: %s", i, len(courses), cid)
        
        # 1. Trigger the enrollment
        success = enroll_in_course(session, cid)
        
        # 2. Verify it
        if success:
            time.sleep(1) # Small buffer for database sync
            verify_enrollment(session, cid)
            
        # 3. Human pause between requests
        if i < len(courses):
            time.sleep(random.uniform(3.0, 6.0))

    log.info("")
    log.info("═" * 60)
    log.info("🎉 All enrollment requests finished!")

if __name__ == "__main__":
    main()