"""Ядро MidiPult для Windows: адреса, хранилище колонок, UDP-поиск.

Модуль не зависит от GUI и легко тестируется отдельно.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

DISCOVERY_PORT = 8056
DISCOVERY_REQUEST = b"MYPULT_DISCOVER"
DEFAULT_PANEL_PORT = 8055
USER_AGENT = "MidiPultWindows/1.0"


# ---------------------------------------------------------------------------
# Адреса и модель колонки
# ---------------------------------------------------------------------------


@dataclass
class Station:
    """Одна колонка в списке приложения."""

    name: str
    url: str
    ip: str = ""
    model: str = ""
    version: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        self.name = (self.name or "").strip()
        self.url = (self.url or "").strip()
        self.ip = (self.ip or "").strip()
        self.model = (self.model or "").strip()
        self.version = (self.version or "").strip()
        self.id = (self.id or "").strip() or self.url

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Station | None":
        if not isinstance(data, dict):
            return None
        url = str(data.get("url") or "").strip()
        if not url:
            return None
        ip = str(data.get("ip") or "").strip()
        if not ip:
            ip = urlsplit(url).hostname or ""
        return cls(
            name=str(data.get("name") or ""),
            url=url,
            ip=ip,
            model=str(data.get("model") or ""),
            version=str(data.get("version") or ""),
            id=str(data.get("id") or ""),
        )


def normalize_address(value: str) -> Station:
    """Приводит введённый адрес к Station с URL панели.

    Принимает ``192.168.1.10``, ``192.168.1.10:8055``,
    ``http://192.168.1.10/`` и адреса с путём. Если порт не указан,
    подставляется штатный 8055.
    """

    text = (value or "").strip()
    if not text:
        raise ValueError("Укажите адрес колонки")

    if "://" not in text:
        text = "http://" + text

    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("Поддерживаются только адреса http:// или https://")
    if not parts.hostname:
        raise ValueError("В адресе нет имени или IP колонки")
    if any(ch.isspace() for ch in parts.hostname):
        raise ValueError("Адрес не должен содержать пробелы")

    try:
        port = parts.port or DEFAULT_PANEL_PORT
    except ValueError as exc:
        raise ValueError("Некорректный порт в адресе") from exc
    if not 1 <= port <= 65535:
        raise ValueError("Порт должен быть от 1 до 65535")

    host = parts.hostname
    if ":" in host:  # IPv6
        host = f"[{host}]"
    netloc = f"{host}:{port}"
    path = parts.path or "/"
    url = urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))

    return Station(
        name="",
        url=url,
        ip=parts.hostname,
        model="",
        version="",
        id=url,
    )


def station_from_discovery(payload: dict, sender_ip: str) -> Station | None:
    """Разбирает JSON-ответ UDP-поиска. Возвращает None для чужих пакетов."""

    if not isinstance(payload, dict):
        return None
    if str(payload.get("service", "")).strip().lower() != "midipult":
        return None

    ip = str(payload.get("ip") or "").strip() or sender_ip
    try:
        port = int(payload.get("port") or DEFAULT_PANEL_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PANEL_PORT
    if not 1 <= port <= 65535:
        port = DEFAULT_PANEL_PORT

    try:
        station = normalize_address(f"http://{ip}:{port}/")
    except ValueError:
        return None

    station.name = str(payload.get("name") or "").strip() or f"Колонка {ip}"
    station.model = str(payload.get("model") or "").strip()
    station.version = str(payload.get("version") or "").strip()
    station.id = str(payload.get("id") or "").strip() or station.url
    return station


# ---------------------------------------------------------------------------
# Хранилище
# ---------------------------------------------------------------------------


class StationStore:
    """Список колонок в JSON. Запись атомарная, доступ из потоков безопасен."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "stations.json"
        self._lock = threading.RLock()
        self.stations: list[Station] = []
        self.active_id: str = ""
        self.load()

    # -- чтение/запись -----------------------------------------------------

    def load(self) -> None:
        with self._lock:
            self.stations = []
            self.active_id = ""
            try:
                raw = self.path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return

            items = data.get("stations") if isinstance(data, dict) else data
            if not isinstance(items, list):
                return
            for item in items:
                station = Station.from_dict(item)
                if station is not None:
                    self.stations.append(station)
            if isinstance(data, dict):
                self.active_id = str(data.get("active_id") or "")

    def save(self) -> None:
        with self._lock:
            payload = {
                "version": 1,
                "active_id": self.active_id,
                "stations": [s.to_dict() for s in self.stations],
            }
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(text + "\n", encoding="utf-8")
            os.replace(tmp, self.path)

    # -- операции ----------------------------------------------------------

    def find(self, station_id: str) -> Station | None:
        with self._lock:
            for station in self.stations:
                if station.id == station_id or station.url == station_id:
                    return station
            return None

    def upsert(self, station: Station, keep_name: bool = False) -> Station:
        """Добавляет колонку или обновляет запись с тем же id/URL."""

        with self._lock:
            for index, existing in enumerate(self.stations):
                if existing.id == station.id or existing.url == station.url:
                    name = existing.name if keep_name and existing.name else station.name
                    merged = Station(
                        name=name or existing.name or station.name,
                        url=station.url,
                        ip=station.ip or existing.ip,
                        model=station.model or existing.model,
                        version=station.version or existing.version,
                        id=existing.id or station.id,
                    )
                    self.stations[index] = merged
                    self.save()
                    return merged
            if not station.name:
                station.name = f"Колонка {station.ip}" if station.ip else station.url
            self.stations.append(station)
            self.save()
            return station

    def rename(self, station_id: str, name: str) -> Station | None:
        clean = (name or "").strip()
        with self._lock:
            station = self.find(station_id)
            if station is None:
                return None
            station.name = clean
            self.save()
            return station

    def remove(self, station_id: str) -> bool:
        with self._lock:
            station = self.find(station_id)
            if station is None:
                return False
            self.stations.remove(station)
            if self.active_id in (station.id, station.url):
                self.active_id = ""
            self.save()
            return True

    def set_active(self, station_id: str) -> None:
        with self._lock:
            self.active_id = station_id
            self.save()

    def as_list(self) -> list[dict]:
        with self._lock:
            return [s.to_dict() for s in self.stations]


# ---------------------------------------------------------------------------
# UDP-поиск
# ---------------------------------------------------------------------------


def _broadcast_targets() -> list[str]:
    """Широковещательные адреса: общий и направленные для каждого интерфейса."""

    targets: list[str] = ["255.255.255.255"]
    try:
        import psutil  # необязательная зависимость, но с ней поиск надёжнее

        for addresses in psutil.net_if_addrs().values():
            for addr in addresses:
                if addr.family != socket.AF_INET or not addr.address or not addr.netmask:
                    continue
                if addr.address.startswith("127."):
                    continue
                try:
                    network = ipaddress.IPv4Network(
                        f"{addr.address}/{addr.netmask}", strict=False
                    )
                except ValueError:
                    continue
                broadcast = str(network.broadcast_address)
                if broadcast not in targets:
                    targets.append(broadcast)
    except Exception:
        # Без psutil остаётся 255.255.255.255 — этого обычно достаточно.
        pass
    return targets


def discover(
    timeout: float = 4.0,
    targets: Iterable[str] | None = None,
    port: int = DISCOVERY_PORT,
) -> list[Station]:
    """Ищет панели MidiPult широковещательным UDP-запросом."""

    targets = list(targets) if targets is not None else _broadcast_targets()
    found: dict[str, Station] = {}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
        sock.settimeout(0.4)

        for target in targets:
            try:
                sock.sendto(DISCOVERY_REQUEST, (target, port))
            except OSError:
                continue

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, address = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                payload = json.loads(data.decode("utf-8", "replace"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            station = station_from_discovery(payload, address[0])
            if station is not None:
                found[station.url] = station
    finally:
        sock.close()

    return sorted(found.values(), key=lambda s: s.name.casefold())


# ---------------------------------------------------------------------------
# Проверка доступности
# ---------------------------------------------------------------------------


def probe(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Проверяет, отвечает ли панель. 401/403 — это тоже ответ (страница PIN)."""

    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return True, f"HTTP {response.status}"
    except HTTPError as exc:
        return True, f"HTTP {exc.code}"
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            return False, "Колонка не ответила за отведённое время"
        if isinstance(reason, ConnectionRefusedError):
            return False, "Порт панели закрыт — проверьте, запущен ли MidiPult"
        text = str(reason) or str(exc)
        return False, _friendly_network_error(text)
    except (socket.timeout, TimeoutError):
        return False, "Колонка не ответила за отведённое время"
    except OSError as exc:
        return False, _friendly_network_error(str(exc))


def _friendly_network_error(text: str) -> str:
    lower = text.lower()
    if "refused" in lower:
        return "Порт панели закрыт — проверьте, запущен ли MidiPult"
    if "timed out" in lower or "timeout" in lower:
        return "Колонка не ответила за отведённое время"
    if "unreachable" in lower or "network is down" in lower:
        return "Колонка недоступна в этой сети"
    if "name or service" in lower or "getaddrinfo" in lower or "nodename" in lower:
        return "Не удалось определить адрес колонки"
    return text


# ---------------------------------------------------------------------------
# Каталоги
# ---------------------------------------------------------------------------


def is_local_page(current_url: str, local_url: str) -> bool:
    """True, если в окне открыт локальный лаунчер, а не панель колонки.

    Список колонок и панель живут в одном окне, поэтому мост в Python
    технически виден и удалённой странице. Методы API, которые трогают файлы
    и настройки, сверяются с этим признаком.
    """

    current = urlsplit((current_url or "").strip())
    local = urlsplit((local_url or "").strip())
    if not local.path or not current.path:
        return False
    if current.scheme != local.scheme or current.netloc != local.netloc:
        return False
    left = os.path.normcase(unquote(current.path))
    right = os.path.normcase(unquote(local.path))
    return left == right


def data_directory(portable: bool = False, base: Path | None = None) -> Path:
    """Каталог настроек: рядом с exe в portable-режиме, иначе %APPDATA%."""

    override = os.environ.get("MYPULT_HOME")
    if override:
        return Path(override).expanduser().resolve()

    if portable:
        if base is None:
            import sys

            base = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
        return (Path(base).resolve().parent / "data").resolve()

    appdata = os.environ.get("APPDATA")
    if appdata:
        return (Path(appdata) / "MidiPult").resolve()
    return (Path.home() / ".midipult").resolve()
