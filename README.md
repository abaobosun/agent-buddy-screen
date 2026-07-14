# Agent Buddy Screen

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

## Options

```powershell
python .\server.py --host 127.0.0.1 --port 8766 --workspace C:\path\to\workspace --context-limit 200000
```