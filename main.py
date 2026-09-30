"""
Course Digest - a tray app that pulls course updates from Canvas and
asks Gemini to turn them into a weekly "what's due" summary, shown
in a flyout that slides up from the taskbar.

Run with: python main.py
"""

import subprocess
import sys
import threading
import time
import tkinter as tk

import keyboard
import schedule
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

import popup
import summarizer

CONFIG_PATH = summarizer.CONFIG_PATH

root: tk.Tk = None  # set in main(), used by every popup call
current_popup: tk.Toplevel = None  # the open flyout, if any


def make_icon_image():
    """Draws a simple circular icon at runtime (no external asset needed)."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=(66, 133, 244, 255))
    draw.text((20, 16), "C", fill="white")
    return img


# ---------- popup helpers (always called on the Tk thread) ----------

def _close_popup() -> bool:
    """Closes the open flyout. Returns True if there was one to close."""
    global current_popup
    if current_popup is not None and current_popup.winfo_exists():
        if hasattr(current_popup, "_close_animated"):
            current_popup._close_animated()
        else:
            current_popup.destroy()
        current_popup = None
        return True
    current_popup = None
    return False


def _open_popup(subtitle, text, on_check=None):
    global current_popup
    _close_popup()
    if on_check is not None:
        current_popup = popup.show_popup(root, "This Week:", subtitle, text, on_check=on_check)
    else:
        current_popup = popup.show_popup(root, "This Week:", subtitle, text)


# ---------- tray / hotkey actions ----------

def _toggle_digest(icon=None, item=None):
    """Opens the flyout (with a freshness check), or closes it if open."""
    def task():
        if not _close_popup():
            subtitle, text = summarizer.get_cached_summary()
            _open_popup(subtitle, text, on_check=summarizer.ensure_fresh)

    root.after(0, task)


def _view_without_checking(icon=None, item=None):
    """Pure cached view - zero network calls."""
    def task():
        if not _close_popup():
            subtitle, text = summarizer.get_cached_summary()
            _open_popup(subtitle, text)

    root.after(0, task)


def _force_full_refresh(icon=None, item=None):
    """Full rebuild in a background thread so the UI doesn't freeze."""
    def worker():
        try:
            text = summarizer.full_rebuild()
            subtitle = "Just did a full refresh"
        except Exception as exc:
            text = f"Something went wrong generating the summary:\n\n{exc}"
            subtitle = "Refresh failed"
        root.after(0, lambda: _open_popup(subtitle, text))

    threading.Thread(target=worker, daemon=True).start()


def _open_settings(icon=None, item=None):
    if sys.platform.startswith("win"):
        subprocess.Popen(["notepad.exe", CONFIG_PATH])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-t", CONFIG_PATH])
    else:
        subprocess.Popen(["xdg-open", CONFIG_PATH])


def _quit(icon, item):
    icon.stop()
    root.after(0, root.quit)


# ---------- scheduler ----------

def _safe(fn):
    """Keeps one failed job from killing the whole scheduler thread."""
    def wrapper():
        try:
            fn()
        except Exception as exc:
            print(f"[Error] {fn.__name__} failed: {exc}")
    wrapper.__name__ = fn.__name__
    return wrapper


def _scheduler_loop():
    config = summarizer.load_config()

    sched = config.get("schedule", {"day": "saturday", "time": "18:00"})
    day = sched.get("day", "saturday").lower()
    at_time = sched.get("time", "18:00")

    try:
        getattr(schedule.every(), day).at(at_time).do(_safe(summarizer.full_rebuild))
    except AttributeError:
        print(f"[Warning] Unknown schedule day '{day}', using saturday 18:00.")
        schedule.every().saturday.at("18:00").do(_safe(summarizer.full_rebuild))

    poll_hours = config.get("poll_interval_hours", 2)
    schedule.every(poll_hours).hours.do(_safe(summarizer.ensure_fresh))

    while True:
        schedule.run_pending()
        time.sleep(60)


# ---------- entry point ----------

def main():
    global root
    root = tk.Tk()
    root.withdraw()  # invisible; keeps the Tk main loop alive

    threading.Thread(target=_scheduler_loop, daemon=True).start()

    hotkey = summarizer.load_config().get("hotkey", "ctrl+`")
    print(f"Registering hotkey: {hotkey}")
    try:
        keyboard.add_hotkey(hotkey, _toggle_digest)
    except Exception as err:
        print(f"Failed to bind {hotkey}, falling back to ctrl+grave: {err}")
        try:
            keyboard.add_hotkey("ctrl+grave", _toggle_digest)
        except Exception as e:
            print(f"Could not register global hotkeys: {e}")

    menu = Menu(
        MenuItem("Toggle digest", _toggle_digest, default=True),
        MenuItem("View without checking", _view_without_checking),
        MenuItem("Force full refresh", _force_full_refresh),
        MenuItem("Edit settings...", _open_settings),
        MenuItem("Quit", _quit),
    )
    icon = Icon("course_digest", make_icon_image(), "This Week:", menu)
    icon.run_detached()  # tray icon lives on its own thread

    root.mainloop()  # main thread belongs to Tk


if __name__ == "__main__":
    main()