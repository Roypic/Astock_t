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
import html
import email.utils
import xml.etree.ElementTree as ET
from http.client import IncompleteRead
import json
import urllib.parse
import urllib.request
import webbrowser
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from model_engine import BEIJING_TZ, INDEX_CODES, TRADING_SESSIONS, MarketClient, ModelSignalEngine, TModel, load_models
from notifier import PushPlusNotifier

try:
    from build_info import BUILD_SHA
except Exception:
    BUILD_SHA = "dev"

DEFAULT_INTERVAL_SECONDS = 30
INTRADAY_NEWS_INTERVAL_SECONDS = 300
MARKET_WEAK_INTERVAL_SECONDS = 300
PUSHPLUS_TOKEN_URL = "https://www.pushplus.plus/"
NEWS_SEARCH_URL = "https://market.ft.tech/data/api/v1/market/data/semantic-search-news"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
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
NEWS_PROFILES = {
    "剑桥科技": {
        "aliases": ("剑桥科技", "剑桥", "603083", "CIG"),
        "required": ("剑桥科技", "剑桥", "603083"),
        "theme": ("CPO", "光模块", "光通信", "算力", "数据中心"),
    },
    "东山精密": {
        "aliases": ("东山精密", "东山", "002384"),
        "required": ("东山精密", "东山", "002384"),
        "theme": ("PCB", "FPC", "AI服务器", "服务器", "电子元器件", "光模块"),
    },
    "福晶科技": {
        "aliases": ("福晶科技", "福晶", "002222"),
        "required": ("福晶科技", "福晶", "002222"),
        "theme": ("激光晶体", "非线性晶体", "光学", "光通信", "光模块"),
    },
    "利通电子": {
        "aliases": ("利通电子", "利通", "603629"),
        "required": ("利通电子", "利通", "603629"),
        "theme": ("PCB", "AI服务器", "电子元器件", "服务器", "算力"),
    },
}
STOCK_CODE_HINTS = {
    "云南锗业": "002428.SZ",
    "云锗": "002428.SZ",
    "驰宏锌锗": "600497.SH",
    "中金岭南": "000060.SZ",
    "有研新材": "600206.SH",
    "江西铜业": "600362.SH",
    "锡业股份": "000960.SZ",
    "贵研铂业": "600459.SH",
    "剑桥科技": "603083.SH",
    "东山精密": "002384.SZ",
    "福晶科技": "002222.SZ",
    "利通电子": "603629.SH",
}
RESEARCH_PEERS = {
    "002428.SZ": ("600497.SH", "000060.SZ", "600206.SH", "600362.SH"),
}
RESEARCH_THEMES = {
    "002428.SZ": ("锗", "红外光学", "光纤级锗", "光伏级锗", "磷化铟", "砷化镓", "化合物半导体"),
}
NEWS_THEME_TERMS = (
    "CPO",
    "光模块",
    "光通信",
    "算力",
    "AI服务器",
    "服务器",
    "PCB",
    "FPC",
    "电子元器件",
    "激光晶体",
    "光学",
)
SEVERE_NEGATIVE_TERMS = {
    "立案": 4,
    "调查": 3,
    "处罚": 4,
    "监管函": 3,
    "问询函": 3,
    "问询": 2,
    "警示函": 3,
    "违法": 4,
    "违规": 3,
    "退市": 5,
    "ST": 4,
    "暴雷": 5,
    "亏损": 3,
    "预亏": 4,
    "业绩预降": 3,
    "下修": 2,
    "下滑": 2,
    "大幅下降": 3,
    "减持": 3,
    "清仓": 4,
    "冻结": 4,
    "诉讼": 3,
    "仲裁": 3,
    "违约": 5,
    "债务": 3,
    "终止": 3,
    "解约": 3,
    "跌停": 4,
    "闪崩": 5,
}
MILD_NEGATIVE_TERMS = {
    "质押": 1,
    "展期": 1,
    "解禁": 1,
    "延期": 1,
    "降价": 1,
    "风险": 1,
}
MARKET_WEAK_INDEX_THRESHOLD = -0.008
MARKET_WEAK_INDEX_MOMENTUM = -0.0025
MARKET_WEAK_BASKET_THRESHOLD = -0.012
MARKET_WEAK_BASKET_MOMENTUM = -0.004
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
        self.news_worker: threading.Thread | None = None
        self.market_worker: threading.Thread | None = None
        self.seen_intraday_news: set[str] = set()
        self.seen_market_alerts: set[str] = set()
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
        self.info_alert_var = tk.BooleanVar(value=True)
        self.market_alert_var = tk.BooleanVar(value=True)
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
        ttk.Button(controls, text="AI研报", style="Ghost.TButton", command=self._open_research_report_window).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="更新程序", style="Ghost.TButton", command=self._check_update).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="测试推送", style="Ghost.TButton", command=self._test_push).pack(side=tk.LEFT)
        tk.Checkbutton(
            controls,
            text="盘中信息异动",
            variable=self.info_alert_var,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            activebackground=COLORS["bg"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["card"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(12, 4))
        tk.Checkbutton(
            controls,
            text="大盘/板块走弱",
            variable=self.market_alert_var,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            activebackground=COLORS["bg"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["card"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(4, 4))
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
        broad_var = tk.BooleanVar(value=True)
        status_var = tk.StringVar(value="输入股票名/代码/主题关键词后搜索。")

        ttk.Label(panel, text="关键词", style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        entry = ttk.Entry(panel, textvariable=query_var)
        entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 10))
        ttk.Label(panel, text="天数", style="Muted.TLabel").grid(row=0, column=2, sticky=tk.E, padx=(0, 8))
        ttk.Entry(panel, textvariable=days_var, width=6).grid(row=0, column=3, sticky=tk.W, padx=(0, 10))
        tk.Checkbutton(
            panel,
            text="宽泛+摘要",
            variable=broad_var,
            bg=COLORS["card"],
            fg=COLORS["text"],
            activebackground=COLORS["card"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["card_soft"],
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=4, sticky=tk.W, padx=(0, 10))

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
                    content = self._build_custom_info_report(query, days, broad=broad_var.get())
                    self.queue.put(("custom_info_result", {"text": result, "status": status_var, "content": content}))
                except Exception as exc:
                    self.queue.put(("custom_info_result", {"text": result, "status": status_var, "content": f"搜索失败：{exc}"}))

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(panel, text="搜索", style="Primary.TButton", command=run_search).grid(row=0, column=5, sticky=tk.E)
        entry.bind("<Return>", lambda _event: run_search())
        entry.focus_set()

    def _build_custom_info_report(self, query: str, days: int, broad: bool = False) -> str:
        now = datetime.now(BEIJING_TZ)
        start = now - timedelta(days=days)
        candidates = self._search_broad_news(query, start, now) if broad else self._search_news(query, start, now, limit=36)
        items = self._rerank_news(query, candidates, limit=20 if broad else 12, broad=broad)
        mode = "宽泛语义检索 + 本地轻量摘要" if broad else "新闻语义检索 + 本地精准匹配过滤"
        lines = [
            f"自选信息面：{query}",
            f"北京时间 {now.strftime('%Y-%m-%d %H:%M')}，范围：最近 {days} 天{mode}。",
            "仅支持查看最近半个月以内的新闻数据。",
            f"候选 {len(candidates)} 条，精准匹配后保留 {len(items)} 条。",
            "",
        ]
        if not items:
            lines.append("暂未检索到相关新闻。可以换成公司简称、股票代码或行业关键词再试。")
            return "\n".join(lines)

        if broad:
            lines.extend(self._local_news_summary(query, items))
            lines.append("")
            lines.append("新闻明细：")

        stance_counts = {"利好": 0, "风险": 0, "中性": 0}
        for item in items:
            stance = self._news_stance(item)
            stance_counts[stance] += 1
            relevance = item.get("_relevance_score")
            relevance_text = f" 相关度 {float(relevance):.1f}" if isinstance(relevance, (int, float)) else ""
            title = self._clean_text(str(item.get("title") or "无标题"), 90)
            source = item.get("source_site") or item.get("media_name") or "未知来源"
            source_channel = item.get("_source")
            if source_channel and source_channel not in str(source):
                source = f"{source} / {source_channel}"
            publish = str(item.get("publish_time") or item.get("fetch_time") or "-").replace("T", " ")[:16]
            url = item.get("article_url") or ""
            lines.append(f"- [{stance}{relevance_text}] {publish} {source}：{title}")
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

    def _open_research_report_window(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("AI研报生成")
        window.geometry("900x700")
        window.configure(bg=COLORS["bg"])

        panel = ttk.Frame(window, style="Card.TFrame", padding=14)
        panel.pack(fill=tk.X, padx=14, pady=(14, 8))
        panel.columnconfigure(1, weight=1)

        query_var = tk.StringVar(value="云南锗业")
        status_var = tk.StringVar(value="输入股票名或代码，生成一份多源信息面的本地 AI 研报。")

        ttk.Label(panel, text="股票", style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        entry = ttk.Entry(panel, textvariable=query_var)
        entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 10))

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

        def run_report() -> None:
            query = query_var.get().strip()
            if not query:
                messagebox.showwarning("缺少股票", "请输入股票名称或代码，例如 云南锗业 / 002428.SZ。")
                return
            result.configure(state=tk.NORMAL)
            result.delete("1.0", tk.END)
            result.insert(tk.END, "正在抓取多源信息并生成研报，请稍候...\n")
            result.configure(state=tk.DISABLED)
            status_var.set("正在生成 AI 研报...")

            def worker() -> None:
                try:
                    content = self._build_research_report(query)
                    self.queue.put(("custom_info_result", {"text": result, "status": status_var, "content": content}))
                except Exception as exc:
                    self.queue.put(("custom_info_result", {"text": result, "status": status_var, "content": f"研报生成失败：{exc}"}))

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(panel, text="生成研报", style="Primary.TButton", command=run_report).grid(row=0, column=2, sticky=tk.E)
        entry.bind("<Return>", lambda _event: run_report())
        entry.focus_set()

    def _build_research_report(self, query: str) -> str:
        now = datetime.now(BEIJING_TZ)
        code = self._resolve_stock_code(query)
        info = self._fetch_security_info(code) if code else {}
        name = str(info.get("symbol_name") or query)
        themes = RESEARCH_THEMES.get(str(info.get("symbol") or code), self._query_terms(query) or (query,))
        industry_ranked = self._collect_research_items(name, themes, now)
        peers = self._research_peers_for(code)
        peer_infos = [self._fetch_security_info(peer) for peer in peers]
        peer_infos = [item for item in peer_infos if item]

        lines = [
            f"{name}（{info.get('symbol', code or '代码未知')}）AI研报草稿",
            f"生成时间：北京时间 {now.strftime('%Y-%m-%d %H:%M')}",
            "流程：LLMQuant 风格多模块路由 = 行业/市场规模 -> 公司现状 -> 同行差异 -> 同行估值 -> 预期与风险。",
            "数据源：FTShare/ftai 行情估值 + FTShare 语义新闻 + Google News RSS；同花顺/东财信息通过公开新闻聚合源和网页标题线索进入摘要。",
            "",
        ]
        lines.extend(self._research_snapshot(info))
        lines.append("")
        lines.extend(self._research_market_size_section(themes, industry_ranked))
        lines.append("")
        lines.extend(self._research_company_status_section(name, industry_ranked))
        lines.append("")
        lines.extend(self._research_peer_section(info, peer_infos))
        lines.append("")
        lines.extend(self._research_expectation_section(name, info, industry_ranked, peer_infos))
        lines.append("")
        lines.extend(self._research_sources_section(industry_ranked))
        lines.append("")
        lines.append("免责声明：这是本地小 AI 模型生成的研报草稿，只做信息整理和研究框架，不构成投资建议；估值和预期需以交易软件、公告原文和正式研报复核。")
        return "\n".join(lines)

    def _collect_research_items(self, name: str, themes: tuple[str, ...] | list[str], now: datetime) -> list[dict[str, object]]:
        start = now - timedelta(days=14)
        queries = [
            name,
            f"{name} 锗 磷化铟 砷化镓",
            f"{name} 东方财富 同花顺 研报",
            f"{name} 龙虎榜 资金 异动",
            "锗 红外 光伏 光纤 半导体 市场规模",
            "锗 出口管制 锗价 供需",
            "磷化铟 砷化镓 化合物半导体 衬底",
        ]
        seen = set()
        candidates = []
        for search_query in queries:
            for item in self._search_broad_news(search_query, start, now):
                key = item.get("article_url") or item.get("news_id") or item.get("title")
                if key in seen:
                    continue
                seen.add(key)
                score = self._research_item_score(name, themes, item)
                if score < 3.0:
                    continue
                copied = dict(item)
                copied["_research_score"] = round(score, 1)
                candidates.append(copied)
        candidates.sort(
            key=lambda item: (float(item.get("_research_score") or 0), str(item.get("publish_time") or item.get("fetch_time") or "")),
            reverse=True,
        )
        return candidates[:24]

    def _research_item_score(self, name: str, themes: tuple[str, ...] | list[str], item: dict[str, object]) -> float:
        text = self._news_body_text(item)
        title = str(item.get("title") or "")
        score = 0.0
        if name and name in text:
            score += 7.0
        core_terms = ("锗", "锗业", "磷化铟", "砷化镓", "化合物半导体", "光伏级锗", "红外级锗", "光纤级锗")
        for word in core_terms:
            if word in text:
                score += 2.0
            if word in title:
                score += 1.2
        for word in themes:
            if word in ("红外光学", "光学", "光伏级锗", "光纤级锗", "磷化铟", "砷化镓", "化合物半导体") and word in text:
                score += 0.8
        irrelevant_terms = ("眼镜", "脱毛", "汽车皮革", "量子光学", "激光相互作用", "光学科技")
        if any(word in text for word in irrelevant_terms) and name not in text and "锗" not in text:
            score -= 5.0
        return score

    def _resolve_stock_code(self, query: str) -> str:
        text = query.strip().upper()
        if re.fullmatch(r"\d{6}\.(SZ|SH)", text):
            return text
        if re.fullmatch(r"\d{6}", text):
            return f"{text}.SH" if text.startswith("6") else f"{text}.SZ"
        return STOCK_CODE_HINTS.get(query.strip(), "")

    def _fetch_security_info(self, symbol: str) -> dict[str, object]:
        if not symbol:
            return {}
        url = f"https://ftai.chat/api/v1/market/security/{urllib.parse.quote(symbol)}/info"
        req = urllib.request.Request(url, headers={"User-Agent": "AShareTSignalMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def _research_peers_for(self, code: str) -> tuple[str, ...]:
        if code in RESEARCH_PEERS:
            return RESEARCH_PEERS[code]
        return tuple(symbol for symbol in ("600497.SH", "000060.SZ", "600206.SH") if symbol != code)

    def _research_snapshot(self, info: dict[str, object]) -> list[str]:
        if not info:
            return ["一、行情估值快照", "- 未获取到行情估值数据。"]
        return [
            "一、行情估值快照",
            f"- 最新价/收盘：{self._fmt_num(info.get('close'))}；涨跌幅：{self._fmt_pct_value(info.get('change_rate'))}；换手率：{self._fmt_pct_value(info.get('turnover_rate'))}。",
            f"- 总市值：{self._fmt_money(info.get('market_cap'))}；流通市值：{self._fmt_money(info.get('float_a_market_cap'))}。",
            f"- PE(TTM)：{self._fmt_num(info.get('pe_ttm'))}；PB：{self._fmt_num(info.get('pb'))}；每股净资产：{self._fmt_num(info.get('bvps'))}。",
        ]

    def _research_market_size_section(self, themes: tuple[str, ...] | list[str], items: list[dict[str, object]]) -> list[str]:
        theme_text = "、".join(themes[:7])
        market_terms = self._top_keyword_hits(items, ("市场规模", "需求", "供给", "出口管制", "红外", "光纤", "光伏", "半导体", "军工", "卫星", "AI", "涨价"))
        return [
            "二、市场规模与行业位置",
            f"- 主题链条：{theme_text or '稀散金属/半导体材料'}。",
            f"- 小 AI 摘要：近期信息主要围绕 {market_terms or '锗价、红外光学、光纤通信、光伏衬底及化合物半导体'} 展开。",
            "- 研究判断：锗不是大众金属，核心看供给约束和高端应用放量；市场空间绝对值不如铜铝锂，但价格弹性和战略属性更强。",
        ]

    def _research_company_status_section(self, name: str, items: list[dict[str, object]]) -> list[str]:
        positive = self._top_keyword_hits(items, ("合作", "扩产", "产能", "增长", "订单", "项目", "磷化铟", "砷化镓", "光伏级", "红外级"))
        risk = self._top_keyword_hits(items, ("亏损", "下滑", "减持", "质押", "问询", "处罚", "现金流", "毛利率"))
        return [
            "三、公司现状",
            f"- 主线：{name} 的稀缺性来自锗资源 + 锗材料深加工 + 化合物半导体延伸。",
            f"- 积极线索：{positive or '高端材料、光伏级/红外级产品、化合物半导体项目'}。",
            f"- 风险线索：{risk or '盈利波动、产品价格周期、项目放量节奏、估值较高'}。",
            "- 差异点：相比资源型有色公司，云南锗业更像“小金属资源 + 材料平台”标的，业绩弹性取决于高附加值产品占比，而不是单纯金属采选量。",
        ]

    def _research_peer_section(self, info: dict[str, object], peers: list[dict[str, object]]) -> list[str]:
        lines = ["四、同行差异与估值"]
        rows = [info] + peers if info else peers
        lines.append("股票 | 定位 | 市值 | PE(TTM) | PB")
        for item in rows:
            name = str(item.get("symbol_name") or item.get("symbol") or "-")
            symbol = str(item.get("symbol") or "-")
            positioning = self._peer_positioning(symbol, name)
            lines.append(
                f"{name}({symbol}) | {positioning} | {self._fmt_money(item.get('market_cap'))} | "
                f"{self._fmt_num(item.get('pe_ttm'))} | {self._fmt_num(item.get('pb'))}"
            )
        lines.append("- 估值解释：若云南锗业 PE 显著高于同行，市场通常在定价资源稀缺性、锗价弹性和化合物半导体成长性；但这也意味着业绩兑现要求更高。")
        return lines

    def _research_expectation_section(
        self,
        name: str,
        info: dict[str, object],
        items: list[dict[str, object]],
        peers: list[dict[str, object]],
    ) -> list[str]:
        pe = self._to_float(info.get("pe_ttm")) if info else None
        peer_pes = [self._to_float(item.get("pe_ttm")) for item in peers]
        peer_pes = [value for value in peer_pes if value and value > 0]
        peer_avg = sum(peer_pes) / len(peer_pes) if peer_pes else None
        valuation_note = "估值缺少可比样本"
        if pe and peer_avg:
            valuation_note = "明显高于同行均值" if pe > peer_avg * 2 else "接近或略高于同行均值" if pe > peer_avg else "低于同行均值"
        catalysts = self._top_keyword_hits(items, ("出口管制", "涨价", "红外", "卫星", "光伏", "磷化铟", "砷化镓", "半导体", "项目", "产能"))
        return [
            "五、预期与跟踪框架",
            f"- 估值状态：{name} 当前 {valuation_note}；如果利润基数较低，PE 会被放大，需更多看 PB、市值/资源量、项目兑现。",
            f"- 上行催化：{catalysts or '锗价上涨、高端产品放量、磷化铟/砷化镓项目兑现、红外/卫星/光伏需求'}。",
            "- 关键观察：1）锗价和出口政策；2）红外级/光伏级/光纤级产品销量与毛利；3）化合物半导体产能利用率；4）同行估值是否同步抬升。",
            "- 初步结论：适合作为“小金属战略资源 + 化合物半导体材料”弹性标的观察；若只按当前利润静态估值，安全垫不足，需用产业趋势和业绩兑现共同验证。",
        ]

    def _research_sources_section(self, items: list[dict[str, object]]) -> list[str]:
        lines = ["六、信息源线索"]
        for item in items[:8]:
            title = self._clean_text(str(item.get("title") or "无标题"), 80)
            source = item.get("source_site") or item.get("media_name") or item.get("_source") or "未知来源"
            url = item.get("article_url") or ""
            lines.append(f"- {source}：{title}")
            if url:
                lines.append(f"  {url}")
        return lines

    def _peer_positioning(self, symbol: str, name: str) -> str:
        mapping = {
            "002428.SZ": "锗全产业链/化合物半导体",
            "600497.SH": "铅锌锗资源/中铝体系",
            "000060.SZ": "铅锌铜综合资源",
            "600206.SH": "稀有金属/电子材料",
            "600362.SH": "铜冶炼/伴生资源",
        }
        return mapping.get(symbol, "有色/材料可比")

    def _top_keyword_hits(self, items: list[dict[str, object]], words: tuple[str, ...]) -> str:
        hits = []
        for word in words:
            count = sum(1 for item in items if word in self._news_body_text(item))
            if count:
                hits.append((word, count))
        hits.sort(key=lambda row: (-row[1], row[0]))
        return "、".join(f"{word}({count})" for word, count in hits[:6])

    def _fmt_num(self, value: object) -> str:
        number = self._to_float(value)
        if number is None:
            return "-"
        return f"{number:.2f}"

    def _fmt_pct_value(self, value: object) -> str:
        number = self._to_float(value)
        if number is None:
            return "-"
        return f"{number:.2f}%"

    def _fmt_money(self, value: object) -> str:
        number = self._to_float(value)
        if number is None:
            return "-"
        if abs(number) >= 100000000:
            return f"{number / 100000000:.2f}亿"
        if abs(number) >= 10000:
            return f"{number / 10000:.2f}万"
        return f"{number:.2f}"

    def _to_float(self, value: object) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

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
        suffix = release_short or datetime.now(BEIJING_TZ).strftime("%Y%m%d%H%M%S")
        target = updates_dir / f"AShareTSignalMonitor-{suffix}.exe"
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
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([str(update_file)], cwd=str(update_file.parent), creationflags=flags)
        self.root.after(300, self.root.destroy)

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
            for item in self._search_precise_news(query, start, now, limit=5, candidate_limit=24):
                key = item.get("article_url") or item.get("news_id") or item.get("title")
                if key in seen:
                    continue
                seen.add(key)
                item["_query"] = query
                results.append(item)

        results.sort(key=lambda item: str(item.get("publish_time") or item.get("fetch_time") or ""), reverse=True)
        lines = [
            f"信息面盘前汇总（北京时间 {now.strftime('%Y-%m-%d %H:%M')}）",
            "范围：最近3天新闻语义检索 + 本地精准匹配过滤；仅支持查看最近半个月以内的新闻数据。",
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
                relevance = item.get("_relevance_score")
                relevance_text = f" 相关度 {float(relevance):.1f}" if isinstance(relevance, (int, float)) else ""
                title = self._clean_text(str(item.get("title") or "无标题"), 70)
                source = item.get("source_site") or item.get("media_name") or "未知来源"
                publish = str(item.get("publish_time") or item.get("fetch_time") or "-").replace("T", " ")[:16]
                url = item.get("article_url") or ""
                lines.append(f"- [{stance}{relevance_text}] {publish} {source}：{title}")
                if url:
                    lines.append(f"  {url}")
            summary = " / ".join(f"{k}{v}" for k, v in stance_counts.items() if v)
            lines.append(f"  小结：{summary or '中性'}；盘前只看信息方向，入场仍等待盘中模型信号。")
            lines.append("")

        lines.append("风险提醒：新闻标题和摘要只能辅助判断情绪，不能替代公告原文、交易所披露和盘中量价确认。")
        return "\n".join(lines)

    def _search_news(self, query: str, start: datetime, end: datetime, limit: int = 5) -> list[dict[str, object]]:
        last_error: Exception | None = None
        requested_limits = []
        for value in (limit, min(limit, 20), min(limit, 12), min(limit, 6)):
            if value > 0 and value not in requested_limits:
                requested_limits.append(value)
        for attempt, request_limit in enumerate(requested_limits, start=1):
            try:
                data = self._fetch_news_json(query, start, end, request_limit)
                return self._extract_news_items(data)
            except (IncompleteRead, json.JSONDecodeError, TimeoutError, OSError) as exc:
                last_error = exc
                time.sleep(min(0.4 * attempt, 1.2))
        if last_error:
            raise RuntimeError(f"新闻接口读取失败，请稍后重试：{last_error}") from last_error
        return []

    def _fetch_news_json(self, query: str, start: datetime, end: datetime, limit: int) -> object:
        params = {
            "query": query,
            "limit": str(limit),
            "year": str(end.year),
            "start_time": start.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        }
        url = NEWS_SEARCH_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AShareTSignalMonitor/1.0",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        with urllib.request.urlopen(req, timeout=18) as resp:
            try:
                raw = resp.read()
            except IncompleteRead as exc:
                raw = exc.partial
                if not raw:
                    raise
            text = raw.decode("utf-8", errors="ignore").strip()
            if not text:
                return []
            return json.loads(text)

    def _extract_news_items(self, data: object) -> list[dict[str, object]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("data", "results", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def _search_precise_news(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 5,
        candidate_limit: int = 24,
    ) -> list[dict[str, object]]:
        candidates = self._search_news(query, start, end, limit=candidate_limit)
        return self._rerank_news(query, candidates, limit=limit)

    def _search_broad_news(self, query: str, start: datetime, end: datetime) -> list[dict[str, object]]:
        queries = self._broad_news_queries(query)
        seen = set()
        results = []
        for index, search_query in enumerate(queries):
            per_limit = 24 if index == 0 else 12
            try:
                items = self._search_news(search_query, start, end, limit=per_limit)
            except Exception as exc:
                self.queue.put(("log", f"宽泛搜索子查询失败：{search_query}｜{exc}"))
                items = []
            for item in items:
                key = item.get("article_url") or item.get("news_id") or item.get("title")
                if key in seen:
                    continue
                seen.add(key)
                copied = dict(item)
                copied["_source"] = copied.get("_source") or "FTShare"
                copied["_query"] = search_query
                results.append(copied)
            try:
                rss_items = self._search_google_news_rss(search_query, start, end, limit=per_limit)
            except Exception as exc:
                self.queue.put(("log", f"Google News RSS 子查询失败：{search_query}｜{exc}"))
                rss_items = []
            for item in rss_items:
                key = item.get("article_url") or item.get("news_id") or item.get("title")
                if key in seen:
                    continue
                seen.add(key)
                item["_query"] = search_query
                results.append(item)
        return results

    def _search_google_news_rss(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 12,
    ) -> list[dict[str, object]]:
        params = {
            "q": query,
            "hl": "zh-CN",
            "gl": "CN",
            "ceid": "CN:zh-Hans",
        }
        url = GOOGLE_NEWS_RSS_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 AShareTSignalMonitor/1.0",
                "Accept": "application/rss+xml, application/xml, text/xml",
                "Connection": "close",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        rows = []
        for item in root.findall("./channel/item"):
            title = html.unescape(item.findtext("title") or "").strip()
            link = html.unescape(item.findtext("link") or "").strip()
            source = html.unescape(item.findtext("source") or "Google News").strip()
            description = re.sub(r"<[^>]+>", " ", html.unescape(item.findtext("description") or ""))
            published_text = item.findtext("pubDate") or ""
            published = self._parse_rss_time(published_text)
            if published and (published < start or published > end + timedelta(days=1)):
                continue
            title, parsed_source = self._split_google_title(title, source)
            rows.append(
                {
                    "title": title,
                    "summary": " ".join(description.split()),
                    "article_url": link,
                    "source_site": parsed_source,
                    "publish_time": published.strftime("%Y-%m-%d %H:%M:%S") if published else published_text,
                    "_source": "Google News RSS",
                }
            )
            if len(rows) >= limit:
                break
        return rows

    def _split_google_title(self, title: str, fallback_source: str) -> tuple[str, str]:
        if " - " not in title:
            return title, fallback_source
        body, source = title.rsplit(" - ", 1)
        return body.strip() or title, source.strip() or fallback_source

    def _parse_rss_time(self, value: str) -> datetime | None:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=BEIJING_TZ)
        return parsed.astimezone(BEIJING_TZ)

    def _broad_news_queries(self, query: str) -> list[str]:
        terms = self._query_terms(query)
        profile = self._news_profile_for_query(query)
        queries = [query]
        if profile:
            required = [word for word in profile.get("required", ()) if word in query]
            company = required[0] if required else str(profile.get("required", ("",))[0])
            themes = list(profile.get("theme", ()))
            queries.extend(
                [
                    company,
                    f"{company} {' '.join(themes[:2])}".strip(),
                    f"{company} {' '.join(themes[2:5])}".strip(),
                    " ".join(themes[:4]),
                ]
            )
        primary_terms = [
            term
            for term in terms
            if term not in NEWS_THEME_TERMS and len(term) >= 3 and not term.lower().startswith(("ai", "cpo"))
        ]
        theme_terms = [term for term in terms if term in NEWS_THEME_TERMS or term.upper() in ("AI", "CPO")]
        if primary_terms:
            primary = primary_terms[0]
            queries.append(primary)
            if theme_terms:
                queries.append(f"{primary} {' '.join(theme_terms[:3])}")
        if theme_terms:
            queries.append(" ".join(theme_terms[:4]))

        deduped = []
        for item in queries:
            normalized = " ".join(item.split())
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        return deduped[:5]

    def _rerank_news(self, query: str, items: list[dict[str, object]], limit: int, broad: bool = False) -> list[dict[str, object]]:
        scored: list[tuple[float, str, dict[str, object]]] = []
        for item in items:
            score = self._news_relevance_score(query, item)
            if broad:
                source_query = str(item.get("_query") or "")
                if source_query and source_query != query:
                    score = max(score, self._news_relevance_score(source_query, item) * 0.85)
            if score < self._news_relevance_threshold(query, broad=broad):
                continue
            copied = dict(item)
            copied["_relevance_score"] = round(score, 1)
            publish = str(copied.get("publish_time") or copied.get("fetch_time") or "")
            scored.append((score, publish, copied))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [item for _score, _publish, item in scored[:limit]]

    def _news_relevance_threshold(self, query: str, broad: bool = False) -> float:
        profile = self._news_profile_for_query(query)
        if profile:
            return 1.8 if broad else 3.2
        primary_terms = [
            term
            for term in self._query_terms(query)
            if term not in NEWS_THEME_TERMS and len(term) >= 3 and not term.lower().startswith(("ai", "cpo"))
        ]
        if primary_terms:
            return 1.8 if broad else 3.0
        return 1.5 if broad else 2.0 if any(term in query for term in NEWS_THEME_TERMS) else 2.4

    def _news_relevance_score(self, query: str, item: dict[str, object]) -> float:
        text = self._news_body_text(item)
        title = str(item.get("title") or "")
        terms = self._query_terms(query)
        profile = self._news_profile_for_query(query)

        score = 0.0
        if query and query in text:
            score += 4.0
        for term in terms:
            if term in text:
                score += 1.8 if len(term) >= 3 else 1.1
            if term and term in title:
                score += 1.1
        primary_terms = [
            term
            for term in terms
            if term not in NEWS_THEME_TERMS and len(term) >= 3 and not term.lower().startswith(("ai", "cpo"))
        ]
        if primary_terms and not any(term in text for term in primary_terms):
            score -= 2.2

        if profile:
            aliases = tuple(profile.get("aliases", ()))
            required = tuple(profile.get("required", ()))
            theme = tuple(profile.get("theme", ()))
            alias_hits = sum(1 for word in aliases if word and word in text)
            required_hits = sum(1 for word in required if word and word in text)
            theme_hits = sum(1 for word in theme if word and word in text)
            score += min(alias_hits, 3) * 2.0
            score += min(theme_hits, 3) * 0.9
            if required_hits:
                score += 3.0
            else:
                score -= 4.0

        theme_hits = sum(1 for word in NEWS_THEME_TERMS if word in query and word in text)
        score += min(theme_hits, 4) * 1.0

        query_grams = self._char_grams(query)
        if query_grams:
            text_grams = self._char_grams(text[:500])
            overlap = len(query_grams & text_grams)
            score += min(2.5, overlap / max(1, len(query_grams)) * 2.5)

        broad_market_words = ("大盘", "指数", "沪指", "深成指", "创业板", "美股", "港股", "期货", "基金")
        if not any(word in query for word in broad_market_words) and any(word in title for word in broad_market_words):
            score -= 0.8
        return score

    def _local_news_summary(self, query: str, items: list[dict[str, object]]) -> list[str]:
        stance_counts = {"利好": 0, "风险": 0, "中性": 0}
        risk_hits: dict[str, int] = {}
        positive_hits: dict[str, int] = {}
        theme_hits: dict[str, int] = {}
        for item in items:
            stance_counts[self._news_stance(item)] += 1
            text = self._news_body_text(item)
            for word in SEVERE_NEGATIVE_TERMS:
                if word in text:
                    risk_hits[word] = risk_hits.get(word, 0) + 1
            for word in ("增长", "中标", "突破", "订单", "扩产", "涨价", "合作", "回购", "增持", "景气"):
                if word in text:
                    positive_hits[word] = positive_hits.get(word, 0) + 1
            for word in NEWS_THEME_TERMS:
                if word in text:
                    theme_hits[word] = theme_hits.get(word, 0) + 1

        def top_words(mapping: dict[str, int], limit: int = 5) -> str:
            pairs = sorted(mapping.items(), key=lambda row: (-row[1], row[0]))[:limit]
            return "、".join(f"{word}({count})" for word, count in pairs) if pairs else "无明显集中项"

        total = max(1, len(items))
        risk_ratio = stance_counts["风险"] / total
        positive_ratio = stance_counts["利好"] / total
        if risk_ratio >= 0.45:
            bias = "偏风险"
        elif positive_ratio >= 0.45:
            bias = "偏利好"
        else:
            bias = "中性偏观察"

        top_items = sorted(
            items,
            key=lambda item: float(item.get("_relevance_score") or 0),
            reverse=True,
        )[:3]
        representative = "；".join(self._clean_text(str(item.get("title") or "无标题"), 36) for item in top_items)
        return [
            "本地轻量摘要：",
            f"- 综合情绪：{bias}；利好 {stance_counts['利好']} 条 / 风险 {stance_counts['风险']} 条 / 中性 {stance_counts['中性']} 条。",
            f"- 相关主题：{top_words(theme_hits)}。",
            f"- 风险关键词：{top_words(risk_hits)}。",
            f"- 利好关键词：{top_words(positive_hits)}。",
            f"- 代表新闻：{representative or '暂无'}。",
            "- 说明：这是本地小模型按标题/摘要/关键词生成的摘要，不调用外部 LLM；重要事项仍需点开原文和公告核对。",
        ]

    def _news_profile_for_query(self, query: str) -> dict[str, tuple[str, ...]] | None:
        for profile in NEWS_PROFILES.values():
            if any(alias and alias in query for alias in profile.get("aliases", ())):
                return profile
        return None

    def _news_text(self, item: dict[str, object]) -> str:
        values = (
            item.get("title", ""),
            item.get("summary", ""),
            item.get("content", ""),
            item.get("_query", ""),
            item.get("source_site", ""),
            item.get("media_name", ""),
        )
        return " ".join(str(value) for value in values if value)

    def _news_body_text(self, item: dict[str, object]) -> str:
        values = (
            item.get("title", ""),
            item.get("summary", ""),
            item.get("content", ""),
            item.get("source_site", ""),
            item.get("media_name", ""),
        )
        return " ".join(str(value) for value in values if value)

    def _query_terms(self, query: str) -> list[str]:
        raw_terms = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", query)
        terms = []
        for term in raw_terms:
            if term not in terms:
                terms.append(term)
        for theme in NEWS_THEME_TERMS:
            if theme in query and theme not in terms:
                terms.append(theme)
        return terms

    def _char_grams(self, text: str) -> set[str]:
        cleaned = re.sub(r"\s+", "", text)
        grams: set[str] = set()
        for size in (2, 3, 4):
            for index in range(0, max(0, len(cleaned) - size + 1)):
                gram = cleaned[index : index + size]
                if re.search(r"[\u4e00-\u9fffA-Za-z0-9]", gram):
                    grams.add(gram)
        return grams

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
        if self.info_alert_var.get():
            self.seen_intraday_news.clear()
            self.news_worker = threading.Thread(
                target=self._run_intraday_news_worker,
                args=(token,),
                daemon=True,
            )
            self.news_worker.start()
        if self.market_alert_var.get():
            self.seen_market_alerts.clear()
            self.market_worker = threading.Thread(
                target=self._run_market_weak_worker,
                args=(model_path, token),
                daemon=True,
            )
            self.market_worker.start()
        self.status_var.set("运行中")
        self.status_badge.configure(bg=COLORS["mint"], fg=COLORS["sage_dark"])
        notes = []
        if self.info_alert_var.get():
            notes.append("盘中信息异动已开启")
        if self.market_alert_var.get():
            notes.append("大盘/板块走弱提醒已开启")
        info_note = "，" + "，".join(notes) if notes else ""
        self._log(f"监控已启动{info_note}")

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

    def _run_intraday_news_worker(self, token: str) -> None:
        notifier = PushPlusNotifier(token)
        self.queue.put(("log", f"盘中信息异动已启动：每 {INTRADAY_NEWS_INTERVAL_SECONDS // 60} 分钟检查一次预设股负面新闻"))
        while not self.stop_event.is_set():
            if not self._is_live_trading_now():
                self.stop_event.wait(60)
                continue
            try:
                alerts = self._scan_intraday_negative_news()
                for alert in alerts:
                    notifier.send_text(alert["content"], title=alert["title"])
                    self.queue.put(("log", f"盘中信息异动已推送：{alert['summary']}"))
            except Exception as exc:
                self.queue.put(("log", f"盘中信息异动检查失败：{exc}"))
            self.stop_event.wait(INTRADAY_NEWS_INTERVAL_SECONDS)

    def _is_live_trading_now(self) -> bool:
        now = datetime.now(BEIJING_TZ)
        if now.weekday() >= 5:
            return False
        minute = now.strftime("%H:%M")
        return any(start <= minute <= end for start, end in TRADING_SESSIONS)

    def _scan_intraday_negative_news(self) -> list[dict[str, str]]:
        now = datetime.now(BEIJING_TZ)
        start = now - timedelta(hours=2)
        alerts = []
        for name, profile in NEWS_PROFILES.items():
            theme = " ".join(profile.get("theme", ())[:3])
            query = f"{name} {theme}".strip()
            items = self._search_precise_news(query, start, now, limit=8, candidate_limit=16)
            severe_items = []
            for item in items:
                key = str(item.get("article_url") or item.get("news_id") or item.get("title") or "")
                if not key or key in self.seen_intraday_news:
                    continue
                severity = self._negative_news_severity(item)
                if severity < 5:
                    continue
                if self._news_age_minutes(item, now) > 180:
                    continue
                self.seen_intraday_news.add(key)
                item["_negative_severity"] = severity
                severe_items.append(item)
            if severe_items:
                alerts.append(self._format_intraday_news_alert(name, severe_items, now))
        return alerts

    def _negative_news_severity(self, item: dict[str, object]) -> int:
        text = self._news_text(item)
        score = 0
        for word, weight in SEVERE_NEGATIVE_TERMS.items():
            if word in text:
                score += weight
        for word, weight in MILD_NEGATIVE_TERMS.items():
            if word in text:
                score += weight
        positive_terms = ("澄清", "解除", "撤销", "完成整改", "回购", "增持", "中标", "增长", "扭亏")
        score -= sum(2 for word in positive_terms if word in text)
        return max(0, score)

    def _news_age_minutes(self, item: dict[str, object], now: datetime) -> float:
        published = self._parse_news_time(str(item.get("publish_time") or item.get("fetch_time") or ""))
        if not published:
            return 0
        return max(0, (now - published).total_seconds() / 60)

    def _parse_news_time(self, value: str) -> datetime | None:
        text = value.strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=BEIJING_TZ)
            return parsed.astimezone(BEIJING_TZ)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text[: len("2026-05-31 15:36:43")], fmt) if "%z" not in fmt else datetime.strptime(text, fmt)
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=BEIJING_TZ)
                return parsed.astimezone(BEIJING_TZ)
            except ValueError:
                continue
        return None

    def _format_intraday_news_alert(self, name: str, items: list[dict[str, object]], now: datetime) -> dict[str, str]:
        lines = [
            f"盘中信息异动：{name}",
            f"北京时间 {now.strftime('%Y-%m-%d %H:%M')} 检测到极差情绪新闻。",
            "这不是交易指令，请优先核对原文/公告，并结合盘中量价处理风险。",
            "",
        ]
        for item in sorted(items, key=lambda row: int(row.get("_negative_severity") or 0), reverse=True)[:3]:
            severity = int(item.get("_negative_severity") or 0)
            relevance = item.get("_relevance_score")
            rel = f"，相关度 {float(relevance):.1f}" if isinstance(relevance, (int, float)) else ""
            title = self._clean_text(str(item.get("title") or "无标题"), 88)
            source = item.get("source_site") or item.get("media_name") or "未知来源"
            publish = str(item.get("publish_time") or item.get("fetch_time") or "-").replace("T", " ")[:16]
            url = item.get("article_url") or ""
            lines.append(f"- 严重度 {severity}{rel}｜{publish}｜{source}")
            lines.append(f"  {title}")
            if url:
                lines.append(f"  {url}")
        return {
            "title": f"盘中信息异动：{name}",
            "content": "\n".join(lines),
            "summary": f"{name} {len(items)} 条极差情绪新闻",
        }

    def _run_market_weak_worker(self, model_path: Path, token: str) -> None:
        try:
            models = load_models(model_path)
            client = MarketClient(ttl_seconds=60)
        except Exception as exc:
            self.queue.put(("log", f"大盘/板块走弱提醒启动失败：{exc}"))
            return

        notifier = PushPlusNotifier(token)
        self.queue.put(("log", f"大盘/板块走弱提醒已启动：每 {MARKET_WEAK_INTERVAL_SECONDS // 60} 分钟检查一次"))
        while not self.stop_event.is_set():
            if not self._is_live_trading_now():
                self.stop_event.wait(60)
                continue
            try:
                alerts = self._scan_market_weakness(models, client)
                for alert in alerts:
                    notifier.send_text(alert["content"], title=alert["title"])
                    self.queue.put(("log", f"大盘/板块走弱已推送：{alert['summary']}"))
            except Exception as exc:
                self.queue.put(("log", f"大盘/板块走弱检查失败：{exc}"))
            self.stop_event.wait(MARKET_WEAK_INTERVAL_SECONDS)

    def _scan_market_weakness(self, models: list[TModel], client: MarketClient) -> list[dict[str, str]]:
        now = datetime.now(BEIJING_TZ)
        alerts: list[dict[str, str]] = []
        index_rows = []
        for name, code in INDEX_CODES.items():
            prices = client.get_index_prices(code)
            stats = self._intraday_weak_stats(prices)
            if not stats:
                continue
            day_return, momentum, minute = stats
            level = self._weak_level(day_return, momentum, MARKET_WEAK_INDEX_THRESHOLD, MARKET_WEAK_INDEX_MOMENTUM)
            if not level:
                continue
            key = self._market_alert_key(now, "INDEX", name, level)
            if key in self.seen_market_alerts:
                continue
            self.seen_market_alerts.add(key)
            index_rows.append((name, day_return, momentum, minute, level))
        if index_rows:
            alerts.append(self._format_market_index_alert(index_rows, now))

        for model in models:
            basket_stats = self._basket_weak_stats(model, client)
            if not basket_stats:
                continue
            basket_return, momentum, minute, valid_count = basket_stats
            level = self._weak_level(
                basket_return,
                momentum,
                MARKET_WEAK_BASKET_THRESHOLD,
                MARKET_WEAK_BASKET_MOMENTUM,
            )
            if not level:
                continue
            key = self._market_alert_key(now, "BASKET", model.name, level)
            if key in self.seen_market_alerts:
                continue
            self.seen_market_alerts.add(key)
            alerts.append(self._format_basket_weak_alert(model, basket_return, momentum, minute, valid_count, level, now))
        return alerts

    def _intraday_weak_stats(self, prices: list[object]) -> tuple[float, float, str] | None:
        if len(prices) < 8:
            return None
        current = prices[-1]
        open_price = getattr(prices[0], "price", 0.0)
        current_price = getattr(current, "price", 0.0)
        if open_price <= 0 or current_price <= 0:
            return None
        lookback_index = max(0, len(prices) - 6)
        lookback_price = getattr(prices[lookback_index], "price", 0.0)
        if lookback_price <= 0:
            return None
        day_return = current_price / open_price - 1
        momentum = current_price / lookback_price - 1
        return day_return, momentum, getattr(current, "minute", "-")

    def _basket_weak_stats(self, model: TModel, client: MarketClient) -> tuple[float, float, str, int] | None:
        returns = []
        momentums = []
        minutes = []
        for peer in model.basket:
            stats = self._intraday_weak_stats(client.get_stock_prices(peer.code))
            if not stats:
                continue
            day_return, momentum, minute = stats
            returns.append(day_return)
            momentums.append(momentum)
            minutes.append(minute)
        if len(returns) < max(2, min(3, len(model.basket))):
            return None
        return sum(returns) / len(returns), sum(momentums) / len(momentums), max(minutes), len(returns)

    def _weak_level(self, day_return: float, momentum: float, return_threshold: float, momentum_threshold: float) -> str:
        if day_return <= return_threshold * 1.7 or momentum <= momentum_threshold * 2.0:
            return "严重"
        if day_return <= return_threshold or momentum <= momentum_threshold:
            return "警戒"
        return ""

    def _market_alert_key(self, now: datetime, category: str, name: str, level: str) -> str:
        bucket_minute = (now.minute // 30) * 30
        return f"{now.strftime('%Y-%m-%d')}:{now.hour:02d}:{bucket_minute:02d}:{category}:{name}:{level}"

    def _format_pct(self, value: float) -> str:
        return f"{value * 100:+.2f}%"

    def _format_market_index_alert(self, rows: list[tuple[str, float, float, str, str]], now: datetime) -> dict[str, str]:
        worst_level = "严重" if any(row[4] == "严重" for row in rows) else "警戒"
        lines = [
            f"大盘走弱提醒（{worst_level}）",
            f"北京时间 {now.strftime('%Y-%m-%d %H:%M')} 检测到指数走弱。",
            "这不是交易指令，请结合持仓、做T计划和盘中量价确认风险。",
            "",
        ]
        for name, day_return, momentum, minute, level in rows:
            lines.append(
                f"- {name}｜{level}｜{minute}｜日内 {self._format_pct(day_return)}｜近约5分钟 {self._format_pct(momentum)}"
            )
        return {
            "title": f"大盘走弱提醒：{worst_level}",
            "content": "\n".join(lines),
            "summary": f"{len(rows)} 个指数走弱",
        }

    def _format_basket_weak_alert(
        self,
        model: TModel,
        basket_return: float,
        momentum: float,
        minute: str,
        valid_count: int,
        level: str,
        now: datetime,
    ) -> dict[str, str]:
        peer_names = "、".join(peer.name for peer in model.basket[:5])
        lines = [
            f"板块/相似股走弱：{model.name}（{level}）",
            f"北京时间 {now.strftime('%Y-%m-%d %H:%M')} 检测到关联篮子走弱。",
            f"样本：{valid_count} 只相似股；{peer_names}",
            "",
            f"- {minute}｜篮子日内均值 {self._format_pct(basket_return)}｜近约5分钟 {self._format_pct(momentum)}",
            "这不是交易指令；若你正在做T或准备挂单，建议先确认大盘、板块和个股承接。",
        ]
        return {
            "title": f"板块走弱提醒：{model.name}",
            "content": "\n".join(lines),
            "summary": f"{model.name} 相似股篮子{level}走弱",
        }

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
                    f"新版已下载。\n版本：{sha}\n更新时间：{updated_at}\n\n是否现在打开新版？当前窗口会关闭，旧版文件会保留。",
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
