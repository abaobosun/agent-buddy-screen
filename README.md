# Agent Buddy Screen

[中文](README_ZH.md) | English

Local/LAN read-only status screen for a small Windows secondary display and e-ink reader.

## Run

```powershell
cd C:\path\to\agent-buddy-screen
python .\server.py --host 0.0.0.0 --port 8766
```

Open <http://127.0.0.1:8766> on this PC. From another trusted device on the same LAN, use `http://<this-PC-LAN-IP>:8766`.

- Horizontal secondary-display view: `/`
- Compact e-ink reader view: `/portrait`

## Data Sources

The first version is read-only and uses local files only:

- cc-switch database under the current Windows user's home directory
- Codex session index and rollout logs under the current Windows user's home directory
- Claude session logs under the current Windows user's home directory
- Hermes local state database under the current Windows user's home directory
- Git branch information from the configured workspace directory

The app does not modify cc-switch, Codex, Claude, Hermes, or git state.

## Acknowledgements

Agent Buddy Screen is independently implemented as a local Windows/LAN status
display for Codex, Claude, and Hermes.

The project was inspired by these excellent projects:

- [op7418/m5-paper-buddy](https://github.com/op7418/m5-paper-buddy):
  inspiration for the compact agent-dashboard information architecture and
  e-ink presentation.
- [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy):
  inspiration for the physical agent-companion concept and status feedback.
- [farion1231/cc-switch](https://github.com/farion1231/cc-switch):
  an optional local, read-only data source for provider and usage information.

No source code, firmware, BLE protocol implementation, fonts, artwork, or other
assets from these projects are included in this repository.

## Options

```powershell
python .\server.py --host 127.0.0.1 --port 8766 --workspace C:\path\to\workspace --context-limit 200000
```