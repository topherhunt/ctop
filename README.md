# ctop

An htop-style process monitor for macOS that reports **accurate per-process CPU%**.

```
 ctop                                                                  16:29:37  up 6d 7h
   0 [||||||||||||||||||||||||||             66%]     4 [|||||||||||||||||||         48%]
   1 [|||||||||||||||||||||||||              65%]     5 [||||||||||||||||||          46%]
   2 [|||||||||||||||||||||||                60%]     6 [|||||||||||||||||           45%]
   3 [|||||||||||||||||||||||                58%]     7 [|||||||||||||||||||||       53%]
 Mem [||||||||||||||||||||||||||||    3.2G/8.0G]   Swp [|||||||||||||||||   5.8G/8.0G]
 Load 5.89 5.15 5.56    Tasks 523    Threads 3616

     PID  COMMAND                                            CPU%▼      MEM    TH
   54034  node                                                98.3     304M    12
     382  WindowServer                                        31.0     596M    18
   98499  Google Chrome Helper (Renderer)                     17.0    67.5M    27
   ...
 c sort CPU   m sort MEM   q quit   (or click the CPU%/MEM headers)
```

## Why

I love htop's colorful TUI, but I was today years old when I discovered it's
been lying to me about per-process CPU usage on macOS this whole time. This is
a known, longstanding problem -- a process pinning a full core can show up as
a few percent:

- [htop-dev/htop#368](https://github.com/htop-dev/htop/issues/368) -- CPU usage incorrect on macOS with M1 (`top` says 100%, htop says 2.4%)
- [htop-dev/htop#751](https://github.com/htop-dev/htop/issues/751) -- total CPU time broken on M1 Macs
- [htop-dev/htop#765](https://github.com/htop-dev/htop/issues/765) -- CPU time calculations wrong under Rosetta 2
- [htop-dev/htop#752](https://github.com/htop-dev/htop/pull/752) -- the fix attempt ("the CPU percentage calculation was wrong, but since it's a ratio, two errors cancelled each other out")
- [htop-dev/htop#1619](https://github.com/htop-dev/htop/issues/1619) -- process CPU time still not updated correctly on macOS

Apple's built-in `top` is accurate but monochrome, has no per-core meters, and
truncates command names to ~16 characters (`Google Chrome He`). ctop gives you
both: htop's look (per-core bars, memory/swap bars, colored process table) with
`top`-accurate numbers and full command names.

The numbers come from [psutil](https://github.com/giampaolo/psutil), which
reads the same Mach kernel APIs Apple's `top` uses (`host_processor_info` for
the core bars, `proc_pidtaskinfo` for per-process CPU). Verified against `top`
on a pinned core: both read ~98-100%.

## Install

```sh
git clone https://github.com/topherhunt/ctop.git
cd ctop
./install
```

The install script creates a local Python venv, installs the two dependencies
(`psutil`, `rich`), smoke-tests by rendering one live frame, and symlinks
`ctop` into `~/.local/bin` (override with `CTOP_BIN_DIR=/some/dir ./install`).
Nothing is touched outside the repo directory except that one symlink.

Requirements: macOS 13+, Python 3.11+ (Homebrew's is fine; no Xcode needed,
psutil installs as a prebuilt wheel).

## Usage

```sh
ctop
```

| Key | Action |
|-----|--------|
| `c` | Sort by CPU% (default) |
| `m` | Sort by memory (RSS) |
| `q` | Quit |

You can also click the `CPU%` / `MEM` column headers to switch the sort. The
`▼` caret marks the active sort column. Updates once per second.

CPU% is Irix-mode like Apple's `top`: one fully-busy core = 100%, so
multithreaded processes can exceed 100%. Bars and CPU% cells are colored green
below 50%, yellow from 50-80%, red above 80%.

## Uninstall

```sh
rm ~/.local/bin/ctop
rm -rf path/to/ctop   # the cloned repo (the venv lives inside it)
```

## Development

`python verify.py` (inside the venv) checks the success criterion headless: it
pins a core with `yes`, asserts the process reads ~100% at the top of the
table, and validates the rendered frame layout.
