"""Matching form questions to answers, and answers to dropdown/radio options."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


class _Skip:
    """Rule value meaning "leave this field alone" (and stop looking for other rules)."""

    def __repr__(self) -> str:
        return "SKIP"


SKIP = _Skip()


def normalize(text: str) -> str:
    """Lowercase, drop required-asterisks and collapse whitespace."""
    text = (text or "").replace("*", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


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


def as_choices(value: Any) -> list[str]:
    """An answer may be one string or a list of acceptable alternatives."""
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    return [str(value)]


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
    return None


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return normalize(str(value)) in {"yes", "y", "true", "1", "checked", "on", "agree", "i agree"}
