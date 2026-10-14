# -*- coding: utf-8 -*-
"""J2ME JAR -> BlackBerry 9930 移植工具 - tkinter GUI"""
import os
import queue
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

import resizer as R


def _excepthook(etype, value, tb):
    try:
        messagebox.showerror("错误", "".join(
            traceback.format_exception_only(etype, value)))
    except Exception:
        pass


sys.excepthook = _excepthook

# 标签直接由 resizer.PRESETS 生成，追加“自定义”，避免两份列表下标错位
_PRESET_ANNOTATIONS = {
    (240, 320): " (QVGA 竖屏)",
    (320, 240): " (QVGA 横屏)",
    (480, 640): " (黑莓 9930 竖屏)",
    (640, 480): " (VGA 横屏)",
}


def _preset_label(wh):
    w, h = wh
    return "%d x %d%s" % (w, h, _PRESET_ANNOTATIONS.get(wh, ""))


PRESET_LABELS = [_preset_label(wh) for wh in R.PRESETS] + ["自定义 ..."]
CUSTOM_PRESET_INDEX = len(R.PRESETS)
# 黑莓 9930 竖屏 480x640 在 PRESETS 中的下标
BB_PORTRAIT_INDEX = R.PRESETS.index((480, 640))

# 键位适配预设：全键盘按键 -> 游戏期望的 T9/软键键码
# MIDP 数字键: '1'..'9'=49..57；诺基亚软键: 左 -6 / 右 -7
KEY_NAMES = {"space": 32, "enter": 10, "esc": 27, "escape": 27,
             "tab": 9, "del": 8, "alt": -128, "shift": -128}
KEY_PRESETS = [
    ("黑莓键帽数字（WER/SDF/ZXC=1-9，E上S左X下F右，D/空格=5）",
     "w=1,e=2,r=3,s=4,d=5,f=6,z=7,x=8,c=9,space=5,enter=-6,esc=-7"),
    ("厂商游戏码（WASD移动 空格动作 回车=左软 ESC=右软）",
     "w=-1,s=-2,a=-3,d=-4,space=-5,enter=-6,esc=-7"),
    ("仅移动+动作（WASD + 空格）",
     "w=-1,s=-2,a=-3,d=-4,space=-5"),
]
DEFAULT_KEY_PRESET = 0

KEY_MODE_AUTO = "自动识别（推荐）"
KEY_MODE_OFF = "不映射（用游戏原版按键）"
KEY_MODE_CUSTOM = "自定义映射"
KEY_MODE_CHOICES = [KEY_MODE_AUTO] + [p[0] for p in KEY_PRESETS] + [
    KEY_MODE_CUSTOM, KEY_MODE_OFF]


def parse_keymap(text):
    """'w=2,s=8,space=5' -> [119,50,115,56,32,53]。抛 ValueError 说明格式错。"""
    out = []
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "=" not in tok:
            raise ValueError("缺少等号: %s" % tok)
        a, b = tok.split("=", 1)
        out.append(_keycode(a.strip()))
        out.append(_keycode(b.strip()))
    return out


def _keycode(s):
    s = s.strip()
    if not s:
        raise ValueError("空键名")
    low = s.lower()
    if low in KEY_NAMES:
        return KEY_NAMES[low]
    if len(s) == 1 and s.isalpha():
        return ord(low)  # MIDP 全键盘字母键码为小写 ASCII
    try:
        return int(s)
    except ValueError:
        raise ValueError("无法识别的键: %s" % s)


def parse_preset(text):
    head = text.split("(")[0]
    parts = head.lower().replace("x", " ").split()
    return int(parts[0]), int(parts[1])


class _FlowFrame(ttk.Frame):
    """按可用宽度自动换行排列子控件的容器。

    子控件以本 Frame 为父创建后不要再调用 pack/grid，布局由
    <Configure> 驱动（窗口拉到多窄都不会被裁切，只会折行变高）。
    """

    def __init__(self, master, gapx=8, gapy=4, **kw):
        super().__init__(master, **kw)
        self.gapx = gapx
        self.gapy = gapy
        self._height = 1
        # 本轮被强制限宽换行的 Label（下轮先还原再重新判定）
        self._wrapped = []
        self.bind("<Configure>", self._relayout)

    def _relayout(self, _event=None):
        width = self.winfo_width()
        if width <= 1:
            return
        # 还原上一轮的限宽，让 Label 重新按内容报告自然尺寸
        for lbl in self._wrapped:
            try:
                lbl.configure(wraplength=0)
            except tk.TclError:
                pass
        self._wrapped = []

        def need(w):
            w.update_idletasks()
            return w.winfo_reqwidth(), w.winfo_reqheight()

        x = 0
        y = 0
        rowh = 0
        for c in self.winfo_children():
            reqw, reqh = need(c)
            is_label = isinstance(c, (tk.Label, ttk.Label))
            if x > 0 and x + reqw > width:
                # 当前行放不下：非标签控件换到新行；标签也换行，
                # 若整行都不够则在下方按整行宽度折行
                x = 0
                y += rowh + self.gapy
                rowh = 0
            if is_label and reqw > width - x:
                avail = max(40, width - x)
                c.configure(wraplength=avail)
                self._wrapped.append(c)
                reqw, reqh = need(c)
            c.place(x=x, y=y, width=reqw, height=reqh)
            x += reqw + self.gapx
            if reqh > rowh:
                rowh = reqh
        total = y + rowh
        if total != self._height:
            self._height = total
            self.configure(height=total)


class _ScrollFrame(ttk.Frame):
    """纵向可滚动容器。

    - body 宽度始终等于可视区宽度，fill="x"/sticky="ew" 正常生效；
    - body 高度至少撑满可视区，内部 fill="both", expand=True 的区域
      （日志、结果表）在窗口拉高时跟着变大；
    - 内容超出时自动出现纵向滚动条，鼠标滚轮可滚；
    - 通过 bind_wrap() 登记的 Label，wraplength 会随宽度自动更新，
      长提示文字自动折行而不是撑破窗口。
    """

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=bg)
        self.vsb = ttk.Scrollbar(self, orient="vertical",
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window(
            (0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._sync_region)
        self.canvas.bind("<Configure>", self._sync_view)
        self._wrap_labels = []
        self.bind("<Enter>", self._wheel_on)
        self.bind("<Leave>", self._wheel_off)

    def bind_wrap(self, label, pad=30):
        self._wrap_labels.append((label, pad))

    def _sync_region(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_view(self, e):
        w = e.width
        self.canvas.itemconfigure(self._win, width=w)
        self.body.update_idletasks()
        self.canvas.itemconfigure(
            self._win, height=max(self.body.winfo_reqheight(), e.height))
        for lbl, pad in self._wrap_labels:
            if w - pad > 60:
                lbl.configure(wraplength=w - pad)
        self._sync_region()

    def _wheel_on(self, _e=None):
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _wheel_off(self, _e=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("J2ME 游戏 → 黑莓 9930 移植工具")
        self.geometry("860x740")
        # 任意形状都可以：内容会折行 + 滚动，不再靠大窗口才能看全
        self.minsize(440, 320)
        self.msgq = queue.Queue()
        self._build()
        self._fixed_toggle()
        self._keymap_mode_change()
        self._fit_initial_size()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(100, self._drain_log)
        # 后台探一次设备，省去手动点“检测设备”
        self.after(400, self.check_device)

    # ------------------------------------------------------------ style
    def _setup_style(self):
        style = ttk.Style(self)
        font_ui = ("Microsoft YaHei UI", 9)
        style.configure(".", font=font_ui)
        style.configure("TButton", padding=(10, 5))
        style.configure("TLabel", padding=1)
        style.configure("TLabelframe.Label",
                        font=("Microsoft YaHei UI", 9, "bold"))
        # 主操作按钮
        style.configure("Primary.TButton",
                        font=("Microsoft YaHei UI", 10, "bold"),
                        padding=(16, 8))
        style.configure("Hint.TLabel", foreground="#777777",
                        font=("Microsoft YaHei UI", 8))
        style.configure("Info.TLabel",
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Ok.TLabel", foreground="#0a7d0a")
        style.configure("Warn.TLabel", foreground="#b06a00")
        style.configure("Bad.TLabel", foreground="#b00020")

    def _fit_initial_size(self):
        """按内容实际需求给一个合适的初始窗口，并居中于屏幕。

        不写死高度：先让控件按内容计算需求，再夹到屏幕范围内；
        窗口拉小后内容靠折行/滚动保持可见。
        """
        self.update_idletasks()
        reqw = max(self.winfo_reqwidth(), 760)
        reqh = max(self.winfo_reqheight(), 620)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(reqw, sw - 40)
        h = min(reqh, sh - 80)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 4)
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))

    # ------------------------------------------------------------ UI
    def _build(self):
        self._setup_style()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(10, 6))

        page1 = _ScrollFrame(self.nb)
        self.nb.add(page1, text="  游戏转换 / 装机  ")
        self._build_convert(page1.body, page1)

        page2 = _ScrollFrame(self.nb)
        self.nb.add(page2, text="  批量兼容性预扫描  ")
        self._build_batch(page2.body, page2)

        self.status = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status,
                  relief="sunken", anchor="w").pack(
            fill="x", side="bottom")

    def _build_convert(self, root, sf):
        PAD = {"padx": 8, "pady": 3}

        # ① 游戏文件 ------------------------------------------------
        f1 = ttk.LabelFrame(root, text="① 游戏 JAR")
        f1.pack(fill="x", **PAD)
        self.jar_var = tk.StringVar()
        ttk.Entry(f1, textvariable=self.jar_var).grid(
            row=0, column=0, sticky="ew", padx=(8, 6), pady=8)
        ttk.Button(f1, text="浏览…", width=10,
                   command=self.pick_jar).grid(row=0, column=1, padx=(0, 8))
        f1.columnconfigure(0, weight=1)

        # ② 适配设置（单列流式：窗口多窄都只折行、不裁切）------------
        f2 = ttk.LabelFrame(root, text="② 适配设置（全部自动识别，通常不用改）")
        f2.pack(fill="x", **PAD)
        f2.columnconfigure(0, weight=1)

        # 原生分辨率：单选项 + 手动宽高，宽度不足自动折行
        r = _FlowFrame(f2)
        r.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))
        self.auto_size = tk.BooleanVar(value=True)
        ttk.Label(r, text="原生分辨率:")
        ttk.Radiobutton(r, text="自动识别（图片/JAD/诺基亚尺寸头）",
                        variable=self.auto_size, value=True,
                        command=self._auto_size_toggle)
        ttk.Radiobutton(r, text="手动", variable=self.auto_size,
                        value=False, command=self._auto_size_toggle)
        ttk.Label(r, text="宽")
        self.nw_var = tk.StringVar(value="240")
        self.nw_entry = ttk.Entry(r, textvariable=self.nw_var, width=6)
        ttk.Label(r, text="高")
        self.nh_var = tk.StringVar(value="320")
        self.nh_entry = ttk.Entry(r, textvariable=self.nh_var, width=6)

        # 键位
        r = ttk.Frame(f2)
        r.grid(row=1, column=0, sticky="ew", padx=8, pady=2)
        r.columnconfigure(0, weight=1)
        ttk.Label(r, text="键位映射:").grid(row=0, column=0, sticky="w")
        self.keymap_mode_var = tk.StringVar(value=KEY_MODE_AUTO)
        self.keymap_mode = ttk.Combobox(
            r, textvariable=self.keymap_mode_var,
            values=KEY_MODE_CHOICES, state="readonly")
        self.keymap_mode.grid(row=1, column=0, sticky="ew")
        self.keymap_mode.bind("<<ComboboxSelected>>",
                              lambda _e: self._keymap_mode_change())
        self.keymap_text_var = tk.StringVar(
            value=KEY_PRESETS[DEFAULT_KEY_PRESET][1])
        self.keymap_entry = ttk.Entry(r, textvariable=self.keymap_text_var)
        self.keymap_entry.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        hint = ttk.Label(
            r, style="Hint.TLabel",
            text="自定义格式：源键=目标键，逗号分隔；字母直接写(w/a/s/d)，"
                 "功能键 space/enter/esc，数字 1-9，左/右软键 -6/-7")
        hint.grid(row=3, column=0, sticky="ew")
        sf.bind_wrap(hint, pad=40)

        # 选项行：同步 JAD + 高级固定补丁（自动折行）
        r = _FlowFrame(f2)
        r.grid(row=2, column=0, sticky="ew", padx=8, pady=(6, 2))
        self.sync_jad = tk.BooleanVar(value=True)
        ttk.Checkbutton(r, text="转换后同步更新同目录 JAD",
                        variable=self.sync_jad)
        self.fixed_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="高级：固定分辨率补丁模式",
                        variable=self.fixed_on,
                        command=self._fixed_toggle)
        ttk.Label(r, style="Hint.TLabel",
                  text="（直接改字节码常量，不注入缩放引擎）")

        # 高级：固定补丁参数（默认隐藏）
        self.fixed_frame = ttk.Frame(f2)
        self.fixed_frame.grid(row=3, column=0, sticky="ew",
                              padx=8, pady=(0, 4))
        ff = _FlowFrame(self.fixed_frame)
        ff.pack(fill="x")
        self.mode_var = tk.StringVar(value="auto")
        self.preset_var = tk.StringVar(
            value=PRESET_LABELS[BB_PORTRAIT_INDEX])
        ttk.Label(ff, text="目标尺寸预设:")
        self.preset = ttk.Combobox(ff, textvariable=self.preset_var,
                                   values=PRESET_LABELS, width=28,
                                   state="readonly")
        self.preset.bind("<<ComboboxSelected>>", self._preset_change)
        ttk.Label(ff, text="宽")
        self.tw_var = tk.StringVar(value="480")
        self.tw_entry = ttk.Entry(ff, textvariable=self.tw_var, width=6)
        ttk.Label(ff, text="高")
        self.th_var = tk.StringVar(value="640")
        self.th_entry = ttk.Entry(ff, textvariable=self.th_var, width=6)

        # 识别信息条：按钮一行，识别结果整行显示在下方（任意宽度可折行）
        r = ttk.Frame(f2)
        r.grid(row=4, column=0, sticky="ew", padx=8, pady=(4, 8))
        r.columnconfigure(0, weight=1)
        ttk.Button(r, text="读取游戏信息",
                   command=self.read_game_info).grid(row=0, column=0,
                                                      sticky="w")
        self.info_var = tk.StringVar(value="尚未读取（选择 JAR 后会自动识别）")
        self.info_lbl = ttk.Label(r, textvariable=self.info_var,
                                  style="Info.TLabel")
        self.info_lbl.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        sf.bind_wrap(self.info_lbl, pad=40)

        # ③ 输出 / 执行 ---------------------------------------------
        f3 = ttk.LabelFrame(root, text="③ 输出与执行")
        f3.pack(fill="x", **PAD)
        f3.columnconfigure(1, weight=1)

        ttk.Label(f3, text="转换后 JAR:").grid(
            row=0, column=0, sticky="e", padx=(8, 4), pady=(8, 2))
        r = ttk.Frame(f3)
        r.grid(row=0, column=1, sticky="ew", pady=(8, 2))
        r.columnconfigure(0, weight=1)
        self.out_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.out_var).grid(
            row=0, column=0, sticky="ew")
        ttk.Button(r, text="另存为…", width=8,
                   command=self.pick_out).grid(row=0, column=1,
                                               padx=(6, 0))

        ttk.Label(f3, text="黑莓模块名:").grid(
            row=1, column=0, sticky="e", padx=(8, 4), pady=2)
        r = ttk.Frame(f3)
        r.grid(row=1, column=1, sticky="ew", pady=2)
        r.columnconfigure(0, weight=1)
        self.codname_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.codname_var,
                  width=18).grid(row=0, column=0, sticky="w")
        hint = ttk.Label(r, text="（留空自动取文件名，只能字母数字下划线）",
                         style="Hint.TLabel")
        hint.grid(row=1, column=0, sticky="ew")
        sf.bind_wrap(hint, pad=140)
        self.device_var = tk.StringVar(value="设备状态：检测中…")
        ttk.Label(r, textvariable=self.device_var).grid(
            row=2, column=0, sticky="w", pady=(4, 0))
        self.dev_btn = ttk.Button(r, text="重新检测",
                                  command=self.check_device)
        self.dev_btn.grid(row=3, column=0, sticky="w", pady=(2, 0))

        # 主操作按钮组（宽度不够自动折成多行）
        r = _FlowFrame(f3)
        r.grid(row=2, column=0, columnspan=2, sticky="ew",
               padx=8, pady=(8, 2))
        self.bb_auto_btn = ttk.Button(
            r, style="Primary.TButton",
            text="一键：转换 → 打包 COD → 装机",
            command=lambda: self.build_cod("auto"))
        self.bb_btn = ttk.Button(
            r, text="仅打包 COD",
            command=lambda: self.build_cod(False))
        self.run_btn = ttk.Button(
            r, text="只转换 JAR（不打包）", command=self.convert)
        self.bb_load_btn = ttk.Button(
            r, text="已有 COD 直接装机",
            command=lambda: self.build_cod(True, rebuild=False))
        hint = ttk.Label(
            f3, style="Hint.TLabel",
            text="主按钮：USB 连着手机就自动装机，没连则只生成 COD；"
                 "回车=左软键，DEL/ESC=右软键，已内置无需配置。")
        hint.grid(row=3, column=0, columnspan=2, sticky="ew",
                  padx=8, pady=(0, 8))
        sf.bind_wrap(hint, pad=40)

        # 日志 ------------------------------------------------------
        box = ttk.LabelFrame(root, text="日志")
        box.pack(fill="both", expand=True, **PAD)
        self.logbox = tk.Text(box, height=9, wrap="word",
                              font=("Consolas", 9), relief="flat",
                              background="#fafafa")
        sb = ttk.Scrollbar(box, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.logbox.pack(fill="both", expand=True, padx=(8, 0), pady=8)

    def _build_batch(self, parent, sf):
        """批量预扫描页面：目录 + 分组操作 + 结果 Treeview。"""
        pad = {"padx": 8, "pady": 3}

        box = ttk.LabelFrame(parent, text="扫描目录")
        box.pack(fill="x", **pad)
        box.columnconfigure(0, weight=1)
        self.batch_dir_var = tk.StringVar()
        ttk.Entry(box, textvariable=self.batch_dir_var).grid(
            row=0, column=0, sticky="ew", padx=(8, 6), pady=8)
        ttk.Button(box, text="浏览…",
                   command=self.pick_batch_dir).grid(
            row=0, column=1, padx=(0, 8))
        # 选项按钮自动折行，窗口窄也不会被切掉
        opt = _FlowFrame(box)
        opt.grid(row=1, column=0, columnspan=2, sticky="ew",
                 padx=8, pady=(0, 4))
        self.batch_recursive = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="递归子目录",
                        variable=self.batch_recursive)
        self.batch_btn = ttk.Button(
            opt, text="开始扫描", style="Primary.TButton",
            command=self.batch_scan)
        # 进度行：进度条 + 状态文字（流式：宽度不够时状态文字自动
        # 换到进度条下方并按宽度折行，绝不被切）
        prow = _FlowFrame(box)
        prow.grid(row=2, column=0, columnspan=2, sticky="ew",
                  padx=8, pady=(0, 8))
        self.scan_pb = ttk.Progressbar(prow, length=240,
                                       mode="indeterminate")
        self.batch_status = tk.StringVar(value="就绪：选择目录后点「开始扫描」")
        ttk.Label(prow, textvariable=self.batch_status,
                  style="Info.TLabel")

        ops = ttk.LabelFrame(parent, text="选中行操作（先在列表中点选）")
        ops.pack(fill="x", **pad)
        r1 = _FlowFrame(ops)
        r1.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(r1, text="选择:", foreground="#555555")
        ttk.Button(r1, text="全选「可上」",
                   command=lambda: self._batch_select_by_tag("ok"))
        ttk.Button(r1, text="全选「不可上」",
                   command=lambda: self._batch_select_by_tag("bad"))
        ttk.Button(r1, text="反选",
                   command=self._batch_invert_selection)
        ttk.Button(r1, text="全不选",
                   command=lambda: self.batch_tree.selection_set(()))
        r2 = _FlowFrame(ops)
        r2.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(r2, text="文件:", foreground="#555555")
        ttk.Button(r2, text="复制到…", command=self._batch_copy)
        ttk.Button(r2, text="移动到…", command=self._batch_move)
        ttk.Button(r2, text="删除…", command=self._batch_delete)
        ttk.Button(r2, text="复制完整路径",
                   command=self._batch_copy_paths)

        conv = ttk.LabelFrame(parent, text="批量转换（自动适配，失败自动跳过并记录）")
        conv.pack(fill="x", **pad)
        conv.columnconfigure(1, weight=1)
        ttk.Label(conv, text="输出目录:").grid(
            row=0, column=0, sticky="e", padx=(8, 4), pady=(6, 2))
        r = ttk.Frame(conv)
        r.grid(row=0, column=1, sticky="ew", pady=(6, 2))
        r.columnconfigure(0, weight=1)
        self.batch_outdir_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.batch_outdir_var).grid(
            row=0, column=0, sticky="ew")
        ttk.Button(r, text="浏览…",
                   command=self.pick_batch_outdir).grid(
            row=0, column=1, padx=(6, 0))
        self.batch_conv_bad = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            conv, text="预扫描判「不可上」的也强制尝试转换",
            variable=self.batch_conv_bad).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        hint = ttk.Label(
            conv, style="Hint.TLabel",
            text="（不勾则直接跳过并记入报告；同名输出自动加序号，"
                 "不会覆盖原游戏）")
        hint.grid(row=2, column=0, columnspan=2, sticky="ew",
                  padx=8, pady=(0, 2))
        sf.bind_wrap(hint, pad=40)
        r = _FlowFrame(conv)
        r.grid(row=3, column=0, columnspan=2, sticky="ew",
               padx=8, pady=(2, 6))
        self.batch_conv_sel_btn = ttk.Button(
            r, text="批量转换：选中行",
            command=lambda: self.batch_convert("selected"))
        self.batch_conv_ok_btn = ttk.Button(
            r, style="Primary.TButton",
            text="批量转换：全部「可上」",
            command=lambda: self.batch_convert("ok"))
        self.batch_pb = ttk.Progressbar(r, length=220, mode="determinate")
        self.batch_pb_var = tk.StringVar(value="")
        ttk.Label(r, textvariable=self.batch_pb_var, style="Hint.TLabel")

        tree_box = ttk.LabelFrame(
            parent, text="扫描结果（双击行查看完整诊断）")
        tree_box.pack(fill="both", expand=True, **pad)
        cols = ("name", "status", "integrity", "major", "vendor", "jsr",
                "canvas", "size", "reason")
        labels = ("文件名", "状态", "完整性", "类版本", "厂商扩展", "JSR 扩展",
                  "Canvas 类型", "推测分辨率", "原因")
        widths = (200, 70, 70, 55, 65, 65, 140, 85, 220)
        anchors = ("w", "center", "center", "center", "center", "center",
                   "w", "center", "w")
        # 窄窗口时横向滚动；宽窗口时首列与原因列吃掉多余宽度
        stretch_cols = {"name", "reason"}
        inner = ttk.Frame(tree_box)
        inner.pack(fill="both", expand=True, padx=8, pady=8)
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)
        self.batch_tree = ttk.Treeview(
            inner, columns=cols, show="headings")
        for c, lab, w, a in zip(cols, labels, widths, anchors):
            self.batch_tree.heading(c, text=lab)
            self.batch_tree.column(
                c, width=w, minwidth=max(50, int(w * 0.6)),
                anchor=a, stretch=(c in stretch_cols))
        vsb = ttk.Scrollbar(inner, orient="vertical",
                            command=self.batch_tree.yview)
        hsb = ttk.Scrollbar(inner, orient="horizontal",
                            command=self.batch_tree.xview)
        self.batch_tree.configure(yscrollcommand=vsb.set,
                                  xscrollcommand=hsb.set)
        hsb.grid(row=1, column=0, sticky="ew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.batch_tree.grid(row=0, column=0, sticky="nsew")
        self.batch_tree.bind("<Double-1>", self._batch_row_detail)
        self.batch_tree.tag_configure("ok", foreground="#0a7d0a")
        self.batch_tree.tag_configure("bad", foreground="#b00020")

        self.batch_results = []

    # --------------------------------------------------------- state
    def _all_action_buttons(self):
        return [getattr(self, n, None) for n in
                ("run_btn", "bb_auto_btn", "bb_btn", "bb_load_btn")]

    def _actions_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for b in self._all_action_buttons():
            if b is not None:
                b.configure(state=state)

    def pick_jar(self):
        p = filedialog.askopenfilename(
            title="选择 J2ME 游戏 jar",
            filetypes=[("JAR 文件", "*.jar"), ("所有文件", "*.*")])
        if not p:
            return
        self.jar_var.set(p)
        base = os.path.splitext(p)[0]
        self.out_var.set(base + "_resized.jar")
        if os.path.isfile(base + ".jad"):
            self.sync_jad.set(True)
        # 自动建议模块名
        if not self.codname_var.get().strip():
            import re
            stem = os.path.splitext(os.path.basename(p))[0]
            guess = re.sub(r"[^A-Za-z0-9_]", "", stem)[:20]
            if guess:
                self.codname_var.set(guess)
        # 后台自动识别一次
        self.read_game_info()

    def pick_out(self):
        p = filedialog.asksaveasfilename(
            title="输出 jar", defaultextension=".jar",
            filetypes=[("JAR 文件", "*.jar")])
        if p:
            self.out_var.set(p)

    def _auto_size_toggle(self):
        state = "disabled" if self.auto_size.get() else "normal"
        self.nw_entry.configure(state=state)
        self.nh_entry.configure(state=state)

    def _fixed_toggle(self):
        on = self.fixed_on.get()
        self.mode_var.set("fixed" if on else "auto")
        if on:
            self.fixed_frame.grid()
        else:
            self.fixed_frame.grid_remove()
        state = "normal" if on else "disabled"
        combo_state = "readonly" if on else "disabled"
        self.preset.configure(state=combo_state)
        self.tw_entry.configure(state=state)
        self.th_entry.configure(state=state)

    def _keymap_mode_change(self):
        v = self.keymap_mode_var.get()
        if v == KEY_MODE_AUTO or v == KEY_MODE_OFF:
            self.keymap_entry.configure(state="disabled")
        elif v == KEY_MODE_CUSTOM:
            self.keymap_entry.configure(state="normal")
        else:
            # 内置预设：把对应映射显示出来但只读
            for name, spec in KEY_PRESETS:
                if name == v:
                    self.keymap_text_var.set(spec)
                    break
            self.keymap_entry.configure(state="disabled")

    def _current_keymap(self):
        """None=自动识别；False=关闭；list=显式映射。解析失败抛 ValueError。"""
        v = self.keymap_mode_var.get()
        if v == KEY_MODE_AUTO:
            return None
        if v == KEY_MODE_OFF:
            return False
        pairs = parse_keymap(self.keymap_text_var.get())
        if not pairs:
            raise ValueError("键位映射表为空")
        return pairs

    def _preset_change(self, _=None):
        if self.preset_var.get().startswith("自定义"):
            self.preset.configure(state="normal")
            return
        try:
            w, h = parse_preset(self.preset_var.get())
            self.tw_var.set(str(w))
            self.th_var.set(str(h))
        except Exception:
            pass

    def log(self, msg):
        self.msgq.put(str(msg))

    def _drain_log(self):
        while True:
            try:
                msg = self.msgq.get_nowait()
            except queue.Empty:
                break
            if isinstance(msg, tuple) and msg:
                tag = msg[0]
                if tag == "__result__":
                    ok, text = msg[1], msg[2]
                    self._actions_enabled(True)
                    self.status.set("转换完成" if ok else "转换失败")
                    if ok:
                        self.logbox.insert("end", text + "\n")
                        messagebox.showinfo("完成", text)
                    else:
                        self.logbox.insert("end", "错误: " + text + "\n")
                        messagebox.showerror("转换失败", text)
                elif tag == "__bb_result__":
                    ok, text = msg[1], msg[2]
                    self._actions_enabled(True)
                    self.dev_btn.configure(state="normal")
                    self.status.set("COD 构建完成" if ok else "COD 构建失败")
                    if ok:
                        self.logbox.insert("end", text + "\n")
                        messagebox.showinfo("完成", text)
                    else:
                        self.logbox.insert("end", "错误: " + text + "\n")
                        messagebox.showerror("构建失败", text)
                elif tag == "__dev_result__":
                    connected = msg[1]
                    self.dev_btn.configure(state="normal")
                    if connected:
                        self.device_var.set("设备状态：已连接 ✓")
                    else:
                        self.device_var.set("设备状态：未连接（仅打包）")
                elif tag == "__info__":
                    jar, info, err = msg[1], msg[2], msg[3]
                    if jar == self.jar_var.get().strip():
                        self._show_game_info(info, err)
                elif tag == "__batch_prog__":
                    i, n, name = msg[1], msg[2], msg[3]
                    # 第一条进度到来时，从滚动动画切换为确定进度条
                    if str(self.scan_pb.cget("mode")) == "indeterminate":
                        self.scan_pb.stop()
                        self.scan_pb.configure(mode="determinate",
                                               maximum=n, value=0)
                    self.scan_pb.configure(value=i + 1)
                    self.batch_status.set(
                        "扫描中: %d/%d  %s" % (i + 1, n, name))
                elif tag == "__batch_done__":
                    self._batch_done(msg[1])
                elif tag == "__batch_fileop__":
                    self._batch_fileop_done(msg[1], msg[2], msg[3], msg[4])
                elif tag == "__batch_err__":
                    self._scan_finished()
                    self.batch_status.set("扫描失败")
                    messagebox.showerror("扫描失败", msg[1])
                elif tag == "__bconv_prog__":
                    i, n, name = msg[1], msg[2], msg[3]
                    self.batch_pb.configure(maximum=n, value=i)
                    self.batch_pb_var.set("%d/%d  %s" % (i, n, name))
                    self.batch_status.set("批量转换 %d/%d: %s"
                                          % (i, n, name))
                elif tag == "__bconv_done__":
                    self._batch_convert_done(msg[1])
                else:
                    self.logbox.insert("end", str(msg) + "\n")
            else:
                self.logbox.insert("end", str(msg) + "\n")
        self.logbox.see("end")
        self.after(100, self._drain_log)

    def _resolve_native(self, jar):
        """Native design size: asset/JAD auto-detect or the manual entries."""
        if self.auto_size.get():
            d = R.detect_native_from_assets(jar)
            if not d:
                raise ValueError("无法自动识别分辨率，请选“手动”并填写")
            w, h, why = d
            self.after(0, lambda: (
                self.nw_var.set(str(w)), self.nh_var.set(str(h))))
            self.log("自动识别原生分辨率: %dx%d（%s）" % (w, h, why))
            return w, h
        return int(self.nw_var.get()), int(self.nh_var.get())

    # ----------------------------------------------------- game info
    def read_game_info(self):
        jar = self.jar_var.get().strip()
        if not jar or not os.path.isfile(jar):
            messagebox.showwarning("提示", "请先选择存在的 JAR 文件")
            return
        self.info_var.set("识别中…")
        self.info_lbl.configure(style="Info.TLabel")

        def work():
            info = None
            err = None
            try:
                graph = R._read_class_names(jar)
                canvases, gamecanvas = R.canvas_hierarchy(graph)
                size = R.detect_native_from_assets(jar)
                scheme = R.detect_key_scheme(jar)
                risks = []
                R.scan_rapc_risks(jar, risks.append)
                integrity = R.scan_package_integrity(jar)
                info = {
                    "canvases": sorted(canvases),
                    "gamecanvas": sorted(gamecanvas),
                    "size": size,
                    "scheme": scheme,
                    "risks": risks,
                    "integrity": integrity,
                }
            except Exception as ex:
                err = str(ex)
            self.msgq.put(("__info__", jar, info, err))

        threading.Thread(target=work, daemon=True).start()

    def _show_game_info(self, info, err):
        if err is not None:
            self.info_var.set("识别失败：%s" % err)
            self.info_lbl.configure(style="Bad.TLabel")
            return
        parts = []
        size = info["size"]
        if size:
            w, h, why = size
            orient = "横屏" if w > h else ("竖屏" if h > w else "方形")
            parts.append("原生 %dx%d %s" % (w, h, orient))
        else:
            parts.append("分辨率未识别")
        scheme = info["scheme"]
        parts.append({"vendor": "按键：厂商码 WASD方案",
                      "digit": "按键：数字键 键帽方案",
                      None: "按键：未识别（用自动/手动映射）"}[scheme])
        if info["gamecanvas"]:
            parts.append("GameCanvas")
        integ = info["integrity"]
        if integ["fatal"]:
            parts.append("✗ 包不可运行（%d 项硬伤）" % len(integ["fatal"]))
        elif integ["warnings"]:
            parts.append("包可运行（%d 项提示）" % len(integ["warnings"]))
        else:
            parts.append("包完整可运行")
        risks = info["risks"]
        if risks:
            parts.append("⚠ 有类过大打 COD 风险")
        else:
            parts.append("无 COD 风险")
        if integ["fatal"] or risks:
            self.info_lbl.configure(style="Bad.TLabel")
        else:
            self.info_lbl.configure(style="Ok.TLabel")
        self.info_var.set("　·　".join(parts))

        # 详细信息进日志
        self.log("—— 游戏信息 ——")
        if size:
            self.log("原生分辨率: %dx%d（%s）" % (size[0], size[1], size[2]))
        self.log("Canvas 类: %s"
                 % (", ".join(info["canvases"]) or "(无)"))
        if info["gamecanvas"]:
            self.log("GameCanvas 类（缩放引擎已支持）: %s"
                     % ", ".join(info["gamecanvas"]))
        self.log("按键风格: %s"
                 % {"vendor": "厂商游戏码(-1~-7)",
                    "digit": "数字键(2/4/6/8)",
                    None: "未识别"}[scheme])
        for f in integ["fatal"]:
            self.log("包完整性[致命]: " + f)
        for w in integ["warnings"]:
            self.log("包完整性[提示]: " + w)
        if not integ["fatal"] and not integ["warnings"]:
            self.log("包完整性: 启动入口/资源齐全")
        for risk in risks:
            self.log(risk)

    # -------------------------------------------------------- convert
    def convert(self):
        jar = self.jar_var.get().strip()
        out = self.out_var.get().strip()
        if not jar or not os.path.isfile(jar):
            messagebox.showwarning("提示", "请先选择 JAR 文件")
            return
        if not out:
            messagebox.showwarning("提示", "请指定输出路径")
            return
        try:
            nw, nh = self._resolve_native(jar)
        except ValueError as ex:
            messagebox.showwarning("提示", str(ex))
            return
        try:
            keymap = self._current_keymap()
        except ValueError as ex:
            messagebox.showwarning("键位格式错误", str(ex))
            return
        fixed = self.fixed_on.get()
        mode = "fixed" if fixed else "auto"
        tw = th = None
        if fixed:
            try:
                tw, th = int(self.tw_var.get()), int(self.th_var.get())
            except ValueError:
                messagebox.showwarning("提示", "目标分辨率必须是整数")
                return
            if (tw, th) == (nw, nh):
                messagebox.showwarning("提示", "目标与原生分辨率相同")
                return
            # 键位注入依赖缩放引擎，固定补丁模式不生效
            keymap = False

        self._actions_enabled(False)
        self.status.set("转换中…")

        def work():
            try:
                size = R.convert(jar, out, mode, nw, nh, tw, th,
                                 keymap=keymap, log=self.log)
                if self.sync_jad.get():
                    base = os.path.splitext(jar)[0]
                    src_jad = base + ".jad"
                    if os.path.isfile(src_jad):
                        dst_jad = os.path.splitext(out)[0] + ".jad"
                        R.update_jad(src_jad, dst_jad,
                                     os.path.basename(out), size)
                        self.log("JAD 已更新: %s" % dst_jad)
                    else:
                        self.log("源 JAR 旁未找到 JAD，跳过 JAD 更新")
                self.msgq.put(
                    ("__result__", True, "转换完成: %s" % out))
            except Exception as ex:
                self.msgq.put(("__result__", False, str(ex)))

        threading.Thread(target=work, daemon=True).start()

    def check_device(self):
        """Probe USB for a BlackBerry; runs in a background thread."""
        self.dev_btn.configure(state="disabled")
        self.device_var.set("设备状态：检测中…")

        def work():
            try:
                import build_bb
                ok = build_bb.device_connected()
            except Exception:
                ok = False
            self.msgq.put(("__dev_result__", ok))

        threading.Thread(target=work, daemon=True).start()

    def build_cod(self, load="auto", rebuild=True):
        """One-click BlackBerry pipeline (auto mode).

        load: "auto" -> install if a device answers USB;
              False -> build COD only;
              True + rebuild=False -> just load the existing COD.
        """
        jar = self.jar_var.get().strip()
        if not jar or not os.path.isfile(jar):
            messagebox.showwarning("提示", "请先选择 JAR 文件")
            return
        try:
            nw, nh = self._resolve_native(jar)
        except ValueError as ex:
            messagebox.showwarning("提示", str(ex))
            return
        try:
            keymap = self._current_keymap()
        except ValueError as ex:
            messagebox.showwarning("键位格式错误", str(ex))
            return
        codname = self.codname_var.get().strip() or None

        self._actions_enabled(False)
        self.dev_btn.configure(state="disabled")
        self.status.set("黑莓流水线运行中…" if rebuild else "装机中…")

        def work():
            try:
                import build_bb
                if not rebuild:
                    cod = build_bb.install_existing(
                        jar, codname=codname, log=self.log)
                    self.msgq.put(("__bb_result__", True,
                                   "装机完成:\n%s\n请在手机上重进游戏。"
                                   % cod))
                    return
                cod, installed = build_bb.build_cod(
                    jar, nw, nh, codname=codname, keymap=keymap,
                    mode="auto", log=self.log, load=load)
                tip = ("已装机：%s\n请在手机上重进游戏。" % cod) if installed \
                    else ("COD 已生成（未检测到设备）:\n%s\n"
                          "连接手机后点\"已有 COD 直接装机\"。" % cod)
                self.msgq.put(("__bb_result__", True, tip))
            except Exception as ex:
                self.msgq.put(("__bb_result__", False, str(ex)))

        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------- batch scan
    def pick_batch_dir(self):
        d = filedialog.askdirectory(title="选择包含 jar 的目录")
        if d:
            self.batch_dir_var.set(d)

    def _scan_started(self):
        """按钮变“扫描中…”+ 进度条滚动，让用户立刻知道在跑。"""
        self.batch_btn.configure(text="扫描中…", state="disabled")
        self.scan_pb.stop()
        self.scan_pb.configure(mode="indeterminate", value=0)
        self.scan_pb.start(12)

    def _scan_finished(self):
        self.scan_pb.stop()
        self.batch_btn.configure(text="开始扫描", state="normal")

    def batch_scan(self):
        d = self.batch_dir_var.get().strip()
        if not d or not os.path.isdir(d):
            messagebox.showwarning("提示", "请选择存在的目录")
            return
        # 清空旧结果
        for i in self.batch_tree.get_children():
            self.batch_tree.delete(i)
        self.batch_results = []
        self._scan_started()
        self.batch_status.set("正在收集 JAR 列表…")
        recursive = self.batch_recursive.get()

        def work():
            try:
                results = R.batch_scan(
                    d, log=self.log,
                    progress_cb=self._batch_progress,
                    recursive=recursive,
                )
                self.msgq.put(("__batch_done__", results))
            except Exception as ex:
                self.msgq.put(("__batch_err__", str(ex)))

        threading.Thread(target=work, daemon=True).start()

    def _batch_progress(self, i, n, jar):
        name = os.path.basename(jar) if jar else ""
        self.msgq.put(("__batch_prog__", i, n, name))

    # ------------------------------------------------ batch convert
    def pick_batch_outdir(self):
        d = filedialog.askdirectory(title="转换后 JAR 输出目录")
        if d:
            self.batch_outdir_var.set(d)

    def _batch_convert_targets(self, scope):
        """返回待转换的预扫描记录列表。"""
        if scope == "selected":
            idxs = sorted(self.batch_tree.index(i)
                          for i in self.batch_tree.selection())
            recs = [self.batch_results[i] for i in idxs
                    if 0 <= i < len(self.batch_results)]
        else:
            recs = list(self.batch_results)
        if not self.batch_conv_bad.get():
            recs = [r for r in recs if r["blackberry_ok"]]
        return recs

    def batch_convert(self, scope):
        """批量自动适配转换；失败/跳过的游戏写进 转换失败.txt。"""
        if not self.batch_results:
            messagebox.showwarning("提示", "请先执行目录扫描")
            return
        recs = self._batch_convert_targets(scope)
        if not recs:
            messagebox.showwarning(
                "提示",
                "没有可转换的游戏（请先选中行，或扫描出「可上」结果；"
 "勾选强制转换可包含「不可上」）")
            return
        outdir = self.batch_outdir_var.get().strip()
        if not outdir:
            messagebox.showwarning("提示", "请选择转换后 JAR 的输出目录")
            return
        try:
            os.makedirs(outdir, exist_ok=True)
        except Exception as ex:
            messagebox.showerror("输出目录无效", str(ex))
            return

        self.batch_conv_sel_btn.configure(state="disabled",
                                          text="转换中，请稍候…")
        self.batch_conv_ok_btn.configure(state="disabled")
        self.batch_pb.configure(mode="determinate",
                                maximum=len(recs), value=0)
        self.batch_pb_var.set("0/%d" % len(recs))
        self.batch_status.set("批量转换开始，共 %d 个（单个约需几秒）"
                              % len(recs))
        # tk 变量只能在主线程读，进线程前全部快照
        force_bad = self.batch_conv_bad.get()
        all_results = list(self.batch_results)

        def work():
            # (kind, name, reason); kind: fail / skip
            problems = []
            ok_count = 0
            for i, r in enumerate(recs, 1):
                src = r["jar_path"]
                name = r["name"]
                self.msgq.put(("__bconv_prog__", i, len(recs), name))
                if not r["blackberry_ok"] and force_bad:
                    self.log("[%d/%d] 强制转换「不可上」: %s"
                             % (i, len(recs), name))
                # 1) 自动识别原生分辨率，失败则退回预扫描推测值
                try:
                    det = R.detect_native_from_assets(src)
                    if det:
                        nw, nh, _why = det
                    elif r.get("native_size_guess"):
                        nw, nh = r["native_size_guess"]
                        self.log("%s: 自动识别失败，使用预扫描推测 %dx%d"
                                 % (name, nw, nh))
                    else:
                        raise ValueError(
                            "无法识别原生分辨率（图片/JAD/字节码均无结果）")
                except Exception as ex:
                    problems.append(("fail", name,
                                     "分辨率识别失败: %s" % ex))
                    continue
                # 2) 安全输出路径（不覆盖原文件、同名加序号）
                base, ext = os.path.splitext(name)
                outp = os.path.join(outdir, name)
                if os.path.normcase(os.path.abspath(outp)) == \
                        os.path.normcase(os.path.abspath(src)) \
                        or os.path.exists(outp):
                    k = 2
                    while os.path.exists(
                            os.path.join(outdir, "%s_%d%s"
                                         % (base, k, ext or ".jar"))):
                        k += 1
                    outp = os.path.join(outdir, "%s_%d%s"
                                        % (base, k, ext or ".jar"))
                # 3) 转换（keymap=None 自动识别；失败不中断整批）
                try:
                    R.convert(src, outp, "auto", nw, nh,
                              keymap=None, log=self.log)
                    src_jad = os.path.splitext(src)[0] + ".jad"
                    if os.path.isfile(src_jad):
                        try:
                            R.update_jad(src_jad,
                                         os.path.splitext(outp)[0] + ".jad",
                                         os.path.basename(outp),
                                         os.path.getsize(outp))
                        except Exception as je:
                            self.log("%s: JAD 同步失败（不影响 JAR）: %s"
                                     % (name, je))
                    ok_count += 1
                except Exception as ex:
                    problems.append(("fail", name, str(ex)))
                    self.log("%s 转换失败: %s" % (name, ex))
            # 4) 未纳入本次转换的「不可上」游戏也写入报告（标记跳过）
            if not force_bad:
                chosen = {r["jar_path"] for r in recs}
                for r in all_results:
                    if not r["blackberry_ok"] and \
                            r["jar_path"] not in chosen:
                        problems.append(
                            ("skip", r["name"],
                             "预扫描判定不兼容，未转换: %s"
                             % (r.get("reason") or "未知原因")))
            # 5) 写 转换失败.txt
            txt_path = os.path.join(outdir, "转换失败.txt")
            try:
                if problems:
                    import time as _t
                    lines = ["批量转换报告  %s"
                             % _t.strftime("%Y-%m-%d %H:%M:%S"),
                             "成功 %d 个，失败/跳过 %d 个"
                             % (ok_count, len(problems)),
                             "输出目录: %s" % outdir,
                             "",
                             "========== 转换失败 / 已跳过 =========="]
                    for kind, nm, why in problems:
                        tag = "[失败]" if kind == "fail" else "[跳过]"
                        lines.append("%s %s" % (tag, nm))
                        lines.append("    原因: %s" % why)
                    with open(txt_path, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines) + "\n")
                elif os.path.exists(txt_path):
                    os.remove(txt_path)
            except Exception as ex:
                txt_path = None
                self.log("写转换失败.txt 出错: %s" % ex)
            self.msgq.put(("__bconv_done__",
                           {"ok": ok_count, "problems": problems,
                            "txt": txt_path, "n": len(recs),
                            "outdir": outdir}))

        threading.Thread(target=work, daemon=True).start()

    def _batch_convert_done(self, res):
        self.batch_conv_sel_btn.configure(state="normal",
                                          text="批量转换：选中行")
        self.batch_conv_ok_btn.configure(state="normal")
        self.batch_pb.configure(value=res["n"])
        self.batch_pb_var.set("完成 %d/%d" % (res["ok"], res["n"]))
        fails = [p for p in res["problems"] if p[0] == "fail"]
        skips = [p for p in res["problems"] if p[0] == "skip"]
        self.batch_status.set(
            "批量转换完成: 成功 %d，失败 %d，跳过 %d"
            % (res["ok"], len(fails), len(skips)))
        if fails:
            messagebox.showwarning(
                "批量转换完成（有失败）",
                "成功 %d 个，失败 %d 个，跳过 %d 个。\n"
                "失败游戏与原因已写入:\n%s"
                % (res["ok"], len(fails), len(skips), res["txt"]))
        else:
            messagebox.showinfo(
                "批量转换完成",
                "本次转换的 %d 个游戏全部成功。\n输出目录: %s%s"
                % (res["ok"], res["outdir"],
                   ("\n另有 %d 个「不可上」游戏已跳过，详见:\n%s"
                    % (len(skips), res["txt"])) if skips else ""))

    def _batch_done(self, results):
        self.batch_results = results
        for r in results:
            status = "✓ 可上" if r["blackberry_ok"] else "✗ 不可上"
            tag = "ok" if r["blackberry_ok"] else "bad"
            major = str(r["max_major"]) if r["max_major"] else "-"
            vendor = "是" if r["has_vendor"] else "否"
            # JSR 列只反映"黑莓不支持的 JSR"，不阻断的不算
            has_jsr_bad = bool(r.get("jsr_unsupported_refs"))
            jsr = "是" if has_jsr_bad else "否"
            canvas_str = []
            if r["has_canvas"]:
                canvas_str.append("Canvas")
            if r["has_fullcanvas"]:
                canvas_str.append("FullCanvas")
            if r["has_gamecanvas"]:
                canvas_str.append("GameCanvas")
            canvas = "+".join(canvas_str) or "无"
            size = "%dx%d" % r["native_size_guess"] \
                if r["native_size_guess"] else "-"
            if r.get("integrity_fatal"):
                integrity = "✗ 不可运行"
            elif r.get("integrity_warn"):
                integrity = "⚠ 有瑕疵"
            else:
                integrity = "✓ 完整"
            self.batch_tree.insert(
                "", "end",
                values=(r["name"], status, integrity, major, vendor, jsr,
                        canvas, size, r["reason"]),
                tags=(tag,))
        self._scan_finished()
        ok = sum(1 for r in results if r["blackberry_ok"])
        if results:
            self.scan_pb.configure(mode="determinate",
                                   maximum=len(results),
                                   value=len(results))
            self.batch_status.set(
                "扫描完成: %d/%d 可上黑莓 9930" % (ok, len(results)))
        else:
            self.scan_pb.configure(mode="determinate", maximum=1, value=0)
            self.batch_status.set("该目录下没找到 jar 文件")

    def _batch_row_detail(self, _=None):
        sel = self.batch_tree.selection()
        if not sel:
            return
        idx = self.batch_tree.index(sel[0])
        if idx >= len(self.batch_results):
            return
        r = self.batch_results[idx]
        lines = ["文件: %s" % r["jar_path"], ""]
        if r.get("integrity_fatal"):
            lines.append("包完整性：✗ 不可运行")
            for f in r["integrity_fatal"]:
                lines.append("  [致命] " + f)
        else:
            lines.append("包完整性：✓ 可运行（有启动入口）")
        for w in (r.get("integrity_warn") or []):
            lines.append("  [提示] " + w)
        lines.append("")
        lines.append("类版本: %d (%s)" % (
            r["max_major"], "合规" if r["class_version_ok"] else "超限"))
        if r["bad_classes"]:
            lines.append("超限类（前 10）:")
            for cn, mj in r["bad_classes"][:10]:
                lines.append("  %s (major=%d)" % (cn, mj))
        lines.append("Canvas 类: %s" % (
            ", ".join(r["canvas_classes"]) or "(无)"))
        lines.append("GameCanvas 类: %s" % (
            ", ".join(r["gamecanvas_classes"]) or "(无)"))
        if r["has_fullcanvas"]:
            lines.append("含 Nokia FullCanvas 子类（可由本工具转换救回）")
        if r["vendor_refs"]:
            lines.append("厂商扩展 API 引用（黑莓不支持）:")
            for cn in sorted(r["vendor_refs"]):
                lines.append("  - %s" % cn)
        if r.get("jsr_unsupported_refs"):
            lines.append("JSR 扩展 API 引用（黑莓不支持）:")
            for cn in sorted(r["jsr_unsupported_refs"]):
                lines.append("  - %s" % cn)
        if r.get("jsr_supported_refs"):
            lines.append("JSR 扩展 API 引用（黑莓支持，仅 PC MicroEmulator 不带）:")
            for cn in sorted(r["jsr_supported_refs"]):
                lines.append("  - %s" % cn)
        if r["native_size_guess"]:
            lines.append("推测原生分辨率: %dx%d" % r["native_size_guess"])
        if r["error"]:
            lines.append("扫描异常: %s" % r["error"])
        messagebox.showinfo("扫描详情", "\n".join(lines))

    # -------------------------------------------------- batch file ops
    def _batch_select_by_tag(self, tag):
        """选中所有 tags 含指定 tag 的行。tag = "ok" 或 "bad"。"""
        sel = []
        for iid in self.batch_tree.get_children(""):
            tags = self.batch_tree.item(iid, "tags")
            if tag in tags:
                sel.append(iid)
        self.batch_tree.selection_set(sel)
        self.batch_status.set(
            "已选中 %d 个%s" % (len(sel),
                                "可上黑莓" if tag == "ok" else "不可上"))

    def _batch_invert_selection(self):
        cur = set(self.batch_tree.selection())
        inv = [iid for iid in self.batch_tree.get_children("")
               if iid not in cur]
        self.batch_tree.selection_set(inv)
        self.batch_status.set("已反选 %d 个" % len(inv))

    def _batch_selected_paths(self):
        """返回当前选中行对应的 jar 文件路径列表。"""
        paths = []
        for iid in self.batch_tree.selection():
            idx = self.batch_tree.index(iid)
            if 0 <= idx < len(self.batch_results):
                paths.append(self.batch_results[idx]["jar_path"])
        return paths

    def _batch_copy_paths(self):
        """复制选中 jar 的完整路径到系统剪贴板。"""
        paths = self._batch_selected_paths()
        if not paths:
            messagebox.showwarning("提示", "请先选中至少一行")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append("\r\n".join(paths))
            self.batch_status.set("已复制 %d 个路径到剪贴板" % len(paths))
        except Exception as ex:
            messagebox.showerror("剪贴板失败", str(ex))

    def _batch_copy(self):
        """把选中的 jar 文件复制到目标目录。"""
        paths = self._batch_selected_paths()
        if not paths:
            messagebox.showwarning("提示", "请先选中至少一行")
            return
        dst = filedialog.askdirectory(title="复制选中的 jar 到目录")
        if not dst:
            return
        self._batch_run_fileop("copy", paths, dst)

    def _batch_move(self):
        """把选中的 jar 文件移动到目标目录。"""
        paths = self._batch_selected_paths()
        if not paths:
            messagebox.showwarning("提示", "请先选中至少一行")
            return
        dst = filedialog.askdirectory(title="移动选中的 jar 到目录")
        if not dst:
            return
        ok = messagebox.askyesno(
            "确认移动",
            "将移动 %d 个 jar 到:\n%s\n源文件会从原位置消失。" % (
                len(paths), dst))
        if not ok:
            return
        self._batch_run_fileop("move", paths, dst)

    def _batch_delete(self):
        """永久删除选中的 jar 文件。"""
        paths = self._batch_selected_paths()
        if not paths:
            messagebox.showwarning("提示", "请先选中至少一行")
            return
        ok = messagebox.askyesnocancel(
            "确认删除",
            "将永久删除 %d 个 jar 文件，不可恢复！\n确定继续？" % len(paths))
        if not ok:
            return
        self._batch_run_fileop("delete", paths, None)

    def _batch_run_fileop(self, op, paths, dst):
        """在线程里执行文件操作，避免阻塞 GUI。"""
        self.batch_btn.configure(state="disabled")
        self.batch_status.set("%s %d 个文件中…" % (op, len(paths)))

        def work():
            import shutil
            done = []
            errors = []
            for p in paths:
                try:
                    if op == "copy":
                        target = os.path.join(dst, os.path.basename(p))
                        # 同名加序号
                        if os.path.exists(target):
                            base, ext = os.path.splitext(target)
                            i = 1
                            while os.path.exists("%s_%d%s" % (base, i, ext)):
                                i += 1
                            target = "%s_%d%s" % (base, i, ext)
                        shutil.copy2(p, target)
                        done.append(p)
                    elif op == "move":
                        target = os.path.join(dst, os.path.basename(p))
                        if os.path.exists(target):
                            base, ext = os.path.splitext(target)
                            i = 1
                            while os.path.exists("%s_%d%s" % (base, i, ext)):
                                i += 1
                            target = "%s_%d%s" % (base, i, ext)
                        shutil.move(p, target)
                        done.append(p)
                    elif op == "delete":
                        os.remove(p)
                        done.append(p)
                except Exception as ex:
                    errors.append((p, str(ex)))
            self.msgq.put(("__batch_fileop__", op, done, errors,
                           len(paths)))

        threading.Thread(target=work, daemon=True).start()

    def _batch_fileop_done(self, op, done, errors, total):
        self.batch_btn.configure(state="normal")
        verb = {"copy": "复制", "move": "移动", "delete": "删除"}[op]
        # 从结果列表和 Treeview 移除已删除/已移动的项
        if op in ("move", "delete"):
            done_set = set(done)
            new_results = []
            old_children = list(self.batch_tree.get_children(""))
            for iid in old_children:
                idx = self.batch_tree.index(iid)
                if 0 <= idx < len(self.batch_results):
                    r = self.batch_results[idx]
                    if r["jar_path"] in done_set:
                        self.batch_tree.delete(iid)
                    else:
                        new_results.append(r)
            self.batch_results = new_results
        # 状态报告
        if errors:
            lines = ["%s失败 %d 个:" % (verb, len(errors))]
            for p, e in errors[:20]:
                lines.append("  %s: %s" % (os.path.basename(p), e))
            messagebox.showwarning("%s完成（部分失败）" % verb,
                                   "\n".join(lines))
        ok_count = len(done)
        fail_count = len(errors)
        if op == "copy":
            self.batch_status.set(
                "%s完成: %d 成功, %d 失败" % (verb, ok_count, fail_count))
        else:
            self.batch_status.set(
                "%s完成: %d/%d 操作成功, 列表已更新"
                % (verb, ok_count, total))


if __name__ == "__main__":
    App().mainloop()
