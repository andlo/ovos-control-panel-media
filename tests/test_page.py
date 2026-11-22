"""The panel finds this page through its entry point and serves it."""
from ovos_webui import pages


def test_the_panel_finds_the_page_and_loads_it_cleanly():
    reg = pages.load()
    page = reg.by_id()["media"]
    assert page.package == "ovos-control-panel-media" and not page.builtin
    assert not [p for p in reg.problems if p.get("page") == "media"]


def test_the_page_is_served(token_client):
    token_client.headers["Authorization"] = "Bearer s3cret-token"
    got = token_client.get("/media")
    assert got.status_code == 200 and "/api/media/status" in got.text
