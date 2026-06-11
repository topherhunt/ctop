#!/usr/bin/env python3
"""ctop -- a colored, accurate `top` for macOS.

htop-style display (per-core bars, mem/swap bars, colored process table) with
per-process CPU% that matches Apple's `top` (Irix mode: one full core = 100%).
Keys: c = sort by CPU, m = sort by MEM, q = quit. The CPU%/MEM column headers
are also clickable; a hint bar at the bottom lists the keys.
"""
import atexit
import math
import sys
import termios
import threading
import time
import tty

import psutil
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

REFRESH_SECONDS = 1.0
PROC_ATTRS = ["pid", "name", "cpu_percent", "memory_info", "num_threads"]
SORT_CARET = "▼"  # ▼ (all sorts are descending)
MOUSE_ON = "\x1b[?1000h\x1b[?1006h"
MOUSE_OFF = "\x1b[?1006l\x1b[?1000l"

# spans: 0-indexed inclusive screen-column ranges of the clickable sort
# columns, recomputed from the terminal width on every render.
state = {"sort": "cpu", "quit": False, "spans": {}}


def load_color(pct):
    if pct < 50:
        return "green"
    if pct < 80:
        return "yellow"
    return "red"


def human(nbytes):
    n = float(nbytes)
    for unit in "BKMGT":
        if n < 1000 or unit == "T":
            decimals = 0 if (unit in "BK" or n >= 100) else 1
            return f"{n:.{decimals}f}{unit}"
        n /= 1024.0


def format_uptime(secs):
    mins = int(secs // 60)
    days, rem = divmod(mins, 1440)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"up {days}d {hours}h"
    return f"up {hours}:{minutes:02d}"


def take_snapshot():
    procs = []
    total = threads = 0
    for p in psutil.process_iter(PROC_ATTRS):
        info = p.info
        total += 1
        threads += info["num_threads"] or 0
        procs.append({
            "pid": info["pid"],
            "name": info["name"] or "?",
            "cpu": info["cpu_percent"] or 0.0,
            "rss": info["memory_info"].rss if info["memory_info"] else 0,
            "threads": info["num_threads"] or 0,
        })
    return {
        "cores": psutil.cpu_percent(percpu=True),
        "mem": psutil.virtual_memory(),
        "swap": psutil.swap_memory(),
        "load": psutil.getloadavg(),
        "uptime": time.time() - psutil.boot_time(),
        "tasks": total,
        "threads": threads,
        "procs": procs,
    }


def bar(frac, inner_width, txt, color):
    """htop-style bar: [|||||      txt] with txt right-aligned inside."""
    frac = max(0.0, min(1.0, frac))
    avail = max(inner_width - len(txt), 0)
    fill = round(frac * avail)
    t = Text()
    t.append("[")
    t.append("|" * fill, style=color)
    t.append(" " * (avail - fill))
    t.append(txt, style="bright_black")
    t.append("]")
    return t


def make_header(snap, width):
    lines = []

    clock = time.strftime("%H:%M:%S")
    right = f"{clock}  {format_uptime(snap['uptime'])}"
    title = Text(" ctop", style="bold cyan")
    title.append(" " * max(width - 5 - len(right) - 1, 1))
    title.append(right, style="bold")
    lines.append(title)

    # Two-column grid: 1 lead + (4 label + 2 brackets + inner) + 3 gutter + same
    inner = max((width - 16) // 2, 10)
    cores = snap["cores"]
    rows = math.ceil(len(cores) / 2)
    for r in range(rows):
        line = Text(" ")
        for col, c in enumerate((r, r + rows)):
            if c >= len(cores):
                continue
            if col:
                line.append("   ")
            pct = cores[c]
            line.append(f"{c:>3} ", style="bold")
            line.append_text(bar(pct / 100, inner, f"{pct:.0f}%", load_color(pct)))
        lines.append(line)

    mem, swap = snap["mem"], snap["swap"]
    swap_frac = swap.used / swap.total if swap.total else 0.0
    line = Text(" ")
    line.append("Mem ", style="bold")
    line.append_text(bar(mem.percent / 100, inner,
                         f"{human(mem.used)}/{human(mem.total)}", load_color(mem.percent)))
    line.append("   ")
    line.append("Swp ", style="bold")
    line.append_text(bar(swap_frac, inner,
                         f"{human(swap.used)}/{human(swap.total)}", load_color(swap_frac * 100)))
    lines.append(line)

    load = snap["load"]
    summary = Text(" ")
    summary.append(f"Load {load[0]:.2f} {load[1]:.2f} {load[2]:.2f}", style="bold")
    summary.append(f"    Tasks {snap['tasks']}    Threads {snap['threads']}")
    lines.append(summary)
    lines.append(Text(""))
    return lines


def make_table(procs, sort_key, nrows):
    key = "cpu" if sort_key == "cpu" else "rss"
    rows = sorted(procs, key=lambda r: (-r[key], r["pid"]))[:nrows]
    table = Table(box=None, expand=True, pad_edge=False, padding=(0, 1),
                  header_style="bold black on green")
    table.add_column("PID", justify="right", width=7, no_wrap=True)
    table.add_column("COMMAND", ratio=1, no_wrap=True, overflow="ellipsis")
    table.add_column("CPU%" + (SORT_CARET if key == "cpu" else ""),
                     justify="right", width=6, no_wrap=True)
    table.add_column("MEM" + (SORT_CARET if key == "rss" else ""),
                     justify="right", width=7, no_wrap=True)
    table.add_column("TH", justify="right", width=4, no_wrap=True)
    for r in rows:
        table.add_row(
            str(r["pid"]),
            r["name"],
            Text(f"{r['cpu']:.1f}", style=load_color(r["cpu"])),
            human(r["rss"]),
            str(r["threads"]),
        )
    return table


def make_footer(width):
    footer = Text(" c", style="bold")
    footer.append(" sort CPU   ", style="bright_black")
    footer.append("m", style="bold")
    footer.append(" sort MEM   ", style="bright_black")
    footer.append("q", style="bold")
    footer.append(" quit   (or click the CPU%/MEM headers)", style="bright_black")
    footer.truncate(width)
    return footer


def render(snap, console):
    width, height = console.size
    # Column layout (see make_table): PID 7 | COMMAND flex | CPU% 6 | MEM 7 |
    # TH 4, with 2 spaces between columns -> the fixed columns hang off the
    # right edge at constant offsets. Verified against rendered output in
    # verify.py.
    state["spans"] = {"cpu": (width - 21, width - 16), "mem": (width - 13, width - 7)}
    header = make_header(snap, width)
    nrows = max(height - len(header) - 2, 1)  # -2: table header + footer
    return Group(*header,
                 make_table(snap["procs"], state["sort"], nrows),
                 make_footer(width))


def handle_click(seq):
    try:
        btn, x, _y = (int(v) for v in seq.split(";"))
    except ValueError:
        return
    if btn != 0:  # left button press only
        return
    for key, (x0, x1) in state["spans"].items():
        if x0 <= x - 1 <= x1:  # mouse x is 1-indexed
            state["sort"] = key


def key_reader():
    while not state["quit"]:
        ch = sys.stdin.read(1)
        if ch == "q":
            state["quit"] = True
        elif ch == "c":
            state["sort"] = "cpu"
        elif ch == "m":
            state["sort"] = "mem"
        elif ch == "\x1b":
            # SGR mouse report: ESC [ < btn ; x ; y M (press) / m (release)
            if sys.stdin.read(1) != "[" or sys.stdin.read(1) != "<":
                continue
            seq = ""
            while len(seq) < 16:
                c = sys.stdin.read(1)
                if c in "Mm":
                    if c == "M":
                        handle_click(seq)
                    break
                seq += c


def prime_cpu_samplers():
    """First cpu_percent() call always returns 0.0; take a throwaway reading."""
    psutil.cpu_percent(percpu=True)
    for _ in psutil.process_iter(["cpu_percent"]):
        pass


def main():
    console = Console()
    console.set_window_title("ctop")
    interactive = sys.stdin.isatty()
    old_attrs = None
    if interactive:
        fd = sys.stdin.fileno()
        old_attrs = termios.tcgetattr(fd)
        atexit.register(termios.tcsetattr, fd, termios.TCSADRAIN, old_attrs)
        tty.setcbreak(fd)
        threading.Thread(target=key_reader, daemon=True).start()
    try:
        prime_cpu_samplers()
        time.sleep(REFRESH_SECONDS)
        snap = take_snapshot()
        if interactive:
            # Must happen outside Live: it redirects sys.stdout and would
            # swallow the escape sequence.
            sys.stdout.write(MOUSE_ON)
            sys.stdout.flush()
        with Live(render(snap, console), console=console, screen=True,
                  auto_refresh=False) as live:
            while not state["quit"]:
                deadline = time.monotonic() + REFRESH_SECONDS
                shown_sort = state["sort"]
                while time.monotonic() < deadline and not state["quit"]:
                    if state["sort"] != shown_sort:  # re-sort immediately on keypress
                        shown_sort = state["sort"]
                        live.update(render(snap, console), refresh=True)
                    time.sleep(0.05)
                if state["quit"]:
                    break
                snap = take_snapshot()
                live.update(render(snap, console), refresh=True)
    except KeyboardInterrupt:
        pass
    finally:
        if interactive:
            sys.stdout.write(MOUSE_OFF)
            sys.stdout.flush()
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attrs)


if __name__ == "__main__":
    main()
