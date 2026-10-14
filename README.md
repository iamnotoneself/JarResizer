# JarResizer — J2ME 游戏黑莓 BlackBerry 转换工具

把诺基亚 360x640（N97 系列）J2ME 游戏 jar 转换到 BlackBerry Bold 9930
（480x640，OS 7.1）：画面缩放、键盘适配、Nokia 兼容层注入、多指触摸虚拟按键，
支持一键构建 COD 并通过 USB 自动装机。

## 快速开始（免安装）

1. 从 [Releases](../../releases) 下载 `JarResizer-portable.zip` 并解压
2. 双击 `JarResizer.exe`
3. 选择游戏 jar → 转换；产物生成在源文件旁

包内自带完整工具链（`tools\jre` 精简运行时、ecj、ProGuard、rapc、javaloader），
无需安装 Python 或 JDK。仅“重构建兼容层源码”时需要本机 JDK17（见下文）。

## 功能

- 360x640 → 480x640 画面适配（fit/cover 模式，触摸坐标自动换算）
- Nokia UI 兼容层自动注入（FullCanvas / DirectUtils / DirectGraphics / TactileFeedback 等）
- 兼容层 stub 注入（重定向到私有 `jr.*` 包）：
  - JSR-75 文件（内存文件系统接管 `file:` 连接，支持通配符列目录）
  - JSR-120 短信（`sms://` 假连接，`send()` 静默成功，不发真实短信）
  - JSR-135 音频（MIDIControl / TempoControl 等预验证 classpath 补全）
  - 传感器假数据（可绕过游戏状态机限制）、MD5（`java.security` 重定向）
- 两套键位方案自动判定（扫描 keyPressed 调用链）：
  - 数字键游戏 → 黑莓键帽方案：QWE/ASD/ZXC = 1-9，空格 = 0/5
  - 厂商码游戏 → WASD 方案：WASD 移动，空格 = 动作/开火
- 多指触摸虚拟按键层（`jr/ScaleCanvas`）：屏幕虚拟按键多键同按，
  `/jr-vkeys` 资源可自定义布局（千分比坐标），半透明渲染、引用计数合成按键
- COD 构建管线：ProGuard `-microedition` 预校验 → class major 48 → JAD → rapc，
  黑莓连接后 `javaloader -u` 自动装机
- rapc 风险预检：单类 64KB 代码段、全方法 try/catch、255 分片 / 约 20MB 解压上限，
  超限提前预警（避免 rapc 内部错误盲查）

## 目录结构

```
JarResizer.exe        主程序（GUI，双击即用）
tools\                自带工具链：jre（运行时）、ecj.jar、proguard、rapc.jar、
                      javaloader.exe、net_rim_*.cod、cldc/midp API jar、microemu
assets\               预验证兼容层 class（Nokia shim、jr.file / jr.sensor / ...、ScaleCanvas）
adapter\              ScaleCanvas 多指触摸层源码
filestub\ 等各 stub\  对应兼容层源码 + 构建脚本
patches\              字节码补丁源码（超大类拆分、数组越界防护）
resizer.py 等         核心逻辑源码（exe 内已打包一份）
```

## 从源码运行 / 构建

- 源码运行：`python gui.py`（Python 3.8+）
- 重打 exe：`powershell -File build_exe.ps1`（需 PyInstaller）
- 重建兼容层（需本机 JDK17，自带 jre 无 jar.exe）：
  `build_adapter.ps1`、`filestub\build_filestub.ps1` 等同名脚本

## 已知限制

- COD 解压总量 ≤ 约 20MB（255 分片硬限制，超出后 rapc 内部错误）
- 单类字节码 ≤ 64KB（rapc 代码段 16 位偏移上限）
- 全方法 try/catch 的重度混淆游戏可能无法通过 rapc 控制流验证
- 模拟器（microemu）验证仅覆盖基础功能，MMAPI 等可选包行为以真机为准

## 版权声明

本工具仅供个人学习研究。`rapc.jar`、`javaloader.exe`、`net_rim_*.cod` 等
BlackBerry/RIM 组件版权归 Research In Motion / BlackBerry 所有，请勿用于商业用途。
