"""ADB 连接与命令执行层。

与 Mimonitor_Toolbox 的 adb.py 采用相同的命令序列（实测于
Redmi G Pro 27U 2026 / HyperOS 3.0.112.0）：

- 无线 ADB 连接 ``adb connect <ip>:5555``
- 普通设置项走 ``settings put global <key> <value>``
- 背光等底层参数走 MtkDirectTool.jar（经 TvService 的 app_process 调用 MTK JNI）
- 改完画面参数广播 ``PIC_MODE_CHANGED`` 让显示器立刻重读
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil

_LOGGER = logging.getLogger(__name__)

#: 设备端固定无线 ADB 端口（小米显示器内置 Android）
ADB_DEVICE_PORT = 5555
#: 本集成使用的私有 ADB server 端口（独立于系统 5037，避免互相干扰）
ADB_SERVER_PORT = os.environ.get("MI_MONITOR_ADB_SERVER_PORT", "5038")
#: MtkDirectTool.jar 在设备上的两个位置
MTK_JAR_NAME = "MtkDirectTool.jar"
MTK_JAR_SDCARD = f"/sdcard/{MTK_JAR_NAME}"
MTK_JAR_CACHE = f"/data/data/mitv.service/cache/{MTK_JAR_NAME}"

#: 输出文本里出现这些字样即视为连接失效
DISCONNECTED_MARKERS = (
    "offline",
    "unauthorized",
    "no devices",
    "not found",
    "cannot",
    "failed",
    "error:",
)


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
    2. 系统 adb 同级目录 / PATH
    3. 环境变量 MI_MONITOR_MTK_JAR
    4. Mimonitor_Toolbox 的 assets/runtime/MtkDirectTool.jar
    """
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "bin", MTK_JAR_NAME),
        os.environ.get("MI_MONITOR_MTK_JAR", ""),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
                     "Mimonitor_Toolbox", "assets", "runtime", MTK_JAR_NAME),
    ]
    adb_dir = shutil.which("adb")
    if adb_dir:
        candidates.append(os.path.join(os.path.dirname(adb_dir), MTK_JAR_NAME))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class AdbError(Exception):
    """ADB 连接或命令失败。"""


class AdbClient:
    """一台小米显示器的 ADB 客户端（ip 唯一标识一台显示器）。"""

    def __init__(self, ip: str, adb_path: str = "adb") -> None:
        self.ip = str(ip or "").strip()
        self.adb_path = adb_path or "adb"
        self._semaphore = asyncio.Semaphore(1)
        #: jar 已确认可用则本会话内不再重复检查（设备侧 jar 不会凭空消失）
        self._jar_healed = False

    # ------------------------------------------------------------------
    # 基础命令
    # ------------------------------------------------------------------
    @property
    def serial(self) -> str:
        return f"{self.ip}:{ADB_DEVICE_PORT}"

    async def _run(
        self, args: list[str], timeout: float = 10.0
    ) -> tuple[int, str, str]:
        """执行一条 adb 命令，返回 (returncode, stdout, stderr)。串行化避免
        adb server 的并发问题，也避免两条命令同时打向同一台显示器。"""
        cmd = [self.adb_path, "-P", ADB_SERVER_PORT, *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise AdbError(f"adb 命令超时: {' '.join(args)}") from None
        return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    async def _shell(self, command: str, timeout: float = 15.0) -> str:
        code, out, err = await self._run(
            ["-s", self.serial, "shell", command], timeout=timeout
        )
        text = (out or err or "").strip()
        return text

    async def _connect_once(self, timeout: float = 6.0) -> str:
        code, out, err = await self._run(["connect", self.serial], timeout=timeout)
        return (out or err or "").strip()

    # ------------------------------------------------------------------
    # 连接状态
    # ------------------------------------------------------------------
    async def connect(self) -> tuple[bool, str]:
        """建立/恢复连接。返回 (是否就绪, 状态文本)。"""
        state = await self.device_state(timeout=3)
        if state == "device":
            return True, "device"
        try:
            await self._connect_once()
        except AdbError:
            pass
        state = await self.device_state(timeout=4)
        return state == "device", state

    async def disconnect(self) -> None:
        try:
            await self._run(["disconnect", self.serial], timeout=3)
        except AdbError:
            pass

    async def device_state(self, timeout: float = 3.0) -> str:
        if not self.ip:
            return "unknown"
        code, out, err = await self._run(
            ["-s", self.serial, "get-state"], timeout=timeout
        )
        text = (out or err or "").strip().lower()
        if text in ("device", "offline", "unauthorized"):
            return text
        if "unauthorized" in text:
            return "unauthorized"
        if "offline" in text:
            return "offline"
        if "not found" in text:
            return "not found"
        if "no devices" in text:
            return "no devices"
        if "cannot" in text or "failed" in text or "error:" in text:
            return text or "error"
        return text or "unknown"

    # ------------------------------------------------------------------
    # 设备信息
    # ------------------------------------------------------------------
    async def get_model(self) -> str:
        return await self._shell("getprop ro.product.model")

    async def ping(self) -> dict[str, object]:
        """连接向导用：验证 ip 是显示器并取回型号。"""
        ok, state = await self.connect()
        if not ok:
            return {"ok": False, "state": state, "model": None}
        model = (await self.get_model()).strip()
        return {
            "ok": True,
            "state": "device",
            "model": model or "unknown",
        }

    # ------------------------------------------------------------------
    # settings 读写
    # ------------------------------------------------------------------
    async def get(self, key: str) -> str:
        value = await self._shell(f"settings get global {key}")
        return value

    async def put(self, key: str, value: object) -> str:
        return await self._shell(f"settings put global {key} {value}")

    async def refresh_pq(self) -> str:
        """广播画面模式变化，让显示器立刻应用参数。"""
        return await self._shell(
            "am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7"
        )

    # ------------------------------------------------------------------
    # MtkDirectTool.jar 部署 + JNI 读写
    # ------------------------------------------------------------------
    async def check_and_heal_jar(self, local_jar: str | None = None) -> bool:
        """确保 MtkDirectTool.jar 在设备端可用；返回是否成功（自带锁）。"""
        async with self._semaphore:
            return await self._heal_jar_locked(local_jar)

    async def _heal_jar_locked(self, local_jar: str | None = None) -> bool:
        """锁内版 jar 部署：**调用方必须已持有 self._semaphore**。

        供 set_* 系列在事务里调用；asyncio.Semaphore 不可重入，
        所以这里绝不能再包一层锁。
        """
        if self._jar_healed:
            return True
        local_jar = local_jar or locate_mtk_jar()
        try:
            size_text = (await self._shell(
                f"stat -c %s {MTK_JAR_SDCARD} 2>/dev/null || echo 0"
            )).splitlines()[-1].strip()
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
                await self._run(
                    ["-s", self.serial, "push", local_jar, MTK_JAR_SDCARD],
                    timeout=30,
                )
            except AdbError as exc:
                _LOGGER.warning("推送 MtkDirectTool.jar 失败: %s", exc)
                return False
        try:
            await self._shell(
                _tvservice_quote(["cp", MTK_JAR_SDCARD, MTK_JAR_CACHE])
            )
        except AdbError as exc:
            _LOGGER.warning("复制 MtkDirectTool.jar 到 cache 失败: %s", exc)
            return False
        self._jar_healed = True
        return True

    async def mtk_get(self, key: str) -> str:
        """单键读 MTK 寄存器：执行 get 后从 logcat 抓结果。"""
        with self._semaphore:
            await self._shell("logcat -c")
            await self._shell(build_mtk_command("get", key))
            await asyncio.sleep(0.8)
            log = await self._shell(
                f"logcat -d | grep 'GET {key}' | tail -1"
            )
        marker = "= "
        idx = log.find(marker)
        if idx < 0:
            return "N/A"
        return log[idx + len(marker):].strip()

    async def mtk_batch_get(self, keys: list[str]) -> dict[str, str]:
        """批量读 MTK 寄存器：结果写到 /sdcard 再 cat 回来。"""
        safe_keys = [
            k for k in keys
            if k and all(ch.isalnum() or ch == "_" for ch in k)
        ]
        if not safe_keys:
            return {}
        result_file = "/sdcard/Download/Mimonitor_Toolbox/.mtk_batch_result.txt"
        cmd = (
            f"mkdir -p /sdcard/Download/Mimonitor_Toolbox; "
            f"rm -f {result_file}; "
            f"{build_mtk_command('batchGet', *safe_keys)} >/dev/null; "
            f"i=0; while [ $i -lt 30 ] && [ ! -f {result_file} ]; do sleep 0.1; i=$((i+1)); done; "
            f"cat {result_file} 2>/dev/null"
        )
        with self._semaphore:
            out = await self._shell(cmd, timeout=30)
        return parse_mtk_batch_output(out)

    async def mtk_set(self, key: str, value: object, upd: int = 3) -> str:
        return await self._shell(build_mtk_command("set", key, value, upd))

    # ------------------------------------------------------------------
    # 输入切换
    # ------------------------------------------------------------------
    INPUT_SOURCES = {
        "hdmi1": 23,
        "hdmi2": 24,
        "dp": 29,
        "usbc": 30,
    }

    async def set_input_source(self, source: str) -> None:
        """切换到指定输入源。source: hdmi1 / hdmi2 / dp / usbc（或中文显示名）。"""
        value = self.INPUT_SOURCES.get(source)
        if value is None:
            value = {
                "HDMI 1": 23,
                "HDMI 2": 24,
                "DP": 29,
                "USB-C": 30,
            }.get(source)
        if value is None:
            raise ValueError(f"未知输入源: {source}")
        await self._shell("am force-stop com.xiaomi.mitv.tvplayer")
        await self._shell(
            f"am start -a com.xiaomi.mitv.tvplayer.EXTSRC_PLAY "
            f"-n com.xiaomi.mitv.tvplayer/.ExternalSourceActivity "
            f"--ei input {value} -f 0x10000000"
        )
        await self._shell("settings put global mitv.tvplayer.hdmi.last.source " + str(value))

    async def get_input_source(self) -> str | None:
        """读当前输入源 id，映射回字符串；未知返回 None。"""
        raw = (await self.get("mitv.tvplayer.hdmi.last.source")).strip()
        for name, value in self.INPUT_SOURCES.items():
            if str(raw) == str(value):
                return name
        return None

    # ------------------------------------------------------------------
    # 便捷写操作（事务语义：多条命令一次锁内完成）
    # ------------------------------------------------------------------
    async def set_backlight(self, value: int, local_jar: str | None = None) -> None:
        """设置背光（1-100）。JNI 直写 + settings 双份 + 刷新广播。"""
        value = max(1, min(100, int(value)))
        async with self._semaphore:
            jar_ok = await self._heal_jar_locked(local_jar)
            if jar_ok:
                await self._shell(build_mtk_command("set", "g_disp__disp_back_light", value, 3))
            await self.put("picture_backlight", value)
            await self.put("xiaomi_picture_backlight", value)
            await self.refresh_pq()

    async def set_picture_mode(self, mode: str) -> None:
        """设置画面模式。mode: standard / game / movie（或中文：标准/游戏/电影）。"""
        values = {"standard": 14, "game": 10, "movie": 9}
        value = values.get(mode)
        if value is None:
            value = {"标准": 14, "游戏": 10, "电影": 9}.get(mode)
        if value is None:
            raise ValueError(f"未知画面模式: {mode}")
        await self.put("picture_mode", value)
        await self.refresh_pq()

    async def get_picture_mode(self) -> str | None:
        raw = (await self.get("picture_mode")).strip()
        for name, value in {"standard": 14, "game": 10, "movie": 9}.items():
            if str(raw) == str(value):
                return name
        return None

    async def set_plain(self, key: str, value: int) -> None:
        """黑色级别/对比度/饱和度/色调/锐度：纯 settings 项，只 put。"""
        await self.put(key, value)

    async def set_color_temp(self, temp: str) -> None:
        """色温。temp: cool / standard / warm / original / custom。"""
        xiaomi_values = {"cool": 0, "standard": 1, "warm": 2, "original": 8, "custom": 3}
        mtk_map = {0: 1, 1: 2, 2: 3, 3: 0, 4: 4, 5: 5, 8: 6}
        ui_value = xiaomi_values[temp]
        mtk_value = mtk_map[ui_value]
        async with self._semaphore:
            jar_ok = await self._heal_jar_locked()
            if jar_ok:
                await self._shell(build_mtk_command("set", "g_video__clr_temp", mtk_value, 3))
            await self.put("picture_color_temperature", ui_value)
            await self.refresh_pq()
