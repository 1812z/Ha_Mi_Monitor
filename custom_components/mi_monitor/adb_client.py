"""ADB 连接与命令执行层（基于 adb-shell 纯 Python 库）。

与 Mimonitor_Toolbox 的 adb.py 采用**完全相同**的命令序列（实测于
Redmi G Pro 27U 2026 / HyperOS 3.0.112.0）：

- 无线 ADB 直连设备 ``<ip>:5555``（由 adb-shell 在进程内实现 ADB 协议）
- 普通设置项走 ``settings put global <key> <value>``
- 背光等底层参数走 MtkDirectTool.jar（经 TvService 的 app_process 调用 MTK JNI）
- 改完画面参数广播 ``PIC_MODE_CHANGED`` 让显示器立刻重读

**不依赖外部 adb 可执行文件**：adb-shell 直接实现 ADB 协议并通过 TCP 连接
设备的 5555 端口，所以 Home Assistant OS / 容器里没有 platform-tools 也能用
（manifest.json 的 requirements 会让 HA 自动安装 adb-shell）。

adb-shell 是同步阻塞库，因此所有 I/O 都放在 ``asyncio.to_thread`` 里执行，
并用一把 ``asyncio.Lock`` 保证同一台显示器上的命令串行。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Any

from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.exceptions import (
    AdbConnectionError,
    AdbTimeoutError,
    DeviceAuthError,
    PushFailedError,
    TcpTimeoutException,
)

_LOGGER = logging.getLogger(__name__)

#: 设备端固定无线 ADB 端口（小米显示器内置 Android）
ADB_DEVICE_PORT = 5555
#: 认证与传输超时（秒）
ADB_AUTH_TIMEOUT = 10.0
ADB_TRANSPORT_TIMEOUT = 10.0
#: 单条 shell 命令超时（秒）
ADB_SHELL_TIMEOUT = 30.0

#: MtkDirectTool.jar 在设备上的两个位置
MTK_JAR_NAME = "MtkDirectTool.jar"
MTK_JAR_SDCARD = f"/sdcard/{MTK_JAR_NAME}"
MTK_JAR_CACHE = f"/data/data/mitv.service/cache/{MTK_JAR_NAME}"

#: 一次 shell 批量读取的设备信息属性（key 为内部字段名）
#: 复刻 Mimonitor_Toolbox 的 ``getprop ro.product.model`` 做法，并补齐型号/固件/
#: 分辨率等能显示在 HA「设备信息」里的基础信息。
DEVICE_PROPERTIES: dict[str, str] = {
    "model": "ro.product.model",                          # MiTV-MFFU1
    "brand": "ro.product.brand",                          # xiaomi
    "manufacturer": "ro.product.manufacturer",            # xiaomi
    "market_name": "ro.product.marketname",               # 部分机型有中文市场名
    "product": "ro.product.device",                       # beekeeper
    "android_version": "ro.build.version.release",        # 14
    "firmware": "ro.build.display.id",                    # OS3.0.109.0.UFFMAMN
    "fingerprint": "ro.build.fingerprint",
    "serial": "ro.serialno",
    "panel_type": "ro.boot.panel_type",                   # lcd / oled
    "display_size": "vendor.display-size",                # 3840x2160
    "density": "ro.sf.lcd_density",                       # 320
    "device_unique": "ro.product.unique",                 # xiaomi+MTK9655+beekeeper+...
    "characteristics": "ro.build.characteristics",        # tv
    "cpu_abi": "ro.product.cpu.abi",
}

#: 读设备信息时一次性执行的命令（每条 getprop 输出一行）
_PROP_READ_COMMAND = "; ".join(
    f"getprop {prop}" for prop in DEVICE_PROPERTIES.values()
)


def parse_prop_output(output: str) -> dict[str, str]:
    """把 ``_PROP_READ_COMMAND`` 的多行输出按字段名解析成 dict。

    行与 ``DEVICE_PROPERTIES`` 的顺序一一对应；空白值会被丢弃（不同机型
    并非所有属性都存在，例如 marketname 在 Redmi G Pro 27U 上就是空的）。
    """
    fields = list(DEVICE_PROPERTIES)
    lines = [line.strip() for line in (output or "").splitlines()]
    result: dict[str, str] = {}
    for index, field in enumerate(fields):
        if index >= len(lines):
            break
        value = lines[index]
        if value and value.lower() not in ("null", "unknown"):
            result[field] = value
    return result


def derive_display_name(info: dict[str, str]) -> str:
    """从设备信息里推一个人类可读的显示器名称。

    优先中文市场名，其次型号；型号只取 ``MiTV-`` / ``-`` 之后更有辨识度的部分
    （``MiTV-MFFU1`` -> ``MFFU1`` 太短，所以保留完整型号更稳妥）。
    """
    market = (info.get("market_name") or "").strip()
    if market:
        return market
    model = (info.get("model") or "").strip()
    if model:
        return model
    product = (info.get("product") or "").strip()
    return product or ""


def derive_chipset(info: dict[str, str]) -> str:
    """从 ``ro.product.unique`` 里抽出芯片平台（``xiaomi+MTK9655+...``）。"""
    unique = (info.get("device_unique") or "").strip()
    parts = [part for part in unique.split("+") if part]
    for part in parts:
        upper = part.upper()
        if upper.startswith(("MTK", "MT", "SM", "SDM", "RK", "AMLOGIC")):
            return part
    return ""


def _tvservice_quote(parts: list[str]) -> str:
    """把一段命令组装成 TvService 可执行的 ``sh -c eval`` 形式。

    TvService 的 runSystemCommand 直接调 ``Runtime.exec(String)``：按空白切分、
    不经过 shell，所以命令体里的空格必须写成 ``${IFS}`` 字面量，交给 sh 展开
    后再 eval 重新解析 —— 这是 Mimonitor_Toolbox 实测可用且不可简化的写法。
    """
    encoded = "".join(f"\\${{IFS}}{part}" for part in parts)
    return f'service call TvService 3 s16 "sh -c eval{encoded}"'


def build_mtk_command(action: str, *args: object) -> str:
    """构造一条经 TvService 执行 MtkDirectTool 的命令。"""
    parts = [MTK_JAR_CACHE, "MtkDirectTool", action]
    parts += [str(a) for a in args]
    return _tvservice_quote(
        [
            f"CLASSPATH={parts[0]}",
            "/system/bin/app_process",
            "/data/data/mitv.service/cache",
            *parts[1:],
        ]
    )


def _value_of(line: str) -> str | None:
    """从 ``key=value`` 形式的输出行里取 value；ERROR 行视为无效。"""
    line = (line or "").strip()
    if not line or line.startswith("__") or "=" not in line:
        return None
    _key, raw = line.split("=", 1)
    raw = raw.strip()
    if raw.startswith("ERROR"):
        return None
    return raw


def parse_mtk_batch_output(output: str) -> dict[str, str]:
    """解析 MtkDirectTool batchGet 的输出（``key=value`` 每行一条）。"""
    result: dict[str, str] = {}
    for line in (output or "").splitlines():
        value = _value_of(line)
        if value is not None:
            result[line.split("=", 1)[0].strip()] = value
    return result


def locate_mtk_jar() -> str | None:
    """在文件系统里找本地 MtkDirectTool.jar。

    查找顺序（都失败时返回 None，背光/色温等 JNI 功能会降级为仅写 settings）：
    1. 集成自带的 ``custom_components/mi_monitor/bin/MtkDirectTool.jar``
    2. 环境变量 MI_MONITOR_MTK_JAR
    3. 同级 Mimonitor_Toolbox 仓库的 assets/runtime/
    4. 系统 adb 同级目录（如果恰好装了 platform-tools）
    """
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "bin", MTK_JAR_NAME),
        os.environ.get("MI_MONITOR_MTK_JAR", ""),
        os.path.join(base, "..", "..", "..", "Mimonitor_Toolbox", "assets",
                     "runtime", MTK_JAR_NAME),
    ]
    adb_exe = shutil.which("adb")
    if adb_exe:
        candidates.append(os.path.join(os.path.dirname(adb_exe), MTK_JAR_NAME))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


# ----------------------------------------------------------------------
# ADB RSA 密钥管理
# ----------------------------------------------------------------------
def system_adb_key() -> str | None:
    """系统自带 adb 密钥（``~/.android/adbkey``）。

    如果用户以前用电脑上的 adb 连接并授权过这台显示器，复用这把密钥就能
    直接连上、不需要在显示器上重新点一次授权。
    """
    key = os.path.join(os.path.expanduser("~"), ".android", "adbkey")
    if os.path.isfile(key) and os.path.isfile(key + ".pub"):
        return key
    return None


def ensure_adb_key(key_path: str) -> str:
    """确保 RSA 密钥对存在（缺失时生成），返回私钥路径。"""
    if os.path.isfile(key_path) and os.path.isfile(key_path + ".pub"):
        return key_path
    folder = os.path.dirname(key_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    keygen(key_path)
    _LOGGER.info("已生成新的 ADB 密钥: %s", key_path)
    return key_path


def resolve_adb_key(hass: Any) -> str:
    """决定本次使用哪把密钥（阻塞操作，调用方应放 executor）。

    优先复用系统 ``~/.android/adbkey``，否则在 HA 配置目录下生成集成自己的。
    """
    existing = system_adb_key()
    if existing:
        return existing
    return ensure_adb_key(os.path.join(hass.config.path("mi_monitor"), "adbkey"))


class AdbError(Exception):
    """ADB 连接或命令失败。"""


class AdbUnauthorizedError(AdbError):
    """设备未授权（需要在显示器上确认调试授权）。"""


class AdbClient:
    """一台小米显示器的 ADB 客户端（ip 唯一标识一台显示器）。"""

    #: 输入源显示名 -> 设备 input id
    INPUT_SOURCES = {
        "hdmi1": 23,
        "hdmi2": 24,
        "dp": 29,
        "usbc": 30,
    }
    _INPUT_ALIASES = {
        "HDMI 1": 23,
        "HDMI 2": 24,
        "DP": 29,
        "USB-C": 30,
    }

    def __init__(self, ip: str, key_path: str | None = None,
                 port: int = ADB_DEVICE_PORT) -> None:
        self.ip = str(ip or "").strip()
        self.port = port
        self.key_path = key_path
        self._device: AdbDeviceTcp | None = None
        self._lock = asyncio.Lock()
        #: jar 已确认可用则本会话内不再重复检查（设备侧 jar 不会凭空消失）
        self._jar_healed = False

    @property
    def serial(self) -> str:
        return f"{self.ip}:{self.port}"

    # ------------------------------------------------------------------
    # 同步核心（全部在 executor 线程里运行）
    # ------------------------------------------------------------------
    def _load_signer(self) -> PythonRSASigner:
        key_path = self.key_path or system_adb_key()
        if not key_path or not os.path.isfile(key_path):
            raise AdbError("找不到 ADB RSA 密钥，请重新添加集成以生成密钥")
        pub_path = key_path + ".pub"
        if not os.path.isfile(pub_path):
            raise AdbError(f"缺少公钥文件 {pub_path}，请重新添加集成以生成密钥")
        with open(key_path, encoding="utf-8") as handle:
            private = handle.read()
        with open(pub_path, encoding="utf-8") as handle:
            public = handle.read()
        return PythonRSASigner(public, private)

    def _connect_sync(self) -> AdbDeviceTcp:
        """返回可用的设备连接（复用已有连接）。"""
        if self._device is not None and self._device.available:
            return self._device
        signer = self._load_signer()
        device = AdbDeviceTcp(
            self.ip, self.port, default_transport_timeout_s=ADB_TRANSPORT_TIMEOUT
        )
        try:
            device.connect(rsa_keys=[signer], auth_timeout_s=ADB_AUTH_TIMEOUT)
        except (TcpTimeoutException, AdbTimeoutError) as exc:
            self._device = None
            raise AdbUnauthorizedError(
                f"连接 {self.serial} 超时：显示器未授权本次调试，或未开启无线 ADB"
            ) from exc
        except DeviceAuthError as exc:
            self._device = None
            raise AdbUnauthorizedError(f"ADB 认证被拒绝: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - 统一成 AdbError 便于上层识别
            self._device = None
            raise AdbError(f"连接 {self.serial} 失败: {exc}") from exc
        self._device = device
        return device

    def _shell_sync(self, command: str, timeout: float = ADB_SHELL_TIMEOUT) -> str:
        """执行一条设备端 shell 命令并返回输出文本。

        ``AdbDevice.shell`` 默认 ``decode=True``，正常情况下直接返回 str。
        """
        device = self._connect_sync()
        try:
            raw = device.shell(command, timeout_s=timeout)
        except (TcpTimeoutException, AdbTimeoutError) as exc:
            raise AdbError(f"命令超时: {command}") from exc
        except (AdbConnectionError, OSError) as exc:
            # 连接可能被设备断开，丢弃它，下次调用自动重连
            self._device = None
            raise AdbError(f"连接已断开: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            self._device = None
            raise AdbError(f"命令执行失败: {exc}") from exc
        if isinstance(raw, bytes):
            return raw.decode(errors="replace").strip()
        return str(raw or "").strip()

    def _get_sync(self, key: str) -> str:
        return self._shell_sync(f"settings get global {key}")

    def _put_sync(self, key: str, value: object) -> str:
        return self._shell_sync(f"settings put global {key} {value}")

    def _push_sync(self, local: str, remote: str) -> None:
        """把本地文件推到设备。

        注意 adb-shell 的 ``push`` 没有 ``timeout_s`` 参数，用 ``transport_timeout_s``。
        """
        device = self._connect_sync()
        try:
            device.push(local, remote, transport_timeout_s=ADB_SHELL_TIMEOUT)
        except (PushFailedError, TcpTimeoutException, AdbTimeoutError) as exc:
            raise AdbError(f"推送 {local} 到 {remote} 失败: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            self._device = None
            raise AdbError(f"推送 {local} 到 {remote} 失败: {exc}") from exc

    def _refresh_pq_sync(self) -> str:
        return self._shell_sync(
            "am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7"
        )

    # ---- MtkDirectTool.jar 部署 -------------------------------------
    def _heal_jar_sync(self, local_jar: str | None = None) -> bool:
        """确保 MtkDirectTool.jar 在设备端可用（同步、锁内调用）。"""
        if self._jar_healed:
            return True
        local_jar = local_jar or locate_mtk_jar()
        try:
            size_text = self._shell_sync(
                f"stat -c %s {MTK_JAR_SDCARD} 2>/dev/null || echo 0"
            ).splitlines()[-1].strip()
            try:
                remote_size = int(size_text)
            except ValueError:
                remote_size = 0
        except AdbError:
            remote_size = 0

        local_size = 0
        if local_jar:
            try:
                local_size = os.path.getsize(local_jar)
            except OSError:
                local_size = 0

        if remote_size < 1000 or (local_size > 0 and remote_size != local_size):
            if not local_jar:
                _LOGGER.warning("MtkDirectTool.jar 本地未找到，JNI 功能不可用")
                return False
            try:
                self._push_sync(local_jar, MTK_JAR_SDCARD)
            except AdbError as exc:
                _LOGGER.warning("推送 MtkDirectTool.jar 失败: %s", exc)
                return False
        try:
            self._shell_sync(_tvservice_quote(["cp", MTK_JAR_SDCARD, MTK_JAR_CACHE]))
        except AdbError as exc:
            _LOGGER.warning("复制 MtkDirectTool.jar 到 cache 失败: %s", exc)
            return False
        self._jar_healed = True
        return True

    # ---- 复合写操作（一条事务内完成，避免中间态） --------------------
    def _set_backlight_sync(self, value: int, local_jar: str | None = None) -> None:
        value = max(1, min(100, int(value)))
        if self._heal_jar_sync(local_jar):
            self._shell_sync(
                build_mtk_command("set", "g_disp__disp_back_light", value, 3)
            )
        self._put_sync("picture_backlight", value)
        self._put_sync("xiaomi_picture_backlight", value)
        self._refresh_pq_sync()

    def _set_color_temp_sync(self, temp: str) -> None:
        xiaomi_values = {
            "cool": 0, "standard": 1, "warm": 2, "original": 8, "custom": 3,
        }
        mtk_map = {0: 1, 1: 2, 2: 3, 3: 0, 4: 4, 5: 5, 8: 6}
        ui_value = xiaomi_values[temp]
        mtk_value = mtk_map[ui_value]
        if self._heal_jar_sync():
            self._shell_sync(build_mtk_command("set", "g_video__clr_temp", mtk_value, 3))
        self._put_sync("picture_color_temperature", ui_value)
        self._refresh_pq_sync()

    def _set_input_source_sync(self, value: int) -> None:
        """切换到指定输入源（复刻 Mimonitor 的 force-stop + EXTSRC_PLAY）。"""
        self._shell_sync("am force-stop com.xiaomi.mitv.tvplayer")
        self._shell_sync(
            f"am start -a com.xiaomi.mitv.tvplayer.EXTSRC_PLAY "
            f"-n com.xiaomi.mitv.tvplayer/.ExternalSourceActivity "
            f"--ei input {value} -f 0x10000000"
        )
        self._put_sync("mitv.tvplayer.hdmi.last.source", value)

    # ------------------------------------------------------------------
    # 异步公开接口（串行化 + 线程池，绝不阻塞事件循环）
    # ------------------------------------------------------------------
    async def _call(self, func, *args, **kwargs):
        """在 executor 里跑一个同步操作，同一客户端内串行。"""
        async with self._lock:
            return await asyncio.to_thread(func, *args, **kwargs)

    async def connect(self) -> tuple[bool, str]:
        """建立/恢复连接。返回 (是否就绪, 状态文本)。"""
        if not self.ip:
            return False, "error: 未配置 IP"
        try:
            await self._call(self._connect_sync)
        except AdbUnauthorizedError:
            return False, "unauthorized"
        except AdbError as exc:
            return False, f"error: {exc}"
        except Exception as exc:  # noqa: BLE001
            return False, f"error: {exc}"
        return True, "device"

    async def disconnect(self) -> None:
        def _close() -> None:
            device, self._device = self._device, None
            if device is not None:
                try:
                    device.close()
                except Exception:  # noqa: BLE001 - 关闭失败无所谓
                    pass

        try:
            await self._call(_close)
        except Exception:  # noqa: BLE001
            pass

    async def device_state(self) -> str:
        """返回设备状态文本（device / unauthorized / error: ...）。"""
        if not self.ip:
            return "unknown"
        try:
            device = await self._call(self._connect_sync)
        except AdbUnauthorizedError:
            return "unauthorized"
        except AdbError as exc:
            return f"error: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"
        return "device" if device.available else "offline"

    async def get_model(self) -> str:
        return await self._call(self._shell_sync, "getprop ro.product.model")

    async def get_device_info(self) -> dict[str, str]:
        """一次 shell 读完所有设备属性（比逐条 getprop 快得多）。"""
        raw = await self._call(self._shell_sync, _PROP_READ_COMMAND)
        info = parse_prop_output(raw)
        chipset = derive_chipset(info)
        if chipset:
            info["chipset"] = chipset
        display_name = derive_display_name(info)
        if display_name:
            info["display_name"] = display_name
        return info

    async def ping(self) -> dict[str, object]:
        """连接向导用：验证 ip 是显示器并取回型号与基础信息。"""
        ok, state = await self.connect()
        if not ok:
            return {"ok": False, "state": state, "model": None, "info": {}}
        try:
            info = await self.get_device_info()
        except AdbError as exc:
            return {"ok": False, "state": f"error: {exc}", "model": None, "info": {}}
        model = info.get("model") or info.get("display_name") or "unknown"
        return {"ok": True, "state": "device", "model": model, "info": info}

    # ---- settings 读写 ----------------------------------------------
    async def get(self, key: str) -> str:
        return await self._call(self._get_sync, key)

    async def put(self, key: str, value: object) -> str:
        return await self._call(self._put_sync, key, value)

    async def refresh_pq(self) -> str:
        return await self._call(self._refresh_pq_sync)

    # ---- jar 与复合写 ------------------------------------------------
    async def check_and_heal_jar(self, local_jar: str | None = None) -> bool:
        return await self._call(self._heal_jar_sync, local_jar)

    async def set_backlight(self, value: int, local_jar: str | None = None) -> None:
        """设置背光（1-100）：JNI 直写 + settings 双份 + 刷新广播。"""
        await self._call(self._set_backlight_sync, value, local_jar)

    async def set_plain(self, key: str, value: int) -> None:
        """黑色级别/对比度/饱和度/色调/锐度：纯 settings 项，只 put。"""
        await self.put(key, value)

    async def set_color_temp(self, temp: str) -> None:
        """色温。temp: cool / standard / warm / original / custom。"""
        await self._call(self._set_color_temp_sync, temp)

    # ---- 画面模式 ----------------------------------------------------
    _PICTURE_MODES = {"standard": 14, "game": 10, "movie": 9}
    _PICTURE_MODE_ALIASES = {"标准": 14, "游戏": 10, "电影": 9}

    async def set_picture_mode(self, mode: str) -> None:
        """设置画面模式（接受英文 key 或中文显示名）。"""
        value = self._PICTURE_MODES.get(mode)
        if value is None:
            value = self._PICTURE_MODE_ALIASES.get(mode)
        if value is None:
            raise ValueError(f"未知画面模式: {mode}")
        await self.put("picture_mode", value)
        await self.refresh_pq()

    async def get_picture_mode(self) -> str | None:
        raw = (await self.get("picture_mode")).strip()
        for name, value in self._PICTURE_MODES.items():
            if str(raw) == str(value):
                return name
        return None

    # ---- 输入切换 ----------------------------------------------------
    async def set_input_source(self, source: str) -> None:
        """切换到指定输入源（接受 hdmi1/dp/... 或 HDMI 1/DP 等显示名）。"""
        value = self.INPUT_SOURCES.get(source)
        if value is None:
            value = self._INPUT_ALIASES.get(source)
        if value is None:
            raise ValueError(f"未知输入源: {source}")
        await self._call(self._set_input_source_sync, value)

    async def get_input_source(self) -> str | None:
        raw = (await self.get("mitv.tvplayer.hdmi.last.source")).strip()
        for name, value in self.INPUT_SOURCES.items():
            if str(raw) == str(value):
                return name
        return None
