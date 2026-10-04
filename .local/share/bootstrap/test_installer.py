#!/usr/bin/env python3
"""The installer's decisions, without pacman, the AUR or a terminal.

    python3 test_installer.py
    python3 -m unittest discover -s .

Everything the tool decides is decided from what two commands and one request
answer, and those are faked here, so the tests run offline in a fraction of a
second and can describe machines this one is not: a name a repository and the
AUR both carry, a name only a group carries, a sync database that could not
be read, a package that provides another.

What needs a real terminal - whether the table is legible, whether curses
hands the screen back after pacman - is not here. That is driven in a
throwaway tmux, where a screenshot is ground truth.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("inst", Path(__file__).with_name("installer.py"))
inst = importlib.util.module_from_spec(_spec)
sys.modules["inst"] = inst  # dataclasses look the module up while decorating
_spec.loader.exec_module(inst)

LIST = inst.LIST_DEFAULT
AUR_ENTRY = {"Name": "x", "Version": "1-1", "Description": "description from the AUR",
             "NumVotes": 12, "Maintainer": "someone", "OutOfDate": None}
# What Harness.load answers with unless a test says otherwise: the entries the
# fake system holds.
FROM_THE_SYSTEM = object()


def block(**fields: str) -> str:
    """One pacman block, the way pacman prints it."""
    return "".join(f"{key.replace('_', ' '):16}: {value}\n"
                   for key, value in fields.items()) + "\n"


def listed(*names: str) -> str:
    """A one-group list, for a test that only cares about these names."""
    return ('[[group]]\nname = "G"\ndescription = "the group"\n'
            f"packages = [{', '.join(repr(name) for name in names)}]\n")


class FakeSystem:
    """pacman and the AUR, answering from dictionaries.

    installed: name -> (version, install reason, provides)
    repos:     name -> [(repository, version, installed size, description)]
    groups:    group -> members
    aur:       name -> entry
    complaint: what pacman says besides "was not found", for the cases where
               the sync database itself is the problem. It belongs to the -Si
               lookup: a local database that cannot be read says so on -Qi,
               which is what qi_readable is for.
    """

    def __init__(self, installed=None, repos=None, groups=None, aur=None, complaint="",
                 qi_readable=True):
        self.installed = installed or {}
        self.repos = repos or {}
        self.groups = groups or {}
        self.aur = aur or {}
        self.complaint = complaint
        # -Q still answers when -Qi cannot: the version is readable, the
        # install reason and the provides are not.
        self.qi_readable = qi_readable
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None):
        self.calls.append(list(argv))
        flag = argv[1] if argv[:1] == ["pacman"] and len(argv) > 1 else None
        text = ""
        if flag == "-Qi":
            if self.qi_readable:
                text = "".join(block(Name=name, Version=version,
                                     Install_Reason=reason, Provides=provides)
                               for name, (version, reason, provides) in self.installed.items())
        elif flag == "-Q":
            text = "".join(f"{name} {version}\n"
                           for name, (version, _, _) in self.installed.items())
        elif flag == "-Sgg":
            text = "".join(f"{group} {member}\n"
                           for group, members in self.groups.items() for member in members)
        elif flag == "-Si":
            text = "".join(
                block(Repository=repository, Name=name, Version=version,
                      Description=description, Installed_Size=size)
                for name in argv[2:] for repository, version, size, description
                in self.repos.get(name, []))
        elif flag == "-Qdtq":
            text = "".join(f"{name}\n" for name in self.installed)
        else:
            return subprocess.CompletedProcess(argv, 1, "", f"unexpected command {argv}")
        stderr = "".join(f"error: package '{name}' was not found\n"
                         for name in argv[2:])
        if flag == "-Si":
            stderr = self.complaint + stderr
        if flag == "-Qi" and not self.qi_readable and not stderr:
            stderr = "error: could not read the local database\n"
        return subprocess.CompletedProcess(argv, 0, text, stderr)

    def asked_about(self) -> list[str]:
        """Every name pacman was asked to look up."""
        return [name for call in self.calls if call[:2] == ["pacman", "-Si"]
                for name in call[2:]]


class Harness(unittest.TestCase):
    """Fakes the two seams, so nothing here touches pacman or the network."""

    helper: str | None = "/usr/bin/yay"
    aur_result: object = FROM_THE_SYSTEM  # None means the request failed

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def load(self, text: str, system: FakeSystem, *, name="packages.toml", **kwargs):
        """The list in `text`, joined to the machine `system` describes."""
        path = Path(self.tmp.name) / name
        path.write_text(text)
        self.system = system
        self.aur_asked: list[str] = []

        def aur(names):
            self.aur_asked = list(names)
            if self.aur_result is not FROM_THE_SYSTEM:
                return self.aur_result
            return {n: system.aur[n] for n in names if n in system.aur}

        for attribute, replacement in (("run", system.run), ("query_aur", aur),
                                       ("aur_helper", lambda: self.helper)):
            self.addCleanup(setattr, inst, attribute, getattr(inst, attribute))
            setattr(inst, attribute, replacement)
        return inst.Catalogue(path, **kwargs)


class Screen:
    """As little of a curses window as the Tui touches."""

    def __init__(self, height=30, width=100):
        self.height = height
        self.width = width
        self.drawn: list[tuple[int, int, str]] = []

    def getmaxyx(self):
        return (self.height, self.width)

    def erase(self):
        self.drawn.clear()

    def clear(self):
        self.drawn.clear()

    def addstr(self, y, x, text, attr=0):
        self.drawn.append((y, x, text))

    def refresh(self):
        pass

    def keypad(self, flag):
        pass

    def text(self) -> str:
        return "\n".join(line for _, _, line in self.drawn)

    def find(self, needle: str) -> str:
        for line in self.text().splitlines():
            if needle in line:
                return line
        raise AssertionError(f"{needle!r} is not on the screen:\n{self.text()}")


def tui(screen: Screen, catalogue) -> inst.Tui:
    """A Tui that has scanned once, as the loop does before its first key.

    Scanning here rather than handing the table over matters: load() is what
    sets `seeded`, and a widget that never scanned would treat its first
    rescan as the first scan and re-seed the ticks."""
    widget = inst.Tui(screen, catalogue.path)
    widget.load()
    return widget


# ----------------------------------------------------------- what a list is


class TestLoadList(unittest.TestCase):
    def load(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "packages.toml"
            path.write_text(text)
            return inst.load_list(path)

    def refuses(self, text: str, *fragments: str) -> None:
        with self.assertRaises(inst.DataError) as caught:
            self.load(text)
        for fragment in fragments:
            self.assertIn(fragment, str(caught.exception))

    def test_a_bare_name_and_a_table_are_both_entries(self):
        groups = self.load('[[group]]\nname = "A"\ndescription = "d"\n'
                           'packages = ["one", { name = "two", source = "aur", note = "n" }]\n')
        self.assertEqual([(e.name, e.source, e.note) for e in groups[0].entries],
                         [("one", None, ""), ("two", "aur", "n")])

    def test_selected_is_false_unless_the_list_says_otherwise(self):
        plain = self.load('[[group]]\nname = "A"\ndescription = "d"\npackages = ["x"]\n')
        self.assertFalse(plain[0].selected)
        chosen = self.load('[[group]]\nname = "A"\ndescription = "d"\nselected = true\n'
                           'packages = ["x"]\n')
        self.assertTrue(chosen[0].selected)

    def test_the_real_list_loads_in_order_without_a_repeat(self):
        groups = inst.load_list(LIST)
        self.assertTrue(groups)
        self.assertEqual(inst.list_names(groups),
                         [entry.name for group in groups for entry in group.entries])
        self.assertEqual(inst.list_names(groups), list(dict.fromkeys(inst.list_names(groups))))
        for group in groups:
            self.assertTrue(group.name and group.description, group.name)
            for entry in group.entries:
                self.assertTrue(entry.name)

    def test_refusals(self):
        self.refuses('[[group]\nname = "A"\n', "not valid TOML")
        self.refuses('[[group]]\nname = "A"\ndescription = "d"\npackages = ["x"]\n[extra]\n',
                     "expected only [[group]]")
        self.refuses('[[group]]\nname = "A"\ndescription = "d"\nstray = 1\npackages = ["x"]\n',
                     "unknown keys")
        self.refuses('[[group]]\nname = "A"\npackages = ["x"]\n', "missing 'description'")
        self.refuses('[[group]]\nname = "A"\ndescription = "d"\npackages = []\n', "is empty")
        self.refuses('[[group]]\nname = "A"\ndescription = "d"\npackages = [7]\n',
                     "neither a name nor a table")
        self.refuses('[[group]]\nname = "A"\ndescription = "d"\npackages = ["x", "x"]\n',
                     "'x' is listed twice")
        self.refuses('[[group]]\nname = "A"\ndescription = "d"\npackages = ["x"]\n'
                     '[[group]]\nname = "B"\ndescription = "d"\npackages = ["x"]\n',
                     "in both 'A' and 'B'")
        self.refuses('[[group]]\nname = "A"\ndescription = "d"\n'
                     'packages = [{ name = "x", src = "aur" }]\n', "unknown keys")
        self.refuses('[[group]]\nname = "A"\ndescription = "d"\n'
                     'packages = [{ note = "x" }]\n', "has no name")
        self.refuses('[[group]]\nname = "A"\ndescription = "d"\n'
                     'packages = [{ name = "x", source = "pacman" }]\n', "invalid source")

class TestCandidateVariants(unittest.TestCase):
    def test_both_suffixes_for_a_plain_name(self):
        self.assertEqual(inst.candidate_variants(["mpv"]),
                         {"mpv-bin": "mpv", "mpv-git": "mpv"})

    def test_a_name_that_already_carries_a_suffix_gives_up_its_stem(self):
        # A suffixed entry still has relatives: the bare name, and the other
        # suffix. The value stays the entry itself, because that is the name
        # the alternatives are looked up by.
        self.assertEqual(inst.candidate_variants(["brave-bin"]),
                         {"brave": "brave-bin", "brave-git": "brave-bin"})
        self.assertEqual(inst.candidate_variants(["kitty-git"]),
                         {"kitty": "kitty-git", "kitty-bin": "kitty-git"})

    def test_a_name_the_list_already_holds_is_not_proposed(self):
        self.assertEqual(inst.candidate_variants(["zotero", "zotero-bin"]),
                         {"zotero-git": "zotero"})

    def test_the_base_is_carried_not_recovered_from_the_name(self):
        # git-filter-repo has a dash in the middle, so rsplit("-", 1) would
        # answer "git-filter". The base is carried along instead.
        self.assertEqual(inst.candidate_variants(["git-filter-repo"]),
                         {"git-filter-repo-bin": "git-filter-repo",
                          "git-filter-repo-git": "git-filter-repo"})

    def test_the_order_follows_the_list(self):
        self.assertEqual(list(inst.candidate_variants(["b", "a"])),
                         ["b-bin", "b-git", "a-bin", "a-git"])

    def test_the_real_list_proposes_nothing_it_already_holds(self):
        names = inst.list_names(inst.load_list(LIST))
        self.assertEqual(set(inst.candidate_variants(names)) & set(names), set())


# ------------------------------------------------------- what a command says


class TestParsing(unittest.TestCase):
    def test_blocks_split_on_a_blank_line(self):
        blocks = inst.parse_blocks(block(Name="a", Version="1") + block(Name="b", Version="2"))
        self.assertEqual([b["Name"] for b in blocks], ["a", "b"])
        self.assertEqual(blocks[0]["Version"], "1")

    def test_a_block_with_no_name_is_not_a_block(self):
        self.assertEqual(inst.parse_blocks("warning : this is not a package\n"), [])

    def test_provides(self):
        self.assertEqual(inst.split_provides("yay=12.4.2"), ["yay"])
        self.assertEqual(inst.split_provides("a=1   b=2"), ["a", "b"])
        self.assertEqual(inst.split_provides("None"), [])
        self.assertEqual(inst.split_provides(""), [])

    def test_repositories_fold_into_columns(self):
        for repository, column in [("cachyos", "cachyos"), ("cachyos-v3", "cachyos"),
                                   ("cachyos-extra-v3", "cachyos"),
                                   ("cachyos-core-v3", "cachyos"),
                                   ("core", "arch"), ("extra", "arch"), ("multilib", "arch"),
                                   ("core-testing", "arch"), ("extra-testing", "arch"),
                                   ("chaotic-aur", "chaotic"), ("aur", "aur"),
                                   ("a-new-one", "a-new-one")]:
            self.assertEqual(inst.canon_repo(repository), column, repository)

    def test_width_counts_cells_not_characters(self):
        self.assertEqual(inst.cell_width("abc"), 3)
        self.assertEqual(inst.cell_width("中文"), 4)
        self.assertEqual(inst.cell_width("a中"), 3)

    def test_fit_pads_and_clip_says_what_it_cut(self):
        self.assertEqual(inst.fit("ab", 4), "ab  ")
        self.assertEqual(inst.cell_width(inst.clip("abcdef", 4)), 4)
        self.assertTrue(inst.clip("abcdef", 4).endswith("…"))
        self.assertEqual(inst.clip("abc", 4), "abc")
        self.assertEqual(inst.clip("abcdef", 0), "")

    def test_counts_are_singular_when_there_is_one(self):
        self.assertEqual(inst.plural(1, "tick"), "1 tick")
        self.assertEqual(inst.plural(0, "tick"), "0 ticks")
        self.assertEqual(inst.plural(2, "vote"), "2 votes")


# ---------------------------------------------------- what the machine says


class TestDescribe(Harness):
    def test_a_repository_package(self):
        catalogue = self.load(listed("x"), FakeSystem(
            repos={"x": [("cachyos-extra-v3", "1-1", "1.0 MiB", "the description")]}))
        record = catalogue["x"]
        self.assertEqual((record.kind, record.sources, record.version),
                         ("repo", ["cachyos"], "1-1"))
        self.assertEqual(record.description, "the description")
        self.assertTrue(record.known)

    def test_a_package_both_places_carry_reads_as_both(self):
        catalogue = self.load(listed("x"), FakeSystem(
            repos={"x": [("extra", "1-1", "", "")]}, aur={"x": dict(AUR_ENTRY)}))
        self.assertEqual(catalogue["x"].sources, ["arch", "aur"])
        self.assertEqual(catalogue["x"].votes, 12)

    def test_repositories_keep_pacman_order_and_the_first_is_the_winner(self):
        catalogue = self.load(listed("x"), FakeSystem(repos={"x": [
            ("cachyos-extra-v3", "1-1", "", ""), ("extra", "1-0", "", "")]}))
        record = catalogue["x"]
        self.assertEqual(record.sources, ["cachyos", "arch"])
        self.assertEqual(record.winner, "cachyos")
        self.assertEqual(record.version, "1-1", "the winner's version, not the other's")

    def test_an_aur_only_package(self):
        catalogue = self.load(listed("x"), FakeSystem(aur={"x": dict(AUR_ENTRY)}))
        record = catalogue["x"]
        self.assertEqual((record.kind, record.sources), ("aur", ["aur"]))
        self.assertIn("12 votes", record.aur_note)
        self.assertIn("maintained by someone", record.aur_note)

    def test_an_orphaned_out_of_date_aur_entry_says_so(self):
        catalogue = self.load(listed("x"), FakeSystem(
            aur={"x": dict(AUR_ENTRY, Maintainer=None, OutOfDate=1)}))
        self.assertIn("orphaned", catalogue["x"].aur_note)
        self.assertIn("out of date", catalogue["x"].aur_note)

    def test_a_name_nobody_has_is_known_missing(self):
        catalogue = self.load(listed("x"), FakeSystem())
        self.assertEqual((catalogue["x"].kind, catalogue["x"].state, catalogue["x"].known),
                         ("unknown", "not found", True))
        self.assertEqual(catalogue.unchecked, [])

    def test_a_name_the_list_pins_to_aur_is_left_to_the_helper(self):
        catalogue = self.load('[[group]]\nname = "G"\ndescription = "d"\n'
                              'packages = [{ name = "x", source = "aur" }]\n', FakeSystem())
        self.assertEqual(catalogue["x"].kind, "aur")
        self.assertFalse(catalogue["x"].known, "nothing answered it, so it is a guess")

    def test_an_installed_package_is_here_whatever_the_lookups_said(self):
        catalogue = self.load(listed("x"), FakeSystem(
            installed={"x": ("1-1", "Explicitly installed", "None")}), verify_aur=False)
        self.assertEqual(catalogue["x"].state, "installed")
        self.assertTrue(catalogue["x"].is_satisfied)
        self.assertFalse(catalogue.machine.aur_checked, "installed is a fact without the AUR")

    def test_a_group_reports_its_members(self):
        catalogue = self.load(listed("g"), FakeSystem(
            groups={"g": ["a", "b"]}, installed={"a": ("1", "As dependency", "None")}))
        record = catalogue["g"]
        self.assertEqual((record.kind, record.members), ("group", ["a", "b"]))
        self.assertEqual(record.state, "1/2 installed")
        self.assertFalse(record.is_satisfied)

    def test_a_name_another_package_provides_counts_as_here(self):
        catalogue = self.load(listed("yay"), FakeSystem(
            installed={"yay-bin": ("12", "Explicitly installed", "yay=12")}))
        record = catalogue["yay"]
        self.assertEqual(record.state, "via yay-bin")
        self.assertTrue(record.is_satisfied)
        self.assertEqual(catalogue.count, (1, 1), "and the header counts it as installed")

    def test_what_is_provided_is_not_what_is_installed(self):
        catalogue = self.load(listed("x"), FakeSystem(
            installed={"yay-bin": ("12", "Explicitly installed", "yay=12")}))
        self.assertEqual(catalogue["x"].provided_by, "")
        self.assertFalse(catalogue["x"].is_satisfied)
        self.assertEqual(catalogue["x"].state, "not found", "yay-bin provides yay, not x")
        self.assertEqual(catalogue.machine.provides, {"yay": "yay-bin"})
        self.assertEqual(catalogue.count, (0, 1))

    def test_an_installed_row_says_why_it_is_here(self):
        catalogue = self.load(listed("x"), FakeSystem(
            installed={"x": ("1-1", "Explicitly installed", "None")},
            repos={"x": [("extra", "1-1", "", "")]}))
        self.assertIn("explicit", catalogue.detail(catalogue["x"]))
        catalogue = self.load(listed("x"), FakeSystem(
            installed={"x": ("1-1", "Installed as a dependency", "None")},
            repos={"x": [("extra", "1-1", "", "")]}))
        self.assertIn("as a dependency", catalogue.detail(catalogue["x"]))

    def test_the_detail_line_describes_the_row(self):
        catalogue = self.load(listed("x"), FakeSystem(
            repos={"x": [("extra", "1-1", "1.0 MiB", "the description")]}))
        detail = catalogue.detail(catalogue["x"])
        for fragment in ("from arch", "1-1", "1.0 MiB", "the description"):
            self.assertIn(fragment, detail)


class TestLookupsDegrade(Harness):
    def test_an_unreadable_sync_database_says_no_source_rather_than_not_found(self):
        catalogue = self.load(listed("x"), FakeSystem(
            complaint="error: database file for 'extra' does not exist (use '-Sy')\n"))
        self.assertFalse(catalogue.machine.repo_checked)
        self.assertEqual(catalogue["x"].state, "no source")
        self.assertIn("does not exist", catalogue.machine.repo_problem)
        notice = inst.lookup_notice(catalogue.machine, catalogue.unchecked)
        self.assertIn("repositories could not be read", notice)
        self.assertIn("does not exist", notice, "it says what pacman said")
        self.assertIn("1 entries have no known source", notice)

    def test_one_repository_missing_still_lets_the_others_answer(self):
        catalogue = self.load(listed("x"), FakeSystem(
            repos={"x": [("extra", "1-1", "", "")]},
            complaint="error: database file for 'chaotic-aur' does not exist\n"))
        self.assertTrue(catalogue.machine.repo_checked)
        self.assertEqual(catalogue["x"].sources, ["arch"])

    def test_without_the_aur_a_name_is_left_to_the_helper_and_flagged(self):
        catalogue = self.load(listed("x"), FakeSystem(), verify_aur=False)
        # Nothing could be asked, so the entry is left as an AUR one for the
        # helper to have the last word on, and `known` is what says so.
        self.assertEqual(catalogue["x"].kind, "aur")
        self.assertEqual(catalogue["x"].state, "not installed")
        self.assertFalse(catalogue["x"].known)
        self.assertEqual(catalogue.unchecked, ["x"])
        notice = inst.lookup_notice(catalogue.machine, catalogue.unchecked)
        # Not "did not answer": nobody asked. Saying the AUR is unreachable
        # would send you looking at the network.
        self.assertIn("the AUR was not asked", notice)
        self.assertNotIn("repositories could not be read", notice)

    def test_a_resolved_machine_says_nothing_about_lookups(self):
        catalogue = self.load(listed("x"), FakeSystem(repos={"x": [("extra", "1", "", "")]}))
        self.assertEqual(inst.lookup_notice(catalogue.machine, catalogue.unchecked), "")

    def test_an_aur_that_was_asked_and_did_not_answer_says_that(self):
        # The other half of the distinction --no-aur-check is named for: this
        # time the question was put, and nothing came back.
        self.aur_result = None
        catalogue = self.load(listed("x"), FakeSystem())
        self.assertFalse(catalogue.machine.aur_checked)
        self.assertTrue(catalogue.machine.aur_asked)
        self.assertIn("the AUR did not answer",
                      inst.lookup_notice(catalogue.machine, catalogue.unchecked))

    def test_a_local_database_that_cannot_be_read_warns_instead_of_inventing(self):
        system = FakeSystem(installed={"provider": ("1-1", "Explicitly installed", "wanted")},
                            repos={"provider": [("extra", "1-1", "1 KiB", "")]},
                            qi_readable=False)
        catalogue = self.load(listed("provider", "wanted"), system)
        machine = catalogue.machine
        self.assertFalse(machine.installed_checked)
        self.assertIn("could not read the local database", machine.installed_problem)
        # -Q still answered, so what is here stays a fact...
        self.assertEqual(catalogue["provider"].state, "installed")
        # ...and the two things it cannot answer are left unanswered rather
        # than guessed. Guessing explicit would be invisible; guessing that
        # nothing provides `wanted` reads as a package that is not here, and
        # this tool would offer to install it again.
        self.assertEqual((machine.explicit, machine.provides), (set(), {}))
        self.assertEqual(catalogue["wanted"].provided_by, "")
        self.assertNotIn("as a dependency", catalogue.detail(catalogue["provider"]))
        notice = inst.lookup_notice(machine, catalogue.unchecked)
        self.assertIn("only the names of installed packages are known", notice)
        self.assertIn("could not read the local database", notice)

    def test_the_same_machine_readable_says_what_provides_what(self):
        # The contrast that makes the test above about something.
        catalogue = self.load(listed("provider", "wanted"), FakeSystem(
            installed={"provider": ("1-1", "Explicitly installed", "wanted")},
            repos={"provider": [("extra", "1-1", "1 KiB", "")]}))
        self.assertTrue(catalogue.machine.installed_checked)
        self.assertEqual(catalogue["wanted"].state, "via provider")
        self.assertIn("explicit", catalogue.detail(catalogue["provider"]))

    def test_both_lookups_missing_names_both_of_them(self):
        catalogue = self.load(listed("x"), FakeSystem(
            complaint="error: database file for 'extra' does not exist\n"), verify_aur=False)
        notice = inst.lookup_notice(catalogue.machine, catalogue.unchecked)
        self.assertIn("repositories could not be read", notice)
        self.assertIn("the AUR was not asked", notice)


# --------------------------------------------------------- the two lookups


class TestAurQuery(unittest.TestCase):
    """The one request, made and answered without a network.

    This is the seam the rest of the tests replace, so it is the one place
    that has to be checked as it really is."""

    def answers(self, body=b'{"type": "multiinfo", "results": []}', fail=False):
        self.requests = []

        def urlopen(request, timeout=None):
            self.requests.append(request)
            if fail:
                raise OSError("the network is not here")
            return io.BytesIO(body)

        self.addCleanup(setattr, inst.urllib.request, "urlopen", inst.urllib.request.urlopen)
        inst.urllib.request.urlopen = urlopen
        # The retry waits between attempts; the tests do not.
        self.addCleanup(setattr, inst.time, "sleep", inst.time.sleep)
        inst.time.sleep = lambda seconds: None

    def test_the_names_go_in_the_body_not_in_the_url(self):
        # A URL stops fitting: 400 names is 12k characters, which the AUR
        # answers with 414 - and a 414 arrives as the same None as a name it
        # does not know, so every entry would read "no source" unexplained.
        self.answers()
        inst.query_aur(["mpv", "yay"])
        request = self.requests[0]
        self.assertEqual(request.full_url, inst.AUR_RPC)
        self.assertEqual(request.data, b"arg%5B%5D=mpv&arg%5B%5D=yay")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"),
                         "application/x-www-form-urlencoded")

    def test_an_empty_list_asks_nothing_at_all(self):
        self.answers()
        self.assertEqual(inst.query_aur([]), {})
        self.assertEqual(self.requests, [])

    def test_the_answer_is_the_entries_keyed_by_name(self):
        self.answers(b'{"type": "multiinfo", "results": [{"Name": "mpv-git",'
                     b' "NumVotes": 230}]}')
        self.assertEqual(list(inst.query_aur(["mpv-git"])), ["mpv-git"])

    def test_an_answer_that_is_not_multiinfo_is_no_answer(self):
        self.answers(b'{"type": "error", "error": "Incorrect request type"}')
        self.assertIsNone(inst.query_aur(["mpv"]))

    def test_an_aur_that_keeps_refusing_gives_up_within_its_budget(self):
        self.answers(fail=True)
        self.assertIsNone(inst.query_aur(["mpv"]))
        self.assertEqual(len(self.requests), inst.AUR_ATTEMPTS)


class TestVariantsOnTheMachine(Harness):
    TEXT = listed("mpv", "zellij")

    def system(self, **overrides) -> FakeSystem:
        described = dict(
            repos={"mpv": [("extra", "1-1", "", "")],
                   "zellij": [("extra", "1-1", "", "")],
                   "zellij-git": [("chaotic-aur", "0.41", "", "")]},
            aur={"mpv-git": dict(AUR_ENTRY, Name="mpv-git", NumVotes=230)})
        described.update(overrides)
        return FakeSystem(**described)

    def test_off_by_default_asks_about_nothing_extra(self):
        catalogue = self.load(self.TEXT, self.system())
        self.assertEqual(catalogue.machine.candidates, {})
        self.assertEqual(catalogue.variants, {})
        self.assertEqual(catalogue.count, (0, 2))
        self.assertEqual(self.system.asked_about(), ["mpv", "zellij"])

    def test_on_looks_for_both_suffixes_and_keeps_only_what_exists(self):
        catalogue = self.load(self.TEXT, self.system(), variants=True)
        self.assertEqual(catalogue.variants, {"mpv": ["mpv-git"], "zellij": ["zellij-git"]})
        self.assertNotIn("mpv-bin", catalogue.variant_records)
        self.assertNotIn("zellij-bin", catalogue.variant_records)

    def test_the_candidates_ride_along_in_the_same_two_lookups(self):
        self.load(self.TEXT, self.system(), variants=True)
        self.assertEqual(len([c for c in self.system.calls if c[:2] == ["pacman", "-Si"]]), 1)
        self.assertEqual(self.system.asked_about(),
                         ["mpv", "zellij", "mpv-bin", "mpv-git", "zellij-bin", "zellij-git"])
        self.assertIn("mpv-git", self.aur_asked, "the one request covers them too")
        self.assertIn("zellij-bin", self.aur_asked)

    def test_a_variant_carries_its_sources_like_any_entry(self):
        catalogue = self.load(self.TEXT, self.system(), variants=True)
        self.assertEqual(catalogue["mpv-git"].sources, ["aur"])
        self.assertEqual(catalogue["mpv-git"].votes, 230)
        self.assertEqual(catalogue["zellij-git"].sources, ["chaotic"])
        self.assertEqual(catalogue["zellij-git"].state, "not installed")

    def test_the_count_and_the_columns_follow_what_is_shown(self):
        catalogue = self.load(self.TEXT, self.system(), variants=True)
        self.assertEqual(catalogue.count, (0, 4))
        catalogue.variants_shown = False
        self.assertEqual(catalogue.count, (0, 2))
        self.assertEqual([record.name for record in catalogue.listed], ["mpv", "zellij"])
        catalogue.variants_shown = True
        self.assertEqual([record.name for record in catalogue.listed],
                         ["mpv", "zellij", "mpv-git", "zellij-git"])

    def test_a_variant_that_is_installed_counts_as_installed(self):
        catalogue = self.load(self.TEXT, self.system(
            installed={"mpv-git": ("1-1", "Explicitly installed", "None")}), variants=True)
        self.assertEqual(catalogue.count, (1, 4))
        self.assertTrue(catalogue["mpv-git"].is_satisfied)
        self.assertEqual(catalogue["mpv-git"].state, "installed")

    def test_a_list_that_already_names_a_variant_does_not_offer_it_twice(self):
        catalogue = self.load(listed("mpv", "mpv-git"), self.system(), variants=True)
        self.assertEqual(catalogue.variants, {})
        self.assertEqual(catalogue.count, (0, 2))
        self.assertEqual([record.name for record in catalogue.listed], ["mpv", "mpv-git"])

    def test_every_family_is_symmetric_and_holds_no_stranger(self):
        catalogue = self.load(self.TEXT, self.system(), variants=True)
        for base, variants in catalogue.variants.items():
            for variant in variants:
                self.assertIn(base, catalogue.alternatives[variant])
                self.assertIn(variant, catalogue.alternatives[base])
        self.assertNotIn("mpv-bin", catalogue.alternatives, "it does not exist anywhere")


# ------------------------------------------------------- what a command does


class TestPlans(Harness):
    TEXT = listed("repo-one", "aur-one", "here-one")

    def system(self) -> FakeSystem:
        return FakeSystem(
            repos={"repo-one": [("extra", "1-1", "", "")]},
            aur={"aur-one": dict(AUR_ENTRY, Name="aur-one")},
            installed={"here-one": ("1-1", "Explicitly installed", "None")})

    def test_a_repository_package_and_an_aur_package_go_in_separate_commands(self):
        catalogue = self.load(self.TEXT, self.system())
        commands, problems = inst.install_plan(catalogue, ["repo-one", "aur-one"], upgrade=False)
        self.assertEqual(problems, [])
        self.assertEqual(commands, [["sudo", "pacman", "-S", "--needed", "repo-one"],
                                    ["/usr/bin/yay", "-S", "--needed", "aur-one"]])

    def test_the_upgrade_is_a_command_of_its_own_and_comes_first(self):
        catalogue = self.load(self.TEXT, self.system())
        commands, _ = inst.install_plan(catalogue, ["aur-one"], upgrade=True)
        self.assertEqual(commands, [["sudo", "pacman", "-Syu"],
                                    ["/usr/bin/yay", "-S", "--needed", "aur-one"]])

    def test_what_is_already_satisfied_is_not_named_again(self):
        catalogue = self.load(self.TEXT, self.system())
        self.assertEqual(inst.install_plan(catalogue, ["here-one"], upgrade=False), ([], []))
        commands, _ = inst.install_plan(catalogue, ["here-one", "repo-one"], upgrade=False)
        self.assertEqual(commands, [["sudo", "pacman", "-S", "--needed", "repo-one"]])

    def test_installing_nothing_runs_no_upgrade_either(self):
        catalogue = self.load(self.TEXT, self.system())
        commands, problems = inst.install_plan(catalogue, ["here-one"], upgrade=True)
        self.assertEqual((commands, problems), ([], []))

    def test_a_group_is_expanded_to_its_members(self):
        catalogue = self.load(listed("fcitx"), FakeSystem(
            groups={"fcitx": ["fcitx5", "fcitx5-configtool"]},
            repos={"fcitx5": [("extra", "1", "", "")],
                   "fcitx5-configtool": [("extra", "1", "", "")]}))
        self.assertEqual(inst.expand(catalogue, ["fcitx"]), ["fcitx5", "fcitx5-configtool"])
        commands, problems = inst.install_plan(catalogue, ["fcitx"], upgrade=False)
        self.assertEqual(problems, [])
        self.assertEqual(commands, [["sudo", "pacman", "-S", "--needed",
                                     "fcitx5", "fcitx5-configtool"]])

    def test_a_half_installed_group_names_only_the_missing_half(self):
        # The group is not satisfied, so it reaches the plan; the member that
        # is already here is not something to ask pacman for again.
        catalogue = self.load(listed("fcitx"), FakeSystem(
            installed={"fcitx5": ("1", "Explicitly installed", "None")},
            groups={"fcitx": ["fcitx5", "fcitx5-configtool"]},
            repos={"fcitx5": [("extra", "1", "", "")],
                   "fcitx5-configtool": [("extra", "1", "", "")]}))
        self.assertEqual(catalogue["fcitx"].state, "1/2 installed")
        commands, problems = inst.install_plan(catalogue, ["fcitx"], upgrade=False)
        self.assertEqual(problems, [])
        self.assertEqual(commands, [["sudo", "pacman", "-S", "--needed",
                                     "fcitx5-configtool"]])

    def test_a_name_nothing_has_is_a_problem_and_nothing_runs(self):
        catalogue = self.load(listed("nonesuch"), FakeSystem())
        commands, problems = inst.install_plan(catalogue, ["nonesuch"], upgrade=True)
        self.assertEqual(commands, [])
        self.assertIn("no repository and no AUR entry by this name: nonesuch", problems)

    def test_an_aur_package_with_no_helper_is_a_problem_and_nothing_runs(self):
        self.helper = None
        catalogue = self.load(self.TEXT, self.system())
        commands, problems = inst.install_plan(catalogue, ["aur-one"], upgrade=True)
        self.assertEqual(commands, [])
        self.assertIn("no AUR helper", " ".join(problems))
        self.assertEqual(inst.install_plan(catalogue, ["repo-one"], upgrade=True)[1], [],
                         "a repository package needs no helper")

    def test_removing_names_only_what_is_here(self):
        catalogue = self.load(self.TEXT, self.system())
        commands, problems = inst.remove_plan(catalogue, ["here-one", "repo-one"])
        self.assertEqual(problems, [])
        self.assertEqual(commands, [["sudo", "pacman", "-Rns", "here-one"]])

    def test_removing_nothing_installed_says_so(self):
        catalogue = self.load(self.TEXT, self.system())
        commands, problems = inst.remove_plan(catalogue, ["repo-one"])
        self.assertEqual(commands, [])
        self.assertIn("nothing ticked is installed", problems[0])

    def test_removing_a_group_removes_the_members_that_are_here(self):
        catalogue = self.load(listed("g"), FakeSystem(
            groups={"g": ["a", "b"]}, installed={"a": ("1", "As dependency", "None")}))
        self.assertEqual(inst.remove_plan(catalogue, ["g"]),
                         ([["sudo", "pacman", "-Rns", "a"]], []))

    def test_a_variant_is_installed_by_the_command_that_fits_it(self):
        catalogue = self.load(listed("mpv"), FakeSystem(
            repos={"mpv": [("extra", "1", "", "")]},
            aur={"mpv-git": dict(AUR_ENTRY, Name="mpv-git")}), variants=True)
        commands, problems = inst.install_plan(catalogue, ["mpv-git"], upgrade=False)
        self.assertEqual(problems, [])
        self.assertEqual(commands, [["/usr/bin/yay", "-S", "--needed", "mpv-git"]])


# --------------------------------------------------------------- the table


class TestTuiLogic(Harness):
    """Keys, and what they do to the ticks and the rows."""

    TEXT = listed("mpv", "zellij")

    def widget(self, **kwargs) -> inst.Tui:
        self.screen = Screen()
        return tui(self.screen, self.load(self.TEXT, FakeSystem(
            repos={"mpv": [("extra", "1-1", "", "a player")],
                   "zellij": [("extra", "1-1", "", "a multiplexer")],
                   "zellij-git": [("chaotic-aur", "0.41", "", "")]},
            aur={"mpv-git": dict(AUR_ENTRY, Name="mpv-git")}), **kwargs))

    def rows(self, widget: inst.Tui) -> list[tuple[str, str]]:
        return [(kind, item.name) for kind, item in widget.build_rows()]

    def press(self, widget: inst.Tui, *keys: str) -> None:
        """A key press as the loop does it: draw, then read the key.

        The drawing is not decoration - the cursor is answered against the
        rows the last draw built, so a key pressed against a table that was
        never drawn would act on nothing."""
        for key in keys:
            widget.draw()
            widget.handle(key)

    def test_the_plain_view_is_the_group_and_its_entries(self):
        widget = self.widget()
        self.assertEqual(self.rows(widget),
                         [("group", "G"), ("entry", "mpv"), ("entry", "zellij")])
        widget.draw()
        self.assertIn("0/2 installed", self.screen.find("0/2 installed"))

    def test_v_searches_and_puts_each_row_under_the_name_it_replaces(self):
        widget = self.widget()
        self.press(widget, "v")
        self.assertTrue(widget.variants)
        self.assertEqual(self.rows(widget), [("group", "G"),
                                             ("entry", "mpv"), ("alternative", "mpv-git"),
                                             ("entry", "zellij"), ("alternative", "zellij-git")])
        widget.draw()
        self.assertIn("0/4 installed", self.screen.find("0/4 installed"),
                      "the count follows the rows, not the list file")

    def test_v_twice_is_a_change_of_view_and_costs_no_second_look(self):
        widget = self.widget()
        self.press(widget, "v")
        calls = len(self.system.calls)
        self.press(widget, "v")
        self.assertFalse(widget.variants)
        self.assertEqual([kind for kind, _ in self.rows(widget)],
                         ["group", "entry", "entry"])
        self.press(widget, "v")
        self.assertTrue(widget.variants)
        self.assertEqual(len(self.system.calls), calls, "the answers were already in hand")

    def test_ticking_a_group_ticks_its_packages(self):
        widget = self.widget()
        widget.cursor = 0
        self.press(widget, " ")
        self.assertEqual(widget.ticked, {"mpv", "zellij"})
        self.press(widget, " ")
        self.assertEqual(widget.ticked, set())

    def test_ticking_a_variant_untick_the_name_it_replaces(self):
        widget = self.widget()
        self.press(widget, "v")
        widget.cursor = 1  # mpv
        self.press(widget, " ")
        self.assertEqual(widget.ticked, {"mpv"})
        widget.cursor = 2  # mpv-git
        self.press(widget, " ")
        self.assertEqual(widget.ticked, {"mpv-git"},
                         "pacman would refuse both of them in one command")
        widget.cursor = 1
        self.press(widget, " ")
        self.assertEqual(widget.ticked, {"mpv"})

    def test_all_takes_one_from_each_family(self):
        widget = self.widget()
        self.press(widget, "v")
        widget.cursor = 2
        self.press(widget, " ")  # mpv-git, ticked by hand
        self.assertEqual(widget.ticked, {"mpv-git"})
        self.press(widget, "a")
        self.assertEqual(widget.ticked, {"mpv", "zellij"},
                         "all must not hand pacman mpv and mpv-git together")
        self.press(widget, "x")
        self.assertEqual(widget.ticked, set())

    def test_hiding_the_variants_drops_a_tick_that_would_be_invisible(self):
        widget = self.widget()
        self.press(widget, "v")
        widget.cursor = 2
        self.press(widget, " ")
        self.press(widget, "v")
        self.assertEqual(widget.ticked, set())
        widget.draw()
        self.assertIn("1 tick dropped", self.screen.find("hidden"))

    def test_a_tick_outlives_a_rescan_but_not_the_list_it_was_on(self):
        widget = self.widget()
        widget.cursor = 2
        self.press(widget, " ")  # zellij
        self.assertEqual(widget.ticked, {"zellij"})
        Path(widget.path).write_text(listed("mpv"))  # zellij is gone from the list
        self.press(widget, "R")
        self.assertEqual(widget.ticked, set())
        widget.draw()
        self.assertIn("1 tick dropped", self.screen.find("no longer in the list"))

    def test_a_rescan_that_cannot_read_the_list_keeps_the_table(self):
        widget = self.widget()
        before = len(widget.catalogue.entries)
        Path(widget.path).write_text("[[group]\nname = broken\n")
        self.press(widget, "R")
        self.assertEqual(len(widget.catalogue.entries), before, "the old table is still there")
        widget.draw()
        self.assertIn("not valid TOML", self.screen.find("not rescan"))
        self.assertTrue(widget.handle("j"), "and it still answers keys")
        self.assertFalse(widget.handle("q"), "q still quits")

    def test_a_list_file_that_vanishes_is_reported_the_same_way(self):
        widget = self.widget()
        Path(widget.path).unlink()
        self.press(widget, "R")
        widget.draw()
        self.assertIn("packages.toml", self.screen.find("not rescan"))

    def test_the_filter_narrows_by_package_or_by_group(self):
        widget = self.widget()
        widget.filter = "zellij"
        self.assertEqual(self.rows(widget), [("group", "G"), ("entry", "zellij")])
        widget.filter = "G"
        self.assertEqual(len(self.rows(widget)), 3, "a group name matches its entries")
        widget.filter = "nonesuch"
        self.assertEqual(self.rows(widget), [])

    def test_only_missing_hides_what_is_already_answered(self):
        catalogue = self.load(self.TEXT, FakeSystem(
            repos={"mpv": [("extra", "1", "", "")], "zellij": [("extra", "1", "", "")]},
            installed={"mpv": ("1", "Explicitly installed", "None")}))
        widget = tui(Screen(), catalogue)
        widget.only_missing = True
        self.assertEqual(self.rows(widget), [("group", "G"), ("entry", "zellij")])

    def test_a_window_too_small_to_draw_ignores_every_key_but_q(self):
        widget = self.widget()
        widget.screen.height, widget.screen.width = 7, 30
        for key in ("a", " ", "i", "v", "R"):
            widget.draw()
            self.assertTrue(widget.handle(key), f"{key!r} must not act")
        self.assertEqual(widget.ticked, set(), "nothing ticked on a table nobody can see")
        widget.draw()
        self.assertFalse(widget.handle("q"), "but the way out is still open")
        widget.draw()
        self.assertIn("terminal too small", widget.screen.find("terminal too small"))

    def test_the_layout_drops_the_columns_before_it_crops_the_names(self):
        widget = self.widget()
        widget.draw()
        self.assertTrue(widget.layout()["sources"], "there is room at 100 columns")
        widget.screen.width = 40
        self.assertEqual(widget.layout()["sources"], [])
        self.assertGreaterEqual(widget.layout()["name_width"], 8)

    def test_the_columns_are_in_priority_order_and_only_when_used(self):
        widget = self.widget()
        self.assertEqual(widget.columns(), ["arch"], "no cachyos, chaotic or aur in the plain view")
        self.press(widget, "v")
        self.assertEqual(widget.columns(), ["arch", "chaotic", "aur"])

    def test_help_is_one_key_away_and_its_way_out_stays_on_screen(self):
        widget = self.widget()
        widget.screen.height = 10
        self.press(widget, "?")
        widget.draw()
        self.assertIn("press any key to go back", self.screen.find("press any key"))
        self.assertEqual(self.screen.drawn[-1][0], 9, "the last line, whatever the height")
        widget.draw()
        self.assertTrue(widget.handle("x"), "any key goes back")
        self.assertFalse(widget.helping)

    def test_the_detail_line_follows_the_cursor(self):
        widget = self.widget()
        widget.draw()
        self.assertIn("· group ·", self.screen.find("· group ·"))
        widget.cursor = 1
        widget.draw()
        self.assertIn("a player", self.screen.find("a player"))
        self.press(widget, "v")
        widget.cursor = 2
        widget.draw()
        self.assertIn("alternative build of mpv", self.screen.find("alternative build"))

    def test_the_header_says_how_many_are_ticked_and_how_syu_is_set(self):
        widget = self.widget()
        widget.draw()
        self.assertIn("0 ticked", self.screen.find("0 ticked"))
        self.assertIn("Syu on", self.screen.find("Syu on"))
        self.press(widget, "U")
        widget.draw()
        self.assertIn("Syu off", self.screen.find("Syu off"))

    def test_the_footer_points_at_the_keys_rather_than_repeating_them(self):
        widget = self.widget()
        widget.screen.width = 130
        widget.draw()
        self.assertIn("U Syu", self.screen.find("U Syu"))
        widget.screen.width = 60
        widget.draw()
        self.assertIn("? keys", self.screen.find("? keys"))
        self.assertIn("i install", self.screen.find("i install"),
                      "the narrow one still names the key that does something")
        self.assertNotIn("U Syu", self.screen.text(),
                         "one line or a pointer, never a third spelling")


class TestExecution(Harness):
    """Running the commands, and what the table says once you are back.

    What the commands print scrolls away with the terminal they ran in, so
    anything the table has to remember has to be put there on purpose."""

    def widget(self) -> inst.Tui:
        self.screen = Screen()
        return tui(self.screen, self.load(listed("repo-one"), FakeSystem(
            repos={"repo-one": [("extra", "1-1", "", "")]})))

    def stand_in(self, exit_code: int) -> None:
        """The calls that need a real screen, and the key that comes back."""
        for name, replacement in (("def_prog_mode", lambda: None),
                                  ("endwin", lambda: None),
                                  ("reset_prog_mode", lambda: None),
                                  ("curs_set", lambda cursors: None)):
            self.addCleanup(setattr, inst.curses, name, getattr(inst.curses, name))
            setattr(inst.curses, name, replacement)
        self.addCleanup(setattr, inst.subprocess, "call", inst.subprocess.call)
        inst.subprocess.call = lambda command: exit_code
        self.addCleanup(setattr, sys, "stdin", sys.stdin)
        sys.stdin = io.StringIO("\n")

    def run_commands(self, widget: inst.Tui, commands: list[list[str]]) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            widget.execute(commands)
        return out.getvalue()

    def test_a_command_that_fails_is_still_said_on_the_table(self):
        widget = self.widget()
        self.stand_in(exit_code=1)
        printed = self.run_commands(widget, [["/bin/false"], ["/bin/true"]])
        self.assertIn("stopped: exit 1", printed, "the terminal gets it too")
        self.assertIn("exit 1", widget.status)
        widget.draw()
        self.assertIn("exit 1", self.screen.find("exit 1"),
                      "and it is still there after the redraw")

    def test_a_command_that_works_leaves_nothing_behind(self):
        widget = self.widget()
        self.stand_in(exit_code=0)
        printed = self.run_commands(widget, [["/bin/true"]])
        self.assertNotIn("stopped", printed)
        self.assertEqual(widget.status, "")
        widget.draw()
        self.assertIn("· group ·", self.screen.find("· group ·"),
                      "the detail line is back to describing the row")


class TestPrintList(Harness):
    def test_the_plain_list_prints_the_rows_and_the_count(self):
        catalogue = self.load(listed("mpv"), FakeSystem(
            repos={"mpv": [("extra", "1-1", "", "")]},
            aur={"mpv-git": dict(AUR_ENTRY, Name="mpv-git")}), variants=True)
        out = io.StringIO()
        inst.print_list(catalogue, out)
        printed = out.getvalue()
        self.assertIn("2 entries, 0 installed", printed)
        self.assertIn("     aur     mpv-git", printed,
                      "an alternative is indented under the name it replaces")

    def test_without_the_variants_none_of_them_are_printed(self):
        catalogue = self.load(listed("mpv"), FakeSystem(
            repos={"mpv": [("extra", "1-1", "", "")]},
            aur={"mpv-git": dict(AUR_ENTRY, Name="mpv-git")}))
        out = io.StringIO()
        inst.print_list(catalogue, out)
        self.assertNotIn("mpv-git", out.getvalue())
        self.assertIn("1 entries, 0 installed", out.getvalue())

    def test_the_real_list_prints_without_a_terminal_or_a_network(self):
        catalogue = self.load(LIST.read_text(), FakeSystem(), verify_aur=False)
        out = io.StringIO()
        inst.print_list(catalogue, out)
        # Counted from the list rather than written down: adding a package is
        # not something a test about printing should have an opinion on.
        listed = sum(len(group.entries) for group in catalogue.groups)
        self.assertIn(f"{listed} entries", out.getvalue())
        self.assertIn("the AUR was not asked", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
