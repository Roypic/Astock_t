from __future__ import annotations

import os
import queue
import random
import shutil
import sys
import threading
import time
import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from model_engine import ModelSignalEngine, load_models
from notifier import PushPlusNotifier


DEFAULT_INTERVAL_SECONDS = 30
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
        self.root.geometry("1060x720")
        self.root.minsize(940, 620)
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

        controls = ttk.Frame(outer)
        controls.pack(fill=tk.X, pady=(0, 12))
        ttk.Button(controls, text="测试推送", style="Ghost.TButton", command=self._test_push).pack(side=tk.LEFT)
        ttk.Button(controls, text="开始监控", style="Primary.TButton", command=self._start).pack(side=tk.LEFT, padx=8)
        ttk.Button(controls, text="停止", style="Warm.TButton", command=self._stop).pack(side=tk.LEFT)

        columns = ("symbol", "code", "status", "price", "minute", "score", "message")
        table_card = ttk.Frame(outer, style="Card.TFrame", padding=12)
        table_card.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
        ttk.Label(table_card, text="监控列表", style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 8))
        self.table = ttk.Treeview(table_card, columns=columns, show="headings", height=10)
        headings = {
            "symbol": "股票",
            "code": "代码",
            "status": "状态",
            "price": "现价",
            "minute": "时间",
            "score": "评分",
            "message": "说明",
        }
        widths = {"symbol": 110, "code": 120, "status": 90, "price": 80, "minute": 80, "score": 70, "message": 360}
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
                    item.get("last_price", "-"),
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
                        f"信号：{alert.get('symbol')} 入场 {alert.get('entry_price')} "
                        f"目标 {alert.get('exit_price')} 止损 {alert.get('stop_price')} "
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
