import importlib.util
import json
import os
import logging
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
# Invasor's core (ctx.toml, forms): $INVASOR_CORE (set by tools/check_module.py), else the
# core this module sits in (modules/<id>), else a checkout next to this repository
# (<repo>/<id>/ -> ../invasor).
_core = os.environ.get("INVASOR_CORE") or next(
    (str(p) for p in (HERE.parents[1], HERE.parents[1] / "invasor") if (p / "backend/invasor").is_dir()), "")
sys.path.insert(0, str(Path(_core) / "backend"))

from invasor import schema, tomlio  # noqa: E402
from invasor.modules import Form  # noqa: E402

# The shape of a real lsfg-vk v2 config (made-up values).
CONF = """version = 2

[global]
allow_fp16 = true
log_level = "info"

[[profile]]
active_in = [ "1000" ]
flow_scale = 0.75
multiplier = 2
name = "default"
override_present_mode = true
pacing_mode = "vsync"
performance_mode = true
preserve_swapchain_image_count = false
unknown_future_key = "kept"
"""


def load_backend():
    name = "ducky_backend_under_test"
    for n in [n for n in sys.modules if n == name or n.startswith(name + ".")]:
        del sys.modules[n]
    spec = importlib.util.spec_from_file_location(name, HERE / "backend.py", submodule_search_locations=[str(HERE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeGame:
    def info(self, appid):
        return {"1000": {"appid": "1000", "name": "Test Game", "shortcut": False}}.get(str(appid))

    def shortcut_exe(self, appid):
        return {"3000000001": '/games/Thing/Thing.exe'}.get(str(appid))


class FakeCtx:
    InvalidArgument = schema.InvalidArgument
    Unavailable = schema.Unavailable
    toml = tomlio

    def __init__(self):
        manifest = schema.parse_manifest(json.loads((HERE / "module.json").read_text()), "ducky")
        self.forms = {n: Form(f) for n, f in manifest["form_fields"].items()}
        self.game = FakeGame()
        self.log = logging.getLogger("test.ducky")


class Backend(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = Path(d.name) / "lsfg-vk" / "conf.toml"
        self.path.parent.mkdir()
        self.path.write_text(CONF)
        self.b = load_backend()
        self.b.lsfg.config_path = lambda env=None: self.path
        self.b._cli = lambda: None  # tests never ask the real lsfg-vk-cli unless they say so
        self.b.setup(FakeCtx())

    def file(self):
        return tomllib.loads(self.path.read_text())

    def test_profile_values_are_validated_and_everything_else_kept(self):
        self.assertEqual(self.b.profile_get("default")["flow_scale"], 0.75)
        self.assertEqual(self.b.profile_set("default", "multiplier", 99), 20)  # clamped by the form
        self.assertEqual(self.b.profile_set("default", "flow_scale", 0.31), 0.3)
        with self.assertRaises(schema.InvalidArgument):
            self.b.profile_set("default", "pacing_mode", "none")  # vsync is the only mode today
        with self.assertRaises(schema.InvalidArgument):
            self.b.profile_set("default", "name", "x")
        p = self.file()["profile"][0]
        self.assertEqual((p["multiplier"], p["flow_scale"], p["unknown_future_key"], p["active_in"]), (20, 0.3, "kept", ["1000"]))
        self.assertTrue(self.path.with_name("conf.toml.invasor-backup").exists())

    def test_per_game_profile(self):
        self.b.profile_create("3x")
        self.assertEqual(self.b.game_profile("1000"), {"entry": "1000", "profile": "default", "custom": True})
        self.b.set_game_profile("1000", False, "3x")
        self.b.set_game_profile("3000000001", True, "3x")
        self.assertEqual(self.file()["profile"][1]["active_in"], ["1000", "3000000001"])
        self.assertNotIn("active_in", self.file()["profile"][0])
        self.assertEqual(self.b.profiles()[1]["games"], [{"entry": "1000", "name": "Test Game"}, {"entry": "3000000001", "name": None}])
        self.b.set_game_profile("1000", False, None)
        self.assertIsNone(self.b.game_profile("1000")["profile"])
        with self.assertRaises(schema.InvalidArgument):
            self.b.set_game_profile("1000", False, "ghost")

    def test_custom_profile_for_a_game(self):
        self.b.set_game_profile("2000", False, "default")  # "default" is now shared by two games
        self.assertFalse(self.b.game_profile("1000")["custom"])
        made = self.b.make_custom("1000")  # title from the game's name
        self.assertEqual(made, {"entry": "1000", "profile": "Test Game", "custom": True})
        self.assertEqual(self.b.game_profile("1000"), {"entry": "1000", "profile": "Test Game", "custom": True})
        own = self.file()["profile"][1]
        self.assertEqual((own["name"], own["flow_scale"], own["active_in"]), ("Test Game", 0.75, ["1000"]))
        self.assertEqual(self.file()["profile"][0]["active_in"], ["2000"])
        self.assertEqual(self.b.make_custom("1000")["profile"], "Test Game")  # already its own: kept

    def test_global(self):
        self.assertEqual(self.b.global_get(), {"allow_fp16": True, "log_level": "info", "dll": ""})
        self.b.global_set("log_level", "debug")
        self.b.global_set("dll", "/x/Lossless.dll")
        self.b.global_set("dll", "")
        self.assertEqual(self.file()["global"], {"allow_fp16": True, "log_level": "debug"})

    def test_profile_management_errors_are_clean(self):
        for call, args in (("profile_create", ("default",)), ("profile_rename", ("ghost", "x")),
                           ("profile_delete", ("ghost",)), ("profile_get", ("ghost",))):
            with self.subTest(call=call), self.assertRaises(schema.InvalidArgument) as cm:
                getattr(self.b, call)(*args)
            self.assertNotIn("'\"", str(cm.exception))

    def test_unsupported_or_broken_files_are_never_written(self):
        self.path.write_text("version = 3\n")
        with self.assertRaises(schema.Unavailable):
            self.b.profile_create("x")
        self.assertEqual(self.path.read_text(), "version = 3\n")
        self.assertIn("version", self.b.status()["error"])
        self.path.write_text("version = = 2")
        with self.assertRaises(schema.InvalidArgument):
            self.b.profiles()

    def test_missing_file_is_created_on_first_change(self):
        self.path.unlink()
        self.assertEqual(self.b.profiles(), [])
        self.b.profile_create("default")
        self.assertEqual(self.file()["version"], 2)
        self.assertEqual(self.b.profiles()[0]["name"], "default")


    def test_updates_only_install_when_newer(self):
        u = self.b.updates
        calls = []
        u.fetch_latest = lambda opener=None: "lsfg-vk-2.1.0.tar.xz"
        u.download = lambda name, opener=None: calls.append(name) or b"data"
        u.install = lambda data, expected, prefix=None: calls.append(expected) or ["lib/x"]
        for have, state in ((None, "not_installed"), ({"version": "2.1.0", "path": "/x", "local": True}, "up_to_date"),
                            ({"version": "2.1.0.r2.gabc1234", "path": "/x", "local": True}, "ahead"),
                            ({"version": "2.0.0", "path": "/usr/lib/x", "local": False}, "system"),
                            ({"version": "2.0.0", "path": "/x", "local": True}, "newer_available")):
            u.installed = lambda have=have: have
            with self.subTest(state=state):
                self.assertEqual(self.b.update_status()["state"], state)
                if state in ("up_to_date", "ahead", "system"):
                    with self.assertRaises(schema.InvalidArgument):
                        self.b.update_install()
                else:
                    self.assertEqual(self.b.update_install(), {"installed": "2.1.0"})
        self.assertEqual(calls, ["lsfg-vk-2.1.0.tar.xz", "2.1.0", "lsfg-vk-2.1.0.tar.xz", "2.1.0"])


    def test_a_new_major_version_is_never_installed(self):
        u = self.b.updates
        u.fetch_latest = lambda opener=None: "lsfg-vk-3.0.0.tar.xz"
        u.installed = lambda: {"version": "2.0.0", "path": "/x", "local": True}
        u.download = lambda *a, **k: self.fail("must not download")
        st = self.b.update_status()
        self.assertEqual((st["state"], st["latest_compat"], st["installed_compat"]), ("newer_available", "unsupported", "tested"))
        with self.assertRaisesRegex(schema.InvalidArgument, "new major version"):
            self.b.update_install()


    def fake_cli(self, code, message=""):
        p = self.path.parent / "fake-cli"
        p.write_text(f"#!/bin/sh\necho 'Validating configuration file: x'\necho '{message}'\nexit {code}\n")
        p.chmod(0o755)
        self.b._cli = lambda: str(p)

    def test_lsfg_vk_validates_every_change(self):
        before = self.path.read_text()
        self.fake_cli(1, "Unknown key in profile section: unknown_future_key")
        with self.assertRaisesRegex(schema.InvalidArgument, "lsfg-vk rejected the change: Unknown key"):
            self.b.profile_set("default", "multiplier", 3)
        self.assertEqual(self.path.read_text(), before)  # original untouched
        self.assertIn("Unknown key", self.b.status()["lsfg_problem"])
        self.fake_cli(0, "The configuration file is valid.")
        self.assertEqual(self.b.profile_set("default", "multiplier", 3), 3)
        self.assertEqual(self.file()["profile"][0]["multiplier"], 3)
        self.assertIsNone(self.b.status()["lsfg_problem"])

    def test_a_broken_validator_does_not_block(self):
        self.b._cli = lambda: str(self.path.parent / "missing-cli")
        with self.assertLogs("test.ducky", "WARNING"):
            self.assertEqual(self.b.profile_set("default", "multiplier", 4), 4)


    def test_older_core_without_the_validate_hook(self):
        real_save = self.b.ctx.toml.save

        class OldToml:
            dumps = staticmethod(self.b.ctx.toml.dumps)
            load = staticmethod(self.b.ctx.toml.load)

            @staticmethod
            def save(path, data, expected_mtime=None, backup=True):
                return real_save(path, data, expected_mtime, backup)

        self.b.ctx.toml = OldToml
        before = self.path.read_text()
        self.fake_cli(1, "Invalid pacing mode: x")
        with self.assertRaisesRegex(schema.InvalidArgument, "rejected"):
            self.b.profile_set("default", "multiplier", 5)
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual([p.name for p in self.path.parent.iterdir() if ".check-" in p.name], [])
        self.fake_cli(0)
        self.assertEqual(self.b.profile_set("default", "multiplier", 5), 5)


if __name__ == "__main__":
    unittest.main()
