"""`atta doctor` (F3 S3, G1): one line per check, a fix hint per failure, exit 1 only on ✗."""

import contextlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from support import TempDirMixin

from atta.spine import cli, doctor, gate, initcmd, project

HAS_GIT = shutil.which("git") is not None
FAKEGH = Path(__file__).resolve().parent / "fakegh" / "fakegh.py"
LABELS = ["atta", "atta:needs-info", "atta:needs-human", "atta:needs-review", "atta:ratchet"]
CHECKS = {"checks": [{"name": "tests", "run": "python -m unittest"}], "timeout_s": 900}
PIP = "24.0"
CLAUDE = "2.1.0 (Claude Code)"


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                           "-c", "commit.gpgsign=false", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


def tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.relative_to(root).parts or not p.is_file():
            continue
        out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


@unittest.skipUnless(HAS_GIT, "git no disponible")
class DoctorBase(TempDirMixin, unittest.TestCase):
    """A healthy project: atta init, one check, develop pushed to a bare origin, fake gh."""

    def setUp(self):
        super().setUp()
        self.root = (self.tmp / "proj").resolve()
        self.root.mkdir()
        self.origin = self.tmp / "origin.git"
        self.bin = self.tmp / "bin"
        git("init", "-q", "--bare", str(self.origin), cwd=self.tmp)
        git("init", "-q", cwd=self.root)
        git("checkout", "-q", "-b", "develop", cwd=self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(initcmd.run(self.root, no_github=True,
                                         templates_dir=project.TEMPLATES_DIR), 0)
        self.write_checks(CHECKS)
        self.set_matrix(True)  # a project that already took a side on the F4 routing matrix
        git("add", "-A", cwd=self.root)
        git("commit", "-q", "-m", "atta init", cwd=self.root)
        git("remote", "add", "origin", str(self.origin), cwd=self.root)
        git("push", "-q", "origin", "HEAD:develop", cwd=self.root)
        self.state = self.tmp / "fakegh.json"
        self.set_gh({"user": "operador", "repo": {"name": "proj"}, "repo_labels": list(LABELS)})
        self.which = {"atta": str(self.bin / "atta"), "atta-board": str(self.bin / "atta-board")}
        # What the Filtro's preflight finds on the PATH for the first command of a check (T6).
        self.commands = {"python": str(self.bin / "python")}
        self.claude = CLAUDE

    def set_matrix(self, value):
        """Put `routing_matrix` in atta/config.json (None: take the key out)."""
        path = self.root / "atta" / "config.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("routing_matrix", None)
        if value is not None:
            data["routing_matrix"] = value
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def write_checks(self, data):
        (self.root / "atta" / "checks.json").write_text(json.dumps(data), encoding="utf-8")

    def set_gh(self, state):
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def gh_calls(self):
        return [c["argv"] for c in
                json.loads(self.state.read_text(encoding="utf-8")).get("calls", [])]

    def run_doctor(self, *, no_github=False, via_cli=False, stdout=None, env=None):
        out = stdout if stdout is not None else io.StringIO()
        environ = {"ATTA_GH": str(FAKEGH), "FAKEGH_STATE": str(self.state), **(env or {})}
        with mock.patch.dict(os.environ, environ), \
                mock.patch.object(doctor, "_which", side_effect=self.which.get), \
                mock.patch.object(doctor, "_pip_version", return_value=PIP), \
                mock.patch.object(doctor, "_claude_version", side_effect=lambda: self.claude), \
                mock.patch.object(gate, "_command_which",
                                  side_effect=lambda name, path=None: self.commands.get(name)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            os.environ.pop("ATTA_ASCII", None)
            if via_cli:
                argv = ["--project", str(self.root), "doctor"] + (
                    ["--no-github"] if no_github else [])
                code = cli.main(argv)
            else:
                code = doctor.run(self.root, no_github=no_github)
        if stdout is not None:
            return code, None
        return code, out.getvalue()

    def healthy_lines(self):
        py = f"{platform.python_version()} con pip {PIP} ({sys.executable})"
        return [
            f"Atta doctor: {self.root}",
            f"✓ atta está en el PATH ({self.bin / 'atta'})",
            f"✓ atta-board está en el PATH ({self.bin / 'atta-board'})",
            f"✓ Python {py}",
            f"✓ proyecto: {self.root} (repo git)",
            "✓ atta/config.json es válido",
            "✓ atta/checks.json tiene 1 check",
            f"✓ remoto origin: {self.origin}",
            "✓ origin/develop existe: ahí integra Atta",
            "✓ el checkout principal (develop) está al día con origin/develop",
            "✓ gh instalado (gh version 2.0.0 (fakegh))",
            "✓ gh tiene una sesión iniciada",
            "✓ la cuenta operador de gh ve el repo proj",
            "✓ labels de Atta en el repo: " + ", ".join(LABELS),
            "✓ .claude/agents: los 12 agentes de Atta",
            "✓ .claude/skills/atta/SKILL.md existe",
            "✓ /atta registrado (.claude/commands/atta.md)",
            "✓ hook del guard instalado (.claude/settings.json)",
            f"✓ Claude Code {CLAUDE}",
            "✓ archivos de Claude Code al día con esta versión de Atta",
        ]


class HealthyTest(DoctorBase):
    def test_healthy_project_is_all_ok_and_exits_0(self):
        before = tree(self.root)
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines(), self.healthy_lines() + [
            "Resumen: 19 bien, 0 avisos, 0 fallas.",
            "Todo listo para correr /atta.",
            "— Atta",
        ])
        self.assertEqual(tree(self.root), before)
        self.assertIn(["repo", "view", "--json", "name"], self.gh_calls())
        self.assertIn(["auth", "status"], self.gh_calls())

    def test_cli_wiring_runs_doctor(self):
        code, out = self.run_doctor(via_cli=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[:len(self.healthy_lines())], self.healthy_lines())

    def test_ascii_fallback_when_stdout_cannot_encode(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii", errors="replace", newline="\n")
        code, _ = self.run_doctor(stdout=stream)
        stream.flush()
        lines = raw.getvalue().decode("ascii").splitlines()
        self.assertEqual(code, 0)
        self.assertTrue(lines[1].startswith("[ok] atta est"), lines[1])
        self.assertEqual(lines[-1], "-- Atta")
        self.assertFalse(any(line.startswith("?") for line in lines), lines)

    def test_no_github_skips_gh_with_a_warning(self):
        code, out = self.run_doctor(no_github=True)
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertIn("⚠ GitHub (gh y labels): salteado (--no-github)", lines)
        for gone in ("✓ gh instalado", "✓ gh tiene", "✓ la cuenta", "✓ labels"):
            self.assertFalse(any(line.startswith(gone) for line in lines), gone)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(lines[-3:], ["Resumen: 15 bien, 1 aviso, 0 fallas.",
                                      "Listo para correr /atta; revisa los avisos.", "— Atta"])


class FailureTest(DoctorBase):
    def assert_line(self, out, line, hint=None):
        lines = out.splitlines()
        self.assertIn(line, lines, out)
        if hint is not None:
            self.assertEqual(lines[lines.index(line) + 1], f"    → {hint}", out)

    # T9: on another branch the Filtro reads another checks.json (L7: `main` of a fresh clone).
    def test_main_checkout_on_another_branch_fails_with_the_filtro_hint(self):
        git("checkout", "-q", "-b", "main", cwd=self.root)
        before = tree(self.root)
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ el checkout principal está en «main», no en develop",
                         "Atta lee checks.json y las reglas desde develop. Corre: git switch "
                         "develop")
        self.assertFalse(any("está al día con origin/develop" in line
                             for line in out.splitlines()), out)
        self.assertIn("Resumen: 18 bien, 0 avisos, 1 falla.", out.splitlines())
        self.assertEqual(tree(self.root), before)

    def test_detached_main_checkout_fails(self):
        git("checkout", "-q", "--detach", cwd=self.root)
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ el checkout principal está en «HEAD suelto», no en develop",
                         "Atta lee checks.json y las reglas desde develop. Corre: git switch "
                         "develop")

    def test_missing_label(self):
        self.set_gh({"user": "operador", "repo": {"name": "proj"}, "repo_labels": LABELS[:3]})
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ faltan labels de Atta en el repo: atta:needs-review, atta:ratchet",
                         "Corre atta init en tu terminal: crea las labels que faltan.")
        self.assertIn("Corrige lo marcado con ✗ antes de correr /atta.", out.splitlines())

    def test_repo_not_visible_for_the_active_account(self):
        self.set_gh({"user": "otra", "repo_labels": LABELS, "fail": {
            "repo view": "GraphQL: Could not resolve to a Repository with the name 'o/proj'."}})
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ la cuenta activa de gh (otra) no ve el repo",
                         "Cambia a una cuenta con acceso: gh auth switch -u <usuario> "
                         "(gh auth status lista tus cuentas).")
        self.assert_line(out, "⚠ labels de Atta: salteado (gh no ve el repo)")

    def test_gh_not_logged_in(self):
        self.set_gh({"fail": {"auth status": "You are not logged into any GitHub hosts."}})
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ gh no tiene una sesión iniciada", "Corre gh auth login.")
        self.assert_line(out, "⚠ repo en gh: salteado (gh no está listo)")
        self.assert_line(out, "⚠ labels de Atta: salteado (gh no está listo)")

    def test_gh_missing(self):
        missing = self.tmp / "no-existe-gh"
        code, out = self.run_doctor(env={"ATTA_GH": str(missing)})
        self.assertEqual(code, 1, out)
        self.assert_line(out, f"✗ no encontré gh ({missing})",
                         "Instala GitHub CLI (https://cli.github.com) o define ATTA_GH.")

    def test_empty_checks_json(self):
        self.write_checks({"checks": [], "timeout_s": 900})
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ atta/checks.json no tiene checks: el Filtro siempre falla",
                         "Agrega los checks reales del proyecto (README, sección 4.2).")

    # F3 upgrade: a routing copied before the Rastreador existed never gets it.
    def test_own_routing_without_rastreador_is_a_warning(self):
        import copy, json
        from atta.spine import config
        routing = copy.deepcopy(config.DEFAULT_ROUTING)
        routing["risk"]["high"]["reviewers"] = [
            r for r in routing["risk"]["high"]["reviewers"] if r["reviewer"] != "rastreador"]
        (self.root / "atta" / "config.json").write_text(
            json.dumps({"routing": routing}), encoding="utf-8")
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assert_line(out, "⚠ tu routing propio no incluye al Rastreador en riesgo alto",
                         "Si nunca editaste «routing», bórralo de atta/config.json para usar "
                         "el de fábrica; si es tuyo, agrega el reviewer rastreador (T3) a «high».")

    # F4 T3 (S9, F3 L18): the default routing changed; existing projects are told.
    MATRIX_WARNING = (
        "⚠ el routing por defecto cambió: los modelos ahora dependen del riesgo del job (bajo: "
        "la Guía y la Jardinera UX bajan un tier; alto: la Cortadora, la Reina y el revisor de "
        "seguridad suben a T3; la Cortadora sube a T3 desde la ronda 2)")
    MATRIX_HINT = ("Mira el efecto con `atta models`. Para aceptarlo, agrega "
                   "\"routing_matrix\": true a atta/config.json; para volver al routing de "
                   "antes, agrega \"routing_matrix\": false (solo una persona edita ese "
                   "archivo).")

    def test_config_without_routing_matrix_warns_and_writes_nothing(self):
        self.set_matrix(None)
        before = tree(self.root)
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assert_line(out, self.MATRIX_WARNING, self.MATRIX_HINT)
        self.assertIn("Resumen: 19 bien, 1 aviso, 0 fallas.", out)
        self.assertEqual(tree(self.root), before)

    def test_routing_matrix_true_or_false_is_silent(self):
        for value in (True, False):
            with self.subTest(value=value):
                self.set_matrix(value)
                code, out = self.run_doctor()
                self.assertEqual(code, 0, out)
                self.assertNotIn("el routing por defecto cambió", out)

    def test_invalid_routing_matrix_is_a_config_failure(self):
        self.set_matrix("si")
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assertIn("✗ atta/config.json no es válido: config.json: routing_matrix debe ser "
                      "true o false.", out)

    def test_missing_command_file(self):
        (self.root / ".claude" / "commands" / "atta.md").unlink()
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ falta .claude/commands/atta.md: /atta no aparece en Claude Code",
                         "Corre atta init en tu terminal.")
        self.assert_line(out, "⚠ archivos de Atta desactualizados: .claude/commands/atta.md",
                         "Corre atta init --force en tu terminal "
                         "(atta init --check muestra la lista).")

    def test_missing_hook(self):
        (self.root / ".claude" / "settings.json").write_text('{"hooks": {}}\n', encoding="utf-8")
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ .claude/settings.json no tiene el hook «atta guard»: el guard "
                              "no bloquea nada",
                         "Corre atta init en tu terminal (agrega el hook sin tocar lo tuyo).")
        self.assert_line(out, "⚠ archivos de Atta desactualizados: .claude/settings.json")

    def test_missing_agent(self):
        (self.root / ".claude" / "agents" / "atta-reina.md").unlink()
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ faltan agentes en .claude/agents: atta-reina.md",
                         "Corre atta init en tu terminal.")

    def test_origin_develop_missing(self):
        git("push", "-q", "origin", "HEAD:main", cwd=self.root)
        git("push", "-q", "origin", "--delete", "develop", cwd=self.root)
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ no encontré la rama develop en origin",
                         "Atta integra solo en develop (nunca en main): créala en origin, "
                         "p. ej. git push origin HEAD:develop.")
        self.assert_line(out, "⚠ checkout principal: salteado (falta origin/develop)")

    def test_no_origin_remote(self):
        git("remote", "remove", "origin", cwd=self.root)
        code, out = self.run_doctor(no_github=True)
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ no hay remoto origin",
                         "Agrega el remoto de GitHub: git remote add origin <url>.")
        self.assert_line(out, "⚠ origin/develop: salteado (no hay remoto origin)")

    def test_behind_origin_is_a_warning(self):
        other = self.tmp / "other"
        git("clone", "-q", "-b", "develop", str(self.origin), str(other), cwd=self.tmp)
        (other / "nuevo.txt").write_text("x\n", encoding="utf-8")
        git("add", "-A", cwd=other)
        git("commit", "-q", "-m", "otro", cwd=other)
        git("push", "-q", "origin", "HEAD:develop", cwd=other)
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assert_line(out, "⚠ el checkout principal (develop) está atrás de origin/develop",
                         "Ponlo al día: git pull --ff-only origin develop (el Filtro lee "
                         "atta/checks.json de este checkout).")

    def test_atta_not_on_path(self):
        self.which.pop("atta")
        code, out = self.run_doctor(no_github=True)
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ atta no está en el PATH",
                         "Agrega al PATH la carpeta Scripts del Python donde instalaste Atta "
                         "(p. ej. …\\Python311\\Scripts) o corre pipx ensurepath, y abre una "
                         "terminal nueva. Sin atta en el PATH el hook del guard no bloquea nada.")

    def test_claude_missing_is_a_warning(self):
        self.claude = None
        code, out = self.run_doctor(no_github=True)
        self.assertEqual(code, 0, out)
        self.assert_line(out, "⚠ no encontré claude en el PATH",
                         "Instala Claude Code para correr /atta.")

    def test_invalid_config_is_reported_by_the_cli(self):
        (self.root / "atta" / "config.json").write_text('{"concurrency": 0}', encoding="utf-8")
        code, out = self.run_doctor(no_github=True, via_cli=True)
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ atta/config.json no es válido: config.json: concurrency debe "
                              "ser un entero mayor que 0.",
                         "Corrígelo a mano en tu terminal (atta settings muestra cada clave).")

    def test_unknown_profile_says_how_to_get_out(self):  # S1 (F2.y A1)
        path = self.root / "atta" / "config.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**data, "profile": "barato"}), encoding="utf-8")
        before = tree(self.root)
        code, out = self.run_doctor(no_github=True, via_cli=True)
        self.assertEqual(code, 1, out)
        self.assert_line(out, "✗ atta/config.json no es válido: config.json: profile «barato» "
                              "no existe: usa economico, equilibrado o calidad.",
                         "Quítalo con atta profiles clear en tu terminal o borra «profile» de "
                         "atta/config.json.")
        self.assertEqual(tree(self.root), before)

    def test_sgconfig_without_ast_grep_is_a_warning(self):
        (self.root / "sgconfig.yml").write_text("ruleDirs:\n  - rules\n", encoding="utf-8")
        code, out = self.run_doctor(no_github=True)
        self.assertEqual(code, 0, out)
        self.assert_line(out, "⚠ hay sgconfig.yml y no encontré ast-grep (ni sg) en el PATH: el "
                              "Filtro va a fallar",
                         "Instala ast-grep (npm i -g @ast-grep/cli, pip install ast-grep-cli o "
                         "cargo install ast-grep) o quita sgconfig.yml del checkout principal.")

    def test_sgconfig_with_ast_grep_is_ok(self):
        (self.root / "sgconfig.yml").write_text("ruleDirs:\n  - rules\n", encoding="utf-8")
        self.which["ast-grep"] = str(self.bin / "ast-grep")
        code, out = self.run_doctor(no_github=True)
        self.assertEqual(code, 0, out)
        exe = self.bin / "ast-grep"
        self.assert_line(out, f"✓ ast-grep para las reglas de sgconfig.yml ({exe})")

    # F4.x T6 (S8): the Filtro now stops as a config failure when a check's command is missing.
    def test_check_command_missing_from_path_is_a_warning(self):
        self.write_checks({"checks": [{"name": "tests", "run": "pytest -q"}],
                           "timeout_s": 900})
        before = tree(self.root)
        code, out = self.run_doctor(no_github=True)
        self.assertEqual(code, 0, out)
        self.assert_line(out, "⚠ No se encontró el comando «pytest» en el PATH de este equipo "
                              "(check «tests»): el Filtro no va a correr (needs-human)",
                         "Instálalo o corrige atta/checks.json en el checkout principal.")
        self.assertIn("Resumen: 15 bien, 2 avisos, 0 fallas.", out.splitlines())
        self.assertEqual(tree(self.root), before)

    def test_check_command_on_path_adds_no_line(self):
        code, out = self.run_doctor(no_github=True)
        self.assertEqual(code, 0, out)
        self.assertFalse(any("No se encontró" in line for line in out.splitlines()), out)
        self.assertIn("Resumen: 15 bien, 1 aviso, 0 fallas.", out.splitlines())

    def test_outdated_agent_is_a_warning(self):
        agent = self.root / ".claude" / "agents" / "atta-guia.md"
        agent.write_text(agent.read_text(encoding="utf-8") + "\nmío\n", encoding="utf-8")
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assert_line(out, "⚠ archivos de Atta desactualizados: .claude/agents/atta-guia.md")


class NotARepoTest(TempDirMixin, unittest.TestCase):
    def test_folder_without_git_fails(self):
        root = (self.tmp / "suelto").resolve()
        root.mkdir()
        out = io.StringIO()
        with mock.patch.object(doctor, "_which", return_value="/bin/x"), \
                mock.patch.object(doctor, "_pip_version", return_value=PIP), \
                mock.patch.object(doctor, "_claude_version", return_value=CLAUDE), \
                contextlib.redirect_stdout(out):
            code = doctor.run(root, no_github=True)
        self.assertEqual(code, 1)
        lines = out.getvalue().splitlines()
        self.assertIn(f"✗ {root} no es un repo git", lines)
        self.assertIn("    → Corre atta doctor dentro del proyecto o pásale --project <carpeta>.",
                      lines)


if __name__ == "__main__":
    unittest.main()
