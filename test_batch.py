# -*- coding: utf-8 -*-
"""批量预扫描功能验证：扫 N97 目录前 50 个 jar。"""
import os
import sys
import time

import resizer as R

DIR = r"D:\BaiduNetdiskDownload\诺基亚\360x640分辨率jar游戏-共3948款\诺基亚 N97系列(360x640) jar 共3948款"
LIMIT = 50


def main():
    if not os.path.isdir(DIR):
        print("目录不存在: %s" % DIR)
        sys.exit(1)
    jars = sorted(f for f in os.listdir(DIR)
                  if f.lower().endswith(".jar"))[:LIMIT]
    print("扫描前 %d 个 jar（目录共 %d 个）\n"
          % (len(jars), sum(1 for f in os.listdir(DIR)
                             if f.lower().endswith(".jar"))))
    t0 = time.time()
    results = []
    for i, name in enumerate(jars):
        path = os.path.join(DIR, name)
        t1 = time.time()
        rec = R._scan_one(path)
        dt = time.time() - t1
        results.append(rec)
        flag = "OK " if rec["blackberry_ok"] else "BAD"
        size = ""
        if rec["native_size_guess"]:
            size = " guess=%dx%d" % rec["native_size_guess"]
        fc = " FullCanvas" if rec["has_fullcanvas"] else ""
        gc = " GameCanvas" if rec["has_gamecanvas"] else ""
        cv = " Canvas" if rec["has_canvas"] else ""
        print("[%2d/%d] %s %-44s major=%2d %-5s%-5s%s%s %s (%.2fs)"
              % (i + 1, len(jars), flag, name[:44], rec["max_major"],
                 "Vend" if rec["has_vendor"] else "----",
                 "JSR" if rec["has_jsr"] else "---",
                 cv + gc + fc, size, rec["reason"], dt))
    elapsed = time.time() - t0
    ok = sum(1 for r in results if r["blackberry_ok"])
    bad = len(results) - ok
    print("\n汇总: %d/%d 可上黑莓 9930, %d 个不可上, 共 %.1fs (%.2fs/jar)"
          % (ok, len(results), bad, elapsed, elapsed / max(1, len(results))))

    # 列出不可上的
    if bad:
        print("\n不可上的 jar 列表:")
        for r in results:
            if not r["blackberry_ok"]:
                print("  ✗ %s  (%s)" % (r["name"], r["reason"]))

    # 列出 vendor/jsr refs 分布（前几个例子）
    print("\n前 5 个不可上样本的厂商/JSR 引用（最多 8 个）:")
    n = 0
    for r in results:
        if r["blackberry_ok"]:
            continue
        n += 1
        if n > 5:
            break
        print("  --- %s ---" % r["name"])
        if r["vendor_refs"]:
            print("    vendor: %s" % ", ".join(
                sorted(r["vendor_refs"])[:8]))
        if r["jsr_refs"]:
            print("    jsr:    %s" % ", ".join(
                sorted(r["jsr_refs"])[:8]))
        if r["bad_classes"]:
            print("    bad class: %s (major=%d)"
                  % (r["bad_classes"][0][0], r["bad_classes"][0][1]))

    # 完整性：所有 rec 必须有 blackberry_ok + reason
    for r in results:
        assert isinstance(r["blackberry_ok"], bool)
        assert isinstance(r["reason"], str)
        assert isinstance(r["vendor_refs"], set)
        assert isinstance(r["jsr_refs"], set)
    print("\n完整性检查通过")


if __name__ == "__main__":
    main()
