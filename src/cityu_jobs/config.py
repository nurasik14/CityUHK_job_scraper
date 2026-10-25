"""AIMS URLs and local file locations, kept in one place."""

import re
from pathlib import Path

# Page where the student starts the AIMS login (redirects to CityU single sign-on).
AIMS_LOGIN_URL = "https://banweb.cityu.edu.hk/pls/PROD/twgkpswd_cityu.P_WWWLogin"
# AIMS main menu. Logged out, AIMS serves a "User Login" page at this same URL,
# so the URL alone never proves a login: we also look for AIMS_LOGGED_IN_MARKER.
AIMS_CHECK_URL = "https://banweb.cityu.edu.hk/pls/PROD/twbkwbis.P_GenMenu?name=bmenu.P_MainMnu"
AIMS_LOGGED_IN_URL_PATTERN = re.compile(re.escape(AIMS_CHECK_URL))
# Text that only appears on the main menu after login.
AIMS_LOGGED_IN_MARKER = "Student Services"
# Student Helper and Campus Internship Scheme job search.
AIMS_JOB_SEARCH_URL = "https://banweb.cityu.edu.hk/pls/PROD/hwosjpstenq_cityu.P_Main"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / ".data"
# Saved cookies act like a password for AIMS: never commit or log this file.
SESSION_FILE = DATA_DIR / "aims_session.json"

# How long the visible browser waits for the student to finish logging in (incl. 2-factor).
LOGIN_TIMEOUT_MS = 5 * 60 * 1000

# --- Cache and scraping -------------------------------------------------------

# Scraped jobs, AI levels and embeddings (see core/cache.py).
CACHE_FILE = DATA_DIR / "cache.json"
# Cache entries are deleted this many days after the job closes (or was last seen).
CACHE_RETENTION_DAYS = 30
# A source is re-scraped only if its last successful scrape is older than this.
# Set to 0 to scrape on every search.
STALE_AFTER_HOURS = 6
# Politeness: pause between AIMS page loads (never parallel).
AIMS_DELAY_S = 1.5

# --- AI provider defaults (the student can change these in the settings page) --

OLLAMA_URL = "http://localhost:11434"
DEFAULT_LLM_MODEL = "llama3.2:3b"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
# The student's choices from the settings page (Ollama URL and model names; no secrets).
SETTINGS_FILE = DATA_DIR / "settings.json"
# Seconds to wait for one AI request. Local models on a laptop CPU can be slow.
LLM_TIMEOUT_S = 120
EMBED_TIMEOUT_S = 60

# --- Ranking (see architecture.md "Score"; every input is scaled to 0-1) --------

WEIGHT_SIMILARITY = 0.45   # how well the job matches the student's free-text query
WEIGHT_DEPARTMENT = 0.10   # job is from the student's department
WEIGHT_HOURS = 0.20        # x earn priority: more weekly hours = more money
WEIGHT_STUDENT_HELPER = 0.08  # x earn priority: Student Helper jobs are paid hourly work
WEIGHT_LEVEL = 0.20        # x learn priority: AI-rated level of the work
WEIGHT_LEARNING_SCHEME = 0.08  # x learn priority: internship (CIS) or ITC STEM job
# The product focuses on Computer Science (owner decision): CS is the default department.
DEFAULT_DEPARTMENT = "CS"
# Department codes that count as "the student's department" for the boost. College of
# Computing (CC) ITC STEM rows may only name the college, so CS students match CC too.
# Departments not listed here match their own code only.
DEPARTMENT_BOOST_ALIASES = {"CS": {"CS", "CC"}}
# Weekly hours at or above this count as "full" on the hours term.
FULL_WEEKLY_HOURS = 20
# Number of jobs shown on the results page.
RESULTS_LIMIT = 20

# --- ITC STEM -------------------------------------------------------------------

# Each college publishes its own ITC STEM job list as an Excel file on its web page.
# Owner decision: College of Computing (CC) is the primary source; others are off by default.
ITC_STEM_SOURCES = [
    {
        "college": "cc",
        "name": "College of Computing",
        "department_code": "CC",  # used when a row has no department column
        "page_url": "https://www.cityu.edu.hk/en/cc/student-life-and-learning/internship-schemes/itc-stem-internship-scheme",
        "enabled": True,
    },
    {
        "college": "csci",
        "name": "College of Science",
        "department_code": "CSCI",
        "page_url": "https://www.cityu.edu.hk/en/csci/student-life-learning/internship-schemes/stem-internship-scheme/stem-internship-scheme-202526",
        "enabled": False,
    },
]
# Manual fallback: the student downloads the list in their own browser and saves it here
# as "<college>_job_list.xlsx", e.g. .data/itc/cc_job_list.xlsx.
ITC_DIR = DATA_DIR / "itc"
# Politeness: pause between requests to cityu.edu.hk.
ITC_DELAY_S = 1.0
