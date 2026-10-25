"""Manual check: log in to AIMS (if needed) and confirm the saved session opens a protected page.

Run with: uv run python scripts/check_auth.py
Prints only a confirmation, never the page's personal content.
"""

from playwright.sync_api import sync_playwright

from cityu_jobs import config
from cityu_jobs.core.auth import aims_session


def main() -> None:
    with sync_playwright() as playwright, aims_session(playwright):
        pass
    print(f"OK: opened {config.AIMS_JOB_SEARCH_URL} with the saved session (job search form present).")
    print(f"Session stored at {config.SESSION_FILE}")


if __name__ == "__main__":
    main()
