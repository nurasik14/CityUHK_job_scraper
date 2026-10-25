"""Manual live run: scrape every AIMS scheme (or the ones named) into the cache.

Run with: uv run python scripts/scrape_aims.py [--no-login] [cis] [sh_summer] [sh_regular]
Needs a valid AIMS session (log in first with scripts/check_auth.py). Without one it opens
a login window, or with --no-login stops with "AIMS login needed" (agents always use it). Read-only: it only
uses the job search form and the links in the results table. The first run takes about
35 minutes (~17 s per job: pauses, a re-search and the detail page); later runs only open
new or changed jobs. Ctrl+C keeps the jobs scraped so far.
"""

import argparse
import functools
import logging
import sys

from playwright.sync_api import sync_playwright

from cityu_jobs.core.aims_scraper import SCHEMES, scrape_aims
from cityu_jobs.core.auth import AimsSessionExpired, aims_session
from cityu_jobs.core.cache import Cache


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("schemes", nargs="*", choices=list(SCHEMES), help="default: all schemes")
    parser.add_argument("--no-login", action="store_true", help="never open a login window")
    args = parser.parse_args()
    scheme_keys = args.schemes or list(SCHEMES)

    cache = Cache.load()
    progress = functools.partial(print, flush=True)  # show each line at once, even when piped to a file
    try:
        with sync_playwright() as playwright, aims_session(playwright, interactive=not args.no_login) as context:
            results = scrape_aims(context, cache, scheme_keys, progress=progress)
    except AimsSessionExpired as e:
        cache.save()  # keep the schemes that finished before the session ran out
        sys.exit(f"AIMS login needed: {e} Run: uv run python scripts/check_auth.py")
    except KeyboardInterrupt:  # scrape_aims already saved the jobs opened so far
        sys.exit(f"Interrupted. Jobs scraped so far are saved in {cache.path}; run again to continue.")

    for r in results:
        matched = "?" if r.matched is None else r.matched
        progress(
            f"{SCHEMES[r.scheme_key]}: {r.listed} listed (AIMS says {matched}), "
            f"{r.opened} opened, {r.unchanged} unchanged, {len(r.errors)} errors"
        )
        for error in r.errors:
            progress(f"  ! {error}")
    progress(f"{len(cache.active_jobs())} active jobs in {cache.path}")


if __name__ == "__main__":
    main()
