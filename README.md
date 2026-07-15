# Agent Buddy Screen

[中文](README_ZH.md) | English

A local/LAN read-only status screen for a small Windows secondary display and e-ink reader. The dashboard provides three switchable views: Codex, Notion, and Hermes.

## Run

Double-click:

- `start-agent-buddy.cmd` to start the server in the background
- `stop-agent-buddy.cmd` to stop it

Or run it manually:

```powershell
python .\server.py --host 0.0.0.0 --port 8766
```

Open <http://127.0.0.1:8766> on this PC. From another trusted device on the same LAN, use `http://<this-PC-LAN-IP>:8766`.

- Horizontal secondary-display view: `/`
- Compact e-ink portrait view: `/portrait`

## Data Sources

The application is read-only:

- cc-switch database and Codex session logs under the current Windows user's home directory
- Notion `Session Log` data source through the official Notion API
- Hermes local state database under the current Windows user's home directory
- Git branch information from the configured workspace directory

The app does not modify cc-switch, Codex, Notion, Hermes, or git state.

## Connect Notion

The default Notion data source is the supplied `Session Log 会话日志` database. To let this standalone application read it:

1. Create a Notion internal integration and copy its token.
2. Open the `Session Log 会话日志` database in Notion, then add the integration under **Connections**.
3. Store the token in the current Windows user's environment:

```powershell
[Environment]::SetEnvironmentVariable("NOTION_TOKEN", "ntn_your_token", "User")
```

Restart Agent Buddy after setting the variable. To use another Notion data source, also set:

```powershell
[Environment]::SetEnvironmentVariable("NOTION_DATA_SOURCE_ID", "your-data-source-id", "User")
```

Never commit a Notion token to this repository.

## Acknowledgements

Agent Buddy Screen is independently implemented as a local Windows/LAN status display.

The project was inspired by these excellent projects:

- [op7418/m5-paper-buddy](https://github.com/op7418/m5-paper-buddy): inspiration for the compact agent-dashboard information architecture and e-ink presentation.
- [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy): inspiration for the physical agent-companion concept and status feedback.
- [farion1231/cc-switch](https://github.com/farion1231/cc-switch): an optional local, read-only data source for provider and usage information.

No source code, firmware, BLE protocol implementation, fonts, artwork, or other assets from these projects are included in this repository.

## Options

```powershell
python .\server.py --host 127.0.0.1 --port 8766 --workspace C:\path\to\workspace --context-limit 200000
```