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
import hashlib
import traceback
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

from model_engine import BEIJING_TZ, INDEX_CODES, TRADING_SESSIONS, MarketClient, ModelSignalEngine, Security, TModel, load_models
from notifier import MultiNotifier, PushPlusNotifier, WeixinPushNotifier
from net_utils import safe_urlopen
from weixin_push import default_credentials_path as weixin_credentials_path
from weixin_push import load_targets as load_weixin_push_targets
from weixin_push import login_with_qr, refresh_context_tokens

try:
    from build_info import BUILD_SHA
except Exception:
    BUILD_SHA = "dev"

DEFAULT_INTERVAL_SECONDS = 30
INTRADAY_NEWS_INTERVAL_SECONDS = 60
MARKET_WEAK_INTERVAL_SECONDS = 300
PUSHPLUS_TOKEN_URL = "https://www.pushplus.plus/"
NEWS_SEARCH_URL = "https://market.ft.tech/data/api/v1/market/data/semantic-search-news"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
BING_SEARCH_URL = "https://cn.bing.com/search"
EASTMONEY_STOCK_INFO_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_STOCK_SECTORS_URL = "https://push2.eastmoney.com/api/qt/slist/get"
EASTMONEY_CONCEPT_BOARDS_URL = "https://push2.eastmoney.com/api/qt/clist/get"
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
WATCH_CONCEPTS = {
    "剑桥科技": ("CPO/光模块", "AI算力"),
    "东山精密": ("CPO/光模块", "PCB/AI服务器"),
    "福晶科技": ("光学/激光", "光通信"),
    "利通电子": ("算力租赁/算力基础设施", "PCB/AI服务器"),
}
CONCEPT_KEYWORDS = {
    "CPO/光模块": ("CPO", "光模块", "光通信", "800G", "1.6T"),
    "PCB/AI服务器": ("PCB", "FPC", "AI服务器", "服务器", "高多层板"),
    "算力租赁/算力基础设施": ("算力租赁", "算力", "智算中心", "数据中心", "云计算"),
    "光学/激光": ("激光晶体", "非线性晶体", "光学", "光通信"),
    "光通信": ("光通信", "光模块", "CPO"),
    "AI算力": ("AI算力", "算力", "数据中心", "CPO"),
}
CONCEPT_BASKETS = {
    "CPO/光模块": (
        ("中际旭创", "300308.XSHE"),
        ("新易盛", "300502.XSHE"),
        ("天孚通信", "300394.XSHE"),
        ("光迅科技", "002281.XSHE"),
        ("博创科技", "300548.XSHE"),
        ("联特科技", "301205.XSHE"),
        ("剑桥科技", "603083.XSHG"),
        ("仕佳光子", "688313.XSHG"),
    ),
    "PCB/AI服务器": (
        ("沪电股份", "002463.XSHE"),
        ("胜宏科技", "300476.XSHE"),
        ("生益电子", "688183.XSHG"),
        ("景旺电子", "603228.XSHG"),
        ("世运电路", "603920.XSHG"),
        ("东山精密", "002384.XSHE"),
        ("深南电路", "002916.XSHE"),
        ("鹏鼎控股", "002938.XSHE"),
    ),
    "算力租赁/算力基础设施": (
        ("鸿博股份", "002229.XSHE"),
        ("中贝通信", "603220.XSHG"),
        ("恒润股份", "603985.XSHG"),
        ("利通电子", "603629.XSHG"),
        ("润建股份", "002929.XSHE"),
        ("亚康股份", "301085.XSHE"),
        ("首都在线", "300846.XSHE"),
        ("奥飞数据", "300738.XSHE"),
    ),
    "光学/激光": (
        ("水晶光电", "002273.XSHE"),
        ("永新光学", "603297.XSHG"),
        ("腾景科技", "688195.XSHG"),
        ("茂莱光学", "688502.XSHG"),
        ("福晶科技", "002222.XSHE"),
        ("联创电子", "002036.XSHE"),
    ),
    "光通信": (
        ("光迅科技", "002281.XSHE"),
        ("中际旭创", "300308.XSHE"),
        ("新易盛", "300502.XSHE"),
        ("天孚通信", "300394.XSHE"),
        ("剑桥科技", "603083.XSHG"),
        ("博创科技", "300548.XSHE"),
    ),
    "AI算力": (
        ("中际旭创", "300308.XSHE"),
        ("新易盛", "300502.XSHE"),
        ("工业富联", "601138.XSHG"),
        ("浪潮信息", "000977.XSHE"),
        ("沪电股份", "002463.XSHE"),
        ("胜宏科技", "300476.XSHE"),
    ),
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
    "603629.SH": ("002463.SZ", "600183.SH", "002916.SZ", "603228.SH", "002938.SZ"),
}
RESEARCH_THEMES = {
    "002428.SZ": ("锗", "红外光学", "光纤级锗", "光伏级锗", "磷化铟", "砷化镓", "化合物半导体"),
    "603629.SH": ("PCB", "AI服务器", "算力", "云计算", "电子元器件", "服务器电源", "高多层板"),
    "002384.SZ": ("PCB", "FPC", "AI服务器", "新能源车", "消费电子", "光模块"),
    "603083.SH": ("CPO", "光模块", "光通信", "数据中心", "AI算力"),
    "002222.SZ": ("激光晶体", "非线性晶体", "光通信", "光学元件", "半导体设备"),
}
GLOBAL_RESEARCH_PEERS = {
    "002428.SZ": (
        ("Teck Resources", "TECK", "全球锌/锗相关资源，锗为伴生战略小金属"),
        ("Umicore", "UMI.BR", "全球材料回收与电子材料平台"),
        ("5N Plus", "VNP.TO", "高纯材料、半导体和特种金属材料"),
        ("AXT", "AXTI", "InP/GaAs 等化合物半导体衬底"),
        ("Indium Corporation", "Private", "铟/镓/锗相关电子材料，非上市可比"),
    ),
    "603629.SH": (
        ("TTM Technologies", "TTMI", "北美高端 PCB 与航空航天/数据中心板"),
        ("Ibiden", "4062.T", "日本高端封装基板/PCB，AI服务器链条"),
        ("Unimicron", "3037.TW", "台湾封装基板和高阶 PCB"),
        ("Compeq", "2313.TW", "台湾 PCB，通信与消费电子"),
        ("Kingboard", "0148.HK", "覆铜板/PCB 上游材料"),
    ),
    "002384.SZ": (
        ("TTM Technologies", "TTMI", "全球高端 PCB"),
        ("Unimicron", "3037.TW", "封装基板/高阶 PCB"),
        ("Compeq", "2313.TW", "通信和消费电子 PCB"),
        ("Ibiden", "4062.T", "高端封装基板"),
    ),
}
TAM_FRAMEWORKS = {
    "002428.SZ": (
        "锗下游不是单一大市场，而是红外光学、光纤通信、太阳能电池、催化剂和化合物半导体等多终端叠加。",
        "TAM 判断重点：全球锗供给相对刚性，若红外/卫星/光伏级锗/磷化铟需求共振，价格弹性通常大于销量弹性。",
        "落地指标：锗价、出口政策、公司高附加值锗产品销量、InP/GaAs 衬底价格和产能利用率。",
    ),
    "603629.SH": (
        "PCB 的 TAM 来自全球电子制造基座，AI服务器/交换机/高速互连把普通 PCB 需求推向高多层、高频高速、高可靠性。",
        "TAM 判断重点：AI服务器出货、英伟达/ASIC 服务器平台迭代、800G/1.6T 交换机、服务器电源和散热结构升级。",
        "落地指标：高多层板订单、AI服务器客户认证、服务器/通信占比、毛利率是否向高端 PCB 靠拢。",
    ),
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
RUMOR_WEAK_SOURCE_TERMS = {
    "小作文": 5,
    "传闻": 4,
    "据传": 4,
    "据说": 3,
    "听说": 3,
    "网传": 4,
    "市场传": 4,
    "内部消息": 5,
    "截图": 3,
    "朋友圈": 3,
    "群里": 3,
    "电话会": 2,
    "纪要": 2,
    "券商群": 4,
    "未证实": 5,
    "辟谣": 3,
}
RUMOR_EVENT_TERMS = {
    "失联": 6,
    "不敢回国": 7,
    "被查": 7,
    "带走": 7,
    "配合调查": 6,
    "行贿": 6,
    "受贿": 6,
    "财务造假": 7,
    "订单取消": 5,
    "砍单": 5,
    "暂停供货": 5,
    "被制裁": 6,
    "审批失败": 5,
    "并购失败": 5,
    "并购黄了": 6,
    "无法并表": 5,
    "客户砍单": 5,
    "董事长出事": 7,
    "实控人出事": 7,
    "监管问询": 4,
    "商誉减值": 4,
    "黑料": 6,
    "举报": 5,
    "抵制": 5,
    "不实": 4,
    "造谣": 4,
    "辟谣": 4,
    "澄清": 3,
    "利空": 4,
    "大利空": 6,
    "恐慌": 4,
    "踩踏": 5,
    "跳水": 4,
    "大跌": 3,
}
WILD_RUMOR_SOURCE_TERMS = (
    "股吧",
    "雪球",
    "微博",
    "weibo",
    "x.com",
    "twitter",
    "X平台",
    "Telegram",
    "电报群",
    "朋友圈",
    "微信群",
)
OFFICIAL_SOURCE_TERMS = (
    "公告",
    "巨潮",
    "上交所",
    "深交所",
    "交易所",
    "互动易",
    "证券时报",
    "中国证券报",
    "上海证券报",
    "财联社",
    "公司回应",
    "澄清",
)
RUMOR_ALERT_HEAT_THRESHOLD = 55
RUMOR_ALERT_IMPACT_THRESHOLD = 55
MARKET_WEAK_INDEX_THRESHOLD = -0.008
MARKET_WEAK_INDEX_MOMENTUM = -0.0025
MARKET_WEAK_BASKET_THRESHOLD = -0.012
MARKET_WEAK_BASKET_MOMENTUM = -0.004
MARKET_ALERT_RETURN_STEP = 0.004
MARKET_ALERT_MOMENTUM_STEP = 0.003
MARKET_ALERT_TURN_MOMENTUM = 0.0015
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
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "AShareTSignalMonitor"
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


def startup_log_path() -> Path:
    try:
        base = app_dir()
        base.mkdir(parents=True, exist_ok=True)
        return base / "startup_error.log"
    except Exception:
        return Path.home() / "AShareTSignalMonitor-startup-error.log"


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
        self.weixin_session_stop = threading.Event()
        self.weixin_session_worker: threading.Thread | None = None
        self.seen_intraday_news: set[str] = set()
        self.seen_rumor_events: dict[str, dict[str, object]] = {}
        self.seen_market_alerts: set[str] = set()
        self.market_alert_states: dict[str, dict[str, object]] = {}
        self.sector_info_cache: dict[str, tuple[float, dict[str, object]]] = {}
        self.concept_board_cache: tuple[float, list[dict[str, object]]] | None = None
        self.news_monitor_started_at: datetime | None = None
        self.models_dir = ensure_default_models()
        self.mascot_x = 86
        self.mascot_start_x = 86
        self.mascot_target_x = 86
        self.mascot_jump_frame = 0
        self.mascot_jump_frames = 0
        self.mascot_idle_step = 0

        self.token_var = tk.StringVar()
        self.weixin_mode_var = tk.StringVar(value="weixin")
        self.model_path_var = tk.StringVar(value=str(self.models_dir))
        self.interval_var = tk.StringVar(value=str(DEFAULT_INTERVAL_SECONDS))
        self.news_watchlist_var = tk.StringVar(value="剑桥科技，东山精密，福晶科技，利通电子")
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

        ttk.Label(form, text="微信推送", style="Muted.TLabel").grid(row=3, column=0, sticky=tk.W, padx=(0, 12), pady=7)
        ttk.Entry(form, textvariable=self.weixin_mode_var).grid(row=3, column=1, columnspan=3, sticky=tk.EW, padx=(0, 0), pady=7)

        ttk.Label(form, text="自选监控池", style="Muted.TLabel").grid(row=4, column=0, sticky=tk.W, padx=(0, 12), pady=7)
        ttk.Entry(form, textvariable=self.news_watchlist_var).grid(row=4, column=1, columnspan=3, sticky=tk.EW, padx=(0, 0), pady=7)

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
        self.risk_label.grid(row=5, column=0, columnspan=4, sticky=tk.EW, pady=(8, 0))

        help_frame = ttk.Frame(form, style="Soft.TFrame", padding=12)
        help_frame.grid(row=6, column=0, columnspan=4, sticky=tk.EW, pady=(10, 0))
        help_frame.columnconfigure(0, weight=1)
        help_text = (
            "PushPlus 使用：1. 打开 PushPlus 官网并用微信扫码登录；"
            "2. 在「一对一推送」页面复制 token；"
            "3. 粘贴到上方 token 输入框；"
            "4. 微信推送填 weixin，点击「微信登录」扫码，再在微信里给 bot 发一句话；"
            "5. 点「测试推送」，手机微信收到测试消息后再点「开始监控」。"
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
        weixin_frame = ttk.Frame(help_frame, style="Soft.TFrame")
        weixin_frame.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(10, 0))
        tk.Label(
            weixin_frame,
            text="微信推送：推荐填 weixin。扫码登录后，先在微信里给 bot 发一句话，再点刷新会话。",
            bg=COLORS["card_soft"],
            fg=COLORS["muted"],
            justify=tk.LEFT,
            anchor=tk.W,
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(weixin_frame, text="微信登录", style="Ghost.TButton", command=self._start_weixin_login).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(weixin_frame, text="刷新会话", style="Ghost.TButton", command=self._start_weixin_session).pack(side=tk.LEFT, padx=(8, 0))

        controls = ttk.Frame(outer)
        controls.pack(fill=tk.X, pady=(0, 12))
        controls_top = ttk.Frame(controls)
        controls_top.pack(fill=tk.X, pady=(0, 8))
        controls_bottom = ttk.Frame(controls)
        controls_bottom.pack(fill=tk.X)
        ttk.Button(controls_top, text="模型盘前", style="Ghost.TButton", command=self._show_premarket_analysis).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls_top, text="信息面盘前", style="Ghost.TButton", command=self._show_info_premarket).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls_top, text="自选信息面", style="Ghost.TButton", command=self._open_custom_info_window).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls_top, text="小作文雷达", style="Ghost.TButton", command=self._open_rumor_radar_window).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls_top, text="自选走势", style="Ghost.TButton", command=self._open_watch_chart_window).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls_top, text="AI研报", style="Ghost.TButton", command=self._open_research_report_window).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls_top, text="更新程序", style="Ghost.TButton", command=self._check_update).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls_top, text="测试推送", style="Ghost.TButton", command=self._test_push).pack(side=tk.LEFT)
        tk.Checkbutton(
            controls_bottom,
            text="盘中信息异动",
            variable=self.info_alert_var,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            activebackground=COLORS["bg"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["card"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(
            controls_bottom,
            text="大盘/自选板块",
            variable=self.market_alert_var,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            activebackground=COLORS["bg"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["card"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(controls_bottom, text="开始监控", style="Primary.TButton", command=self._start).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls_bottom, text="停止", style="Warm.TButton", command=self._stop).pack(side=tk.LEFT)

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

    def _start_weixin_login(self) -> None:
        if self.weixin_session_worker and self.weixin_session_worker.is_alive():
            self._log("微信会话刷新已经在运行")
        self.weixin_mode_var.set("weixin")
        self.weixin_session_stop.clear()
        threading.Thread(target=self._run_weixin_login, daemon=True).start()

    def _run_weixin_login(self) -> None:
        try:
            login_with_qr(logger=lambda msg: self.queue.put(("log", msg)), stop=self.weixin_session_stop.is_set)
            self.queue.put(("log", "微信扫码登录完成。请在微信里给这个 bot 发一句话，然后点「刷新会话」。"))
            self._ensure_weixin_session_worker()
        except Exception as exc:
            self.queue.put(("error", f"微信登录失败：{exc}"))

    def _start_weixin_session(self) -> None:
        try:
            load_weixin_push_targets()
            messagebox.showinfo("微信会话已就绪", f"已找到可推送会话。凭证：{weixin_credentials_path()}")
        except Exception as exc:
            self._log(f"微信会话还未就绪：{exc}")
        self._ensure_weixin_session_worker()

    def _ensure_weixin_session_worker(self) -> None:
        if self.weixin_session_worker and self.weixin_session_worker.is_alive():
            return
        self.weixin_session_stop.clear()
        self.weixin_session_worker = threading.Thread(target=self._run_weixin_session_worker, daemon=True)
        self.weixin_session_worker.start()
        self._log("微信会话刷新已启动。请在微信里给这个 bot 发一句话。")

    def _run_weixin_session_worker(self) -> None:
        refresh_context_tokens(logger=lambda msg: self.queue.put(("log", msg)), stop=self.weixin_session_stop.is_set)

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
        weixin_mode = self.weixin_mode_var.get().strip()
        if not token and not weixin_mode:
            messagebox.showwarning("缺少推送配置", "请先输入 PushPlus token，或填写微信推送模式 weixin。")
            return
        try:
            self._build_alert_notifier(token, weixin_mode).send_text("做T提醒测试：GUI 监控程序已接通。", title="做T提醒测试")
            self._log("测试消息已发送")
            messagebox.showinfo("成功", "测试消息已发送。")
        except Exception as exc:
            messagebox.showerror("推送失败", str(exc))

    def _build_alert_notifier(self, token: str, weixin_mode: str = "") -> MultiNotifier:
        return MultiNotifier([PushPlusNotifier(token), WeixinPushNotifier(weixin_mode)])

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

    def _open_rumor_radar_window(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("小作文雷达")
        window.geometry("900x680")
        window.configure(bg=COLORS["bg"])

        panel = ttk.Frame(window, style="Card.TFrame", padding=14)
        panel.pack(fill=tk.X, padx=14, pady=(14, 8))
        panel.columnconfigure(1, weight=1)

        watch_var = tk.StringVar(value=self.news_watchlist_var.get())
        hours_var = tk.StringVar(value="3")
        status_var = tk.StringVar(value="手动查看当前时间之前的小作文/传闻雷达，不会触发推送。")

        ttk.Label(panel, text="股票池", style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        entry = ttk.Entry(panel, textvariable=watch_var)
        entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 10))
        ttk.Label(panel, text="小时", style="Muted.TLabel").grid(row=0, column=2, sticky=tk.E, padx=(0, 8))
        ttk.Entry(panel, textvariable=hours_var, width=6).grid(row=0, column=3, sticky=tk.W, padx=(0, 10))

        result = tk.Text(window, wrap=tk.WORD, bg=COLORS["card"], fg=COLORS["text"], relief=tk.FLAT, padx=16, pady=14, font=("Microsoft YaHei UI", 10))
        result.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        tk.Label(window, textvariable=status_var, bg=COLORS["bg"], fg=COLORS["muted"], anchor=tk.W, font=("Microsoft YaHei UI", 9)).pack(fill=tk.X, padx=16, pady=(0, 10))

        def run_radar() -> None:
            watch_text = watch_var.get().strip()
            if not watch_text:
                messagebox.showwarning("缺少股票", "请输入要查看的小作文监控股，例如 东山精密，福晶科技。")
                return
            try:
                hours = max(1, min(8, int(hours_var.get().strip())))
            except ValueError:
                messagebox.showwarning("小时无效", "小时请输入 1-8 的数字。")
                return
            result.configure(state=tk.NORMAL)
            result.delete("1.0", tk.END)
            result.insert(tk.END, "正在扫描当前时间之前的传闻/小作文线索，请稍候...\n")
            result.configure(state=tk.DISABLED)
            status_var.set("正在扫描小作文雷达...")

            def worker() -> None:
                try:
                    content = self._build_rumor_radar_report(watch_text, hours)
                    self.queue.put(("custom_info_result", {"text": result, "status": status_var, "content": content}))
                except Exception as exc:
                    self.queue.put(("custom_info_result", {"text": result, "status": status_var, "content": f"小作文雷达扫描失败：{exc}"}))

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(panel, text="扫描", style="Primary.TButton", command=run_radar).grid(row=0, column=4, sticky=tk.E)
        entry.bind("<Return>", lambda _event: run_radar())
        entry.focus_set()

    def _build_rumor_radar_report(self, watch_text: str, hours: int = 3) -> str:
        now = datetime.now(BEIJING_TZ)
        start = now - timedelta(hours=hours)
        profiles = self._profiles_from_watch_text(watch_text)
        client = MarketClient(ttl_seconds=30)
        lines = [
            f"小作文雷达手动扫描（北京时间 {now.strftime('%Y-%m-%d %H:%M')}）",
            f"范围：当前时间之前最近 {hours} 小时；只展示，不推送，也不计入自动推送去重。",
            "说明：它判断的是传播/杀伤力/验证状态，不裁定真假。",
            "",
        ]
        if not profiles:
            lines.append("没有识别到股票池。")
            return "\n".join(lines)

        for name, profile in profiles.items():
            items = self._collect_rumor_candidates(name, profile, start, now)
            scored = []
            for item in items:
                if self._news_age_minutes(item, now) > hours * 60:
                    continue
                score = self._rumor_item_score(name, profile, item)
                if score["single_score"] < 8:
                    continue
                copied = dict(item)
                copied.update(score)
                scored.append(copied)
            events = self._cluster_rumor_events(name, profile, scored, client, now)
            lines.append(f"{name}：")
            if not events:
                lines.append("- 暂未发现明显小作文/传闻扩散线索。")
                lines.append("")
                continue
            for index, event in enumerate(events[:4], start=1):
                market = event.get("market") if isinstance(event.get("market"), dict) else {}
                lines.append(f"- 事件 {index}：{event.get('event_title', event.get('claim', '未提取到明确主张'))}")
                lines.append(
                    f"  热度 {event.get('rumor_heat')}/100｜可信度 {event.get('credibility')}/100｜"
                    f"杀伤力 {event.get('impact_score')}/100｜官方反证/澄清 {event.get('contradiction')}/100"
                )
                lines.append(
                    f"  类型：{event.get('event_type')}；来源数：{event.get('source_count')}；"
                    f"野源数：{event.get('wild_source_count', 0)}；"
                    f"时间：{event.get('first_seen', '-')} 至 {event.get('latest_seen', '-')}"
                )
                lines.append(
                    f"  行情共振：{market.get('minute', '-')} 近15分钟 {market.get('price_drop_15m', 0)}%，量能比 {market.get('volume_ratio', 1)}"
                )
                if int(event.get("wild_source_count") or 0) == 0:
                    lines.append("  覆盖提示：未命中微博/X/股吧/雪球原始野源，当前结果可能只是正规新闻或聚合噪音。")
                lines.append(f"  核心主张：{event.get('claim', '未提取到明确主张')}")
                lines.append(f"  触发词：{event.get('trigger_words', '无明显触发词')}")
                for item in event.get("items", [])[:4]:
                    title = self._clean_text(str(item.get("title") or "无标题"), 88)
                    source = item.get("source_site") or item.get("media_name") or item.get("_source") or "未知来源"
                    publish = str(item.get("publish_time") or item.get("fetch_time") or "-").replace("T", " ")[:16]
                    url = item.get("article_url") or ""
                    source_type = "野源" if int(item.get("wild_score") or 0) else "正式/聚合"
                    lines.append(f"  - 单条强度 {item.get('single_score')}｜{source_type}｜{publish}｜{source}：{title}")
                    if url:
                        lines.append(f"    {url}")
            lines.append("")
        return "\n".join(lines)

    def _open_watch_chart_window(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("自选走势")
        window.geometry("980x680")
        window.configure(bg=COLORS["bg"])

        panel = ttk.Frame(window, style="Card.TFrame", padding=14)
        panel.pack(fill=tk.X, padx=14, pady=(14, 8))
        panel.columnconfigure(1, weight=1)

        query_var = tk.StringVar(value="剑桥科技")
        period_var = tk.StringVar(value="日线")
        status_var = tk.StringVar(value="输入股票名或代码，查看分时/日线/周线/月线及支撑压力。")

        ttk.Label(panel, text="股票", style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        entry = ttk.Entry(panel, textvariable=query_var)
        entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 10))
        period_box = ttk.Combobox(panel, textvariable=period_var, values=("分时", "日线", "周线", "月线"), width=8, state="readonly")
        period_box.grid(row=0, column=2, sticky=tk.W, padx=(0, 10))

        canvas = tk.Canvas(window, bg=COLORS["card"], highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        summary = tk.Text(window, height=5, wrap=tk.WORD, bg=COLORS["card_soft"], fg=COLORS["text"], relief=tk.FLAT, padx=12, pady=10, font=("Microsoft YaHei UI", 9))
        summary.pack(fill=tk.X, padx=14, pady=(0, 8))
        tk.Label(window, textvariable=status_var, bg=COLORS["bg"], fg=COLORS["muted"], anchor=tk.W, font=("Microsoft YaHei UI", 9)).pack(fill=tk.X, padx=16, pady=(0, 10))

        def run_chart() -> None:
            query = query_var.get().strip()
            if not query:
                messagebox.showwarning("缺少股票", "请输入股票名称或代码，例如 剑桥科技 / 603083.SH。")
                return
            status_var.set("正在获取走势数据...")
            summary.configure(state=tk.NORMAL)
            summary.delete("1.0", tk.END)
            summary.insert(tk.END, "正在计算支撑、压力和筹码峰...\n")
            summary.configure(state=tk.DISABLED)
            canvas.delete("all")

            def worker() -> None:
                try:
                    payload = self._build_chart_payload(query, period_var.get())
                    payload.update({"canvas": canvas, "summary": summary, "status": status_var})
                    self.queue.put(("chart_result", payload))
                except Exception as exc:
                    self.queue.put(("chart_result", {"canvas": canvas, "summary": summary, "status": status_var, "error": str(exc)}))

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(panel, text="查看走势", style="Primary.TButton", command=run_chart).grid(row=0, column=3, sticky=tk.E)
        entry.bind("<Return>", lambda _event: run_chart())
        period_box.bind("<<ComboboxSelected>>", lambda _event: run_chart())
        entry.focus_set()

    def _build_chart_payload(self, query: str, period: str) -> dict[str, object]:
        code = self._resolve_stock_code(query)
        if not code:
            raise RuntimeError("没有识别到股票代码，请输入 6 位代码或已内置的股票简称。")
        ft_code = self._to_ft_stock_code(code)
        rows = self._fetch_intraday_chart_rows(ft_code) if period == "分时" else self._fetch_ohlc_chart_rows(ft_code, period)
        if len(rows) < 2:
            raise RuntimeError("走势数据不足，可能非交易时间或接口暂时无数据。")
        levels = self._chart_levels(rows)
        info = self._fetch_security_info(code)
        name = str(info.get("symbol_name") or query)
        return {"query": query, "name": name, "code": code, "period": period, "rows": rows, "levels": levels}

    def _to_ft_stock_code(self, code: str) -> str:
        upper = code.upper()
        if upper.endswith(".SH"):
            return upper.replace(".SH", ".XSHG")
        if upper.endswith(".SZ"):
            return upper.replace(".SZ", ".XSHE")
        if re.fullmatch(r"\d{6}", upper):
            return f"{upper}.XSHG" if upper.startswith("6") else f"{upper}.XSHE"
        return upper

    def _fetch_intraday_chart_rows(self, ft_code: str) -> list[dict[str, float | str]]:
        prices = MarketClient(ttl_seconds=20).get_stock_prices(ft_code)
        return [{"label": item.minute, "close": item.price, "high": item.price, "low": item.price, "volume": item.volume} for item in prices]

    def _fetch_ohlc_chart_rows(self, ft_code: str, period: str) -> list[dict[str, float | str]]:
        span = {"日线": "DAY1", "周线": "WEEK1", "月线": "MONTH1"}.get(period, "DAY1")
        query = urllib.parse.urlencode({"span": span, "limit": 160})
        url = f"https://market.ft.tech/app/api/v2/stocks/{urllib.parse.quote(ft_code)}/ohlcs?{query}"
        req = urllib.request.Request(url, headers={"X-Client-Name": "ft-claw", "Content-Type": "application/json"})
        with safe_urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows: list[dict[str, float | str]] = []
        for item in data.get("ohlcs", []):
            if not isinstance(item, dict) or "c" not in item:
                continue
            ts = item.get("tm") or item.get("ctm") or item.get("otm")
            label = "-"
            if isinstance(ts, int):
                label = datetime.fromtimestamp(ts / 1000, BEIJING_TZ).strftime("%m-%d")
            close = float(item["c"])
            rows.append({"label": label, "close": close, "high": float(item.get("h") or close), "low": float(item.get("l") or close), "volume": float(item.get("v") or 1)})
        return rows

    def _chart_levels(self, rows: list[dict[str, float | str]]) -> dict[str, float]:
        level_rows = rows[-min(30, len(rows)) :]
        volume_rows = rows[-min(80, len(rows)) :]
        closes = [float(row["close"]) for row in level_rows]
        lows = [float(row.get("low") or row["close"]) for row in level_rows]
        highs = [float(row.get("high") or row["close"]) for row in level_rows]
        current = closes[-1]
        sorted_lows = sorted(lows)
        sorted_highs = sorted(highs)
        support_candidates = [
            sorted_lows[max(0, int(len(sorted_lows) * ratio) - 1)]
            for ratio in (0.28, 0.38, 0.50)
        ]
        support_below = [value for value in support_candidates if value < current]
        support = max(support_below) if support_below else min(lows)
        resistance_candidates = [
            sorted_highs[min(len(sorted_highs) - 1, int(len(sorted_highs) * ratio))]
            for ratio in (0.62, 0.78, 0.90)
        ]
        resistance_above = [value for value in resistance_candidates if value > current]
        resistance = min(resistance_above) if resistance_above else max(highs)
        low = min(lows)
        high = max(highs)
        chip_peak = current
        if high > low:
            bins = [0.0] * 24
            step = (high - low) / len(bins)
            for row in volume_rows:
                price = float(row["close"])
                volume = max(1.0, float(row.get("volume") or 1))
                index = min(len(bins) - 1, max(0, int((price - low) / step)))
                bins[index] += volume
            max_index = max(range(len(bins)), key=lambda idx: bins[idx])
            chip_peak = low + (max_index + 0.5) * step
        return {"current": current, "support": support, "resistance": resistance, "chip_peak": chip_peak, "low": low, "high": high}

    def _render_chart_payload(self, payload: dict[str, object]) -> None:
        canvas = payload.get("canvas")
        summary = payload.get("summary")
        status_var = payload.get("status")
        if payload.get("error"):
            if hasattr(status_var, "set"):
                status_var.set("走势获取失败")
            if hasattr(summary, "configure") and hasattr(summary, "insert"):
                summary.configure(state=tk.NORMAL)
                summary.delete("1.0", tk.END)
                summary.insert(tk.END, f"走势获取失败：{payload.get('error')}")
                summary.configure(state=tk.DISABLED)
            return
        rows = payload.get("rows")
        levels = payload.get("levels")
        if not hasattr(canvas, "create_line") or not isinstance(rows, list) or not isinstance(levels, dict):
            return
        self._draw_price_chart(canvas, rows, levels, str(payload.get("name") or ""), str(payload.get("period") or ""))
        if hasattr(summary, "configure") and hasattr(summary, "insert"):
            summary.configure(state=tk.NORMAL)
            summary.delete("1.0", tk.END)
            summary.insert(tk.END, self._chart_summary_text(payload))
            summary.configure(state=tk.DISABLED)
        if hasattr(status_var, "set"):
            status_var.set("走势更新完成")

    def _draw_price_chart(self, canvas: tk.Canvas, rows: list[dict[str, float | str]], levels: dict[str, float], name: str, period: str) -> None:
        canvas.update_idletasks()
        width = max(760, canvas.winfo_width())
        height = max(380, canvas.winfo_height())
        canvas.delete("all")
        pad_left, pad_right, pad_top, pad_bottom = 58, 86, 34, 42
        lows = [float(row.get("low") or row["close"]) for row in rows]
        highs = [float(row.get("high") or row["close"]) for row in rows]
        min_price = min(min(lows), float(levels.get("support", lows[-1])), float(levels.get("chip_peak", lows[-1])))
        max_price = max(max(highs), float(levels.get("resistance", highs[-1])), float(levels.get("chip_peak", highs[-1])))
        spread = max_price - min_price or 1.0
        min_price -= spread * 0.08
        max_price += spread * 0.08

        def x_at(index: int) -> float:
            return pad_left + index * (width - pad_left - pad_right) / max(1, len(rows) - 1)

        def y_at(price: float) -> float:
            return pad_top + (max_price - price) * (height - pad_top - pad_bottom) / (max_price - min_price)

        for i in range(5):
            y = pad_top + i * (height - pad_top - pad_bottom) / 4
            price = max_price - i * (max_price - min_price) / 4
            canvas.create_line(pad_left, y, width - pad_right, y, fill="#E7DED0")
            canvas.create_text(12, y, text=f"{price:.2f}", anchor=tk.W, fill=COLORS["muted"], font=("Microsoft YaHei UI", 8))

        points = []
        for index, row in enumerate(rows):
            points.extend([x_at(index), y_at(float(row["close"]))])
            if period != "分时":
                x = x_at(index)
                canvas.create_line(x, y_at(float(row.get("low") or row["close"])), x, y_at(float(row.get("high") or row["close"])), fill="#B8C9BD")
        if len(points) >= 4:
            canvas.create_line(*points, fill=COLORS["sage_dark"], width=2, smooth=True)

        for label, price, color in (
            ("压力", float(levels["resistance"]), COLORS["danger"]),
            ("筹码峰", float(levels["chip_peak"]), COLORS["coral"]),
            ("支撑", float(levels["support"]), COLORS["sage_dark"]),
        ):
            y = y_at(price)
            canvas.create_line(pad_left, y, width - pad_right, y, fill=color, dash=(6, 4), width=1)
            canvas.create_text(width - pad_right + 8, y, text=f"{label} {price:.2f}", anchor=tk.W, fill=color, font=("Microsoft YaHei UI", 9, "bold"))

        canvas.create_text(pad_left, 18, text=f"{name} {period} 走势", anchor=tk.W, fill=COLORS["text"], font=("Microsoft YaHei UI", 12, "bold"))
        canvas.create_text(pad_left, height - 18, text=str(rows[0].get("label") or ""), anchor=tk.W, fill=COLORS["muted"], font=("Microsoft YaHei UI", 8))
        canvas.create_text(width - pad_right, height - 18, text=str(rows[-1].get("label") or ""), anchor=tk.E, fill=COLORS["muted"], font=("Microsoft YaHei UI", 8))

    def _chart_summary_text(self, payload: dict[str, object]) -> str:
        levels = payload.get("levels") if isinstance(payload.get("levels"), dict) else {}
        rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
        current = float(levels.get("current") or 0)
        support = float(levels.get("support") or 0)
        resistance = float(levels.get("resistance") or 0)
        chip_peak = float(levels.get("chip_peak") or 0)
        support_gap = (current / support - 1) * 100 if support else 0.0
        resistance_gap = (resistance / current - 1) * 100 if current else 0.0
        return (
            f"{payload.get('name')}（{payload.get('code')}）{payload.get('period')}，样本 {len(rows)} 条。\n"
            f"现价/最新：{current:.2f}；支撑位：{support:.2f}（距现价 {support_gap:.1f}%）；压力位：{resistance:.2f}（上方空间 {resistance_gap:.1f}%）；筹码峰：{chip_peak:.2f}。\n"
            "支撑/压力来自近段高低价分位，筹码峰来自成交量加权价格分布；它们是辅助观察线，不是自动买卖建议。"
        )

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
        symbol = str(info.get("symbol") or code)
        themes = self._research_themes_for(symbol, name, query)
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
        lines.extend(self._research_market_size_section(symbol, themes, industry_ranked))
        lines.append("")
        lines.extend(self._research_company_status_section(symbol, name, themes, industry_ranked))
        lines.append("")
        lines.extend(self._research_peer_section(info, peer_infos))
        lines.append("")
        lines.extend(self._research_global_peer_section(symbol))
        lines.append("")
        lines.extend(self._research_expectation_section(symbol, name, info, industry_ranked, peer_infos))
        lines.append("")
        lines.extend(self._research_target_distribution_section(info, peer_infos, industry_ranked))
        lines.append("")
        lines.extend(self._research_action_risk_section(symbol, name, info, industry_ranked, peer_infos))
        lines.append("")
        lines.extend(self._research_sources_section(industry_ranked))
        lines.append("")
        lines.append("免责声明：这是本地小 AI 模型生成的研报草稿，只做信息整理和研究框架，不构成投资建议；估值和预期需以交易软件、公告原文和正式研报复核。")
        return "\n".join(lines)

    def _collect_research_items(self, name: str, themes: tuple[str, ...] | list[str], now: datetime) -> list[dict[str, object]]:
        start = now - timedelta(days=14)
        core_theme = " ".join(themes[:4])
        alt_theme = " ".join(themes[4:8])
        queries = [
            name,
            f"{name} {core_theme}",
            f"{name} 东方财富 同花顺 研报",
            f"{name} 龙虎榜 资金 异动",
            f"{core_theme} 市场规模 行业空间",
            f"{core_theme} 全球 同行 估值",
            f"{alt_theme} 需求 供给 价格" if alt_theme else f"{core_theme} 需求 供给 价格",
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
        core_terms = tuple(dict.fromkeys([str(word) for word in themes] + ["市场规模", "行业空间", "全球", "同行", "估值"]))
        for word in core_terms:
            if word in text:
                score += 2.0
            if word in title:
                score += 1.2
        for word in themes:
            if word in text:
                score += 0.8
        irrelevant_terms = ("眼镜", "脱毛", "汽车皮革", "量子光学", "激光相互作用", "光学科技")
        if any(word in text for word in irrelevant_terms) and name not in text and not any(word in text for word in themes[:4]):
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
        with safe_urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def _research_peers_for(self, code: str) -> tuple[str, ...]:
        if code in RESEARCH_PEERS:
            return RESEARCH_PEERS[code]
        return tuple(symbol for symbol in ("600497.SH", "000060.SZ", "600206.SH") if symbol != code)

    def _research_themes_for(self, symbol: str, name: str, query: str) -> tuple[str, ...]:
        if symbol in RESEARCH_THEMES:
            return RESEARCH_THEMES[symbol]
        profile = self._news_profile_for_query(name) or self._news_profile_for_query(query)
        if profile:
            return tuple(profile.get("theme", ())) or tuple(self._query_terms(query))
        terms = tuple(self._query_terms(query))
        return terms or (name,)

    def _research_snapshot(self, info: dict[str, object]) -> list[str]:
        if not info:
            return ["一、行情估值快照", "- 未获取到行情估值数据。"]
        return [
            "一、行情估值快照",
            f"- 最新价/收盘：{self._fmt_num(info.get('close'))}；涨跌幅：{self._fmt_pct_value(info.get('change_rate'))}；换手率：{self._fmt_pct_value(info.get('turnover_rate'))}。",
            f"- 总市值：{self._fmt_money(info.get('market_cap'))}；流通市值：{self._fmt_money(info.get('float_a_market_cap'))}。",
            f"- PE(TTM)：{self._fmt_num(info.get('pe_ttm'))}；PB：{self._fmt_num(info.get('pb'))}；每股净资产：{self._fmt_num(info.get('bvps'))}。",
        ]

    def _research_market_size_section(self, symbol: str, themes: tuple[str, ...] | list[str], items: list[dict[str, object]]) -> list[str]:
        theme_text = "、".join(themes[:7])
        market_terms = self._top_keyword_hits(items, ("市场规模", "需求", "供给", "出口管制", "红外", "光纤", "光伏", "半导体", "军工", "卫星", "AI", "涨价"))
        lines = [
            "二、市场规模与行业位置",
            f"- 主题链条：{theme_text or '稀散金属/半导体材料'}。",
            f"- 小 AI 摘要：近期信息主要围绕 {market_terms or '锗价、红外光学、光纤通信、光伏衬底及化合物半导体'} 展开。",
        ]
        framework = TAM_FRAMEWORKS.get(symbol)
        if framework:
            lines.extend(f"- {item}" for item in framework)
        else:
            lines.append("- TAM 判断：先看终端需求是否扩张，再看公司产品是否切入高价值环节，最后看估值是否已提前透支。")
        return lines

    def _research_company_status_section(self, symbol: str, name: str, themes: tuple[str, ...] | list[str], items: list[dict[str, object]]) -> list[str]:
        positive = self._top_keyword_hits(items, ("合作", "扩产", "产能", "增长", "订单", "项目", "磷化铟", "砷化镓", "光伏级", "红外级"))
        risk = self._top_keyword_hits(items, ("亏损", "下滑", "减持", "质押", "问询", "处罚", "现金流", "毛利率"))
        if symbol == "603629.SH":
            main_line = "PCB制造 + AI服务器/算力链条 + 服务器电源相关业务弹性"
            positive = positive or self._top_keyword_hits(items, ("AI服务器", "算力", "PCB", "电源", "订单", "增长", "高多层"))
        elif symbol == "002428.SZ":
            main_line = "锗资源 + 锗材料深加工 + 化合物半导体延伸"
        else:
            main_line = "、".join(themes[:4]) or "主营业务景气和公司份额变化"
        return [
            "三、公司现状",
            f"- 主线：{name} 的研究主线来自 {main_line}。",
            f"- 积极线索：{positive or '高端材料、光伏级/红外级产品、化合物半导体项目'}。",
            f"- 风险线索：{risk or '盈利波动、产品价格周期、项目放量节奏、估值较高'}。",
            f"- 差异点：{name} 的关键不只是所在行业景气，而是能否在高价值产品/高端客户/产能兑现上形成可验证差异。",
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
        target_name = str(info.get("symbol_name") or "目标公司") if info else "目标公司"
        lines.append(f"- 估值解释：若 {target_name} PE/PB 显著高于同行，市场通常在定价更高成长性、稀缺性或资金情绪；但这也意味着业绩兑现要求更高。")
        return lines

    def _research_global_peer_section(self, symbol: str) -> list[str]:
        peers = GLOBAL_RESEARCH_PEERS.get(symbol)
        lines = ["五、全球同行与产业坐标"]
        if not peers:
            lines.append("- 暂无内置全球同行池；建议补充海外龙头、上游材料商、下游客户链条后再做全球估值横比。")
            return lines
        lines.append("公司 | 代码 | 对标逻辑")
        for name, ticker, logic in peers:
            lines.append(f"{name} | {ticker} | {logic}")
        lines.append("- 使用方式：全球同行不一定同业务同利润率，更多用于判断产业链位置、估值天花板和资金偏好，不能简单套 PE。")
        return lines

    def _research_expectation_section(
        self,
        symbol: str,
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
        if symbol == "603629.SH":
            catalyst_words = ("AI服务器", "算力", "PCB", "电源", "高多层", "订单", "客户认证", "毛利率", "产能")
            fallback_catalysts = "AI服务器订单、高多层 PCB 认证、服务器电源相关业务放量、毛利率改善"
            key_observation = "1）AI服务器/算力链订单；2）高多层板和服务器电源客户认证；3）毛利率是否向高端产品靠拢；4）PCB全球同行估值是否同步抬升。"
        else:
            catalyst_words = ("出口管制", "涨价", "红外", "卫星", "光伏", "磷化铟", "砷化镓", "半导体", "项目", "产能")
            fallback_catalysts = "锗价上涨、高端产品放量、磷化铟/砷化镓项目兑现、红外/卫星/光伏需求"
            key_observation = "1）锗价和出口政策；2）红外级/光伏级/光纤级产品销量与毛利；3）化合物半导体产能利用率；4）同行估值是否同步抬升。"
        catalysts = self._top_keyword_hits(items, catalyst_words)
        scenarios = self._research_scenarios(symbol, valuation_note)
        return [
            "六、预期与跟踪框架",
            f"- 估值状态：{name} 当前 {valuation_note}；如果利润基数较低，PE 会被放大，需更多看 PB、市值/资源量、项目兑现。",
            f"- 上行催化：{catalysts or fallback_catalysts}。",
            f"- 关键观察：{key_observation}",
            f"- 情景推演：{scenarios}",
        ]

    def _research_scenarios(self, symbol: str, valuation_note: str) -> str:
        if symbol == "603629.SH":
            return "乐观：AI服务器订单和高多层板认证兑现，毛利率上修；中性：传统电子+服务器业务温和改善；悲观：AI链条订单低于预期且价格竞争压低利润。"
        if symbol == "002428.SZ":
            return "乐观：锗价强势+InP/GaAs 放量，利润弹性兑现；中性：锗价高位震荡、项目逐步爬坡；悲观：产品价格回落或项目爬坡慢，高估值承压。"
        return f"乐观：行业景气和公司份额共振；中性：估值跟随基本面缓慢消化；悲观：{valuation_note} 且业绩兑现不及预期。"

    def _research_target_distribution_section(
        self,
        info: dict[str, object],
        peers: list[dict[str, object]],
        items: list[dict[str, object]],
    ) -> list[str]:
        lines = ["七、目标价分布与风险曲线"]
        current = self._to_float(info.get("close")) if info else None
        if not current or current <= 0:
            return lines + ["- 缺少当前价格，无法生成目标价分布。"]

        valuation = self._target_price_estimate(info, peers)
        base = valuation.get("base")
        if not base or base <= 0:
            return lines + ["- EPS/PB/PS 可用数据不足，无法用相对估值生成目标价。"]

        risk_score = self._research_risk_score(info, peers, items)
        bear = base * 0.78
        bull = base * 1.32
        stress = min(current * 0.65, bear)
        narrative = max(current * 1.18, bull * 1.22)
        grid = self._target_price_grid(stress, narrative, current, base, risk_score)
        method_text = "；".join(valuation.get("methods", []))
        implied_revenue = valuation.get("revenue")
        net_margin = valuation.get("net_margin")

        lines.extend(
            [
                f"- 方法：国际通用相对估值三法融合，EPS×PE、BVPS×PB、收入/股×PS，并按 ROE、净利率、同行估值分位做调整；不是 DCF 正式估值。",
                f"- 关键假设：{method_text or '可用指标不足，权重已自动归一'}。",
                f"- 隐含基本面：估算收入 {self._fmt_money(implied_revenue)}；估算净利率 {self._fmt_pct_ratio(net_margin)}；当前价 {current:.2f}。",
                f"- 中性合理区间：悲观 {bear:.2f} / 中性 {base:.2f} / 乐观 {bull:.2f}。价格越高，兑现要求和风险率越高。",
                "目标价-风险率分布：",
            ]
        )
        for price, risk in grid:
            bar = "█" * max(1, int(risk / 6))
            lines.append(f"  {price:.2f} 元 | 风险率 {risk:02d}/100 | {bar}")
        lines.append("- 解读：低风险价格不等于一定会跌到；高风险价格表示需要更强利润率、成长或情绪溢价才能支撑。")
        return lines

    def _target_price_estimate(self, info: dict[str, object], peers: list[dict[str, object]]) -> dict[str, object]:
        current = self._to_float(info.get("close"))
        pe = self._to_float(info.get("pe_ttm"))
        pb = self._to_float(info.get("pb"))
        ps = self._to_float(info.get("ps_ttm"))
        eps = self._to_float(info.get("eps_ttm"))
        bvps = self._to_float(info.get("bvps"))
        roe = self._to_float(info.get("roe_ttm"))
        market_cap = self._to_float(info.get("market_cap"))
        shares = self._to_float(info.get("shares")) or self._to_float(info.get("total_share"))
        if eps is None and current and pe and pe > 0:
            eps = current / pe
        if bvps is None and current and pb and pb > 0:
            bvps = current / pb
        revenue = market_cap / ps if market_cap and ps and ps > 0 else None
        revenue_per_share = revenue / shares if revenue and shares else None
        earnings = eps * shares if eps is not None and shares else None
        net_margin = earnings / revenue if earnings is not None and revenue else None

        peer_pe = self._peer_metric_average(peers, "pe_ttm", 5, 180)
        peer_pb = self._peer_metric_average(peers, "pb", 0.3, 25)
        peer_ps = self._peer_metric_average(peers, "ps_ttm", 0.2, 40)
        quality_factor = 1 + self._clamp(((roe or 0.08) - 0.08) * 1.8, -0.25, 0.35)
        margin_factor = 1 + self._clamp(((net_margin or 0.05) - 0.05) * 2.0, -0.22, 0.32)

        estimates: list[tuple[str, float, float]] = []
        if eps is not None and eps > 0 and peer_pe:
            value = eps * min(180.0, peer_pe * quality_factor)
            estimates.append(("EPS×调整PE", value, 0.45))
        if bvps is not None and bvps > 0 and peer_pb:
            value = bvps * min(25.0, peer_pb * quality_factor)
            estimates.append(("BVPS×调整PB", value, 0.30))
        if revenue_per_share and revenue_per_share > 0 and peer_ps:
            value = revenue_per_share * min(40.0, peer_ps * margin_factor)
            estimates.append(("收入/股×调整PS", value, 0.25))
        if not estimates:
            return {"base": None, "methods": [], "revenue": revenue, "net_margin": net_margin}
        total_weight = sum(weight for _name, _value, weight in estimates)
        base = sum(value * weight for _name, value, weight in estimates) / total_weight
        methods = [f"{name}={value:.2f}" for name, value, _weight in estimates]
        return {"base": base, "methods": methods, "revenue": revenue, "net_margin": net_margin}

    def _peer_metric_average(self, peers: list[dict[str, object]], key: str, low: float, high: float) -> float | None:
        values = [self._to_float(item.get(key)) for item in peers]
        filtered = sorted(value for value in values if value is not None and low <= value <= high)
        if not filtered:
            return None
        if len(filtered) >= 4:
            filtered = filtered[1:-1]
        return sum(filtered) / len(filtered)

    def _target_price_grid(self, low: float, high: float, current: float, base: float, risk_score: int) -> list[tuple[float, int]]:
        if high <= low:
            high = low * 1.5
        points = [low + (high - low) * index / 6 for index in range(7)]
        if all(abs(point - current) / current > 0.04 for point in points):
            points.append(current)
        points = sorted({round(point, 2) for point in points if point > 0})
        return [(point, self._target_price_risk(point, current, base, risk_score)) for point in points]

    def _target_price_risk(self, price: float, current: float, base: float, risk_score: int) -> int:
        upside = price / current - 1 if current else 0
        valuation_premium = price / base - 1 if base else 0
        downside_discount = max(0.0, current / price - 1) if price else 0.0
        raw = risk_score * 0.36 + max(0.0, upside) * 85 + max(0.0, valuation_premium) * 42 - min(24.0, downside_discount * 45)
        return int(round(self._clamp(raw, 0, 95)))

    def _fmt_pct_ratio(self, value: object) -> str:
        number = self._to_float(value)
        if number is None:
            return "-"
        return f"{number * 100:.2f}%"

    def _clamp(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _research_action_risk_section(
        self,
        symbol: str,
        name: str,
        info: dict[str, object],
        items: list[dict[str, object]],
        peers: list[dict[str, object]],
    ) -> list[str]:
        risk_score = self._research_risk_score(info, peers, items)
        if risk_score >= 70:
            risk_level = "高"
            action = "只适合观察或轻仓事件驱动，等待估值/回撤/业绩兑现出现更好赔率。"
        elif risk_score >= 45:
            risk_level = "中"
            action = "适合分批跟踪，重点看催化是否兑现，避免追高一次性重仓。"
        else:
            risk_level = "较低"
            action = "可作为基本面观察池，但仍需结合流动性和行业周期。"
        risk_terms = self._top_keyword_hits(items, ("跌停", "减持", "质押", "问询", "处罚", "亏损", "下滑", "价格不确定", "竞争"))
        return [
            "八、建议点与风险率",
            f"- 本地风险率：{risk_score}/100（{risk_level}）。该分数由估值溢价、PB、新闻风险词、短期异动共同估算。",
            f"- 操作建议点：{action}",
            f"- 重点风险：{risk_terms or '估值高、业绩兑现慢、行业景气波动、资金情绪退潮'}。",
            f"- 验证清单：若后续 1）订单/价格/产能数据改善，2）同行估值同步上移，3）公司利润率改善，则 {name} 的高估值更容易被消化；反之应降低预期。",
        ]

    def _research_risk_score(self, info: dict[str, object], peers: list[dict[str, object]], items: list[dict[str, object]]) -> int:
        score = 25
        pe = self._to_float(info.get("pe_ttm")) if info else None
        pb = self._to_float(info.get("pb")) if info else None
        peer_pes = [self._to_float(item.get("pe_ttm")) for item in peers]
        peer_pes = [value for value in peer_pes if value and value > 0]
        peer_avg = sum(peer_pes) / len(peer_pes) if peer_pes else None
        if pe and peer_avg and pe > peer_avg * 2:
            score += 25
        elif pe and pe > 80:
            score += 15
        if pb and pb > 10:
            score += 20
        elif pb and pb > 5:
            score += 10
        negative_hits = sum(1 for item in items if self._negative_news_severity(item) >= 5)
        score += min(20, negative_hits * 5)
        hot_terms = ("龙虎榜", "异动", "跌停", "涨停", "成交额", "主力资金")
        hot_hits = sum(1 for item in items if any(word in self._news_body_text(item) for word in hot_terms))
        score += min(15, hot_hits * 3)
        return max(0, min(100, score))

    def _research_sources_section(self, items: list[dict[str, object]]) -> list[str]:
        lines = ["九、信息源线索"]
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
            "603629.SH": "PCB/算力租赁/服务器电源相关",
            "002463.SZ": "高端 PCB/服务器与通信板",
            "600183.SH": "覆铜板/电子材料龙头",
            "002916.SZ": "封装基板/通信 PCB",
            "603228.SH": "PCB制造/汽车与服务器链",
            "002938.SZ": "消费电子/FPC/高端 PCB",
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
        with safe_urlopen(req, timeout=15) as resp:
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
        with safe_urlopen(req, timeout=60) as resp, target.open("wb") as out:
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
        with safe_urlopen(req, timeout=18) as resp:
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
        with safe_urlopen(req, timeout=10) as resp:
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
        weixin_mode = self.weixin_mode_var.get().strip()
        model_path = Path(self.model_path_var.get().strip())
        if not token and not weixin_mode:
            messagebox.showwarning("缺少推送配置", "请先输入 PushPlus token，或填写微信推送模式 weixin。")
            return
        if not model_path.exists():
            messagebox.showwarning("模型不存在", "请选择模型 JSON 文件或模型文件夹。")
            return
        try:
            interval = max(5, int(self.interval_var.get().strip()))
        except ValueError:
            messagebox.showwarning("间隔无效", "检查间隔必须是数字。")
            return

        if weixin_mode.lower() in ("weixin", "wechat", "wx", "builtin"):
            self._ensure_weixin_session_worker()
        self.stop_event.clear()
        self.worker = threading.Thread(
            target=self._run_worker,
            args=(model_path, token, weixin_mode, interval),
            daemon=True,
        )
        self.worker.start()
        if self.info_alert_var.get():
            self.seen_intraday_news.clear()
            self.seen_rumor_events.clear()
            self.news_monitor_started_at = datetime.now(BEIJING_TZ)
            self.news_worker = threading.Thread(
                target=self._run_intraday_news_worker,
                args=(token, weixin_mode),
                daemon=True,
            )
            self.news_worker.start()
        if self.market_alert_var.get():
            self.seen_market_alerts.clear()
            self.market_alert_states.clear()
            self.market_worker = threading.Thread(
                target=self._run_market_weak_worker,
                args=(model_path, token, weixin_mode),
                daemon=True,
            )
            self.market_worker.start()
        self.status_var.set("运行中")
        self.status_badge.configure(bg=COLORS["mint"], fg=COLORS["sage_dark"])
        notes = []
        if self.info_alert_var.get():
            notes.append("自选信息/小作文雷达已开启：" + "、".join(self._watched_news_profiles().keys()))
        if self.market_alert_var.get():
            notes.append("自选概念板块提醒已开启")
        info_note = "，" + "，".join(notes) if notes else ""
        self._log(f"监控已启动{info_note}")

    def _stop(self) -> None:
        self.stop_event.set()
        self.weixin_session_stop.set()
        self.news_monitor_started_at = None
        self.status_var.set("停止中")
        self.status_badge.configure(bg=COLORS["cream"], fg=COLORS["coral_dark"])
        self._log("正在停止监控")

    def _run_worker(self, model_path: Path, token: str, weixin_mode: str, interval: int) -> None:
        try:
            models = load_models(model_path)
            self.queue.put(("log", f"已加载 {len(models)} 个模型：" + "、".join(m.name for m in models)))
            self.queue.put(("risk", self._model_risk_summary(model_path)))
            engine = ModelSignalEngine(models, app_dir() / "data", token, weixin_mode)
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

    def _run_intraday_news_worker(self, token: str, weixin_mode: str) -> None:
        notifier = self._build_alert_notifier(token, weixin_mode)
        self.queue.put(("log", f"盘中小作文雷达已启动：每 {INTRADAY_NEWS_INTERVAL_SECONDS} 秒检查一次自选股"))
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
        start = now - timedelta(hours=3)
        alerts = []
        client = MarketClient(ttl_seconds=30)
        for name, profile in self._watched_news_profiles().items():
            items = self._collect_rumor_candidates(name, profile, start, now)
            scored = []
            for item in items:
                key = str(item.get("article_url") or item.get("news_id") or item.get("title") or "")
                if not key:
                    continue
                if self._news_age_minutes(item, now) > 180:
                    continue
                score = self._rumor_item_score(name, profile, item)
                if score["single_score"] < 8:
                    continue
                copied = dict(item)
                copied.update(score)
                scored.append(copied)
            for event in self._cluster_rumor_events(name, profile, scored, client, now):
                should_alert, reason = self._rumor_event_should_alert(event, now)
                if not should_alert:
                    continue
                for item in event["items"]:
                    key = str(item.get("article_url") or item.get("news_id") or item.get("title") or "")
                    if key:
                        self.seen_intraday_news.add(key)
                event["alert_reason"] = reason
                alerts.append(self._format_intraday_news_alert(name, event, now))
        return alerts

    def _rumor_event_should_alert(self, event: dict[str, object], now: datetime) -> tuple[bool, str]:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            return False, "缺少事件指纹"
        heat = int(event.get("rumor_heat") or 0)
        impact = int(event.get("impact_score") or 0)
        credibility = int(event.get("credibility") or 0)
        wild_count = int(event.get("wild_source_count") or 0)
        market = event.get("market") if isinstance(event.get("market"), dict) else {}
        market_resonance = float(market.get("price_drop_15m") or 0) <= -1.2 or float(market.get("volume_ratio") or 1) >= 1.8
        sensitive_enough = (
            heat >= RUMOR_ALERT_HEAT_THRESHOLD
            or impact >= RUMOR_ALERT_IMPACT_THRESHOLD
            or (wild_count > 0 and (heat >= 42 or impact >= 45))
            or (market_resonance and (heat >= 38 or impact >= 42))
            or (credibility >= 65 and impact >= 38)
        )
        if not sensitive_enough:
            return False, "强度未达推送阈值"

        latest_dt = self._parse_event_time(str(event.get("latest_seen") or ""))
        if self.news_monitor_started_at and latest_dt and latest_dt < self.news_monitor_started_at:
            return False, "事件发生在本轮监控启动前"

        prev = self.seen_rumor_events.get(event_id)
        current_score = max(heat, impact)
        if not prev:
            self.seen_rumor_events[event_id] = {"last_score": current_score, "last_seen": now.isoformat()}
            return True, "本轮监控发现的新事件簇"

        last_score = int(prev.get("last_score") or 0)
        if current_score >= last_score + 12:
            prev["last_score"] = current_score
            prev["last_seen"] = now.isoformat()
            return True, f"事件强度升级：{last_score} -> {current_score}"

        return False, "已推送过且未明显升级"

    def _watched_news_profiles(self) -> dict[str, dict[str, tuple[str, ...]]]:
        text = self.news_watchlist_var.get().strip() if hasattr(self, "news_watchlist_var") else ""
        return self._profiles_from_watch_text(text)

    def _profiles_from_watch_text(self, text: str) -> dict[str, dict[str, tuple[str, ...]]]:
        names = [part.strip() for part in re.split(r"[,，;；\s]+", text) if part.strip()]
        if not names:
            names = list(NEWS_PROFILES)
        profiles: dict[str, dict[str, tuple[str, ...]]] = {}
        for name in names[:12]:
            profile = self._news_profile_for_query(name)
            if profile:
                display_name = next((key for key, value in NEWS_PROFILES.items() if value is profile), name)
                profiles[display_name] = profile
                continue
            code = self._resolve_stock_code(name)
            themes = self._research_themes_for(code, name, name) if code else tuple(self._query_terms(name))
            aliases = tuple(dict.fromkeys((name, code.replace(".SH", "").replace(".SZ", "") if code else "")))
            profiles[name] = {"aliases": aliases, "required": aliases, "theme": tuple(themes[:7])}
        return profiles

    def _collect_rumor_candidates(
        self,
        name: str,
        profile: dict[str, tuple[str, ...]],
        start: datetime,
        now: datetime,
    ) -> list[dict[str, object]]:
        aliases = [word for word in profile.get("aliases", ()) if word]
        themes = [word for word in profile.get("theme", ()) if word]
        primary = aliases[0] if aliases else name
        queries = [
            primary,
            f"{primary} 小作文 传闻",
            f"{primary} 股吧 雪球 传闻",
            f"{primary} 董事长 实控人 被查 砍单",
            f"{primary} 小作文 利空 跳水 辟谣",
            f"{primary} 黑料 举报 不实 澄清",
            f"{primary} 微博 小作文",
            f"{primary} X Twitter 传闻",
            f"{primary} {' '.join(themes[:3])}".strip(),
            f"site:guba.eastmoney.com {primary}",
            f"site:xueqiu.com {primary}",
            f"site:weibo.com {primary}",
            f"site:x.com {primary}",
            f"site:twitter.com {primary}",
        ]
        seen = set()
        results = []
        for query in queries:
            if not query.strip():
                continue
            try:
                ft_items = self._search_news(query, start, now, limit=12)
            except Exception as exc:
                self.queue.put(("log", f"小作文 FTShare 搜索失败：{query}｜{exc}"))
                ft_items = []
            try:
                rss_items = self._search_google_news_rss(query, start, now, limit=12)
            except Exception as exc:
                self.queue.put(("log", f"小作文 Google 搜索失败：{query}｜{exc}"))
                rss_items = []
            try:
                web_items = self._search_bing_web(query, start, now, limit=10)
            except Exception as exc:
                self.queue.put(("log", f"小作文网页搜索失败：{query}｜{exc}"))
                web_items = []
            for item in [*ft_items, *rss_items, *web_items]:
                key = item.get("article_url") or item.get("news_id") or item.get("title")
                if not key or key in seen:
                    continue
                if not self._rumor_entity_match(name, profile, item):
                    continue
                seen.add(key)
                copied = dict(item)
                copied["_query"] = query
                results.append(copied)
        ranked = [
            item
            for item in self._rerank_news(primary, results, limit=48, broad=True)
            if self._rumor_entity_match(name, profile, item)
        ]
        return ranked

    def _search_bing_web(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 10,
    ) -> list[dict[str, object]]:
        params = {
            "q": query,
            "ensearch": "0",
            "setlang": "zh-CN",
            "cc": "cn",
            "count": str(limit),
        }
        url = BING_SEARCH_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AShareTSignalMonitor/1.0",
                "Accept": "text/html,application/xhtml+xml",
                "Connection": "close",
            },
        )
        with safe_urlopen(req, timeout=10) as resp:
            html_text = resp.read().decode("utf-8", errors="ignore")
        rows = []
        for match in re.finditer(r'<li class="b_algo".*?<h2.*?<a href="([^"]+)".*?>(.*?)</a>.*?(?:<p>(.*?)</p>)?', html_text, re.S):
            link = html.unescape(match.group(1)).strip()
            title = self._clean_html(match.group(2))
            summary = self._clean_html(match.group(3) or "")
            if not title or not link:
                continue
            source = self._source_from_url(link)
            rows.append(
                {
                    "title": title,
                    "summary": summary,
                    "article_url": link,
                    "source_site": source,
                    "publish_time": end.strftime("%Y-%m-%d %H:%M:%S"),
                    "_source": "Bing Web",
                    "_query": query,
                }
            )
            if len(rows) >= limit:
                break
        return rows

    def _clean_html(self, value: str) -> str:
        text = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
        return " ".join(text.split())

    def _source_from_url(self, url: str) -> str:
        host = urllib.parse.urlparse(url).netloc.lower()
        if "weibo.com" in host:
            return "微博"
        if "x.com" in host or "twitter.com" in host:
            return "X/Twitter"
        if "guba.eastmoney.com" in host:
            return "东方财富股吧"
        if "xueqiu.com" in host:
            return "雪球"
        return host or "Bing Web"

    def _rumor_item_score(self, name: str, profile: dict[str, tuple[str, ...]], item: dict[str, object]) -> dict[str, int | str]:
        text = self._news_body_text(item)
        title = str(item.get("title") or "")
        weak_score = sum(weight for word, weight in RUMOR_WEAK_SOURCE_TERMS.items() if word in text)
        event_score = sum(weight for word, weight in RUMOR_EVENT_TERMS.items() if word in text)
        negative_score = self._negative_news_severity(item)
        source = str(item.get("source_site") or item.get("media_name") or item.get("_source") or "")
        source_is_official = any(word in source for word in OFFICIAL_SOURCE_TERMS)
        title_has_confirmation = any(word in title for word in ("公司回应", "澄清", "公告称", "交易所", "监管函", "问询函"))
        title_denies_source = any(word in title for word in ("暂无公告", "无公告", "未公告", "未证实"))
        official_score = 18 if source_is_official or (title_has_confirmation and not title_denies_source) else 0
        aliases = tuple(profile.get("aliases", ()))
        theme = tuple(profile.get("theme", ()))
        alias_score = min(8, sum(3 for word in aliases if word and word in text))
        theme_score = min(5, sum(1 for word in theme if word and word in text))
        wild_score = 10 if self._is_wild_rumor_source(item) else 0
        single_score = alias_score + theme_score + weak_score + event_score + negative_score + official_score + wild_score
        event_type = self._rumor_event_type(text)
        return {
            "single_score": single_score,
            "weak_score": weak_score,
            "event_score": event_score,
            "negative_score": negative_score,
            "official_score": official_score,
            "wild_score": wild_score,
            "event_type": event_type,
        }

    def _rumor_entity_match(self, name: str, profile: dict[str, tuple[str, ...]], item: dict[str, object]) -> bool:
        text = self._news_body_text(item)
        aliases = [word for word in profile.get("aliases", ()) if word]
        required = [word for word in profile.get("required", ()) if word]
        code = self._resolve_stock_code(name)
        if code:
            raw_code = code.replace(".SH", "").replace(".SZ", "")
            aliases.extend([code, raw_code])
            required.extend([raw_code])
        aliases = list(dict.fromkeys(aliases))
        required = list(dict.fromkeys(required))
        if any(alias and alias in text for alias in aliases):
            return True
        if any(re.search(rf"[\$＄][^\\s，,。；;]*{re.escape(alias)}", text) for alias in required if alias):
            return True
        return False

    def _is_wild_rumor_source(self, item: dict[str, object]) -> bool:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("source_site", "media_name", "_source", "_query", "article_url", "title")
        )
        lowered = text.lower()
        return any(term.lower() in lowered for term in WILD_RUMOR_SOURCE_TERMS)

    def _rumor_event_type(self, text: str) -> str:
        groups = (
            ("实控人/监管", ("董事长", "实控人", "被查", "调查", "行贿", "带走", "失联")),
            ("订单/客户", ("订单", "砍单", "客户", "暂停供货", "解约")),
            ("并购/资产", ("并购", "收购", "审批", "并表", "商誉")),
            ("财务/业绩", ("财务", "造假", "亏损", "减值", "下修")),
            ("政策/制裁", ("制裁", "出口管制", "监管", "政策")),
            ("情绪/声誉传闻", ("黑料", "举报", "抵制", "不实", "造谣", "辟谣", "澄清", "利空", "恐慌", "踩踏", "跳水", "大跌")),
        )
        for label, words in groups:
            if any(word in text for word in words):
                return label
        return "其他传闻"

    def _cluster_rumor_events(
        self,
        name: str,
        profile: dict[str, tuple[str, ...]],
        items: list[dict[str, object]],
        client: MarketClient,
        now: datetime,
    ) -> list[dict[str, object]]:
        clusters: list[dict[str, object]] = []
        for item in sorted(items, key=lambda row: int(row.get("single_score") or 0), reverse=True):
            tokens = self._rumor_cluster_tokens(name, profile, item)
            triggers = self._rumor_item_triggers(item)
            event_type = str(item.get("event_type") or self._rumor_event_type(self._news_body_text(item)))
            placed = False
            for cluster in clusters:
                overlap = self._rumor_cluster_overlap(tokens, cluster["tokens"])
                trigger_overlap = len(triggers & cluster["triggers"])
                same_type = event_type == cluster["event_type"]
                if overlap >= 0.24 or (same_type and (overlap >= 0.10 or trigger_overlap >= 2)):
                    cluster["items"].append(item)
                    cluster["tokens"].update(tokens)
                    cluster["triggers"].update(triggers)
                    placed = True
                    break
            if not placed:
                clusters.append({"event_type": event_type, "tokens": set(tokens), "triggers": set(triggers), "items": [item]})

        events = []
        for cluster in clusters:
            event = self._build_rumor_event(name, profile, list(cluster["items"]), client, now)
            if event:
                event["event_id"] = self._rumor_cluster_id(name, str(cluster["event_type"]), cluster["tokens"], cluster["triggers"])
                events.append(event)
        events.sort(
            key=lambda event: (
                int(event.get("rumor_heat") or 0) + int(event.get("impact_score") or 0),
                int(event.get("wild_source_count") or 0),
            ),
            reverse=True,
        )
        return events

    def _rumor_cluster_tokens(
        self,
        name: str,
        profile: dict[str, tuple[str, ...]],
        item: dict[str, object],
    ) -> set[str]:
        text = self._news_body_text(item)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        for word in (name, *profile.get("aliases", ()), *profile.get("required", ())):
            if word:
                text = text.replace(word, " ")
        text = re.sub(r"https?://\S+", " ", text)
        text = re.sub(r"(东方财富|股吧|雪球|微博|证券时报|上海证券报|中国证券报|财联社|同花顺|新浪财经|腾讯新闻)", " ", text)
        words = set(re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text))
        useful = {
            word
            for word in words
            if word not in {"新闻", "财经", "公司", "股票", "市场", "今日", "一个", "这个", "相关", "链接"}
        }
        triggers = {word for word in (*RUMOR_WEAK_SOURCE_TERMS.keys(), *RUMOR_EVENT_TERMS.keys(), *SEVERE_NEGATIVE_TERMS.keys()) if word in text}
        grams = self._char_grams("".join(sorted(useful))[:140])
        return set(list(useful)[:40]) | triggers | set(list(grams)[:80])

    def _rumor_cluster_overlap(self, left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / max(1, min(len(left), len(right)))

    def _rumor_item_triggers(self, item: dict[str, object]) -> set[str]:
        text = self._news_body_text(item)
        vocab = [*RUMOR_WEAK_SOURCE_TERMS.keys(), *RUMOR_EVENT_TERMS.keys(), *SEVERE_NEGATIVE_TERMS.keys()]
        return {word for word in vocab if word in text}

    def _rumor_cluster_id(self, name: str, event_type: str, tokens: set[str], triggers: set[str]) -> str:
        stable_tokens = sorted(token for token in tokens if len(token) >= 2 and not re.fullmatch(r"\d+", token))[:18]
        stable_triggers = sorted(triggers)[:10]
        text = "|".join([name, event_type, *stable_triggers, *stable_tokens])
        normalized = re.sub(r"\W+", "", text.lower())
        return hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _build_rumor_event(
        self,
        name: str,
        profile: dict[str, tuple[str, ...]],
        items: list[dict[str, object]],
        client: MarketClient,
        now: datetime,
    ) -> dict[str, object] | None:
        if not items:
            return None
        items.sort(key=lambda item: int(item.get("single_score") or 0), reverse=True)
        top_items = items[:8]
        unique_sources = {
            str(item.get("source_site") or item.get("media_name") or item.get("_source") or "未知")
            for item in top_items
        }
        wild_source_count = sum(1 for item in top_items if int(item.get("wild_score") or 0) > 0)
        weak_total = sum(int(item.get("weak_score") or 0) for item in top_items)
        event_total = sum(int(item.get("event_score") or 0) for item in top_items)
        official_total = sum(int(item.get("official_score") or 0) for item in top_items)
        negative_total = sum(int(item.get("negative_score") or 0) for item in top_items)
        freshness_bonus = max(0, 20 - int(min((self._news_age_minutes(top_items[0], now), 60)) / 3))
        rumor_heat = min(100, len(top_items) * 12 + len(unique_sources) * 8 + weak_total + freshness_bonus)
        credibility = min(100, official_total + len(unique_sources) * 7 + max(0, 18 - weak_total // 2))
        impact = min(100, event_total * 3 + negative_total * 5 + weak_total * 2 + rumor_heat // 3)
        if wild_source_count == 0 and weak_total < 10:
            rumor_heat = min(rumor_heat, 55)
            impact = min(impact, 55)
        contradiction = 0
        if official_total and weak_total:
            contradiction = min(100, official_total + weak_total * 2)
        market = self._rumor_market_reaction(name, profile, client)
        if market["price_drop_15m"] <= -2.5:
            impact = min(100, impact + 18)
            rumor_heat = min(100, rumor_heat + 8)
        if market["volume_ratio"] >= 1.8:
            impact = min(100, impact + 10)
        event_types: dict[str, int] = {}
        for item in top_items:
            event_type = str(item.get("event_type") or "其他传闻")
            event_types[event_type] = event_types.get(event_type, 0) + 1
        dominant_type = max(event_types, key=event_types.get)
        claim = self._rumor_claim(name, top_items)
        trigger_words = self._rumor_trigger_words(top_items)
        times = [
            parsed
            for item in top_items
            if (parsed := self._parse_news_time(str(item.get("publish_time") or item.get("fetch_time") or "")))
        ]
        first_seen = min(times).strftime("%Y-%m-%d %H:%M") if times else "-"
        latest_seen = max(times).strftime("%Y-%m-%d %H:%M") if times else "-"
        event_id = self._rumor_event_id(name, dominant_type, claim, trigger_words, top_items)
        return {
            "items": top_items,
            "rumor_heat": rumor_heat,
            "credibility": credibility,
            "impact_score": impact,
            "contradiction": contradiction,
            "market": market,
            "event_type": dominant_type,
            "source_count": len(unique_sources),
            "wild_source_count": wild_source_count,
            "claim": claim,
            "trigger_words": trigger_words,
            "first_seen": first_seen,
            "latest_seen": latest_seen,
            "event_id": event_id,
            "event_title": self._rumor_event_title(name, dominant_type, claim, top_items),
        }

    def _rumor_event_id(
        self,
        name: str,
        event_type: str,
        claim: str,
        trigger_words: str,
        items: list[dict[str, object]],
    ) -> str:
        text = f"{name}|{event_type}|{claim}|{trigger_words}|"
        for item in items[:3]:
            text += self._clean_text(str(item.get("title") or ""), 48)
        normalized = re.sub(r"\W+", "", text.lower())
        return hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _rumor_event_title(
        self,
        name: str,
        event_type: str,
        claim: str,
        items: list[dict[str, object]],
    ) -> str:
        wild_sources = []
        for item in items:
            if int(item.get("wild_score") or 0) > 0:
                source = str(item.get("source_site") or item.get("media_name") or item.get("_source") or "")
                if source and source not in wild_sources:
                    wild_sources.append(source)
        source_hint = "/".join(wild_sources[:2]) if wild_sources else "新闻/聚合源"
        return self._clean_text(f"{source_hint}出现{name}{event_type}：{claim}", 82)

    def _parse_event_time(self, value: str) -> datetime | None:
        if not value or value == "-":
            return None
        return self._parse_news_time(value if len(value) > 16 else f"{value}:00")

    def _rumor_claim(self, name: str, items: list[dict[str, object]]) -> str:
        if not items:
            return "未提取到明确主张"
        best = max(items, key=lambda item: int(item.get("single_score") or 0))
        text = str(best.get("title") or best.get("summary") or best.get("content") or "")
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(rf"^{re.escape(name)}[:：｜|\\-\\s]*", "", text)
        text = re.sub(r"(东方财富|雪球|股吧|财联社|证券时报|新浪财经|腾讯新闻)[:：｜|\\-\\s]*", "", text)
        parts = re.split(r"[。；;！？!?]| - | — | _ ", text)
        candidates = [part.strip(" ：:，,、") for part in parts if len(part.strip()) >= 6]
        claim = candidates[0] if candidates else text
        return self._clean_text(claim or "未提取到明确主张", 96)

    def _rumor_trigger_words(self, items: list[dict[str, object]]) -> str:
        counts: dict[str, int] = {}
        vocab = [*RUMOR_WEAK_SOURCE_TERMS.keys(), *RUMOR_EVENT_TERMS.keys(), *SEVERE_NEGATIVE_TERMS.keys()]
        for item in items:
            text = self._news_body_text(item)
            for word in vocab:
                if word in text:
                    counts[word] = counts.get(word, 0) + 1
        if not counts:
            return "无明显触发词"
        pairs = sorted(counts.items(), key=lambda row: (-row[1], row[0]))[:8]
        return "、".join(f"{word}({count})" for word, count in pairs)

    def _rumor_market_reaction(self, name: str, profile: dict[str, tuple[str, ...]], client: MarketClient) -> dict[str, float | str]:
        code = self._resolve_stock_code(name)
        if not code:
            aliases = profile.get("aliases", ())
            code = next((alias for alias in aliases if re.fullmatch(r"\d{6}\.(SH|SZ)", alias)), "")
        ft_code = self._to_ft_stock_code(code) if code else ""
        if not ft_code:
            return {"price_drop_15m": 0.0, "volume_ratio": 1.0, "minute": "-"}
        try:
            prices = client.get_stock_prices(ft_code)
        except Exception:
            return {"price_drop_15m": 0.0, "volume_ratio": 1.0, "minute": "-"}
        if len(prices) < 3:
            return {"price_drop_15m": 0.0, "volume_ratio": 1.0, "minute": prices[-1].minute if prices else "-"}
        latest = prices[-1]
        base = prices[-min(16, len(prices))]
        drop = (latest.price / base.price - 1) * 100 if base.price else 0.0
        recent_vol = sum(max(0.0, item.volume) for item in prices[-15:])
        prev_slice = prices[-30:-15]
        prev_vol = sum(max(0.0, item.volume) for item in prev_slice) if prev_slice else 0.0
        volume_ratio = recent_vol / prev_vol if prev_vol > 0 else 1.0
        return {"price_drop_15m": round(drop, 2), "volume_ratio": round(volume_ratio, 2), "minute": latest.minute}

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

    def _format_intraday_news_alert(self, name: str, event: dict[str, object], now: datetime) -> dict[str, str]:
        items = event.get("items") if isinstance(event.get("items"), list) else []
        market = event.get("market") if isinstance(event.get("market"), dict) else {}
        rumor_heat = int(event.get("rumor_heat") or 0)
        credibility = int(event.get("credibility") or 0)
        impact = int(event.get("impact_score") or 0)
        contradiction = int(event.get("contradiction") or 0)
        if rumor_heat >= 75 and credibility < 45 and impact >= 65:
            conclusion = "高传播、低验证、强杀伤小作文。优先控风险/暂停加仓，等待公告或公司回应。"
        elif credibility >= 65:
            conclusion = "存在较强正式来源，按硬信息处理，需优先核对公告/媒体原文。"
        elif impact >= 65:
            conclusion = "传闻杀伤力较强，真假未明也可能被短线资金交易。"
        else:
            conclusion = "暂属观察级异动，继续跟踪是否扩散和是否触发行情共振。"
        alert_reason = str(event.get("alert_reason") or "达到盘中监控阈值")
        lines = [
            f"盘中小作文雷达：{name}",
            f"北京时间 {now.strftime('%Y-%m-%d %H:%M')} 检测到疑似传闻/负面信息扩散。",
            f"事件标题：{event.get('event_title', event.get('claim', '未提取到明确主张'))}",
            f"推送原因：{alert_reason}",
            f"事件类型：{event.get('event_type', '其他传闻')}；来源数：{event.get('source_count', '-')}；野源数：{event.get('wild_source_count', 0)}；时间：{event.get('first_seen', '-')} 至 {event.get('latest_seen', '-')}",
            f"核心主张：{event.get('claim', '未提取到明确主张')}",
            f"触发词：{event.get('trigger_words', '无明显触发词')}",
            f"传闻热度：{rumor_heat}/100｜可信度：{credibility}/100｜杀伤力：{impact}/100｜官方反证/澄清强度：{contradiction}/100",
            f"行情共振：{market.get('minute', '-')} 近15分钟 {market.get('price_drop_15m', 0)}%，量能比 {market.get('volume_ratio', 1)}",
            f"覆盖提示：{'已命中野源原文' if int(event.get('wild_source_count') or 0) else '未命中微博/X/股吧/雪球原始野源，可能只是正规新闻或聚合噪音'}",
            f"结论：{conclusion}",
            "这不是交易指令，请优先核对原文/公告，并结合盘中量价处理风险。",
            "",
        ]
        for item in sorted(items, key=lambda row: int(row.get("single_score") or row.get("_negative_severity") or 0), reverse=True)[:3]:
            severity = int(item.get("single_score") or item.get("_negative_severity") or 0)
            relevance = item.get("_relevance_score")
            rel = f"，相关度 {float(relevance):.1f}" if isinstance(relevance, (int, float)) else ""
            title = self._clean_text(str(item.get("title") or "无标题"), 88)
            source = item.get("source_site") or item.get("media_name") or "未知来源"
            publish = str(item.get("publish_time") or item.get("fetch_time") or "-").replace("T", " ")[:16]
            url = item.get("article_url") or ""
            source_type = "野源" if int(item.get("wild_score") or 0) else "正式/聚合"
            lines.append(f"- 单条强度 {severity}{rel}｜{source_type}｜{publish}｜{source}")
            lines.append(f"  {title}")
            if url:
                lines.append(f"  {url}")
        return {
            "title": f"小作文雷达：{name}",
            "content": "\n".join(lines),
            "summary": f"{name} 热度{rumor_heat} 杀伤{impact} 可信{credibility}",
        }

    def _run_market_weak_worker(self, model_path: Path, token: str, weixin_mode: str) -> None:
        try:
            models = load_models(model_path)
            client = MarketClient(ttl_seconds=60)
        except Exception as exc:
            self.queue.put(("log", f"大盘/板块走弱提醒启动失败：{exc}"))
            return

        notifier = self._build_alert_notifier(token, weixin_mode)
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
            level = self._movement_level(day_return, momentum, MARKET_WEAK_INDEX_THRESHOLD, MARKET_WEAK_INDEX_MOMENTUM)
            should_alert, reason, alert_level = self._market_alert_decision(
                now,
                "INDEX",
                name,
                level,
                day_return,
                momentum,
                MARKET_WEAK_INDEX_THRESHOLD,
                MARKET_WEAK_INDEX_MOMENTUM,
            )
            if not should_alert:
                continue
            index_rows.append((name, day_return, momentum, minute, alert_level, reason))
        if index_rows:
            alerts.append(self._format_market_index_alert(index_rows, now))

        for basket in self._watched_sector_baskets(models):
            basket_stats = self._security_basket_weak_stats(basket["peers"], client)
            if not basket_stats:
                continue
            basket_return, momentum, minute, valid_count = basket_stats
            level = self._movement_level(
                basket_return,
                momentum,
                MARKET_WEAK_BASKET_THRESHOLD,
                MARKET_WEAK_BASKET_MOMENTUM,
            )
            basket["technical"] = self._watch_technical_snapshot(str(basket.get("code") or ""), client)
            should_alert, reason, alert_level = self._market_alert_decision(
                now,
                "BASKET",
                str(basket["name"]),
                level,
                basket_return,
                momentum,
                MARKET_WEAK_BASKET_THRESHOLD,
                MARKET_WEAK_BASKET_MOMENTUM,
            )
            if not should_alert:
                continue
            alerts.append(self._format_basket_weak_alert(basket, basket_return, momentum, minute, valid_count, alert_level, reason, now))
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
        return self._security_basket_weak_stats(model.basket, client)

    def _security_basket_weak_stats(self, basket: tuple[Security, ...], client: MarketClient) -> tuple[float, float, str, int] | None:
        returns = []
        momentums = []
        minutes = []
        for peer in basket:
            stats = self._intraday_weak_stats(client.get_stock_prices(peer.code))
            if not stats:
                continue
            day_return, momentum, minute = stats
            returns.append(day_return)
            momentums.append(momentum)
            minutes.append(minute)
        if len(returns) < max(2, min(3, len(basket))):
            return None
        return sum(returns) / len(returns), sum(momentums) / len(momentums), max(minutes), len(returns)

    def _watched_sector_baskets(self, models: list[TModel]) -> list[dict[str, object]]:
        model_by_name = {model.name: model for model in models}
        model_by_code = {self._normalize_ft_code(model.code): model for model in models}
        baskets: list[dict[str, object]] = []
        seen_names: set[str] = set()
        for name, profile in self._watched_news_profiles().items():
            code = self._watch_profile_code(name, profile)
            sector_info = self._fetch_eastmoney_sector_info(code)
            concepts = self._concepts_for_watch(name, profile, sector_info)
            peers = self._concept_peers(concepts, code)
            model = model_by_name.get(name) or model_by_code.get(self._normalize_ft_code(code))
            if model:
                peers = self._merge_securities(peers, model.basket, exclude_code=code)
            if len(peers) < 2:
                continue
            label = f"{name}｜{'+'.join(concepts[:3]) if concepts else '自选概念'}"
            if label in seen_names:
                continue
            seen_names.add(label)
            board_rows = self._matched_concept_board_rows(concepts, sector_info)
            baskets.append(
                {
                    "name": label,
                    "stock": name,
                    "code": code,
                    "concepts": concepts,
                    "remote_concepts": tuple(sector_info.get("concepts", ())) if sector_info else tuple(),
                    "industry": str(sector_info.get("industry") or "") if sector_info else "",
                    "sector_source": str(sector_info.get("source") or "fallback") if sector_info else "fallback",
                    "board_rows": board_rows,
                    "peers": peers[:12],
                }
            )
        return baskets

    def _concepts_for_watch(
        self,
        name: str,
        profile: dict[str, tuple[str, ...]],
        sector_info: dict[str, object] | None = None,
    ) -> tuple[str, ...]:
        concepts = list(WATCH_CONCEPTS.get(name, ()))
        remote_text = ""
        if sector_info:
            remote_concepts = [str(item) for item in sector_info.get("concepts", ()) if item]
            remote_text = " ".join([str(sector_info.get("industry") or ""), *remote_concepts])
            concepts.extend(self._map_remote_concepts(remote_text))
        text = " ".join([name, *profile.get("theme", ()), *profile.get("aliases", ())])
        for concept, words in CONCEPT_KEYWORDS.items():
            if any(word and (word in text or word in remote_text) for word in words):
                concepts.append(concept)
        return tuple(dict.fromkeys(concepts))

    def _map_remote_concepts(self, text: str) -> tuple[str, ...]:
        mapped = []
        for concept, words in CONCEPT_KEYWORDS.items():
            if any(word and word in text for word in words):
                mapped.append(concept)
        extra_rules = {
            "算力租赁/算力基础设施": ("算力租赁", "智算中心", "数据中心", "东数西算"),
            "CPO/光模块": ("共封装光学", "光模块", "CPO", "硅光"),
            "PCB/AI服务器": ("PCB", "印制电路板", "服务器", "英伟达概念"),
            "AI算力": ("人工智能", "AI", "算力", "英伟达"),
        }
        for concept, words in extra_rules.items():
            if any(word in text for word in words):
                mapped.append(concept)
        return tuple(dict.fromkeys(mapped))

    def _concept_peers(self, concepts: tuple[str, ...], exclude_code: str = "") -> tuple[Security, ...]:
        peers: list[Security] = []
        seen: set[str] = set()
        exclude = self._normalize_ft_code(exclude_code)
        for concept in concepts:
            for name, code in CONCEPT_BASKETS.get(concept, ()):
                normalized = self._normalize_ft_code(code)
                if not normalized or normalized == exclude or normalized in seen:
                    continue
                seen.add(normalized)
                peers.append(Security(name, normalized))
        return tuple(peers)

    def _merge_securities(
        self,
        primary: tuple[Security, ...],
        extra: tuple[Security, ...],
        exclude_code: str = "",
    ) -> tuple[Security, ...]:
        rows: list[Security] = []
        seen: set[str] = set()
        exclude = self._normalize_ft_code(exclude_code)
        for peer in (*primary, *extra):
            normalized = self._normalize_ft_code(peer.code)
            if not normalized or normalized == exclude or normalized in seen:
                continue
            seen.add(normalized)
            rows.append(Security(peer.name, normalized))
        return tuple(rows)

    def _watch_profile_code(self, name: str, profile: dict[str, tuple[str, ...]]) -> str:
        code = self._resolve_stock_code(name)
        if code:
            return self._to_ft_stock_code(code)
        for alias in profile.get("aliases", ()):
            if re.fullmatch(r"\d{6}(\.(SH|SZ|XSHG|XSHE))?", alias.upper()):
                return self._normalize_ft_code(alias)
        return ""

    def _normalize_ft_code(self, code: str) -> str:
        if not code:
            return ""
        upper = code.upper()
        if upper.endswith(".XSHG") or upper.endswith(".XSHE"):
            return upper
        return self._to_ft_stock_code(upper)

    def _eastmoney_secid(self, code: str) -> tuple[str, str]:
        ft_code = self._normalize_ft_code(code)
        raw = ft_code.split(".")[0] if ft_code else code.split(".")[0]
        market = "1" if ft_code.endswith(".XSHG") or raw.startswith("6") else "0"
        return f"{market}.{raw.zfill(6)}", raw.zfill(6)

    def _fetch_eastmoney_sector_info(self, code: str) -> dict[str, object]:
        if not code:
            return {}
        ft_code = self._normalize_ft_code(code)
        now = time.time()
        cached = self.sector_info_cache.get(ft_code)
        if cached and now - cached[0] < 1800:
            return cached[1]

        secid, code6 = self._eastmoney_secid(ft_code)
        result: dict[str, object] = {"code": code6, "name": "", "industry": "", "concepts": [], "source": "eastmoney", "error": ""}
        try:
            params = urllib.parse.urlencode({"secid": secid, "fields": "f57,f58,f127", "ut": "fa5fd1943c7b386f172d6893dbfba10b"})
            req = urllib.request.Request(
                f"{EASTMONEY_STOCK_INFO_URL}?{params}",
                headers={"User-Agent": "Mozilla/5.0 AShareTSignalMonitor/1.0", "Accept": "application/json"},
            )
            with safe_urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            row = data.get("data") if isinstance(data, dict) else {}
            if isinstance(row, dict):
                result["name"] = row.get("f58") or ""
                result["industry"] = row.get("f127") or ""
        except Exception as exc:
            result["error"] = f"info:{exc}"

        try:
            params = urllib.parse.urlencode({"secid": secid, "fields": "f12,f14", "spt": "3", "ut": "fa5fd1943c7b386f172d6893dbfba10b"})
            req = urllib.request.Request(
                f"{EASTMONEY_STOCK_SECTORS_URL}?{params}",
                headers={"User-Agent": "Mozilla/5.0 AShareTSignalMonitor/1.0", "Accept": "application/json"},
            )
            with safe_urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            diff = data.get("data", {}).get("diff", []) if isinstance(data, dict) else []
            concepts = []
            for item in diff if isinstance(diff, list) else []:
                concept = str(item.get("f14") or "").strip()
                if concept and concept not in concepts:
                    concepts.append(concept)
            result["concepts"] = concepts
        except Exception as exc:
            result["error"] = f"{result.get('error')};concepts:{exc}".strip(";")

        self.sector_info_cache[ft_code] = (now, result)
        return result

    def _fetch_eastmoney_concept_boards(self) -> list[dict[str, object]]:
        now = time.time()
        if self.concept_board_cache and now - self.concept_board_cache[0] < 180:
            return self.concept_board_cache[1]
        params = urllib.parse.urlencode(
            {
                "pn": 1,
                "pz": 300,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fs": "m:90+t:3+f:!50",
                "fields": "f12,f14,f2,f3,f4,f8,f20,f21,f62",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            }
        )
        rows: list[dict[str, object]] = []
        try:
            req = urllib.request.Request(
                f"{EASTMONEY_CONCEPT_BOARDS_URL}?{params}",
                headers={"User-Agent": "Mozilla/5.0 AShareTSignalMonitor/1.0", "Accept": "application/json"},
            )
            with safe_urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            diff = data.get("data", {}).get("diff", []) if isinstance(data, dict) else []
            for item in diff if isinstance(diff, list) else []:
                rows.append(
                    {
                        "code": item.get("f12"),
                        "name": item.get("f14"),
                        "price": self._safe_float(item.get("f2")),
                        "change_pct": self._safe_float(item.get("f3")),
                        "turnover_rate": self._safe_float(item.get("f8")),
                        "market_cap": self._safe_float(item.get("f20")),
                        "free_cap": self._safe_float(item.get("f21")),
                        "main_net": self._safe_float(item.get("f62")),
                    }
                )
        except Exception:
            rows = []
        self.concept_board_cache = (now, rows)
        return rows

    def _matched_concept_board_rows(
        self,
        concepts: tuple[str, ...],
        sector_info: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], ...]:
        board_rows = self._fetch_eastmoney_concept_boards()
        if not board_rows:
            return tuple()
        remote_terms = [str(item) for item in (sector_info or {}).get("concepts", ()) if item]
        query_terms = list(concepts) + remote_terms
        matched: list[dict[str, object]] = []
        for row in board_rows:
            board_name = str(row.get("name") or "")
            if not board_name:
                continue
            for term in query_terms:
                words = CONCEPT_KEYWORDS.get(term, (term,))
                if term in board_name or board_name in term or any(word and word in board_name for word in words):
                    matched.append(row)
                    break
        matched.sort(key=lambda row: abs(float(row.get("change_pct") or 0)), reverse=True)
        return tuple(matched[:5])

    def _safe_float(self, value: object) -> float | None:
        try:
            if value in (None, "-", ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _watch_technical_snapshot(self, code: str, client: MarketClient) -> dict[str, object]:
        ft_code = self._normalize_ft_code(code)
        if not ft_code:
            return {"grade": "低置信", "summary": "未识别代码", "invalidation": "缺少数据"}
        try:
            prices = client.get_stock_prices(ft_code)
            trend = client.get_daily_trend(ft_code)
        except Exception as exc:
            return {"grade": "低置信", "summary": f"技术数据缺失：{exc}", "invalidation": "缺少数据"}
        if not prices:
            return {"grade": "低置信", "summary": "分时数据不足", "invalidation": "缺少分时"}
        latest = prices[-1]
        price = float(getattr(latest, "price", 0.0) or 0.0)
        avg_price = float(getattr(latest, "avg_price", 0.0) or price)
        ma5 = trend.get("ma5") if isinstance(trend, dict) else None
        ma10 = trend.get("ma10") if isinstance(trend, dict) else None
        ma20 = trend.get("ma20") if isinstance(trend, dict) else None
        ma_values = [value for value in (ma5, ma10, ma20) if isinstance(value, (int, float)) and value > 0]
        above_count = sum(1 for value in ma_values if price >= float(value))
        below_avg = avg_price > 0 and price < avg_price
        above_avg = avg_price > 0 and price >= avg_price
        if len(ma_values) >= 3 and above_count == 3 and above_avg:
            grade = "A"
            summary = "价格站上 MA5/10/20 且不弱于盘中均价，趋势承接较好"
            invalidation = "跌回盘中均价且跌破 MA5，做T信号降级"
        elif ma_values and above_count >= max(1, len(ma_values) - 1):
            grade = "B"
            summary = "均线结构尚可，但需要量价继续确认"
            invalidation = "跌破 MA10 或盘中均价，观察优先"
        elif below_avg or (ma_values and above_count <= 1):
            grade = "C"
            summary = "价格偏弱或均线承接不足，做T需要更高价差补偿"
            invalidation = "继续跌破 MA20/日内低点，暂停正T"
        else:
            grade = "D"
            summary = "关键均线数据不足或结构不清晰"
            invalidation = "等待重新站回均价和短均线"
        return {
            "grade": grade,
            "summary": summary,
            "invalidation": invalidation,
            "price": price,
            "avg_price": avg_price,
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
        }

    def _basket_action_plan(
        self,
        level: str,
        reason: str,
        basket_return: float,
        momentum: float,
        technical: dict[str, object],
        board_rows: tuple[dict[str, object], ...],
    ) -> dict[str, str]:
        grade = str(technical.get("grade") or "低置信")
        board_text = self._concept_board_digest(board_rows)
        if "严重走弱" in level or (grade in ("C", "D") and "走弱" in level):
            action = "强风险"
            risk = "板块与个股结构共振偏弱，做T入场应降频，优先保护仓位。"
        elif "警戒走弱" in level:
            action = "谨慎"
            risk = "板块短线转弱，只有个股承接明显强于篮子时才考虑。"
        elif "走强" in level and grade in ("A", "B"):
            action = "可观察"
            risk = "板块转强但仍需防止冲高回落，避免把情绪脉冲当趋势。"
        elif level == "修复":
            action = "修复观察"
            risk = "弱势缓和，不等于趋势反转，需看下一段量价能否延续。"
        else:
            action = "观察"
            risk = "数据不足或信号中性，等待更明确方向。"
        trigger = f"{level}：{reason}；篮子日内 {self._format_pct(basket_return)}，近5分钟 {self._format_pct(momentum)}"
        if board_text:
            trigger += f"；概念热度：{board_text}"
        invalidation = str(technical.get("invalidation") or "若板块动量反向并放量，当前判断失效")
        return {"action": action, "trigger": trigger, "invalidation": invalidation, "risk": risk}

    def _concept_board_digest(self, rows: tuple[dict[str, object], ...]) -> str:
        parts = []
        for row in rows[:3]:
            name = str(row.get("name") or "")
            pct = row.get("change_pct")
            turnover = row.get("turnover_rate")
            main_net = row.get("main_net")
            if not name:
                continue
            pct_text = f"{float(pct):+.2f}%" if isinstance(pct, (int, float)) else "-"
            turn_text = f"，换手{float(turnover):.2f}%" if isinstance(turnover, (int, float)) else ""
            fund_text = f"，主力{float(main_net) / 100000000:+.2f}亿" if isinstance(main_net, (int, float)) else ""
            parts.append(f"{name}{pct_text}{turn_text}{fund_text}")
        return "；".join(parts)

    def _weak_level(self, day_return: float, momentum: float, return_threshold: float, momentum_threshold: float) -> str:
        return self._movement_level(day_return, momentum, return_threshold, momentum_threshold)

    def _movement_level(self, day_return: float, momentum: float, return_threshold: float, momentum_threshold: float) -> str:
        if day_return <= return_threshold * 1.7 or momentum <= momentum_threshold * 2.0:
            return "严重走弱"
        if day_return <= return_threshold or momentum <= momentum_threshold:
            return "警戒走弱"
        strong_return = abs(return_threshold)
        strong_momentum = abs(momentum_threshold)
        if day_return >= strong_return * 1.7 or momentum >= strong_momentum * 2.0:
            return "严重走强"
        if day_return >= strong_return or momentum >= strong_momentum:
            return "警戒走强"
        return ""

    def _market_alert_decision(
        self,
        now: datetime,
        category: str,
        name: str,
        level: str,
        day_return: float,
        momentum: float,
        return_threshold: float,
        momentum_threshold: float,
    ) -> tuple[bool, str, str]:
        key = f"{now.strftime('%Y-%m-%d')}:{category}:{name}"
        state = self.market_alert_states.get(key, {})
        prev_level = str(state.get("level") or "")
        active_level = str(state.get("active_level") or prev_level)
        prev_return = state.get("day_return")
        prev_momentum = state.get("momentum")
        last_alert_return = state.get("last_alert_return")
        last_alert_momentum = state.get("last_alert_momentum")
        last_alert_level = str(state.get("last_alert_level") or "")

        reason = ""
        alert_level = level
        if level:
            if not last_alert_level:
                reason = f"首次进入{level}区间"
            elif self._weak_level_rank(level) > self._weak_level_rank(last_alert_level):
                reason = f"状态升级：{last_alert_level} -> {level}"
            elif isinstance(last_alert_return, (int, float)) and abs(day_return - float(last_alert_return)) >= MARKET_ALERT_RETURN_STEP:
                if "走强" in level:
                    direction = "增强" if day_return > float(last_alert_return) else "回落"
                else:
                    direction = "扩大" if day_return < float(last_alert_return) else "收敛"
                reason = f"日内幅度明显{direction}"
            elif isinstance(last_alert_momentum, (int, float)) and abs(momentum - float(last_alert_momentum)) >= MARKET_ALERT_MOMENTUM_STEP:
                direction = "增强" if momentum > float(last_alert_momentum) else "转弱"
                reason = f"近5分钟动量明显{direction}"
        elif active_level and isinstance(prev_momentum, (int, float)) and momentum >= MARKET_ALERT_TURN_MOMENTUM:
            alert_level = "修复"
            reason = f"拐点修复：脱离{active_level}，近5分钟转正"
        elif active_level and isinstance(prev_return, (int, float)) and day_return - float(prev_return) >= MARKET_ALERT_RETURN_STEP:
            alert_level = "修复"
            reason = f"拐点修复：日内跌幅明显收敛"

        state.update({"level": level, "day_return": day_return, "momentum": momentum, "minute": now.strftime("%H:%M")})
        if level:
            state["active_level"] = level
        elif reason and alert_level == "修复":
            state["active_level"] = ""
        if reason:
            state.update(
                {
                    "last_alert_level": alert_level,
                    "last_alert_return": day_return,
                    "last_alert_momentum": momentum,
                    "last_alert_at": now.isoformat(),
                }
            )
        self.market_alert_states[key] = state
        return bool(reason), reason, alert_level

    def _weak_level_rank(self, level: str) -> int:
        return {"修复": 0, "警戒走强": 1, "警戒走弱": 1, "严重走强": 2, "严重走弱": 2}.get(level, 0)

    def _market_alert_key(self, now: datetime, category: str, name: str, level: str) -> str:
        bucket_minute = (now.minute // 30) * 30
        return f"{now.strftime('%Y-%m-%d')}:{now.hour:02d}:{bucket_minute:02d}:{category}:{name}:{level}"

    def _format_pct(self, value: float) -> str:
        return f"{value * 100:+.2f}%"

    def _format_market_index_alert(self, rows: list[tuple[str, float, float, str, str, str]], now: datetime) -> dict[str, str]:
        worst_level = "严重走弱" if any(row[4] == "严重走弱" for row in rows) else (
            "严重走强" if any(row[4] == "严重走强" for row in rows) else (
                "警戒走弱" if any(row[4] == "警戒走弱" for row in rows) else ("警戒走强" if any(row[4] == "警戒走强" for row in rows) else "修复")
            )
        )
        title_word = "修复" if worst_level == "修复" else ("走强" if "走强" in worst_level else "走弱")
        lines = [
            f"大盘{title_word}提醒（{worst_level}）",
            f"北京时间 {now.strftime('%Y-%m-%d %H:%M')} 检测到指数状态变化。",
            "这不是交易指令，请结合持仓、做T计划和盘中量价确认风险。",
            "",
        ]
        for name, day_return, momentum, minute, level, reason in rows:
            lines.append(
                f"- {name}｜{level}｜{reason}｜{minute}｜日内 {self._format_pct(day_return)}｜近约5分钟 {self._format_pct(momentum)}"
            )
        return {
            "title": f"大盘{title_word}提醒：{worst_level}",
            "content": "\n".join(lines),
            "summary": f"{len(rows)} 个指数状态变化",
        }

    def _format_basket_weak_alert(
        self,
        basket: dict[str, object],
        basket_return: float,
        momentum: float,
        minute: str,
        valid_count: int,
        level: str,
        reason: str,
        now: datetime,
    ) -> dict[str, str]:
        peers = basket.get("peers") if isinstance(basket.get("peers"), tuple) else tuple()
        concepts = basket.get("concepts") if isinstance(basket.get("concepts"), tuple) else tuple()
        remote_concepts = basket.get("remote_concepts") if isinstance(basket.get("remote_concepts"), tuple) else tuple()
        board_rows = basket.get("board_rows") if isinstance(basket.get("board_rows"), tuple) else tuple()
        technical = basket.get("technical") if isinstance(basket.get("technical"), dict) else {}
        name = str(basket.get("name") or "自选板块")
        stock = str(basket.get("stock") or "")
        industry = str(basket.get("industry") or "")
        sector_source = str(basket.get("sector_source") or "fallback")
        peer_names = "、".join(peer.name for peer in peers[:6])
        concept_text = "、".join(str(item) for item in concepts) or "自选概念"
        remote_text = "、".join(str(item) for item in remote_concepts[:8]) or "无/接口为空"
        title_word = "修复" if level == "修复" else ("走强" if "走强" in level else "走弱")
        action_plan = self._basket_action_plan(level, reason, basket_return, momentum, technical, board_rows)
        board_digest = self._concept_board_digest(board_rows)
        lines = [
            f"自选概念板块{title_word}：{stock or name}（{level}）",
            f"北京时间 {now.strftime('%Y-%m-%d %H:%M')} 检测到自选股关联板块状态变化。",
            f"action_grade：{action_plan['action']}",
            f"trigger：{action_plan['trigger']}",
            f"invalidation：{action_plan['invalidation']}",
            f"risk：{action_plan['risk']}",
            "",
            f"匹配概念：{concept_text}",
            f"东财行业：{industry or '未知'}；东财概念：{remote_text}；来源：{sector_source}",
            f"概念板块热度：{board_digest or '未匹配到东财概念板块行情'}",
            f"技术结构：{technical.get('grade', '低置信')}｜{technical.get('summary', '技术数据不足')}",
            f"样本：{valid_count} 只成份/相似股；{peer_names}",
            "",
            f"- {minute}｜篮子日内均值 {self._format_pct(basket_return)}｜近约5分钟 {self._format_pct(momentum)}",
            "这不是交易指令；若你正在做T或准备挂单，建议先确认大盘、板块和个股承接。",
        ]
        return {
            "title": f"自选板块{title_word}：{stock or name}",
            "content": "\n".join(lines),
            "summary": f"{stock or name} 自选概念篮子{level}{title_word}",
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
            elif kind == "text_window":
                title, content = payload if isinstance(payload, tuple) else ("信息", str(payload))
                self._open_text_window(str(title), str(content))
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
            elif kind == "chart_result":
                if isinstance(payload, dict):
                    self._render_chart_payload(payload)
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
    try:
        root = tk.Tk()
        MonitorApp(root)
        root.mainloop()
    except Exception:
        log_path = startup_log_path()
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        try:
            error_root = tk.Tk()
            error_root.withdraw()
            messagebox.showerror("启动失败", f"程序启动失败，错误日志已写入：\n{log_path}")
            error_root.destroy()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
