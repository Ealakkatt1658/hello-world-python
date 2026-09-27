"""Browser helpers shared by every site (Workday, Greenhouse, Lever)."""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from job_autofill.filling import Field
from job_autofill.profile import build_rules

log = logging.getLogger(__name__)

SUCCESS_TEXT = re.compile(
    r"application (has been |was )?(successfully )?(submitted|received)|thank(s| you) for (applying|your application|your interest)|"
    r"successfully submitted|congratulations|we have received your application|we've received your application",
    re.I,
)
CAPTCHA = [
    'iframe[src*="captcha" i]:not([src*="invisible" i])',
    'iframe[title*="captcha" i]:not([title*="invisible" i])',
    'iframe[src*="hcaptcha" i][src*="challenge" i]',
    'iframe[title*="challenge" i]',
]


class NeedsAttention(Exception):
    """Raised instead of pausing when running non-interactively."""


class BaseApplier:
    SITE = "site"
    ERRORS: list[str] = ['[role="alert"]']
    JOB_DESCRIPTION: list[str] = ["main", "body"]

    def __init__(
        self,
        page: Page,
        profile: dict,
        auto_submit: bool = False,
        interactive: bool = True,
        screenshot_dir: Optional[Path] = Path("screenshots"),
        confirm: Callable[[str], str] = input,
        answerer=None,
    ) -> None:
        self.page = page
        self.profile = profile
        self.auto_submit = auto_submit
        self.interactive = interactive
        self.screenshot_dir = screenshot_dir
        self.confirm = confirm
        self.answerer = answerer
        if answerer:
            answerer.set_job("", "")  # forget the previous job's posting and drafted answers
        self.rules = build_rules(profile)

    def run(self, url: str) -> bool:
        """Fill in the application at ``url``. Returns True if it was submitted."""
        raise NotImplementedError

    # ------------------------------------------------------------------ waiting / finding
    def settle(self, extra_ms: int = 500) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightError:
            pass
        self.page.wait_for_timeout(extra_ms)

    def first_visible(self, candidates: list, timeout_ms: int = 0) -> Optional[Locator]:
        """First visible match among CSS selectors / Locators, polling up to ``timeout_ms``."""
        deadline = dt.datetime.now() + dt.timedelta(milliseconds=timeout_ms)
        while True:
            for cand in candidates:
                loc = self.page.locator(cand) if isinstance(cand, str) else cand
                try:
                    for i in range(min(loc.count(), 5)):
                        if loc.nth(i).is_visible():
                            return loc.nth(i)
                except PlaywrightError:
                    continue
            if dt.datetime.now() >= deadline:
                return None
            self.page.wait_for_timeout(250)

    def visible_errors(self) -> list[str]:
        texts = []
        for sel in self.ERRORS:
            loc = self.page.locator(sel)
            for i in range(loc.count()):
                if loc.nth(i).is_visible():
                    t = loc.nth(i).inner_text().strip()
                    if t and t not in texts:
                        texts.append(t)
        return texts

    # ------------------------------------------------------------------ talking to the user
    def pause(self, message: str) -> None:
        if not self.interactive:
            raise NeedsAttention(message)
        bar = "=" * 70
        print(f"\n{bar}\nACTION NEEDED: {message}\n{bar}")
        self.confirm("Do it in the browser window, then press Enter here to continue... ")

    def report_unresolved(self, unresolved: list[Field]) -> None:
        if unresolved:
            self.pause(
                "These required questions have no answer in your profile (add them to "
                "`answers:` in profile.yaml for next time). Fill them in:\n  - "
                + "\n  - ".join(f"{f.label or '(no label)'} [{f.kind}]" for f in unresolved)
            )

    def check_captcha(self) -> None:
        if self.first_visible(CAPTCHA):
            self.pause("A CAPTCHA is showing. Please solve it.")

    def screenshot(self, name: str) -> None:
        if not self.screenshot_dir:
            return
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        host = urlparse(self.page.url).netloc
        company = host.split(".")[0] if host else "application"
        path = self.screenshot_dir / f"{self.SITE}-{company}-{dt.datetime.now():%Y%m%d-%H%M%S}-{name}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
            log.info("Saved screenshot %s", path)
        except PlaywrightError as exc:
            log.debug("Screenshot failed: %s", exc)

    # ------------------------------------------------------------------ job description / AI
    def capture_job_description(self) -> None:
        """Give the AI answerer the job posting's text so answers can be specific to it."""
        if not self.answerer:
            return
        for sel in self.JOB_DESCRIPTION:
            loc = self.page.locator(sel)
            try:
                if loc.count() and loc.first.is_visible():
                    text = loc.first.inner_text().strip()
                    if len(text) > 200:
                        self.answerer.set_job(self.page.url, text)
                        return
            except PlaywrightError:
                continue

    # ------------------------------------------------------------------ submitting
    def ok_to_submit(self) -> bool:
        """Screenshot the filled form and decide whether to submit."""
        self.screenshot("review")
        drafted = self.answerer.drafted if self.answerer else []
        if drafted:
            print("\nThe AI drafted these answers -- check them in the browser before submitting:")
            for label, answer in drafted:
                print(f"  * {label}\n      {answer.replace(chr(10), chr(10) + '      ')}")
        must_review = bool(drafted) and (self.profile.get("ai") or {}).get("review_before_submit", True)
        if self.auto_submit and not must_review:
            return True
        answer = self.confirm(
            "\nReview the application in the browser. Type 'submit' to submit it, anything else to skip: "
        )
        if answer.strip().lower() != "submit":
            log.info("Not submitted.")
            return False
        return True

    def wait_for_confirmation(self, timeout_ms: int = 30_000) -> bool:
        try:
            self.page.get_by_text(SUCCESS_TEXT).first.wait_for(timeout=timeout_ms)
            return True
        except PlaywrightError:
            return False

    def finish_submit(self) -> bool:
        """After clicking submit: wait for the thank-you page, or ask the user to sort it out."""
        if not self.wait_for_confirmation():
            self.check_captcha()
            errors = self.visible_errors()
            self.pause(
                ("Submission was blocked:\n  - " + "\n  - ".join(errors) + "\n" if errors else "")
                + "Finish submitting in the browser if it isn't done (CAPTCHA, email code, missing fields)."
            )
            if not self.wait_for_confirmation(5000):
                log.warning("Didn't see a confirmation message; check the browser.")
                self.screenshot("after-submit")
                return False
        log.info("Application submitted!")
        self.screenshot("submitted")
        return True
