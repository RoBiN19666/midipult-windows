from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import (  # noqa: E402
    StationStore,
    discover,
    is_local_page,
    normalize_address,
    probe,
    station_from_discovery,
)


class AddressTests(unittest.TestCase):
    def test_plain_ip_adds_scheme_and_port(self):
        station = normalize_address("192.168.1.10")
        self.assertEqual(station.url, "http://192.168.1.10:8055/")
        self.assertEqual(station.ip, "192.168.1.10")

    def test_ip_with_port(self):
        station = normalize_address("192.168.1.10:9000")
        self.assertEqual(station.url, "http://192.168.1.10:9000/")

    def test_url_with_path_and_query(self):
        station = normalize_address("http://station.local:8055/panel?x=1")
        self.assertEqual(station.url, "http://station.local:8055/panel?x=1")

    def test_https_and_ipv6(self):
        station = normalize_address("https://[fe80::1]:8443")
        self.assertEqual(station.url, "https://[fe80::1]:8443/")
        self.assertEqual(station.ip, "fe80::1")

    def test_empty_and_bad_values(self):
        for value in ("", "   ", "ftp://host", "http://", "http://host:99999"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_address(value)


class DiscoveryPayloadTests(unittest.TestCase):
    def test_parses_valid_response(self):
        station = station_from_discovery(
            {
                "service": "midipult",
                "name": "Кухня",
                "ip": "192.168.0.50",
                "port": 8055,
                "model": "Station Midi",
                "version": "0.5.64",
                "id": "abc",
            },
            "192.168.0.50",
        )
        self.assertIsNotNone(station)
        self.assertEqual(station.name, "Кухня")
        self.assertEqual(station.url, "http://192.168.0.50:8055/")
        self.assertEqual(station.model, "Station Midi")
        self.assertEqual(station.id, "abc")

    def test_uses_sender_ip_and_fallback_name(self):
        station = station_from_discovery(
            {"service": "MidiPult", "port": 7000}, "10.0.0.7"
        )
        self.assertIsNotNone(station)
        self.assertEqual(station.url, "http://10.0.0.7:7000/")
        self.assertEqual(station.name, "Колонка 10.0.0.7")

    def test_ignores_foreign_packets(self):
        self.assertIsNone(station_from_discovery({"service": "other"}, "10.0.0.7"))
        self.assertIsNone(station_from_discovery({}, "10.0.0.7"))
        self.assertIsNone(station_from_discovery({"service": "midipult", "ip": ""}, ""))


class StoreTests(unittest.TestCase):
    def test_crud_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StationStore(Path(tmp))
            station = normalize_address("192.168.1.10")
            station.name = "Гостиная"
            store.upsert(station)

            self.assertEqual(len(store.stations), 1)
            self.assertTrue((Path(tmp) / "stations.json").exists())

            reloaded = StationStore(Path(tmp))
            self.assertEqual(reloaded.stations[0].name, "Гостиная")

            reloaded.rename(reloaded.stations[0].id, "Кухня")
            self.assertEqual(StationStore(Path(tmp)).stations[0].name, "Кухня")

            reloaded.set_active(reloaded.stations[0].id)
            self.assertEqual(StationStore(Path(tmp)).active_id, reloaded.stations[0].id)

            self.assertTrue(reloaded.remove(reloaded.stations[0].id))
            self.assertEqual(StationStore(Path(tmp)).stations, [])

    def test_upsert_keeps_user_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StationStore(Path(tmp))
            first = normalize_address("192.168.1.10")
            first.name = "Моё имя"
            store.upsert(first)

            found = station_from_discovery(
                {"service": "midipult", "ip": "192.168.1.10", "name": "Другое имя"},
                "192.168.1.10",
            )
            store.upsert(found, keep_name=True)
            self.assertEqual(store.stations[0].name, "Моё имя")


class DiscoverySocketTests(unittest.TestCase):
    def test_udp_discovery_returns_station(self):
        responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        responder.bind(("127.0.0.1", 0))
        port = responder.getsockname()[1]
        payload = json.dumps(
            {
                "service": "midipult",
                "name": "Тестовая",
                "ip": "127.0.0.1",
                "port": 8055,
            }
        ).encode()

        def serve():
            try:
                data, address = responder.recvfrom(1024)
                if data == b"MYPULT_DISCOVER":
                    responder.sendto(payload, address)
            finally:
                responder.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        found = discover(timeout=2.0, targets=["127.0.0.1"], port=port)
        thread.join(timeout=2)

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "Тестовая")
        self.assertEqual(found[0].url, "http://127.0.0.1:8055/")


class ProbeTests(unittest.TestCase):
    def _serve(self, status):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(status)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_ok_and_auth_are_reachable(self):
        for status in (200, 401, 403):
            with self.subTest(status=status):
                port = self._serve(status)
                ok, detail = probe(f"http://127.0.0.1:{port}/", timeout=2)
                self.assertTrue(ok, detail)

    def test_closed_port_is_unreachable(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        ok, detail = probe(f"http://127.0.0.1:{port}/", timeout=2)
        self.assertFalse(ok)
        self.assertTrue(detail)


class LocalPageTests(unittest.TestCase):
    """Мост в Python должен работать только на локальном лаунчере."""

    def test_same_page_is_local(self):
        local = "file:///C:/Apps/MidiPult/web/index.html"
        self.assertTrue(is_local_page(local, local))
        self.assertTrue(is_local_page(local + "#stations", local))

    def test_station_panel_is_not_local(self):
        local = "file:///C:/Apps/MidiPult/web/index.html"
        for current in (
            "http://192.168.1.10:8055/",
            "http://192.168.1.10:8055/index.html",
            "https://192.168.1.10:8055/",
            "file:///C:/Windows/System32/drivers/etc/hosts",
            "",
        ):
            with self.subTest(current=current):
                self.assertFalse(is_local_page(current, local))

    def test_path_comparison_is_case_insensitive_on_windows(self):
        self.assertTrue(
            is_local_page(
                "file:///C:/Apps/MidiPult/web/index.html",
                "file:///c:/apps/midipult/web/index.html",
            )
        )


if __name__ == "__main__":
    unittest.main()
