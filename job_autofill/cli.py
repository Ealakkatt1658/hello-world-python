"""Command line entry point: ``python -m job_autofill <job url> [<job url> ...]``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from job_autofill.profile import ProfileError, load_profile

log = logging.getLogger("job_autofill")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m job_autofill",
        description="Fill in (and optionally submit) Workday, Greenhouse and Lever job applications from your profile.",
    )
    parser.add_argument("urls", nargs="*", help="Job posting or application URL(s), each in quotes. Leave out to be asked.")
    parser.add_argument("--setup", action="store_true", help="Create your profile by answering questions")
    parser.add_argument("--check", action="store_true", help="Check your setup (profile, resume, AI key, browser) and exit")
    parser.add_argument("--profile", default="profile.yaml", help="Your profile file (default: profile.yaml)")
    parser.add_argument(
        "--auto-submit",
        action="store_true",
        help="Submit without asking first. By default it stops on the Review page and waits for you to type 'submit'.",
    )
    ai = parser.add_mutually_exclusive_group()
    ai.add_argument(
        "--ai",
        dest="ai",
        action="store_true",
        default=None,
        help="Let Claude draft answers to questions your profile doesn't cover (needs ANTHROPIC_API_KEY). "
        "On by default when ANTHROPIC_API_KEY is set.",
    )
    ai.add_argument("--no-ai", dest="ai", action="store_false", help="Never use AI; ask me instead")
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


def setup_logging(verbose: bool) -> Path:
    """Log to the terminal and to logs/<time>.log (send that file along with any bug report)."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{datetime.now():%Y%m%d-%H%M%S}.log"
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    file = logging.FileHandler(log_file, encoding="utf-8")
    file.setLevel(logging.DEBUG)
    file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file)
    for noisy in ("httpx", "httpcore", "anthropic", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log_file = setup_logging(args.verbose)
    log.info("Log file: %s", log_file)
    try:
        if args.setup:
            from job_autofill.setup_wizard import run_setup

            run_setup(Path(args.profile))
            args.check = True  # show the user straight away whether everything works
        profile = load_profile(args.profile)
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from playwright.sync_api import sync_playwright

    from job_autofill.ai import make_answerer
    from job_autofill.sites import SUPPORTED, applier_for

    if args.check:
        return check_setup(args, profile)
    if not args.urls:
        try:
            pasted = input("Paste the job link (several links: separate them with spaces) and press Enter:\n> ")
        except (KeyboardInterrupt, EOFError):
            return 2
        args.urls = pasted.split()
        if not args.urls:
            print("error: no link given", file=sys.stderr)
            return 2
    not_links = [u for u in args.urls if not u.lower().startswith(("http://", "https://"))]
    if not_links:
        print(
            "error: these aren't links: " + ", ".join(not_links) + "\n"
            "Put each link in quotes -- links containing '&' get split up by the terminal otherwise.",
            file=sys.stderr,
        )
        return 2
    unsupported = [u for u in args.urls if applier_for(u) is None]
    if unsupported:
        print(f"error: only {SUPPORTED} applications are supported so far:\n  " + "\n  ".join(unsupported), file=sys.stderr)
        return 2
    answerer = make_answerer(profile, args.ai)

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
        for url in args.urls:
            applier = applier_for(url)(page, profile, auto_submit=args.auto_submit, answerer=answerer)
            try:
                results[url] = "submitted" if applier.run(url) else "not submitted"
            except KeyboardInterrupt:
                results[url] = "cancelled"
                break
            except Exception as exc:  # keep going with the next job
                log.exception("Failed on %s", url)
                applier.save_debug("error")
                results[url] = f"error: {str(exc).splitlines()[0]}"
        if not args.headless:
            try:
                input("\nDone. Press Enter to close the browser... ")
            except (KeyboardInterrupt, EOFError):
                pass
        context.close()

    print("\nSummary:")
    for url, status in results.items():
        print(f"  [{status}] {url}")
    print(f"\nFull log: {log_file}")
    return 0 if all(s == "submitted" for s in results.values()) else 1


def check_setup(args: argparse.Namespace, profile: dict) -> int:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    from job_autofill.ai import make_answerer

    ok = True
    per = profile["personal"]
    print(f"Profile:  {per['first_name']} {per['last_name']} <{per['email']}>")
    print(f"Resume:   {profile.get('resume') or 'NOT SET'}")
    print(f"Jobs: {len(profile.get('work_experience') or [])}, schools: {len(profile.get('education') or [])}, "
          f"custom answers: {len(profile.get('answers') or [])}")
    has_password = bool(os.environ.get("WORKDAY_PASSWORD") or profile["account"].get("password"))
    print(f"Workday password: {'set' if has_password else 'not set (you will be asked when needed)'}")

    answerer = make_answerer(profile, args.ai)
    if answerer is None:
        print("AI answers: off (set ANTHROPIC_API_KEY to turn them on)")
    else:
        try:
            answerer.client.models.retrieve(answerer.model)
            print(f"AI answers: on, API key works ({answerer.model})")
        except Exception as exc:
            ok = False
            print(f"AI answers: PROBLEM -- {getattr(exc, 'message', exc)}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, channel=args.channel, executable_path=os.environ.get("CHROMIUM_PATH") or None
            )
            browser.close()
        print("Browser: OK")
    except PlaywrightError as exc:
        ok = False
        print(f"Browser: PROBLEM -- {str(exc).splitlines()[0]}\n  Fix: python -m playwright install chromium")

    print("\nAll good -- ready to apply." if ok else "\nFix the problems above first.")
    return 0 if ok else 1
