from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .config import CrawlConfig, default_data_root
from .engine import CrawlEngine
from .exporter import TxtExporter
from .models import CrawlState
from .storage import SQLiteStore


class CrawlerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Persian Comment Crawler")
        self.geometry("1120x760")
        self.minsize(900, 640)
        self.configure(bg="#102e32")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.store: SQLiteStore | None = None
        self.engine: CrawlEngine | None = None
        self.config_model = CrawlConfig()
        self._build_styles()
        self._build_ui()
        self.after(300, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#102e32")
        style.configure("Card.TFrame", background="#173b3f")
        style.configure("TLabel", background="#102e32", foreground="#e7f4eb")
        style.configure("Muted.TLabel", background="#173b3f", foreground="#9bb6ad", font=("Segoe UI", 9))
        style.configure("CardValue.TLabel", background="#173b3f", foreground="#d9f27f", font=("Segoe UI", 21, "bold"))
        style.configure("CardTitle.TLabel", background="#173b3f", foreground="#b4ccc2", font=("Segoe UI", 9))
        style.configure("Heading.TLabel", background="#102e32", foreground="#f4fff5", font=("Segoe UI", 20, "bold"))
        style.configure("TButton", font=("Segoe UI", 9, "bold"), padding=(11, 7))
        style.configure("Start.TButton", background="#d9f27f", foreground="#102e32")
        style.map("Start.TButton", background=[("active", "#c6e55f")])
        style.configure("Danger.TButton", background="#bb6645", foreground="#fff")
        style.configure("Treeview", background="#102e32", fieldbackground="#102e32", foreground="#e7f4eb", rowheight=28)
        style.configure("Treeview.Heading", background="#214d50", foreground="#d9f27f", font=("Segoe UI", 9, "bold"))

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", padx=24, pady=(22, 14))
        ttk.Label(header, text="Persian Comment Crawler", style="Heading.TLabel").pack(side="left")
        self.safe_label = ttk.Label(header, text="SAFE TEST MODE · 10 MIN · 1 GB", foreground="#d9f27f", background="#102e32", font=("Segoe UI", 9, "bold"))
        self.safe_label.pack(side="right", pady=6)

        cards = ttk.Frame(self)
        cards.pack(fill="x", padx=24)
        self.card_values: dict[str, ttk.Label] = {}
        for title, key in (("Websites", "websites"), ("Visited pages", "pages"), ("Unique Persian comments", "comments"), ("Requests / sec", "speed"), ("Storage used", "storage"), ("Errors", "errors")):
            card = ttk.Frame(cards, style="Card.TFrame", padding=(15, 12))
            card.pack(side="left", fill="both", expand=True, padx=(0, 8))
            value = ttk.Label(card, text="0", style="CardValue.TLabel")
            value.pack(anchor="w")
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(4, 0))
            self.card_values[key] = value

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=24, pady=18)
        left = ttk.Frame(body, style="Card.TFrame", padding=16)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        right = ttk.Frame(body, style="Card.TFrame", padding=16)
        right.pack(side="right", fill="both", expand=True)

        ttk.Label(left, text="Crawl setup", foreground="#d9f27f", background="#173b3f", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(left, text="Seeds are optional, but the first crawl needs at least one public URL or domain.", style="Muted.TLabel", wraplength=400).pack(anchor="w", pady=(5, 12))
        ttk.Label(left, text="Seed URLs or domains", style="Muted.TLabel").pack(anchor="w")
        self.seed_text = ScrolledText(left, height=6, background="#102e32", foreground="#e7f4eb", insertbackground="#d9f27f", relief="flat", font=("Cascadia Code", 9), wrap="none")
        self.seed_text.pack(fill="x", pady=(5, 12))

        discovery_row = ttk.Frame(left, style="Card.TFrame")
        discovery_row.pack(fill="x", pady=(0, 8))
        self.auto_ir_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(discovery_row, text="Automatically discover .ir websites", variable=self.auto_ir_var).pack(side="left")
        ttk.Label(discovery_row, text="No seed required", style="Muted.TLabel").pack(side="right")
        ttk.Label(left, text="Bing Web Search API key (optional)", style="Muted.TLabel").pack(anchor="w")
        self.search_key_var = tk.StringVar()
        ttk.Entry(left, textvariable=self.search_key_var, show="•").pack(fill="x", pady=(5, 3))
        ttk.Label(left, text="Required for automatic search-engine discovery; never written to the database.", style="Muted.TLabel", wraplength=400).pack(anchor="w", pady=(0, 11))

        ttk.Label(left, text="Managed data directory", style="Muted.TLabel").pack(anchor="w")
        data_row = ttk.Frame(left, style="Card.TFrame")
        data_row.pack(fill="x", pady=(5, 12))
        self.data_var = tk.StringVar(value=str(default_data_root()))
        ttk.Entry(data_row, textvariable=self.data_var).pack(side="left", fill="x", expand=True)
        ttk.Button(data_row, text="Browse", command=self._browse).pack(side="right", padx=(8, 0))

        settings = ttk.Frame(left, style="Card.TFrame")
        settings.pack(fill="x", pady=(0, 14))
        self.duration_var = tk.IntVar(value=10)
        self.max_comments_var = tk.IntVar(value=5000)
        self.concurrency_var = tk.IntVar(value=2)
        for label, variable, width in (("Minutes", self.duration_var, 5), ("Max comments", self.max_comments_var, 7), ("Workers", self.concurrency_var, 4)):
            ttk.Label(settings, text=label, style="Muted.TLabel").pack(side="left", padx=(0, 4))
            ttk.Spinbox(settings, from_=1, to=1440, textvariable=variable, width=width).pack(side="left", padx=(0, 13))

        controls = ttk.Frame(left, style="Card.TFrame")
        controls.pack(fill="x", pady=(5, 0))
        self.start_button = ttk.Button(controls, text="Start", style="Start.TButton", command=self._start)
        self.start_button.pack(side="left", padx=(0, 7))
        self.pause_button = ttk.Button(controls, text="Pause", command=self._pause)
        self.pause_button.pack(side="left", padx=(0, 7))
        self.resume_button = ttk.Button(controls, text="Resume", command=self._resume)
        self.resume_button.pack(side="left", padx=(0, 7))
        self.stop_button = ttk.Button(controls, text="Stop", command=self._stop)
        self.stop_button.pack(side="left", padx=(0, 7))
        self.emergency_button = ttk.Button(controls, text="Emergency stop", style="Danger.TButton", command=self._emergency_stop)
        self.emergency_button.pack(side="right")

        exports = ttk.Frame(left, style="Card.TFrame")
        exports.pack(fill="x", pady=(18, 0))
        ttk.Button(exports, text="Export all comments to TXT", command=self._export).pack(side="left")
        ttk.Button(exports, text="Open data folder", command=self._open_folder).pack(side="right")

        ttk.Label(right, text="Recent activity", foreground="#d9f27f", background="#173b3f", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self.state_label = ttk.Label(right, text="Idle", foreground="#e7f4eb", background="#173b3f", font=("Segoe UI", 10, "bold"))
        self.state_label.pack(anchor="w", pady=(5, 8))
        self.activity = ttk.Treeview(right, columns=("time", "message"), show="headings")
        self.activity.heading("time", text="Time")
        self.activity.heading("message", text="Event")
        self.activity.column("time", width=75, stretch=False)
        self.activity.column("message", width=360, stretch=True)
        self.activity.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(right, orient="vertical", command=self.activity.yview)
        scroll.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")
        self.activity.configure(yscrollcommand=scroll.set)

    def _browse(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.data_var.get(), title="Choose managed data directory")
        if selected:
            self.data_var.set(selected)

    def _config(self) -> CrawlConfig:
        seeds = [line.strip() for line in self.seed_text.get("1.0", "end").splitlines() if line.strip()]
        return CrawlConfig(test_duration_seconds=max(60, self.duration_var.get() * 60), maximum_comments=self.max_comments_var.get(), concurrency=self.concurrency_var.get(), auto_ir_discovery=self.auto_ir_var.get(), search_api_key=self.search_key_var.get().strip(), seed_urls=seeds, data_root=Path(self.data_var.get()))

    def _start(self) -> None:
        if self.engine and self.engine.state in {CrawlState.RUNNING, CrawlState.PAUSED, CrawlState.STOPPING}:
            return
        try:
            self.config_model = self._config()
            self.store = SQLiteStore(self.config_model.data_root, self.config_model)
            self.engine = CrawlEngine(self.config_model, self.store, lambda event, payload: self.events.put((event, payload)))
            self.engine.start()
        except Exception as exc:
            messagebox.showerror("Could not start crawl", str(exc))

    def _pause(self) -> None:
        if self.engine:
            self.engine.pause()

    def _resume(self) -> None:
        if self.engine:
            self.engine.resume()

    def _stop(self) -> None:
        if self.engine:
            self.engine.stop()

    def _emergency_stop(self) -> None:
        if self.engine:
            self.engine.emergency_stop()

    def _export(self) -> None:
        if not self.store:
            messagebox.showinfo("Nothing to export", "Start a session first so the managed database is available.")
            return
        try:
            outputs = TxtExporter(self.store, self.config_model).export()
            messagebox.showinfo("Export complete", f"Wrote {len(outputs)} TXT file(s) under the managed data folder.")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))

    def _open_folder(self) -> None:
        path = Path(self.data_var.get())
        path.mkdir(parents=True, exist_ok=True)
        import os
        os.startfile(path)  # type: ignore[attr-defined]

    def _drain_events(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "state":
                self.state_label.configure(text=str(payload.value if hasattr(payload, "value") else payload).replace("_", " ").title())
            elif event == "stats" and isinstance(payload, dict):
                self.card_values["pages"].configure(text=f"{payload.get('visited_pages', 0):,}")
                self.card_values["comments"].configure(text=f"{payload.get('unique_comments', 0):,}")
                self.card_values["speed"].configure(text=f"{payload.get('current_speed', 0):.1f}")
                self.card_values["storage"].configure(text=f"{payload.get('storage_bytes', 0) / 1_000_000:.1f} MB")
                self.card_values["errors"].configure(text=f"{payload.get('errors', 0):,}")
            elif event == "activity":
                self.activity.insert("", "end", values=("now", str(payload)))
                children = self.activity.get_children()
                if len(children) > 100:
                    self.activity.delete(children[0])
            elif event == "budget":
                messagebox.showwarning("Safe storage stop", str(payload))
        self.after(300, self._drain_events)

    def _close(self) -> None:
        if self.engine and self.engine.state in {CrawlState.RUNNING, CrawlState.PAUSED, CrawlState.BUDGET_PAUSED}:
            self.engine.stop()
            self.engine.wait(3)
        if self.store:
            self.store.close()
        self.destroy()
