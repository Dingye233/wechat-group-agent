# Clash Verge DSH 插件

## 拆解结论

目标：

```text
D:\Clash Verge\clash-verge.exe
Clash Verge 2.5.8
```

REA 观察结果：

```text
PE x64 桌面程序
resources/clash-verge-service.exe
resources/verge-mihomo.exe
resources/frontend-contract.exe
clash-verge 2.5.8
mihomo core v1.19.32
```

REA 的深度 native provider 当前不可用（Ghidra 未配置、Hopper 不支持 Windows、
IDA/Kuna 未配置），因此只完成了 artifact inventory 和目录/组件识别，没有做
伪代码级重组。

实际控制面来自 Clash Verge 的 mihomo 本地 API：

```text
external-controller-pipe:
  \\.\pipe\verge-mihomo-sidecar-release-...
secret: ...
```

Clash Verge 默认关闭 TCP external controller，但 named pipe 接受普通 HTTP 请求，
所以插件直接通过 named pipe 调用，不需要开启 9097、不需要改用户的代理配置。

## 插件

源码：

```text
dsh/clash-verge/
```

安装到：

```text
data/dsh/studio/clash-verge
```

DSH 工具：

```text
clash_verge_status
clash_verge_proxies
clash_verge_select_proxy
clash_verge_set_mode
clash_verge_delay
clash_verge_start
```

权限：

```text
私聊：全部工具
群聊：只读；写入工具会拒绝
```

## 后台开关

助手 → DeepSeek Harness → 「Clash Verge 管理」。

开关操作的是 DSH 插件：

```text
studio-clash-verge
@qunban/clash-verge
```

开关后调用 DSH 热加载，下一条消息生效。

## 安全边界

- 不注入 DLL
- 不修改 mihomo 配置
- 不自动开启系统代理
- 不默认开放群聊写权限
- 切换节点/模式只允许管理员私聊
