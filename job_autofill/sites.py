"""Picking the right applier for a job link."""

from __future__ import annotations

from typing import Optional

from job_autofill.base import BaseApplier
from job_autofill.greenhouse import GreenhouseApplier, is_greenhouse_url
from job_autofill.lever import LeverApplier, is_lever_url
from job_autofill.workday import WorkdayApplier, is_workday_url

SUPPORTED = "Workday, Greenhouse and Lever"


def applier_for(url: str) -> Optional[type[BaseApplier]]:
    if is_workday_url(url):
        return WorkdayApplier
    if is_greenhouse_url(url):
        return GreenhouseApplier
    if is_lever_url(url):
        return LeverApplier
    return None
