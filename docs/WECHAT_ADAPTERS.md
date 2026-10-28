## 0. 近期变更（2026-10）

- 默认使用 `replica` 适配器读取微信 4.x 本地数据库，不再依赖 3.9 界面自动化。
- 群名、群 ID 和消息从本地数据库读取；过滤 `<id>@chatroom` 形式的临时原始 ID。
- 发送消息、图片和表情仍通过 `wechatauto-replica` 完成；读取不需要保持微信前台。
- 默认每 1 秒读取一次，每个群有独立计时器。

# 微信适配器说明

当前版本内置三个适配器：

| 适配器 | 平台 | 登录方式 | 适用场景 |
| --- | --- | --- | --- |
| `mock` 演示模式 | 全平台 | 网页二维码 + 自动确认 | 开发、演示、UI 联调、二次开发 |
| `replica` 微信 4.x 本地数据库 | Windows | 复用已登录微信 | **推荐；群/消息只读数据库，发送走 wechatauto-replica** |
| `wcf` 微信 PC（WeChatFerry） | Windows | 官方登录窗口截图 + 手机扫码 | 已固定使用微信 3.9.12.51 的实验性方案 |

> `uia.py` 和 `requirements-uia.txt` 是微信 3.9 旧方案，当前未注册；新部署请使用 `replica`。

所有适配器实现同一份 `BaseAdapter` 接口，Runtime、Agent、前端完全复用。

---

## 1. 为什么需要适配器

微信没有开放个人号机器人 API，社区方案大致分为：

| 方案 | 代表项目 | 原理 | 优点 | 风险/限制 |
| --- | --- | --- | --- | --- |
| PC Hook | WeChatFerry | 注入微信 PC 客户端 | 速度快、可拿到 wxid、支持图片/GIF | 仅 Windows、绑定微信版本、封号风险、上游维护不稳定 |
| UI 自动化 | wxauto | 通过 Windows UIAutomation 读取/操作 PC 微信 | 不注入、兼容 32 位微信、版本要求宽松 | 依赖界面、较慢、会切换前台、拿不到真实 wxid |
| 协议/多协议框架 | Wechaty | 不同 puppet 适配 | 生态大、接口统一 | 免费 puppet 多已失效，商业 puppet 需付费 |
| Web 协议 | itchat 类 | 网页版微信 | 扫码简单 | 微信早已限制大多数账号，基本不可用 |
| 官方 API | 企业微信 / 公众号 | 官方接口 | 合规、稳定 | 不是个人微信群聊 |

因此本项目把协议放在适配器层：默认用 mock 保证“开箱即用”，真实场景优先界面自动化；后续可以继续替换为其他内部协议，而无需改 Agent 与前端。

---

## 2. 界面自动化（推荐）

### 2.1 为什么选择它

微信 PC 3.9 的 32 位客户端目前仍能登录，但 WeChatFerry 对 3.9.12.56 的通讯录/数据库适配不完整，会出现“登录成功但群列表为 0”的问题。界面自动化直接读取微信窗口，不依赖协议偏移与 DLL 版本，因此更适合当前环境：

```text
微信 PC 3.9.12.56 32 位
  + 64 位 Windows 兼容层
  + wxauto / UIAutomation
  → 扫码、群列表、成员昵称、最近聊天记录、实时消息
```

### 2.2 依赖

```powershell
.\.venv312\Scripts\python.exe -m pip install -r requirements-uia.txt
```

`requirements-uia.txt` 从项目的 `vendor/wxauto` 安装界面自动化库，并安装：

- `pywin32`：窗口、截图、兼容层相关
- `comtypes`：UIAutomation COM 接口
- `pyperclip`：发送消息时写入剪贴板
- `psutil`、`tenacity`、`pillow`
- `wxauto`：随项目分发的 3.9 界面自动化封装

### 2.3 登录流程

```text
用户点击「微信 PC（界面自动化）」
  → 适配器查找 WeChat.exe（设置项 / 注册表 / 常见路径）
  → 若微信未运行则启动（__COMPAT_LAYER=~ ARM64WOWONAMD64）
  → 后台线程读取 WeChatLoginWndForPC 的「二维码」控件
  → 图片经 EventBus 推送到前端 <img>
  → 用户手机扫码
  → 主窗口出现后初始化 wxauto
  → 读取账号昵称，进入在线状态
  → 读取通讯录「群聊」列表（失败时回退到会话列表识别）
  → 开始轮询新消息
```

关键点：

- 不需要注入微信，不需要 `wcferry` / `spy.dll`。
- 3.9 是 32 位程序，服务启动它时会自动加兼容层，避免“版本过低”。
- 登录二维码来自微信自己的二维码控件，不是整窗截图。
- 如果二维码区域为空，可以直接扫描电脑上的微信登录窗口；登录成功后网页会自动进入。

### 2.4 群列表与群成员

群列表的读取顺序：

1. 打开微信「通讯录 → 通讯录管理 → 最近群聊」，读取真实群聊行；
2. 如果该窗口不可用，回退到「通讯录 → 群聊」页面；
3. 如果仍不可用，回退到当前会话列表的「发送者：内容」预览识别，只读不打开群聊。

> 旧版本曾把「置顶 / 最小化 / 最大化 / 关闭」等窗口按钮或「群聊」页签误当成群名。现在只从 `ListControl` 的真实行读取，并过滤窗口按钮、页签和系统项。

群表新增 `account_key` 字段：群聊、消息、成员按微信账号隔离。切换账号后，旧账号的群保留但不会显示，也不会被新账号的成员同步/发送逻辑打开；切回旧账号时恢复。直接通过旧群 ID 调用成员、消息、发送接口会返回「该群聊属于其他微信账号」。

### 2.4.1 群级读取/回复开关

每个群在 `GroupConfig` 中有两个独立开关：

| 开关 | 字段 | 行为 |
| --- | --- | --- |
| 读取群消息 | `read_enabled` | 关闭后适配器不读取该群的新消息，Runtime 也会丢弃该群消息事件 |
| 自动回复 | `agent_enabled` | 开启前必须开启读取；只开读取、不开回复就是只读 |

Runtime 通过 `apply_read_filter()` 把当前账号下 `read_enabled=true` 的群 ID 下发给适配器：

- UI 自动化适配器只处理这些群的会话预览，其他群即使出现在微信会话列表也不读取；
- 发送、成员同步、手动刷新不受读取开关限制，仍然可以手动操作；
- 关闭读取时会自动关闭回复；开启回复时会自动开启读取。

### 2.4.2 后台读取

消息轮询只读取会话列表的预览文本：

```text
群预览：发送者：内容
私聊预览：内容（没有发送者前缀）
```

因此它不需要打开群聊，也不需要微信窗口在前台。微信被其他窗口遮挡时可以持续读取；最小化时使用 `SW_SHOWNOACTIVATE` 恢复窗口，但不激活、不抢焦点。只有发送消息、同步群成员、手动刷新群列表时才会短暂切到前台。

每个群有独立计时器：

- `read_interval_seconds` 是群级覆盖值，`0` 表示使用全局默认 `read_poll_interval_seconds`（默认 5 秒，1–300 秒）。
- Runtime 把 `{群 external_id: 间隔}` 下发给适配器；适配器为每个群维护 `next_check`，到期后只处理该群。
- 调度时先读一次会话列表，按清理后的群名建立索引；然后只把到期的、`read_enabled=true` 的群交给预览解析。未勾选读取的群不会进入计时器，也不会处理其预览。
- 下次检查时间会加入轻微随机抖动，避免所有群同时刷新。
- 诊断：`GET /api/debug/uia?action=timers` 可以看到每个群的间隔和下次检查倒计时。
- 登录时如果已有读取计划，直接使用保存的群名索引启动，不重新扫描整个通讯录；需要发现新群时再手动刷新群列表。

### 2.4.2 置顶当前聊天

- 群聊控制台/聊天标题提供置顶按钮，全局只允许一个置顶群。
- 置顶会自动把该群 `read_enabled` 设为 true，并在微信聊天列表中选中该聊天。
- 适配器每秒读取一次置顶聊天窗口的 `GetAllMessage()`，只发送基线之后的新消息；置顶群不再走普通会话预览计时器。
- 置顶新群会覆盖旧置顶；置顶 ID 保存在设置表，重新登录/重启后会恢复。
- 如果置顶群被关闭读取、删除或切换到其他微信账号，适配器会自动取消置顶/不打开该聊天。

### 2.4.3 重复消息防护

- 会话预览指纹只使用“发送者 + 内容”，不包含 `[N条]` 未读数或“21:33/昨天”等会变化的标签。
- 历史同步消息在 Runtime 中按“群 + 内容 + 时间窗口”去重（历史 10 分钟，实时 45 秒且匹配发送者），避免 UIA runtime id 变化导致重复入库。
- 置顶当前聊天首次读取会先建立基线，不会把窗口里的旧消息重新发一遍。

限制：预览只包含每个群的最新一条消息；两次轮询之间的多条消息只保留最后一条。完整最近记录在选中群聊时同步。只有出现在当前会话列表中的群才能按预览读取；不在列表中的群需要先在微信里打开一次。

图片/表情包消息默认只记录占位（`[图片]` / `[动画表情]`），不会打开微信图片预览。设置中的「尝试下载图片/表情包」默认关闭；开启后才会尝试保存图片到 `data/media/chat`，原生表情包可能只能截取静态图或保留占位。该功能较慢，建议确认核心读取/回复稳定后再开启。

群成员的读取：

- 选中群聊后，控制台会调用 `GET /api/groups/{id}/members`；
- 适配器打开该群聊，点击「聊天信息」读取成员昵称；
- 如果成员面板读取失败，会退回从当前聊天记录中的发送者昵称提取；
- 成员 id 使用 `uia:昵称`，因为界面不暴露真实 `wxid`。

### 2.5 聊天记录

- 选中群聊时，适配器会打开该群、加载一页更早消息，并把最近约 60 条消息同步入库；
- 之后每个开启读取的群按自己的计时器，通过会话列表中的群名索引读取该群的最新预览；
- 系统消息、时间分隔、撤回提示会被跳过；图片/文件/语音/视频/表情只记录占位文本；
- 消息 `external_id` 使用 `群名 + UIA runtime id` 的哈希，Runtime 会去重。

### 2.6 发送文字与表情包

- 文字：打开目标群聊，通过剪贴板粘贴并回车发送；
- 表情包：微信界面没有稳定的“选择指定表情包”接口，因此把表情图片作为图片消息发送；控制台仍会把它记录为 `sticker`；
- 发送时微信窗口会被切到前台，这是 UI 自动化无法完全避免的；
- 微信窗口必须保持登录并打开，最小化时部分操作可能失败；
- 所有点击通过缓动曲线平滑移动光标，不使用瞬移；点击前有随机停顿，点击点会在控件内轻微随机；中文发送使用剪贴板并加入随机前后等待，避免机械式瞬间操作。

### 2.7 线程模型

```text
asyncio 主线程
  ├─ Runtime / API / WebSocket
  ├─ 发送、查成员 → 提交命令到队列
  └─ 后台守护线程
       ├─ 启动/检测微信
       ├─ 捕获登录二维码
       ├─ 轮询会话与新消息
       ├─ 发现群聊
       └─ 串行执行所有 UIAutomation 操作
```

所有 UIAutomation/COM 调用都在同一个线程执行，避免跨 apartment 错误；其他线程只等待结果。

### 2.8 限制与注意事项

| 限制 | 说明 |
| --- | --- |
| 需要保持微信窗口 | 退出微信、锁屏过久、窗口被系统挂起都可能导致读取失败 |
| 无法拿到 wxid | 群 id 为 `uia:群名`，成员 id 为 `uia:昵称`；改群名会产生新群记录 |
| 同名群/同名成员 | 界面无法区分，可能合并 |
| 会切换前台 | 同步成员、发送消息时微信窗口会短暂置前 |
| 表情包以图片发送 | 不是微信原生表情消息 |
| 速度较慢 | 每次同步/发送需要 UI 操作，通常 1–5 秒 |
| 不支持微信 4.x | 4.x 窗口结构不同，请使用 3.9.x |

### 2.9 诊断：到底读到了什么

适配器提供两个只读诊断接口（仅监听本机，无鉴权，调试完可关闭服务）：

```text
GET /api/debug/uia?action=sessions
GET /api/debug/uia?action=recent_groups
GET /api/debug/uia?action=contact_manager
```

- `sessions`：逐条返回会话名称、时间、未读数、解析出的发送者/内容、是否通过群名校验。
- `recent_groups`：打开「通讯录管理 → 最近群聊」，返回读取到的真实群名。
- `contact_manager`：返回通讯录管理窗口的完整 UIA 树，用于排查 UI 结构变化。

示例（真实输出）：

```text
session|示例群聊A|time=20:37|new=False|sender=示例成员|content=示例消息|valid=True
session|示例群聊B|time=21:33|new=True|sender=[2条] 示例成员|content=示例消息|valid=True
manager|12|ButtonControl|示例群聊C|...
manager|12|ButtonControl|示例群聊A|...
```

### 2.10 常见问题

| 问题 | 原因 | 处理 |
| --- | --- | --- |
| 适配器不可用 | 缺少 wxauto / 找不到微信 3.9 | 执行 `pip install -r requirements-uia.txt`；确认 3.9 已安装 |
| 一直等待扫码 | 微信未启动 / 登录窗口被遮挡 | 手动打开微信登录窗口；确认二维码可见 |
| 二维码空白 | 登录窗口未就绪 | 重新登录；直接扫描微信窗口中的二维码 |
| 登录后群列表为空 | 通讯录页面结构未识别 / 群聊未加载 | 点「刷新群列表」；在微信里打开一次群聊；查看 `data/server.log` |
| 群成员为空 | 聊天信息面板读取失败 | 手动打开群聊后重试；查看日志 |
| 发送失败 | 微信窗口被锁/未登录/群名变化 | 确认微信在线；刷新群列表后重试 |
| 群列表出现「最大化/最小化/关闭」 | 旧版采集误读窗口按钮 | 升级后已修复；可调用 `/api/debug/uia?action=recent_groups` 查看真实群名 |
| 微信窗口频繁弹出 | 发送/同步成员仍需要操作窗口 | 后台读取本身不抢前台；可降低同步频率 |

---

## 3. WeChatFerry（实验性备选）

### 3.1 版本

WCF 通过注入 `WeChat.exe` 工作，不同 WCF Release 对应不同的微信版本。当前项目固定：

```text
WeChat PC 3.9.12.51
wcferry 39.5.2.0
```

**微信 4.x 不支持**。也不建议在 3.9.12.56 上使用 WCF：上游对它的通讯录读取适配不完整，可能出现登录成功但群列表为 0。

如果已经安装 3.9.12.51，可以保留 WCF 作为备选；否则优先使用界面自动化。

### 3.2 安装依赖

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-wcf.txt
```

`requirements-wcf.txt` 包含：

- `wcferry`：Python SDK
- `pywin32`：枚举窗口、截图
- 同时依赖 `Pillow`（基础依赖已包含）

推荐 Python 3.11 / 3.12。Python 3.14 目前可能没有 wcferry 原生依赖的预编译包。

### 3.3 登录流程

```text
用户点击「微信 PC（WeChatFerry）」
  → 适配器查找 WeChat.exe（设置项 / 注册表 / 常见路径）
  → 若微信未运行则启动
  → 后台线程枚举 WeChatLoginWndForPC，截图登录窗口
  → 图片经 EventBus 推送到前端 <img>
  → 用户手机扫码
  → 适配器尝试 Wcf().get_user_info()
  → 成功：enable_receiving_msg()，拉群列表，进入接收循环
```

为什么用截图：WCF 只在登录后注入，无法在未登录时通过 SDK 直接拿到二维码。截图方案不需要破解登录协议，但需要微信窗口可见（代码会尝试置前）。

如果截图失败：

- 手动打开微信登录窗口扫码；
- 登录成功后 WCF 会自动接入，网页状态会变成 online；
- 检查是否安装了 `pywin32` 与 `Pillow`。

### 3.4 微信 3.9 的「版本过低」

微信 3.9 是 32 位程序，在 64 位 Windows 上直接启动会提示「版本过低」，无法完成登录。解决方式是给微信进程设置：

```text
__COMPAT_LAYER=~ ARM64WOWONAMD64
```

本项目在适配器启动微信时自动注入该环境变量，并把兼容层写入当前用户的注册表：

```text
HKCU\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers
C:\Program Files\Tencent\WeChat\WeChat.exe = ~ ARM64WOWONAMD64
```

方案来自 [WeChat3.9-32bit-Compatibility-Launcher](https://github.com/Skyler1n/WeChat3.9-32bit-Compatibility-Launcher)，未修改微信文件。

### 3.5 消息接收

`WeChatFerryAdapter` 使用独立守护线程运行阻塞 SDK：

```text
threading.Thread(_worker)
  ├─ 等待微信启动
  ├─ 尝试 Wcf() 连接
  ├─ get_msg(block=True, timeout=1) 循环
  ├─ 解析 type 1/3/34/43/47/49/10000
  ├─ 解析群消息 "wxid:\n内容"
  ├─ 用 get_chatroom_members 缓存昵称
  └─ emit("message", ...)
```

注意：

- 群消息发送者昵称来自 `get_chatroom_members` 缓存（10 分钟）；首次进入群或缓存过期时可能短暂显示 wxid。
- 图片/语音/视频不下载原文件，只记录占位文本；表情包发送使用本地文件。
- 自己通过 WCF 发出的消息可能被回显，适配器用最近发送内容/时间过滤，避免重复入库。
- 微信客户端掉线后消息循环会退出；需要重新扫码时状态回到 `qr`。

### 3.6 发送

```python
wcf.send_text(text, room_id)
wcf.send_image(str(path), room_id)   # 表情包
```

GIF 会优先尝试 `send_emotion`（部分 WCF 版本提供），失败或返回非 0 时回退为 `send_image`。不同客户端版本对 GIF 的展示可能不同。

### 3.7 常见坑

| 问题 | 原因 | 处理 |
| --- | --- | --- |
| `缺少 wcferry` | 未安装额外依赖 | `pip install -r requirements-wcf.txt` |
| 一直等待登录 | 微信版本不对 / 未启动 | 安装 3.9.12.51，手动打开窗口 |
| 注入失败 | 权限、杀软、微信 4.x | 管理员运行、关闭拦截、降级微信 |
| 二维码空白 | 截图失败 | 手动扫码；安装 pywin32 |
| 群列表为空 | `get_chatrooms()` 不可用/版本不匹配 | 改用界面自动化；或安装 3.9.12.51 后重试 |
| 发送返回错误码 | 掉线/被限制/参数错误 | 重新登录，降低频率，检查日志 |
| 账号被封 | 平台风控 | 使用小号、控制频率、遵守平台规则 |

---

## 4. 新增一个适配器

### 4.1 实现接口

```python
# app/adapters/my_adapter.py
from pathlib import Path
from app.adapters.base import BaseAdapter, GroupInfo, IncomingMessage


class MyAdapter(BaseAdapter):
    adapter_id = "my"
    display_name = "我的协议"
    description = "接入内部微信网关"

    @classmethod
    def availability(cls) -> tuple[bool, str]:
        # 返回 (是否可用, 不可用原因)，前端会展示
        return True, ""

    async def start(self) -> None:
        self.emit("status", state="starting", message="正在连接…")
        # 建立连接、获取二维码、登录成功后：
        self.emit("status", state="online", message="连接成功")
        self.emit("account", wxid="...", name="...")
        self.emit("groups", groups=[GroupInfo(external_id="xxx@chatroom", name="测试群")])

    async def stop(self) -> None:
        ...

    async def send_text(self, room_id: str, text: str) -> None:
        ...

    async def send_sticker(self, room_id: str, path: Path) -> None:
        ...

    async def list_groups(self) -> list[GroupInfo]:
        ...

    async def get_members(self, room_id: str):
        ...
```

收到协议消息时：

```python
self.emit("message", message={
    "room_id": "xxx@chatroom",
    "sender_id": "wxid_xxx",
    "sender_name": "阿哲",
    "content": "你好",
    "kind": "text",
    "timestamp": 1730000000,
    "external_id": "protocol-msg-id",
    "is_self": False,
    "is_group": True,
    "meta": {},
})
```

登录二维码：

```python
self.emit("qr", image_b64="<base64 PNG>", mime="image/png")
self.emit("status", state="qr", message="请扫码")
```

### 4.2 注册

```python
# app/adapters/__init__.py
from app.adapters.my_adapter import MyAdapter

ADAPTERS = {
    MockAdapter.adapter_id: MockAdapter,
    UiAutomationAdapter.adapter_id: UiAutomationAdapter,
    WeChatFerryAdapter.adapter_id: WeChatFerryAdapter,
    MyAdapter.adapter_id: MyAdapter,
}
```

前端会自动从 `/api/status` 的 `available_adapters` 渲染选项；如果希望新适配器使用微信图标或专属文案，再改 `frontend/app.js` 的 `renderAdapters()`。

### 4.3 注意

- `emit` 必须线程安全且快速返回；阻塞工作放线程/后台任务。
- 适配器不要写数据库、不要调用 Agent。
- 消息 `external_id` 尽量稳定，Runtime 用它去重。
- 发送失败抛异常；Runtime/Agent 会记录错误。
- `availability()` 要检查平台和依赖，避免启动后才发现不可用。

---

## 5. 对比与选型建议

- **只想体验/开发前端**：`mock`，零依赖。
- **Windows 本地真实微信群（当前首选）**：`uia`，不需要注入，兼容 32 位 3.9.12.56；代价是速度较慢、会切换前台、拿不到 wxid。
- **已经固定使用微信 3.9.12.51**：可以尝试 `wcf`，但上游维护与版本适配存在不确定性。
- **多平台/商业场景**：优先企业微信、公众号官方 API；个人微信协议方案都不具备稳定性承诺。
- **Wechaty**：接口优雅，但当前免费 puppet 可维护性较差，商业 puppet 需要成本；可以作为新适配器接入。

---

## 6. 参考

- [WeChatFerry](https://github.com/lich0821/WeChatFerry)
- [wxauto](https://github.com/cluic/wxauto)
- [Wechaty](https://github.com/wechaty/wechaty)
- [chatgpt-on-wechat](https://github.com/zhayujie/chatgpt-on-wechat)
- [LangBot](https://github.com/RockChinQ/LangBot)
- [WeChat3.9-32bit-Compatibility-Launcher](https://github.com/Skyler1n/WeChat3.9-32bit-Compatibility-Launcher)

再次提醒：个人微信自动化可能违反平台规则并导致账号受限。请仅用于个人学习与合规场景。
