"""Test run: scrape ONE Campus Internship Scheme job from AIMS and log the parsed result.

Run with: uv run python scripts/scrape_aims_test.py
Writes .data/aims_test_job.json (git-ignored).
"""

import json

from playwright.sync_api import sync_playwright

from cityu_jobs import config
from cityu_jobs.core.aims_scraper import SCHEMES, open_job, parse_job_detail, search_jobs
from cityu_jobs.core.auth import aims_session

OUTPUT_FILE = config.DATA_DIR / "aims_test_job.json"


def main() -> None:
    with sync_playwright() as playwright, aims_session(playwright) as context:
        page = context.new_page()
        rows = search_jobs(page, SCHEMES["cis"])
        if not rows:
            raise SystemExit("The Campus Internship Scheme search returned no jobs.")
        print(f"Found {len(rows)} jobs; opening the first one: {rows[0]['Job No.']}")
        job = {"summary": rows[0], **parse_job_detail(open_job(page, rows[0]["Job No."]))}

    output = json.dumps(job, indent=2, ensure_ascii=False)
    print(output)
    OUTPUT_FILE.write_text(output)
    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
