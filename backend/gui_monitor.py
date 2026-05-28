from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from model_engine import ModelSignalEngine, load_models
from notifier import PushPlusNotifier


DEFAULT_INTERVAL_SECONDS = 30


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
            if not dest.exists():
                shutil.copy2(model_file, dest)
    return target


class MonitorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("A股做T信号监控")
        self.root.geometry("980x640")
        self.root.minsize(860, 560)

        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.models_dir = ensure_default_models()

        self.token_var = tk.StringVar()
        self.model_path_var = tk.StringVar(value=str(self.models_dir))
        self.interval_var = tk.StringVar(value=str(DEFAULT_INTERVAL_SECONDS))
        self.status_var = tk.StringVar(value="未启动")

        self._build_ui()
        self.root.after(300, self._drain_queue)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        form = ttk.LabelFrame(outer, text="配置")
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="模型文件/文件夹").grid(row=0, column=0, sticky=tk.W, padx=8, pady=8)
        ttk.Entry(form, textvariable=self.model_path_var).grid(row=0, column=1, sticky=tk.EW, padx=8, pady=8)
        ttk.Button(form, text="选择文件", command=self._choose_file).grid(row=0, column=2, padx=4, pady=8)
        ttk.Button(form, text="选择文件夹", command=self._choose_dir).grid(row=0, column=3, padx=8, pady=8)

        ttk.Label(form, text="PushPlus token").grid(row=1, column=0, sticky=tk.W, padx=8, pady=8)
        ttk.Entry(form, textvariable=self.token_var, show="*").grid(row=1, column=1, sticky=tk.EW, padx=8, pady=8)
        ttk.Label(form, text="间隔秒").grid(row=1, column=2, sticky=tk.E, padx=4, pady=8)
        ttk.Entry(form, textvariable=self.interval_var, width=8).grid(row=1, column=3, sticky=tk.W, padx=8, pady=8)

        controls = ttk.Frame(outer)
        controls.pack(fill=tk.X, pady=(10, 8))
        ttk.Button(controls, text="测试推送", command=self._test_push).pack(side=tk.LEFT)
        ttk.Button(controls, text="开始监控", command=self._start).pack(side=tk.LEFT, padx=8)
        ttk.Button(controls, text="停止", command=self._stop).pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self.status_var).pack(side=tk.RIGHT)

        columns = ("symbol", "code", "status", "price", "minute", "score", "message")
        self.table = ttk.Treeview(outer, columns=columns, show="headings", height=11)
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
        self.table.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        log_frame = ttk.LabelFrame(outer, text="运行日志 / 信号")
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(log_frame, height=10, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择模型 JSON",
            initialdir=str(self.models_dir),
            filetypes=[("JSON 模型", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self.model_path_var.set(path)

    def _choose_dir(self) -> None:
        path = filedialog.askdirectory(title="选择模型文件夹", initialdir=str(self.models_dir))
        if path:
            self.model_path_var.set(path)

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
        self._log("监控已启动")

    def _stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("停止中")
        self._log("正在停止监控")

    def _run_worker(self, model_path: Path, token: str, interval: int) -> None:
        try:
            models = load_models(model_path)
            self.queue.put(("log", f"已加载 {len(models)} 个模型：" + "、".join(m.name for m in models)))
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
                messagebox.showerror("启动失败", str(payload))
            elif kind == "stopped":
                self.status_var.set("已停止")
            elif kind == "result":
                self._render_result(payload)  # type: ignore[arg-type]
        self.root.after(300, self._drain_queue)

    def _render_result(self, result: dict[str, object]) -> None:
        checked_at = result.get("checked_at", "-")
        items = result.get("items", [])
        alerts = result.get("alerts", [])
        self.status_var.set(f"最近检查：{checked_at}")
        self.table.delete(*self.table.get_children())
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
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
