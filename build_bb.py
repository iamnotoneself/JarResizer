# -*- coding: utf-8 -*-
"""BlackBerry COD one-click build pipeline.

convert (resizer, auto-injects Nokia shim) -> ProGuard -microedition
preverify -> class major 48 -> JAD (MIDP2/CLDC1.1) -> rapc COD.

By default the pipeline auto-installs: if a BlackBerry answers over USB
(device_connected(), javaloader -u dir), the COD is loaded automatically.

Usage:
    python build_bb.py game.jar 640x360 [--keymap "w=2,space=5"]
                        [--codname G1Game] [--workdir DIR]
                        [--load | --no-load]

Importable: build_cod(...) returns (cod_path, installed_bool); load is
"auto" (default), True (force) or False (build only). install_existing()
loads a previously built COD. All steps stream through the log callback.
"""
import argparse
import os
import re
import struct
import subprocess
import sys
import zipfile
import glob

if getattr(sys, "frozen", False):
    # exe sits at the package root; tools\ lives next to it
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
    HERE = ROOT
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    ROOT = HERE
sys.path.insert(0, HERE)
import resizer as R  # noqa: E402
import gui  # noqa: E402  (parse_keymap / presets live there)


def _find_java():
    """java.exe: bundled tools/jre first, then env / common JDK dirs / PATH."""
    cands = [os.path.join(ROOT, "tools", "jre", "bin", "java.exe")]
    for var in ("JAVA17", "JAVA_HOME"):
        v = os.environ.get(var)
        if v:
            cands.append(v if v.lower().endswith("java.exe")
                         else os.path.join(v, "bin", "java.exe"))
    for base in (r"C:\Program Files\Microsoft",
                 r"C:\Program Files\Eclipse Adoptium",
                 r"C:\Program Files\Java"):
        for pat in ("jdk-17*", "jdk-21*"):
            cands.extend(glob.glob(os.path.join(base, pat, "bin", "java.exe")))
    for p in os.environ.get("PATH", "").split(os.path.pathsep):
        if p:
            cands.append(os.path.join(p, "java.exe"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return "java"


JAVA = _find_java()
PROGUARD = os.path.join(
    ROOT, "tools", "proguard", "proguard-7.4.2", "lib", "proguard.jar")
CLDC = os.path.join(ROOT, "tools", "cldcapi11.jar")
MIDP = os.path.join(ROOT, "tools", "midpapi20.jar")
# JSR-135 controls not covered by MIDP2 (MIDIControl/TempoControl/...).
# Extracted from RIM's official compile stub: preverify-time classpath
# only, never merged into the output jar; rapc binds them on device.
MMAPI_EXTRA = os.path.join(ROOT, "tools", "mmapi-extra.jar")
RAPC = os.path.join(ROOT, "tools", "rapc.jar")
RIM_LIBS = [os.path.join(ROOT, "tools", "net_rim_pdap.cod"),
            os.path.join(ROOT, "tools", "net_rim_cldc.cod")]
JAVALOADER = os.path.join(ROOT, "tools", "javaloader.exe")

CLASS_MAJOR = 48  # BlackBerry rapc accepts Java 5 (major 48) class files


class BBError(Exception):
    pass


def _run(cmd, cwd, log):
    log("> " + " ".join('"%s"' % c if " " in c else c for c in cmd))
    p = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = []
    for raw in iter(p.stdout.readline, b""):
        ln = raw.decode("utf-8", "replace").rstrip()
        lines.append(ln)
    p.wait()
    text = "\n".join(lines)
    return p.returncode, text


def _change_major(jar_in, jar_out, major):
    with zipfile.ZipFile(jar_in) as zin, \
            zipfile.ZipFile(jar_out, "w", zipfile.ZIP_DEFLATED) as zout:
        items = zin.infolist()
        # Old rapc keeps a flat simple-name class table: when a class in a
        # named package (e.g. com/fox/fisher/d) is parsed BEFORE the same
        # simple name in the default package (root d.class), rapc aborts
        # with "Duplicate definition for 'd' found in: d". ProGuard emits
        # entries alphabetically, so com/.../d precedes root d. Emit all
        # default-package classes first - the reverse order (packaged
        # second) is proven harmless (a/b/c exist in three packages).
        def _sort_key(item):
            n = item.filename
            if n.endswith(".class") and "/" not in n:
                return (0, n)
            return (1, n)
        for item in sorted(items, key=_sort_key):
            data = zin.read(item.filename)
            if item.filename.endswith(".class") and len(data) >= 8:
                data = data[:4] + b"\x00\x00" + struct.pack(">H", major) \
                    + data[8:]
            zout.writestr(item, data)


def _gen_jad(jar_path, jad_path, log):
    """Build JAD from MANIFEST.MF; upgrade MIDP/CLDC versions for rapc."""
    with zipfile.ZipFile(jar_path) as z:
        mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    main = mf.split("\n\n", 1)[0] if "\n\n" in mf else mf
    folded = []
    for ln in (l.rstrip("\r") for l in main.split("\n")):
        if not ln:
            continue
        if ln[:1] == " " and folded:
            folded[-1] += ln[1:]
        else:
            folded.append(ln)
    drop = ("midlet-jar-url", "midlet-jar-size")
    out = [ln for ln in folded if ln.split(":", 1)[0].strip().lower()
           not in drop]

    def set_prop(key, value):
        for i, ln in enumerate(out):
            if ln.split(":", 1)[0].strip().lower() == key.lower():
                out[i] = "%s: %s" % (key, value)
                return
        out.append("%s: %s" % (key, value))

    # rapc links against MIDP 2 / CLDC 1.1; old Nokia MIDP-1.0 manifests fail
    set_prop("MicroEdition-Profile", "MIDP-2.0")
    set_prop("MicroEdition-Configuration", "CLDC-1.1")
    set_prop("MIDlet-Jar-URL", os.path.basename(jar_path))
    set_prop("MIDlet-Jar-Size", str(os.path.getsize(jar_path)))
    # RIM 触摸机型(Storm/9900/9930)：旧 JDE 编译的 MIDlet 默认运行在
    # 触摸兼容模式——系统只给一个 480x360 的虚拟画布并 1:1 贴在 VGA
    # (640x480)屏幕顶部，下方留黑，导致任何缩放都无法铺满。显式关闭
    # 兼容模式，让 Canvas 报告真实物理分辨率；并禁止用户误开启。
    set_prop("RIM-TouchCompatibilityMode", "false")
    set_prop("RIM-TouchCompatibilityMode-UserChangeable", "false")
    with open(jad_path, "wb") as f:
        f.write(("\r\n".join(out) + "\r\n").encode("utf-8"))
    log("JAD: %s" % jad_path)


def _refresh_stub_assets(jar_path, pkg, log):
    """把 assets/<pkg>/*.class 全量覆盖进已转换的 jar（增量重建用）。

    原始游戏 jar 不在手边时（只改 stub 不想重跑 R.convert），复用
    workdir 里已有的 game_bb.jar，仅把 stub 家族刷新为最新构建产物：
    jar 内同名条目被替换、assets 里新增的类（内部类/新实现类）被追加、
    jar 里已废弃的条目被剔除。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, "assets", pkg.replace("/", os.sep))]
    if getattr(sys, "frozen", False):
        cands.insert(0, os.path.join(sys._MEIPASS, "assets",
                                     pkg.replace("/", os.sep)))
    adir = next((p for p in cands if os.path.isdir(p)), None)
    if adir is None:
        raise BBError("找不到 assets/%s（先运行对应 stub 的 build 脚本）"
                      % pkg)
    new = {}
    for fn in os.listdir(adir):
        if fn.endswith(".class"):
            with open(os.path.join(adir, fn), "rb") as f:
                new[fn] = f.read()
    if not new:
        raise BBError("assets/%s 下没有 .class（先运行 stub 的 build 脚本）"
                      % pkg)
    prefix = pkg.rstrip("/") + "/"
    tmp = jar_path + ".refresh"
    kept = 0
    dropped = 0
    with zipfile.ZipFile(jar_path) as zin, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        seen = set()
        for item in zin.infolist():
            if item.filename.startswith(prefix) \
                    and item.filename.endswith(".class"):
                fn = item.filename[len(prefix):]
                if fn in new:
                    zout.writestr(item, new[fn])
                    seen.add(fn)
                    kept += 1
                else:
                    dropped += 1  # 已废弃的旧 stub 类不再保留
                continue
            zout.writestr(item, zin.read(item.filename))
        added = 0
        for fn in sorted(new):
            if fn not in seen:
                zout.writestr(prefix + fn, new[fn])
                added += 1
    os.replace(tmp, jar_path)
    log("刷新 %s: 替换 %d、新增 %d、剔除 %d 个类"
        % (pkg, kept, added, dropped))


def device_connected(timeout=12):
    """True if a BlackBerry answers javaloader over USB.

    `javaloader -u dir` lists installed modules; on a connected device it
    returns quickly with a module table. Missing device / Desktop Manager
    holding the port produces a non-zero exit or a timeout.
    """
    if not os.path.isfile(JAVALOADER):
        return False
    try:
        p = subprocess.Popen(
            [JAVALOADER, "-u", "dir"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception:
        return False
    try:
        out, _ = p.communicate(timeout=timeout)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
        return False
    if p.returncode != 0:
        return False
    text = out.decode("utf-8", "replace").lower()
    # module table contains real cod names / size header
    return (".cod" in text or "modules" in text or "guid" in text) \
        and "error" not in text and "unable" not in text


def build_cod(jar_in, native_w, native_h, codname=None, keymap=None,
              mode="auto", workdir=None, log=lambda s: None, load="auto",
              fit=None, crop=None, reuse_conv=False):
    """Run the full pipeline. Returns (cod_path, installed_bool).

    load: True -> install and fail on error; False -> never install;
    "auto" (default) -> install only if a device is connected over USB.
    reuse_conv: True -> 跳过 [1/5] R.convert，复用 workdir 里已有的
    game_bb.jar（增量重建：只刷新 m3g stub 后重跑 preverify/rapc）。
    """
    jar_in = os.path.abspath(jar_in)
    if not os.path.isfile(jar_in):
        raise BBError("找不到 jar: %s" % jar_in)
    for tool in (JAVA, PROGUARD, RAPC, CLDC, MIDP) + tuple(RIM_LIBS):
        if not os.path.isfile(tool):
            raise BBError("缺少工具链文件: %s" % tool)
    base = os.path.splitext(os.path.basename(jar_in))[0]
    codname = codname or re.sub(r"[^A-Za-z0-9_]", "", base) or "Game"
    workdir = os.path.abspath(workdir or
                              os.path.join(os.path.dirname(jar_in), "bbbuild"))
    if not os.path.isdir(workdir):
        os.makedirs(workdir)

    # 中间产物一律 ASCII 文件名：源 jar 可能在中文目录/含中文名
    # （如 百战天虫2010_诺基亚...jar），ProGuard 配置按 JVM 默认字符集
    # 读取（中文 Windows 是 GBK），用 ascii 写配置会直接 UnicodeEncodeError，
    # 改成 utf-8 又会让 GBK 端读乱码找不到文件。cwd 仍是原 bbbuild 目录，
    # 最终 COD 用纯英文 codname，不受影响。
    conv_jar = os.path.join(workdir, "game_bb.jar")
    if reuse_conv:
        if not os.path.isfile(conv_jar):
            raise BBError("增量重建需要已存在的 %s（首次构建请用完整管线）"
                          % conv_jar)
        log("==== [1/5] 增量重建：复用 game_bb.jar 并刷新 stub ====")
        _refresh_stub_assets(conv_jar, "jr/m3g", log)
    else:
        log("==== [1/5] 转换 %s (%dx%d) ====" % (base, native_w, native_h))
        R.convert(jar_in, conv_jar, mode, native_w, native_h,
                  keymap=keymap, fit=fit, crop=crop, log=log)
        # rapc COD code sections cap at ~64 KB and cannot split one class:
        # move oversized classes' methods to same-package siblings.
        R.split_fat_classes(conv_jar, log)

    # 2) ProGuard -microedition preverify. Warnings are fatal on purpose:
    # an unresolved Nokia/Motorola member means a NCDFE on the device.
    pv_jar = os.path.join(workdir, "game_pv.jar")
    pro = os.path.join(workdir, "preverify.pro")
    with open(pro, "w", encoding="ascii") as f:
        f.write("-injars %s\n" % os.path.basename(conv_jar))
        f.write("-outjars %s\n" % os.path.basename(pv_jar))
        f.write("-libraryjars '%s'\n" % CLDC)
        f.write("-libraryjars '%s'\n" % MIDP)
        if os.path.isfile(MMAPI_EXTRA):
            f.write("-libraryjars '%s'\n" % MMAPI_EXTRA)
        f.write("-microedition\n-dontshrink\n-dontoptimize\n-dontobfuscate\n")
        f.write("-dontnote\n-keep class * { *; }\n-keepattributes *\n")
    log("==== [2/5] ProGuard 预验证 ====")
    # ProGuard skips work when outjar looks newer ("output seems up to
    # date"); force a clean preverify of the freshly converted jar.
    if os.path.isfile(pv_jar):
        os.remove(pv_jar)
    rc, text = _run([JAVA, "-jar", PROGUARD, "@preverify.pro"], workdir, log)
    if rc != 0 or not os.path.isfile(pv_jar):
        raise BBError("预验证失败（未解析的类/方法引用会导致真机 "
                      "NoClassDefFoundError，请先补齐兼容层）:\n"
                      + "\n".join(text.splitlines()[-15:]))

    # 3) class major -> 48
    u2_jar = os.path.join(workdir, "game_u2.jar")
    _change_major(pv_jar, u2_jar, CLASS_MAJOR)
    log("==== [3/5] class 版本 -> major %d ====" % CLASS_MAJOR)

    # 4) JAD
    jad = os.path.join(workdir, "game_u2.jad")
    _gen_jad(u2_jar, jad, log)

    # 5) rapc
    log("==== [4/5] rapc 编译 COD (%s) ====" % codname)
    # rapc scans EVERY jar/class it sees in its working directory; the
    # shared bbbuild dir keeps intermediates from previous (different)
    # games, and two jars each containing an obfuscated root class like
    # d.class make rapc abort with "Duplicate definition for 'd'".
    # Remove stale rapc outputs and any jar not produced by THIS run.
    keep_jars = {os.path.basename(conv_jar), os.path.basename(pv_jar),
                 os.path.basename(u2_jar)}
    for fn in os.listdir(workdir):
        full = os.path.join(workdir, fn)
        if not os.path.isfile(full):
            continue
        low = fn.lower()
        if low.endswith((".cso", ".debug", ".err")):
            os.remove(full)
        elif (fn == codname + ".jar"
              or (low.endswith(".jar") and fn not in keep_jars)):
            # codname.jar is rapc's own output from an earlier run;
            # other jars are leftovers from other games' builds.
            os.remove(full)
        elif fn.startswith(codname) and fn.endswith(".cod"):
            os.remove(full)
    rc, text = _run([
        JAVA, "-jar", RAPC,
        "import=%s;%s" % (RIM_LIBS[0], RIM_LIBS[1]),
        "codename=%s" % codname,
        "jad=%s" % os.path.basename(jad),
        os.path.basename(u2_jar),
    ], workdir, log)
    cod = os.path.join(workdir, codname + ".cod")
    if rc != 0 or not os.path.isfile(cod):
        if "control flow verification information" in text \
                and "too large" in text:
            # rapc's per-method flow-verification buffer overflows on
            # giant obfuscated engine classes (e.g. Gameloft h/k).
            # ProGuard optimize can shrink the frames past this error,
            # but such games are usually also the oversized multi-COD
            # packages that then die on the 255-sibling wall — both are
            # RIM platform limits, not adapter problems.
            msg = ("rapc 验证失败：游戏的核心类字节码过于庞大/分支过多，"
                   "超过黑莓 COD 转换器的控制流验证信息上限（错误 "
                   "\"control flow verification information too large\"）。"
                   "这是黑莓 OS7.1 平台的硬限制，重排/优化字节码无法可靠"
                   "解决，请换用其它游戏。")
            try:
                import zipfile
                with zipfile.ZipFile(jar_in) as _z:
                    _tot = sum(i.file_size for i in _z.infolist()
                               if not i.is_dir())
                if _tot >= 20 << 20:
                    msg += ("另外本游戏解压后 %.1f MB，即使强行越过本错误，"
                            "还会撞上单个 COD 最多 255 个分片的设备硬上限，"
                            "仍然无法安装。" % (_tot / 1048576.0))
            except Exception:
                pass
            raise BBError(msg)
        if "code section too large" in text:
            # split_fat_classes already ran before preverify; reaching
            # here means even the extracted remainder is too big.
            raise BBError(
                "rapc 失败：某个类的 COD 代码段仍超过 64KB 上限（错误 "
                "\"code section too large\"）。工具已尝试自动拆分大类，"
                "但本游戏单个方法/类的核心代码过大，无法再安全拆出；这是"
                "黑莓 OS7.1 的平台限制，请换用其它游戏版本。")
        if "hQ" in text and "is null" in text:
            # COD module ordinal is one byte with 255 reserved for the
            # root module; rapc NPEs building the 256th sibling section.
            # Device-side cap (JVM 544 VM_SECTION_MAP_OVERFLOW) makes
            # this un-fixable: oversized jar (measured at 22 MB / 257
            # sections with Moto Racer).
            raise BBError(
                "rapc 内部错误（this.hQ is null）：本游戏资源总量超过黑莓"
                "单个 COD 应用的硬上限（约 20 MB / 最多 255 个兄弟分片，"
                "序值 255 被根模块保留，设备 JVM 同样无法加载更多分片）。"
                "nolimit/优化等任何手段都无法绕过，请换更小的游戏版本。")
        raise BBError("rapc 失败:\n" + "\n".join(text.splitlines()[-15:]))
    log("COD 就绪: %s (%d 字节)" % (cod, os.path.getsize(cod)))

    installed = False
    if load == "auto":
        log("==== [5/5] 检测 USB 设备 ====")
        if device_connected():
            log("已连接黑莓设备，自动装机。")
            do_load = True
        else:
            log("未检测到设备（或端口被桌面管理器占用），COD 已生成，"
                "连接手机后点\"装机\"即可。")
            do_load = False
    else:
        do_load = bool(load)

    if do_load:
        log("---- 装机中 ----")
        _run([JAVALOADER, "-u", "erase", codname], workdir, log)
        # erase 失败不阻断：in use=游戏仍在运行（load 可覆盖），
        # invalid handle=首次安装本就没有旧模块
        rc2, text2 = _run([JAVALOADER, "-u", "load", cod], workdir, log)
        if rc2 != 0 or "Done" not in text2:
            raise BBError("加载失败（请确认手机已连接、游戏已关闭）:\n"
                          + text2[-500:])
        installed = True
        log("装机完成，请在手机上重新进入游戏。")
    return cod, installed


def install_existing(jar_in, codname=None, workdir=None,
                     log=lambda s: None):
    """Load an already-built COD from the work directory (no rebuild).

    Requires a connected device. Raises BBError otherwise.
    """
    if not device_connected():
        raise BBError("未检测到黑莓设备，请用 USB 连接手机后重试。")
    base = os.path.splitext(os.path.basename(os.path.abspath(jar_in)))[0]
    codname = codname or re.sub(r"[^A-Za-z0-9_]", "", base) or "Game"
    workdir = os.path.abspath(workdir or
                              os.path.join(os.path.dirname(os.path.abspath(
                                  jar_in)), "bbbuild"))
    cod = os.path.join(workdir, codname + ".cod")
    if not os.path.isfile(cod):
        raise BBError("找不到已构建的 %s，请先执行一键构建。" % cod)
    log("---- 装机: %s ----" % cod)
    _run([JAVALOADER, "-u", "erase", codname], workdir, log)
    rc, text = _run([JAVALOADER, "-u", "load", cod], workdir, log)
    if rc != 0 or "Done" not in text:
        raise BBError("加载失败（请确认游戏已在手机上关闭）:\n" + text[-500:])
    log("装机完成，请在手机上重新进入游戏。")
    return cod


def _parse_size(s):
    m = re.match(r"^(\d+)\s*[xX*]\s*(\d+)$", s.strip())
    if not m:
        raise BBError("分辨率格式应为 640x360，收到: %s" % s)
    return int(m.group(1)), int(m.group(2))


def main(argv=None):
    ap = argparse.ArgumentParser(description="J2ME jar -> BlackBerry COD")
    ap.add_argument("jar")
    ap.add_argument("size", help="原生分辨率，如 640x360；填 auto 自动识别")
    ap.add_argument("--keymap", default=None,
                    help='如 "w=2,s=8,space=5,enter=-6"')
    ap.add_argument("--codname", default=None)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--mode", default="auto", choices=("auto", "fixed"))
    ap.add_argument("--fit", default=None, choices=("fit", "stretch", "cover"),
                    help="铺满策略：fit=等比留黑边(默认)，"
                         "stretch=非等比拉满，cover=等比放大裁剪")
    ap.add_argument("--crop", default=None,
                    help="原生帧有效内容区 x,y,w,h，如 0,0,360,460；"
                         "框外（通常是虚拟按键区）不参与铺满")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--load", action="store_true",
                     help="强制装机（默认：设备连接时自动装）")
    grp.add_argument("--no-load", action="store_true",
                     help="只生成 COD，不检测/不装机")
    ap.add_argument("--reuse-conv", action="store_true",
                    help="复用 workdir 已有 game_bb.jar，只刷新 m3g stub "
                         "后重跑 preverify/rapc（增量重建，size 可填 0x0）")
    args = ap.parse_args(argv)

    if args.reuse_conv:
        w, h = 0, 0
    elif args.size.strip().lower() == "auto":
        detected = R.detect_native_from_assets(args.jar)
        if not detected:
            print("错误: 无法自动识别原生分辨率，请显式指定，如 360x640",
                  file=sys.stderr)
            return 1
        w, h, why = detected
        print("自动识别原生分辨率: %dx%d（%s）" % (w, h, why))
    else:
        w, h = _parse_size(args.size)
    km = gui.parse_keymap(args.keymap) if args.keymap else None
    crop = None
    if args.crop:
        parts = [p.strip() for p in args.crop.split(",")]
        if len(parts) not in (4, 5):
            print("错误: --crop 格式应为 x,y,w,h 或 x,y,w,h,1"
                  "（末位1=逐帧动态内容区），如 0,0,360,460,1",
                  file=sys.stderr)
            return 1
        try:
            cv = [int(p) for p in parts[:4]]
        except ValueError:
            print("错误: --crop 前四个值必须是整数", file=sys.stderr)
            return 1
        crop = "%d,%d,%d,%d" % tuple(cv)
        if len(parts) == 5:
            if parts[4] not in ("0", "1"):
                print("错误: --crop 第五位只能是 0 或 1", file=sys.stderr)
                return 1
            crop += "," + parts[4]
    load = True if args.load else (False if args.no_load else "auto")
    try:
        build_cod(args.jar, w, h, codname=args.codname, keymap=km,
                  mode=args.mode, workdir=args.workdir,
                  log=lambda s: print(s), load=load, fit=args.fit,
                  crop=crop, reuse_conv=args.reuse_conv)
    except BBError as e:
        print("错误: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
