# Agent Buddy Screen

[English](README.md) | 中文

一个用于 Windows 小尺寸副屏和墨水屏阅读器的本地/局域网只读 Agent 状态屏。页面保留三个可切换入口：Codex、Notion 和 Hermes。

## 运行

直接双击：

- `start-agent-buddy.cmd`：后台启动程序，不显示命令行窗口
- `stop-agent-buddy.cmd`：关闭程序

也可以手动运行：

```powershell
python .\server.py --host 0.0.0.0 --port 8766
```

本机打开 <http://127.0.0.1:8766>。同一可信局域网内的其他设备使用 `http://<本机局域网-IP>:8766`。

- 横向副屏页面：`/`
- 紧凑型墨水屏竖屏页面：`/portrait`
- Kindle 大字体兼容页面：`/kindle`

## 数据来源

程序只读，不会修改数据源：

- 当前 Windows 用户目录中的 cc-switch 数据库和 Codex 会话日志
- 通过 Notion 官方 API 读取的 `Session Log 会话日志` 数据源
- 当前 Windows 用户目录中的 Hermes 本地状态数据库
- 已配置工作目录中的 Git 分支信息

程序不会修改 cc-switch、Codex、Notion、Hermes 或 Git 状态。

## 连接 Notion

程序默认读取你提供的 `Session Log 会话日志` 数据库。因为这是独立运行的本地程序，需要给它单独配置 Notion Integration：

1. 在 Notion 中创建一个内部 Integration，并复制 Token。
2. 打开 `Session Log 会话日志` 数据库，在“连接/Connections”中添加这个 Integration。
3. 在 Windows PowerShell 中保存 Token：

```powershell
[Environment]::SetEnvironmentVariable("NOTION_TOKEN", "ntn_your_token", "User")
```

设置完成后重启 Agent Buddy。若以后要改用其他 Notion 数据源，再设置：

```powershell
[Environment]::SetEnvironmentVariable("NOTION_DATA_SOURCE_ID", "你的-data-source-id", "User")
```

不要把 Notion Token 写进代码，也不要提交到 GitHub。

Notion 页面中的字段对应关系：

- `SESSIONS`：最近的会话日志，点击后切换当前记录
- `SESSION`：当前会话标题
- `SOURCE`：Notion AI / Session Log
- `STATUS`：进行中或已完结
- `CHANGE`：当前记录是否填写了“变更”
- `TODO`：当前记录是否还有“待办”
- `DECISION`：当前记录的“结论/决策”

## 致谢

Agent Buddy Screen 是独立实现的 Windows/局域网状态显示程序。

本项目参考并受到以下优秀项目启发：

- [op7418/m5-paper-buddy](https://github.com/op7418/m5-paper-buddy)：紧凑 Agent 看板的信息结构与墨水屏展示思路。
- [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy)：实体 Agent 伴侣和状态反馈的产品思路。
- [farion1231/cc-switch](https://github.com/farion1231/cc-switch)：可选的本地只读 Provider 与用量数据来源。

本仓库不包含上述项目的源代码、固件、BLE 协议实现、字体、美术资源或其他资产。

## 参数

```powershell
python .\server.py --host 127.0.0.1 --port 8766 --workspace C:\path\to\workspace --context-limit 200000
```