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
import time
import tkinter as tk
from tkinter import font as tkfont

# Imported up front so the first popup doesn't pay for it
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageGrab, ImageTk  # noqa: F401

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
SLIDE_MS = 160
SLIDE_STEPS = 24
TICK_MS = 8     # how often new backdrop frames are applied
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

# Liquid Glass palette (text sits directly on the blurred glass)
GLASS_TINT = "#14141a"   # dark frosted tint, ~rgba(20,20,25,0.45) over the blur
GLASS_TEXT = "#ffffff"
GLASS_SUBTLE = "#d5dae3"
GLASS_SHADOW = "#0b0d12"
TILE_RADIUS = 24
TILE_GAP = 16
TILE_PAD = 26
TILE_INSET = 12


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


def _round_widget(widget, radius=None):
    """Clips a child widget to rounded corners (radius=None gives a full pill).
    Re-applied on every resize since the region is in widget pixels."""
    if not sys.platform.startswith("win"):
        return

    def apply(event):
        w, h = event.width, event.height
        r = min(radius if radius is not None else h, w, h)
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, r, r)
        ctypes.windll.user32.SetWindowRgn(widget.winfo_id(), rgn, True)

    widget.bind("<Configure>", apply, add="+")


class _Backdrop:
    """Liquid-glass backdrop renderer: turns a screenshot of what sits behind
    the popup into blurred, saturated, tinted glass with a soft top sheen and a
    bright rim. Blur runs on a quarter-size copy so it is cheap enough to redo
    several times a second."""

    SCALE = 4

    def __init__(self, w, h):
        from PIL import Image, ImageChops, ImageDraw, ImageFilter

        self.w, self.h = w, h
        sheen = Image.new("L", (w, h), 0)
        sd = ImageDraw.Draw(sheen)
        sheen_h = h // 3
        for row in range(sheen_h):
            sd.line([(0, row), (w, row)], fill=int(70 * (1 - row / sheen_h) ** 2))
        rim = Image.new("L", (w, h), 0)
        ImageDraw.Draw(rim).rounded_rectangle(
            (0, 0, w - 1, h - 1), CORNER_RADIUS // 2, outline=255, width=3)
        rim = rim.filter(ImageFilter.GaussianBlur(1)).point(lambda v: int(v * 0.7))
        self.overlay = ImageChops.lighter(sheen, rim)

    def render(self, small):
        """small: capture already downscaled to (w // SCALE, h // SCALE)."""
        from PIL import Image, ImageEnhance, ImageFilter

        sw, sh = small.size
        small = small.filter(ImageFilter.GaussianBlur(28 / self.SCALE))
        small = ImageEnhance.Color(small).enhance(1.8)  # saturate(180%)
        small = Image.blend(small, Image.new("RGB", (sw, sh), GLASS_TINT), 0.45)
        glass = small.resize((self.w, self.h), Image.BILINEAR)
        glass.paste((255, 255, 255), mask=self.overlay)
        return glass


class _Grabber:
    """Screen region -> small PIL image. StretchBlt downsamples on the GDI
    side, so only w/scale x h/scale pixels ever get copied into Python; about
    half the cost of a full-size ImageGrab, and no resize afterwards."""

    def __init__(self, w, h, scale):
        from ctypes import wintypes as wt

        class BMI(ctypes.Structure):
            _fields_ = [
                ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
            ]

        self.w, self.h = w, h
        self.sw, self.sh = w // scale, h // scale
        self.u32, self.g32 = ctypes.windll.user32, ctypes.windll.gdi32
        # Handles are pointer-sized; ctypes' default int args would overflow
        self.u32.GetDC.restype = wt.HDC
        self.u32.GetDC.argtypes = [wt.HWND]
        self.g32.CreateCompatibleDC.restype = wt.HDC
        self.g32.CreateCompatibleDC.argtypes = [wt.HDC]
        self.g32.CreateCompatibleBitmap.restype = wt.HBITMAP
        self.g32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
        self.g32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
        self.g32.SetStretchBltMode.argtypes = [wt.HDC, ctypes.c_int]
        self.g32.StretchBlt.argtypes = [
            wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.DWORD]
        self.g32.GetDIBits.argtypes = [
            wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
            ctypes.c_void_p, wt.UINT]
        self.screen = self.u32.GetDC(0)
        self.mem = self.g32.CreateCompatibleDC(self.screen)
        self.bmp = self.g32.CreateCompatibleBitmap(self.screen, self.sw, self.sh)
        self.g32.SelectObject(self.mem, self.bmp)
        self.g32.SetStretchBltMode(self.mem, 4)  # HALFTONE: averages, not drops
        self.bmi = BMI(ctypes.sizeof(BMI), self.sw, -self.sh, 1, 32, 0)
        self.buf = ctypes.create_string_buffer(self.sw * self.sh * 4)

    def grab_raw(self, x, y):
        """Capture to bytes. Blocks ~one display refresh (GDI syncs to the
        compositor), so this is the pipeline's rate limiter."""
        self.g32.StretchBlt(
            self.mem, 0, 0, self.sw, self.sh,
            self.screen, x, y, self.w, self.h, 0x00CC0020)  # SRCCOPY
        self.g32.GetDIBits(
            self.mem, self.bmp, 0, self.sh, self.buf, ctypes.byref(self.bmi), 0)
        return bytes(self.buf)

    def to_image(self, raw):
        from PIL import Image

        return Image.frombuffer("RGB", (self.sw, self.sh), raw, "raw", "BGRX", 0, 1)

    def grab(self, x, y):
        return self.to_image(self.grab_raw(x, y))


def _exclude_from_capture(window):
    """Hides the window from screenshots (not from the screen), so the live
    backdrop never captures the popup itself. Needs Windows 10 2004+."""
    if not sys.platform.startswith("win"):
        return False
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        WDA_EXCLUDEFROMCAPTURE = 0x11
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
            hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False


def _glass_overlay(w, h, radius, base=0.10, sheen=(0.18, 0.02), rim=0.4):
    """Translucent white rounded tile: flat fill plus a 135-degree sheen
    gradient (top-left to bottom-right) and a 1px light rim. RGBA, drawn at 2x
    and downsampled so the corners are antialiased."""
    from PIL import Image, ImageChops, ImageDraw

    ss = 2
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, w * ss - 1, h * ss - 1), radius * ss, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)

    grad = Image.new("L", (32, 32))
    grad.putdata([
        min(255, int(255 * (base + sheen[0] + (sheen[1] - sheen[0]) * (x + y) / 62)))
        for y in range(32) for x in range(32)
    ])
    alpha = ImageChops.multiply(grad.resize((w, h), Image.BILINEAR), mask)
    tile = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    tile.putalpha(alpha)

    rim_mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(rim_mask).rounded_rectangle(
        (0, 0, w * ss - 1, h * ss - 1), radius * ss, outline=255, width=ss)
    rim_mask = rim_mask.resize((w, h), Image.LANCZOS).point(lambda v: int(v * rim))
    rim_layer = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    rim_layer.putalpha(rim_mask)
    return Image.alpha_composite(tile, rim_layer)


def _round_rect(canvas, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)


def _glass_pill(canvas, x, y, text, command, font, anchor="nw"):
    """Frosted pill button drawn on the canvas; lifts 2px on hover."""
    from PIL import ImageTk

    w = font.measure(text) + 60
    h = font.metrics("linespace") + 24
    left = x if anchor == "nw" else x - w
    normal = ImageTk.PhotoImage(_glass_overlay(w, h, h // 2, 0.14, (0.18, 0.06), 0.55))
    hover = ImageTk.PhotoImage(_glass_overlay(w, h, h // 2, 0.24, (0.28, 0.12), 0.8))
    if not hasattr(canvas, "_pill_imgs"):
        canvas._pill_imgs = []
    canvas._pill_imgs += [normal, hover]

    img = canvas.create_image(left, y, image=normal, anchor="nw")
    label = canvas.create_text(
        left + w / 2, y + h / 2, text=text, fill=GLASS_TEXT, font=font)
    lifted = [False]

    def enter(_e):
        if not lifted[0]:
            lifted[0] = True
            canvas.itemconfigure(img, image=hover)
            canvas.move(img, 0, -2)
            canvas.move(label, 0, -2)
        canvas.configure(cursor="hand2")

    def leave(_e):
        if lifted[0]:
            lifted[0] = False
            canvas.itemconfigure(img, image=normal)
            canvas.move(img, 0, 2)
            canvas.move(label, 0, 2)
        canvas.configure(cursor="")

    for item in (img, label):
        canvas.tag_bind(item, "<Button-1>", lambda e: command())
        canvas.tag_bind(item, "<Enter>", enter)
        canvas.tag_bind(item, "<Leave>", leave)
    return w, h


def show_popup(root: tk.Tk, title: str, subtitle: str, text: str, on_check=None) -> tk.Toplevel:
    """Builds and slides up an Apple Music inspired flyout window."""
    top = tk.Toplevel(root)
    top.withdraw()  # stay hidden until the backdrop is captured
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

    # Backdrop must be captured before the window is on screen
    from PIL import Image, ImageTk
    backdrop = grabber = None
    try:
        backdrop = _Backdrop(WIDTH, HEIGHT)
        grabber = _Grabber(WIDTH, HEIGHT, _Backdrop.SCALE)
        glass = backdrop.render(grabber.grab(final_x, final_y))
    except Exception:
        glass = Image.new("RGB", (WIDTH, HEIGHT), GLASS_TINT)
    glass_photo = ImageTk.PhotoImage(glass)

    canvas = tk.Canvas(top, width=WIDTH, height=HEIGHT, bg=BG, highlightthickness=0, bd=0)
    canvas.pack(fill=tk.BOTH, expand=True)
    canvas.create_image(0, 0, image=glass_photo, anchor="nw")
    canvas._glass_photo = glass_photo  # keep a reference alive

    def shadow_text(x, y, **kw):
        canvas.create_text(x + 1, y + 2, **{**kw, "fill": GLASS_SHADOW})
        return canvas.create_text(x, y, **kw)

    # ----- header -----
    title_font = tkfont.Font(family="Segoe UI Variable Display", size=22, weight="bold")
    sub_font = tkfont.Font(family="Segoe UI Text", size=13)
    shadow_text(48, 32, text="This Week:", fill=GLASS_TEXT, font=title_font, anchor="nw")
    sub_y = 32 + title_font.metrics("linespace") + 2
    canvas.create_text(
        48, sub_y, text=subtitle, fill=GLASS_SUBTLE, font=sub_font, anchor="nw")
    view_top = sub_y + sub_font.metrics("linespace") + 14

    def _close_animated():
        _animate(top, final_x, final_y, start_y, closing=True, on_done=top.destroy)

    # Drawn as two lines: the U+2715 glyph isn't in Segoe UI, and the font
    # fallback lookup alone cost ~130ms on every popup open.
    cx, cy, arm = WIDTH - 48 - 12, 32 + 15, 8
    hit = canvas.create_rectangle(
        cx - 18, cy - 18, cx + 18, cy + 18, fill="", outline="", tags="close")
    for dx, dy in ((1, 1), (1, -1)):
        canvas.create_line(
            cx - arm * dx, cy - arm * dy, cx + arm * dx, cy + arm * dy,
            fill=GLASS_SUBTLE, width=3, capstyle="round", tags=("close", "close_x"))
    canvas.tag_bind("close", "<Button-1>", lambda e: _close_animated())
    canvas.tag_bind("close", "<Enter>", lambda e: (
        canvas.itemconfigure("close_x", fill=GLASS_TEXT), canvas.configure(cursor="hand2")))
    canvas.tag_bind("close", "<Leave>", lambda e: (
        canvas.itemconfigure("close_x", fill=GLASS_SUBTLE), canvas.configure(cursor="")))

    status_id = canvas.create_text(
        WIDTH - 48 - 42, 36, text="", fill=GLASS_SUBTLE,
        font=("Segoe UI Text", 13, "italic"), anchor="ne",
    )

    class _Status:
        def set(self, value):
            canvas.itemconfigure(status_id, text=value)

    status_var = _Status()

    # ----- footer pills -----
    pill_font = tkfont.Font(family="Segoe UI Text", size=13, weight="bold")
    pill_h = pill_font.metrics("linespace") + 24
    footer_y = HEIGHT - 32 - pill_h
    if on_check:
        _glass_pill(canvas, 48, footer_y, "Check again", lambda: _run_check(), pill_font)
    _glass_pill(canvas, WIDTH - 48, footer_y, "Close", _close_animated, pill_font, anchor="ne")

    # ----- body: scrollable stack of glass tiles -----
    view_x = 40
    view_w = WIDTH - 80
    view_h = footer_y - 16 - view_top
    view = tk.Canvas(
        canvas, width=view_w, height=view_h, bg=BG, highlightthickness=0, bd=0,
        yscrollincrement=20,
    )
    canvas.create_window(view_x, view_top, window=view, anchor="nw")
    view._bg_photo = ImageTk.PhotoImage(
        glass.crop((view_x, view_top, view_x + view_w, view_top + view_h)))
    bg_id = view.create_image(0, 0, image=view._bg_photo, anchor="nw")
    _round_widget(view, 28)

    base_font = tkfont.Font(family="Segoe UI Text", size=15)
    bold_font = tkfont.Font(family="Segoe UI Text", size=15, weight="bold")
    head_font = tkfont.Font(family="Segoe UI Variable Display", size=18, weight="bold")
    badge_font = tkfont.Font(family="Segoe UI Text", size=11, weight="bold")
    line_h = base_font.metrics("linespace") + 4
    space_w = base_font.measure(" ")
    bold_pattern = re.compile(r"\*\*(.+?)\*\*")

    def sync_bg():
        view.coords(bg_id, 0, view.canvasy(0))

    def on_wheel(event):
        view.yview_scroll(int(-event.delta / 120) * 3, "units")
        sync_bg()

    view.bind("<MouseWheel>", on_wheel)

    def words(line):
        """Split a line into (word, is_bold) tokens, honoring **bold** spans."""
        out, pos = [], 0
        for m in bold_pattern.finditer(line):
            out += [(w, False) for w in line[pos:m.start()].split()]
            out += [(w, True) for w in m.group(1).split()]
            pos = m.end()
        out += [(w, False) for w in line[pos:].split()]
        return out

    def parse_sections(markdown):
        sections = []  # [title or None, [lines]]
        for raw in markdown.splitlines():
            line = raw.rstrip()
            heading = re.match(r"#{1,6}\s+(.*)", line)
            if heading:
                sections.append([heading.group(1).strip(), []])
            elif line:
                if not sections:
                    sections.append([None, []])
                sections[-1][1].append(line)
        return sections

    def render(markdown):
        view.delete("content")
        view._tile_imgs = []
        tile_w = view_w - 2 * TILE_INSET
        text_l = TILE_PAD
        text_r = tile_w - TILE_PAD
        y_off = TILE_INSET

        def flow(ops, tokens, x_start, x_wrap, y):
            # Consecutive same-font words share one canvas item; one item per
            # word made every popup open slow.
            cx = x_start
            run = None  # [x, y, font, text]

            def flush():
                nonlocal run
                if run:
                    ops.append(("text", run[0], run[1], run[3], run[2], GLASS_TEXT, False))
                    run = None

            for word, bold in tokens:
                font = bold_font if bold else base_font
                w = font.measure(word)
                if cx > x_start and cx + w > text_r:
                    flush()
                    cx, y = x_wrap, y + line_h
                if run and run[2] is font:
                    run[3] += " " + word
                else:
                    flush()
                    run = [cx, y, font, word]
                cx += w + space_w
            flush()
            return y + line_h

        for title, lines in parse_sections(markdown):
            ops = []
            y = TILE_PAD
            if title:
                ops.append(("text", text_l, y, title.replace("**", ""), head_font,
                            GLASS_TEXT, True))
                y += head_font.metrics("linespace") + 10
            for line in lines:
                if line.startswith(("- ", "* ")):
                    content = line[2:].lstrip()
                    is_new = NEW_MARK in content
                    content = content.replace(NEW_MARK, "").strip()
                    if is_new:
                        bw = badge_font.measure("NEW") + 16
                        ops.append(("badge", text_l, y + 3, bw, line_h - 6))
                        indent = text_l + bw + 10
                    else:
                        ops.append(("text", text_l, y, "\u2022", base_font, GLASS_TEXT, False))
                        indent = text_l + 22
                    y = flow(ops, words(content), indent, indent, y)
                else:
                    y = flow(ops, words(line), text_l, text_l, y)
                y += 4
            tile_h = int(y + TILE_PAD - 4)

            tile = ImageTk.PhotoImage(
                _glass_overlay(tile_w, tile_h, TILE_RADIUS, 0.10, (0.18, 0.02), 0.45))
            view._tile_imgs.append(tile)
            view.create_image(TILE_INSET, y_off, image=tile, anchor="nw", tags="content")
            ox = TILE_INSET
            for op in ops:
                if op[0] == "badge":
                    _, bx, by, bw, bh = op
                    _round_rect(view, ox + bx, y_off + by, ox + bx + bw, y_off + by + bh,
                                bh // 2, fill=NEW_ACCENT, outline="", tags="content")
                    view.create_text(ox + bx + bw / 2, y_off + by + bh / 2, text="NEW",
                                     fill="#ffffff", font=badge_font, tags="content")
                else:
                    _, tx, ty, txt, font, fill, shadow = op
                    if shadow:
                        view.create_text(ox + tx + 1, y_off + ty + 2, text=txt, font=font,
                                         fill=GLASS_SHADOW, anchor="nw", tags="content")
                    view.create_text(ox + tx, y_off + ty, text=txt, font=font, fill=fill,
                                     anchor="nw", tags="content")
            y_off += tile_h + TILE_GAP

        total = y_off - TILE_GAP + TILE_INSET
        view.configure(scrollregion=(0, 0, view_w, max(total, view_h)))
        view.yview_moveto(0)
        sync_bg()

    render(text)

    # ----- live backdrop -----
    # Two-stage pipeline so it can keep up with video: a capture thread (paced
    # by the display refresh) hands raw frames to a render thread (blur +
    # tint), and a 10ms main-thread tick swaps the newest finished frame into
    # the Tk images. Each stage only ever keeps the latest frame, so a slow
    # stage drops frames instead of building up lag. Frames where nothing
    # behind the popup changed are skipped.
    live = {"raw": None, "frame": None, "pos": (final_x, final_y), "run": False}
    raw_ready = threading.Event()

    def _capture_loop():
        last = None
        while live["run"]:
            try:
                x, y = live["pos"]
                raw = grabber.grab_raw(x, y)
                if raw != last:
                    last = raw
                    live["raw"] = raw
                    raw_ready.set()
                else:
                    time.sleep(0.004)
            except Exception:
                time.sleep(0.2)

    def _render_loop():
        while live["run"]:
            if not raw_ready.wait(0.25):
                continue
            raw_ready.clear()
            raw = live["raw"]
            try:
                live["frame"] = backdrop.render(grabber.to_image(raw))
            except Exception:
                pass

    def _live_tick():
        try:
            if not top.winfo_exists():
                live["run"] = False
                return
            live["pos"] = (top.winfo_x(), top.winfo_y())
            frame = live["frame"]
            if frame is not None:
                live["frame"] = None
                glass_photo.paste(frame)
                view._bg_photo.paste(
                    frame.crop((view_x, view_top, view_x + view_w, view_top + view_h)))
            top.after(TICK_MS, _live_tick)
        except tk.TclError:
            live["run"] = False

    def _start_live():
        # Only live if the popup can hide itself from capture; otherwise the
        # backdrop would blur its own previous frame.
        if grabber is not None and _exclude_from_capture(top):
            live["run"] = True
            threading.Thread(target=_capture_loop, daemon=True).start()
            threading.Thread(target=_render_loop, daemon=True).start()
            _live_tick()

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
                    render(
                        f"## Check failed\n\n{error_msg}\n\nSee the terminal for details."
                    )
                    status_var.set(f"Error: {short}")
                elif changed and new_text:
                    render(new_text)
                    status_var.set("Updated with new items")
                    top.after(STATUS_HOLD_MS, lambda: status_var.set(""))
                else:
                    status_var.set("")

            top.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    # Apply rounded corner mask to match Apple card UI
    _apply_custom_rounded_region(top)

    # Start off-screen, animate up, then kick off the background check
    top.geometry(f"{WIDTH}x{HEIGHT}+{final_x}+{start_y}")
    top.deiconify()
    _animate(top, final_x, final_y, start_y, closing=False,
             on_done=lambda: (_start_live(), _run_check()))

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