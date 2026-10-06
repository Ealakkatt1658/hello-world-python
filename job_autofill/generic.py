"""Applying on any job site that has no dedicated support (Ashby, iCIMS, SmartRecruiters,
Workable, Jobvite, BambooHR, company career pages, ...).

There is no site-specific knowledge here. Each loop looks at the page and decides what kind
of page it is: a job posting (click Apply), a sign-in form (sign in, or go create an
account), an account-creation form, or an application page. On an application page it fills
in every question by its label, then clicks Next/Continue, or Submit on the last page.
Whenever it can't tell what to do, it asks you to do that one step in the browser and
carries on from there.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator

from job_autofill.base import BaseApplier
from job_autofill.forms import fill_fields, list_fields, upload_file
from job_autofill.matching import normalize
from job_autofill.profile import account_email, account_password

log = logging.getLogger(__name__)

APPLY = re.compile(
    r"^(apply|apply now|apply here|apply online|apply today|apply for (this|the) (job|position|role|opportunity)|"
    r"apply to (this )?(job|position)|apply for this job online|start (your |my )?application|begin (your )?application|"
    r"i'?m interested)$"
)
MANUAL = re.compile(
    r"apply manually|continue manually|enter (my |your )?(details|information) manually|fill (it )?out (the )?(form|application)|"
    r"continue without|apply without"
)
NEXT = re.compile(
    r"^(next|next step|next page|continue|save (and|&) continue|save (and|&) next|proceed|continue to (the )?next step|"
    r"review|review (my |your )?application|continue to review)$"
)
SUBMIT = re.compile(
    r"^(submit|submit (my |your )?application|send (my |your )?application|submit and finish|finish|complete|"
    r"complete (my |your )?application|confirm and submit)$"
)
SIGN_IN = re.compile(r"^(sign in|log ?in|login|continue|next|submit)$")
CREATE_LINK = re.compile(
    r"^(create (an |your |new )?account|sign ?up|register|new (user|candidate)|don'?t have an account\??.*|"
    r"first time( here)?\??.*|new here\??.*)$"
)
CREATE_BUTTON = re.compile(r"^(create (an |my |your )?account|sign ?up|register|continue|next|submit)$")
# Third-party "apply with" buttons, and job boards that forbid automated tools.
NEVER_CLICK = re.compile(r"linkedin|indeed|seek|glassdoor|google|facebook|apple|microsoft|with (my )?profile")

_BUTTONS_JS = r"""
(scope) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const CHROME = 'header, nav, footer, [role="navigation"], [role="banner"], [role="contentinfo"]';
  const form = scope && document.querySelector(scope);
  const out = [];
  document.querySelectorAll('[data-af-btn]').forEach(e => delete e.dataset.afBtn);
  const els = document.querySelectorAll('button, input[type=submit], input[type=button], a, [role=button]');
  els.forEach((el, i) => {
    if (!visible(el) || el.disabled || el.getAttribute('aria-disabled') === 'true') return;
    const text = clean(el.innerText || el.value || el.getAttribute('aria-label') || el.title);
    if (!text || text.length > 60) return;
    el.dataset.afBtn = String(i);
    // A button that submits a form with fields in it (e.g. a final "Apply") is never the job
    // posting's "Apply" link.
    const f = el.closest('form');
    const submitsForm = el.type === 'submit' || !!(f && f.querySelectorAll('input:not([type=hidden]), textarea, select').length >= 2);
    out.push({ i, text, inForm: !!(form && form.contains(el)), inChrome: !!el.closest(CHROME), tag: el.tagName, submitsForm });
  });
  return out;
}
"""

_COUNT_JS = r"""
() => {
  const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const CHROME = 'header, nav, footer, [role="search"], [role="navigation"], [role="banner"], [role="contentinfo"]';
  const ok = el => visible(el) && !el.closest(CHROME);
  const inputs = [...document.querySelectorAll(
    'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=search]), textarea, select')].filter(ok);
  const files = [...document.querySelectorAll('input[type=file]')].filter(e => !e.closest(CHROME));
  return {
    fields: inputs.filter(e => e.type !== 'password').length,
    passwords: inputs.filter(e => e.type === 'password').length,
    email: inputs.some(e => e.type === 'email' || /e-?mail|user/i.test(e.name + ' ' + e.id + ' ' + (e.getAttribute('autocomplete') || ''))),
    files: files.length,
  };
}
"""

# Tags the <form> holding most of the visible fields, so filling stays inside the application.
_PICK_FORM_JS = r"""
() => {
  const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const CHROME = 'header, nav, footer, [role="search"], [role="navigation"], [role="banner"], [role="contentinfo"]';
  const fields = root => [...root.querySelectorAll('input:not([type=hidden]), textarea, select, [role=radiogroup]')]
    .filter(e => visible(e) && !e.closest(CHROME));
  document.querySelectorAll('[data-af-form]').forEach(f => delete f.dataset.afForm);
  const total = fields(document).length;
  let best = null, bestN = 0;
  for (const f of document.querySelectorAll('form')) {
    if (f.closest(CHROME) || f.matches(CHROME)) continue;
    const n = fields(f).length;
    if (n > bestN) { best = f; bestN = n; }
  }
  // Only narrow down to a form that holds most of the page's questions.
  if (!best || bestN < 2 || bestN * 2 < total) return false;
  best.dataset.afForm = '1';
  return true;
}
"""

# Which iframe (if any) holds the application, e.g. iCIMS or Jobvite embedded on a company site.
_FRAME_FIELDS_JS = r"""
() => [...document.querySelectorAll('input:not([type=hidden]), textarea, select')]
  .filter(e => e.getClientRects().length > 0).length
"""


class GenericApplier(BaseApplier):
    SITE = "site"
    ERRORS = [
        "[role='alert']",
        "[aria-live='assertive']",
        "[class*='error' i]:not(input):not(select):not(textarea):not(form)",
        "[class*='invalid-feedback' i]",
    ]
    JOB_DESCRIPTION = ["[class*='description' i]", "[class*='job-details' i]", "[class*='posting' i]", "main", "article", "body"]
    MAX_STEPS = 25

    def run(self, url: str) -> bool:
        log.info("Opening %s (no dedicated support for this site; using the general method)", url)
        self.page.goto(url, wait_until="domcontentloaded")
        self.settle(1000)
        self.dismiss_cookie_banner()
        self.enter_frame()
        self.capture_job_description()
        self.mark_before_submit()

        tried_sign_in = tried_sign_up = apply_clicks = 0
        for _ in range(self.MAX_STEPS):
            self.settle()
            self.dismiss_cookie_banner()
            self.enter_frame()
            self.check_captcha()
            if self._success_texts() - self._before_submit:
                log.info("The application looks submitted.")
                self.screenshot("submitted")
                return True

            counts = self.page.evaluate(_COUNT_JS)
            if counts["passwords"] >= 2:
                tried_sign_up += 1
                if tried_sign_up > 2:
                    self.pause("Creating an account didn't work. Please create it (or sign in) in the browser.")
                    tried_sign_up = 0
                else:
                    self.create_account()
                    tried_sign_in = 0  # some sites send you to sign in after creating the account
                continue
            if counts["passwords"] == 1 and counts["fields"] <= 3:
                tried_sign_in += 1
                if tried_sign_in == 1:
                    self.sign_in()
                elif not self.click_text(CREATE_LINK, prefer_links=True):
                    self.pause("Couldn't sign in. Sign in or create an account in the browser.")
                    tried_sign_in = 0
                continue
            fields = list_fields(self.page)
            size = len(fields) + counts["files"]
            looks_like_posting = size < 4 and not any(f.required for f in fields)
            if looks_like_posting and apply_clicks < 2 and self.click_apply():
                apply_clicks += 1  # looked like the job posting; Apply was clicked
                continue
            if size < 2 and not (counts["email"] and counts["fields"]) and self.find_action(None)[1] is None:
                # Neither questions nor a Next/Submit button (a review page has only the button).
                self.pause("Couldn't find the application form. Click Apply (or open the form) in the browser.")
                continue

            result = self.fill_page()
            if result is not None:
                return result
        self.pause("This is taking more steps than expected. Please finish the application in the browser.")
        return self.wait_for_confirmation(5000)

    # ------------------------------------------------------------------ pages
    def fill_page(self) -> Optional[bool]:
        """Fill the current application page, then go on. Returns True/False once submitted
        (or skipped), None to keep going."""
        scope = '[data-af-form="1"]' if self.page.evaluate(_PICK_FORM_JS) else None
        self.upload_documents()
        self.fill_passwords()
        self.report_unresolved(fill_fields(self.page, self.rules, scope=scope, answerer=self.answerer))

        button, kind = self.find_action(scope)
        if kind == "next":
            before = self.signature()
            log.info("Clicking %r", button.inner_text().strip() or "Next")
            button.click()
            self.wait_for_change(before)
            return None
        if kind == "submit":
            self.check_captcha()
            if not self.ok_to_submit():
                return False
            log.info("Clicking %r", button.inner_text().strip() or "Submit")
            button.click()
            return self.finish_submit()
        self.pause("Couldn't tell which button goes to the next step. Click Next/Continue (or Submit) in the browser.")
        return None

    def click_apply(self) -> bool:
        context = self.page.context
        before = list(context.pages)
        if not self.click_text(APPLY, skip_form_submit=True):
            return False
        self.page.wait_for_timeout(1500)
        new_tabs = [p for p in context.pages if p not in before]
        if new_tabs:
            log.info("The application opened in a new tab")
            self.page = new_tabs[-1]
            self.page.wait_for_load_state("domcontentloaded")
        self.settle()
        self.click_text(MANUAL)  # "Apply manually" vs "Apply with resume/LinkedIn"
        return True

    def sign_in(self) -> None:
        email = self.first_visible(
            ["input[type=email]", "input[autocomplete=email]", "input[autocomplete=username]",
             "input[name*=email i]", "input[id*=email i]", "input[name*=user i]", "input[id*=user i]"]
        )
        password = self.first_visible(["input[type=password]"])
        if email is not None and not email.input_value():
            email.fill(account_email(self.profile))
        if password is None:
            return
        password.fill(account_password(self.profile))
        log.info("Signing in as %s", account_email(self.profile))
        if not self.click_text(SIGN_IN, near=password):
            password.press("Enter")
        self.settle(1500)

    def create_account(self) -> None:
        log.info("Creating an account with %s", account_email(self.profile))
        self.fill_passwords()
        fill_fields(self.page, self.rules)
        self.check_captcha()
        if not self.click_text(CREATE_BUTTON):
            self.pause("Fill in anything missing and click the button to create the account.")
            return
        self.settle(2000)
        body = normalize(self.page.inner_text("body"))
        if re.search(r"verif|confirm your email|check your (email|inbox)|activation", body):
            self.pause(f"The site sent an email to {account_email(self.profile)}. Open it and click the link "
                       "(or type the code into the page).")

    def fill_passwords(self) -> None:
        boxes = self.page.locator("input[type=password]")
        for i in range(boxes.count()):
            box = boxes.nth(i)
            if box.is_visible() and not box.input_value():
                box.fill(account_password(self.profile))

    def upload_documents(self) -> None:
        resume = self.profile.get("resume")
        if not resume or not self.page.locator("input[type=file]").count():
            return
        name = resume.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name in normalize(self.page.inner_text("body")):
            return  # already attached on this page
        files = self.page.locator("input[type=file]")
        uploaded = upload_file(self.page, r"resume|\bcv\b|curriculum", resume)
        if not uploaded and files.count() == 1 and re.search(r"resume|\bcv\b", normalize(self.page.inner_text("body"))):
            files.first.set_input_files(resume)  # the page's only upload box, on a page about your resume
            uploaded = True
        if uploaded:
            log.info("Uploaded resume")
            self.settle(1500)
        cover = self.profile.get("cover_letter")
        if cover and upload_file(self.page, r"cover", cover):
            log.info("Uploaded cover letter")

    # ------------------------------------------------------------------ frames
    def enter_frame(self) -> None:
        """If the application lives in an iframe, open that iframe's page directly."""
        if self.page.evaluate(_FRAME_FIELDS_JS) >= 3:
            return
        best, best_n = None, 2
        for frame in self.page.frames[1:]:
            if not frame.url.startswith("http") or re.search(r"captcha|recaptcha|hcaptcha|doubleclick|analytics", frame.url):
                continue
            try:
                n = frame.evaluate(_FRAME_FIELDS_JS)
            except PlaywrightError:
                continue
            if n > best_n:
                best, best_n = frame, n
        if best is not None:
            log.info("The application is embedded in the page; opening it directly")
            self.page.goto(best.url, wait_until="domcontentloaded")
            self.settle(1000)

    # ------------------------------------------------------------------ buttons
    def _buttons(self, scope: Optional[str] = None) -> list[dict]:
        return [b for b in self.page.evaluate(_BUTTONS_JS, scope) if not NEVER_CLICK.search(b["text"].lower())]

    def _button(self, b: dict) -> Locator:
        return self.page.locator(f'[data-af-btn="{b["i"]}"]')

    def click_text(self, pattern: re.Pattern, prefer_links: bool = False, near: Optional[Locator] = None,
                   skip_form_submit: bool = False) -> bool:
        matches = [b for b in self._buttons() if pattern.search(normalize(b["text"]).rstrip(" ›»>→"))]
        if skip_form_submit:
            # "Apply" on the job posting: never a form's submit button, never the site menu's link.
            matches = [b for b in matches if not b["submitsForm"] and not b["inChrome"]]
        matches = [b for b in matches if not b["inChrome"]] or matches
        if prefer_links:
            matches.sort(key=lambda b: b["tag"] != "A")
        if near is not None:
            # Prefer the button in the same form as the password box.
            near.evaluate("e => { document.querySelectorAll('[data-af-near]').forEach(x => delete x.dataset.afNear);"
                          " if (e.form) e.form.dataset.afNear = '1'; }")
            inside = [b for b in matches if self._button(b).evaluate("e => !!e.closest('[data-af-near]')")]
            matches = inside or matches
        if not matches:
            return False
        try:
            self._button(matches[0]).click()
            return True
        except PlaywrightError as exc:
            log.debug("Click failed: %s", exc)
            return False

    def find_action(self, scope: Optional[str]) -> tuple[Optional[Locator], Optional[str]]:
        """The button that moves the application on: ('next' button, 'next') or ('submit' button, 'submit')."""
        buttons = [b for b in self._buttons(scope) if not b["inChrome"]]
        if scope:
            buttons.sort(key=lambda b: not b["inForm"])  # buttons inside the application form first
        text = lambda b: normalize(b["text"]).rstrip(" ›»>→")  # noqa: E731
        for b in buttons:
            if NEXT.search(text(b)):
                return self._button(b), "next"
        for b in buttons:
            if SUBMIT.search(text(b)):
                return self._button(b), "submit"
        # "Apply" as the final button only when it belongs to the form (not a header "Apply" link).
        for b in buttons:
            if APPLY.search(text(b)) and (b["inForm"] or b["tag"] in ("BUTTON", "INPUT")):
                return self._button(b), "submit"
        return None, None

    # ------------------------------------------------------------------ progress
    def signature(self) -> tuple:
        try:
            return (self.page.url, tuple(f.label for f in list_fields(self.page)))
        except PlaywrightError:
            return (self.page.url, ())

    def wait_for_change(self, before: tuple, timeout_ms: int = 15_000) -> None:
        waited = 0
        while waited < timeout_ms:
            self.page.wait_for_timeout(500)
            waited += 500
            if self.signature() != before:
                return
            if waited >= 2000 and self.visible_errors():
                break
        errors = self.visible_errors()
        self.pause(
            ("The site said:\n  - " + "\n  - ".join(errors[:8]) + "\n" if errors else "")
            + "The page didn't move on. Fix anything marked in red, then click Next/Continue yourself."
        )
