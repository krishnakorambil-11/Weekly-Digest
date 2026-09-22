"""
A borderless, landscape-oriented "flyout" window inspired by Apple Music UI aesthetic.
Slides up from just above the taskbar, showing content instantly with a light glassmorphic 
card theme, vibrant accents, smooth typography, and heavily rounded corners.

Built as a tk.Toplevel off a single persistent, hidden Tk root that main.py owns.
"""

import ctypes
import re
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont

# Enable Per-Monitor DPI awareness on Windows before creating any UI elements
if sys.platform.startswith("win"):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

WIDTH = 1140
HEIGHT = 630
CORNER_RADIUS = 72  # Soft, heavily rounded corners matching Apple UI
RIGHT_MARGIN = 33
BOTTOM_MARGIN = 24  # gap above the taskbar/work-area edge
SLIDE_MS = 320
SLIDE_STEPS = 24
STATUS_HOLD_MS = 2500  # how long "Updated" stays visible before fading

NEW_MARK = "\U0001F195"  # 🆕

# Color Palette - Apple Music Light Theme
BG = "#e8ecef"          # Main flyout frosted background container
CARD_BG = "#f5f7fa"     # Inner elevated card container
CARD_BORDER = "#d8dee6" # Ultra-subtle border for inner card
TEXT = "#1a1d24"        # High-contrast primary text
SUBTLE = "#7a8290"      # Secondary muted text
ACCENT = "#e03b5a"      # Vibrant Apple-style pink/red primary accent
BULLET_TEXT = "#2c3038"

# Interactive Pill Buttons
BTN_PRIMARY_BG = "#ff2d55"      # Vibrant Apple accent red/pink
BTN_PRIMARY_FG = "#ffffff"      # Crisp white text
BTN_PRIMARY_HOVER = "#e02648"   # Darker accent hover state

BTN_SECONDARY_BG = "#e2e7ec"    # Soft grey glass pill background
BTN_SECONDARY_FG = "#1a1d24"    # Charcoal text
BTN_SECONDARY_HOVER = "#d4dadf" # Slightly darker grey hover state

# Badges & Highlighting for NEW items
NEW_ACCENT = "#ff2d55"
NEW_BG = "#fde8ec"


def _apply_custom_rounded_region(window: tk.Toplevel):
    """Clips the top-level window using Win32 CreateRoundRectRgn to achieve
    a custom corner radius matching Apple OS window aesthetics."""
    if not sys.platform.startswith("win"):
        return

    window.update()
    hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
    if not hwnd:
        hwnd = window.winfo_id()

    # Apply GDI region clip for deep corner rounding
    rgn = ctypes.windll.gdi32.CreateRoundRectRgn(
        0, 0, WIDTH + 1, HEIGHT + 1, CORNER_RADIUS, CORNER_RADIUS
    )
    ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)


def _work_area():
    """Bottom-right usable screen corner, avoiding the taskbar where possible."""
    if sys.platform.startswith("win"):
        try:
            import ctypes.wintypes as wt

            rect = wt.RECT()
            SPI_GETWORKAREA = 0x0030
            ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
            )
            return rect.right, rect.bottom
        except Exception:
            pass
    return None


def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def _render_markdown(text_widget: tk.Text, markdown: str):
    text_widget.configure(state="normal")
    text_widget.delete("1.0", tk.END)

    base_font = tkfont.Font(family="Segoe UI Text", size=15)
    header_font = tkfont.Font(family="Segoe UI Variable Display", size=18, weight="bold")
    bold_font = tkfont.Font(family="Segoe UI Text", size=15, weight="bold")
    badge_font = tkfont.Font(family="Segoe UI Text", size=11, weight="bold")

    text_widget.tag_configure(
        "h2", font=header_font, foreground=ACCENT, spacing1=18, spacing3=6
    )
    text_widget.tag_configure(
        "bullet", font=base_font, foreground=BULLET_TEXT, lmargin1=18, lmargin2=39,
        spacing1=5, spacing3=5,
    )
    text_widget.tag_configure(
        "bullet_new", font=base_font, foreground=TEXT, background=NEW_BG,
        lmargin1=18, lmargin2=39, spacing1=5, spacing3=5,
    )
    text_widget.tag_configure(
        "new_dot", font=base_font, foreground=NEW_ACCENT, background=NEW_BG,
    )
    text_widget.tag_configure(
        "new_badge", font=badge_font, foreground=NEW_ACCENT, background=NEW_BG,
    )
    text_widget.tag_configure("body", font=base_font, foreground=TEXT, spacing1=3)
    text_widget.tag_configure("bold", font=bold_font, foreground=TEXT)
    text_widget.tag_configure("empty", font=base_font)

    bold_pattern = re.compile(r"\*\*(.+?)\*\*")

    def insert_with_bold(line: str, base_tag: str):
        pos = 0
        for m in bold_pattern.finditer(line):
            if m.start() > pos:
                text_widget.insert(tk.END, line[pos:m.start()], base_tag)
            text_widget.insert(tk.END, m.group(1), (base_tag, "bold"))
            pos = m.end()
        text_widget.insert(tk.END, line[pos:], base_tag)

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line:
            text_widget.insert(tk.END, "\n", "empty")
            continue
        if line.startswith("## "):
            insert_with_bold(line[3:], "h2")
            text_widget.insert(tk.END, "\n")
        elif line.startswith("# "):
            insert_with_bold(line[2:], "h2")
            text_widget.insert(tk.END, "\n")
        elif line.startswith(("- ", "* ")):
            content = line[2:].lstrip()
            is_new = NEW_MARK in content
            if is_new:
                content = content.replace(NEW_MARK, "").strip()
                text_widget.insert(tk.END, " NEW ", "new_badge")
                text_widget.insert(tk.END, " • ", "new_dot")
                insert_with_bold(content, "bullet_new")
            else:
                text_widget.insert(tk.END, "• ", "bullet")
                insert_with_bold(content, "bullet")
            text_widget.insert(tk.END, "\n")
        else:
            insert_with_bold(line, "body")
            text_widget.insert(tk.END, "\n")

    text_widget.configure(state="disabled")


def _make_pill(parent, text, command, bg, hover_bg, fg=TEXT):
    btn = tk.Label(
        parent, text=text, bg=bg, fg=fg, font=("Segoe UI Text", 13, "bold"),
        padx=28, pady=10, cursor="hand2",
    )
    btn.bind("<Button-1>", lambda e: command())
    btn.bind("<Enter>", lambda e: btn.configure(bg=hover_bg))
    btn.bind("<Leave>", lambda e: btn.configure(bg=bg))
    return btn


def show_popup(root: tk.Tk, title: str, subtitle: str, text: str, on_check=None) -> tk.Toplevel:
    """Builds and slides up an Apple Music inspired flyout window."""
    top = tk.Toplevel(root)
    top.overrideredirect(True)
    top.attributes("-topmost", True)

    screen_w = top.winfo_screenwidth()
    screen_h = top.winfo_screenheight()
    work = _work_area()
    work_right = work[0] if work else screen_w
    work_bottom = work[1] if work else screen_h - 72

    final_x = work_right - WIDTH - RIGHT_MARGIN
    final_y = work_bottom - HEIGHT - BOTTOM_MARGIN
    start_y = screen_h + 15

    top.configure(bg=BG)

    content = tk.Frame(top, bg=BG, width=WIDTH, height=HEIGHT)
    content.pack(fill=tk.BOTH, expand=True)

    # ----- header -----
    header = tk.Frame(content, bg=BG)
    header.pack(fill=tk.X, padx=48, pady=(32, 14))

    title_col = tk.Frame(header, bg=BG)
    title_col.pack(side=tk.LEFT, fill=tk.X, expand=True)
    tk.Label(
        title_col, text="This Week:", bg=BG, fg=TEXT,
        font=("Segoe UI Variable Display", 22, "bold"), anchor="w",
    ).pack(fill=tk.X)
    subtitle_var = tk.StringVar(value=subtitle)
    tk.Label(
        title_col, textvariable=subtitle_var, bg=BG, fg=SUBTLE,
        font=("Segoe UI Text", 13), anchor="w",
    ).pack(fill=tk.X, pady=(2, 0))

    def _close_animated():
        _animate(top, final_x, final_y, start_y, closing=True, on_done=top.destroy)

    close_btn = tk.Label(
        header, text="\u2715", bg=BG, fg=SUBTLE, font=("Segoe UI", 16, "bold"),
        cursor="hand2", width=2,
    )
    close_btn.pack(side=tk.RIGHT, anchor="n")
    close_btn.bind("<Button-1>", lambda e: _close_animated())
    close_btn.bind("<Enter>", lambda e: close_btn.configure(fg=TEXT))
    close_btn.bind("<Leave>", lambda e: close_btn.configure(fg=SUBTLE))

    status_var = tk.StringVar(value="")
    status_label = tk.Label(
        header, textvariable=status_var, bg=BG, fg=SUBTLE,
        font=("Segoe UI Text", 13, "italic"),
    )
    status_label.pack(side=tk.RIGHT, anchor="n", padx=(0, 21))

    # ----- body: elevated card container -----
    card_frame = tk.Frame(
        content, bg=CARD_BG,
        highlightbackground=CARD_BORDER, highlightthickness=1
    )
    card_frame.pack(fill=tk.BOTH, expand=True, padx=40, pady=(0, 16))

    scrollbar = tk.Scrollbar(card_frame, relief="flat", bd=0, bg=CARD_BG)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 6), pady=6)

    box = tk.Text(
        card_frame, wrap=tk.WORD, bg=CARD_BG, fg=TEXT,
        font=("Segoe UI Text", 15), borderwidth=0, highlightthickness=0,
        padx=24, pady=20, yscrollcommand=scrollbar.set,
    )
    box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.configure(command=box.yview)

    _render_markdown(box, text)

    # ----- footer -----
    footer = tk.Frame(content, bg=BG)
    footer.pack(fill=tk.X, padx=48, pady=(0, 32))

    def _run_check():
        if not on_check or not top.winfo_exists():
            return
        status_var.set("Checking for updates...")

        def worker():
            error_msg = None
            try:
                changed, new_text = on_check()
            except Exception as exc:
                changed, new_text = False, None
                error_msg = str(exc)
                import traceback

                traceback.print_exc()

            def apply():
                if not top.winfo_exists():
                    return
                if error_msg:
                    short = error_msg if len(error_msg) < 80 else error_msg[:77] + "..."
                    _render_markdown(
                        box, f"## Check failed\n\n{error_msg}\n\nSee the terminal for details."
                    )
                    status_var.set(f"Error: {short}")
                elif changed and new_text:
                    _render_markdown(box, new_text)
                    status_var.set("Updated with new items")
                    top.after(STATUS_HOLD_MS, lambda: status_var.set(""))
                else:
                    status_var.set("")

            top.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    if on_check:
        _make_pill(
            footer, "Check again", _run_check,
            BTN_SECONDARY_BG, BTN_SECONDARY_HOVER, BTN_SECONDARY_FG
        ).pack(side=tk.LEFT)

    _make_pill(
        footer, "Close", _close_animated,
        BTN_PRIMARY_BG, BTN_PRIMARY_HOVER, BTN_PRIMARY_FG
    ).pack(side=tk.RIGHT)

    # Apply rounded corner mask to match Apple card UI
    _apply_custom_rounded_region(top)

    # Start off-screen, animate up, then kick off the background check
    top.geometry(f"{WIDTH}x{HEIGHT}+{final_x}+{start_y}")
    top.deiconify()
    _animate(top, final_x, final_y, start_y, closing=False, on_done=_run_check)

    # Attach close handle and return instance for main.py tracking
    top._close_animated = _close_animated
    return top


def _animate(win, x, final_y, start_y, closing: bool, on_done=None, duration=SLIDE_MS, steps=SLIDE_STEPS):
    step_ms = max(duration // steps, 1)
    begin, end = (final_y, start_y) if closing else (start_y, final_y)
    delta = end - begin

    def step(i=0):
        if not win.winfo_exists():
            return
        if i > steps:
            win.geometry(f"{WIDTH}x{HEIGHT}+{x}+{end}")
            if on_done:
                on_done()
            return
        t = _ease_out_cubic(i / steps)
        y = int(begin + delta * t)
        win.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        win.after(step_ms, lambda: step(i + 1))

    step()