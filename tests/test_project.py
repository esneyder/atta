"""Installable tool (S1, S10, S11): package layout, project resolution, entry points."""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from support import REPO_ROOT, TempDirMixin, snapshot

import atta
from atta.spine import board, cli, config, gate, project

ENV_DROP = ("NO_COLOR", "ATTA_NO_ANIM", "ATTA_ASCII", "ATTA_THEME", "COLORTERM",
            "PYTHONIOENCODING", "PYTHONUTF8", project.ENV_VAR)
HAS_GIT = shutil.which("git") is not None


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ENV_DROP}
    env.update(extra)
    return env


def same(a, b) -> bool:
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)


def git_worktree(repo: Path, name: str) -> Path:
    """A first commit in `repo` and a linked worktree at `repo/.worktrees/<name>`."""
    ident = ["-c", "user.name=Atta Test", "-c", "user.email=atta@example.invalid",
             "-c", "commit.gpgsign=false"]
    for args in ([*ident, "commit", "-q", "--allow-empty", "-m", "inicio"],
                 ["worktree", "add", "-q", "-b", f"atta/{name}", f".worktrees/{name}"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)
    return repo / ".worktrees" / name


class PackageTest(unittest.TestCase):
    def test_version(self):
        self.assertEqual(atta.__version__, "0.2.0")

    def test_atta_is_a_regular_package(self):
        self.assertTrue(same(atta.__file__, REPO_ROOT / "atta" / "__init__.py"))

    def test_assets_come_from_the_package(self):
        pkg = Path(atta.__file__).resolve().parent
        self.assertTrue(same(project.PACKAGE_DIR, pkg))
        self.assertTrue(same(project.BOARD_DIR, pkg / "board"))
        self.assertTrue(same(board.STATIC_DIR, project.BOARD_DIR))
        self.assertIn("index.html", board.static_files())
        self.assertTrue((project.TEMPLATES_DIR / "README.md").is_file())
        self.assertTrue(project.CHECKS_TEMPLATE.is_file())
        self.assertEqual(json.loads(project.CHECKS_TEMPLATE.read_text(encoding="utf-8"))["checks"], [])

    def test_pyproject_declares_the_console_scripts(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertRegex(text, r'(?m)^atta = "atta\.spine\.cli:main"$')
        self.assertRegex(text, r'(?m)^atta-board = "atta\.spine\.board:main"$')
        self.assertRegex(text, r'(?m)^requires-python = ">=3\.10"$')
        self.assertRegex(text, r'(?m)^dependencies = \[\]$')
        self.assertIn('attr = "atta.__version__"', text)


class FindRootTest(TempDirMixin, unittest.TestCase):
    def test_non_git_directory_is_cwd(self):
        with mock.patch.object(project, "git_toplevel", return_value=None):
            self.assertTrue(same(project.find_root(cwd=self.tmp, environ={}), self.tmp))

    @unittest.skipUnless(HAS_GIT, "git no disponible")
    def test_non_git_directory_with_real_git_is_cwd(self):
        self.assertIsNone(project.git_toplevel(self.tmp))
        self.assertTrue(same(project.find_root(cwd=self.tmp, environ={}), self.tmp))

    @unittest.skipUnless(HAS_GIT, "git no disponible")
    def test_git_subdirectory_resolves_to_toplevel(self):
        repo = self.tmp / "repo"
        sub = repo / "src" / "pkg"
        sub.mkdir(parents=True)
        git_init(repo)
        self.assertTrue(same(project.find_root(cwd=sub, environ={}), repo))

    @unittest.skipUnless(HAS_GIT, "git no disponible")
    def test_linked_worktree_resolves_to_the_main_checkout(self):
        # L14 (2): inside .worktrees/<id> the project is the main checkout, not the worktree.
        repo = self.tmp / "repo"
        repo.mkdir()
        git_init(repo)
        wt = git_worktree(repo, "7-hola")
        sub = wt / "src"
        sub.mkdir()
        self.assertTrue(same(project.find_root(cwd=wt, environ={}), repo))
        self.assertTrue(same(project.find_root(cwd=sub, environ={}), repo))
        self.assertTrue(same(project.find_root(cwd=repo, environ={}), repo))
        # --project and ATTA_PROJECT still win as given.
        self.assertTrue(same(project.find_root(str(wt), cwd=repo, environ={}), wt))
        self.assertTrue(same(project.find_root(cwd=wt, environ={project.ENV_VAR: str(wt)}), wt))

    def test_missing_git_falls_back_to_cwd(self):
        with mock.patch.object(project.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertIsNone(project.git_toplevel(self.tmp))
            self.assertTrue(same(project.find_root(cwd=self.tmp, environ={}), self.tmp))

    def test_env_var_overrides_git(self):
        other = self.tmp / "otro"
        other.mkdir()
        with mock.patch.object(project, "git_toplevel", side_effect=AssertionError("no git")):
            root = project.find_root(cwd=self.tmp, environ={project.ENV_VAR: str(other)})
        self.assertTrue(same(root, other))

    def test_explicit_overrides_env_var(self):
        a, b = self.tmp / "a", self.tmp / "b"
        a.mkdir(), b.mkdir()
        root = project.find_root(str(a), cwd=self.tmp, environ={project.ENV_VAR: str(b)})
        self.assertTrue(same(root, a))

    def test_relative_explicit_is_relative_to_cwd(self):
        (self.tmp / "rel").mkdir()
        self.assertTrue(same(project.find_root("rel", cwd=self.tmp, environ={}), self.tmp / "rel"))

    def test_blank_env_var_is_ignored(self):
        with mock.patch.object(project, "git_toplevel", return_value=None):
            root = project.find_root(cwd=self.tmp, environ={project.ENV_VAR: "  "})
        self.assertTrue(same(root, self.tmp))

    def test_missing_project_dir_is_an_error(self):
        with self.assertRaises(project.ProjectError) as ctx:
            project.find_root(str(self.tmp / "nada"), cwd=self.tmp, environ={})
        self.assertEqual(str(ctx.exception),
                         f"La carpeta del proyecto no existe: {self.tmp / 'nada'}.")


class ProjectPathsTest(TempDirMixin, unittest.TestCase):
    def test_paths_live_under_the_project(self):
        self.assertEqual(project.config_path(self.tmp), self.tmp / "atta" / "config.json")
        self.assertEqual(project.checks_path(self.tmp), self.tmp / gate.CHECKS_RELPATH)
        self.assertEqual(project.jobs_dir(self.tmp), self.tmp / "atta" / "jobs")

    def test_missing_config_uses_defaults(self):
        self.assertEqual(project.load_config(self.tmp), config.with_defaults({}))
        self.assertIsNone(snapshot(self.tmp / "atta" / "config.json"))

    def test_project_config_is_loaded(self):
        (self.tmp / "atta").mkdir()
        (self.tmp / "atta" / "config.json").write_text(
            json.dumps({"integration_branch": "trunk", "max_rounds": 5}), encoding="utf-8")
        cfg = project.load_config(self.tmp)
        self.assertEqual(cfg["integration_branch"], "trunk")
        self.assertEqual(cfg["max_rounds"], 5)
        self.assertEqual(cfg["forbidden_branches"], ["main", "master"])

    def test_invalid_project_config_is_refused(self):
        (self.tmp / "atta").mkdir()
        (self.tmp / "atta" / "config.json").write_text("{", encoding="utf-8")
        with self.assertRaises(config.ConfigError):
            project.load_config(self.tmp)


class CliProjectTest(TempDirMixin, unittest.TestCase):
    """`atta` resolves the project from cwd / --project / ATTA_PROJECT (subprocess, real entry)."""

    BIN = REPO_ROOT / "atta" / "bin" / "atta.py"

    def run_atta(self, *args, cwd=None, env=None):
        proc = subprocess.run([sys.executable, str(self.BIN), *args], capture_output=True,
                              cwd=str(cwd or self.tmp), env=env or clean_env(),
                              stdin=subprocess.DEVNULL, timeout=60)
        return (proc.returncode, proc.stdout.decode("utf-8").replace("\r\n", "\n"),
                proc.stderr.decode("utf-8").replace("\r\n", "\n"))

    def test_status_without_project_files(self):
        self.assertEqual(self.run_atta("status"), (0, "No hay jobs.\n", ""))
        self.assertFalse((self.tmp / "atta").exists())

    @unittest.skipUnless(HAS_GIT, "git no disponible")
    def test_create_from_git_subdir_writes_under_toplevel(self):
        repo = self.tmp / "repo"
        sub = repo / "a" / "b"
        sub.mkdir(parents=True)
        git_init(repo)
        code, out, err = self.run_atta("create", "7", "--title", "Hola mundo", cwd=sub)
        self.assertEqual((code, out, err), (0, "7-hola-mundo\n", ""))
        self.assertTrue((repo / "atta" / "jobs" / "7-hola-mundo" / "job.json").is_file())
        self.assertFalse((sub / "atta").exists())

    @unittest.skipUnless(HAS_GIT, "git no disponible")
    def test_status_inside_a_job_worktree_sees_the_jobs(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        git_init(repo)
        wt = git_worktree(repo, "7-hola-mundo")
        self.assertEqual(self.run_atta("create", "7", "--title", "Hola mundo", cwd=repo),
                         (0, "7-hola-mundo\n", ""))
        code, out, err = self.run_atta("status", cwd=wt)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("7-hola-mundo", out)
        self.assertFalse((wt / "atta").exists())
        # An explicit --project pointing at the worktree is used as given.
        self.assertEqual(self.run_atta("--project", str(wt), "status", cwd=repo),
                         (0, "No hay jobs.\n", ""))

    def test_project_option_and_env_var(self):
        proj = self.tmp / "proj"
        proj.mkdir()
        code, out, err = self.run_atta("--project", str(proj), "create", "1", "--title", "Uno")
        self.assertEqual((code, out, err), (0, "1-uno\n", ""))
        self.assertTrue((proj / "atta" / "jobs" / "1-uno" / "job.json").is_file())
        env = clean_env(**{project.ENV_VAR: str(proj)})
        code, out, err = self.run_atta("status", env=env)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("1-uno", out)

    def test_jobs_dir_still_overrides(self):
        proj, jobs = self.tmp / "proj", self.tmp / "otros"
        proj.mkdir()
        code, out, err = self.run_atta("--project", str(proj), "--jobs-dir", str(jobs),
                                       "create", "2", "--title", "Dos")
        self.assertEqual((code, out, err), (0, "2-dos\n", ""))
        self.assertTrue((jobs / "2-dos" / "job.json").is_file())
        self.assertFalse((proj / "atta").exists())

    def test_config_comes_from_the_project(self):
        proj = self.tmp / "proj"
        (proj / "atta").mkdir(parents=True)
        (proj / "atta" / "config.json").write_text('{"integration_branch": "trunk"}',
                                                    encoding="utf-8")
        self.assertEqual(self.run_atta("--project", str(proj), "check-pr-base", "trunk"),
                         (0, "La Reina acepta la base trunk.\n", ""))
        code, _, _ = self.run_atta("--project", str(proj), "check-pr-base", "develop")
        self.assertEqual(code, 2)

    def test_invalid_project_config_exit_2(self):
        proj = self.tmp / "proj"
        (proj / "atta").mkdir(parents=True)
        (proj / "atta" / "config.json").write_text("{", encoding="utf-8")
        code, out, err = self.run_atta("--project", str(proj), "status")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("config.json no es JSON válido:"), err)

    def test_missing_project_dir_exit_2(self):
        missing = self.tmp / "nada"
        self.assertEqual(self.run_atta("--project", str(missing), "status"),
                         (2, "", f"La carpeta del proyecto no existe: {missing}.\n"))
        self.assertFalse(missing.exists())

    def test_help_mentions_project(self):
        code, out, err = self.run_atta("--help")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("--project", out)
        self.assertIn(project.ENV_VAR, out)


class GateUsesProjectRootTest(TempDirMixin, unittest.TestCase):
    def test_gate_main_checkout_is_the_project(self):
        proj = self.tmp / "proj"
        proj.mkdir()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["--project", str(proj), "create", "3", "--title", "Tres"]), 0)
        result = gate.GateResult(True, "abc", None, 0.1, "ok", Path("r.md"), Path("r.log"))
        with mock.patch.object(gate, "run_gate", return_value=result) as fake, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.dict(os.environ, {"ATTA_ASCII": ""}, clear=False):
            code = cli.main(["--project", str(proj), "gate", "3-tres", "--worktree",
                             str(self.tmp), "--base", "develop"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertTrue(same(fake.call_args[0][0], proj))
        self.assertEqual(Path(fake.call_args[0][3]), proj.resolve() / "atta" / "jobs" / "3-tres" / "round-1")


class EntryPointsTest(TempDirMixin, unittest.TestCase):
    def test_cli_main_help_exits_0(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--help"]), 0)
        self.assertIn("--project", out.getvalue())

    def test_board_main_help_exits_0(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(board.main(["--help"]), 0)
        self.assertIn("--project", out.getvalue())

    def test_board_resolves_project_paths(self):
        proj = self.tmp / "proj"
        proj.mkdir()
        args = board.build_parser().parse_args(["--project", str(proj)])
        jobs_dir, config_file = board.resolve_paths(args)
        self.assertEqual(jobs_dir, proj.resolve() / "atta" / "jobs")
        self.assertEqual(config_file, proj.resolve() / "atta" / "config.json")
        args = board.build_parser().parse_args(["--project", str(proj), "--jobs-dir", "x"])
        self.assertEqual(board.resolve_paths(args)[0], Path("x"))

    def test_board_payload_reads_project_config(self):
        cfg = self.tmp / "config.json"
        cfg.write_text('{"integration_branch": "trunk", "max_rounds": 4}', encoding="utf-8")
        payload = board.jobs_payload(self.tmp / "jobs", board.utcnow(), config_file=cfg)
        self.assertEqual((payload["integration_branch"], payload["max_rounds"]), ("trunk", 4))
        payload = board.jobs_payload(self.tmp / "jobs", board.utcnow(),
                                     config_file=self.tmp / "nada.json")
        self.assertEqual((payload["integration_branch"], payload["max_rounds"]), ("develop", 3))

    def test_python_m_cli_help(self):
        proc = subprocess.run([sys.executable, "-m", "atta.spine.cli", "--help"],
                              capture_output=True, cwd=str(REPO_ROOT), env=clean_env(),
                              stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Atta", proc.stdout.decode("utf-8"))

    def test_board_help_piped_is_utf8(self):
        """S11: accents survive a pipe on every OS (Windows used to print «m�quina»)."""
        for argv in ([str(REPO_ROOT / "atta" / "bin" / "board.py")], ["-m", "atta.spine.board"]):
            with self.subTest(argv=argv):
                proc = subprocess.run([sys.executable, *argv, "--help"], capture_output=True,
                                      cwd=str(REPO_ROOT), env=clean_env(),
                                      stdin=subprocess.DEVNULL, timeout=60)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("máquina", proc.stdout.decode("utf-8"))

    def test_cli_help_piped_is_utf8(self):
        proc = subprocess.run([sys.executable, "-m", "atta.spine.cli", "--help"],
                              capture_output=True, cwd=str(REPO_ROOT), env=clean_env(),
                              stdin=subprocess.DEVNULL, timeout=60)
        self.assertIn("Códigos de salida", proc.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
