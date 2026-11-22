"""Your own media: files put on the device for ovos-skill-local-media."""
import pytest

from ovos_control_panel_media import local

SID = "ovos-skill-local-media.openvoiceos"


@pytest.fixture
def media(tmp_path, monkeypatch):
    """The skill is installed and looks in tmp_path; files up to 20 bytes."""
    monkeypatch.setattr(local, "skill_id", lambda: SID)
    monkeypatch.setattr(local, "folder", lambda sid: tmp_path)
    monkeypatch.setattr(local, "max_bytes", lambda: 20)
    monkeypatch.setattr(local, "PART", 8)
    return tmp_path


@pytest.fixture
def c(token_client):
    token_client.headers["Authorization"] = "Bearer s3cret-token"
    return token_client


def _send(c, data, name="song.mp3", category="Music", part=8):
    r = None
    for offset in range(0, len(data), part):
        r = c.post("/api/media/local/upload", content=data[offset:offset + part],
                   params={"category": category, "name": name, "offset": offset, "total": len(data)})
        if r.status_code != 200:
            return r
    return r


def test_without_the_skill_there_is_nothing_to_show(c, monkeypatch):
    monkeypatch.setattr(local, "skill_id", lambda: None)
    assert c.get("/api/media/local").json() == {"installed": False}
    r = c.post("/api/media/local/upload", content=b"x",
               params={"category": "Music", "name": "a.mp3", "offset": 0, "total": 1})
    assert r.status_code == 409


def test_every_route_needs_the_token(token_client, media):
    assert token_client.get("/api/media/local").status_code == 401
    assert token_client.post("/api/media/local/upload", content=b"x", params={
        "category": "Music", "name": "a.mp3", "offset": 0, "total": 1}).status_code == 401
    assert token_client.post("/api/media/local/delete",
                             json={"category": "Music", "name": "a.mp3"}).status_code == 401


def test_a_file_comes_in_parts_and_the_skill_reads_its_folder_again(c, media, bus):
    asked = []
    bus.on(f"{SID}.scan", lambda m: asked.append(m))
    data = b"0123456789abcdefghi"
    r = _send(c, data)
    assert r.json() == {"done": True, "received": 19, "name": "song.mp3"}
    assert (media / "Music" / "song.mp3").read_bytes() == data
    assert not list((media / "Music").glob(".*")), "no part left behind"
    assert len(asked) == 1
    seen = c.get("/api/media/local").json()
    assert seen["installed"] and seen["folder"] == str(media)
    music = next(x for x in seen["categories"] if x["id"] == "Music")
    assert music["files"] == [{"name": "song.mp3", "size": 19}] and music["kind"] == "audio"


def test_a_part_that_does_not_follow_on_is_refused(c, media):
    q = {"category": "Music", "name": "a.mp3", "total": 16}
    assert c.post("/api/media/local/upload", content=b"12345678", params=dict(q, offset=0)).status_code == 200
    r = c.post("/api/media/local/upload", content=b"12345678", params=dict(q, offset=4))
    assert r.status_code == 409 and "start it again" in r.json()["detail"]
    r = c.post("/api/media/local/upload", content=b"123456789", params=dict(q, offset=8))
    assert r.status_code == 400, "larger than a part"
    assert not (media / "Music" / "a.mp3").exists()


@pytest.mark.parametrize("category,name,size,status", [
    ("Music", "film.mp4", 4, 415),        # sound only in Music
    ("Movies", "film.mp4", 4, 200),       # films go in Movies
    ("Music", "big.mp3", 21, 413),        # larger than the panel's limit
    ("Nope", "a.mp3", 4, 400),            # not one of the skill's folders
    ("Music", "notes.txt", 4, 415),
])
def test_what_each_folder_takes(c, media, category, name, size, status):
    assert _send(c, b"x" * size, name=name, category=category).status_code == status


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


def test_a_file_is_deleted_and_the_skill_told(c, media, bus):
    asked = []
    bus.on(f"{SID}.scan", lambda m: asked.append(m))
    _send(c, b"abc")
    asked.clear()
    assert c.post("/api/media/local/delete", json={"category": "Music", "name": "nope.mp3"}).status_code == 404
    assert c.post("/api/media/local/delete", json={"category": "Music", "name": "song.mp3"}).json() == {"ok": True}
    assert not (media / "Music" / "song.mp3").exists() and len(asked) == 1
    assert c.post("/api/media/local/delete", json={"category": "Music", "name": "../x"}).status_code == 404


def test_no_room_on_the_disk_is_said(c, media, monkeypatch):
    import shutil
    from collections import namedtuple
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(1, 1, 10))
    assert _send(c, b"abc").status_code == 507


def test_the_folder_is_the_skills_own_setting(tmp_path, monkeypatch):
    from ovos_webui import skillsio
    monkeypatch.setattr(skillsio, "read_settings", lambda sid: {"media_path": str(tmp_path / "x")})
    assert local.folder(SID) == tmp_path / "x"
    monkeypatch.setattr(skillsio, "read_settings", lambda sid: {})
    assert str(local.folder(SID)).endswith("OCPMedia")


def test_the_largest_file_is_the_panels_setting(monkeypatch):
    from ovos_webui import configio
    monkeypatch.setattr(configio, "max_upload_mb", lambda: 3)
    assert local.max_bytes() == 3 * 1024 * 1024


def test_the_page_asks_for_uploads_of_one_part():
    from ovos_control_panel_media import PAGE
    assert PAGE["uploads"]["/api/media/local/upload"] >= local.PART
