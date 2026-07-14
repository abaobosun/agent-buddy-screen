# Agent Buddy Screen

[English](README.md) | 中文

一个用于 Windows 副屏和墨水屏阅读器的本地/局域网只读 Agent 状态屏，可查看 Codex、Claude 与 Hermes 的状态。

## 运行

```powershell
cd C:\path\to\agent-buddy-screen
python .\server.py --host 0.0.0.0 --port 8766
```

本机打开 <http://127.0.0.1:8766>。同一可信局域网内的设备可访问 `http://<本机局域网-IP>:8766`。

- 横向副屏页面：`/`
- 紧凑型墨水屏竖屏页面：`/portrait`

## 数据来源

第一版为只读模式，只读取本机文件：

- 当前 Windows 用户目录中的 cc-switch 数据库
- 当前 Windows 用户目录中的 Codex 会话索引与 rollout 日志
- 当前 Windows 用户目录中的 Claude 会话日志
- 当前 Windows 用户目录中的 Hermes 本地状态数据库
- 已配置工作目录中的 Git 分支信息

程序不会修改 cc-switch、Codex、Claude、Hermes 或 Git 状态。

## 致谢

Agent Buddy Screen 是为 Codex、Claude 与 Hermes 独立实现的 Windows/局域网状态显示程序。

本项目参考并受到以下优秀项目启发：

- [op7418/m5-paper-buddy](https://github.com/op7418/m5-paper-buddy)：紧凑 Agent 看板的信息结构与墨水屏展示思路。
- [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy)：实体 Agent 伴侣和状态反馈的产品思路。
- [farion1231/cc-switch](https://github.com/farion1231/cc-switch)：可选的本地只读 Provider 与用量数据来源。

本仓库不包含上述项目的源代码、固件、BLE 协议实现、字体、美术资源或其他资产。

## 参数

```powershell
python .\server.py --host 127.0.0.1 --port 8766 --workspace C:\path\to\workspace --context-limit 200000
```