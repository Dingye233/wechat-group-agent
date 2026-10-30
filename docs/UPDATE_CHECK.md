# 群伴 GitHub 更新检测

## 接口

```text
GET  /api/update/status
POST /api/update/check
```

返回：

```json
{
  "update": {
    "repo": "https://github.com/Dingye233/wechat-group-agent",
    "branch": "main",
    "current": "<本地 commit>",
    "current_short": "60ab459a79a0",
    "latest": "<远端 commit>",
    "latest_short": "60ab459a79a0",
    "current_version": "0.1.0",
    "update_available": false,
    "latest_message": "Auto-probe UIA only when the WeChat version changes",
    "latest_time": "2026-10-30T15:48:08Z",
    "checked_at": "...",
    "error": ""
  }
}
```

## 判定方式

群伴当前没有 release tag，因此以 GitHub `main` 的 commit 为准，和 DSH 用 tag 不同：

```text
本地 git rev-parse HEAD
远端 git ls-remote origin refs/heads/main
```

如果远端 commit 是本地 HEAD 的祖先，则视为本地更新，不提示更新。

检测只读，不执行 `git pull`，不修改 checkout。

## 前端

助手 → DeepSeek Harness 面板顶部：

```text
群伴版本  v0.1.0 · #60ab459a79a0
远程 #... · 已是最新 / 有新提交
[检查群伴更新]
```

点击只是重新检测，不会自动拉取代码。

## 手动检查

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/update/check
```

如果 GitHub 不可达，接口返回 `error`，UI 显示检查失败，不影响 Qunban 运行。

## 安全更新

前端在有新提交时显示「安全更新」。流程：

```text
检查 tracked 文件是否干净
→ 拒绝未提交修改
→ git fetch origin main
→ 校验 fast-forward
→ 备份 user state
→ git merge --ff-only FETCH_HEAD
→ 标记 restart_required
```

不会执行：

```text
git reset --hard
git clean
覆盖 data/
删除插件
```

备份位置：

```text
data/update-backups/<timestamp>/
```

备份范围：

```text
data/agent.db
data/wechatauto-queue.db
data/knowledge
data/dsh/studio
data/dsh/home/profiles（不含 node_modules/sessions/logs）
profiles/*/node_modules/@qunban
.env
```

不备份：

```text
data/agent_workspace（可能数 GB）
data/dsh/runtimes
data/media
wechatauto_logs
```

更新完成后必须重启 Qunban 才加载新代码。失败时接口返回错误，备份目录保留。
