#!/usr/bin/env python3
"""Offline tests for logi_mx.py: no Logitech software or Herdr needed."""
import base64
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import logi_mx as L  # noqa: E402

MAP = L.load_mapping()
DEV = "mx-master-4-2b042"


def settings():
    """Shape of a real Options+ settings blob (trimmed)."""
    gesture = {"card": {"id": "card_global_presets_one_of_gesture_button", "selectedNestedCard": "virtual_desktops",
                        "nestedCards": {"custom_gesture": {"nestedCards": {}}, "virtual_desktops": {}}},
               "cardId": "card_global_presets_one_of_gesture_button", "slotId": f"{DEV}_c195", "tags": ["UI_PAGE_BUTTONS"]}
    simple = lambda cid, card: {"card": {"id": card, "attribute": "MACRO_PLAYBACK"}, "cardId": card,
                                "slotId": f"{DEV}_c{cid}", "tags": ["UI_PAGE_BUTTONS"]}
    default = {"activeForApplication": True, "applicationId": "desk", "id": "desk", "name": "PROFILE_NAME_DEFAULT",
               "assignments": [gesture, simple(83, "card_global_presets_win_back"),
                               simple(86, "card_global_presets_win_forward"),
                               simple(416, "card_global_presets_show_radial_menu"),
                               {"card": {}, "cardId": "x", "slotId": f"{DEV}_mouse_settings", "tags": ["UI_PAGE_POINTER"]},
                               {"card": {}, "cardId": "w", "slotId": f"{DEV}_thumb_wheel_adapter", "tags": ["UI_PAGE_BUTTONS"]},
                               {"card": {}, "cardId": "y", "slotId": "radial-menu-virtual-device-10000000_c408",
                                "tags": ["UI_PAGE_BUTTONS"]}]}
    return {"applications": {"applications": [
                {"applicationId": "app1", "name": "herdr-app",
                 "applicationPath": r"c:\users\x\appdata\local\programs\herdr-app\herdr-app.exe"}]},
            "profile_keys": ["profile-desk"], "profile-desk": default}


class Combos(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(L.norm_combo("shift-ctrl-F1"), "ctrl-shift-f1")
        self.assertEqual(L.hid_code("f1"), 58)
        self.assertEqual(L.hid_code("f12"), 69)
        self.assertEqual(L.hid_code("f13"), 104)
        self.assertEqual(L.hid_code("c"), 6)
        self.assertEqual(L.win_vk("f9"), 120)
        self.assertRaises(ValueError, L.norm_combo, "hyper-f1")

    def test_ring_combo_real_strings(self):
        sep = "#\u00a4%&+?"
        cases = {
            f"ControlOrCommand+AltOrOption+Shift+F1___68158480___Ctrl+Alt+Shift+F1___win-112{sep}134{sep}68158480{sep}0": "ctrl-alt-shift-f1",
            f"ControlOrCommand+Shift+F9___68158480___Ctrl+Shift+F9___win-120{sep}132{sep}68158480{sep}0": "ctrl-shift-f9",
            f"ControlOrCommand+Shift+KeyC___68158480___Ctrl+Shift+C___win-67{sep}132{sep}68158480{sep}0": "ctrl-shift-c",
        }
        for raw, want in cases.items():
            self.assertEqual(L.ring_combo(raw), want)


class Keys(unittest.TestCase):
    def test_adds_then_idempotent(self):
        keys = {"global": {"ctrl-k": "mod.open:picker"}, "terminal": {"ctrl-shift-c": "terminal.copy"}}
        changes, clashes = L.plan_keys(keys, MAP)
        self.assertFalse(clashes)
        self.assertEqual(keys["global"]["ctrl-shift-f1"], "space.prev")
        self.assertEqual(keys["global"]["ctrl-alt-shift-f1"]["args"]["argv"], ["claude"])
        self.assertNotIn("ctrl-shift-c", [c.split(": ")[1].split(" ")[0] for c in changes])
        self.assertEqual(L.plan_keys(keys, MAP), ([], []))

    def test_omarchy_variant(self):
        keys = {"global": {}, "layout:omarchy": {"ctrl-k": "mod.open:omarchy-picker"}}
        L.plan_keys(keys, MAP)
        self.assertEqual(keys["global"]["ctrl-shift-f8"], "mod.open:omarchy-palette")

    def test_clash_is_reported_not_overwritten(self):
        keys = {"global": {"ctrl-shift-f1": "app.help"}}
        _, clashes = L.plan_keys(keys, MAP)
        self.assertEqual(keys["global"]["ctrl-shift-f1"], "app.help")
        self.assertTrue(any("ctrl-shift-f1" in c for c in clashes))

    def test_more_specific_section_warns(self):
        keys = {"global": {}, "terminal": {"ctrl-shift-f2": "send:x"}}
        _, clashes = L.plan_keys(keys, MAP)
        self.assertTrue(any(c.startswith("terminal: ctrl-shift-f2") for c in clashes))


class Buttons(unittest.TestCase):
    def test_creates_profile_like_the_ui(self):
        d = settings()
        changes = L.plan_buttons(d, MAP)
        self.assertTrue(changes)
        p = d["profile-app1"]
        self.assertEqual((p["id"], p["applicationId"], p["baseProfileId"]), ("app1", "app1", "desk"))
        self.assertEqual(d["profile_keys"][0], "profile-app1")
        slots = [a["slotId"] for a in p["assignments"]]
        self.assertNotIn(f"{DEV}_mouse_settings", slots)  # pointer page is not copied
        self.assertIn(f"{DEV}_thumb_wheel_adapter", slots)
        self.assertNotIn("radial-menu-virtual-device-10000000_c408", slots)
        g = next(a for a in p["assignments"] if a["slotId"].endswith("_c195"))["card"]
        self.assertEqual(g["selectedNestedCard"], "custom_gesture")
        up = g["nestedCards"]["custom_gesture"]["nestedCards"]["up"]["macro"]["keystroke"]
        self.assertEqual((up["code"], up["modifiers"]), (58, [224, 225]))
        back = next(a for a in p["assignments"] if a["slotId"].endswith("_c83"))["card"]["macro"]["keystroke"]
        self.assertEqual(back["code"], 62)
        self.assertEqual(L.plan_buttons(d, MAP), [])  # second run: nothing to do

    def test_restores_ring_button_taken_by_smart_action(self):
        d = settings()
        L.plan_buttons(d, MAP)
        p = d["profile-app1"]
        for a in p["assignments"]:
            if a["slotId"].endswith("_c416"):
                a["card"] = {"attribute": "MACRO_REF", "id": "abc"}
        changes = L.plan_buttons(d, MAP)
        self.assertTrue(any("Show Actions Ring" in c for c in changes))

    def test_missing_app(self):
        d = settings()
        d["applications"]["applications"] = []
        self.assertRaises(SystemExit, L.plan_buttons, d, MAP)


class Ring(unittest.TestCase):
    def test_icons(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = os.path.join(tmp, "Loupedeck72", "herdr-app", "Profiles", "P1")
            os.makedirs(os.path.join(pdir, "ActionIcons"))
            json.dump({"processOrBundleName": "herdr-app", "name": "herdr-app"},
                      open(os.path.join(tmp, "Loupedeck72", "herdr-app", "ApplicationInfo.json"), "w"))
            kk = lambda s: {"keyboardKey": s + "___68158480___x___win-1"}
            prof = {"profileActions": [
                {"name": "A1", "displayName": "Palette", "templateActionName": "$@Generic___@KeyboardKey",
                 "actionParameters": {"parameters": kk("ControlOrCommand+Shift+F8")}},
                {"name": "A2", "displayName": "Claude", "templateActionName": "$@Generic___@KeyboardKey",
                 "actionParameters": {"parameters": kk("ControlOrCommand+AltOrOption+Shift+F1")}},
                {"name": "F", "displayName": "Agents", "templateActionName": "$@Generic___@OpenFolder",
                 "actionParameters": {"parameters": {"folderName": "F"}}}]}
            json.dump(prof, open(os.path.join(pdir, "ProfileInfo.json"), "w"))
            json.dump(L.ict("Palette", "<svg/>", False), open(os.path.join(pdir, "ActionIcons", "A1.ict"), "w"))
            self.assertEqual(list(L.ring_profiles(tmp, "herdr")), [pdir])
            writes, missing = L.plan_ring_icons(pdir, MAP)
            self.assertEqual(sorted(w[2] for w in writes), ["Agents", "Claude", "Palette"])
            claude = next(w[1] for w in writes if w[2] == "Claude")
            self.assertEqual(claude["backgroundColor"], L.WHITE)
            svg = base64.b64decode(claude["items"][0]["image"]).decode()
            self.assertIn("#D97757", svg)
            self.assertIn("ctrl-shift-f9", [a["combo"] for a in missing])  # lazygit not in this ring
            for path, new, _ in writes:
                with open(path, "w") as f:
                    json.dump(new, f)
            self.assertEqual(L.plan_ring_icons(pdir, MAP)[0], [])


class Logiops(unittest.TestCase):
    def test_snippet(self):
        s = L.logiops_snippet(MAP)
        self.assertIn('direction: "Up"', s)
        self.assertIn('"KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_F1"', s)
        self.assertIn("cid: 0x53", s)


if __name__ == "__main__":
    unittest.main()
