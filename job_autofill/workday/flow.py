"""End-to-end Workday application: open job -> sign in / create account -> fill every step -> submit."""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator

from job_autofill.base import BaseApplier
from job_autofill.filling import Field
from job_autofill.matching import normalize
from job_autofill.profile import account_email, account_password, education_rules, website_rules, work_rules
from job_autofill.workday.fields import click_add, fill_fields, list_fields, set_prompt, tag_group

log = logging.getLogger(__name__)

APP_MARKERS = [
    '[data-automation-id="progressBar"]',
    '[data-automation-id="bottom-navigation-next-button"]',
    '[data-automation-id="pageFooterNextButton"]',
]
EMAIL = ['input[data-automation-id="email"]', 'input[type="email"]', 'input[autocomplete="email"]']
PASSWORD = ['input[data-automation-id="password"]', 'input[type="password"]']
VERIFY_PASSWORD = ['input[data-automation-id="verifyPassword"]']

_STEP_JS = r"""
() => {
  const active = document.querySelector('[data-automation-id="progressBarActiveStep"]')
    || document.querySelector('[data-automation-id="progressBar"] [aria-current="step"]');
  const header = document.querySelector('[data-automation-id="pageHeader"], main h2, h2');
  return [(active && active.innerText) || '', (header && header.innerText) || ''].join(' | ');
}
"""


def is_workday_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "myworkdayjobs.com" in host or "myworkday.com" in host or "workday" in host


class WorkdayApplier(BaseApplier):
    SITE = "workday"
    ERRORS = [
        '[data-automation-id="errorMessage"]',
        '[data-automation-id="errorBanner"]',
        '[data-automation-id="inputAlert"]',
        '[role="alert"]',
    ]
    JOB_DESCRIPTION = ['[data-automation-id="jobPostingDescription"]', '[data-automation-id="job-posting-details"]', "main"]

    def in_application(self, timeout_ms: int = 0) -> bool:
        return self.first_visible(APP_MARKERS, timeout_ms) is not None

    # ------------------------------------------------------------------ flow
    def run(self, url: str) -> bool:
        """Returns True if the application was submitted."""
        log.info("Opening %s", url)
        self.open_application(url)
        self.authenticate()
        if not self.in_application(10_000):
            # Some tenants send you back to the job posting after signing in.
            self.open_application(url)
        if not self.in_application(15_000):
            self.pause("Couldn't reach the application form. Get to the first application step (My Information).")
        self.fill_all_steps()
        return self.submit()

    def open_application(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")
        self.settle(1000)
        if self.in_application():
            return
        self.capture_job_description()
        apply = self.first_visible(
            [
                '[data-automation-id="adventureButton"]',
                self.page.get_by_role("link", name=re.compile(r"^\s*apply( now)?\s*$", re.I)),
                self.page.get_by_role("button", name=re.compile(r"^\s*apply( now)?\s*$", re.I)),
            ],
            15_000,
        )
        if apply is None:
            log.info("No Apply button found; assuming we're already past the job posting.")
            return
        apply.click()
        self.settle()
        manual = self.first_visible(
            [
                '[data-automation-id="applyManually"]',
                self.page.get_by_role("button", name=re.compile("apply manually", re.I)),
                self.page.get_by_role("link", name=re.compile("apply manually", re.I)),
            ],
            8000,
        )
        if manual is not None:
            manual.click()
            self.settle()

    # ------------------------------------------------------------------ auth
    def authenticate(self) -> None:
        if self.in_application(3000):
            log.info("Already signed in.")
            return
        if self.first_visible(EMAIL, 15_000) is None:
            if not self.in_application():
                self.pause("Couldn't find the sign-in form. Sign in (or create an account) manually.")
            return
        self.check_captcha()

        if self.first_visible(VERIFY_PASSWORD):
            # Tenant opened on "Create Account"; try signing in first.
            link = self.first_visible(
                ['[data-automation-id="signInLink"]', self.page.get_by_role("button", name=re.compile(r"^sign in$", re.I))]
            )
            if link:
                link.click()
                self.settle()

        if self.sign_in():
            return
        log.info("Sign-in failed; creating a new account.")
        if self.create_account():
            return
        self.pause("Couldn't sign in or create an account automatically. Please finish signing in.")

    def _click_submit(self, label: str, automation_id: str) -> None:
        button = self.first_visible(
            [
                f'[data-automation-id="click_filter"][aria-label="{label}"]',
                f'[data-automation-id="{automation_id}"]',
                self.page.get_by_role("button", name=re.compile(rf"^{label}$", re.I)),
            ],
            5000,
        )
        if button is None:
            raise PlaywrightError(f"Couldn't find the {label} button")
        button.click()

    def _wait_auth_result(self, timeout_ms: int = 20_000) -> bool:
        deadline = dt.datetime.now() + dt.timedelta(milliseconds=timeout_ms)
        while dt.datetime.now() < deadline:
            if self.in_application():
                return True
            if self.first_visible(EMAIL) is None and self.first_visible(PASSWORD) is None:
                self.settle()
                return True  # left the auth page (e.g. back on the job posting)
            if self.visible_errors():
                return False
            self.page.wait_for_timeout(500)
        return False

    def sign_in(self) -> bool:
        email, password = account_email(self.profile), account_password(self.profile)
        self.first_visible(EMAIL, 5000).fill(email)
        self.first_visible(PASSWORD, 5000).fill(password)
        try:
            self._click_submit("Sign In", "signInSubmitButton")
        except PlaywrightError as exc:
            log.warning("%s", exc)
            return False
        ok = self._wait_auth_result()
        if ok:
            log.info("Signed in as %s", email)
        else:
            log.info("Sign-in rejected: %s", "; ".join(self.visible_errors()) or "no response")
        return ok

    def create_account(self) -> bool:
        if not self.first_visible(VERIFY_PASSWORD):
            link = self.first_visible(
                [
                    '[data-automation-id="createAccountLink"]',
                    self.page.get_by_role("button", name=re.compile(r"^create account$", re.I)),
                    self.page.get_by_role("link", name=re.compile(r"^create account$", re.I)),
                ],
                5000,
            )
            if link is None:
                return False
            link.click()
            self.settle()
        email, password = account_email(self.profile), account_password(self.profile)
        self.first_visible(EMAIL, 5000).fill(email)
        self.first_visible(PASSWORD, 5000).fill(password)
        verify = self.first_visible(VERIFY_PASSWORD, 5000) or self.page.locator('input[type="password"]').nth(1)
        verify.fill(password)
        consent = self.first_visible(
            ['input[data-automation-id="createAccountCheckbox"]', 'form input[type="checkbox"]']
        )
        if consent is not None:
            consent.set_checked(True, force=True)
        self.check_captcha()
        try:
            self._click_submit("Create Account", "createAccountSubmitButton")
        except PlaywrightError as exc:
            log.warning("%s", exc)
            return False
        if self._wait_auth_result(25_000):
            log.info("Created account for %s", email)
            return True

        body = normalize(self.page.inner_text("body"))
        if "verif" in body:
            self.pause(
                f"Workday sent a verification email to {email}. Click the link in it "
                "(it can open in any browser)."
            )
            if self.first_visible(EMAIL, 5000):
                return self.sign_in()
            return True
        log.warning("Account creation failed: %s", "; ".join(self.visible_errors()) or "unknown reason")
        return False

    # ------------------------------------------------------------------ steps
    def current_step(self) -> str:
        return normalize(self.page.evaluate(_STEP_JS))

    def next_button(self) -> Optional[Locator]:
        return self.first_visible(
            [
                '[data-automation-id="bottom-navigation-next-button"]',
                '[data-automation-id="pageFooterNextButton"]',
                self.page.get_by_role("button", name=re.compile(r"^(save and continue|next|continue|submit)$", re.I)),
            ],
            5000,
        )

    def on_review_page(self) -> bool:
        if "review" in self.current_step():
            return True
        button = self.next_button()
        return button is not None and normalize(button.inner_text()) == "submit"

    def fill_all_steps(self, max_steps: int = 25) -> None:
        for _ in range(max_steps):
            self.settle()
            if self.on_review_page():
                log.info("Reached the Review page.")
                return
            step = self.current_step()
            log.info("Step: %s", step)
            self.report_unresolved(self.fill_step(step))
            self.go_next(step)
        self.pause("Too many steps; please finish the application manually.")

    def go_next(self, step: str) -> None:
        button = self.next_button()
        if button is None:
            self.pause("Couldn't find the 'Save and Continue' button. Click it for me.")
            return
        button.click()
        deadline = dt.datetime.now() + dt.timedelta(seconds=20)
        while dt.datetime.now() < deadline:
            self.page.wait_for_timeout(500)
            if self.current_step() != step:
                return
            errors = self.visible_errors()
            if errors:
                self.pause("Workday rejected this page:\n  - " + "\n  - ".join(errors) + "\nFix these fields.")
                return

    def fill_step(self, step: str) -> list[Field]:
        unresolved: list[Field] = []
        self.upload_resume()
        if "experience" in step:
            unresolved += self.fill_experience()
        unresolved += fill_fields(self.page, self.rules, answerer=self.answerer)
        return unresolved

    def upload_resume(self) -> None:
        resume = self.profile.get("resume")
        if not resume:
            return
        file_input = self.page.locator('input[type="file"]')
        if not file_input.count():
            return
        body = normalize(self.page.inner_text("body"))
        name = Path(resume).name
        if name.lower() in body:
            return  # already attached
        if not re.search(r"\b(resume|résumé|cv)\b", body):
            return  # some other attachment (e.g. transcript)
        log.info("Uploading resume %s", name)
        file_input.first.set_input_files(resume)
        try:
            self.page.get_by_text(name).first.wait_for(timeout=30_000)
        except PlaywrightError:
            log.warning("Didn't see the resume appear after upload; continuing.")
        self.settle()

    def fill_experience(self) -> list[Field]:
        unresolved: list[Field] = []
        sections = [
            ("Work Experience", re.escape("Work Experience"), self.profile.get("work_experience") or [], work_rules),
            ("Education", re.escape("Education"), self.profile.get("education") or [], education_rules),
            ("Websites", "Websites?", self.website_urls(), website_rules),
        ]
        for section, pattern, items, make_rules in sections:
            for n, item in enumerate(items, 1):
                scope = f"{section}-{n}"
                if not tag_group(self.page, pattern, n, scope):
                    if not click_add(self.page, section) or not tag_group(self.page, pattern, n, scope):
                        log.warning("Couldn't add %s %d; skipping the rest of that section.", section, n)
                        break
                log.info("%s %d", section, n)
                unresolved += fill_fields(self.page, make_rules(item), scope=scope)
        self.add_skills()
        return unresolved

    def website_urls(self) -> list[str]:
        links = self.profile.get("links") or {}
        urls = [links.get("github"), links.get("website"), *(links.get("other") or [])]
        return [u for u in urls if u]

    def add_skills(self) -> None:
        skills = self.profile.get("skills") or []
        if not skills:
            return
        field = next((f for f in list_fields(self.page) if f.kind == "prompt" and "skill" in f.label.lower()), None)
        if field is None:
            return
        have = normalize(field.current)
        box = self.page.locator(f'[data-af-id="{field.id}"]')
        for skill in skills:
            if normalize(skill) in have:
                continue
            try:
                if set_prompt(self.page, box, skill):
                    log.info("  Skill -> %s", skill)
            except PlaywrightError as exc:
                log.warning("  Couldn't add skill %s: %s", skill, exc)

    # ------------------------------------------------------------------ submit
    def submit(self) -> bool:
        if not self.ok_to_submit():
            return False
        button = self.next_button()
        if button is None or normalize(button.inner_text()) != "submit":
            self.pause("Couldn't find the Submit button. Click Submit yourself.")
        else:
            button.click()
        return self.finish_submit()
