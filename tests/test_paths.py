"""Ruta relativa al proyecto (F4.x T2, S2): rutas absolutas de hallazgos dentro del proyecto."""

import unittest

import support  # noqa: F401

from atta.spine.paths import project_rel


class ProjectRelTests(unittest.TestCase):
    def test_posix_worktree_prefix_is_removed(self):
        self.assertEqual(project_rel("/home/u/proj/.worktrees/7-x/calc.py", "/home/u/proj"),
                         "calc.py")
        self.assertEqual(project_rel("/home/u/proj/.worktrees/37-cifrar/src/a/b.py",
                                     "/home/u/proj/"), "src/a/b.py")

    def test_posix_root_prefix_is_removed(self):
        self.assertEqual(project_rel("/home/u/proj/src/calc.py", "/home/u/proj"), "src/calc.py")

    def test_windows_worktree_and_root_prefixes(self):
        root = "C:\\Users\\u\\proj"
        self.assertEqual(project_rel("C:\\Users\\u\\proj\\.worktrees\\7-x\\calc.py", root),
                         "calc.py")
        self.assertEqual(project_rel("C:/Users/u/proj/src/calc.py", root), "src/calc.py")
        # Drive paths compare without case (Windows file systems).
        self.assertEqual(project_rel("c:\\users\\U\\PROJ\\.worktrees\\7-x\\src\\m.py", root),
                         "src/m.py")

    def test_ignore_case_flag(self):
        self.assertEqual(project_rel("/Users/U/Proj/src/a.py", "/Users/u/proj",
                                     ignore_case=True), "src/a.py")
        self.assertEqual(project_rel("/Users/U/Proj/src/a.py", "/Users/u/proj",
                                     ignore_case=False), "/Users/U/Proj/src/a.py")

    def test_outside_path_is_unchanged_with_normalized_slashes(self):
        self.assertEqual(project_rel("/home/u/otro/calc.py", "/home/u/proj"),
                         "/home/u/otro/calc.py")
        self.assertEqual(project_rel("/home/u/project2/calc.py", "/home/u/proj"),
                         "/home/u/project2/calc.py")
        self.assertEqual(project_rel("D:\\otro\\calc.py", "C:\\Users\\u\\proj"),
                         "D:/otro/calc.py")

    def test_relative_path_is_unchanged_but_normalized(self):
        self.assertEqual(project_rel("src/calc.py", "/home/u/proj"), "src/calc.py")
        self.assertEqual(project_rel("./src/calc.py", "/home/u/proj"), "src/calc.py")
        self.assertEqual(project_rel(".\\src\\win.py", "C:\\proj"), "src/win.py")
        self.assertEqual(project_rel(".worktrees/7-x/a.py", "/home/u/proj"), ".worktrees/7-x/a.py")

    def test_dot_dot_cannot_escape_the_root(self):
        self.assertEqual(project_rel("/home/u/proj/../otro/a.py", "/home/u/proj"),
                         "/home/u/otro/a.py")

    def test_without_root_only_normalizes(self):
        self.assertEqual(project_rel(".\\a\\b.py", None), "a/b.py")

    def test_explicit_worktree_prefix(self):
        self.assertEqual(project_rel("/tmp/wt/src/a.py", "/home/u/proj", worktree="/tmp/wt"),
                         "src/a.py")


if __name__ == "__main__":
    unittest.main()
