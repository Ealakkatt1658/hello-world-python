"""Greenhouse applications (boards.greenhouse.io, job-boards.greenhouse.io and embedded boards).

No account is needed: the form is on the job page itself.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

from job_autofill.base import BaseApplier
from job_autofill.forms import fill_fields, upload_file

log = logging.getLogger(__name__)

FORMS = ["#application-form", "#application_form", "form[action*='application']"]
FORM = ", ".join(FORMS)
SECURITY_CODE = ["input[id*='security' i]", "input[name*='security' i]", "input[aria-label*='security code' i]"]


def is_greenhouse_url(url: str) -> bool:
    return "greenhouse.io" in urlparse(url).netloc.lower() or "gh_jid=" in url


class GreenhouseApplier(BaseApplier):
    SITE = "greenhouse"
    ERRORS = [
        ".helper-text--error",
        ".field-error-msg",
        "#error_message",
        "[class*='error-message' i]",
        "[role='alert']",
    ]
    JOB_DESCRIPTION = [".job__description", "#content", ".job-post", "main", "body"]

    def run(self, url: str) -> bool:
        log.info("Opening %s", url)
        self.page.goto(url, wait_until="domcontentloaded")
        self.settle(1000)
        self.dismiss_cookie_banner()
        self.follow_embed(wait="gh_jid=" in url)
        self.capture_job_description()
        self.open_form()

        self.upload_documents()
        self.report_unresolved(fill_fields(self.page, self.rules, scope=FORM, answerer=self.answerer))
        self.check_captcha()
        if not self.ok_to_submit():
            return False
        self.click_submit()
        # Greenhouse sometimes emails a code that has to be typed in before it accepts the application.
        for _ in range(20):
            if self.wait_for_confirmation(500):
                break
            if self.first_visible(SECURITY_CODE):
                self.pause(
                    f"Greenhouse emailed a security code to {self.profile['personal']['email']}. "
                    "Type it into the page and click submit."
                )
                break
        return self.finish_submit()

    def follow_embed(self, wait: bool = False) -> None:
        """Company career pages embed the Greenhouse form in an iframe; open it directly."""
        frame = self.page.locator("iframe#grnhse_iframe, iframe[src*='greenhouse.io']")
        if wait:
            try:
                frame.first.wait_for(state="attached", timeout=10_000)
            except PlaywrightError:
                log.info("No embedded Greenhouse form found on this page")
        if frame.count():
            src = frame.first.get_attribute("src")
            if src:
                log.info("Opening embedded Greenhouse form")
                self.page.goto(src, wait_until="domcontentloaded")
                self.settle(1000)

    def open_form(self) -> None:
        form_inputs = [f"{f} input:not([type=hidden])" for f in FORMS]
        if self.first_visible(form_inputs) is not None:
            return
        apply = self.first_visible(
            [
                self.page.get_by_role("button", name=re.compile(r"^\s*apply( for this job| now)?\s*$", re.I)),
                self.page.get_by_role("link", name=re.compile(r"^\s*apply( for this job| now)?\s*$", re.I)),
            ],
            5000,
        )
        if apply is not None:
            apply.click()
            self.settle()
        if self.first_visible(form_inputs + ["input[type=text]"], 10_000) is None:
            self.pause("Couldn't find the application form. Open it in the browser.")

    def upload_documents(self) -> None:
        resume = self.profile.get("resume")
        if resume and upload_file(self.page, r"resume|\bcv\b", resume):
            log.info("Uploaded resume")
            self.settle(1500)
        cover = self.profile.get("cover_letter")
        if cover and upload_file(self.page, r"cover", cover):
            log.info("Uploaded cover letter")
            self.settle(1000)

    def click_submit(self) -> None:
        button = self.first_visible(
            [
                "#submit_app",
                self.page.get_by_role("button", name=re.compile(r"submit( application)?", re.I)),
                *(f"{f} button[type=submit]" for f in FORMS),
                "button[type=submit]",
                "input[type=submit]",
            ],
            5000,
        )
        if button is None:
            self.pause("Couldn't find the Submit button. Click it yourself.")
        else:
            button.click()
