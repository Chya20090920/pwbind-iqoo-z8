# pwbind — iQOO Z8 两个 USB 端口的 WinUSB 绑定工具

iQOO Z8（PD2314 / MT6895）的两个 USB 端口需要 WinUSB 才能裸 USB 访问：

| 端口 | VID:PID | 工具 |
|---|---|---|
| PR（Preloader / EDL） | `0E8D:2000` | `绑定工具_PR.exe` |
| FA（Fastboot） | `0E8D:201C` | `绑定工具_FA.exe` |

## 用法

Windows 10/11，管理员运行对应 exe。窗口打印一句横幅，抓到设备打印 `成功`，回车退出。

- PR：`请摁住音量键10秒加或手动触发ELD命令`
- FA：`请进入 Fastboot 模式并保持连接`

两个 exe 由 `pwbind.py` 同一份源码按文件名区分端口打包。

## 适配自己的机器

改 `pwbind.py` 顶部的 `VID`、`GUID_PR` / `GUID_FA`、`PROFILES` 里的 `pid` / `name` / `banner`。
固定 GUID 必须与上位机里写死的接口 GUID 一致，否则程序按 GUID 找不到设备。

```
pip install pyinstaller
python -m PyInstaller --onefile --name 绑定工具_PR --add-binary "wdi-simple.exe;." pwbind.py
python -m PyInstaller --onefile --name 绑定工具_FA --add-binary "wdi-simple.exe;." pwbind.py
```

## 工作流程

1. `wdi-simple.exe -x -g <GUID>` 生成只匹配该 VID/PID、带固定接口 GUID 的 INF + 自签 CAT；管理员权限下自签证书写入本机 `Root` 与 `TrustedPublisher`。
2. `pnputil /add-driver <inf>` 入库一次。
3. SetupAPI 按 Hardware ID 真实枚举在位设备；多于一台匹配实例时拒绝操作。
4. 对确认的实例调用 `UpdateDriverForPlugAndPlayDevicesW(..., INSTALLFLAG_FORCE)` 强制换绑。
5. 验收：`service=WinUSB` 且设备注册的接口 GUID 等于预期值。

`wdi-simple.patch` 给上游 libwdi 的 `examples/wdi-simple.c` 增加 `-g/--guid`，使生成的 INF 使用固定 GUID。上游 `wdi_options_prepare_driver.device_guid` 已支持，只是命令行未暴露。

## 签名

- INF 只 `Include=winusb.inf` / `Needs=WINUSB.NT`，使用系统自带微软签名的 `winusb.sys`，不含自有内核驱动，因此不需要 WHQL / attestation 签名，Secure Boot 与 HVCI 不适用。
- 家用 Win10/11 默认设置、管理员运行可过。
- 会被拦的情况：非管理员或 UAC 被拒；企业策略禁止写根证书存储或强制「仅 WHQL 驱动」；App Control for Business / Smart App Control 严格策略。工具打印明确失败原因，不静默循环。
- 开发机若开着 `nointegritychecks` 或已装过 libwdi 证书，本机成功不代表干净机器成功；发布前在未开测试签名的干净机上实测。

## 边界

- 按 Hardware ID 离线入库对同一 VID/PID 的所有设备全局生效，不针对单台物理设备。
- INF 不匹配 `MI_XX`；复合设备需确认绑定的是 function 而非父节点。
- 驱动包缓存在 `%ProgramData%\pwbind\driver-v1\`，证书写入失败后删除该目录重跑。
- `wdi-simple.exe` 来自 libwdi（LGPL），见 `LICENSE`。
