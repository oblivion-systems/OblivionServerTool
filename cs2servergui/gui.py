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
from PIL import Image, ImageDraw

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
    "de_overpass":  ( 55,  85,145),  "cs_office":    ( 70,  90, 70),
    "cs_italy":     (140, 100, 50),  "ar_shoots":    (100,  60, 30),
    "ar_baggage":   ( 80,  80, 80),  "ar_dizzy":     ( 90,  60, 40),
    "de_lake":      ( 60, 120, 80),  "de_safehouse": ( 90,  70, 50),
    "de_shortdust": (120,  90, 40),  "de_stmarc":    ( 80,  80,100),
    "de_bank":      ( 70,  70, 80),  "de_sugarcane": ( 80, 110, 60),
}


class CS2GUI:
    # ── Colour palette ────────────────────────────────────────────────────────
    BG       = "#09090e"
    CARD     = "#0f0f16"
    DEEP     = "#060609"
    BORDER   = "#1c1c28"
    ACCENT   = "#a78bfa"
    ACCENT_H = "#8b5cf6"
    BLUE     = "#4e9aff"
    BLUE_H   = "#3b82f6"
    STOP     = "#e05c6b"
    STOP_H   = "#be2a3e"
    GREEN    = "#22c55e"
    ORANGE   = "#f59e0b"
    RED      = "#ef4444"
    TEXT     = "#e8e8f4"
    SUB      = "#9090aa"

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

        self.root = ctk.CTk()
        self.root.title("Oblivion Server Tool")
        self.root.geometry("1060x800")
        self.root.configure(fg_color=self.BG)
        self.root.resizable(True, True)
        self.root.minsize(860, 700)

        self._build()
        self._start_monitor()
        self._tick_uptime()

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

        # First-run: prompt for server directory if not yet configured
        if not self.core.server_dir:
            self.root.after(200, self._show_setup_dialog)

    # ── top-level layout ──────────────────────────────────────────────────────

    def _build(self) -> None:
        # ── thin accent stripe ──
        ctk.CTkFrame(self.root, fg_color=self.ACCENT,
                     corner_radius=0, height=2).pack(fill="x")

        # ── header bar ──
        hdr = ctk.CTkFrame(self.root, fg_color=self.CARD, corner_radius=0, height=54)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        brand = ctk.CTkFrame(hdr, fg_color="transparent")
        brand.pack(side="left", padx=20, fill="y")
        ctk.CTkLabel(brand, text="OBLIVION",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=self.ACCENT).pack(side="left")
        ctk.CTkLabel(brand, text="  SERVER TOOL",
                     font=ctk.CTkFont(size=12),
                     text_color=self.SUB).pack(side="left", pady=(6, 0))
        self._dot = ctk.CTkLabel(hdr, text="⬤  OFFLINE",
                                  font=ctk.CTkFont(size=12), text_color=self.RED)
        self._dot.pack(side="right", padx=20)

        # App self-update notification — hidden until a newer release is found
        self._app_upd_lbl = ctk.CTkLabel(
            hdr, text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.ORANGE, cursor="hand2",
        )
        self._app_upd_lbl.pack(side="right", padx=(0, 6))
        self._app_upd_lbl.bind("<Button-1>", lambda _e: self._open_app_release())

        # ── status bar ──
        sb = ctk.CTkFrame(self.root, fg_color=self.DEEP, corner_radius=0, height=34)
        sb.pack(fill="x")
        sb.pack_propagate(False)
        sf = ctk.CTkFont(size=12)
        ctk.CTkLabel(sb, text="Map:",    text_color=self.SUB, font=sf).pack(side="left", padx=(16, 3))
        self._sb_map = ctk.CTkLabel(sb, text="—", text_color=self.TEXT, font=sf)
        self._sb_map.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(sb, text="Mode:",   text_color=self.SUB, font=sf).pack(side="left", padx=(0, 3))
        self._sb_mode = ctk.CTkLabel(sb, text="—", text_color=self.TEXT, font=sf)
        self._sb_mode.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(sb, text="Uptime:", text_color=self.SUB, font=sf).pack(side="left", padx=(0, 3))
        self._sb_uptime = ctk.CTkLabel(sb, text="—", text_color=self.TEXT, font=sf)
        self._sb_uptime.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(sb, text="Build:", text_color=self.SUB, font=sf).pack(side="left", padx=(0, 3))
        self._sb_build = ctk.CTkLabel(sb, text="—", text_color=self.TEXT, font=sf)
        self._sb_build.pack(side="left")
        ctk.CTkLabel(sb,
                     text=f"Remote admin → http://localhost:{FLASK_PORT}",
                     text_color=self.SUB, font=sf).pack(side="right", padx=(4, 16))

        # Clickable connect string — copies to clipboard on click
        conn_lbl = ctk.CTkLabel(sb,
                                text=f"connect {RCON_HOST}:{RCON_PORT}",
                                text_color=self.SUB, font=sf, cursor="hand2")
        conn_lbl.pack(side="right", padx=(16, 4))
        conn_lbl.bind("<Button-1>", lambda _e: self._copy_connect_string())

        # Public IP label (fetched async; clickable to copy)
        self._pub_ip_lbl = ctk.CTkLabel(sb, text="ext: fetching…",
                                         text_color=self.SUB, font=sf, cursor="hand2")
        self._pub_ip_lbl.pack(side="right", padx=(16, 4))
        self._pub_ip_lbl.bind("<Button-1>", lambda _e: self._copy_public_ip())

        # ── log panel — packed FIRST to side="bottom" so it always gets its
        #    full height.  The content area (expand=True) then fills what's left.
        lp = ctk.CTkFrame(self.root, fg_color=self.CARD, corner_radius=12)
        lp.pack(side="bottom", fill="x", padx=14, pady=(0, 12))

        # Log header row: section label on left, Export + Clear buttons on right
        log_hdr = ctk.CTkFrame(lp, fg_color="transparent")
        log_hdr.pack(fill="x", padx=14, pady=(14, 4))
        ctk.CTkLabel(log_hdr, text="LOG",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Clear", width=52, height=22,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=11),
            corner_radius=6, command=self._clear_log,
        ).pack(side="right")
        ctk.CTkButton(
            log_hdr, text="Export", width=60, height=22,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=11),
            corner_radius=6, command=self._export_log,
        ).pack(side="right", padx=(0, 6))

        self._logbox = ctk.CTkTextbox(
            lp, fg_color=self.DEEP, text_color="#a8c4bf",
            font=ctk.CTkFont(family="Consolas", size=12),
            height=140, state="disabled",
        )
        self._logbox.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        # ── main two-column area ──
        content = ctk.CTkFrame(self.root, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=14, pady=6)
        content.columnconfigure(0, weight=2, minsize=300)
        content.columnconfigure(1, weight=3, minsize=420)
        content.rowconfigure(0, weight=1)

        left = ctk.CTkFrame(content, fg_color=self.CARD, corner_radius=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self._build_config_panel(left)

        right = ctk.CTkFrame(content, fg_color=self.CARD, corner_radius=12)
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self._build_controls_panel(right)

    # ── left panel: maps & config ─────────────────────────────────────────────

    def _build_config_panel(self, parent: ctk.CTkFrame) -> None:
        _cb = dict(
            fg_color=self.DEEP, button_color=self.BORDER,
            border_color=self.BORDER, dropdown_fg_color=self.CARD,
            dropdown_hover_color=self.BORDER, text_color=self.TEXT,
            dropdown_text_color=self.TEXT, button_hover_color="#2a2a40",
            font=ctk.CTkFont(size=13),
        )

        self._sec(parent, "MAPS & MODE")

        # Which map source is currently active: "official" or "workshop"
        self._map_source: str = "official"

        self._off_lbl_w = ctk.CTkLabel(parent, text="Official Map",
                                        font=ctk.CTkFont(size=13),
                                        text_color=self.TEXT, anchor="w")
        self._off_lbl_w.pack(fill="x", padx=14, pady=(4, 2))
        self._off_var = ctk.StringVar(value=OFFICIAL_MAPS[0])
        # ── official-map thumbnail grid (replaces dropdown) ──
        self._off_scroll = ctk.CTkScrollableFrame(
            parent, height=185,
            fg_color=self.DEEP, corner_radius=8,
            scrollbar_button_color=self.BORDER,
            scrollbar_button_hover_color="#2a2a40",
        )
        self._off_scroll.pack(fill="x", padx=14, pady=(0, 3))
        self._off_scroll.columnconfigure(0, weight=1, uniform="mc")
        self._off_scroll.columnconfigure(1, weight=1, uniform="mc")
        self._off_scroll.columnconfigure(2, weight=1, uniform="mc")

        self._wk_lbl_w = ctk.CTkLabel(parent, text="Workshop Map",
                                       font=ctk.CTkFont(size=13),
                                       text_color=self.SUB, anchor="w")
        self._wk_lbl_w.pack(fill="x", padx=14, pady=(4, 2))
        wkrow = ctk.CTkFrame(parent, fg_color="transparent")
        wkrow.pack(fill="x", padx=14, pady=(0, 3))
        self._wk_var = ctk.StringVar(value="")
        self._wk_cb = ctk.CTkComboBox(
            wkrow, values=[""], variable=self._wk_var,
            command=self._on_workshop_select, **_cb)
        self._wk_cb.pack(side="left", fill="x", expand=True)
        self._patch_dropdown_toggle(self._wk_cb)
        ctk.CTkButton(
            wkrow, text="↺", width=36, height=34,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.TEXT, font=ctk.CTkFont(size=15),
            command=self._refresh_wk,
        ).pack(side="right", padx=(6, 0))

        # Border colours only — preview label doesn't exist yet
        self._update_map_selection_ui()

        self._lbl(parent, "Game Mode")
        self._mode_var = ctk.StringVar(value="Competitive")
        self._mode_cb = ctk.CTkComboBox(
            parent, values=GAME_MODES, variable=self._mode_var,
            command=self._on_mode_change, **_cb,
        )
        self._mode_cb.pack(fill="x", padx=14, pady=(0, 3))

        # hint: shown when mode has non-standard or no official maps
        self._mode_hint_lbl = ctk.CTkLabel(
            parent, text="", text_color=self.SUB,
            font=ctk.CTkFont(size=12), anchor="w",
        )
        self._mode_hint_lbl.pack(fill="x", padx=14, pady=(0, 2))

        # Launch-preview chip — always shows exactly what START / CHANGE MAP will use
        _prev_wrap = ctk.CTkFrame(parent, fg_color=self.DEEP, corner_radius=8,
                                   border_width=1, border_color=self.ACCENT)
        _prev_wrap.pack(fill="x", padx=14, pady=(2, 6))
        self._map_preview_lbl = ctk.CTkLabel(
            _prev_wrap, text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="transparent", text_color=self.ACCENT, anchor="w",
        )
        self._map_preview_lbl.pack(fill="x", padx=10, pady=5)
        self._rebuild_official_grid()         # populate cards now that _mode_var exists
        self._update_map_selection_ui()       # now _mode_var exists — full preview

        # browse Steam Workshop — label updates with the selected mode
        self._browse_btn = ctk.CTkButton(
            parent, text="🔍  Browse Workshop Maps",
            height=28, corner_radius=6,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=self._browse_workshop,
        )
        self._browse_btn.pack(fill="x", padx=14, pady=(0, 8))

        # ── Workshop download (compact, no section header) ──
        ctk.CTkFrame(parent, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=14, pady=(4, 6))
        ws_row = ctk.CTkFrame(parent, fg_color="transparent")
        ws_row.pack(fill="x", padx=14, pady=(0, 3))
        self._wsid_var = ctk.StringVar()
        ctk.CTkEntry(
            ws_row, textvariable=self._wsid_var,
            placeholder_text="Workshop ID to download…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=13),
        ).pack(side="left", fill="x", expand=True)
        # Cancel button (invisible until a download is active)
        self._cancel_dl_btn = ctk.CTkButton(
            ws_row, text="✕", width=34, height=34,
            fg_color=self.BORDER, hover_color=self.BORDER,
            text_color=self.BORDER,          # invisible until active
            font=ctk.CTkFont(size=14, weight="bold"),
            state="disabled",
            command=self.core.cancel_download,
        )
        self._cancel_dl_btn.pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            ws_row, text="DL", width=52, height=34,
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._local_dl,
        ).pack(side="right", padx=(6, 0))
        self._wsid_lbl = ctk.CTkLabel(
            parent, text="", text_color=self.SUB,
            font=ctk.CTkFont(size=12))
        self._wsid_lbl.pack(anchor="w", padx=14, pady=(0, 4))

        ctk.CTkButton(
            parent, text="↻  Check Map Updates",
            height=28, corner_radius=6,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=self._check_map_updates,
        ).pack(fill="x", padx=14, pady=(0, 10))

    # ── right panel: tabbed controls ─────────────────────────────────────────

    def _build_controls_panel(self, parent: ctk.CTkFrame) -> None:
        tabs = ctk.CTkTabview(
            parent,
            fg_color=self.CARD,
            segmented_button_fg_color=self.DEEP,
            segmented_button_selected_color=self.ACCENT,
            segmented_button_selected_hover_color=self.ACCENT_H,
            segmented_button_unselected_color=self.DEEP,
            segmented_button_unselected_hover_color=self.BORDER,
            text_color=self.TEXT,
            text_color_disabled=self.SUB,
        )
        tabs.pack(fill="both", expand=True, padx=0, pady=0)
        tabs.add("Controls")
        tabs.add("Players")
        tabs.add("Config")
        tabs.add("Console")

        self._build_tab_controls(tabs.tab("Controls"))
        self._build_tab_players(tabs.tab("Players"))
        self._build_tab_config(tabs.tab("Config"))
        self._build_tab_console(tabs.tab("Console"))

    # ── TAB: Controls ─────────────────────────────────────────────────────────

    def _build_tab_controls(self, parent: ctk.CTkFrame) -> None:
        # ── Primary server buttons ──
        self._start_btn = ctk.CTkButton(
            parent, text="▶  START SERVER",
            height=44, corner_radius=10,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14", command=self._start)
        self._start_btn.pack(fill="x", padx=12, pady=(10, 4))

        # Stop + Change Map side-by-side
        _pm = ctk.CTkFrame(parent, fg_color="transparent")
        _pm.pack(fill="x", padx=12, pady=(0, 4))
        _pm.columnconfigure(0, weight=1)
        _pm.columnconfigure(1, weight=1)
        self._stop_btn = ctk.CTkButton(
            _pm, text="■  STOP",
            height=38, corner_radius=10,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=self.STOP, hover_color=self.STOP_H,
            state="disabled", command=self._stop)
        self._stop_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self._chg_btn = ctk.CTkButton(
            _pm, text="⟳  CHANGE MAP",
            height=38, corner_radius=10,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=self.BLUE, hover_color=self.BLUE_H,
            state="disabled", command=self._change)
        self._chg_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        # ── Utility row ──
        util = ctk.CTkFrame(parent, fg_color="transparent")
        util.pack(fill="x", padx=12, pady=(0, 8))
        _ub = {"height": 28, "corner_radius": 8,
               "font": ctk.CTkFont(size=11, weight="bold")}
        self._upd_btn = ctk.CTkButton(
            util, text="⟳  Update",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, command=self._check_update_btn, **_ub)
        self._upd_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            util, text="⚙  Plugins",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, command=self._check_plugins, **_ub,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._steam_btn = ctk.CTkButton(
            util, text="🔑  Steam",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, command=self._show_steam_account_dialog, **_ub)
        self._steam_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            util, text="🌐  Web Panel",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, command=self._open_web_panel, **_ub,
        ).pack(side="left", fill="x", expand=True)

        # ── Divider + section label ──
        ctk.CTkFrame(parent, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkLabel(parent, text="QUICK ACTIONS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 4))

        # ── Broadcast: entry + send icon button on one row ──
        self._chat_var = ctk.StringVar()
        _chat_row = ctk.CTkFrame(parent, fg_color="transparent")
        _chat_row.pack(fill="x", padx=12, pady=(0, 8))
        chat_ent = ctk.CTkEntry(
            _chat_row, textvariable=self._chat_var,
            placeholder_text="Broadcast to all players…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=13), height=36,
        )
        chat_ent.pack(side="left", fill="x", expand=True, padx=(0, 6))
        chat_ent.bind("<Return>", lambda _e: self._send_chat())
        ctk.CTkButton(
            _chat_row, text="📢", width=42, height=36,
            corner_radius=10, fg_color=self.BLUE, hover_color=self.BLUE_H,
            text_color=self.TEXT, font=ctk.CTkFont(size=18),
            command=self._send_chat,
        ).pack(side="right")

        # ── Quick action tiles — 3-column grid, expands to fill remaining height ──
        _qa = ctk.CTkFrame(parent, fg_color="transparent")
        _qa.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        _qa.columnconfigure(0, weight=1, uniform="qc")
        _qa.columnconfigure(1, weight=1, uniform="qc")
        _qa.columnconfigure(2, weight=1, uniform="qc")
        _qa.rowconfigure(0, weight=1, uniform="qr")
        _qa.rowconfigure(1, weight=1, uniform="qr")

        _tb = {
            "corner_radius": 10, "border_width": 1,
            "border_color": self.BORDER,
            "fg_color": self.DEEP, "hover_color": "#15151f",
            "font": ctk.CTkFont(size=12, weight="bold"),
        }

        # Row 0
        self._ff_btn = ctk.CTkButton(
            _qa, text="🔥  Friendly Fire\nOFF",
            text_color=self.SUB, command=self._toggle_ff, **_tb)
        self._ff_btn.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 4))

        ctk.CTkButton(
            _qa, text="↺  Restart\nRound",
            text_color=self.TEXT,
            command=lambda: self.core.restart_round(), **_tb,
        ).grid(row=0, column=1, sticky="nsew", padx=(0, 4), pady=(0, 4))

        ctk.CTkButton(
            _qa, text="⏩  End\nWarmup",
            text_color=self.TEXT,
            command=lambda: self.core.end_warmup(), **_tb,
        ).grid(row=0, column=2, sticky="nsew", pady=(0, 4))

        # Row 1
        ctk.CTkButton(
            _qa, text="⏸  Pause\nMatch",
            text_color=self.TEXT,
            command=lambda: self.core.pause_match(), **_tb,
        ).grid(row=1, column=0, sticky="nsew", padx=(0, 4))

        ctk.CTkButton(
            _qa, text="▶  Unpause\nMatch",
            text_color=self.TEXT,
            command=lambda: self.core.unpause_match(), **_tb,
        ).grid(row=1, column=1, sticky="nsew", padx=(0, 4))

        # Placeholder tile (reserved for future action)
        ctk.CTkFrame(
            _qa, fg_color=self.DEEP, corner_radius=10,
            border_width=1, border_color=self.BORDER,
        ).grid(row=1, column=2, sticky="nsew")

    # ── TAB: Players ──────────────────────────────────────────────────────────

    def _build_tab_players(self, parent: ctk.CTkFrame) -> None:
        # Header row: refresh + auto-refresh
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(10, 4))
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
            parent, text="", text_color=self.SUB, font=ctk.CTkFont(size=12))
        self._player_status_lbl.pack(anchor="w", padx=12, pady=(0, 4))

        # Scrollable player list
        self._player_scroll = ctk.CTkScrollableFrame(
            parent, fg_color=self.DEEP, corner_radius=8, height=140)
        self._player_scroll.pack(fill="x", padx=12, pady=(0, 8))
        self._player_rows: list[ctk.CTkFrame] = []

        ctk.CTkFrame(parent, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkLabel(parent, text="BAN MANAGEMENT",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 4))

        # Manual ban row
        ban_row = ctk.CTkFrame(parent, fg_color="transparent")
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
            parent, text="↺ Refresh Ban List", height=28,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            corner_radius=6, command=self._refresh_ban_list,
        ).pack(fill="x", padx=12, pady=(0, 4))

        self._ban_scroll = ctk.CTkScrollableFrame(
            parent, fg_color=self.DEEP, corner_radius=8, height=100)
        self._ban_scroll.pack(fill="x", padx=12, pady=(0, 8))
        self._ban_rows: list[ctk.CTkFrame] = []
        self._auto_refresh_after: str | None = None

    # ── TAB: Config ───────────────────────────────────────────────────────────

    def _build_tab_config(self, parent: ctk.CTkFrame) -> None:
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=0, pady=0)
        p = scroll   # alias

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
        self._maxp_var = ctk.StringVar(value=self.core.max_players_override)
        ctk.CTkEntry(p, textvariable=self._maxp_var,
                     placeholder_text="e.g. 16",
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, placeholder_text_color=self.SUB,
                     font=ctk.CTkFont(size=12),
                     ).pack(fill="x", padx=12, pady=(2, 6))

        # Checkboxes row
        chk_row = ctk.CTkFrame(p, fg_color="transparent")
        chk_row.pack(fill="x", padx=12, pady=(0, 6))
        self._tick128_var = ctk.BooleanVar(value=self.core.tickrate_128)
        ctk.CTkCheckBox(
            chk_row,
            text="Tickrate 128  (legacy, subtick handles timing)",
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
                      height=30, corner_radius=6,
                      command=self.core.kick_bots,
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

    # ── TAB: Console ──────────────────────────────────────────────────────────

    def _build_tab_console(self, parent: ctk.CTkFrame) -> None:
        self._rcon_box = ctk.CTkTextbox(
            parent, fg_color=self.DEEP, text_color="#a8c4bf",
            font=ctk.CTkFont(family="Consolas", size=12),
            state="disabled", wrap="word",
        )
        self._rcon_box.pack(fill="both", expand=True, padx=12, pady=(10, 6))

        cmd_row = ctk.CTkFrame(parent, fg_color="transparent")
        cmd_row.pack(fill="x", padx=12, pady=(0, 6))
        self._rcon_var = ctk.StringVar()
        rcon_ent = ctk.CTkEntry(
            cmd_row, textvariable=self._rcon_var,
            placeholder_text="Enter RCON command…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=13),
        )
        rcon_ent.pack(side="left", fill="x", expand=True)
        rcon_ent.bind("<Return>", lambda _e: self._send_rcon())
        ctk.CTkButton(
            cmd_row, text="SEND", width=72, height=34,
            fg_color=self.BLUE, hover_color=self.BLUE_H,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._send_rcon,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            parent, text=f"⚑  TEST RCON  ({RCON_HOST}:{RCON_PORT})",
            height=30, corner_radius=6,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=self._test_rcon,
        ).pack(fill="x", padx=12, pady=(0, 10))

    # ── widget helpers ────────────────────────────────────────────────────────

    def _sec(self, parent: ctk.CTkFrame, title: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(8, 4))
        ctk.CTkFrame(row, fg_color=self.ACCENT, width=3,
                     corner_radius=2).pack(side="left", fill="y", padx=(0, 8))
        ctk.CTkLabel(row, text=title,
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(side="left")

    def _sec_sub(self, parent: ctk.CTkFrame, title: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(2, 4))
        ctk.CTkFrame(row, fg_color=self.ACCENT, width=3,
                     corner_radius=2).pack(side="left", fill="y", padx=(0, 8))
        ctk.CTkLabel(row, text=title,
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(side="left")

    def _lbl(self, parent: ctk.CTkFrame, text: str) -> None:
        ctk.CTkLabel(parent, text=text,
                     font=ctk.CTkFont(size=13),
                     text_color=self.TEXT).pack(anchor="w", padx=14, pady=(4, 2))

    # ── log / RCON output ─────────────────────────────────────────────────────

    def _append_log(self, entry: str) -> None:
        self._logbox.configure(state="normal")
        self._logbox.insert("end", entry + "\n")
        self._logbox.see("end")
        self._logbox.configure(state="disabled")

    def _clear_log(self) -> None:
        self._logbox.configure(state="normal")
        self._logbox.delete("1.0", "end")
        self._logbox.configure(state="disabled")

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

    def _make_placeholder_image(self, map_id: str) -> ctk.CTkImage:
        """Generate a coloured gradient thumbnail for an official map."""
        base = _MAP_COLORS.get(map_id, (55, 60, 80))
        r, g, b = base
        w, h = 162, 96
        img = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(img)
        # Top-to-bottom gradient (slightly darker at the bottom)
        for y in range(h):
            f = 1.0 - 0.45 * (y / h)
            draw.line([(0, y), (w, y)], fill=(int(r * f), int(g * f), int(b * f)))
        # Subtle grid texture
        gc = (min(r + 22, 255), min(g + 22, 255), min(b + 22, 255))
        for x in range(0, w, 18):
            draw.line([(x, 0), (x, h)], fill=gc)
        for yy in range(0, h, 18):
            draw.line([(0, yy), (w, yy)], fill=gc)
        return ctk.CTkImage(img, size=(81, 48))

    def _get_map_image(self, map_id: str) -> ctk.CTkImage:
        """Return a cached thumbnail or generate a placeholder."""
        cache = os.path.join(_THUMB_DIR, f"{map_id}.jpg")
        if os.path.exists(cache):
            try:
                pil = Image.open(cache).resize((162, 96))
                return ctk.CTkImage(pil, size=(81, 48))
            except Exception:
                pass
        return self._make_placeholder_image(map_id)

    def _make_map_card(self, parent: ctk.CTkFrame, map_id: str,
                       row: int, col: int, selected: bool = False) -> ctk.CTkFrame:
        """Build a compact clickable map thumbnail card."""
        border_c = self.ACCENT if selected else self.BORDER
        card = ctk.CTkFrame(
            parent, corner_radius=8, border_width=2,
            border_color=border_c, fg_color=self.DEEP, cursor="hand2",
        )
        card.grid(row=row, column=col, sticky="ew",
                  padx=(0, 4) if col < 2 else (0, 0), pady=(0, 4))
        img = self._get_map_image(map_id)
        img_lbl = ctk.CTkLabel(card, text="", image=img, fg_color="transparent")
        img_lbl.pack(padx=3, pady=(3, 0))
        # Short human-readable name (strips prefix and underscores)
        parts = map_id.split("_", 1)
        short = parts[1].replace("_", " ").title() if len(parts) > 1 else map_id
        ctk.CTkLabel(
            card, text=short,
            font=ctk.CTkFont(size=9),
            text_color=self.TEXT, fg_color="transparent",
            anchor="center",
        ).pack(fill="x", padx=2, pady=(1, 3))
        click = lambda _e, m=map_id: self._select_official_card(m)
        card.bind("<Button-1>", click)
        img_lbl.bind("<Button-1>", click)
        return card

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
            ).grid(row=0, column=0, columnspan=3, padx=6, pady=18)
            self._off_var.set("")
            return
        current = self._off_var.get()
        if current not in maps:
            current = maps[0]
            self._off_var.set(current)
        for i, m in enumerate(maps):
            row, col = divmod(i, 3)
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

        # Rebuild the map card grid for the new mode, then update browse button
        self._rebuild_official_grid()
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

    def _on_official_select(self, _value: str) -> None:
        """User explicitly chose an official map — make it the active source."""
        self._map_source = "official"
        self._update_map_selection_ui()

    def _on_workshop_select(self, _value: str) -> None:
        """User explicitly chose a workshop map — make it the active source."""
        self._map_source = "workshop"
        self._update_map_selection_ui()

    def _update_map_selection_ui(self) -> None:
        """Sync border colours, label brightness, and the launch-preview chip."""
        mode_var = getattr(self, "_mode_var", None)
        mode     = mode_var.get() if mode_var else ""

        if self._map_source == "workshop":
            wk = self._wk_var.get().strip()
            # Strip "  [id]" suffix — map name alone is enough for the chip
            wk_name = re.sub(r"\s*\[\d+\]$", "", wk) if wk else ""
            if wk_name:
                preview = f"▶  {wk_name}  ·  {mode}" if mode else f"▶  {wk_name}"
            else:
                preview = "▶  (no workshop map selected)"
            self._off_lbl_w.configure(text_color=self.SUB)
            self._wk_lbl_w.configure(text_color=self.TEXT)
            self._set_official_active_style(False)
            self._wk_cb.configure(border_color=self.ACCENT)
        else:
            off     = self._off_var.get().strip() or "—"
            preview = f"▶  {off}  ·  {mode}" if mode else f"▶  {off}"
            self._off_lbl_w.configure(text_color=self.TEXT)
            self._wk_lbl_w.configure(text_color=self.SUB)
            self._set_official_active_style(True)
            self._wk_cb.configure(border_color=self.BORDER)

        # Guard: preview label doesn't exist on the first call during construction
        if hasattr(self, "_map_preview_lbl"):
            self._map_preview_lbl.configure(text=preview)

    def _refresh_wk(self) -> None:
        from . import config as _cfg
        self.core.log(f"Workshop scan: {_cfg.WORKSHOP_DIR}")
        ids = load_workshop()
        self.core.log(f"Workshop scan: {len(ids)} map(s) found")
        self._wk_all_ids = ids
        # Show plain IDs immediately while names load in background
        self._wk_cb.configure(values=ids or [""])

        def _on_names_done() -> None:
            labels = []
            for wid in ids:
                name = self.core._map_name_cache.get(wid, "")
                labels.append(f"{name}  [{wid}]" if name else wid)

            def _apply() -> None:
                self._wk_all_labels = labels
                # Upgrade any bare-ID display to "Name  [id]" now that names loaded
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

        self._wk_cb.configure(values=filtered_labels or [""])
        # If the currently selected map was filtered out, deselect it
        current = self._wk_var.get().strip()
        if current and current not in filtered_labels:
            self._wk_var.set("")
            if self._map_source == "workshop":
                self._map_source = "official"
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

    def _on_core_state_change(self) -> None:
        """Called on the main thread whenever AppCore.boot_state changes."""
        self._set_state(self.core.boot_state)

    def _boot_pulse(self) -> None:
        """Animate the header dot while the server is booting."""
        if self.core.boot_state != "booting":
            return
        frames = ["⬤  BOOTING ·  ", "⬤  BOOTING ·· ", "⬤  BOOTING ···"]
        self._dot.configure(text=frames[self._pulse_step % 3],
                            text_color=self.ORANGE)
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
        if state == "offline":
            self._dot.configure(text="⬤  OFFLINE",  text_color=self.RED)
            if self._ff_btn:
                self._ff_btn.configure(state="disabled")
        elif state == "booting":
            self._dot.configure(text="⬤  BOOTING…", text_color=self.ORANGE)
            if self._ff_btn:
                self._ff_btn.configure(state="disabled")
        else:
            self._dot.configure(text="⬤  ONLINE",   text_color=self.GREEN)
            if self._ff_btn:
                self._ff_btn.configure(state="normal")
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

    def _local_dl(self) -> None:
        wid = self._wsid_var.get().strip()
        if not wid or not wid.isdigit():
            self._wsid_lbl.configure(
                text="⚠  Enter a numeric Workshop ID", text_color=self.RED)
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
        self._pub_ip_lbl.configure(text=f"ext: {ip}")

    def _copy_public_ip(self) -> None:
        ip = self.core.public_ip
        if not ip:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(ip)
        self.core.log(f"Copied public IP: {ip}")

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
            self._ff_btn.configure(
                text="🔥  Friendly Fire\nON",
                fg_color=self.ORANGE, hover_color="#d97706",
                text_color="#0d0d14",
            )
        else:
            self._ff_btn.configure(
                text="🔥  Friendly Fire\nOFF",
                fg_color=self.DEEP, hover_color="#15151f",
                text_color=self.SUB,
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
        if not self.core.running:
            return
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
            ctk.CTkLabel(row, text=entry[:40],
                         text_color=self.TEXT, font=ctk.CTkFont(size=11),
                         anchor="w").pack(side="left", fill="x", expand=True)
            # Try to extract STEAM_ id from entry string
            sid_match = re.search(r'STEAM_\S+', entry)
            sid = sid_match.group(0) if sid_match else entry.split()[0]
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
