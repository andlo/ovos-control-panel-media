"""Your own media: files put on the device for ovos-media-provider-local, or
for ovos-skill-local-media where that is what the device has."""
import pytest

from ovos_control_panel_media import local


@pytest.fixture
def media(tmp_path, monkeypatch):
    """The provider is installed and searches tmp_path/Music and
    tmp_path/Videos; files up to 20 bytes."""
    src = local.Source("provider", [local.Folder(tmp_path / "Music", None, local.ENDINGS),
                                    local.Folder(tmp_path / "Videos", None, local.ENDINGS)])
    monkeypatch.setattr(local, "source", lambda: src)
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


def test_without_the_provider_or_the_skill_there_is_nothing_to_show(c, monkeypatch):
    monkeypatch.setattr(local, "source", lambda: None)
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
    assert seen["installed"] and seen["enabled"] and seen["by"] == "provider"
    music, videos = seen["folders"]
    assert music["path"] == str(media / "Music") and music["category"] is None and ".mkv" in music["endings"]
    assert music["files"] == [{"name": "song.mp3", "size": 19}] and music["exists"]
    assert videos["files"] == [] and not videos["exists"]


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


def _config(monkeypatch, cfg):
    from ovos_webui import configio
    monkeypatch.setattr(configio, "read_merged_config", lambda: cfg)


def _only(monkeypatch, package):
    """Only ``package`` of the two is installed."""
    from types import SimpleNamespace as NS
    eps = [NS(group="opm.skill", name="ovos-skill-local-media.openvoiceos")]
    monkeypatch.setattr(local, "_installed", lambda p: NS(entry_points=eps) if p == package else None)


def test_the_provider_searches_its_own_paths(tmp_path, monkeypatch):
    _only(monkeypatch, local.PROVIDER_PACKAGE)
    pipe = {"intents": {"ovos-ocp-pipeline-plugin": {"media_providers": {
        "local": {"paths": [str(tmp_path / "a"), "~/b", str(tmp_path / "a"), "", 5]}}}}}
    _config(monkeypatch, pipe)
    src = local.source()
    import os
    from pathlib import Path
    assert src.kind == "provider" and src.enabled and src.skill is None
    assert [f.path for f in src.folders] == [tmp_path / "a", Path(os.path.expanduser("~/b"))]
    _config(monkeypatch, {"media_providers": {"local": {"enabled": False}}})
    monkeypatch.setenv("XDG_MUSIC_DIR", str(tmp_path / "m"))
    monkeypatch.delenv("XDG_VIDEOS_DIR", raising=False)
    src = local.source()
    assert not src.enabled, "the top-level settings, as the pipeline has none of its own"
    assert [f.path for f in src.folders] == [tmp_path / "m", Path(os.path.expanduser("~/Videos"))]


def test_the_skill_has_its_category_folders(tmp_path, monkeypatch):
    from ovos_webui import skillsio
    _only(monkeypatch, local.SKILL_PACKAGE)
    monkeypatch.setattr(skillsio, "read_settings", lambda sid: {"media_path": str(tmp_path)})
    src = local.source()
    assert src.kind == "skill" and src.skill == "ovos-skill-local-media.openvoiceos"
    music = src.folders[0]
    assert music.path == tmp_path / "Music" and music.category == "Music" and ".mp4" not in music.endings
    movies = next(f for f in src.folders if f.category == "Movies")
    assert ".mp4" in movies.endings and len(src.folders) == len(local.SKILL_CATEGORIES)
    monkeypatch.setattr(skillsio, "read_settings", lambda sid: {})
    assert str(local.source().folders[0].path).endswith("OCPMedia/Music")


def test_with_both_the_provider_is_used(monkeypatch):
    monkeypatch.setattr(local, "_installed", lambda p: object())
    _config(monkeypatch, {})
    assert local.source().kind == "provider"


def test_the_skill_is_asked_to_read_its_folders_again(c, tmp_path, monkeypatch, bus):
    asked = []
    bus.on("ovos-skill-local-media.openvoiceos.scan", lambda m: asked.append(m))
    src = local.Source("skill", [local.Folder(tmp_path / "Music", "Music", local.AUDIO),
                                 local.Folder(tmp_path / "Movies", "Movies", local.ENDINGS)],
                       skill="ovos-skill-local-media.openvoiceos")
    monkeypatch.setattr(local, "source", lambda: src)
    monkeypatch.setattr(local, "max_bytes", lambda: 20)
    assert _send(c, b"abc", name="film.mp4", folder=0).status_code == 415, "Music takes sound"
    assert _send(c, b"abc", name="film.mp4", folder=1).json() == {"done": True, "received": 3, "name": "film.mp4"}
    assert len(asked) == 1
    assert c.post("/api/media/local/delete", json={"folder": 1, "name": "film.mp4"}).json() == {"ok": True}
    assert len(asked) == 2
    seen = c.get("/api/media/local").json()
    assert seen["by"] == "skill" and seen["folders"][1]["category"] == "Movies"


def test_the_largest_file_is_the_panels_setting(monkeypatch):
    from ovos_webui import configio
    monkeypatch.setattr(configio, "max_upload_mb", lambda: 3)
    assert local.max_bytes() == 3 * 1024 * 1024


def test_the_page_asks_for_uploads_of_one_part():
    from ovos_control_panel_media import PAGE
    assert PAGE["uploads"]["/api/media/local/upload"] >= local.PART
