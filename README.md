# Mi Monitor (ADB) — Home Assistant 集成

通过无线 ADB 控制小米 / 红米显示器（Mimonitor Toolbox 的 Home Assistant 移植）。

实测机型：**Redmi G Pro 27U 2026**（系统 HyperOS 3.0.112.0）。原理与
[Mimonitor_Toolbox](https://github.com/) 一致：无线 ADB 连上显示器内置的 Android 系统，
用 `settings` 命令读写 Android Global Settings，背光等底层参数经 `MtkDirectTool.jar`
直写 MTK 寄存器。

> 小米显示器（如 Redmi G Pro 27U）本身带一个完整的 Android 系统，默认开启无线
> ADB（端口 **5555**）。本集成就是连接这个 Android 系统来调显示器参数，与
> Mimonitor Toolbox 桌面版走的是同一套命令。

本集成已在 **Redmi G Pro 27U 2026（系统识别型号 MiTV-MFFU1，HyperOS 3.0.112.0）**
上真机验证：背光 36→41→36、画面模式 16→9→16、输入源 24(HDMI 2)→23(HDMI 1)→24
全部生效。

## 安装

1. 把 `custom_components/mi_monitor` 整个目录复制到 Home Assistant 的
   `config/custom_components/` 下。
2. 重启 Home Assistant。
3. 设置 → 设备与服务 → 添加集成 → 搜索 **Mi Monitor (ADB)**。
4. 输入显示器的 IP 地址，向导会测试连接并显示设备型号。
5. 想加第二台显示器，重复第 3~4 步即可（一台显示器 = 一个 IP = 一个配置条目）。

### 前置条件

- Home Assistant 2024.2+（`DataUpdateCoordinator`、`ConfigFlow` 均用标准 API）。
- 显示器需已开启无线 ADB（小米显示器默认开启；若关闭，参考 Mimonitor Toolbox
  的说明用 `adbguardian` 或显示器设置重新打开）。
- HA 所在机器需能 `adb connect <显示器IP>:5555`：
  - 安装 [platform-tools](https://developer.android.com/tools/releases/platform-tools)（提供 `adb`），
  - 或在 Home Assistant 里安装 [Android Debug Bridge 插件](https://github.com/AlexxIT/HA_AndroidDebugBridge)，
    把 `adb` 放进 PATH。
  - 集成的 `adb_client.py` 用 `adb` 命令行二进制（从 PATH 找），不依赖任何 Python 包。

### MtkDirectTool.jar（可选）

背光 / 色温的**底层**寄存器写入走 `MtkDirectTool.jar`（已随集成附在
`custom_components/mi_monitor/bin/`）。首次使用背光调节时会自动 push 到显示器。
找不到 jar 时这些功能降级为仅写 `settings`（参数仍会变化，只是不经 JNI）。

## 功能

当前实现**基础显示参数控制 + 输入切换**（高级功能后续再加）：

| 实体 | 类型 | 说明 |
| --- | --- | --- |
| `number.<ip>_背光` | Number | 1–100（`picture_backlight` + JNI `g_disp__disp_back_light`） |
| `number.<ip>_黑色级别` | Number | 0–100（`picture_brightness`） |
| `number.<ip>_对比度` | Number | 0–100（`picture_contrast`） |
| `number.<ip>_饱和度` | Number | 0–100（`picture_saturation`） |
| `number.<ip>_色调` | Number | 0–100（`picture_hue`） |
| `number.<ip>_锐度` | Number | 0–100（`picture_sharpness`） |
| `select.<ip>_画面模式` | Select | 标准 / 游戏 / 电影（`picture_mode` 14/10/9） |
| `select.<ip>_输入源` | Select | HDMI 1 / HDMI 2 / DP / USB-C |

多台显示器时实体名带各自 IP，互不干扰。设备处于其他模式（如 HDR 图片 16）时，
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
├── config_flow.py       # 配置向导（按 IP 添加，支持多台）
├── adb_client.py        # ADB 连接 / 命令封装 / jar 自动部署
├── number.py            # 背光 / 黑色级别 / 对比度 / 饱和度 / 色调 / 锐度
├── select.py            # 画面模式 / 输入源
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
