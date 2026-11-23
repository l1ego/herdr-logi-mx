# herdr-logi-mx

Drive the Herdr app from a Logitech MX mouse (built and tested with an MX Master 4 on Windows 11):

| Mouse | Herdr |
| --- | --- |
| Gesture button ↑ / ↓ | previous / next space |
| Gesture button ← / → | previous / next tab |
| Gesture button click | picker |
| Back / Forward | previous / next agent |
| Actions Ring | palette, lazygit, split right, split down, zoom pane, copy, paste |
| Actions Ring → Agents | new tab with Claude, Codex or Kimi |

Only while Herdr is the foreground app; everywhere else the mouse keeps its usual setup.

It is an agent skill: tell your agent "set up my MX mouse for Herdr" and it follows [`SKILL.md`](SKILL.md).
By hand:

```sh
python3 logi_mx.py status
python3 logi_mx.py keys        # Herdr keys.json
python3 logi_mx.py buttons     # Logi Options+: gestures, back, forward
python3 logi_mx.py ring        # Actions Ring icons (add the actions in Options+ first)
python3 logi_mx.py logiops     # Linux: logid.cfg snippet
```

Every command takes `--dry-run`, and every write makes a backup first. Change the actions, combos or icons in [`mapping.json`](mapping.json).

Install as a skill for Claude Code, Codex and other agents:

```sh
bash install.sh
```

Windows is tested (from WSL). macOS uses the same Options+ formats, but its paths are unverified. Linux gets a logiops snippet.
