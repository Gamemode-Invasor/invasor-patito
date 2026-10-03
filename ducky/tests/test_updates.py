import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import updates

# The shape of the real index (rows trimmed).
INDEX = """<table>
<tr><td><a href="lsfg-vk-2.0.0.r13.g50845d4.tar.xz">&gt;&gt; Latest git version (2.0.0.r13.g50845d4) &lt;&lt;</a></td></tr>
<tr><td><a href="lsfg-vk-2.0.0.tar.xz">&gt;&gt; Latest release candidate (2.0.0) &lt;&lt;</a></td></tr>
<tr><td><a href="lsfg-vk-2.1.0.tar.xz">&gt;&gt; Latest release (2.1.0) &lt;&lt;</a></td></tr>
<tr><td><a href="lsfg-vk-2.0.0-rc1.tar.xz">lsfg-vk-2.0.0-rc1.tar.xz</a></td></tr>
</table>"""

LAYER_JSON = {"layer": {"name": "VK_LAYER_LSFGVK_frame_generation", "library_path": "../../../lib/liblsfg-vk-layer.so"}}


def fake_library(version):
    """Bytes shaped like the layer library: the version as a NUL-terminated string."""
    body = b"\x7fELF\x00junk\x00Invalid pacing mode\x00"
    return body + (version.encode() + b"\x00" if version else b"") + b"more\x00"


def tarball(version="2.1.0", extra=None, links=()):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:xz") as tar:
        def add(name, data, mode=0o644):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
        add("./bin/lsfg-vk-cli", b"#!cli", 0o755)
        add("./lib/liblsfg-vk-layer.so", fake_library(version))
        add(f"./{updates.LAYER}", json.dumps(LAYER_JSON).encode())
        for name, data in (extra or {}).items():
            add(name, data)
        for name in links:
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            tar.addfile(info)
    return out.getvalue()


class Versions(unittest.TestCase):
    def test_order(self):
        names = ["2.0.0-rc1", "2.0.0-rc1.r5.gfcd3e4b", "2.0.0", "2.0.0.r1.g0e7a389", "2.0.0.r13.g50845d4", "2.0.1", "2.1.0"]
        self.assertEqual(sorted(names, key=updates.parse_version), names)
        self.assertEqual(updates.parse_version("lsfg-vk-2.0.0.tar.xz"), updates.parse_version("2.0.0"))

    def test_compare(self):
        self.assertEqual(updates.compare("2.0.0", "lsfg-vk-2.1.0.tar.xz"), "newer_available")
        self.assertEqual(updates.compare("2.1.0", "lsfg-vk-2.1.0.tar.xz"), "up_to_date")
        self.assertEqual(updates.compare("2.1.0.r3.gabcdef0", "lsfg-vk-2.1.0.tar.xz"), "ahead")
        self.assertEqual(updates.compare(None, "lsfg-vk-2.1.0.tar.xz"), "newer_available")

    def test_latest_stable_from_the_index(self):
        self.assertEqual(updates.latest_stable(INDEX), "lsfg-vk-2.1.0.tar.xz")
        with self.assertRaises(updates.UpdateError):
            updates.latest_stable("<html>nothing</html>")
        with self.assertRaises(updates.UpdateError):
            updates.latest_stable('<a href="lsfg-vk-2.1.0.r2.gabc1234.tar.xz">Latest release (x)</a>')


class Compatibility(unittest.TestCase):
    def test_series(self):
        cases = {"2.0.0": "tested", "2.0.0.r5.gabc1234": "tested", "lsfg-vk-2.0.3.tar.xz": "tested",
                 "2.1.0": "newer_minor", "1.9.0": "unsupported", "3.0.0": "unsupported", None: None}
        for version, expected in cases.items():
            with self.subTest(version=version):
                self.assertEqual(updates.compatibility(version), expected)


class Installed(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        self.local = self.root / "home/.local"
        self.system = self.root / "usr"

    def put(self, prefix, version):
        (prefix / "lib").mkdir(parents=True, exist_ok=True)
        (prefix / "lib/liblsfg-vk-layer.so").write_bytes(fake_library(version))
        (prefix / "share/vulkan/implicit_layer.d").mkdir(parents=True, exist_ok=True)
        (prefix / updates.LAYER).write_text(json.dumps(LAYER_JSON))

    def dirs(self):
        return [self.local / "share/vulkan/implicit_layer.d", self.system / "share/vulkan/implicit_layer.d"]

    def test_version_read_from_the_library(self):
        for version in ("2.0.0", "2.0.0.r1.g0e7a389", "2.1.0-rc2"):
            with self.subTest(version=version):
                self.put(self.local, version)
                self.assertEqual(updates.installed(self.dirs(), self.local),
                                 {"version": version, "path": str(self.local / "lib/liblsfg-vk-layer.so"), "local": True})
        self.put(self.local, None)
        self.assertIsNone(updates.installed(self.dirs(), self.local)["version"])

    def test_user_install_wins_and_system_is_flagged(self):
        self.assertIsNone(updates.installed(self.dirs(), self.local))
        self.put(self.system, "2.0.0")
        self.assertEqual(updates.installed(self.dirs(), self.local)["local"], False)
        self.put(self.local, "2.1.0")
        self.assertEqual(updates.installed(self.dirs(), self.local)["version"], "2.1.0")


class Install(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.prefix = Path(d.name) / ".local"

    def test_installs_into_the_prefix(self):
        (self.prefix / "bin").mkdir(parents=True)
        (self.prefix / "bin/other-tool").write_text("keep me")
        files = updates.install(tarball("2.1.0"), "2.1.0", self.prefix)
        self.assertIn("lib/liblsfg-vk-layer.so", files)
        self.assertEqual(updates.library_version(self.prefix / "lib/liblsfg-vk-layer.so"), "2.1.0")
        self.assertTrue((self.prefix / "bin/lsfg-vk-cli").stat().st_mode & 0o100)
        self.assertEqual((self.prefix / "bin/other-tool").read_text(), "keep me")
        self.assertEqual([p.name for p in self.prefix.iterdir() if p.name.startswith(".")], [])  # no staging left

    def test_version_mismatch_is_reported(self):
        with self.assertRaisesRegex(updates.UpdateError, "reports"):
            updates.install(tarball("2.0.0"), "2.1.0", self.prefix)

    def test_bad_archives_change_nothing(self):
        cases = {
            "not xz": b"nope",
            "traversal": tarball(extra={"./bin/../../evil": b"x"}),
            "absolute": tarball(extra={"/etc/evil": b"x"}),
            "outside bin/lib/share": tarball(extra={"./etc/thing": b"x"}),
            "symlink": tarball(links=["./lib/link.so"]),
        }
        for label, data in cases.items():
            with self.subTest(case=label), self.assertRaises(updates.UpdateError):
                updates.install(data, "2.1.0", self.prefix)
        self.assertFalse((self.prefix / "lib").exists())

    def test_no_layer_no_install(self):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w:xz") as tar:
            info = tarfile.TarInfo("./bin/x")
            tar.addfile(info, io.BytesIO(b""))
        with self.assertRaisesRegex(updates.UpdateError, "Vulkan layer"):
            updates.install(out.getvalue(), "2.1.0", self.prefix)

    def test_downloads_only_stable_from_builds(self):
        for name in ("lsfg-vk-2.1.0.r2.gabc1234.tar.xz", "../x.tar.xz", "other.tar.xz"):
            with self.subTest(name=name), self.assertRaises(updates.UpdateError):
                updates.download(name, opener=lambda *a, **k: None)
        with self.assertRaises(updates.UpdateError):
            updates._get("https://evil.example/x", 10, opener=lambda *a, **k: None)


if __name__ == "__main__":
    unittest.main()
