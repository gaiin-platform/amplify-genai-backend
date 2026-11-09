"""An unchanged page must produce the same bytes (and so the same document) on every scan.

The function is read straight from the source file so the test does not need the service's heavy imports."""
import json
from pathlib import Path

import pytest

SOURCE = (Path(__file__).parents[1] / "service" / "scrape_websites.py").read_text()


@pytest.fixture
def stable_page_json():
    start = SOURCE.index("def stable_page_json")
    namespace = {"json": json, "SmartDecimalEncoder": json.JSONEncoder}
    exec(SOURCE[start:SOURCE.index("def save_scraped_content")], namespace)
    return namespace["stable_page_json"]


def page(scanned_at, text="# Title\n\nbody"):
    return {"metadata": {"title": "Title", "url": "https://x.edu/a", "contentType": "text/html", "scrapedAt": scanned_at}, "text": text}


def test_the_scan_time_does_not_change_the_stored_bytes(stable_page_json):
    assert stable_page_json(page("2026-01-01T00:00:00")) == stable_page_json(page("2026-02-02T09:30:00"))


def test_changed_text_still_changes_the_bytes(stable_page_json):
    assert stable_page_json(page("t", "old")) != stable_page_json(page("t", "new"))


def test_everything_else_in_the_page_is_kept(stable_page_json):
    stored = json.loads(stable_page_json(page("t")))
    assert stored["metadata"] == {"title": "Title", "url": "https://x.edu/a", "contentType": "text/html"} and stored["text"] == "# Title\n\nbody"


def test_the_save_path_uses_it():
    assert "file_contents = stable_page_json(scraped_data['content'])" in SOURCE
