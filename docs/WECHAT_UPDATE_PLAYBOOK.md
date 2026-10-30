# 微信小版本更新应对手册

## 症状

微信更新后常见：

```text
未找到 mmui::MainTabBar
热激活 UIA 失败：PID ... 未找到 Weixin.dll
UIA 树不可用，本次会话使用 OCR 驱动
落点窗口不属于微信（进程 A != B）
屏幕截图失败：Coordinate 'right' is less than 'left'
发送消息失败：按群名「...」没有打开目标会话或输入框
```

这时不要再让 Agent「自己找新的 UIA」，也不要让它连续截图、点击、按快捷键。
盲操作会点到别的窗口或触发 OSD/overlay，严重时导致显卡驱动 WATCHDOG 卡死。

## 安全恢复流程

1. 停止微信自动发送（Qunban / wechatauto service）。
2. 手动启动微信，登录，保持主窗口最大化，确认只有一个主窗口。
3. 运行只读体检：

```powershell
.\.venv312\Scripts\python.exe .\scripts\wechat_uia_doctor.py
```

成功应看到：

```text
Weixin.exe
ensure_window: True
root: mmui::MainWindow '微信'
children: [('MMUIRenderSubWindowHW', ...)]
```

4. 启动 wechatauto service，再启动 Qunban。
5. 只做 preflight，不直接发消息：

```text
POST /api/groups/<测试群>/preflight
```

6. preflight 成功后再恢复 Agent 自动发送。

## 版本兼容

当前已验证：

```text
Weixin 4.2.0.12
Weixin.dll identity: 4.2.0.12|205932072|1793333908
QAccessible gate RVA: 0x0B507C08
```

gate 缓存：

```text
%USERPROFILE%\.wechatauto\gate_cache.json
```

换版本后缓存会按 DLL 身份失效；如果 doctor 不能得到 `mmui::MainWindow`，
说明这个版本还没有验证，不应继续自动操作。

## 更新策略

- 关闭微信自动更新，至少保留上一个可用版本的安装目录。
- 更新前备份 `D:\Weixin`，更新后先跑 doctor，再恢复 Agent。
- 版本不兼容时：回滚微信，或只读数据库 + 人工操作，不做 OCR 盲发。
- 不要用截屏点击来“修 UIA”；UIA 修复是确定性维护，不是群聊任务。

## 需要的代码护栏

后续应在 adapter 层加入：

- UIA 树不可用时 fail-closed，禁止自动点击/发送
- 每次点击前校验目标 hwnd 的 PID 属于微信
- 连续两次目标窗口不匹配立即中止
- 屏幕控制期间禁止 OSD/overlay 热键
- 版本/窗口/UIA 状态暴露到 `/api/status` 和 diagnostics

## 当前版本优化

已加入：

```text
app/services/wechat_compat.py
```

记录已验证版本、Weixin.dll 身份和 gate RVA。

service `/v1/status` 现在返回：

```json
{
  "strict_uia": true,
  "uia_ready": true,
  "wechat": {
    "version": "4.2.0.12",
    "dll_exists": true,
    "uia_profile": true,
    "gate_rva": "0xb507c08",
    "known": true
  }
}
```

`ReplicaAdapter` 默认 **strict_uia=true**：

- UIA 不可用时停止发送
- 不再降级 OCR 盲点击
- 报错会提示运行 doctor 或设置 `WECHATAUTO_STRICT_UIA=0`

临时允许 OCR：

```powershell
$env:WECHATAUTO_STRICT_UIA = "0"
```

不建议在微信刚更新、UIA 未验证时开启。

## 启动自动版本检查开关

service 启动时默认先读取 `Weixin.exe` 版本，并和 `%USERPROFILE%\.wechatauto\uia_state.json` 比较：

```text
版本相同 + 上次 UIA 成功 → 跳过扫描，快速启动
版本变化，或上次 UIA 失败 → 自动做一次 UIA 探测
探测成功 → 记录版本和 uia_ok=true
探测失败 → 记录失败，strict_uia 会阻止发送
```

开关：

```powershell
# 默认开启
$env:WECHATAUTO_AUTO_UIA_PROBE = "1"

# 关闭自动探测（只允许人工 probe）
$env:WECHATAUTO_AUTO_UIA_PROBE = "0"
```

手动探测：

```powershell
curl -X POST http://127.0.0.1:8010/v1/uia/probe
```

service status 会显示：

```json
{
  "auto_uia_probe": true,
  "uia_probe": {
    "probed": true,
    "version": "4.2.0.12",
    "uia_ok": true
  },
  "uia_ready": true
}
```

启动探测最多重试 4 次，避免微信刚启动、窗口还没就绪时误记失败。
