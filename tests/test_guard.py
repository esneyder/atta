"""`atta guard` (S8, S10): Claude Code PreToolUse hook that blocks writes and commands."""

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401
from support import REPO_ROOT, TempDirMixin

from atta.spine import guard

JID = "7-agregar-csv"
OLD = "3-viejo"
SETTINGS = REPO_ROOT / "atta" / "templates" / "claude" / "settings.json"


def write(path, text="x"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class GuardBase(TempDirMixin, unittest.TestCase):
    """Project under a test temp folder. The system temp dir allowed for writes (L17 b) is
    pinned to `<tmp>/systmp` so the rest of the test temp folder counts as «outside the project»;
    TempDirWriteTests uses the real system temp dir."""

    real_temp = False

    def setUp(self):
        super().setUp()
        self.systmp = Path(os.path.realpath(self.tmp / "systmp"))
        self.systmp.mkdir()
        if not self.real_temp:
            patcher = mock.patch.object(guard, "temp_roots", create=True,
                                        return_value=[self.systmp])
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root = Path(os.path.realpath(self.tmp / "proj"))
        self.root.mkdir()
        self.make_job(JID, "build")
        self.make_job(OLD, "merged")
        self.wt = self.root / ".worktrees" / JID
        self.wt.mkdir(parents=True)
        (self.root / ".worktrees" / OLD).mkdir()

    def make_job(self, jid, stage):
        write(self.root / "atta" / "jobs" / jid / "job.json",
              json.dumps({"id": jid, "stage": stage}))

    def hook(self, tool, **tool_input):
        return {"session_id": "s", "hook_event_name": "PreToolUse", "cwd": str(self.root),
                "tool_name": tool, "tool_input": tool_input, "extra": [1, 2]}

    def write_reason(self, path, tool="Write", cwd=None):
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        h = self.hook(tool, **{key: str(path)})
        if cwd is not None:
            h["cwd"] = str(cwd)
        return guard.evaluate(h, root=self.root)

    def bash_reason(self, command, tool="Bash", cwd=None):
        h = self.hook(tool, command=command)
        if cwd is not None:
            h["cwd"] = str(cwd)
        return guard.evaluate(h, root=self.root)

    def assertAllowed(self, reason):
        self.assertIsNone(reason)

    def assertDenied(self, reason, *fragments):
        self.assertIsInstance(reason, str)
        self.assertTrue(reason.strip())
        for frag in fragments:
            self.assertIn(frag, reason)


class WriteTests(GuardBase):
    def test_worktree_write_allowed_for_every_write_tool(self):
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool=tool):
                self.assertAllowed(self.write_reason(self.wt / "src" / "app.py", tool))

    def test_relative_path_resolves_against_hook_cwd(self):
        self.assertAllowed(self.write_reason("src/app.py", cwd=self.wt))
        self.assertDenied(self.write_reason("README.md", cwd=self.root))

    def test_protected_paths_inside_worktree_denied(self):
        for rel in ("atta/x", "atta/config.json", ".github/workflows/x.yml", ".claude/x",
                    ".claude/settings.json", ".pi/x", "ATTA/X", ".git/config", "sub/.git/x"):
            with self.subTest(rel=rel):
                reason = self.write_reason(self.wt / rel, "Edit")
                self.assertDenied(reason)
                self.assertTrue("protegida" in reason or ".git" in reason, reason)

    def test_artifacts_of_active_job_allowed(self):
        job = self.root / "atta" / "jobs" / JID
        for rel in ("intake.md", "spec.md", "spec-review.md", "decision.md", "feedback-1.md",
                    "feedback-humano.md", "round-1/build.md", "round-2/review-code.md",
                    "round-12/review-security.md"):
            with self.subTest(rel=rel):
                self.assertAllowed(self.write_reason(job / rel))

    def test_other_job_files_denied(self):
        job = self.root / "atta" / "jobs" / JID
        for rel in ("job.json", "events.jsonl", "issue.md", "round-1/checks.md",
                    "round-1/checks.log", "round-0/build.md", "round-x/build.md", "notes.md",
                    "sub/intake.md", "round-1/review-.md", "feedback-.md", "intake.md.bak",
                    ".lock"):
            with self.subTest(rel=rel):
                self.assertDenied(self.write_reason(job / rel), "artefactos")

    def test_artifacts_of_inactive_or_unknown_job_denied(self):
        self.assertDenied(self.write_reason(self.root / "atta" / "jobs" / OLD / "decision.md"))
        self.assertDenied(self.write_reason(self.root / "atta" / "jobs" / "9-nada" / "intake.md"))

    def test_project_control_files_and_main_checkout_denied(self):
        for rel in ("atta/config.json", "atta/checks.json", ".claude/settings.json",
                    ".claude/agents/atta-reina.md", "README.md", "src/app.py", ".gitignore",
                    ".worktrees/x", f".worktrees/{JID}"):
            with self.subTest(rel=rel):
                self.assertDenied(self.write_reason(self.root / rel))

    def test_inactive_job_worktree_denied(self):
        self.assertDenied(self.write_reason(self.root / ".worktrees" / OLD / "src" / "a.py"),
                          "merged")
        self.make_job("4-rechazado", "rejected")
        (self.root / ".worktrees" / "4-rechazado").mkdir()
        self.assertDenied(self.write_reason(self.root / ".worktrees" / "4-rechazado" / "a.py"))

    def test_worktree_without_job_or_bad_id_denied(self):
        (self.root / ".worktrees" / "9-sin-job").mkdir()
        self.assertDenied(self.write_reason(self.root / ".worktrees" / "9-sin-job" / "a.py"))
        self.assertDenied(self.write_reason(self.root / ".worktrees" / "Bad_Id" / "a.py"))

    def test_corrupt_job_json_denied(self):
        write(self.root / "atta" / "jobs" / JID / "job.json", "{no json")
        self.assertDenied(self.write_reason(self.wt / "a.py"))

    def test_dotdot_escape_and_absolute_outside_denied(self):
        self.assertDenied(self.write_reason(str(self.wt) + "/../../README.md"))
        self.assertDenied(self.write_reason("../../atta/config.json", cwd=self.wt))
        self.assertDenied(self.write_reason(self.tmp / "fuera.txt"))
        self.assertDenied(self.write_reason(Path(tempfile.gettempdir()) / "x.txt"))

    def test_link_escape_denied(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        link = self.wt / "link"
        if not self._make_dir_link(link, outside):
            self.skipTest("no se pudo crear un junction/symlink en este equipo")
        self.assertDenied(self.write_reason(link / "x.txt"), "fuera del proyecto")
        # A link back into the project's protected area is also denied.
        link2 = self.wt / "link2"
        if self._make_dir_link(link2, self.root / "atta"):
            self.assertDenied(self.write_reason(link2 / "config.json"))

    @staticmethod
    def _make_dir_link(link, target):
        if os.name == "nt":
            done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                  capture_output=True, stdin=subprocess.DEVNULL)
            return done.returncode == 0 and link.exists()
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
        return True

    @unittest.skipUnless(sys.platform.startswith("win"), "rutas de Windows")
    def test_windows_alternate_stream_and_trailing_dot_denied(self):
        self.assertDenied(self.write_reason(str(self.wt / ".claude.") + "\\x"))
        self.assertDenied(self.write_reason(str(self.wt / "a.py") + ":stream"))
        self.assertDenied(self.write_reason("\\\\?\\" + str(self.wt / "a.py")))

    @unittest.skipUnless(sys.platform in ("win32", "darwin"), "sistema sin mayúsculas")
    def test_case_variants_resolve_on_case_insensitive_systems(self):
        upper = Path(str(self.wt).upper())
        self.assertAllowed(self.write_reason(upper / "src" / "a.py"))
        self.assertDenied(self.write_reason(self.root / "atta" / "jobs" / JID / "JOB.JSON"))
        self.assertAllowed(self.write_reason(self.root / "atta" / "jobs" / JID / "INTAKE.md"))

    def test_missing_or_bad_path_denied(self):
        for tool_input in ({}, {"file_path": ""}, {"file_path": 5}):
            with self.subTest(tool_input=tool_input):
                h = self.hook("Write")
                h["tool_input"] = tool_input
                self.assertDenied(guard.evaluate(h, root=self.root))

    def test_system_temp_dir_allowed_rest_outside_denied(self):
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool=tool):
                self.assertAllowed(self.write_reason(self.systmp / "scratchpad" / "r.txt", tool))
                self.assertDenied(self.write_reason(self.tmp / "fuera.txt", tool),
                                  "fuera del proyecto")
        self.assertDenied(self.write_reason(str(self.systmp) + "/../fuera.txt"))

    def test_other_tools_allowed(self):
        self.assertAllowed(guard.evaluate(self.hook("Read", file_path=str(self.root / "x")),
                                          root=self.root))

    def test_invalid_config_fails_closed(self):
        write(self.root / "atta" / "config.json", "{roto")
        self.assertDenied(self.write_reason(self.wt / "a.py"), "config")


class PushTests(GuardBase):
    def test_push_to_job_branch_allowed(self):
        for cmd in (f"git push origin atta/{JID}", f"git push -u origin HEAD:atta/{JID}",
                    f"git push origin refs/heads/atta/{JID}:refs/heads/atta/{JID}",
                    f"git -C {self.wt.as_posix()} push --set-upstream origin atta/{JID}",
                    f"git push -q --no-verify origin atta/{JID}"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.bash_reason(cmd))

    def test_push_outside_job_branches_denied(self):
        for cmd in ("git push origin develop", "git push origin main", "git push origin HEAD:main",
                    "git push origin HEAD:refs/heads/main", f"git push origin +atta/{JID}",
                    f"git push origin +HEAD:atta/{JID}", f"git push --force origin atta/{JID}",
                    f"git push -f origin atta/{JID}", f"git push -uf origin atta/{JID}",
                    f"git push --force-with-lease origin atta/{JID}",
                    f"git push --force-with-lease=atta/{JID} origin atta/{JID}",
                    "git push --mirror origin", "git push --all origin",
                    f"git push --delete origin atta/{JID}", f"git push origin --delete atta/{JID}",
                    f"git push origin :atta/{JID}", "git push", "git push origin",
                    "git push origin HEAD", "git push origin feature/x", "git push origin v1.0",
                    f"git push --receive-pack=evil origin atta/{JID}",
                    f"git -c alias.p=push p origin main", f"git push origin atta/{JID} main",
                    f"GIT_TRACE=1 git push origin main", f"sudo git push origin main",
                    f"/usr/bin/git push origin main", f"git.exe push origin main"):
            with self.subTest(cmd=cmd):
                self.assertDenied(self.bash_reason(cmd))

    def test_push_reasons_name_the_rule(self):
        self.assertDenied(self.bash_reason("git push origin main"), "rama prohibida")
        self.assertDenied(self.bash_reason("git push origin HEAD:main"), "rama prohibida")
        self.assertDenied(self.bash_reason("git push origin develop"), "solo se integra por PR")
        self.assertDenied(self.bash_reason(f"git push -f origin atta/{JID}"), "-f")
        self.assertDenied(self.bash_reason(f"git push origin :atta/{JID}"), "borra")
        self.assertDenied(self.bash_reason("ls\ngit push origin main"), "rama prohibida")

    def test_branch_deletion_denied(self):
        for cmd in ("git branch -D atta/x", "git branch -d x", "git branch --delete x",
                    "git branch -dr origin/x"):
            with self.subTest(cmd=cmd):
                self.assertDenied(self.bash_reason(cmd))
        self.assertAllowed(self.bash_reason("git branch --show-current"))

    def test_push_inside_powershell_tool(self):
        self.assertDenied(self.bash_reason("git push origin main", tool="PowerShell"))
        self.assertAllowed(self.bash_reason(f"git push origin atta/{JID}", tool="PowerShell"))


class CommandTests(GuardBase):
    def test_gh_dangerous_commands_denied(self):
        for cmd in ("gh pr merge 12", "gh pr merge --squash 12", "gh -R o/r pr merge 1",
                    'bash -c "gh pr merge 12"', "bash -lc 'gh pr merge 1'",
                    "sh -c 'echo hola; gh pr merge 1'", "git status && gh pr merge 12",
                    "true; gh pr merge 1", "echo x | gh pr merge 1", "true || gh pr merge 1",
                    "echo $(gh pr merge 1)", 'echo "$(gh pr merge 1)"', "echo `gh pr merge 1`",
                    "(gh pr merge 1)", "ls\ngh pr merge 1",
                    'pwsh -Command "gh pr merge 1"', 'powershell -NoProfile -c "gh pr merge 1"',
                    'cmd /c "gh pr merge 1"', "eval 'gh pr merge 1'",
                    "gh repo delete o/r --yes", "gh release create v1", "gh release delete v1",
                    "gh api -X PUT repos/o/r/pulls/1/merge", "gh pr create --base main",
                    "gh pr create --fill", "gh alias set m 'pr merge'"):
            with self.subTest(cmd=cmd):
                self.assertDenied(self.bash_reason(cmd))

    def test_gh_dangerous_commands_in_powershell_tool(self):
        for cmd in ("gh pr merge 1", "Get-Date; gh pr merge 1", "& gh pr merge 1",
                    "Invoke-Expression 'gh pr merge 1'"):
            with self.subTest(cmd=cmd):
                self.assertDenied(self.bash_reason(cmd, tool="PowerShell"))

    def test_encoded_powershell_is_decoded(self):
        import base64
        enc = base64.b64encode("gh pr merge 1".encode("utf-16-le")).decode()
        self.assertDenied(self.bash_reason(f"pwsh -EncodedCommand {enc}"))
        self.assertDenied(self.bash_reason("pwsh -enc !!!"))

    def test_safe_commands_allowed(self):
        for cmd in ("git status && python -m unittest", "gh pr view 12 --json state",
                    "gh issue view 3", "ls -la | grep x", "gh pr create --base develop --fill",
                    "echo hola > out.txt", "python -m unittest 2>&1 | tail -5",
                    "git log --oneline -5", "echo a#b"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.bash_reason(cmd, cwd=self.wt))

    def test_default_deploy_commands_denied(self):
        for cmd in ("kubectl apply -f k8s.yaml", "kubectl -n prod apply -f x",
                    "terraform apply -auto-approve", "vercel --prod", "vercel deploy --prod",
                    "fly deploy", "npx vercel --prod", "cd infra && terraform apply"):
            with self.subTest(cmd=cmd):
                self.assertDenied(self.bash_reason(cmd))
        self.assertDenied(self.bash_reason("kubectl -n x apply -f y"), "deny_commands")
        self.assertDenied(self.bash_reason("echo `gh pr merge 1`"), "gh pr merge")
        self.assertDenied(self.bash_reason("echo x > atta/config.json"), "ruta protegida")
        self.assertAllowed(self.bash_reason("terraform plan"))
        self.assertAllowed(self.bash_reason("kubectl get pods"))

    def test_configured_deny_commands(self):
        write(self.root / "atta" / "config.json",
              json.dumps({"guard": {"deny_commands": ["make deploy", "./deploy.sh"]}}))
        self.assertDenied(self.bash_reason("make deploy ENV=prod"))
        self.assertDenied(self.bash_reason("./deploy.sh"))
        self.assertAllowed(self.bash_reason("make test"))
        # The configured list replaces the defaults.
        self.assertAllowed(self.bash_reason("terraform apply"))
        # git push / gh pr merge are never configurable.
        self.assertDenied(self.bash_reason("gh pr merge 1"))

    def test_bad_deny_commands_config_fails_closed(self):
        write(self.root / "atta" / "config.json", json.dumps({"guard": {"deny_commands": "x"}}))
        self.assertDenied(self.bash_reason("ls"), "deny_commands")

    def test_redirect_into_protected_paths_denied(self):
        for cmd, cwd in (("echo x > atta/config.json", self.root),
                         ("echo x >> .claude/settings.json", self.root),
                         ("echo x | tee .claude/x", self.root),
                         ("echo x | tee -a out.txt atta/checks.json", self.root),
                         ("echo x > .github/workflows/ci.yml", self.wt),
                         ("echo x>atta/x", self.wt),
                         ("cd atta && echo x > config.json", self.root),
                         ("echo x 2> .git/config", self.root),
                         ("echo x > $HOME/x", self.root),
                         ("Set-Content -Path atta/config.json -Value x", self.root),
                         ("'x' | Out-File .claude/x", self.root)):
            with self.subTest(cmd=cmd):
                tool = "PowerShell" if cmd[:1] in "S'" else "Bash"
                self.assertDenied(self.bash_reason(cmd, tool=tool, cwd=cwd))

    def test_redirect_to_ordinary_files_allowed(self):
        """Shell writes are allowed inside the active job worktree and outside the project.

        Before the T5 correction (L9, M2) this test also stood for redirects anywhere in the
        project that were not protected paths. Now a redirect that lands inside the project but
        outside the active job worktree is denied (see ShellWriteScopeTests); only the worktree,
        null devices and folders outside the project (temp dirs) remain allowed.
        """
        for cmd, cwd in (("echo x > out.txt", self.wt), ("echo x > /dev/null", self.root),
                         ("echo x 2>&1 | tee log.txt", self.wt),
                         (f"echo x > {(self.wt / 'src' / 'a.txt').as_posix()}", self.root),
                         (f"echo x > {(self.tmp / 'scratch.txt').as_posix()}", self.root),
                         (f"echo x > {Path(tempfile.gettempdir()).as_posix()}/atta-x.txt",
                          self.wt)):
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.bash_reason(cmd, cwd=cwd))

    def test_unbalanced_quotes_still_scanned(self):
        self.assertDenied(self.bash_reason("gh pr merge 1 'oops"))
        self.assertDenied(self.bash_reason('echo "x; git push origin main'))

    def test_missing_command_denied(self):
        h = self.hook("Bash")
        self.assertDenied(guard.evaluate(h, root=self.root))


class BypassTests(GuardBase):
    """Regression tests for the independent verifier findings (L9: B1-B9, M1, minor)."""

    def denied_all(self, cmds, tool="Bash", cwd=None):
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertDenied(self.bash_reason(cmd, tool=tool, cwd=cwd))

    def allowed_all(self, cmds, tool="Bash", cwd=None):
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.bash_reason(cmd, tool=tool, cwd=cwd))

    def test_b1_shell_keywords_do_not_hide_the_command(self):
        self.denied_all(("for x in 1; do git push origin main; done",
                         "if git push origin main; then echo ok; fi",
                         "if true; then git push origin main; fi",
                         "if false; then :; elif gh pr merge 1; then :; fi",
                         "if false; then :; else git push origin main; fi",
                         "while true; do gh pr merge 1; done",
                         "until gh pr merge 1; do sleep 1; done",
                         "! git push origin main", "time git push origin main",
                         "{ git push origin main; }"))
        self.allowed_all((f"if git push origin atta/{JID}; then echo ok; fi",
                          "for f in a b; do echo $f; done", "! git diff --quiet"))

    def test_b2_line_continuations_are_joined(self):
        self.denied_all(("git \\\npush origin main", "git push \\\norigin \\\nmain",
                         "gh pr \\\nmerge 1", "gh pr \\\r\nmerge 1"))
        self.denied_all(("git `\npush origin main", "gh pr `\r\nmerge 1"), tool="PowerShell")
        self.allowed_all((f"git push \\\norigin atta/{JID}",))

    def test_b3_gh_api_every_endpoint_and_method(self):
        self.denied_all((
            "gh api -H 'Accept: application/vnd.github+json' -X PUT repos/o/r/pulls/1/merge",
            "gh api --header 'Accept: a/b' --method PUT repos/o/r/pulls/1/merge",
            "gh api -X PUT 'repos/o/r/pulls/1/merge?x=1'",
            "gh api -X PUT 'repos/o/r/pulls/1/merge#frag'",
            "gh api --method=PUT /repos/o/r/pulls/1/merge",
            "gh api -XPUT repos/o/r/pulls/1/merge",
            "gh api repos/o/r/pulls/1/merge -f merge_method=squash",
            "gh api -X PATCH repos/o/r/git/refs/heads/main -f sha=abc",
            "gh api -X POST repos/o/r/git/refs -f ref=refs/heads/x -f sha=abc",
            "gh api -X PUT repos/o/r/branches/main/protection --input p.json",
            "gh api -X DELETE repos/o/r/branches/main/protection",
            "gh api -X POST repos/o/r/merges -f base=main -f head=x",
            "gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'",
            "gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {}) { x } }'",
            "gh api graphql -f query='mutation { mergeBranch(input: {}) { x } }'",
            "gh api graphql -f query='mutation { updateRef(input: {}) { x } }'",
            "gh api graphql -f query='mutation { deleteRef(input: {}) { x } }'"))
        self.allowed_all(("gh api repos/o/r/pulls/1", "gh api repos/o/r/pulls/1/merge",
                          "gh api -H 'Accept: a/b' repos/o/r/git/refs/heads/develop",
                          "gh api graphql -f query='query { viewer { login } }'",
                          "gh api -X POST repos/o/r/issues/1/comments -f body=hola"))

    def test_b4_stdin_fed_shells_denied(self):
        self.denied_all(("echo 'gh pr merge 1' | bash", "cat x | sh", "bash < cmds.txt",
                         "bash -s < cmds.txt", "sh -", "bash <<< 'gh pr merge 1'",
                         "zsh", "dash -x", "echo x | pwsh", "echo x | powershell -NoProfile",
                         "pwsh -File -", "echo x | cmd", "pwsh -ExecutionPolicy Bypass"))
        self.denied_all(("cat <<< 'gh pr merge 1'", "grep x <<< \"$(gh pr merge 1)\""))
        self.allowed_all(("bash scripts/test.sh", "bash -x scripts/test.sh arg",
                          "bash -c 'ls -la'", "sh -o pipefail -c 'ls | wc -l'",
                          "pwsh -File build.ps1", "pwsh -NoProfile -Command Get-Date",
                          "cmd /c dir", "cat <<< 'hola'"))

    def test_b5_wrapped_commands_rechecked(self):
        self.denied_all(("find . -exec git push origin main \\;",
                         "find . -execdir gh pr merge 1 {} +",
                         "find . -ok git push origin main \\;",
                         "find . -okdir git push origin main \\;",
                         "find . -name x -exec sh -c 'gh pr merge 1' {} \\;",
                         "echo 1 | xargs gh pr merge", "env git push origin main",
                         "env -i A=1 git push origin main", "nice -n 5 git push origin main",
                         "nohup git push origin main", "timeout 5 git push origin main",
                         "timeout -s KILL 5s gh pr merge 1", "sudo -u bob git push origin main",
                         "command git push origin main", "exec git push origin main",
                         "time -p git push origin main"))
        self.allowed_all(("find . -name '*.py' -exec grep -n x {} +",
                          "find . -exec bash {} \\;", "timeout 60 python -m unittest"))

    def test_b6_git_config_environment_denied(self):
        self.denied_all((
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p origin main",
            "env GIT_CONFIG_PARAMETERS=\"'alias.p=push'\" git p origin main",
            "export GIT_CONFIG_GLOBAL=/tmp/evil; git p", "GIT_ALIAS_P=x git status",
            "git -c include.path=/tmp/evil status", "git -c includeIf.gitdir:/.path=x status",
            "git -c Alias.p=push p origin main", "git -c core.hooksPath=/tmp/h commit -m x"))
        self.denied_all(("$env:GIT_CONFIG_COUNT = 1; git p",
                         "[Environment]::SetEnvironmentVariable('GIT_CONFIG_GLOBAL', 'x')"),
                        tool="PowerShell")
        self.allowed_all(("GIT_TRACE=1 git status", "git -c user.name=x commit -m y",
                          "python -m unittest tests/test_git_config.py"))

    def test_b7_git_config_key_after_valued_flags(self):
        self.denied_all(("git config --file .git/config alias.p push",
                         "git config -f .git/config remote.origin.push refs/heads/main",
                         "git config --file=x alias.p push", "git config --blob HEAD:x alias.p push",
                         "git config --type bool alias.p push",
                         "git config --default x --comment y alias.p push",
                         "git config set --file x alias.p push", "git config include.path /tmp/x",
                         "git config includeIf.gitdir:/x/.path /tmp/x",
                         "git config core.hooksPath /tmp/h", "git config --global alias.p push"))
        self.allowed_all(("git config user.name x", "git config --get remote.origin.url",
                          "git config -f .gitmodules submodule.x.url",
                          "git config --file x user.email a@b"))

    def test_b8_powershell_parameter_spellings(self):
        import base64
        enc = base64.b64encode("gh pr merge 1".encode("utf-16-le")).decode()
        ok = base64.b64encode("Get-Date".encode("utf-16-le")).decode()
        self.denied_all(('powershell /c "gh pr merge 1"', "pwsh /Command 'gh pr merge 1'",
                         "pwsh -c 'gh pr merge 1'", "pwsh -Co 'gh pr merge 1'",
                         "pwsh -Com 'gh pr merge 1'", f"pwsh -e {enc}", f"pwsh -ec {enc}",
                         f"pwsh -EncodedCommand {enc}", f"powershell /EncodedCommand {enc}",
                         f"powershell -NoProfile /e {enc}", "pwsh -enc !!!"))
        self.denied_all(('powershell /c "gh pr merge 1"',), tool="PowerShell")
        self.allowed_all((f"pwsh -EncodedCommand {ok}", "powershell /c Get-Date"))

    def test_b9_update_ref_denied(self):
        self.denied_all((f"git update-ref -d refs/heads/atta/{JID}",
                         f"git update-ref --delete refs/heads/atta/{JID}",
                         "git update-ref refs/heads/develop HEAD",
                         "git update-ref refs/heads/main abc123", "git update-ref main HEAD",
                         "git update-ref -m x refs/heads/Main HEAD", "git update-ref --stdin"))
        self.allowed_all((f"git update-ref refs/heads/atta/{JID} HEAD",))

    def test_m1_http_writes_to_github_api_denied(self):
        url = "https://api.github.com/repos/o/r/pulls/1/merge"
        self.denied_all((f"curl -X PUT {url}", f"curl -XPUT {url}", f"curl --request PUT {url}",
                         f"curl --request=PATCH {url}", f"curl -sSX POST {url}",
                         f"curl -d '{{}}' {url}", f"curl --data-raw x {url}",
                         f"curl --json '{{}}' {url}", f"curl -H 'A: b' -X DELETE {url}",
                         f"wget --method=PUT {url}", f"wget --post-data=x {url}",
                         f"curl -X POST HTTPS://API.GITHUB.COM/graphql -d x"))
        self.denied_all((f"Invoke-RestMethod -Method Put -Uri {url}",
                         f"irm {url} -Method POST", f"iwr -Uri {url} -Method:Delete",
                         f"Invoke-WebRequest {url} -Me Patch"), tool="PowerShell")
        self.allowed_all((f"curl {url}", "curl -s https://api.github.com/repos/o/r",
                          "curl -X POST -d x https://example.com/api",
                          f"curl -G -d per_page=5 https://api.github.com/repos/o/r/pulls"))
        self.allowed_all((f"Invoke-RestMethod -Uri {url}",), tool="PowerShell")

    def test_long_s_does_not_match_s(self):
        # casefold() maps U+017F (long s) to «s»; the folder «.worktreeſ» is not «.worktrees».
        target = self.root / ".worktree\u017f" / JID / "a.py"
        self.assertDenied(self.write_reason(target))
        self.assertDenied(self.bash_reason(f"echo x > {target.as_posix()}"))

    @unittest.skipUnless(sys.platform.startswith("win"), "rutas de Windows")
    def test_drive_relative_path_denied(self):
        drive = self.wt.drive
        self.assertDenied(self.write_reason(f"{drive}src/a.py", cwd=self.wt), "unidad")
        self.assertDenied(self.bash_reason(f"echo x > {drive}src/a.py", cwd=self.wt))


class ShellWriteScopeTests(GuardBase):
    """M2: shell writes inside the project but outside the active worktree are denied."""

    def test_redirect_into_main_checkout_or_control_files_denied(self):
        job = self.root / "atta" / "jobs" / JID
        for cmd, cwd in (("echo x > README.md", self.root),
                         ("echo x > out.txt", self.root),
                         ("echo x >> src/app.py", self.root),
                         ("echo x | tee notes.txt", self.root),
                         ("echo x > ../../README.md", self.wt),
                         (f"echo x > {(job / 'job.json').as_posix()}", self.wt),
                         (f"echo x > {(job / 'intake.md').as_posix()}", self.wt),
                         (f"echo x > {(self.root / '.worktrees' / OLD / 'a').as_posix()}",
                          self.wt),
                         ("cd .. && echo x > y.txt", self.wt)):
            with self.subTest(cmd=cmd):
                self.assertDenied(self.bash_reason(cmd, cwd=cwd))
        self.assertDenied(self.bash_reason("Set-Content -Path README.md -Value x",
                                           tool="PowerShell", cwd=self.root))

    def test_redirect_inside_worktree_or_outside_project_allowed(self):
        for cmd, cwd in (("echo x > src/a.txt", self.wt),
                         (f"echo x > {(self.tmp / 'fuera.txt').as_posix()}", self.root),
                         ("echo x > /dev/null", self.root)):
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.bash_reason(cmd, cwd=cwd))
        self.assertAllowed(self.bash_reason("Set-Content -Path a.txt -Value x",
                                            tool="PowerShell", cwd=self.wt))

    def test_expected_allow_controls(self):
        self.assertAllowed(self.bash_reason(f"git push origin atta/{JID}"))
        self.assertAllowed(self.bash_reason("gh pr create --base develop --fill"))


RUN_BODY = (
    "Aquí está el intake.\n"
    "<<<ATTA-ARTIFACT\n"
    "<<<ATTA-ARTIFACT\n"
    "DECISION: ACCEPT\nRISK: low\nSURFACES: none\n\n## Summary\n\n"
    "La Exploradora aceptó el issue: agregar la función de resta.\n\n## Scope\n\n"
    "- Dentro: `def subtract(a: float, b: float) -> float:` en calculator.py.\n"
    "def subtract(a: float, b: float) -> float:\n"
    "    return a - b\n"
    "Si x > y el resultado es positivo.\n"
    "python -m unittest | tee salida.txt\n"
    "git push origin main\n"
    "</ATTA-ARTIFACT>>>\n"
    "DECISION: ACCEPT"
)


class HeredocTests(GuardBase):
    """L17 (a): heredoc and here-string bodies are data unless they feed a shell/interpreter."""

    def denied_all(self, cmds, tool="Bash", cwd=None):
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertDenied(self.bash_reason(cmd, tool=tool, cwd=cwd))

    def allowed_all(self, cmds, tool="Bash", cwd=None):
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.bash_reason(cmd, tool=tool, cwd=cwd))

    def test_run_command_from_l17_allowed(self):
        cmd = (f'cd "{self.root}" && atta save-artifact 1-agregar-la-funcion-de intake '
               f"--from-reply - <<'ATTA_REPLY_END'\n{RUN_BODY}\nATTA_REPLY_END")
        self.assertAllowed(self.bash_reason(cmd, cwd=self.root))
        self.assertAllowed(self.bash_reason(cmd + "\n", cwd=self.root))

    def test_heredoc_bodies_are_data(self):
        self.allowed_all((
            "cat <<EOF\ngit push origin main\nx > y\nEOF",
            'cat <<"EOF"\ngh pr merge 1\n-> float\nEOF',
            "cat <<-EOF\n\tgit push origin main\n\tEOF",
            "cat << 'EOF' > out.txt\ngit push origin main | tee x\nEOF",
            "cat <<EOF\nx > y\nEOF\necho listo",
            "cat <<A <<B\ngit push origin main\nA\ngh pr merge 1\nB",
            "python x.py <<EOF\ngit push origin main\nEOF",
            "cat <<'EOF'\n$(gh pr merge 1) `gh pr merge 1`\nEOF",
        ), cwd=self.wt)

    def test_heredoc_feeding_a_shell_or_interpreter_denied(self):
        self.denied_all((
            "bash <<EOF\ngit push origin main\nEOF",
            "sh <<'EOF'\ngh pr merge 1\nEOF",
            "env bash <<EOF\nls\nEOF",
            "cat <<EOF | bash\nls\nEOF",
            "pwsh <<EOF\nGet-Date\nEOF",
            "python <<EOF\nprint(1)\nEOF",
            "python3 - <<'EOF'\nprint(1)\nEOF",
            "py -3 - <<EOF\nprint(1)\nEOF",
            "node <<EOF\nconsole.log(1)\nEOF",
            "env perl <<EOF\nprint 1\nEOF",
            "cat <<EOF | ruby\nputs 1\nEOF",
            "source /dev/stdin <<EOF\ngit push origin main\nEOF",
            "while read l; do $l; done <<EOF\ngit push origin main\nEOF",
            "bash -c \"$(cat <<'EOF'\ngh pr merge 1\nEOF\n)\"",
            "read -r -d '' X <<'EOF'\ngh pr merge 1\nEOF\neval \"$X\"",
        ), cwd=self.wt)

    def test_heredoc_redirects_and_trailing_commands_still_checked(self):
        self.denied_all(("cat <<EOF > atta/checks.json\n{}\nEOF",
                         "tee atta/checks.json <<EOF\n{}\nEOF",
                         "cat <<'EOF' >> .claude/settings.json\nx\nEOF"), cwd=self.root)
        self.denied_all((
            "cat <<EOF\nx\nEOF\ngit push origin main",
            "cat <<EOF\n$(gh pr merge 1)\nEOF",
            "cat <<EOF\n`gh pr merge 1`\nEOF",
            "cat <<EOF\ngit push origin main",
            'echo "<<X"\ngit push origin main\nX',
            "echo '<<X'\ngh pr merge 1\nX",
            "# <<X\ngh pr merge 1\nX",
        ), cwd=self.wt)

    def test_powershell_here_strings(self):
        self.allowed_all((
            f"@'\n{RUN_BODY}\n'@ | atta save-artifact {JID} intake --from-reply -",
            "$t = @'\ngit push origin main\nx > y\n'@",
            '@"\ngh pr merge 1 -> float\n"@ | Out-Null',
        ), tool="PowerShell", cwd=self.root)
        self.denied_all((
            "@'\ngh pr merge 1\n'@ | Invoke-Expression",
            "Invoke-Expression @'\ngh pr merge 1\n'@",
            "@'\ngh pr merge 1\n'@ | pwsh -Command -",
            "$s = @'\ngh pr merge 1\n'@; iex $s",
            "Set-Content -Path atta/checks.json -Value @'\n{}\n'@",
            '@"\n$(gh pr merge 1)\n"@ | Out-Null',
            "@'\nx\n'@\ngh pr merge 1",
            "echo 'a@'\ngh pr merge 1\n'@",
            "@'\ngh pr merge 1",
        ), tool="PowerShell", cwd=self.root)


class TempDirWriteTests(GuardBase):
    """L17 (b): Write/Edit into the system temp dir are allowed; the rest outside stays denied."""

    real_temp = True

    def test_scratchpad_under_system_temp_allowed(self):
        scratch = Path(tempfile.gettempdir()) / "claude" / "scratchpad" / "atta-reply.txt"
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool=tool):
                self.assertAllowed(self.write_reason(scratch, tool))
        self.assertAllowed(self.bash_reason(f"echo x > {scratch.as_posix()}", cwd=self.root))

    def test_tmpdir_variable_counts_as_temp(self):
        other = Path(os.path.realpath(self.tmp / "otro-temp"))
        other.mkdir()
        system = Path(os.path.realpath(tempfile.gettempdir()))
        for var in ("TMPDIR", "TEMP", "TMP"):
            with self.subTest(var=var):
                roots = guard.temp_roots({var: str(other)})
                self.assertIn(system, roots)
                self.assertIn(other, roots)
        roots = guard.temp_roots({"TEMP": str(Path.home()), "TMPDIR": Path.home().anchor or "/",
                                  "TMP": "relativo"})
        self.assertEqual(roots, [system])
        h = self.hook("Write", file_path=str(other / "a.txt"))
        self.assertAllowed(guard.evaluate(h, root=self.root, environ={"TMPDIR": str(other)}))

    def test_home_files_stay_denied(self):
        bashrc = Path.home() / ".bashrc"
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool=tool):
                self.assertDenied(self.write_reason(bashrc, tool), "fuera del proyecto")
        h = self.hook("Write", file_path=str(bashrc))
        for env in ({"TEMP": str(Path.home())}, {"TMPDIR": Path.home().anchor or "/"}):
            with self.subTest(env=env):
                self.assertDenied(guard.evaluate(h, root=self.root, environ=env))

    def test_project_rules_still_apply_inside_temp(self):
        # The test project itself lives under the system temp dir: its own rules win.
        self.assertDenied(self.write_reason(self.root / "atta" / "config.json"))
        self.assertDenied(self.write_reason(self.root / "README.md"))
        self.assertAllowed(self.write_reason(self.wt / "src" / "a.py"))


class OutputTests(GuardBase):
    def run_text(self, text):
        out = io.StringIO()
        code = guard.run(text, out, root=self.root)
        return code, out.getvalue()

    def assertDenyJson(self, output):
        data = json.loads(output)
        self.assertEqual(set(data), {"hookSpecificOutput"})
        spec = data["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "PreToolUse")
        self.assertEqual(spec["permissionDecision"], "deny")
        self.assertTrue(spec["permissionDecisionReason"].endswith("«Atta»"))
        return spec["permissionDecisionReason"]

    def test_allow_prints_nothing(self):
        code, out = self.run_text(json.dumps(self.hook("Write", file_path=str(self.wt / "a"))))
        self.assertEqual((code, out), (0, ""))

    def test_deny_prints_decision_json(self):
        code, out = self.run_text(json.dumps(self.hook("Bash", command="gh pr merge 1")))
        self.assertEqual(code, 0)
        self.assertIn("gh pr merge", self.assertDenyJson(out))

    def test_malformed_input_denied(self):
        for text in ("", "{no json", "[]", "42", json.dumps({"tool_input": {}}),
                     json.dumps({"tool_name": "Write", "tool_input": "x"})):
            with self.subTest(text=text):
                code, out = self.run_text(text)
                self.assertEqual(code, 0)
                self.assertDenyJson(out)

    def test_module_entry_point_via_stdin(self):
        env = {k: v for k, v in os.environ.items() if k != guard.project.ENV_VAR}
        env["PYTHONPATH"] = str(REPO_ROOT)
        cases = (
            (self.hook("Write", file_path=str(self.wt / "src" / "a.py")), False),
            (self.hook("Write", file_path=str(self.root / "atta" / "config.json")), True),
            (self.hook("Bash", command='bash -c "gh pr merge 1"'), True),
            (self.hook("Bash", command=f"git push origin atta/{JID}"), False),
        )
        for hook, denied in cases:
            with self.subTest(hook=hook["tool_input"]):
                done = subprocess.run([sys.executable, "-m", "atta.spine.guard"],
                                      input=json.dumps(hook).encode("utf-8"), cwd=str(self.root),
                                      capture_output=True, env=env, timeout=60)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stderr, b"")
                if denied:
                    self.assertDenyJson(done.stdout.decode("utf-8"))
                else:
                    self.assertEqual(done.stdout, b"")
        done = subprocess.run([sys.executable, "-m", "atta.spine.guard"], input=b"{roto",
                              cwd=str(self.root), capture_output=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0)
        self.assertDenyJson(done.stdout.decode("utf-8"))

    def test_root_resolves_from_worktree_cwd(self):
        self.assertEqual(guard.resolve_root(self.wt, environ={}), self.root)
        self.assertEqual(guard.resolve_root(self.root, environ={}), self.root)

    def test_register_adds_guard_command(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--project")
        sub = parser.add_subparsers(dest="command")
        guard.register(sub)
        args = parser.parse_args(["--project", str(self.root), "guard"])
        args.root = self.root
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(
            self.hook("Bash", command="gh pr merge 1")).encode("utf-8")), encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(out):
            code = args.func(args, {}, None)
        self.assertEqual(code, 0)
        self.assertDenyJson(out.getvalue())


class SettingsTemplateTests(unittest.TestCase):
    def test_settings_wires_pretooluse_hook(self):
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        entries = data["hooks"]["PreToolUse"]
        self.assertEqual(len(entries), 1)
        matcher = set(entries[0]["matcher"].split("|"))
        self.assertEqual(matcher, {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash",
                                   "PowerShell"})
        self.assertEqual(entries[0]["hooks"], [{"type": "command", "command": "atta guard",
                                                "timeout": 30}])


if __name__ == "__main__":
    unittest.main()
