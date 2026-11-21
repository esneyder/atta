"""Terminal ant (S12, S11): frames from terminal-frames.json, fallbacks, palette, no added wait."""

import io
import json
import threading
import time
import unittest
from unittest import mock

import support  # noqa: F401
from support import REPO_ROOT

from spine import term

CONTRACT = json.loads((REPO_ROOT / "design" / "hormiguero" / "terminal-frames.json")
                      .read_text(encoding="utf-8"))
UNICODE_GLYPHS = set("".join("".join(f) for f in CONTRACT["unicode"]["frames"])) - {" "}


class FakeTTY(io.StringIO):
    def __init__(self, encoding="utf-8"):
        super().__init__()
        self._enc = encoding

    @property
    def encoding(self):
        return self._enc

    def isatty(self):
        return True


TTY_ENV = {"TERM": "xterm-256color", "COLORTERM": "truecolor"}


def vt_ok():
    return mock.patch.object(term, "_enable_vt", return_value=True)


class ContractTest(unittest.TestCase):
    def test_frames_match_contract(self):
        for mode in ("unicode", "ascii"):
            self.assertEqual([list(f) for f in term.FRAMES[mode]], CONTRACT[mode]["frames"], mode)
        self.assertEqual(term.WIDTH, CONTRACT["width"])
        self.assertEqual(term.INTERVAL_S, CONTRACT["interval_ms"] / 1000)

    def test_ascii_frames_are_pure_ascii(self):
        for frame in term.FRAMES["ascii"]:
            for line in frame:
                self.assertTrue(line.isascii(), line)
        for name in CONTRACT["status_icons"]:
            self.assertTrue(term.ICONS["ascii"][name].isascii())

    def test_icons_and_palette_match_contract(self):
        for name, spec in CONTRACT["status_icons"].items():
            self.assertEqual(term.ICONS["unicode"][name], spec["unicode"])
            self.assertEqual(term.ICONS["ascii"][name], spec["ascii"])
        for theme in ("dark", "light"):
            for token, spec in CONTRACT["ansi"][theme].items():
                self.assertEqual(term.PALETTE[theme][token],
                                 {k: spec[k] for k in ("truecolor", "ansi256", "ansi16")},
                                 (theme, token))


class SettingsTest(unittest.TestCase):
    def test_non_tty_is_static_without_color(self):
        s = term.settings(io.StringIO(), TTY_ENV)
        self.assertEqual((s.animate, s.color), (False, False))

    def test_tty_animates_with_color(self):
        with vt_ok():
            s = term.settings(FakeTTY(), TTY_ENV)
        self.assertEqual((s.animate, s.color, s.ascii, s.theme, s.depth),
                         (True, True, False, "dark", "truecolor"))

    def test_disablers(self):
        for extra in ({"NO_COLOR": "1"}, {"ATTA_NO_ANIM": "1"}, {"TERM": "dumb"}):
            with vt_ok():
                s = term.settings(FakeTTY(), {**TTY_ENV, **extra})
            self.assertEqual((s.animate, s.color), (False, False), extra)

    def test_vt_failure_falls_back_to_static(self):
        with mock.patch.object(term, "_enable_vt", return_value=False):
            s = term.settings(FakeTTY(), TTY_ENV)
        self.assertEqual((s.animate, s.color), (False, False))

    def test_color_depth(self):
        cases = [({"COLORTERM": "truecolor", "TERM": "xterm"}, "truecolor"),
                 ({"COLORTERM": "24bit", "TERM": "xterm"}, "truecolor"),
                 ({"TERM": "xterm-256color"}, "ansi256"),
                 ({"TERM": "xterm"}, "ansi16"),
                 ({}, "ansi16")]
        for env, depth in cases:
            with vt_ok():
                self.assertEqual(term.settings(FakeTTY(), env).depth, depth, env)

    def test_theme_light_only_when_explicit(self):
        with vt_ok():
            self.assertEqual(term.settings(FakeTTY(), {**TTY_ENV, "COLORFGBG": "0;15"}).theme, "dark")
            self.assertEqual(term.settings(FakeTTY(), {**TTY_ENV, "ATTA_THEME": "light"}).theme, "light")

    def test_light_theme_picks_light_colors(self):
        with vt_ok():
            dark = term.settings(FakeTTY(), TTY_ENV)
            light = term.settings(FakeTTY(), {**TTY_ENV, "ATTA_THEME": "light"})
        self.assertEqual(term.paint("x", "culona", dark), "\x1b[38;2;229;122;82mx\x1b[0m")
        self.assertEqual(term.paint("x", "culona", light), "\x1b[38;2;160;56;26mx\x1b[0m")
        self.assertEqual(term.paint("x", "chicamocha", light),
                         "\x1b[48;2;211;155;42m\x1b[38;2;36;23;15mx\x1b[0m")
        with vt_ok():
            s256 = term.settings(FakeTTY(), {"TERM": "xterm-256color", "ATTA_THEME": "light"})
            s16 = term.settings(FakeTTY(), {"TERM": "xterm"})
        self.assertEqual(term.paint("x", "hoja", s256), "\x1b[38;5;22mx\x1b[0m")
        self.assertEqual(term.paint("x", "hoja", s16), "\x1b[32mx\x1b[0m")

    def test_paint_without_color_is_plain(self):
        self.assertEqual(term.paint("x", "culona", term.settings(io.StringIO(), {})), "x")

    def test_ascii_fallback(self):
        with vt_ok():
            self.assertTrue(term.settings(FakeTTY(), {**TTY_ENV, "ATTA_ASCII": "1"}).ascii)
            self.assertTrue(term.settings(FakeTTY("cp1252"), TTY_ENV).ascii)
            self.assertTrue(term.settings(FakeTTY("ascii"), TTY_ENV).ascii)
            self.assertFalse(term.settings(FakeTTY("utf-8"), TTY_ENV).ascii)


class AntTest(unittest.TestCase):
    def test_non_tty_prints_one_static_line_without_ansi_or_frames(self):
        out = io.StringIO()
        with term.ant("Filtro", "corriendo los checks", stream=out, env=TTY_ENV):
            pass
        text = out.getvalue()
        self.assertEqual(text, "◐ Filtro: corriendo los checks…\n")
        self.assertNotIn("\x1b", text)
        self.assertFalse(UNICODE_GLYPHS & (set(text) - {"·"}))

    def test_static_ascii_and_label_only(self):
        out = FakeTTY("ascii")
        with term.ant("El Filtro revisa", stream=out, env={"ATTA_NO_ANIM": "1"}):
            pass
        self.assertEqual(out.getvalue(), "[..] El Filtro revisa\n")

    def test_animation_draws_frames_and_clears_the_line(self):
        out = FakeTTY()
        with vt_ok():
            with term.ant("Filtro", "tests", stream=out, env=TTY_ENV) as a:
                time.sleep(term.INTERVAL_S * 2.5)
        text = out.getvalue()
        self.assertIn("\x1b[38;2;229;122;82m", text)
        self.assertIn("Filtro · tests · 00:00", text)
        self.assertIn("●•●", text)
        self.assertTrue(text.endswith(term.CLEAR), repr(text[-30:]))
        self.assertGreaterEqual(a.frames_drawn, 2)
        self.assertFalse(a.thread.is_alive())

    def test_ascii_animation_uses_ascii_frames(self):
        out = FakeTTY()
        with vt_ok():
            with term.ant("Filtro", stream=out, env={**TTY_ENV, "ATTA_ASCII": "1"}):
                pass
        self.assertIn("O-oo", out.getvalue())
        self.assertNotIn("●", out.getvalue())

    def test_spinner_adds_no_wait(self):
        for stream, env in ((FakeTTY(), TTY_ENV), (io.StringIO(), {})):
            with vt_ok():
                start = time.perf_counter()
                with term.ant("Filtro", "no-op", stream=stream, env=env):
                    pass
                elapsed = time.perf_counter() - start
            self.assertLess(elapsed, 0.05, stream)

    def test_thread_stops_when_body_raises(self):
        out = FakeTTY()
        with vt_ok():
            with self.assertRaises(ValueError):
                with term.ant("Filtro", stream=out, env=TTY_ENV) as a:
                    raise ValueError("x")
        self.assertFalse(a.thread.is_alive())
        self.assertTrue(out.getvalue().endswith(term.CLEAR))
        self.assertEqual([t for t in threading.enumerate() if t.name == "atta-ant"], [])

    def test_icon(self):
        self.assertEqual(term.icon("ok", term.settings(io.StringIO(), {})), "✔")
        self.assertEqual(term.icon("ok", term.settings(io.StringIO(), {"ATTA_ASCII": "1"})), "[ok]")


if __name__ == "__main__":
    unittest.main()
