"""Your own media: files put on the device from the browser, where
ovos-skill-local-media finds them.

The skill reads ``<media_path>/<category>/`` (``~/OCPMedia/Music``,
``~/OCPMedia/Movies`` and so on; ``media_path`` is its own setting) and is
asked to read it again with ``<skill id>.scan``. A file comes in parts, so no
request is larger than :data:`PART`, and it is written beside its final name
as ``.<name>.part`` until the last part. The largest file is the panel's
``webui.max_upload_mb``, set on the Control panel page.

This runs where the panel runs, so it only helps when the skill runs there too,
as the panel's Skills page does.
"""
from __future__ import annotations

import os
import shutil
from importlib import metadata
from pathlib import Path
from typing import Any

SKILL_PACKAGE = "ovos-skill-local-media"
DEFAULT_PATH = "~/OCPMedia"
#: The skill's folders, and whether each holds sound or pictures as well
CATEGORIES = {
    "Music": "audio", "Podcasts": "audio", "Audiobooks": "audio", "RadioTheatre": "audio",
    "AudioDescriptions": "audio", "Movies": "video", "Series": "video",
    "Documentaries": "video", "Anime": "video", "Cartoon": "video", "Shorts": "video",
    "SilentMovies": "video", "BWMovies": "video",
}
AUDIO = (".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav", ".m4a", ".aac", ".wma")
VIDEO = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".mpg", ".mpeg")
#: One request's part of a file
PART = 8 * 1024 * 1024
#: Room left on the disk after a file, so an upload never fills it
SPARE = 200 * 1024 * 1024
MAX_FILES = 2000


class LocalMediaError(Exception):
    """Something the person can fix; ``status`` is the HTTP status to answer."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def skill_id() -> str | None:
    """The skill's id, if ovos-skill-local-media is installed where the panel
    runs: the name of its skill entry point."""
    try:
        eps = metadata.distribution(SKILL_PACKAGE).entry_points
    except metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - broken metadata: as if it were not there
        return None
    names = sorted({ep.name for ep in eps if ep.group in ("opm.skill", "ovos.plugin.skill")})
    return names[0] if names else None


def folder(sid: str) -> Path:
    """Where the skill looks: its own ``media_path`` setting, or ~/OCPMedia."""
    from ovos_webui import skillsio
    try:
        raw = skillsio.read_settings(sid).get("media_path")
    except (skillsio.SkillSettingsError, ValueError, AttributeError):
        raw = None
    path = raw if isinstance(raw, str) and raw.strip() else DEFAULT_PATH
    return Path(os.path.expanduser(path.strip()))


def endings(category: str) -> tuple[str, ...]:
    return AUDIO if CATEGORIES[category] == "audio" else AUDIO + VIDEO


def max_bytes() -> int:
    from ovos_webui import configio
    return configio.max_upload_mb() * 1024 * 1024


def _name(raw: str) -> str:
    from ovos_webui.described import upload_name
    return upload_name(raw or "")


def _target(base: Path, category: str, name: str) -> Path:
    """The file's place, which must stay inside the category's folder."""
    if category not in CATEGORIES:
        raise LocalMediaError(400, "not one of the skill's folders")
    if not name or name.startswith("."):
        raise LocalMediaError(400, "not a file name")
    where = base / category
    path = where / name
    if path.resolve().parent != where.resolve():
        raise LocalMediaError(400, "not a file name")
    return path


def overview() -> dict[str, Any]:
    """Whether the skill is here, where it looks, the largest file, and the
    files in each of its folders."""
    sid = skill_id()
    if sid is None:
        return {"installed": False}
    base = folder(sid)
    cats = []
    count = 0
    for cat in CATEGORIES:
        files = []
        where = base / cat
        if where.is_dir():
            for f in sorted(where.iterdir(), key=lambda p: p.name.lower()):
                if count >= MAX_FILES:
                    break
                if f.name.startswith(".") or not f.is_file():
                    continue
                files.append({"name": f.name, "size": f.stat().st_size})
                count += 1
        cats.append({"id": cat, "kind": CATEGORIES[cat], "files": files})
    return {"installed": True, "skill": sid, "folder": str(base), "max_bytes": max_bytes(),
            "part": PART, "audio": list(AUDIO), "video": list(VIDEO), "categories": cats}


def receive(category: str, raw_name: str, offset: int, total: int, blob: bytes) -> dict[str, Any]:
    """Write one part of a file. The first part (offset 0) starts it; each
    next one must follow on from what is there; the last one gives it its
    name. Answers whether the file is whole."""
    sid = skill_id()
    if sid is None:
        raise LocalMediaError(409, "ovos-skill-local-media is not installed here")
    if category not in CATEGORIES:
        raise LocalMediaError(400, "not one of the skill's folders")
    name = _name(raw_name)
    if not name.lower().endswith(endings(category)):
        raise LocalMediaError(415, "not a file this folder takes: " + ", ".join(endings(category)))
    if total <= 0 or offset < 0 or not blob or len(blob) > PART:
        raise LocalMediaError(400, "not a part of a file")
    if total > max_bytes():
        raise LocalMediaError(413, f"the file is larger than {max_bytes() // (1024 * 1024)} MB")
    if offset + len(blob) > total:
        raise LocalMediaError(400, "the part goes past the end of the file")
    base = folder(sid)
    path = _target(base, category, name)
    part = path.with_name("." + name + ".part")
    if offset == 0:
        if path.exists():
            raise LocalMediaError(409, "a file with that name is already there")
        path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(path.parent).free < total + SPARE:
            raise LocalMediaError(507, "there is not room for it on the device")
        part.write_bytes(b"")
    elif not part.is_file() or part.stat().st_size != offset:
        raise LocalMediaError(409, "the upload was interrupted; start it again")
    with open(part, "ab") as f:
        f.write(blob)
    have = offset + len(blob)
    if have < total:
        return {"done": False, "received": have}
    if path.exists():
        part.unlink(missing_ok=True)
        raise LocalMediaError(409, "a file with that name is already there")
    part.replace(path)
    return {"done": True, "received": have, "name": name, "skill": sid}


def remove(category: str, raw_name: str) -> str:
    """Delete one of the files; answers the skill's id, to ask it to read
    the folder again."""
    sid = skill_id()
    if sid is None:
        raise LocalMediaError(409, "ovos-skill-local-media is not installed here")
    path = _target(folder(sid), category, _name(raw_name))
    if not path.is_file():
        raise LocalMediaError(404, "there is no such file")
    path.unlink()
    return sid


def rescan(bus, sid: str) -> None:
    """Ask the skill to read its folders again."""
    if bus is None:
        return
    from ovos_bus_client.message import Message
    bus.emit(Message(f"{sid}.scan", {}))
