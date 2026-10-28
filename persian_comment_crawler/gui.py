from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .config import CrawlConfig, default_data_root
from .engine import CrawlEngine
from .exporter import ArticleTxtExporter, TxtExporter
from .models import CrawlState
from .storage import SQLiteStore


class CrawlerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Persian Comment Crawler")
        self.geometry("1360x900")
        self.minsize(1100, 760)
        self.configure(bg="#102e32")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.store: SQLiteStore | None = None
        self.engine: CrawlEngine | None = None
        self.config_model = CrawlConfig()
        self._build_styles()
        self._build_ui()
        self._load_existing_stats()
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
        style.configure("CardValue.TLabel", background="#173b3f", foreground="#d9f27f", font=("Segoe UI", 17, "bold"))
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

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=24, pady=(0, 18))
        self.comments_tab = ttk.Frame(self.notebook, style="TFrame")
        self.articles_tab = ttk.Frame(self.notebook, style="TFrame")
        self.notebook.add(self.comments_tab, text="Comments")
        self.notebook.add(self.articles_tab, text="Articles")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        content = self.comments_tab

        cards = ttk.Frame(content)
        cards.pack(fill="x", padx=24)
        self.card_values: dict[str, ttk.Label] = {}
        for title, key in (("Websites", "websites"), ("Visited pages", "pages"), ("Queued", "queued"), ("Active lanes", "active"), ("Unique Persian comments", "comments"), ("Requests / sec", "speed"), ("Storage used", "storage"), ("Errors", "errors")):
            card = ttk.Frame(cards, style="Card.TFrame", padding=(15, 12))
            card.pack(side="left", fill="both", expand=True, padx=(0, 8))
            value = ttk.Label(card, text="0", style="CardValue.TLabel")
            value.pack(anchor="w")
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(4, 0))
            self.card_values[key] = value

        monitor = ttk.Frame(content)
        monitor.pack(fill="both", expand=False, padx=24, pady=(14, 0))
        monitor.configure(height=310)
        monitor.pack_propagate(False)

        workers_panel = ttk.Frame(monitor, style="Card.TFrame", padding=14)
        workers_panel.pack(side="left", fill="both", expand=True, padx=(0, 10))
        ttk.Label(workers_panel, text="Live worker lanes", foreground="#d9f27f", background="#173b3f", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(workers_panel, text="One active URL per host; idle lanes are ready for another website.", style="Muted.TLabel").pack(anchor="w", pady=(3, 8))
        self.worker_tree = ttk.Treeview(workers_panel, columns=("lane", "host", "url", "state"), show="headings", height=8)
        for column, heading, width in (("lane", "Lane", 55), ("host", "Website", 155), ("url", "Current URL", 430), ("state", "State", 90)):
            self.worker_tree.heading(column, text=heading)
            self.worker_tree.column(column, width=width, stretch=column in {"host", "url"})
        self.worker_tree.tag_configure("active", background="#214d50", foreground="#d9f27f")
        self.worker_tree.tag_configure("idle", background="#173b3f", foreground="#8aa69b")
        self.worker_tree.pack(fill="both", expand=True)

        monitor_right = ttk.Frame(monitor, style="Card.TFrame", padding=14)
        monitor_right.pack(side="right", fill="both", expand=True)
        ttk.Label(monitor_right, text="Queue control room", foreground="#d9f27f", background="#173b3f", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self.queue_summary = ttk.Label(monitor_right, text="Waiting for the crawler…", style="Muted.TLabel")
        self.queue_summary.pack(anchor="w", pady=(3, 6))
        self.queue_tree = ttk.Treeview(monitor_right, columns=("priority", "domain", "url", "source"), show="headings", height=4)
        for column, heading, width in (("priority", "P", 35), ("domain", "Website", 125), ("url", "Next URL", 330), ("source", "Source", 125)):
            self.queue_tree.heading(column, text=heading)
            self.queue_tree.column(column, width=width, stretch=column == "url")
        self.queue_tree.pack(fill="x", expand=False)
        ttk.Label(monitor_right, text="Focused targets", foreground="#b4ccc2", background="#173b3f", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(9, 3))
        self.focused_tree = ttk.Treeview(monitor_right, columns=("site", "pending", "done", "comments", "errors"), show="headings", height=5)
        for column, heading, width in (("site", "Site", 145), ("pending", "Queued", 65), ("done", "Done", 55), ("comments", "Comments", 75), ("errors", "Errors", 55)):
            self.focused_tree.heading(column, text=heading)
            self.focused_tree.column(column, width=width, stretch=column == "site")
        self.focused_tree.pack(fill="both", expand=True)
        self._render_workers([])

        body = ttk.Frame(content)
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
        ttk.Label(left, text="Optional live-search boost. Common Crawl discovery works without a key; this key is never written to the database.", style="Muted.TLabel", wraplength=400).pack(anchor="w", pady=(0, 11))

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
        # Eight workers can cover many domains in parallel; the scheduler
        # assigns at most one active URL to each domain.
        self.concurrency_var = tk.IntVar(value=8)
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
        self._build_articles_tab()

    def _build_articles_tab(self) -> None:
        """Build the focused article workspace without disturbing comments UI."""
        tab = self.articles_tab
        hero = ttk.Frame(tab, style="Card.TFrame", padding=(18, 15))
        hero.pack(fill="x", pady=(0, 12))
        ttk.Label(hero, text="Article Desk", foreground="#d9f27f", background="#173b3f", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(hero, text="Collect Persian news and editorial pages with title, body, source, author, and publication metadata.", style="Muted.TLabel", wraplength=980).pack(anchor="w", pady=(4, 0))

        cards = ttk.Frame(tab)
        cards.pack(fill="x", pady=(0, 12))
        self.article_card_values: dict[str, ttk.Label] = {}
        for title, key in (("Articles saved", "articles"), ("Pages with articles", "article_pages"), ("Queued pages", "article_queue"), ("Article pages / sec", "article_rate")):
            card = ttk.Frame(cards, style="Card.TFrame", padding=(15, 12))
            card.pack(side="left", fill="both", expand=True, padx=(0, 8))
            value = ttk.Label(card, text="0", style="CardValue.TLabel")
            value.pack(anchor="w")
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(4, 0))
            self.article_card_values[key] = value

        results = ttk.Frame(tab, style="Card.TFrame", padding=14)
        results.pack(fill="both", expand=True)
        top_row = ttk.Frame(results, style="Card.TFrame")
        top_row.pack(fill="x")
        ttk.Label(top_row, text="Latest articles", foreground="#d9f27f", background="#173b3f", font=("Segoe UI", 13, "bold")).pack(side="left")
        self.article_state = ttk.Label(top_row, text="Ready", style="Muted.TLabel")
        self.article_state.pack(side="right")
        self.article_summary = ttk.Label(results, text="No article run has started yet.", style="Muted.TLabel")
        self.article_summary.pack(anchor="w", pady=(4, 8))
        self.article_tree = ttk.Treeview(results, columns=("title", "domain", "published", "words", "url"), show="headings")
        for column, heading, width in (("title", "Title", 390), ("domain", "Website", 145), ("published", "Published", 120), ("words", "Words", 70), ("url", "Source URL", 390)):
            self.article_tree.heading(column, text=heading)
            self.article_tree.column(column, width=width, stretch=column in {"title", "url"})
        self.article_tree.pack(fill="both", expand=True)

        controls = ttk.Frame(tab, style="Card.TFrame", padding=(14, 10))
        controls.pack(fill="x", pady=(12, 0))
        ttk.Label(controls, text="Article mode", style="Muted.TLabel").pack(side="left", padx=(0, 10))
        ttk.Button(controls, text="Start article crawl", style="Start.TButton", command=self._start).pack(side="left", padx=(0, 7))
        ttk.Button(controls, text="Pause", command=self._pause).pack(side="left", padx=(0, 7))
        ttk.Button(controls, text="Resume", command=self._resume).pack(side="left", padx=(0, 7))
        ttk.Button(controls, text="Stop", command=self._stop).pack(side="left", padx=(0, 7))
        ttk.Button(controls, text="Export articles to TXT", command=self._export_articles).pack(side="right")

    def _render_workers(self, workers: list[dict[str, object]]) -> None:
        if not hasattr(self, "worker_tree"):
            return
        active = {int(item.get("lane", 0)): item for item in workers}
        self.worker_tree.delete(*self.worker_tree.get_children())
        slots = self.engine.config.concurrency if self.engine else self.config_model.concurrency
        for lane in range(1, slots + 1):
            item = active.get(lane)
            if item:
                self.worker_tree.insert("", "end", values=(f"W{lane:02d}", item.get("domain", ""), item.get("url", ""), "Fetching"), tags=("active",))
            else:
                self.worker_tree.insert("", "end", values=(f"W{lane:02d}", "—", "Waiting for a website", "Idle"), tags=("idle",))

    def _render_queue(self, payload: dict[str, object]) -> None:
        if not hasattr(self, "queue_tree"):
            return
        pending = int(payload.get("pending", 0))
        active = int(payload.get("active", 0))
        errors = int(payload.get("errors", 0))
        self.queue_summary.configure(text=f"{pending:,} queued  ·  {active:,} active  ·  {errors:,} failed")
        self.queue_tree.delete(*self.queue_tree.get_children())
        for item in payload.get("top", []):
            if isinstance(item, dict):
                self.queue_tree.insert("", "end", values=(item.get("priority", ""), item.get("domain", ""), item.get("url", ""), item.get("discovery_source", "")))
        self.focused_tree.delete(*self.focused_tree.get_children())
        for item in payload.get("focused", []):
            if isinstance(item, dict):
                self.focused_tree.insert("", "end", values=(item.get("domain", ""), item.get("pending", 0), item.get("done", 0), item.get("comments", 0), item.get("errors", 0)))

    def _render_articles(self, payload: object) -> None:
        if not hasattr(self, "article_tree") or not isinstance(payload, list):
            return
        self.article_tree.delete(*self.article_tree.get_children())
        for item in payload:
            if isinstance(item, dict):
                self.article_tree.insert("", "end", values=(
                    item.get("title", ""), item.get("domain", ""), item.get("publication_date", "") or "unknown",
                    item.get("word_count", 0), item.get("source_url", ""),
                ))

    def _on_tab_changed(self, _event=None) -> None:
        if hasattr(self, "article_state"):
            self.article_state.configure(text="Article mode ready" if self._active_mode() == "articles" else "Comments mode selected")

    def _active_mode(self) -> str:
        return "articles" if self.notebook.index(self.notebook.select()) == 1 else "comments"

    def _browse(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.data_var.get(), title="Choose managed data directory")
        if selected:
            self.data_var.set(selected)

    def _config(self) -> CrawlConfig:
        seeds = [line.strip() for line in self.seed_text.get("1.0", "end").splitlines() if line.strip()]
        return CrawlConfig(test_duration_seconds=max(60, self.duration_var.get() * 60), maximum_comments=self.max_comments_var.get(), maximum_articles=self.max_comments_var.get(), content_mode=self._active_mode(), concurrency=self.concurrency_var.get(), auto_ir_discovery=self.auto_ir_var.get(), search_api_key=self.search_key_var.get().strip(), seed_urls=seeds, data_root=Path(self.data_var.get()))

    def _start(self) -> None:
        if self.engine and self.engine.state in {CrawlState.RUNNING, CrawlState.PAUSED, CrawlState.STOPPING}:
            return
        try:
            self.config_model = self._config()
            label = "ARTICLES" if self.config_model.content_mode == "articles" else "COMMENTS"
            self.safe_label.configure(text=f"SAFE TEST · {label} · {self.config_model.test_duration_seconds // 60} MIN · {self.config_model.concurrency} LANES")
            self.store = SQLiteStore(self.config_model.data_root, self.config_model)
            self.engine = CrawlEngine(self.config_model, self.store, lambda event, payload: self.events.put((event, payload)))
            self.events.put(("stats", asdict(self.engine.stats)))
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

    def _export_articles(self) -> None:
        if not self.store:
            messagebox.showinfo("Nothing to export", "Start an article session first so the managed database is available.")
            return
        try:
            outputs = ArticleTxtExporter(self.store, self.config_model).export()
            messagebox.showinfo("Article export complete", f"Wrote {len(outputs)} article TXT file(s) under the managed data folder.")
        except Exception as exc:
            messagebox.showerror("Article export failed", str(exc))

    def _open_folder(self) -> None:
        path = Path(self.data_var.get())
        path.mkdir(parents=True, exist_ok=True)
        import os
        os.startfile(path)  # type: ignore[attr-defined]

    def _load_existing_stats(self) -> None:
        """Hydrate the cards when opening an existing managed data directory."""
        try:
            store = SQLiteStore(self.config_model.data_root, self.config_model)
            counts = store.dashboard_counts()
            articles = store.latest_articles()
            store.close()
            self.events.put(("stats", counts))
            self.events.put(("articles", articles))
        except Exception:
            # A first-run directory has no persisted data yet.
            return

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
                self.card_values["queued"].configure(text=f"{payload.get('pending_urls', 0):,}")
                self.card_values["active"].configure(text=f"{payload.get('active_urls', 0):,}")
                self.card_values["comments"].configure(text=f"{payload.get('unique_comments', 0):,}")
                self.card_values["speed"].configure(text=f"{payload.get('current_speed', 0):.1f}")
                self.card_values["storage"].configure(text=f"{payload.get('storage_bytes', 0) / 1_000_000:.1f} MB")
                self.card_values["errors"].configure(text=f"{payload.get('errors', 0):,}")
                if hasattr(self, "article_card_values"):
                    self.article_card_values["articles"].configure(text=f"{payload.get('unique_articles', 0):,}")
                    self.article_card_values["article_pages"].configure(text=f"{payload.get('pages_with_articles', 0):,}")
                    self.article_card_values["article_queue"].configure(text=f"{payload.get('pending_urls', 0):,}")
                    self.article_card_values["article_rate"].configure(text=f"{payload.get('current_speed', 0):.1f}")
            elif event == "workers" and isinstance(payload, list):
                self._render_workers(payload)
            elif event == "queue" and isinstance(payload, dict):
                self._render_queue(payload)
            elif event == "articles":
                self._render_articles(payload)
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
