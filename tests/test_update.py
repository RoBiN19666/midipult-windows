from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from update import (  # noqa: E402
    allowed_url,
    is_newer,
    parse_release,
    parse_sha256,
    version_tuple,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
REPO = "RoBiN19666/midipult-windows"


def make_payload(assets, tag="windows-v1.2.0", draft=False, prerelease=False):
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "html_url": f"https://github.com/{REPO}/releases/tag/{tag}",
        "assets": [
            {
                "name": item[0],
                "browser_download_url": item[1]
                or f"https://github.com/{REPO}/releases/download/{tag}/{item[0]}",
            }
            for item in assets
        ],
    }


class VersionTests(unittest.TestCase):
    def test_parses_plain_and_prefixed(self):
        self.assertEqual(version_tuple("1.2.3"), (1, 2, 3))
        self.assertEqual(version_tuple("v1.2.3"), (1, 2, 3))
        self.assertEqual(version_tuple("1.0"), (1, 0, 0))

    def test_compares(self):
        self.assertTrue(is_newer("1.0.1", "1.0.0"))
        self.assertFalse(is_newer("1.0.0", "1.0.0"))
        self.assertFalse(is_newer("0.9.9", "1.0.0"))
        self.assertTrue(is_newer("1.1", "1.0.9"))
        self.assertTrue(is_newer("v2.0.0", "1.9.9"))


class UrlTests(unittest.TestCase):
    def test_https_github_only(self):
        self.assertTrue(allowed_url(f"https://github.com/{REPO}/releases/download/v1/MidiPult.exe"))
        self.assertTrue(allowed_url("https://api.github.com/repos/x/y/releases/latest"))
        self.assertTrue(allowed_url("https://objects.githubusercontent.com/abc"))
        self.assertFalse(allowed_url("http://github.com/x"))
        self.assertFalse(allowed_url("https://evil.example.com/MidiPult.exe"))
        self.assertFalse(allowed_url("https://github.com.evil.com/x"))
        self.assertFalse(allowed_url(""))
        self.assertFalse(allowed_url("ftp://github.com/x"))


class ShaTests(unittest.TestCase):
    def test_reads_sha256sum_format(self):
        self.assertEqual(parse_sha256(SHA_A + "  MidiPult.exe"), SHA_A)
        self.assertEqual(parse_sha256(SHA_A + " *MidiPult.exe"), SHA_A)
        self.assertEqual(parse_sha256(SHA_A.upper() + "  file"), SHA_A)

    def test_filename_must_match(self):
        self.assertEqual(parse_sha256(SHA_A + "  other.exe", "MidiPult.exe"), "")
        self.assertEqual(parse_sha256(SHA_A + "  MidiPult.exe", "MidiPult.exe"), SHA_A)

    def test_rejects_garbage(self):
        self.assertEqual(parse_sha256("not a hash"), "")
        self.assertEqual(parse_sha256(""), "")
        self.assertEqual(parse_sha256("# comment"), "")


class ReleaseTests(unittest.TestCase):
    def _full(self):
        return make_payload(
            [
                ("MidiPult.exe", None),
                ("MidiPult.exe.sha256", None),
                ("MidiPult-1.2.0-setup.exe", None),
                ("MidiPult-1.2.0-setup.exe.sha256", None),
            ]
        )

    def test_full_release(self):
        release = parse_release(self._full())
        self.assertIsNotNone(release)
        assert release is not None
        self.assertEqual(release.version, "1.2.0")
        self.assertIsNotNone(release.setup)
        self.assertIsNotNone(release.exe)
        assert release.setup is not None
        self.assertTrue(release.setup[0].endswith("MidiPult-1.2.0-setup.exe"))
        self.assertTrue(release.setup[1].endswith("-setup.exe.sha256"))

    def test_draft_and_prerelease_are_skipped(self):
        self.assertIsNone(parse_release(make_payload([("MidiPult.exe", None)], draft=True)))
        self.assertIsNone(parse_release(make_payload([("MidiPult.exe", None)], prerelease=True)))

    def test_asset_without_checksum_is_not_offered(self):
        payload = make_payload([("MidiPult.exe", None)])
        self.assertIsNone(parse_release(payload))

        payload = make_payload([("MidiPult.exe", None), ("MidiPult.exe.sha256", None)])
        release = parse_release(payload)
        self.assertIsNotNone(release)
        assert release is not None
        self.assertIsNotNone(release.exe)
        self.assertIsNone(release.setup)

    def test_foreign_host_assets_are_ignored(self):
        payload = make_payload(
            [
                ("MidiPult.exe", "https://evil.example.com/MidiPult.exe"),
                ("MidiPult.exe.sha256", "https://evil.example.com/MidiPult.exe.sha256"),
            ]
        )
        self.assertIsNone(parse_release(payload))

    def test_version_falls_back_to_tag(self):
        payload = make_payload([("MidiPult.exe", None), ("MidiPult.exe.sha256", None)])
        payload["tag_name"] = "v1.4.0"
        release = parse_release(payload)
        assert release is not None
        self.assertEqual(release.version, "1.4.0")


if __name__ == "__main__":
    unittest.main()
