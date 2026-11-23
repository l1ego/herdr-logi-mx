---
name: logi-mx
description: "Make a Logitech MX mouse drive the Herdr app: gesture-button directions, back/forward and the Actions Ring switch spaces, tabs and agents, open the palette or lazygit, split panes, and launch Claude, Codex or Kimi. Use when the user wants mouse gestures, buttons or Actions Ring actions/icons for Herdr, wants to change them, or wants the same setup on another machine (Windows, macOS, Linux)."
---

# logi-mx

The mouse never talks to Herdr directly. Every action has a **hard-to-type key
combo** (`ctrl-shift-f1`…, `ctrl-alt-shift-f1`…): Herdr binds the combo, and the
mouse sends it. One file, `mapping.json`, holds action → combo → mouse control →
ring place → icon. `logi_mx.py` (Python 3, standard library) applies it.

Run every command from this folder: `python3 logi_mx.py <command>`.

| Layer | Where | Command |
| --- | --- | --- |
| Herdr keys | `keys.json` in each Herdr home (`~/.config/herdr-app`, and the Windows one from WSL) | `keys` |
| Gesture button, back, forward | Logi Options+ `settings.db`, the app's own profile | `buttons` |
| Actions Ring | Logi Plugin Service `Applications/Loupedeck72/<app>/Profiles/<id>` | `ring --create`, `ring` |
| Linux (no Options+) | `logid.cfg` for logiops | `logiops` |

## Procedure

1. `python3 logi_mx.py status`: what is done and what is missing on this machine.
2. **Herdr keys**: `keys --dry-run`, then `keys`.
   - A combo already bound to something else is reported as a clash and left alone. Ask the person before changing either side.
   - Then check `herdr-app status` (or `herdr-app.exe status` for the Windows app) for `errors`.
3. **Options+ buttons** (Windows, macOS):
   - Options+ must already list the app. If `status` says the app is missing, ask the person to add it in Logi Options+ (Add application → the Herdr executable).
   - `buttons --dry-run`, then `buttons`.
   - Tell the person first: the command closes the Options+ window and restarts its agent for a few seconds. The mouse works as a plain mouse meanwhile.
   - The script makes a backup (`backup-logi-mx-<time>` next to `settings.db`) and re-reads the file afterwards to confirm the change stuck.
4. **Actions Ring**:
   - No ring profile for the app yet: `ring --create --dry-run`, then `ring --create`.
     - Windows only. It writes the whole profile (actions, the agents folder, icons) as the Options+ UI does, then restarts the Logi Plugin Service.
     - Defaults: `--process herdr-app --display-name Herdr --device-type Loupedeck72` (the MX Master 4 ring).
   - Elsewhere, or if the person prefers it, they add the actions in Options+ (Actions Ring → the app → "Keyboard shortcut" actions, plus one folder). `ring --dry-run` prints the table of what is missing.
   - With the actions in place, `ring` sets the icons. It is safe to run again.
5. Ask the person to try it with Herdr in front: gestures up/down (spaces), left/right (tabs), back/forward (agents), and the ring.

All of it applies only while Herdr is the foreground app: Options+ profiles and the ring are per app.

## Changing the set

- Edit `mapping.json`: `combo`; `mouse` (`gesture-up|down|left|right|click`, `back`, `forward`); `ring` (1–7, or `"folder"`); `label`; `icon` (a file in `icons/`); `colored`; `herdr` (any keys.json action, string or `{"command", "args"}`); `herdr_omarchy` (used when keys.json has a `layout:omarchy` section).
- Then run `keys`, `buttons` and `ring` again. All three are idempotent.
- Pick combos nobody types:
  - On Windows, ctrl+alt is AltGr, so ctrl-alt combos only with F-keys.
  - Avoid alt+F4 and the Windows key.

## What the person cannot do in the Options+ UI

- **Gesture directions.** For the MX Master 4 the UI has no "custom gestures" mode, and Smart Actions bind the whole button, not a direction. Only `buttons` sets per-direction keystrokes.
  - Warn the person: editing the gesture button in the UI afterwards replaces them.
  - Run `buttons` again to restore them.
- A Smart Action whose trigger is "Show Actions Ring" steals the ring button in that app. `buttons` puts the ring back.

## Facts (for debugging)

- **settings.db**
  - It is SQLite, table `data`, one row: a JSON blob.
  - An app profile is the key `profile-<applicationId>` with `"id" = applicationId` and `"baseProfileId"` = the default profile's id. Its `assignments` are only the mouse's Buttons-page slots.
  - Wrong id: the gestures still run, but the UI shows "MISSING ASSIGNMENT".
  - Slots are `<device>_c<cid>`: 195 gesture, 83 back, 86 forward, 416 ring button. The gesture card's `custom_gesture` nested card holds `up/down/left/right/click`.
  - Edit only with the agent stopped: copy the files out, checkpoint, edit, delete `-wal`/`-shm`, copy back, start the agent.
- **Ring**
  - `ProfileInfo.json` → `profileActions`: `$@Generic___@KeyboardKey` with `keyboardKey` = `ControlOrCommand+Shift+F9___68158480___Ctrl+Shift+F9___win-<VK>#¤%&+?<mods>#¤%&+?68158480#¤%&+?0`, where mods is 132 for ctrl+shift and 134 for ctrl+alt+shift. A folder is `$@Generic___@OpenFolder`.
  - `ApplicationInfo.json` ties the folder to the app's process.
  - The layout has one 8-place page and a `folderPages` entry per folder, holding only the used places.
  - Nothing outside the app's folder refers to the profile (only `CurrentApplication` in `LoupedeckSettings.ini`).
  - Icon files are `ActionIcons/<action name>.ict`: JSON whose Image item is a base64 SVG.
  - A one-color SVG is tinted by the ring theme. A multi-color one is drawn as is on `backgroundColor`, so `colored: true` uses a white background.
- **Platforms**
  - Tested on Windows 11 from WSL. Native Windows Python uses the same code.
  - macOS paths (`~/Library/Application Support/LogiOptionsPlus`, `…/Logi/LogiPluginService/Applications`) and process names are **unverified**. Check that they exist and run `status` and `--dry-run` before writing; pass `--options-dir` / `--ring-dir` if they differ.
  - Linux has no Options+ and no ring. `logiops` prints a `logid.cfg` snippet for the person to merge, which needs sudo. It is global, not per app, which is why the combos must be harmless elsewhere.

Tests: `python3 tests/test_logi_mx.py` (offline, no Logitech software needed).
