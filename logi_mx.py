#!/usr/bin/env python3
"""logi-mx: drive the Herdr app from a Logitech MX mouse.

Three layers, one mapping (mapping.json):
  keys     Herdr keys.json: a hard-to-type combo for every action
  buttons  Logi Options+ settings.db: gesture button directions, back, forward
  ring     Logi Plugin Service: Actions Ring icons (actions are added in the UI)
  logiops  Linux has no Options+: prints a logid.cfg snippet instead

Every write makes a backup first. Use --dry-run to see the changes.
Python 3 standard library only.
"""
import argparse
import base64
import copy
import glob
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

def read_json(path, encoding="utf-8-sig"):
    with open(path, encoding=encoding) as f:
        return json.load(f)


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


HERE = os.path.dirname(os.path.abspath(__file__))
STAMP = time.strftime("%Y%m%d-%H%M%S")

# ---------------------------------------------------------------- combos

MOD_ORDER = ["ctrl", "alt", "shift", "super"]
MOD_ALIASES = {"control": "ctrl", "option": "alt", "cmd": "super", "win": "super", "meta": "super"}
MOD_HID = {"ctrl": 224, "shift": 225, "alt": 226, "super": 227}
NAMED_HID = {"enter": 40, "escape": 41, "backspace": 42, "tab": 43, "space": 44,
             "right": 79, "left": 80, "down": 81, "up": 82,
             "pageup": 75, "pagedown": 78, "home": 74, "end": 77, "insert": 73, "delete": 76}
NAMED_VK = {"enter": 13, "escape": 27, "backspace": 8, "tab": 9, "space": 32,
            "left": 37, "up": 38, "right": 39, "down": 40,
            "pageup": 33, "pagedown": 34, "home": 36, "end": 35, "insert": 45, "delete": 46}


def parse_combo(combo):
    """'ctrl-shift-f1' -> (['ctrl', 'shift'], 'f1'), modifiers in canonical order."""
    parts = combo.lower().split("-")
    key, mods = parts[-1], [MOD_ALIASES.get(m, m) for m in parts[:-1]]
    bad = [m for m in mods if m not in MOD_HID]
    if bad or not key:
        raise ValueError(f"bad combo {combo!r}")
    hid_code(key)
    return [m for m in MOD_ORDER if m in mods], key


def norm_combo(combo):
    mods, key = parse_combo(combo)
    return "-".join(mods + [key])


def hid_code(key):
    if len(key) == 1 and "a" <= key <= "z":
        return 4 + ord(key) - ord("a")
    if len(key) == 1 and key.isdigit():
        return 39 if key == "0" else 29 + int(key)
    m = re.fullmatch(r"f(\d+)", key)
    if m and 1 <= int(m.group(1)) <= 24:
        n = int(m.group(1))
        return 57 + n if n <= 12 else 91 + n
    if key in NAMED_HID:
        return NAMED_HID[key]
    raise ValueError(f"unsupported key {key!r}")


def win_vk(key):
    if len(key) == 1 and key.isalnum():
        return ord(key.upper())
    m = re.fullmatch(r"f(\d+)", key)
    if m:
        return 111 + int(m.group(1))
    return NAMED_VK[key]


def display(combo):
    mods, key = parse_combo(combo)
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "super": "Win"}
    return " + ".join([names[m] for m in mods] + [key.upper()])


def ring_combo(keyboard_key):
    """Options+ ring 'keyboardKey' ('ControlOrCommand+Shift+KeyC___…') -> 'ctrl-shift-c'."""
    head = keyboard_key.split("___", 1)[0]
    mods, key = [], None
    for tok in head.split("+"):
        low = tok.lower()
        if low in ("controlorcommand", "control", "ctrl"):
            mods.append("ctrl")
        elif low in ("altoroption", "alt", "option"):
            mods.append("alt")
        elif low == "shift":
            mods.append("shift")
        elif low in ("windows", "command", "meta", "win", "super"):
            mods.append("super")
        elif low.startswith("key") and len(low) == 4:
            key = low[3]
        elif low.startswith("digit") and len(low) == 6:
            key = low[5]
        else:
            key = low
    return "-".join([m for m in MOD_ORDER if m in mods] + [key or ""])


# ---------------------------------------------------------------- platform

def detect_os():
    system = platform.system()
    if system == "Linux":
        try:
            if "microsoft" in read_text("/proc/version").lower():
                return "wsl"
        except OSError:
            pass
        return "linux"
    return {"Windows": "windows", "Darwin": "macos"}.get(system, "linux")


class Host:
    """Paths and process control for the machine that runs Logi Options+."""

    def __init__(self, os_name=None):
        self.os = os_name or detect_os()

    # Windows (native or from WSL) goes through PowerShell.
    def ps(self, command):
        exe = "powershell.exe" if self.os == "wsl" else "powershell"
        cwd = "/mnt/c" if self.os == "wsl" and os.path.isdir("/mnt/c") else None
        out = subprocess.run([exe, "-NoProfile", "-Command", command], capture_output=True, text=True, cwd=cwd)
        return out.stdout.replace("\r", "").strip()

    def win_path(self, path):
        if self.os != "wsl" or not path:
            return path
        return subprocess.run(["wslpath", "-u", path], capture_output=True, text=True).stdout.strip()

    def win_env(self, name):
        if self.os == "windows":
            return os.environ.get(name, "")
        return self.win_path(self.ps(f"$env:{name}"))

    @property
    def is_windows(self):
        return self.os in ("windows", "wsl")

    def options_dir(self):
        if self.is_windows:
            return os.path.join(self.win_env("LOCALAPPDATA"), "LogiOptionsPlus")
        if self.os == "macos":  # unverified
            return os.path.expanduser("~/Library/Application Support/LogiOptionsPlus")
        return None

    def ring_dir(self):
        if self.is_windows:
            return os.path.join(self.win_env("LOCALAPPDATA"), "Logi", "LogiPluginService", "Applications")
        if self.os == "macos":  # unverified
            return os.path.expanduser("~/Library/Application Support/Logi/LogiPluginService/Applications")
        return None

    def presets_globs(self):
        if self.is_windows:
            pd = self.win_env("ProgramData")
            return [os.path.join(pd, "LogiOptionsPlus", "depots", "*", "logioptionsplus", "data", "card_presets", "card_presets_win.json")]
        if self.os == "macos":  # unverified
            return ["/Library/Application Support/Logitech.localized/LogiOptionsPlus/**/card_presets_osx.json"]
        return []

    def herdr_homes(self):
        homes = []
        if os.environ.get("HERDR_APP_HOME"):
            homes.append(os.environ["HERDR_APP_HOME"])
        homes.append(os.path.expanduser("~/.config/herdr-app"))
        if self.os == "wsl":
            homes.append(os.path.join(self.win_env("USERPROFILE"), ".config", "herdr-app"))
        seen, out = set(), []
        for h in homes:
            if h and os.path.isdir(h) and os.path.realpath(h) not in seen:
                seen.add(os.path.realpath(h))
                out.append(h)
        return out

    # processes
    DEFAULT_EXE = {
        "logioptionsplus_agent": r"C:\Program Files\LogiOptionsPlus\logioptionsplus_agent.exe",
        "LogiPluginService": r"C:\Program Files\Logi\LogiPluginService\LogiPluginService.exe",
    }

    def exe_path(self, name):
        if self.is_windows:
            path = self.ps(f"(Get-Process {name} -EA SilentlyContinue | Select -First 1).Path")
            return path or self.DEFAULT_EXE.get(name)
        return name

    def running(self, name):
        if self.is_windows:
            return bool(self.ps(f"Get-Process {name} -EA SilentlyContinue | Select -First 1 -Expand Id"))
        return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0

    def stop(self, *names):
        if self.is_windows:
            self.ps("Stop-Process -Name " + ",".join(names) + " -Force -EA SilentlyContinue")
        else:
            for n in names:
                subprocess.run(["pkill", "-x", n], capture_output=True)
        time.sleep(1)

    def start(self, name, exe):
        if self.running(name):
            return True
        if self.is_windows:
            self.ps(f"Start-Process '{exe}'")
        else:  # macOS, unverified: launchd usually restarts the agent by itself
            subprocess.run(["open", "-g", "-a", exe], capture_output=True)
        for _ in range(10):
            time.sleep(1)
            if self.running(name):
                return True
        return False


# ---------------------------------------------------------------- mapping

def load_mapping(path=None):
    m = read_json(path or os.path.join(HERE, "mapping.json"))
    for a in m["actions"]:
        a["combo"] = norm_combo(a["combo"])
    return m


def icon_svg(name):
    return read_text(os.path.join(HERE, "icons", name + ".svg"))


# ---------------------------------------------------------------- keys (Herdr)

def plan_keys(keys, mapping):
    """Mutates a keys.json dict. Returns (changes, clashes)."""
    changes, clashes = [], []
    omarchy = "layout:omarchy" in keys
    for a in mapping["actions"]:
        combo, section = a["combo"], a.get("section", "global")
        wanted = a["herdr"]
        accepted = [wanted] + ([a["herdr_omarchy"]] if a.get("herdr_omarchy") else [])
        sec = keys.setdefault(section, {})
        have = sec.get(combo)
        if have is None:
            sec[combo] = a["herdr_omarchy"] if (omarchy and a.get("herdr_omarchy") and section == "global") else wanted
            changes.append(f"{section}: {combo} -> {json.dumps(sec[combo])}")
        elif have not in accepted:
            clashes.append(f"{section}: {combo} is already {json.dumps(have)}, wanted {json.dumps(wanted)}")
            continue
        # a more specific section with the same key would win over ours
        for other, binds in keys.items():
            if other != section and other != "global" and combo in binds and binds[combo] not in accepted:
                clashes.append(f"{other}: {combo} is {json.dumps(binds[combo])} and wins over {section}")
    return changes, clashes


def cmd_keys(args, host, mapping):
    homes = args.home or host.herdr_homes()
    if not homes:
        sys.exit("no Herdr home found (~/.config/herdr-app); pass --home")
    for home in homes:
        path = os.path.join(home, "keys.json")
        keys = read_json(path) if os.path.exists(path) else {}
        changes, clashes = plan_keys(keys, mapping)
        print(f"== {path}")
        for c in changes:
            print("  +", c)
        for c in clashes:
            print("  ! clash:", c)
        if not changes:
            print("  nothing to add")
            continue
        if args.dry_run:
            continue
        if os.path.exists(path):
            shutil.copy2(path, f"{path}.bak-logi-mx-{STAMP}")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(keys, indent=2, ensure_ascii=True))
        print("  written (Herdr reloads keys.json on save; check `herdr-app status` errors)")


# ---------------------------------------------------------------- buttons (Options+ settings.db)

CID = {"gesture": 195, "back": 83, "forward": 86, "ring": 416}
DIRECTIONS = {"gesture-up": "up", "gesture-down": "down", "gesture-left": "left",
              "gesture-right": "right", "gesture-click": "click"}


def keystroke_macro(combo):
    mods, key = parse_combo(combo)
    return {"actionName": display(combo),
            "keystroke": {"code": hid_code(key), "modifiers": [MOD_HID[m] for m in mods],
                          "displayCharacter": key.upper(), "virtualKeyId": "VK_" + key.upper()},
            "type": "KEYSTROKE"}


def keystroke_card(combo):
    return {"attribute": "MACRO_PLAYBACK", "continuous": False,
            "icons": {"icons": ["Shortcut.png", "Shortcut.svg"], "uri": "pipeline://system_actions/"},
            "id": "card_global_presets_keyboard_shortcut", "macro": keystroke_macro(combo),
            "name": "ASSIGNMENT_NAME_KEYBOARD_SHORTCUT", "nestedCards": {}, "readOnly": False,
            "tags": ["PRESET_TAG_KEY_OR_BUTTON", "PRESET_TAG_MACROS_UNSUPPORTED",
                     "PRESET_TAG_PRESENTER_BUTTON", "PRESET_KEYBOARD_FUNCTIONS"], "taskId": 65536}


def inner_keystroke_card(combo):
    return {"attribute": "MACRO_PLAYBACK", "continuous": False, "id": "inner_card_keyboard_shortcut",
            "macro": keystroke_macro(combo), "name": "ASSIGNMENT_NAME_KEYBOARD_SHORTCUT",
            "nestedCards": {}, "readOnly": False, "tags": ["PRESET_TAG_KEY_OR_BUTTON"], "taskId": 65536}


def default_profile(d):
    profiles = [d[k] for k in d.get("profile_keys", []) if k in d]
    for p in profiles:
        if p.get("name") == "PROFILE_NAME_DEFAULT":
            return p
    for p in profiles:
        if not p.get("baseProfileId"):
            return p
    raise SystemExit("no default profile in settings.db")


def find_app(d, match):
    for a in d.get("applications", {}).get("applications", []):
        hay = " ".join([a.get("applicationPath") or "", a.get("name") or ""]).lower()
        if match.lower() in hay:
            return a
    return None


def slot_cid(slot):
    m = re.fullmatch(r"(.+)_c(\d+)", slot)
    if not m or m.group(1).startswith(("radial-menu", "software-events")):
        return None, None
    return m.group(1), int(m.group(2))


def is_button_slot(a):
    """What the UI copies into a new app profile: the mouse's Buttons page."""
    virtual = a["slotId"].startswith(("radial-menu", "software-events"))
    return not virtual and "UI_PAGE_BUTTONS" in (a.get("tags") or [])


def gesture_template(d, presets):
    for a in default_profile(d)["assignments"]:
        if slot_cid(a["slotId"])[1] == CID["gesture"] and "custom_gesture" in a["card"].get("nestedCards", {}):
            return copy.deepcopy(a)
    for card in presets:
        if card.get("id") == "card_global_presets_one_of_gesture_button":
            return {"card": copy.deepcopy(card), "cardId": card["id"], "slotId": None, "tags": ["UI_PAGE_BUTTONS"]}
    raise SystemExit("can't find the gesture button card (custom_gesture) to start from")


def plan_buttons(d, mapping, presets=()):
    """Mutates an Options+ settings dict. Returns the list of changes."""
    app = find_app(d, mapping["app"])
    if not app:
        raise SystemExit(f"Options+ has no application matching {mapping['app']!r}: "
                         "add it in Logi Options+ (Add application) first")
    app_id, base = app["applicationId"], default_profile(d)
    key, changes = "profile-" + app_id, []
    if key not in d:
        d[key] = {"applicationId": app_id, "baseProfileId": base["id"], "id": app_id,
                  "assignments": [copy.deepcopy(a) for a in base["assignments"] if is_button_slot(a)]}
        d["profile_keys"].insert(0, key)
        changes.append(f"new Options+ profile for {app.get('name')}")
    prof = d[key]
    by_slot = {a["slotId"]: a for a in prof["assignments"]}
    devices = sorted({slot_cid(s)[0] for s in by_slot if slot_cid(s)[0]})
    mouse = {a["mouse"]: a["combo"] for a in mapping["actions"] if a.get("mouse")}
    for dev in devices:
        # gesture button: one keystroke per direction
        gslot = f"{dev}_c{CID['gesture']}"
        if gslot in by_slot and any(m in DIRECTIONS for m in mouse):
            cur = by_slot[gslot]
            new = copy.deepcopy(cur) if "custom_gesture" in cur["card"].get("nestedCards", {}) else gesture_template(d, presets)
            new["slotId"], new["cardId"] = gslot, new["card"]["id"]
            nested = new["card"]["nestedCards"]["custom_gesture"]["nestedCards"]
            for m, direction in DIRECTIONS.items():
                if m in mouse:
                    nested[direction] = inner_keystroke_card(mouse[m])
            new["card"]["selectedNestedCard"] = "custom_gesture"
            if new != cur:
                by_slot[gslot] = new
                changes.append(f"{gslot}: gesture directions -> " +
                               ", ".join(f"{DIRECTIONS[m]} {mouse[m]}" for m in DIRECTIONS if m in mouse))
        for m in ("back", "forward"):
            slot = f"{dev}_c{CID[m]}"
            if m in mouse and slot in by_slot:
                new = {"card": keystroke_card(mouse[m]), "cardId": "card_global_presets_keyboard_shortcut",
                       "slotId": slot, "tags": ["UI_PAGE_BUTTONS"]}
                if new != by_slot[slot]:
                    by_slot[slot] = new
                    changes.append(f"{slot}: {m} -> {mouse[m]}")
        # the ring button must still open the ring (a Smart Action test can steal it)
        rslot = f"{dev}_c{CID['ring']}"
        if rslot in by_slot and by_slot[rslot]["card"].get("attribute") == "MACRO_REF":
            base_ring = next((a for a in base["assignments"] if a["slotId"] == rslot), None)
            if base_ring:
                by_slot[rslot] = copy.deepcopy(base_ring)
                changes.append(f"{rslot}: back to Show Actions Ring (was a Smart Action)")
    prof["assignments"] = [by_slot[a["slotId"]] for a in prof["assignments"]]
    return changes


def load_presets(host):
    cards = []
    for pattern in host.presets_globs():
        for f in sorted(glob.glob(pattern, recursive=True))[-1:]:
            try:
                cards = read_json(f).get("cards", [])
            except (OSError, ValueError):
                pass
    return cards


def _read_db(db_dir, tmp):
    for f in glob.glob(os.path.join(db_dir, "settings.db*")):
        shutil.copy2(f, tmp)
    c = sqlite3.connect(os.path.join(tmp, "settings.db"))
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    rid, blob = c.execute("select _id, file from data").fetchone()
    return c, rid, json.loads(blob)


def read_settings(db_dir):
    with tempfile.TemporaryDirectory() as tmp:
        c, _, d = _read_db(db_dir, tmp)
        c.close()
        return d


def edit_settings(host, db_dir, plan, dry_run):
    """Options+ keeps settings in memory and writes them back, so the agent is
    stopped while the file is replaced, then started again."""
    changes = plan(read_settings(db_dir))
    for c in changes:
        print("  +", c)
    if not changes:
        print("  nothing to change")
        return
    if dry_run:
        return
    exe = host.exe_path("logioptionsplus_agent")
    host.stop("logioptionsplus", "logioptionsplus_agent")
    backup = os.path.join(db_dir, f"backup-logi-mx-{STAMP}")
    os.makedirs(backup, exist_ok=True)
    for f in glob.glob(os.path.join(db_dir, "settings.db*")):
        shutil.copy2(f, backup)
    with tempfile.TemporaryDirectory() as tmp:
        c, rid, d = _read_db(db_dir, tmp)  # re-read: the agent may have saved on the way out
        plan(d)
        c.execute("update data set file=? where _id=?", (json.dumps(d).encode(), rid))
        c.commit()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
        host.stop("logioptionsplus_agent")  # in case something restarted it meanwhile
        for ext in ("-wal", "-shm"):
            p = os.path.join(db_dir, "settings.db" + ext)
            if os.path.exists(p):
                os.remove(p)
        shutil.copy2(os.path.join(tmp, "settings.db"), os.path.join(db_dir, "settings.db"))
    print("  backup:", backup)
    print("  agent restarted" if host.start("logioptionsplus_agent", exe) else "  ! start Logi Options+ by hand")
    time.sleep(2)
    left = plan(read_settings(db_dir))
    print("  verified" if not left else f"  ! Options+ did not keep: {left}")


def cmd_buttons(args, host, mapping):
    db_dir = args.options_dir or host.options_dir()
    if not db_dir or not os.path.exists(os.path.join(db_dir, "settings.db")):
        sys.exit("Logi Options+ settings.db not found (Linux: use `logiops`)")
    print(f"== {db_dir}")
    presets = load_presets(host)
    edit_settings(host, db_dir, lambda d: plan_buttons(d, mapping, presets), args.dry_run)


# ---------------------------------------------------------------- ring (Logi Plugin Service)

WHITE, BLACK = 4294967295, 4278190080


def ring_profiles(ring_dir, match):
    for info in glob.glob(os.path.join(ring_dir, "*", "*", "ApplicationInfo.json")):
        j = read_json(info)
        hay = " ".join([j.get("processOrBundleName") or "", j.get("name") or ""]).lower()
        if match.lower() in hay:
            for prof in glob.glob(os.path.join(os.path.dirname(info), "Profiles", "*", "ProfileInfo.json")):
                yield os.path.dirname(prof)


def ring_actions(profile_dir):
    prof = read_json(os.path.join(profile_dir, "ProfileInfo.json"))
    out = []
    for a in prof.get("profileActions", []):
        params = (a.get("actionParameters") or {}).get("parameters") or {}
        kind = a.get("templateActionName", "")
        combo = ring_combo(params["keyboardKey"]) if "keyboardKey" in params else None
        out.append({"name": a["name"], "label": a.get("displayName"), "combo": combo,
                    "folder": kind.endswith("@OpenFolder")})
    return out


def ict(label, svg, colored):
    return {"backgroundColor": WHITE if colored else BLACK, "items": [
        {"image": base64.b64encode(svg.encode()).decode(), "imageFileName": None, "imageColor": WHITE,
         "imageRotation": "None", "isVisible": True, "itemType": "Image",
         "area": {"x": 17, "y": 0, "width": 65, "height": 65}},
        {"text": label, "originalText": None, "textColor": WHITE, "fontSize": 5,
         "fontName": "Brown Logitech Pan Light", "isVisible": True, "itemType": "Text",
         "area": {"x": 0, "y": 65, "width": 100, "height": 30}}]}


def plan_ring_icons(profile_dir, mapping):
    """Returns ([(path, new_ict_dict)], missing_actions)."""
    by_combo = {a["combo"]: a for a in mapping["actions"] if a.get("ring")}
    writes, found = [], set()
    for ra in ring_actions(profile_dir):
        if ra["folder"]:
            want = mapping.get("ring_folder")
            icon, colored = (want["icon"], want.get("colored", False)) if want else (None, False)
        elif ra["combo"] in by_combo:
            found.add(ra["combo"])
            icon, colored = by_combo[ra["combo"]]["icon"], by_combo[ra["combo"]].get("colored", False)
        else:
            continue
        if not icon:
            continue
        path = os.path.join(profile_dir, "ActionIcons", ra["name"] + ".ict")
        cur = read_json(path) if os.path.exists(path) else None
        new = copy.deepcopy(cur) if cur else ict(ra["label"] or "", "", colored)
        new["backgroundColor"] = WHITE if colored else BLACK
        for item in new["items"]:
            if item.get("itemType") == "Image":
                item["image"] = base64.b64encode(icon_svg(icon).encode()).decode()
        if new != cur:
            writes.append((path, new, ra["label"]))
    missing = [a for c, a in by_combo.items() if c not in found]
    return writes, missing


def cmd_ring(args, host, mapping):
    ring_dir = args.ring_dir or host.ring_dir()
    if args.create:
        if not host.is_windows and not args.ring_dir:
            raise SystemExit("--create is only known for Windows; on macOS add the actions in Options+")
        return create_ring(host, ring_dir, mapping, args.process, args.display_name, args.device_type, args.dry_run)
    profiles = list(ring_profiles(ring_dir, mapping["app"])) if ring_dir and os.path.isdir(ring_dir) else []
    if not profiles:
        print("No Actions Ring profile for this app yet. Either `ring --create` (Windows), or in Logi Options+:")
        print("Actions Ring -> add the app, add these 'Keyboard shortcut' actions, then run `ring` for the icons:")
        print_ring_table(mapping["actions"], mapping)
        return
    restart = False
    for pdir in profiles:
        print(f"== {pdir}")
        for ra in ring_actions(pdir):
            print(f"  {'folder' if ra['folder'] else ra['combo'] or '?':>18}  {ra['label']}")
        writes, missing = plan_ring_icons(pdir, mapping)
        if missing:
            print("  missing in the ring (add them in Options+):")
            print_ring_table(missing, mapping)
        for path, _, label in writes:
            print(f"  + icon: {label}")
        if not writes:
            print("  icons up to date")
        if writes and not args.dry_run:
            app_dir = os.path.dirname(os.path.dirname(pdir))
            backup = os.path.join(os.path.dirname(os.path.dirname(app_dir)) + ".Backups",
                                  f"{os.path.basename(app_dir)}-logi-mx-{STAMP}")
            shutil.copytree(app_dir, backup)
            print("  backup:", backup)
            exe = host.exe_path("LogiPluginService")
            host.stop("LogiPluginService")
            for path, new, _ in writes:
                with open(path, "w", encoding="utf-8", newline="\r\n") as f:
                    f.write(json.dumps(new, indent=4))
            restart = True
    if restart:
        print("  ring service restarted" if host.start("LogiPluginService", exe) else "  ! start Logi Plugin Service by hand")



# ring --create: a whole ring profile, as the Options+ UI writes it (Windows)

SEP = "#¤%&+?"
T7 = "Loupedeck.Service.Devices.Loupedeck7Devices.{}, LoupedeckService"
KEY_DESC = "Activate a keyboard shortcut with a single press or hold down for continuous use like a keyboard key"


def ring_keyboard_key(combo):
    """'ctrl-shift-f9' -> the Options+ ring keyboardKey string (Windows)."""
    mods, key = parse_combo(combo)
    if "super" in mods:
        raise ValueError("the Windows key is not supported in ring combos")
    tok = {"ctrl": "ControlOrCommand", "alt": "AltOrOption", "shift": "Shift"}
    shown = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift"}
    if len(key) == 1 and key.isalpha():
        ktok, kshow = "Key" + key.upper(), key.upper()
    elif len(key) == 1:
        ktok, kshow = "Digit" + key, key
    else:
        ktok = kshow = key.upper() if re.fullmatch(r"f\d+", key) else key.capitalize()
    bits = sum({"ctrl": 128, "alt": 2, "shift": 4}[m] for m in mods)
    left = "+".join([tok[m] for m in mods] + [ktok])
    right = "+".join([shown[m] for m in mods] + [kshow])
    return f"{left}___68158480___{right}___win-{win_vk(key)}{SEP}{bits}{SEP}68158480{SEP}0"


def _id():
    import uuid
    return uuid.uuid4().hex.upper()


def _page(name, display, controls, description=None, size=8):
    return {"$type": T7.format("ProfileLayoutPage7"), "name": name, "displayName": display,
            "description": description,
            "controls": [{"$type": T7.format("ProfileLayoutControl7"), "controlId": i,
                          "pressAction": controls.get(i), "rotateAction": None} for i in range(size)]}


def build_ring_profile(mapping, app_name, display_name, device="Loupedeck72"):
    """Returns (ApplicationInfo, ProfileInfo, {action name: ict})."""
    actions, icons, main, folder = [], {}, {}, {}
    def add(label, params, template, group, supergroup, desc, icon=None, colored=False):
        name = "$@Generic___@ProfileAction___" + _id()
        actions.append({"$type": "Loupedeck.Service.ApplicationProfileCommand, LoupedeckService", "isCommand": True,
                        "name": name, "templateActionName": template,
                        "actionParameters": {"$type": "Loupedeck.ActionEditorActionParameters, PluginApi",
                                             "parameters": dict({"$type": "Loupedeck.StringDictionaryNoCase, PluginApi"}, **params),
                                             "count": 1},
                        "displayName": label, "description": desc, "groupName": group, "superGroupName": supergroup,
                        "isProfileAction": True, "isMultiState": False, "isResetCommand": False,
                        "adjustmentName": None, "states": None})
        if icon:
            icons[name] = ict(label, icon_svg(icon), colored)
        return name
    ring = [a for a in mapping["actions"] if a.get("ring")]
    for a in sorted((a for a in ring if a["ring"] != "folder"), key=lambda a: a["ring"]):
        main[a["ring"] - 1] = add(a.get("label", a["id"]), {"keyboardKey": ring_keyboard_key(a["combo"])},
                                  "$@Generic___@KeyboardKey", "", "@macro", KEY_DESC, a.get("icon"), a.get("colored", False))
    in_folder = [a for a in ring if a["ring"] == "folder"]
    folder_pages = []
    if in_folder:
        f = mapping.get("ring_folder", {})
        fid = _id()
        for i, a in enumerate(in_folder[:8]):
            folder[i] = add(a.get("label", a["id"]), {"keyboardKey": ring_keyboard_key(a["combo"])},
                            "$@Generic___@KeyboardKey", "", "@macro", KEY_DESC, a.get("icon"), a.get("colored", False))
        place = f.get("place", 8) - 1
        if place in main:
            raise SystemExit(f"ring place {place + 1} is taken by an action and by the folder")
        fname = "$@Generic___@ProfileAction___" + fid
        actions.append({"$type": "Loupedeck.Service.ApplicationProfileCommand, LoupedeckService", "isCommand": True,
                        "name": fname, "templateActionName": "$@Generic___@OpenFolder",
                        "actionParameters": {"$type": "Loupedeck.ActionEditorActionParameters, PluginApi",
                                             "parameters": {"$type": "Loupedeck.StringDictionaryNoCase, PluginApi", "folderName": fid},
                                             "count": 1},
                        "displayName": f.get("label", "Folder"), "description": "", "groupName": "Folders",
                        "superGroupName": "@navigation", "isProfileAction": True, "isMultiState": False,
                        "isResetCommand": False, "adjustmentName": None, "states": None})
        if f.get("icon"):
            icons[fname] = ict(f.get("label", "Folder"), icon_svg(f["icon"]), f.get("colored", False))
        main[place] = fname
        folder_pages.append(_page(fid, "Folder", folder, "", size=len(folder)))  # the UI keeps only used places
    ws = _id()
    layout = {"$type": T7.format("ProfileLayout7"), "deviceType": device, "profileFlags": "None",
              "layoutModes": [{"$type": T7.format("ProfileLayoutMode7"), "deviceType": device, "modeName": "main",
                               "parentModeName": None, "actions": None, "dynamicButtonPages": None,
                               "dynamicEncoderPages": None,
                               "workspaces": [{"$type": T7.format("ProfileLayoutWorkspace7"), "name": ws,
                                               "displayName": "Workspace 1", "description": None,
                                               "pressPages": [_page(_id(), "Page (1)", main)],
                                               "rotatePages": [_page(_id(), "Dial Page", {})]}],
                               "homeWorkspaceName": ws}],
              "folderPages": folder_pages}
    pid = _id()
    profile = {"$type": "Loupedeck.Service.ApplicationProfile, LoupedeckService", "name": pid, "profileFlags": "None",
               "displayName": f"{display_name} Profile", "description": None, "deviceType": device,
               "applicationName": app_name, "nativePluginName": None, "hasNativePlugin": False,
               "additionalNativePluginNames": ["DefaultWin"],
               "lastModifiedTimeUtc": time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime()),
               "profileSettings": {"$type": "Loupedeck.DictionaryNoCase`1[[System.String, System.Private.CoreLib]], PluginApi"},
               "actionImages90": None, "actionImages60": None, "wheelImages": None, "actionColors": None,
               "layout": layout, "macroCommands": [], "macroAdjustments": [], "profileCommands": [],
               "profileAdjustments": [], "conversionHistory": None, "packageName": None, "packageVersion": None,
               "profileActions": actions}
    info = {"$type": "Loupedeck.Service.SupportedApplicationInfo, LoupedeckService", "name": app_name,
            "displayName": display_name, "description": None, "deviceType": device, "nativePluginName": None,
            "hasNativePlugin": False, "processOrBundleName": app_name,
            "modes": [{"$type": "Loupedeck.Service.ApplicationMode, LoupedeckService", "name": "main",
                       "parentModeName": None, "displayName": "Main"}],
            "defaultProfileName": None, "isEnabled": True}
    return info, profile, icons


def write_json(path, obj, newline="\r\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(json.dumps(obj, indent=4, ensure_ascii=False))


def create_ring(host, ring_dir, mapping, process, display_name, device, dry_run):
    app_dir = os.path.join(ring_dir, device, process)
    if os.path.exists(app_dir):
        raise SystemExit(f"{app_dir} exists: run `ring` to update icons, or move it away to recreate it")
    info, profile, icons = build_ring_profile(mapping, process, display_name, device)
    print(f"== new ring profile {app_dir}")
    for a in profile["profileActions"]:
        print(f"  + {a['displayName']}")
    if dry_run:
        return
    exe = host.exe_path("LogiPluginService")
    host.stop("LogiPluginService")
    pdir = os.path.join(app_dir, "Profiles", profile["name"])
    write_json(os.path.join(app_dir, "ApplicationInfo.json"), info)
    write_json(os.path.join(pdir, "ProfileInfo.json"), profile)
    for name, icon in icons.items():
        write_json(os.path.join(pdir, "ActionIcons", name + ".ict"), icon)
    print("  ring service restarted" if host.start("LogiPluginService", exe) else "  ! start Logi Plugin Service by hand")

def print_ring_table(actions, mapping):
    for a in actions:
        if not a.get("ring"):
            continue
        where = f"in folder '{mapping['ring_folder']['label']}'" if a["ring"] == "folder" else f"place {a['ring']}"
        print(f"    {display(a['combo']):<22} {a.get('label', a['id']):<14} {where}")


# ---------------------------------------------------------------- logiops (Linux)

def evdev(combo):
    mods, key = parse_combo(combo)
    names = {"ctrl": "KEY_LEFTCTRL", "alt": "KEY_LEFTALT", "shift": "KEY_LEFTSHIFT", "super": "KEY_LEFTMETA"}
    return [names[m] for m in mods] + ["KEY_" + key.upper()]


def logiops_snippet(mapping, device="MX Master 4"):
    mouse = {a["mouse"]: a["combo"] for a in mapping["actions"] if a.get("mouse")}

    def keypress(combo):
        return 'action = { type: "Keypress"; keys: [' + ", ".join(f'"{k}"' for k in evdev(combo)) + "]; };"
    gestures = []
    for m, d in (("gesture-up", "Up"), ("gesture-down", "Down"), ("gesture-left", "Left"),
                 ("gesture-right", "Right"), ("gesture-click", "None")):
        if m in mouse:
            gestures.append(f'        {{ direction: "{d}"; mode: "OnRelease"; {keypress(mouse[m])} }}')
    buttons = [f'    {{ cid: 0xc3; action = {{ type: "Gestures"; gestures: (\n' + ",\n".join(gestures) + "\n      ); };\n    }"]
    for m, cid in (("back", "0x53"), ("forward", "0x56")):
        if m in mouse:
            buttons.append(f"    {{ cid: {cid}; {keypress(mouse[m])} }}")
    return (f'devices: ({{\n  name: "{device}";\n  buttons: (\n' + ",\n".join(buttons) + "\n  );\n});\n")


def cmd_logiops(args, host, mapping):
    print("# logid.cfg (logiops). Unverified for MX Master 4; check the name with `logid -v`.")
    print("# The combos fire in every app: they are chosen to do nothing outside Herdr.")
    print(logiops_snippet(mapping, args.device))


# ---------------------------------------------------------------- status

def cmd_status(args, host, mapping):
    print("os:", host.os)
    for home in host.herdr_homes():
        path = os.path.join(home, "keys.json")
        keys = read_json(path) if os.path.exists(path) else {}
        changes, clashes = plan_keys(copy.deepcopy(keys), mapping)
        print(f"herdr keys {path}: {len(mapping['actions']) - len(changes) - len(clashes)}/{len(mapping['actions'])} set"
              + (f", {len(clashes)} clash" if clashes else ""))
    db_dir = host.options_dir()
    if db_dir and os.path.exists(os.path.join(db_dir, "settings.db")):
        d = read_settings(db_dir)
        app = find_app(d, mapping["app"])
        print("options+ app:", app.get("name") if app else "missing (add it in Options+)")
        if app:
            left = plan_buttons(copy.deepcopy(d), mapping, load_presets(host))
            print("options+ buttons:", "done" if not left else f"{len(left)} to do")
    else:
        print("options+: not found" + (" (Linux: use `logiops`)" if host.os == "linux" else ""))
    ring_dir = host.ring_dir()
    profs = list(ring_profiles(ring_dir, mapping["app"])) if ring_dir and os.path.isdir(ring_dir) else []
    if not profs:
        print("actions ring: no profile for the app")
    for p in profs:
        writes, missing = plan_ring_icons(p, mapping)
        print(f"actions ring: {len(missing)} actions missing, {len(writes)} icons to update")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mapping", help="mapping.json (default: next to this script)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    k = sub.add_parser("keys")
    k.add_argument("--home", action="append", help="Herdr home (repeatable); default: every one found")
    b = sub.add_parser("buttons")
    b.add_argument("--options-dir")
    r = sub.add_parser("ring")
    r.add_argument("--ring-dir")
    r.add_argument("--create", action="store_true", help="write a whole ring profile for the app (Windows)")
    r.add_argument("--process", default="herdr-app", help="the app's process name (default herdr-app)")
    r.add_argument("--display-name", default="Herdr")
    r.add_argument("--device-type", default="Loupedeck72", help="Loupedeck72 is the MX Master 4 Actions Ring")
    lo = sub.add_parser("logiops")
    lo.add_argument("--device", default="MX Master 4")
    for p in (k, b, r):
        p.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    host, mapping = Host(), load_mapping(args.mapping)
    {"status": cmd_status, "keys": cmd_keys, "buttons": cmd_buttons,
     "ring": cmd_ring, "logiops": cmd_logiops}[args.cmd](args, host, mapping)


if __name__ == "__main__":
    main()
