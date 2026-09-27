"""Lever applications (jobs.lever.co). No account is needed."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from job_autofill.base import BaseApplier
from job_autofill.forms import fill_fields, upload_file

log = logging.getLogger(__name__)

FORM = "#application-form, form[action*='apply']"


def is_lever_url(url: str) -> bool:
    return "lever.co" in urlparse(url).netloc.lower()


def posting_and_apply_urls(url: str) -> tuple[str, str]:
    base = url.split("?")[0].rstrip("/")
    if base.endswith("/apply"):
        base = base[: -len("/apply")]
    return base, base + "/apply"


class LeverApplier(BaseApplier):
    SITE = "lever"
    ERRORS = [".error-message", ".application-error", "[class*='error' i][role='alert']", "[role='alert']"]
    JOB_DESCRIPTION = [".posting-page", ".content", "main", "body"]

    def run(self, url: str) -> bool:
        posting, apply = posting_and_apply_urls(url)
        if self.answerer:
            log.info("Opening %s", posting)
            self.page.goto(posting, wait_until="domcontentloaded")
            self.settle()
            self.capture_job_description()
        log.info("Opening %s", apply)
        self.page.goto(apply, wait_until="domcontentloaded")
        self.settle(1000)
        if self.first_visible([FORM + " input", "input[name='name']"], 10_000) is None:
            self.pause("Couldn't find the application form. Open it in the browser.")

        resume = self.profile.get("resume")
        if resume and upload_file(self.page, r"resume|\bcv\b", resume):
            log.info("Uploaded resume (Lever may pre-fill some fields from it)")
            self.settle(2500)
        self.report_unresolved(fill_fields(self.page, self.rules, scope=FORM, answerer=self.answerer))
        self.check_captcha()
        if not self.ok_to_submit():
            return False
        button = self.first_visible(
            ["#btn-submit", self.page.get_by_role("button", name=re.compile(r"submit( application)?", re.I))],
            5000,
        )
        if button is None:
            self.pause("Couldn't find the Submit button. Click it yourself.")
        else:
            button.click()
        return self.finish_submit()
