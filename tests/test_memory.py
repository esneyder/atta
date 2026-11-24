"""Memoria cultivada (F3 T4, S6, K1 + G9): `PREVENTION:` → lecciones en atta/memory/lessons.md."""

import argparse
import contextlib
import io
import unittest
from datetime import datetime, timezone
from pathlib import Path

from support import TempDirMixin, snapshot

from atta.spine import memory
from atta.spine.store import JobStore

NOW = datetime(2025, 3, 4, 10, 0, tzinfo=timezone.utc)
LATER = datetime(2025, 3, 9, 10, 0, tzinfo=timezone.utc)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def review(verdict: str, findings=(), preventions=()) -> str:
    lines = [f"VERDICT: {verdict}", "", "## Summary", "", "Resumen.", ""]
    if findings:
        lines += ["## Findings", ""] + [f"- [{sev}] {where} — problema — arreglo"
                                        for sev, where in findings] + [""]
    lines += [f"PREVENTION: {p}" for p in preventions]
    return "\n".join(lines) + "\n"


def lesson_input(rule, job="1-a", paths=("src/a.py",), reviewer="code"):
    return {"rule": rule, "job": job, "paths": list(paths), "reviewer": reviewer}


class Base(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self.jobs_dir = self.root / "atta" / "jobs"
        self.lessons = memory.Lessons(self.root)
        self.path = self.root / "atta" / "memory" / "lessons.md"

    def job(self, jid: str) -> Path:
        d = self.jobs_dir / jid
        write(d / "job.json", '{"id": "%s"}\n' % jid)
        return d


class ParseSerializeTest(Base):
    def test_round_trip_is_stable(self):
        text = memory.serialize([
            memory.Lesson(slug="validar-entrada", rule="Validar la entrada en el servidor",
                          variants=["validar input del lado del servidor"],
                          cases=[("3-b", ["src/api/users.py", "src/api/orders.py"]),
                                 ("1-a", [])],
                          times=2, updated="2025-03-04", mechanizable="ast-grep"),
            memory.Lesson(slug="otra", rule="Probar con un ítem malo", cases=[("2-c", ["x.py"])],
                          times=1, updated="2025-03-01"),
        ])
        lessons = memory.parse(text)
        self.assertEqual(memory.serialize(lessons), text)
        self.assertEqual(lessons[0].cases, [("3-b", ["src/api/users.py", "src/api/orders.py"]),
                                            ("1-a", [])])
        self.assertIsNone(lessons[1].mechanizable)
        self.assertIn("## validar-entrada\nRegla: Validar la entrada en el servidor\n", text)
        self.assertIn("Veces: 2\nActualizada: 2025-03-04\nMecanizable: ast-grep\n", text)

    def test_corrupt_file_is_a_spanish_error(self):
        bad = ["## x\nRegla: r\nVeces: dos\nActualizada: 2025-01-01\n",
               "## x\nVeces: 1\nActualizada: 2025-01-01\n",
               "## x\nRegla: r\nVeces: 1\nActualizada: ayer\n",
               "## x\nRegla: r\nVeces: 1\nActualizada: 2025-01-01\nMecanizable: quizás\n",
               "## x\nRegla: r\nVeces: 1\nActualizada: 2025-01-01\nbasura\n",
               "## x\nRegla: r\nVeces: 1\nActualizada: 2025-01-01\n"
               "## x\nRegla: s\nVeces: 1\nActualizada: 2025-01-01\n"]
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(memory.LessonsError) as ctx:
                    memory.parse(text)
                self.assertIn("lessons.md está dañado", str(ctx.exception))


class UpsertTest(Base):
    def test_similar_rules_merge_despite_accents_case_and_stopwords(self):
        a = self.lessons.upsert(lesson_input("Validar la entrada del usuario en el servidor",
                                             job="1-a", paths=["src/api/users.py"]), now=NOW)
        b = self.lessons.upsert(lesson_input("VALIDÁR entrada de usuario en servidor",
                                             job="2-b", paths=["src/api/orders.py"]), now=LATER)
        self.assertEqual(a, "created")
        self.assertEqual(b, "updated")
        [lesson] = self.lessons.load()
        self.assertEqual(lesson.rule, "Validar la entrada del usuario en el servidor")
        self.assertEqual(lesson.variants, [])  # ≥ 0.9 similar: no new phrasing
        self.assertEqual(lesson.times, 2)
        self.assertEqual(lesson.updated, "2025-03-09")
        self.assertEqual(lesson.cases, [("2-b", ["src/api/orders.py"]),
                                        ("1-a", ["src/api/users.py"])])

    def test_boundary_jaccard_0_6_merges_and_keeps_new_phrasing(self):
        self.lessons.upsert(lesson_input("alpha beta gamma delta", job="1-a"), now=NOW)
        self.lessons.upsert(lesson_input("alpha beta gamma epsilon", job="2-b"), now=NOW)
        [lesson] = self.lessons.load()
        self.assertEqual(lesson.rule, "alpha beta gamma delta")
        self.assertEqual(lesson.variants, ["alpha beta gamma epsilon"])
        self.assertEqual(lesson.times, 2)

    def test_below_threshold_creates_a_distinct_lesson(self):
        self.lessons.upsert(lesson_input("alpha beta gamma delta", job="1-a"), now=NOW)
        out = self.lessons.upsert(lesson_input("alpha beta gamma epsilon zeta", job="2-b"),
                                  now=NOW)  # 3/6 = 0.5
        self.assertEqual(out, "created")
        self.lessons.upsert(lesson_input("Leer la clave del entorno y fallar si falta"), now=NOW)
        self.assertEqual(len(self.lessons.load()), 3)

    def test_distinct_rules_get_distinct_slugs(self):
        self.lessons.upsert(lesson_input("Probar el filtro con facturas anuladas"), now=NOW)
        self.lessons.upsert(lesson_input("Probar el filtro con facturas anuladas y vencidas "
                                         "de clientes de otros paises sin moneda"), now=NOW)
        slugs = [l.slug for l in self.lessons.load()]
        self.assertEqual(len(set(slugs)), 2)
        self.assertEqual(slugs[0], "probar-filtro-facturas-anuladas")

    def test_cases_capped_at_five_newest_first(self):
        for i in range(1, 8):
            self.lessons.upsert(lesson_input("validar entrada servidor", job=f"{i}-j",
                                             paths=[f"src/m{i}.py"]), now=NOW)
        [lesson] = self.lessons.load()
        self.assertEqual([c[0] for c in lesson.cases], ["7-j", "6-j", "5-j", "4-j", "3-j"])
        self.assertEqual(lesson.times, 7)

    def test_same_job_twice_merges_paths_and_counts_once(self):
        self.lessons.upsert(lesson_input("validar entrada servidor", job="1-a",
                                         paths=["a.py"]), now=NOW)
        self.lessons.upsert(lesson_input("validar entrada servidor", job="1-a",
                                         paths=["b.py", "a.py"]), now=NOW)
        [lesson] = self.lessons.load()
        self.assertEqual(lesson.times, 1)
        self.assertEqual(lesson.cases, [("1-a", ["b.py", "a.py"])])

    def test_ast_grep_rule_is_marked_mechanizable(self):
        self.lessons.upsert(lesson_input("regla ast-grep: prohibir eval en src"), now=NOW)
        self.assertEqual(self.lessons.load()[0].mechanizable, "ast-grep")

    def test_corrupt_lessons_md_is_left_untouched(self):
        write(self.path, "## x\nRegla: r\nVeces: muchas\n")
        before = snapshot(self.path)
        with self.assertRaises(memory.LessonsError) as ctx:
            self.lessons.upsert(lesson_input("validar entrada"), now=NOW)
        self.assertIn("lessons.md está dañado", str(ctx.exception))
        self.assertEqual(snapshot(self.path), before)


class CollectHarvestTest(Base):
    def make_job(self):
        d = self.job("7-cifrar-tokens")
        # Round 1: code CHANGES with an upheld finding; security CHANGES fully refuted;
        # resilience PASS with a stray PREVENTION (ignored).
        write(d / "round-1" / "review-code.md", review(
            "CHANGES", [("major", "src/auth/tokens.py:42")],
            ['checklist item "leer secretos del entorno y fallar si faltan"']))
        write(d / "round-1" / "review-security.md", review(
            "CHANGES", [("blocker", "src/auth/keys.py:7")],
            ["no guardar la clave en texto plano"]))
        write(d / "round-1" / "review-resilience.md", review(
            "PASS", [], ["algo que no cuenta"]))
        write(d / "round-1" / "refute.md",
              "REFUTE: PARTIAL\n\n"
              "- UPHELD code src/auth/tokens.py:42 — real.\n"
              "- REFUTED security src/auth/keys.py:7 — ya estaba.\n\n## Summary\n\nx\n")
        # Round 2: a second upheld finding with two paths, no refute file.
        write(d / "round-2" / "review-code.md", review(
            "CHANGES", [("major", "`src/auth/session.py:3`"), ("minor", "src/auth/util.py:9")],
            ["probar el vencimiento de la sesión con reloj falso"]))
        return d

    def test_pass_with_minor_findings_is_harvested(self):
        # Sandbox #11: a PASS with minors carried a useful PREVENTION that was lost.
        d = self.job("11-historial")
        write(d / "round-1" / "review-code.md", review(
            "PASS", [("minor", "history.py:41")],
            ["cada temporal tiene un test por tipo de fallo"]))
        self.assertEqual(memory.collect(d), [
            {"rule": "cada temporal tiene un test por tipo de fallo", "job": "11-historial",
             "reviewer": "code", "paths": ["history.py"]}])

    def test_collect_skips_refuted_and_pass(self):
        d = self.make_job()
        got = memory.collect(d)
        self.assertEqual(got, [
            {"rule": "leer secretos del entorno y fallar si faltan", "job": "7-cifrar-tokens",
             "reviewer": "code", "paths": ["src/auth/tokens.py"]},
            {"rule": "probar el vencimiento de la sesión con reloj falso",
             "job": "7-cifrar-tokens", "reviewer": "code",
             "paths": ["src/auth/session.py", "src/auth/util.py"]},
        ])

    def test_harvest_is_idempotent(self):
        self.make_job()
        result = self.lessons.harvest("7-cifrar-tokens", jobs_dir=self.jobs_dir, now=NOW)
        self.assertEqual(result, {"created": 2, "updated": 0, "already": False})
        first = snapshot(self.path)
        self.assertNotIn("texto plano", first.decode("utf-8"))
        self.assertNotIn("algo que no cuenta", first.decode("utf-8"))
        self.assertEqual((self.root / "atta" / "memory" / ".harvested")
                         .read_text(encoding="utf-8"), "7-cifrar-tokens\n")
        again = self.lessons.harvest("7-cifrar-tokens", jobs_dir=self.jobs_dir, now=LATER)
        self.assertEqual(again, {"created": 0, "updated": 0, "already": True})
        self.assertEqual(snapshot(self.path), first)

    def test_harvest_unknown_job_is_an_error(self):
        with self.assertRaises(memory.LessonsError) as ctx:
            self.lessons.harvest("99-nada", jobs_dir=self.jobs_dir, now=NOW)
        self.assertIn("No existe el job 99-nada", str(ctx.exception))

    def test_harvest_with_corrupt_store_touches_nothing(self):
        self.make_job()
        write(self.path, "## x\nVeces: 1\n")
        before = snapshot(self.path)
        with self.assertRaises(memory.LessonsError):
            self.lessons.harvest("7-cifrar-tokens", jobs_dir=self.jobs_dir, now=NOW)
        self.assertEqual(snapshot(self.path), before)
        self.assertIsNone(snapshot(self.root / "atta" / "memory" / ".harvested"))


class RelevantTest(Base):
    def seed(self):
        u = self.lessons.upsert
        u(lesson_input("alfa uno", job="1-a", paths=["src/orders/export.py"]), now=NOW)
        for j in ("1-a", "2-b", "3-c"):
            u(lesson_input("beta dos", job=j, paths=["docs/x.md"]), now=NOW)
        for j in ("1-a", "2-b"):
            u(lesson_input("gamma tres", job=j, paths=["lib/y.py"]), now=NOW)
        u(lesson_input("delta cuatro", job="4-d", paths=["src/orders/import.py"]), now=NOW)
        u(lesson_input("delta cuatro", job="5-e", paths=["z.py"]), now=NOW)

    def test_matching_directory_first_then_top_by_veces(self):
        self.seed()
        got = [l.rule for l in self.lessons.relevant(["src/orders/new.py"], limit=3)]
        self.assertEqual(got, ["delta cuatro", "alfa uno", "beta dos"])

    def test_glob_paths_match(self):
        self.seed()
        got = [l.rule for l in self.lessons.relevant(["lib/**"], limit=1)]
        self.assertEqual(got, ["gamma tres"])

    def test_budget_caps_total_size(self):
        for i in range(10):
            self.lessons.upsert(lesson_input(f"regla{i} " + " ".join(f"w{i}x{k}"
                                                                    for k in range(60)),
                                             job=f"{i + 1}-j"), now=NOW)
        lessons = self.lessons.relevant(["src/a.py"], limit=10)
        text = memory.render(lessons)
        self.assertLessEqual(len(text), memory.BUDGET_CHARS)
        self.assertGreaterEqual(len(lessons), 1)
        self.assertLess(len(lessons), 10)

    def test_no_store_means_no_lessons(self):
        self.assertEqual(self.lessons.relevant(["a.py"]), [])
        self.assertEqual(memory.render([]), "")


class CliTest(Base):
    def run_cli(self, *argv):
        parser = argparse.ArgumentParser(prog="atta")
        memory.register(parser.add_subparsers(dest="command"))
        args = parser.parse_args(list(argv))
        args.root = self.root
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = args.func(args, None, JobStore(self.jobs_dir))
        return code, out.getvalue(), err.getvalue()

    def test_list_empty(self):
        code, out, err = self.run_cli("memory")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Todavía no hay lecciones", out)

    def test_harvest_then_list(self):
        CollectHarvestTest.make_job(self)
        code, out, err = self.run_cli("memory", "harvest", "7-cifrar-tokens")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "7-cifrar-tokens: 2 lecciones nuevas, 0 actualizadas.\n")
        code, out, _ = self.run_cli("memory", "harvest", "7-cifrar-tokens")
        self.assertEqual((code, out), (0, "7-cifrar-tokens: ya estaba cosechado; "
                                          "no se agregó nada.\n"))
        code, out, err = self.run_cli("memory")
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(lines[0].split(), ["LECCIÓN", "VECES", "MECANIZABLE", "REGLA"])
        self.assertTrue(lines[1].startswith("leer-secretos-entorno-fallar-faltan "))
        self.assertIn(" 1 ", lines[1])
        self.assertEqual(len(lines), 3)

    def test_corrupt_store_exits_2_and_is_untouched(self):
        write(self.path, "## x\nRegla: r\n")
        before = snapshot(self.path)
        code, out, err = self.run_cli("memory")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("lessons.md está dañado", err)
        self.job("1-a")
        code, out, err = self.run_cli("memory", "harvest", "1-a")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(snapshot(self.path), before)

    def test_invalid_job_id_exits_2(self):
        code, out, err = self.run_cli("memory", "harvest", "../x")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("Id de job inválido", err)


if __name__ == "__main__":
    unittest.main()


class RatchetTest(Base):
    """K1 (F3.x T3, S5): an issue `atta:ratchet` per mechanizable lesson seen in 2+ jobs, once."""

    def seed(self, *specs):
        lessons = [memory.Lesson(slug=slug, rule=f"Regla {slug}", cases=[("1-a", ["a.py"])],
                                 times=times, updated="2025-03-01", mechanizable=mech,
                                 issue=issue)
                   for slug, times, mech, issue in specs]
        write(self.path, memory.serialize(lessons))

    def issues(self):
        return {l.slug: l.issue for l in self.lessons.load()}

    def test_opens_one_issue_per_mechanizable_lesson_seen_twice_and_keeps_its_number(self):
        self.seed(("a", 2, "ast-grep", None), ("b", 3, "test", None), ("c", 1, "ast-grep", None),
                  ("d", 5, "no", None), ("e", 2, None, None), ("f", 2, "ast-grep", 9))
        seen = []
        opened = self.lessons.ratchet(lambda lesson: seen.append(lesson.slug) or 40 + len(seen))
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual(opened, [("a", 41), ("b", 42)])
        self.assertEqual(self.issues(), {"a": 41, "b": 42, "c": None, "d": None, "e": None,
                                         "f": 9})
        self.assertIn("Mecanizable: ast-grep\nIssue: #41\n", self.path.read_text(encoding="utf-8"))

    def test_second_run_never_opens_a_duplicate(self):
        self.seed(("a", 2, "ast-grep", None))
        self.lessons.ratchet(lambda lesson: 41)
        before = snapshot(self.path)
        seen = []
        self.assertEqual(self.lessons.ratchet(lambda lesson: seen.append(lesson.slug) or 50), [])
        self.assertEqual(seen, [])
        self.assertEqual(snapshot(self.path), before)

    def test_a_failure_keeps_the_numbers_already_opened(self):
        self.seed(("a", 2, "ast-grep", None), ("b", 2, "test", None))

        def open_issue(lesson):
            if lesson.slug == "b":
                raise RuntimeError("gh caído")
            return 41

        with self.assertRaises(RuntimeError):
            self.lessons.ratchet(open_issue)
        self.assertEqual(self.issues(), {"a": 41, "b": None})

    def test_without_lessons_md_nothing_is_written(self):
        self.assertEqual(self.lessons.ratchet(lambda lesson: 1), [])
        self.assertFalse(self.path.exists())

    def test_issue_field_round_trips_and_rejects_bad_numbers(self):
        text = "## x\nRegla: r\nCasos:\n- 1-a: a.py\nVeces: 2\nActualizada: 2025-01-01\n" \
               "Mecanizable: test\nIssue: #12\n"
        [lesson] = memory.parse(text)
        self.assertEqual(lesson.issue, 12)
        self.assertEqual(memory.serialize([lesson]), memory.HEADER + "\n" + text)
        for bad in ("Issue: 12", "Issue: #0", "Issue: #x", "Issue:"):
            with self.subTest(bad=bad):
                with self.assertRaises(memory.LessonsError) as ctx:
                    memory.parse("## x\nRegla: r\nVeces: 1\nActualizada: 2025-01-01\n"
                                 + bad + "\n")
                self.assertIn("lessons.md está dañado", str(ctx.exception))
