# -*- coding: utf-8 -*-
"""diff 原 jar 和 resized jar 的 class 字节，看 patch_constants 改了哪些位置。"""
import os
import struct
import zipfile

import resizer as R

DIR = r"D:\BaiduNetdiskDownload\诺基亚\360x640分辨率jar游戏-共3948款\诺基亚 N97系列(360x640) jar 共3948款"
RESIZED = os.path.join(DIR, "龙与地下城勇士魔剑士篇_诺基亚 N97系列(360x640)_resized.jar")
ORIG = os.path.join(DIR, "龙与地下城勇士魔剑士篇_诺基亚 N97系列(360x640).jar")


def collect_bipush_sipush(jar_path):
    """返回 {class_name: [(offset, op, value), ...]}"""
    out = {}
    with zipfile.ZipFile(jar_path) as z:
        for e in z.infolist():
            if not e.filename.endswith(".class"):
                continue
            try:
                cf = R.ClassFile(z.read(e.filename))
            except Exception:
                continue
            cn = cf.this_name()
            consts = []
            for m, a, code, blob in cf.code_blobs():
                for off, op, operand in R.walk_code(code):
                    if op == 0x10:
                        v = struct.unpack(">b", operand)[0]
                        consts.append((off, op, v, cf.utf8(m["name"])))
                    elif op == 0x11:
                        v = struct.unpack(">h", operand)[0]
                        consts.append((off, op, v, cf.utf8(m["name"])))
            out[cn] = consts
    return out


def main():
    orig_consts = collect_bipush_sipush(ORIG)
    resized_consts = collect_bipush_sipush(RESIZED)

    # 1) 用户填的原生分辨率 / 目标分辨率是什么？
    # 推测：原 jar 的某个 class 的常量被改成 resized jar 的对应位置
    # 假设 patch_constants 同名同位置替换：找首个不同
    print("===== 推测用户填的 (native_w, native_h) → (target_w, target_h) =====")
    sample_changes = []
    for cn, rconsts in resized_consts.items():
        oconsts = orig_consts.get(cn, [])
        if len(oconsts) != len(rconsts):
            print("WARN: %s 常量数变化 %d → %d" % (cn, len(oconsts), len(rconsts)))
            continue
        for (oo, oop, ov, omn), (ro, rop, rv, rmn) in zip(oconsts, rconsts):
            if ov != rv:
                sample_changes.append((cn, omn, ov, rv))
    # 频率统计：哪些 (orig→new) 对出现最多
    from collections import Counter
    freq = Counter((ov, rv) for cn, omn, ov, rv in sample_changes)
    print("改动值对 (orig → new) 频率（前 10）:")
    for (ov, rv), cnt in freq.most_common(10):
        print("  %d → %d  ×%d" % (ov, rv, cnt))
    print("总改动点数:", len(sample_changes))

    # 2) 列出所有改动点（按 class 分组）
    print("\n===== 各 class 的改动点 =====")
    by_class = {}
    for cn, omn, ov, rv in sample_changes:
        by_class.setdefault(cn, []).append((omn, ov, rv))
    for cn in sorted(by_class.keys()):
        print("--- %s ---" % cn)
        for omn, ov, rv in by_class[cn][:15]:
            print("  method=%s  %d → %d" % (omn, ov, rv))

    # 3) 推测用户的输入：
    # 如果只有一个 orig → new 对，且 orig==native_w==native_h，那 patch_constants
    # 用的 replacements = {128: target_h}（因为 native_w:target_w 被覆盖）
    if len(freq) == 1:
        (ov, rv), cnt = list(freq.items())[0]
        print("\n推断：用户填 native=(%d, %d), target=(?, %d)"
              % (ov, ov, rv))
        print("       （target_w 丢失——因为 replacements[native_w] 被覆盖）")

    # 4) 原 jar 是否真的硬编码 208x208？
    print("\n===== 原 jar 中 208 出现的位置 =====")
    has_208 = False
    for cn, consts in orig_consts.items():
        for off, op, v, mn in consts:
            if v == 208:
                print("  %s method=%s  off=%d op=%02x  v=208"
                      % (cn, mn, off, op))
                has_208 = True
    if not has_208:
        print("  原字节码里没有 208 常量——游戏不硬编码尺寸")

    # 5) 原 jar 是否硬编码 360x640 或 360x360？
    print("\n===== 原 jar 中 360 / 640 出现的位置 =====")
    has_size_const = False
    for cn, consts in orig_consts.items():
        for off, op, v, mn in consts:
            if v in (360, 640):
                print("  %s method=%s  off=%d op=%02x  v=%d"
                      % (cn, mn, off, op, v))
                has_size_const = True
    if not has_size_const:
        print("  原字节码里没有 360/640 常量")

    # 6) Canvas 子类 q 的 paint 方法是否调用 getWidth/getHeight？
    print("\n===== Canvas 子类 q 的方法引用 =====")
    with zipfile.ZipFile(ORIG) as z:
        for e in z.infolist():
            if not e.filename.endswith(".class"):
                continue
            cf = R.ClassFile(z.read(e.filename))
            if cf.this_name() != "q":
                continue
            print("  super:", cf.super_name())
            for m in cf.methods:
                mn = cf.utf8(m["name"])
                md = cf.utf8(m["desc"])
                print("  declared: %s %s" % (mn, md))
            # 列出 q 内部调用的所有 methodref 的 name+desc
            print("  --- methodref calls in q ---")
            seen = set()
            for ent in cf.cp:
                if not ent or ent[0] not in (9, 10, 11):
                    continue
                owner = cf.class_name(ent[1])
                nat = cf.cp[ent[2]]
                nm = cf.utf8(nat[1])
                ds = cf.utf8(nat[2])
                key = (owner, nm, ds)
                if key in seen:
                    continue
                seen.add(key)
                if owner in ("javax/microedition/lcdui/Canvas",
                              "javax/microedition/lcdui/Display",
                              "javax/microedition/lcdui/Graphics",
                              "javax/microedition/lcdui/Displayable"):
                    print("    %s.%s%s" % (owner, nm, ds))
            break


if __name__ == "__main__":
    main()
