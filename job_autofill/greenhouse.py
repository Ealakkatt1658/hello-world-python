"""Greenhouse applications (boards.greenhouse.io, job-boards.greenhouse.io and embedded boards).

No account is needed: the form is on the job page itself.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from job_autofill.base import BaseApplier
from job_autofill.forms import fill_fields, upload_file

log = logging.getLogger(__name__)

FORM = "#application-form, #application_form, form[action*='application']"


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
        self.follow_embed()
        self.capture_job_description()
        self.open_form()

        self.upload_documents()
        self.report_unresolved(fill_fields(self.page, self.rules, scope=FORM, answerer=self.answerer))
        self.check_captcha()
        if not self.ok_to_submit():
            return False
        self.click_submit()
        code = self.first_visible(["input[id*='security' i]", "input[name*='security' i]"], 8000)
        if code is not None:
            self.pause(f"Greenhouse emailed a security code to {self.profile['personal']['email']}. Enter it and submit.")
        return self.finish_submit()

    def follow_embed(self) -> None:
        """Company career pages embed the Greenhouse form in an iframe; open it directly."""
        frame = self.page.locator("iframe#grnhse_iframe, iframe[src*='greenhouse.io']")
        if frame.count():
            src = frame.first.get_attribute("src")
            if src:
                log.info("Opening embedded Greenhouse form")
                self.page.goto(src, wait_until="domcontentloaded")
                self.settle(1000)

    def open_form(self) -> None:
        if self.first_visible([FORM + " input"]) is not None:
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
        if self.first_visible([FORM + " input", "input[type=text]"], 10_000) is None:
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
                FORM.split(",")[0] + " button[type=submit]",
                "button[type=submit]",
                "input[type=submit]",
            ],
            5000,
        )
        if button is None:
            self.pause("Couldn't find the Submit button. Click it yourself.")
        else:
            button.click()
