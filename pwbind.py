# -*- coding: utf-8 -*-
"""为 MTK PR / FA 端口预置、并在设备出现时绑定 WinUSB。

“预置”只依赖已知的 USB Hardware ID：INF 可在设备第一次连接之前放入
Driver Store。设备出现时，Windows 会用 USB 描述符给出的 VID/PID 匹配它。
不需要该电脑以前安装、见过或记录过这台设备。
"""
import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass


VID = 0x0E8D
GUID_PR = "{F1B2C3D4-1111-4222-8333-944455556666}"
GUID_FA = "{A7B8C9D0-2222-4333-8444-955566667777}"

PROFILES = {
    "PR": {
        "key": "PR",
        "pid": 0x2000,
        "name": "MTK PR Port",
        "guid": GUID_PR,
        "banner": "请摁住音量键10秒加或手动触发ELD命令",
    },
    "FA": {
        "key": "FA",
        "pid": 0x201C,
        "name": "MTK FA Port",
        "guid": GUID_FA,
        "banner": "请进入 Fastboot 模式并保持连接",
    },
}


# SetupAPI constants.  Keeping them here avoids a pywin32 dependency in the EXE.
DIGCF_PRESENT = 0x00000002
DIGCF_ALLCLASSES = 0x00000004
SPDRP_HARDWAREID = 0x00000001
SPDRP_SERVICE = 0x00000004
SPDRP_CONFIGFLAGS = 0x0000000A
DICS_FLAG_GLOBAL = 0x00000001
DIREG_DEV = 0x00000001
KEY_READ = 0x20019
REG_MULTI_SZ = 7
CONFIGFLAG_REINSTALL = 0x00000020
INSTALLFLAG_FORCE = 0x00000001
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_MORE_DATA = 234
ERROR_NO_MORE_ITEMS = 259
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", ctypes.c_byte * 16),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


@dataclass(frozen=True)
class UsbDevice:
    instance_id: str
    hardware_ids: tuple
    service: str
    interface_guids: frozenset


class BindError(RuntimeError):
    pass


def _profile():
    # Running ``python pwbind.py`` formerly selected PR every time because
    # sys.executable is python.exe.  Frozen builds deliberately use their EXE name.
    name = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    return PROFILES["FA"] if "FA" in os.path.basename(name).upper() else PROFILES["PR"]


def _res(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(base, name)
    return bundled if os.path.isfile(bundled) else os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def _configure_apis():
    setup = ctypes.WinDLL("setupapi", use_last_error=True)
    setup.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setup.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setup.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_SP_DEVINFO_DATA)]
    setup.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
    setup.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    setup.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
    setup.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA), wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    setup.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
    setup.SetupDiSetDeviceRegistryPropertyW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA), wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD,
    ]
    setup.SetupDiSetDeviceRegistryPropertyW.restype = wintypes.BOOL
    setup.SetupDiGetDeviceInstanceIdW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA), wintypes.LPWSTR,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    setup.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
    setup.SetupDiOpenDevRegKey.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA), wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    ]
    setup.SetupDiOpenDevRegKey.restype = ctypes.c_void_p

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.RegQueryValueExW.argtypes = [
        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.RegQueryValueExW.restype = wintypes.LONG
    advapi.RegCloseKey.argtypes = [ctypes.c_void_p]
    advapi.RegCloseKey.restype = wintypes.LONG
    return setup, advapi


def _property_bytes(setup, handle, data, prop):
    kind = wintypes.DWORD()
    size = wintypes.DWORD()
    if not setup.SetupDiGetDeviceRegistryPropertyW(
        handle, ctypes.byref(data), prop, ctypes.byref(kind), None, 0, ctypes.byref(size)
    ) and ctypes.get_last_error() not in (ERROR_INSUFFICIENT_BUFFER, ERROR_MORE_DATA):
        return None, None
    if not size.value:
        return kind.value, b""
    raw = (ctypes.c_byte * size.value)()
    if not setup.SetupDiGetDeviceRegistryPropertyW(
        handle, ctypes.byref(data), prop, ctypes.byref(kind), raw, size.value, ctypes.byref(size)
    ):
        return None, None
    return kind.value, bytes(raw[:size.value])


def _string_property(setup, handle, data, prop):
    _, raw = _property_bytes(setup, handle, data, prop)
    return "" if raw is None else raw.decode("utf-16-le", "replace").rstrip("\0")


def _multi_string_property(setup, handle, data, prop):
    kind, raw = _property_bytes(setup, handle, data, prop)
    if raw is None or kind != REG_MULTI_SZ:
        return ()
    return tuple(item for item in raw.decode("utf-16-le", "replace").split("\0") if item)


def _instance_id(setup, handle, data):
    needed = wintypes.DWORD()
    size = 256
    while True:
        buf = ctypes.create_unicode_buffer(size)
        if setup.SetupDiGetDeviceInstanceIdW(handle, ctypes.byref(data), buf, size, ctypes.byref(needed)):
            return buf.value
        if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER or needed.value <= size:
            return ""
        size = needed.value + 1


def _interface_guids(setup, advapi, handle, data):
    key = setup.SetupDiOpenDevRegKey(handle, ctypes.byref(data), DICS_FLAG_GLOBAL, 0, DIREG_DEV, KEY_READ)
    if key == INVALID_HANDLE_VALUE or key is None:
        return frozenset()
    try:
        kind = wintypes.DWORD()
        size = wintypes.DWORD()
        if advapi.RegQueryValueExW(key, "DeviceInterfaceGUIDs", None, ctypes.byref(kind), None, ctypes.byref(size)) != 0:
            return frozenset()
        if kind.value != REG_MULTI_SZ or not size.value:
            return frozenset()
        raw = (ctypes.c_byte * size.value)()
        if advapi.RegQueryValueExW(key, "DeviceInterfaceGUIDs", None, ctypes.byref(kind), raw, ctypes.byref(size)) != 0:
            return frozenset()
        return frozenset(item.upper() for item in bytes(raw[:size.value]).decode("utf-16-le", "replace").split("\0") if item)
    finally:
        advapi.RegCloseKey(key)


def _matches_profile(hardware_ids, prof):
    exact = "USB\\VID_%04X&PID_%04X" % (VID, prof["pid"])
    return any(h.upper() == exact or h.upper().startswith(exact + "&REV_") for h in hardware_ids)


def _enumerate_usb(prof, present_only):
    setup, advapi = _configure_apis()
    flags = DIGCF_ALLCLASSES | (DIGCF_PRESENT if present_only else 0)
    handle = setup.SetupDiGetClassDevsW(None, "USB", None, flags)
    if handle == INVALID_HANDLE_VALUE or handle is None:
        raise BindError("无法枚举 USB 设备：%s" % ctypes.WinError(ctypes.get_last_error()))
    result = []
    try:
        index = 0
        while True:
            data = _SP_DEVINFO_DATA()
            data.cbSize = ctypes.sizeof(data)
            if not setup.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(data)):
                if ctypes.get_last_error() == ERROR_NO_MORE_ITEMS:
                    break
                raise BindError("枚举 USB 设备失败：%s" % ctypes.WinError(ctypes.get_last_error()))
            index += 1
            hardware_ids = _multi_string_property(setup, handle, data, SPDRP_HARDWAREID)
            if not _matches_profile(hardware_ids, prof):
                continue
            instance_id = _instance_id(setup, handle, data)
            if instance_id:
                result.append(UsbDevice(
                    instance_id=instance_id,
                    hardware_ids=hardware_ids,
                    service=_string_property(setup, handle, data, SPDRP_SERVICE),
                    interface_guids=_interface_guids(setup, advapi, handle, data),
                ))
    finally:
        setup.SetupDiDestroyDeviceInfoList(handle)
    return result


def _is_bound(device, prof):
    return device.service.casefold() == "winusb" and prof["guid"].upper() in device.interface_guids


def _set_reinstall(instance_ids, prof):
    """Mark only explicit historical instances, never every VID/PID match."""
    wanted = {item.upper() for item in instance_ids}
    if not wanted:
        return 0
    setup, _ = _configure_apis()
    handle = setup.SetupDiGetClassDevsW(None, "USB", None, DIGCF_ALLCLASSES)
    if handle == INVALID_HANDLE_VALUE or handle is None:
        raise BindError("无法打开 USB 设备列表：%s" % ctypes.WinError(ctypes.get_last_error()))
    changed = 0
    try:
        index = 0
        while True:
            data = _SP_DEVINFO_DATA()
            data.cbSize = ctypes.sizeof(data)
            if not setup.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(data)):
                if ctypes.get_last_error() == ERROR_NO_MORE_ITEMS:
                    break
                raise BindError("枚举 USB 设备失败：%s" % ctypes.WinError(ctypes.get_last_error()))
            index += 1
            instance_id = _instance_id(setup, handle, data)
            if instance_id.upper() not in wanted:
                continue
            if not _matches_profile(_multi_string_property(setup, handle, data, SPDRP_HARDWAREID), prof):
                continue
            _, raw = _property_bytes(setup, handle, data, SPDRP_CONFIGFLAGS)
            old_flags = int.from_bytes(raw[:4], "little") if raw and len(raw) >= 4 else 0
            payload = (ctypes.c_byte * 4).from_buffer_copy((old_flags | CONFIGFLAG_REINSTALL).to_bytes(4, "little"))
            if not setup.SetupDiSetDeviceRegistryPropertyW(handle, ctypes.byref(data), SPDRP_CONFIGFLAGS, payload, 4):
                raise BindError("无法标记 %s 下次重装：%s" % (instance_id, ctypes.WinError(ctypes.get_last_error())))
            changed += 1
    finally:
        setup.SetupDiDestroyDeviceInfoList(handle)
    return changed


def _run(command, timeout):
    try:
        return subprocess.run(command, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise BindError("找不到程序：%s" % command[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise BindError("命令超时（%s 秒）：%s" % (timeout, command[0])) from exc


def _command_text(result):
    raw = (result.stdout or b"") + (result.stderr or b"")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    for encoding in ("utf-8", "mbcs", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def _driver_dir(prof):
    # This survives a PyInstaller temporary extraction directory.  The package
    # is generated once here, so repeated launches do not create new certs/CATs.
    root = os.environ.get("ProgramData") or os.environ.get("ALLUSERSPROFILE") or os.path.dirname(os.path.abspath(sys.executable))
    return os.path.join(root, "pwbind", "driver-v1", "PID_%04X" % prof["pid"])


def _inf_has_expected_identity(inf, prof):
    try:
        with open(inf, "rb") as source:
            raw = source.read()
    except OSError:
        return False
    text = raw.decode("utf-16", "replace") if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else raw.decode("utf-8", "replace")
    # libwdi writes DeviceID = "VID_xxxx&PID_yyyy" (no USB\\ prefix).
    hardware_id = "VID_%04X&PID_%04X" % (VID, prof["pid"])
    return hardware_id in text.upper() and prof["guid"].upper() in text.upper()


def _prepare_and_stage(prof):
    directory = _driver_dir(prof)
    inf = os.path.join(directory, "usb_device.inf")
    if not _inf_has_expected_identity(inf, prof):
        os.makedirs(directory, exist_ok=True)
        wdi = _res("wdi-simple.exe")
        if not os.path.isfile(wdi):
            raise BindError("缺少 wdi-simple.exe：%s" % wdi)
        made = _run([
            wdi, "-n", prof["name"], "-v", "0x%04X" % VID,
            "-p", "0x%04X" % prof["pid"], "-g", prof["guid"],
            "-t", "0", "-x", "-l", "0", "-d", directory,
        ], 120)
        if made.returncode != 0 or not _inf_has_expected_identity(inf, prof):
            raise BindError("生成 WinUSB 驱动包失败（%s）：\n%s" % (made.returncode, _command_text(made).strip()))

    # /add-driver is the pre-stage operation.  /install is intentionally not
    # used here because it is not a force operation and applies to all live
    # devices matching the hardware ID.
    staged = _run(["pnputil", "/add-driver", inf], 180)
    if staged.returncode != 0:
        raise BindError("驱动包未能入库（%s）：\n%s" % (staged.returncode, _command_text(staged).strip()))
    return inf


def _force_install_present(inf, prof):
    """Use libwdi's original FORCE mechanism after verifying one live target."""
    newdev = ctypes.WinDLL("newdev", use_last_error=True)
    update = newdev.UpdateDriverForPlugAndPlayDevicesW
    update.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.BOOL)]
    update.restype = wintypes.BOOL
    reboot = wintypes.BOOL()
    hardware_id = "USB\\VID_%04X&PID_%04X" % (VID, prof["pid"])
    if not update(None, hardware_id, inf, INSTALLFLAG_FORCE, ctypes.byref(reboot)):
        raise BindError("强制绑定失败：%s" % ctypes.WinError(ctypes.get_last_error()))
    return bool(reboot.value)


def _wait_for_bound(prof, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for device in _enumerate_usb(prof, present_only=True):
            if _is_bound(device, prof):
                return device
        time.sleep(0.25)
    return None


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    # Python source runs through python.exe and therefore must retain the
    # script path; a frozen executable must not pass its own path twice.
    args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
    params = " ".join('"%s"' % arg.replace('"', r'\"') for arg in args)
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1) > 32


def _console_utf8():
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _pause():
    try:
        input()
    except EOFError:
        pass


def main():
    _console_utf8()
    argparse.ArgumentParser(add_help=False).parse_known_args()
    prof = _profile()
    print(prof["banner"], flush=True)

    if os.name != "nt":
        print("此工具只能在 Windows 上运行")
        return 2
    if not is_admin():
        if elevate():
            return 0
        print("需要管理员权限")
        return 2

    try:
        inf = _prepare_and_stage(prof)

        while True:
            present = _enumerate_usb(prof, present_only=True)
            if len(present) > 1:
                raise BindError("检测到 %d 台匹配的在位设备，拒绝对多个实例同时强制换绑。" % len(present))
            if present:
                if _is_bound(present[0], prof):
                    print("成功", flush=True)
                    _pause()
                    return 0
                _force_install_present(inf, prof)
                if _wait_for_bound(prof):
                    print("成功", flush=True)
                    _pause()
                    return 0
            time.sleep(0.25)
    except BindError as exc:
        print("失败：%s" % exc, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
