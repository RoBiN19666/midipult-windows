"""MidiPult для Windows — оболочка вокруг веб-панели колонки.

Приложение находит колонку в локальной сети, хранит список станций и держит
и список, и панель в одном окне WebView2: панель загружается в то же окно,
а вернуться к списку можно кнопкой на плавающей панели.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

import webview

import update
from core import (
    StationStore,
    data_directory,
    discover,
    is_local_page,
    normalize_address,
    probe,
)

APP_NAME = "MidiPult"
APP_VERSION = "1.0.2"
APP_ID = "RoBiN19666.MidiPult.Windows"
WINDOW_TITLE = "MidiPult"
LIST_TITLE = "MidiPult — колонки"


def resource_path(*parts: str) -> Path:
    """Путь к ресурсу как в исходниках, так и внутри собранного exe."""

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def webview2_available() -> bool:
    """Проверяет установленный Edge WebView2 Runtime."""

    try:
        import winreg

        paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        ]
        for root, path in paths:
            try:
                with winreg.OpenKey(root, path) as key:
                    version, _ = winreg.QueryValueEx(key, "pv")
                    if version and version != "0.0.0.0":
                        return True
            except OSError:
                continue
    except Exception:
        pass

    for folder in (
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
    ):
        if folder and (Path(folder) / "Microsoft" / "EdgeWebView" / "Application").is_dir():
            return True
    return False


def setup_logging(data_dir: Path) -> None:
    try:
        logging.basicConfig(
            filename=str(data_dir / "midipult.log"),
            filemode="a",
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            encoding="utf-8",
        )
    except OSError:
        logging.basicConfig(level=logging.INFO)


def copy_to_clipboard(text: str) -> bool:
    """Кладёт строку в буфер обмена Windows без внешних утилит."""

    try:
        from ctypes import wintypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p

        if not user32.OpenClipboard(None):
            return False
        try:
            if not user32.EmptyClipboard():
                return False
            data = text.encode("utf-16-le") + b"\x00\x00"
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                return False
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                kernel32.GlobalFree(handle)
                return False
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
                return False
            return True
        finally:
            user32.CloseClipboard()
    except Exception:
        logging.exception("Не удалось скопировать в буфер обмена")
        return False


def apply_dark_title_bar(hwnd: int) -> bool:
    """Форсирует тёмный заголовок окна.

    pywebview вызывает ``update_title_bar_theme()``, который смотрит на тему
    Windows: при светлой системе заголовок становится белым и на тёмном окне
    выглядит инородной полосой. Здесь цвет задаётся явно.
    """

    if not hwnd:
        return False
    try:
        from ctypes import wintypes

        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
            result = dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                attribute,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            if result == 0:
                return True
    except Exception:
        logging.exception("Не удалось включить тёмный заголовок окна")
    return False


# Плавающая панель управления на странице колонки. Панель чужая (её отдаёт
# сама колонка), поэтому разметка живёт в shadow DOM и не пересекается с её
# стилями. Кнопки зовут api.nav(), который не трогает файлы и настройки.
PANEL_BAR_JS = """
(function () {
  if (document.getElementById('midipult-bar')) { return; }

  var host = document.createElement('div');
  host.id = 'midipult-bar';
  host.style.cssText = 'position:fixed;left:0;top:50%;transform:translateY(-50%);' +
    'z-index:2147483647;';

  var root = host.attachShadow({ mode: 'open' });
  root.innerHTML =
    '<style>' +
      ':host{all:initial}' +
      '.rail{display:flex;flex-direction:column;align-items:center;gap:2px;padding:5px 3px;' +
      'border-radius:0 13px 13px 0;background:rgba(13,15,29,.86);' +
      'border:1px solid rgba(120,130,190,.26);border-left:0;' +
      'box-shadow:0 10px 26px rgba(0,0,0,.42);backdrop-filter:blur(10px);' +
      'opacity:.5;transition:opacity .18s ease}' +
      ':host(:hover) .rail{opacity:1}' +
      'button{all:unset;box-sizing:border-box;width:32px;height:28px;display:grid;' +
      'place-items:center;border-radius:9px;color:#d5d9f2;cursor:pointer;' +
      'transition:background .15s ease,color .15s ease}' +
      'button:hover{background:rgba(140,150,220,.2);color:#fff}' +
      'button:active{transform:scale(.93)}' +
      'button:focus-visible{outline:2px solid #8f7dff;outline-offset:1px}' +
      'svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.7;' +
      'stroke-linecap:round;stroke-linejoin:round}' +
    '</style>' +
    '<div class="rail">' +
      '<button data-a="list" title="Список колонок" aria-label="Список колонок">' +
        '<svg viewBox="0 0 16 16"><path d="M9.5 3.5 5 8l4.5 4.5"/></svg>' +
      '</button>' +
      '<button data-a="reload" title="Обновить" aria-label="Обновить">' +
        '<svg viewBox="0 0 16 16"><path d="M13 8a5 5 0 1 1-1.6-3.7"/>' +
        '<path d="M13 3.2v3.2h-3.2"/></svg>' +
      '</button>' +
      '<button data-a="browser" title="Открыть в браузере" aria-label="Открыть в браузере">' +
        '<svg viewBox="0 0 16 16"><path d="M6.8 3.5H3.5v9h9V9.2"/>' +
        '<path d="M9.4 3.5h3.1v3.1"/><path d="M12.5 3.5 7.2 8.8"/></svg>' +
      '</button>' +
      '<button data-a="fullscreen" title="На весь экран" aria-label="На весь экран">' +
        '<svg viewBox="0 0 16 16"><path d="M6 3.5H3.5V6"/><path d="M10 3.5h2.5V6"/>' +
        '<path d="M6 12.5H3.5V10"/><path d="M10 12.5h2.5V10"/></svg>' +
      '</button>' +
    '</div>';

  function call(action) {
    var tries = 0;
    (function attempt() {
      var api = window.pywebview && window.pywebview.api;
      if (api && typeof api.nav === 'function') {
        try { api.nav(action); } catch (error) { /* мост мог не подняться */ }
        return;
      }
      tries += 1;
      if (tries < 50) { setTimeout(attempt, 100); }
    })();
  }

  root.querySelector('.rail').addEventListener('click', function (event) {
    var button = event.target.closest('button[data-a]');
    if (!button) { return; }
    event.preventDefault();
    event.stopPropagation();
    call(button.getAttribute('data-a'));
  });

  (document.body || document.documentElement).appendChild(host);
})();

"""


class Api:
    """Мост между страницами и Python.

    Окно одно, поэтому мост виден и удалённой панели колонки. Всё, что трогает
    файлы, настройки и поиск в сети, доступно только локальному лаунчеру;
    ``nav()`` — это оконный хром (назад, обновить, браузер, полный экран),
    он не принимает адреса и ничего не читает с диска.
    """

    def __init__(self, app: "MidiPultApp") -> None:
        # Подчёркивание важно: pywebview обходит публичные атрибуты объекта,
        # а ссылка на приложение содержит окна и .NET-объекты.
        self._app = app

    # -- доступ только со своей страницы ----------------------------------

    def _local_only(self) -> dict | None:
        if self._app.on_local_page():
            return None
        logging.info("Вызов API отклонён: открыта страница %s", self._app.current_url())
        return {"ok": False, "error": "Действие доступно только в списке колонок"}

    # -- состояние ---------------------------------------------------------

    def get_state(self) -> dict:
        blocked = self._local_only()
        return blocked or self._app.state()

    # -- поиск и CRUD ------------------------------------------------------

    def scan(self) -> dict:
        blocked = self._local_only()
        return blocked or self._app.scan()

    def add_station(self, address: str, name: str = "") -> dict:
        blocked = self._local_only()
        return blocked or self._app.add_station(address, name)

    def rename_station(self, station_id: str, name: str) -> dict:
        blocked = self._local_only()
        return blocked or self._app.rename_station(station_id, name)

    def delete_station(self, station_id: str) -> dict:
        blocked = self._local_only()
        return blocked or self._app.delete_station(station_id)

    def open_station(self, station_id: str) -> dict:
        blocked = self._local_only()
        return blocked or self._app.open_station(station_id)

    def open_data_folder(self) -> dict:
        blocked = self._local_only()
        return blocked or self._app.open_data_folder()

    # -- обновления --------------------------------------------------------

    def check_update(self) -> dict:
        blocked = self._local_only()
        return blocked or self._app.check_update()

    def install_update(self) -> dict:
        blocked = self._local_only()
        return blocked or self._app.install_update()

    def open_release_page(self) -> dict:
        blocked = self._local_only()
        return blocked or self._app.open_release_page()

    # -- хром окна ---------------------------------------------------------

    def nav(self, action: str) -> dict:
        """Кнопки на плавающей панели. Адреса сюда не передаются."""

        return self._app.nav(action)

    def quit(self) -> dict:
        blocked = self._local_only()
        if blocked:
            return blocked
        self._app.quit_app()
        return {"ok": True}


class MidiPultApp:
    def __init__(self, portable: bool = False, data_dir: Path | None = None) -> None:
        self.portable = portable
        self.data_dir = Path(data_dir).expanduser().resolve() if data_dir else data_directory(portable)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        setup_logging(self.data_dir)

        self.store = StationStore(self.data_dir)
        self.api = Api(self)
        self.window: webview.Window | None = None
        self.current_station = None
        self.quitting = False
        self.local_url = Path(resource_path("web", "index.html")).as_uri()

        update.cleanup_old_executable()
        self._clean_update_cache()

        logging.info("Запуск MidiPult %s, данные в %s", APP_VERSION, self.data_dir)

    def _clean_update_cache(self) -> None:
        """Убирает остатки прошлых обновлений из %APPDATA%\\MidiPult\\updates."""

        folder = self.data_dir / "updates"
        try:
            if not folder.is_dir():
                return
            for item in folder.iterdir():
                if item.is_file():
                    item.unlink(missing_ok=True)
        except OSError:
            logging.info("Не удалось очистить кэш обновлений", exc_info=True)

    # ------------------------------------------------------------------
    # Состояние для интерфейса
    # ------------------------------------------------------------------

    def state(self) -> dict:
        return {
            "ok": True,
            "version": APP_VERSION,
            "data_dir": str(self.data_dir),
            "portable": self.portable,
            "active_id": self.store.active_id,
            "stations": self.store.as_list(),
        }

    def current_url(self) -> str:
        window = self.window
        if window is None:
            return ""
        try:
            return window.get_current_url() or ""
        except Exception:
            return ""

    def on_local_page(self) -> bool:
        """Открыт ли сейчас локальный лаунчер, а не страница колонки."""

        return is_local_page(self.current_url(), self.local_url)

    # ------------------------------------------------------------------
    # Окно: список колонок и панель живут в одном окне
    # ------------------------------------------------------------------

    def _load(self, url: str) -> None:
        """Загружает адрес в окно, не блокируя поток обработки вызова JS."""

        window = self.window
        if window is None or not url:
            return

        def run() -> None:
            try:
                window.load_url(url)
            except Exception:
                logging.exception("Не удалось открыть %s", url)

        threading.Timer(0.05, run).start()

    def _set_title(self, title: str) -> None:
        window = self.window
        if window is None:
            return
        try:
            window.set_title(title)
        except Exception:
            logging.exception("Не удалось изменить заголовок окна")

    def show_list(self) -> dict:
        self.current_station = None
        self._set_title(LIST_TITLE)
        self._load(self.local_url)
        return {"ok": True}

    def nav(self, action: str) -> dict:
        name = (action or "").strip().lower()
        if name == "list":
            return self.show_list()
        if name == "reload":
            return self._reload()
        if name in ("browser", "open_browser"):
            return self._open_in_browser()
        if name == "copy":
            return self._copy_address()
        if name == "fullscreen":
            return self._toggle_fullscreen()
        return {"ok": False, "error": f"Неизвестное действие: {action}"}

    def _reload(self) -> dict:
        url = self.current_url()
        if not url and self.current_station is not None:
            url = self.current_station.url
        if not url:
            return {"ok": False, "error": "Нечего обновлять"}
        self._load(url)
        return {"ok": True}

    def _open_in_browser(self) -> dict:
        url = self.current_station.url if self.current_station is not None else ""
        if not url:
            return {"ok": False, "error": "Колонка не выбрана"}
        webbrowser.open(url)
        return {"ok": True}

    def _copy_address(self) -> dict:
        url = self.current_station.url if self.current_station is not None else ""
        if not url:
            return {"ok": False, "error": "Колонка не выбрана"}
        return {"ok": copy_to_clipboard(url)}

    def _toggle_fullscreen(self) -> dict:
        window = self.window
        if window is None:
            return {"ok": False, "error": "Окно не готово"}
        try:
            window.toggle_fullscreen()
        except Exception:
            logging.exception("Не удалось переключить полноэкранный режим")
            return {"ok": False, "error": "Не удалось переключить режим"}
        return {"ok": True}

    # ------------------------------------------------------------------
    # Действия лаунчера
    # ------------------------------------------------------------------

    def scan(self) -> dict:
        try:
            found = discover()
        except OSError as exc:
            logging.warning("UDP-поиск не удался: %s", exc)
            return {
                "ok": False,
                "error": "Не удалось выполнить поиск в сети. Проверьте подключение "
                "к Wi-Fi или добавьте колонку по адресу.",
                "detail": str(exc),
                "state": self.state(),
            }

        for station in found:
            self.store.upsert(station, keep_name=True)
        logging.info("Поиск завершён, найдено: %d", len(found))
        return {"ok": True, "found": len(found), "state": self.state()}

    def add_station(self, address: str, name: str = "") -> dict:
        try:
            station = normalize_address(address)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        clean_name = (name or "").strip()
        station.name = clean_name or f"Колонка {station.ip}"
        saved = self.store.upsert(station, keep_name=not clean_name)
        return {"ok": True, "station": saved.to_dict(), "state": self.state()}

    def rename_station(self, station_id: str, name: str) -> dict:
        clean = (name or "").strip()
        if not clean:
            return {"ok": False, "error": "Название не может быть пустым"}
        if len(clean) > 80:
            return {"ok": False, "error": "Название слишком длинное (до 80 символов)"}
        station = self.store.rename(station_id, clean)
        if station is None:
            return {"ok": False, "error": "Колонка не найдена"}
        if self.current_station is not None and self.current_station.id == station.id:
            self.current_station = station
            self._set_title(f"{station.name} — MidiPult")
        return {"ok": True, "state": self.state()}

    def delete_station(self, station_id: str) -> dict:
        if not self.store.remove(station_id):
            return {"ok": False, "error": "Колонка не найдена"}
        return {"ok": True, "state": self.state()}

    def open_station(self, station_id: str) -> dict:
        station = self.store.find(station_id)
        if station is None:
            return {"ok": False, "error": "Колонка не найдена"}

        ok, detail = probe(station.url)
        if not ok:
            logging.warning("Панель %s недоступна: %s", station.url, detail)
            return {
                "ok": False,
                "station_id": station.id,
                "error": f"Не удалось подключиться к «{station.name}»: {detail}",
            }

        self.store.set_active(station.id)
        self.current_station = station
        self._set_title(f"{station.name} — MidiPult")
        self._load(station.url)
        return {"ok": True}

    def open_data_folder(self) -> dict:
        try:
            os.startfile(str(self.data_dir))  # type: ignore[attr-defined]
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Обновления
    # ------------------------------------------------------------------

    def check_update(self) -> dict:
        page = update.RELEASE_PAGE
        if not update.is_frozen():
            return {
                "ok": False,
                "url": page,
                "error": "Приложение запущено из исходников — обновитесь вручную.",
            }

        installed = update.is_installed()
        try:
            release = update.fetch_release()
        except Exception as exc:  # сеть, TLS, лимиты GitHub
            logging.warning("Проверка обновлений не удалась: %s", exc)
            return {"ok": False, "url": page, "error": f"Не удалось проверить обновления: {exc}"}

        if release is None:
            return {"ok": False, "url": page, "error": "В репозитории нет опубликованных релизов."}

        pair = release.setup if installed else release.exe
        return {
            "ok": True,
            "current": APP_VERSION,
            "latest": release.version,
            "available": update.is_newer(release.version, APP_VERSION),
            "can_install": bool(pair),
            "installed": installed,
            "url": release.page_url,
        }

    def install_update(self) -> dict:
        page = update.RELEASE_PAGE
        if not update.is_frozen():
            return {
                "ok": False,
                "url": page,
                "error": "Приложение запущено из исходников — обновитесь вручную.",
            }

        installed = update.is_installed()
        try:
            release = update.fetch_release()
        except Exception as exc:
            return {"ok": False, "url": page, "error": f"Не удалось получить релиз: {exc}"}

        if release is None:
            return {"ok": False, "url": page, "error": "В репозитории нет опубликованных релизов."}
        if not update.is_newer(release.version, APP_VERSION):
            return {"ok": False, "error": f"Установлена последняя версия — {APP_VERSION}."}

        artifact = release.setup if installed else release.exe
        if artifact is None:
            return {
                "ok": False,
                "url": page,
                "error": "В релизе нет подходящего файла — обновитесь вручную.",
            }

        expected = artifact.sha256
        if not expected and artifact.checksum_url:
            try:
                expected = update.parse_sha256(
                    update.fetch_text(artifact.checksum_url), artifact.name
                )
            except Exception as exc:
                return {"ok": False, "url": page, "error": f"Не удалось получить SHA-256: {exc}"}
        if not expected:
            return {
                "ok": False,
                "url": page,
                "error": "В релизе нет корректного SHA-256 — обновление отменено.",
            }

        name = artifact.name

        target_dir = self.data_dir / "updates" if installed else update.executable_path().parent
        # Портативную версию нельзя писать прямо в имя работающего файла.
        final = target_dir / name if installed else target_dir / (name + ".new")
        partial = final.with_name(final.name + ".part")

        try:
            update.download(artifact.url, partial)
        except Exception as exc:
            self._discard(partial)
            return {"ok": False, "url": page, "error": f"Не удалось скачать обновление: {exc}"}

        actual = update.sha256_file(partial)
        if actual != expected:
            self._discard(partial)
            logging.error("SHA-256 обновления не совпал: %s != %s", actual, expected)
            return {"ok": False, "url": page, "error": "SHA-256 не совпал — обновление отменено."}

        try:
            partial.replace(final)
        except OSError as exc:
            self._discard(partial)
            return {"ok": False, "url": page, "error": f"Не удалось сохранить файл обновления: {exc}"}

        logging.info("Обновление %s загружено: %s", release.version, final)

        if installed:
            if not update.run_installer(final):
                return {"ok": False, "url": page, "error": "Не удалось запустить установщик."}
        else:
            swapped, error = update.swap_portable(final)
            if not swapped:
                return {"ok": False, "url": page, "error": error}
            update.restart(update.executable_path())

        threading.Timer(0.4, self.quit_app).start()
        return {"ok": True, "restart": True}

    def open_release_page(self) -> dict:
        webbrowser.open(update.RELEASE_PAGE)
        return {"ok": True}

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def quit_app(self) -> None:
        self.quitting = True
        window = self.window
        if window is None:
            return
        try:
            window.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Запуск
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.window = webview.create_window(
            LIST_TITLE,
            self.local_url,
            js_api=self.api,
            width=1180,
            height=820,
            min_size=(720, 520),
            background_color="#0b0c18",
            text_select=True,
        )
        if self.window is not None:
            self.window.events.shown += self._on_shown
            self.window.events.loaded += self._on_loaded
            self.window.events.closed += self._on_closed

        icon = resource_path("assets", "icon.ico")
        webview.start(
            icon=str(icon) if icon.exists() else None,
            private_mode=False,
            storage_path=str(self.data_dir / "webview"),
            debug=os.environ.get("MYPULT_DEBUG") == "1",
        )

    def _on_shown(self) -> None:
        self._apply_dark_title()

    def _on_loaded(self) -> None:
        self._apply_dark_title()
        if self.current_station is not None:
            self._inject_panel_bar()

    def _on_closed(self) -> None:
        self.quitting = True

    def _apply_dark_title(self) -> None:
        hwnd = self._window_handle()
        if hwnd:
            apply_dark_title_bar(hwnd)

    def _window_handle(self) -> int:
        window = self.window
        if window is None:
            return 0

        gui = getattr(window, "gui", None)
        try:
            handle = getattr(gui, "Handle", None)
            if handle is not None:
                return int(handle.ToInt32())
        except Exception:
            pass

        for title in (LIST_TITLE, WINDOW_TITLE):
            try:
                hwnd = ctypes.windll.user32.FindWindowW(None, title)
                if hwnd:
                    return int(hwnd)
            except Exception:
                pass
        return 0

    def _inject_panel_bar(self) -> None:
        window = self.window
        if window is None:
            return
        try:
            window.evaluate_js(PANEL_BAR_JS)
        except Exception:
            logging.exception("Не удалось показать панель управления колонкой")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="MidiPult", description="Клиент MidiPult для Windows"
    )
    parser.add_argument(
        "--portable",
        action="store_true",
        help="хранить настройки рядом с exe в папке data",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="свой каталог настроек",
    )
    parser.add_argument("--version", action="version", version=APP_VERSION)
    args = parser.parse_args(argv)

    if os.name != "nt":
        print("MidiPult для Windows запускается только на Windows.", file=sys.stderr)
        return 1

    try:
        from ctypes import wintypes

        shell32 = ctypes.windll.shell32
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
        shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

    if not webview2_available():
        ctypes.windll.user32.MessageBoxW(
            0,
            "Для работы MidiPult нужен Microsoft Edge WebView2 Runtime.\n\n"
            "Скачайте и установите Evergreen Runtime с сайта Microsoft:\n"
            "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
            "В Windows 11 он обычно уже установлен.",
            "MidiPult — не найден WebView2",
            0x10,
        )
        return 1

    app = MidiPultApp(portable=args.portable, data_dir=args.data_dir)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
