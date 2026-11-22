"""Your own media: files put on the device for ovos-media-provider-local."""
import pytest

from ovos_control_panel_media import local


@pytest.fixture
def media(tmp_path, monkeypatch):
    """The provider is installed and searches tmp_path/Music and
    tmp_path/Videos; files up to 20 bytes."""
    monkeypatch.setattr(local, "installed", lambda: True)
    monkeypatch.setattr(local, "folders", lambda: [tmp_path / "Music", tmp_path / "Videos"])
    monkeypatch.setattr(local, "max_bytes", lambda: 20)
    monkeypatch.setattr(local, "PART", 8)
    return tmp_path


@pytest.fixture
def c(token_client):
    token_client.headers["Authorization"] = "Bearer s3cret-token"
    return token_client


def _send(c, data, name="song.mp3", folder=0, part=8):
    r = None
    for offset in range(0, len(data), part):
        r = c.post("/api/media/local/upload", content=data[offset:offset + part],
                   params={"folder": folder, "name": name, "offset": offset, "total": len(data)})
        if r.status_code != 200:
            return r
    return r


def test_without_the_provider_there_is_nothing_to_show(c, monkeypatch):
    monkeypatch.setattr(local, "installed", lambda: False)
    assert c.get("/api/media/local").json() == {"installed": False}
    r = c.post("/api/media/local/upload", content=b"x",
               params={"folder": 0, "name": "a.mp3", "offset": 0, "total": 1})
    assert r.status_code == 409


def test_every_route_needs_the_token(token_client, media):
    assert token_client.get("/api/media/local").status_code == 401
    assert token_client.post("/api/media/local/upload", content=b"x", params={
        "folder": 0, "name": "a.mp3", "offset": 0, "total": 1}).status_code == 401
    assert token_client.post("/api/media/local/delete",
                             json={"folder": 0, "name": "a.mp3"}).status_code == 401


def test_a_file_comes_in_parts_and_is_put_where_the_provider_searches(c, media):
    data = b"0123456789abcdefghi"
    r = _send(c, data)
    assert r.json() == {"done": True, "received": 19, "name": "song.mp3"}
    assert (media / "Music" / "song.mp3").read_bytes() == data
    assert not list((media / "Music").glob(".*")), "no part left behind"
    (media / "Music" / "notes.txt").write_text("not media")
    seen = c.get("/api/media/local").json()
    assert seen["installed"] and seen["enabled"] and ".mp3" in seen["endings"]
    assert seen["folders"] == [
        {"path": str(media / "Music"), "exists": True, "files": [{"name": "song.mp3", "size": 19}]},
        {"path": str(media / "Videos"), "exists": False, "files": []}]


def test_a_part_that_does_not_follow_on_is_refused(c, media):
    q = {"folder": 0, "name": "a.mp3", "total": 16}
    assert c.post("/api/media/local/upload", content=b"12345678", params=dict(q, offset=0)).status_code == 200
    r = c.post("/api/media/local/upload", content=b"12345678", params=dict(q, offset=4))
    assert r.status_code == 409 and "start it again" in r.json()["detail"]
    r = c.post("/api/media/local/upload", content=b"123456789", params=dict(q, offset=8))
    assert r.status_code == 400, "larger than a part"
    assert not (media / "Music" / "a.mp3").exists()


@pytest.mark.parametrize("folder,name,size,status", [
    (1, "film.mkv", 4, 200),
    (0, "film.mp4", 4, 200),              # any folder takes sound and films
    (0, "big.mp3", 21, 413),              # larger than the panel's limit
    (2, "a.mp3", 4, 400),                 # not one of the provider's folders
    (0, "notes.txt", 4, 415),
])
def test_what_the_folders_take(c, media, folder, name, size, status):
    assert _send(c, b"x" * size, name=name, folder=folder).status_code == status


def test_a_name_cannot_leave_the_folder(c, media):
    r = _send(c, b"abc", name="../../evil.mp3")
    assert r.status_code == 200 and r.json()["name"] == "evil.mp3"
    assert (media / "Music" / "evil.mp3").is_file()
    assert _send(c, b"abc", name=".hidden.mp3").json()["name"] == "hidden.mp3"
    outside = media.parent / "outside.mp3"
    (media / "Music" / "link.mp3").symlink_to(outside)
    assert _send(c, b"abc", name="link.mp3").status_code == 400
    assert not outside.exists()


def test_a_file_already_there_is_kept(c, media):
    (media / "Music").mkdir()
    (media / "Music" / "song.mp3").write_bytes(b"mine")
    assert _send(c, b"other").status_code == 409
    assert (media / "Music" / "song.mp3").read_bytes() == b"mine"


def test_a_file_is_deleted(c, media):
    _send(c, b"abc")
    (media / "Music" / "keep.txt").write_text("x")
    assert c.post("/api/media/local/delete", json={"folder": 0, "name": "nope.mp3"}).status_code == 404
    assert c.post("/api/media/local/delete", json={"folder": 0, "name": "keep.txt"}).status_code == 404, \
        "only the media files the card shows"
    assert c.post("/api/media/local/delete", json={"folder": 0, "name": "song.mp3"}).json() == {"ok": True}
    assert not (media / "Music" / "song.mp3").exists()
    assert c.post("/api/media/local/delete", json={"folder": 0, "name": "../x.mp3"}).status_code == 404


def test_no_room_on_the_disk_is_said(c, media, monkeypatch):
    import shutil
    from collections import namedtuple
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(1, 1, 10))
    assert _send(c, b"abc").status_code == 507


def _config(monkeypatch, local_cfg):
    from ovos_webui import configio
    cfg = {"intents": {"ovos-ocp-pipeline-plugin": {"media_providers": {"local": local_cfg}}}}
    monkeypatch.setattr(configio, "read_merged_config", lambda: cfg)


def test_the_folders_are_the_providers_own_setting(tmp_path, monkeypatch):
    _config(monkeypatch, {"paths": [str(tmp_path / "a"), "~/b", str(tmp_path / "a"), "", 5]})
    import os
    assert local.folders() == [tmp_path / "a", __import__("pathlib").Path(os.path.expanduser("~/b"))]
    _config(monkeypatch, {})
    monkeypatch.setenv("XDG_MUSIC_DIR", str(tmp_path / "m"))
    monkeypatch.delenv("XDG_VIDEOS_DIR", raising=False)
    assert local.folders() == [tmp_path / "m", __import__("pathlib").Path(os.path.expanduser("~/Videos"))]


def test_a_provider_turned_off_is_said(monkeypatch):
    _config(monkeypatch, {"enabled": False})
    assert local.enabled() is False
    _config(monkeypatch, {})
    assert local.enabled() is True


def test_the_largest_file_is_the_panels_setting(monkeypatch):
    from ovos_webui import configio
    monkeypatch.setattr(configio, "max_upload_mb", lambda: 3)
    assert local.max_bytes() == 3 * 1024 * 1024


def test_the_page_asks_for_uploads_of_one_part():
    from ovos_control_panel_media import PAGE
    assert PAGE["uploads"]["/api/media/local/upload"] >= local.PART
