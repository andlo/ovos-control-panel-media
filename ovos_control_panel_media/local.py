"""Your own media: files put on the device from the browser, where the
device's local media search finds them.

Two things search local files, and the card works with the one installed:

- **ovos-media-provider-local**, the MediaProvider the OCP pipeline loads. It
  searches the folders in its ``paths`` setting (``~/Music`` and ``~/Videos``
  unless it is set), with their subfolders, and reads them again at every
  search. Its settings are ``media_providers.local`` where the pipeline reads
  them (:func:`ovos_webui.described.providers_place`).
- **ovos-skill-local-media**, the OCP skill it replaces, still in the OVOS
  releases. It reads ``<media_path>/<category>/`` (``~/OCPMedia/Music`` and
  so on; ``media_path`` is its own setting) and is asked to read them again
  with ``<skill id>.scan``.

With both installed the provider is used: it is the one OVOS is moving to.

A file comes in parts, so no request is larger than :data:`PART`, and it is
written beside its final name as ``.<name>.part`` until the last part. The
largest file is the panel's ``webui.max_upload_mb``, set on the Control panel
page. This runs where the panel runs, so it only helps when the assistant runs
there too.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

PROVIDER_PACKAGE = "ovos-media-provider-local"
SKILL_PACKAGE = "ovos-skill-local-media"
#: What ovos-media-provider-local indexes (its AUDIO_EXTENSIONS, VIDEO_EXTENSIONS)
AUDIO = (".aac", ".ac3", ".aiff", ".amr", ".ape", ".au", ".flac", ".alac", ".m4a", ".m4b",
         ".m4p", ".mp2", ".mp3", ".mpc", ".oga", ".ogg", ".opus", ".wav", ".wma")
VIDEO = (".3g2", ".3gp", ".3gpp", ".asf", ".avi", ".flv", ".m2ts", ".mkv", ".mov", ".mp4",
         ".mpeg", ".mpg", ".mts", ".ogm", ".ogv", ".qt", ".vob", ".webm", ".wmv")
ENDINGS = AUDIO + VIDEO
#: The skill's folders under its media_path, and whether each holds sound only
SKILL_CATEGORIES = {
    "Music": "audio", "Podcasts": "audio", "Audiobooks": "audio", "RadioTheatre": "audio",
    "AudioDescriptions": "audio", "Movies": "video", "Series": "video",
    "Documentaries": "video", "Anime": "video", "Cartoon": "video", "Shorts": "video",
    "SilentMovies": "video", "BWMovies": "video",
}
SKILL_DEFAULT_PATH = "~/OCPMedia"
#: One request's part of a file
PART = 8 * 1024 * 1024
#: Room left on the disk after a file, so an upload never fills it
SPARE = 200 * 1024 * 1024
#: The files shown per folder
MAX_FILES = 500


class LocalMediaError(Exception):
    """Something the person can fix; ``status`` is the HTTP status to answer."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class Folder:
    path: Path
    #: the skill's category (Music, Movies, ...), for the card to translate
    category: str | None
    endings: tuple[str, ...]


@dataclass
class Source:
    """What finds the files: ``provider`` or ``skill``, and its folders."""
    kind: str
    folders: list[Folder]
    enabled: bool = True
    #: the skill's id, to ask it to read its folders again
    skill: str | None = None


def _installed(package: str) -> metadata.Distribution | None:
    try:
        return metadata.distribution(package)
    except Exception:  # noqa: BLE001 - not there, or broken metadata
        return None


def _provider_settings() -> dict[str, Any]:
    from ovos_webui import configio, described
    found = configio.get_in(configio.read_merged_config(), described.providers_place() + ["local"])
    return found if isinstance(found, dict) else {}


def provider_folders(settings: dict[str, Any]) -> list[Path]:
    """The folders the provider searches: its ``paths``, or the user's music
    and videos folders as it picks them."""
    raw = settings.get("paths")
    if isinstance(raw, list) and any(isinstance(p, str) and p.strip() for p in raw):
        paths = [p.strip() for p in raw if isinstance(p, str) and p.strip()]
    else:
        paths = [os.environ.get("XDG_MUSIC_DIR") or "~/Music",
                 os.environ.get("XDG_VIDEOS_DIR") or "~/Videos"]
    return [Path(os.path.expanduser(p)) for p in dict.fromkeys(paths)]


def _skill_id(dist: metadata.Distribution) -> str | None:
    try:
        names = sorted({ep.name for ep in dist.entry_points
                        if ep.group in ("opm.skill", "ovos.plugin.skill")})
    except Exception:  # noqa: BLE001
        return None
    return names[0] if names else None


def skill_folder(sid: str) -> Path:
    """Where the skill looks: its own ``media_path`` setting, or ~/OCPMedia."""
    from ovos_webui import skillsio
    try:
        raw = skillsio.read_settings(sid).get("media_path")
    except (skillsio.SkillSettingsError, ValueError, AttributeError):
        raw = None
    path = raw if isinstance(raw, str) and raw.strip() else SKILL_DEFAULT_PATH
    return Path(os.path.expanduser(path.strip()))


def source() -> Source | None:
    """The provider if it is installed, else the skill, else nothing."""
    if _installed(PROVIDER_PACKAGE) is not None:
        settings = _provider_settings()
        return Source("provider", [Folder(p, None, ENDINGS) for p in provider_folders(settings)],
                      enabled=settings.get("enabled", True) is not False)
    dist = _installed(SKILL_PACKAGE)
    sid = _skill_id(dist) if dist is not None else None
    if sid is not None:
        base = skill_folder(sid)
        return Source("skill", [Folder(base / cat, cat, AUDIO if kind == "audio" else ENDINGS)
                                for cat, kind in SKILL_CATEGORIES.items()], skill=sid)
    return None


def max_bytes() -> int:
    from ovos_webui import configio
    return configio.max_upload_mb() * 1024 * 1024


def _name(raw: str) -> str:
    from ovos_webui.described import upload_name
    return upload_name(raw or "")


def _need() -> Source:
    found = source()
    if found is None:
        raise LocalMediaError(409, "neither ovos-media-provider-local nor ovos-skill-local-media "
                                   "is installed here")
    return found


def _folder(src: Source, n: int) -> Folder:
    if not 0 <= n < len(src.folders):
        raise LocalMediaError(400, "not one of the folders")
    return src.folders[n]


def _target(where: Path, name: str) -> Path:
    """The file's place, which must stay inside the folder itself."""
    if not name or name.startswith("."):
        raise LocalMediaError(400, "not a file name")
    path = where / name
    if path.resolve().parent != where.resolve():
        raise LocalMediaError(400, "not a file name")
    return path


def overview() -> dict[str, Any]:
    """What finds the files, whether it is on, the largest file, and the
    files directly in each of its folders."""
    src = source()
    if src is None:
        return {"installed": False}
    out = []
    for folder in src.folders:
        files = []
        if folder.path.is_dir():
            for f in sorted(folder.path.iterdir(), key=lambda p: p.name.lower()):
                if len(files) >= MAX_FILES:
                    break
                if f.name.startswith(".") or not f.is_file() \
                        or not f.name.lower().endswith(folder.endings):
                    continue
                files.append({"name": f.name, "size": f.stat().st_size})
        out.append({"path": str(folder.path), "category": folder.category,
                    "endings": list(folder.endings), "exists": folder.path.is_dir(), "files": files})
    return {"installed": True, "by": src.kind, "enabled": src.enabled, "max_bytes": max_bytes(),
            "part": PART, "folders": out}


def receive(folder: int, raw_name: str, offset: int, total: int, blob: bytes) -> dict[str, Any]:
    """Write one part of a file. The first part (offset 0) starts it; each
    next one must follow on from what is there; the last one gives it its
    name. Answers whether the file is whole, and when it is, the skill to ask
    to read its folders again (if it is the skill that finds them)."""
    src = _need()
    where = _folder(src, folder)
    name = _name(raw_name)
    if not name.lower().endswith(where.endings):
        raise LocalMediaError(415, "not a file this folder takes: " + ", ".join(where.endings))
    if total <= 0 or offset < 0 or not blob or len(blob) > PART:
        raise LocalMediaError(400, "not a part of a file")
    if total > max_bytes():
        raise LocalMediaError(413, f"the file is larger than {max_bytes() // (1024 * 1024)} MB")
    if offset + len(blob) > total:
        raise LocalMediaError(400, "the part goes past the end of the file")
    path = _target(where.path, name)
    part = path.with_name("." + name + ".part")
    if offset == 0:
        if path.exists():
            raise LocalMediaError(409, "a file with that name is already there")
        where.path.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(where.path).free < total + SPARE:
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
    return {"done": True, "received": have, "name": name, "rescan": src.skill}


def remove(folder: int, raw_name: str) -> str | None:
    """Delete one of the files the card shows; answers the skill to ask to
    read its folders again, if it is the skill that finds them."""
    src = _need()
    where = _folder(src, folder)
    name = _name(raw_name)
    path = _target(where.path, name)
    if not path.is_file() or not name.lower().endswith(where.endings):
        raise LocalMediaError(404, "there is no such file")
    path.unlink()
    return src.skill


def rescan(bus, sid: str | None) -> None:
    """Ask the skill to read its folders again; the provider needs nothing."""
    if bus is None or not sid:
        return
    from ovos_bus_client.message import Message
    bus.emit(Message(f"{sid}.scan", {}))
