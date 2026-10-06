"""Picking the right applier for a job link."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from job_autofill.base import BaseApplier
from job_autofill.generic import GenericApplier
from job_autofill.greenhouse import GreenhouseApplier, is_greenhouse_url
from job_autofill.lever import LeverApplier, is_lever_url
from job_autofill.workday import WorkdayApplier, is_workday_url

# Job boards whose terms forbid automated tools (and that can ban accounts for it).
JOB_BOARDS = {
    "linkedin.com": "LinkedIn",
    "indeed.com": "Indeed",
    "glassdoor.com": "Glassdoor",
    "ziprecruiter.com": "ZipRecruiter",
    "joinhandshake.com": "Handshake",
    "simplyhired.com": "SimplyHired",
}


def job_board(url: str) -> Optional[str]:
    host = urlparse(url).netloc.lower()
    return next((name for domain, name in JOB_BOARDS.items() if host == domain or host.endswith("." + domain)), None)


def applier_for(url: str) -> type[BaseApplier]:
    if is_workday_url(url):
        return WorkdayApplier
    if is_greenhouse_url(url):
        return GreenhouseApplier
    if is_lever_url(url):
        return LeverApplier
    return GenericApplier
