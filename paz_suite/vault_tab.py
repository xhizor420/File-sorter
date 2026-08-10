"""The Vault tab: paste a list of post IDs (or filenames) to find them in
the library, then mark the ones you actually used in a named project.

Grabber downloads and finished PMVs both leave you with a pile of numeric
filenames (428483.mp4) and no memory of which ones already went into an
edit. This tab exists for the moment after you finish a project: paste
whatever list you have, find them here, mark them under a project name,
and from then on the Library gallery shows a coloured border on anything
you've already spent - a clip can carry marks from more than one project,
since reused footage across separate edits is normal.

No ffmpeg, no network - everything here is a handful of fast local SQLite
reads/writes, so unlike the other two tabs this one has no background
threads at all.
"""

from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk

from .theme import T, font, VAULT_LABELS
from .format import fmt_len
from .library_db import (
    db_connect, vault_ensure_project, vault_mark,
    vault_clear_project, vault_rename_project, vault_projects_list,
)
from .library_windows import HelpWindow

_SPLIT_RE = re.compile(r"[,\n\r\t;]+|\s+")


def split_terms(text: str) -> list:
    """Pasted list -> individual lookup terms. Commas, semicolons, newlines
    and plain whitespace all work as separators, and order/duplicates in
    the input don't matter for matching."""
    seen = set()
    terms = []
    for raw in _SPLIT_RE.split(text):
        term = raw.strip()
        if term and term not in seen:
            seen.add(term)
            terms.append(term)
    return terms


class VaultTab(ctk.CTkFrame):

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=T.BG, corner_radius=0)
        self.pack(fill="both", expand=True)

        self.app = app
        self.root = app.root
        self.cfg = app.cfg

        self._results: dict = {}   # tree iid -> Rec
        self._unmatched: list = []

        self.grid_columnconfigure(0, weight=2, uniform="cols")
        self.grid_columnconfigure(1, weight=1, uniform="cols")
        self.grid_rowconfigure(1, weight=1)

        self._build()
        self._refresh_projects()
        self.set_status(self.F("empty"), T.FAINT)

    # ── copy ─────────────────────────────────────────────────────────────

    def F(self, key: str, **fmt) -> str:
        text = VAULT_LABELS[key]
        return text.format(**fmt) if fmt else text

    # ── layout ──────────────────────────────────────────────────────────

    def _build(self):
        self._build_topbar()
        self._build_lookup()
        self._build_projects()

    def _build_topbar(self):
        bar = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=0, height=58)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=20, pady=10)
        ctk.CTkLabel(left, text="PAZ", font=font(19, "bold"),
                     text_color=T.ACCENT3).pack(side="left")
        ctk.CTkLabel(left, text="Vault", font=font(19), text_color=T.TEXT
                     ).pack(side="left", padx=(5, 0))
        ctk.CTkLabel(left, text=self.F("tagline"), font=font(10, mono=True),
                     text_color=T.FAINT).pack(side="left", padx=(12, 0), pady=(6, 0))

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e", padx=20)
        ctk.CTkButton(right, text="?", width=30, height=30, corner_radius=7,
                     font=font(12, "bold"), fg_color=T.BTN, hover_color=T.BTN_HOV,
                     text_color=T.FAINT, command=self._open_help).pack(side="left")

    def _build_lookup(self):
        panel = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        panel.grid(row=1, column=0, sticky="nsew", padx=(14, 7), pady=14)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(panel, text="PASTE POST IDS OR FILENAMES", font=font(9, "bold"),
                     text_color=T.FAINT, anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.paste_box = ctk.CTkTextbox(
            panel, height=110, corner_radius=10, fg_color=T.SURFACE,
            border_width=1, border_color=T.ACCENT3_DEEP, text_color=T.TEXT,
            font=font(11, mono=True))
        self.paste_box.grid(row=1, column=0, sticky="ew")

        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", pady=(8, 10))
        ctk.CTkButton(row, text="Look up", width=100, height=32, corner_radius=7,
                      font=font(11, "bold"), fg_color=T.ACCENT3_DEEP, hover_color=T.BTN_HOV,
                      text_color=T.ACCENT3, command=self._run_lookup).pack(side="left")
        ctk.CTkButton(row, text="Clear", width=80, height=32, corner_radius=7,
                      font=font(11), fg_color=T.BTN, hover_color=T.BTN_HOV,
                      text_color=T.DIM, command=self._clear_lookup).pack(side="left", padx=(8, 0))
        ctk.CTkButton(row, text="Select all", width=90, height=32, corner_radius=7,
                      font=font(11), fg_color=T.BTN, hover_color=T.BTN_HOV,
                      text_color=T.DIM, command=self._select_all).pack(side="left", padx=(8, 0))
        ctk.CTkButton(row, text="Select none", width=100, height=32, corner_radius=7,
                      font=font(11), fg_color=T.BTN, hover_color=T.BTN_HOV,
                      text_color=T.DIM, command=self._select_none).pack(side="left", padx=(8, 0))

        self.status_label = ctk.CTkLabel(panel, text="", font=font(10),
                                         text_color=T.DIM, anchor="w", justify="left",
                                         wraplength=560)
        self.status_label.grid(row=2, column=0, sticky="e")

        self._build_style()
        tree_wrap = ctk.CTkFrame(panel, fg_color=T.SURFACE, corner_radius=12,
                                 border_width=1, border_color=T.ACCENT3_DEEP)
        tree_wrap.grid(row=3, column=0, sticky="nsew")
        tree_wrap.grid_columnconfigure(0, weight=1)
        tree_wrap.grid_rowconfigure(0, weight=1)

        columns = ("name", "artist", "res", "len", "used")
        self.tree = ttk.Treeview(tree_wrap, style="V.Treeview", columns=columns,
                                 show="headings", selectmode="extended")
        for key, title, width, anchor in (
                ("name", "File", 220, "w"), ("artist", "Artist", 130, "w"),
                ("res", "Resolution", 90, "w"), ("len", "Length", 64, "e"),
                ("used", "Already used in", 190, "w")):
            self.tree.column(key, width=width, minwidth=60, anchor=anchor)
            self.tree.heading(key, text=title)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scroll = ctk.CTkScrollbar(tree_wrap, command=self.tree.yview, width=12,
                                  button_color=T.LINE, button_hover_color=T.FAINT)
        scroll.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=8)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda e: self._open_selected())

        mark_row = ctk.CTkFrame(panel, fg_color="transparent")
        mark_row.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ctk.CTkLabel(mark_row, text="Project:", font=font(11), text_color=T.DIM
                     ).pack(side="left", padx=(0, 6))
        self.project_box = ctk.CTkComboBox(
            mark_row, width=220, height=32, corner_radius=7, font=font(11),
            fg_color=T.INPUT, border_color=T.ACCENT3_DEEP, button_color=T.LINE,
            button_hover_color=T.BTN_HOV, dropdown_fg_color=T.ELEVATED,
            text_color=T.TEXT, values=[])
        self.project_box.pack(side="left")
        self.project_box.set("")
        ctk.CTkButton(mark_row, text="Mark selected as used", height=32, corner_radius=7,
                      font=font(11, "bold"), fg_color=T.ACCENT3_DEEP, hover_color=T.BTN_HOV,
                      text_color=T.ACCENT3, command=self._mark_selected
                      ).pack(side="left", padx=(10, 0))

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("V.Treeview", background=T.ROW, fieldbackground=T.ROW,
                        foreground=T.TEXT, rowheight=26, borderwidth=0, font=(T.UI, 10))
        style.configure("V.Treeview.Heading", background=T.ELEVATED, foreground=T.FAINT,
                        relief="flat", borderwidth=0, font=(T.UI, 9, "bold"), padding=(8, 7))
        style.map("V.Treeview.Heading", background=[("active", T.BTN_HOV)])
        style.map("V.Treeview", background=[("selected", T.ROW_SEL)],
                  foreground=[("selected", T.TEXT)])

    def _build_projects(self):
        panel = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        panel.grid(row=1, column=1, sticky="nsew", padx=(7, 14), pady=14)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(panel, text="PROJECTS · click to browse in Library",
                     font=font(9, "bold"), text_color=T.FAINT, anchor="w"
                     ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.projects_list = ctk.CTkScrollableFrame(
            panel, fg_color=T.SURFACE, corner_radius=12, border_width=1,
            border_color=T.ACCENT3_DEEP, scrollbar_button_color=T.LINE,
            scrollbar_button_hover_color=T.FAINT)
        self.projects_list.grid(row=1, column=0, sticky="nsew")
        self.projects_list.grid_columnconfigure(0, weight=1)

    # ── status ──────────────────────────────────────────────────────────

    def set_status(self, text: str, colour: str = T.DIM) -> None:
        self.status_label.configure(text=text, text_color=colour)

    # ── lookup ──────────────────────────────────────────────────────────

    def _clear_lookup(self) -> None:
        self.paste_box.delete("1.0", tk.END)
        self.tree.delete(*self.tree.get_children())
        self._results = {}
        self._unmatched = []
        self.set_status(self.F("empty"), T.FAINT)

    def _run_lookup(self) -> None:
        terms = split_terms(self.paste_box.get("1.0", tk.END))
        self.tree.delete(*self.tree.get_children())
        self._results = {}
        if not terms:
            self.set_status(self.F("empty"), T.FAINT)
            return

        library = getattr(self.app, "library", None)
        records = library.records if library else []
        if not records:
            self.set_status("No library indexed yet - sync the Library tab first.", T.WARN)
            return

        by_pid = {r.pid: r for r in records if r.pid}
        matched: list = []
        matched_paths: set = set()
        self._unmatched = []
        for term in terms:
            stem = os.path.splitext(term)[0]
            rec = by_pid.get(stem) or by_pid.get(term)
            if rec is None and not stem.isdigit():
                needle = stem.lower()
                for candidate in records:
                    if needle in candidate.name.lower() and candidate.path not in matched_paths:
                        matched.append(candidate)
                        matched_paths.add(candidate.path)
                continue
            if rec is None:
                self._unmatched.append(term)
                continue
            if rec.path not in matched_paths:
                matched.append(rec)
                matched_paths.add(rec.path)

        for index, rec in enumerate(matched):
            iid = f"v{index}"
            self._results[iid] = rec
            used = ", ".join(rec.used_projects) if rec.used_projects else "--"
            self.tree.insert("", "end", iid=iid, values=(
                rec.name, ", ".join(rec.artists[:2]) or "--",
                f"{rec.width}x{rec.height}" if rec.width else "--",
                fmt_len(rec.duration), used))

        bits = [f"{len(matched)} found"]
        if self._unmatched:
            shown = ", ".join(self._unmatched[:12])
            more = f" (+{len(self._unmatched) - 12} more)" if len(self._unmatched) > 12 else ""
            bits.append(f"{len(self._unmatched)} not found: {shown}{more}")
        self.set_status(" · ".join(bits), T.OK if matched else T.WARN)

    def _select_all(self) -> None:
        self.tree.selection_set(list(self._results.keys()))

    def _select_none(self) -> None:
        self.tree.selection_remove(*self.tree.selection())

    def _open_selected(self) -> None:
        iid = self.tree.focus()
        rec = self._results.get(iid)
        if not rec:
            return
        from .files import open_file
        open_file(rec.path)

    def _open_help(self) -> None:
        HelpWindow(self.root)

    # ── marking ─────────────────────────────────────────────────────────

    def _next_color(self, conn) -> str:
        existing = len(vault_projects_list(conn))
        return T.PROJECT_PALETTE[existing % len(T.PROJECT_PALETTE)]

    def _mark_selected(self) -> None:
        project = self.project_box.get().strip()
        if not project:
            self.set_status("Name the project first - type a new one or pick "
                            "an existing one.", T.WARN)
            return
        selected = [self._results[iid] for iid in self.tree.selection() if iid in self._results]
        if not selected:
            self.set_status("Select at least one clip in the results first.", T.WARN)
            return

        conn = db_connect()
        try:
            existing_names = {name for name, _c, _n, _t in vault_projects_list(conn)}
            if project not in existing_names:
                vault_ensure_project(conn, project, self._next_color(conn))
            vault_mark(conn, [rec.path for rec in selected], project)
        finally:
            conn.close()

        self._reload_library()
        self._refresh_projects()
        self.set_status(f"Marked {len(selected)} clip{'s' if len(selected) != 1 else ''} "
                        f"as used in '{project}'.", T.OK)

    def _reload_library(self) -> None:
        library = getattr(self.app, "library", None)
        if library is None:
            return
        library._load_library()
        library.run_search()
        # Rec objects are rebuilt fresh by _load_library(), so the results
        # table's references would otherwise go stale - re-run the same
        # lookup to pick up the new used_projects/used_color.
        if self._results:
            self._run_lookup()

    # ── projects panel ──────────────────────────────────────────────────

    def _refresh_projects(self) -> None:
        for child in self.projects_list.winfo_children():
            child.destroy()
        conn = db_connect()
        try:
            projects = vault_projects_list(conn)
        finally:
            conn.close()

        self.project_box.configure(values=[name for name, _c, _n, _t in projects])

        if not projects:
            ctk.CTkLabel(self.projects_list, text="No projects yet - mark some clips "
                                                   "as used to start one.",
                         font=font(11), text_color=T.FAINT, wraplength=260,
                         justify="left").grid(row=0, column=0, padx=10, pady=10, sticky="w")
            return

        for row, (name, color, count, _created) in enumerate(projects):
            card = ctk.CTkFrame(self.projects_list, fg_color=T.ELEVATED, corner_radius=8)
            card.grid(row=row, column=0, sticky="ew", padx=6, pady=4)
            card.grid_columnconfigure(1, weight=1)

            swatch = ctk.CTkFrame(card, width=8, fg_color=color, corner_radius=4)
            swatch.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(8, 8), pady=8)

            label = ctk.CTkButton(
                card, text=name, font=font(12, "bold"), anchor="w", height=22,
                fg_color="transparent", hover_color=T.BTN_HOV, text_color=T.TEXT,
                command=lambda n=name: self._open_in_library(n))
            label.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
            ctk.CTkLabel(card, text=f"{count} clip{'s' if count != 1 else ''}",
                        font=font(9, mono=True), text_color=T.FAINT, anchor="w"
                        ).grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(0, 6))

            actions = ctk.CTkFrame(card, fg_color="transparent")
            actions.grid(row=0, column=2, rowspan=2, padx=(0, 6))
            ctk.CTkButton(actions, text="Rename", width=56, height=22, corner_radius=5,
                         font=font(9), fg_color=T.BTN, hover_color=T.BTN_HOV,
                         text_color=T.DIM, command=lambda n=name: self._rename_project(n)
                         ).pack(side="top", pady=(0, 3))
            ctk.CTkButton(actions, text="Clear", width=56, height=22, corner_radius=5,
                         font=font(9), fg_color=T.BTN, hover_color=T.FAIL_DEEP,
                         text_color=T.FAIL, command=lambda n=name: self._clear_project(n)
                         ).pack(side="top")

    def _open_in_library(self, project: str) -> None:
        library = getattr(self.app, "library", None)
        if library is None:
            return
        library.search.delete(0, tk.END)
        library.search.insert(0, f'used:"{project}"')
        library.run_search()
        self.app.tabview.set("Library")

    def _rename_project(self, name: str) -> None:
        dialog = ctk.CTkInputDialog(text=f"Rename '{name}' to:", title="Rename project")
        new_name = (dialog.get_input() or "").strip()
        if not new_name or new_name == name:
            return
        conn = db_connect()
        try:
            vault_rename_project(conn, name, new_name)
        finally:
            conn.close()
        self._reload_library()
        self._refresh_projects()
        self.set_status(f"Renamed '{name}' to '{new_name}'.", T.OK)

    def _clear_project(self, name: str) -> None:
        if not messagebox.askyesno(
                "Clear project",
                f"Remove every 'used in {name}' mark? This only clears the "
                "mark - the clips themselves are untouched.", parent=self):
            return
        conn = db_connect()
        try:
            vault_clear_project(conn, name)
        finally:
            conn.close()
        self._reload_library()
        self._refresh_projects()
        self.set_status(f"Cleared '{name}'.", T.OK)

    # ── keyboard / lifecycle (dispatched centrally by the app) ─────────

    @staticmethod
    def is_typing(event) -> bool:
        return isinstance(event.widget, (ctk.CTkEntry, tk.Entry, tk.Text, ttk.Entry))

    def key_lookup(self, event=None) -> None:
        self._run_lookup()

    def on_app_close(self) -> bool:
        return True

    def after_settings_saved(self) -> None:
        pass
