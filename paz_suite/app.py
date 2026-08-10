"""PAZ Suite application shell: one window, a Convert/Library tabview, and
the shared services (config, e621 cache, thumbnail cache, toasts, hover
peek) that both tabs draw on. Also owns the keyboard-shortcut dispatch,
since several shortcuts mean different things on each tab and must only
fire for whichever one is currently visible.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageTk

from .theme import T, paw_photo
from .config import AppConfig
from .e621 import E621Meta, APP_NAME, APP_VERSION
from .media import ThumbCache
from .widgets import Toaster, PeekWindow
from .convert_tab import ConvertTab
from .library_tab import LibraryTab
from .settings_window import SettingsWindow

TAB_NAMES = ("Convert", "Library")


class PazApp:

    def __init__(self, root: ctk.CTk):
        self.root = root
        self.cfg = AppConfig.load()
        self.emeta = E621Meta()
        self.cache = ThumbCache()          # shared frame/thumbnail cache
        self.toaster = Toaster(root)
        self.peek = PeekWindow(root)
        self._icon_paw = None
        self._icon_neutral = None

        root.geometry("1760x1020")
        root.minsize(1280, 760)
        root.configure(fg_color=T.BG)

        self.tabview = ctk.CTkTabview(
            root, fg_color=T.BG, corner_radius=0,
            segmented_button_fg_color=T.SURFACE,
            segmented_button_selected_color=T.ACCENT_DEEP,
            segmented_button_selected_hover_color=T.ACCENT_DEEP,
            segmented_button_unselected_color=T.SURFACE,
            segmented_button_unselected_hover_color=T.BTN_HOV,
            text_color=T.TEXT, command=self._on_tab_changed)
        self.tabview.pack(fill="both", expand=True)
        for name in TAB_NAMES:
            self.tabview.add(name)

        self.convert = ConvertTab(self.tabview.tab("Convert"), self)
        self.library = LibraryTab(self.tabview.tab("Library"), self)

        if self.cfg.last_tab in TAB_NAMES:
            self.tabview.set(self.cfg.last_tab)

        self._apply_chrome()
        self._bind_keys()
        root.bind("<Configure>", self._on_root_configure, add="+")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── chrome (title/icon), shared discreet + boss-key toggles ────────────

    def _apply_chrome(self) -> None:
        self.root.title(self.cfg.neutral_title if self.cfg.discreet
                        else f"{APP_NAME}  {APP_VERSION}")
        try:
            if self.cfg.discreet:
                if self._icon_neutral is None:
                    blank = Image.new("RGBA", (32, 32), (26, 22, 36, 255))
                    self._icon_neutral = ImageTk.PhotoImage(blank)
                self.root.iconphoto(False, self._icon_neutral)
            else:
                if self._icon_paw is None:
                    self._icon_paw = paw_photo(32, T.ACCENT)
                self.root.iconphoto(False, self._icon_paw)
        except tk.TclError:
            pass

    def _toggle_discreet(self) -> None:
        self.cfg.discreet = not self.cfg.discreet
        self.cfg.save()
        self._apply_chrome()
        self.peek.hide()
        self.convert.on_discreet_changed()
        self.library.on_discreet_changed()

    def _boss(self) -> None:
        self.peek.hide()
        self.toaster.hide()
        self.root.title(self.cfg.neutral_title)
        self.root.iconify()
        self.convert.on_boss_key()
        self.library.on_boss_key()

    def _on_tab_changed(self) -> None:
        self.cfg.last_tab = self.tabview.get()
        self.cfg.save()

    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        self.library.on_root_resize()

    # ── settings ─────────────────────────────────────────────────────────

    def open_settings(self, initial_tab: str = "Encoding") -> None:
        SettingsWindow(self.root, self, initial_tab=initial_tab)

    def on_settings_saved(self) -> None:
        self._apply_chrome()
        self.convert.after_settings_saved()
        self.library.after_settings_saved()

    # ── keyboard dispatch ────────────────────────────────────────────────
    #
    # Convert and Library each bind their own row/tree-level shortcuts
    # locally (unaffected here). Everything below used to be bound
    # separately on each app's own root window; sharing one root means a
    # key like Escape or Space means something different depending on
    # which tab is showing, so every shared shortcut is dispatched by the
    # currently active tab instead of being bound twice.

    def _active(self) -> str:
        return self.tabview.get()

    def _bind_keys(self) -> None:
        root = self.root
        root.bind("<Control-d>", lambda e: self._toggle_discreet())
        root.bind("<F12>", lambda e: self._boss())

        root.bind("<Escape>", lambda e: (
            self.convert.key_stop() if self._active() == "Convert"
            else self.library.key_escape(e)))
        root.bind("<space>", lambda e: (
            self.convert.key_space(e) if self._active() == "Convert"
            else self.library.key_space(e)))
        root.bind("<F5>", lambda e: (
            self.convert.key_scan() if self._active() == "Convert"
            else self.library.key_sync()))
        root.bind("<Control-f>", lambda e: (
            self.convert.key_find_search() if self._active() == "Convert"
            else self.library.key_find_search(e)))
        root.bind("<Left>", self._left)
        root.bind("<Right>", self._right)

        # Convert-only
        root.bind("<Control-Return>", lambda e: self._only("Convert", self.convert.key_start))
        for key in ("g", "G"):
            root.bind(key, lambda e: self._only_evt("Convert", self.convert.key_grid, e))
        for key in ("h", "H"):
            root.bind(key, lambda e: self._only_evt("Convert", self.convert.key_peek_toggle, e))
        root.bind("<Shift-Left>", lambda e: self._only(
            "Convert", lambda: self.convert.key_scrub(e, -10)))
        root.bind("<Shift-Right>", lambda e: self._only(
            "Convert", lambda: self.convert.key_scrub(e, 10)))

        # Library-only
        for key in ("p", "P"):
            root.bind(key, lambda e: self._only_evt("Library", self.library.key_pick, e))
        for key in ("r", "R"):
            root.bind(key, lambda e: self._only_evt("Library", self.library.key_random, e))
        for key in ("1", "2", "3", "4"):
            root.bind(key, lambda e: self._only_evt("Library", self.library.key_size, e))
        root.bind("<Return>", lambda e: self._only_evt("Library", self.library.key_play, e))
        root.bind("<Control-o>", lambda e: self._only("Library", self.library.key_open_folders))
        root.bind("<Control-b>", lambda e: self._only("Library", self.library.key_open_beats))
        root.bind("<Control-l>", lambda e: self._only("Library", self.library.key_toggle_sidebar))
        root.bind("<Control-t>", lambda e: self._only("Library", self.library.key_toggle_theater))
        root.bind("<Control-Shift-R>",
                  lambda e: self._only("Library", self.library.key_full_rebuild))
        root.bind("<Prior>", lambda e: self._only("Library", lambda: self.library.key_page(-1)))
        root.bind("<Next>", lambda e: self._only("Library", lambda: self.library.key_page(1)))
        root.bind("/", lambda e: self._only_evt("Library", self.library.key_find_search, e))
        root.bind("<Control-c>", lambda e: self._only_evt("Library", self.library.key_copy_name, e))
        root.bind("<Control-Shift-C>",
                  lambda e: self._only_evt("Library", self.library.key_copy_path, e))

    def _only(self, tab_name: str, fn) -> None:
        if self._active() == tab_name:
            fn()

    def _only_evt(self, tab_name: str, fn, event):
        if self._active() == tab_name:
            return fn(event)
        return None

    def _left(self, event):
        if self._active() == "Convert":
            self.convert.key_scrub(event, -1)
        else:
            self.library.key_seek(event, -5)

    def _right(self, event):
        if self._active() == "Convert":
            self.convert.key_scrub(event, 1)
        else:
            self.library.key_seek(event, 5)

    # ── shutdown ─────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        if not self.convert.on_app_close():
            return
        self.library.on_app_close()
        self.cfg.save()
        self.root.destroy()


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    root = ctk.CTk()
    root.configure(fg_color=T.BG)
    PazApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
