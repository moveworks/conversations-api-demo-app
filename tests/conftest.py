"""Shared fixtures for the test suite.

The demo is installed in editable mode (``pip install -e ".[test]"``), with
``moveworks_capi`` from the starter, so both import without path manipulation.
"""

import pytest

from moveworks_capi import rendering

SHORT_LINKS: dict = {}

# Where each short link in fixtures/portal_form_message.json redirects.
PORTAL_REDIRECTS = {
    "https://app.moveworks.ai/r?v2_fixture_a": "https://example.service-now.com/sp?id=sc_cat_item&sys_id=retirement_inquiry",
    "https://app.moveworks.ai/r?v2_fixture_b": "https://example.service-now.com/sp?id=sc_cat_item&sys_id=enrollment_inquiry",
    "https://app.moveworks.ai/r?v2_fixture_c": "https://example.service-now.com/sp?id=sc_cat_item&sys_id=plan_enrollment",
}


@pytest.fixture(autouse=True)
def offline_short_links(monkeypatch):
    """Short links resolve from ``SHORT_LINKS`` instead of the network."""
    SHORT_LINKS.clear()
    monkeypatch.setattr(rendering, "short_link_target", SHORT_LINKS.get)
    yield SHORT_LINKS
