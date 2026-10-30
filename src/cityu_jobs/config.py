"""AIMS URLs and local file locations, kept in one place."""

import os
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
# Everything local lives here (git-ignored). CITYU_JOBS_DATA_DIR points it elsewhere, e.g. for a demo.
DATA_DIR = Path(os.environ.get("CITYU_JOBS_DATA_DIR") or PROJECT_ROOT / ".data")
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

# Which model rates jobs: "claude" (the student's Claude plan, through the `claude` CLI;
# owner decision 2026-10-30) or "ollama" (free local models, nothing leaves the machine).
# Embeddings always come from Ollama: Claude has no embedding model.
DEFAULT_AI_PROVIDER = "claude"
AI_PROVIDERS = ("claude", "ollama")
# Claude model alias for `claude --model`: "haiku" (fastest), "sonnet" (default), "opus".
DEFAULT_CLAUDE_MODEL = "sonnet"
CLAUDE_MODELS = ("haiku", "sonnet", "opus")
CLAUDE_TIMEOUT_S = 240
# Jobs per Claude request, and requests at once. Batching keeps plan usage low:
# ~220 jobs = ~28 requests instead of 220.
CLAUDE_BATCH_SIZE = 8
CLAUDE_MAX_PARALLEL = 3

OLLAMA_URL = "http://localhost:11434"
DEFAULT_LLM_MODEL = "llama3.2:3b"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
# The student's choices from the settings page (Ollama URL and model names; no secrets).
SETTINGS_FILE = DATA_DIR / "settings.json"
# Seconds to wait for one AI request. Local models on a laptop CPU can be slow.
LLM_TIMEOUT_S = 120
EMBED_TIMEOUT_S = 60
# nomic-embed-text reads up to 2048 tokens and Ollama refuses longer input; stay below it.
EMBED_MAX_TOKENS = 1800

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
# Tech-industry internships (department "TECH") also count for CS students.
DEPARTMENT_BOOST_ALIASES = {"CS": {"CS", "CC", "TECH"}}
# Weekly hours at or above this count as "full" on the hours term.
FULL_WEEKLY_HOURS = 20
# Number of jobs shown on the results page.
RESULTS_LIMIT = 30

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

# --- Tech internships outside CityU (core/internships/) ---------------------------

# Regions the student can pick on the Settings page (patterns live in core/internships/regions.py).
# Default: places a Hong Kong student can realistically work from. North America and
# Europe usually need a work visa, so they are opt-in.
INTERN_REGIONS = {
    "hk": "Hong Kong",
    "china": "Mainland China",
    "apac": "Singapore & rest of Asia-Pacific",
    "remote": "Remote (worldwide)",
    "europe": "Europe & UK",
    "americas": "North America & elsewhere",
}
DEFAULT_INTERN_REGIONS = ("hk", "china", "apac", "remote")
# Pause between requests to the same job-board host (public JSON APIs).
INTERN_DELAY_S = 0.5
INTERN_TIMEOUT_S = 30
# Internship descriptions are long; the first part is enough for matching and rating.
INTERN_MAX_DESCRIPTION_CHARS = 6000

# --- The student's profile, fit estimates and applications ---------------------------

# CV text, projects and skills (personal data: file mode 600, never committed).
PROFILE_FILE = DATA_DIR / "profile.json"
# Uploaded CVs larger than this are refused.
CV_MAX_BYTES = 5 * 1024 * 1024
CV_EXTENSIONS = (".pdf", ".docx", ".txt", ".md", ".tex")
# Text of the profile sent to the AI, at most (a 1-2 page CV is ~4-8k characters).
PROFILE_MAX_CHARS = 14000
# "Difficulty for you" is estimated for the top N results overall and the top N of each
# group (CityU / big tech / other tech), so every filter tab has estimates. Claude only.
FIT_TOP_N = 30
FIT_BATCH_SIZE = 6
# Generated cover letters / application emails, one JSON file per job.
LETTERS_DIR = DATA_DIR / "letters"
# Saved / applied marks per job.
TRACKER_FILE = DATA_DIR / "applications.json"
# "Your gaps" reports (one per results tab), and how many top results to read skills from
# when no job has a "difficulty for you" estimate yet.
GAPS_FILE = DATA_DIR / "gaps.json"
GAPS_FALLBACK_JOBS = 40
# Uploaded ITC STEM spreadsheets are capped at this size.
ITC_MAX_BYTES = 10 * 1024 * 1024
