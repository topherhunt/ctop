# ctop -- a colored, accurate `top` for macOS

A small TUI process monitor for macOS that looks like htop (per-core color bars,
memory/swap bars, colored process table) but reports **accurate per-process CPU**,
unlike htop on macOS. Updates once per second.

## 1. Goal & scope

**Build (the "Nice" tier):**
- Per-core CPU color bars (one per logical core, green/yellow/red by load)
- Memory and swap bars
- Summary line: load average, uptime, task counts, thread count
- Process table: PID, command, CPU%, MEM (RSS), threads, state
- Sortable by **CPU% desc (default)** or **MEM desc**, toggled with a keypress
- 1-second refresh

**Explicitly skip:** tree view, search/filter, scrolling, kill/renice, mouse,
themes, config files, per-process IO/net, Irix/Solaris toggle, setup screens.

**Success criterion (the whole reason this exists):** a process pinning one core
reads ~100% CPU, matching Apple's `top` -- the exact number htop gets wrong.

## 2. Research findings (verified on this machine, 2026-06-11)

| Fact | Implication |
|------|-------------|
| Apple `top` header gives only **aggregate** CPU (`% user/sys/idle`), **no per-core** | Per-core bars cannot come from `top`; need the Mach `host_processor_info` API |
| `top` truncates COMMAND to ~16 chars (`Google Chrome He`) | Parsing `top` gives ugly truncated names; psutil gives full names |
| `top -l 1` first frame CPU is all `0.0` | Any sampler must discard/prime the first sample |
| 8 logical cores (`hw.logicalcpu = 8`) | 8 core bars; fits a 2-column layout like htop |
| Python 3.14.5 at `/opt/homebrew/bin/python3` (also 3.13, 3.11) | Homebrew Python -- PEP 668 "externally managed", needs a venv |
| `rich` and `psutil` **not installed** | Must install into a project venv |

The pivotal finding: **htop's per-core meters were always accurate** (they come
from `host_processor_info`); only its per-*process* CPU is wrong. `psutil` reads
per-core from that same reliable API, so the bars are safe. The open question is
purely whether psutil's per-*process* CPU is accurate -- settled in Phase 0.

## 3. Architecture decision: psutil-first, with a verification gate

Because per-core bars force a Mach-API dependency (psutil) regardless, the choice
is not "top vs psutil" but "do we *also* need to parse top for the process table?"

- **Architecture B (recommended): psutil for everything.** One dependency, full
  command names, robust structured data, no fragile text parsing. Valid **only if**
  psutil's per-process CPU matches `top`.
- **Architecture A (fallback): psutil for bars/mem, parse `top -l 0 -s 1` for the
  process table.** Guarantees top-identical process CPU, but adds brittle text
  parsing and truncated names. Use only if Phase 0 shows psutil is inaccurate.

### Phase 0 -- the deciding spike (do this first, ~20 min)
1. Create venv, install psutil + rich.
2. Pin one core: `yes > /dev/null &` (one core ~100%).
3. Print that PID's CPU from psutil once/sec for ~5s, beside `top -l 2`'s number
   for the same PID.
4. **Decision:** psutil reads ~95-100% (one core, Irix-style) and tracks top within
   a few points -> **Architecture B**. If it underreports like htop -> Architecture A.

psutil computes per-process CPU as (process busy-time delta / wall-clock delta) x 100,
not divided by core count, so a full core should read ~100%. Expectation: B passes.
Everything below assumes B; the §11 fallback covers A.

## 4. Data source map (Architecture B)

| UI element | psutil source | Notes |
|------------|---------------|-------|
| Per-core bars | `cpu_percent(percpu=True)` | host_processor_info; one value per core |
| Core color split (optional) | `cpu_times_percent(percpu=True)` | user vs sys shading if wanted |
| Mem bar | `virtual_memory()` | `.used` / `.total`; macOS "used" excludes cached |
| Swap bar | `swap_memory()` | `.used` / `.total` |
| Load average | `getloadavg()` | 1/5/15 min |
| Uptime | `boot_time()` | now - boot; now via `time.time()` |
| Task counts | iterate `process_iter` | total + running count from status |
| Thread count | sum `num_threads()` | aggregate across processes |
| Process row | `process_iter([...])` | pid, name, cpu_percent, memory_info.rss, num_threads, status |

**CPU% sampling rule:** call `cpu_percent(interval=None)` (non-blocking) once per
loop. The first reading after process discovery is `0.0` (no interval yet), so the
first painted frame will under-read -- prime once before the first paint, then the
1s cadence supplies the delta. Same rule for system `cpu_percent`.

## 5. Tech stack & install

- **Language:** Python 3.13 (use 3.13, not 3.14 -- see Risks; psutil C-extension
  wheels lag the newest interpreter).
- **Libraries:** `psutil` (data), `rich` (rendering; `rich.live.Live` + `Table`).
- **Keyboard:** stdlib `termios` + `tty` cbreak mode in a reader thread (no extra dep)
  for the c/m sort toggle and `q` to quit. Avoids pulling in `textual`.

Install (PEP 668 safe):
```
cd ~/Sites/personal/ctop
python3.13 -m venv .venv
.venv/bin/pip install psutil rich
```
Run via a wrapper `ctop` so the venv is implicit:
```
#!/bin/sh
exec "$HOME/Sites/personal/ctop/.venv/bin/python" "$HOME/Sites/personal/ctop/ctop.py" "$@"
```
(`chmod +x ctop`, symlink into a PATH dir.)

## 6. Layout

```
 ctop                                                  16:29:37  up 6d 7h
  0 [||||||||||||          45%]    4 [||||||||              32%]
  1 [|||||||||||||||||     58%]    5 [|||||||||||           41%]
  2 [|||||||||             35%]    6 [||||||||||||||||      62%]
  3 [|||||||               28%]    7 [|||||||||||||||||||   74%]
  Mem [|||||||||||||||||   6.5G/8.0G]   Swp [|||||||||   5.8G/7.0G]
  Load 5.89 5.15 5.56    Tasks 523 (4 run)    Threads 3616    [sort: CPU]

  PID     COMMAND                    CPU%    MEM     TH   STATE
  54034   node                       98.3    304M    12   running
  382     WindowServer               31.0    596M    18   sleeping
  ...                          (fills remaining terminal height)
```

- Bars: filled vs empty blocks scaled to column width; color **green <50%,
  yellow 50-80%, red >=80%**. Same thresholds for the CPU% cell.
- Two-column core grid (8 cores -> 4 rows x 2). Generalize: `ceil(ncpu/2)` rows.
- Process table fills `terminal_height - header_rows`; render only the rows that
  fit (top N after sort) -- no scrolling.

## 7. Implementation phases

```
0. Spike: psutil vs top on a pinned core        -> verify: ~100%, picks Arch B/A
1. Data layer: snapshot() -> dict of cores, mem, swap, load, tasks, procs
                                                -> verify: prints sane numbers, 1/s
2. Render header: core bars + mem/swap + summary via rich
                                                -> verify: bars match Activity Monitor
3. Render process table, sorted CPU desc, color thresholds
                                                -> verify: node-pinned core shows ~100% at top
4. rich.Live loop @ 1s; prime CPU sampling before first paint
                                                -> verify: smooth no-flicker updates, no first-frame 0s
5. Key thread: 'c'/'m' switch sort, 'q' quits; redraw on next tick
                                                -> verify: keys re-sort live; q restores terminal
6. Wrapper script + venv; resize handling (read terminal size each frame)
                                                -> verify: runs as `ctop`, adapts to window resize
```

Single file `ctop.py`, ~200-250 lines. Structure: `snapshot()`, `make_header()`,
`make_table(procs, sort_key)`, `key_reader()` thread, `main()` Live loop.

## 8. Performance -- is 1s OK?

Yes, comfortably. Per tick the cost is:
- One `host_processor_info` call (per-core) -- microseconds.
- `process_iter` over ~500 procs reading cached fields -- a few ms; psutil reuses
  `proc_pid_taskinfo` per process. Expect single-digit ms total.
- One rich repaint of ~40 visible rows -- a few ms.

Total well under ~10ms/sec, i.e. <1% of one core. `top` itself and htop do the same
work at the same cadence. 1s is the natural floor for CPU% accuracy anyway (need a
sampling interval); going faster than ~0.5s adds noise, not value. **No concern.**

One caveat: `process_iter` with `oneshot()` per process avoids redundant syscalls --
use it. Don't fetch fields you don't display.

## 9. Sorting & interaction

- Default sort: CPU% desc. `c` -> CPU%, `m` -> MEM desc. `q` quits.
- Stable secondary sort by PID so equal-CPU rows don't jitter frame to frame.
- Key handling: background thread in `tty.setcbreak`, sets a shared `sort_key`;
  main loop reads it each tick. Restore termios on exit via `try/finally` (and an
  `atexit`) so a crash never leaves the terminal in raw mode.

## 10. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| **psutil per-process CPU inaccurate like htop** | Phase 0 gate; fall back to Arch A (parse top) -- §11 |
| psutil has no cp314 wheel -> source build needs Xcode CLT | Use **python3.13**; or `xcode-select --install` |
| macOS `virtual_memory().used` differs from top's "PhysMem used" | Document the definition; match top's fields if it bothers you (wired+compressed+active) |
| First-frame 0% CPU | Prime `cpu_percent` once before first paint |
| Terminal left in raw mode on crash | `try/finally` + `atexit` restore |
| Process vanishes mid-iteration | Catch `NoSuchProcess`/`AccessDenied`, skip row |
| Many short-lived procs churn the table | Acceptable; mirrors top's behavior |

## 11. Fallback: Architecture A (only if Phase 0 fails)

Keep psutil for bars/mem/swap/load. Replace the process table source with a parser
over a streamed `top`:
```
top -l 0 -s 1 -stats pid,command,cpu,mem,threads,state
```
- Spawn as a subprocess; read stdout line-by-line; split into frames on the
  `Processes:` header. **Discard frame 1** (bogus CPU).
- Parse fixed `-stats` columns positionally. Accept truncated command names.
- Feeds the same `make_table()`; everything else unchanged.

Downsides accepted: brittle to `top` format changes across macOS versions,
truncated names, locale sensitivity. Hence B is preferred.

## 12. Open questions for the user

- Color split inside each core bar (user vs sys, like htop's blue/red), or a single
  solid color by total load? Plan assumes single solid color.
- Show a TIME+ column (cumulative CPU time)? Skipped by default to keep rows narrow.
