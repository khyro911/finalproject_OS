"""
Process & Resource Monitor - desktop window (Tkinter + psutil).

    python app.py

Runs on Linux and Windows. Data is collected on a background thread so the
window stays responsive while the process list refreshes.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

import psutil

import monitor

BLACK = "#000000"
WHITE = "#ffffff"
HISTORY_POINTS = 60
INTERVALS = {"1 s": 1.0, "2 s": 2.0, "5 s": 5.0}


# --------------------------------------------------------------------------
# Background collector
# --------------------------------------------------------------------------

class Collector(threading.Thread):
    """Collects a full snapshot every `interval` seconds and keeps the latest."""

    def __init__(self, interval: float = 2.0):
        super().__init__(daemon=True)
        self.interval = interval
        self.paused = threading.Event()
        self.wake = threading.Event()
        self.latest: dict | None = None
        self.error: str | None = None
        self._lock = threading.Lock()

    def run(self) -> None:
        # Prime the CPU counters so the first real sample is meaningful.
        psutil.cpu_percent(percpu=True)
        monitor.list_processes()
        time.sleep(0.5)
        while True:
            if not self.paused.is_set():
                try:
                    snap = monitor.full_snapshot()
                    with self._lock:
                        self.latest = snap
                        self.error = None
                except Exception as exc:  # keep running, show it in the status bar
                    self.error = str(exc)
            self.wake.wait(self.interval)
            self.wake.clear()

    def take(self) -> dict | None:
        with self._lock:
            snap, self.latest = self.latest, None
        return snap

    def refresh_now(self) -> None:
        self.wake.set()


# --------------------------------------------------------------------------
# Small drawing widgets
# --------------------------------------------------------------------------

class Bar(tk.Canvas):
    """A plain horizontal bar: black outline, black fill."""

    def __init__(self, parent, height=14, **kw):
        super().__init__(parent, height=height, bg=WHITE, highlightthickness=0, **kw)
        self.value = 0.0
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, percent: float) -> None:
        self.value = max(0.0, min(100.0, percent or 0.0))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            return
        self.create_rectangle(0, 0, w - 1, h - 1, outline=BLACK)
        fill = int((w - 4) * self.value / 100)
        if fill > 0:
            self.create_rectangle(2, 2, 2 + fill, h - 3, fill=BLACK, outline="")


class History(tk.Canvas):
    """Line graph of the last N percentages (0-100)."""

    def __init__(self, parent, height=110, **kw):
        super().__init__(parent, height=height, bg=WHITE, highlightthickness=0, **kw)
        self.values: list[float] = []
        self.bind("<Configure>", lambda e: self._draw())

    def push(self, value: float) -> None:
        self.values.append(value)
        del self.values[:-HISTORY_POINTS]
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        self.create_rectangle(0, 0, w - 1, h - 1, outline=BLACK)
        for pct in (25, 50, 75):
            y = h - 1 - (h - 2) * pct / 100
            self.create_line(1, y, w - 1, y, fill=BLACK, dash=(1, 4))
        if len(self.values) < 2:
            return
        step = (w - 2) / (HISTORY_POINTS - 1)
        start = HISTORY_POINTS - len(self.values)
        pts = []
        for i, v in enumerate(self.values):
            pts.append(1 + (start + i) * step)
            pts.append(h - 2 - (h - 4) * max(0.0, min(100.0, v)) / 100)
        self.create_line(*pts, fill=BLACK, width=1.5)


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

PROC_COLUMNS = [
    # key,      heading,     width, anchor
    ("pid",     "PID",         70, "e"),
    ("name",    "Name",       240, "w"),
    ("user",    "User",       130, "w"),
    ("cpu",     "CPU %",       70, "e"),
    ("mem",     "Mem %",       70, "e"),
    ("rss",     "Memory",      90, "e"),
    ("status",  "Status",      90, "w"),
    ("threads", "Threads",     70, "e"),
]

DISK_COLUMNS = [
    ("device",     "Device",     200, "w"),
    ("mountpoint", "Mounted on", 200, "w"),
    ("fstype",     "Type",        80, "w"),
    ("total",      "Total",       90, "e"),
    ("used",       "Used",        90, "e"),
    ("free",       "Free",        90, "e"),
    ("percent",    "Use %",       70, "e"),
]


class MonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Process & Resource Monitor")
        self.geometry("1040x680")
        self.minsize(820, 520)
        self.configure(bg=WHITE)

        self.collector = Collector(interval=2.0)
        self.ui_queue: queue.Queue = queue.Queue()
        self.processes: dict[int, dict] = {}
        self.sort_key = "cpu"
        self.sort_desc = True
        self.core_rows: list[tuple[Bar, ttk.Label]] = []
        self.last_io = None
        self.notice = ("", 0.0)  # (text, show until)

        self._setup_fonts()
        self._setup_style()
        self._build()

        self.collector.start()
        self.after(150, self._poll)

    # ---- look -----------------------------------------------------------

    def _setup_fonts(self) -> None:
        base = tkfont.nametofont("TkDefaultFont")
        family = "Segoe UI" if monitor.IS_WINDOWS else base.actual("family")
        base.configure(family=family, size=10)
        tkfont.nametofont("TkTextFont").configure(family=family, size=10)
        tkfont.nametofont("TkHeadingFont").configure(family=family, size=10, weight="bold")
        self.mono = tkfont.nametofont("TkFixedFont").copy()
        self.mono.configure(size=10)
        self.bold = tkfont.Font(family=family, size=10, weight="bold")
        self.big = tkfont.Font(family=family, size=22)
        self.small = tkfont.Font(family=family, size=9)

    def _setup_style(self) -> None:
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=WHITE, foreground=BLACK, bordercolor=BLACK,
                    darkcolor=WHITE, lightcolor=WHITE, troughcolor=WHITE,
                    focuscolor=BLACK, selectbackground=BLACK,
                    selectforeground=WHITE, fieldbackground=WHITE,
                    insertcolor=BLACK)
        s.configure("TFrame", background=WHITE)
        s.configure("TLabel", background=WHITE, foreground=BLACK)
        s.configure("Big.TLabel", font=self.big)
        s.configure("Bold.TLabel", font=self.bold)
        s.configure("Small.TLabel", font=self.small)
        s.configure("Mono.TLabel", font=self.mono)
        s.configure("Box.TLabelframe", background=WHITE, bordercolor=BLACK, relief="solid", borderwidth=1)
        s.configure("Box.TLabelframe.Label", background=WHITE, foreground=BLACK, font=self.bold)

        s.configure("TButton", background=WHITE, foreground=BLACK, bordercolor=BLACK,
                    relief="solid", borderwidth=1, padding=(10, 3))
        s.map("TButton",
              background=[("pressed", BLACK), ("active", BLACK)],
              foreground=[("pressed", WHITE), ("active", WHITE), ("disabled", "#000000")])

        s.configure("TEntry", fieldbackground=WHITE, foreground=BLACK, bordercolor=BLACK, padding=3)
        s.configure("TCombobox", fieldbackground=WHITE, background=WHITE, foreground=BLACK,
                    arrowcolor=BLACK, bordercolor=BLACK, padding=2)
        s.map("TCombobox", fieldbackground=[("readonly", WHITE)],
              selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", BLACK)])
        self.option_add("*TCombobox*Listbox.background", WHITE)
        self.option_add("*TCombobox*Listbox.foreground", BLACK)
        self.option_add("*TCombobox*Listbox.selectBackground", BLACK)
        self.option_add("*TCombobox*Listbox.selectForeground", WHITE)

        s.configure("TNotebook", background=WHITE, bordercolor=BLACK, tabmargins=(0, 0, 0, 0))
        s.configure("TNotebook.Tab", background=WHITE, foreground=BLACK, bordercolor=BLACK,
                    padding=(14, 4))
        s.map("TNotebook.Tab",
              background=[("selected", BLACK)], foreground=[("selected", WHITE)],
              expand=[("selected", (0, 0, 0, 0))])

        s.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=BLACK,
                    bordercolor=BLACK, rowheight=22, font=self.mono)
        s.map("Treeview", background=[("selected", BLACK)], foreground=[("selected", WHITE)])
        s.configure("Treeview.Heading", background=WHITE, foreground=BLACK, bordercolor=BLACK,
                    relief="solid", borderwidth=1, font=self.bold, padding=(4, 3))
        s.map("Treeview.Heading", background=[("active", WHITE)])

        for orient in ("Vertical", "Horizontal"):
            s.configure(f"{orient}.TScrollbar", background=WHITE, troughcolor=WHITE,
                        bordercolor=BLACK, arrowcolor=BLACK, gripcount=0)
            s.map(f"{orient}.TScrollbar", background=[("active", BLACK)],
                  arrowcolor=[("active", WHITE)])

    # ---- layout ---------------------------------------------------------

    def _build(self) -> None:
        info = monitor.system_info()

        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.pack(fill="x")
        ttk.Label(top, text=info["hostname"], style="Bold.TLabel").pack(side="left")
        ttk.Label(top, text=f"   {info['os']}  ·  {info['architecture']}").pack(side="left")
        self.uptime_lbl = ttk.Label(top, text="")
        self.uptime_lbl.pack(side="left")

        self.pause_btn = ttk.Button(top, text="Pause", command=self._toggle_pause, width=8)
        self.pause_btn.pack(side="right")
        self.interval = tk.StringVar(value="2 s")
        cb = ttk.Combobox(top, textvariable=self.interval, values=list(INTERVALS),
                          state="readonly", width=5)
        cb.pack(side="right", padx=(0, 8))
        cb.bind("<<ComboboxSelected>>", self._interval_changed)
        ttk.Label(top, text="Refresh").pack(side="right", padx=(0, 6))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12)

        self._build_overview()
        self._build_processes()
        self._build_disks()
        self._build_system(info)

        self.status = ttk.Label(self, text="Collecting data...", style="Small.TLabel",
                                padding=(12, 4))
        self.status.pack(fill="x", side="bottom")

    def _build_overview(self) -> None:
        page = ttk.Frame(self.nb, padding=12)
        self.nb.add(page, text="Overview")
        page.columnconfigure(0, weight=1, uniform="half")
        page.columnconfigure(1, weight=1, uniform="half")
        page.rowconfigure(0, weight=1)

        # CPU
        cpu = ttk.LabelFrame(page, text=" CPU ", style="Box.TLabelframe", padding=10)
        cpu.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.cpu_lbl = ttk.Label(cpu, text="-- %", style="Big.TLabel")
        self.cpu_lbl.pack(anchor="w")
        self.cpu_sub = ttk.Label(cpu, text="", style="Small.TLabel")
        self.cpu_sub.pack(anchor="w", pady=(0, 6))
        self.cpu_bar = Bar(cpu)
        self.cpu_bar.pack(fill="x")
        self.cpu_hist = History(cpu)
        self.cpu_hist.pack(fill="both", expand=True, pady=(8, 0))
        ttk.Label(cpu, text="Last 60 samples", style="Small.TLabel").pack(anchor="w")

        # Memory
        mem = ttk.LabelFrame(page, text=" Memory ", style="Box.TLabelframe", padding=10)
        mem.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.mem_lbl = ttk.Label(mem, text="-- %", style="Big.TLabel")
        self.mem_lbl.pack(anchor="w")
        self.mem_sub = ttk.Label(mem, text="", style="Small.TLabel")
        self.mem_sub.pack(anchor="w", pady=(0, 6))
        self.mem_bar = Bar(mem)
        self.mem_bar.pack(fill="x")
        self.mem_hist = History(mem)
        self.mem_hist.pack(fill="both", expand=True, pady=(8, 0))
        self.swap_lbl = ttk.Label(mem, text="", style="Small.TLabel")
        self.swap_lbl.pack(anchor="w")

        # Per-core
        cores = ttk.LabelFrame(page, text=" Per core ", style="Box.TLabelframe", padding=10)
        cores.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.cores_frame = ttk.Frame(cores)
        self.cores_frame.pack(fill="both", expand=True)

    def _build_processes(self) -> None:
        page = ttk.Frame(self.nb, padding=(0, 10, 0, 0))
        self.nb.add(page, text="Processes")

        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Filter").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self._render_processes())
        ttk.Entry(bar, textvariable=self.filter_var, width=28).pack(side="left", padx=(6, 12))
        self.count_lbl = ttk.Label(bar, text="")
        self.count_lbl.pack(side="left")
        self.kill_btn = ttk.Button(bar, text="End process", command=self._end_selected)
        self.kill_btn.pack(side="right")
        ttk.Button(bar, text="Refresh", command=self.collector.refresh_now).pack(side="right", padx=(0, 6))

        wrap = ttk.Frame(page)
        wrap.pack(fill="both", expand=True)
        self.proc_tree = ttk.Treeview(wrap, columns=[c[0] for c in PROC_COLUMNS],
                                      show="headings", selectmode="browse")
        for key, head, width, anchor in PROC_COLUMNS:
            self.proc_tree.heading(key, text=head, command=lambda k=key: self._sort_by(k))
            self.proc_tree.column(key, width=width, anchor=anchor,
                                  stretch=(key == "name"), minwidth=50)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.proc_tree.yview)
        self.proc_tree.configure(yscrollcommand=vs.set)
        self.proc_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.proc_tree.bind("<Delete>", lambda e: self._end_selected())
        self._update_sort_headings()

    def _build_disks(self) -> None:
        page = ttk.Frame(self.nb, padding=(0, 10, 0, 0))
        self.nb.add(page, text="Disks")
        self.disk_tree = ttk.Treeview(page, columns=[c[0] for c in DISK_COLUMNS],
                                      show="headings", selectmode="browse", height=8)
        for key, head, width, anchor in DISK_COLUMNS:
            self.disk_tree.heading(key, text=head)
            self.disk_tree.column(key, width=width, anchor=anchor, stretch=key in ("device", "mountpoint"))
        self.disk_tree.pack(fill="x")

        self.disk_bars = ttk.Frame(page, padding=(0, 14, 0, 0))
        self.disk_bars.pack(fill="x")
        self.io_lbl = ttk.Label(page, text="", style="Small.TLabel", padding=(0, 10, 0, 0))
        self.io_lbl.pack(anchor="w")

    def _build_system(self, info: dict) -> None:
        page = ttk.Frame(self.nb, padding=16)
        self.nb.add(page, text="System")
        rows = [
            ("Hostname", info["hostname"]),
            ("Operating system", info["os"]),
            ("OS version", info["os_version"]),
            ("Architecture", info["architecture"]),
            ("Processor", info["processor"]),
            ("Physical cores", info["physical_cores"]),
            ("Logical cores", info["logical_cores"]),
            ("Max frequency", f"{info['max_frequency_mhz']} MHz" if info["max_frequency_mhz"] else "-"),
            ("Total RAM", monitor.human_bytes(info["total_ram"])),
            ("Boot time", info["boot_time"]),
            ("Python", info["python"]),
            ("psutil", info["psutil"]),
        ]
        for i, (k, v) in enumerate(rows):
            ttk.Label(page, text=k, style="Bold.TLabel").grid(row=i, column=0, sticky="w", pady=3, padx=(0, 24))
            ttk.Label(page, text=str(v)).grid(row=i, column=1, sticky="w", pady=3)

    # ---- update loop ----------------------------------------------------

    def _poll(self) -> None:
        while not self.ui_queue.empty():
            self.ui_queue.get_nowait()()

        snap = self.collector.take()
        if snap is not None:
            self._apply(snap)
        elif self.collector.error:
            self.status.configure(text=f"Error: {self.collector.error}")
        self.after(150, self._poll)

    def _apply(self, snap: dict) -> None:
        cpu, mem = snap["cpu"], snap["memory"]

        self.uptime_lbl.configure(text=f"  ·  up {snap['uptime']}")

        self.cpu_lbl.configure(text=f"{cpu['total']:.1f} %")
        sub = f"{len(cpu['per_core'])} logical cores"
        if cpu["freq_mhz"]:
            sub += f"  ·  {cpu['freq_mhz']:.0f} MHz"
        if cpu["load_avg"]:
            sub += "  ·  load " + "  ".join(f"{x:.2f}" for x in cpu["load_avg"])
        self.cpu_sub.configure(text=sub)
        self.cpu_bar.set(cpu["total"])
        self.cpu_hist.push(cpu["total"])
        self._update_cores(cpu["per_core"])

        self.mem_lbl.configure(text=f"{mem['percent']:.1f} %")
        self.mem_sub.configure(text=f"{monitor.human_bytes(mem['used'])} used of "
                                    f"{monitor.human_bytes(mem['total'])}  ·  "
                                    f"{monitor.human_bytes(mem['available'])} available")
        self.mem_bar.set(mem["percent"])
        self.mem_hist.push(mem["percent"])
        self.swap_lbl.configure(text=f"Swap  {monitor.human_bytes(mem['swap_used'])} / "
                                     f"{monitor.human_bytes(mem['swap_total'])}  ({mem['swap_percent']:.0f} %)")

        self.processes = {p["pid"]: p for p in snap["processes"]}
        self._render_processes()
        self._render_disks(snap["disks"], snap["disk_io"], snap["time"])

        text = f"Updated {time.strftime('%H:%M:%S', time.localtime(snap['time']))}" \
               f"  ·  {len(self.processes)} processes"
        if time.time() < self.notice[1]:
            text = f"{self.notice[0]}  ·  {text}"
        self.status.configure(text=text)

    def _update_cores(self, per_core: list[float]) -> None:
        if len(self.core_rows) != len(per_core):
            for child in self.cores_frame.winfo_children():
                child.destroy()
            self.core_rows = []
            cols = 4 if len(per_core) > 8 else 2
            for c in range(cols):
                self.cores_frame.columnconfigure(c * 3 + 1, weight=1)
            for i in range(len(per_core)):
                r, c = divmod(i, cols)
                ttk.Label(self.cores_frame, text=f"Core {i}", style="Mono.TLabel", width=8).grid(
                    row=r, column=c * 3, sticky="w", pady=2)
                bar = Bar(self.cores_frame, height=12)
                bar.grid(row=r, column=c * 3 + 1, sticky="ew", pady=2)
                lbl = ttk.Label(self.cores_frame, text="", style="Mono.TLabel", width=7, anchor="e")
                lbl.grid(row=r, column=c * 3 + 2, sticky="e", padx=(4, 16), pady=2)
                self.core_rows.append((bar, lbl))
        for (bar, lbl), value in zip(self.core_rows, per_core):
            bar.set(value)
            lbl.configure(text=f"{value:5.1f}%")

    def _render_processes(self) -> None:
        tree = self.proc_tree
        text = self.filter_var.get().strip().lower()
        rows = [p for p in self.processes.values()
                if not text or text in p["name"].lower() or text in p["user"].lower()
                or text == str(p["pid"])]

        key = self.sort_key
        if key in ("name", "user", "status"):
            rows.sort(key=lambda p: p[key].lower(), reverse=self.sort_desc)
        else:
            rows.sort(key=lambda p: p[key] or 0, reverse=self.sort_desc)

        wanted = {str(p["pid"]) for p in rows}
        for iid in tree.get_children():
            if iid not in wanted:
                tree.delete(iid)
        existing = set(tree.get_children())
        for index, p in enumerate(rows):
            iid = str(p["pid"])
            values = (p["pid"], p["name"], p["user"], f"{p['cpu']:.1f}", f"{p['mem']:.1f}",
                      monitor.human_bytes(p["rss"]), p["status"], p["threads"])
            if iid in existing:
                tree.item(iid, values=values)
                tree.move(iid, "", index)
            else:
                tree.insert("", index, iid=iid, values=values)

        shown = len(rows)
        total = len(self.processes)
        self.count_lbl.configure(text=f"{shown} of {total}" if text else f"{total} processes")

    def _render_disks(self, disks: list[dict], io: dict | None, now: float) -> None:
        tree = self.disk_tree
        tree.delete(*tree.get_children())
        tree.configure(height=max(1, min(len(disks), 10)))
        for d in disks:
            pct = f"{d['percent']:.0f} %" if d["percent"] is not None else "not ready"
            tree.insert("", "end", values=(d["device"], d["mountpoint"], d["fstype"],
                                           monitor.human_bytes(d["total"]),
                                           monitor.human_bytes(d["used"]),
                                           monitor.human_bytes(d["free"]), pct))

        # One bar per disk under the table
        current = [d for d in disks if d["percent"] is not None]
        children = self.disk_bars.winfo_children()
        if len(children) != len(current) * 3:
            for c in children:
                c.destroy()
            self.disk_bars.columnconfigure(1, weight=1)
            for i, d in enumerate(current):
                ttk.Label(self.disk_bars, text=d["mountpoint"], style="Mono.TLabel").grid(
                    row=i, column=0, sticky="w", padx=(0, 12), pady=3)
                Bar(self.disk_bars).grid(row=i, column=1, sticky="ew", pady=3)
                ttk.Label(self.disk_bars, text="", style="Mono.TLabel", width=24, anchor="e").grid(
                    row=i, column=2, sticky="e", padx=(12, 0), pady=3)
        widgets = self.disk_bars.winfo_children()
        for i, d in enumerate(current):
            _, bar, lbl = widgets[i * 3: i * 3 + 3]
            bar.set(d["percent"])
            lbl.configure(text=f"{monitor.human_bytes(d['free'])} free  {d['percent']:.0f}%")

        if io:
            text = f"Total since boot  ·  read {monitor.human_bytes(io['read_bytes'])}  ·  " \
                   f"written {monitor.human_bytes(io['write_bytes'])}"
            if self.last_io:
                prev_io, prev_t = self.last_io
                dt = max(now - prev_t, 0.001)
                r = (io["read_bytes"] - prev_io["read_bytes"]) / dt
                w = (io["write_bytes"] - prev_io["write_bytes"]) / dt
                text += f"      Now  ·  read {monitor.human_bytes(r)}/s  ·  write {monitor.human_bytes(w)}/s"
            self.io_lbl.configure(text=text)
            self.last_io = (io, now)

    # ---- actions --------------------------------------------------------

    def _sort_by(self, key: str) -> None:
        if self.sort_key == key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key = key
            self.sort_desc = key not in ("name", "user", "status", "pid")
        self._update_sort_headings()
        self._render_processes()

    def _update_sort_headings(self) -> None:
        for key, head, _, _ in PROC_COLUMNS:
            mark = (" ▼" if self.sort_desc else " ▲") if key == self.sort_key else ""
            self.proc_tree.heading(key, text=head + mark)

    def _toggle_pause(self) -> None:
        if self.collector.paused.is_set():
            self.collector.paused.clear()
            self.collector.refresh_now()
            self.pause_btn.configure(text="Pause")
            self.status.configure(text="Resumed")
        else:
            self.collector.paused.set()
            self.pause_btn.configure(text="Resume")
            self.status.configure(text="Paused")

    def _interval_changed(self, _event=None) -> None:
        self.collector.interval = INTERVALS[self.interval.get()]
        self.collector.refresh_now()

    def _end_selected(self) -> None:
        sel = self.proc_tree.selection()
        if not sel:
            messagebox.showinfo("End process", "Select a process in the list first.", parent=self)
            return
        pid = int(sel[0])
        proc = self.processes.get(pid)
        if proc is None:
            return
        name = proc["name"]

        check = monitor.check_can_terminate(pid, name)
        if not check.allowed:
            messagebox.showwarning("Not allowed", f"{name} (PID {pid}) cannot be ended.\n\n{check.reason}",
                                   parent=self)
            return

        user = f"\nUser: {proc['user']}" if proc["user"] else ""
        ok = messagebox.askyesno(
            "End process",
            f"End {name}?\n\nPID: {pid}{user}\nMemory: {monitor.human_bytes(proc['rss'])}\n\n"
            "Any unsaved work in this program will be lost.",
            icon="warning", default="no", parent=self)
        if not ok:
            return
        self._terminate(pid, name, proc["create_time"], force=False)

    def _terminate(self, pid: int, name: str, create_time, force: bool) -> None:
        try:
            handle = monitor.terminate_process(pid, create_time, force=force)
        except monitor.TerminateError as exc:
            messagebox.showerror("End process", f"Could not end {name} (PID {pid}).\n\n{exc}", parent=self)
            return
        self.status.configure(text=f"Waiting for {name} (PID {pid}) to exit...")

        def wait():
            gone = monitor.wait_for_exit(handle, timeout=3.0)
            self.ui_queue.put(lambda: self._after_terminate(pid, name, create_time, gone, force))

        threading.Thread(target=wait, daemon=True).start()

    def _after_terminate(self, pid, name, create_time, gone, was_forced) -> None:
        if gone:
            self.notice = (f"Ended {name} (PID {pid})", time.time() + 6)
            self.status.configure(text=self.notice[0])
            self.collector.refresh_now()
            return
        if was_forced:
            messagebox.showerror("End process", f"{name} (PID {pid}) is still running.", parent=self)
            return
        again = messagebox.askyesno(
            "Not responding",
            f"{name} (PID {pid}) did not exit within 3 seconds.\n\nForce it to stop?",
            icon="warning", default="no", parent=self)
        if again:
            self._terminate(pid, name, create_time, force=True)


def main() -> None:
    if monitor.IS_WINDOWS:
        try:  # sharp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = MonitorApp()
    app.mainloop()


if __name__ == "__main__":
    if "--text" in sys.argv:
        monitor.print_report()
    else:
        main()
