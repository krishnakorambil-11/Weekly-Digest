"""
Course Digest - a tray app that pulls course emails from Gmail and
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
current_popup: tk.Toplevel = None  # tracks the active flyout instance for toggling


def make_icon_image():
    """Draws a simple circular icon at runtime (no external asset needed)."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=(66, 133, 244, 255))
    draw.text((20, 16), "C", fill="white")
    return img


def _toggle_digest(icon=None, item=None):
    """Hotkey or tray click action: opens the popup if it's closed, 
    or triggers its close animation if it's currently open."""
    global current_popup

    def task():
        global current_popup
        # If a popup already exists and hasn't been destroyed, toggle it off
        if current_popup is not None and current_popup.winfo_exists():
            if hasattr(current_popup, "_close_animated"):
                current_popup._close_animated()
            else:
                current_popup.destroy()
            current_popup = None
        else:
            subtitle, text = summarizer.get_cached_summary()
            current_popup = popup.show_popup(
                root, "This Week:", subtitle, text, on_check=summarizer.ensure_fresh
            )

    root.after(0, task)


def _view_without_checking(icon=None, item=None):
    """Pure cached view - zero network calls, for a guaranteed no-quota peek."""
    global current_popup

    def task():
        global current_popup
        if current_popup is not None and current_popup.winfo_exists():
            if hasattr(current_popup, "_close_animated"):
                current_popup._close_animated()
            else:
                current_popup.destroy()
            current_popup = None
        else:
            subtitle, text = summarizer.get_cached_summary()
            current_popup = popup.show_popup(root, "This Week:", subtitle, text)

    root.after(0, task)


def _force_full_refresh(icon=None, item=None):
    """Manually triggers the same full rebuild that normally only
    happens automatically on Saturday."""

    def worker():
        try:
            text = summarizer.full_rebuild()
            subtitle = "Just did a full refresh"
        except Exception as exc:
            text = f"Something went wrong generating the summary:\n\n{exc}"
            subtitle = "Refresh failed"

        def show():
            global current_popup
            current_popup = popup.show_popup(root, "This Week:", subtitle, text)

        root.after(0, show)

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


def _scheduler_loop():
    config = summarizer.load_config()

    sched = config.get("schedule", {"day": "saturday", "time": "18:00"})
    day = sched.get("day", "saturday").lower()
    at_time = sched.get("time", "18:00")
    getattr(schedule.every(), day).at(at_time).do(summarizer.full_rebuild)

    poll_hours = config.get("poll_interval_hours", 2)
    schedule.every(poll_hours).hours.do(summarizer.ensure_fresh)

    while True:
        schedule.run_pending()
        time.sleep(60)


def main():
    global root
    root = tk.Tk()
    root.withdraw()  # invisible; just keeps the Tk main loop alive

    threading.Thread(target=_scheduler_loop, daemon=True).start()

    hotkey = summarizer.load_config().get("hotkey", "ctrl+`")
    print(f"Registering hotkey: {hotkey}")
    try:
        keyboard.add_hotkey(hotkey, _toggle_digest)
    except Exception as err:
        print(f"Failed to bind {hotkey}, falling back to ctrl+grave: {err}")
        keyboard.add_hotkey("ctrl+grave", _toggle_digest)

    print("Hotkey registered successfully")

    menu = Menu(
        MenuItem("Toggle digest", _toggle_digest, default=True),
        MenuItem("View without checking", _view_without_checking),
        MenuItem("Force full refresh", _force_full_refresh),
        MenuItem("Edit settings...", _open_settings),
        MenuItem("Quit", _quit),
    )
    icon = Icon("course_digest", make_icon_image(), "This Week:", menu)
    icon.run_detached()  # tray icon lives on its own thread

    root.mainloop()  # main thread belongs to Tk for the app's whole life


if __name__ == "__main__":
    main()