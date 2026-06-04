from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui_monitor import MonitorApp as LegacyMonitorApp
from gui_monitor import create_windows_desktop_shortcut
from model_engine import BEIJING_TZ, ModelSignalEngine, TModel, load_models
from notifier import MultiNotifier, PushPlusNotifier, WeixinPushNotifier
from weixin_push import default_credentials_path as weixin_credentials_path
from weixin_push import load_targets as load_weixin_push_targets
from weixin_push import login_with_qr, refresh_context_tokens

try:
    from build_info import BUILD_SHA
except Exception:
    BUILD_SHA = "dev"


DEFAULT_INTERVAL_SECONDS = 30
PUSHPLUS_TOKEN_URL = "https://www.pushplus.plus/push1.html"
RELEASE_URL = "https://github.com/Roypic/Astock_t/releases"
WINDOWS_EXE_URL = "https://github.com/Roypic/Astock_t/releases/download/windows-latest/AShareTSignalMonitor.exe"
WINDOWS_V2_EXE_URL = "https://github.com/Roypic/Astock_t/releases/download/windows-v2-latest/AShareTSignalMonitor.exe"
MACOS_ZIP_URL = "https://github.com/Roypic/Astock_t/releases/download/macos-latest/AShareTSignalMonitor-macOS.zip"
GITHUB_DOWNLOAD_MIRRORS = (
    "https://gh.llkk.cc/",
    "https://ghproxy.net/",
    "https://hub.gitmirror.com/",
    "https://gh-proxy.com/",
)


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


def target_ladder(item: dict[str, object]) -> str:
    target1 = item.get("target1_price")
    target2 = item.get("target2_price")
    target3 = item.get("target3_price")
    if target1 is not None and target2 is not None and target3 is not None:
        return f"{target1}/{target2}/{target3}"
    return str(item.get("exit_price", "-"))


class ModernButton(QPushButton):
    def __init__(self, text: str, kind: str = "ghost") -> None:
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(38)
        self.setProperty("kind", kind)


class GlassCard(QFrame):
    def __init__(self, title: str = "", subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("glassCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(12)
        if title:
            row = QHBoxLayout()
            accent = QFrame()
            accent.setObjectName("accent")
            accent.setFixedSize(4, 30)
            copy = QVBoxLayout()
            copy.setSpacing(1)
            heading = QLabel(title)
            heading.setObjectName("sectionTitle")
            copy.addWidget(heading)
            if subtitle:
                sub = QLabel(subtitle)
                sub.setObjectName("sectionSubtitle")
                copy.addWidget(sub)
            row.addWidget(accent)
            row.addLayout(copy, 1)
            self.layout.addLayout(row)


class PriceChartWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.payload: dict[str, object] | None = None
        self.error = ""
        self.setMinimumHeight(430)
        self.setObjectName("priceChart")

    def set_payload(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.error = ""
        self.update()

    def set_error(self, message: str) -> None:
        self.payload = None
        self.error = message
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor("#07192D"))
        painter.setPen(QPen(QColor("#1B4B7D"), 1))
        painter.drawRoundedRect(rect.adjusted(1, 1, -2, -2), 18, 18)
        painter.setPen(QPen(QColor("#2E75B8"), 1))
        painter.drawLine(18, 16, rect.width() - 18, 16)

        if self.error:
            painter.setPen(QColor("#DDEEFF"))
            painter.drawText(rect, Qt.AlignCenter, self.error)
            return
        if not self.payload:
            painter.setPen(QColor("#92A9C4"))
            painter.drawText(rect, Qt.AlignCenter, "点击查看走势后显示图表")
            return

        rows = self.payload.get("rows")
        levels = self.payload.get("levels")
        if not isinstance(rows, list) or not isinstance(levels, dict) or len(rows) < 2:
            painter.setPen(QColor("#92A9C4"))
            painter.drawText(rect, Qt.AlignCenter, "走势数据不足")
            return

        width = max(320, rect.width())
        height = max(260, rect.height())
        pad_left, pad_right, pad_top, pad_bottom = 64, 118, 46, 36
        volume_top = max(pad_top + 170, int(height * 0.74))
        price_bottom = volume_top - 18
        volume_bottom = height - pad_bottom
        lows = [float(row.get("low") or row["close"]) for row in rows if isinstance(row, dict)]
        highs = [float(row.get("high") or row["close"]) for row in rows if isinstance(row, dict)]
        if not lows or not highs:
            return
        supports = [float(value) for value in levels.get("supports", []) if isinstance(value, (int, float))]
        resistances = [float(value) for value in levels.get("resistances", []) if isinstance(value, (int, float))]
        chip_peak = float(levels.get("chip_peak") or lows[-1])
        level_prices = supports + resistances + [chip_peak]
        min_price = min([min(lows), *level_prices])
        max_price = max([max(highs), *level_prices])
        spread = max_price - min_price or 1.0
        min_price -= spread * 0.08
        max_price += spread * 0.08

        def x_at(index: int) -> float:
            return pad_left + index * (width - pad_left - pad_right) / max(1, len(rows) - 1)

        def y_at(price: float) -> float:
            return pad_top + (max_price - price) * (price_bottom - pad_top) / (max_price - min_price)

        painter.setFont(QFont("Microsoft YaHei UI", 8))
        for i in range(5):
            y = pad_top + i * (price_bottom - pad_top) / 4
            price = max_price - i * (max_price - min_price) / 4
            painter.setPen(QPen(QColor("#173755"), 1))
            painter.drawLine(pad_left, int(y), width - pad_right, int(y))
            painter.setPen(QColor("#92A9C4"))
            painter.drawText(12, int(y + 4), f"{price:.2f}")
        for i in range(5):
            x = pad_left + i * (width - pad_left - pad_right) / 4
            painter.setPen(QPen(QColor("#102842"), 1))
            painter.drawLine(int(x), pad_top, int(x), volume_bottom)
        painter.setPen(QPen(QColor("#1B4B7D"), 1))
        painter.drawLine(pad_left, volume_top, width - pad_right, volume_top)

        volumes = [max(0.0, float(row.get("volume") or 0)) for row in rows if isinstance(row, dict)]
        max_volume = max(volumes) if volumes else 0.0
        if max_volume > 0:
            bar_span = max(2.0, (width - pad_left - pad_right) / max(1, len(rows)))
            bar_width = max(1.0, min(6.0, bar_span * 0.62))
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                volume = max(0.0, float(row.get("volume") or 0))
                bar_height = (volume / max_volume) * max(8, volume_bottom - volume_top - 8)
                x = x_at(index)
                close = float(row.get("close") or 0)
                prev_close = float(rows[index - 1].get("close") or close) if index > 0 and isinstance(rows[index - 1], dict) else close
                color = QColor(255, 107, 114, 130) if close >= prev_close else QColor(72, 213, 151, 130)
                painter.fillRect(int(x - bar_width / 2), int(volume_bottom - bar_height), int(bar_width), int(bar_height), color)
            painter.setFont(QFont("Microsoft YaHei UI", 8))
            painter.setPen(QColor("#92A9C4"))
            painter.drawText(pad_left, volume_top - 5, "成交量")

        period = str(self.payload.get("period") or "")
        if period != "分时":
            painter.setPen(QPen(QColor("#607D9C"), 1))
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                x = x_at(index)
                painter.drawLine(QPointF(x, y_at(float(row.get("low") or row["close"]))), QPointF(x, y_at(float(row.get("high") or row["close"]))))

        path = QPainterPath()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            point = QPointF(x_at(index), y_at(float(row["close"])))
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QPen(QColor("#164D86"), 7))
        painter.drawPath(path)
        painter.setPen(QPen(QColor("#6DB7FF"), 3))
        painter.drawPath(path)
        painter.setPen(QPen(QColor("#D8ECFF"), 1))
        painter.drawPath(path)

        dash = QPen(QColor("#D71920"), 1)
        dash.setStyle(Qt.DashLine)
        green_dash = QPen(QColor("#24C07A"), 1)
        green_dash.setStyle(Qt.DashLine)
        chip_dash = QPen(QColor("#FF8C6B"), 1)
        chip_dash.setStyle(Qt.DashLine)
        line_specs: list[tuple[str, float, QPen, QColor]] = []
        for index, price in enumerate(resistances[:3], start=1):
            line_specs.append((f"压{index}", price, dash, QColor("#FF6B72")))
        line_specs.append(("筹码峰", chip_peak, chip_dash, QColor("#FFB199")))
        for index, price in enumerate(supports[:3], start=1):
            line_specs.append((f"支{index}", price, green_dash, QColor("#48D597")))
        painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Bold))
        label_positions: list[float] = []
        min_label_gap = 15
        for label, price, pen, color in line_specs:
            y = y_at(price)
            painter.setPen(pen)
            painter.drawLine(pad_left, int(y), width - pad_right, int(y))
            label_y = y
            for used in label_positions:
                if abs(label_y - used) < min_label_gap:
                    label_y = used + min_label_gap
            label_y = max(pad_top + 12, min(price_bottom - 8, label_y))
            label_positions.append(label_y)
            painter.setPen(color)
            painter.drawText(width - pad_right + 7, int(label_y + 3), f"{label} {price:.2f}")

        name = str(self.payload.get("name") or "")
        trend = str(levels.get("trend") or "")
        current = float(levels.get("current") or 0)
        prev = float(rows[-2].get("close") or current) if len(rows) > 1 and isinstance(rows[-2], dict) else current
        current_color = QColor("#FF6B72") if current >= prev else QColor("#48D597")
        painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        painter.setPen(QColor("#EAF3FF"))
        painter.drawText(pad_left, 28, f"{name} {period} 走势｜{trend}")
        painter.setPen(current_color)
        painter.drawText(width - pad_right - 128, 28, f"现价 {current:.2f}")
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.setPen(QColor("#92A9C4"))
        first_label = str(rows[0].get("label") or "") if isinstance(rows[0], dict) else ""
        last_label = str(rows[-1].get("label") or "") if isinstance(rows[-1], dict) else ""
        painter.drawText(pad_left, height - 14, first_label)
        painter.drawText(width - pad_right - 80, height - 14, last_label)


class MonitorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("A股做T行情终端 v2")
        self.resize(1220, 820)
        self.setMinimumSize(1040, 720)

        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.weixin_session_stop = threading.Event()
        self.weixin_session_worker: threading.Thread | None = None
        self.seen_intraday_news: set[str] = set()
        self.seen_rumor_events: dict[str, dict[str, object]] = {}
        self.seen_market_alerts: set[str] = set()
        self.market_alert_states: dict[str, dict[str, object]] = {}
        self.sector_info_cache: dict[str, tuple[float, dict[str, object]]] = {}
        self.concept_board_cache: tuple[float, list[dict[str, object]]] | None = None
        self.news_monitor_started_at = None
        self.daily_report_sent_day = ""
        self.child_windows: list[QWidget] = []
        self.models_dir = ensure_default_models()

        self._build_ui()
        self._apply_style()
        self._refresh_risk_summary()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_queue)
        self.timer.start(250)

    def _build_ui(self) -> None:
        shell = QWidget()
        shell.setObjectName("root")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("mainScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root = QWidget()
        root.setObjectName("contentRoot")
        scroll.setWidget(root)
        shell_layout.addWidget(scroll)
        self.setCentralWidget(shell)

        page = QVBoxLayout(root)
        page.setContentsMargins(22, 22, 22, 18)
        page.setSpacing(16)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(24, 20, 24, 20)
        hero_layout.setSpacing(18)
        copy = QVBoxLayout()
        copy.setSpacing(8)
        title = QLabel("A股做T行情终端")
        title.setObjectName("heroTitle")
        subtitle = QLabel("Qt v2 现代桌面壳｜模型监控｜微信推送｜自选池过滤")
        subtitle.setObjectName("heroSubtitle")
        chips = QHBoxLayout()
        chips.setSpacing(8)
        for text, name in (("CN A-SHARE", "chipBlue"), ("T SIGNAL", "chipRed"), ("LIVE PUSH", "chipGold"), (f"BUILD {BUILD_SHA[:7]}", "chipDark")):
            chip = QLabel(text)
            chip.setObjectName(name)
            chips.addWidget(chip)
        chips.addStretch(1)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        copy.addLayout(chips)
        hero_layout.addLayout(copy, 1)
        status_box = QVBoxLayout()
        status_label = QLabel("状态")
        status_label.setObjectName("heroMetricLabel")
        self.status_value = QLabel("未启动")
        self.status_value.setObjectName("heroMetric")
        status_box.addWidget(status_label, alignment=Qt.AlignRight)
        status_box.addWidget(self.status_value, alignment=Qt.AlignRight)
        hero_layout.addLayout(status_box)
        page.addWidget(hero)

        top = QHBoxLayout()
        top.setSpacing(16)
        config = GlassCard("监控配置", "保持旧版模型目录和推送配置，旧版更新后可直接使用")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.model_path = QLineEdit(str(self.models_dir))
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.weixin_mode = QLineEdit("weixin")
        self.interval = QLineEdit(str(DEFAULT_INTERVAL_SECONDS))
        self.watchlist = QLineEdit("剑桥科技，东山精密，福晶科技，利通电子，锡业股份，沃格光电，生益科技，通信ETF国泰，科创芯片ETF鹏华")
        self.only_watchlist = QCheckBox("只监控自选池匹配到的模型")
        self.only_watchlist.setChecked(True)
        choose_file = ModernButton("选择模型", "ghost")
        choose_dir = ModernButton("选择文件夹", "ghost")
        choose_file.clicked.connect(self._choose_file)
        choose_dir.clicked.connect(self._choose_dir)
        form.addWidget(QLabel("模型文件/文件夹"), 0, 0)
        form.addWidget(self.model_path, 0, 1)
        form.addWidget(choose_file, 0, 2)
        form.addWidget(choose_dir, 0, 3)
        form.addWidget(QLabel("PushPlus token"), 1, 0)
        form.addWidget(self.token, 1, 1)
        form.addWidget(QLabel("间隔秒"), 1, 2)
        form.addWidget(self.interval, 1, 3)
        form.addWidget(QLabel("微信推送"), 2, 0)
        form.addWidget(self.weixin_mode, 2, 1, 1, 3)
        form.addWidget(QLabel("自选池"), 3, 0)
        form.addWidget(self.watchlist, 3, 1, 1, 3)
        form.addWidget(self.only_watchlist, 4, 1, 1, 3)
        config.layout.addLayout(form)
        self.risk_summary = QLabel("模型风险摘要加载中")
        self.risk_summary.setObjectName("riskBox")
        self.risk_summary.setWordWrap(True)
        config.layout.addWidget(self.risk_summary)
        top.addWidget(config, 3)

        actions = GlassCard("快捷操作", "开始监控前建议先测试推送")
        action_grid = QGridLayout()
        action_grid.setSpacing(10)
        self.start_btn = ModernButton("开始监控", "primary")
        self.stop_btn = ModernButton("停止", "danger")
        self.test_btn = ModernButton("测试推送", "ghost")
        self.open_push_btn = ModernButton("打开 PushPlus", "ghost")
        self.weixin_login_btn = ModernButton("微信登录", "ghost")
        self.weixin_session_btn = ModernButton("刷新会话", "ghost")
        self.update_v1_btn = ModernButton("更新 v1 稳定版", "ghost")
        self.update_v2_btn = ModernButton("更新 v2 预览版", "ghost")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.test_btn.clicked.connect(self._test_push)
        self.open_push_btn.clicked.connect(lambda: webbrowser.open(PUSHPLUS_TOKEN_URL))
        self.weixin_login_btn.clicked.connect(self._start_weixin_login)
        self.weixin_session_btn.clicked.connect(self._start_weixin_session)
        self.update_v1_btn.clicked.connect(lambda: self._start_update("v1", WINDOWS_EXE_URL))
        self.update_v2_btn.clicked.connect(lambda: self._start_update("v2", WINDOWS_V2_EXE_URL))
        buttons = [
            self.start_btn,
            self.stop_btn,
            self.test_btn,
            self.open_push_btn,
            self.weixin_login_btn,
            self.weixin_session_btn,
            self.update_v1_btn,
            self.update_v2_btn,
        ]
        for idx, button in enumerate(buttons):
            action_grid.addWidget(button, idx // 2, idx % 2)
        actions.layout.addLayout(action_grid)
        hint = QLabel("旧版自动更新仍下载同名 EXE；新版构建会无缝覆盖旧入口。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        actions.layout.addWidget(hint)
        top.addWidget(actions, 1)
        page.addLayout(top)

        feature_card = GlassCard("功能区", "和 v1 对齐的分析、消息面、雷达、走势和研报入口")
        feature_grid = QGridLayout()
        feature_grid.setSpacing(10)
        feature_buttons: list[tuple[str, Any, str]] = [
            ("模型盘前", self._show_premarket_analysis, "ghost"),
            ("信息面盘前", self._show_info_premarket, "ghost"),
            ("自选信息面", self._open_custom_info_window, "ghost"),
            ("小作文雷达", self._open_rumor_radar_window, "ghost"),
            ("自选走势", self._open_watch_chart_window, "ghost"),
            ("AI研报", self._open_research_report_window, "ghost"),
            ("桌面图标", self._create_desktop_shortcut, "ghost"),
        ]
        for idx, (text, callback, kind) in enumerate(feature_buttons):
            button = ModernButton(text, kind)
            button.clicked.connect(callback)
            feature_grid.addWidget(button, idx // 4, idx % 4)
        feature_card.layout.addLayout(feature_grid)
        page.addWidget(feature_card)

        self.table = QTableWidget(0, 10)
        self.table.setObjectName("signalTable")
        self.table.setHorizontalHeaderLabels(["股票", "代码", "状态", "信号", "入场/现价", "目标1/2/3", "止损", "时间", "评分", "说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(290)
        page.addWidget(self.table)

        self.log = QTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(150)
        self.log.setMaximumHeight(240)
        page.addWidget(self.log)

    def _apply_style(self) -> None:
        self.setFont(QFont("Microsoft YaHei UI", 10))
        self.setStyleSheet(
            """
            QWidget#root, QWidget#contentRoot, QScrollArea#mainScroll {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #edf7ff, stop:0.45 #dfefff, stop:1 #f7fbff);
                color: #172437;
            }
            QScrollArea#mainScroll {
                border: 0px;
            }
            QScrollBar:vertical {
                background: rgba(255, 255, 255, 95);
                width: 12px;
                margin: 10px 3px 10px 3px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: rgba(45, 140, 255, 135);
                min-height: 48px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(23, 103, 212, 180);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QScrollBar:horizontal {
                background: rgba(255, 255, 255, 95);
                height: 12px;
                margin: 3px 10px 3px 10px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(45, 140, 255, 135);
                min-width: 48px;
                border-radius: 5px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
            QFrame#hero {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #061326, stop:0.52 #0c3a78, stop:1 #1788ff);
                border: 1px solid rgba(255, 255, 255, 180);
                border-radius: 24px;
            }
            QLabel#heroTitle {
                color: white;
                font-size: 30px;
                font-weight: 800;
                letter-spacing: 0px;
            }
            QLabel#heroSubtitle, QLabel#heroMetricLabel {
                color: #d7ebff;
                font-size: 13px;
            }
            QLabel#heroMetric {
                color: white;
                font-size: 22px;
                font-weight: 800;
            }
            QLabel#chipBlue, QLabel#chipRed, QLabel#chipGold, QLabel#chipDark {
                border-radius: 12px;
                padding: 5px 11px;
                font-size: 11px;
                font-weight: 800;
            }
            QLabel#chipBlue { background: #2d8cff; color: white; }
            QLabel#chipRed { background: #d71920; color: white; }
            QLabel#chipGold { background: #f5b342; color: #061326; }
            QLabel#chipDark { background: rgba(255,255,255,0.14); color: #d7ebff; border: 1px solid rgba(255,255,255,0.24); }
            QFrame#glassCard {
                background: rgba(248, 251, 255, 222);
                border: 1px solid rgba(255, 255, 255, 230);
                border-radius: 22px;
            }
            QFrame#accent {
                background: #8ec7ff;
                border-radius: 2px;
            }
            QLabel#sectionTitle {
                color: #0b4ea2;
                font-size: 15px;
                font-weight: 800;
            }
            QLabel#sectionSubtitle, QLabel#hint {
                color: #5b708a;
                font-size: 12px;
            }
            QLabel#riskBox {
                background: rgba(255, 248, 232, 210);
                border: 1px solid rgba(255, 255, 255, 230);
                border-radius: 16px;
                color: #9f2d33;
                padding: 10px;
            }
            QLineEdit {
                background: rgba(255, 255, 255, 230);
                border: 1px solid #c6d8ee;
                border-radius: 13px;
                padding: 9px 11px;
                selection-background-color: #2d8cff;
            }
            QLineEdit:focus {
                border: 1px solid #2d8cff;
                background: white;
            }
            QPushButton {
                background: rgba(247, 251, 255, 225);
                border: 1px solid rgba(255,255,255,230);
                border-radius: 15px;
                color: #0b4ea2;
                padding: 9px 14px;
                font-weight: 650;
            }
            QPushButton:hover {
                background: #ddeeff;
                border: 1px solid #8ec7ff;
            }
            QPushButton[kind="primary"] {
                color: white;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #37a0ff, stop:1 #1767d4);
            }
            QPushButton[kind="danger"] {
                color: white;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff6067, stop:1 #d71920);
            }
            QPushButton:disabled {
                color: #9aa9ba;
                background: rgba(225, 233, 242, 180);
            }
            QCheckBox {
                color: #2b3c50;
                spacing: 8px;
            }
            QTableWidget#signalTable {
                background: rgba(251, 253, 255, 230);
                alternate-background-color: rgba(241, 248, 255, 230);
                border: 1px solid rgba(255,255,255,230);
                border-radius: 18px;
                gridline-color: #d6e6f6;
                selection-background-color: #ddeeff;
                selection-color: #0b4ea2;
            }
            QHeaderView::section {
                background: #061326;
                color: white;
                border: 0px;
                padding: 8px;
                font-weight: 800;
            }
            QTextEdit#log {
                background: #061326;
                color: #d7ebff;
                border: 1px solid #1b4b7d;
                border-radius: 18px;
                padding: 12px;
                font-family: "Cascadia Mono", "Consolas";
            }
            """
        )

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择模型 JSON", str(self.models_dir), "JSON 模型 (*.json);;所有文件 (*.*)")
        if path:
            self.model_path.setText(path)
            self._refresh_risk_summary()

    def _choose_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择模型文件夹", str(self.models_dir))
        if path:
            self.model_path.setText(path)
            self._refresh_risk_summary()

    def _start_update(self, channel: str, download_url: str) -> None:
        if sys.platform != "win32":
            url = MACOS_ZIP_URL if sys.platform == "darwin" and channel == "v1" else RELEASE_URL
            self._log(f"当前系统暂不支持一键替换，正在打开下载页：{channel}")
            webbrowser.open(url)
            return
        if channel == "v2" and not QMessageBox.question(
            self,
            "更新 v2 预览版",
            "v2 是现代界面预览版，功能仍需继续对齐 v1。\n\n是否下载并打开 v2？当前 v2 会关闭，新版本会启动。",
        ) == QMessageBox.Yes:
            return
        self.status_value.setText(f"下载 {channel}")
        self._log(f"正在下载 {channel} 更新包")
        threading.Thread(target=self._run_update, args=(channel, download_url), daemon=True).start()

    def _run_update(self, channel: str, download_url: str) -> None:
        try:
            updates_dir = app_dir() / "updates"
            updates_dir.mkdir(parents=True, exist_ok=True)
            suffix = datetime.now(BEIJING_TZ).strftime(f"{channel}-%Y%m%d%H%M%S")
            target = updates_dir / f"AShareTSignalMonitor-{suffix}.exe"
            used_url = self._download_with_mirrors(download_url, target)
            self.queue.put(("update_ready", {"channel": channel, "file": str(target), "url": used_url}))
        except Exception as exc:
            self.queue.put(("error", f"{channel} 更新失败：{exc}"))

    def _download_with_mirrors(self, download_url: str, target: Path) -> str:
        errors = []
        for url in self._download_candidates(download_url):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "AShareTSignalMonitor/2.0"})
                with urllib.request.urlopen(req, timeout=90) as resp, target.open("wb") as out:
                    shutil.copyfileobj(resp, out)
                if target.stat().st_size < 1024 * 1024:
                    raise RuntimeError("下载文件过小，可能不是有效 EXE")
                with target.open("rb") as handle:
                    if handle.read(2) != b"MZ":
                        raise RuntimeError("下载文件不是有效 Windows EXE")
                return url
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                self.queue.put(("log", f"下载源不可用，继续尝试下一个：{url}"))
                try:
                    if target.exists():
                        target.unlink()
                except Exception:
                    pass
        raise RuntimeError("所有下载源都失败：\n" + "\n".join(errors[-5:]))

    def _download_candidates(self, download_url: str) -> list[str]:
        urls = [download_url]
        for base in GITHUB_DOWNLOAD_MIRRORS:
            urls.append(base.rstrip("/") + "/" + download_url)
        deduped = []
        seen = set()
        for url in urls:
            if url not in seen:
                seen.add(url)
                deduped.append(url)
        return deduped

    def _install_update(self, update_file: Path) -> None:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) if os.name == "nt" else 0
        subprocess.Popen([str(update_file)], cwd=str(update_file.parent), creationflags=flags)
        QApplication.instance().quit()

    def _model_files(self, model_path: Path) -> list[Path]:
        if model_path.is_file():
            return [model_path]
        if model_path.is_dir():
            return sorted(model_path.glob("*.json"))
        return []

    def _refresh_risk_summary(self) -> None:
        model_path = Path(self.model_path.text().strip())
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
                parts.append(f"{name}：未写入回测摘要。")
        text = "模型风险摘要：" + "  ".join(parts) if parts else "模型风险摘要：没有找到模型 JSON。"
        self.risk_summary.setText(text + " 历史回测不代表未来收益，请小心使用。")

    def _selected_models(self, path: Path) -> list[TModel]:
        models = load_models(path)
        if not self.only_watchlist.isChecked():
            return models
        raw = self.watchlist.text().replace("，", ",")
        keys = {item.strip().upper() for item in raw.split(",") if item.strip()}
        if not keys:
            return models
        selected = [
            model
            for model in models
            if model.name.upper() in keys or model.code.upper() in keys or model.code.split(".")[0] in keys
        ]
        return selected or models

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            self._log("监控已经在运行")
            return
        model_path = Path(self.model_path.text().strip())
        try:
            interval = max(10, int(self.interval.text().strip() or DEFAULT_INTERVAL_SECONDS))
        except ValueError:
            QMessageBox.warning(self, "间隔错误", "间隔秒必须是数字。")
            return
        token = self.token.text().strip()
        weixin_mode = self.weixin_mode.text().strip()
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._run_worker, args=(model_path, token, weixin_mode, interval), daemon=True)
        self.worker.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_value.setText("监控中")
        self._log("监控已启动")

    def _stop(self) -> None:
        self.stop_event.set()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_value.setText("已停止")
        self._log("正在停止监控")

    def _run_worker(self, model_path: Path, token: str, weixin_mode: str, interval: int) -> None:
        try:
            models = self._selected_models(model_path)
            self.queue.put(("risk", f"已加载 {len(models)} 个模型：" + "、".join(model.name for model in models)))
            engine = ModelSignalEngine(models, app_dir() / "data", token, weixin_mode)
            while not self.stop_event.is_set():
                result = engine.check_all()
                self.queue.put(("result", result))
                now = datetime.now(BEIJING_TZ)
                day = now.strftime("%Y-%m-%d")
                if self._should_send_daily_report(now, day):
                    report = engine.build_daily_battle_report(day)
                    engine.notifier.send_text(str(report["content"]), title=f"做T盘后战报：{day}")
                    self.daily_report_sent_day = day
                    self.queue.put(("log", f"盘后战报已推送：{report['summary']}"))
                for _ in range(interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)
        except Exception as exc:
            self.queue.put(("error", str(exc)))

    def _should_send_daily_report(self, now: datetime, day: str) -> bool:
        if self.daily_report_sent_day == day:
            return False
        if now.weekday() >= 5:
            return False
        return now.strftime("%H:%M") >= "15:05"

    def _test_push(self) -> None:
        token = self.token.text().strip()
        weixin_mode = self.weixin_mode.text().strip()

        def worker() -> None:
            try:
                notifier = MultiNotifier([PushPlusNotifier(token), WeixinPushNotifier(weixin_mode)])
                notifier.send_text("做T提醒测试：Qt v2 现代版已接通。", title="做T提醒测试")
                self.queue.put(("log", "测试推送已发送"))
            except Exception as exc:
                self.queue.put(("error", f"测试推送失败：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _open_text_window(self, title: str, content: str, width: int = 920, height: int = 680) -> QTextEdit:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(width, height)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(content)
        text.setObjectName("reportText")
        layout.addWidget(text)
        dialog.setStyleSheet(
            """
            QDialog {
                background: #edf7ff;
            }
            QTextEdit#reportText {
                background: rgba(251, 253, 255, 240);
                border: 1px solid rgba(255,255,255,230);
                border-radius: 18px;
                padding: 14px;
                color: #172437;
                font-family: "Microsoft YaHei UI";
                font-size: 13px;
            }
            """
        )
        dialog.show()
        self.child_windows.append(dialog)
        return text

    def _show_premarket_analysis(self) -> None:
        model_path = Path(self.model_path.text().strip())
        if not model_path.exists():
            QMessageBox.warning(self, "模型不存在", "请选择模型 JSON 文件或模型文件夹。")
            return
        try:
            analysis = self._build_premarket_analysis(model_path)
            self._open_text_window("模型盘前", analysis)
            self._log("已生成模型盘前分析")
        except Exception as exc:
            QMessageBox.critical(self, "盘前分析失败", str(exc))

    def _show_info_premarket(self) -> None:
        self.status_value.setText("信息面盘前")
        self._log("正在获取信息面盘前摘要")

        def worker() -> None:
            try:
                self.queue.put(("text_window", ("信息面盘前", self._build_info_premarket())))
                self.queue.put(("log", "信息面盘前摘要已生成"))
            except Exception as exc:
                self.queue.put(("error", f"信息面盘前失败：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _open_custom_info_window(self) -> None:
        self._open_query_window(
            title="自选信息面",
            default_query="东山精密",
            action_label="搜索",
            extra_label="天数",
            extra_default="3",
            builder=lambda query, extra: self._build_custom_info_report(query, max(1, min(14, int(extra or 3))), broad=True),
        )

    def _open_rumor_radar_window(self) -> None:
        self._open_query_window(
            title="小作文雷达",
            default_query=self.watchlist.text(),
            action_label="扫描",
            extra_label="小时",
            extra_default="8",
            builder=lambda query, extra: self._build_rumor_radar_report(query, max(1, min(48, int(extra or 8)))),
        )

    def _open_research_report_window(self) -> None:
        self._open_query_window(
            title="AI研报",
            default_query="云南锗业",
            action_label="生成研报",
            extra_label="",
            extra_default="",
            builder=lambda query, _extra: self._build_research_report(query),
        )

    def _open_watch_chart_window(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("自选走势")
        dialog.resize(980, 720)
        layout = QVBoxLayout(dialog)
        bar = QHBoxLayout()
        query = QLineEdit("剑桥科技")
        period = QComboBox()
        period.addItems(["分时", "日线", "周线", "月线"])
        period.setCurrentText("日线")
        run = ModernButton("查看走势", "primary")
        bar.addWidget(QLabel("股票"))
        bar.addWidget(query, 1)
        bar.addWidget(period)
        bar.addWidget(run)
        layout.addLayout(bar)
        chart = PriceChartWidget()
        layout.addWidget(chart)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setMaximumHeight(190)
        text.setPlainText("输入股票后点击查看走势。")
        layout.addWidget(text)

        def execute() -> None:
            q = query.text().strip()
            if not q:
                QMessageBox.warning(dialog, "缺少股票", "请输入股票名称或代码。")
                return
            text.setPlainText("正在获取走势数据...")
            chart.set_error("正在获取走势数据...")

            def worker() -> None:
                try:
                    payload = self._build_chart_payload(q, period.currentText())
                    content = self._chart_summary_text(payload)
                    self.queue.put(("chart_payload", (chart, text, payload, content)))
                except Exception as exc:
                    self.queue.put(("chart_error", (chart, text, f"走势获取失败：{exc}")))

            threading.Thread(target=worker, daemon=True).start()

        run.clicked.connect(execute)
        query.returnPressed.connect(execute)
        dialog.show()
        self.child_windows.append(dialog)

    def _open_query_window(
        self,
        title: str,
        default_query: str,
        action_label: str,
        extra_label: str,
        extra_default: str,
        builder: Any,
    ) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(920, 680)
        layout = QVBoxLayout(dialog)
        bar = QHBoxLayout()
        query = QLineEdit(default_query)
        extra = QLineEdit(extra_default)
        extra.setFixedWidth(76)
        run = ModernButton(action_label, "primary")
        bar.addWidget(QLabel("关键词"))
        bar.addWidget(query, 1)
        if extra_label:
            bar.addWidget(QLabel(extra_label))
            bar.addWidget(extra)
        bar.addWidget(run)
        layout.addLayout(bar)
        text = QTextEdit()
        text.setReadOnly(True)
        layout.addWidget(text)

        def execute() -> None:
            q = query.text().strip()
            if not q:
                QMessageBox.warning(dialog, "缺少输入", "请输入股票名、代码或关键词。")
                return
            text.setPlainText("正在处理，请稍候...")

            def worker() -> None:
                try:
                    content = builder(q, extra.text().strip())
                    self.queue.put(("set_text", (text, content)))
                except Exception as exc:
                    self.queue.put(("set_text", (text, f"{title}失败：{exc}")))

            threading.Thread(target=worker, daemon=True).start()

        run.clicked.connect(execute)
        query.returnPressed.connect(execute)
        dialog.show()
        self.child_windows.append(dialog)

    def _create_desktop_shortcut(self) -> None:
        try:
            shortcut = create_windows_desktop_shortcut()
            self._log(f"桌面图标已创建：{shortcut}")
            QMessageBox.information(self, "桌面图标", f"已创建桌面图标：\n{shortcut}")
        except Exception as exc:
            self._log(f"桌面图标创建失败：{exc}")
            QMessageBox.critical(self, "桌面图标创建失败", str(exc))

    def _start_weixin_login(self) -> None:
        self.weixin_mode.setText("weixin")
        self.weixin_session_stop.clear()
        threading.Thread(target=self._run_weixin_login, daemon=True).start()

    def _run_weixin_login(self) -> None:
        try:
            login_with_qr(logger=lambda msg: self.queue.put(("log", msg)), stop=self.weixin_session_stop.is_set)
            self.queue.put(("log", "微信扫码登录完成。请在微信里给 bot 发一句话，然后点刷新会话。"))
            self._ensure_weixin_session_worker()
        except Exception as exc:
            self.queue.put(("error", f"微信登录失败：{exc}"))

    def _start_weixin_session(self) -> None:
        try:
            load_weixin_push_targets()
            self._log(f"已找到可推送会话。凭证：{weixin_credentials_path()}")
        except Exception as exc:
            self._log(f"微信会话还未就绪：{exc}")
        self._ensure_weixin_session_worker()

    def _ensure_weixin_session_worker(self) -> None:
        if self.weixin_session_worker and self.weixin_session_worker.is_alive():
            return
        self.weixin_session_stop.clear()
        self.weixin_session_worker = threading.Thread(target=self._run_weixin_session_worker, daemon=True)
        self.weixin_session_worker.start()
        self._log("微信会话刷新已启动。请在微信里给 bot 发一句话。")

    def _run_weixin_session_worker(self) -> None:
        refresh_context_tokens(logger=lambda msg: self.queue.put(("log", msg)), stop=self.weixin_session_stop.is_set)

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(str(payload))
            elif kind == "risk":
                self.risk_summary.setText(str(payload) + "。历史回测不代表未来收益，请小心使用。")
            elif kind == "text_window":
                title, content = payload if isinstance(payload, tuple) else ("信息", str(payload))
                self._open_text_window(str(title), str(content))
            elif kind == "set_text":
                if isinstance(payload, tuple) and len(payload) == 2:
                    widget, content = payload
                    if hasattr(widget, "setPlainText"):
                        widget.setPlainText(str(content))
            elif kind == "chart_payload":
                if isinstance(payload, tuple) and len(payload) == 4:
                    chart, text, chart_payload, content = payload
                    if hasattr(chart, "set_payload") and isinstance(chart_payload, dict):
                        chart.set_payload(chart_payload)
                    if hasattr(text, "setPlainText"):
                        text.setPlainText(str(content))
            elif kind == "chart_error":
                if isinstance(payload, tuple) and len(payload) == 3:
                    chart, text, message = payload
                    if hasattr(chart, "set_error"):
                        chart.set_error(str(message))
                    if hasattr(text, "setPlainText"):
                        text.setPlainText(str(message))
            elif kind == "error":
                self.status_value.setText("错误")
                self._log(str(payload))
                QMessageBox.critical(self, "错误", str(payload))
            elif kind == "update_ready" and isinstance(payload, dict):
                update_file = Path(str(payload.get("file", "")))
                channel = str(payload.get("channel", "新版"))
                self.status_value.setText(f"{channel} 已下载")
                self._log(f"{channel} 更新包已下载：{update_file}")
                if update_file.exists() and QMessageBox.question(
                    self,
                    "更新完成",
                    f"{channel} 更新包已下载。\n\n是否现在打开新版本？当前 v2 会关闭。",
                ) == QMessageBox.Yes:
                    self._install_update(update_file)
            elif kind == "result" and isinstance(payload, dict):
                self._render_result(payload)

    def _render_result(self, result: dict[str, object]) -> None:
        checked_at = result.get("checked_at", "-")
        items = result.get("items", [])
        alerts = result.get("alerts", [])
        self.status_value.setText(f"最近检查 {checked_at}")
        if not isinstance(items, list):
            return
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            values = [
                item.get("symbol", "-"),
                item.get("code", "-"),
                item.get("status", "-"),
                item.get("entry_label", "-") if item.get("status") == "signal" else "-",
                item.get("entry_price", item.get("last_price", "-")),
                target_ladder(item),
                item.get("stop_price", "-"),
                item.get("minute", "-"),
                item.get("signal_score", "-"),
                item.get("message", "-"),
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setFlags(cell.flags() ^ Qt.ItemIsEditable)
                if item.get("status") == "signal":
                    cell.setForeground(QColor("#b00020"))
                elif item.get("status") == "error":
                    cell.setForeground(QColor("#d71920"))
                self.table.setItem(row, col, cell)
        self._log(f"[{checked_at}] 检查完成，新信号 {len(alerts) if isinstance(alerts, list) else 0} 个")
        if isinstance(alerts, list):
            for alert in alerts:
                if isinstance(alert, dict):
                    self._log(f"信号：{alert.get('symbol')} {alert.get('entry_label')} 入场 {alert.get('entry_price')} 目标 {target_ladder(alert)}")

    def _log(self, message: str) -> None:
        now = time.strftime("%H:%M:%S")
        self.log.append(f"[{now}] {message}")

    def closeEvent(self, event: Any) -> None:
        self.stop_event.set()
        self.weixin_session_stop.set()
        super().closeEvent(event)


for _legacy_name, _legacy_value in LegacyMonitorApp.__dict__.items():
    if _legacy_name.startswith("__"):
        continue
    if callable(_legacy_value) and not hasattr(MonitorWindow, _legacy_name):
        setattr(MonitorWindow, _legacy_name, _legacy_value)


def main() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    app = QApplication(sys.argv)
    window = MonitorWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_path = startup_log_path()
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        raise
