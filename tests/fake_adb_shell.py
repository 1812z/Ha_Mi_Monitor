"""adb_shell 的最小替身，用于在没有第三方库的环境里测试集成逻辑。

API 签名严格对齐真实 adb-shell（已对照官方源码核对）：
- AdbDeviceTcp(host, port=5555, default_transport_timeout_s=None, banner=None)
- .available 是 property（不是方法！）
- .connect(rsa_keys=None, transport_timeout_s=None, auth_timeout_s=..., read_timeout_s=..., auth_callback=None)
- .shell(command, transport_timeout_s=None, read_timeout_s=..., timeout_s=None, decode=True)
- .push(local_path, device_path, st_mode=..., mtime=0, progress_callback=None, transport_timeout_s=None, read_timeout_s=...)
- .close()
"""

from __future__ import annotations


class _Exceptions:
    class AdbCommandFailureException(Exception):
        pass

    class AdbConnectionError(Exception):
        pass

    class AdbTimeoutError(Exception):
        pass

    class DeviceAuthError(Exception):
        def __init__(self, message, *args):
            message %= args
            super().__init__(message, *args)

    class InvalidChecksumError(Exception):
        pass

    class InvalidCommandError(Exception):
        pass

    class InvalidTransportError(Exception):
        pass

    class InvalidResponseError(Exception):
        pass

    class DevicePathInvalidError(Exception):
        pass

    class PushFailedError(Exception):
        pass

    class TcpTimeoutException(Exception):
        pass


class _Auth:
    class keygen:
        @staticmethod
        def keygen(path):  # pragma: no cover - 由测试里的假实现覆盖
            raise NotImplementedError


def install_fake_adb_shell(monkeypatch_targets=None):
    """把假的 adb_shell 模块树塞进 sys.modules，返回录制器。"""
    import sys
    import types
    from pathlib import Path

    recorded = {"shell": [], "push": [], "connect": 0, "close": 0}
    behaviour = {
        "device_output": {},   # 命令子串 -> 输出
        "connect_error": None,
        "shell_error": None,
        "push_error": None,
    }

    exceptions_mod = types.ModuleType("adb_shell.exceptions")
    for name, cls in vars(_Exceptions).items():
        if name.startswith("_"):
            continue
        setattr(exceptions_mod, name, cls)

    class FakeAdbDeviceTcp:
        def __init__(self, host, port=5555, default_transport_timeout_s=None, banner=None):
            self.host = host
            self.port = port
            self.default_transport_timeout_s = default_transport_timeout_s
            self.banner = banner
            self._available = False

        @property
        def available(self):  # 真实库是 property
            return self._available

        def connect(self, rsa_keys=None, transport_timeout_s=None,
                    auth_timeout_s=10.0, read_timeout_s=10.0, auth_callback=None):
            recorded["connect"] += 1
            err = behaviour["connect_error"]
            if err is not None:
                raise err
            self._available = True
            return True, 256 * 1024

        def shell(self, command, transport_timeout_s=None, read_timeout_s=10.0,
                  timeout_s=None, decode=True):
            recorded["shell"].append(
                {"command": command, "timeout_s": timeout_s, "decode": decode}
            )
            err = behaviour["shell_error"]
            if err is not None:
                raise err
            for needle, output in behaviour["device_output"].items():
                if needle in command:
                    return output if decode else output.encode()
            return "" if decode else b""

        def push(self, local_path, device_path, st_mode=0o644, mtime=0,
                 progress_callback=None, transport_timeout_s=None, read_timeout_s=10.0):
            recorded["push"].append(
                {"local": local_path, "remote": device_path,
                 "transport_timeout_s": transport_timeout_s}
            )
            err = behaviour["push_error"]
            if err is not None:
                raise err

        def close(self):
            recorded["close"] += 1
            self._available = False

    device_mod = types.ModuleType("adb_shell.adb_device")
    device_mod.AdbDeviceTcp = FakeAdbDeviceTcp

    auth_mod = types.ModuleType("adb_shell.auth")
    keygen_mod = types.ModuleType("adb_shell.auth.keygen")
    sign_mod = types.ModuleType("adb_shell.auth.sign_pythonrsa")

    def fake_keygen(path):
        Path(path).write_text("FAKE-PRIVATE-KEY", encoding="utf-8")
        Path(path + ".pub").write_text("FAKE-PUBLIC-KEY", encoding="utf-8")

    keygen_mod.keygen = fake_keygen

    class FakePythonRSASigner:
        def __init__(self, public, private):
            self.public = public
            self.private = private

        def Sign(self, data):  # noqa: N802 - 对齐真实库命名
            return b"signature"

        def GetPublicKey(self):  # noqa: N802
            return b"pubkey"

    sign_mod.PythonRSASigner = FakePythonRSASigner

    root = types.ModuleType("adb_shell")
    root.adb_device = device_mod
    root.exceptions = exceptions_mod

    sys.modules["adb_shell"] = root
    sys.modules["adb_shell.adb_device"] = device_mod
    sys.modules["adb_shell.exceptions"] = exceptions_mod
    sys.modules["adb_shell.auth"] = auth_mod
    sys.modules["adb_shell.auth.keygen"] = keygen_mod
    sys.modules["adb_shell.auth.sign_pythonrsa"] = sign_mod

    return recorded, behaviour, exceptions_mod
