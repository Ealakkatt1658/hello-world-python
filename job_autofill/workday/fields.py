"""Finding and filling Workday form widgets by their visible label.

Workday renders every question inside a ``data-automation-id="formField-..."``
container, but the widget inside differs: plain inputs, "Select One" button
dropdowns, searchable "prompt" pickers, radio groups, checkboxes and split
month/day/year date inputs. Rather than hard-coding per-tenant ids, we list
every visible field with its label, look the label up in the answer rules and
drive the widget according to its kind. That works across companies and across
the old and new Workday UI versions.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from job_autofill.matching import SKIP, Rule, as_choices, best_option, find_answer, truthy

log = logging.getLogger(__name__)

# Enumerates visible form fields. ``scope`` limits the search to one tagged
# group (e.g. "Work Experience 2"); without it, fields inside tagged groups are
# skipped so the generic pass doesn't touch them.
_LIST_FIELDS_JS = r"""
(scope) => {
  window.__afSeq = window.__afSeq || 0;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const CONTROL = 'input:not([type=hidden]), textarea, button[aria-haspopup="listbox"]';

  let containers = [...document.querySelectorAll('[data-automation-id^="formField"]')];
  // Fallback for pages/tenants that don't use formField wrappers.
  for (const lab of document.querySelectorAll('label, legend')) {
    if (lab.closest('[data-automation-id^="formField"]')) continue;
    const target = lab.htmlFor && document.getElementById(lab.htmlFor);
    if (target && target.type === 'radio') continue;
    let el = lab.parentElement;
    while (el && el !== document.body && !el.querySelector(CONTROL)) el = el.parentElement;
    if (el && el !== document.body) containers.push(el);
  }
  containers = [...new Set(containers)];
  containers = containers.filter(c => !containers.some(o => o !== c && c.contains(o)));

  const out = [];
  for (const c of containers) {
    if (!visible(c)) continue;
    const group = c.closest('[data-af-scope]');
    if (scope ? (!group || group.dataset.afScope !== scope) : group) continue;

    const radios = [...c.querySelectorAll('input[type=radio]')];
    const checks = [...c.querySelectorAll('input[type=checkbox]')];
    const dropdown = c.querySelector('button[aria-haspopup="listbox"]');
    const date = c.querySelector('[data-automation-id^="dateSection"], [data-automation-id="dateInputWrapper"]');
    const prompt = c.querySelector('[data-automation-id="multiselectInputContainer"], [data-uxi-widget-type="selectinput"], [data-uxi-widget-type="multiselect"], [data-automation-id="searchBox"]');
    const file = c.querySelector('input[type=file]');
    const textarea = c.querySelector('textarea');
    const text = c.querySelector('input:not([type]), input[type=text], input[type=email], input[type=tel], input[type=number], input[type=url]');

    const kind = file ? 'file' : date ? 'date' : dropdown ? 'dropdown' : prompt ? 'prompt'
      : radios.length ? 'radio' : checks.length > 1 ? 'checkboxes' : checks.length === 1 ? 'checkbox'
      : textarea ? 'textarea' : text ? 'text' : null;
    if (!kind || kind === 'file') continue;

    const optionLabel = inp => {
      const l = inp.id && document.querySelector(`label[for="${CSS.escape(inp.id)}"]`);
      return clean(l ? l.innerText : (inp.closest('label') || {}).innerText || inp.getAttribute('aria-label'));
    };
    const optionInputs = kind === 'radio' ? radios : kind === 'checkboxes' ? checks : [];
    const optionIds = new Set(optionInputs.map(i => i.id).filter(Boolean));
    const legend = c.querySelector('legend');
    const label = [...c.querySelectorAll('label')].find(l => !optionIds.has(l.htmlFor) && !optionInputs.some(i => l.contains(i)));
    let rawLabel = clean(legend && legend.innerText) || clean(label && label.innerText);
    if (!rawLabel) {
      const ctl = c.querySelector('[aria-label]');
      rawLabel = clean(ctl && ctl.getAttribute('aria-label'));
    }
    const required = /\*/.test(rawLabel) || !!c.querySelector('[aria-required="true"], [required]');

    let filled = false, current = '';
    if (kind === 'text' || kind === 'textarea') {
      current = (textarea || text).value; filled = !!current.trim();
    } else if (kind === 'dropdown') {
      current = clean(dropdown.innerText); filled = !!current && !/^select one$/i.test(current);
    } else if (kind === 'prompt') {
      const sel = [...c.querySelectorAll('[data-automation-id="selectedItem"], [data-automation-id="promptSelectionLabel"]')];
      current = sel.map(e => clean(e.innerText)).join('; '); filled = sel.length > 0;
    } else if (kind === 'date') {
      const y = c.querySelector('[data-automation-id="dateSectionYear-input"], [data-automation-id="dateSectionYear-display"]');
      current = y ? clean(y.value || y.innerText) : ''; filled = /\d{4}/.test(current);
    } else if (kind === 'radio' || kind === 'checkboxes') {
      const on = optionInputs.filter(i => i.checked); filled = on.length > 0; current = on.map(optionLabel).join('; ');
    } else if (kind === 'checkbox') {
      current = checks[0].checked ? 'checked' : '';
    }

    const id = String(++window.__afSeq);
    c.dataset.afId = id;
    out.push({
      id, kind, required, filled, current,
      label: clean(rawLabel.replace(/\*/g, '')),
      context: clean(c.innerText).slice(0, 400),
      options: optionInputs.map(optionLabel),
    });
  }
  return out;
}
"""

# Tags the group under the heading "<name> <n>" (e.g. "Work Experience 2").
_TAG_GROUP_JS = r"""
([name, n, scope]) => {
  const re = new RegExp('^\\s*' + name + '\\s+' + n + '\\s*$', 'i');
  const heads = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"],legend')]
    .filter(h => h.getClientRects().length && re.test(h.innerText));
  if (!heads.length) return false;
  let el = heads[0];
  while (el.parentElement && !el.querySelector('[data-automation-id^="formField"], input, textarea')) el = el.parentElement;
  el.dataset.afScope = scope;
  return true;
}
"""

# Tags the "Add"/"Add Another" button belonging to a section.
_TAG_ADD_BUTTON_JS = r"""
([name, token]) => {
  const lname = name.toLowerCase();
  const visible = el => el.getClientRects().length > 0;
  const buttons = [...document.querySelectorAll('button')].filter(visible);
  let hit = buttons.find(b => /^add/i.test((b.innerText || '').trim()) &&
    (b.getAttribute('aria-label') || '').toLowerCase().includes(lname));
  if (!hit) {
    let heading = '';
    for (const el of document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"],legend,button')) {
      if (el.tagName !== 'BUTTON') { if (visible(el)) heading = (el.innerText || '').trim().toLowerCase(); continue; }
      if (visible(el) && /^add( another)?$/i.test((el.innerText || '').trim()) && heading.startsWith(lname)) { hit = el; break; }
    }
  }
  if (!hit) return false;
  hit.dataset.afAdd = token;
  return true;
}
"""


@dataclass
class Field:
    id: str
    kind: str
    label: str
    required: bool
    filled: bool
    current: str
    context: str
    options: list[str]


def list_fields(page: Page, scope: Optional[str] = None) -> list[Field]:
    return [Field(**f) for f in page.evaluate(_LIST_FIELDS_JS, scope)]


def fill_fields(
    page: Page,
    rules: Iterable[Rule],
    scope: Optional[str] = None,
    overwrite: bool = False,
) -> list[Field]:
    """Fill every visible field whose label matches a rule.

    Returns required fields that are still empty afterwards (no answer, or the
    answer didn't fit any option) so the caller can ask the user about them.
    """
    rules = list(rules)
    unresolved: list[Field] = []
    for field in list_fields(page, scope):
        context = field.context if field.kind == "checkbox" else ""
        value = find_answer(rules, field.label, field.kind, context)
        if value is SKIP:
            continue
        if value is None:
            if field.required and not field.filled and field.kind != "checkbox":
                unresolved.append(field)
            continue
        if field.filled and not overwrite:
            continue
        try:
            ok = set_field(page, field, value)
        except PlaywrightError as exc:
            log.warning("Could not fill %r: %s", field.label, exc)
            ok = False
        if ok:
            log.info("  %-45s -> %s", field.label[:45], value)
        elif field.required:
            unresolved.append(field)
        # Selecting one option can reveal new fields (e.g. "State" after "Country").
        page.wait_for_timeout(150)
    return unresolved


def set_field(page: Page, field: Field, value: Any) -> bool:
    box = page.locator(f'[data-af-id="{field.id}"]')
    kind = field.kind
    if kind in ("text", "textarea"):
        return _set_text(box, value)
    if kind == "dropdown":
        return _set_dropdown(page, box, value)
    if kind == "prompt":
        return set_prompt(page, box, value)
    if kind == "date":
        return _set_date(box, value)
    if kind == "radio":
        return _set_choice(box, "radio", field.options, value)
    if kind == "checkboxes":
        return _set_choice(box, "checkbox", field.options, value)
    if kind == "checkbox":
        box.locator("input[type=checkbox]").first.set_checked(truthy(value), force=True)
        return True
    return False


def _set_text(box: Locator, value: Any) -> bool:
    inp = box.locator(
        "textarea, input:not([type]), input[type=text], input[type=email], input[type=tel], "
        "input[type=number], input[type=url]"
    ).first
    inp.fill(str(value))
    inp.blur()  # Workday validates/saves on blur
    return True


def _visible_options(page: Page, selector: str) -> Locator:
    return page.locator(f"{selector} >> visible=true")


def _pick_from_list(page: Page, options: Locator, wanted: Any, timeout: int = 5000) -> bool:
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


def _set_dropdown(page: Page, box: Locator, value: Any) -> bool:
    box.locator('button[aria-haspopup="listbox"]').first.click()
    ok = _pick_from_list(page, _visible_options(page, '[role="listbox"] [role="option"]'), value)
    if not ok:
        page.keyboard.press("Escape")
    return ok


def set_prompt(page: Page, box: Locator, value: Any) -> bool:
    """Searchable picker. ``"Job Board > LinkedIn"`` walks a category tree."""
    inp = box.locator("input").first
    options = _visible_options(page, '[data-automation-id="promptOption"], [role="option"]')
    choices = as_choices(value)
    path = [p.strip() for p in choices[0].split(">")] if len(choices) == 1 and ">" in choices[0] else None

    if path:
        inp.click()
        for step in path:
            if not _pick_from_list(page, options, step):
                page.keyboard.press("Escape")
                return False
            page.wait_for_timeout(500)
    else:
        ok = False
        for choice in choices:
            inp.click()
            inp.fill(choice)
            inp.press("Enter")
            page.wait_for_timeout(800)
            if _pick_from_list(page, options, choice, timeout=3000):
                ok = True
                break
            inp.fill("")
        if not ok:
            # Some pickers don't search; browse the top-level list instead.
            inp.click()
            if not _pick_from_list(page, options, choices, timeout=3000):
                page.keyboard.press("Escape")
                return False
    page.wait_for_timeout(300)
    page.keyboard.press("Tab")
    return True


def parse_date(value: Any) -> dict[str, str]:
    """'today', 'YYYY', 'MM/YYYY', 'MM/DD/YYYY' or 'YYYY-MM[-DD]' -> {'month','day','year'}."""
    if isinstance(value, (dt.date, dt.datetime)):
        d = value
        return {"month": f"{d.month:02d}", "day": f"{d.day:02d}", "year": str(d.year)}
    text = str(value).strip().lower()
    if text in ("today", "now"):
        return parse_date(dt.date.today())
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", text)
    if m:
        year, month, day = m.groups()
    else:
        parts = re.split(r"[/.\-]", text)
        if len(parts) == 2:
            (month, year), day = parts, None
        elif len(parts) == 3:
            month, day, year = parts
        else:
            raise ValueError(f"Unrecognised date {value!r}; use MM/YYYY, MM/DD/YYYY or YYYY")
    out = {"year": year}
    if month:
        out["month"] = f"{int(month):02d}"
    if day:
        out["day"] = f"{int(day):02d}"
    return out


def _set_date(box: Locator, value: Any) -> bool:
    parts = parse_date(value)
    wrote = False
    for section in ("Month", "Day", "Year"):
        inp = box.locator(f'input[data-automation-id="dateSection{section}-input"]')
        if not inp.count():
            continue
        digits = parts.get(section.lower()) or ("01" if section != "Year" else None)
        if not digits:
            return False
        inp.first.click(force=True)
        inp.first.press_sequentially(digits, delay=40)
        wrote = True
    if not wrote:  # single text input date (e.g. "MM/YYYY")
        inp = box.locator("input").first
        inp.fill("/".join(p for p in (parts.get("month"), parts.get("day"), parts["year"]) if p))
        wrote = True
    box.locator("input").first.blur()
    return wrote


def _set_choice(box: Locator, input_type: str, options: list[str], value: Any) -> bool:
    inputs = box.locator(f"input[type={input_type}]")
    wanted = as_choices(value)
    if input_type == "radio":
        idx = best_option(options, wanted)
        if idx is None:
            log.warning("  No option matching %r among %s", value, options)
            return False
        inputs.nth(idx).check(force=True)
        return True
    hit = False
    for choice in wanted:
        idx = best_option(options, choice)
        if idx is not None:
            inputs.nth(idx).set_checked(True, force=True)
            hit = True
    if not hit:
        log.warning("  No option matching %r among %s", value, options)
    return hit


def tag_group(page: Page, name_pattern: str, n: int, scope: str) -> bool:
    """Tag the group headed "<name> <n>"; ``name_pattern`` is a regex."""
    return bool(page.evaluate(_TAG_GROUP_JS, [name_pattern, n, scope]))


def click_add(page: Page, section: str) -> bool:
    token = f"add-{section}-{dt.datetime.now().timestamp()}"
    if not page.evaluate(_TAG_ADD_BUTTON_JS, [section, token]):
        return False
    page.locator(f'[data-af-add="{token}"]').click()
    page.wait_for_timeout(800)
    return True
