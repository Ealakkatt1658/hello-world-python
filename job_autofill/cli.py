"""Command line entry point: ``python -m job_autofill <job url> [<job url> ...]``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from job_autofill.profile import ProfileError, load_profile

log = logging.getLogger("job_autofill")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m job_autofill",
        description="Fill in (and optionally submit) Workday job applications from your profile.",
    )
    parser.add_argument("urls", nargs="+", help="Job posting or application URL(s)")
    parser.add_argument("--profile", default="profile.yaml", help="Your profile file (default: profile.yaml)")
    parser.add_argument(
        "--auto-submit",
        action="store_true",
        help="Submit without asking first. By default it stops on the Review page and waits for you to type 'submit'.",
    )
    parser.add_argument("--headless", action="store_true", help="Hide the browser window (not recommended)")
    parser.add_argument(
        "--browser-dir",
        default=".browser-profile",
        help="Where the browser keeps cookies so you stay signed in between runs",
    )
    parser.add_argument("--channel", help="Use an installed browser instead of Playwright's, e.g. 'chrome' or 'msedge'")
    parser.add_argument("--slow-mo", type=int, default=50, help="Milliseconds to wait between browser actions")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        profile = load_profile(args.profile)
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from playwright.sync_api import sync_playwright

    from job_autofill.workday import WorkdayApplier, is_workday_url

    unsupported = [u for u in args.urls if not is_workday_url(u)]
    if unsupported:
        print("error: only Workday applications are supported so far:\n  " + "\n  ".join(unsupported), file=sys.stderr)
        return 2

    results: dict[str, str] = {}
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(Path(args.browser_dir).resolve()),
            headless=args.headless,
            slow_mo=args.slow_mo,
            channel=args.channel,
            executable_path=os.environ.get("CHROMIUM_PATH") or None,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(15_000)
        applier = WorkdayApplier(page, profile, auto_submit=args.auto_submit)
        for url in args.urls:
            try:
                results[url] = "submitted" if applier.run(url) else "not submitted"
            except KeyboardInterrupt:
                results[url] = "cancelled"
                break
            except Exception as exc:  # keep going with the next job
                log.exception("Failed on %s", url)
                results[url] = f"error: {exc}"
        if not args.headless:
            input("\nDone. Press Enter to close the browser... ")
        context.close()

    print("\nSummary:")
    for url, status in results.items():
        print(f"  [{status}] {url}")
    return 0 if all(s == "submitted" for s in results.values()) else 1
