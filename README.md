# Mi Monitor (ADB) — Home Assistant 集成

通过无线 ADB 控制小米 / 红米显示器（Mimonitor Toolbox 的 Home Assistant 移植）。

实测机型：**Redmi G Pro 27U 2026**（系统识别型号 `MiTV-MFFU1`，HyperOS 3.0.112.0）。
原理与 [Mimonitor_Toolbox](https://github.com/) 一致：无线 ADB 连上显示器内置的 Android
系统，用 `settings` 命令读写 Android Global Settings，背光等底层参数经
`MtkDirectTool.jar` 直写 MTK 寄存器。

> 小米显示器（如 Redmi G Pro 27U）本身带一个完整的 Android 系统，默认开启无线
> ADB（端口 **5555**）。本集成就是连接这个 Android 系统来调显示器参数，与
> Mimonitor Toolbox 桌面版走的是同一套命令。

**不依赖外部 `adb` 命令**：集成用纯 Python 的
[adb-shell](https://github.com/JeffLIrion/adb_shell) 在进程内实现 ADB 协议，
直接 TCP 连接显示器的 5555 端口。所以 Home Assistant OS / 容器里**不需要**安装
platform-tools，也不会出现 `No such file or directory: 'adb'` 这类问题
（`adb-shell` 由 HA 依据 manifest 的 requirements 自动安装）。

已在真机（`MiTV-MFFU1`）用真实 adb-shell 验证：背光 36→41→36、画面模式
16→9→10→16、输入源 24(HDMI 2)→23(HDMI 1)→24 全部生效，且 jar 自动部署成功。

## 安装

1. 把 `custom_components/mi_monitor` 整个目录复制到 Home Assistant 的
   `config/custom_components/` 下。
2. 重启 Home Assistant（HA 会自动安装 `adb-shell` 依赖）。
3. 设置 → 设备与服务 → 添加集成 → 搜索 **Mi Monitor (ADB)**。
4. 输入显示器的 IP 地址，向导会测试连接并显示设备型号。
5. 想加第二台显示器，重复第 3~4 步即可（一台显示器 = 一个 IP = 一个配置条目）。

### 前置条件

- Home Assistant 2024.2+（`DataUpdateCoordinator`、`ConfigFlow` 均用标准 API）。
- 显示器需已开启无线 ADB（小米显示器默认开启），且与 HA 在同一网络。

### 调试授权

ADB 需要 RSA 密钥认证。集成的密钥策略：

1. **优先复用系统 `~/.android/adbkey`** —— 如果你以前用电脑上的 adb / Mimonitor
   Toolbox 连接并授权过这台显示器，集成会复用那把密钥，**不需要重新授权**。
2. 没有系统密钥时，在 HA 配置目录 `config/mi_monitor/adbkey` 自动生成一对，
   首次连接需在显示器画面上确认「允许调试」弹窗。

### MtkDirectTool.jar

背光 / 色温的**底层**寄存器写入走 `MtkDirectTool.jar`（已随集成附在
`custom_components/mi_monitor/bin/`）。首次使用背光调节时会自动经 adb-shell
的 push 推到显示器，再复制到应用私有目录。找不到 jar 时这些功能降级为仅写
`settings`（参数仍会变化，只是不经 JNI）。

## 功能

### 设备识别与命名

添加时（以及每次启动刷新）会读取设备信息，**配置条目和实体名称用显示器型号，
而不是 IP**（参考 Mimonitor Toolbox 读 `getprop ro.product.model` 识别机型的做法，
并补齐了固件、分辨率等信息）：

- 条目名 / 设备名：`MiTV-MFFU1`（有中文市场名时优先用它）
- 实体名：`MiTV-MFFU1 背光`、`MiTV-MFFU1 输入源` …
- 老版本添加的条目（名称为 `Mi Monitor <ip>`）会在下次启动时自动升级为型号；
  你手动改过名字则保持不动。

HA 设备页的「信息」列会显示：

| 项 | 来源 | 实测值（Redmi G Pro 27U） |
| --- | --- | --- |
| 名称 / 型号 | `ro.product.model` | `MiTV-MFFU1` |
| 制造商 | `ro.product.manufacturer` | Xiaomi |
| 固件版本 | `ro.build.display.id` | `OS3.0.109.0.UFFMAMN` |
| 硬件版本 | `ro.build.version.release` | Android 14 |
| 序列号 | `ro.serialno` | `63895171690000351` |
| 连接地址 | — | `http://<ip>` |

同时提供一组**信息传感器**实体（诊断类）：型号、固件版本、Android 版本、
面板分辨率（`vendor.display-size` → 3840x2160）、面板类型（`ro.boot.panel_type`
→ lcd）、芯片平台（从 `ro.product.unique` 解析 → MTK9655）、序列号、IP 地址、
连接状态，以及可读的「当前画面模式」「当前输入源」。

### 控制实体

| 实体 | 类型 | 说明 |
| --- | --- | --- |
| `number.<型号>_背光` | Number | 1–100（`picture_backlight` + JNI `g_disp__disp_back_light`） |
| `number.<型号>_黑色级别` | Number | 0–100（`picture_brightness`） |
| `number.<型号>_对比度` | Number | 0–100（`picture_contrast`） |
| `number.<型号>_饱和度` | Number | 0–100（`picture_saturation`） |
| `number.<型号>_色调` | Number | 0–100（`picture_hue`） |
| `number.<型号>_锐度` | Number | 0–100（`picture_sharpness`） |
| `select.<型号>_画面模式` | Select | 标准 / 游戏 / 电影（`picture_mode` 14/10/9） |
| `select.<型号>_输入源` | Select | HDMI 1 / HDMI 2 / DP / USB-C |

多台显示器时实体名带各自型号，互不干扰。设备处于其他模式（如 HDR 图片 16）时，
画面模式 select 显示为未选，属正常行为（与 Mimonitor Toolbox 桌面版一致）。

## 底层命令

与 Mimonitor Toolbox 完全相同：

```bash
# 连接
adb connect <ip>:5555

# 背光（settings 双份 + JNI + 刷新广播）
adb shell settings put global picture_backlight 50
adb shell settings put global xiaomi_picture_backlight 50
# 经 TvService 执行 MtkDirectTool.jar：
#   service call TvService 3 s16 "sh -c eval\${IFS}CLASSPATH=...\${IFS}MtkDirectTool\${IFS}set\${IFS}g_disp__disp_back_light\${IFS}50\${IFS}3"
adb shell am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7

# 画面模式
adb shell settings put global picture_mode 10   # 游戏

# 输入切换
adb shell am force-stop com.xiaomi.mitv.tvplayer
adb shell am start -a com.xiaomi.mitv.tvplayer.EXTSRC_PLAY -n com.xiaomi.mitv.tvplayer/.ExternalSourceActivity --ei input 23 -f 0x10000000   # HDMI 1
```

## 目录结构

```text
custom_components/mi_monitor/
├── __init__.py          # 集成入口 + 每台显示器一个 DataUpdateCoordinator
├── config_flow.py       # 配置向导（按 IP 添加，支持多台；名称取型号）
├── adb_client.py        # ADB 连接 / 命令封装 / jar 自动部署 / 设备信息读取
├── number.py            # 背光 / 黑色级别 / 对比度 / 饱和度 / 色调 / 锐度
├── select.py            # 画面模式 / 输入源
├── sensor.py            # 型号 / 固件 / 分辨率 / 序列号 / 连接状态等信息
├── const.py             # 常量
├── strings.json         # 向导文案
├── manifest.json
└── bin/MtkDirectTool.jar
```

## 后续路线（未实现）

- 色温 / 色域 / 精密控光 / 响应时间 / 动态清晰度（JNI 项）
- 游戏模式特性（准星 / FreeSync / 320Hz / 帧率计数器）
- 屏幕灯（ColorfulLedTool.jar）
- HDR / SDR 分区控光记忆
- 操作日志导出
