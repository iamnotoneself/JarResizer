# JarResizer 2.0 — J2ME 游戏黑莓 BlackBerry 转换工具（Rust）

把诺基亚 360x640（N97 系列）J2ME 游戏 jar 转换到 BlackBerry Bold 9930
（480x640，OS 7.1）：画面缩放、键盘适配、兼容层注入，支持一键构建 COD
并通过 USB 自动装机。2.0 为 Rust 重写版，GUI 使用 eframe/egui。

## 构建

需要 Windows + Rust（stable，edition 2024）与 MinGW 工具链：

```powershell
$env:Path = "C:\mingw64\bin;$env:USERPROFILE\.cargo\bin;$env:Path"
cargo build --release
```

产物：`target\release\JarResizer.exe`。运行方式：双击 exe，或

```powershell
.\target\release\JarResizer.exe
```

`assets\`、`tools\`、`adapter\`、各 stub 目录与 `patches\` 必须与 exe 位于同一
根目录下（程序按 exe/当前目录自动定位）。纯逻辑单元测试：

```powershell
cargo test
```

## 功能

- **两种转换模式**
  - `AUTO`：注入预验证的 `jr/ScaleCanvas`，把 Canvas/GameCanvas 子类改挂到
    适配层，保留原生设计分辨率渲染后再缩放；支持 `jr-fit`（0 铺满 / 1 适应 /
    2 裁剪）、`jr-crop` 与 `/jr-size`、`/jr-keys` 资源。
  - `FIXED`：同长度改写字节码中的 BIPUSH/SIPUSH 尺寸常量，StackMap 保持有效，
    适合简单固定分辨率游戏。
- **原生分辨率识别**：图片尺寸（PNG/JPEG）、JAD orientation/display-size、
  预设分辨率常量统计、最大图片兜底。
- **键位适配**：BFS 扫描 `keyPressed(I)V` 调用链，自动判定数字键或厂商码游戏；
  黑莓键帽方案 WER/SDF/ZXC = 1–9，或 WASD + 空格厂商码方案；也支持关闭/自定义。
- **兼容层 shim 自动注入**：Nokia UI（FullCanvas/DirectGraphics/…）、JSR-256
  传感器、`java.security` MD5、JSR-120 WMA、JSR-75 文件、JSR-184 M3G；受保护
  包 Utf8 重写、`Connector.open` 重定向、按键钩子与 ASM 字节码补丁。
- **包完整性预检**：MIDlet 入口、启动类缺失、0 字节 class、残留签名文件等，
  引擎/残缺包直接拦截；厂商扩展与黑莓不支持 JSR 预警。
- **rapc 风险预检**：单类 64KB 代码段、单方法 20KB、全方法 try/catch 覆盖率、
  StackMap 体积、解压总量 18/20MB、255 分片上限。
- **大类自动拆分**：超过代码段上限的类调用 `patches/ClassSplit.class` 把方法搬到
  同包 `jrsN` 兄弟类；游戏原包 bug 由 `GuardArrayPatcher` 兜底修复。
- **COD 构建管线**：ProGuard `-microedition` 预验证 → class major 48 → 生成 JAD
  → rapc；检测到 USB 设备时 `javaloader -u` 自动装机。
- **批量扫描 / 批量转换**：目录递归扫描可上黑莓的 jar，逐项显示风险；批量转换
  自动识别分辨率、防覆盖输出、同步 JAD，失败/跳过写入 `转换失败.txt`。

## 目录结构

```
Cargo.toml  Cargo.lock
src\main.rs       入口与模块声明
src\classfile.rs  Java class 常量池/成员/属性解析与回写
src\bytecode.rs   线性字节码遍历（含 tableswitch/lookupswitch/wide）
src\archive.rs    jar(zip) 读写（保留条目顺序，GBK 文件名解码）
src\consts.rs     常量、预设分辨率、重命名规则、键位表
src\patch.rs      AUTO 改写与 FIXED 常量补丁、原始字节补丁
src\shim.rs       兼容层家族定义、扫描、注入与包重定位
src\scan.rs       Canvas 层级、rapc 风险、原生分辨率、键位、完整性、批量扫描
src\convert.rs    转换主流程与 JAD 更新
src\cod.rs        COD 构建/安装管线（ProGuard/rapc/javaloader）
src\batch.rs      批量转换与失败报告
src\gui.rs        egui 界面（转换 / 批量扫描 / 帮助）
assets\           预验证兼容层 class 与 ScaleCanvas（运行时读取，勿删）
adapter\          ScaleCanvas 多指触摸层源码
filestub\ jsecstub\ nokiashim\ sensorstub\ wmastub\   各兼容层源码与构建脚本
patches\          ClassSplit / GuardArrayPatcher 源码与 asm.jar
tools\            自带 JRE、ProGuard、rapc、javaloader、API jar 等（勿删）
```

## 已知限制

- 单个 class 的字节码 ≤ 64KB（rapc 代码段 16 位偏移上限）；超限类会尝试拆分，
  仍失败时 rapc 可能报错。
- COD 解压总量 ≤ 约 20MB（255 分片上限），超出后 rapc 内部错误。
- 全方法 try/catch 的重度混淆游戏可能无法通过 rapc 控制流验证。
- 真机行为（音频、短信、文件、传感器）以 BlackBerry 9930 为准；M3G 兼容层
  资源未随仓库提供，游戏引用时才会报错。
- 写出 jar 时文件名统一按 UTF-8 记录（读取支持 GBK），极老打包器的中文文件名
  在个别工具中可能显示异常。

## 版权声明

本工具仅供个人学习研究。`rapc.jar`、`javaloader.exe`、`net_rim_*.cod` 等
BlackBerry/RIM 组件版权归 Research In Motion / BlackBerry 所有，请勿用于商业用途。
