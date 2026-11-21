"""The ant in the terminal (S12, design/hormiguero/04-terminal.html + terminal-frames.json).

`with ant("Filtro", "corriendo los checks"): ...` animates the ant in a background thread ONLY while
the wrapped work runs: no minimum duration, no sleep; on exit the thread is stopped, joined and the
two animation lines are cleared at once.

Animation and color turn off (one static line, no ANSI) without a TTY, with NO_COLOR, ATTA_NO_ANIM,
TERM=dumb, or when Windows cannot enable VT processing. ASCII glyphs are used when the stream
encoding cannot encode the Unicode ones or with ATTA_ASCII=1. Palette: dark unless
ATTA_THEME=light (no autodetection). Color depth: truecolor (COLORTERM=truecolor|24bit),
256 (TERM contains 256) or 16.

Sprites, frames, icons and colors reproduce terminal-frames.json verbatim (checked by tests).
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass

WIDTH = 24
INTERVAL_S = 0.120
LABEL_GAP = "  "
INDENT = "  "

SPRITES = {
    "unicode": {"body": "●•●●❦", "legs": ("╱╲╱╲", "╲╱╲╱"), "path": "·"},
    "ascii": {"body": "O-oo%", "legs": ("/|/|", "|/|/"), "path": "."},
}

ICONS = {
    "unicode": {"ok": "✔", "running": "◐", "retry": "↻", "human": "⚑", "rejected": "✖", "skip": "·"},
    "ascii": {"ok": "[ok]", "running": "[..]", "retry": "[~]", "human": "[!]", "rejected": "[x]",
              "skip": "[-]"},
}
ICON_COLORS = {"ok": "hoja", "running": "chicamocha", "retry": "chicamocha", "human": "culona",
               "rejected": "tinta-suave", "skip": "tinta-suave"}

PALETTE = {
    "dark": {
        "culona": {"truecolor": "38;2;229;122;82", "ansi256": 173, "ansi16": 31},
        "hoja": {"truecolor": "38;2;124;194;131", "ansi256": 108, "ansi16": 32},
        "chicamocha": {"truecolor": "38;2;211;155;42", "ansi256": 178, "ansi16": 33},
        "tinta": {"truecolor": "38;2;244;234;219", "ansi256": 255, "ansi16": 39},
        "tinta-suave": {"truecolor": "38;2;187;168;148", "ansi256": 144, "ansi16": 90},
    },
    "light": {
        "culona": {"truecolor": "38;2;160;56;26", "ansi256": 130, "ansi16": 31},
        "hoja": {"truecolor": "38;2;47;107;53", "ansi256": 22, "ansi16": 32},
        "chicamocha": {"truecolor": "38;2;211;155;42", "ansi256": 178, "ansi16": 33},
        "tinta": {"truecolor": "38;2;36;23;15", "ansi256": 234, "ansi16": 39},
        "tinta-suave": {"truecolor": "38;2;107;84;68", "ansi256": 59, "ansi16": 90},
    },
}

RESET = "\x1b[0m"
UP = "\x1b[1A"
ERASE = "\x1b[2K"
# Erase the legs line, go up, erase the body line: the cursor ends at column 0 of the first line.
CLEAR = "\r" + ERASE + UP + ERASE

_GLYPHS = "".join(SPRITES["unicode"]["body"] + "".join(SPRITES["unicode"]["legs"])
                  + SPRITES["unicode"]["path"] + "".join(ICONS["unicode"].values()) + "…")


def _frame(mode: str, p: int) -> tuple[str, str]:
    s = SPRITES[mode]
    n = len(s["body"])
    pad = WIDTH - n - p
    return (" " * p + s["body"] + " " * pad,
            s["path"] * p + s["legs"][p % 2] + " " * (n - 4 + pad))


FRAMES = {mode: tuple(_frame(mode, p) for p in range(WIDTH - len(SPRITES[mode]["body"]) + 1))
          for mode in SPRITES}


@dataclass(frozen=True)
class Settings:
    animate: bool
    color: bool
    ascii: bool
    theme: str
    depth: str


def _flag(env, name: str) -> bool:
    return env.get(name, "") not in ("", "0")


def _can_encode(encoding) -> bool:
    if not encoding:
        return True
    try:
        _GLYPHS.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _isatty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _enable_vt(stream) -> bool:
    """Make sure the terminal understands ANSI sequences (Windows: ENABLE_VIRTUAL_TERMINAL_PROCESSING)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt

        handle = ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno()))
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & 0x0004:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001 - any failure means: no ANSI, static output
        return False


def settings(stream=None, env=None) -> Settings:
    stream = sys.stdout if stream is None else stream
    env = os.environ if env is None else env
    fancy = (_isatty(stream) and env.get("TERM", "") != "dumb" and not env.get("NO_COLOR")
             and not _flag(env, "ATTA_NO_ANIM") and _enable_vt(stream))
    colorterm = env.get("COLORTERM", "").strip().lower()
    if colorterm in ("truecolor", "24bit"):
        depth = "truecolor"
    elif "256" in env.get("TERM", ""):
        depth = "ansi256"
    else:
        depth = "ansi16"
    return Settings(
        animate=bool(fancy),
        color=bool(fancy),
        ascii=_flag(env, "ATTA_ASCII") or not _can_encode(getattr(stream, "encoding", None)),
        theme="light" if env.get("ATTA_THEME", "").strip().lower() == "light" else "dark",
        depth=depth,
    )


def _sgr(spec: dict, depth: str, background: bool = False) -> str:
    if depth == "truecolor":
        code = spec["truecolor"]
        return f"\x1b[{'48' + code[2:] if background else code}m"
    if depth == "ansi256":
        return f"\x1b[{48 if background else 38};5;{spec['ansi256']}m"
    return f"\x1b[{spec['ansi16'] + 10 if background else spec['ansi16']}m"


def paint(text: str, token: str, s: Settings) -> str:
    if not s.color or not text:
        return text
    colors = PALETTE[s.theme]
    if s.theme == "light" and token == "chicamocha":
        # Not legible as text on a light terminal: chicamocha background with tinta text.
        start = _sgr(colors["chicamocha"], s.depth, background=True) + _sgr(colors["tinta"], s.depth)
    else:
        start = _sgr(colors[token], s.depth)
    return f"{start}{text}{RESET}"


def icon(name: str, s: Settings) -> str:
    return paint(ICONS["ascii" if s.ascii else "unicode"][name], ICON_COLORS[name], s)


def _mmss(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


class Ant:
    """Context manager: static line or animated ant while the body runs."""

    def __init__(self, label: str, detail: str | None = None, *, stream=None, env=None):
        self.label, self.detail = label, detail
        self.stream = sys.stdout if stream is None else stream
        self.s = settings(self.stream, env)
        self.mode = "ascii" if self.s.ascii else "unicode"
        self.frames_drawn = 0
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._start = 0.0

    def _lines(self, p: int) -> str:
        a, b = FRAMES[self.mode][p]
        body = SPRITES[self.mode]["body"]
        n = len(body)
        parts = [self.label, self.detail, _mmss(time.monotonic() - self._start)]
        label = " · ".join(x for x in parts if x)
        room = shutil.get_terminal_size((80, 24)).columns - len(INDENT) - WIDTH - len(LABEL_GAP) - 1
        if len(label) > room:
            label = label[:max(room - 1, 0)] + ("…" if room > 0 and not self.s.ascii else "")
        s = self.s
        line1 = (INDENT + a[:p] + paint(body[:-1], "culona", s) + paint(body[-1], "hoja", s)
                 + a[p + n:] + LABEL_GAP + paint(label, "chicamocha", s))
        line2 = INDENT + paint(b[:p], "tinta-suave", s) + paint(b[p:p + 4], "culona", s) + b[p + 4:]
        return line1, line2

    def _draw(self, first: bool = False) -> None:
        line1, line2 = self._lines(self.frames_drawn % len(FRAMES[self.mode]))
        prefix = "" if first else "\r" + UP
        self.stream.write(f"{prefix}{ERASE}{line1}\n{ERASE}{line2}")
        self.stream.flush()
        self.frames_drawn += 1

    def _run(self) -> None:
        while not self._stop.wait(INTERVAL_S):
            self._draw()

    def __enter__(self) -> "Ant":
        self._start = time.monotonic()
        if not self.s.animate:
            if self.detail:
                dots = "..." if self.s.ascii else "…"
                text = f"{self.label}: {self.detail}{dots}"
            else:
                text = self.label
            self.stream.write(f"{icon('running', self.s)} {text}\n")
            self.stream.flush()
            return self
        self._draw(first=True)
        self.thread = threading.Thread(target=self._run, name="atta-ant", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        if self.thread is not None:
            self._stop.set()
            self.thread.join()
            self.stream.write(CLEAR)
            self.stream.flush()
        return False


def ant(label: str, detail: str | None = None, *, stream=None, env=None) -> Ant:
    return Ant(label, detail, stream=stream, env=env)
