"""
gui.py — CS2GUI class (customtkinter front-end).

All widget construction and user-interaction logic lives here.
AppCore is injected at construction time; GUI and business logic share
no module-level state.
"""
from __future__ import annotations

import functools
import os
import re
import socket
import tempfile
import threading
import time
import tkinter.filedialog
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from .config import (
    OFFICIAL_MAPS, GAME_MODES, MODE_MAPS, MODE_WORKSHOP_SEARCH,
    RCON_HOST, RCON_PORT, FLASK_PORT, _WS_BROWSE, load_workshop,
)
from .core import AppCore

# ── Global CTk theme (applied once at import time) ────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Thumbnail cache directory ─────────────────────────────────────────────────
_THUMB_DIR = os.path.join(tempfile.gettempdir(), "oblivion_thumbs")
os.makedirs(_THUMB_DIR, exist_ok=True)

# ── Colour seeds for placeholder map thumbnails ───────────────────────────────
_MAP_COLORS: dict[str, tuple[int, int, int]] = {
    "de_dust2":     (130,  95, 45),  "de_mirage":    (140, 100, 55),
    "de_inferno":   (140,  60, 30),  "de_nuke":      ( 70, 110, 70),
    "de_ancient":   (100,  85, 65),  "de_anubis":    (140, 115, 55),
    "de_vertigo":   ( 50,  80,150),  "de_cache":     (110, 110, 60),
    "de_overpass":  ( 55,  85,145),  "de_train":     ( 75,  75, 88),
    "cs_office":    ( 70,  90, 70),
    "cs_italy":     (140, 100, 50),  "ar_shoots":    (100,  60, 30),
    "ar_baggage":   ( 80,  80, 80),  "ar_dizzy":     ( 90,  60, 40),
    "de_lake":      ( 60, 120, 80),  "de_safehouse": ( 90,  70, 50),
    "de_shortdust": (120,  90, 40),  "de_stmarc":    ( 80,  80,100),
    "de_bank":      ( 70,  70, 80),  "de_sugarcane": ( 80, 110, 60),
}

# ── Public URLs for official CS2 map thumbnails ──────────────────────────────
# Source: Counter-Strike Wiki on Fandom (counterstrike.fandom.com) — these are
# the actual CDN URLs returned by the MediaWiki pageimages API for each map.
# They sit on Wikia's static CDN (static.wikia.nocookie.net) which is stable
# for years.  If any URL ever returns 404, the placeholder thumbnail remains.
_OFFICIAL_MAP_URLS: dict[str, list[str]] = {
    "de_dust2":   ["https://static.wikia.nocookie.net/cswikia/images/1/16/Cs2_dust2.png/revision/latest"],
    "de_mirage":  ["https://static.wikia.nocookie.net/cswikia/images/f/f5/De_mirage_cs2.png/revision/latest"],
    "de_inferno": ["https://static.wikia.nocookie.net/cswikia/images/1/17/Cs2_inferno_remake.png/revision/latest"],
    "de_nuke":    ["https://static.wikia.nocookie.net/cswikia/images/d/d6/De_nuke_cs2.png/revision/latest"],
    "de_anubis":  ["https://static.wikia.nocookie.net/cswikia/images/a/a0/CS2_Anubis_B_site.png/revision/latest"],
    "de_ancient": ["https://static.wikia.nocookie.net/cswikia/images/5/5c/De_ancient_cs2.png/revision/latest"],
    "de_vertigo": ["https://static.wikia.nocookie.net/cswikia/images/8/88/De_vertigo_cs2.jpg/revision/latest"],
    "de_overpass":["https://static.wikia.nocookie.net/cswikia/images/5/55/Overpass_loading_screen.png/revision/latest"],
    "de_train":   ["https://static.wikia.nocookie.net/cswikia/images/3/3e/De_train_cs2.png/revision/latest",
                   "https://static.wikia.nocookie.net/cswikia/images/4/4d/De_train_cs2.jpg/revision/latest"],
    "de_cache":   ["https://static.wikia.nocookie.net/cswikia/images/5/5b/De_cache_cs2.png/revision/latest"],
    "cs_office":  ["https://static.wikia.nocookie.net/cswikia/images/f/f0/Cs2_office.png/revision/latest"],
    "cs_italy":   ["https://static.wikia.nocookie.net/cswikia/images/a/aa/Cs2_italy.png/revision/latest"],
}


class CS2GUI:
    # ── Colour palette ────────────────────────────────────────────────────────
    # Backgrounds carry a subtle violet undertone (closer to the mockup's
    # purple-tinted dark theme) without crossing into "obviously purple".
    BG       = "#15131e"   # window background (very dark charcoal-violet)
    CARD     = "#1c1a28"   # card / panel surface (one step lighter)
    SIDE     = "#181625"   # sidebar (sits between BG and CARD)
    DEEP     = "#0f0d18"   # inset surface — log box, dropdown bg
    BORDER   = "#2a2638"   # frame borders + inactive accents
    ACCENT   = "#8a2be2"   # electric violet
    ACCENT_H = "#7b2cbf"   # accent (hover / pressed — slightly muted)
    BLUE     = "#4e9aff"
    BLUE_H   = "#3b82f6"
    STOP     = "#e05c6b"   # destructive red (kick / unban / ban / delete)
    STOP_H   = "#be2a3e"
    NEUTRAL  = "#52525b"   # muted grey — used by the server STOP control button
    NEUTRAL_H = "#3f3f46"
    GREEN    = "#22c55e"
    GREEN_H  = "#16a34a"
    ORANGE   = "#f59e0b"
    RED      = "#ef4444"
    TEXT     = "#e8e8f4"
    SUB      = "#9090aa"
    DIM      = "#606078"

    def __init__(self, core: AppCore) -> None:
        self.core = core
        self._uptime_start:        float | None       = None
        self._pulse_step:          int                = 0
        self._manual_update_check: bool               = False
        self._ff_btn:              ctk.CTkButton | None = None
        self._app_upd_url:         str                = ""
        self._wk_all_ids:          list[str]          = []
        self._wk_all_labels:       list[str]          = []
        self._map_cards:           dict[str, ctk.CTkFrame] = {}  # map_id → card frame
        # In-memory CTkImage cache — keys are map_id or "ws_<id>".
        # CTkImage objects hold their Tk PhotoImages internally; reusing the same
        # object means zero re-decoding and zero new PhotoImage allocation on every
        # grid rebuild, mode switch, or page lift.  Invalidated only when a fresh
        # download replaces a placeholder (see _refresh_*_card_image).
        self._img_cache:           dict[str, ctk.CTkImage]  = {}

        self.root = ctk.CTk()
        self.root.title("Oblivion Server Tool")
        self.root.geometry("1480x980")
        self.root.configure(fg_color=self.BG)
        self.root.resizable(True, True)
        self.root.minsize(1240, 860)

        self._build()
        self._start_monitor()
        self._tick_uptime()
        # Stagger-warm all map images across idle cycles so the first page-switch
        # never stalls waiting for PhotoImage creation.
        self.root.after(200, self._prewarm_image_cache)

        # Register callbacks after widgets are built
        self.core.on_log            = lambda e:            self.root.after(0, self._append_log, e)
        self.core.on_dl_request     = lambda wid, ip:      self.root.after(0, self._show_dl_dialog, wid, ip)
        self.core.on_state_change   = lambda:              self.root.after(0, self._on_core_state_change)
        self.core.on_update_checked = lambda av, ins, lat: self.root.after(
            0, self._on_update_checked, av, ins, lat
        )
        self.core.on_public_ip      = lambda ip: self.root.after(
            0, self._on_public_ip, ip
        )
        self.core.on_steam_session_change = lambda: self.root.after(
            0, self._update_steam_btn
        )
        self.core.on_app_update_checked = lambda av, cur, lat, url: self.root.after(
            0, self._on_app_update_checked, av, cur, lat, url
        )
        # Set initial button state from saved config
        self._update_steam_btn()

        # Probe for an already-running server (GUI closed and reopened while
        # cs2.exe was still up).  Delayed slightly so the first paint completes.
        self.root.after(800, self.core.probe_existing_server)

        # First-run: prompt for server directory if not yet configured
        if not self.core.server_dir:
            self.root.after(200, self._show_setup_dialog)

    # ── top-level layout ──────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────────────────
    # Layout builders
    # ─────────────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # ── soft violet glow at the top edge (replaces the hard accent line) ──
        self._glow_lbl = ctk.CTkLabel(self.root, text="",
                                       fg_color="transparent",
                                       image=self._make_top_glow_image(1600, 26))
        self._glow_lbl.pack(fill="x")

        # ── main frame: sidebar (col 0) + pages (col 1) ──
        main = ctk.CTkFrame(self.root, fg_color="transparent")
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        side = ctk.CTkFrame(main, fg_color=self.SIDE, corner_radius=0, width=210)
        side.grid(row=0, column=0, sticky="nsew")
        side.pack_propagate(False)
        self._build_sidebar(side)

        host = ctk.CTkFrame(main, fg_color=self.BG)
        host.grid(row=0, column=1, sticky="nsew")
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)

        # ── build pages (stacked with place) ──
        self._pages: dict[str, ctk.CTkFrame] = {}
        self._pages["status"]   = self._build_page_status(host)
        self._pages["players"]  = self._build_page_players(host)
        self._pages["config"]   = self._build_page_config(host)
        self._pages["workshop"] = self._build_page_workshop(host)
        for p in self._pages.values():
            p.place(relx=0, rely=0, relwidth=1, relheight=1)

        # Default landing page — Server Status dashboard
        self._show_page("status")

        # ── slim status bar (bottom) ──
        sb = ctk.CTkFrame(self.root, fg_color=self.DEEP, corner_radius=0, height=26)
        sb.pack(side="bottom", fill="x")
        sb.pack_propagate(False)
        sf = ctk.CTkFont(size=11)
        ctk.CTkLabel(sb, text="Uptime:", text_color=self.DIM, font=sf
                     ).pack(side="left", padx=(14, 3))
        self._sb_uptime = ctk.CTkLabel(sb, text="—", text_color=self.SUB, font=sf)
        self._sb_uptime.pack(side="left", padx=(0, 14))
        ctk.CTkLabel(sb, text="Build:", text_color=self.DIM, font=sf
                     ).pack(side="left", padx=(0, 3))
        self._sb_build = ctk.CTkLabel(sb, text="—", text_color=self.SUB, font=sf)
        self._sb_build.pack(side="left")
        ctk.CTkLabel(sb, text=f"http://localhost:{FLASK_PORT}",
                     text_color=self.DIM, font=sf
                     ).pack(side="right", padx=(4, 14))

    # ── sidebar ───────────────────────────────────────────────────────────────

    @staticmethod
    def _make_logo_image(size: int = 44) -> ctk.CTkImage:
        """Render the Oblivion brand mark: layered violet rings on dark ground."""
        scale = 4                      # supersample for smooth anti-aliased edges
        s = size * scale
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        # Background: dark rounded square (DEEP tone)
        d.rounded_rectangle([0, 0, s - 1, s - 1],
                             radius=s // 5,
                             fill=(15, 13, 24, 255))

        cx = cy = s // 2

        # Outer bloom ring — wide, semi-transparent dark violet
        r0 = s * 40 // 100
        d.ellipse([cx - r0, cy - r0, cx + r0, cy + r0],
                  outline=(100, 20, 180, 140), width=s // 7)

        # Main ring — solid accent violet
        r1 = s * 36 // 100
        d.ellipse([cx - r1, cy - r1, cx + r1, cy + r1],
                  outline=(138, 43, 226, 255), width=s // 11)

        # Inner bright ring — lighter, thinner
        r2 = s * 28 // 100
        d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2],
                  outline=(191, 95, 255, 200), width=max(3, s // 18))

        # Four short tick marks at cardinal points (like a scope reticle)
        tick_outer = s * 44 // 100
        tick_inner = s * 38 // 100
        tick_w = max(2, s // 20)
        tick_col = (191, 95, 255, 220)
        d.line([cx, cy - tick_outer, cx, cy - tick_inner], fill=tick_col, width=tick_w)
        d.line([cx, cy + tick_inner, cx, cy + tick_outer], fill=tick_col, width=tick_w)
        d.line([cx - tick_outer, cy, cx - tick_inner, cy], fill=tick_col, width=tick_w)
        d.line([cx + tick_inner, cy, cx + tick_outer, cy], fill=tick_col, width=tick_w)

        # Centre diamond — bright highlight dot
        dr = max(4, s // 12)
        d.polygon(
            [(cx, cy - dr), (cx + dr, cy), (cx, cy + dr), (cx - dr, cy)],
            fill=(220, 160, 255, 255),
        )

        # Resize back to display size with Lanczos for crisp edges
        img = img.resize((size, size), Image.LANCZOS)
        return ctk.CTkImage(img, size=(size, size))

    def _build_sidebar(self, parent: ctk.CTkFrame) -> None:
        """Left navigation sidebar: branding, page nav, utility buttons."""
        # ── Branding row with logo mark ──
        brand = ctk.CTkFrame(parent, fg_color="transparent")
        brand.pack(fill="x", padx=14, pady=(20, 4))
        _logo_img = self._make_logo_image(44)
        logo = ctk.CTkLabel(brand, text="", image=_logo_img, width=44, height=44)
        logo.pack(side="left", padx=(0, 10))
        title_col = ctk.CTkFrame(brand, fg_color="transparent")
        title_col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(title_col, text="OBLIVION",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=self.TEXT, anchor="w").pack(fill="x")
        ctk.CTkLabel(title_col, text="SERVER TOOL",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=self.SUB, anchor="w").pack(fill="x")

        # App update notification (hidden until found)
        self._app_upd_lbl = ctk.CTkLabel(
            parent, text="",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=self.ORANGE, cursor="hand2", anchor="w",
        )
        self._app_upd_lbl.pack(fill="x", padx=16, pady=(6, 0))
        self._app_upd_lbl.bind("<Button-1>", lambda _e: self._open_app_release())

        ctk.CTkFrame(parent, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=14, pady=(14, 6))

        ctk.CTkLabel(parent, text="NAVIGATION",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=self.SUB, anchor="w").pack(fill="x", padx=16, pady=(2, 6))

        # Navigation buttons
        self._nav_btns: dict[str, ctk.CTkButton] = {}
        _nav = [
            ("status",   "⏻  Server Status"),
            ("players",  "👥  Players"),
            ("config",   "⚙  Configuration"),
            ("workshop", "📦  Workshop"),
        ]
        for pid, label in _nav:
            btn = ctk.CTkButton(
                parent, text=label, anchor="w", height=42,
                corner_radius=10, border_width=1,
                fg_color="transparent", hover_color=self.BORDER,
                border_color=self.SIDE,
                text_color=self.SUB, font=ctk.CTkFont(size=14, weight="bold"),
                command=lambda p=pid: self._show_page(p),
            )
            btn.pack(fill="x", padx=10, pady=3)
            self._nav_btns[pid] = btn

        # Pushes utility buttons to bottom
        ctk.CTkFrame(parent, fg_color="transparent").pack(fill="both", expand=True)

        ctk.CTkFrame(parent, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=14, pady=(0, 8))

        _ub = dict(height=30, corner_radius=6, fg_color=self.BORDER,
                   hover_color="#2a2a40", text_color=self.SUB,
                   font=ctk.CTkFont(size=11), anchor="w")
        self._upd_btn = ctk.CTkButton(
            parent, text="⟳  Update", command=self._check_update_btn, **_ub)
        self._upd_btn.pack(fill="x", padx=10, pady=(0, 4))
        self._steam_btn = ctk.CTkButton(
            parent, text="🔑  Steam", command=self._show_steam_account_dialog, **_ub)
        self._steam_btn.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkButton(parent, text="🌐  Web Panel",
                      command=self._open_web_panel, **_ub).pack(
            fill="x", padx=10, pady=(0, 14))

    def _show_page(self, pid: str) -> None:
        """Bring the requested page to front; update nav button highlight."""
        for key, frame in self._pages.items():
            if key == pid:
                frame.lift()
            else:
                frame.lower()
        for key, btn in self._nav_btns.items():
            if key == pid:
                btn.configure(fg_color=self.BORDER,
                               border_color=self.ACCENT,
                               text_color=self.ACCENT)
            else:
                btn.configure(fg_color="transparent",
                               border_color=self.SIDE,
                               text_color=self.SUB)

    # ── status page ───────────────────────────────────────────────────────────

    def _build_page_status(self, host: ctk.CTkFrame) -> ctk.CTkFrame:
        """Main dashboard: left panel (ops + config + log) | full-width map selection."""
        page = ctk.CTkFrame(host, fg_color=self.BG)
        page.columnconfigure(0, minsize=300, weight=0)
        page.columnconfigure(1, weight=1)
        page.rowconfigure(0, weight=1)

        # Maps column is built first so _mode_var / _off_var exist when the
        # Quick Config dropdowns in _build_ops_col are wired to them.
        maps = ctk.CTkFrame(page, fg_color="transparent")
        maps.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
        maps.rowconfigure(0, weight=1)
        maps.columnconfigure(0, weight=1)
        self._build_maps_col(maps)

        ops = ctk.CTkFrame(page, fg_color="transparent")
        ops.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        self._build_ops_col(ops)

        return page

    def _build_ops_col(self, parent: ctk.CTkFrame) -> None:
        """Left column: server card → quick actions → quick config → console log."""

        # ── OptionMenu style (Quick Config dropdowns) ─────────────────────────
        _om_kw = dict(
            fg_color=self.DEEP, button_color=self.ACCENT,
            button_hover_color=self.ACCENT_H,
            dropdown_fg_color=self.CARD,
            dropdown_hover_color=self.BORDER,
            text_color=self.TEXT,
            dropdown_text_color=self.TEXT,
            font=ctk.CTkFont(size=12),
            corner_radius=8, height=30,
        )

        # ── Server Status card ────────────────────────────────────────────────
        sc = ctk.CTkFrame(parent, fg_color=self.CARD, corner_radius=12)
        sc.pack(fill="x", pady=(0, 4))

        sc_hdr = ctk.CTkFrame(sc, fg_color="transparent")
        sc_hdr.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(sc_hdr, text="Server Status:",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=self.TEXT).pack(side="left")

        self._sc_badge_wrap = ctk.CTkFrame(
            sc_hdr, fg_color=self.RED, corner_radius=10)
        self._sc_badge_wrap.pack(side="right")
        self._dot = ctk.CTkLabel(
            self._sc_badge_wrap, text="OFFLINE",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#ffffff", fg_color="transparent")
        self._dot.pack(padx=10, pady=2)

        def _stat_row(lbl_text: str) -> ctk.CTkLabel:
            row = ctk.CTkFrame(sc, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=1)
            ctk.CTkLabel(row, text=lbl_text, text_color=self.SUB,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         anchor="w", width=72).pack(side="left")
            val = ctk.CTkLabel(row, text="—", text_color=self.TEXT,
                                font=ctk.CTkFont(size=13, weight="bold"),
                                anchor="w")
            val.pack(side="left")
            return val

        self._sb_map  = _stat_row("Map:")
        self._sb_mode = _stat_row("Mode:")

        ctk.CTkFrame(sc, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=16, pady=(8, 0))

        btn_grid = ctk.CTkFrame(sc, fg_color="transparent")
        btn_grid.pack(fill="x", padx=12, pady=(8, 10))
        btn_grid.columnconfigure(0, weight=1, uniform="cb")
        btn_grid.columnconfigure(1, weight=1, uniform="cb")
        btn_grid.columnconfigure(2, weight=1, uniform="cb")

        _bs = dict(height=52, corner_radius=10, border_width=0,
                   font=ctk.CTkFont(size=11, weight="bold"))

        # Ensure the row never collapses (place() children don't contribute size)
        btn_grid.rowconfigure(0, minsize=60)

        def _make_glow_slot(col: int, padx):
            """Rounded wrapper + live-rendered gradient canvas.
            The button is floated on top via .place() so the canvas is never
            obscured by a CTk internal frame."""
            f = ctk.CTkFrame(btn_grid, fg_color=self.CARD,
                             corner_radius=12, height=60)
            f.grid(row=0, column=col, sticky="nsew", padx=padx)
            cv = tkinter.Canvas(f, bg=self.CARD,
                                highlightthickness=0, bd=0)
            cv.place(relx=0, rely=0, relwidth=1, relheight=1)
            cv._glow_color: str | None = None
            cv._photo_ref              = None
            # Render at the correct pixel size once the widget is laid out
            def _on_cfg(event, _cv=cv):
                if event.width > 4 and event.height > 4:
                    self._redraw_btn_glow(_cv, event.width, event.height)
            cv.bind("<Configure>", _on_cfg)
            return f, cv

        # ── START ──────────────────────────────────────────────────────────────
        self._start_glow, self._start_cv = _make_glow_slot(0, (0, 5))
        self._start_cv._glow_color = "#bf5fff"    # violet — active at launch
        self._start_btn = ctk.CTkButton(
            self._start_glow, text="▶\nSTART",
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#ffffff", command=self._start, **_bs)
        # Use tkinter.Widget.place directly to bypass CTk's override — CTk's
        # .place() rejects 'width' even as a negative relwidth offset.
        # relwidth=1, width=-8 → button = frame_width − 8 (4 px gap each side).
        # height is already set in the constructor via _bs; no need to pass it.
        tkinter.Widget.place(self._start_btn,
                             relx=0, rely=0, x=4, y=4, relwidth=1, width=-8)
        self._start_btn.lift()

        # ── STOP ───────────────────────────────────────────────────────────────
        self._stop_glow, self._stop_cv = _make_glow_slot(1, (0, 5))
        self._stop_btn = ctk.CTkButton(
            self._stop_glow, text="■\nSTOP",
            fg_color=self.NEUTRAL, hover_color=self.NEUTRAL_H,
            text_color=self.TEXT, state="disabled",
            command=self._stop, **_bs)
        tkinter.Widget.place(self._stop_btn,
                             relx=0, rely=0, x=4, y=4, relwidth=1, width=-8)
        self._stop_btn.lift()

        # ── CHANGE ─────────────────────────────────────────────────────────────
        self._chg_glow, self._chg_cv = _make_glow_slot(2, 0)
        self._chg_btn = ctk.CTkButton(
            self._chg_glow, text="⟳\nCHANGE",
            fg_color=self.NEUTRAL, hover_color=self.BLUE_H,
            text_color=self.TEXT, state="disabled",
            command=self._change, **_bs)
        tkinter.Widget.place(self._chg_btn,
                             relx=0, rely=0, x=4, y=4, relwidth=1, width=-8)
        self._chg_btn.lift()

        # ── Selected map preview chip (between Status card and Quick Actions) ──
        _prev_wrap = ctk.CTkFrame(parent, fg_color=self.DEEP, corner_radius=8,
                                   border_width=1, border_color=self.ACCENT)
        _prev_wrap.pack(fill="x", pady=(0, 4))
        self._map_preview_lbl = ctk.CTkLabel(
            _prev_wrap, text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="transparent", text_color=self.ACCENT, anchor="w")
        self._map_preview_lbl.pack(fill="x", padx=10, pady=6)
        # Populate now — _build_maps_col ran first so _mode_var/_off_var exist
        self._update_map_selection_ui()

        # ── Quick Actions card ────────────────────────────────────────────────
        # 2px gap below so it visually "flows" into the Quick Config card
        qa = ctk.CTkFrame(parent, fg_color=self.CARD, corner_radius=12)
        qa.pack(fill="x", pady=(0, 2))

        qa_hdr = ctk.CTkFrame(qa, fg_color="transparent")
        qa_hdr.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(qa_hdr, text="Quick Actions",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=self.TEXT).pack(side="left")
        ctk.CTkLabel(qa_hdr, text="LIVE",
                     font=ctk.CTkFont(size=9, weight="bold"),
                     fg_color=self.ACCENT, text_color="#0d0d14",
                     corner_radius=4, padx=6, pady=1).pack(side="right")

        self._chat_var = ctk.StringVar()
        br_row = ctk.CTkFrame(qa, fg_color="transparent")
        br_row.pack(fill="x", padx=12, pady=(0, 8))
        chat_ent = ctk.CTkEntry(
            br_row, textvariable=self._chat_var,
            placeholder_text="📣  Broadcast to players…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=12), height=36)
        chat_ent.pack(side="left", fill="x", expand=True, padx=(0, 6))
        chat_ent.bind("<Return>", lambda _e: self._send_chat())
        ctk.CTkButton(br_row, text="SEND", width=62, height=36,
                      corner_radius=8, fg_color=self.BLUE,
                      hover_color=self.BLUE_H, text_color=self.TEXT,
                      font=ctk.CTkFont(size=11, weight="bold"),
                      command=self._send_chat).pack(side="right")

        tg = ctk.CTkFrame(qa, fg_color="transparent")
        tg.pack(fill="x", padx=12, pady=(0, 12))
        tg.columnconfigure(0, weight=1, uniform="qc")
        tg.columnconfigure(1, weight=1, uniform="qc")
        tg.columnconfigure(2, weight=1, uniform="qc")

        _tb_base = dict(corner_radius=10, border_width=2,
                        fg_color=self.DEEP, hover_color="#15151f",
                        font=ctk.CTkFont(size=11, weight="bold"), height=62)

        def _tile(row: int, col: int, text: str, accent: str,
                  cmd, text_color: str | None = None) -> ctk.CTkButton:
            padx = (0, 5) if col < 2 else (0, 0)
            pady = (0, 4) if row == 0 else (0, 0)
            btn = ctk.CTkButton(
                tg, text=text,
                text_color=text_color or self.TEXT,
                border_color=accent,
                command=cmd, **_tb_base,
            )
            btn.grid(row=row, column=col, sticky="nsew", padx=padx, pady=pady)
            return btn

        self._ff_btn = _tile(0, 0, "⊘\nFF OFF",    self.ORANGE, self._toggle_ff)
        _tile(0, 1, "↺\nRestart",   self.BLUE,   self.core.restart_round)
        _tile(0, 2, "⏵\nWarmup",    self.GREEN,  self.core.end_warmup)
        _tile(1, 0, "▮▮\nPause",    "#facc15",   self.core.pause_match)
        _tile(1, 1, "▶\nUnpause",   self.ACCENT, self.core.unpause_match)
        _tile(1, 2, "✕\nKick Bots", self.STOP,   self.core.kick_bots)

        # ── Quick Config card ─────────────────────────────────────────────────
        # Sits directly below Quick Actions (2px gap = continuous visual block)
        # but uses its own card + header so it reads as a distinct section.
        qc = ctk.CTkFrame(parent, fg_color=self.CARD, corner_radius=12)
        qc.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(qc, text="Quick Config",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.TEXT).pack(anchor="w", padx=14, pady=(12, 8))

        def _form_row(label: str, widget_factory):
            """Label + widget row; returns the created widget."""
            row = ctk.CTkFrame(qc, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=(0, 5))
            ctk.CTkLabel(row, text=label,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=self.TEXT, anchor="w",
                         width=88).pack(side="left")
            w = widget_factory(row)
            w.pack(side="right", fill="x", expand=True)
            return w

        _mode_maps = MODE_MAPS.get(self._mode_var.get(), OFFICIAL_MAPS) or OFFICIAL_MAPS
        self._qc_map_cb = _form_row(
            "Map:", lambda r: ctk.CTkOptionMenu(
                r, values=_mode_maps, variable=self._off_var,
                command=self._on_official_select, **_om_kw))
        self._patch_option_menu_toggle(self._qc_map_cb)

        self._qc_mode_cb = _form_row(
            "Mode:", lambda r: ctk.CTkOptionMenu(
                r, values=GAME_MODES, variable=self._mode_var,
                command=self._on_mode_change, **_om_kw))
        self._patch_option_menu_toggle(self._qc_mode_cb)

        if not hasattr(self, "_maxp_var"):
            self._maxp_var = ctk.StringVar(value=self.core.max_players_override or "16")
        _maxp_choices = ["2", "4", "6", "8", "10", "12", "16", "20", "24", "32"]
        self._qc_maxp_cb = _form_row(
            "Max Players:", lambda r: ctk.CTkOptionMenu(
                r, values=_maxp_choices, variable=self._maxp_var,
                command=self._on_max_players_quickset, **_om_kw))
        self._patch_option_menu_toggle(self._qc_maxp_cb)

        # Server IP (click to copy)
        ip_row = ctk.CTkFrame(qc, fg_color="transparent")
        ip_row.pack(fill="x", padx=14, pady=(0, 5))
        ctk.CTkLabel(ip_row, text="Server IP:",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=self.TEXT, anchor="w",
                     width=88).pack(side="left")
        self._ip_entry = ctk.CTkEntry(
            ip_row, fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, font=ctk.CTkFont(size=12),
            height=30, corner_radius=8)
        self._ip_entry.insert(0, f"{RCON_HOST}:{RCON_PORT}")
        self._ip_entry.configure(state="readonly")
        self._ip_entry.pack(side="right", fill="x", expand=True)
        self._ip_entry.bind("<Button-1>", lambda _e: self._copy_connect_string())

        self._pub_ip_lbl = ctk.CTkButton(
            qc, text="📋  Copy Ext IP",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=self.TEXT,
            fg_color=self.DEEP, hover_color="#2a2a40",
            anchor="w", height=30, corner_radius=6,
            border_width=1, border_color=self.BORDER,
            command=self._copy_public_ip)
        self._pub_ip_lbl.pack(fill="x", padx=10, pady=(0, 10))

        # ── Console log card (fills all remaining vertical space) ─────────────
        console_card = ctk.CTkFrame(parent, fg_color=self.CARD, corner_radius=12)
        console_card.pack(fill="both", expand=True)
        console_card.rowconfigure(1, weight=1)
        console_card.columnconfigure(0, weight=1)

        con_hdr = ctk.CTkFrame(console_card, fg_color="transparent")
        con_hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        ctk.CTkLabel(con_hdr, text="SERVER CONSOLE OUTPUT",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=self.SUB).pack(side="left")
        ctk.CTkButton(
            con_hdr, text="Clear", width=46, height=20,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=10),
            corner_radius=4, command=self._clear_log,
        ).pack(side="right")
        ctk.CTkButton(
            con_hdr, text="Export", width=50, height=20,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=10),
            corner_radius=4, command=self._export_log,
        ).pack(side="right", padx=(0, 4))

        self._logbox = ctk.CTkTextbox(
            console_card, fg_color=self.DEEP, text_color="#a8c4bf",
            font=ctk.CTkFont(family="Consolas", size=11),
            state="disabled", wrap="word",
        )
        self._logbox.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 4))
        self._rcon_box = self._logbox  # alias — _append_rcon writes here too

        rcon_row = ctk.CTkFrame(console_card, fg_color="transparent")
        rcon_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        self._rcon_var = ctk.StringVar()
        rcon_ent = ctk.CTkEntry(
            rcon_row, textvariable=self._rcon_var,
            placeholder_text="RCON command…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=12))
        rcon_ent.pack(side="left", fill="x", expand=True)
        rcon_ent.bind("<Return>", lambda _e: self._send_rcon())
        ctk.CTkButton(
            rcon_row, text="›", width=34, height=34,
            corner_radius=8, fg_color=self.BLUE, hover_color=self.BLUE_H,
            text_color=self.TEXT, font=ctk.CTkFont(size=20, weight="bold"),
            command=self._send_rcon,
        ).pack(side="right", padx=(4, 0))

    def _build_maps_col(self, parent: ctk.CTkFrame) -> None:
        """Centre column: mode picker + official map card grid + workshop picker."""
        _cb_kw = dict(
            fg_color=self.DEEP, button_color=self.BORDER,
            border_color=self.BORDER, dropdown_fg_color=self.CARD,
            dropdown_hover_color=self.BORDER, text_color=self.TEXT,
            dropdown_text_color=self.TEXT, button_hover_color="#2a2a40",
            font=ctk.CTkFont(size=13),
        )

        # Scrollable content area
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent",
                                         scrollbar_button_color=self.BORDER,
                                         scrollbar_button_hover_color="#2a2a40")
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.columnconfigure(0, weight=1)
        p = scroll   # alias

        # ── Header row: "Map Selection" + mode dropdown inline ──
        hdr_row = ctk.CTkFrame(p, fg_color="transparent", height=34)
        hdr_row.pack(fill="x", padx=4, pady=(2, 4))
        hdr_row.pack_propagate(False)
        ctk.CTkLabel(hdr_row, text="Map Selection",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=self.TEXT).pack(side="left")
        self._mode_var = ctk.StringVar(value="Competitive")
        self._mode_cb = ctk.CTkComboBox(
            hdr_row, values=GAME_MODES, variable=self._mode_var,
            command=self._on_mode_change, width=160, **_cb_kw)
        self._mode_cb.pack(side="right")
        self._patch_dropdown_toggle(self._mode_cb)

        # ── Standard Maps subsection ──
        std_hdr = ctk.CTkFrame(p, fg_color="transparent", height=22)
        std_hdr.pack(fill="x", padx=4, pady=(2, 2))
        std_hdr.pack_propagate(False)
        self._off_lbl_w = ctk.CTkLabel(std_hdr, text="Standard Maps",
                                        font=ctk.CTkFont(size=12, weight="bold"),
                                        text_color=self.TEXT, anchor="w")
        self._off_lbl_w.pack(side="left")

        # Official map source flag + StringVar
        self._map_source: str = "official"
        self._off_var = ctk.StringVar(value=OFFICIAL_MAPS[0])
        self._map_cards: dict[str, ctk.CTkFrame] = {}

        # Official map scrollable card grid — 4 columns, taller to use freed space
        self._off_scroll = ctk.CTkScrollableFrame(
            p, height=380, fg_color=self.DEEP, corner_radius=10,
            scrollbar_button_color=self.BORDER,
            scrollbar_button_hover_color="#2a2a40",
        )
        self._off_scroll.pack(fill="x", padx=4, pady=(0, 2))
        self._off_scroll.columnconfigure(0, weight=1, uniform="mc")
        self._off_scroll.columnconfigure(1, weight=1, uniform="mc")
        self._off_scroll.columnconfigure(2, weight=1, uniform="mc")
        self._off_scroll.columnconfigure(3, weight=1, uniform="mc")

        # ── Workshop Maps subsection ──
        wk_hdr = ctk.CTkFrame(p, fg_color="transparent", height=26)
        wk_hdr.pack(fill="x", padx=4, pady=(6, 0))
        wk_hdr.pack_propagate(False)
        self._wk_lbl_w = ctk.CTkLabel(wk_hdr, text="Workshop Maps",
                                       font=ctk.CTkFont(size=12, weight="bold"),
                                       text_color=self.TEXT, anchor="w")
        self._wk_lbl_w.pack(side="left")
        # Default to bright — _update_map_selection_ui keeps both headers equal
        ctk.CTkButton(
            wk_hdr, text="↺  Refresh", width=78, height=24,
            corner_radius=6,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=11),
            command=self._refresh_wk,
        ).pack(side="right")
        # Subtitle directly under the Workshop Maps heading (mockup)
        ctk.CTkLabel(p, text="Workshop Maps – subscribed",
                     font=ctk.CTkFont(size=10),
                     text_color=self.DIM, anchor="w",
                     ).pack(fill="x", padx=4, pady=(0, 2))

        # Workshop selection state
        self._wk_var = ctk.StringVar(value="")
        self._wk_cards: dict[str, ctk.CTkFrame] = {}

        # Workshop card grid — 4 columns, taller to match the expanded space
        self._wk_scroll = ctk.CTkScrollableFrame(
            p, height=290, fg_color=self.DEEP, corner_radius=10,
            scrollbar_button_color=self.BORDER,
            scrollbar_button_hover_color="#2a2a40",
        )
        self._wk_scroll.pack(fill="x", padx=4, pady=(0, 4))
        self._wk_scroll.columnconfigure(0, weight=1, uniform="wc")
        self._wk_scroll.columnconfigure(1, weight=1, uniform="wc")
        self._wk_scroll.columnconfigure(2, weight=1, uniform="wc")
        self._wk_scroll.columnconfigure(3, weight=1, uniform="wc")

        # Placeholder while the workshop list is empty
        self._wk_empty_lbl = ctk.CTkLabel(
            self._wk_scroll,
            text="No workshop maps downloaded yet.\nUse the Workshop tab to grab some.",
            text_color=self.SUB, font=ctk.CTkFont(size=11))
        self._wk_empty_lbl.grid(row=0, column=0, columnspan=4, padx=10, pady=24)

        # Mode hint
        self._mode_hint_lbl = ctk.CTkLabel(
            p, text="", text_color=self.SUB,
            font=ctk.CTkFont(size=12), anchor="w")
        self._mode_hint_lbl.pack(fill="x", padx=4, pady=(0, 4))

        # Populate card grid now that _mode_var exists
        self._rebuild_official_grid()
        self._update_map_selection_ui()

    # _build_right_col removed — Quick Config and console log now live in
    # _build_ops_col, directly below Quick Actions in the left panel.

    # ── players page ──────────────────────────────────────────────────────────

    def _build_page_players(self, host: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(host, fg_color=self.BG)
        card = ctk.CTkFrame(page, fg_color=self.CARD, corner_radius=12)
        card.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(card, text="Players",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=self.TEXT).pack(anchor="w", padx=14, pady=(14, 4))

        # Header row: refresh + auto-refresh
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkButton(
            hdr, text="↺ Refresh", width=90, height=28,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            corner_radius=6, command=self._refresh_players,
        ).pack(side="left")
        self._auto_refresh_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            hdr, text="Auto (10s)", variable=self._auto_refresh_var,
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            border_color=self.BORDER, checkmark_color="#0d0d14",
            command=self._toggle_auto_refresh,
        ).pack(side="left", padx=(10, 0))

        self._player_status_lbl = ctk.CTkLabel(
            card, text="", text_color=self.SUB, font=ctk.CTkFont(size=12))
        self._player_status_lbl.pack(anchor="w", padx=12, pady=(0, 4))

        self._player_scroll = ctk.CTkScrollableFrame(
            card, fg_color=self.DEEP, corner_radius=8, height=180)
        self._player_scroll.pack(fill="x", padx=12, pady=(0, 8))
        self._player_rows: list[ctk.CTkFrame] = []

        ctk.CTkFrame(card, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(card, text="BAN MANAGEMENT",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 4))

        ban_row = ctk.CTkFrame(card, fg_color="transparent")
        ban_row.pack(fill="x", padx=12, pady=(0, 4))
        self._ban_id_var = ctk.StringVar()
        ctk.CTkEntry(
            ban_row, textvariable=self._ban_id_var,
            placeholder_text="SteamID to ban…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=12),
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            ban_row, text="Ban", width=60, height=32,
            fg_color=self.STOP, hover_color=self.STOP_H,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._manual_ban,
        ).pack(side="right", padx=(5, 0))

        ctk.CTkButton(
            card, text="↺ Refresh Ban List", height=28,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            corner_radius=6, command=self._refresh_ban_list,
        ).pack(fill="x", padx=12, pady=(0, 4))

        self._ban_scroll = ctk.CTkScrollableFrame(
            card, fg_color=self.DEEP, corner_radius=8, height=120)
        self._ban_scroll.pack(fill="x", padx=12, pady=(0, 12))
        self._ban_rows: list[ctk.CTkFrame] = []
        self._auto_refresh_after: str | None = None

        return page

    # ── config page ───────────────────────────────────────────────────────────

    def _build_page_config(self, host: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(host, fg_color=self.BG)
        outer = ctk.CTkFrame(page, fg_color=self.CARD, corner_radius=12)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(outer, text="Configuration",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=self.TEXT).pack(anchor="w", padx=14, pady=(14, 4))

        scroll = ctk.CTkScrollableFrame(outer, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=0, pady=0)
        p = scroll

        ctk.CTkLabel(p, text="INSTALL LOCATION",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(10, 4))
        ctk.CTkLabel(p, text="CS2 Server Directory  (folder containing steamcmd.exe)",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=12)
        dir_row = ctk.CTkFrame(p, fg_color="transparent")
        dir_row.pack(fill="x", padx=12, pady=(2, 6))
        self._server_dir_var = ctk.StringVar(value=self.core.server_dir)
        ctk.CTkEntry(dir_row, textvariable=self._server_dir_var,
                     placeholder_text=r"e.g. C:\cs2server",
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, placeholder_text_color=self.SUB,
                     font=ctk.CTkFont(size=12),
                     ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            dir_row, text="Browse", width=72, height=30,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.TEXT, font=ctk.CTkFont(size=11),
            command=self._browse_server_dir,
        ).pack(side="right", padx=(5, 0))
        ctk.CTkButton(
            p, text="⬇  Install / Reinstall CS2 Server",
            height=30, corner_radius=6,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=self._install_server,
        ).pack(fill="x", padx=12, pady=(4, 8))

        ctk.CTkFrame(p, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(p, text="SERVER SETTINGS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 4))

        ctk.CTkLabel(p, text="Server Hostname",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=12)
        self._hostname_var = ctk.StringVar(value=self.core.hostname)
        ctk.CTkEntry(p, textvariable=self._hostname_var,
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, font=ctk.CTkFont(size=12),
                     ).pack(fill="x", padx=12, pady=(2, 6))

        ctk.CTkLabel(p, text="Server Password  (blank = public)",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=12)
        pw_row = ctk.CTkFrame(p, fg_color="transparent")
        pw_row.pack(fill="x", padx=12, pady=(2, 6))
        self._svpw_var = ctk.StringVar(value=self.core.sv_password)
        ctk.CTkEntry(pw_row, textvariable=self._svpw_var, show="●",
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, font=ctk.CTkFont(size=12),
                     ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            pw_row, text="Set Live", width=72, height=30,
            fg_color=self.BLUE, hover_color=self.BLUE_H,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._set_sv_password_live,
        ).pack(side="right", padx=(5, 0))

        ctk.CTkLabel(p, text="Game Server Login Token  (GSLT — required for workshop maps)",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=12)
        self._gslt_var = ctk.StringVar(value=self.core.gslt_token)
        ctk.CTkEntry(p, textvariable=self._gslt_var, show="●",
                     placeholder_text="Get one at steamcommunity.com/dev/managegameservers",
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, placeholder_text_color=self.SUB,
                     font=ctk.CTkFont(size=12),
                     ).pack(fill="x", padx=12, pady=(2, 6))

        ctk.CTkLabel(p, text="Max Players Override  (blank = mode default)",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=12)
        # _maxp_var may already exist (the right-col Quick Config creates it
        # first).  Reuse it so the Configuration entry and the Quick Config
        # dropdown stay in lock-step instead of holding orphaned StringVars.
        if not hasattr(self, "_maxp_var"):
            self._maxp_var = ctk.StringVar(value=self.core.max_players_override)
        ctk.CTkEntry(p, textvariable=self._maxp_var, placeholder_text="e.g. 16",
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, placeholder_text_color=self.SUB,
                     font=ctk.CTkFont(size=12),
                     ).pack(fill="x", padx=12, pady=(2, 6))

        chk_row = ctk.CTkFrame(p, fg_color="transparent")
        chk_row.pack(fill="x", padx=12, pady=(0, 6))
        self._tick128_var = ctk.BooleanVar(value=self.core.tickrate_128)
        ctk.CTkCheckBox(
            chk_row, text="Tickrate 128",
            variable=self._tick128_var,
            text_color=self.TEXT, font=ctk.CTkFont(size=12),
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            border_color=self.BORDER, checkmark_color="#0d0d14",
        ).pack(side="left", padx=(0, 20))
        self._autostart_var = ctk.BooleanVar(value=self.core.auto_start)
        ctk.CTkCheckBox(
            chk_row, text="Auto-start on launch", variable=self._autostart_var,
            text_color=self.TEXT, font=ctk.CTkFont(size=12),
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            border_color=self.BORDER, checkmark_color="#0d0d14",
        ).pack(side="left")
        ctk.CTkButton(
            p, text="💾  Save Settings", height=34, corner_radius=8,
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14", font=ctk.CTkFont(size=12, weight="bold"),
            command=self._save_server_settings,
        ).pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkFrame(p, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(p, text="BOTS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 4))

        bot_row = ctk.CTkFrame(p, fg_color="transparent")
        bot_row.pack(fill="x", padx=12, pady=(0, 4))
        _bb = {"height": 30, "corner_radius": 6,
               "fg_color": self.BORDER, "hover_color": "#2a2a40",
               "text_color": self.TEXT, "font": ctk.CTkFont(size=12, weight="bold")}
        ctk.CTkButton(bot_row, text="+1 Bot",
                      command=lambda: self.core.add_bots(1), **_bb,
                      ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(bot_row, text="+5 Bots",
                      command=lambda: self.core.add_bots(5), **_bb,
                      ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(bot_row, text="Kick All",
                      fg_color=self.STOP, hover_color=self.STOP_H,
                      text_color=self.TEXT, font=ctk.CTkFont(size=12, weight="bold"),
                      height=30, corner_radius=6, command=self.core.kick_bots,
                      ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(p, text="Bot Difficulty",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=12, pady=(4, 2))
        self._bot_diff_var = ctk.StringVar(value=self.core.bot_difficulty)
        ctk.CTkComboBox(
            p, values=["Easy", "Normal", "Hard", "Expert"],
            variable=self._bot_diff_var,
            fg_color=self.DEEP, button_color=self.BORDER,
            border_color=self.BORDER, dropdown_fg_color=self.CARD,
            dropdown_hover_color=self.BORDER, text_color=self.TEXT,
            dropdown_text_color=self.TEXT, button_hover_color="#2a2a40",
            font=ctk.CTkFont(size=12),
            command=lambda v: setattr(self.core, "bot_difficulty", v),
        ).pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkFrame(p, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(p, text="CONFIG PRESETS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 4))

        preset_save_row = ctk.CTkFrame(p, fg_color="transparent")
        preset_save_row.pack(fill="x", padx=12, pady=(0, 4))
        self._preset_name_var = ctk.StringVar()
        ctk.CTkEntry(
            preset_save_row, textvariable=self._preset_name_var,
            placeholder_text="Preset name…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=12),
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            preset_save_row, text="Save", width=60, height=30,
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14", font=ctk.CTkFont(size=12, weight="bold"),
            command=self._save_preset,
        ).pack(side="right", padx=(5, 0))

        preset_load_row = ctk.CTkFrame(p, fg_color="transparent")
        preset_load_row.pack(fill="x", padx=12, pady=(0, 6))
        preset_names = list(self.core.presets.keys()) or [""]
        self._preset_sel_var = ctk.StringVar(value=preset_names[0])
        self._preset_cb = ctk.CTkComboBox(
            preset_load_row, values=preset_names,
            variable=self._preset_sel_var,
            fg_color=self.DEEP, button_color=self.BORDER,
            border_color=self.BORDER, dropdown_fg_color=self.CARD,
            dropdown_hover_color=self.BORDER, text_color=self.TEXT,
            dropdown_text_color=self.TEXT, button_hover_color="#2a2a40",
            font=ctk.CTkFont(size=12),
        )
        self._preset_cb.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            preset_load_row, text="Load", width=55, height=30,
            fg_color=self.BLUE, hover_color=self.BLUE_H,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._load_preset,
        ).pack(side="right", padx=(5, 0))
        ctk.CTkButton(
            preset_load_row, text="Del", width=40, height=30,
            fg_color=self.STOP, hover_color=self.STOP_H,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._delete_preset,
        ).pack(side="right", padx=(5, 0))

        return page

    # ── workshop page ─────────────────────────────────────────────────────────

    def _build_page_workshop(self, host: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(host, fg_color=self.BG)
        card = ctk.CTkFrame(page, fg_color=self.CARD, corner_radius=12)
        card.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(card, text="Workshop Maps",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=self.TEXT).pack(anchor="w", padx=14, pady=(14, 4))

        ctk.CTkLabel(card, text="DOWNLOAD A MAP",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(4, 4))
        ctk.CTkLabel(card, text="Paste a Workshop ID or full Steam Workshop URL:",
                     font=ctk.CTkFont(size=12), text_color=self.SUB,
                     anchor="w").pack(fill="x", padx=12)

        ws_row = ctk.CTkFrame(card, fg_color="transparent")
        ws_row.pack(fill="x", padx=12, pady=(4, 4))
        self._wsid_var = ctk.StringVar()
        ctk.CTkEntry(
            ws_row, textvariable=self._wsid_var,
            placeholder_text="ID or URL (steamcommunity.com/…?id=…)",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=13),
        ).pack(side="left", fill="x", expand=True)
        self._cancel_dl_btn = ctk.CTkButton(
            ws_row, text="✕", width=34, height=34,
            fg_color=self.BORDER, hover_color=self.BORDER,
            text_color=self.BORDER, font=ctk.CTkFont(size=14, weight="bold"),
            state="disabled", command=self.core.cancel_download,
        )
        self._cancel_dl_btn.pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            ws_row, text="DL", width=52, height=34,
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14", font=ctk.CTkFont(size=12, weight="bold"),
            command=self._local_dl,
        ).pack(side="right", padx=(6, 0))

        self._wsid_lbl = ctk.CTkLabel(
            card, text="", text_color=self.SUB, font=ctk.CTkFont(size=12))
        self._wsid_lbl.pack(anchor="w", padx=12, pady=(0, 8))

        ctk.CTkFrame(card, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(card, text="MANAGE MAPS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 4))
        ctk.CTkButton(
            card, text="↻  Check Map Updates",
            height=32, corner_radius=6,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=self._check_map_updates,
        ).pack(fill="x", padx=12, pady=(0, 6))

        ctk.CTkFrame(card, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(card, text="PLUGINS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 4))
        ctk.CTkLabel(
            card,
            text="Deploy copies bundled plugin files into the server.\n"
                 "Run this before starting if you changed the game mode.",
            font=ctk.CTkFont(size=11), text_color=self.DIM,
            justify="left", anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 6))

        self._deploy_btn = ctk.CTkButton(
            card, text="⚡  Deploy Plugins for Current Mode",
            height=34, corner_radius=8,
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14", font=ctk.CTkFont(size=12, weight="bold"),
            command=self._deploy_plugins,
        )
        self._deploy_btn.pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkButton(
            card, text="⚙  Check Installed Plugins",
            height=32, corner_radius=6,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=self._check_plugins,
        ).pack(fill="x", padx=12, pady=(0, 6))
        self._browse_btn = ctk.CTkButton(
            card, text="🔍  Browse Workshop Maps",
            height=32, corner_radius=6,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=self._browse_workshop,
        )
        self._browse_btn.pack(fill="x", padx=12, pady=(0, 10))

        # ── Download log ──────────────────────────────────────────────────────
        ctk.CTkFrame(card, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 6))

        log_hdr = ctk.CTkFrame(card, fg_color="transparent")
        log_hdr.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(log_hdr, text="DOWNLOAD LOG",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Clear", width=46, height=20,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=10),
            corner_radius=4, command=self._clear_wk_log,
        ).pack(side="right")

        self._wk_logbox = ctk.CTkTextbox(
            card, fg_color=self.DEEP, text_color="#a8c4bf",
            font=ctk.CTkFont(family="Consolas", size=11),
            state="disabled", wrap="word",
        )
        self._wk_logbox.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        return page

    # ── log / RCON output ─────────────────────────────────────────────────────

    def _append_log(self, entry: str) -> None:
        self._logbox.configure(state="normal")
        self._logbox.insert("end", entry + "\n")
        self._logbox.see("end")
        self._logbox.configure(state="disabled")
        # Mirror every log line to the Workshop tab's download log so download
        # progress is visible without leaving the Workshop page.
        wk = getattr(self, "_wk_logbox", None)
        if wk is not None:
            wk.configure(state="normal")
            wk.insert("end", entry + "\n")
            wk.see("end")
            wk.configure(state="disabled")

    def _clear_log(self) -> None:
        self._logbox.configure(state="normal")
        self._logbox.delete("1.0", "end")
        self._logbox.configure(state="disabled")

    def _clear_wk_log(self) -> None:
        self._wk_logbox.configure(state="normal")
        self._wk_logbox.delete("1.0", "end")
        self._wk_logbox.configure(state="disabled")

    def _copy_connect_string(self) -> None:
        """Copy 'connect <ip>:<port>' to the clipboard and confirm in the log."""
        s = f"connect {RCON_HOST}:{RCON_PORT}"
        self.root.clipboard_clear()
        self.root.clipboard_append(s)
        self.core.log(f"Copied to clipboard: {s}")

    def _append_rcon(self, line: str) -> None:
        self._rcon_box.configure(state="normal")
        self._rcon_box.insert("end", line + "\n")
        self._rcon_box.see("end")
        self._rcon_box.configure(state="disabled")

    # ── map helpers ───────────────────────────────────────────────────────────

    # ── thumbnail helpers ─────────────────────────────────────────────────────

    def _make_top_glow_image(self, w: int, h: int) -> ctk.CTkImage:
        """Render a soft horizontal violet glow that fades into the background.

        Cosine-bell horizontal falloff × linear vertical falloff, blended
        from BG → ACCENT.  Pixels are written through a single bytearray
        and handed to Image.frombytes — ~10× faster than per-pixel
        draw.point() calls.
        """
        def _hex(c: str) -> tuple[int, int, int]:
            c = c.lstrip("#")
            return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
        br, bgn, bb     = _hex(self.BG)
        ar, ag, ab      = _hex(self.ACCENT)
        cx              = w / 2.0
        denom           = max(h - 1, 1)

        # Pre-compute horizontal falloff once; per-row only multiplies by vfall.
        hfall = [max(0.0, 1.0 - ((abs(x - cx) / cx) ** 2)) for x in range(w)]
        buf   = bytearray(w * h * 3)
        for y in range(h):
            vfall = (1.0 - y / denom) * 0.55
            row_off = y * w * 3
            for x in range(w):
                m = hfall[x] * vfall
                inv = 1.0 - m
                i = row_off + x * 3
                buf[i]     = int(br  * inv + ar * m)
                buf[i + 1] = int(bgn * inv + ag * m)
                buf[i + 2] = int(bb  * inv + ab * m)
        img = Image.frombytes("RGB", (w, h), bytes(buf))
        return ctk.CTkImage(img, size=(w, h))

    def _make_btn_glow_image(self, glow_color: str | None = None) -> ctk.CTkImage:
        """Inward glow gradient for Start / Stop / Change button wrappers.

        Same bytearray technique as _make_top_glow_image.
        The glow is brightest at the perimeter and fades toward the interior
        over ~10 px — the inverse of the top-edge bloom.
        Pass glow_color=None to get a flat CARD-coloured image (inactive state).
        """
        w, h    = 90, 74          # wrapper = button (h=66) + 4 px pad each side
        glow_px = 10.0            # falloff depth from each edge in pixels

        def _hex(c: str) -> tuple[int, int, int]:
            c = c.lstrip("#")
            return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))

        br, bgn, bb = _hex(self.CARD)

        if glow_color is None:
            buf = bytearray([br, bgn, bb] * (w * h))
            img = Image.frombytes("RGB", (w, h), bytes(buf))
            return ctk.CTkImage(img, size=(w, h))

        gr, gg, gb = _hex(glow_color)
        buf = bytearray(w * h * 3)
        for y in range(h):
            v_dist  = min(y, h - 1 - y)
            v_edge  = max(0.0, 1.0 - v_dist / glow_px)
            row_off = y * w * 3
            for x in range(w):
                h_dist = min(x, w - 1 - x)
                h_edge = max(0.0, 1.0 - h_dist / glow_px)
                # Brightest where closest to any edge; zero beyond glow_px inward
                m   = max(h_edge, v_edge) ** 1.5 * 0.85
                inv = 1.0 - m
                i   = row_off + x * 3
                buf[i]     = int(br  * inv + gr * m)
                buf[i + 1] = int(bgn * inv + gg * m)
                buf[i + 2] = int(bb  * inv + gb * m)

        img = Image.frombytes("RGB", (w, h), bytes(buf))
        return ctk.CTkImage(img, size=(w, h))

    def _redraw_btn_glow(self, cv: tkinter.Canvas,
                          w: int, h: int) -> None:
        """Paint the inward glow gradient onto a button's tk.Canvas at runtime.

        Called on <Configure> (first layout / window resize) and by _set_state
        whenever the active button changes.  Same bytearray technique as
        _make_top_glow_image; uses ImageTk.PhotoImage so it lands directly on
        the canvas without any CTk wrapper getting in the way.

        The image is masked to a rounded rectangle (radius 14) so the gradient
        is clipped to the wrapper frame's visible boundary.
        """
        def _hex(c: str) -> tuple[int, int, int]:
            c = c.lstrip("#")
            return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))

        br, bgn, bb = _hex(self.CARD)
        color       = getattr(cv, "_glow_color", None)
        glow_px     = max(8.0, h / 8)    # depth scales with button height

        if color is None:
            img = Image.new("RGB", (w, h), (br, bgn, bb))
        else:
            gr, gg, gb = _hex(color)
            buf = bytearray(w * h * 3)
            for y in range(h):
                v_dist  = min(y, h - 1 - y)
                v_edge  = max(0.0, 1.0 - v_dist / glow_px)
                row_off = y * w * 3
                for x in range(w):
                    h_dist = min(x, w - 1 - x)
                    h_edge = max(0.0, 1.0 - h_dist / glow_px)
                    m      = max(h_edge, v_edge) ** 1.5 * 0.85
                    inv    = 1.0 - m
                    i      = row_off + x * 3
                    buf[i]     = int(br  * inv + gr * m)
                    buf[i + 1] = int(bgn * inv + gg * m)
                    buf[i + 2] = int(bb  * inv + gb * m)
            img = Image.frombytes("RGB", (w, h), bytes(buf))

        # Mask to rounded rectangle so gradient respects wrapper corner_radius=12
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                               radius=12, fill=255)
        card_bg = Image.new("RGB", (w, h), (br, bgn, bb))
        img = Image.composite(img, card_bg, mask)

        photo = ImageTk.PhotoImage(img)
        cv.delete("all")
        cv.create_image(0, 0, anchor="nw", image=photo)
        cv._photo_ref = photo       # keep reference — GC would blank the canvas

    def _make_placeholder_image(self, map_id: str) -> ctk.CTkImage:
        """Generate a placeholder thumbnail with sky/ground gradient + skyline.

        The sky and ground bands are uniform-colour rows, so we fill the pixel
        buffer with ``bytes([r, g, b]) * width`` slice-assignment — pure C,
        ~8× faster than the equivalent ``draw.line`` Python loop.  The skyline
        silhouettes are drawn with PIL's C-accelerated rectangle/point calls.
        """
        # Strip "ws_" prefix so workshop placeholders use the same colour table
        clean_id = map_id[3:] if map_id.startswith("ws_") else map_id
        base     = _MAP_COLORS.get(clean_id, (60, 65, 90))
        r, g, b  = base
        W, H     = 320, 192
        sky_h    = int(H * 0.45)   # ≈ 86 rows

        sky_top = (max(int(r * 0.45) + 12, 0),
                   max(int(g * 0.55) + 22, 0),
                   max(int(b * 0.75) + 30, 0))
        sky_bot = (int(r * 0.80), int(g * 0.85), int(b * 0.90))

        # ── Fill pixel buffer row-by-row using fast bytes multiplication ──────
        buf = bytearray(W * H * 3)

        for y in range(sky_h):
            f   = y / max(sky_h - 1, 1)
            row = bytes([
                int(sky_top[0] * (1 - f) + sky_bot[0] * f),
                int(sky_top[1] * (1 - f) + sky_bot[1] * f),
                int(sky_top[2] * (1 - f) + sky_bot[2] * f),
            ]) * W
            buf[y * W * 3 : (y + 1) * W * 3] = row

        for y in range(sky_h, H):
            f   = (y - sky_h) / max(H - sky_h - 1, 1)
            s   = 1.0 - 0.35 * f
            row = bytes([int(r * s), int(g * s), int(b * s)]) * W
            buf[y * W * 3 : (y + 1) * W * 3] = row

        img  = Image.frombytes("RGB", (W, H), bytes(buf))
        draw = ImageDraw.Draw(img)

        # ── Skyline silhouettes (C-accelerated rectangle/point calls) ─────────
        seed    = sum(ord(c) for c in clean_id) if clean_id else 0
        rng     = [(seed + i * 7) % 100 / 100.0 for i in range(10)]
        x = idx = 0
        while x < W:
            bw     = 24 + int(rng[idx % 10] * 38)
            bh     = 18 + int(rng[(idx + 3) % 10] * 44)
            factor = 0.45 + rng[(idx + 5) % 10] * 0.20
            br2    = int(r * factor)
            bg2    = int(g * factor)
            bb2    = int(b * factor)
            draw.rectangle([(x, sky_h - bh), (x + bw, sky_h + 6)],
                           fill=(br2, bg2, bb2))
            if bw > 28 and bh > 22:
                wr = min(br2 + 60, 255)
                wg = min(bg2 + 50, 255)
                wb = min(bb2 + 30, 180)
                for wy in range(sky_h - bh + 6, sky_h - 4, 8):
                    for wx in range(x + 4, x + bw - 4, 6):
                        draw.point((wx, wy), fill=(wr, wg, wb))
            x  += bw + 3 + int(rng[(idx + 1) % 10] * 6)
            idx += 1

        # ── Scan-line grid + border ───────────────────────────────────────────
        gc     = (min(r + 18, 255), min(g + 18, 255), min(b + 18, 255))
        accent = (min(r + 45, 255), min(g + 45, 255), min(b + 65, 255))
        for gy in range(0, H, 32):
            draw.line([(0, gy), (W, gy)], fill=gc)
        draw.rectangle([(0, 0), (W - 1, H - 1)], outline=accent, width=1)
        draw.line([(0, H - 1), (W - 1, H - 1)], fill=accent, width=2)

        return ctk.CTkImage(img, size=(160, 96))

    def _prewarm_image_cache(self) -> None:
        """Stagger-load every known map image into _img_cache across idle cycles.

        CTkImage creates its internal Tk PhotoImages lazily — only when a widget
        first renders them.  Without pre-warming, the first time the user visits
        a page every image allocates a new PhotoImage in one big synchronous
        burst, causing a visible freeze.

        We load one image per scheduled call so the work is spread across the
        event loop with zero perceived impact.  Images already in the cache (e.g.
        built during _rebuild_official_grid at startup) are skipped instantly.
        """
        # Gather every map ID that could appear anywhere in the UI
        all_map_ids: list[str] = list({
            m
            for maps in MODE_MAPS.values()
            if maps
            for m in maps
        })
        # Workshop IDs that are already on disk
        wk_keys = [f"ws_{wid}" for wid in (self._wk_all_ids or [])]
        queue   = [mid for mid in all_map_ids if mid not in self._img_cache]
        queue  += [key for key in wk_keys     if key  not in self._img_cache]

        def _load_one(remaining: list[str]) -> None:
            if not remaining:
                return
            map_id = remaining[0]
            rest   = remaining[1:]
            try:
                # _get_map_image populates _img_cache as a side-effect
                if map_id.startswith("ws_"):
                    self._get_workshop_image(map_id[3:])
                else:
                    self._get_map_image(map_id)
            except Exception:
                pass
            # Schedule the next image on the next idle tick (non-blocking)
            if rest:
                self.root.after(10, _load_one, rest)

        if queue:
            self.root.after(10, _load_one, queue)

    def _get_map_image(self, map_id: str) -> ctk.CTkImage:
        """Return a CTkImage for map_id, backed by an in-memory cache.

        Disk I/O and PIL decode happen at most once per session per map.
        The cached CTkImage carries its Tk PhotoImage internally, so page
        switches and grid rebuilds never allocate a new PhotoImage.
        """
        if map_id in self._img_cache:
            return self._img_cache[map_id]
        disk = os.path.join(_THUMB_DIR, f"{map_id}.jpg")
        if os.path.exists(disk):
            try:
                pil = Image.open(disk)
                pil.load()                   # force full decode now, not lazily
                pil = pil.resize((320, 192))
                img = ctk.CTkImage(pil, size=(160, 96))
                self._img_cache[map_id] = img
                return img
            except Exception:
                pass
        img = self._make_placeholder_image(map_id)
        self._img_cache[map_id] = img
        return img

    def _make_map_card(self, parent: ctk.CTkFrame, map_id: str,
                       row: int, col: int, selected: bool = False) -> ctk.CTkFrame:
        """Build a clickable map thumbnail card with screenshot-style image."""
        border_c = self.ACCENT if selected else self.BORDER
        card = ctk.CTkFrame(
            parent, corner_radius=12, border_width=2,
            border_color=border_c, fg_color=self.DEEP, cursor="hand2",
        )
        card.grid(row=row, column=col, sticky="ew",
                  padx=(0, 8) if col < 3 else (0, 0), pady=(0, 8))
        img = self._get_map_image(map_id)
        img_lbl = ctk.CTkLabel(card, text="", image=img, fg_color="transparent")
        img_lbl.pack(padx=5, pady=(5, 0))
        # Stash image-label ref on the card so _refresh_official_card_image
        # can swap the image in without poking CTk-internal attributes.
        card._img_lbl = img_lbl  # type: ignore[attr-defined]
        # Display the raw map_id (de_dust2, de_inferno…) like the mockup
        ctk.CTkLabel(
            card, text=map_id,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.ACCENT if selected else self.TEXT,
            fg_color="transparent", anchor="center",
        ).pack(fill="x", padx=2, pady=(4, 7))
        click = lambda _e, m=map_id: self._select_official_card(m)
        card.bind("<Button-1>", click)
        img_lbl.bind("<Button-1>", click)
        # Kick off async fetch of the real official screenshot if not cached
        if not os.path.exists(os.path.join(_THUMB_DIR, f"{map_id}.jpg")):
            self._fetch_official_thumb(map_id)
        return card

    def _fetch_official_thumb(self, map_id: str) -> None:
        """Background-download a real screenshot for an official map.

        Tries each candidate URL in _OFFICIAL_MAP_URLS in order; first one
        that returns >2 KB of data wins.  Saves to oblivion_thumbs/<map_id>.jpg
        and triggers a card refresh on the main thread.
        """
        urls = _OFFICIAL_MAP_URLS.get(map_id, [])
        if not urls:
            return
        cache = os.path.join(_THUMB_DIR, f"{map_id}.jpg")

        def _do() -> None:
            for url in urls:
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "Mozilla/5.0 OblivionServerTool"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        data = resp.read()
                    if len(data) > 2048:   # sanity: skip error pages / placeholders
                        # Re-encode to JPEG via PIL so any source format works
                        from io import BytesIO
                        pil = Image.open(BytesIO(data)).convert("RGB")
                        pil.save(cache, "JPEG", quality=82)
                        self.root.after(0, self._refresh_official_card_image, map_id)
                        return
                except Exception:
                    continue

        threading.Thread(target=_do, daemon=True).start()

    def _refresh_official_card_image(self, map_id: str) -> None:
        """Swap the placeholder image inside an official-map card for the real one."""
        # Evict stale placeholder so _get_map_image loads the freshly-saved file.
        self._img_cache.pop(map_id, None)
        card = self._map_cards.get(map_id)
        if not card:
            return
        img_lbl = getattr(card, "_img_lbl", None)
        if img_lbl is None:
            return
        try:
            img_lbl.configure(image=self._get_map_image(map_id))
        except Exception:
            pass

    def _rebuild_official_grid(self) -> None:
        """Repopulate the official-map card grid for the current mode."""
        for w in list(self._off_scroll.winfo_children()):
            w.destroy()
        self._map_cards.clear()
        mode = self._mode_var.get() if hasattr(self, "_mode_var") else "Competitive"
        maps = MODE_MAPS.get(mode, OFFICIAL_MAPS)
        if not maps:
            # Mode needs a workshop map only — show informational label
            ctk.CTkLabel(
                self._off_scroll,
                text=f"Workshop map required for {mode}",
                text_color=self.SUB, font=ctk.CTkFont(size=11),
            ).grid(row=0, column=0, columnspan=4, padx=6, pady=18)
            self._off_var.set("")
            return
        current = self._off_var.get()
        if current not in maps:
            current = maps[0]
            self._off_var.set(current)
        for i, m in enumerate(maps):
            row, col = divmod(i, 4)
            card = self._make_map_card(self._off_scroll, m, row, col, selected=(m == current))
            self._map_cards[m] = card

    def _select_official_card(self, map_id: str) -> None:
        """Select an official-map card, deselecting the previous one."""
        old = self._off_var.get()
        if old in self._map_cards:
            self._map_cards[old].configure(border_color=self.BORDER)
        self._off_var.set(map_id)
        if map_id in self._map_cards:
            self._map_cards[map_id].configure(border_color=self.ACCENT)
        self._on_official_select(map_id)

    def _set_official_active_style(self, active: bool) -> None:
        """Highlight or dim the selected official-map card border."""
        sel = self._off_var.get()
        if sel and sel in self._map_cards:
            self._map_cards[sel].configure(
                border_color=self.ACCENT if active else self.BORDER
            )

    # ── workshop card helpers ────────────────────────────────────────────────

    def _get_workshop_image(self, wid: str) -> ctk.CTkImage:
        """Return a CTkImage for a workshop map, backed by an in-memory cache."""
        key = f"ws_{wid}"
        if key in self._img_cache:
            return self._img_cache[key]
        disk = os.path.join(_THUMB_DIR, f"ws_{wid}.jpg")
        if os.path.exists(disk):
            try:
                pil = Image.open(disk)
                pil.load()
                pil = pil.resize((320, 192))
                img = ctk.CTkImage(pil, size=(160, 96))
                self._img_cache[key] = img
                return img
            except Exception:
                pass
        img = self._make_placeholder_image(key)
        self._img_cache[key] = img
        return img

    def _make_workshop_card(self, parent: ctk.CTkFrame, wid: str, label: str,
                            row: int, col: int,
                            selected: bool = False) -> ctk.CTkFrame:
        """Build a clickable workshop-map card with thumbnail + Subscribed tag."""
        border_c = self.ACCENT if selected else self.BORDER
        card = ctk.CTkFrame(
            parent, corner_radius=12, border_width=2,
            border_color=border_c, fg_color=self.DEEP, cursor="hand2",
        )
        card.grid(row=row, column=col, sticky="ew",
                  padx=(0, 8) if col < 3 else (0, 0), pady=(0, 8))

        # ── Image area with workshop-icon overlay in the top-right corner ──
        img_wrap = ctk.CTkFrame(card, fg_color="transparent")
        img_wrap.pack(padx=5, pady=(5, 0))
        img = self._get_workshop_image(wid)
        img_lbl = ctk.CTkLabel(img_wrap, text="", image=img, fg_color="transparent")
        img_lbl.pack()
        # Stash for _refresh_workshop_card_image (avoids poking CTk internals)
        card._img_lbl = img_lbl  # type: ignore[attr-defined]
        # Steam workshop icon: small circular badge with a wrench/gear glyph
        badge = ctk.CTkLabel(
            img_wrap, text="⚒",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#ffffff",
            fg_color=self.ACCENT,
            corner_radius=10,
            width=20, height=20,
        )
        badge.place(relx=1.0, rely=0.0, anchor="ne", x=-3, y=3)

        # Extract the human-readable name from "Name  [id]"; fall back to ID
        name = re.sub(r"\s*\[\d+\]$", "", label).strip() or wid
        display = name if len(name) <= 18 else name[:17] + "…"
        ctk.CTkLabel(
            card, text=display,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.ACCENT if selected else self.TEXT,
            fg_color="transparent", anchor="center",
        ).pack(fill="x", padx=2, pady=(4, 0))

        # "Subscribed" subtitle — these are maps already downloaded
        ctk.CTkLabel(
            card, text="Subscribed",
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=self.ACCENT, fg_color="transparent",
            anchor="center",
        ).pack(fill="x", padx=2, pady=(0, 6))

        click = lambda _e, lbl=label: self._select_workshop_card(lbl)
        for w in (card, img_wrap, img_lbl, badge):
            w.bind("<Button-1>", click)
        return card

    def _rebuild_wk_grid(self, ids: list[str], labels: list[str]) -> None:
        """Repopulate the workshop card grid from the current ID/label lists."""
        for w in list(self._wk_scroll.winfo_children()):
            w.destroy()
        self._wk_cards.clear()

        if not ids:
            ctk.CTkLabel(
                self._wk_scroll,
                text="No workshop maps downloaded yet.\nUse the Workshop tab to grab some.",
                text_color=self.SUB, font=ctk.CTkFont(size=11)
            ).grid(row=0, column=0, columnspan=4, padx=10, pady=24)
            return

        current = self._wk_var.get().strip()
        for i, (wid, lbl) in enumerate(zip(ids, labels)):
            row, col = divmod(i, 4)
            selected = (lbl == current)
            card = self._make_workshop_card(
                self._wk_scroll, wid, lbl, row, col, selected=selected)
            self._wk_cards[lbl] = card
            # Background-fetch the Steam preview image if not already cached
            url = self.core._preview_url_cache.get(wid, "")
            cache = os.path.join(_THUMB_DIR, f"ws_{wid}.jpg")
            if url and not os.path.exists(cache):
                self._fetch_workshop_thumb(wid, url, lbl)

    def _select_workshop_card(self, label: str) -> None:
        """Select a workshop card; deselect the previously selected one."""
        old = self._wk_var.get()
        if old in self._wk_cards:
            self._wk_cards[old].configure(border_color=self.BORDER)
        self._wk_var.set(label)
        if label in self._wk_cards:
            self._wk_cards[label].configure(border_color=self.ACCENT)
        self._on_workshop_select(label)

    def _set_workshop_active_style(self, active: bool) -> None:
        """Highlight/dim the selected workshop card border."""
        sel = self._wk_var.get()
        if sel and sel in self._wk_cards:
            self._wk_cards[sel].configure(
                border_color=self.ACCENT if active else self.BORDER
            )

    def _fetch_workshop_thumb(self, wid: str, url: str, label: str) -> None:
        """Background-download a Steam preview thumbnail and refresh the card."""
        cache = os.path.join(_THUMB_DIR, f"ws_{wid}.jpg")

        def _do() -> None:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = resp.read()
                with open(cache, "wb") as f:
                    f.write(data)
                self.root.after(0, self._refresh_workshop_card_image, wid, label)
            except Exception:
                pass  # Silently fall back to the placeholder

        threading.Thread(target=_do, daemon=True).start()

    def _refresh_workshop_card_image(self, wid: str, label: str) -> None:
        """Replace the placeholder image inside a workshop card with the real one."""
        # Evict stale placeholder so _get_workshop_image loads the freshly-saved file.
        self._img_cache.pop(f"ws_{wid}", None)
        card = self._wk_cards.get(label)
        if not card:
            return
        img_lbl = getattr(card, "_img_lbl", None)
        if img_lbl is None:
            return
        try:
            img_lbl.configure(image=self._get_workshop_image(wid))
        except Exception:
            pass

    # ── mode / workshop ───────────────────────────────────────────────────────

    def _on_mode_change(self, mode: str) -> None:
        """Update Official Map picker whenever the game mode changes."""
        maps = MODE_MAPS.get(mode, OFFICIAL_MAPS)

        if maps is None:
            # Workshop map required — clear official selection and force source
            self._off_var.set("")
            self._map_source = "workshop"
            self._mode_hint_lbl.configure(
                text=f"⚑  {mode} requires a workshop map — select or download one below",
                text_color=self.ORANGE,
            )
        else:
            if self._off_var.get() not in maps:
                self._off_var.set(maps[0])
            # If no workshop map is selected, revert to official automatically
            if not self._wk_var.get().strip():
                self._map_source = "official"
            if maps == OFFICIAL_MAPS:
                self._mode_hint_lbl.configure(text="")
            else:
                self._mode_hint_lbl.configure(
                    text=f"✓  {len(maps)} compatible maps for {mode}",
                    text_color=self.SUB,
                )

        # Rebuild the map card grid for the new mode
        self._rebuild_official_grid()
        # Sync Quick Config map dropdown to mode-compatible maps
        if hasattr(self, "_qc_map_cb"):
            qc_maps = MODE_MAPS.get(mode, OFFICIAL_MAPS) or OFFICIAL_MAPS
            self._qc_map_cb.configure(values=qc_maps)
        self._browse_btn.configure(
            text=f"🔍  Browse {mode} Maps on Workshop"
        )
        self._apply_wk_filter(mode)

    def _patch_dropdown_toggle(self, cb: ctk.CTkComboBox) -> None:
        """Make clicking the dropdown arrow close it when it's already open.

        CTk default: click → open; click again → FocusOut hides/destroys the
        popup, then the button command fires and immediately reopens it.
        We record when the popup disappears and suppress the reopen within
        250 ms.  Handles both old CTk (_button/_clicked) and new CTk
        (_dropdown_button/_dropdown_callback / place_forget vs destroy).
        """
        # Resolve version-specific button attribute
        btn = (getattr(cb, "_dropdown_button", None) or
               getattr(cb, "_button", None))
        orig_open = getattr(cb, "_open_dropdown_menu", None)
        if btn is None or orig_open is None:
            return   # Unknown CTk internals — skip silently

        _closed_at = [0.0]

        def _patched_open() -> None:
            orig_open()
            self.root.after(15, _attach_tracker)

        def _attach_tracker() -> None:
            try:
                dm = getattr(cb, "_dropdown_menu", None)
                if not dm:
                    return
                stamp = lambda _e: _closed_at.__setitem__(0, time.time())
                # <Destroy> fires when the widget is destroyed (older CTk)
                # <Unmap>   fires when place_forget() hides it (newer CTk)
                for ev in ("<Destroy>", "<Unmap>"):
                    try:
                        dm.bind(ev, stamp, add=True)
                    except Exception:
                        pass
            except Exception:
                pass

        cb._open_dropdown_menu = _patched_open

        def _guarded_click() -> None:
            if time.time() - _closed_at[0] < 0.25:
                return
            # Call whichever click handler this CTk version exposes
            clicked = (getattr(cb, "_dropdown_callback", None) or
                       getattr(cb, "_clicked", None))
            if clicked:
                clicked()

        try:
            btn.configure(command=_guarded_click)
        except Exception:
            pass

    def _patch_option_menu_toggle(self, om: ctk.CTkOptionMenu) -> None:
        """Make CTkOptionMenu close its dropdown when clicked while already open.

        CTkOptionMenu routes all opens through ``_open_dropdown_menu()``.
        We wrap that method: the first time it is called we attach Unmap/Destroy
        trackers to the popup frame so we can record when it closes.  On the
        next call, if the popup just closed (<250 ms ago) we suppress the reopen
        — the net effect is that a second click on the button dismisses rather
        than re-shows the dropdown.
        """
        orig_open = getattr(om, "_open_dropdown_menu", None)
        if orig_open is None:
            return   # Unknown CTk internals — skip silently

        _closed_at = [0.0]

        def _patched_open() -> None:
            # If the popup just closed (user clicked the arrow to dismiss),
            # swallow this open so the dropdown stays closed.
            if time.time() - _closed_at[0] < 0.25:
                return
            orig_open()
            self.root.after(15, _attach_tracker)

        def _attach_tracker() -> None:
            try:
                dm = getattr(om, "_dropdown_menu", None)
                if not dm:
                    return
                stamp = lambda _e: _closed_at.__setitem__(0, time.time())
                for ev in ("<Destroy>", "<Unmap>"):
                    try:
                        dm.bind(ev, stamp, add=True)
                    except Exception:
                        pass
            except Exception:
                pass

        om._open_dropdown_menu = _patched_open

    def _on_official_select(self, _value: str) -> None:
        """User explicitly chose an official map — make it the active source."""
        self._map_source = "official"
        # Keep card grid in sync when the user picked via the Quick Config dropdown
        if hasattr(self, "_map_cards"):
            for mid, card in self._map_cards.items():
                card.configure(border_color=self.ACCENT if mid == _value else self.BORDER)
        self._update_map_selection_ui()

    def _on_max_players_quickset(self, value: str) -> None:
        """Quick Config max-players dropdown changed — push to core + log."""
        self.core.max_players_override = value.strip()
        self.core.log(f"Max players override set to {value}")

    def _on_workshop_select(self, _value: str) -> None:
        """User explicitly chose a workshop map — make it the active source."""
        self._map_source = "workshop"
        self._update_map_selection_ui()

    def _update_map_selection_ui(self) -> None:
        """Sync border colours, label brightness, and the launch-preview chip."""
        mode_var = getattr(self, "_mode_var", None)
        mode     = mode_var.get() if mode_var else ""

        # Section headers stay equally bright — they're titles, not state cues.
        # Which source is "active" is conveyed by the selected card border instead.
        self._off_lbl_w.configure(text_color=self.TEXT)
        self._wk_lbl_w.configure(text_color=self.TEXT)

        if self._map_source == "workshop":
            wk = self._wk_var.get().strip()
            # Strip "  [id]" suffix — map name alone is enough for the chip
            wk_name = re.sub(r"\s*\[\d+\]$", "", wk) if wk else ""
            if wk_name:
                preview = f"▶  {wk_name}  ·  {mode}" if mode else f"▶  {wk_name}"
            else:
                preview = "▶  (no workshop map selected)"
            self._set_official_active_style(False)
            self._set_workshop_active_style(True)
        else:
            off     = self._off_var.get().strip() or "—"
            preview = f"▶  {off}  ·  {mode}" if mode else f"▶  {off}"
            self._set_official_active_style(True)
            self._set_workshop_active_style(False)

        # Guard: preview label doesn't exist on the first call during construction
        if hasattr(self, "_map_preview_lbl"):
            self._map_preview_lbl.configure(text=preview)

    def _refresh_wk(self) -> None:
        from . import config as _cfg
        self.core.log(f"Workshop scan: {_cfg.WORKSHOP_DIR}")
        ids = load_workshop()
        self.core.log(f"Workshop scan: {len(ids)} map(s) found")
        self._wk_all_ids    = ids
        self._wk_all_labels = list(ids)  # bare-ID labels while names load
        # Build the grid immediately so the UI doesn't sit empty during the lookup
        self._rebuild_wk_grid(ids, self._wk_all_labels)

        def _on_names_done() -> None:
            labels = []
            for wid in ids:
                name = self.core._map_name_cache.get(wid, "")
                labels.append(f"{name}  [{wid}]" if name else wid)

            def _apply() -> None:
                self._wk_all_labels = labels
                # Upgrade any bare-ID selection to "Name  [id]" now that names loaded
                current = self._wk_var.get().strip()
                for i, wid in enumerate(ids):
                    if current == wid:
                        self._wk_var.set(labels[i])
                        break
                self._apply_wk_filter(self._mode_var.get())

            self.root.after(0, _apply)

        self.core.fetch_workshop_names(ids, on_done=_on_names_done)

    def _apply_wk_filter(self, mode: str) -> None:
        """Show only workshop maps whose tags match the current game mode.

        Maps with no tags are always included — we can't exclude what isn't
        labelled.  If zero maps match the filter, fall back to showing all of
        them so the user isn't left with an empty picker.
        """
        from .config import MODE_WORKSHOP_TAGS
        ids    = self._wk_all_ids
        labels = self._wk_all_labels
        if not ids:
            return

        mode_tags = MODE_WORKSHOP_TAGS.get(mode, [])

        if not mode_tags:
            # No tag definition for this mode → show everything
            filtered_labels = labels
        else:
            matched = [
                label for wid, label in zip(ids, labels)
                if not self.core._map_tag_cache.get(wid)          # untagged → always show
                or any(mt in self.core._map_tag_cache[wid]        # any tag matches
                       for mt in mode_tags)
            ]
            if matched:
                filtered_labels = matched
                self.core.log(
                    f"Workshop filter ({mode}): {len(matched)} of {len(ids)} map(s)"
                )
            else:
                # No matches at all — show everything with a note
                filtered_labels = labels
                self.core.log(
                    f"Workshop filter ({mode}): no tag matches — showing all {len(ids)}"
                )

        # If the currently selected map was filtered out, deselect it
        current = self._wk_var.get().strip()
        if current and current not in filtered_labels:
            self._wk_var.set("")
            if self._map_source == "workshop":
                self._map_source = "official"

        # Rebuild the card grid with the filtered set (matching IDs)
        filtered_ids = [
            wid for wid, lbl in zip(ids, labels) if lbl in filtered_labels
        ]
        self._rebuild_wk_grid(filtered_ids, filtered_labels)
        self._update_map_selection_ui()

    def _selected_map(self) -> tuple[str, bool]:
        if self._map_source == "workshop":
            wk = self._wk_var.get().strip()
            # Extract bare numeric ID from "Map Name  [123456]" format
            m = re.search(r'\[(\d+)\]', wk)
            raw_id = m.group(1) if m else wk
            return (raw_id, True)
        return (self._off_var.get().strip(), False)

    def _sync_status_bar(self) -> None:
        """Update status-bar labels from AppCore state (main-thread only)."""
        self._sb_map.configure( text=self.core.current_map  if self.core.running else "—")
        self._sb_mode.configure(text=self.core.current_mode if self.core.running else "—")

    def _set_status_badge(self, text: str, color: str) -> None:
        """Update the pill-shaped status badge in the Server card header."""
        self._dot.configure(text=text)
        if hasattr(self, "_sc_badge_wrap") and self._sc_badge_wrap is not None:
            self._sc_badge_wrap.configure(fg_color=color)

    def _on_core_state_change(self) -> None:
        """Called on the main thread whenever AppCore.boot_state changes."""
        self._set_state(self.core.boot_state)

    def _boot_pulse(self) -> None:
        """Animate the header dot while the server is booting."""
        if self.core.boot_state != "booting":
            return
        frames = ["BOOTING ·  ", "BOOTING ·· ", "BOOTING ···"]
        self._set_status_badge(frames[self._pulse_step % 3], self.ORANGE)
        self._pulse_step += 1
        self.root.after(500, self._boot_pulse)

    # ── uptime ticker ─────────────────────────────────────────────────────────

    def _tick_uptime(self) -> None:
        if self._uptime_start is not None and self.core.running:
            secs  = int(time.time() - self._uptime_start)
            h, r  = divmod(secs, 3600)
            m, s  = divmod(r, 60)
            self._sb_uptime.configure(text=f"{h:02d}:{m:02d}:{s:02d}")
        self.root.after(1000, self._tick_uptime)

    # ── button handlers ───────────────────────────────────────────────────────

    def _start(self) -> None:
        m, is_wk = self._selected_map()
        self.core.start_server(m, self._mode_var.get(), is_wk)
        if self.core.running:
            self._uptime_start = time.time()
            self._set_state("booting")
            self._boot_pulse()

    def _stop(self) -> None:
        self.core.stop_server()
        self._uptime_start = None
        self._set_state("offline")

    def _change(self) -> None:
        m, is_wk = self._selected_map()
        self.core.change_map(m, self._mode_var.get(), is_wk, caller="local")

    def _check_update_btn(self) -> None:
        """User clicked Update — run the check and show result."""
        self._manual_update_check = True
        self._upd_btn.configure(
            state="disabled", text="Checking…",
            fg_color=self.BORDER, text_color=self.SUB,
        )
        self.core.check_update()

    def _on_update_checked(self, available: bool,
                            installed: str, latest: str) -> None:
        """Fires on the main thread when check_update() finishes."""
        if available:
            self._upd_btn.configure(
                state="normal",
                fg_color="#d97706", hover_color=self.ORANGE,
                text_color="#0d0d14", text="⬆  Update!",
            )
            # Show both installed and latest so version mismatch is obvious
            self._sb_build.configure(
                text=f"{installed} → {latest}",
                text_color=self.ORANGE,
            )
            if self._manual_update_check:
                self._show_update_dialog(installed, latest)
        else:
            self._upd_btn.configure(
                state="normal",
                fg_color=self.BORDER, hover_color="#2a2a40",
                text_color=self.SUB, text="⟳  Update",
            )
            if installed != "unknown":
                self._sb_build.configure(
                    text=installed, text_color=self.TEXT)
        self._manual_update_check = False

    def _show_update_dialog(self, installed: str, latest: str) -> None:
        """Modal dialog: shows version info and offers to update now."""
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Update Available")
        dlg.geometry("400x230")
        dlg.configure(fg_color=self.CARD)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        ctk.CTkFrame(dlg, fg_color=self.ORANGE,
                     corner_radius=0, height=2).pack(fill="x")
        ctk.CTkLabel(dlg, text="UPDATE AVAILABLE",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.ORANGE).pack(pady=(20, 10))
        ctk.CTkLabel(dlg,
                     text=f"Installed build :  {installed}",
                     font=ctk.CTkFont(size=12),
                     text_color=self.SUB).pack()
        ctk.CTkLabel(dlg,
                     text=f"Latest build    :  {latest}",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=self.TEXT).pack(pady=(2, 24))

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack()

        def _do_update() -> None:
            dlg.destroy()
            self._run_update_now()

        ctk.CTkButton(
            row, text="⬇  Update Now",
            fg_color=self.ORANGE, hover_color="#d97706",
            text_color="#0d0d14", width=160,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_do_update,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            row, text="Later",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, width=80,
            font=ctk.CTkFont(size=12),
            command=dlg.destroy,
        ).pack(side="left")

    def _run_update_now(self) -> None:
        """Kick off steamcmd update after the user confirmed."""
        self._upd_btn.configure(
            state="disabled", text="⬇   UPDATING…",
            fg_color=self.BORDER, text_color=self.SUB,
        )
        self.core.run_update(
            on_done=lambda: self.root.after(0, self._on_update_done)
        )

    def _on_update_done(self) -> None:
        """Re-enable the button after steamcmd finishes, then re-check version."""
        self._upd_btn.configure(
            state="normal",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, text="⟳  Update",
        )
        # Re-check so the status bar shows the new build number immediately
        self._sb_build.configure(text="checking…", text_color=self.SUB)
        self.core.check_update()

    # ── App self-update ───────────────────────────────────────────────────────

    def _on_app_update_checked(self, available: bool,
                                current: str, latest: str, url: str) -> None:
        """Fires on the main thread when check_app_update() finishes."""
        if available:
            self._app_upd_url = url
            self._app_upd_lbl.configure(
                text=f"⬆  App v{latest} available — click to download",
            )
            self.core.log(f"Oblivion Tool update: v{latest} is out (you have v{current})")
            self.core.log(f"  Download: {url}")

    def _open_app_release(self) -> None:
        if self._app_upd_url:
            webbrowser.open(self._app_upd_url)

    # ── Steam account dialogs ─────────────────────────────────────────────────

    def _show_setup_dialog(self) -> None:
        """First-run dialog: ask for the CS2 server directory."""
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("First-Time Setup")
        dlg.geometry("480x300")
        dlg.configure(fg_color=self.CARD)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        ctk.CTkFrame(dlg, fg_color=self.ACCENT,
                     corner_radius=0, height=2).pack(fill="x")
        ctk.CTkLabel(dlg, text="WELCOME — FIRST TIME SETUP",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.ACCENT).pack(pady=(20, 6))
        ctk.CTkLabel(dlg,
                     text="Select the folder where steamcmd.exe is installed.\n"
                          "All other paths (CS2 server, workshop maps, etc.) are\n"
                          "derived automatically from this single location.",
                     font=ctk.CTkFont(size=12), text_color=self.SUB,
                     justify="center").pack(pady=(0, 16))

        dir_row = ctk.CTkFrame(dlg, fg_color="transparent")
        dir_row.pack(fill="x", padx=24, pady=(0, 6))
        dir_var = ctk.StringVar()
        dir_entry = ctk.CTkEntry(
            dir_row, textvariable=dir_var,
            placeholder_text=r"e.g. C:\cs2server",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=13),
        )
        dir_entry.pack(side="left", fill="x", expand=True)

        def _browse() -> None:
            path = tkinter.filedialog.askdirectory(
                title="Select folder containing steamcmd.exe",
                initialdir="C:\\",
            )
            if path:
                dir_var.set(path.replace("/", "\\"))

        ctk.CTkButton(
            dir_row, text="Browse", width=80, height=34,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.TEXT, font=ctk.CTkFont(size=12),
            command=_browse,
        ).pack(side="right", padx=(8, 0))

        err_lbl = ctk.CTkLabel(dlg, text="",
                                font=ctk.CTkFont(size=11), text_color=self.RED)
        err_lbl.pack(pady=(0, 8))

        def _confirm() -> None:
            path = dir_var.get().strip().replace("/", "\\")
            if not path:
                err_lbl.configure(text="Please select a directory.")
                return
            import os as _os
            if not _os.path.isdir(path):
                err_lbl.configure(text="Directory does not exist.")
                return
            self.core.update_server_dir(path)
            self._server_dir_var.set(path)
            self.core.save_config()
            self._refresh_wk()
            dlg.destroy()
            self.core.log("Setup complete — you can change this anytime in Config → Save Settings")
            # If steamcmd.exe is not present, offer to install the server
            from . import config as _cfg
            if not _os.path.isfile(_cfg.STEAMCMD_PATH):
                self.root.after(150, self._show_install_offer)

        ctk.CTkButton(
            dlg, text="Confirm & Continue",
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14", height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_confirm,
        ).pack(padx=24, fill="x", pady=(0, 20))

    def _show_steam_account_dialog(self) -> None:
        """Settings dialog for storing Steam credentials used by workshop downloads."""
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Steam Account")
        dlg.geometry("420x540")
        dlg.configure(fg_color=self.CARD)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        ctk.CTkFrame(dlg, fg_color=self.ACCENT,
                     corner_radius=0, height=2).pack(fill="x")
        ctk.CTkLabel(dlg, text="STEAM ACCOUNT",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.ACCENT).pack(pady=(18, 4))
        ctk.CTkLabel(dlg,
                     text="⚠  Use a DEDICATED server account, not your personal one.\n"
                          "steamcmd will disconnect your Steam desktop client\n"
                          "if they share the same account.\n\n"
                          "CS2 is free — create a second account at store.steampowered.com\n"
                          "and add it here. Server updates always use anonymous login.",
                     font=ctk.CTkFont(size=12), text_color=self.SUB,
                     justify="center").pack(pady=(0, 14))

        # Username
        ctk.CTkLabel(dlg, text="Steam Username",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=24)
        user_var = ctk.StringVar(value=self.core.steam_username)
        ctk.CTkEntry(dlg, textvariable=user_var,
                     placeholder_text="Your Steam username",
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, placeholder_text_color=self.SUB,
                     font=ctk.CTkFont(size=13),
                     ).pack(fill="x", padx=24, pady=(2, 10))

        # Password
        ctk.CTkLabel(dlg, text="Steam Password",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=24)
        pass_var = ctk.StringVar(value=self.core.steam_password)
        ctk.CTkEntry(dlg, textvariable=pass_var, show="●",
                     placeholder_text="Your Steam password",
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, placeholder_text_color=self.SUB,
                     font=ctk.CTkFont(size=13),
                     ).pack(fill="x", padx=24, pady=(2, 14))

        status_lbl = ctk.CTkLabel(dlg, text="", font=ctk.CTkFont(size=12))
        if self.core.steam_username:
            status_lbl.configure(
                text=f"Saved: '{self.core.steam_username}'",
                text_color=self.GREEN)
        else:
            status_lbl.configure(
                text="No credentials — anonymous login will be used",
                text_color=self.SUB)
        status_lbl.pack(pady=(0, 12))

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack()

        def _save() -> None:
            self.core.steam_username = user_var.get().strip()
            self.core.steam_password = pass_var.get()
            self.core.save_config()
            if self.core.steam_username:
                status_lbl.configure(
                    text=f"Saved: '{self.core.steam_username}'",
                    text_color=self.GREEN)
            else:
                status_lbl.configure(
                    text="Cleared — anonymous login will be used",
                    text_color=self.SUB)

        def _clear() -> None:
            user_var.set("")
            pass_var.set("")
            self.core.steam_username      = ""
            self.core.steam_password      = ""
            self.core.steam_session_active = False
            self.core.save_config()
            if self.core.on_steam_session_change:
                self.core.on_steam_session_change()
            status_lbl.configure(
                text="Cleared — anonymous login will be used",
                text_color=self.SUB)

        ctk.CTkButton(
            row, text="Save", width=100,
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_save,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row, text="Clear", width=80,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=_clear,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row, text="Close", width=80,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=dlg.destroy,
        ).pack(side="left")

        # Interactive login section — for one-time 2FA setup
        ctk.CTkFrame(dlg, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=24, pady=(16, 0))
        ctk.CTkLabel(
            dlg,
            text="If workshop downloads get stuck on 2FA, run an interactive\n"
                 "login once so steamcmd can cache your session.",
            font=ctk.CTkFont(size=11), text_color=self.SUB,
            justify="center",
        ).pack(pady=(8, 6))
        ctk.CTkButton(
            dlg, text="🖥  Login Interactively (opens console for 2FA)",
            height=32, corner_radius=8,
            fg_color=self.BLUE, hover_color=self.BLUE_H,
            text_color=self.TEXT, font=ctk.CTkFont(size=11, weight="bold"),
            command=lambda: (dlg.destroy(), self.core.steam_login_interactive()),
        ).pack(padx=24, pady=(0, 16))

    def _show_guard_dialog(self, prompt_type: str,
                            submit: Callable[[str], None]) -> None:
        """Modal dialog for entering a Steam Guard or 2FA code."""
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Steam Guard")
        dlg.geometry("380x250")
        dlg.configure(fg_color=self.CARD)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        ctk.CTkFrame(dlg, fg_color=self.ORANGE,
                     corner_radius=0, height=2).pack(fill="x")
        ctk.CTkLabel(dlg, text="STEAM GUARD REQUIRED",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.ORANGE).pack(pady=(20, 8))

        if prompt_type == "2fa":
            desc = "Open your Steam mobile app and enter\nthe two-factor authenticator code."
        else:
            desc = "Check your email for a Steam Guard code\nand enter it below."
        ctk.CTkLabel(dlg, text=desc,
                     font=ctk.CTkFont(size=12), text_color=self.SUB,
                     justify="center").pack(pady=(0, 14))

        code_var   = ctk.StringVar()
        code_entry = ctk.CTkEntry(
            dlg, textvariable=code_var,
            placeholder_text="Enter code…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=16, weight="bold"),
            justify="center", width=180,
        )
        code_entry.pack(pady=(0, 18))
        code_entry.focus_set()

        def _submit() -> None:
            submit(code_var.get().strip())
            dlg.destroy()

        code_entry.bind("<Return>", lambda _e: _submit())
        ctk.CTkButton(
            dlg, text="Submit Code", width=140,
            fg_color=self.ORANGE, hover_color="#d97706",
            text_color="#0d0d14",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_submit,
        ).pack()

    # ── server install helpers ────────────────────────────────────────────────

    def _show_install_offer(self) -> None:
        """Modal: steamcmd not found — offer to download and install CS2 server."""
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Install CS2 Server?")
        dlg.geometry("460x290")
        dlg.configure(fg_color=self.CARD)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        ctk.CTkFrame(dlg, fg_color=self.BLUE,
                     corner_radius=0, height=2).pack(fill="x")
        ctk.CTkLabel(dlg, text="CS2 SERVER NOT FOUND",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.BLUE).pack(pady=(20, 8))
        ctk.CTkLabel(
            dlg,
            text="steamcmd.exe was not found in the selected directory.\n\n"
                 "Would you like to download and install the\n"
                 "CS2 dedicated server automatically?\n\n"
                 "Step 1 — downloads steamcmd from Valve  (~1 MB)\n"
                 "Step 2 — downloads CS2 server files  (~15 GB, 10-30 min)",
            font=ctk.CTkFont(size=12), text_color=self.SUB,
            justify="center",
        ).pack(pady=(0, 20))

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack()

        def _do_install() -> None:
            dlg.destroy()
            self._install_server()

        ctk.CTkButton(
            btn_row, text="⬇  Install Now",
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14", width=160,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_do_install,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            btn_row, text="Skip",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, width=80,
            font=ctk.CTkFont(size=12),
            command=dlg.destroy,
        ).pack(side="left")

    def _install_server(self) -> None:
        """Start full CS2 server installation in the background."""
        if not self.core.server_dir:
            self.core.log("[!] Set the server directory first (Config tab → Save Settings)")
            return
        self.core.log("╔══════════════════════════════════════════════╗")
        self.core.log("║  CS2 SERVER INSTALLATION STARTED             ║")
        self.core.log("╚══════════════════════════════════════════════╝")
        self.core.log("  Watch the log for progress — this takes a while.")
        self.core.install_server(
            on_done=lambda: self.root.after(0, self._on_install_done)
        )

    def _on_install_done(self) -> None:
        """Called on the main thread when install_server() finishes."""
        self.core.log("Installation complete ✓")
        self.core.log("  You can now start the server with the ▶ START SERVER button.")
        self._refresh_wk()   # pick up any workshop maps that were already there

    def _set_state(self, state: str) -> None:
        """Update every piece of UI that reflects server state.

        state: "offline" | "booting" | "ready"
        """
        running = state != "offline"
        self._start_btn.configure(state="disabled" if running     else "normal")
        self._stop_btn.configure( state="normal"   if running     else "disabled")
        self._chg_btn.configure(  state="normal"   if state == "ready" else "disabled")
        def _glow(cv: tkinter.Canvas, color: str | None) -> None:
            cv._glow_color = color
            w, h = cv.winfo_width(), cv.winfo_height()
            if w > 4 and h > 4:
                self._redraw_btn_glow(cv, w, h)

        if state == "offline":
            self._set_status_badge("OFFLINE", self.RED)
            if self._ff_btn:
                self._ff_btn.configure(state="disabled")
            # START active (violet), STOP + CHANGE inactive (grey)
            self._start_btn.configure(fg_color=self.ACCENT)
            self._stop_btn.configure( fg_color=self.NEUTRAL)
            self._chg_btn.configure(  fg_color=self.NEUTRAL)
            _glow(self._start_cv, "#bf5fff")   # violet inward bloom
            _glow(self._stop_cv,  None)
            _glow(self._chg_cv,   None)
        elif state == "booting":
            self._set_status_badge("BOOTING", self.ORANGE)
            if self._ff_btn:
                self._ff_btn.configure(state="disabled")
            # STOP active (red), START + CHANGE inactive (grey)
            self._start_btn.configure(fg_color=self.NEUTRAL)
            self._stop_btn.configure( fg_color=self.STOP)
            self._chg_btn.configure(  fg_color=self.NEUTRAL)
            _glow(self._start_cv, None)
            _glow(self._stop_cv,  "#ff4d6d")   # coral-red inward bloom
            _glow(self._chg_cv,   None)
        else:
            self._set_status_badge("ONLINE", self.GREEN)
            if self._ff_btn:
                self._ff_btn.configure(state="normal")
            # STOP (red) + CHANGE (blue) active, START inactive (grey)
            self._start_btn.configure(fg_color=self.NEUTRAL)
            self._stop_btn.configure( fg_color=self.STOP)
            self._chg_btn.configure(  fg_color=self.BLUE)
            _glow(self._start_cv, None)
            _glow(self._stop_cv,  "#ff4d6d")
            _glow(self._chg_cv,   "#38bdf8")   # sky-blue inward bloom
        self._sync_status_bar()
        if not running:
            self._sb_uptime.configure(text="—")

    # ── RCON console ──────────────────────────────────────────────────────────

    def _send_rcon(self) -> None:
        cmd = self._rcon_var.get().strip()
        if not cmd:
            return
        self._rcon_var.set("")
        if not self.core.running:
            self._append_rcon("[!] Server is not running")
            return
        self._append_rcon(f"› {cmd}")
        def _do() -> None:
            try:
                resp = self.core.rcon.execute(cmd)
                self.root.after(0, self._append_rcon, resp.strip() or "(no output)")
            except ConnectionRefusedError:
                self.root.after(0, self._append_rcon,
                                "[!] RCON not ready — server is still loading, "
                                "wait ~30 s and try again")
            except ConnectionError as exc:
                self.root.after(0, self._append_rcon, f"[!] RCON: {exc}")
            except Exception as exc:
                self.root.after(0, self._append_rcon, f"[err] {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── RCON diagnostic ───────────────────────────────────────────────────────

    def _test_rcon(self) -> None:
        """Full two-phase RCON diagnostic — runs on a background thread."""
        self._append_rcon(f"— Testing RCON at {RCON_HOST}:{RCON_PORT} —")
        def _do() -> None:
            # Phase 1: raw TCP probe
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(3)
                    s.connect((RCON_HOST, RCON_PORT))
            except ConnectionRefusedError:
                self.root.after(0, self._append_rcon,
                    f"[✗] Port {RCON_PORT} REFUSED\n"
                    "    → Server is not running, or Windows Firewall is\n"
                    "      blocking TCP on this port.\n"
                    "    Fix: Start the server, or add a Windows Firewall\n"
                    f"      inbound rule for TCP port {RCON_PORT}.")
                return
            except OSError as exc:
                self.root.after(0, self._append_rcon, f"[✗] TCP error: {exc}")
                return

            self.root.after(0, self._append_rcon,
                            f"[✓] Port {RCON_PORT} is OPEN")

            # Phase 2: RCON auth
            try:
                resp = self.core.rcon.execute("status")
                self.root.after(0, self._append_rcon,
                    f"[✓] RCON auth OK — server is ready\n"
                    + (resp.strip()[:300] if resp.strip() else "(no status output)"))
            except ConnectionError as exc:
                self.root.after(0, self._append_rcon,
                    f"[✗] Port open but RCON handshake failed: {exc}\n"
                    "    → Wrong rcon_password, or server still initialising.")
            except Exception as exc:
                self.root.after(0, self._append_rcon, f"[✗] RCON error: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── local workshop download ───────────────────────────────────────────────

    @staticmethod
    def _parse_workshop_id(raw: str) -> str | None:
        """Extract a numeric workshop ID from a bare ID or a full Steam URL.

        Accepts:
          - Plain numeric ID :  "3070923712"
          - Workshop URL     :  "https://steamcommunity.com/sharedfiles/filedetails/?id=3070923712"
          - Any URL with     :  …?id=<digits>… or …&id=<digits>…

        Returns the numeric ID string, or None if nothing parseable was found.
        """
        raw = raw.strip()
        if raw.isdigit():
            return raw
        # Extract ?id= or &id= from any URL query string
        m = re.search(r'[?&]id=(\d+)', raw)
        if m:
            return m.group(1)
        return None

    def _local_dl(self) -> None:
        raw = self._wsid_var.get().strip()
        wid = self._parse_workshop_id(raw)
        if not wid:
            self._wsid_lbl.configure(
                text="⚠  Enter a Workshop ID or paste a Steam Workshop URL",
                text_color=self.RED)
            return
        self._wsid_var.set("")
        self._wsid_lbl.configure(text=f"Downloading {wid}…", text_color=self.SUB)
        self._cancel_dl_btn.configure(
            state="normal",
            fg_color=self.STOP, hover_color=self.STOP_H,
            text_color=self.TEXT,
        )
        self.core.approve_download(
            wid,
            on_done=lambda ok: self.root.after(0, self._on_dl_done, wid, ok),
        )

    def _on_dl_done(self, wid: str, success: bool) -> None:
        self._cancel_dl_btn.configure(
            state="disabled",
            fg_color=self.BORDER, hover_color=self.BORDER,
            text_color=self.BORDER,   # back to invisible
        )
        if success:
            self._wsid_lbl.configure(text=f"✓  {wid} downloaded", text_color=self.GREEN)
            self._refresh_wk()
        else:
            self._wsid_lbl.configure(text=f"✗  {wid} failed — see log", text_color=self.RED)

# ── workshop browser / update / plugin handlers ───────────────────────────

    def _browse_workshop(self) -> None:
        """Open Steam Workshop in the default browser, filtered by mode search term."""
        mode   = self._mode_var.get()
        search = MODE_WORKSHOP_SEARCH.get(mode, "")
        url    = _WS_BROWSE
        if search:
            url += "&searchtext=" + urllib.parse.quote(search)
        self.core.log(f"Opening Steam Workshop ({mode}): {url}")
        webbrowser.open(url)

    def _check_map_updates(self) -> None:
        self.core.check_workshop_updates()

    def _check_plugins(self) -> None:
        self.core.check_plugins()

    def _deploy_plugins(self) -> None:
        """Deploy bundled plugins for the currently selected game mode."""
        mode = self._mode_var.get()
        self._deploy_btn.configure(
            state="disabled", text="Deploying…",
            fg_color=self.BORDER, text_color=self.SUB,
        )
        self.core.deploy_plugins_async(
            mode,
            on_done=lambda ok: self.root.after(0, self._on_deploy_done, mode, ok),
        )

    def _on_deploy_done(self, mode: str, ok: bool) -> None:
        if ok:
            self._deploy_btn.configure(
                state="normal",
                text=f"✓  Deployed: {mode}",
                fg_color=self.GREEN, hover_color=self.GREEN_H,
                text_color="#0d0d14",
            )
            # Reset to default after 4 seconds
            self.root.after(4000, lambda: self._deploy_btn.configure(
                state="normal",
                text="⚡  Deploy Plugins for Current Mode",
                fg_color=self.ACCENT, hover_color=self.ACCENT_H,
                text_color="#0d0d14",
            ))
        else:
            self._deploy_btn.configure(
                state="normal",
                text="✗  Deploy failed — see console log",
                fg_color=self.STOP, hover_color=self.STOP_H,
                text_color=self.TEXT,
            )
            self.root.after(4000, lambda: self._deploy_btn.configure(
                state="normal",
                text="⚡  Deploy Plugins for Current Mode",
                fg_color=self.ACCENT, hover_color=self.ACCENT_H,
                text_color="#0d0d14",
            ))

    def _open_web_panel(self) -> None:
        url = f"http://localhost:{FLASK_PORT}"
        self.core.log(f"Opening web panel: {url}")
        webbrowser.open(url)

    # ── process monitor ───────────────────────────────────────────────────────

    def _start_monitor(self) -> None:
        """Daemon thread: detects unexpected server process death."""
        def _watch() -> None:
            while True:
                time.sleep(2)
                if (self.core.running
                        and self.core.proc is not None
                        and self.core.proc.poll() is not None):
                    self.core.proc       = None
                    self.core.running    = False
                    self.core.boot_state = "offline"
                    self._uptime_start   = None
                    self.core.log("Server process exited unexpectedly")
                    self.root.after(0, self._set_state, "offline")
                    # Crash notification: bell + bring window to front
                    self.root.after(100, self.root.bell)
                    self.root.after(200, self.root.lift)
        threading.Thread(target=_watch, daemon=True).start()

    # ── workshop download approval dialog (from web requests) ─────────────────

    def _show_dl_dialog(self, workshop_id: str, requester: str) -> None:
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Workshop Download Request")
        dlg.geometry("420x240")
        dlg.configure(fg_color=self.CARD)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        ctk.CTkFrame(dlg, fg_color=self.ACCENT, corner_radius=0, height=2).pack(fill="x")
        ctk.CTkLabel(dlg, text="WORKSHOP DOWNLOAD REQUEST",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=self.ACCENT).pack(pady=(20, 4))
        ctk.CTkLabel(dlg, text=f"Requested by:  {requester}",
                     font=ctk.CTkFont(size=11), text_color=self.SUB).pack()
        ctk.CTkLabel(dlg, text=f"Workshop ID:  {workshop_id}",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.TEXT).pack(pady=(4, 20))

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack()

        def _approve() -> None:
            dlg.destroy()
            self.core.approve_download(
                workshop_id,
                on_done=lambda _ok: self.root.after(0, self._refresh_wk),
            )

        def _reject() -> None:
            self.core.reject_download(workshop_id, requester=requester)
            dlg.destroy()

        ctk.CTkButton(
            row, text="✓  Approve & Download",
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            width=190, font=ctk.CTkFont(size=12, weight="bold"),
            command=_approve,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            row, text="✕  Reject",
            fg_color="#333", hover_color="#555",
            width=100, font=ctk.CTkFont(size=12),
            command=_reject,
        ).pack(side="left")

    # ── public IP ─────────────────────────────────────────────────────────────

    def _on_public_ip(self, ip: str) -> None:
        self._pub_ip_lbl.configure(
            text=f"📋  {ip}:{RCON_PORT}", text_color=self.TEXT)

    def _copy_public_ip(self) -> None:
        ip = self.core.public_ip
        if not ip:
            return
        text = f"{ip}:{RCON_PORT}"
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.core.log(f"Copied public IP: {text}")
        # Visual confirmation — flash green then revert
        self._pub_ip_lbl.configure(text="✓  Copied!", text_color=self.GREEN)
        self.root.after(2000, lambda: self._pub_ip_lbl.configure(
            text=f"📋  {ip}:{RCON_PORT}", text_color=self.TEXT))

    # ── log export ────────────────────────────────────────────────────────────

    def _export_log(self) -> None:
        path = tkinter.filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"oblivion_log_{time.strftime('%Y%m%d_%H%M%S')}.txt",
        )
        if not path:
            return
        lines = self.core.get_log()
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.core.log(f"Log exported: {path}")
        except Exception as exc:
            self.core.log(f"Log export failed: {exc}")

    # ── friendly fire toggle ──────────────────────────────────────────────────

    def _update_steam_btn(self) -> None:
        """Reflect steam_session_active on the Steam utility button."""
        if self.core.steam_session_active and self.core.steam_username:
            self._steam_btn.configure(
                fg_color=self.GREEN, hover_color="#16a34a",
                text_color="#0d0d14",
                text=f"✓  {self.core.steam_username}",
            )
        else:
            self._steam_btn.configure(
                fg_color=self.BORDER, hover_color="#2a2a40",
                text_color=self.SUB,
                text="🔑  Steam",
            )

    def _toggle_ff(self) -> None:
        new_state = not self.core._ff_enabled
        self.core.set_friendly_fire(new_state)
        if new_state:
            # ON: orange fill, dark text — reads as "armed"
            self._ff_btn.configure(
                text="⊙\nFF ON",
                fg_color=self.ORANGE, hover_color="#d97706",
                text_color="#0d0d14",
            )
        else:
            # OFF: same neutral tile look as the other action buttons
            self._ff_btn.configure(
                text="⊘\nFF OFF",
                fg_color=self.DEEP, hover_color="#15151f",
                text_color=self.TEXT,
            )

    # ── server chat broadcast ─────────────────────────────────────────────────

    def _send_chat(self) -> None:
        msg = self._chat_var.get().strip()
        if not msg:
            return
        self._chat_var.set("")
        if not self.core.running:
            self.core.log("[!] Server not running — cannot broadcast chat")
            return
        self.core.server_say(msg)

    # ── player list ───────────────────────────────────────────────────────────

    def _refresh_players(self) -> None:
        if not self.core.running:
            self._player_status_lbl.configure(text="Server offline", text_color=self.SUB)
            return
        self._player_status_lbl.configure(text="Refreshing…", text_color=self.SUB)
        self.core.get_players(
            lambda players: self.root.after(0, self._populate_players, players)
        )

    def _populate_players(self, players: list[dict]) -> None:
        # Clear old rows
        for row in self._player_rows:
            row.destroy()
        self._player_rows.clear()

        if not players:
            self._player_status_lbl.configure(
                text="No players connected", text_color=self.SUB)
            return

        self._player_status_lbl.configure(
            text=f"{len(players)} player(s) connected", text_color=self.GREEN)

        for p in players:
            row = ctk.CTkFrame(self._player_scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)

            ctk.CTkLabel(
                row, text=p["name"][:24],
                text_color=self.TEXT, font=ctk.CTkFont(size=12), anchor="w",
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(
                row, text=f"{p['ping']}ms",
                text_color=self.SUB, font=ctk.CTkFont(size=11), width=46,
            ).pack(side="left")
            ctk.CTkButton(
                row, text="Kick", width=46, height=26,
                fg_color=self.ORANGE, hover_color="#d97706",
                text_color="#0d0d14", font=ctk.CTkFont(size=11, weight="bold"),
                command=functools.partial(
                    self.core.kick_player, p["userid"], p["name"]),
            ).pack(side="left", padx=(4, 2))
            ctk.CTkButton(
                row, text="Ban", width=40, height=26,
                fg_color=self.STOP, hover_color=self.STOP_H,
                text_color=self.TEXT, font=ctk.CTkFont(size=11, weight="bold"),
                command=functools.partial(
                    self.core.ban_player, p["steamid"], p["name"]),
            ).pack(side="left", padx=(2, 0))

            self._player_rows.append(row)

    def _toggle_auto_refresh(self) -> None:
        if self._auto_refresh_var.get():
            self._schedule_auto_refresh()
        elif self._auto_refresh_after:
            self.root.after_cancel(self._auto_refresh_after)
            self._auto_refresh_after = None

    def _schedule_auto_refresh(self) -> None:
        if not self._auto_refresh_var.get():
            return
        self._refresh_players()
        self._auto_refresh_after = self.root.after(10000, self._schedule_auto_refresh)

    # ── ban list management ───────────────────────────────────────────────────

    def _manual_ban(self) -> None:
        steamid = self._ban_id_var.get().strip()
        if not steamid:
            self.core.log("[!] Enter a SteamID to ban")
            return
        self._ban_id_var.set("")
        if not self.core.running:
            self.core.log("[!] Server not running")
            return
        self.core.ban_player(steamid, duration=0)

    def _refresh_ban_list(self) -> None:
        self.core.get_ban_list(
            lambda entries: self.root.after(0, self._populate_ban_list, entries)
        )

    def _populate_ban_list(self, entries: list[str]) -> None:
        for row in self._ban_rows:
            row.destroy()
        self._ban_rows.clear()

        if not entries:
            row = ctk.CTkFrame(self._ban_scroll, fg_color="transparent")
            row.pack(fill="x")
            ctk.CTkLabel(row, text="No bans on record",
                         text_color=self.SUB, font=ctk.CTkFont(size=12)).pack()
            self._ban_rows.append(row)
            return

        for entry in entries:
            row = ctk.CTkFrame(self._ban_scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=entry[:48],
                         text_color=self.TEXT, font=ctk.CTkFont(size=11),
                         anchor="w").pack(side="left", fill="x", expand=True)
            # Extract SteamID in any format CS2 may use:
            #   STEAM_X:X:XXXXXXXX  |  [U:1:XXXXXXXX]  |  76561XXXXXXXXXXXX
            sid_match = re.search(
                r'(STEAM_\S+|\[U:[^\]]+\]|765\d{14,})', entry, re.IGNORECASE
            )
            sid = sid_match.group(0) if sid_match else entry.strip()
            ctk.CTkButton(
                row, text="Unban", width=56, height=24,
                fg_color=self.GREEN, hover_color="#16a34a",
                text_color="#0d0d14", font=ctk.CTkFont(size=11, weight="bold"),
                command=functools.partial(self.core.unban_player, sid),
            ).pack(side="right")
            self._ban_rows.append(row)

    # ── config tab handlers ───────────────────────────────────────────────────

    def _browse_server_dir(self) -> None:
        path = tkinter.filedialog.askdirectory(
            title="Select the folder containing steamcmd.exe",
            initialdir=self.core.server_dir or "C:\\",
        )
        if path:
            # Normalise to Windows-style backslashes
            path = path.replace("/", "\\")
            self._server_dir_var.set(path)

    def _save_server_settings(self) -> None:
        new_dir = self._server_dir_var.get().strip()
        if new_dir and new_dir != self.core.server_dir:
            self.core.update_server_dir(new_dir)
            self._refresh_wk()   # rescan workshop maps in the new location
        self.core.hostname             = self._hostname_var.get().strip()
        self.core.sv_password          = self._svpw_var.get()
        self.core.gslt_token           = self._gslt_var.get().strip()
        self.core.tickrate_128         = self._tick128_var.get()
        self.core.auto_start           = self._autostart_var.get()
        self.core.max_players_override = self._maxp_var.get().strip()
        self.core.save_config()
        self.core.log("Settings saved — path changes apply immediately, "
                      "other changes on next server start")

    def _set_sv_password_live(self) -> None:
        pw = self._svpw_var.get()
        if not self.core.running:
            self.core.log("[!] Server not running — password will apply on next start")
            return
        def _do() -> None:
            try:
                self.core.rcon.execute(f"sv_password {pw}")
                self.core.sv_password = pw
                self.core.log(f"sv_password updated live {'(public)' if not pw else '(password set)'}")
            except Exception as exc:
                self.core.log(f"sv_password live update failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── preset management ─────────────────────────────────────────────────────

    def _current_config_snapshot(self) -> dict:
        return {
            "hostname":             self.core.hostname,
            "sv_password":          self.core.sv_password,
            "tickrate_128":         self.core.tickrate_128,
            "auto_start":           self.core.auto_start,
            "bot_difficulty":       self.core.bot_difficulty,
            "max_players_override": self.core.max_players_override,
        }

    def _save_preset(self) -> None:
        name = self._preset_name_var.get().strip()
        if not name:
            self.core.log("[!] Enter a preset name")
            return
        self.core.presets[name] = self._current_config_snapshot()
        self.core.save_config()
        self._refresh_preset_list()
        self._preset_name_var.set("")
        self.core.log(f"Preset saved: {name}")

    def _load_preset(self) -> None:
        name = self._preset_sel_var.get()
        cfg  = self.core.presets.get(name)
        if not cfg:
            self.core.log(f"[!] Preset not found: {name}")
            return
        self.core.hostname             = cfg.get("hostname", self.core.hostname)
        self.core.sv_password          = cfg.get("sv_password", "")
        self.core.tickrate_128         = cfg.get("tickrate_128", False)
        self.core.auto_start           = cfg.get("auto_start", False)
        self.core.bot_difficulty       = cfg.get("bot_difficulty", "Normal")
        self.core.max_players_override = cfg.get("max_players_override", "")
        # Sync UI fields
        self._hostname_var.set(self.core.hostname)
        self._svpw_var.set(self.core.sv_password)
        self._tick128_var.set(self.core.tickrate_128)
        self._autostart_var.set(self.core.auto_start)
        self._bot_diff_var.set(self.core.bot_difficulty)
        self._maxp_var.set(self.core.max_players_override)
        self.core.log(f"Preset loaded: {name}")

    def _delete_preset(self) -> None:
        name = self._preset_sel_var.get()
        if name in self.core.presets:
            del self.core.presets[name]
            self.core.save_config()
            self._refresh_preset_list()
            self.core.log(f"Preset deleted: {name}")

    def _refresh_preset_list(self) -> None:
        names = list(self.core.presets.keys()) or [""]
        self._preset_cb.configure(values=names)
        self._preset_sel_var.set(names[0])

    def run(self) -> None:
        self.root.mainloop()
