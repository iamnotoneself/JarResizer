# -*- coding: utf-8 -*-
"""J2ME JAR -> BlackBerry 9930 移植工具 - tkinter GUI（药丸圆角风格）"""
import os
import queue
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, font as tkfont, messagebox, ttk

import resizer as R

# ------------------------------------------------------------ 主题配色
PAGE_BG = "#eef1f6"      # 页面底色（浅灰蓝）
CARD_BG = "#ffffff"      # 功能卡片底色（与页面低对比）
CARD_EDGE = "#dde4ef"    # 卡片描边
INK = "#2b3244"          # 正文
SUB = "#7a8496"          # 提示文字
ACCENT = "#3f6ad8"       # 主色（标题药丸 / 主按钮）
ACCENT_HV = "#3560c6"    # 主色悬停
OK_FG = "#1b7e2a"
WARN_FG = "#b06a00"
BAD_FG = "#c62f2f"

FONT = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 9, "bold")
FONT_H2 = ("Microsoft YaHei UI", 10, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 8)


def _rounded(cv, x1, y1, x2, y2, r, **kw):
    """在 canvas 上画圆角矩形（polygon + smooth）。"""
    r = max(2, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


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


class _FlowFrame(tk.Frame):
    """按可用宽度自动换行排列子控件的容器。

    子控件以本 Frame 为父创建后不要再调用 pack/grid，布局由
    <Configure> 驱动（窗口拉到多窄都不会被裁切，只会折行变高）。
    """

    def __init__(self, master, gapx=8, gapy=4, bg=None, **kw):
        if bg is None:
            try:
                bg = master.cget("bg")
            except Exception:
                bg = CARD_BG
        super().__init__(master, bg=bg, highlightthickness=0, **kw)
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


class _ScrollFrame(tk.Frame):
    """纵向可滚动容器。

    - body 宽度始终等于可视区宽度，fill="x"/sticky="ew" 正常生效；
    - body 高度至少撑满可视区，内部 fill="both", expand=True 的区域
      （日志、结果表）在窗口拉高时跟着变大；
    - 内容超出时自动出现纵向滚动条，鼠标滚轮可滚；
    - 通过 bind_wrap() 登记的 Label，wraplength 会随宽度自动更新，
      长提示文字自动折行而不是撑破窗口。
    """

    def __init__(self, master, **kw):
        super().__init__(master, bg=PAGE_BG, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0,
                                bg=PAGE_BG)
        self.vsb = ttk.Scrollbar(self, orient="vertical",
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=PAGE_BG)
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


class PillButton(tk.Canvas):
    """胶囊形（药丸）按钮：圆角 = 高度一半，自绘底色/描边/悬停效果。

    兼容旧代码需要的接口：configure(text=…/state=…/command=…)、
    cget("state")。kind: default/primary/danger/soft。
    """

    _STYLE = {
        # kind: (fill, hover_fill, outline, fg)
        "default": ("#ffffff", "#eef2f9", "#c7d1e2", INK),
        "primary": (ACCENT, ACCENT_HV, "", "#ffffff"),
        "danger": ("#fdecea", "#f9d9d5", "#f0c4be", "#bf3a30"),
        "soft": ("#eaeef7", "#dfe6f3", "", "#41527a"),
    }

    def __init__(self, master, text="", command=None, kind="default",
                 padx=14, pady=6, **kw):
        self._kind = kind if kind in self._STYLE else "default"
        self._text = text
        self._command = command
        self._state = "normal"
        self._hover = False
        self._padx = padx
        self._pady = pady
        bg = CARD_BG
        try:
            bg = master.cget("bg")
        except Exception:
            pass
        super().__init__(master, highlightthickness=0, bd=0, bg=bg, **kw)
        self._font = tkfont.Font(
            family="Microsoft YaHei UI",
            size=10 if kind == "primary" else 9,
            weight="bold" if kind == "primary" else "normal")
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._draw()

    # ------------------------------------------------------------ 外观
    def _colors(self):
        fill, hover, outline, fg = self._STYLE[self._kind]
        if self._state == "disabled":
            if self._kind == "primary":
                return "#b9c6ea", "", "#eef2fb"
            return "#e9edf4", "#e2e8f1", "#a0a9ba"
        return (hover if self._hover else fill), outline, fg

    def _draw(self):
        tw = self._font.measure(self._text)
        h = self._font.metrics("linespace") + 2 * self._pady
        w = tw + 2 * self._padx
        self.configure(width=w, height=h,
                       cursor="hand2" if self._state == "normal"
                       else "arrow")
        self.delete("all")
        fill, outline, fg = self._colors()
        _rounded(self, 1, 1, w - 2, h - 2, (h - 2) / 2.0,
                 fill=fill, outline=outline,
                 width=1 if outline else 0)
        self.create_text(w / 2.0, h / 2.0, text=self._text,
                         fill=fg, font=self._font)

    # ------------------------------------------------------------ 交互
    def _set_hover(self, on):
        if self._state != "disabled" and on != self._hover:
            self._hover = on
            self._draw()

    def _on_click(self, _e=None):
        if self._state == "disabled" or not self._command:
            return
        self._command()

    # ------------------------------------------------------------ 兼容
    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        changed = False
        if "text" in kw:
            self._text = kw.pop("text")
            changed = True
        if "state" in kw:
            self._state = kw.pop("state")
            self._hover = False
            changed = True
        if "command" in kw:
            self._command = kw.pop("command")
        super().configure(**kw)
        if changed:
            self._draw()

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().cget(key)


class Card(tk.Frame):
    """圆角功能卡片：白底胶囊大圆角 + 彩色标题药丸，内嵌 body 容器。

    用法：card = Card(parent, title="① xxx")；控件放进 card.body。
    高度随 body 内容自适应，宽度跟随 pack（fill="x"）。
    """

    PADX = 14       # body 左右留白
    TPAD = 10       # 卡片顶部留白
    TITLE_H = 26    # 标题药丸高
    TGAP = 8        # 标题与 body 间距
    BPAD = 12       # body 底部留白
    R = 20          # 卡片圆角半径

    def __init__(self, master, title=""):
        super().__init__(master, bg=PAGE_BG, highlightthickness=0)
        self._title = title
        self._h = 0
        self.canvas = tk.Canvas(self, bg=PAGE_BG, highlightthickness=0,
                                bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=CARD_BG)
        self._win = self.canvas.create_window(
            self.PADX, self._body_y(), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._relayout)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _body_y(self):
        return self.TPAD + (self.TITLE_H + self.TGAP if self._title else 0)

    def _on_canvas(self, _e=None):
        w = self.canvas.winfo_width()
        if w <= 1:
            return
        self.canvas.itemconfigure(self._win, width=w - 2 * self.PADX)
        self._draw()

    def _relayout(self, _e=None):
        self.body.update_idletasks()
        h = self.body.winfo_reqheight()
        total = self._body_y() + h + self.BPAD
        if total != self._h:
            self._h = total
            self.canvas.configure(height=total)
        self.canvas.itemconfigure(self._win, height=h)
        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        h = max(self._h, c.winfo_height())
        if w <= 1:
            return
        _rounded(c, 1, 1, w - 2, h - 2, self.R,
                 fill=CARD_BG, outline=CARD_EDGE, width=1)
        if self._title:
            f = tkfont.Font(family="Microsoft YaHei UI", size=9,
                            weight="bold")
            tw = f.measure(self._title)
            x0, y0 = self.PADX, self.TPAD - 2
            _rounded(c, x0, y0, x0 + tw + 22, y0 + self.TITLE_H,
                     self.TITLE_H / 2.0, fill=ACCENT, outline="")
            c.create_text(x0 + 11, y0 + self.TITLE_H / 2.0,
                          text=self._title, fill="#ffffff", font=f,
                          anchor="w")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.configure(bg=PAGE_BG)
        self.title("J2ME 游戏 → 黑莓 9930 移植工具")
        self.geometry("880x760")
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
        try:
            style.theme_use("clam")  # clam 尊重自定义配色
        except tk.TclError:
            pass
        font_ui = ("Microsoft YaHei UI", 9)
        style.configure(".", font=font_ui, background=PAGE_BG)
        # 笔记本（页签）
        style.configure("TNotebook", background=PAGE_BG, borderwidth=0,
                        tabmargins=(10, 8, 10, 0))
        style.configure("TNotebook.Tab", padding=(18, 8),
                        font=("Microsoft YaHei UI", 9),
                        background="#dfe5ef", foreground=SUB,
                        borderwidth=0, focuscolor=PAGE_BG)
        style.map("TNotebook.Tab",
                  background=[("selected", CARD_BG)],
                  foreground=[("selected", INK)])
        # 输入框 / 下拉框
        style.configure("TEntry", fieldbackground="#ffffff",
                        foreground=INK, insertcolor=INK,
                        padding=(6, 4))
        style.configure("TCombobox", fieldbackground="#ffffff",
                        background="#eef2f9", foreground=INK,
                        arrowsize=14, padding=(4, 3))
        style.map("TCombobox",
                  fieldbackground=[("readonly", "#ffffff")],
                  foreground=[("readonly", INK)])
        # 进度条
        style.configure("Horizontal.TProgressbar",
                        troughcolor="#e2e8f2", background=ACCENT,
                        bordercolor=PAGE_BG, lightcolor=ACCENT,
                        darkcolor=ACCENT, thickness=10)
        # 树形列表
        style.configure("Treeview", background="#ffffff",
                        fieldbackground="#ffffff", foreground=INK,
                        rowheight=26, borderwidth=0, font=font_ui)
        style.configure("Treeview.Heading", background="#f0f3f9",
                        foreground=INK,
                        font=("Microsoft YaHei UI", 9, "bold"),
                        relief="flat", padding=(6, 5))
        style.map("Treeview.Heading", background=[("active", "#e6ebf4")])
        style.map("Treeview",
                  background=[("selected", "#d7e0f5")],
                  foreground=[("selected", INK)])

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
        self.nb.pack(fill="both", expand=True, padx=8, pady=(8, 6))

        page1 = _ScrollFrame(self.nb)
        self.nb.add(page1, text="  游戏转换 / 装机  ")
        self._build_convert(page1.body, page1)

        page2 = _ScrollFrame(self.nb)
        self.nb.add(page2, text="  批量兼容性预扫描  ")
        self._build_batch(page2.body, page2)

        page3 = _ScrollFrame(self.nb)
        self.nb.add(page3, text="  帮助 · 常见问题  ")
        self._build_help(page3.body, page3)

        self.status = tk.StringVar(value="就绪")
        tk.Label(self, textvariable=self.status, bg="#e3e9f3",
                 fg=SUB, anchor="w", padx=14, pady=6,
                 font=FONT).pack(fill="x", side="bottom")

    def _build_convert(self, root, sf):
        PAD = {"padx": 10, "pady": 5}

        # ① 选择游戏 ------------------------------------------------
        f1 = Card(root, title="① 选择游戏 JAR")
        f1.pack(fill="x", **PAD)
        row = tk.Frame(f1.body, bg=CARD_BG)
        row.pack(fill="x", padx=12, pady=10)
        row.columnconfigure(0, weight=1)
        self.jar_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.jar_var).grid(
            row=0, column=0, sticky="ew", padx=(0, 8))
        PillButton(row, text="浏览…", command=self.pick_jar).grid(
            row=0, column=1)

        # ② 适配设置（单列网格 + 流式行：窗口多窄都只折行、不裁切）--
        f2 = Card(root, title="② 适配设置（全部自动识别，通常不用改）")
        f2.pack(fill="x", **PAD)
        f2.body.columnconfigure(0, weight=1)

        # 原生分辨率：单选项 + 手动宽高，宽度不足自动折行
        r = _FlowFrame(f2.body)
        r.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        self.auto_size = tk.BooleanVar(value=True)
        tk.Label(r, text="原生分辨率:", bg=CARD_BG, fg=INK, font=FONT)
        tk.Radiobutton(r, text="自动识别（图片/JAD/诺基亚尺寸头）",
                       variable=self.auto_size, value=True,
                       command=self._auto_size_toggle, bg=CARD_BG,
                       activebackground=CARD_BG, fg=INK, font=FONT,
                       selectcolor="#ffffff", highlightthickness=0,
                       bd=0, anchor="w")
        tk.Radiobutton(r, text="手动", variable=self.auto_size,
                       value=False, command=self._auto_size_toggle,
                       bg=CARD_BG, activebackground=CARD_BG, fg=INK,
                       font=FONT, selectcolor="#ffffff",
                       highlightthickness=0, bd=0, anchor="w")
        tk.Label(r, text="宽", bg=CARD_BG, fg=INK, font=FONT)
        self.nw_var = tk.StringVar(value="240")
        self.nw_entry = ttk.Entry(r, textvariable=self.nw_var, width=6)
        tk.Label(r, text="高", bg=CARD_BG, fg=INK, font=FONT)
        self.nh_var = tk.StringVar(value="320")
        self.nh_entry = ttk.Entry(r, textvariable=self.nh_var, width=6)

        # 键位
        r = tk.Frame(f2.body, bg=CARD_BG)
        r.grid(row=1, column=0, sticky="ew", padx=12, pady=2)
        r.columnconfigure(0, weight=1)
        tk.Label(r, text="键位映射:", bg=CARD_BG, fg=INK,
                 font=FONT).grid(row=0, column=0, sticky="w")
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
        hint = tk.Label(
            r, bg=CARD_BG, fg=SUB, font=FONT_SMALL, anchor="w",
            justify="left",
            text="自定义格式：源键=目标键，逗号分隔；字母直接写(w/a/s/d)，"
                 "功能键 space/enter/esc，数字 1-9，左/右软键 -6/-7")
        hint.grid(row=3, column=0, sticky="ew")
        sf.bind_wrap(hint, pad=40)

        # 选项行：同步 JAD + 高级固定补丁（自动折行）
        r = _FlowFrame(f2.body)
        r.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 2))
        self.sync_jad = tk.BooleanVar(value=True)
        tk.Checkbutton(r, text="转换后同步更新同目录 JAD",
                       variable=self.sync_jad, bg=CARD_BG,
                       activebackground=CARD_BG, fg=INK, font=FONT,
                       selectcolor="#ffffff", highlightthickness=0,
                       bd=0, anchor="w")
        self.fixed_on = tk.BooleanVar(value=False)
        tk.Checkbutton(r, text="高级：固定分辨率补丁模式",
                       variable=self.fixed_on,
                       command=self._fixed_toggle, bg=CARD_BG,
                       activebackground=CARD_BG, fg=INK, font=FONT,
                       selectcolor="#ffffff", highlightthickness=0,
                       bd=0, anchor="w")
        tk.Label(r, text="（直接改字节码常量，不注入缩放引擎）",
                 bg=CARD_BG, fg=SUB, font=FONT_SMALL)

        # 高级：固定补丁参数（默认隐藏）
        self.fixed_frame = tk.Frame(f2.body, bg=CARD_BG)
        self.fixed_frame.grid(row=3, column=0, sticky="ew",
                              padx=12, pady=(0, 4))
        ff = _FlowFrame(self.fixed_frame)
        ff.pack(fill="x")
        self.mode_var = tk.StringVar(value="auto")
        self.preset_var = tk.StringVar(
            value=PRESET_LABELS[BB_PORTRAIT_INDEX])
        tk.Label(ff, text="目标尺寸预设:", bg=CARD_BG, fg=INK, font=FONT)
        self.preset = ttk.Combobox(ff, textvariable=self.preset_var,
                                   values=PRESET_LABELS, width=28,
                                   state="readonly")
        self.preset.bind("<<ComboboxSelected>>", self._preset_change)
        tk.Label(ff, text="宽", bg=CARD_BG, fg=INK, font=FONT)
        self.tw_var = tk.StringVar(value="480")
        self.tw_entry = ttk.Entry(ff, textvariable=self.tw_var, width=6)
        tk.Label(ff, text="高", bg=CARD_BG, fg=INK, font=FONT)
        self.th_var = tk.StringVar(value="640")
        self.th_entry = ttk.Entry(ff, textvariable=self.th_var, width=6)

        # 识别信息条：按钮一行，识别结果整行显示在下方（任意宽度可折行）
        r = tk.Frame(f2.body, bg=CARD_BG)
        r.grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 10))
        r.columnconfigure(0, weight=1)
        PillButton(r, text="读取游戏信息",
                   command=self.read_game_info).grid(row=0, column=0,
                                                     sticky="w")
        self.info_var = tk.StringVar(value="尚未读取（选择 JAR 后会自动识别）")
        self.info_lbl = tk.Label(r, textvariable=self.info_var,
                                 bg=CARD_BG, fg=INK, font=FONT_BOLD,
                                 anchor="w", justify="left")
        self.info_lbl.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        sf.bind_wrap(self.info_lbl, pad=40)

        # ③ 输出 / 执行 ---------------------------------------------
        f3 = Card(root, title="③ 输出与执行")
        f3.pack(fill="x", **PAD)
        f3.body.columnconfigure(1, weight=1)

        tk.Label(f3.body, text="转换后 JAR:", bg=CARD_BG, fg=INK,
                 font=FONT).grid(row=0, column=0, sticky="e",
                                 padx=(12, 6), pady=(10, 2))
        r = tk.Frame(f3.body, bg=CARD_BG)
        r.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(10, 2))
        r.columnconfigure(0, weight=1)
        self.out_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.out_var).grid(
            row=0, column=0, sticky="ew")
        PillButton(r, text="另存为…", command=self.pick_out).grid(
            row=0, column=1, padx=(8, 0))

        tk.Label(f3.body, text="黑莓模块名:", bg=CARD_BG, fg=INK,
                 font=FONT).grid(row=1, column=0, sticky="e",
                                 padx=(12, 6), pady=2)
        r = tk.Frame(f3.body, bg=CARD_BG)
        r.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=2)
        r.columnconfigure(0, weight=1)
        self.codname_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.codname_var,
                  width=18).grid(row=0, column=0, sticky="w")
        hint = tk.Label(r, bg=CARD_BG, fg=SUB, font=FONT_SMALL,
                        anchor="w", justify="left",
                        text="（留空自动取文件名，只能字母数字下划线）")
        hint.grid(row=1, column=0, sticky="ew")
        sf.bind_wrap(hint, pad=140)
        self.device_var = tk.StringVar(value="设备状态：检测中…")
        self.device_lbl = tk.Label(r, textvariable=self.device_var,
                                   bg=CARD_BG, fg=SUB, font=FONT)
        self.device_lbl.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.dev_btn = PillButton(r, text="重新检测",
                                  command=self.check_device)
        self.dev_btn.grid(row=3, column=0, sticky="w", pady=(4, 2))

        # 主操作按钮组（宽度不够自动折成多行）
        r = _FlowFrame(f3.body)
        r.grid(row=2, column=0, columnspan=2, sticky="ew",
               padx=12, pady=(8, 2))
        self.bb_auto_btn = PillButton(
            r, kind="primary", padx=18, pady=8,
            text="一键：转换 → 打包 COD → 装机",
            command=lambda: self.build_cod("auto"))
        self.bb_btn = PillButton(
            r, text="仅打包 COD",
            command=lambda: self.build_cod(False))
        self.run_btn = PillButton(
            r, text="只转换 JAR（不打包）", command=self.convert)
        self.bb_load_btn = PillButton(
            r, text="已有 COD 直接装机",
            command=lambda: self.build_cod(True, rebuild=False))
        hint = tk.Label(
            f3.body, bg=CARD_BG, fg=SUB, font=FONT_SMALL, anchor="w",
            justify="left",
            text="主按钮：USB 连着手机就自动装机，没连则只生成 COD；"
                 "回车=左软键，DEL/ESC=右软键，已内置无需配置。")
        hint.grid(row=3, column=0, columnspan=2, sticky="ew",
                  padx=12, pady=(0, 10))
        sf.bind_wrap(hint, pad=40)

        # 日志 ------------------------------------------------------
        box = Card(root, title="运行日志")
        box.pack(fill="both", expand=True, **PAD)
        inner = tk.Frame(box.body, bg=CARD_BG)
        inner.pack(fill="both", expand=True, padx=12, pady=10)
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)
        self.logbox = tk.Text(inner, height=9, wrap="word",
                              font=("Consolas", 9), relief="flat",
                              background="#fafbfd", foreground=INK,
                              insertbackground=INK, bd=0,
                              highlightthickness=1,
                              highlightbackground=CARD_EDGE,
                              highlightcolor=CARD_EDGE)
        sb = ttk.Scrollbar(inner, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")
        self.logbox.grid(row=0, column=0, sticky="nsew")

    def _build_batch(self, parent, sf):
        """批量预扫描页面：目录 + 分组操作 + 结果 Treeview。"""
        pad = {"padx": 10, "pady": 5}

        # ① 扫描目录 ------------------------------------------------
        c1 = Card(parent, title="① 扫描目录")
        c1.pack(fill="x", **pad)
        c1.body.columnconfigure(0, weight=1)
        self.batch_dir_var = tk.StringVar()
        ttk.Entry(c1.body, textvariable=self.batch_dir_var).grid(
            row=0, column=0, sticky="ew", padx=(12, 8), pady=10)
        PillButton(c1.body, text="浏览…",
                   command=self.pick_batch_dir).grid(row=0, column=1,
                                                     padx=(0, 12))
        # 选项按钮自动折行，窗口窄也不会被切掉
        opt = _FlowFrame(c1.body)
        opt.grid(row=1, column=0, columnspan=2, sticky="ew",
                 padx=12, pady=(0, 4))
        self.batch_recursive = tk.BooleanVar(value=False)
        tk.Checkbutton(opt, text="递归子目录",
                       variable=self.batch_recursive, bg=CARD_BG,
                       activebackground=CARD_BG, fg=INK, font=FONT,
                       selectcolor="#ffffff", highlightthickness=0,
                       bd=0, anchor="w")
        self.batch_btn = PillButton(opt, text="开始扫描",
                                    kind="primary",
                                    command=self.batch_scan)
        # 进度行：进度条 + 状态文字（流式：宽度不够时状态文字自动
        # 换到进度条下方并按宽度折行，绝不被切）
        prow = _FlowFrame(c1.body)
        prow.grid(row=2, column=0, columnspan=2, sticky="ew",
                  padx=12, pady=(0, 10))
        self.scan_pb = ttk.Progressbar(prow, length=240,
                                       mode="indeterminate")
        self.batch_status = tk.StringVar(value="就绪：选择目录后点「开始扫描」")
        ttk.Label(prow, textvariable=self.batch_status)

        # ② 选中行操作 ----------------------------------------------
        c2 = Card(parent, title="② 选中行操作（先在列表中点选）")
        c2.pack(fill="x", **pad)
        r1 = _FlowFrame(c2.body)
        r1.pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(r1, text="选择:", bg=CARD_BG, fg=SUB, font=FONT)
        PillButton(r1, kind="soft", text="全选「可上」",
                   command=lambda: self._batch_select_by_tag("ok"))
        PillButton(r1, kind="soft", text="全选「不可上」",
                   command=lambda: self._batch_select_by_tag("bad"))
        PillButton(r1, kind="soft", text="反选",
                   command=self._batch_invert_selection)
        PillButton(r1, kind="soft", text="全不选",
                   command=lambda: self.batch_tree.selection_set(()))
        r2 = _FlowFrame(c2.body)
        r2.pack(fill="x", padx=12, pady=(2, 10))
        tk.Label(r2, text="文件:", bg=CARD_BG, fg=SUB, font=FONT)
        PillButton(r2, text="复制到…", command=self._batch_copy)
        PillButton(r2, text="移动到…", command=self._batch_move)
        PillButton(r2, kind="danger", text="删除…", command=self._batch_delete)
        PillButton(r2, text="复制完整路径", command=self._batch_copy_paths)

        # ③ 批量转换 ------------------------------------------------
        c3 = Card(parent, title="③ 批量转换（自动适配，失败自动跳过并记录）")
        c3.pack(fill="x", **pad)
        c3.body.columnconfigure(1, weight=1)
        tk.Label(c3.body, text="输出目录:", bg=CARD_BG, fg=INK,
                 font=FONT).grid(row=0, column=0, sticky="e",
                                 padx=(12, 6), pady=(10, 2))
        r = tk.Frame(c3.body, bg=CARD_BG)
        r.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(10, 2))
        r.columnconfigure(0, weight=1)
        self.batch_outdir_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.batch_outdir_var).grid(
            row=0, column=0, sticky="ew")
        PillButton(r, text="浏览…",
                   command=self.pick_batch_outdir).grid(
            row=0, column=1, padx=(8, 0))
        self.batch_conv_bad = tk.BooleanVar(value=False)
        tk.Checkbutton(
            c3.body, text="预扫描判「不可上」的也强制尝试转换",
            variable=self.batch_conv_bad, bg=CARD_BG,
            activebackground=CARD_BG, fg=INK, font=FONT,
            selectcolor="#ffffff", highlightthickness=0, bd=0,
            anchor="w").grid(row=1, column=0, columnspan=2, sticky="w",
                             padx=12, pady=2)
        hint = tk.Label(
            c3.body, bg=CARD_BG, fg=SUB, font=FONT_SMALL, anchor="w",
            justify="left",
            text="（不勾则直接跳过并记入报告；同名输出自动加序号，"
                 "不会覆盖原游戏）")
        hint.grid(row=2, column=0, columnspan=2, sticky="ew",
                  padx=12, pady=(0, 2))
        sf.bind_wrap(hint, pad=40)
        r = _FlowFrame(c3.body)
        r.grid(row=3, column=0, columnspan=2, sticky="ew",
               padx=12, pady=(2, 10))
        self.batch_conv_sel_btn = PillButton(
            r, text="批量转换：选中行",
            command=lambda: self.batch_convert("selected"))
        self.batch_conv_ok_btn = PillButton(
            r, kind="primary", text="批量转换：全部「可上」",
            command=lambda: self.batch_convert("ok"))
        self.batch_pb = ttk.Progressbar(r, length=220, mode="determinate")
        self.batch_pb_var = tk.StringVar(value="")
        tk.Label(r, textvariable=self.batch_pb_var, bg=CARD_BG, fg=SUB,
                 font=FONT_SMALL)

        # ④ 扫描结果 ------------------------------------------------
        c4 = Card(parent, title="④ 扫描结果（双击行查看完整诊断）")
        c4.pack(fill="both", expand=True, **pad)
        cols = ("name", "status", "integrity", "major", "vendor", "jsr",
                "canvas", "size", "reason")
        labels = ("文件名", "状态", "完整性", "类版本", "厂商扩展", "JSR 扩展",
                  "Canvas 类型", "推测分辨率", "原因")
        widths = (200, 70, 70, 55, 65, 65, 140, 85, 220)
        anchors = ("w", "center", "center", "center", "center", "center",
                   "w", "center", "w")
        # 窄窗口时横向滚动；宽窗口时首列与原因列吃掉多余宽度
        stretch_cols = {"name", "reason"}
        inner = tk.Frame(c4.body, bg=CARD_BG)
        inner.pack(fill="both", expand=True, padx=12, pady=10)
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
        self.batch_tree.tag_configure("ok", foreground=OK_FG)
        self.batch_tree.tag_configure("bad", foreground=BAD_FG)

        self.batch_results = []

    def _build_help(self, root, sf):
        """帮助页：各功能讲解 + 常见问题解答。"""

        def card(title, lines):
            c = Card(root, title=title)
            c.pack(fill="x", padx=10, pady=5)
            for kind, txt in lines:
                if kind == "h":       # 小节标题
                    lbl = tk.Label(c.body, text=txt, bg=CARD_BG, fg=INK,
                                   font=FONT_H2, anchor="w",
                                   justify="left")
                elif kind == "q":     # FAQ 问题
                    lbl = tk.Label(c.body, text=txt, bg=CARD_BG,
                                   fg=ACCENT, font=FONT_BOLD, anchor="w",
                                   justify="left")
                elif kind == "a":     # FAQ 答案（缩进）
                    lbl = tk.Label(c.body, text="　" + txt, bg=CARD_BG,
                                   fg=INK, font=FONT, anchor="w",
                                   justify="left")
                elif kind == "s":     # 弱化说明
                    lbl = tk.Label(c.body, text=txt, bg=CARD_BG, fg=SUB,
                                   font=FONT_SMALL, anchor="w",
                                   justify="left")
                else:                 # 普通条目
                    lbl = tk.Label(c.body, text=txt, bg=CARD_BG, fg=INK,
                                   font=FONT, anchor="w", justify="left")
                lbl.pack(fill="x", padx=14, pady=(6, 1))
                sf.bind_wrap(lbl, pad=60)

        card("快速上手（三步）", [
            ("n", "1. 在「① 选择游戏 JAR」里选一个 J2ME 游戏 jar —— 工具会"
                  "自动识别原生分辨率与按键方案，并预检 COD 转换风险。"),
            ("n", "2. 设置一般不用动，直接点「一键：转换 → 打包 COD → 装机」："
                  "USB 连着手机就自动装机，没连则只生成 COD。"),
            ("n", "3. 装机后点图标闪退？多半是手机里有旧坏模块：先在 "
                  "选项→设备→应用程序管理 里彻底删除并重启手机，再装一次"
                  "（详见下方 FAQ）。"),
        ])

        card("转换 / 装机页功能详解", [
            ("h", "输入与识别"),
            ("n", "原生分辨率 —— 游戏原始画面尺寸。默认「自动识别」"
                  "（从游戏内图片 / JAD / 诺基亚尺寸头推断）；识别失败时"
                  "选「手动」填宽高。"),
            ("n", "读取游戏信息 —— 显示识别结果：原生分辨率、按键风格、"
                  "Canvas 类型、包完整性、COD 风险预警；详细内容同时"
                  "写入日志。"),
            ("h", "画面与键位"),
            ("n", "键位映射 —— 默认「自动识别」：数字键常量多的游戏用"
                  "「黑莓键帽数字」方案（WER/SDF/ZXC=1-9），厂商码"
                  "（-1~-7）多的用「WASD」方案；也可手动选方案或"
                  "「自定义映射」。"),
            ("n", "转换后同步更新同目录 JAD —— 游戏旁有同名 JAD 时，"
                  "自动生成配套新 JAD（装机 / OTA 优先用 JAD 入口）。"),
            ("n", "固定分辨率补丁模式（高级）—— 直接改字节码里的屏幕"
                  "常量，不注入缩放引擎，适合缩放后画面异常的游戏；"
                  "此模式下键位注入不生效。"),
            ("h", "输出与执行"),
            ("n", "黑莓模块名 —— 安装后的内部模块名，留空自动取文件名"
                  "（仅字母 / 数字 / 下划线）。"),
            ("n", "四个操作按钮 —— 「一键」= 转换+打包 COD+自动装机；"
                  "「仅打包 COD」= 只出文件不装机；「只转换 JAR」= 给 "
                  "PC 模拟器试玩用；「已有 COD 直接装机」= 重复安装 / "
                  "换机时用。"),
            ("n", "运行日志 —— 全部步骤输出；报错时先看这里的错误行。"),
        ])

        card("批量预扫描页功能详解", [
            ("n", "扫描 —— 对目录内每个 jar 检查：包完整性（有无启动入口）、"
                  "类版本（须 ≤48 即 Java 1.4）、是否引用黑莓不支持的"
                  "厂商 / JSR 扩展、Canvas 类型、推测分辨率。"),
            ("n", "结果列含义 —— 状态=可否上黑莓；完整性=能否运行；"
                  "厂商扩展 / JSR=是否用了黑莓没有的 API；"
                  "Canvas=画面类；原因=不可上的具体理由。"),
            ("n", "双击任意行 —— 查看完整诊断：超限类清单、扩展 API 列表、"
                  "完整性明细。"),
            ("n", "选中行操作 —— 全选「可上」/「不可上」、反选后，可批量"
                  "复制、移动、删除或复制完整路径。"),
            ("n", "批量转换 —— 转换选中行或全部「可上」游戏到指定目录；"
                  "同名输出自动加序号，绝不覆盖；失败 / 跳过明细写入"
                  "输出目录的「转换失败.txt」。"),
            ("n", "强制尝试「不可上」—— 勾选后连判定不兼容的也试一次，"
                  "偶尔能救回。"),
        ])

        card("键位方案与默认按键", [
            ("n", "黑莓键帽数字：W/E/R=1/2/3，S/D/F=4/5/6，Z/X/C=7/8/9，"
                  "空格与 D=中央 5 键（跳 / 开火 / 确认）。"),
            ("n", "厂商游戏码：W/A/S/D=上下左右，空格=动作 / 确认。"),
            ("n", "软键全方案通用：回车=左软键（确定 / 菜单），DEL 或 "
                  "ESC=右软键（返回 / 取消），已内置无需配置。"),
            ("n", "触摸板：上 / 下 / 左 / 右滑动=方向键，短按=中央 5 键；"
                  "JAD 固定写入 RIM-TouchCompatibilityMode: false，"
                  "系统会给完整 480×640 真屏而非虚拟小窗。"),
        ])

        card("常见问题 FAQ", [
            ("q", "Q1 转换后游戏画面偏移 / 有大黑边？"),
            ("a", "原生分辨率识别错了。点「读取游戏信息」确认真实尺寸，"
                  "或在②中选「手动」填宽高后重新转换。"),
            ("q", "Q2 按键和预期不符（跳跃无效、方向错位）？"),
            ("a", "键位方案判定错了。在键位映射下拉里手动换成另一方案；"
                  "仍不行就用「自定义映射」按 源键=目标键 逐个写。"),
            ("q", "Q3 装到手机点图标就闪退 / 提示模块损坏？"),
            ("a", "手机里残留了旧坏模块。先在 选项→设备→应用程序管理 里"
                  "彻底删除该游戏并重启手机；安装时打开 .jad（不是 jar），"
                  "且 jad 与 jar 必须在同一目录。"),
            ("q", "Q4 COD 构建失败，日志提到类过大 / 控制流验证？"),
            ("a", "触发了 RIM COD 转换器硬限制（单类字节码 ≥60KB 或"
                  "验证帧 ≥50KB、全方法 try/catch 的混淆类）。基本无解，"
                  "请找该游戏的更小版本；「读取游戏信息」会提前预警。"),
            ("q", "Q5 日志提示解压后超过 20MB？"),
            ("a", "超过 COD 分片硬上限（最多 255 片），无法转换，"
                  "请换更小的游戏版本。"),
            ("q", "Q6 点「一键」提示未检测到设备？"),
            ("a", "没连手机时只会生成 COD。连接手机（装好黑莓驱动）后点"
                  "「重新检测」，再用「已有 COD 直接装机」。"),
            ("q", "Q7 批量转换会覆盖原文件吗？"),
            ("a", "不会。输出到指定目录，同名自动加序号；原目录文件不动。"),
            ("q", "Q8 横屏 / 触屏游戏怎么处理？"),
            ("a", "横屏游戏（如 640×360）转换后自动横屏显示；触摸屏游戏"
                  "拿到完整 480×640 真屏，触摸板与键盘映射见上方键位说明。"),
            ("q", "Q9 想先在电脑上试玩再装机？"),
            ("a", "用「只转换 JAR（不打包）」，产物可在 MicroEmulator 等"
                  "PC 模拟器里运行验证，确认没问题再回来打包装机。"),
            ("s", "更多技术细节（构建管线、COD 限制、移植原理）见仓库 "
                  "README.md。"),
        ])

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
                        self.device_lbl.configure(fg=OK_FG)
                    else:
                        self.device_var.set("设备状态：未连接（仅打包）")
                        self.device_lbl.configure(fg=WARN_FG)
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
        self.info_lbl.configure(fg=INK)

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
            self.info_lbl.configure(fg=BAD_FG)
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
            self.info_lbl.configure(fg=BAD_FG)
        else:
            self.info_lbl.configure(fg=OK_FG)
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
        self.device_lbl.configure(fg=SUB)

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
