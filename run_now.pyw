# -*- coding: utf-8 -*-
"""咕咚一键跑步 —— 双击即跑，无控制台窗口，面向非技术用户。

界面显示登录状态、当前账号和今日里程；
支持一键跑步、重新登录、把凭证换成另一个账号。
"""
import os
import sys
import json
import base64
import shutil
import subprocess
import threading
import time
import datetime
import ctypes

REPO = os.path.dirname(os.path.abspath(__file__))


def find_python():
    exe = sys.executable or "python"
    if exe.lower().endswith("pythonw.exe"):
        cand = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(cand):
            return cand
    return exe


PY = find_python()
PS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

BLACK = "#1a1a1a"
GRAY  = "#71717a"
BLUE  = "#1d4ed8"
GREEN = "#15803d"
RED   = "#b91c1c"
AMBER = "#b45309"
CARD  = "#f8fafc"
BORDER = "#e4e4e7"

FONT = "Microsoft YaHei"
WIN_W = 1200
WRAP = 1100
CFG_PATH = os.path.join(REPO, "config.json")
EXAMPLE_PATH = os.path.join(REPO, "config.example.json")
TOKEN_PATH = os.path.join(REPO, "token.txt")
TOKEN_BACKUP = os.path.join(REPO, "token.old.txt")
PARAMS_PATH = os.path.join(REPO, "captured_params.json")
SWITCH_LOG = os.path.join(REPO, "account_switch.log")


if not os.path.exists(CFG_PATH) and os.path.exists(EXAMPLE_PATH):
    try:
        shutil.copyfile(EXAMPLE_PATH, CFG_PATH)
    except OSError:
        pass


def load_cfg():
    with open(CFG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_cfg(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


try:
    CFG = load_cfg()
except Exception:
    CFG = {}

KM      = CFG.get("target_m", 3300) / 1000.0
MINUTES = CFG.get("target_m", 3300) * CFG.get("base_pace", 300) / 60000.0
CAP_M   = 6000

DIST_MIN, DIST_MAX = 1.0, 6.5          # 可自由选择的里程范围(公里)
PACE_MIN_S, PACE_MAX_S = 240, 600      # 可自由选择的配速范围(秒/公里, 即 4'00"~10'00")
ROUTE_NAMES = ["路线 %d" % i for i in range(1, 11)]
ROUTE_FILES = ["route_%d.json" % i for i in range(1, 11)]

# 10 个方案预设: (名称, 路线文件, 公里, 秒/公里)
PRESETS = [
    ("方案 1 · 轻松跑 3 公里", "route_1.json", 3.0, 360),
    ("方案 2 · 慢摇 2 公里", "route_2.json", 2.0, 420),
    ("方案 3 · 4 公里匀速", "route_3.json", 4.0, 320),
    ("方案 4 · 5 公里节奏跑", "route_4.json", 5.0, 300),
    ("方案 5 · 6.5 公里耐力跑", "route_5.json", 6.5, 340),
    ("方案 6 · 1 公里恢复走跑", "route_6.json", 1.0, 600),
    ("方案 7 · 4.5 公里慢跑", "route_7.json", 4.5, 480),
    ("方案 8 · 5.5 公里快速跑", "route_8.json", 5.5, 240),
    ("方案 9 · 6 公里六分半", "route_9.json", 6.0, 390),
    ("方案 10 · 3.5 公里九分配", "route_10.json", 3.5, 540),
]
PRESET_LABELS = [p[0] for p in PRESETS]
COMBO_VALUES = PRESET_LABELS + ["自定义"]


def fmt_pace(sec):
    sec = int(round(sec))
    return "%d'%02d\"" % (sec // 60, sec % 60)


def _run(argv, timeout=None):
    return subprocess.run(
        argv, cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, env=CHILD_ENV,
        creationflags=NO_WINDOW,
    )


def token_info():
    """读 token.txt，解出过期时间和账号 ID（sub）。"""
    info = {"present": False, "remaining": -1, "sub": "", "broken": False}
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            tok = f.read().strip()
    except OSError:
        return info
    if not tok:
        return info
    info["present"] = True
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        d = json.loads(base64.urlsafe_b64decode(p))
        info["remaining"] = int(d.get("exp", 0) - time.time())
        info["sub"] = d.get("sub", "") or ""
    except Exception:
        info["broken"] = True
        info["remaining"] = -1
    return info


def mask_uid(uid):
    uid = (uid or "").strip()
    if not uid:
        return "未知"
    if len(uid) <= 12:
        return uid
    return uid[:6] + "…" + uid[-4:]


def fmt_rem(sec):
    if sec <= 0:
        return "已过期"
    if sec < 3600:
        return "%d 分钟" % max(1, int(sec // 60))
    return "%d 小时 %d 分" % (int(sec // 3600), int(sec % 3600 // 60))


def token_remaining():
    try:
        return int(_run([PY, "check_status.py", "remaining"], timeout=30).stdout.strip())
    except Exception:
        return -1


def dedup_state():
    try:
        return _run([PY, "check_status.py", "dedup"], timeout=40).stdout.strip()
    except Exception:
        return "UNKNOWN"


def today_meters():
    try:
        return int(_run([PY, "check_status.py", "today"], timeout=40).stdout.strip())
    except Exception:
        return -1


def enable_dpi_awareness():
    """让窗口按真实像素绘制，避免高分屏下被系统整体放大两倍。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def keep_awake():
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)


def release_awake():
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def refresh_token(timeout=220):
    """调用 refresh.ps1 抓取 token，成功返回 (True, '')，失败返回 (False, 原因)。"""
    try:
        p = _run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                  os.path.join(REPO, "refresh.ps1")], timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "等待超时"
    except Exception as e:
        return False, str(e)
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        return False, "；".join(tail[-3:]) if tail else "refresh.ps1 退出码 %s" % p.returncode
    if not token_info()["present"]:
        return False, "没有生成 token.txt"
    return True, ""


def captured_params():
    try:
        with open(PARAMS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def sync_cfg_account(sub):
    """把 config.json 的 user_id / club_id 同步成刚抓到的账号信息。返回是否改动。"""
    if not os.path.exists(CFG_PATH):
        return False
    try:
        cfg = load_cfg()
    except Exception:
        return False
    changed = False
    if sub and cfg.get("user_id") != sub:
        cfg["user_id"] = sub
        changed = True
    try:
        club = int(captured_params().get("club_id") or 0)
    except (TypeError, ValueError):
        club = 0
    if club and cfg.get("club_id") != club:
        cfg["club_id"] = club
        changed = True
    if changed:
        save_cfg(cfg)
    return changed


def note_switch(mode, ok, detail):
    try:
        with open(SWITCH_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] mode=%s ok=%s %s\n" % (
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mode, ok, detail))
    except OSError:
        pass


def refresh_status(root, ui):
    """后台刷新登录状态 + 今日里程，所有界面更新都回到主线程。"""
    info = token_info()
    try:
        cfg = load_cfg()
    except Exception:
        cfg = {}
    valid = info["present"] and info["remaining"] > 0 and not info["broken"]
    root.after(0, lambda: ui.render_status(info, None if valid else "skip", cfg))
    root.after(0, lambda: ui.status_btn.configure(state="disabled"))

    def work():
        today = today_meters() if valid else "skip"
        root.after(0, lambda: ui.render_status(info, today, cfg))
        root.after(0, lambda: ui.status_btn.configure(state="normal"))

    threading.Thread(target=work, daemon=True).start()


class UI:
    """把技术动作翻译成大字号的中文提示。"""

    STEPS = ["检查登录状态", "查看今天的记录", "上传这 %.1f 公里的跑步数据" % KM]

    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk, scrolledtext

        self.root = root
        self.actions = {}
        self._need_login = True
        root.title("咕咚一键跑步")
        root.geometry("%dx720" % WIN_W)
        root.minsize(900, 700)
        root.configure(bg="white")

        tk.Label(root, text="咕咚一键跑步", font=(FONT, 14, "bold"),
                 bg="white", fg=BLACK).pack(fill="x", padx=24, pady=(12, 0))
        tk.Label(root, text="双击一次，自动跑完你选的路线、里程和配速", font=(FONT, 9),
                 bg="white", fg=GRAY).pack(fill="x", padx=24, pady=(2, 8))

        card = tk.Frame(root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", padx=24)

        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=14, pady=(12, 4))
        self.dot = tk.Label(top, text="●", font=(FONT, 13), bg=CARD, fg=GRAY)
        self.dot.pack(side="left")
        self.state_lbl = tk.Label(top, text="检查中…", font=(FONT, 11, "bold"),
                                  bg=CARD, fg=BLACK)
        self.state_lbl.pack(side="left", padx=(6, 0))
        self.rem_lbl = tk.Label(top, text="", font=(FONT, 9), bg=CARD, fg=GRAY)
        self.rem_lbl.pack(side="left", padx=(8, 0))
        self.status_btn = tk.Button(top, text="刷新状态", font=(FONT, 9),
                                    relief="groove", command=lambda: self._act("status"))
        self.status_btn.pack(side="right")

        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=14, pady=(4, 0))
        tk.Label(row, text="当前账号", font=(FONT, 9), bg=CARD, fg=GRAY,
                 width=10, anchor="w").pack(side="left")
        self.uid_lbl = tk.Label(row, text="—", font=(FONT, 9), bg=CARD,
                                fg=BLACK, anchor="w")
        self.uid_lbl.pack(side="left")

        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=14, pady=(2, 12))
        tk.Label(row, text="今日已跑", font=(FONT, 9), bg=CARD, fg=GRAY,
                 width=10, anchor="w").pack(side="left")
        self.today_lbl = tk.Label(row, text="—", font=(FONT, 9), bg=CARD,
                                  fg=BLACK, anchor="w")
        self.today_lbl.pack(side="left")

        self.warn_lbl = tk.Label(card, text="", font=(FONT, 9), bg=CARD, fg=AMBER,
                                 anchor="w", justify="left", wraplength=WRAP)

        self._syncing = False
        self._build_plan_card(root, tk, ttk)

        self.big = tk.Label(root, text="准备中…", font=(FONT, 14, "bold"),
                            bg="white", fg=BLACK, justify="left",
                            anchor="w", wraplength=WRAP)
        self.big.pack(fill="x", padx=24, pady=(10, 0))

        steps = tk.Frame(root, bg="white")
        steps.pack(fill="x", padx=24, pady=(8, 4))
        self.marks = []
        self.step_labels = []
        for i, name in enumerate(self.STEPS):
            r = tk.Frame(steps, bg="white")
            r.pack(fill="x", pady=2)
            mark = tk.Label(r, text="　　", font=(FONT, 10, "bold"),
                            bg="white", fg=GRAY, width=7, anchor="w")
            mark.pack(side="left")
            lbl = tk.Label(r, text=name, font=(FONT, 10), bg="white",
                           fg=BLACK, anchor="w")
            lbl.pack(side="left")
            self.marks.append(mark)
            self.step_labels.append(lbl)
        self._on_preset()

        self.bar = ttk.Progressbar(root, mode="indeterminate")
        self.bar.pack(fill="x", padx=24, pady=(8, 4))
        self.elapsed = tk.Label(root, text="", font=(FONT, 9), bg="white",
                                fg=GRAY, anchor="w")
        self.elapsed.pack(fill="x", padx=24)

        btns = tk.Frame(root, bg="white")
        btns.pack(fill="x", padx=24, pady=(8, 4))
        self.start_btn = tk.Button(btns, text="开始跑步", font=(FONT, 11, "bold"),
                                   bg=BLUE, fg="white", activebackground="#1e40af",
                                   activeforeground="white", relief="flat", bd=0,
                                   padx=22, pady=8, command=lambda: self._act("start"))
        self.start_btn.pack(side="left")
        self.change_btn = tk.Button(btns, text="更换账号", font=(FONT, 10),
                                    relief="groove", padx=12, pady=5,
                                    command=lambda: self._act("change"))
        self.change_btn.pack(side="left", padx=(10, 0))
        self.detail_btn = tk.Button(btns, text="显示技术详情", font=(FONT, 9),
                                    command=self.toggle_detail, relief="groove")
        self.detail_btn.pack(side="right")

        self.logbox = scrolledtext.ScrolledText(root, height=7, font=("Consolas", 9),
                                                background="#f4f4f5",
                                                foreground="#3f3f46")
        self.logbox.configure(state="disabled")
        self.log_shown = False

        tk.Label(root, text="小提示：跑的过程中请不要打开「企业咕咚」小程序",
                 font=(FONT, 9), bg="white", fg=AMBER).pack(fill="x", padx=24, pady=(6, 10))

    # ---- 方案选择 ----
    def _build_plan_card(self, root, tk, ttk):
        self._syncing = True
        plan = tk.Frame(root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        plan.pack(fill="x", padx=24, pady=(10, 0))
        grid = tk.Frame(plan, bg=CARD)
        grid.pack(fill="x", padx=14, pady=(6, 4))
        grid.columnconfigure(1, weight=1)

        def lab(text):
            return tk.Label(grid, text=text, font=(FONT, 9), bg=CARD, fg=GRAY,
                            width=6, anchor="w")

        lab("方案").grid(row=0, column=0, sticky="w")
        self.preset_var = tk.StringVar(value=PRESET_LABELS[0])
        self.preset_box = ttk.Combobox(grid, textvariable=self.preset_var,
                                       values=COMBO_VALUES, state="readonly",
                                       font=(FONT, 9), width=24)
        self.preset_box.grid(row=0, column=1, sticky="w")
        self.preset_box.bind("<<ComboboxSelected>>", lambda e: self._on_preset())
        self.preview_lbl = tk.Label(grid, text="", font=(FONT, 9), bg=CARD, fg=BLACK,
                                    anchor="w")
        self.preview_lbl.grid(row=0, column=2, sticky="w", padx=(14, 0))

        lab("路线").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.route_var = tk.StringVar(value=ROUTE_NAMES[0])
        self.route_box = ttk.Combobox(grid, textvariable=self.route_var,
                                      values=ROUTE_NAMES, state="readonly",
                                      font=(FONT, 9), width=24)
        self.route_box.grid(row=1, column=1, sticky="w", pady=(5, 0))
        self.route_box.bind("<<ComboboxSelected>>", lambda e: self._on_manual())

        lab("距离").grid(row=2, column=0, sticky="w", pady=(5, 0))
        self.dist_scale = ttk.Scale(grid, from_=DIST_MIN, to=DIST_MAX,
                                    orient="horizontal", length=430,
                                    command=lambda v: self._on_manual())
        self.dist_scale.grid(row=2, column=1, sticky="w", pady=(5, 0))
        self.dist_lbl = tk.Label(grid, text="", font=(FONT, 9), bg=CARD, fg=BLACK,
                                 anchor="w", width=12)
        self.dist_lbl.grid(row=2, column=2, sticky="w", padx=(14, 0))

        lab("配速").grid(row=3, column=0, sticky="w", pady=(5, 0))
        self.pace_scale = ttk.Scale(grid, from_=PACE_MIN_S, to=PACE_MAX_S,
                                    orient="horizontal", length=430,
                                    command=lambda v: self._on_manual())
        self.pace_scale.grid(row=3, column=1, sticky="w", pady=(5, 0))
        self.pace_lbl = tk.Label(grid, text="", font=(FONT, 9), bg=CARD, fg=BLACK,
                                 anchor="w", width=12)
        self.pace_lbl.grid(row=3, column=2, sticky="w", padx=(14, 0))

        tk.Label(plan, text="选方案可一键套用；也能自己拖路线、距离、配速（低于 3 公里可能不算打卡）。",
                 font=(FONT, 8), bg=CARD, fg=GRAY, anchor="w",
                 justify="left", wraplength=WRAP).pack(fill="x", padx=14, pady=(0, 6))
        self.plan_widgets = [self.preset_box, self.route_box,
                             self.dist_scale, self.pace_scale]

    def _current_plan(self):
        name = self.route_var.get()
        idx = ROUTE_NAMES.index(name) if name in ROUTE_NAMES else 0
        return (ROUTE_FILES[idx],
                round(float(self.dist_scale.get()), 1),
                int(round(float(self.pace_scale.get()))))

    @property
    def target_m(self):
        return int(round(self._current_plan()[1] * 1000))

    @property
    def pace(self):
        return self._current_plan()[2]

    def route_file(self):
        return self._current_plan()[0]

    def _sync_labels(self):
        _, km, pace = self._current_plan()
        self.preview_lbl.configure(text="%s · %s/公里 · 预计 %.0f 分钟" % (
            self.route_var.get(), fmt_pace(pace), km * pace / 60.0))
        self.dist_lbl.configure(text="%.1f 公里" % km)
        self.pace_lbl.configure(text="%s/公里" % fmt_pace(pace))
        self.step_labels[2].configure(text="上传这 %.1f 公里的跑步数据" % km)

    def _on_preset(self):
        if self.preset_var.get() != "自定义":
            try:
                idx = PRESET_LABELS.index(self.preset_var.get())
            except ValueError:
                idx = 0
            _, route, km, pace = PRESETS[idx]
            self._syncing = True
            try:
                self.route_var.set(ROUTE_NAMES[ROUTE_FILES.index(route)])
                self.dist_scale.set(km)
                self.pace_scale.set(pace)
            finally:
                self._syncing = False
        self._sync_labels()

    def _on_manual(self):
        if self._syncing:
            return
        self.preset_var.set("自定义")
        self._sync_labels()

    # ---- helpers ----
    def _act(self, name):
        fn = self.actions.get(name)
        if fn:
            fn()

    def say(self, text, color=BLACK):
        self.big.configure(text=text, fg=color)
        self._fit()

    def _fit(self):
        """文字变长时把窗口撑高，避免按钮被挤出屏幕。"""
        try:
            self.root.update_idletasks()
            need = self.root.winfo_reqheight() + 16
            sw = self.root.winfo_screenwidth()
            cap = self.root.winfo_screenheight() - 120
            h = min(max(self.root.winfo_height(), need, 760), cap)
            if h != self.root.winfo_height():
                x = max(0, (sw - WIN_W) // 2)
                self.root.geometry("%dx%d+%d+80" % (WIN_W, h, x))
        except Exception:
            pass

    def step(self, index, state):
        labels = {
            "wait":   ("…", GRAY),
            "doing":  ("进行中", BLUE),
            "done":   ("完成 ✓", GREEN),
            "skip":   ("不需要", GRAY),
            "fail":   ("失败 ✗", RED),
        }
        text, color = labels[state]
        self.marks[index].configure(text=text, fg=color)

    def render_status(self, info, today, cfg):
        rem = info.get("remaining", -1)
        sub = info.get("sub", "")
        broken = info.get("broken", False)

        if broken:
            color, state, remtxt, need_login = RED, "凭证异常", "需要重新登录", True
        elif not info.get("present"):
            color, state, remtxt, need_login = RED, "未登录", "需要先登录一次", True
        elif rem <= 0:
            color, state, remtxt, need_login = RED, "登录已过期", "需要重新登录", True
        elif rem < 1200:
            color, state = AMBER, "登录快过期"
            remtxt, need_login = "还剩 %s，跑步时会自动续" % fmt_rem(rem), False
        else:
            color, state = GREEN, "已登录"
            remtxt, need_login = "还剩 %s" % fmt_rem(rem), False

        self.dot.configure(fg=color)
        self.state_lbl.configure(text=state, fg=color)
        self.rem_lbl.configure(text=("· " + remtxt) if remtxt else "")
        self.uid_lbl.configure(text=mask_uid(sub))
        self.status_btn.configure(text="重新登录" if need_login else "刷新状态")
        self._need_login = need_login

        if today is None:
            self.today_lbl.configure(text="查询中…", fg=GRAY)
        elif today == "skip":
            self.today_lbl.configure(text="登录后可查看", fg=GRAY)
        elif not isinstance(today, (int, float)) or today < 0:
            self.today_lbl.configure(text="查询失败（网络问题，不影响跑步）", fg=GRAY)
        else:
            self.today_lbl.configure(
                text="%.2f 公里（学校上限 6 公里/日）" % (today / 1000.0),
                fg=BLACK if today < CAP_M else AMBER)

        warns = []
        if broken:
            warns.append("登录凭证读取失败（文件可能损坏），请点右上角「重新登录」。")
        if sub and cfg.get("user_id") and sub != cfg["user_id"]:
            warns.append("现在的登录账号（%s）和配置里的账号不一致，直接跑会失败。"
                         "点「更换账号」重新抓一次就能自动同步。" % mask_uid(sub))
        if warns:
            self.warn_lbl.configure(text="\n".join(warns))
            if not self.warn_lbl.winfo_ismapped():
                self.warn_lbl.pack(fill="x", padx=14, pady=(0, 10))
        else:
            self.warn_lbl.pack_forget()
        self._fit()

    def set_running(self, running):
        state = "disabled" if running else "normal"
        for b in (self.start_btn, self.change_btn, self.status_btn):
            b.configure(state=state)
        for w in getattr(self, "plan_widgets", []):
            if w in (getattr(self, "preset_box", None), getattr(self, "route_box", None)):
                w.configure(state="disabled" if running else "readonly")
            else:
                w.configure(state=state)

    def progress_on(self):
        self.bar.start(12)

    def progress_off(self):
        self.bar.stop()

    def set_elapsed(self, text):
        self.elapsed.configure(text=text)

    def log(self, line):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", line.rstrip() + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def toggle_detail(self):
        if self.log_shown:
            self.logbox.pack_forget()
            self.detail_btn.configure(text="显示技术详情")
        else:
            self.logbox.pack(fill="both", expand=False, padx=24, pady=(4, 0))
            self.detail_btn.configure(text="收起技术详情")
        self.log_shown = not self.log_shown
        self._fit()


class GuideDialog:
    """分步操作指引弹窗，适用于「重新登录」和「更换账号」。"""

    def __init__(self, root, title, heading, steps, note, button, on_start):
        import tkinter as tk

        self.root = root
        self.on_start = on_start
        self.busy = False
        self.top = tk.Toplevel(root)
        self.top.title(title)
        self.top.configure(bg="white")
        self.top.resizable(False, False)
        self.top.transient(root)

        tk.Label(self.top, text=heading, font=(FONT, 13, "bold"), bg="white",
                 fg=BLACK, anchor="w", justify="left").pack(fill="x", padx=24, pady=(20, 12))

        for num, title_text, detail in steps:
            row = tk.Frame(self.top, bg="white")
            row.pack(fill="x", padx=24, pady=(0, 10))
            tk.Label(row, text=num, font=(FONT, 10, "bold"), bg=BLUE, fg="white",
                     width=2).pack(side="left", anchor="n")
            col = tk.Frame(row, bg="white")
            col.pack(side="left", fill="x", expand=True, padx=(10, 0))
            tk.Label(col, text=title_text, font=(FONT, 10, "bold"), bg="white",
                     fg=BLACK, anchor="w").pack(fill="x")
            tk.Label(col, text=detail, font=(FONT, 9), bg="white", fg="#3f3f46",
                     anchor="w", justify="left").pack(fill="x")

        tk.Label(self.top, text=note, font=(FONT, 9), bg="#fffbeb", fg=AMBER,
                 anchor="w", justify="left", wraplength=800,
                 padx=10, pady=8).pack(fill="x", padx=24, pady=(2, 12))

        self.msg = tk.Label(self.top, text="", font=(FONT, 9), bg="white", fg=GREEN,
                            anchor="w", justify="left", wraplength=800)
        self.msg.pack(fill="x", padx=24)

        row = tk.Frame(self.top, bg="white")
        row.pack(fill="x", padx=24, pady=(12, 20))
        self.cancel_btn = tk.Button(row, text="取消", font=(FONT, 10), relief="groove",
                                    padx=16, pady=4, command=self._cancel)
        self.cancel_btn.pack(side="right")
        self.start_btn = tk.Button(row, text=button, font=(FONT, 10, "bold"),
                                   bg=BLUE, fg="white", relief="flat",
                                   padx=18, pady=5, command=self._start)
        self.start_btn.pack(side="right", padx=(0, 10))

        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.top.update_idletasks()
        w = 900
        h = max(self.top.winfo_reqheight() + 16, 420)
        x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2)
        y = max(0, self.root.winfo_rooty() + (self.root.winfo_height() - h) // 3)
        self.top.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.top.grab_set()

    def _start(self):
        if self.busy:
            return
        self.busy = True
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="disabled")
        self.set_msg("正在接管网络…", BLUE)
        self.on_start(self)

    def _cancel(self):
        if self.busy:
            return
        self._destroy()

    def set_msg(self, text, color=GREEN):
        import tkinter as tk

        def apply():
            try:
                self.msg.configure(text=text, fg=color)
            except tk.TclError:
                pass

        self.root.after(0, apply)

    def close(self):
        self.root.after(0, self._destroy)

    def _destroy(self):
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass


def run_flow(ui, root, finish, route_file, target_m, pace):
    km = target_m / 1000.0
    minutes = km * pace / 60.0
    route_name = ("路线 %d" % (ROUTE_FILES.index(route_file) + 1)
                  if route_file in ROUTE_FILES else route_file)
    log_path = os.path.join(REPO, "run_gui_%s.log" %
                            datetime.datetime.now().strftime("%Y%m%d_%H%M"))

    def bg(fn, *args):
        root.after(0, lambda: fn(*args))

    def log(msg):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        bg(ui.log, "[%s] %s" % (stamp, msg))
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n" % (stamp, msg))
        except OSError:
            pass

    log("开始：%s" % os.path.basename(log_path))
    for i in range(3):
        bg(ui.step, i, "wait")

    # 1) 登录状态
    bg(ui.step, 0, "doing")
    bg(ui.say, "先检查一下你的登录状态…")
    info = token_info()
    log("登录剩余 %s 秒，账号 %s" % (info["remaining"], mask_uid(info["sub"])))

    if (not info["present"]) or info["broken"] or info["remaining"] < 1200:
        bg(ui.say, "登录状态快过期了，需要你帮个忙：\n现在到微信里打开「企业咕咚」小程序，我等你 2 分钟。", AMBER)
        log("登录状态不足，开始续期")
        ok, err = refresh_token()
        if not ok:
            log("续期失败：%s" % err)
        info = token_info()
        log("续期后剩余 %s 秒，账号 %s" % (info["remaining"], mask_uid(info["sub"])))
        if (not info["present"]) or info["remaining"] < 1200:
            bg(ui.step, 0, "fail")
            bg(ui.say, "没能连上账号。请点右上角「重新登录」，\n或者手动运行一次 refresh.ps1，再点「开始跑步」。", RED)
            finish(False)
            return
        if sync_cfg_account(info["sub"]):
            log("已同步账号 %s" % mask_uid(info["sub"]))
        bg(ui.say, "已经连上账号了。\n请先把「企业咕咚」小程序关掉，我接着做下一步。", GREEN)
        time.sleep(2)

    bg(ui.step, 0, "done")

    # 2) 今天已经跑了多少（只做提示，不拦）
    bg(ui.step, 1, "doing")
    bg(ui.say, "看看今天已经跑了多少…")
    today = today_meters()
    log("今日累计 %s 米" % today)
    try:
        cfg = load_cfg()
    except Exception:
        cfg = {}

    if isinstance(today, int) and today >= CAP_M:
        bg(ui.say, "今天已经到 6 公里了。\n学校只算 6 公里/日，再多跑不会被计入——但我不拦你，照常开始。", AMBER)
    elif isinstance(today, int) and today > 0:
        bg(ui.say, "今天已经跑了 %.2f 公里，接着跑。" % (today / 1000.0))
    elif today == 0:
        bg(ui.say, "今天还没有记录，开始跑。")
    else:
        bg(ui.say, "今天跑了多少没查到，不影响，照常开始。")
    bg(ui.render_status, info, today, cfg)

    bg(ui.step, 1, "done")
    time.sleep(1)

    # 3) 提交
    bg(ui.step, 2, "doing")
    bg(ui.say, "正在跑 %s，%.1f 公里，大约要 %.0f 分钟。\n"
               "这段时间请不要打开「企业咕咚」小程序。" % (route_name, km, minutes), BLUE)
    bg(ui.progress_on)
    log("开始上传跑步数据: %s %.1fkm %s/km" % (route_name, km, fmt_pace(pace)))

    t0 = time.time()
    keep_state = {"alive": True}

    def tick():
        while keep_state["alive"]:
            used = int(time.time() - t0)
            bg(ui.set_elapsed, "已跑 %02d:%02d（总共约 %.0f 分钟）" % (used // 60, used % 60, minutes))
            time.sleep(5)

    threading.Thread(target=tick, daemon=True).start()

    keep_awake()
    ok = False
    rid = None
    try:
        proc = subprocess.Popen(
            [PY, "-u", "forge2.py", "--route", route_file, "--target", str(target_m), "--pace", str(pace)], cwd=REPO,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=CHILD_ENV, bufsize=1,
            creationflags=NO_WINDOW)
        for line in proc.stdout:
            log(line.rstrip())
            if "[OK] 记录ID" in line:
                ok = True
                try:
                    rid = line.split("[OK] 记录ID")[1].split(",")[0].strip()
                except Exception:
                    pass
        proc.wait()
        if proc.returncode != 0:
            log("上传程序退出码 %s" % proc.returncode)
    except Exception as e:
        log("上传出错：%s" % e)
    finally:
        keep_state["alive"] = False
        release_awake()
        bg(ui.progress_off)

    if ok:
        bg(ui.step, 2, "done")
        bg(ui.say, "跑完了！今天的记录号是 %s。\n\n现在可以打开「企业咕咚」看一眼这条记录；\n看完请关掉小程序，再关掉这个窗口。" % (rid or "?"), GREEN)
        bg(ui.set_elapsed, "用时 %s" % datetime.timedelta(seconds=int(time.time() - t0)))
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
    else:
        bg(ui.step, 2, "fail")
        bg(ui.say, "这次没成功，记录没有生成。\n请把「技术详情」里的内容发给我，我按里面的原因处理。", RED)
        bg(ui.set_elapsed, "")

    root.after(0, lambda: refresh_status(root, ui))
    finish(True)


def demo(root, ui):
    """不联网的界面预览：走一遍所有状态。"""
    def later(ms, fn, *a):
        root.after(ms, lambda: fn(*a))

    info = {"present": True, "remaining": 7 * 3600 + 32 * 60,
            "sub": "11111111-2222-3333-4444-555555555555", "broken": False}
    ui.render_status(info, 6011, CFG)
    ui.say("先检查一下你的登录状态…")
    ui.step(0, "doing")
    later(1200, ui.say, "已经连上账号了。")
    later(1200, ui.step, 0, "done")
    later(1600, ui.say, "看一看今天有没有已经跑过的记录…")
    later(1600, ui.step, 1, "doing")
    later(2600, ui.step, 1, "done")
    later(2600, ui.say, "正在跑，大约要 %.0f 分钟。\n这段时间请不要打开「企业咕咚」小程序。" % MINUTES, BLUE)
    later(2600, ui.step, 2, "doing")
    later(2600, ui.progress_on)
    later(2600, ui.set_elapsed, "已跑 00:00（总共约 %.0f 分钟）" % MINUTES)
    later(5000, ui.set_elapsed, "已跑 00:05（总共约 %.0f 分钟）" % MINUTES)
    later(6000, ui.progress_off)
    later(6000, ui.step, 2, "done")
    later(6000, ui.say, "跑完了！今天的记录号是 12345678。\n\n现在可以打开「企业咕咚」看一眼这条记录；\n看完请关掉小程序，再关掉这个窗口。", GREEN)
    later(6000, ui.set_elapsed, "用时 0:18:22")
    later(6000, ui.log, "[预览] 这是界面演示，没有真的上传数据")


def main():
    if "--check" in sys.argv:
        info = token_info()
        print("token_remaining:", info["remaining"])
        print("account:", mask_uid(info["sub"]))
        print("today_meters:", today_meters())
        print("dedup:", dedup_state())
        return 0

    import tkinter as tk
    from tkinter import messagebox

    enable_dpi_awareness()
    root = tk.Tk()
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
    except Exception:
        dpi = 96
    root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))

    ui = UI(root)
    root.update_idletasks()
    w = WIN_W
    h = min(max(root.winfo_reqheight() + 30, 760), root.winfo_screenheight() - 120)
    x = max(0, (root.winfo_screenwidth() - w) // 2)
    root.geometry("%dx%d+%d+%d" % (w, h, x, 80))

    done = {"v": False}
    busy = {"v": False}

    def finish(_ok):
        done["v"] = True

    # ---- 状态刷新 / 重新登录 ----
    def status_click():
        if busy["v"]:
            return
        info = token_info()
        if (not info["present"]) or info["broken"] or info["remaining"] <= 0:
            open_guide("relogin")
        else:
            refresh_status(root, ui)

    def open_guide(mode):
        if busy["v"]:
            return
        if mode == "change":
            dlg = GuideDialog(
                root,
                title="更换账号",
                heading="把跑步记录换到另一个微信号",
                steps=[
                    ("1", "先切微信账号（先别打开小程序）",
                     "点微信左下角的「☰」或头像 →「切换账号」；\n没有这个选项就选「退出登录」，再登录你要用的微信号。"),
                    ("2", "确认微信已经是新账号",
                     "微信主界面显示的是新账号，就说明切好了。"),
                    ("3", "回来点下面的蓝色按钮",
                     "按钮点下去之后，请在 2 分钟内到微信打开一次\n「企业咕咚」小程序，等它进到首页。"),
                ],
                note="抓不到新账号时，原来的账号不受影响，可以再试一次。\n"
                     "新账号的跑步速度仍按原来的配置计算；如果差得太多告诉我，我调一下。",
                button="开始抓取新账号",
                on_start=lambda d: start_change(d),
            )
        else:
            dlg = GuideDialog(
                root,
                title="重新登录",
                heading="登录过期了，重新登录一次",
                steps=[
                    ("1", "先切到微信（先别打开小程序）",
                     "确认微信里现在是你要用的那个账号。"),
                    ("2", "回来点下面的蓝色按钮",
                     "按钮点下去之后，请在 2 分钟内到微信打开一次\n「企业咕咚」小程序，等它进到首页。"),
                    ("3", "抓完把小程序关掉",
                     "我会提示你「登录成功了」，这时候关掉小程序再回来跑步。"),
                ],
                note="如果 2 分钟内没抓到，原来的登录不会被删除，可以再试一次。",
                button="开始登录",
                on_start=lambda d: start_relogin(d),
            )

    def start_change(dlg):
        busy["v"] = True
        ui.set_running(True)

        def work():
            try:
                old_sub = token_info()["sub"]
                try:
                    if os.path.exists(TOKEN_PATH):
                        shutil.copy2(TOKEN_PATH, TOKEN_BACKUP)
                except OSError as e:
                    dlg.set_msg("备份旧账号失败：%s" % e, RED)
                    note_switch("change", False, "backup failed: %s" % e)
                    time.sleep(1.5)
                    dlg.close()
                    return

                dlg.set_msg("正在接管网络…请现在到微信打开「企业咕咚」小程序（最多等 2 分钟）", BLUE)
                ok, err = refresh_token()
                if not ok:
                    dlg.set_msg("没抓到新账号的登录。原来的账号还在，可以再试一次。\n"
                                "请确认：微信已切到新账号，并且打开过小程序。", RED)
                    note_switch("change", False, err)
                    time.sleep(2)
                    dlg.close()
                    root.after(0, lambda: refresh_status(root, ui))
                    return

                info = token_info()
                new_sub = info["sub"]
                sync_cfg_account(new_sub)
                same = bool(new_sub) and new_sub == old_sub
                today = today_meters()
                note_switch("change", True, "new=%s today=%s" % (mask_uid(new_sub), today))

                if same:
                    dlg.set_msg("抓到的是原来的账号（%s），没有换成功。\n"
                                "请确认微信已经切到新的微信号，再试一次。" % mask_uid(new_sub), AMBER)
                    msg = ("抓到的是原来的账号（%s）。\n\n"
                           "多半是微信没有切过去，或者打开小程序时用的还是旧账号。" % mask_uid(new_sub))
                else:
                    dlg.set_msg("换好了，现在的账号是 %s。" % mask_uid(new_sub), GREEN)
                    msg = ("换好了，现在的账号是 %s。\n\n"
                           "跑步前请先关掉「企业咕咚」小程序，再点「开始跑步」。" % mask_uid(new_sub))
                    if today is not None and (not isinstance(today, int) or today < 0):
                        msg += ("\n\n注意：查不到这个账号今天的记录。"
                                "如果新账号是别的学校的，还需要改学校编号——遇到就把这个提示告诉我。")

                time.sleep(2.5)
                dlg.close()
                root.after(0, lambda: refresh_status(root, ui))
                root.after(0, lambda: messagebox.showinfo("更换账号", msg))
            except Exception as e:
                dlg.set_msg("操作出错：%s" % e, RED)
                note_switch("change", False, "error: %s" % e)
                time.sleep(1.5)
                dlg.close()
            finally:
                busy["v"] = False
                root.after(0, lambda: ui.set_running(False))

        threading.Thread(target=work, daemon=True).start()

    def start_relogin(dlg):
        busy["v"] = True
        ui.set_running(True)

        def work():
            try:
                dlg.set_msg("正在接管网络…请现在到微信打开「企业咕咚」小程序（最多等 2 分钟）", BLUE)
                ok, err = refresh_token()
                if not ok:
                    dlg.set_msg("登录没有成功：%s\n请稍后再点一次。" % err, RED)
                    note_switch("relogin", False, err)
                    time.sleep(2.5)
                    dlg.close()
                    root.after(0, lambda: refresh_status(root, ui))
                    return
                info = token_info()
                sync_cfg_account(info["sub"])
                dlg.set_msg("登录成功了。请先关掉「企业咕咚」小程序，再回来点「开始跑步」。", GREEN)
                note_switch("relogin", True, "account=%s" % mask_uid(info["sub"]))
                time.sleep(2.5)
                dlg.close()
                root.after(0, lambda: refresh_status(root, ui))
            except Exception as e:
                dlg.set_msg("操作出错：%s" % e, RED)
                note_switch("relogin", False, "error: %s" % e)
                time.sleep(1.5)
                dlg.close()
            finally:
                busy["v"] = False
                root.after(0, lambda: ui.set_running(False))

        threading.Thread(target=work, daemon=True).start()

    # ---- 开始跑步 ----
    def start_click():
        if busy["v"]:
            return
        busy["v"] = True
        ui.set_running(True)
        for b in (ui.start_btn,):
            b.configure(text="正在跑…")
        plan = (ui.route_file(), ui.target_m, ui.pace)

        def work():
            try:
                run_flow(ui, root, finish, *plan)
            except Exception as e:
                root.after(0, lambda: ui.say("出错了：%s" % e, RED))
            finally:
                busy["v"] = False
                root.after(0, lambda: ui.set_running(False))
                root.after(0, lambda: ui.start_btn.configure(text="开始跑步"))

        threading.Thread(target=work, daemon=True).start()

    ui.actions["status"] = status_click
    ui.actions["change"] = lambda: open_guide("change")
    ui.actions["start"] = start_click

    if "--uitest" in sys.argv:
        info = {"present": True, "remaining": 7 * 3600 + 32 * 60,
                "sub": "11111111-2222-3333-4444-555555555555", "broken": False}
        ui.render_status(info, 6011, CFG)
        for i, state in enumerate(("done", "done", "done")):
            ui.step(i, state)
        ui.set_elapsed("用时 0:18:22")
        ui.say("跑完了！今天的记录号是 12345678。\n\n现在可以打开「企业咕咚」看一眼这条记录；\n看完请关掉小程序，再关掉这个窗口。", GREEN)
        root.attributes("-topmost", True)
        root.mainloop()
        return 0
    if "--demo" in sys.argv:
        root.attributes("-topmost", True)
        demo(root, ui)
        root.after(11000, root.destroy)
        root.mainloop()
        return 0

    root.after(150, lambda: refresh_status(root, ui))

    asked = {"v": False}

    def on_close():
        if not done["v"] and not asked["v"]:
            asked["v"] = True
            if not messagebox.askyesno("咕咚一键跑步",
                                       "还在跑，确定要关掉这个窗口吗？\n（关掉后跑步数据可能传不完）"):
                asked["v"] = False
                return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
