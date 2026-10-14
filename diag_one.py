# -*- coding: utf-8 -*-
"""诊断单个 jar 转换前后的问题。"""
import os
import sys
import zipfile
import struct

import resizer as R

DIR = r"D:\BaiduNetdiskDownload\诺基亚\360x640分辨率jar游戏-共3948款\诺基亚 N97系列(360x640) jar 共3948款"
RESIZED = os.path.join(DIR, "龙与地下城勇士魔剑士篇_诺基亚 N97系列(360x640)_resized.jar")
ORIG = os.path.join(DIR, "龙与地下城勇士魔剑士篇_诺基亚 N97系列(360x640).jar")


def dump_zip_contents(path, label):
    print("\n=========== %s ===========" % label)
    print("path: %s" % path)
    if not os.path.isfile(path):
        print("  NOT FOUND")
        return
    print("size: %d bytes" % os.path.getsize(path))
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        print("entries: %d" % len(names))
        # 列出 class 文件
        classes = [n for n in names if n.endswith(".class")]
        print(".class count: %d" % len(classes))
        # manifest
        for n in ("META-INF/MANIFEST.MF", "jr/ScaleCanvas.class", "jr-size"):
            if n in names:
                print("  has: %s" % n)
        # manifest 内容
        if "META-INF/MANIFEST.MF" in names:
            print("--- MANIFEST.MF ---")
            print(z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace"))
        # jad
        # 列出所有 class 名 + major version + super
        print("\n--- class list (name, major, super) ---")
        for n in sorted(classes):
            try:
                cf = R.ClassFile(z.read(n))
                print("  %s  major=%d  super=%s" % (
                    cf.this_name(), cf.major, cf.super_name()))
            except Exception as e:
                print("  %s  PARSE ERROR: %s" % (n, e))


def scan_one(jar):
    if not os.path.isfile(jar):
        print("NOT FOUND: %s" % jar)
        return None
    rec = R._scan_one(jar)
    print("\n=========== _scan_one: %s ==========" % os.path.basename(jar))
    print("error           :", rec["error"])
    print("max_major       :", rec["max_major"])
    print("class_version_ok:", rec["class_version_ok"])
    print("has_canvas      :", rec["has_canvas"])
    print("canvas_classes  :", rec["canvas_classes"])
    print("has_gamecanvas  :", rec["has_gamecanvas"])
    print("gamecanvas_classes:", rec["gamecanvas_classes"])
    print("has_fullcanvas  :", rec["has_fullcanvas"])
    print("has_vendor      :", rec["has_vendor"])
    print("vendor_refs    :", sorted(rec["vendor_refs"]))
    print("has_jsr (bad)   :", bool(rec["jsr_unsupported_refs"]))
    print("jsr_unsupported :", sorted(rec["jsr_unsupported_refs"]))
    print("jsr_supported   :", sorted(rec["jsr_supported_refs"]))
    print("native_size_guess:", rec["native_size_guess"])
    print("blackberry_ok   :", rec["blackberry_ok"])
    print("reason          :", rec["reason"])
    return rec


def check_resized_struct(path):
    """专门检查 resized jar 的 ScaleCanvas 注入和 Canvas 改写是否正确。"""
    print("\n=========== resized jar 结构审计 ===========")
    if not os.path.isfile(path):
        print("NOT FOUND")
        return
    with zipfile.ZipFile(path) as z:
        # 1) jr/ScaleCanvas.class 存在？
        if "jr/ScaleCanvas.class" in z.namelist():
            sc = R.ClassFile(z.read("jr/ScaleCanvas.class"))
            print("jr/ScaleCanvas.class: major=%d, this=%s, super=%s" % (
                sc.major, sc.this_name(), sc.super_name()))
        else:
            print("jr/ScaleCanvas.class: MISSING")
        # 2) jr-size 内容
        if "jr-size" in z.namelist():
            print("jr-size:", z.read("jr-size").decode("ascii"))
        else:
            print("jr-size: MISSING")
        # 3) 找出 super == jr/ScaleCanvas 的类（应该有，是改写后的 Canvas 子类）
        rewritten = []
        for n in z.namelist():
            if not n.endswith(".class"):
                continue
            try:
                cf = R.ClassFile(z.read(n))
            except Exception:
                continue
            if cf.super_name() == "jr/ScaleCanvas":
                rewritten.append(cf.this_name())
        print("rewritten Canvas subclasses (super=jr/ScaleCanvas):", rewritten)
        # 4) 这些类的 super 调用：检查 invokespecial 的目标
        for cn in rewritten:
            for n in z.namelist():
                if not n.endswith(".class"):
                    continue
                try:
                    cf = R.ClassFile(z.read(n))
                except Exception:
                    continue
                if cf.this_name() != cn:
                    continue
                print("\n--- %s super-calls ---" % cn)
                for m, a, code, blob in cf.code_blobs():
                    for off, op, operand in R.walk_code(code):
                        if op == 0xB7:  # invokespecial
                            idx = struct.unpack(">H", operand[:2])[0]
                            e = cf.cp[idx]
                            if e and e[0] in (10, 11):
                                owner = cf.class_name(e[1])
                                nat = cf.cp[e[2]]
                                nm = cf.utf8(nat[1])
                                ds = cf.utf8(nat[2])
                                print("  invokespecial %s.%s%s (in %s)"
                                      % (owner, nm, ds,
                                         cf.utf8(m["name"])))
                break


def main():
    print("===== ORIG vs RESIZED 对比 =====")
    rec_o = scan_one(ORIG)
    rec_r = scan_one(RESIZED)
    # 重点：转换前 Canvas 类的 super 链是什么
    print("\n===== 原 jar Canvas 层级 =====")
    if rec_o:
        print("transformed(原):", rec_o["canvas_classes"])
    print("\n===== resized jar 内容 =====")
    dump_zip_contents(RESIZED, "RESIZED contents")
    check_resized_struct(RESIZED)


if __name__ == "__main__":
    main()
