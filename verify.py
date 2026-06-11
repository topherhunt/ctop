"""Verification: render one ctop frame headless and check the success criterion
(a pinned core shows ~100% CPU at the top of the process table)."""
import re
import subprocess
import time

import psutil
from rich.console import Console

import ctop

WIDTH, HEIGHT = 100, 30

pinned = subprocess.Popen(["yes"], stdout=subprocess.DEVNULL)
try:
    ctop.prime_cpu_samplers()
    time.sleep(1)
    snap = ctop.take_snapshot()

    # Sanity: snapshot numbers
    assert len(snap["cores"]) == psutil.cpu_count(), snap["cores"]
    assert all(0 <= c <= 100 for c in snap["cores"])
    assert snap["mem"].total > 0 and snap["tasks"] > 100
    print(f"cores: {[f'{c:.0f}' for c in snap['cores']]}")
    print(f"mem: {ctop.human(snap['mem'].used)}/{ctop.human(snap['mem'].total)}  "
          f"swap: {ctop.human(snap['swap'].used)}/{ctop.human(snap['swap'].total)}")
    print(f"tasks: {snap['tasks']}  threads: {snap['threads']}  load: {snap['load']}")

    # Success criterion: pinned process reads ~100% and sorts to the top
    rows = sorted(snap["procs"], key=lambda r: (-r["cpu"], r["pid"]))
    top_row = rows[0]
    print(f"\ntop row: pid={top_row['pid']} name={top_row['name']} cpu={top_row['cpu']:.1f}")
    assert top_row["pid"] == pinned.pid, "pinned proc not at top of CPU sort"
    assert top_row["cpu"] >= 90, f"pinned core reads only {top_row['cpu']}%"

    # MEM sort puts a big-RSS proc on top
    by_mem = sorted(snap["procs"], key=lambda r: (-r["rss"], r["pid"]))
    print(f"mem sort top: {by_mem[0]['name']} rss={ctop.human(by_mem[0]['rss'])}")
    assert by_mem[0]["rss"] > 100 * 1024 * 1024

    # Render a full frame to text
    console = Console(width=WIDTH, height=HEIGHT, force_terminal=True)
    with console.capture() as cap:
        console.print(ctop.render(snap, console))
    frame = cap.get()
    plain = re.sub(r"\x1b\[[0-9;]*m", "", frame)
    plain_lines = plain.splitlines()
    print(f"\nframe renders {len(plain_lines)} lines at {WIDTH}x{HEIGHT}:")
    print(frame)
    assert len(plain_lines) <= HEIGHT, f"frame overflows: {len(plain_lines)} lines"

    # Caret marks the active sort column, and the clickable spans computed in
    # render() line up with where rich actually painted the headers
    header_line = next(l for l in plain_lines if "PID" in l and "COMMAND" in l)
    assert "CPU%" + ctop.SORT_CARET in header_line, "no caret on CPU% header"
    assert "MEM" + ctop.SORT_CARET not in header_line
    spans = ctop.state["spans"]
    cpu_x = header_line.index("CPU%")
    mem_x = header_line.index("MEM")
    assert spans["cpu"][0] <= cpu_x <= spans["cpu"][1], (cpu_x, spans)
    assert spans["mem"][0] <= mem_x <= spans["mem"][1], (mem_x, spans)
    assert "STATE" not in header_line

    # Footer hint bar present
    assert "q quit" in plain, "footer hint bar missing"

    # MEM sort renders with caret on MEM
    ctop.state["sort"] = "mem"
    with console.capture() as cap:
        console.print(ctop.render(snap, console))
    plain2 = re.sub(r"\x1b\[[0-9;]*m", "", cap.get())
    header2 = next(l for l in plain2.splitlines() if "PID" in l and "COMMAND" in l)
    assert "MEM" + ctop.SORT_CARET in header2, "no caret on MEM header after sort switch"
    ctop.state["sort"] = "cpu"

    print("ALL CHECKS PASSED")
finally:
    pinned.kill()
