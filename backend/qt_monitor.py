from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
        self.watchlist = QLineEdit("剑桥科技，东山精密，福晶科技，利通电子，锡业股份，沃格光电")
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
        self.update_btn = ModernButton("下载最新版", "ghost")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.test_btn.clicked.connect(self._test_push)
        self.open_push_btn.clicked.connect(lambda: webbrowser.open(PUSHPLUS_TOKEN_URL))
        self.weixin_login_btn.clicked.connect(self._start_weixin_login)
        self.weixin_session_btn.clicked.connect(self._start_weixin_session)
        self.update_btn.clicked.connect(lambda: webbrowser.open(RELEASE_URL))
        buttons = [
            self.start_btn,
            self.stop_btn,
            self.test_btn,
            self.open_push_btn,
            self.weixin_login_btn,
            self.weixin_session_btn,
            self.update_btn,
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
                for _ in range(interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)
        except Exception as exc:
            self.queue.put(("error", str(exc)))

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
            elif kind == "error":
                self.status_value.setText("错误")
                self._log(str(payload))
                QMessageBox.critical(self, "错误", str(payload))
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
