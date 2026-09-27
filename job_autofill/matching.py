"""Matching form questions to answers, and answers to dropdown/radio options."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


class _Skip:
    """Rule value meaning "leave this field alone" (and stop looking for other rules)."""

    def __repr__(self) -> str:
        return "SKIP"


SKIP = _Skip()


_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " ", "*": " "})


def normalize(text: str) -> str:
    """Lowercase, straighten quotes, drop required-asterisks and collapse whitespace."""
    return re.sub(r"\s+", " ", (text or "").translate(_QUOTES)).strip().lower()


@dataclass
class Rule:
    """If a field's label matches ``pattern``, answer it with ``value``.

    ``kinds`` optionally restricts the rule to certain field kinds
    (text, textarea, dropdown, prompt, radio, checkbox, checkboxes, date).
    A ``value`` of ``None`` means "no answer configured" and the rule is ignored.
    """

    pattern: str
    value: Any
    kinds: Optional[set[str]] = None
    regex: re.Pattern = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.regex = re.compile(self.pattern, re.IGNORECASE)

    def applies(self, label: str, kind: str) -> bool:
        if self.value is None or self.value == "":
            return False
        if self.kinds and kind not in self.kinds:
            return False
        return bool(self.regex.search(normalize(label)))


def find_answer(rules: Iterable[Rule], label: str, kind: str, context: str = "") -> Any:
    """Return the answer for a field, ``SKIP``, or ``None`` if nothing matches.

    ``context`` (e.g. the full text around a checkbox) is only consulted if the
    label itself matched nothing.
    """
    rules = list(rules)
    for text in (label, context):
        if not text:
            continue
        for rule in rules:
            if rule.applies(text, kind):
                return rule.value
    return None


def to_text(value: Any) -> str:
    """How an answer is typed into a form: booleans become Yes/No."""
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return str(value)


def as_choices(value: Any) -> list[str]:
    """An answer may be one string or a list of acceptable alternatives."""
    if isinstance(value, (list, tuple)):
        return [to_text(v) for v in value if v is not None and to_text(v).strip()]
    return [to_text(value)]


def best_option(options: list[str], wanted: Any) -> Optional[int]:
    """Index of the option that best matches the wanted answer(s), or None.

    Preference order for each wanted alternative: exact match, option starts
    with the answer, option contains the answer, answer contains the option.
    """
    normed = [normalize(o) for o in options]
    for choice in as_choices(wanted):
        w = normalize(choice)
        if not w:
            continue
        for i, o in enumerate(normed):
            if o == w:
                return i
        for i, o in enumerate(normed):
            if o.startswith(w):
                return i
        for i, o in enumerate(normed):
            if w in o:
                return i
        for i, o in enumerate(normed):
            if len(o) > 2 and o in w:
                return i
        idx = _word_match(normed, w)
        if idx is not None:
            return idx
    return None


_STOPWORDS = {"i", "am", "a", "an", "the", "of", "and", "or", "to", "is", "are", "my", "me", "have", "has"}
_NEGATIONS = {"not", "no", "don't", "dont", "doesn't", "never", "non", "decline"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z']*", text) if w not in _STOPWORDS}


def _word_match(options: list[str], wanted: str) -> Optional[int]:
    """Option whose meaningful words all appear in the answer, e.g. "United States +1" for
    "United States of America". Never pairs a negative answer with a positive option or vice versa."""
    want = _words(wanted)
    negated = bool(want & _NEGATIONS)
    best, best_size = None, 0
    for i, o in enumerate(options):
        words = _words(o)
        if words and words <= want and bool(words & _NEGATIONS) == negated and len(words) > best_size:
            best, best_size = i, len(words)
    return best


def checkbox_state(label: str, value: Any) -> bool:
    """Whether to tick a single checkbox.

    Yes/No-style answers tick or untick it. Any other answer means the checkbox
    is one option of a choice (e.g. "No, I do not have a disability"): tick it
    only if its label is that option.
    """
    if isinstance(value, bool):
        return value
    choices = as_choices(value)
    answers = {normalize(c) for c in choices}
    if answers <= {"yes", "y", "true", "1", "checked", "on", "agree", "i agree"}:
        return True
    if answers <= {"no", "n", "false", "0", "off", "unchecked"}:
        return False
    return best_option([label], choices) is not None


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return normalize(str(value)) in {"yes", "y", "true", "1", "checked", "on", "agree", "i agree"}


def parse_date(value: Any) -> dict[str, str]:
    """'today', 'YYYY', 'MM/YYYY', 'MM/DD/YYYY' or 'YYYY-MM[-DD]' -> {'month','day','year'}."""
    if isinstance(value, (dt.date, dt.datetime)):
        return {"month": f"{value.month:02d}", "day": f"{value.day:02d}", "year": str(value.year)}
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
