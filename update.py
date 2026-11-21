"""Проверка и установка обновлений MidiPult для Windows.

Правила простые и жёсткие: сеть только по https и только хосты GitHub,
целостность — SHA-256 из того же релиза. Если файла ``.sha256`` рядом нет,
обновление не предлагается, а установка не начинается. Тихих обновлений
без действия пользователя здесь нет намеренно: сборка не подписана.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

REPO = "RoBiN19666/midipult-windows"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_PAGE = f"https://github.com/{REPO}/releases/latest"

# Куда разрешено ходить за релизом и файлами. Всё остальное — отказ.
ALLOWED_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)

USER_AGENT = "MidiPultWindows/1.0"
TIMEOUT = 20.0
MAX_DOWNLOAD = 200 * 1024 * 1024
SETUP_RE = re.compile(r"^MidiPult-(.+)-setup\.exe$")
PORTABLE_RE = re.compile(r"^MidiPult(?:-(.+))?-portable\.exe$")
PLAIN_EXE_RE = re.compile(r"^MidiPult\.exe$")


# ---------------------------------------------------------------------------
# Версии
# ---------------------------------------------------------------------------


def version_tuple(value: str) -> tuple[int, ...]:
    """``v1.2.3`` -> ``(1, 2, 3)``; мусорные хвосты отбрасываются."""

    text = (value or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in re.split(r"[.\-+_]", text):
        if chunk.isdigit():
            parts.append(int(chunk))
        elif chunk:
            break
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return version_tuple(latest) > version_tuple(current)


# ---------------------------------------------------------------------------
# Адреса и целостность
# ---------------------------------------------------------------------------


def allowed_url(url: str) -> bool:
    parts = urlsplit((url or "").strip())
    if parts.scheme != "https":
        return False
    return (parts.hostname or "").lower() in ALLOWED_HOSTS


def file_name(url: str) -> str:
    return Path(urlsplit((url or "").strip()).path).name


def parse_sha256(text: str, filename: str = "") -> str:
    """Достаёт хеш из файла формата ``sha256sum``: ``<hash>  <имя>``."""

    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([0-9a-fA-F]{64})(?:\s+\*?(.+))?$", line)
        if not match:
            continue
        name = (match.group(2) or "").strip()
        if filename and name and Path(name).name != Path(filename).name:
            continue
        return match.group(1).lower()
    return ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Разбор релиза
# ---------------------------------------------------------------------------

MANIFEST_NAME = "update.json"
ROLE_SETUP = "setup"
ROLE_PORTABLE = "portable"


@dataclass
class Artifact:
    """Файл релиза: имя, адрес и ожидаемый SHA-256."""

    name: str
    url: str
    sha256: str = ""
    checksum_url: str = ""


@dataclass
class Release:
    version: str
    page_url: str
    setup: Artifact | None = None
    exe: Artifact | None = None


def _assets(payload: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in payload.get("assets") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("browser_download_url") or "").strip()
        if name and allowed_url(url):
            found[name] = url
    return found


def _clean_sha(value: str) -> str:
    digest = (value or "").strip().lower()
    return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else ""


def _artifact_by_name(assets: dict[str, str], pattern: re.Pattern[str]) -> Artifact | None:
    """Запасной путь: роль определяется по имени файла (для релизов без манифеста)."""

    for name, url in assets.items():
        if not pattern.match(name):
            continue
        checksum = assets.get(name + ".sha256")
        if checksum:
            return Artifact(name=name, url=url, checksum_url=checksum)
    return None


def _manifest_files(manifest: dict) -> dict:
    files = manifest.get("files")
    if isinstance(files, dict):
        return files
    return {key: value for key, value in manifest.items() if isinstance(value, dict)}


def _artifact_from_manifest(files: dict, role: str, assets: dict[str, str]) -> Artifact | None:
    """Роль из манифеста: имя файла берётся оттуда, а не угадывается по шаблону."""

    item = files.get(role)
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    if not name or name not in assets:
        return None
    digest = _clean_sha(str(item.get("sha256") or ""))
    checksum_url = "" if digest else assets.get(name + ".sha256", "")
    if not digest and not checksum_url:
        return None
    return Artifact(name=name, url=assets[name], sha256=digest, checksum_url=checksum_url)


def release_version(payload: dict, assets: dict[str, str]) -> str:
    for pattern in (SETUP_RE, PORTABLE_RE):
        for name in assets:
            match = pattern.match(name)
            if match and match.group(1):
                return match.group(1)
    tag = str(payload.get("tag_name") or "").strip()
    if tag.startswith("windows-v"):
        return tag[len("windows-v"):]
    return tag.lstrip("vV")


def parse_release(payload: dict, manifest: dict | None = None) -> Release | None:
    """Разбирает ответ GitHub API. Черновики и пре-релизы не предлагаются.

    Если в релизе есть ``update.json``, роли файлов берутся из него: имена ассетов
    можно менять свободно, обновление не сломается. Без манифеста работает разбор
    по именам — чтобы не ломать уже выпущенные релизы.
    """

    if not isinstance(payload, dict):
        return None
    if payload.get("draft") or payload.get("prerelease"):
        return None

    assets = _assets(payload)
    page = str(payload.get("html_url") or "").strip()
    if not allowed_url(page):
        page = RELEASE_PAGE

    if isinstance(manifest, dict):
        files = _manifest_files(manifest)
        setup = _artifact_from_manifest(files, ROLE_SETUP, assets)
        exe = _artifact_from_manifest(files, ROLE_PORTABLE, assets)
        if setup or exe:
            version = str(manifest.get("version") or "").strip() or release_version(payload, assets)
            if version:
                return Release(version=version, page_url=page, setup=setup, exe=exe)

    version = release_version(payload, assets)
    if not version:
        return None

    release = Release(
        version=version,
        page_url=page,
        setup=_artifact_by_name(assets, SETUP_RE),
        exe=_artifact_by_name(assets, PORTABLE_RE) or _artifact_by_name(assets, PLAIN_EXE_RE),
    )
    if release.setup is None and release.exe is None:
        return None
    return release


# ---------------------------------------------------------------------------
# Сеть
# ---------------------------------------------------------------------------


def _request(url: str) -> Request:
    if not allowed_url(url):
        raise ValueError("Недопустимый адрес загрузки")
    return Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )


def fetch_release() -> Release | None:
    with urlopen(_request(RELEASES_API), timeout=TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))

    manifest = None
    manifest_url = _assets(payload).get(MANIFEST_NAME)
    if manifest_url:
        try:
            manifest = json.loads(fetch_text(manifest_url).lstrip("\ufeff"))
        except (OSError, ValueError):
            manifest = None
    return parse_release(payload, manifest)


def fetch_text(url: str) -> str:
    with urlopen(_request(url), timeout=TIMEOUT) as response:
        return response.read(64 * 1024).decode("utf-8", "replace")


def download(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with urlopen(_request(url), timeout=TIMEOUT) as response, open(destination, "wb") as handle:
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_DOWNLOAD:
                raise ValueError("Файл обновления слишком большой")
            handle.write(chunk)
    return destination


# ---------------------------------------------------------------------------
# Установка
# ---------------------------------------------------------------------------


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_path() -> Path:
    return Path(sys.executable)


def is_installed() -> bool:
    """Установленная версия — та, рядом с которой лежит деинсталлятор Inno."""

    if not is_frozen():
        return False
    return (executable_path().parent / "unins000.exe").is_file()


def swap_portable(new_exe: Path) -> tuple[bool, str]:
    """Подменяет работающий exe: переименовать старый, поставить новый.

    Windows не даёт перезаписать запущенный файл, но разрешает его
    переименовать. При любой ошибке старый файл возвращается на место.
    """

    if not is_frozen():
        return False, "Приложение запущено из исходников — обновитесь вручную."

    target = executable_path()
    backup = target.with_name(target.name + ".old")
    try:
        if backup.exists():
            backup.unlink()
    except OSError:
        pass

    try:
        target.rename(backup)
    except OSError as exc:
        return False, f"Нет прав на замену файла: {exc}"

    try:
        new_exe.replace(target)
    except OSError as exc:
        try:
            backup.rename(target)
        except OSError:
            pass
        return False, f"Не удалось поставить новый файл: {exc}"
    return True, ""


def restart(executable: Path) -> bool:
    try:
        subprocess.Popen([str(executable)])
        return True
    except OSError:
        return False


def run_installer(setup_path: Path) -> bool:
    try:
        subprocess.Popen(
            [
                str(setup_path),
                "/SILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/CLOSEAPPLICATIONS",
            ]
        )
        return True
    except OSError:
        return False


def cleanup_old_executable() -> None:
    """Убирает ``MidiPult.exe.old``, оставшийся после прошлого обновления."""

    if not is_frozen():
        return
    target = executable_path()
    old = target.with_name(target.name + ".old")
    try:
        if old.exists():
            old.unlink()
    except OSError:
        pass
