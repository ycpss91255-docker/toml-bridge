#!/usr/bin/env python3
"""Specs for the four modes base still calls (toml-bridge#4, base#2017).

Run:  python3 -m unittest discover -s script/ci -p 'test_*.py'

Every expected value below is a literal copied from running base's
`dockerfile/toml_bridge.py` (base main, 2026-11-20) on the same fixture,
never recomputed here. Tabs are written as `\\t` so a reader sees them.
Each case runs the script as a subprocess, which is how the image's
ENTRYPOINT runs it, so stdout bytes and the exit code are both checked.
"""
import os
import subprocess
import sys
import unittest

BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "..", "toml_bridge.py")


def run(mode, toml):
    """Run the bridge in <mode> on <toml> from stdin.

    Returns (exit code, stdout, stderr), decoded as UTF-8.
    """
    proc = subprocess.run([sys.executable, BRIDGE, mode],
                          input=toml.encode(), capture_output=True)
    return proc.returncode, proc.stdout.decode(), proc.stderr.decode()


class TablesMode(unittest.TestCase):
    def test_lists_every_declared_table_by_dotted_path(self):
        toml = ('[gui]\nmode = "wayland"\n\n'
                '[logging.web]\nlevel = "debug"\n\n'
                '[deploy2]\n\n'
                '[[volumes]]\nsource = "/a"\n')
        self.assertEqual(
            run("--tables", toml),
            (0, "gui\nlogging\nlogging.web\ndeploy2\nvolumes\n", ""))


class EmptyFamiliesMode(unittest.TestCase):
    def test_names_the_row_the_first_entry_would_take(self):
        toml = ('[deploy]\nargs = []\n\n'
                '[logging.web]\nrules = []\n\n'
                '[misc]\nunknown = []\n\n'
                '[image]\nrules = []\n')
        # `misc.unknown` is not an _ARRAY_SPEC family, so it is left out.
        self.assertEqual(
            run("--empty-families", toml),
            (0, "deploy\targ_1\tdeploy.args\n"
                "logging.web\trule_1\tlogging.web.rules\n"
                "image\trule_1\timage.rules\n", ""))

    def test_empty_root_array_is_its_own_home(self):
        self.assertEqual(
            run("--empty-families", "volumes = []\n"),
            (0, "volumes\tmount_1\tvolumes\n", ""))


class EmptyArraysMode(unittest.TestCase):
    def test_section_is_the_first_path_component(self):
        toml = ('[deploy]\nargs = []\n\n'
                '[logging.web]\nrules = []\n\n'
                '[misc]\nunknown = []\n\n'
                '[image]\nrules = []\n')
        # Unlike --empty-families, an unknown array is listed too, and
        # `logging.web.rules` belongs to `logging`.
        self.assertEqual(
            run("--empty-arrays", toml),
            (0, "deploy\tdeploy.args\n"
                "logging\tlogging.web.rules\n"
                "misc\tmisc.unknown\n"
                "image\timage.rules\n", ""))

    def test_empty_root_array(self):
        self.assertEqual(
            run("--empty-arrays", "volumes = []\n"),
            (0, "volumes\tvolumes\n", ""))


class EntriesMode(unittest.TestCase):
    def test_displays_fields_under_the_header_the_file_spells(self):
        toml = ('[gui]\nmode = "wayland"\ndebug = true\nempty = ""\n\n'
                '[[build.args]]\nkey = "HTTP_PROXY"\n'
                'value = "http://p:3128"\n\n'
                '[[build.args]]\nkey = "EMPTY"\nvalue = ""\n\n'
                '[image]\nrule_1 = "@basename"\n\n'
                '[[volumes]]\nsource = "/a"\ntarget = "/b"\nmode = "ro"\n')
        # An empty table scalar (`empty`) is no row; an empty entry field
        # (`value` of EMPTY) is. A numbered scalar of a hosted family
        # (`[image] rule_1`) prints as the [[image.rules]] entry it
        # stands for.
        self.assertEqual(
            run("--entries", toml),
            (0, "gui\t1\t[gui]\tmode\twayland\n"
                "gui\t1\t[gui]\tdebug\ttrue\n"
                "build\t2\t[[build.args]]\tkey\tHTTP_PROXY\n"
                "build\t2\t[[build.args]]\tvalue\thttp://p:3128\n"
                "build\t3\t[[build.args]]\tkey\tEMPTY\n"
                "build\t3\t[[build.args]]\tvalue\t\n"
                "image\t4\t[[image.rules]]\trule\t@basename\n"
                "volumes\t5\t[[volumes]]\tsource\t/a\n"
                "volumes\t5\t[[volumes]]\ttarget\t/b\n"
                "volumes\t5\t[[volumes]]\tmode\tro\n", ""))

    def test_unknown_array_is_refused_with_empty_stdout(self):
        toml = '[misc]\nlist = ["a"]\n\n[[install]]\nx = 1\n'
        self.assertEqual(
            run("--entries", toml),
            (1, "",
             "toml-bridge: misc.list is an array, and no numbered family "
             "of the shell view carries an array by that name, so nothing "
             "would read it: check the spelling of the array name, or "
             "remove it\n"))

    def test_merge_is_refused(self):
        proc = subprocess.run(
            [sys.executable, BRIDGE, "--entries", "--merge", "x.toml"],
            input=b"", capture_output=True)
        self.assertEqual(
            (proc.returncode, proc.stdout),
            (1, b""))
        self.assertEqual(
            proc.stderr.decode(),
            "toml-bridge: --entries reads one file from stdin, "
            "not --merge\n")


if __name__ == "__main__":
    unittest.main()
