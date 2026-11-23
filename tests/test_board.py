"""El hormiguero: read-only HTTP board over the job store (T6, S11, S12, S1)."""

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from urllib.parse import quote

import support  # noqa: F401
from support import REPO_ROOT, TempDirMixin

from atta.spine import board

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "jobs"
BOARD_DIR = REPO_ROOT / "atta" / "board"
NOW = datetime(2025, 6, 6, 12, 0, 0, tzinfo=timezone.utc)

MERGED = "31-exportar-pedidos-a-csv"
REJECTED = "34-mostrar-correos-de-clientes"
GATE_FAILED = "35-validar-fechas-en-reservas"
ESCALATED = "36-cifrar-tokens-de-sesion"
IN_REVIEW = "37-filtrar-facturas-por-cliente"

# Strings that must never appear in an error body: config.json, job.json, events.jsonl.
LEAK_MARKERS = ("integration_branch", '"stage"', "duration_s", "protected_paths", "DECISION:")


class ServerMixin:
    jobs_dir = FIXTURES

    @classmethod
    def start(cls, jobs_dir):
        cls.server = board.make_server(jobs_dir, host="127.0.0.1", port=0,
                                       now=lambda: NOW, quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def stop(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)

    def request(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def get_json(self, path):
        status, headers, body = self.request(path)
        self.assertEqual(status, 200, body)
        self.assertTrue(headers["content-type"].startswith("application/json"))
        self.assertEqual(headers["cache-control"], "no-store")
        return json.loads(body.decode("utf-8"))

    def assert_refused(self, path, statuses=(400, 404)):
        status, headers, body = self.request(path)
        self.assertIn(status, statuses, f"{path} -> {status}")
        self.assertEqual(headers.get("cache-control"), "no-store", path)
        text = body.decode("utf-8", "replace")
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, text, f"{path} leaked {marker!r}")
        return status


class BoardApiTests(ServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.start(FIXTURES)

    @classmethod
    def tearDownClass(cls):
        cls.stop()

    def jobs(self):
        data = self.get_json("/api/jobs")
        return data, {j["id"]: j for j in data["jobs"]}

    # --- /api/jobs ---

    def test_lists_every_fixture_job_without_warnings(self):
        data, by_id = self.jobs()
        self.assertEqual([j["id"] for j in data["jobs"]],
                         [MERGED, REJECTED, GATE_FAILED, ESCALATED, IN_REVIEW])
        self.assertEqual(data["warnings"], [])
        self.assertEqual(data["integration_branch"], "develop")
        self.assertEqual(data["max_rounds"], 3)
        self.assertEqual(data["generated"], "2025-06-06T12:00:00Z")

    def test_merged_job(self):
        j = self.jobs()[1][MERGED]
        self.assertEqual((j["stage"], j["column"], j["state"]), ("merged", "closed", "ok"))
        self.assertEqual((j["issue"], j["pr"], j["risk"]), (31, 40, "low"))
        self.assertEqual(j["intake_decision"], "ACCEPT")
        self.assertEqual(j["decision"], "APPROVE")
        self.assertEqual(j["rounds"], [{
            "n": 1, "build": "DONE", "gate": {"verdict": "PASS", "commit": "3f9a2c1"},
            "reviews": [{"lens": "code", "verdict": "PASS"}]}])
        self.assertEqual(j["latest"], j["rounds"][0])
        self.assertEqual(j["durations"], {"intake": 41, "spec": 180, "build": 540, "gate": 112,
                                          "review": 240, "approve": 6, "merging": 12})
        self.assertEqual(j["elapsed_s"], 1131)
        self.assertEqual(j["cost_usd"], 0.284)
        self.assertEqual(j["tier"], "T1")
        self.assertIsNone(j["human_reason"])

    def test_rejected_job(self):
        j = self.jobs()[1][REJECTED]
        self.assertEqual((j["stage"], j["column"], j["state"]), ("rejected", "closed", "off"))
        self.assertEqual(j["intake_decision"], "REJECT")
        self.assertEqual(j["risk"], "high")
        self.assertEqual(j["rounds"], [])
        self.assertIsNone(j["latest"])
        self.assertEqual(j["durations"], {"intake": 38})
        self.assertEqual(j["elapsed_s"], 38)

    def test_gate_failed_job_returns_to_the_cortadora(self):
        j = self.jobs()[1][GATE_FAILED]
        self.assertEqual((j["stage"], j["column"], j["state"]), ("build", "build", "retry"))
        self.assertEqual(j["latest"], {"n": 1, "build": "DONE",
                                       "gate": {"verdict": "FAIL", "commit": "c07e5a9"},
                                       "reviews": []})
        self.assertEqual(j["counters"]["gate_rounds"], 1)
        self.assertEqual(j["durations"], {"intake": 52, "spec": 300, "spec-review": 90,
                                          "build": 720, "gate": 105})
        # Open job: elapsed runs until the injected now.
        self.assertEqual(j["elapsed_s"], int((NOW - datetime(2025, 6, 4, 10, tzinfo=timezone.utc))
                                             .total_seconds()))

    def test_escalated_job_needs_a_human(self):
        j = self.jobs()[1][ESCALATED]
        self.assertEqual((j["stage"], j["column"], j["state"]), ("needs-human", "human", "human"))
        self.assertEqual(j["decision"], "ESCALATE")
        self.assertEqual(j["human_reason"], "Riesgo alto: cambia cómo se cifran las sesiones y "
                                            "la Reina no lo aprueba sola. Te lo dejamos a ti.")
        self.assertEqual([r["gate"]["verdict"] for r in j["rounds"]], ["PASS", "FAIL", "PASS"])
        self.assertEqual(j["rounds"][0]["reviews"], [
            {"lens": "code", "verdict": "PASS"}, {"lens": "resilience", "verdict": "PASS"},
            {"lens": "security", "verdict": "CHANGES"}])
        self.assertEqual(j["rounds"][1]["reviews"], [])
        self.assertEqual(j["latest"]["n"], 3)
        self.assertEqual(j["durations"]["build"], 2220)
        self.assertEqual(j["durations"]["gate"], 360)
        self.assertEqual(j["durations"]["review"], 960)
        self.assertEqual(j["counters"], {"review_rounds": 1, "gate_rounds": 1, "sync_attempts": 0,
                                         "total_rounds": 2, "max_total_rounds": 6})
        self.assertEqual(j["tier"], "T3")

    def test_job_in_review_round_two(self):
        j = self.jobs()[1][IN_REVIEW]
        self.assertEqual((j["stage"], j["column"], j["state"]), ("review", "review", "run"))
        self.assertEqual(j["round"], 2)
        self.assertEqual(j["rounds"][0]["reviews"], [{"lens": "code", "verdict": "CHANGES"}])
        self.assertEqual(j["latest"], {"n": 2, "build": "DONE",
                                       "gate": {"verdict": "PASS", "commit": "8b41d0e"},
                                       "reviews": []})
        self.assertEqual(j["durations"], {"intake": 40, "spec": 180, "build": 780, "gate": 180,
                                          "review": 240})
        self.assertEqual(j["models"][-1], {"stage": "build", "model": "anthropic/claude-sonnet-4.5",
                                           "tier": "T2"})

    def test_global_stats(self):
        stats = self.jobs()[0]["stats"]
        self.assertEqual(stats["active"], 2)
        self.assertEqual(stats["needs_human"], 1)
        self.assertEqual((stats["merged"], stats["closed"], stats["merge_rate"]), (1, 2, 50))
        self.assertEqual(stats["cost_usd"], round(0.284 + 0.002 + 0.465 + 3.956 + 0.474, 6))
        self.assertEqual(stats["p50"]["intake"], 41)
        self.assertEqual(stats["p50"]["approve"], 18)
        self.assertNotIn("needs-human", stats["p50"])

    # --- files ---

    def test_lists_markdown_artifacts_with_first_line_and_summary(self):
        data = self.get_json(f"/api/jobs/{MERGED}/files")
        self.assertEqual(data["job"], MERGED)
        self.assertEqual([f["path"] for f in data["files"]], [
            "issue.md", "intake.md", "spec.md", "round-1/build.md", "round-1/checks.md",
            "round-1/review-code.md", "decision.md"])
        checks = data["files"][4]
        self.assertEqual(checks["first_line"], "GATE: PASS")
        self.assertEqual(checks["summary"], "El Filtro pasó: 3 checks en 18 s.")
        self.assertIsNone(data["files"][0]["summary"])

    def test_reads_a_markdown_artifact(self):
        status, headers, body = self.request(
            f"/api/jobs/{ESCALATED}/file?path=round-1/review-security.md")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        expected = (FIXTURES / ESCALATED / "round-1" / "review-security.md").read_bytes()
        self.assertEqual(body, expected)

    def test_refuses_non_markdown_reads(self):
        for name in ("job.json", "events.jsonl", "round-1/checks.log", "intake.md.json"):
            with self.subTest(name=name):
                self.assertEqual(self.assert_refused(f"/api/jobs/{MERGED}/file?path={name}"), 400)
        self.assertEqual(self.assert_refused(f"/api/jobs/{MERGED}/file"), 400)

    def test_unknown_or_invalid_job_is_404(self):
        for path in ("/api/jobs/99-no-existe/files", "/api/jobs/99-no-existe/file?path=issue.md",
                     "/api/jobs/ABC/files", "/api/jobs/-x/files", "/api/jobs/x/y/files",
                     "/api/nada", "/no-existe.html"):
            with self.subTest(path=path):
                self.assertEqual(self.assert_refused(path), 404)

    def test_traversal_is_refused_without_leaking(self):
        other = FIXTURES / REJECTED / "intake.md"
        paths = [
            "/../config.json", "/..%2fconfig.json", "/%2e%2e/config.json",
            "/%2e%2e%2fatta%2fconfig.json", "/%2E%2E/%2E%2E/atta/config.json",
            "/..\\config.json", "/..%5cconfig.json", "//etc/passwd", "/atta/config.json",
            "/board/../../config.json", "/board.js/../../config.json", "/./index.html",
            "/api/jobs/../x/file", f"/api/jobs/../{MERGED}/files",
            "/api/jobs/..%2f..%2fconfig.json/files", "/api/jobs/%2e%2e/files",
            f"/api/jobs/{MERGED}/file?path=../../job.json",
            f"/api/jobs/{MERGED}/file?path=../{REJECTED}/intake.md",
            f"/api/jobs/{MERGED}/file?path=..%2f{REJECTED}%2fintake.md",
            f"/api/jobs/{MERGED}/file?path=%2e%2e/{REJECTED}/intake.md",
            f"/api/jobs/{MERGED}/file?path=round-1/../../{REJECTED}/intake.md",
            f"/api/jobs/{MERGED}/file?path=..%5c{REJECTED}%5cintake.md",
            f"/api/jobs/{MERGED}/file?path=round-1\\..\\intake.md",
            f"/api/jobs/{MERGED}/file?path=./intake.md",
            f"/api/jobs/{MERGED}/file?path=/etc/passwd.md",
            f"/api/jobs/{MERGED}/file?path=C:/Windows/win.md",
            f"/api/jobs/{MERGED}/file?path=C:%5CWindows%5Cwin.md",
            f"/api/jobs/{MERGED}/file?path={quote(other.as_posix())}",
            f"/api/jobs/{MERGED}/file?path=intake.md%00.md",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assert_refused(path)

    def test_write_methods_are_refused(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                status, headers, _ = self.request("/api/jobs", method=method)
                self.assertEqual(status, 405)
                self.assertEqual(headers["cache-control"], "no-store")
                self.assertEqual(headers["allow"], "GET, HEAD")

    def test_foreign_host_header_is_refused(self):
        status, _, body = self.request("/api/jobs", headers={"Host": "atacante.example"})
        self.assertEqual(status, 400)
        self.assertNotIn(MERGED, body.decode("utf-8"))
        status, _, _ = self.request("/api/jobs", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(status, 200)

    def test_lookalike_hosts_are_refused(self):
        for host in ("[::1]evil.com", "127.0.0.1:80@evil.com", "localhost:evil.com",
                     "localhost\t", "localhost.evil.com", "127.0.0.1.evil.com",
                     "user@localhost", "localhost:", "localhost:80:80", "[::1", "::1",
                     "127.0.0.1 :80", "localhost:8x", "evil.com#localhost", ""):
            with self.subTest(host=host):
                status, _, body = self.request("/api/jobs", headers={"Host": host})
                self.assertEqual(status, 400)
                self.assertNotIn(MERGED, body.decode("utf-8"))

    def test_duplicate_host_headers_are_refused(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("GET", "/api/jobs", skip_host=True)
            conn.putheader("Host", "localhost")
            conn.putheader("Host", "evil.com")
            conn.endheaders()
            resp = conn.getresponse()
            self.assertEqual(resp.status, 400)
            self.assertNotIn(MERGED, resp.read().decode("utf-8"))
        finally:
            conn.close()

    def test_loopback_hosts_are_accepted(self):
        for host in ("localhost", "LocalHost", f"LOCALHOST:{self.port}", "127.0.0.1",
                     f"127.0.0.1:{self.port}", "[::1]", "[::1]:80"):
            with self.subTest(host=host):
                status, _, _ = self.request("/api/jobs", headers={"Host": host})
                self.assertEqual(status, 200)

    def test_request_timeout_default(self):
        self.assertEqual(board.REQUEST_TIMEOUT_S, 10)
        self.assertEqual(self.server.RequestHandlerClass.timeout, 10)

    # --- static files ---

    def test_index_is_served_with_no_store(self):
        for path in ("/", "/index.html"):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
                self.assertEqual(headers["cache-control"], "no-store")
                self.assertIn("default-src 'self'", headers["content-security-policy"])
                html = body.decode("utf-8")
                self.assertIn('id="board"', html)
                self.assertIn('src="board.js"', html)
                self.assertIn('href="tokens.css"', html)

    def test_static_assets(self):
        expected = {"/board.css": "text/css; charset=utf-8",
                    "/board.js": "text/javascript; charset=utf-8",
                    "/tokens.css": "text/css; charset=utf-8",
                    "/atta-marca.svg": "image/svg+xml"}
        for path, ctype in expected.items():
            with self.subTest(path=path):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["content-type"], ctype)
                self.assertEqual(headers["cache-control"], "no-store")

    def test_head_has_headers_and_no_body(self):
        status, headers, body = self.request("/api/jobs", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(body, b"")


class UnreadableJobsTests(ServerMixin, TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        root = self.tmp / "jobs"
        shutil.copytree(FIXTURES / MERGED, root / MERGED)
        (root / "40-roto").mkdir()
        (root / "40-roto" / "job.json").write_text("{no es json", encoding="utf-8")
        (root / "41-lista").mkdir()
        (root / "41-lista" / "job.json").write_text("[1, 2]", encoding="utf-8")
        (root / "Nombre Malo").mkdir()
        (root / "Nombre Malo" / "job.json").write_text("{}", encoding="utf-8")
        (root / "42-sin-job").mkdir()
        (root / "suelto.txt").write_text("x", encoding="utf-8")
        events = root / MERGED / "events.jsonl"
        events.write_text(events.read_text(encoding="utf-8") + "{roto\n", encoding="utf-8")
        self.start(root)

    def tearDown(self):
        self.stop()
        super().tearDown()

    def test_unreadable_dirs_are_skipped_with_warnings(self):
        data = self.get_json("/api/jobs")
        self.assertEqual([j["id"] for j in data["jobs"]], [MERGED])
        self.assertEqual(len(data["warnings"]), 3)
        joined = " ".join(data["warnings"])
        self.assertIn("40-roto", joined)
        self.assertIn("41-lista", joined)
        self.assertNotIn("42-sin-job", joined)
        job = data["jobs"][0]
        self.assertEqual(job["durations"]["merging"], 12)
        self.assertEqual(len(job["warnings"]), 1)

    def test_missing_jobs_dir_is_an_empty_board(self):
        self.stop()
        self.start(self.tmp / "no-existe")
        data = self.get_json("/api/jobs")
        self.assertEqual((data["jobs"], data["warnings"]), ([], []))
        self.assertEqual(data["stats"]["active"], 0)
        self.assertIsNone(data["stats"]["merge_rate"])


def _make_dir_link(link, target, kind):
    """A directory link: an NTFS junction (no privilege needed) or a symlink."""
    if kind == "junction":
        if sys.platform != "win32":
            raise unittest.SkipTest("Las junctions son de Windows.")
        out = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                             capture_output=True, timeout=30)
        if out.returncode != 0:
            raise unittest.SkipTest("No pude crear la junction.")
    else:
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            raise unittest.SkipTest("Sin privilegio para crear symlinks.")


class LinkConfinementTests(ServerMixin, TempDirMixin, unittest.TestCase):
    """B1/m1: no junction, symlink or hardlink lets the board list or read outside a job."""

    SECRET = "SECRETO-FUERA-DEL-JOB"

    def setUp(self):
        super().setUp()
        self.links = []
        self.serving = False
        self.root = self.tmp / "jobs"
        shutil.copytree(FIXTURES / MERGED, self.root / MERGED)
        self.outside = self.tmp / "fuera"
        shutil.copytree(FIXTURES / MERGED, self.outside)
        (self.outside / "intake.md").write_text(f"ACCEPT\n{self.SECRET}\n", encoding="utf-8")
        (self.outside / "round-1" / "review-secreto.md").write_text(
            f"VERDICT: PASS\n## Summary\n{self.SECRET}\n", encoding="utf-8")
        (self.outside / "secreto.md").write_text(f"{self.SECRET}\n", encoding="utf-8")

    def tearDown(self):
        if self.serving:
            self.stop()
        # Remove the links themselves before the temp tree, never through them.
        for link in reversed(self.links):
            try:
                if sys.platform == "win32" and link.is_dir():
                    os.rmdir(link)
                else:
                    os.unlink(link)
            except OSError:
                pass
        super().tearDown()

    def dir_link(self, link, target, kind):
        _make_dir_link(link, target, kind)
        self.links.append(link)

    def serve(self):
        self.start(self.root)
        self.serving = True

    def assert_no_secret(self, path):
        status, _, body = self.request(path)
        self.assertNotIn(self.SECRET, body.decode("utf-8", "replace"), path)
        return status, body

    def check_job_dir_link(self, kind):
        self.dir_link(self.root / "50-enlace", self.outside, kind)
        self.dir_link(self.root / "51-gemelo", self.root / MERGED, kind)
        self.serve()
        data = self.get_json("/api/jobs")
        self.assertEqual([j["id"] for j in data["jobs"]], [MERGED])
        joined = " ".join(data["warnings"])
        self.assertIn("50-enlace", joined)
        self.assertIn("51-gemelo", joined)
        self.assertNotIn(self.SECRET, json.dumps(data))
        for jid in ("50-enlace", "51-gemelo"):
            for path in (f"/api/jobs/{jid}/files", f"/api/jobs/{jid}/file?path=intake.md",
                         f"/api/jobs/{jid}/file?path=secreto.md"):
                with self.subTest(path=path):
                    self.assertEqual(self.assert_no_secret(path)[0], 404)

    def test_job_dir_junction_is_not_a_job(self):
        self.check_job_dir_link("junction")

    def test_job_dir_symlink_is_not_a_job(self):
        self.check_job_dir_link("symlink")

    def check_round_dir_link(self, kind):
        self.dir_link(self.root / MERGED / "round-2", self.outside / "round-1", kind)
        self.serve()
        data = self.get_json("/api/jobs")
        job = data["jobs"][0]
        self.assertEqual([r["n"] for r in job["rounds"]], [1])
        self.assertIn("round-2", " ".join(job["warnings"]))
        self.assertNotIn(self.SECRET, json.dumps(data))
        files = self.get_json(f"/api/jobs/{MERGED}/files")
        self.assertFalse([f for f in files["files"] if f["path"].startswith("round-2/")])
        self.assertIn("round-2", " ".join(files["warnings"]))
        self.assertNotIn(self.SECRET, json.dumps(files))
        status, _ = self.assert_no_secret(
            f"/api/jobs/{MERGED}/file?path=round-2/review-secreto.md")
        self.assertEqual(status, 404)

    def test_round_dir_junction_is_ignored(self):
        self.check_round_dir_link("junction")

    def test_round_dir_symlink_is_ignored(self):
        self.check_round_dir_link("symlink")

    def check_file_ignored(self, name):
        self.serve()
        data = self.get_json("/api/jobs")
        self.assertIn(name, " ".join(data["jobs"][0]["warnings"]))
        self.assertNotIn(self.SECRET, json.dumps(data))
        files = self.get_json(f"/api/jobs/{MERGED}/files")
        self.assertNotIn(name, [f["path"] for f in files["files"]])
        self.assertNotIn(self.SECRET, json.dumps(files))
        status, _ = self.assert_no_secret(f"/api/jobs/{MERGED}/file?path={name}")
        self.assertEqual(status, 404)

    def test_file_symlink_is_ignored(self):
        link = self.root / MERGED / "notas.md"
        try:
            os.symlink(self.outside / "secreto.md", link)
        except (OSError, NotImplementedError):
            self.skipTest("Sin privilegio para crear symlinks.")
        self.links.append(link)
        self.check_file_ignored("notas.md")

    def test_hardlink_is_ignored(self):
        try:
            os.link(self.outside / "secreto.md", self.root / MERGED / "copia.md")
        except (OSError, NotImplementedError):
            self.skipTest("El sistema de archivos no admite enlaces duros.")
        self.check_file_ignored("copia.md")

    def test_hardlinked_job_json_is_not_a_job(self):
        target = self.root / "52-duro"
        target.mkdir()
        try:
            os.link(self.outside / "job.json", target / "job.json")
        except (OSError, NotImplementedError):
            self.skipTest("El sistema de archivos no admite enlaces duros.")
        self.serve()
        data = self.get_json("/api/jobs")
        self.assertEqual([j["id"] for j in data["jobs"]], [MERGED])
        self.assertIn("52-duro", " ".join(data["warnings"]))
        self.assertEqual(self.request("/api/jobs/52-duro/files")[0], 404)


class PrototypeKeyTests(ServerMixin, TempDirMixin, unittest.TestCase):
    """m2: stage names from job data are data, never object-prototype lookups."""

    def setUp(self):
        super().setUp()
        root = self.tmp / "jobs"
        shutil.copytree(FIXTURES / MERGED, root / MERGED)
        events = root / MERGED / "events.jsonl"
        extra = [{"from": "constructor", "to": "__proto__", "duration_s": 5,
                  "model": "x/y", "tier": "T1"},
                 {"from": "__proto__", "to": "toString", "duration_s": 7}]
        events.write_text(events.read_text(encoding="utf-8")
                          + "".join(json.dumps(e) + "\n" for e in extra), encoding="utf-8")
        self.start(root)

    def tearDown(self):
        self.stop()
        super().tearDown()

    def test_prototype_stage_names_are_returned_as_data(self):
        job = self.get_json("/api/jobs")["jobs"][0]
        self.assertEqual(job["durations"]["constructor"], 5)
        self.assertEqual(job["durations"]["__proto__"], 7)
        self.assertIn({"stage": "constructor", "model": "x/y", "tier": "T1"}, job["models"])

    def test_board_js_lookups_are_own_property_only(self):
        js = (BOARD_DIR / "board.js").read_text(encoding="utf-8")
        self.assertIn("Object.hasOwn", js)
        self.assertIn("Object.create(null)", js)
        for table in ("STAGE_ROLE", "RISK", "ICON"):
            with self.subTest(table=table):
                self.assertIsNone(re.search(rf"\b{table}\[", js), f"{table}[...] directo")


class SocketTimeoutTests(unittest.TestCase):
    """m5: a connection that never sends headers does not hold a thread forever."""

    def test_headerless_connection_is_closed(self):
        timeout = 1
        server = board.make_server(FIXTURES, host="127.0.0.1", port=0, now=lambda: NOW,
                                   quiet=True, request_timeout=timeout)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address[:2], timeout=10) as raw:
                raw.settimeout(timeout + 5)
                start = time.monotonic()
                try:
                    data = raw.recv(1024)
                except ConnectionResetError:
                    data = b""
                except socket.timeout:
                    self.fail("El servidor no cerró la conexión sin headers.")
                self.assertEqual(data, b"")
                self.assertLess(time.monotonic() - start, timeout + 5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


class LoopbackTests(unittest.TestCase):
    """m6: all of 127.0.0.0/8 and ::1 are this machine."""

    def test_is_loopback(self):
        for host in ("127.0.0.1", "127.0.0.2", "127.255.255.254", "::1", "[::1]",
                     "localhost", "LOCALHOST"):
            with self.subTest(host=host):
                self.assertTrue(board.is_loopback(host))
        for host in ("0.0.0.0", "::", "192.168.1.2", "128.0.0.1", "10.0.0.1",
                     "localhost.evil.com", "", "127.0.0.1.evil.com"):
            with self.subTest(host=host):
                self.assertFalse(board.is_loopback(host))

    def test_other_loopback_address_checks_host_and_accepts_itself(self):
        try:
            server = board.make_server(FIXTURES, host="127.0.0.2", port=0, now=lambda: NOW,
                                       quiet=True)
        except OSError:
            self.skipTest("Este sistema no escucha en 127.0.0.2.")
        self.assertTrue(server.RequestHandlerClass.check_host)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            for host, expected in ((f"127.0.0.2:{port}", 200), ("localhost", 200),
                                   ("evil.com", 400)):
                with self.subTest(host=host):
                    conn = http.client.HTTPConnection("127.0.0.2", port, timeout=10)
                    try:
                        conn.request("GET", "/api/jobs", headers={"Host": host})
                        resp = conn.getresponse()
                        resp.read()
                        self.assertEqual(resp.status, expected)
                    finally:
                        conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


class AwaitingMergeBoardTests(TempDirMixin, unittest.TestCase):
    """S13: a PR waiting for your merge is shown like needs-human: «te toca mergear»."""

    def build(self, **raw):
        job_dir = self.tmp / "40-x"
        job_dir.mkdir(exist_ok=True)
        base = {"id": "40-x", "issue": 40, "title": "X", "stage": "awaiting-merge", "pr": 41,
                "started": "2025-06-06T10:00:00Z", "stage_since": "2025-06-06T11:00:00Z"}
        return board.build_job("40-x", job_dir, {**base, **raw}, NOW)

    def test_awaiting_merge_is_a_human_card_with_the_pr_link(self):
        j = self.build(pr_url="https://github.com/o/r/pull/41")
        self.assertEqual((j["stage"], j["column"], j["state"]),
                         ("awaiting-merge", "human", "human"))
        self.assertEqual(j["pr_url"], "https://github.com/o/r/pull/41")

    def test_only_https_pr_urls_reach_the_board(self):
        for bad in ("javascript:alert(1)", "http://github.com/o/r/pull/41", 41, None):
            with self.subTest(url=bad):
                self.assertIsNone(self.build(pr_url=bad)["pr_url"])

    def test_stats_count_awaiting_merge_as_needing_you(self):
        waiting = self.build()
        running = dict(waiting, stage="review", id="41-y")
        stats = board.stats([waiting, running])
        self.assertEqual((stats["needs_human"], stats["active"]), (1, 1))

    def test_board_js_labels_awaiting_merge(self):
        js = (BOARD_DIR / "board.js").read_text(encoding="utf-8")
        self.assertIn('case "awaiting-merge": return "te toca mergear";', js)
        self.assertIn('"awaiting-merge"', js)


class BoardAssetsTests(unittest.TestCase):
    def test_tokens_are_a_copy_of_the_brand(self):
        self.assertEqual((BOARD_DIR / "tokens.css").read_bytes(),
                         (REPO_ROOT / "brand" / "tokens.css").read_bytes())

    def test_no_external_urls_and_no_inner_html(self):
        for path in BOARD_DIR.iterdir():
            # The SVG namespace is an identifier, never fetched.
            text = path.read_text(encoding="utf-8").replace("http://www.w3.org/2000/svg", "")
            with self.subTest(path=path.name):
                for bad in ("http://", "https://", "//cdn", "@import", "url("):
                    self.assertNotIn(bad, text)
                self.assertNotIn("innerHTML", text)
                self.assertNotIn("insertAdjacentHTML", text)
                self.assertNotIn("document.write", text)

    def test_cli_help(self):
        out = subprocess.run([sys.executable, str(REPO_ROOT / "atta" / "bin" / "board.py"), "--help"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        for flag in ("--jobs-dir", "--port", "--host"):
            self.assertIn(flag, out.stdout)


class StageTimeoutEventBoardTests(unittest.TestCase):
    def test_stage_timeout_events_add_no_stage_time(self):
        # F4 T5: `stage_timeout` has no from/duration_s, so it never counts as a stage duration.
        events = [{"event": "stage_timeout", "stage": "intake", "agent": "atta-exploradora",
                   "model": "haiku", "limit_s": 600, "elapsed_s": 700, "outcome": "retry"},
                  {"from": "intake", "to": "spec", "duration_s": 5}]
        self.assertEqual(board.durations(events), {"intake": 5})


if __name__ == "__main__":
    unittest.main()
