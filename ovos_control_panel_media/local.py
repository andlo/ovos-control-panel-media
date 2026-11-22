"""Your own media: files put on the device from the browser, where
ovos-media-provider-local finds them.

The provider is loaded by the OCP pipeline and searches the folders in its
``paths`` setting (``~/Music`` and ``~/Videos`` unless it is set), with their
subfolders. Its settings are ``media_providers.local`` in the pipeline's own
section (``intents.ovos-ocp-pipeline-plugin``) when that section has a
``media_providers``, and the top-level ``media_providers.local`` when not, as
the pipeline reads them. It reads them again by itself at the next search, so nothing has
to be told about a new file.

A file comes in parts, so no request is larger than :data:`PART`, and it is
written beside its final name as ``.<name>.part`` (which the provider skips)
until the last part. The largest file is the panel's ``webui.max_upload_mb``,
set on the Control panel page.

This runs where the panel runs, so it only helps when the OCP pipeline runs
there too.
"""
from __future__ import annotations

import os
import shutil
from importlib import metadata
from pathlib import Path
from typing import Any

PROVIDER_PACKAGE = "ovos-media-provider-local"
#: Where the pipeline's own media_providers is in mycroft.conf
PIPELINE_PATH = ["intents", "ovos-ocp-pipeline-plugin", "media_providers"]
#: What the provider indexes (its AUDIO_EXTENSIONS and VIDEO_EXTENSIONS)
AUDIO = (".aac", ".ac3", ".aiff", ".amr", ".ape", ".au", ".flac", ".alac", ".m4a", ".m4b",
         ".m4p", ".mp2", ".mp3", ".mpc", ".oga", ".ogg", ".opus", ".wav", ".wma")
VIDEO = (".3g2", ".3gp", ".3gpp", ".asf", ".avi", ".flv", ".m2ts", ".mkv", ".mov", ".mp4",
         ".mpeg", ".mpg", ".mts", ".ogm", ".ogv", ".qt", ".vob", ".webm", ".wmv")
ENDINGS = AUDIO + VIDEO
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


def installed() -> bool:
    """Whether ovos-media-provider-local is installed where the panel runs."""
    try:
        metadata.distribution(PROVIDER_PACKAGE)
    except Exception:  # noqa: BLE001 - not there, or broken metadata
        return False
    return True


def _settings() -> dict[str, Any]:
    """The provider's settings, from where the pipeline reads them: its own
    media_providers if it has one, else the top-level one."""
    from ovos_webui import configio
    cfg = configio.read_merged_config()
    providers = configio.get_in(cfg, PIPELINE_PATH)
    if not isinstance(providers, dict):
        providers = cfg.get("media_providers")
    found = providers.get("local") if isinstance(providers, dict) else None
    return found if isinstance(found, dict) else {}


def folders() -> list[Path]:
    """The folders the provider searches: its ``paths``, or the user's music
    and videos folders as it picks them."""
    raw = _settings().get("paths")
    if isinstance(raw, list) and any(isinstance(p, str) and p.strip() for p in raw):
        paths = [p.strip() for p in raw if isinstance(p, str) and p.strip()]
    else:
        paths = [os.environ.get("XDG_MUSIC_DIR") or "~/Music",
                 os.environ.get("XDG_VIDEOS_DIR") or "~/Videos"]
    return [Path(os.path.expanduser(p)) for p in dict.fromkeys(paths)]


def enabled() -> bool:
    return _settings().get("enabled", True) is not False


def max_bytes() -> int:
    from ovos_webui import configio
    return configio.max_upload_mb() * 1024 * 1024


def _name(raw: str) -> str:
    from ovos_webui.described import upload_name
    return upload_name(raw or "")


def _folder(n: int) -> Path:
    found = folders()
    if not 0 <= n < len(found):
        raise LocalMediaError(400, "not one of the provider's folders")
    return found[n]


def _target(where: Path, name: str) -> Path:
    """The file's place, which must stay inside the folder itself."""
    if not name or name.startswith("."):
        raise LocalMediaError(400, "not a file name")
    path = where / name
    if path.resolve().parent != where.resolve():
        raise LocalMediaError(400, "not a file name")
    return path


def overview() -> dict[str, Any]:
    """Whether the provider is here and on, the largest file, and the files
    the panel can see in each of its folders (not those in subfolders, which
    the provider finds too)."""
    if not installed():
        return {"installed": False}
    out = []
    for where in folders():
        files = []
        if where.is_dir():
            for f in sorted(where.iterdir(), key=lambda p: p.name.lower()):
                if len(files) >= MAX_FILES:
                    break
                if f.name.startswith(".") or not f.is_file() or not f.name.lower().endswith(ENDINGS):
                    continue
                files.append({"name": f.name, "size": f.stat().st_size})
        out.append({"path": str(where), "exists": where.is_dir(), "files": files})
    return {"installed": True, "enabled": enabled(), "max_bytes": max_bytes(), "part": PART,
            "endings": list(ENDINGS), "folders": out}


def receive(folder: int, raw_name: str, offset: int, total: int, blob: bytes) -> dict[str, Any]:
    """Write one part of a file. The first part (offset 0) starts it; each
    next one must follow on from what is there; the last one gives it its
    name. Answers whether the file is whole."""
    if not installed():
        raise LocalMediaError(409, "ovos-media-provider-local is not installed here")
    where = _folder(folder)
    name = _name(raw_name)
    if not name.lower().endswith(ENDINGS):
        raise LocalMediaError(415, "not a sound or film file the provider finds")
    if total <= 0 or offset < 0 or not blob or len(blob) > PART:
        raise LocalMediaError(400, "not a part of a file")
    if total > max_bytes():
        raise LocalMediaError(413, f"the file is larger than {max_bytes() // (1024 * 1024)} MB")
    if offset + len(blob) > total:
        raise LocalMediaError(400, "the part goes past the end of the file")
    path = _target(where, name)
    part = path.with_name("." + name + ".part")
    if offset == 0:
        if path.exists():
            raise LocalMediaError(409, "a file with that name is already there")
        where.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(where).free < total + SPARE:
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
    return {"done": True, "received": have, "name": name}


def remove(folder: int, raw_name: str) -> None:
    """Delete one of the files the card shows."""
    if not installed():
        raise LocalMediaError(409, "ovos-media-provider-local is not installed here")
    name = _name(raw_name)
    path = _target(_folder(folder), name)
    if not path.is_file() or not name.lower().endswith(ENDINGS):
        raise LocalMediaError(404, "there is no such file")
    path.unlink()
