# pwbind — 给 iQOO Z8 的两个 USB 端口绑 WinUSB

说白了：这台手机（iQOO Z8 / PD2314 / MT6895）有两个需要裸 USB 访问的端口，
Windows 默认不会给它们装 WinUSB，所以有了这两个工具。

| 端口 | VID:PID | 用途 | 工具 |
|---|---|---|---|
| PR | `0E8D:2000` | Preloader / EDL 窗口 | `绑定工具_PR.exe` |
| FA | `0E8D:201C` | Fastboot 窗口 | `绑定工具_FA.exe` |

不刷机、不解锁、不碰分区。这里只有「把驱动换成 WinUSB」这一件事。
刷机/维修相关代码**不在本仓库**。

## 用法

Windows 10/11，以管理员身份运行对应的 exe。窗口只打印一句横幅，
抓到设备就打印 `成功`，按回车退出。没抓到就一直等。

- PR：横幅 `请摁住音量键10秒加或手动触发ELD命令`
- FA：横幅 `请进入 Fastboot 模式并保持连接`

代码在 `pwbind.py`，两个 exe 由同一份源码按文件名区分端口打包。

## 换成你自己的机器

改 `pwbind.py` 顶部：

```python
VID = 0x0E8D
GUID_PR = "{...}"   # 你自己的接口 GUID
GUID_FA = "{...}"
PROFILES = {
    "PR": {"pid": 0x2000, "guid": GUID_PR, "name": "...", "banner": "..."},
    "FA": {"pid": 0x201C, "guid": GUID_FA, "name": "...", "banner": "..."},
}
```

**固定 GUID 必须和你自己的上位机/壳里写死的接口 GUID 一致**，否则工具装好了、
你的程序按 GUID 找不到设备。GUID 随便换，两边同步即可。

重新打包：

```
pip install pyinstaller
python -m PyInstaller --onefile --name 绑定工具_PR --add-binary "wdi-simple.exe;." pwbind.py
python -m PyInstaller --onefile --name 绑定工具_FA --add-binary "wdi-simple.exe;." pwbind.py
```

## 它到底做了什么

1. 用 libwdi 的 `wdi-simple.exe -x` 生成一份只匹配该 VID/PID、带固定接口 GUID 的
   INF + 自签 CAT。生成时管理员权限会把自签证书加入本机
   `Root` 和 `TrustedPublisher`，CAT 签名链因此成立。
2. `pnputil /add-driver <inf>` 把包放进 Driver Store（只做一次，不循环签名）。
3. SetupAPI 真实枚举在位设备，按 Hardware ID 精确匹配；多于一台匹配实例时拒绝操作，
   避免误伤同型号其它设备。
4. 对确认的那一台调用 `UpdateDriverForPlugAndPlayDevicesW(..., INSTALLFLAG_FORCE)`
   强制换绑（libwdi 原始的 FORCE 机制）。
5. 验收条件是「当前 function driver 为 WinUSB **且** 设备注册的接口 GUID 等于预期值」，
   不是「pnputil 没报错」。

`wdi-simple.patch` 是给上游 libwdi 的 `examples/wdi-simple.c` 加的 `-g/--guid`
参数（让生成的 INF 用固定 GUID，而不是每次随机）。上游本身支持
`options->device_guid`，只是命令行没暴露。

## 签名这件事，说实话

- 驱动包**不含自有内核 .sys**，只 `Include=winusb.inf` / `Needs=WINUSB.NT`，
  用系统自带微软签名的 `winusb.sys`。因此不需要 WHQL / attestation 签名，
  Secure Boot、HVCI 也管不到它。
- 家用 Win10/11、默认设置、管理员运行：能过。这就是 Zadig / libwdi 的通用做法。
- 以下情况会被拦，工具会打印明确的失败原因（不会静默死循环）：
  非管理员或 UAC 被拒；企业策略禁止写根证书存储或强制「仅 WHQL 驱动」；
  App Control for Business / Smart App Control 严格策略。
- 首次运行会把一张自签证书写进 `Root` + `TrustedPublisher`。介意的话别用，
  改用固件实现 Microsoft OS 描述符（WCID）让 Windows 直接用收件箱 WinUSB。
- 注意：开发机若开着 `nointegritychecks` 或已装过 libwdi 证书，本机成功**不能**
  代表干净机器成功。发布前请在未开测试签名、未装过 Zadig 的干净机上实测。

## 已知边界

- 按 Hardware ID 离线入库是全局生效的，只对**同一 VID/PID** 的所有设备有效，
  不是写到某一台物理设备里。
- INF 只匹配 VID/PID，没有 `MI_XX`。复合设备请确认自己绑的是 function 而不是父节点；
  代码已拒绝「多台在位实例同时强制换绑」。
- 驱动包缓存在 `%ProgramData%\pwbind\driver-v1\`。若首次证书写入被策略拦下、
  之后策略放开，删掉该目录重跑一次即可。
- `wdi-simple.exe` 来自 libwdi（LGPL）。本仓库其余代码随便改、随便用于你自己的
  机器，无附加条件。

## 免责

改驱动绑定有风险。用之前确认你知道自己在绑哪台设备。作者不对变砖、
设备被误绑、企业策略冲突负责。
