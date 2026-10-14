# -*- coding: utf-8 -*-
"""
J2ME MIDP jar screen-resolution converter core.

Two conversion modes:
  AUTO  - inject jr.ScaleCanvas (precompiled, preverified) and rewrite game
          Canvas classes so they render at native design resolution into an
          offscreen buffer that is scaled to the real display at runtime.
  FIXED - in-place bytecode constant patch (BIPUSH/SIPUSH) of hardcoded
          dimensions, same instruction size so preverify StackMaps survive.
"""
import os
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

# ---------------------------------------------------------------- constants

CANVAS = "javax/microedition/lcdui/Canvas"
GAMECANVAS = "javax/microedition/lcdui/game/GameCanvas"
ADAPTER = "jr/ScaleCanvas"
SIZE_RESOURCE = "jr-size"
KEYS_RESOURCE = "jr-keys"
FIT_RESOURCE = "jr-fit"
CROP_RESOURCE = "jr-crop"

# Nokia 全屏 Canvas：继承自 lcdui.Canvas，视为 Canvas 的等价物
# （转换后用 ScaleCanvas 替换其 super，与 Canvas 子类同样处理）
NOKIA_FULLCANVAS = "com/nokia/mid/ui/FullCanvas"

# 厂商扩展 API 前缀：MicroEmulator 不提供这些类，PC 上无法加载验证
VENDOR_PREFIXES = (
    "com/nokia/", "com/motorola/", "com/samsung/",
    "com/siemens/", "com/sonyericsson/", "com/lge/",
    "com/sprint/", "com/vodafone/",
)
# MIDP 之外的 JSR 扩展包前缀（也属于厂商/可选 API，MicroEmulator 默认不带）
# 用于单文件转换前的 PC 加载预警。
JSR_EXTENSION_PREFIXES = (
    "javax/microedition/sensor/",        # JSR-256 Mobile Sensor
    "javax/microedition/m3g/",           # JSR-184 3D
    "javax/microedition/amms/",          # JSR-234 Advanced Multimedia
    "javax/microedition/pki/",           # JSR-219 Security
    "javax/microedition/location/",      # JSR-179 Location
    "javax/microedition/payment/",       # JSR-229 Payment
    "javax/microedition/provisioning/",  # JSR-241 Provisioning
    "javax/microedition/global/",        # JSR-238 i18n
    "javax/microedition/content/",       # JSR-211 CHAPI
    "javax/microedition/xlet/",           # xlet
    "javax/microedition/media/",         # MMAPI（部分 MIDP 设备才有）
    "javax/microedition/io/pim/",        # JSR-75 PIM
    "javax/microedition/io/file/",       # JSR-75 FileConnection
)

# 批量预扫描（黑莓 9930 兼容性）专用细分：
# 黑莓 9930 (CLDC 1.1 / MIDP 2.0 / BB OS 7) 真正不支持的 JSR → 标红
JSR_BLACKBERRY_UNSUPPORTED = (
    "javax/microedition/sensor/",        # JSR-256 Mobile Sensor
    "javax/microedition/m3g/",           # JSR-184 3D
    "javax/microedition/amms/",          # JSR-234 Advanced Multimedia
    "javax/microedition/pki/",           # JSR-219 Security
    "javax/microedition/payment/",       # JSR-229 Payment
    "javax/microedition/provisioning/",  # JSR-241 Provisioning
    "javax/microedition/global/",        # JSR-238 i18n
    "javax/microedition/content/",       # JSR-211 CHAPI
    "javax/microedition/xlet/",           # xlet
)
# 黑莓 9930 实际支持的 JSR → 不阻断，仅在详情里提示
JSR_BLACKBERRY_SUPPORTED = (
    "javax/microedition/media/",         # MMAPI JSR-135（9930 有完整实现）
    "javax/microedition/location/",      # JSR-179（9930 有 GPS）
    "javax/microedition/io/pim/",        # JSR-75 PIM
    "javax/microedition/io/file/",       # JSR-75 FileConnection
)

# (width, height, label)
PRESETS = [
    (128, 128), (128, 160), (132, 176), (176, 208), (176, 220),
    (208, 208), (208, 320), (240, 240), (240, 320), (320, 240),
    (240, 400), (240, 432), (320, 480), (352, 416), (360, 640),
    (640, 360), (480, 360), (480, 640), (640, 480), (480, 320),
    (320, 320), (800, 480),
]

# declared-method renames: (name, descriptor) -> new name
RENAME_METHODS = {
    ("paint", "(Ljavax/microedition/lcdui/Graphics;)V"): "paintScaled",
    ("pointerPressed", "(II)V"): "scPointerPressed",
    ("pointerReleased", "(II)V"): "scPointerReleased",
    ("pointerDragged", "(II)V"): "scPointerDragged",
    ("keyPressed", "(I)V"): "scKeyPressed",
    ("keyReleased", "(I)V"): "scKeyReleased",
    ("keyRepeated", "(I)V"): "scKeyRepeated",
    ("getWidth", "()I"): "jr$getWidth",
    ("getHeight", "()I"): "jr$getHeight",
}

# CP method-ref renames (descriptor specific for paint; pointer/get* too)
RENAME_REF_RULES = {
    ("paint", "(Ljavax/microedition/lcdui/Graphics;)V"): "paintScaled",
    ("pointerPressed", "(II)V"): "scPointerPressed",
    ("pointerReleased", "(II)V"): "scPointerReleased",
    ("pointerDragged", "(II)V"): "scPointerDragged",
    ("keyPressed", "(I)V"): "scKeyPressed",
    ("keyReleased", "(I)V"): "scKeyReleased",
    ("keyRepeated", "(I)V"): "scKeyRepeated",
    # getWidth/getHeight refs are deliberately left untouched: virtual
    # dispatch must resolve to ScaleCanvas.getWidth/getHeight (native size).
}


class ConvertError(Exception):
    pass


# ------------------------------------------------- class file parse/emit

class ClassFile(object):
    def __init__(self, data):
        d = self.data = bytearray(data)
        if bytes(d[:4]) != b"\xca\xfe\xba\xbe":
            raise ConvertError("bad class magic")
        self.minor = struct.unpack(">H", d[4:6])[0]
        self.major = struct.unpack(">H", d[6:8])[0]
        self._parse_cp()
        p = self._pos
        self.access, self.this_c, self.super_c = struct.unpack(
            ">HHH", d[p:p + 6])
        p += 6
        ic = struct.unpack(">H", d[p:p + 2])[0]
        p += 2
        self.interfaces = [struct.unpack(">H", d[p + j * 2:p + j * 2 + 2])[0]
                           for j in range(ic)]
        p += ic * 2
        self.fields, p = self._parse_members(p)
        self.methods, p = self._parse_members(p)
        self.attrs, p = self._parse_attrs(p)
        self.end = p

    # -- constant pool --------------------------------------------------
    def _parse_cp(self):
        d = self.data
        n = struct.unpack(">H", d[8:10])[0]
        # entries: dict index -> [tag, ...mutable fields...]
        self.cp = [None]
        i = 10
        idx = 1
        while idx < n:
            tag = d[i]
            if tag == 1:
                ln = struct.unpack(">H", d[i + 1:i + 3])[0]
                s = bytes(d[i + 3:i + 3 + ln]).decode("utf-8", "replace")
                self.cp.append([1, s])
                i += 3 + ln
            elif tag in (3, 4):
                self.cp.append([tag, bytes(d[i + 1:i + 5])])
                i += 5
            elif tag in (5, 6):
                self.cp.append([tag, bytes(d[i + 1:i + 9])])
                self.cp.append(None)  # two-slot entry
                i += 9
                idx += 1
            elif tag == 7:
                self.cp.append([7, struct.unpack(">H", d[i + 1:i + 3])[0]])
                i += 3
            elif tag == 8:
                self.cp.append([8, struct.unpack(">H", d[i + 1:i + 3])[0]])
                i += 3
            elif tag in (9, 10, 11):
                a, b = struct.unpack(">HH", d[i + 1:i + 5])
                self.cp.append([tag, a, b])
                i += 5
            elif tag == 12:
                a, b = struct.unpack(">HH", d[i + 1:i + 5])
                self.cp.append([12, a, b])
                i += 5
            elif tag == 15:
                self.cp.append([15, d[i + 1],
                                struct.unpack(">H", d[i + 2:i + 4])[0]])
                i += 4
            elif tag == 16:
                self.cp.append([16, struct.unpack(">H", d[i + 1:i + 3])[0]])
                i += 3
            elif tag in (17, 18):
                a, b = struct.unpack(">HH", d[i + 1:i + 5])
                self.cp.append([tag, a, b])
                i += 5
            elif tag in (19, 20):
                self.cp.append([tag, struct.unpack(">H", d[i + 1:i + 3])[0]])
                i += 3
            else:
                raise ConvertError("unsupported cp tag %d" % tag)
            idx += 1
        self._pos = i

    def _parse_members(self, p):
        d = self.data
        out = []
        mc = struct.unpack(">H", d[p:p + 2])[0]
        p += 2
        for _ in range(mc):
            access, ni, di = struct.unpack(">HHH", d[p:p + 6])
            p += 6
            attrs, p = self._parse_attrs(p)
            out.append({"access": access, "name": ni, "desc": di,
                        "attrs": attrs})
        return out, p

    def _parse_attrs(self, p):
        d = self.data
        out = []
        ac = struct.unpack(">H", d[p:p + 2])[0]
        p += 2
        for _ in range(ac):
            ni = struct.unpack(">H", d[p:p + 2])[0]
            ln = struct.unpack(">I", d[p + 2:p + 6])[0]
            blob = bytes(d[p + 6:p + 6 + ln])
            out.append({"name": ni, "data": blob})
            p += 6 + ln
        return out, p

    # -- cp helpers -----------------------------------------------------
    def utf8(self, idx):
        e = self.cp[idx]
        return e[1] if e and e[0] == 1 else None

    def class_name(self, idx):
        e = self.cp[idx]
        if not e or e[0] != 7:
            return None
        return self.utf8(e[1])

    def add_utf8(self, s):
        for i, e in enumerate(self.cp):
            if e and e[0] == 1 and e[1] == s:
                return i
        self.cp.append([1, s])
        return len(self.cp) - 1

    def add_class(self, name):
        for i, e in enumerate(self.cp):
            if e and e[0] == 7 and self.utf8(e[1]) == name:
                return i
        ni = self.add_utf8(name)
        self.cp.append([7, ni])
        return len(self.cp) - 1

    def add_nat(self, name, desc):
        ni = self.add_utf8(name)
        di = self.add_utf8(desc)
        for i, e in enumerate(self.cp):
            if e and e[0] == 12 and e[1] == ni and e[2] == di:
                return i
        self.cp.append([12, ni, di])
        return len(self.cp) - 1

    def this_name(self):
        return self.class_name(self.this_c)

    def super_name(self):
        return self.class_name(self.super_c)

    # -- emit -----------------------------------------------------------
    def build(self):
        out = bytearray(b"\xca\xfe\xba\xbe")
        out += struct.pack(">HH", self.minor, self.major)
        out += struct.pack(">H", len(self.cp))
        for e in self.cp[1:]:
            if e is None:
                continue
            tag = e[0]
            out.append(tag)
            if tag == 1:
                b = e[1].encode("utf-8")
                out += struct.pack(">H", len(b)) + b
            elif tag in (3, 4):
                out += e[1]
            elif tag in (5, 6):
                out += e[1]
            elif tag in (7, 8, 16, 19, 20):
                out += struct.pack(">H", e[1])
            elif tag == 15:
                out.append(e[1])
                out += struct.pack(">H", e[2])
            elif tag in (9, 10, 11, 12, 17, 18):
                out += struct.pack(">HH", e[1], e[2])
            else:
                raise ConvertError("emit tag %d" % tag)
        out += struct.pack(">HHH", self.access, self.this_c, self.super_c)
        out += struct.pack(">H", len(self.interfaces))
        for x in self.interfaces:
            out += struct.pack(">H", x)
        out += self._emit_members(self.fields)
        out += self._emit_members(self.methods)
        out += self._emit_attrs(self.attrs)
        return bytes(out)

    def _emit_members(self, members):
        out = struct.pack(">H", len(members))
        for m in members:
            out += struct.pack(">HHH", m["access"], m["name"], m["desc"])
            out += self._emit_attrs(m["attrs"])
        return out

    def _emit_attrs(self, attrs):
        out = struct.pack(">H", len(attrs))
        for a in attrs:
            out += struct.pack(">H", a["name"])
            out += struct.pack(">I", len(a["data"]))
            out += a["data"]
        return out

    def code_blobs(self):
        """Yield (method, attr, code bytearray, raw blob) for Code attrs."""
        for m in self.methods:
            for a in m["attrs"]:
                if self.utf8(a["name"]) == "Code":
                    blob = a["data"]
                    code_len = struct.unpack(">I", blob[4:8])[0]
                    code = bytearray(blob[8:8 + code_len])
                    yield m, a, code, blob


# ----------------------------------------------- bytecode linear scanner

def _fill_op_table():
    t = [0] * 256
    for op in range(0x00, 0x0A):  # nop..dconst_1 etc
        t[op] = 0
    t[0x10] = 1   # bipush
    t[0x11] = 2   # sipush
    t[0x12] = 1   # ldc
    t[0x13] = 2   # ldc_w
    t[0x14] = 2   # ldc2_w
    for op in range(0x15, 0x1A):  # iload..aload
        t[op] = 1
    for op in range(0x36, 0x3B):  # istore..astore
        t[op] = 1
    t[0x84] = 2   # iinc
    for op in range(0x99, 0xA9):  # ifeq..if_acmpne, goto, jsr
        t[op] = 2
    t[0xA9] = 1   # ret
    for op in (0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8,
               0xBB, 0xBD, 0xC0, 0xC1):
        t[op] = 2
    t[0xB9] = 4   # invokeinterface
    t[0xBA] = 4   # invokedynamic
    t[0xBC] = 1   # newarray
    t[0xC5] = 3   # multianewarray
    t[0xC6] = 2   # ifnull
    t[0xC7] = 2   # ifnonnull  (NOT goto_w!)
    t[0xC8] = 4   # goto_w
    t[0xC9] = 4   # jsr_w
    return t


OP_EXTRA = _fill_op_table()


def walk_code(code):
    """Yield (index, opcode, operand_bytes) for every instruction."""
    i, n = 0, len(code)
    while i < n:
        op = code[i]
        if op == 0xAA:  # tableswitch
            pad = (4 - (i + 1) % 4) % 4
            base = i + 1 + pad
            low = struct.unpack(">i", code[base + 4:base + 8])[0]
            high = struct.unpack(">i", code[base + 8:base + 12])[0]
            end = base + 12 + (high - low + 1) * 4
            yield i, op, bytes(code[i + 1:end])
            i = end
            continue
        if op == 0xAB:  # lookupswitch
            pad = (4 - (i + 1) % 4) % 4
            base = i + 1 + pad
            npairs = struct.unpack(">i", code[base + 4:base + 8])[0]
            end = base + 8 + npairs * 8
            yield i, op, bytes(code[i + 1:end])
            i = end
            continue
        if op == 0xC4:  # wide
            sub = code[i + 1]
            size = 4 if sub == 0x84 else 3
            yield i, op, bytes(code[i + 1:i + size + 1])
            i += size + 1
            continue
        extra = OP_EXTRA[op]
        yield i, op, bytes(code[i + 1:i + 1 + extra])
        i += 1 + extra


# ------------------------------------------------------------- analysis

def _read_class_names(jar):
    """name -> super name for every class in jar (tolerant light parse)."""
    graph = {}
    with zipfile.ZipFile(jar) as z:
        for e in z.infolist():
            if not e.filename.endswith(".class"):
                continue
            try:
                cf = ClassFile(z.read(e.filename))
                graph[cf.this_name()] = cf.super_name()
            except Exception:
                continue
    return graph


def scan_rapc_risks(jar, log=lambda s: None):
    """Pre-flight for the RIM COD converter ("control flow verification
    information too large: <m>"). The named method is only where rapc
    overflowed; two distinct obfuscation signatures predict the failure,
    both measurable in the ORIGINAL jar:

    A. Giant class/method: whole engine in one obfuscated class.
       g3 18 KB frames/38 KB code, g4 11 KB/43 KB -> builds;
       g5 d.class 66 KB frames/93 KB code (method aR 27 KB, 829 branch
       targets) -> rapc reject.
    B. Whole-method try/catch obfuscation: an exception range covers
       >=90% of a method (typical CN obfuscator pattern: one
       try{ everything }catch{ dispatch-back }). rapc must store local
       type state for every throwing instruction in the range, so the
       flow-info buffer explodes even for medium methods:
       g6 u.f 6.8 KB covered 99% (68 switch cases in try) -> reject;
       g8 o.a(J) 6.4 KB covered 99.9% (62 cases) -> reject;
       g8 o.a(Lr;) 5.9 KB covered 99% -> reject.
       Passing games only have small/local try ranges (g7 116/5331
       bytes covered; g4 <=358/1370). g1-g4/g7: NO method with ratio
       above 0.9 at all.
    RECALIBRATION (Contra 魂斗罗1代, 2026-09): c.class 68 KB code,
       27 KB method, 100%-try 15.8 KB method, frames only 28 KB ->
       BUILT fine (its real wall was "code section too large 77732",
       now auto-fixed by split_fat_classes). So code/method/try sizes
       are WEAK hints; only StackMap frames >=50 KB stay a strong
       reject predictor.
    C. COD 255-sibling hard limit: module ordinals in the COD wire
       format are one byte with 255 reserved for the root module
       (device JVM error 544 VM_SECTION_MAP_OVERFLOW). rapc 5.0
       builds the sibling list then NPEs on index 255
       ("this.hQ is null" in d.af.bl). Measured: a 22.2 MB Gameloft
       jar (Moto Racer) needs 257 sections (~86 KB/section) and dies
       on the 256th. nolimit/slicesize/resourcesize do not help;
       the device could not address the slices anyway.
    Warn (non-fatal) so the user learns the cause before a multi-minute
    COD build; ProGuard optimize/stackmap tricks cannot fix either case.
    """
    STACKMAP_LIMIT, CLASS_CODE_LIMIT, METHOD_CODE_LIMIT = 50000, 60000, 20000
    TRY_RATIO, TRY_CODE = 0.9, 3000
    # Empirical: 22.19 MB unpacked -> 257 sections -> rapc NPE.
    COD_SIZE_WARN, COD_SIZE_HARD = 18 << 20, 20 << 20
    try:
        with zipfile.ZipFile(jar) as z:
            total = sum(i.file_size for i in z.infolist()
                        if not i.is_dir())
            if total >= COD_SIZE_WARN:
                mb = total / 1048576.0
                if total >= COD_SIZE_HARD:
                    log("警告: 本游戏解压后总大小 %.1f MB，超过黑莓单个 COD "
                        "应用的容量上限（约 20 MB / 255 个兄弟分片，序值 255 "
                        "被根模块保留，rapc 会在第 256 片处内部错误中止；"
                        "这是黑莓 OS 平台硬限制，任何选项/优化都无法绕过，"
                        "请换更小的游戏版本）。" % mb)
                else:
                    log("提示: 本游戏解压后 %.1f MB，接近黑莓 COD 255 分片"
                        "上限（约 20 MB），rapc 可能在生成 COD 时内部错误"
                        "中止，届时只能换更小的游戏版本。" % mb)
            for n in z.namelist():
                if not n.endswith(".class"):
                    continue
                try:
                    cf = ClassFile(z.read(n))
                except Exception:
                    continue
                map_bytes, code_bytes = 0, 0
                max_method = 0
                whole_try = []  # (code_len, ratio, method desc)
                for m, _a, code, blob in cf.code_blobs():
                    cl = len(code)
                    code_bytes += cl
                    max_method = max(max_method, cl)
                    p = 8 + cl
                    exn = struct.unpack_from(">H", blob, p)[0]
                    p += 2
                    ranges = [struct.unpack_from(">HHHH", blob,
                                                 p + 8 * i)[:2]
                              for i in range(exn)]
                    p += 8 * exn
                    if ranges and cl >= TRY_CODE:
                        covered = bytearray(cl)
                        for s, e in ranges:
                            for x in range(max(0, s), min(cl, e)):
                                covered[x] = 1
                        ratio = sum(covered) / cl
                        if ratio >= TRY_RATIO:
                            whole_try.append((cl, ratio,
                                              cf.utf8(m["name"])))
                    ac = struct.unpack_from(">H", blob, p)[0]
                    p += 2
                    for _i in range(ac):
                        idx = struct.unpack(">H", blob[p:p + 2])[0]
                        al = struct.unpack_from(">I", blob, p + 2)[0]
                        if cf.utf8(idx) in ("StackMap", "StackMapTable"):
                            map_bytes += al
                        p += 6 + al
                weak = []
                if code_bytes >= CLASS_CODE_LIMIT:
                    weak.append("字节码 %d 字节" % code_bytes)
                if max_method >= METHOD_CODE_LIMIT:
                    weak.append("含 %d 字节的超大方法（分支/栈帧较多）"
                                % max_method)
                if whole_try:
                    worst = max(whole_try)
                    weak.append(
                        "%d 个方法被全方法 try/catch 包裹（最大 %d 字节，"
                        "覆盖率 %.0f%%）"
                        % (len(whole_try), worst[0], worst[1] * 100))
                if map_bytes >= STACKMAP_LIMIT:
                    # Strong correlation with every measured reject
                    # (g5 66KB, Moto 62KB); no game with >=50KB frames
                    # has ever built.
                    log("警告: %s 验证帧 %d 字节，rapc 极可能报 "
                        "\"control flow verification information too large\" "
                        "拒收 COD（此为强信号，优化无法可靠解决，建议换游戏）。"
                        % (n[:-6], map_bytes))
                elif weak:
                    tail = ""
                    if code_bytes >= SPLIT_CLASS_CODE_LIMIT:
                        tail = ("；若仅为代码段超限，工具会自动把大类方法拆到"
                                "同类的辅助类后继续构建")
                    log("提示: %s %s。旧版 rapc 对这类混淆较敏感（少数游戏"
                        "会报验证信息过大），实际仍可成功构建，将继续尝试%s。"
                        % (n[:-6], "，".join(weak), tail))
    except Exception:
        return


def scan_vendor_extensions(jar, log=lambda s: None):
    """扫描 jar 内所有 class 引用的厂商扩展 / JSR 扩展 API。

    返回 {前缀标签: set(被引用的类名)}。同时通过 log 给出提示：
    MicroEmulator 不带这些类，PC 上加载会 NoClassDefFoundError，
    应在对应厂商真机上验证。

    扫描范围：常量池里 tag=7 (Class) 与 tag=9/10/11 (Field/Method/
    InterfaceMethod ref 的 owner class) 的类名。不阻断转换。
    """
    found = {"vendor": set(), "jsr": set()}
    with zipfile.ZipFile(jar) as z:
        for e in z.infolist():
            if not e.filename.endswith(".class"):
                continue
            try:
                cf = ClassFile(z.read(e.filename))
            except Exception:
                continue
            for ent in cf.cp:
                if not ent:
                    continue
                tag = ent[0]
                if tag == 7:
                    cn = cf.utf8(ent[1])
                elif tag in (9, 10, 11):
                    cn = cf.class_name(ent[1])
                else:
                    continue
                if not cn or cn[0] == "[" or cn[0] in "()VTIJZBCDFS":
                    continue
                if cn.startswith("java/") or cn == NOKIA_FULLCANVAS:
                    continue
                # 先匹配精确的 JSR 扩展前缀（含 io/pim、io/file）
                matched_jsr = False
                for jp in JSR_EXTENSION_PREFIXES:
                    if cn.startswith(jp):
                        found["jsr"].add(cn)
                        matched_jsr = True
                        break
                if matched_jsr:
                    continue
                # 厂商扩展前缀
                matched_vendor = False
                for vp in VENDOR_PREFIXES:
                    if cn.startswith(vp):
                        found["vendor"].add(cn)
                        matched_vendor = True
                        break
                if matched_vendor:
                    continue
                # 标准 MIDP/CLDC/WMA，跳过
                if cn.startswith("javax/microedition/lcdui/") or \
                        cn.startswith("javax/microedition/midlet/") or \
                        cn.startswith("javax/microedition/rms/") or \
                        cn.startswith("javax/microedition/io/") or \
                        cn.startswith("javax/wireless/"):
                    continue
    if found["vendor"] or found["jsr"]:
        log("==== 厂商扩展 API 预警 ====")
        if found["vendor"]:
            log("发现厂商扩展 API 引用:")
            for cn in sorted(found["vendor"])[:20]:
                log("  - %s" % cn)
            if len(found["vendor"]) > 20:
                log("  ... 共 %d 个" % len(found["vendor"]))
        if found["jsr"]:
            log("发现 JSR 扩展 API 引用:")
            for cn in sorted(found["jsr"])[:20]:
                log("  - %s" % cn)
            if len(found["jsr"]) > 20:
                log("  ... 共 %d 个" % len(found["jsr"]))
        log("这些类 MicroEmulator 不提供，PC 上加载会 NoClassDefFoundError。")
        log("转换本身不受影响，但请在对应厂商真机上验证运行效果。")
        log("============================")
    return found


def canvas_hierarchy(graph):
    """Return (transformed, gamecanvas_classes).

    transformed: classes whose super chain reaches lcdui Canvas or
    GameCanvas (both are reparented onto jr.ScaleCanvas, which itself
    extends GameCanvas).
    gamecanvas_classes: subset whose chain reaches GameCanvas, kept for
    logging / constructor-signature handling.
    """
    transformed, gamecanvas, bad = set(), set(), set()

    def chain(name, seen):
        if name in seen:
            return None
        seen = seen | {name}
        if name == CANVAS or name == NOKIA_FULLCANVAS:
            # Nokia FullCanvas 继承自 Canvas，对游戏来说是全屏 Canvas
            # 的等价物，AUTO 模式按 Canvas 子类同样处理（注入 ScaleCanvas
            # 作为新父类）。注意：FullCanvas 是 Nokia UI 包里的类，本 jar
            # 通常不含它（super 在 graph 中查不到）——直接在这里短路即可。
            return "canvas"
        if name == GAMECANVAS:
            return "gamecanvas"
        sup = graph.get(name)
        if sup is None:
            return None
        return chain(sup, seen)

    for name in graph:
        r = chain(name, set())
        if r == "canvas":
            transformed.add(name)
        elif r == "gamecanvas":
            transformed.add(name)
            gamecanvas.add(name)
    return transformed, gamecanvas


def scan_constants(jar_path):
    """Tally positive BIPUSH/SIPUSH int constants over all class code."""
    tally = {}
    with zipfile.ZipFile(jar_path) as z:
        for e in z.infolist():
            if not e.filename.endswith(".class"):
                continue
            try:
                cf = ClassFile(z.read(e.filename))
            except Exception:
                continue
            for m, a, code, blob in cf.code_blobs():
                try:
                    for idx, op, operand in walk_code(code):
                        if op == 0x10:
                            v = struct.unpack(">b", operand)[0]
                        elif op == 0x11:
                            v = struct.unpack(">h", operand)[0]
                        else:
                            continue
                        if v > 0:
                            tally[v] = tally.get(v, 0) + 1
                except Exception:
                    continue
    return tally


def detect_native_size(tally):
    """Guess native design resolution from constant frequency."""
    best, bestscore = None, 0
    for w, h in PRESETS:
        cw, ch = tally.get(w, 0), tally.get(h, 0)
        if cw and ch:
            score = min(cw, ch) * 10000 + cw + ch
            if score > bestscore:
                bestscore, best = score, (w, h)
    return best


def _image_wh(data):
    """Return (w, h) for PNG/JPEG bytes, else None."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                ln = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + ln
    except Exception:
        return None
    return None


def manifest_orientation(jar):
    """'landscape'/'portrait' from JAD-style manifest hints, else None."""
    try:
        with zipfile.ZipFile(jar) as z:
            try:
                mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
            except KeyError:
                return None
        for line in mf.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            if k.strip().lower() in ("nokia-midlet-app-orientation",
                                     "midlet-orientation",
                                     "lg-midlet-orientation",
                                     "msa-midlet-orientation"):
                v = v.strip().lower()
                if v.startswith("land"):
                    return "landscape"
                if v.startswith("port"):
                    return "portrait"
    except Exception:
        return None
    return None


def manifest_display_size(jar):
    """(w,h) from Nokia-MIDlet-Target/Original-Display-Size headers."""
    try:
        with zipfile.ZipFile(jar) as z:
            try:
                mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
            except KeyError:
                return None
        want = ("nokia-midlet-target-display-size",
                "nokia-midlet-original-display-size")
        vals = {}
        for line in mf.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower()
            if k in want:
                import re as _re
                parts = _re.split(r"[^0-9]+", v.strip())
                parts = [p for p in parts if p]
                if len(parts) >= 2:
                    try:
                        vals[k] = (int(parts[0]), int(parts[1]))
                    except ValueError:
                        pass
        for key in want:
            if key in vals:
                return vals[key]
    except Exception:
        return None
    return None


def detect_native_from_assets(jar):
    """Detect design resolution + orientation without user input.

    Priority:
      1. Nokia-MIDlet-App-Orientation: landscape/portrait, confirmed by a
         same-orientation preset backdrop if one exists (g3/g4/g1/g2);
      2. Nokia-MIDlet-Target-Display-Size manifest header (g5 Metal Slug:
         360x640 while its biggest art is a misleading 320x320 logo);
      3. biggest bundled picture matching a known preset;
      4. biggest picture overall, if it is clearly a full-screen backdrop;
      5. bytecode constant frequency guess.
    Returns (w, h, source_text) or None.
    """
    pics = []  # (area, w, h)
    orient = manifest_orientation(jar)
    msize = manifest_display_size(jar)
    try:
        with zipfile.ZipFile(jar) as z:
            for n in z.namelist():
                if not n.lower().endswith((".png", ".jpg", ".jpeg")):
                    continue
                wh = _image_wh(z.read(n))
                if wh:
                    pics.append((wh[0] * wh[1], wh[0], wh[1]))
    except Exception:
        pics = []
    if pics:
        pics.sort(reverse=True)
        preset_pics = [(a, w, h) for a, w, h in pics
                       if (w, h) in PRESETS and w >= 240 and h >= 240]
    else:
        preset_pics = []
    if orient:
        # Orientation declared in the JAD wins; prefer same-orientation
        # full-screen art when present (g3 ships only 240x320 portrait
        # logos for a 640x360 landscape game - cross-orientation sprites
        # must not downgrade it).
        want_land = orient == "landscape"
        aligned = [(a, w, h) for a, w, h in preset_pics
                   if (w > h) == want_land]
        if aligned:
            _a, w, h = aligned[0]
            return w, h, "背景图尺寸 %dx%d（JAD:%s）" % (w, h, orient)
        if orient == "landscape":
            return 640, 360, "JAD 方向标记 landscape"
        return 360, 640, "JAD 方向标记 portrait"
    if msize:
        return msize[0], msize[1], \
            "诺基亚尺寸头 %dx%d" % msize
    if preset_pics:
        _a, w, h = preset_pics[0]
        return w, h, "背景图尺寸 %dx%d" % (w, h)
    if pics and pics[0][0] >= 200 * 200:
        _area, w, h = pics[0]
        return w, h, "最大图片 %dx%d" % (w, h)
    guess = detect_native_size(scan_constants(jar))
    if guess:
        return guess[0], guess[1], "字节码常量推断 %dx%d" % guess
    return None


# QWERTY -> game keys, auto-injected after scanning the game's key handler.
# Bold 9930 ships full QWERTY only; digit games still get physical Alt+numpad.
_KEYMAP_VENDOR = [  # games that switch on MIDP game/soft codes -1..-7 (g4)
    119, -1,  87, -1,   # w/W up
    115, -2,  83, -2,   # s/S down
    97, -3,   65, -3,   # a/A left
    100, -4,  68, -4,   # d/D right
    32, -5,               # space fire
    # 9930 trackpad (device-measured: up1 down6 left2 right5 press-8);
    # swipe codes coincide with MIDP action constants but vendor games
    # switch on -1..-5, so translate them too.
    1, -1,  6, -2,  2, -3,  5, -4,  -8, -5,
]
# Bold 9930 actual key legends (confirmed by user photo): the digits
# 1..9 are printed on W E R / S D F / Z X C (right of the usual PC
# numpad guess). Map each letter to the digit on its keycap so users
# just press the key carrying the game's number:
#   1=W 2=E 3=R / 4=S 5=D 6=F / 7=Z 8=X 9=C
# Directions therefore: E=2 up, S=4 left, X=8 down, F=6 right, D=5 centre.
_NUMPAD_KEYS = [
    119, 49,  87, 49,   # w/W -> '1'
    101, 50,  69, 50,   # e/E -> '2' up
    114, 51,  82, 51,   # r/R -> '3'
    115, 52,  83, 52,   # s/S -> '4' left
    100, 53,  68, 53,   # d/D -> '5' (centre: fire / step-down)
    102, 54,  70, 54,   # f/F -> '6' right
    122, 55,  90, 55,   # z/Z -> '7'
    120, 56,  88, 56,   # x/X -> '8' down
    99, 57,   67, 57,   # c/C -> '9'
]
# Space always equals the centre numpad key '5'. Device test (魂斗罗):
# even though the game reads '0' for a separate flag, the real jump/action
# is the centre '5' (ae flag -> aJ=3 airborne) - space->'0' never jumped.
# '5' is fire in shooters and jump in auto-fire platformers; mapping space
# to it gives the expected "space = main action / jump" everywhere.
_KEYMAP_DIGIT = [
    32, 53,              # space -> '5' (fire, or jump in auto-fire games)
    # trackpad swipes/press -> same T9 directions + centre '5'
    1, 50,               # up    -> '2'
    6, 56,               # down  -> '8'
    2, 52,               # left  -> '4'
    5, 54,               # right -> '6'
    -8, 53,              # press -> '5'
] + _NUMPAD_KEYS


def _method_code(cf, name, desc):
    """Code bytes of the named method, else None."""
    for m in cf.methods:
        if cf.utf8(m["name"]) == name and cf.utf8(m["desc"]) == desc:
            for at in m.get("attrs", []):
                if cf.utf8(at["name"]) == "Code":
                    blob = at["data"]
                    return blob[8:8 + struct.unpack_from(">I", blob, 4)[0]]
    return None


def _int_callees(cf, code):
    """(owner,name,desc) of (I)V/(I)I methods invoked from one Code blob."""
    out = set()
    for _off, op, operand in walk_code(code):
        if op not in (0xB6, 0xB7, 0xB8, 0xB9):  # invoke*
            continue
        idx = struct.unpack(">H", operand[:2])[0]
        try:
            ref = cf.cp[idx]
            if not ref or ref[0] not in (10, 11):
                continue
            owner = cf.class_name(ref[1])
            nt = cf.cp[ref[2]]
            name = cf.utf8(nt[1])
            desc = cf.utf8(nt[2])
        except Exception:
            continue
        if desc in ("(I)V", "(I)I"):
            out.add((owner, name, desc))
    return out


def detect_key_scheme(jar):
    """Public wrapper: just the scheme string (GUI info panel)."""
    scheme, _digits = _detect_key_scheme(jar, True)
    return scheme


def _detect_key_scheme(jar, collect_digits=False):
    """Decide what key codes the game understands (g4 lesson).

    Scans every keyPressed(I)V and the one-int-arg methods it delegates to
    (obfuscated games often just do `t.a(key)`; the real tableswitch lives
    in the callee). Returns:
      'vendor' - negative game/soft codes (-1..-22) handled
      'digit'  - ASCII '0'..'9' handled (Nokia numpad style)
      None     - neither / touch-only
    With collect_digits, also returns the set of ASCII digit key codes
    ('0'..'9' = 48..57) actually seen, so the caller can tell platformers
    that use '0' as jump (魂斗罗) from shmups that only have '5' fire.
    Both often coexist (Contra 魂斗罗 handles digits '1'..'9' but also
    soft codes -6/-7) so a bare "vendor wins" misfired. Digit wins when
    digit constants outnumber vendor constants (Contra 35 vs 23); g4's
    vendor switch spans -22..57 so its negatives still outnumber the dead
    digit branches.
    """
    vendor_hits = digit_hits = 0
    digit_seen = set()
    try:
        classes = {}
        with zipfile.ZipFile(jar) as z:
            for e in z.infolist():
                if not e.filename.endswith(".class"):
                    continue
                try:
                    classes[e.filename[:-6]] = ClassFile(z.read(e.filename))
                except Exception:
                    pass
            # BFS along (I)V/(I)I calls: obfuscated games hide the real
            # switch several hops away (g4: keyPressed -> t.a(I)V ->
            # t.a(I)I where the -22..57 tableswitch lives).
            queue = []
            seen = set()
            for entry, cf in classes.items():
                code = _method_code(cf, "keyPressed", "(I)V")
                if code is not None:
                    queue.append((entry, "keyPressed", "(I)V", 0))
            while queue:
                entry, name, desc, depth = queue.pop(0)
                if (entry, name, desc) in seen or depth > 4:
                    continue
                seen.add((entry, name, desc))
                cf = classes.get(entry)
                if cf is None:
                    continue
                code = _method_code(cf, name, desc)
                if code is None:
                    continue
                neg, dig, digset = _scan_key_constants(code)
                vendor_hits += neg
                digit_hits += dig
                if collect_digits:
                    digit_seen |= digset
                if depth < 4:
                    for owner, mname, mdesc in _int_callees(cf, code):
                        if owner in classes and \
                                (owner, mname, mdesc) not in seen:
                            queue.append((owner, mname, mdesc, depth + 1))
    except Exception:
        return (None, set()) if collect_digits else None
    if digit_hits > vendor_hits:
        scheme = "digit"
    elif vendor_hits:
        scheme = "vendor"
    elif digit_hits:
        scheme = "digit"
    else:
        scheme = None
    if collect_digits:
        return scheme, digit_seen
    return scheme


def _scan_key_constants(code):
    """Count vendor-coded and ASCII-digit int constants in one Code blob.

    Returns (vendor_count, digit_count, digit_set) where digit_set holds
    the ASCII codes 48..57 actually compared against ('0'..'9').
    """
    neg = dig = 0
    digset = set()
    i, end = 0, len(code)
    while i < end:
        op = code[i]
        if op == 0x10:  # bipush signed
            v = struct.unpack_from(">b", code, i + 1)[0]
            if -22 <= v <= -1:
                neg += 1
            if 48 <= v <= 57:
                dig += 1
                digset.add(v)
            i += 2
        elif op == 0x11:  # sipush signed
            v = struct.unpack_from(">h", code, i + 1)[0]
            if -22 <= v <= -1:
                neg += 1
            if 48 <= v <= 57:
                dig += 1
                digset.add(v)
            i += 3
        elif op == 0xAA:  # tableswitch: default(4), low(4), high(4)
            pad = (4 - ((i + 1) % 4)) % 4
            base = i + 1 + pad
            if base + 12 > end:
                break
            low, high = struct.unpack_from(">ii", code, base + 4)
            if low <= -1 and high >= -1 and low >= -30:
                neg += 1
            if low <= 57 and high >= 48:
                dig += 1
                digset.update(range(max(low, 48), min(high, 57) + 1))
            i = base + 12 + 4 * (high - low + 1)
        elif op == 0xAB:  # lookupswitch
            pad = (4 - ((i + 1) % 4)) % 4
            base = i + 1 + pad
            if base + 8 > end:
                break
            npairs = struct.unpack_from(">i", code, base + 4)[0]
            if npairs < 0 or base + 8 + 8 * npairs > end:
                break
            for p in range(npairs):
                v = struct.unpack_from(">i", code, base + 8 + 8 * p)[0]
                if -22 <= v <= -1:
                    neg += 1
                if 48 <= v <= 57:
                    dig += 1
                    digset.add(v)
            i = base + 8 + 8 * npairs
        else:
            L = OP_EXTRA[op]
            if L < 0:
                break
            i += 1 + L
    return neg, dig, digset


# ------------------------------------------------------- batch pre-scan
# 批量预扫描：检查目录下所有 jar 是否能上黑莓 9930（CLDC 1.1 / MIDP 2.0）
# 不依赖厂商扩展、JSR 扩展，类版本 ≤48。

def _scan_class_versions(jar):
    """返回 (max_major, bad_classes)。bad_classes = [(name, major)] major > 48."""
    max_major = 0
    bad = []
    with zipfile.ZipFile(jar) as z:
        for e in z.infolist():
            if not e.filename.endswith(".class"):
                continue
            try:
                cf = ClassFile(z.read(e.filename))
            except Exception:
                continue
            if cf.major > max_major:
                max_major = cf.major
            if cf.major > 48:
                bad.append((cf.this_name() or e.filename, cf.major))
    return max_major, bad


def _scan_one(jar):
    """对单个 jar 做合规诊断，返回结构化记录字典。

    只打开 zip 一次，避免对 3948 个 jar 重复 IO。
    """
    rec = {
        "jar_path": jar, "name": os.path.basename(jar),
        "error": None, "max_major": 0, "class_version_ok": True,
        "has_vendor": False, "has_jsr": False,
        "has_fullcanvas": False, "has_gamecanvas": False,
        "has_canvas": False,
        "canvas_classes": [], "gamecanvas_classes": [],
        "vendor_refs": set(),
        "jsr_refs": set(),                       # = jsr_unsupported ∪ jsr_supported
        "jsr_unsupported_refs": set(),           # 黑莓不支持
        "jsr_supported_refs": set(),             # 黑莓支持（不阻断）
        "native_size_guess": None, "bad_classes": [],
        "blackberry_ok": True, "reason": "",
        "integrity_fatal": [], "integrity_warn": [],
    }
    try:
        # 单次 zip 打开：解析所有 class，收集 (name, major, cf)
        classes = []
        with zipfile.ZipFile(jar) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            sizes = dict((i.filename, i.file_size) for i in infos)
            mf_text = ""
            if "META-INF/MANIFEST.MF" in names:
                mf_text = z.read(
                    "META-INF/MANIFEST.MF").decode("utf-8", "replace")
            for e in infos:
                if not e.filename.endswith(".class"):
                    continue
                try:
                    cf = ClassFile(z.read(e.filename))
                except Exception:
                    continue
                classes.append((cf.this_name() or e.filename, cf.major, cf))

        # 类版本审计
        max_major = 0
        bad = []
        for name, major, cf in classes:
            if major > max_major:
                max_major = major
            if major > 48:
                bad.append((name, major))
        rec["max_major"] = max_major
        rec["bad_classes"] = bad
        rec["class_version_ok"] = max_major <= 48

        # 厂商/JSR 扩展（细分黑莓支持/不支持）
        vendor_refs = set()
        jsr_unsupported = set()
        jsr_supported = set()
        for name, major, cf in classes:
            for ent in cf.cp:
                if not ent:
                    continue
                tag = ent[0]
                if tag == 7:
                    cn = cf.utf8(ent[1])
                elif tag in (9, 10, 11):
                    cn = cf.class_name(ent[1])
                else:
                    continue
                if not cn or cn[0] == "[" or cn[0] in "()VTIJZBCDFS":
                    continue
                if cn.startswith("java/") or cn == NOKIA_FULLCANVAS:
                    continue
                matched = False
                # 黑莓不支持的 JSR → 不可上
                for jp in JSR_BLACKBERRY_UNSUPPORTED:
                    if cn.startswith(jp):
                        jsr_unsupported.add(cn)
                        matched = True
                        break
                if matched:
                    continue
                # 黑莓支持的 JSR → 不阻断
                for jp in JSR_BLACKBERRY_SUPPORTED:
                    if cn.startswith(jp):
                        jsr_supported.add(cn)
                        matched = True
                        break
                if matched:
                    continue
                # 厂商扩展 → 不可上
                for vp in VENDOR_PREFIXES:
                    if cn.startswith(vp):
                        vendor_refs.add(cn)
                        matched = True
                        break
                if matched:
                    continue
                # 跳过标准 MIDP/CLDC/WMA
                if cn.startswith("javax/microedition/lcdui/") or \
                        cn.startswith("javax/microedition/midlet/") or \
                        cn.startswith("javax/microedition/rms/") or \
                        cn.startswith("javax/microedition/io/") or \
                        cn.startswith("javax/wireless/"):
                    continue
        rec["vendor_refs"] = vendor_refs
        rec["jsr_unsupported_refs"] = jsr_unsupported
        rec["jsr_supported_refs"] = jsr_supported
        rec["jsr_refs"] = jsr_unsupported | jsr_supported
        rec["has_vendor"] = bool(vendor_refs)
        rec["has_jsr"] = bool(jsr_unsupported)  # 旧字段语义收紧：仅黑莓不支持的

        # Canvas 层级（识别 FullCanvas 为 Canvas 等价物）
        graph = {name: cf.super_name() for name, major, cf in classes}
        canvases, gamecanvas = canvas_hierarchy(graph)
        rec["canvas_classes"] = sorted(canvases)
        rec["gamecanvas_classes"] = sorted(gamecanvas)
        rec["has_canvas"] = bool(canvases)
        rec["has_gamecanvas"] = bool(gamecanvas)
        # 是否有继承 Nokia FullCanvas 的类（可由本工具转换救回）
        rec["has_fullcanvas"] = any(
            sup == NOKIA_FULLCANVAS for sup in graph.values())

        # 推测原生分辨率（内联 scan_constants + detect_native_size）
        tally = {}
        for name, major, cf in classes:
            for m, a, code, blob in cf.code_blobs():
                try:
                    for idx, op, operand in walk_code(code):
                        if op == 0x10:
                            v = struct.unpack(">b", operand)[0]
                        elif op == 0x11:
                            v = struct.unpack(">h", operand)[0]
                        else:
                            continue
                        if v > 0:
                            tally[v] = tally.get(v, 0) + 1
                except Exception:
                    continue
        rec["native_size_guess"] = detect_native_size(tally)

        # 包完整性/可运行性（引擎库、残缺包、入口类/图标缺失）
        all_class_entries = set(
            n[:-6] for n in names if n.endswith(".class"))
        integ = _evaluate_integrity(names, sizes, mf_text,
                                    graph.get, all_class_entries)
        rec["integrity_fatal"] = integ["fatal"]
        rec["integrity_warn"] = integ["warnings"]

        # 综合判断：能否上黑莓 9930
        # 不可上的原因：包不完整/无启动入口 + 类版本超 + 真厂商扩展
        # （FullCanvas 单独处理）+ 黑莓不支持的 JSR
        # has_fullcanvas 不算"不可上"，因为本工具自动模式可处理
        # has_jsr_supported（MMAPI/PIM/FileConnection）不算"不可上"
        reasons = []
        for f in integ["fatal"]:
            reasons.append("包不可运行: " + f)
        if not rec["class_version_ok"]:
            reasons.append("类版本 %d（需 ≤48）" % max_major)
        if rec["has_vendor"]:
            reasons.append("厂商扩展 API")
        if rec["jsr_unsupported_refs"]:
            reasons.append("JSR 扩展 API（黑莓不支持）")
        rec["reason"] = "；".join(reasons)
        rec["blackberry_ok"] = not reasons
    except Exception as ex:
        rec["error"] = str(ex)
        rec["blackberry_ok"] = False
        rec["reason"] = "扫描异常: %s" % ex
    return rec


def batch_scan(directory, log=None, progress_cb=None, recursive=False):
    """扫描目录下所有 jar，返回每个 jar 的合规诊断列表。

    对每个 jar 检查：
    - 类版本 ≤48（黑莓 9930 / CLDC 1.1 要求）
    - 厂商扩展 API 引用（Nokia UI / Motorola / Samsung 等）
    - JSR 扩展 API 引用（sensor / m3g / amms / MMAPI 等）
    - Canvas / FullCanvas / GameCanvas 类型
    - 推测原生分辨率

    参数:
      directory   - 要扫描的目录
      log         - 日志回调（可选）
      progress_cb - 进度回调 (i, total, jar_path)（可选）
      recursive   - 是否递归子目录

    返回 list of dict，每项见 _scan_one。
    """
    if log is None:
        log = lambda s: None
    # 收集 jar
    jars = []
    if recursive:
        for root, _, files in os.walk(directory):
            for f in files:
                if f.lower().endswith(".jar"):
                    jars.append(os.path.join(root, f))
    else:
        for f in os.listdir(directory):
            if f.lower().endswith(".jar"):
                jars.append(os.path.join(directory, f))
    jars.sort()
    total = len(jars)
    log("批量扫描: 共 %d 个 jar" % total)
    results = []
    for i, jar in enumerate(jars):
        if progress_cb:
            progress_cb(i, total, jar)
        rec = _scan_one(jar)
        results.append(rec)
        # 不可上的在 log 里提示
        if not rec["blackberry_ok"]:
            log("  ✗ %s  (%s)" % (rec["name"], rec["reason"]))
    if progress_cb:
        progress_cb(total, total, "")
    ok = sum(1 for r in results if r["blackberry_ok"])
    log("扫描完成: %d/%d 可上黑莓 9930" % (ok, total))
    return results


# ------------------------------------------------------- transform: AUTO

# pointer hooks that have concrete (empty) implementations in the adapter
# and can be used as the target of a retargeted super.pointerXxx() call
SUPER_RETARGET = {
    ("pointerPressed", "(II)V"),
    ("pointerReleased", "(II)V"),
    ("pointerDragged", "(II)V"),
    ("keyPressed", "(I)V"),
    ("keyReleased", "(I)V"),
    ("keyRepeated", "(I)V"),
}


def transform_auto(data, transformed_names, adapter_idx_cache=None):
    cf = ClassFile(data)
    name = cf.this_name()
    if name not in transformed_names:
        return None
    adapter_class = cf.add_class(ADAPTER)

    # 1) rename declared overrides so they don't clash with final methods
    for m in cf.methods:
        nm, ds = cf.utf8(m["name"]), cf.utf8(m["desc"])
        new = RENAME_METHODS.get((nm, ds))
        if new:
            m["name"] = cf.add_utf8(new)

    # 2) classify invoke usage of each method-ref in this class; one CP
    #    entry may be shared by several invoke sites of different kinds
    used_special, used_virtual = set(), set()
    for m, a, code, blob in cf.code_blobs():
        for _off, op, operand in walk_code(code):
            if op == 0xB7:
                used_special.add(struct.unpack(">H", operand[:2])[0])
            elif op in (0xB6, 0xB9):
                used_virtual.add(struct.unpack(">H", operand[:2])[0])

    # entries shared between invokespecial and virtual sites need their
    # own CP entry for the retargeted super call: old idx -> new idx
    code_patches = {}
    pending_cp = []
    # cp indices of GameCanvas.<init>(Z)V refs rewritten to the adapter
    # no-arg constructor; the pushed boolean argument must be NOPed.
    gc_init_refs = set()

    # 3) rewrite constant-pool refs
    for ci, e in enumerate(cf.cp):
        if not e or e[0] not in (10, 11):
            continue
        owner = cf.class_name(e[1])
        nat = cf.cp[e[2]]
        nm = cf.utf8(nat[1])
        ds = cf.utf8(nat[2])
        # constructor of Canvas / Nokia FullCanvas -> adapter constructor
        if owner in (CANVAS, NOKIA_FULLCANVAS) and \
                nm == "<init>" and ds == "()V":
            e[1] = adapter_class
            continue
        # super(boolean) GameCanvas constructor -> adapter no-arg ctor;
        # the iconst pushed for the boolean is removed at instruction level
        if owner == GAMECANVAS and nm == "<init>" and ds == "(Z)V":
            gc_init_refs.add(ci)
            e[1] = adapter_class
            e[2] = cf.add_nat("<init>", "()V")
            continue
        # calls on game classes of the transformed hierarchy: the target
        # method was renamed in place
        if owner in transformed_names:
            new = RENAME_REF_RULES.get((nm, ds))
            if new:
                e[2] = cf.add_nat(new, ds)
            continue
        # refs owned by the API Canvas class itself:
        #  - virtual/interface calls keep their name so dispatch reaches
        #    the adapter's final overrides (coordinate mapping etc.)
        #  - invokespecial is a game-class super.pointerXxx() call; it must
        #    be retargeted to the adapter hook. Renaming in place would
        #    reference a method Canvas does not declare, while keeping it
        #    makes ACC_SUPER resolution find the final adapter override,
        #    causing infinite recursion.
        if (owner in (CANVAS, NOKIA_FULLCANVAS)
                and (nm, ds) in SUPER_RETARGET
                and ci in used_special):
            new = RENAME_REF_RULES[(nm, ds)]
            if ci in used_virtual:
                nati = cf.add_nat(new, ds)
                pending_cp.append([10, adapter_class, nati])
                code_patches[ci] = len(cf.cp) + len(pending_cp) - 1
            else:
                e[1] = adapter_class
                e[2] = cf.add_nat(new, ds)

    for pe in pending_cp:
        cf.cp.append(pe)

    # 4) instruction-level retargets for shared CP entries + GameCanvas
    #    boolean-argument removal (iconst_0/1 -> nop before super call)
    if code_patches or gc_init_refs:
        for m, a, code, blob in cf.code_blobs():
            changed = False
            prev = -10
            for off, op, operand in walk_code(code):
                if op == 0xB7:
                    oldi = struct.unpack(">H", operand[:2])[0]
                    if oldi in code_patches:
                        struct.pack_into(">H", code, off + 1,
                                         code_patches[oldi])
                        changed = True
                    elif oldi in gc_init_refs and off - prev == 1 and \
                            prev >= 0 and code[prev] in (0x03, 0x04):
                        # iconst_0/1 pushed the suppressKeyEvents flag
                        code[prev] = 0x00  # nop
                        changed = True
                prev = off
            if changed:
                a["data"] = (bytes(blob[:8]) + bytes(code)
                             + bytes(blob[8 + len(code):]))

    # 5) superclass swap for direct Canvas / Nokia FullCanvas / GameCanvas
    #    subclasses
    if cf.super_name() in (CANVAS, NOKIA_FULLCANVAS, GAMECANVAS):
        cf.super_c = adapter_class

    return cf.build()


# ------------------------------------------------------ transform: FIXED

def _fits_bipush(v):
    return -128 <= v <= 127


def _fits_sipush(v):
    return -32768 <= v <= 32767


def patch_constants(data, replacements):
    """replacements: {orig_value: new_value}. In-place, same instruction size.

    Returns count applied. Raises ConvertError if encoding mismatch.
    """
    cf = ClassFile(data)
    applied = 0
    for m, a, code, blob in cf.code_blobs():
        changed = False
        try:
            for idx, op, operand in walk_code(code):
                if op == 0x10:
                    v = struct.unpack(">b", operand)[0]
                    if v in replacements:
                        nv = replacements[v]
                        if not _fits_bipush(nv):
                            raise ConvertError(
                                "常量 %d→%d 无法同长度替换(BIPUSH)" % (v, nv))
                        struct.pack_into(">b", code, idx + 1, nv)
                        changed = True
                        applied += 1
                elif op == 0x11:
                    v = struct.unpack(">h", operand)[0]
                    if v in replacements:
                        nv = replacements[v]
                        if not _fits_sipush(nv):
                            raise ConvertError(
                                "常量 %d→%d 超出 SIPUSH 范围" % (v, nv))
                        struct.pack_into(">h", code, idx + 1, nv)
                        changed = True
                        applied += 1
        except ConvertError:
            raise
        except Exception:
            continue
        if changed:
            a["data"] = blob[:8] + bytes(code) + blob[8 + len(code):]
    if applied:
        return cf.build(), applied
    return None, 0


# ------------------------------------------------------------ jar output

def _adapter_bytes():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "assets", "jr", "ScaleCanvas.class"),
    ]
    # frozen PyInstaller onefile bundle
    if getattr(sys, "frozen", False):
        candidates.insert(0, os.path.join(
            sys._MEIPASS, "assets", "jr", "ScaleCanvas.class"))
    for p in candidates:
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read()
    raise ConvertError("缺少 assets/jr/ScaleCanvas.class（先运行 build_adapter.ps1）")


# ----------------- 游戏原包自带 bug 的字节码兜底补丁 -----------------
#
# GuardArrayPatcher（ASM）：结构性扫描每个类的 (IZ)V 方法，把
#   GETSTATIC 某静态数组; ILOAD n; xALOAD
# 改成越界时返回 0/null 的守卫访问。无该模式的游戏输出与原 jar 完全一致，
# 属于安全 no-op。
#
# 首个命中：g9《奥特曼-超英雄传说》原版（诺基亚 N97 360x640 包同样崩）：
# 资源名数组 aD 长度 38，精灵数组 b 被多开 1 槽（length 39），
# LoadLevel 预载循环 i=0..38 在 i=38 处先撞 i.h[38]（long[38]）、
# 再撞 aD[38]，进战斗必抛 ArrayIndexOutOfBoundsException。

def _find_java():
    """Path to a java.exe able to run the ASM patcher, or None."""
    env = os.environ.get("JAVA_HOME")
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(base, "tools", "jre", "bin", "java.exe")]
    if env:
        candidates.append(os.path.join(env, "bin", "java.exe"))
    candidates.append(
        r"C:\Program Files\Microsoft\jdk-17.0.14.7-hotspot\bin\java.exe")
    for p in candidates:
        if os.path.isfile(p):
            return p
    for p in os.environ.get("PATH", "").split(os.pathsep):
        q = os.path.join(p, "java.exe")
        if p and os.path.isfile(q):
            return q
    return None


def _patches_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, "assets", "patches")]
    if getattr(sys, "frozen", False):
        candidates.insert(0, os.path.join(sys._MEIPASS, "assets", "patches"))
    for p in candidates:
        if os.path.isfile(os.path.join(p, "GuardArrayPatcher.class")):
            return p
    return None


def run_game_bytecode_patches(jar_path, log=lambda s: None):
    """Post-convert safety patches against shipped-game bugs.

    No-op (both on disk and behaviour) when the pattern is absent or the
    toolchain is missing - conversion must never hard-fail on this stage.
    """
    pdir = _patches_dir()
    java = _find_java()
    if not pdir or not java:
        log("跳过游戏字节码兜底补丁（未找到补丁类或 Java 运行时）")
        return
    fd, tmpout = tempfile.mkstemp(
        suffix=".jar", prefix="gpatch_", dir=os.path.dirname(jar_path))
    os.close(fd)
    try:
        sep = ";" if os.name == "nt" else ":"
        cp = pdir + sep + os.path.join(pdir, "asm.jar")
        proc = subprocess.Popen(
            [java, "-cp", cp, "GuardArrayPatcher", jar_path, tmpout],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        text = proc.stdout.read().decode("utf-8", "replace")
        proc.wait()
        hits = 0
        for ln in text.splitlines():
            if "guarded array load" in ln:
                try:
                    hits = int(ln.split(":")[1].strip().split()[0])
                except (IndexError, ValueError):
                    pass
        if hits:
            os.replace(tmpout, jar_path)
            log("游戏自带 bug 兜底: 已守卫 %d 处越界数组访问" % hits)
        else:
            log("游戏字节码兜底补丁: 无命中")
    except Exception as ex:
        log("游戏字节码兜底补丁运行失败（不阻断转换）: %s" % ex)
    finally:
        try:
            if os.path.isfile(tmpout):
                os.remove(tmpout)
        except OSError:
            pass


def encode_keymap(pairs):
    """pairs: flat [from,to,from,to,...] MIDP key codes -> /jr-keys text."""
    return ",".join(str(int(v)) for v in pairs)


# --- oversized-class splitter (rapc "code section too large" wall) ------
#
# rapc emits COD code sections with 16-bit offsets (cap ~64 KB) and cannot
# split a single class across sections. Gameloft-style engines may pack
# 68+ KB of bytecode into one class (Contra 魂斗罗 c.class: 68146 bytes ->
# 77732-byte section -> "I/O Error: code section too large"). Observed
# bytecode->COD section ratio ~1.14, so split classes whose bytecode is
# >=56 KB and keep each side <=45 KB. ClassSplit moves the largest methods
# into a sibling class in the SAME package (instance methods become static
# with the owner prepended; slots/frames stay identical).
SPLIT_CLASS_CODE_LIMIT = 56000
SPLIT_CLASS_CODE_TARGET = 45000
_SPLIT_RESERVED_METHODS = frozenset((
    "<init>", "<clinit>",
    # ScaleCanvas adapter surface / lifecycle / callbacks - must stay on
    # the canvas class.
    "paintScaled", "scKeyPressed", "scKeyReleased",
    "scPointerPressed", "scPointerReleased", "scPointerDragged",
    "hideNotify", "showNotify", "notifyInfo", "playerUpdate",
))


def _class_code_size(cf):
    return sum(len(code) for _m, _a, code, _b in cf.code_blobs())


def split_fat_classes(jar_path, log=lambda s: None):
    """Move methods out of classes whose bytecode would overflow rapc's
    ~64 KB COD code section. Mutates the jar in place. No-op when nothing
    qualifies (also when the tool is unavailable - non-fatal stage).

    魂斗罗1代 (Contra_I): root c.class 68 KB, method l(I)V 27 KB moved
    to sibling jrs1 -> sections ~47/31 KB, COD builds and loads.
    """
    pdir = _patches_dir()
    java = _find_java()
    if not pdir or not java:
        return
    if not os.path.isfile(os.path.join(pdir, "ClassSplit.class")):
        return

    def plan(name, cf, existing):
        total = _class_code_size(cf)
        if total < SPLIT_CLASS_CODE_LIMIT:
            return None
        cands = []
        for m in cf.methods:
            nm = cf.utf8(m["name"])
            ds = cf.utf8(m["desc"])
            if nm in _SPLIT_RESERVED_METHODS:
                continue
            acc = m["access"]
            if acc & 0x0400 or acc & 0x0100 or acc & 0x0020:
                # abstract / native / synchronized - not movable as-is
                continue
            sz = 0
            for a in m["attrs"]:
                if cf.utf8(a["name"]) == "Code":
                    sz = struct.unpack_from(">I", a["data"], 4)[0]
            if sz:
                cands.append((sz, nm, ds))
        cands.sort(reverse=True)
        picked, left = [], total
        for sz, nm, ds in cands:
            if left <= SPLIT_CLASS_CODE_TARGET:
                break
            picked.append("%s%s" % (nm, ds))
            left -= sz
        if left >= SPLIT_CLASS_CODE_LIMIT:
            log("警告: 类 %s 字节码 %d 字节，可拆分后仍约 %d 字节，"
                "可能仍超过 COD 代码段上限" % (name, total, left))
        if not picked:
            log("警告: 类 %s 字节码 %d 字节超过 COD 代码段上限且没有可安全"
                "拆出的方法，rapc 可能失败" % (name, total))
            return None
        # unique sibling name in the same package
        pkg = name.rsplit("/", 1)[0] if "/" in name else ""
        i = 1
        while True:
            sib = ("jrs%d" % i) if not pkg else ("%s/jrs%d" % (pkg, i))
            if (sib + ".class") not in existing:
                break
            i += 1
        return sib, picked, total, left

    work = []
    created = set()
    try:
        with zipfile.ZipFile(jar_path) as z:
            names = set(z.namelist())
            for n in z.namelist():
                if not n.endswith(".class"):
                    continue
                try:
                    cf = ClassFile(z.read(n))
                except Exception:
                    continue
                pl = plan(n[:-6], cf, names | created)
                if pl:
                    work.append((n[:-6],) + pl)
                    created.add(pl[0] + ".class")
    except Exception as ex:
        log("大类拆分扫描失败（不阻断转换）: %s" % ex)
        return

    if not work:
        return

    sep = ";" if os.name == "nt" else ":"
    cp = pdir + sep + os.path.join(pdir, "asm.jar")
    for owner, sib, specs, total, left in work:
        fd, tmpout = tempfile.mkstemp(
            suffix=".jar", prefix="split_", dir=os.path.dirname(jar_path))
        os.close(fd)
        try:
            proc = subprocess.Popen(
                [java, "-cp", cp, "ClassSplit", jar_path, tmpout,
                 owner, sib] + specs,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            text = proc.stdout.read().decode("utf-8", "replace")
            proc.wait()
            if proc.returncode != 0 or "split OK" not in text:
                log("大类拆分失败（不阻断转换）: %s"
                    % "\n".join(text.splitlines()[-3:]))
                continue
            os.replace(tmpout, jar_path)
            log("大类拆分: %s(%dKB) 已将 %d 个方法移至 %s（保留约 %dKB），"
                "以绕过 COD 64KB 代码段限制"
                % (owner, total // 1024, len(specs), sib, left // 1024))
        except Exception as ex:
            log("大类拆分运行失败（不阻断转换）: %s" % ex)
        finally:
            try:
                if os.path.isfile(tmpout):
                    os.remove(tmpout)
            except OSError:
                pass


# --------------------------- vendor/JSR API shim auto-injection ----------
#
# Each family: classes under "prefix" provided by a bundled stub in assets/.
# files     - jar entries the family can inject
# members   - implemented members per class; other referenced members warn
# groups    - classes that always travel together (DirectUtils newImpl)

NOKIA_UI_PREFIX = "com/nokia/mid/ui/"
# DirectGraphics is obtained through DirectUtils, so they travel together
_DIRECT_GRAPHICS_FAMILY = {
    "com/nokia/mid/ui/DirectGraphics",
    "com/nokia/mid/ui/DirectUtils",
    "com/nokia/mid/ui/DirectGraphicsImpl",
}

SHIM_FAMILIES = [
    {
        "label": "Nokia UI API",
        "prefix": NOKIA_UI_PREFIX,
        "files": (
            "com/nokia/mid/ui/DirectGraphics.class",
            "com/nokia/mid/ui/DirectGraphicsImpl.class",
            "com/nokia/mid/ui/DirectUtils.class",
            "com/nokia/mid/ui/FullCanvas.class",
            "com/nokia/mid/ui/DeviceControl.class",
            "com/nokia/mid/ui/TactileFeedback.class",
        ),
        "groups": (
            _DIRECT_GRAPHICS_FAMILY,
        ),
        "members": {
            "com/nokia/mid/ui/DirectGraphics": {
                "fillPolygon", "drawPixels",
            },
            "com/nokia/mid/ui/DirectUtils": {
                "getDirectGraphics",
            },
            "com/nokia/mid/ui/FullCanvas": {
                "<init>",
            },
            "com/nokia/mid/ui/DeviceControl": {
                "<init>", "setLights", "flashLights", "startVibra",
                "stopVibra",
            },
            "com/nokia/mid/ui/TactileFeedback": {
                "<init>", "directFeedback", "triggerFeedback",
            },
        },
    },
    {
        # JSR-256 classes live in jr/sensor (BB forbids app-defined
        # javax.* classes); game bytecode refs are rewritten on convert.
        "label": "JSR-256 传感器 API",
        "prefix": "jr/sensor/",
        "scan_prefix": "javax/microedition/sensor/",
        "rewrites": (("javax/microedition/sensor/", "jr/sensor/"),),
        # route ALL GCF Connector.open(String) through our manager so
        # sensor: URLs get the fake accelerometer (HTTP etc. delegated)
        "method_retargets": (
            ("javax/microedition/io/Connector", "open",
             "(Ljava/lang/String;)Ljavax/microedition/io/Connection;",
             "jr/sensor/SensorManager"),
        ),
        # inject keyPressed(int) -> SensorManager.keyPressed into canvases
        "key_hook": ("jr/sensor/SensorManager", "keyPressed", "(I)V"),
        # g2: startGame() first call normally just shows the "hold phone
        # upright" GO screen and waits for a second touch (which misses on
        # the wider 480px screen). Replace
        #   ifeq L1; aload_0; invokevirtual GoToPlay
        # with
        #   pop; aload_0; invokevirtual GoToPlay; return; nop
        # (same length; original else branch becomes dead code).
        "code_patches": (
            ("cc/jmap/games/GameLogic", "startGame", "()V", 3,
             b"\x99\x00\x08\x2a\xb6\x00\x69",
             b"\x57\x2a\xb6\x00\x69\xb1\x00",
             "跳过体感预备画面直接开局"),
        ),
        "files": (
            "jr/sensor/Unit.class",
            "jr/sensor/MeasurementRange.class",
            "jr/sensor/ChannelInfo.class",
            "jr/sensor/Data.class",
            "jr/sensor/DataListener.class",
            "jr/sensor/SensorInfo.class",
            "jr/sensor/SensorConnection.class",
            "jr/sensor/SensorManager.class",
            "jr/sensor/FakeChannelInfo.class",
            "jr/sensor/FakeSensorInfo.class",
            "jr/sensor/FakeData.class",
            "jr/sensor/FakeSensorConnection.class",
        ),
        # implementation classes reached only from other injected stubs
        "companions": (
            "jr/sensor/FakeChannelInfo.class",
            "jr/sensor/FakeSensorInfo.class",
            "jr/sensor/FakeData.class",
            "jr/sensor/FakeSensorConnection.class",
        ),
        "groups": (),
        "members": {
            "javax/microedition/sensor/Unit": {
                "<init>", "toString",
            },
            "javax/microedition/sensor/MeasurementRange": {
                "<init>", "getSmallestValue", "getLargestValue",
                "getResolution",
            },
            "javax/microedition/sensor/ChannelInfo": {
                "getDataType", "getScale", "getUnit", "getName",
                "getAccuracy", "getMeasurementRanges",
            },
            "javax/microedition/sensor/Data": {
                "getChannelInfo", "getIntValues",
            },
            "javax/microedition/sensor/DataListener": {
                "dataReceived",
            },
            "javax/microedition/sensor/SensorInfo": {
                "getChannelInfos", "getConnectionType", "getContextType",
                "getDescription", "getMaxBufferSize", "getModel",
                "getProperty", "getPropertyNames", "getQuantity", "getUrl",
                "isAvailabilityPushSupported", "isAvailable",
                "isConditionPushSupported",
            },
            "javax/microedition/sensor/SensorConnection": {
                "setDataListener", "removeDataListener", "close",
            },
            "javax/microedition/sensor/SensorManager": {
                "findSensors",
            },
        },
    },
    {
        # BlackBerry CLDC lacks java.security.MessageDigest; the bundled
        # one is a self-contained MD5 (score-signing etc.). Relocated to
        # jr.jsec since BB forbids app-defined java.* classes.
        "label": "java.security MD5",
        "prefix": "jr/jsec/",
        "scan_prefix": "java/security/MessageDigest",
        "rewrites": (("java/security/MessageDigest",
                      "jr/jsec/MessageDigest"),),
        "files": (
            "jr/jsec/MessageDigest.class",
            "jr/jsec/MessageDigest$MD5Digest.class",
        ),
        # inner implementation class is never referenced by game code but
        # must ship whenever MessageDigest does
        "companions": (
            "jr/jsec/MessageDigest$MD5Digest.class",
        ),
        "groups": (),
        "members": {
            "java/security/MessageDigest": {
                "<init>", "getInstance", "reset", "update", "digest",
            },
        },
    },
    {
        # JSR-120 Wireless Messaging (SMS, used by CN feature-phone games
        # for premium billing). Relocated to jr/wireless since BB forbids
        # app-defined javax.* classes; sends silently no-op.
        "label": "JSR-120 无线短信 API",
        "prefix": "jr/wireless/",
        "scan_prefix": "javax/wireless/messaging/",
        "rewrites": (("javax/wireless/messaging/",
                      "jr/wireless/messaging/"),),
        # route GCF Connector.open(String) through jr.wireless.Wma so
        # sms: URLs get the fake connection (other schemes delegated)
        "method_retargets": (
            ("javax/microedition/io/Connector", "open",
             "(Ljava/lang/String;)Ljavax/microedition/io/Connection;",
             "jr/wireless/Wma"),
        ),
        "files": (
            "jr/wireless/Wma.class",
            "jr/wireless/messaging/Message.class",
            "jr/wireless/messaging/TextMessage.class",
            "jr/wireless/messaging/BinaryMessage.class",
            "jr/wireless/messaging/MessageListener.class",
            "jr/wireless/messaging/MessageConnection.class",
            "jr/wireless/messaging/FakeTextMessage.class",
            "jr/wireless/messaging/FakeBinaryMessage.class",
            "jr/wireless/messaging/FakeMessageConnection.class",
        ),
        # implementation classes reached only from injected stubs;
        # Message is the super-interface of Text/Binary so ProGuard's
        # hierarchy resolution needs it even when game bytecode never
        # names it directly
        "companions": (
            "jr/wireless/Wma.class",
            "jr/wireless/messaging/Message.class",
            # Text/Binary are super-interfaces of the fakes; a game that
            # only sends text SMS never names BinaryMessage directly, so
            # the scanner can't detect it but ProGuard still needs the
            # hierarchy while reading FakeBinaryMessage.
            "jr/wireless/messaging/TextMessage.class",
            "jr/wireless/messaging/BinaryMessage.class",
            # MessageListener only appears in method descriptors
            # (setMessageListener), never as a Class_info entry, so the
            # scanner cannot detect it; ship for hierarchy resolution
            "jr/wireless/messaging/MessageListener.class",
            "jr/wireless/messaging/MessageConnection.class",
            "jr/wireless/messaging/FakeTextMessage.class",
            "jr/wireless/messaging/FakeBinaryMessage.class",
            "jr/wireless/messaging/FakeMessageConnection.class",
        ),
        "groups": (),
        "members": {
            "javax/wireless/messaging/Message": {
                "getAddress", "setAddress", "getTimestamp",
            },
            "javax/wireless/messaging/TextMessage": {
                "getPayloadText", "setPayloadText",
            },
            "javax/wireless/messaging/BinaryMessage": {
                "getPayloadData", "setPayloadData",
            },
            "javax/wireless/messaging/MessageListener": {
                "notifyIncomingMessage",
            },
            "javax/wireless/messaging/MessageConnection": {
                "newMessage", "send", "receive", "close",
                "setMessageListener", "numberOfSegments",
            },
        },
    },
    {
        # JSR-75 FileConnection (PIM optional package's file part).
        # BB 7.1's compile library lacks the package and BB forbids
        # app-defined javax.* classes; refs relocate to jr.file, served
        # by a self-contained in-memory file system. Connector.open(...)
        # is retargeted so file: URLs hit the fake FS while other schemes
        # (http/socket/...) are delegated to the real GCF.
        "label": "JSR-75 文件连接 API",
        "prefix": "jr/file/",
        "scan_prefix": "javax/microedition/io/file/",
        "rewrites": (("javax/microedition/io/file/", "jr/file/"),),
        "method_retargets": (
            ("javax/microedition/io/Connector", "open",
             "(Ljava/lang/String;)Ljavax/microedition/io/Connection;",
             "jr/file/FileManager"),
            ("javax/microedition/io/Connector", "open",
             "(Ljava/lang/String;I)Ljavax/microedition/io/Connection;",
             "jr/file/FileManager"),
            ("javax/microedition/io/Connector", "open",
             "(Ljava/lang/String;IZ)Ljavax/microedition/io/Connection;",
             "jr/file/FileManager"),
        ),
        "files": (
            "jr/file/FileConnection.class",
            "jr/file/FileSystemRegistry.class",
            "jr/file/FileSystemListener.class",
            "jr/file/IllegalModeException.class",
            "jr/file/ConnectionClosedException.class",
            "jr/file/FakeFileConnection.class",
            "jr/file/FakeFileConnection$Entry.class",
            "jr/file/FakeFileConnection$MemOutputStream.class",
            "jr/file/FileManager.class",
        ),
        # FileManager is the bytecode retarget target (never named in the
        # original jar); FakeFileConnection + its inner classes are only
        # reached from it, so ship all of them unconditionally.
        "companions": (
            "jr/file/FakeFileConnection.class",
            "jr/file/FakeFileConnection$Entry.class",
            "jr/file/FakeFileConnection$MemOutputStream.class",
            "jr/file/FileManager.class",
            # only named from the injected stub's method descriptors
            "jr/file/ConnectionClosedException.class",
            "jr/file/IllegalModeException.class",
        ),
        "groups": (),
        "members": {
            "javax/microedition/io/file/FileConnection": {
                "availableSize", "canRead", "canWrite", "create",
                "delete", "directorySize", "exists", "fileSize",
                "getName", "getPath", "getURL", "isDirectory", "isHidden",
                "isOpen", "lastModified", "list", "mkdir",
                "openInputStream", "openOutputStream",
                "openDataInputStream", "openDataOutputStream",
                "rename", "setFileConnection", "setHidden", "setReadable",
                "setWritable", "truncate", "totalSize", "usedSize",
                "close",
            },
            "javax/microedition/io/file/FileSystemRegistry": {
                "roots", "openFileConnection", "addFileSystemListener",
                "removeFileSystemListener",
            },
            "javax/microedition/io/file/FileSystemListener": {
                "rootChanged",
            },
            "javax/microedition/io/file/IllegalModeException": {
                "<init>",
            },
            "javax/microedition/io/file/ConnectionClosedException": {
                "<init>",
            },
        },
    },
    # ---- JSR-184 M3G 3D（黑莓 OS 无此 JSR，空壳 stub 仅供预验证通过）
    {
        "label": "JSR-184 M3G",
        "prefix": "jr/m3g/",
        "scan_prefix": "javax/microedition/m3g/",
        "rewrites": (("javax/microedition/m3g/", "jr/m3g/"),),
        "files": (
            "jr/m3g/Appearance.class",
            "jr/m3g/Background.class",
            "jr/m3g/Camera.class",
            "jr/m3g/CompositingMode.class",
            "jr/m3g/Graphics3D.class",
            "jr/m3g/Group.class",
            "jr/m3g/Image2D.class",
            "jr/m3g/IndexBuffer.class",
            "jr/m3g/Light.class",
            "jr/m3g/Loader.class",
            "jr/m3g/Mesh.class",
            "jr/m3g/Node.class",
            "jr/m3g/Object3D.class",
            "jr/m3g/PolygonMode.class",
            "jr/m3g/Sprite3D.class",
            "jr/m3g/Texture2D.class",
            "jr/m3g/Transform.class",
            "jr/m3g/Transformable.class",
            "jr/m3g/TriangleStripArray.class",
            "jr/m3g/VertexBuffer.class",
            "jr/m3g/World.class",
        ),
        # 描述符/继承链里独有的类（Transformable、IndexBuffer 等）不一定会
        # 以 Class 常量形式被扫描到，整个家族整组随行注入。
        "companions": (
            "jr/m3g/Appearance.class",
            "jr/m3g/Background.class",
            "jr/m3g/BR.class",
            "jr/m3g/Camera.class",
            "jr/m3g/CompositingMode.class",
            "jr/m3g/Graphics3D.class",
            "jr/m3g/Group.class",
            "jr/m3g/Image2D.class",
            "jr/m3g/IndexBuffer.class",
            "jr/m3g/Light.class",
            "jr/m3g/Loader.class",
            "jr/m3g/M3GParser.class",
            "jr/m3g/Mesh.class",
            "jr/m3g/Node.class",
            "jr/m3g/Object3D.class",
            "jr/m3g/PolygonMode.class",
            "jr/m3g/Raster$State.class",
            "jr/m3g/Raster.class",
            "jr/m3g/Renderer.class",
            "jr/m3g/Sprite3D.class",
            "jr/m3g/Texture2D.class",
            "jr/m3g/Transform.class",
            "jr/m3g/Transformable.class",
            "jr/m3g/TriangleStripArray.class",
            "jr/m3g/VAData.class",
            "jr/m3g/VertexBuffer.class",
            "jr/m3g/World.class",
        ),
        "groups": (),
        "members": {
            "javax/microedition/m3g/Appearance": {
                "<init>", "getCompositingMode", "getLayer", "getTexture",
                "setCompositingMode", "setLayer", "setPolygonMode",
                "setTexture",
            },
            "javax/microedition/m3g/Background": {
                "<init>", "setDepthClearEnable",
            },
            "javax/microedition/m3g/Camera": {"<init>", "setPerspective"},
            "javax/microedition/m3g/CompositingMode": {
                "<init>", "getBlending", "isDepthWriteEnabled",
                "setAlphaWriteEnable", "setBlending", "setDepthWriteEnable",
            },
            "javax/microedition/m3g/Graphics3D": {
                "bindTarget", "getInstance", "releaseTarget", "render",
                "setViewport",
            },
            "javax/microedition/m3g/Group": {
                "<init>", "addChild", "removeChild",
            },
            "javax/microedition/m3g/Image2D": {"<init>"},
            "javax/microedition/m3g/Light": {
                "<init>", "setIntensity", "setMode",
            },
            "javax/microedition/m3g/Loader": {"load"},
            "javax/microedition/m3g/Mesh": {
                "<init>", "getIndexBuffer", "getSubmeshCount",
                "getVertexBuffer",
            },
            "javax/microedition/m3g/Node": {
                "getScope", "isRenderingEnabled", "setRenderingEnable",
                "setScope",
            },
            "javax/microedition/m3g/PolygonMode": {
                "<init>", "setCulling", "setPerspectiveCorrectionEnable",
                "setShading",
            },
            "javax/microedition/m3g/Sprite3D": {
                "<init>", "setAppearance", "setCrop",
            },
            "javax/microedition/m3g/Texture2D": {
                "<init>", "getBlending", "getImage", "setBlending",
                "setFiltering", "setWrapping",
            },
            "javax/microedition/m3g/Transform": {
                "<init>", "get", "set",
            },
            "javax/microedition/m3g/Transformable": {
                "getTransform", "setScale", "setTransform", "setTranslation",
            },
            "javax/microedition/m3g/VertexBuffer": {"setDefaultColor"},
            "javax/microedition/m3g/World": {
                "<init>", "setActiveCamera", "setBackground",
            },
        },
    },
]

# back-compat aliases
NOKIA_SHIM_MEMBERS = SHIM_FAMILIES[0]["members"]
_NOKIA_SHIM_FILES = SHIM_FAMILIES[0]["files"]


def _asset_bytes(rel):
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, "assets", rel.replace("/", os.sep))]
    if getattr(sys, "frozen", False):
        candidates.insert(0, os.path.join(sys._MEIPASS, "assets", rel))
    for p in candidates:
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read()
    return None


def scan_shim_needs(jar):
    """Inspect every class' constant pool for bundled shim API references.

    Returns list of (family, needed, uncovered):
      family    - SHIM_FAMILIES entry
      needed    - set of jar entries (arc names) to inject
      uncovered - list of (class, member_or_None) the stubs do not provide
    """
    def _apply(s, pairs):
        for a, b in pairs:
            s = s.replace(a, b)
        return s

    # Gather all referenced classes/members per family in one jar pass.
    # Scan uses scan_prefix (the ORIGINAL package the game bytecode
    # mentions); injected files live at f["files"] (relocated packages).
    refs = {i: (set(), {}) for i in range(len(SHIM_FAMILIES))}
    with zipfile.ZipFile(jar) as z:
        existing = set(z.namelist())
        for e in z.infolist():
            if not e.filename.endswith(".class"):
                continue
            try:
                cf = ClassFile(z.read(e.filename))
            except Exception:
                continue
            for ent in cf.cp:
                if not ent:
                    continue
                if ent[0] == 7:
                    cn = cf.utf8(ent[1])
                    if not cn:
                        continue
                    for i, f in enumerate(SHIM_FAMILIES):
                        if cn.startswith(f.get("scan_prefix", f["prefix"])):
                            refs[i][0].add(cn)
                elif ent[0] in (9, 10, 11):
                    cn = cf.class_name(ent[1])
                    if not cn:
                        continue
                    for i, f in enumerate(SHIM_FAMILIES):
                        sp = f.get("scan_prefix", f["prefix"])
                        if cn.startswith(sp):
                            referenced, used_members = refs[i]
                            referenced.add(cn)
                            nt = cf.cp[ent[2]]
                            if nt and nt[0] == 12:
                                used_members.setdefault(
                                    cn, set()).add(cf.utf8(nt[1]))
    results = []
    for i, f in enumerate(SHIM_FAMILIES):
        referenced, used_members = refs[i]
        if not referenced:
            continue
        rw = f.get("rewrites", ())
        inv = tuple((b, a) for a, b in rw)
        # map original class name -> injected jar entry
        provided = {}
        for arc in f["files"]:
            old_cn = _apply(arc[:-6], inv)
            provided[old_cn] = arc
        needed = set()
        uncovered = []
        for cn in sorted(referenced):
            if cn + ".class" in existing:
                continue  # jar ships its own implementation
            # groups are expressed in ORIGINAL names when rewrites exist
            for grp in f["groups"]:
                if cn in grp:
                    needed |= grp
                    break
            else:
                if cn in provided:
                    needed.add(cn)
                else:
                    uncovered.append((cn, None))
        for cn, members in used_members.items():
            covered = f["members"].get(cn)
            if covered is None:
                continue
            for m in members:
                if m not in covered:
                    uncovered.append((cn, m))
        # expand needed original class names -> injected jar entries
        arcs = set()
        for cn in needed:
            if cn in provided:
                arcs.add(provided[cn])
            else:
                uncovered.append((cn, None))
        # companion classes (e.g. inner impls) always travel with the family
        if arcs:
            for arc in f.get("companions", ()):
                if arc not in existing:
                    arcs.add(arc)
        results.append((f, arcs, uncovered))
    return results


_HOOKABLE_SUPERS = (
    "javax/microedition/lcdui/Canvas",
    "javax/microedition/lcdui/game/GameCanvas",
)


def _code_attr(cf, code_bytes, max_stack, max_locals):
    blob = struct.pack(">HHI", max_stack, max_locals, len(code_bytes)) \
        + code_bytes + struct.pack(">HH", 0, 0)
    return {"name": cf.add_utf8("Code"), "data": blob}


def inject_key_hook(cf, hook):
    """Add `public void keyPressed(int k){ owner.name(k); }` if absent.

    Only for direct Canvas/GameCanvas subclasses. Straight-line code ->
    no stack map frames needed. Letters (non-game keys) are still
    delivered even when GameCanvas was constructed with suppress=true.
    """
    if cf.super_name() not in _HOOKABLE_SUPERS:
        return False
    for m in cf.methods:
        if cf.utf8(m["name"]) == "keyPressed" \
                and cf.utf8(m["desc"]) == "(I)V":
            return False
    owner, mname, mdesc = hook
    ci = cf.add_class(owner)
    ni = cf.add_utf8(mname)
    di = cf.add_utf8(mdesc)
    nati = None
    for i, e in enumerate(cf.cp):
        if e and e[0] == 12 and e[1] == ni and e[2] == di:
            nati = i
            break
    if nati is None:
        cf.cp.append([12, ni, di])
        nati = len(cf.cp) - 1
    cf.cp.append([10, ci, nati])
    ref = len(cf.cp) - 1
    # iload_1; invokestatic ref; return
    code = bytes([0x1b, 0xb8]) + struct.pack(">H", ref) + bytes([0xb1])
    method = {
        "access": 0x0001,  # ACC_PUBLIC
        "name": cf.add_utf8("keyPressed"),
        "desc": cf.add_utf8("(I)V"),
        "attrs": [_code_attr(cf, code, 1, 2)],
    }
    cf.methods.append(method)
    return True


def apply_shim_edits(payload, rewrites, retargets, key_hook):
    """Utf8 package relocation + Methodref owner retarget + key hook."""
    if not (rewrites or retargets or key_hook):
        return payload
    cf = ClassFile(payload)
    changed = False
    for ent in cf.cp:
        if ent and ent[0] == 1:
            s = ent[1]
            ns = s
            for a, b in rewrites:
                if a in ns:
                    ns = ns.replace(a, b)
            if ns != s:
                ent[1] = ns
                changed = True
    if retargets:
        rule_map = {}
        for cls, mn, ds, new_cls in retargets:
            rule_map[(cls, mn, ds)] = new_cls
        for ent in cf.cp:
            if ent and ent[0] == 10:  # Methodref only (not interface)
                cn = cf.class_name(ent[1])
                nt = cf.cp[ent[2]]
                if nt and nt[0] == 12:
                    key = (cn, cf.utf8(nt[1]), cf.utf8(nt[2]))
                    if key in rule_map:
                        ent[1] = cf.add_class(rule_map[key])
                        changed = True
    if key_hook and inject_key_hook(cf, key_hook):
        changed = True
    return cf.build() if changed else payload


def _strip_code_debug_attrs(cf, data):
    """Rebuild a Code attribute body without line/local debug tables."""
    import struct as _s
    p = 0
    maxs, maxl = _s.unpack(">HH", data[p:p + 4]); p += 4
    clen = _s.unpack(">I", data[p:p + 4])[0]; p += 4
    code = data[p:p + clen]; p += clen
    exn_len = _s.unpack(">H", data[p:p + 2])[0]; p += 2
    exn = data[p:p + 8 * exn_len]; p += 8 * exn_len
    ac = _s.unpack(">H", data[p:p + 2])[0]; p += 2
    kept = []
    for _ in range(ac):
        ni, alen = _s.unpack(">HI", data[p:p + 6])
        body = data[p + 6:p + 6 + alen]
        nm = cf.utf8(ni)
        if nm not in ("LineNumberTable", "LocalVariableTable",
                      "LocalVariableTypeTable", "StackMap",
                      "StackMapTable"):
            kept.append((ni, body))
        p += 6 + alen
    out = _s.pack(">HHI", maxs, maxl, clen) + code
    out += _s.pack(">H", exn_len) + exn
    out += _s.pack(">H", len(kept))
    for ni, body in kept:
        out += _s.pack(">HI", ni, len(body)) + body
    return out


def apply_code_patches(payload, class_name, patches, log):
    """Apply verified raw byte patches inside named methods.

    patches: (method, desc, code_offset, expect_bytes, new_bytes, label)
    Bytes are verified before writing; mismatch raises ConvertError.
    """
    cf = ClassFile(payload)
    if cf.this_name() != class_name:
        return payload
    by_method = {}
    for m, d, off, old, new, label in patches:
        by_method.setdefault((m, d), []).append((off, old, new, label))
    hit = set()
    for meth in cf.methods:
        key = (cf.utf8(meth["name"]), cf.utf8(meth["desc"]))
        rules = by_method.get(key)
        if not rules:
            continue
        for attr in meth["attrs"]:
            if cf.utf8(attr["name"]) != "Code":
                continue
            blob = bytearray(attr["data"])
            # u2 max_stack, u2 max_locals, u4 code_length => code at 8
            base = 8
            for off, old, new, label in rules:
                pos = base + off
                cur = bytes(blob[pos:pos + len(old)])
                if cur != old:
                    raise ConvertError(
                        "字节码补丁[%s] %s.%s 偏移 %d 期望 %s 实际 %s"
                        % (label, class_name, key[0], off,
                           old.hex(), cur.hex()))
                blob[pos:pos + len(new)] = new
                hit.add((key, off))
                log("字节码补丁: %s (%s.%s)" % (label,
                    class_name.split("/")[-1], key[0]))
            attr["data"] = bytes(blob)
        # patched raw code invalidates line/local debug attributes
        # (ProGuard overflows writing stale line PCs against dead code)
        for attr in meth["attrs"]:
            if cf.utf8(attr["name"]) == "Code":
                attr["data"] = _strip_code_debug_attrs(cf, attr["data"])
        meth["attrs"] = [a for a in meth["attrs"]
                         if cf.utf8(a["name"]) in ("Code",
                                                   "Exceptions",
                                                   "Deprecated",
                                                   "Synthetic")]
    if len(hit) != len(by_method):
        raise ConvertError("字节码补丁未命中方法: %s" % class_name)
    return cf.build()


def relocate_packages(payload, rewrites):
    """Back-compat wrapper (package relocation only)."""
    return apply_shim_edits(payload, rewrites, (), None)


def scan_nokia_usage(jar):
    """Back-compat wrapper: (needed class names, uncovered) for Nokia UI."""
    for f, arcs, uncovered in scan_shim_needs(jar):
        if f["prefix"] == NOKIA_UI_PREFIX:
            return {a[:-6] for a in arcs}, uncovered
    return set(), []


def landscape_hint(jar, native_w, native_h, log):
    """Heuristic: a square/narrow design size with a wider constant frame
    (e.g. 360x360 entered, but code anchors content at 320,180 of a 640x360
    landscape screen) -> suggest the wider native resolution."""
    # Only square inputs are ambiguous (landscape game with a centred
    # square viewport); explicit portrait/landscape sizes need no hint.
    if native_w != native_h or native_w <= 0:
        return
    consts = set()
    with zipfile.ZipFile(jar) as z:
        for e in z.infolist():
            if not e.filename.endswith(".class"):
                continue
            try:
                cf = ClassFile(z.read(e.filename))
            except Exception:
                continue
            for m in cf.methods:
                for at in m.get("attrs", []):
                    if cf.utf8(at["name"]) != "Code":
                        continue
                    blob = at["data"]
                    code = blob[8:8 + struct.unpack_from(">I", blob, 4)[0]]
                    for i in range(len(code) - 2):
                        if code[i] == 17:  # sipush
                            consts.add(struct.unpack_from(">h", code, i + 1)[0])
    half_h = native_h // 2
    raw = [c for c in consts
           if 240 <= c <= 1280 and c > native_w
           and (c & 1) == 0 and c // 2 in consts]
    if not raw or half_h not in consts:
        return
    # Score candidates by "side margin" evidence: a square viewport centred
    # in a wider landscape frame leaves touch/UI bounds at margin and
    # c-margin (e.g. 640 frame, 360 viewport -> constants 140 and 500).
    def score(c):
        s = 0
        margin2 = c - native_w
        if margin2 >= 0 and (margin2 & 1) == 0:
            m = margin2 // 2
            if m in consts:
                s += 1
            if c - m in consts:
                s += 1
        return s
    ranked = sorted(raw, key=lambda c: (-score(c), c))
    best = ranked[0]
    if score(best) == 0:
        return  # no supporting evidence beyond the half constant
    log("提示: 字节码中存在 %d×%d 横屏逻辑的锚点常量（中心 %d,%d），"
        % (best, native_h, best // 2, half_h))
    log("      若真机画面横向被裁切，请把原生分辨率改为 %d×%d 后重新转换。"
        % (best, native_h))


# ----------------------------- package integrity scan ----------------------
# "Installed OK but no icon" lesson (ArmyKnifeLibrary, 2026-09): a jar that
# is actually an engine/library bundle converts and loads fine, but without
# a MIDlet entry the OS registers a library module -> no home-screen icon.
# These checks answer "is this package a complete, launchable MIDlet game".

_UI_SUPERS = ("javax/microedition/lcdui/Canvas",
              "javax/microedition/lcdui/game/GameCanvas",
              NOKIA_FULLCANVAS)


def _read_manifest_headers(mf_text):
    """Parse MANIFEST.MF honouring continuation lines (leading space)."""
    lines = mf_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    folded = []
    for line in lines:
        if line.startswith(" ") and folded:
            folded[-1] += line[1:]
        else:
            folded.append(line)
    headers = {}
    for line in folded:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers


def _parse_midlet_entries(headers):
    """MIDlet-<n>: Name, icon, class  ->  [(n, name, icon, cls)] sorted."""
    out = []
    for k, v in headers.items():
        m = re.match(r"^MIDlet-(\d+)$", k)
        if not m:
            continue
        parts = v.split(",")
        out.append((
            int(m.group(1)),
            parts[0].strip() if parts else "",
            parts[1].strip() if len(parts) > 1 else "",
            parts[2].strip() if len(parts) > 2 else ""))
    out.sort()
    return out


def _extends_chain(internal, super_of, entries, targets, depth=0):
    """Walk the superclass chain (only classes inside the jar) and return
    True if any ancestor is in targets."""
    seen = set()
    cur = internal
    while cur and cur not in seen and depth < 24:
        seen.add(cur)
        sup = super_of(cur)
        if sup in targets:
            return True
        if not sup or sup not in entries:
            return False
        cur = sup
        depth += 1
    return False


def _evaluate_integrity(names, sizes, mf_text, super_of, class_entries):
    """Pure core, shared by the one-jar scan and the 3000-jar batch scan.
    names: jar entry list; sizes: name -> uncompressed size;
    super_of(internal)->super internal name or None;
    class_entries: set of internal class names present in the jar.
    Returns {fatal:[...], warnings:[...], midlets:[...], midlet_name}."""
    fatal, warnings = [], []
    headers = _read_manifest_headers(mf_text) if mf_text else {}
    mids = _parse_midlet_entries(headers)
    name_set = set(names)

    if mids:
        for n, disp, icon, cls in mids:
            label = disp or cls or ("MIDlet-%d" % n)
            if not cls:
                fatal.append("MIDlet-%d（%s）没有指定启动类" % (n, label))
                continue
            internal = cls.replace(".", "/").strip()
            if internal + ".class" not in name_set:
                fatal.append("启动类 %s 不在包内（%s）——文件残缺"
                             % (cls, label))
                continue
            sup = super_of(internal)
            if sup and sup != "javax/microedition/midlet/MIDlet" \
                    and sup not in class_entries:
                warnings.append("启动类 %s 的父类 %s 不在包内，平台缺失时无法"
                                "启动" % (cls, sup.replace("/", ".")))
            if icon:
                # MIDP icon paths are jar-root-relative; Chinese packagers
                # routinely add a leading slash ("/icon.png" vs "icon.png").
                if icon not in name_set and icon.lstrip("/") not in name_set:
                    warnings.append("图标文件 %s 缺失（%s），将显示默认图标"
                                    % (icon, label))
            else:
                warnings.append("%s 未声明图标，桌面显示默认图标" % label)
    else:
        any_midlet = any(
            super_of(c) == "javax/microedition/midlet/MIDlet"
            for c in class_entries)
        if not any_midlet:
            mn = headers.get("MIDlet-Name", "未知")
            fatal.append(
                "没有 MIDlet 启动入口（清单无 MIDlet-<n>，包内也无继承 "
                "MIDlet 的类；MIDlet-Name=%s）——这是引擎/类库包或残缺文件，"
                "黑莓会装成桌面无图标的模块" % mn)

    zero = [n for n in names if n.endswith(".class")
            and sizes.get(n, 0) == 0]
    if zero:
        warnings.append("%d 个 class 为 0 字节（如 %s），包可能不完整"
                        % (len(zero), zero[0].split("/")[-1]))
    if not class_entries:
        warnings.append("包内没有任何 class 文件")
    elif not any(_extends_chain(c, super_of, class_entries, _UI_SUPERS)
                 for c in class_entries):
        warnings.append("没有 Canvas/GameCanvas 界面类，可能不是可玩游戏")

    return {"fatal": fatal, "warnings": warnings, "midlets": mids,
            "midlet_name": headers.get("MIDlet-Name", ""),
            "runnable": not fatal}


def scan_package_integrity(jar, crc=True):
    """One-jar package completeness / launchability check (GUI info and the
    convert() gate). Opens the jar and parses every class once.
    crc=False skips the full-decompression testzip (batch path parses all
    classes anyway)."""
    result = {"fatal": [], "warnings": [], "midlets": [],
              "midlet_name": "", "runnable": False}
    try:
        zf = zipfile.ZipFile(jar)
    except Exception as ex:
        result["fatal"].append("jar 无法打开（已损坏或下载不完整）: %s" % ex)
        return result
    with zf as z:
        crc_bad = z.testzip() if crc else None
        if crc_bad:
            result["fatal"].append("zip 内容校验失败: %s（文件损坏）" % crc_bad)
        names = z.namelist()
        sizes = dict((i.filename, i.file_size) for i in z.infolist())
        if "META-INF/MANIFEST.MF" in names:
            mf_text = z.read(
                "META-INF/MANIFEST.MF").decode("utf-8", "replace")
        else:
            mf_text = ""
            result["fatal"].append(
                "缺少 META-INF/MANIFEST.MF，不是标准 MIDlet 包")
        supers, entries = {}, set()
        for n in names:
            if not n.endswith(".class"):
                continue
            internal = n[:-6]
            entries.add(internal)
            try:
                supers[internal] = ClassFile(z.read(n)).super_name()
            except Exception:
                supers[internal] = None
        ev = _evaluate_integrity(names, sizes, mf_text,
                                 supers.get, entries)
    result.update(ev)
    result["runnable"] = not result["fatal"]
    return result


def convert(jar_in, jar_out, mode, native_w, native_h,
            target_w=None, target_h=None, keymap=None, fit=None,
            crop=None, log=lambda s: None):
    if not os.path.isfile(jar_in):
        raise ConvertError("找不到 jar: %s" % jar_in)
    # 包完整性/可运行性：引擎库/残缺包直接拦截，警告仅提示
    integ = scan_package_integrity(jar_in)
    for _w in integ["warnings"]:
        log("包完整性提示: " + _w)
    if integ["fatal"]:
        raise ConvertError(
            "包完整性检查未通过，该包无法在黑莓上作为应用运行：\n"
            + "\n".join("  · " + f for f in integ["fatal"])
            + "\n请换包含游戏本体的完整 jar（通常数百 KB 以上）再转。")
    # keymap 语义：None=自动识别（默认）；False=显式关闭；非空 list=手动表。
    # 自动识别游戏按键风格（g4 教训：只认 -1~-7 厂商码的游戏拿到全键盘
    # 字母码会全部落空）。
    if keymap is None:
        scheme, digits = _detect_key_scheme(jar_in, True)
        if scheme == "vendor":
            keymap = _KEYMAP_VENDOR
            log("自动键位: 厂商游戏码方案 WASD移动+空格动作，触摸板同向滑动/按压同步可用")
        elif scheme == "digit":
            keymap = _KEYMAP_DIGIT
            log("自动键位: 黑莓键帽数字方案 WER/SDF/ZXC=1-9（E上 S左 X下 F右，"
                "D=中央5），空格=5（射击开火/自动开火游戏即跳），触摸板同向可用")
    elif keymap is False:
        keymap = []
    # 转换前预警：扫描厂商扩展 API（不阻断转换）
    scan_vendor_extensions(jar_in, log)
    scan_rapc_risks(jar_in, log)
    landscape_hint(jar_in, native_w, native_h, log)
    # 厂商/JSR 扩展 API：检测引用并准备注入内置兼容层
    shim_plans = scan_shim_needs(jar_in)
    for f, arcs, uncovered in shim_plans:
        if arcs:
            log("检测到 %s 引用，将自动注入兼容类: %s"
                % (f["label"],
                   ", ".join(sorted(a[:-6].split("/")[-1] for a in arcs))))
        for cn, mem in uncovered:
            if mem:
                log("警告: 兼容层未实现 %s.%s，该功能在真机可能异常"
                    % (cn.replace("/", "."), mem))
            else:
                log("警告: 兼容层未提供 %s，真机可能 NoClassDefFoundError"
                    % cn.replace("/", "."))
    # relocated stub families (jr.*): rewrite game bytecode references so
    # no app-defined class ends up in a protected java/javax package
    pkg_rewrites = []
    method_retargets = []
    code_patches = {}  # class -> [(method,desc,off,old,new,label)]
    key_hook = None
    for f, arcs, _uncovered in shim_plans:
        if arcs and f.get("rewrites"):
            pkg_rewrites.extend(f["rewrites"])
            for a, b in f["rewrites"]:
                log("受保护包重定位: %s -> %s" % (a, b))
        if arcs and f.get("method_retargets"):
            method_retargets.extend(f["method_retargets"])
            for _c, mn, _d, nc in f["method_retargets"]:
                log("调用重定向: %s.*%s -> %s.%s" % (_c, mn, nc, mn))
        if arcs and f.get("key_hook"):
            key_hook = f["key_hook"]
            log("画布按键钩子: keyPressed -> %s.%s"
                % (key_hook[0], key_hook[1]))
        if arcs and f.get("code_patches"):
            for cls, mn, ds, off, old, new, label in f["code_patches"]:
                code_patches.setdefault(cls, []).append(
                    (mn, ds, off, old, new, label))
    graph = _read_class_names(jar_in)
    transformed, gamecanvas = canvas_hierarchy(graph)

    if mode == "auto" and not transformed:
        raise ConvertError("jar 中未找到任何 lcdui Canvas/GameCanvas "
                           "子类，无法适配。")

    if gamecanvas:
        log("GameCanvas 游戏: %s（getGraphics/flushGraphics/getKeyStates "
            "由适配层接管）" % ", ".join(sorted(gamecanvas)))

    adapter_bytes = _adapter_bytes() if mode == "auto" else None
    counts = {}
    wrote_class = False

    with zipfile.ZipFile(jar_in) as zin, \
            zipfile.ZipFile(jar_out, "w", zipfile.ZIP_DEFLATED) as zout:
        names = set(zin.namelist())
        for e in zin.infolist():
            n = e.filename
            # remove JAR signatures - content is modified
            upper = n.upper()
            if upper.startswith("META-INF/") and (
                    upper.endswith(".SF") or upper.endswith(".RSA")
                    or upper.endswith(".DSA") or upper.endswith(".EC")):
                log("删除签名文件: %s" % n)
                continue
            if n in ("jr/ScaleCanvas.class", SIZE_RESOURCE,
                     KEYS_RESOURCE, FIT_RESOURCE, CROP_RESOURCE):
                continue  # replaced below
            payload = zin.read(n)
            if n.endswith(".class") and (pkg_rewrites
                                         or method_retargets):
                # relocate refs to jr.* stubs + retarget GCF open
                # (key hook is added LATER, transform_auto would rename
                # keyPressed into scKeyPressed)
                try:
                    payload = apply_shim_edits(
                        payload, pkg_rewrites,
                        tuple(method_retargets), None)
                except ConvertError:
                    raise
                except Exception as ex:
                    raise ConvertError("shim 字节码改写 %s 失败: %s" % (n, ex))
            if n.endswith(".class") and n[:-6] in transformed:
                try:
                    if mode == "auto":
                        out = transform_auto(payload, transformed)
                    else:
                        out, cnt = patch_constants(
                            payload,
                            {native_w: target_w, native_h: target_h})
                        counts[n] = counts.get(n, 0) + cnt
                    if out is not None:
                        payload = out
                        wrote_class = True
                except ConvertError:
                    raise
                except Exception as ex:
                    raise ConvertError("改写 %s 失败: %s" % (n, ex))
            elif n.endswith(".class") and mode == "fixed":
                try:
                    out, cnt = patch_constants(
                        payload,
                        {native_w: target_w, native_h: target_h})
                    if out is not None:
                        payload = out
                        wrote_class = True
                        counts[n] = counts.get(n, 0) + cnt
                except ConvertError:
                    raise
            if n.endswith(".class") and key_hook:
                # LAST step: transformed canvases now extend ScaleCanvas
                # (whose keyPressed is final -> must not be overridden);
                # only untouched Canvas/GameCanvas subclasses get the hook
                try:
                    payload = apply_shim_edits(
                        payload, (), (), key_hook)
                except ConvertError:
                    raise
                except Exception as ex:
                    raise ConvertError("按键钩子注入 %s 失败: %s" % (n, ex))
            if n.endswith(".class") and n[:-6] in code_patches:
                try:
                    payload = apply_code_patches(
                        payload, n[:-6], code_patches[n[:-6]], log)
                except ConvertError:
                    raise
                except Exception as ex:
                    raise ConvertError("字节码补丁 %s 失败: %s" % (n, ex))
            zout.writestr(n, payload)

        if mode == "auto":
            zout.writestr("jr/ScaleCanvas.class", adapter_bytes)
            zout.writestr(SIZE_RESOURCE, ("%d,%d" % (native_w, native_h)
                                          ).encode("ascii"))
            log("已注入 jr/ScaleCanvas.class 与 %s (%dx%d)"
                % (SIZE_RESOURCE, native_w, native_h))
            # 键位映射资源：仅在用户提供了成对映射时写入
            if keymap:
                pairs = [int(v) for v in keymap]
                if len(pairs) >= 2 and (len(pairs) & 1) == 0:
                    body = encode_keymap(pairs)
                    zout.writestr(KEYS_RESOURCE, body.encode("ascii"))
                    log("已注入键位映射 %s（%d 组）"
                        % (KEYS_RESOURCE, len(pairs) // 2))
                else:
                    log("警告: 键位映射数量不是偶数对，已忽略")
            # 铺满策略：fit=等比留黑边(默认) stretch=非等比拉满 cover=等比裁剪
            if fit:
                fit_code = {"fit": "0", "stretch": "1",
                            "cover": "2"}.get(fit)
                if fit_code is not None:
                    zout.writestr(FIT_RESOURCE, fit_code.encode("ascii"))
                    log("已注入铺满策略 %s=%s" % (FIT_RESOURCE, fit))
            # 有效内容区：x,y,w,h（原生帧坐标），框外部分（如虚拟按键区）
            # 不参与缩放铺满
            if crop:
                zout.writestr(CROP_RESOURCE, crop.encode("ascii"))
                log("已注入内容区裁剪 %s=%s" % (CROP_RESOURCE, crop))

        # 厂商/JSR 兼容层（auto/fixed 模式都可能需要）
        for f, arcs, _uncovered in shim_plans:
            injected = 0
            # 遍历 arcs（被引用的 files + 整组 companions）而非 files：
            # 内部类/实现类（如 Raster$State、M3GParser）只登记在
            # companions 里，不注入会导致预验证/真机 NoClassDefFoundError
            for arc in sorted(arcs):
                if arc in names:
                    continue
                payload = _asset_bytes(arc)
                if payload is None:
                    raise ConvertError(
                        "缺少 assets/%s（先运行对应 stub 的 build 脚本）"
                        % arc)
                zout.writestr(arc, payload)
                injected += 1
            if injected:
                log("已注入 %d 个 %s 兼容类" % (injected, f["label"]))

    # 游戏原包自带 bug 的字节码兜底（结构性 no-op，无命中不改内容）
    run_game_bytecode_patches(jar_out, log)

    if not wrote_class and mode == "fixed":
        raise ConvertError(
            "未在字节码中找到原生尺寸常量 %d/%d，请确认原始分辨率。"
            % (native_w, native_h))

    size = os.path.getsize(jar_out)
    log("输出: %s (%d 字节)" % (jar_out, size))
    if mode == "fixed":
        total = sum(counts.values())
        log("共替换 %d 处常量" % total)
        for k in sorted(counts):
            if counts[k]:
                log("  %s: %d 处" % (k, counts[k]))
    else:
        log("改写 %d 个 Canvas 层级类" % len(transformed))
    return size


# ---------------------------------------------------------------- JAD

def update_jad(jad_in, jad_out, jar_url, jar_size):
    with open(jad_in, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", "replace")
    raw_lines = [ln.rstrip("\r") for ln in text.split("\n")]
    out = []
    for ln in raw_lines:
        if not ln.strip():
            continue  # manifest/jad parsers reject stray blank lines
        key = ln.split(":", 1)[0].strip()
        if key in ("MIDlet-Jar-URL", "MIDlet-Jar-Size"):
            continue
        out.append(ln)
    out.append("MIDlet-Jar-URL: %s" % jar_url)
    out.append("MIDlet-Jar-Size: %d" % jar_size)
    body = "\r\n".join(out)
    if not body.endswith("\r\n"):
        body += "\r\n"
    with open(jad_out, "wb") as f:
        f.write(body.encode("utf-8"))
