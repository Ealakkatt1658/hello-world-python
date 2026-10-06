"""Filling ordinary HTML application forms (Greenhouse, Lever, ...).

Unlike Workday these are one long page of standard inputs, native <select>s,
radio/checkbox groups and React-Select style comboboxes. Each field's question
is found from its <label>, aria-labelledby, or the nearest label-like element
around it.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from job_autofill.filling import Answerer, Field, fill_loop, pick_from_list, visible_options
from job_autofill.matching import Rule, as_choices, best_option, checkbox_state, parse_date, to_text

log = logging.getLogger(__name__)

_LIST_FIELDS_JS = r"""
(scope) => {
  window.__afSeq = window.__afSeq || 0;
  const clean = s => (s || '').replace(/[✱∗*]/g, ' *').replace(/\s+/g, ' ').trim();
  const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const root = (scope && document.querySelector(scope)) || document;
  // Custom-styled radios/checkboxes hide the real input, so for those the label counts. Other
  // hidden inputs are skipped on purpose: some forms hide a "honeypot" field to catch bots.
  const shown = el => visible(el) || (['radio', 'checkbox'].includes(el.type) &&
    ((el.labels && [...el.labels].some(visible)) || visible(el.parentElement)));

  // The site's own search bar, menus and footer are never part of the application.
  const CHROME = 'header, nav, footer, [role="search"], [role="navigation"], [role="banner"], [role="contentinfo"]';
  const inChrome = el => !!el.closest(CHROME);

  const LABELISH = 'legend, label, .application-label, [class*="label" i], [class*="question" i], [class*="title" i]';
  const textOf = el => clean([...el.childNodes].map(n => n.nodeType === 3 ? n.textContent : (n.matches && n.matches('input, select, textarea, [role=listbox], ul[class*="menu" i]') ? '' : n.innerText || n.textContent)).join(' '));

  // Nearest label-like element around `els` that is not itself an option label. It only looks
  // inside containers that hold this question alone: once a container also holds other
  // questions' inputs, any label found there could belong to one of them.
  const CONTROLS = 'input:not([type=hidden]), select, textarea';
  const questionFor = (els, maxUp = 5) => {
    let p = els[0].parentElement;
    while (p && !els.every(e => p.contains(e))) p = p.parentElement;
    for (let i = 0; i < maxUp && p && p !== document.body; i++, p = p.parentElement) {
      const others = [...p.querySelectorAll(CONTROLS)].filter(c => !els.includes(c));
      if (others.length) break;
      const cands = [...p.querySelectorAll(LABELISH)].filter(c =>
        c.tagName !== 'BUTTON' && !c.querySelector('input, select, textarea, button') && !els.some(e => e.labels && [...e.labels].includes(c)) &&
        !els.some(e => c.contains(e)));
      const hit = cands.find(c => clean(c.innerText));
      if (hit) return clean(hit.innerText);
    }
    return '';
  };

  const labelFor = el => {
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = clean(by.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean).map(e => e.innerText).join(' '));
      if (t) return t;
    }
    if (el.labels && el.labels.length) {
      const t = textOf(el.labels[0]);
      if (t) return t;
    }
    return questionFor([el]) || clean(el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '');
  };

  const out = [];
  const seen = new Set();
  const tag = (els) => { const id = String(++window.__afSeq); els.forEach(e => e.dataset.afId = id); return id; };

  for (const el of root.querySelectorAll('input, textarea, select')) {
    if (seen.has(el) || el.disabled) continue;
    const type = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'file', 'image', 'reset', 'password'].includes(type)) continue;
    if (el.closest('[data-af-skip]') || inChrome(el)) continue;

    let kind, label, options = [], filled = false, current = '', context = '', els = [el], required = false;
    if (type === 'radio' || type === 'checkbox') {
      els = el.name ? [...root.querySelectorAll(`input[type="${type}"][name="${CSS.escape(el.name)}"]`)] : [el];
      els.forEach(e => seen.add(e));
      if (!els.some(shown)) continue;
      const optLabel = i => clean((i.labels && i.labels[0] && i.labels[0].innerText) || i.getAttribute('aria-label') || i.value);
      const question = questionFor(els);
      if (type === 'checkbox' && els.length === 1) {
        kind = 'checkbox';
        label = optLabel(el) || question;
        context = clean(question + ' ' + label);
        current = el.checked ? 'checked' : '';
      } else {
        kind = type === 'radio' ? 'radio' : 'checkboxes';
        label = question;
        options = els.map(optLabel);
        const on = els.filter(e => e.checked);
        filled = on.length > 0; current = on.map(optLabel).join('; ');
      }
      required = els.some(e => e.required || e.getAttribute('aria-required') === 'true');
    } else if (el.tagName === 'SELECT') {
      if (!shown(el)) continue;
      kind = 'select';
      label = labelFor(el);
      options = [...el.options].map(o => clean(o.text));
      const sel = el.selectedIndex >= 0 ? clean(el.options[el.selectedIndex].text) : '';
      filled = !!el.value && !!sel && !/^(select|choose|please|--|—)/i.test(sel);
      current = filled ? sel : '';
      required = el.required || el.getAttribute('aria-required') === 'true';
    } else if (el.getAttribute('role') === 'combobox' || el.closest('[class*="select__control"]')) {
      if (!shown(el)) continue;
      kind = 'combobox';
      label = labelFor(el);
      const control = el.closest('[class*="select__control"], [class*="control" i]') || el.parentElement.parentElement;
      const value = control && control.querySelector('[class*="single-value" i], [class*="multi-value__label" i]');
      current = value ? clean(value.innerText) : '';
      filled = !!current;
      required = el.required || el.getAttribute('aria-required') === 'true';
    } else {
      if (!shown(el)) continue;
      kind = el.tagName === 'TEXTAREA' ? 'textarea' : type === 'date' ? 'date' : 'text';
      label = labelFor(el);
      current = el.value || '';
      filled = !!current.trim();
      required = el.required || el.getAttribute('aria-required') === 'true';
    }
    required = required || /\*/.test(label);
    out.push({
      id: tag(els), kind, required, filled, current, options,
      label: clean(label.replace(/\*/g, ' ')).replace(/\(\s*(required|optional)\s*\)$/i, '').trim(),
      context,
    });
  }

  const pushGroup = (kind, opts, label, required, isOn, optText) => {
    const on = opts.filter(isOn);
    out.push({
      id: tag(opts), kind, required: required || /\*/.test(label), filled: on.length > 0,
      current: on.map(optText).join('; '), options: opts.map(optText),
      label: clean(label.replace(/\*/g, ' ')), context: '',
    });
  };
  const byIds = ids => clean((ids || '').split(/\s+/).map(id => document.getElementById(id)).filter(Boolean).map(e => e.innerText).join(' '));

  // Custom radio groups built from ARIA roles instead of <input type=radio>.
  for (const g of root.querySelectorAll('[role="radiogroup"]')) {
    if (g.querySelector('input[type=radio]') || inChrome(g) || !visible(g)) continue;
    const opts = [...g.querySelectorAll('[role="radio"]')];
    if (!opts.length) continue;
    const label = byIds(g.getAttribute('aria-labelledby')) || clean(g.getAttribute('aria-label')) || questionFor(opts);
    pushGroup('radio', opts, label, g.getAttribute('aria-required') === 'true',
      o => o.getAttribute('aria-checked') === 'true', o => clean(o.getAttribute('aria-label') || o.innerText));
  }

  // Yes/No answered with a pair of buttons instead of radio buttons (e.g. Ashby).
  const groups = new Set();
  for (const b of root.querySelectorAll('button')) {
    const p = b.parentElement;
    if (!p || groups.has(p) || inChrome(p) || !visible(p)) continue;
    groups.add(p);
    const btns = [...p.children].filter(c => c.tagName === 'BUTTON' && visible(c));
    const texts = btns.map(x => clean(x.innerText).toLowerCase());
    const yesNo = texts.includes('yes') && texts.includes('no');
    if (btns.length < 2 || btns.length > 6 || !(yesNo || btns.every(x => x.hasAttribute('aria-pressed')))) continue;
    pushGroup('buttons', btns, questionFor(btns), false,
      x => x.getAttribute('aria-pressed') === 'true' || /(^|[^a-z])(active|selected|checked)/i.test(String(x.className)),
      x => clean(x.innerText));
  }
  return out;
}
"""


def list_fields(page: Page, scope: Optional[str] = None) -> list[Field]:
    return [Field(**f) for f in page.evaluate(_LIST_FIELDS_JS, scope)]


def fill_fields(
    page: Page,
    rules: Iterable[Rule],
    scope: Optional[str] = None,
    answerer: Optional[Answerer] = None,
    overwrite: bool = False,
) -> list[Field]:
    def set_one(field: Field, value: Any) -> bool:
        ok = set_field(page, field, value)
        page.wait_for_timeout(100)
        return ok

    return fill_loop(
        lambda: list_fields(page, scope),
        set_one,
        rules,
        answerer=answerer,
        read_options=lambda f: read_combobox_options(page, f),
        overwrite=overwrite,
    )


def set_field(page: Page, field: Field, value: Any) -> bool:
    loc = page.locator(f'[data-af-id="{field.id}"]')
    kind = field.kind
    if kind in ("text", "textarea"):
        el = loc.first
        el.fill(to_text(value))
        el.blur()
        return True
    if kind == "date":
        d = parse_date(value)
        loc.first.fill(f"{d['year']}-{d.get('month', '01')}-{d.get('day', '01')}")
        return True
    if kind == "select":
        idx = best_option(field.options, value)
        if idx is None:
            log.warning("  No option matching %r among %s", value, field.options[:15])
            return False
        loc.first.select_option(index=idx)
        return True
    if kind == "combobox":
        return set_combobox(page, field, value)
    if kind == "radio":
        idx = best_option(field.options, value)
        if idx is None:
            log.warning("  No option matching %r among %s", value, field.options)
            return False
        _check(loc.nth(idx), True)
        return True
    if kind == "buttons":
        idx = best_option(field.options, value)
        if idx is None:
            log.warning("  No option matching %r among %s", value, field.options)
            return False
        loc.nth(idx).click()
        return True
    if kind == "checkboxes":
        hit = False
        for choice in as_choices(value):
            idx = best_option(field.options, choice)
            if idx is not None:
                _check(loc.nth(idx), True)
                hit = True
        if not hit:
            log.warning("  No option matching %r among %s", value, field.options)
        return hit
    if kind == "checkbox":
        _check(loc.first, checkbox_state(field.label, value))
        return True
    return False


def _check(inp, state: bool) -> None:
    """Custom-styled radios/checkboxes often hide the real input; fall back to its label."""
    try:
        inp.set_checked(state, force=True, timeout=3000)
    except PlaywrightError:
        if inp.is_checked() != state:
            inp.evaluate("e => (e.labels && e.labels[0] ? e.labels[0] : e).click()")


OPTION_SELECTOR = '[role="option"], [class*="select__option"]'


def set_combobox(page: Page, field: Field, value: Any) -> bool:
    """React-Select style: click, type to filter, pick the best option."""
    inp = page.locator(f'[data-af-id="{field.id}"]').first
    options = visible_options(page, OPTION_SELECTOR)
    for choice in as_choices(value):
        inp.click(force=True)
        inp.fill(choice)
        page.wait_for_timeout(800)  # async options (e.g. location search) take a moment
        if pick_from_list(page, options, choice, timeout=4000):
            return True
        inp.fill("")
    # Typing may not filter the way we expect; browse the full list instead.
    inp.click(force=True)
    if pick_from_list(page, options, value, timeout=3000):
        return True
    page.keyboard.press("Escape")
    return False


def read_combobox_options(page: Page, field: Field) -> list[str]:
    if field.kind != "combobox":
        return []
    inp = page.locator(f'[data-af-id="{field.id}"]').first
    inp.click(force=True)
    options = visible_options(page, OPTION_SELECTOR)
    try:
        options.first.wait_for(state="visible", timeout=3000)
        texts = [t.strip() for t in options.all_inner_texts()]
    except PlaywrightError:
        texts = []
    page.keyboard.press("Escape")
    return [t for t in texts if t]


def upload_file(page: Page, keyword_regex: str, path: str) -> bool:
    """Set the file input whose id/name/label/nearby text matches ``keyword_regex``."""
    idx = page.evaluate(
        r"""
        (pattern) => {
          const re = new RegExp(pattern, 'i');
          const inputs = [...document.querySelectorAll('input[type=file]')];
          return inputs.findIndex(inp => {
            let text = [inp.id, inp.name, inp.getAttribute('aria-label') || ''].join(' ');
            let p = inp.parentElement;
            for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
              if (p.querySelectorAll('input[type=file]').length > 1) break;
              text += ' ' + (p.innerText || '').slice(0, 200);
            }
            return re.test(text);
          });
        }
        """,
        keyword_regex,
    )
    if idx < 0:
        return False
    page.locator("input[type=file]").nth(idx).set_input_files(path)
    return True
