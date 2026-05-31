from __future__ import annotations

import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import json
import urllib.parse
import urllib.request
import webbrowser
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from model_engine import BEIJING_TZ, ModelSignalEngine, load_models
from notifier import PushPlusNotifier

try:
    from build_info import BUILD_SHA
except Exception:
    BUILD_SHA = "dev"

DEFAULT_INTERVAL_SECONDS = 30
PUSHPLUS_TOKEN_URL = "https://www.pushplus.plus/"
NEWS_SEARCH_URL = "https://market.ft.tech/data/api/v1/market/data/semantic-search-news"
LATEST_RELEASE_API = "https://api.github.com/repos/Roypic/Astock_t/releases/latest"
RELEASE_PAGE_URL = "https://github.com/Roypic/Astock_t/releases/latest"
EXE_ASSET_NAME = "AShareTSignalMonitor.exe"
INFO_QUERIES = (
    "剑桥科技 CPO 光模块",
    "东山精密 PCB AI服务器 光模块",
    "福晶科技 激光晶体 光学 光通信",
    "利通电子 PCB 电子元器件 AI服务器",
    "CPO 光模块 AI算力",
    "PCB AI服务器",
)
COLORS = {
    "bg": "#F6F3EC",
    "card": "#FFFDF7",
    "card_soft": "#FDF8EE",
    "text": "#2F3834",
    "muted": "#748079",
    "line": "#E7DDCF",
    "sage": "#6E927C",
    "sage_dark": "#527261",
    "coral": "#D87A68",
    "coral_dark": "#BE6352",
    "mint": "#DDECE3",
    "cream": "#FFF7DF",
    "danger": "#B45C5C",
}


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def ensure_default_models() -> Path:
    target = app_dir() / "models"
    target.mkdir(parents=True, exist_ok=True)
    source = bundled_dir() / "models"
    if source.exists():
        for model_file in source.glob("*.json"):
            dest = target / model_file.name
            if not dest.exists() or dest.read_text(encoding="utf-8") != model_file.read_text(encoding="utf-8"):
                shutil.copy2(model_file, dest)
    return target


class MonitorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("A股做T信号监控")
        self.root.geometry("1060x780")
        self.root.minsize(940, 680)
        self.root.configure(bg=COLORS["bg"])

        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.models_dir = ensure_default_models()
        self.mascot_x = 86
        self.mascot_start_x = 86
        self.mascot_target_x = 86
        self.mascot_jump_frame = 0
        self.mascot_jump_frames = 0
        self.mascot_idle_step = 0

        self.token_var = tk.StringVar()
        self.model_path_var = tk.StringVar(value=str(self.models_dir))
        self.interval_var = tk.StringVar(value=str(DEFAULT_INTERVAL_SECONDS))
        self.status_var = tk.StringVar(value="未启动")
        self.risk_var = tk.StringVar(value="模型风险摘要：选择模型后显示回测胜率、最大回撤等信息。")

        self._configure_style()
        self._build_ui()
        self._refresh_risk_summary(self.models_dir)
        self.root.after(300, self._drain_queue)

    def _configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Card.TFrame", background=COLORS["card"], relief="flat")
        style.configure("Soft.TFrame", background=COLORS["card_soft"], relief="flat")
        style.configure("Muted.TLabel", background=COLORS["card"], foreground=COLORS["muted"])
        style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=("Microsoft YaHei UI", 10))
        style.configure("CardTitle.TLabel", background=COLORS["card"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=COLORS["line"], lightcolor=COLORS["line"], darkcolor=COLORS["line"], padding=8)
        style.configure("Primary.TButton", background=COLORS["sage"], foreground="#FFFFFF", bordercolor=COLORS["sage"], focusthickness=0, padding=(14, 9), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", COLORS["sage_dark"]), ("disabled", COLORS["line"])])
        style.configure("Warm.TButton", background=COLORS["coral"], foreground="#FFFFFF", bordercolor=COLORS["coral"], focusthickness=0, padding=(14, 9), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Warm.TButton", background=[("active", COLORS["coral_dark"]), ("disabled", COLORS["line"])])
        style.configure("Ghost.TButton", background=COLORS["card_soft"], foreground=COLORS["text"], bordercolor=COLORS["line"], focusthickness=0, padding=(12, 8))
        style.map("Ghost.TButton", background=[("active", COLORS["mint"])])
        style.configure("Treeview", background="#FFFDF7", fieldbackground="#FFFDF7", foreground=COLORS["text"], rowheight=34, bordercolor=COLORS["line"], lightcolor=COLORS["line"], darkcolor=COLORS["line"])
        style.configure("Treeview.Heading", background=COLORS["mint"], foreground=COLORS["text"], relief="flat", padding=(8, 8), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Treeview", background=[("selected", COLORS["sage"])], foreground=[("selected", "#FFFFFF")])

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 14))
        header.columnconfigure(0, weight=1)
        title_block = ttk.Frame(header)
        title_block.grid(row=0, column=0, sticky=tk.W)
        ttk.Label(title_block, text="A股做T信号监控", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(title_block, text="模型驱动的盘中提醒，界面观察 + 微信推送", style="Subtitle.TLabel").pack(anchor=tk.W, pady=(4, 0))
        self.mascot = tk.Canvas(header, width=170, height=92, bg=COLORS["bg"], highlightthickness=0, cursor="hand2")
        self.mascot.grid(row=0, column=1, sticky=tk.E)
        self.mascot.bind("<Button-1>", self._mascot_jump_away)
        self._animate_mascot()

        form = ttk.Frame(outer, style="Card.TFrame", padding=16)
        form.pack(fill=tk.X, pady=(0, 12))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="配置", style="CardTitle.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 12), pady=(0, 10))
        self.status_badge = tk.Label(
            form,
            textvariable=self.status_var,
            bg=COLORS["cream"],
            fg=COLORS["sage_dark"],
            padx=12,
            pady=5,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.status_badge.grid(row=0, column=3, sticky=tk.E, pady=(0, 10))

        ttk.Label(form, text="模型文件/文件夹", style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W, padx=(0, 12), pady=7)
        ttk.Entry(form, textvariable=self.model_path_var).grid(row=1, column=1, sticky=tk.EW, padx=(0, 8), pady=7)
        ttk.Button(form, text="选择文件", style="Ghost.TButton", command=self._choose_file).grid(row=1, column=2, padx=4, pady=7)
        ttk.Button(form, text="选择文件夹", style="Ghost.TButton", command=self._choose_dir).grid(row=1, column=3, padx=(4, 0), pady=7)

        ttk.Label(form, text="PushPlus token", style="Muted.TLabel").grid(row=2, column=0, sticky=tk.W, padx=(0, 12), pady=7)
        ttk.Entry(form, textvariable=self.token_var, show="*").grid(row=2, column=1, sticky=tk.EW, padx=(0, 8), pady=7)
        ttk.Label(form, text="间隔秒", style="Muted.TLabel").grid(row=2, column=2, sticky=tk.E, padx=4, pady=7)
        ttk.Entry(form, textvariable=self.interval_var, width=8).grid(row=2, column=3, sticky=tk.W, padx=(8, 0), pady=7)

        self.risk_label = tk.Label(
            form,
            textvariable=self.risk_var,
            bg=COLORS["cream"],
            fg=COLORS["coral_dark"],
            justify=tk.LEFT,
            anchor=tk.W,
            padx=12,
            pady=8,
            wraplength=850,
            font=("Microsoft YaHei UI", 9),
        )
        self.risk_label.grid(row=3, column=0, columnspan=4, sticky=tk.EW, pady=(8, 0))

        help_frame = ttk.Frame(form, style="Soft.TFrame", padding=12)
        help_frame.grid(row=4, column=0, columnspan=4, sticky=tk.EW, pady=(10, 0))
        help_frame.columnconfigure(0, weight=1)
        help_text = (
            "PushPlus 使用：1. 打开 PushPlus 官网并用微信扫码登录；"
            "2. 在「一对一推送」页面复制 token；"
            "3. 粘贴到上方 token 输入框；"
            "4. 点「测试推送」，手机微信收到测试消息后再点「开始监控」。"
        )
        tk.Label(
            help_frame,
            text=help_text,
            bg=COLORS["card_soft"],
            fg=COLORS["text"],
            justify=tk.LEFT,
            anchor=tk.W,
            wraplength=780,
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=0, sticky=tk.EW)
        ttk.Button(help_frame, text="打开 PushPlus", style="Ghost.TButton", command=self._open_pushplus).grid(row=0, column=1, padx=(12, 0))

        controls = ttk.Frame(outer)
        controls.pack(fill=tk.X, pady=(0, 12))
        ttk.Button(controls, text="模型盘前", style="Ghost.TButton", command=self._show_premarket_analysis).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="信息面盘前", style="Ghost.TButton", command=self._show_info_premarket).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="自选信息面", style="Ghost.TButton", command=self._open_custom_info_window).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="更新程序", style="Ghost.TButton", command=self._check_update).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="测试推送", style="Ghost.TButton", command=self._test_push).pack(side=tk.LEFT)
        ttk.Button(controls, text="开始监控", style="Primary.TButton", command=self._start).pack(side=tk.LEFT, padx=8)
        ttk.Button(controls, text="停止", style="Warm.TButton", command=self._stop).pack(side=tk.LEFT)

        columns = ("symbol", "code", "status", "action", "price", "target", "stop", "minute", "score", "message")
        table_card = ttk.Frame(outer, style="Card.TFrame", padding=12)
        table_card.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
        ttk.Label(table_card, text="监控列表", style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 8))
        self.table = ttk.Treeview(table_card, columns=columns, show="headings", height=10)
        headings = {
            "symbol": "股票",
            "code": "代码",
            "status": "状态",
            "action": "信号",
            "price": "入场/现价",
            "target": "目标价",
            "stop": "止损价",
            "minute": "时间",
            "score": "评分",
            "message": "说明",
        }
        widths = {
            "symbol": 100,
            "code": 112,
            "status": 82,
            "action": 100,
            "price": 72,
            "target": 76,
            "stop": 76,
            "minute": 70,
            "score": 64,
            "message": 300,
        }
        for col in columns:
            self.table.heading(col, text=headings[col])
            self.table.column(col, width=widths[col], anchor=tk.W)
        self.table.tag_configure("even", background="#FFFDF7")
        self.table.tag_configure("odd", background="#F8F1E6")
        self.table.tag_configure("signal", background="#FBE3DA", foreground=COLORS["coral_dark"])
        self.table.tag_configure("error", background="#F5DDDD", foreground=COLORS["danger"])
        self.table.pack(fill=tk.BOTH, expand=True)

        log_frame = ttk.Frame(outer, style="Card.TFrame", padding=12)
        log_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(log_frame, text="运行日志 / 信号", style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 8))
        self.log = tk.Text(
            log_frame,
            height=9,
            wrap=tk.WORD,
            bg="#2F3834",
            fg="#F9F4E8",
            insertbackground="#F9F4E8",
            relief=tk.FLAT,
            padx=12,
            pady=10,
            font=("Cascadia Mono", 10),
        )
        self.log.pack(fill=tk.BOTH, expand=True)

    def _animate_mascot(self) -> None:
        if not hasattr(self, "mascot"):
            return
        if self.mascot_jump_frames:
            progress = self.mascot_jump_frame / self.mascot_jump_frames
            self.mascot_x = self.mascot_start_x + (self.mascot_target_x - self.mascot_start_x) * progress
            y_offset = -34 * (1 - (2 * progress - 1) ** 2)
            self.mascot_jump_frame += 1
            if self.mascot_jump_frame > self.mascot_jump_frames:
                self.mascot_jump_frames = 0
                self.mascot_x = self.mascot_target_x
        else:
            self.mascot_idle_step = (self.mascot_idle_step + 1) % 40
            y_offset = -4 if self.mascot_idle_step < 20 else 0
        self._draw_mascot(self.mascot_x, 62 + y_offset)
        self.root.after(90, self._animate_mascot)

    def _mascot_jump_away(self, _event: tk.Event) -> None:
        self.mascot_start_x = self.mascot_x
        candidates = [42, 86, 128]
        far_choices = [x for x in candidates if abs(x - self.mascot_x) > 24]
        self.mascot_target_x = random.choice(far_choices or candidates)
        self.mascot_jump_frame = 0
        self.mascot_jump_frames = 18
        self._log("小伙伴跳开了，继续陪你盯盘")

    def _draw_mascot(self, x: float, y: float) -> None:
        c = self.mascot
        c.delete("mascot")
        body = "#9DB7A7"
        body_dark = "#779887"
        belly = "#F8E9D0"
        ink = "#2F3834"
        blush = "#E8A39A"
        c.create_oval(x - 34, 79, x + 34, 88, fill="#E8DDCF", outline="", tags="mascot")
        c.create_oval(x - 35, y - 42, x + 35, y + 20, fill=body, outline=body_dark, width=2, tags="mascot")
        c.create_oval(x - 48, y - 55, x - 18, y - 22, fill=body, outline=body_dark, width=2, tags="mascot")
        c.create_oval(x + 18, y - 55, x + 48, y - 22, fill=body, outline=body_dark, width=2, tags="mascot")
        c.create_oval(x - 26, y - 12, x + 26, y + 22, fill=belly, outline="#E7D3B6", width=1, tags="mascot")
        c.create_oval(x - 16, y - 24, x - 9, y - 17, fill=ink, outline="", tags="mascot")
        c.create_oval(x + 9, y - 24, x + 16, y - 17, fill=ink, outline="", tags="mascot")
        c.create_oval(x - 14, y - 22, x - 12, y - 20, fill="#FFFFFF", outline="", tags="mascot")
        c.create_oval(x + 11, y - 22, x + 13, y - 20, fill="#FFFFFF", outline="", tags="mascot")
        c.create_oval(x - 4, y - 15, x + 4, y - 8, fill=ink, outline="", tags="mascot")
        c.create_arc(x - 9, y - 12, x, y - 2, start=220, extent=100, style=tk.ARC, outline=ink, width=2, tags="mascot")
        c.create_arc(x, y - 12, x + 9, y - 2, start=220, extent=100, style=tk.ARC, outline=ink, width=2, tags="mascot")
        c.create_oval(x - 28, y - 12, x - 18, y - 4, fill=blush, outline="", tags="mascot")
        c.create_oval(x + 18, y - 12, x + 28, y - 4, fill=blush, outline="", tags="mascot")
        for offset in (-4, 1, 6):
            c.create_line(x - 10, y - 9 + offset, x - 30, y - 13 + offset, fill=ink, width=1, tags="mascot")
            c.create_line(x + 10, y - 9 + offset, x + 30, y - 13 + offset, fill=ink, width=1, tags="mascot")
        c.create_line(x - 13, y + 3, x - 5, y + 9, x - 13, y + 14, fill=body_dark, smooth=True, width=2, tags="mascot")
        c.create_line(x + 13, y + 3, x + 5, y + 9, x + 13, y + 14, fill=body_dark, smooth=True, width=2, tags="mascot")
        c.create_oval(x + 24, y - 48, x + 34, y - 38, fill="#B9D6A6", outline="#88A979", width=1, tags="mascot")
        c.create_line(x + 24, y - 39, x + 31, y - 50, fill="#88A979", width=1, tags="mascot")

    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择模型 JSON",
            initialdir=str(self.models_dir),
            filetypes=[("JSON 模型", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self.model_path_var.set(path)
            self._refresh_risk_summary(Path(path))

    def _open_pushplus(self) -> None:
        webbrowser.open(PUSHPLUS_TOKEN_URL)

    def _choose_dir(self) -> None:
        path = filedialog.askdirectory(title="选择模型文件夹", initialdir=str(self.models_dir))
        if path:
            self.model_path_var.set(path)
            self._refresh_risk_summary(Path(path))

    def _refresh_risk_summary(self, model_path: Path) -> None:
        self.risk_var.set(self._model_risk_summary(model_path))

    def _model_files(self, model_path: Path) -> list[Path]:
        if model_path.is_file():
            return [model_path]
        if model_path.is_dir():
            return sorted(model_path.glob("*.json"))
        return []

    def _model_risk_summary(self, model_path: Path) -> str:
        parts = []
        for file in self._model_files(model_path):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except Exception:
                continue
            backtest = data.get("backtest") or {}
            name = data.get("name", file.stem)
            if backtest:
                parts.append(
                    f"{name}：{backtest.get('window', '回测')}，{backtest.get('mode', '模型信号')}，"
                    f"交易 {backtest.get('trade_count', '-')} 次，胜率 {backtest.get('win_rate_pct', '-')}%，"
                    f"单次均值 {backtest.get('avg_result_pct', '-')}%，最大回撤 {backtest.get('max_drawdown_pct', '-')}%。"
                )
            else:
                parts.append(f"{name}：未写入回测摘要，请谨慎使用。")
        if not parts:
            return "模型风险摘要：没有找到模型 JSON。"
        return "模型风险摘要：" + "  ".join(parts) + " 历史回测不代表未来收益，请小心使用。"

    def _test_push(self) -> None:
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("缺少 token", "请先输入 PushPlus token。")
            return
        try:
            PushPlusNotifier(token).send_text("做T提醒测试：GUI 监控程序已接通。", title="做T提醒测试")
            self._log("PushPlus 测试消息已发送")
            messagebox.showinfo("成功", "测试消息已发送。")
        except Exception as exc:
            messagebox.showerror("推送失败", str(exc))

    def _show_premarket_analysis(self) -> None:
        model_path = Path(self.model_path_var.get().strip())
        if not model_path.exists():
            messagebox.showwarning("模型不存在", "请选择模型 JSON 文件或模型文件夹。")
            return
        try:
            analysis = self._build_premarket_analysis(model_path)
        except Exception as exc:
            messagebox.showerror("盘前分析失败", str(exc))
            return
        self._log("已生成盘前分析")
        self._open_text_window("盘前分析", analysis)

    def _show_info_premarket(self) -> None:
        self.status_var.set("正在获取信息面")
        self.status_badge.configure(bg=COLORS["cream"], fg=COLORS["sage_dark"])
        self._log("正在获取信息面盘前摘要")
        threading.Thread(target=self._run_info_premarket, daemon=True).start()

    def _open_custom_info_window(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("自选信息面搜索")
        window.geometry("820x620")
        window.configure(bg=COLORS["bg"])

        panel = ttk.Frame(window, style="Card.TFrame", padding=14)
        panel.pack(fill=tk.X, padx=14, pady=(14, 8))
        panel.columnconfigure(1, weight=1)

        query_var = tk.StringVar()
        days_var = tk.StringVar(value="3")
        status_var = tk.StringVar(value="输入股票名/代码/主题关键词后搜索。")

        ttk.Label(panel, text="关键词", style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        entry = ttk.Entry(panel, textvariable=query_var)
        entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 10))
        ttk.Label(panel, text="天数", style="Muted.TLabel").grid(row=0, column=2, sticky=tk.E, padx=(0, 8))
        ttk.Entry(panel, textvariable=days_var, width=6).grid(row=0, column=3, sticky=tk.W, padx=(0, 10))

        result = tk.Text(
            window,
            wrap=tk.WORD,
            bg=COLORS["card"],
            fg=COLORS["text"],
            relief=tk.FLAT,
            padx=16,
            pady=14,
            font=("Microsoft YaHei UI", 10),
        )
        result.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        tk.Label(
            window,
            textvariable=status_var,
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            anchor=tk.W,
            font=("Microsoft YaHei UI", 9),
        ).pack(fill=tk.X, padx=16, pady=(0, 10))

        def run_search() -> None:
            query = query_var.get().strip()
            if not query:
                messagebox.showwarning("缺少关键词", "请输入股票名、代码或主题关键词。")
                return
            try:
                days = max(1, min(14, int(days_var.get().strip())))
            except ValueError:
                messagebox.showwarning("天数无效", "天数请输入 1-14 的数字。")
                return
            result.configure(state=tk.NORMAL)
            result.delete("1.0", tk.END)
            result.insert(tk.END, "正在搜索，请稍候...\n")
            result.configure(state=tk.DISABLED)
            status_var.set("正在搜索信息面...")

            def worker() -> None:
                try:
                    content = self._build_custom_info_report(query, days)
                    self.queue.put(("custom_info_result", {"text": result, "status": status_var, "content": content}))
                except Exception as exc:
                    self.queue.put(("custom_info_result", {"text": result, "status": status_var, "content": f"搜索失败：{exc}"}))

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(panel, text="搜索", style="Primary.TButton", command=run_search).grid(row=0, column=4, sticky=tk.E)
        entry.bind("<Return>", lambda _event: run_search())
        entry.focus_set()

    def _build_custom_info_report(self, query: str, days: int) -> str:
        now = datetime.now(BEIJING_TZ)
        start = now - timedelta(days=days)
        items = self._search_news(query, start, now, limit=12)
        lines = [
            f"自选信息面：{query}",
            f"北京时间 {now.strftime('%Y-%m-%d %H:%M')}，范围：最近 {days} 天新闻语义检索。",
            "仅支持查看最近半个月以内的新闻数据。",
            "",
        ]
        if not items:
            lines.append("暂未检索到相关新闻。可以换成公司简称、股票代码或行业关键词再试。")
            return "\n".join(lines)

        stance_counts = {"利好": 0, "风险": 0, "中性": 0}
        for item in items:
            stance = self._news_stance(item)
            stance_counts[stance] += 1
            title = self._clean_text(str(item.get("title") or "无标题"), 90)
            source = item.get("source_site") or item.get("media_name") or "未知来源"
            publish = str(item.get("publish_time") or item.get("fetch_time") or "-").replace("T", " ")[:16]
            url = item.get("article_url") or ""
            lines.append(f"- [{stance}] {publish} {source}：{title}")
            if url:
                lines.append(f"  {url}")
        summary = " / ".join(f"{k}{v}" for k, v in stance_counts.items() if v)
        lines.extend(
            [
                "",
                f"小结：{summary or '中性'}。",
                "风险提醒：这是新闻语义搜索和关键词分类，不是投资建议；重要消息请打开原文和公告核对。",
            ]
        )
        return "\n".join(lines)

    def _check_update(self) -> None:
        if not getattr(sys, "frozen", False):
            webbrowser.open(RELEASE_PAGE_URL)
            messagebox.showinfo("源码模式", "当前不是打包后的 EXE，已打开最新版下载页面。")
            return
        self.status_var.set("正在检查更新")
        self.status_badge.configure(bg=COLORS["cream"], fg=COLORS["sage_dark"])
        self._log("正在从 GitHub 检查并下载最新版")
        threading.Thread(target=self._run_update_download, daemon=True).start()

    def _run_update_download(self) -> None:
        try:
            update = self._download_latest_exe()
            if update.get("current"):
                self.queue.put(("update_current", update))
                self.queue.put(("log", "当前已经是最新版，无需更新"))
                return
            self.queue.put(("update_ready", update))
            self.queue.put(("log", f"更新包已下载：{Path(str(update['file'])).name}"))
        except Exception as exc:
            self.queue.put(("update_error", str(exc)))
            self.queue.put(("log", f"更新失败：{exc}"))

    def _download_latest_exe(self) -> dict[str, object]:
        req = urllib.request.Request(
            LATEST_RELEASE_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "AShareTSignalMonitor/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            release = json.loads(resp.read().decode("utf-8"))
        release_sha = str(release.get("target_commitish") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", release_sha):
            match = re.search(r"Commit:\s*([0-9a-fA-F]{7,40})", str(release.get("body") or ""))
            release_sha = match.group(1) if match else ""
        release_short = release_sha[:7] if release_sha else ""
        current_short = str(BUILD_SHA)[:7]
        if current_short != "dev" and release_short == current_short:
            return {
                "current": True,
                "updated_at": str(release.get("published_at") or "-"),
                "sha": release_short,
            }

        assets = release.get("assets", [])
        asset = next((item for item in assets if item.get("name") == EXE_ASSET_NAME), None)
        if not asset:
            raise RuntimeError("最新版 release 中没有找到 EXE 文件")

        download_url = asset.get("browser_download_url")
        if not download_url:
            raise RuntimeError("最新版 EXE 缺少下载链接")

        updates_dir = app_dir() / "updates"
        updates_dir.mkdir(parents=True, exist_ok=True)
        target = updates_dir / f"{EXE_ASSET_NAME}.download"
        req = urllib.request.Request(download_url, headers={"User-Agent": "AShareTSignalMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, target.open("wb") as out:
            shutil.copyfileobj(resp, out)
        if target.stat().st_size < 1024 * 1024:
            raise RuntimeError("下载文件过小，可能不是有效 EXE")
        with target.open("rb") as handle:
            if handle.read(2) != b"MZ":
                raise RuntimeError("下载文件不是有效 Windows EXE，请稍后重试")
        return {
            "current": False,
            "file": str(target),
            "updated_at": str(asset.get("updated_at") or release.get("published_at") or "-"),
            "sha": release_short,
        }

    def _install_update(self, update_file: Path) -> None:
        current_exe = Path(sys.executable).resolve()
        updater_dir = app_dir() / "updates"
        updater_dir.mkdir(parents=True, exist_ok=True)
        batch = updater_dir / "apply_update.bat"
        script = f"""@echo off
setlocal
chcp 65001 >nul
set "SRC={update_file}"
set "DST={current_exe}"
set "APPDIR={app_dir()}"
set "OLD={current_exe}.old"
set "LOG={app_dir()}\\update.log"
set "PID={os.getpid()}"
set "BAT=%~f0"
echo [%date% %time%] update started > "%LOG%"
for /l %%i in (1,1,20) do (
  tasklist /fi "PID eq %PID%" | find "%PID%" >nul
  if errorlevel 1 goto replace
  timeout /t 1 /nobreak >nul
)
taskkill /pid %PID% /f >nul 2>nul
timeout /t 1 /nobreak >nul
:replace
if not exist "%SRC%" goto failed
if exist "%OLD%" del /f /q "%OLD%" >nul 2>nul
move /y "%DST%" "%OLD%" >> "%LOG%" 2>&1
move /y "%SRC%" "%DST%" >> "%LOG%" 2>&1
if errorlevel 1 goto restore
start "" "%DST%"
del /f /q "%OLD%" >nul 2>nul
del /f /q "%BAT%" >nul 2>nul
exit /b 0
:restore
if exist "%OLD%" if not exist "%DST%" move /y "%OLD%" "%DST%" >> "%LOG%" 2>&1
:failed
echo [%date% %time%] update failed >> "%LOG%"
start "" "%APPDIR%"
mshta "javascript:alert('自动更新失败，请手动下载最新版覆盖。详情见 update.log');close()"
exit /b 1
"""
        batch.write_text(script, encoding="gbk", errors="ignore")
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(["cmd.exe", "/c", str(batch)], cwd=str(app_dir()), creationflags=flags)
        os._exit(0)

    def _run_info_premarket(self) -> None:
        try:
            content = self._build_info_premarket()
            self.queue.put(("info_premarket", content))
            self.queue.put(("log", "信息面盘前摘要已生成"))
        except Exception as exc:
            self.queue.put(("log", f"信息面盘前失败：{exc}"))
            self.queue.put(("info_error", str(exc)))

    def _build_info_premarket(self) -> str:
        now = datetime.now(BEIJING_TZ)
        start = now - timedelta(days=3)
        results = []
        seen = set()
        for query in INFO_QUERIES:
            for item in self._search_news(query, start, now, limit=5):
                key = item.get("article_url") or item.get("news_id") or item.get("title")
                if key in seen:
                    continue
                seen.add(key)
                item["_query"] = query
                results.append(item)

        results.sort(key=lambda item: str(item.get("publish_time") or item.get("fetch_time") or ""), reverse=True)
        lines = [
            f"信息面盘前汇总（北京时间 {now.strftime('%Y-%m-%d %H:%M')}）",
            "范围：最近3天新闻语义检索；仅支持查看最近半个月以内的新闻数据。",
            "",
        ]
        if not results:
            lines.append("暂未检索到相关信息。盘中仍按实时信号和个人风控执行。")
            return "\n".join(lines)

        grouped = self._group_news(results[:24])
        for group_name in ("剑桥科技", "东山精密", "福晶科技", "利通电子", "CPO/光模块", "PCB/AI服务器", "其他"):
            items = grouped.get(group_name, [])
            if not items:
                continue
            lines.append(f"{group_name}：")
            stance_counts = {"利好": 0, "风险": 0, "中性": 0}
            for item in items[:5]:
                stance = self._news_stance(item)
                stance_counts[stance] += 1
                title = self._clean_text(str(item.get("title") or "无标题"), 70)
                source = item.get("source_site") or item.get("media_name") or "未知来源"
                publish = str(item.get("publish_time") or item.get("fetch_time") or "-").replace("T", " ")[:16]
                url = item.get("article_url") or ""
                lines.append(f"- [{stance}] {publish} {source}：{title}")
                if url:
                    lines.append(f"  {url}")
            summary = " / ".join(f"{k}{v}" for k, v in stance_counts.items() if v)
            lines.append(f"  小结：{summary or '中性'}；盘前只看信息方向，入场仍等待盘中模型信号。")
            lines.append("")

        lines.append("风险提醒：新闻标题和摘要只能辅助判断情绪，不能替代公告原文、交易所披露和盘中量价确认。")
        return "\n".join(lines)

    def _search_news(self, query: str, start: datetime, end: datetime, limit: int = 5) -> list[dict[str, object]]:
        params = {
            "query": query,
            "limit": str(limit),
            "year": str(end.year),
            "start_time": start.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        }
        url = NEWS_SEARCH_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "AShareTSignalMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("data", "results", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def _group_news(self, items: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
        groups: dict[str, list[dict[str, object]]] = {
            "剑桥科技": [],
            "东山精密": [],
            "福晶科技": [],
            "利通电子": [],
            "CPO/光模块": [],
            "PCB/AI服务器": [],
            "其他": [],
        }
        for item in items:
            text = f"{item.get('title', '')} {item.get('summary', '')} {item.get('content', '')} {item.get('_query', '')}"
            placed = False
            for name in ("剑桥科技", "东山精密", "福晶科技", "利通电子"):
                if name in text:
                    groups[name].append(item)
                    placed = True
            if any(word in text for word in ("CPO", "光模块", "光通信", "算力")):
                groups["CPO/光模块"].append(item)
                placed = True
            if any(word in text for word in ("PCB", "AI服务器", "服务器", "电子元器件")):
                groups["PCB/AI服务器"].append(item)
                placed = True
            if not placed:
                groups["其他"].append(item)
        return groups

    def _news_stance(self, item: dict[str, object]) -> str:
        text = f"{item.get('title', '')} {item.get('summary', '')} {item.get('content', '')}"
        positive = ("增长", "中标", "突破", "新高", "扩产", "订单", "涨价", "景气", "利好", "放量", "上调", "合作", "并购")
        negative = ("减持", "亏损", "下滑", "风险", "处罚", "问询", "诉讼", "终止", "跌", "降价", "延期", "利空")
        pos = sum(1 for word in positive if word in text)
        neg = sum(1 for word in negative if word in text)
        if pos > neg:
            return "利好"
        if neg > pos:
            return "风险"
        return "中性"

    def _clean_text(self, value: str, limit: int) -> str:
        text = " ".join(value.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _build_premarket_analysis(self, model_path: Path) -> str:
        files = self._model_files(model_path)
        if not files:
            return "没有找到模型 JSON。"

        now = datetime.now(BEIJING_TZ)
        trade_note = "今天是交易日" if now.weekday() < 5 else "今天不是交易日，以下仅作下个交易日前的准备清单"
        rows = []
        for file in files:
            data = json.loads(file.read_text(encoding="utf-8"))
            params = data.get("params", {})
            backtest = data.get("backtest", {})
            win = float(backtest.get("win_rate_pct") or 0)
            avg = float(backtest.get("avg_result_pct") or 0)
            dd = float(backtest.get("max_drawdown_pct") or 0)
            n = int(backtest.get("trade_count") or 0)
            score = win + avg * 8 + dd * 2 + min(n, 12) * 0.8
            rows.append((score, data, params, backtest))

        rows.sort(key=lambda item: item[0], reverse=True)
        lines = [
            f"盘前分析（北京时间 {now.strftime('%Y-%m-%d %H:%M')}）",
            trade_note,
            "",
            "今日使用方式：开盘后先看大盘和板块是否配合；只有 EXE 盘中推送出现时才按信号执行，不要盘前提前下单。",
            "",
            "优先观察顺序：",
        ]
        for rank, (_score, data, params, backtest) in enumerate(rows, start=1):
            side = "正T/倒T" if params.get("trade_sides") == "both" else "正T" if params.get("trade_sides", "buy") == "buy" else "倒T"
            lines.append(
                f"{rank}. {data.get('name')} {data.get('code')}：{side}，"
                f"回测 {backtest.get('trade_count', '-')} 次，胜率 {backtest.get('win_rate_pct', '-')}%，"
                f"单次均值 {backtest.get('avg_result_pct', '-')}%，最大回撤 {backtest.get('max_drawdown_pct', '-')}%。"
            )
        lines.extend(["", "触发条件速查："])
        for _score, data, params, _backtest in rows:
            volume_note = (
                f"，放量比 >= {params.get('volume_ratio_threshold')}"
                if float(params.get("volume_ratio_threshold") or 0) > 0
                else ""
            )
            lines.append(
                f"- {data.get('name')}：相似股篮子 > {float(params.get('basket_threshold', 0))*100:.2f}%，"
                f"大盘 > {float(params.get('market_threshold', 0))*100:.2f}%，"
                f"相对篮子 > {float(params.get('relative_threshold', 0))*100:.2f}%，"
                f"价格高于分时均价 {float(params.get('avg_threshold', 0))*100:.2f}%{volume_note}；"
                f"目标 {float(params.get('take_profit', 0))*100:.2f}%，止损 {float(params.get('stop_loss', 0))*100:.2f}%。"
            )
        lines.extend(
            [
                "",
                "风险提醒：历史回测不代表未来收益；盘前分析只告诉你今天重点盯谁，真正买卖价以盘中实时信号为准。",
            ]
        )
        return "\n".join(lines)

    def _open_text_window(self, title: str, content: str) -> None:
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("760x560")
        window.configure(bg=COLORS["bg"])
        text = tk.Text(
            window,
            wrap=tk.WORD,
            bg=COLORS["card"],
            fg=COLORS["text"],
            relief=tk.FLAT,
            padx=16,
            pady=14,
            font=("Microsoft YaHei UI", 10),
        )
        text.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)
        text.insert(tk.END, content)
        text.configure(state=tk.DISABLED)

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("已启动", "监控已经在运行。")
            return

        token = self.token_var.get().strip()
        model_path = Path(self.model_path_var.get().strip())
        if not token:
            messagebox.showwarning("缺少 token", "请先输入 PushPlus token。")
            return
        if not model_path.exists():
            messagebox.showwarning("模型不存在", "请选择模型 JSON 文件或模型文件夹。")
            return
        try:
            interval = max(5, int(self.interval_var.get().strip()))
        except ValueError:
            messagebox.showwarning("间隔无效", "检查间隔必须是数字。")
            return

        self.stop_event.clear()
        self.worker = threading.Thread(
            target=self._run_worker,
            args=(model_path, token, interval),
            daemon=True,
        )
        self.worker.start()
        self.status_var.set("运行中")
        self.status_badge.configure(bg=COLORS["mint"], fg=COLORS["sage_dark"])
        self._log("监控已启动")

    def _stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("停止中")
        self.status_badge.configure(bg=COLORS["cream"], fg=COLORS["coral_dark"])
        self._log("正在停止监控")

    def _run_worker(self, model_path: Path, token: str, interval: int) -> None:
        try:
            models = load_models(model_path)
            self.queue.put(("log", f"已加载 {len(models)} 个模型：" + "、".join(m.name for m in models)))
            self.queue.put(("risk", self._model_risk_summary(model_path)))
            engine = ModelSignalEngine(models, app_dir() / "data", token)
        except Exception as exc:
            self.queue.put(("error", str(exc)))
            return

        while not self.stop_event.is_set():
            try:
                result = engine.check_all()
                self.queue.put(("result", result))
            except Exception as exc:
                self.queue.put(("log", f"检查失败：{exc}"))
            self.stop_event.wait(interval)
        self.queue.put(("stopped", None))

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(str(payload))
            elif kind == "error":
                self.status_var.set("错误")
                self.status_badge.configure(bg="#F5DDDD", fg=COLORS["danger"])
                messagebox.showerror("启动失败", str(payload))
            elif kind == "risk":
                self.risk_var.set(str(payload))
            elif kind == "info_premarket":
                self.status_var.set("信息面盘前完成")
                self.status_badge.configure(bg=COLORS["mint"], fg=COLORS["sage_dark"])
                self._open_text_window("信息面盘前", str(payload))
            elif kind == "info_error":
                self.status_var.set("信息面获取失败")
                self.status_badge.configure(bg="#F5DDDD", fg=COLORS["danger"])
                messagebox.showerror("信息面盘前失败", str(payload))
            elif kind == "update_ready":
                self.status_var.set("更新包已就绪")
                self.status_badge.configure(bg=COLORS["mint"], fg=COLORS["sage_dark"])
                info = payload if isinstance(payload, dict) else {}
                update_file = Path(str(info.get("file", "")))
                updated_at = info.get("updated_at", "-")
                sha = info.get("sha", "-")
                should_install = messagebox.askyesno(
                    "更新程序",
                    f"新版已下载。\n版本：{sha}\n更新时间：{updated_at}\n\n是否现在退出并安装更新？",
                )
                if should_install and update_file.exists():
                    self._install_update(update_file)
            elif kind == "update_current":
                self.status_var.set("已是最新版")
                self.status_badge.configure(bg=COLORS["mint"], fg=COLORS["sage_dark"])
                info = payload if isinstance(payload, dict) else {}
                messagebox.showinfo(
                    "更新程序",
                    f"当前已经是最新版。\n版本：{info.get('sha', BUILD_SHA)}\n更新时间：{info.get('updated_at', '-')}",
                )
            elif kind == "update_error":
                self.status_var.set("更新失败")
                self.status_badge.configure(bg="#F5DDDD", fg=COLORS["danger"])
                messagebox.showerror("更新失败", str(payload))
            elif kind == "custom_info_result":
                info = payload if isinstance(payload, dict) else {}
                text_widget = info.get("text")
                status_var = info.get("status")
                content = str(info.get("content", ""))
                if hasattr(text_widget, "configure") and hasattr(text_widget, "delete"):
                    try:
                        text_widget.configure(state=tk.NORMAL)
                        text_widget.delete("1.0", tk.END)
                        text_widget.insert(tk.END, content)
                        text_widget.configure(state=tk.DISABLED)
                    except tk.TclError:
                        pass
                if hasattr(status_var, "set"):
                    status_var.set("搜索完成")
            elif kind == "stopped":
                self.status_var.set("已停止")
                self.status_badge.configure(bg=COLORS["cream"], fg=COLORS["muted"])
            elif kind == "result":
                self._render_result(payload)  # type: ignore[arg-type]
        self.root.after(300, self._drain_queue)

    def _render_result(self, result: dict[str, object]) -> None:
        checked_at = result.get("checked_at", "-")
        items = result.get("items", [])
        alerts = result.get("alerts", [])
        self.status_var.set(f"最近检查：{checked_at}")
        self.status_badge.configure(bg=COLORS["mint"], fg=COLORS["sage_dark"])
        self.table.delete(*self.table.get_children())
        for index, item in enumerate(items if isinstance(items, list) else []):
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "-"))
            tag = "signal" if status == "signal" else "error" if status == "error" else "even" if index % 2 == 0 else "odd"
            self.table.insert(
                "",
                tk.END,
                values=(
                    item.get("symbol", "-"),
                    item.get("code", "-"),
                    item.get("status", "-"),
                    item.get("entry_label", "-") if status == "signal" else "-",
                    item.get("entry_price", item.get("last_price", "-")),
                    item.get("exit_price", "-"),
                    item.get("stop_price", "-"),
                    item.get("minute", "-"),
                    item.get("signal_score", "-"),
                    item.get("message", "-"),
                ),
                tags=(tag,),
            )
        self._log(f"[{checked_at}] 检查完成，新信号 {len(alerts) if isinstance(alerts, list) else 0} 个")
        if isinstance(alerts, list):
            for alert in alerts:
                if isinstance(alert, dict):
                    self._log(
                        f"信号：{alert.get('symbol')} {alert.get('signal_detail', alert.get('entry_label', ''))} "
                        f"入场 {alert.get('entry_price')} {alert.get('exit_label', '目标')} {alert.get('exit_price')} "
                        f"止损 {alert.get('stop_price')} "
                        f"推送 {alert.get('notify_status', '-')}"
                    )

    def _log(self, message: str) -> None:
        now = time.strftime("%H:%M:%S")
        self.log.insert(tk.END, f"[{now}] {message}\n")
        self.log.see(tk.END)


def main() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
