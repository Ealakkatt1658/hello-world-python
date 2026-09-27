"""Site-independent part of filling a form: match each field to an answer and set it.

Each site module supplies two functions: one that lists the visible fields on
the page, and one that sets a single field. This module runs the loop, asks the
optional AI answerer when no rule matches, and repeats the pass so it also
fills questions that only appear after an earlier answer (for example "Race"
showing up after "Hispanic or Latino? No").
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Protocol

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from job_autofill.matching import SKIP, Rule, best_option, find_answer

log = logging.getLogger(__name__)

# Questions the AI must never answer. These come only from your profile, or from you.
SENSITIVE = re.compile(
    r"gender|\bsex\b|\brace\b|ethnic|hispanic|latin[oax]|veteran|military|disabilit|sexual orientation|"
    r"pronoun|transgender|signature|consent|acknowledg|certify|attest|convict|criminal|felony|"
    r"social security|\bssn\b|date of birth|birth ?date|password",
    re.I,
)


@dataclass
class Field:
    id: str
    kind: str
    label: str
    required: bool
    filled: bool
    current: str = ""
    context: str = ""
    options: list[str] = field(default_factory=list)


class Answerer(Protocol):
    def __call__(self, field: Field, options: list[str]) -> Optional[str]: ...


def fill_loop(
    list_fields: Callable[[], list[Field]],
    set_field: Callable[[Field, Any], bool],
    rules: Iterable[Rule],
    answerer: Optional[Answerer] = None,
    read_options: Optional[Callable[[Field], list[str]]] = None,
    overwrite: bool = False,
    passes: int = 3,
) -> list[Field]:
    """Fill every field we have an answer for; return required fields still empty."""
    rules = list(rules)
    done: set[tuple[str, str]] = set()  # (label, kind) already handled, so later passes skip them
    unresolved: list[Field] = []
    for _ in range(passes):
        progressed = False
        unresolved = []
        for f in list_fields():
            key = (f.label, f.kind)
            if key in done and f.kind == "checkbox":
                continue
            context = f.context if f.kind == "checkbox" else ""
            value = find_answer(rules, f.label, f.kind, context)
            if value is SKIP:
                continue
            ai = False
            if value is None and answerer and not f.filled and ai_may_answer(f):
                options = f.options
                if not options and read_options and f.kind in ("dropdown", "select", "combobox"):
                    options = read_options(f)
                value = answerer(f, options)
                ai = value is not None
            if value is None:
                if f.required and not f.filled and f.kind != "checkbox":
                    unresolved.append(f)
                continue
            if f.filled and not overwrite:
                continue
            try:
                ok = set_field(f, value)
            except PlaywrightError as exc:
                log.warning("Could not fill %r: %s", f.label, exc)
                ok = False
            if ok:
                done.add(key)
                progressed = True
                log.info("  %-45s -> %s%s", f.label[:45], _short(value), "  [AI]" if ai else "")
            elif f.required:
                unresolved.append(f)
        if not progressed:
            break
    return unresolved


def ai_may_answer(f: Field) -> bool:
    if f.kind in ("checkbox", "date"):
        return False
    if SENSITIVE.search(f.label):
        return False
    # Optional short fields are left alone; optional essay questions are worth answering.
    return f.required or f.kind == "textarea"


def _short(value: Any) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= 70 else text[:67] + "..."


def visible_options(page: Page, selector: str) -> Locator:
    return page.locator(f"{selector} >> visible=true")


def pick_from_list(page: Page, options: Locator, wanted: Any, timeout: int = 5000) -> bool:
    """Wait for a popup list and click the option that best matches ``wanted``."""
    try:
        options.first.wait_for(state="visible", timeout=timeout)
    except PlaywrightError:
        return False
    texts = options.all_inner_texts()
    idx = best_option(texts, wanted)
    if idx is None:
        log.warning("  No option matching %r among %s", wanted, texts[:15])
        return False
    options.nth(idx).click()
    return True
