import unittest
from pathlib import Path

import lsfg


def config():
    return {"version": 2, "global": {"allow_fp16": True}, "profile": [
        {"active_in": ["1000", "Other.exe"], "multiplier": 2, "name": "default"},
        {"multiplier": 3, "name": "3x"},
    ]}


class Paths(unittest.TestCase):
    def test_config_path(self):
        self.assertEqual(lsfg.config_path({"LSFGVK_CONFIG": "/x/c.toml"}), Path("/x/c.toml"))
        self.assertEqual(lsfg.config_path({"XDG_CONFIG_HOME": "/cfg"}), Path("/cfg/lsfg-vk/conf.toml"))
        self.assertEqual(lsfg.config_path({}), Path.home() / ".config/lsfg-vk/conf.toml")


class Profiles(unittest.TestCase):
    def test_check(self):
        lsfg.check(config())
        for bad in ({"version": 1}, {"version": 2, "global": []}, {"version": 2, "profile": {"name": "x"}}):
            with self.subTest(data=bad), self.assertRaises(ValueError):
                lsfg.check(bad)

    def test_create_copy_rename_delete(self):
        data = config()
        p = lsfg.create(data, " New ", {"multiplier": 2, "flow_scale": 1.0})
        self.assertEqual(list(p), ["flow_scale", "multiplier", "name"])  # alphabetical, like lsfg-vk-ui
        self.assertEqual(p["name"], "New")
        copy = lsfg.create(data, "Copy", {}, copy_from="default")
        self.assertNotIn("active_in", copy)  # its games stay with the original
        self.assertEqual(copy["multiplier"], 2)
        lsfg.rename(data, "Copy", "Renamed")
        self.assertEqual(lsfg.names(data), ["default", "3x", "New", "Renamed"])
        lsfg.delete(data, "Renamed")
        self.assertNotIn("Renamed", lsfg.names(data))
        for bad in ("", "  ", "3x", "x" * 65):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                lsfg.create(data, bad, {})
        with self.assertRaises(KeyError):
            lsfg.rename(data, "ghost", "z")

    def test_values_and_global(self):
        data = config()
        lsfg.set_value(lsfg.find(data, "3x"), "flow_scale", 0.5)
        self.assertEqual(lsfg.find(data, "3x")["flow_scale"], 0.5)
        for key in ("name", "active_in"):
            with self.assertRaises(ValueError):
                lsfg.set_value(lsfg.find(data, "3x"), key, "x")
        lsfg.set_global(data, "dll", "/x/Lossless.dll")
        self.assertEqual(data["global"]["dll"], "/x/Lossless.dll")
        lsfg.set_global(data, "dll", "")
        self.assertNotIn("dll", data["global"])


class PerGame(unittest.TestCase):
    def test_entries_are_numeric_ids_for_steam_and_shortcuts(self):
        self.assertEqual(lsfg.game_entry("1245620"), "1245620")
        self.assertEqual(lsfg.game_entry(3000000001, True), "3000000001")
        for bad in ("../1", "", "Game.exe", "12a"):
            with self.subTest(appid=bad), self.assertRaises(ValueError):
                lsfg.game_entry(bad)

    def test_one_profile_per_game_and_other_entries_untouched(self):
        data = config()
        self.assertEqual(lsfg.game_profile(data, "1000"), "default")
        lsfg.assign(data, "1000", "3x")
        self.assertEqual(lsfg.game_profile(data, "1000"), "3x")
        self.assertEqual(lsfg.find(data, "default")["active_in"], ["Other.exe"])  # not ours: kept
        lsfg.assign(data, "1000", "3x")  # twice: still once
        self.assertEqual(lsfg.find(data, "3x")["active_in"], ["1000"])
        lsfg.assign(data, "1000", None)
        self.assertIsNone(lsfg.game_profile(data, "1000"))
        self.assertNotIn("active_in", lsfg.find(data, "3x"))  # empty list removed
        with self.assertRaises(KeyError):
            lsfg.assign(data, "1000", "ghost")



class Custom(unittest.TestCase):
    def test_own_profile_copies_the_current_one(self):
        data = config()  # "1000" uses "default" (shared with Other.exe)
        name = lsfg.make_custom(data, "1000", "Test Game", {"multiplier": 9})
        self.assertEqual(name, "Test Game")
        own = lsfg.find(data, "Test Game")
        self.assertEqual((own["multiplier"], own["active_in"]), (2, ["1000"]))  # copied from "default"
        self.assertEqual(lsfg.find(data, "default")["active_in"], ["Other.exe"])
        self.assertTrue(lsfg.is_custom(data, "Test Game", "1000"))

    def test_already_own_is_kept_and_defaults_when_no_profile(self):
        data = config()
        first = lsfg.make_custom(data, "1000", "Test Game", {})
        self.assertEqual(lsfg.make_custom(data, "1000", "Renamed In Steam", {}), first)  # no duplicate
        fresh = lsfg.make_custom(data, "2000", "Other Game", {"multiplier": 4, "flow_scale": 1.0})
        self.assertEqual(lsfg.find(data, fresh)["multiplier"], 4)

    def test_names_are_unique_and_bounded(self):
        data = config()
        self.assertEqual(lsfg.unique_name(data, "3x"), "3x (2)")
        lsfg.create(data, "3x (2)", {})
        self.assertEqual(lsfg.unique_name(data, "3x"), "3x (3)")
        self.assertLessEqual(len(lsfg.unique_name(data, "x" * 200)), lsfg.MAX_NAME)
        self.assertEqual(lsfg.unique_name(data, "  "), "Game")
        self.assertFalse(lsfg.is_custom(data, "default", "1000"))  # shared with Other.exe



class StringActiveIn(unittest.TestCase):
    """lsfg-vk-ui writes a single game as a plain string: active_in = "878670"."""

    def data(self):
        return {"version": 2, "profile": [{"active_in": "878670", "name": "default"}, {"name": "2x"}]}

    def test_whole_entries_only(self):
        data = self.data()
        self.assertEqual(lsfg.game_profile(data, "878670"), "default")
        self.assertIsNone(lsfg.game_profile(data, "87"))  # not a substring match
        self.assertTrue(lsfg.is_custom(data, "default", "878670"))

    def test_assign_and_remove(self):
        data = self.data()
        lsfg.assign(data, "1000", "default")
        self.assertEqual(lsfg.find(data, "default")["active_in"], ["878670", "1000"])
        data = self.data()
        lsfg.assign(data, "878670", "2x")
        self.assertNotIn("active_in", lsfg.find(data, "default"))
        self.assertEqual(lsfg.find(data, "2x")["active_in"], ["878670"])
        self.assertEqual(lsfg.games({"active_in": 5}), [])



class OffAndOn(unittest.TestCase):
    def test_turning_back_on_reuses_the_games_profile(self):
        data = config()
        name = lsfg.make_custom(data, "2000", "Some Game", {"multiplier": 2})
        lsfg.set_value(lsfg.find(data, name), "multiplier", 5)
        lsfg.assign(data, "2000", None)  # off: the profile stays, unused
        self.assertEqual(lsfg.make_custom(data, "2000", "Some Game", {"multiplier": 2}), "Some Game")
        self.assertEqual(lsfg.find(data, "Some Game")["multiplier"], 5)
        self.assertEqual(lsfg.names(data).count("Some Game"), 1)


if __name__ == "__main__":
    unittest.main()
