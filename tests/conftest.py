import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("CHROMIUM_PATH")
    if not executable and Path("/opt/pw-browsers/chromium").exists():
        executable = "/opt/pw-browsers/chromium"
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=executable)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    pg.set_default_timeout(5000)
    yield pg
    context.close()


@pytest.fixture
def mock_workday_url():
    return (FIXTURES / "mock_workday.html").resolve().as_uri()
