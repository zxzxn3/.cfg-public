#!/usr/bin/env python3
"""Install and remove this machine's wanted packages, from a terminal.

    installer.py          the TUI
    installer.py --list   the same table, no TUI and no terminal needed

packages/packages.toml is the list: functional groups of packages, in the
shape cachyos-pi uses. Where a name comes from is not written down there,
because the system can answer it and the answer moves. In a couple of
seconds, three batch calls and one request answer everything:

    pacman -Qi             what is installed: version, install reason, and
                           the names each package provides
    pacman -Sgg            every repository group and its members
    pacman -Si NAMES...    the repositories carrying each name, in priority
                           order, so the first is what pacman would install
    the AUR's RPC          which of the names the AUR knows

Every name goes to both places, which is how a package the repositories and
the AUR both carry reads as both, and where the vote count comes from. The v
key widens the question to the -bin and -git names of every entry (off by
default - it is a second look, not a different answer).

Asking the AUR itself, rather than a helper on its behalf, is what tells a
name that does not exist anywhere from one that merely could not be looked
up. A helper still installs AUR packages, so it is needed for that and
nothing else.

Nothing here installs anything by itself: every command is shown first and
then run in this same terminal with curses out of the way, so sudo, the
package output and Ctrl-C behave normally. Nothing but the standard library
is needed.
"""

from __future__ import annotations

import argparse
import curses
import json
import locale
import os
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIST_DEFAULT = HERE / "packages" / "packages.toml"

GROUP_KEYS = {"name", "description", "selected", "packages"}
ENTRY_KEYS = {"name", "source", "note"}
SOURCES = ("repo", "group", "aur")

# The two ways a package is commonly published other than by its upstream
# name: a binary build, and a build of the current git master.
VARIANT_SUFFIXES = ("-bin", "-git")

SCAN_TIMEOUT = 60  # seconds per batch call: a slow mirror must not hang the TUI
# The AUR lookup is a plain HTTPS request that sometimes drops the TLS
# connection in bursts, so the retry is bounded by a total budget rather
# than a count: never let a bad network hold the tool past AUR_BUDGET.
AUR_BUDGET = 12.0
AUR_TIMEOUT = 6.0
AUR_ATTEMPTS = 3
AUR_PAUSE = 0.5
AUR_RPC = "https://aur.archlinux.org/rpc/v5/info"

# The repository names that answer to one column: the cachyos -v3 variants
# are one source to anyone choosing a package, and core/extra/multilib are
# the Arch ones. Anything else keeps its own name.
ARCH_REPOS = {"core", "extra", "multilib"}
SOURCE_ORDER = ["cachyos", "arch", "chaotic", "aur"]


def canon_repo(repository: str) -> str:
    """Which column a repository belongs in."""
    if repository == "aur":
        return "aur"
    if repository.startswith("cachyos"):
        return "cachyos"
    if repository == "chaotic-aur":
        return "chaotic"
    if repository in ARCH_REPOS or repository.removesuffix("-testing") in ARCH_REPOS:
        return "arch"
    return repository


# ---------------------------------------------------------------- the list


class DataError(Exception):
    """The list file is not what the schema describes."""


@dataclass
class Entry:
    name: str
    source: str | None  # pinned "repo"/"group"/"aur", or None to ask the system
    note: str


@dataclass
class Group:
    name: str
    description: str
    selected: bool
    entries: list[Entry] = field(default_factory=list)


def load_list(path: Path) -> list[Group]:
    """Parse the TOML list, refusing anything the schema does not describe."""
    with open(path, "rb") as handle:
        try:
            document = tomllib.load(handle)
        except tomllib.TOMLDecodeError as error:
            raise DataError(f"{path}: not valid TOML: {error}") from None

    if set(document) != {"group"}:
        raise DataError(f"{path}: expected only [[group]] tables, found {sorted(document)}")

    groups: list[Group] = []
    seen: dict[str, str] = {}  # name -> where it was listed, to catch a repeat
    for raw in document["group"]:
        unknown = set(raw) - GROUP_KEYS
        if unknown:
            raise DataError(f"{path}: group {raw.get('name')!r} has unknown keys: {sorted(unknown)}")
        for key in ("name", "description", "packages"):
            if key not in raw:
                raise DataError(f"{path}: group {raw.get('name')!r} is missing {key!r}")

        group = Group(str(raw["name"]), str(raw["description"]), bool(raw.get("selected", False)))
        for item in raw["packages"]:
            if isinstance(item, str):
                entry = Entry(item, None, "")
            elif isinstance(item, dict):
                unknown = set(item) - ENTRY_KEYS
                if unknown:
                    raise DataError(f"{path}: {item.get('name')!r} has unknown keys: {sorted(unknown)}")
                if "name" not in item:
                    raise DataError(f"{path}: an entry in group {group.name!r} has no name")
                if item.get("source") not in (None, *SOURCES):
                    raise DataError(f"{path}: {item['name']!r} has an invalid source {item['source']!r}")
                entry = Entry(item["name"], item.get("source"), item.get("note", ""))
            else:
                raise DataError(f"{path}: {item!r} in group {group.name!r} is neither a "
                                "name nor a table of name/source/note")

            # One name, one entry. The header counts names while a group row
            # counts its own entries, so a name listed twice would make the
            # two disagree and quietly hide the mistake.
            if entry.name in seen:
                where = (f"twice in {group.name!r}" if seen[entry.name] == group.name
                         else f"in both {seen[entry.name]!r} and {group.name!r}")
                raise DataError(f"{path}: {entry.name!r} is listed {where}")
            seen[entry.name] = group.name
            group.entries.append(entry)

        if not group.entries:
            raise DataError(f"{path}: group {group.name!r} is empty")
        groups.append(group)
    return groups


def list_names(groups: list[Group]) -> list[str]:
    """Every name, in list order, with duplicates collapsed."""
    seen: dict[str, None] = {}
    for group in groups:
        for entry in group.entries:
            seen.setdefault(entry.name, None)
    return list(seen)


def candidate_variants(names: list[str]) -> dict[str, str]:
    """candidate name -> the entry it would replace, in list order.

    The mapping is built here rather than recovered from the candidate later
    with rsplit, which would be wrong for a name that contains a dash
    (git-filter-repo). Names the list already holds get no candidate.

    A name that already carries a suffix is not skipped. Only the stem is
    stripped off and the suffixes put back on, which is the unambiguous way
    round: bluetuith-bin replaces bluetuith and bluetuith-git."""
    known = set(names)
    found: dict[str, str] = {}
    for name in names:
        stem = name
        for suffix in VARIANT_SUFFIXES:
            if name.endswith(suffix):
                stem = name[: -len(suffix)]
                break
        for candidate in (stem, *(stem + suffix for suffix in VARIANT_SUFFIXES)):
            if candidate != name and candidate not in known:
                found.setdefault(candidate, name)
    return found


# ------------------------------------------------------------- the machine


def run(argv: list[str], timeout: int = SCAN_TIMEOUT) -> subprocess.CompletedProcess:
    """Never raises on a failing command, and never on a timeout either: a
    scan that cannot complete degrades, it does not abort.

    LC_ALL is forced to C because pacman translates its own field names -
    under LANG=zh_CN.UTF-8, `pacman -Si` prints '仓库' where this expects
    'Repository' - so the machine output is read in the one locale it is
    defined in. An install keeps your locale, because that output is for a
    person."""
    environment = dict(os.environ, LC_ALL="C")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=environment)
    except (subprocess.TimeoutExpired, OSError):
        return subprocess.CompletedProcess(argv, 1, "", "unavailable")


def parse_blocks(text: str) -> list[dict[str, str]]:
    """pacman and yay print blank-line separated 'Key : value' blocks."""
    blocks = []
    for raw in text.split("\n\n"):
        fields = {}
        for line in raw.splitlines():
            if " : " in line:
                key, _, value = line.partition(" : ")
                fields[key.strip()] = value.strip()
        if "Name" in fields:
            blocks.append(fields)
    return blocks


def split_provides(value: str) -> list[str]:
    """`yay` -> ['yay']; `betterbird=153.2.0` -> ['betterbird']; None -> []."""
    if not value or value == "None":
        return []
    return [item.split("=", 1)[0].strip() for item in value.split() if item.strip()]


def query_aur(names: list[str]) -> dict[str, dict] | None:
    """What the AUR itself says about these names: {name: entry}. None means
    the question could not be asked at all, which is not the same answer as an
    empty dict - that one means the AUR knows none of them.

    The names go in a form body rather than a query string. As a URL this
    stops fitting once the list is long enough: 400 names came to 12k
    characters and the AUR answered 414, which reaches the caller as the same
    None as a network failure - every entry would read "no source" with
    nothing saying why. The endpoint takes either and answers the same."""
    if not names:
        return {}
    query = urllib.parse.urlencode([("arg[]", name) for name in names])
    request = urllib.request.Request(
        AUR_RPC, data=query.encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    deadline = time.monotonic() + AUR_BUDGET
    for attempt in range(AUR_ATTEMPTS):
        left = deadline - time.monotonic()
        if left <= 0:
            break
        try:
            with urllib.request.urlopen(request, timeout=min(AUR_TIMEOUT, left)) as response:
                payload = json.load(response)
            if payload.get("type") != "multiinfo":
                return None
            return {result["Name"]: result for result in payload.get("results", [])}
        except (OSError, ValueError):
            # Seen in the wild about one attempt in six, as
            # URLError: [SSL: UNEXPECTED_EOF_WHILE_READING].
            if attempt + 1 < AUR_ATTEMPTS:
                time.sleep(min(AUR_PAUSE, max(0.0, deadline - time.monotonic())))
    return None


def aur_meta(entry: dict) -> str:
    """The bits of an AUR entry worth seeing before building it."""
    bits = ["AUR"]
    votes = entry.get("NumVotes")
    if votes is not None:
        bits.append(f"{votes} votes")
    if entry.get("Maintainer"):
        bits.append(f"maintained by {entry['Maintainer']}")
    else:
        bits.append("orphaned")
    if entry.get("OutOfDate"):
        bits.append("flagged out of date")
    return " · ".join(bits)


def lookup_notice(machine: Machine, unchecked: list[str]) -> str:
    """What to say when a lookup did not happen. Both front ends say the same
    thing, and what they say names the lookup that failed rather than blaming
    the AUR for the repositories being unreadable. A lookup nobody asked for
    is named as that too: being told the AUR did not answer would send you
    looking at the network when the answer is --no-aur-check."""
    failed = []
    if not machine.installed_checked:
        detail = f": {machine.installed_problem}" if machine.installed_problem else ""
        failed.append("the local database could not be read in full, so only the "
                      "names of installed packages are known" + detail)
    if not machine.repo_checked:
        detail = f": {machine.repo_problem}" if machine.repo_problem else ""
        failed.append("the repositories could not be read at all" + detail)
    if not machine.aur_checked:
        failed.append("the AUR was not asked" if not machine.aur_asked
                      else "the AUR did not answer")
    if not failed:
        return ""
    if not unchecked:
        return "; ".join(failed)
    listed = ", ".join(unchecked[:3]) + (", …" if len(unchecked) > 3 else "")
    return f"{'; '.join(failed)} · {len(unchecked)} entries have no known source ({listed})"


def cell_width(text: str) -> int:
    """How many terminal cells a string covers. Same as len() except for the
    wide characters (a Chinese description, say) and the zero-width ones, so
    the columns stay lined up whatever the list is written in."""
    total = 0
    for character in text:
        if unicodedata.combining(character):
            continue
        total += 2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
    return total


def clip(text: str, width: int) -> str:
    """As much of the string as fits in that many cells, with an ellipsis when
    there is more of it: a silently cut name reads like a different name."""
    if cell_width(text) <= width:
        return text
    out: list[str] = []
    used = 0
    for character in text:
        size = cell_width(character)
        if used + size > max(0, width - 1):  # one cell reserved for the ellipsis
            break
        out.append(character)
        used += size
    return "".join(out) + ("…" if width > 0 else "")


def fit(text: str, width: int) -> str:
    """Truncate to a cell width, then pad to exactly that width."""
    clipped = clip(text, width)
    return clipped + " " * max(0, width - cell_width(clipped))


def plural(count: int, noun: str) -> str:
    """'1 tick', '2 ticks': the same rule wherever a count is spoken."""
    return f"{count} {noun}{'' if count == 1 else 's'}"


@dataclass
class Record:
    """What one wanted name turns out to be."""

    name: str
    kind: str  # "repo" | "group" | "aur" | "unknown"
    sources: list[str]  # every place that carries it, the one pacman takes first
    version: str  # from the repository, for repo and aur
    size: str  # installed size, when the repository gives one
    description: str
    members: list[str]  # a group's members, otherwise just the name
    installed: list[str]  # members (or the name) that are here
    provided_by: str  # the installed package that provides the name
    note: str  # from the list file
    known: bool  # where it comes from is known, rather than assumed
    aur_note: str  # votes, maintainer and out-of-date flags, for AUR entries
    votes: int | None  # the AUR entry's vote count, when there is one

    @property
    def is_here(self) -> bool:
        return bool(self.members) and len(self.installed) == len(self.members)

    @property
    def is_satisfied(self) -> bool:
        """This name is answered: here, or provided by something that is
        here. Every place that asks the question asks it here, or the header
        and a group row can come out with different answers."""
        return self.is_here or bool(self.provided_by)

    @property
    def winner(self) -> str:
        """The source pacman would take, or "" for a group or an unknown."""
        return self.sources[0] if self.sources else "generic"

    @property
    def state(self) -> str:
        # Installed comes from pacman -Qi and owes nothing to the lookups, so
        # it is answered first: an installed AUR package still reads
        # "installed" when the AUR cannot be reached.
        if self.kind == "group":
            return f"{len(self.installed)}/{len(self.members)} installed" if self.installed else "not installed"
        if self.installed:
            return "installed"
        if self.provided_by:
            return f"via {self.provided_by}"
        if self.kind == "unknown":
            return "not found" if self.known else "no source"
        return "not installed"


class Machine:
    """The batch calls, and the answers that come out of them."""

    def __init__(self, names: list[str], *, verify_aur: bool = True, variants: bool = False):
        # One pass over every installed package answers three questions at
        # once: what is here, which of it was asked for on purpose, and which
        # names are provided by something already here.
        self.installed: dict[str, str] = {}
        self.explicit: set[str] = set()
        self.provides: dict[str, str] = {}
        self.installed_checked = False  # set by _read_installed
        self.installed_problem = ""
        self._read_installed()
        self.groups = self._groups()
        # The -bin/-git names ride along in the same two calls rather than
        # costing a lookup each: measured, the wider -Si went 0.33s -> 0.42s
        # and the request stayed one request. Only when asked for, though -
        # they are a second look at the machine, not the answer to its list.
        self.candidates: dict[str, str] = candidate_variants(names) if variants else {}
        asked = [*names, *self.candidates]
        self.repo_checked = bool(asked)  # set properly by _providers below
        self.repo_problem = ""  # pacman's own words, when it could not answer
        self.providers = self._providers(asked)
        # Every name is put to the AUR, not just the ones no repository has:
        # nine of the fifty here are in both, and "in both" is a fact worth
        # seeing. One HTTPS call either way, a hundred names or nine.
        self.aur_asked = verify_aur
        answer = query_aur(asked) if verify_aur else None
        self.aur: dict[str, dict] = answer or {}
        self.aur_checked = answer is not None
        self.variants = self._alternatives()

    def _read_installed(self) -> None:
        """Who is here, and - when pacman says - why and what each name covers.

        -Qi answers all three in one pass. When it cannot be read at all, -Q
        still answers the one that matters most, so "installed" stays a fact
        rather than becoming a guess. The other two go missing, and that is
        said out loud instead of invented: calling every package explicit, and
        letting nothing provide anything, would read on screen as a machine
        where a name is absent when it is really covered by another name -
        which is a package this tool would then offer to install again.
        """
        done = run(["pacman", "-Qi"])
        blocks = parse_blocks(done.stdout)
        for fields in blocks:
            name = fields["Name"]
            self.installed[name] = fields.get("Version", "")
            if fields.get("Install Reason", "").startswith("Explicitly"):
                self.explicit.add(name)
            for provided in split_provides(fields.get("Provides", "")):
                self.provides.setdefault(provided, name)
        if blocks:
            self.installed_checked = True
            return
        # Nothing from -Qi. -Q still tells us who is here, and nothing else.
        complaints = [line for line in done.stderr.splitlines() if line.strip()]
        self.installed_checked = not complaints
        self.installed_problem = complaints[0] if complaints else ""
        for line in run(["pacman", "-Q"]).stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                self.installed[parts[0]] = parts[1]

    def _groups(self) -> dict[str, list[str]]:
        """Every repository group and its members, from one call. -Sgg prints
        'group member'; a group that two repositories carry (fcitx5-im is in
        both cachyos-extra-v3 and extra) prints its members twice, hence the
        deduplication."""
        groups: dict[str, list[str]] = {}
        for line in run(["pacman", "-Sgg"]).stdout.splitlines():
            parts = line.split()
            if len(parts) == 2:
                members = groups.setdefault(parts[0], [])
                if parts[1] not in members:
                    members.append(parts[1])
        return groups

    def _alternatives(self) -> dict[str, list[str]]:
        """name -> the -bin and -git names that turned out to exist, in the
        order they were asked about. One that exists in neither place is
        dropped here, so the list only ever grows by what is really there."""
        found: dict[str, list[str]] = {}
        for candidate, base in self.candidates.items():
            if candidate in self.providers or candidate in self.aur:
                found.setdefault(base, []).append(candidate)
        return found

    def _providers(self, names: list[str]) -> dict[str, list[dict[str, str]]]:
        """name -> the repositories that carry it, in the priority order
        pacman itself uses, so the first one is the one pacman would pick.

        An unusable sync database answers "package 'x' was not found" just
        like a name that is genuinely absent, so the return code says nothing.
        What separates the two is everything else pacman complains about - a
        missing database, a lock, a corrupted file. A lookup therefore counts
        as having happened only when the only complaint is about names, or
        every repository package would quietly come out as an AUR one."""
        found: dict[str, list[dict[str, str]]] = {}
        if names:
            done = run(["pacman", "-Si", *names])
            for fields in parse_blocks(done.stdout):
                found.setdefault(fields["Name"], []).append(fields)
            complaints = [line for line in done.stderr.splitlines()
                          if line.strip() and "was not found" not in line]
            # A complaint alone is not failure: one repository without a
            # downloaded database still lets the others answer. It is having
            # nothing to show *and* a complaint that means nothing was read.
            self.repo_checked = bool(found) or not complaints
            if not self.repo_checked and complaints:
                # Report pacman's own words: a stale lock, an unsynced
                # database and a corrupt file need different fixes.
                self.repo_problem = complaints[0]
        return found

    # --- what a name is ---------------------------------------------------

    def describe(self, entry: Entry) -> Record:
        name = entry.name
        providers = self.providers.get(name, [])
        pinned = entry.source

        aur = self.aur.get(name, {})
        sources: list[str] = []
        kind, version, size = "unknown", "", ""
        description, members, aur_note = "", [name], ""
        if name in self.groups and pinned in (None, "repo", "group"):
            kind, members = "group", self.groups[name]
        elif providers:
            kind = "repo"
            for block in providers:
                column = canon_repo(block.get("Repository", ""))
                if column not in sources:
                    sources.append(column)
            if aur:
                # In the AUR as well, and worth saying so: this is where the
                # vote count comes from, and for a package pacman would take
                # from a repository the AUR entry is the alternative.
                sources.append("aur")
            winner = providers[0]
            version = winner.get("Version", "")
            size = winner.get("Installed Size", "")
            description = winner.get("Description", "")
        elif aur:
            kind, sources = "aur", ["aur"]
            version = aur.get("Version", "")
            description = aur.get("Description", "")
            aur_note = aur_meta(aur)
        elif pinned == "aur" or not self.aur_checked:
            # The list says AUR, or nothing could be asked: either way this is
            # left as an AUR package for the helper to have the last word on,
            # with no column ticked, because that is a guess and not a fact.
            kind = "aur"

        installed = [member for member in members if member in self.installed]
        provided_by = "" if installed else self.provides.get(name, "")
        return Record(
            name=name,
            kind=kind,
            sources=sources,
            version=version,
            size=size,
            description=description,
            members=members,
            installed=installed,
            provided_by=provided_by,
            note=entry.note,
            # Known means the sources above are an answer rather than a guess:
            # for an AUR entry that needs the AUR to have listed it, and for
            # an unknown one it needs the repositories to have answered at all.
            known=self._known(kind, aur),
            aur_note=aur_note,
            votes=aur.get("NumVotes"),
        )

    def _known(self, kind: str, aur: dict) -> bool:
        if kind == "aur":
            return bool(aur)
        if kind == "unknown":
            return self.repo_checked and self.aur_checked
        return True

    @property
    def orphans(self) -> list[str]:
        return run(["pacman", "-Qdtq"]).stdout.split()


def aur_helper() -> str | None:
    return shutil.which("yay") or shutil.which("paru")


# ----------------------------------------------------------- the catalogue


@dataclass(frozen=True)
class Alternative:
    """A -bin or -git name found at run time, drawn under the entry it may
    replace. It is ticked and installed exactly like an entry, but it is not
    one: the list only ever holds what was chosen on purpose."""

    name: str
    base: str


# One line of the table: which kind it is, and the thing behind it. Spelled
# out once, because four methods pass rows around and only one of them cares
# that an alternative is not an entry.
Row = tuple[str, Group | Entry | Alternative]


class Catalogue:
    """The list, joined to what the machine says about it."""

    def __init__(self, path: Path, *, verify_aur: bool = True, variants: bool = False):
        self.path = path
        self.groups = load_list(path)
        self.entries = {entry.name: entry for group in self.groups for entry in group.entries}
        self.machine = Machine(list(self.entries), verify_aur=verify_aur, variants=variants)
        self.records = {name: self.machine.describe(entry) for name, entry in self.entries.items()}
        # The -bin/-git names the machine turned out to have: not entries of
        # the list, but chosen like one, so they get records of the same shape.
        self.variants: dict[str, list[str]] = self.machine.variants
        self.variant_records = {name: self.machine.describe(Entry(name, None, ""))
                                for names in self.variants.values() for name in names}
        # Looked for and shown are two different things: hiding them needs no
        # second scan, and showing them after a scan that skipped them does.
        self.variants_searched = variants
        self.variants_shown = variants
        # A name and its -bin/-git alternative replace each other, so pacman
        # refuses both in one command; a tick on one clears the family.
        self.alternatives: dict[str, set[str]] = {}
        for name, variants in self.variants.items():
            family = {name, *variants}
            for member in family:
                self.alternatives[member] = family - {member}

    def __getitem__(self, name: str) -> Record:
        record = self.records.get(name)
        return record if record is not None else self.variant_records[name]

    @property
    def listed(self) -> list[Record]:
        """Everything the view holds. The header, the source columns and the
        footer are all measured against this, so none of them can disagree
        with what is on screen."""
        if not self.variants_shown:
            return list(self.records.values())
        return [*self.records.values(), *self.variant_records.values()]

    @property
    def count(self) -> tuple[int, int]:
        """(here, listed): how many of the view's names this machine has, out of
        how many the view holds. Showing the -bin/-git rows inflates the
        second number without changing the first."""
        records = self.listed
        return sum(1 for record in records if record.is_satisfied), len(records)

    def ticked_groups(self) -> list[Group]:
        return [group for group in self.groups if group.selected]

    @property
    def unchecked(self) -> list[str]:
        """Entries whose source is a guess rather than an answer, because one
        of the two lookups did not happen. Their installed state is still a
        fact from pacman and is shown as one; only the source is missing."""
        return [name for name, record in self.records.items() if not record.known]

    def detail(self, record: Record) -> str:
        bits = [record.name]
        if record.kind == "group":
            bits.append(f"a repository group of {len(record.members)}: " + ", ".join(record.members))
        elif record.sources:
            bits.append("from " + " then ".join(record.sources))
        elif not self.machine.aur_checked:
            bits.append("in no repository, and the AUR was not asked")
        else:
            bits.append("in no repository and not in the AUR")
        here = self.machine.installed.get(record.name)
        if here:
            bits.append("installed " + here)
            # Which of the two, when pacman was readable enough to say. With
            # only -Q behind us there is no answer to this, and saying
            # "as a dependency" would be one.
            if self.machine.installed_checked:
                bits.append("explicit" if record.name in self.machine.explicit
                            else "as a dependency")
        elif record.provided_by:
            bits.append(f"provided by {record.provided_by}")
        elif record.version:
            bits.append(record.version)
            if record.size:
                bits.append(record.size)
        if record.aur_note:
            bits.append(record.aur_note)
        elif record.kind == "aur" and not record.known:
            bits.append("AUR: not looked up")
        if record.note:
            bits.append(record.note)
        if record.description:
            bits.append(record.description)
        return "  ·  ".join(bits)


# --------------------------------------------------------------- the plans


def expand(catalogue: Catalogue, names: list[str]) -> list[str]:
    """A ticked group stands for its members, so a group name is never handed
    to pacman (which would be an error)."""
    out: dict[str, None] = {}
    for name in names:
        record = catalogue[name]
        for member in record.members:
            out.setdefault(member, None)
    return list(out)


def install_plan(catalogue: Catalogue, names: list[str], upgrade: bool) -> tuple[list[list[str]], list[str]]:
    """(commands, problems). Nothing that is already here is named again."""
    todo = [name for name in names if not catalogue[name].is_satisfied]
    problems = []
    if not todo:
        return [], problems  # nothing to do, so no system upgrade on the way to it

    unknown = sorted({name for name in todo if catalogue[name].kind == "unknown"})
    if unknown:
        problems.append("no repository and no AUR entry by this name: " + ", ".join(unknown))

    # Groups are expanded here: pacman wants the members, and a member is a
    # repository package by definition, not an entry of the list.
    repository: dict[str, None] = {}
    aur: dict[str, None] = {}
    for name in todo:
        record = catalogue[name]
        if record.kind == "group":
            # Only the members that are missing. A group that is half
            # installed is not satisfied, so it lands here, and naming the
            # half that is already present would contradict the promise this
            # function opens with. --needed makes it harmless either way,
            # which is what let it go unnoticed.
            for member in record.members:
                if member not in catalogue.machine.installed:
                    repository.setdefault(member, None)
        elif record.kind == "aur":
            aur.setdefault(name, None)
        elif record.kind == "repo":
            repository.setdefault(name, None)
    if aur and not aur_helper():
        problems.append("no AUR helper is installed, and these are AUR packages: " + ", ".join(aur))
    if problems:
        return [], problems

    commands = []
    if upgrade:
        commands.append(["sudo", "pacman", "-Syu"])
    if repository:
        commands.append(["sudo", "pacman", "-S", "--needed", *repository])
    if aur:
        commands.append([aur_helper() or "yay", "-S", "--needed", *aur])
    return commands, []


def remove_plan(catalogue: Catalogue, names: list[str]) -> tuple[list[list[str]], list[str]]:
    """(command, problems). Only what is here, and only the members that are."""
    here: dict[str, None] = {}
    for name in expand(catalogue, names):
        if name in catalogue.machine.installed:
            here.setdefault(name, None)
    if not here:
        return [], ["nothing ticked is installed, so there is nothing to remove"]
    return [["sudo", "pacman", "-Rns", *here]], []


# ------------------------------------------------------------------- the TUI


PAIRS = {"title": 1, "group": 2, "ok": 3, "missing": 4, "bad": 5, "dim": 6, "warn": 7}

HELP = [
    ("↑ ↓  j k", "move"),
    ("PgUp PgDn", "move a page"),
    ("g / G", "top / bottom"),
    ("space enter", "tick or untick the row; a group ticks its packages"),
    ("a / x", "tick every package in the list / clear every tick"),
    ("i", "install what is ticked"),
    ("r", "uninstall what is ticked"),
    ("/", "filter; enter keeps it, esc clears it"),
    ("f", "show only what is not installed"),
    ("v", "look for -bin and -git names too, and draw the ones that exist; "
          "off by default, because turning it on costs a second scan"),
    ("U", "upgrade first when installing: pacman -Syu, on by default"),
    ("R", "scan the system again"),
    ("● ○ (n)", "the repositories a package is in: ● is the one pacman would take, "
                "and (n) is the AUR entry's vote count"),
    ("?", "this list"),
    ("q", "quit"),
]

# Two footers: the whole thing, and a short one that keeps the keys nobody
# would guess and points at ? for the rest. This used to be a ladder of nine
# spellings of the same thing, one per width - nine places to edit whenever a
# key was added, and two of the rungs were unreachable, because MIN_WIDTH
# stops the drawing before a window ever gets that narrow.
FOOTER_KEYS = "↑↓ move · space tick · a all · x clear · i install · r remove · U Syu · / filter · f missing · v builds · ? help · q quit"
FOOTER_SHORT = "space tick · i install · ? keys · q quit"


class Tui:
    def __init__(self, screen, path: Path, *, verify_aur: bool = True, variants: bool = False):
        self.screen = screen
        self.path = path
        self.verify_aur = verify_aur
        self.catalogue: Catalogue | None = None
        self.seeded = False
        self.ticked: set[str] = set()
        self.filter = ""
        self.filtering = False
        self.only_missing = False
        self.variants = variants  # look for -bin/-git names, off unless asked for
        self.upgrade_first = True
        self.cursor = 0
        self.offset = 0
        self.status = ""
        self.helping = False
        self.has_colours = False
        self.rows: list[Row] = []

    # --- drawing ----------------------------------------------------------

    def pair(self, name: str) -> int:
        return curses.color_pair(PAIRS[name]) if self.has_colours else 0

    def put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        height, width = self.screen.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= width:
            return
        try:
            self.screen.addstr(y, x, clip(text, max(0, width - x - 1)), attr)
        except curses.error:
            pass

    def put_right(self, y: int, text: str, attr: int = 0) -> None:
        _, width = self.screen.getmaxyx()
        self.put(y, max(0, width - cell_width(text) - 2), text, attr)

    def bar(self, y: int, attr: int = 0) -> None:
        _, width = self.screen.getmaxyx()
        self.put(y, 0, " " * max(0, width - 1), attr)

    def visible_rows(self) -> int:
        return max(1, self.screen.getmaxyx()[0] - 6)

    def too_small(self) -> bool:
        """The window cannot hold the table, so no key acts on it: a list
        nobody can see is not something to tick or install from."""
        height, width = self.screen.getmaxyx()
        return width < self.MIN_WIDTH or height < self.MIN_HEIGHT

    def draw(self) -> None:
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if self.too_small():
            self.put(0, 0, f"terminal too small: {width}x{height}, "
                           f"{self.MIN_WIDTH}x{self.MIN_HEIGHT} needed", curses.A_BOLD)
            self.put(1, 0, "q quits", self.pair("dim"))
            self.screen.refresh()
            return
        if self.helping:
            self.draw_help()
            self.screen.refresh()
            return

        left = f" Package installer · {self.catalogue.path.name}"
        here, total = self.catalogue.count
        # Syu is spelled out in both states: showing nothing when it is off
        # makes the key look like it did nothing.
        right = (f"{here}/{total} installed"
                 f" · {len(self.ticked)} ticked"
                 f" · Syu {'on' if self.upgrade_first else 'off'}")
        self.bar(0, curses.A_REVERSE | self.pair("title"))
        title = curses.A_REVERSE | self.pair("title")
        if cell_width(left) + cell_width(right) + 3 <= width - 1:
            self.put(0, 0, left, title | curses.A_BOLD)
            self.put_right(0, right + " ", title)
        else:
            # No room for both: the counts are the part worth keeping, and the
            # name of the tool is already on the line the user typed.
            self.put(0, 0, f" {right} ", title | curses.A_BOLD)

        notice = self.notice()
        if notice:
            colour = "warn" if self.catalogue.unchecked else "dim"
            self.put(1, 0, " " + notice, self.pair(colour) | curses.A_BOLD)

        self.rows = self.build_rows()
        self.draw_header(2, self.layout())
        first, last = 3, height - 4
        self.clamp(len(self.rows), self.visible_rows())
        self.draw_list(first, last)
        self.draw_detail(height - 3)
        self.draw_footer(height - 1)
        self.screen.refresh()

    def notice(self) -> str:
        """Show lookup failures before the active filter."""
        failed = lookup_notice(self.catalogue.machine, self.catalogue.unchecked)
        if failed:
            return failed + " · R rescans"
        if self.filter:
            return "filter: " + self.filter
        return ""

    def build_rows(self) -> list[Row]:
        """The visible list: a group header, then the entries under it that
        survive the filter, then the -bin/-git alternatives under those."""
        needle = self.filter.lower()
        rows: list[Row] = []
        for group in self.catalogue.groups:
            shown = [entry for entry in group.entries if self.shows(entry, group, needle)]
            if not shown:
                continue
            rows.append(("group", group))
            for entry in shown:
                rows.append(("entry", entry))
                rows.extend(("alternative", alternative)
                            for alternative in self.alternatives_of(entry, needle))
        return rows

    def alternatives_of(self, entry: Entry, needle: str) -> list[Alternative]:
        """The -bin/-git names of one entry, as far as the filter and 'only
        what is not installed' let them through."""
        if not self.catalogue.variants_shown:
            return []
        rows = []
        for name in self.catalogue.variants.get(entry.name, []):
            if needle and needle not in name.lower():
                continue
            record = self.catalogue[name]
            if self.only_missing and record.is_satisfied:
                continue
            rows.append(Alternative(name, entry.name))
        return rows

    def shows(self, entry: Entry, group: Group, needle: str) -> bool:
        if needle and needle not in entry.name.lower() and needle not in group.name.lower():
            return False
        record = self.catalogue[entry.name]
        # "not installed" means not here at all: a name some installed package
        # provides is already answered, so it is not something left to do.
        return not (self.only_missing and record.is_satisfied)

    def tick_box(self, group: Group) -> str:
        pushed = [entry.name in self.ticked for entry in group.entries]
        if all(pushed):
            return "[x]"
        return "[~]" if any(pushed) else "[ ]"

    def state_attr(self, record: Record) -> int:
        # Colour follows the state, not the source: an installed package is
        # green whatever the lookups managed to say about where it comes from,
        # and only a package that is neither here nor placeable is a problem.
        if record.is_satisfied:
            return self.pair("ok")
        if not record.known:
            return self.pair("warn")
        if record.kind == "unknown":
            return self.pair("bad")
        return self.pair("missing")

    # --- the columns ------------------------------------------------------
    #
    # Every row is laid out against the same edges, so the state text sits in
    # one column whatever the names and sources are:
    #
    #      > [x] Group name                    3/3 installed   · · · ·
    #          [ ] package-name                installed       ● · · ·
    #              [ ] package-name-bin        not installed   ○ · · ·
    #
    # Where a row's checkbox and name start, per kind: a package under its
    # group, an alternative one step further under the package it replaces.
    NAME_X = 7  # the reference the name column is measured from
    INDENT = {"group": (3, 7), "entry": (5, 9), "alternative": (8, 12)}
    DEEPEST_X = 12
    STATE_WIDTH = 20
    MIN_WIDTH = 40  # below this the columns cannot be told apart
    MIN_HEIGHT = 8  # below this there is no room for the list and its footer
    COLUMN_GAP = 2  # spaces kept between one column's widest mark and the next
    MARK_MARGIN = 4  # the smallest space a name keeps before its state text

    def layout(self) -> dict[str, int]:
        """The x of every column, worked out once per draw. The repository
        matrix is dropped when there is no room for a readable name.

        Each column is as wide as its own label or its widest mark, so the aur
        column can carry a vote count without pushing the others apart."""
        _, width = self.screen.getmaxyx()
        sources = self.columns()
        widths = self.column_widths(sources)
        matrix = sum(widths)
        state_x = width - 2 - matrix - self.STATE_WIDTH
        if state_x - (self.DEEPEST_X + 3) < self.MARK_MARGIN or not sources:
            sources, widths, matrix = [], [], 0
            state_x = width - 2 - self.STATE_WIDTH
        return {
            "name_x": self.NAME_X,
            "name_width": max(8, state_x - self.NAME_X - 3),
            "state_x": state_x,
            "state_width": self.STATE_WIDTH,
            "sources": sources,
            "widths": widths,
            "matrix_x": width - 2 - matrix,
        }

    def columns(self) -> list[str]:
        """One column per source anything on screen has, in priority order."""
        seen = {source for record in self.catalogue.listed for source in record.sources}
        ordered = [source for source in SOURCE_ORDER if source in seen]
        return ordered + sorted(seen - set(SOURCE_ORDER))

    def column_widths(self, sources: list[str]) -> list[int]:
        widths = []
        for source in sources:
            widest = cell_width(source)
            for record in self.catalogue.listed:
                if source in record.sources:
                    widest = max(widest, cell_width(self.mark(record, source)))
            widths.append(widest + self.COLUMN_GAP)
        return widths

    def column_x(self, layout: dict, index: int) -> int:
        return layout["matrix_x"] + sum(layout["widths"][:index])

    def mark(self, record: Record, source: str) -> str:
        """A dot for the source pacman would take, a ring for one it would
        not, and the AUR's vote count after either: how many people run that
        entry is the thing worth knowing before building it."""
        dot = "●" if source == record.winner else "○"
        if source == "aur" and record.votes is not None:
            return f"{dot} ({record.votes})"
        return dot

    def draw_header(self, y: int, layout: dict) -> None:
        """The repository names, over the columns their marks fall in."""
        for index, source in enumerate(layout["sources"]):
            self.put(y, self.column_x(layout, index),
                     fit(source, layout["widths"][index] - self.COLUMN_GAP),
                     self.pair("dim"))

    def draw_row(self, y: int, layout: dict, kind: str, marker: str, box: str, name: str,
                 state: str, record: Record | None, attr: int, state_attr: int) -> None:
        box_x, name_x = self.INDENT[kind]
        self.put(y, 1, marker, attr)
        self.put(y, box_x, box, attr)
        width = layout["name_width"] - (name_x - layout["name_x"])
        self.put(y, name_x, fit(name, max(4, width)), attr)
        self.put(y, layout["state_x"], fit(state, layout["state_width"]), attr | state_attr)
        if record is None:
            return
        for index, source in enumerate(layout["sources"]):
            if source not in record.sources:
                continue
            # The one pacman would take is the bright mark; the others say the
            # package is here too, which is the point of the whole column set.
            chosen = source == record.winner
            self.put(y, self.column_x(layout, index), self.mark(record, source),
                     attr | (self.pair("ok") if chosen else self.pair("dim")))

    def draw_list(self, first: int, last: int) -> None:
        layout = self.layout()
        for y in range(first, last + 1):
            position = self.offset + y - first
            if position >= len(self.rows):
                break
            kind, item = self.rows[position]
            chosen = position == self.cursor
            attr = curses.A_REVERSE if chosen else 0
            marker = ">" if chosen else " "
            if chosen:
                # Paint the whole row first: the fields are written over it,
                # and the gaps between them would otherwise stay unpainted and
                # break the highlight into pieces.
                self.bar(y, curses.A_REVERSE)
            if kind == "group":
                here = sum(1 for entry in item.entries if self.catalogue[entry.name].is_satisfied)
                self.draw_row(y, layout, kind, marker, self.tick_box(item), item.name,
                              f"{here}/{len(item.entries)} installed", None,
                              attr | curses.A_BOLD | self.pair("group"), self.pair("dim"))
            else:
                record = self.catalogue[item.name]
                box = "[x]" if item.name in self.ticked else "[ ]"
                self.draw_row(y, layout, kind, marker, box, item.name, record.state, record, attr,
                              self.state_attr(record))

    def draw_detail(self, y: int) -> None:
        _, width = self.screen.getmaxyx()
        self.put(y, 0, "─" * max(0, width - 1), self.pair("dim"))
        if self.status:
            self.put(y, 0, " " + self.status, self.pair("warn") | curses.A_BOLD)
            return
        current = self.current()
        if current is None:
            self.put(y, 0, " nothing matches", self.pair("dim"))
            return
        kind, item = current
        if kind == "group":
            text = f"{item.name} · group · {item.description}"
        elif kind == "alternative":
            # The fact worth having comes first, because the line is clipped
            # at the window edge and the description is the longest part.
            text = (f"an alternative build of {item.base}: pacman takes one of "
                    f"the two, not both  ·  {self.catalogue.detail(self.catalogue[item.name])}")
        else:
            text = self.catalogue.detail(self.catalogue[item.name])
        self.put(y, 0, " " + text, self.pair("dim"))

    def draw_footer(self, y: int) -> None:
        if self.filtering:
            self.put(y, 0, f" filter: {self.filter}", curses.A_BOLD)
            # cell_width, not len: the cursor has to sit after the text on
            # screen, and a filter may hold a character two cells wide.
            self.put(y, 9 + cell_width(self.filter), "_")
            return
        _, width = self.screen.getmaxyx()
        keys = FOOTER_KEYS
        if cell_width(keys) + 1 > width - 1:
            keys = FOOTER_SHORT
        self.put(y, 0, " " + keys, self.pair("dim"))

    def draw_help(self) -> None:
        height, width = self.screen.getmaxyx()
        self.put(0, 0, " Package installer", curses.A_BOLD)
        # The way out sits on the last line, so a window too short to list
        # every key still says how to leave - the same reason the confirm
        # screen keeps its question in view.
        last = height - 1
        for index, (key, what) in enumerate(HELP):
            y = 2 + index
            if y >= last:
                break
            self.put(y, 3, key.ljust(14), self.pair("group") | curses.A_BOLD)
            self.put(y, 18, what[: max(0, width - 19)])
        self.put(last, 3, "press any key to go back", self.pair("dim"))

    # --- moving -----------------------------------------------------------

    def clamp(self, total: int, visible: int) -> None:
        if total == 0:
            self.cursor = self.offset = 0
            return
        self.cursor = max(0, min(self.cursor, total - 1))
        if self.cursor < self.offset:
            self.offset = self.cursor
        if self.cursor >= self.offset + visible:
            self.offset = self.cursor - visible + 1

    def move(self, delta: int) -> None:
        self.cursor += delta
        self.clamp(len(self.rows), self.visible_rows())
        self.status = ""

    def current(self) -> Row | None:
        if not self.rows:
            return None
        return self.rows[min(self.cursor, len(self.rows) - 1)]

    # --- ticking ----------------------------------------------------------

    def row_names(self, kind: str, item: Group | Entry | Alternative) -> list[str]:
        return [entry.name for entry in item.entries] if kind == "group" else [item.name]

    def toggle(self) -> None:
        current = self.current()
        if current is None:
            return
        names = self.row_names(*current)
        push = not all(name in self.ticked for name in names)
        if push:
            # A -bin or -git package replaces the name it sits under instead
            # of joining it, so ticking one has to untick the other: pacman
            # would refuse the two of them in one command.
            for name in names:
                self.ticked -= self.catalogue.alternatives.get(name, set())
        for name in names:
            self.ticked.add(name) if push else self.ticked.discard(name)
        self.status = ""

    def tick_shown(self, push: bool) -> None:
        """Tick or untick everything in the list, not just what the filter is
        letting through: 'all' and 'clear' should mean what they say, and a
        half-empty list because of a forgotten filter would be a trap."""
        if push:
            self.ticked = set(self.catalogue.entries)
            # The -bin/-git rows are deliberately not part of 'all' - one
            # from each family is what all means - so a tick that was already
            # on one of them goes, or all would hand pacman both mpv and
            # mpv-git and be refused as a conflict.
            self.ticked -= set(self.catalogue.variant_records)
        else:
            self.ticked = set()
        self.status = ""

    def ticked_names(self) -> list[str]:
        """The ticks in list order. Alternatives come last: they have no place
        of their own in the list, so the command follows the list's order and
        then whatever was picked from the -bin/-git ones."""
        order = [*self.catalogue.entries, *self.catalogue.variant_records]
        return [name for name in order if name in self.ticked]

    # --- the loop ---------------------------------------------------------

    def loop(self) -> int:
        self.has_colours = self.setup_colours()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.screen.keypad(True)
        self.load()
        while True:
            self.draw()
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if not self.handle(key):
                return 0

    def handle(self, key) -> bool:
        if self.too_small():
            # Nothing is on screen to act on, so no key acts on the list. q
            # still quits: a window too small to draw must not be a trap.
            return key not in ("q", "\x03", "\x04")
        if self.helping:
            self.helping = False
            return True
        if self.filtering:
            return self.handle_filter(key)
        if isinstance(key, str):
            if key in ("\n", "\r", " "):
                self.toggle()
            elif key in ("q", "\x03", "\x04"):
                return False
            elif key == "j":
                self.move(1)
            elif key == "k":
                self.move(-1)
            elif key == "g":
                self.cursor = 0
            elif key == "G":
                self.cursor = len(self.rows) - 1
            elif key == "a":
                self.tick_shown(True)
            elif key == "x":
                self.tick_shown(False)
            elif key == "f":
                self.only_missing = not self.only_missing
                self.cursor = 0
            elif key == "v":
                self.toggle_variants()
            elif key == "U":
                self.upgrade_first = not self.upgrade_first
            elif key == "/":
                self.filtering = True
            elif key == "\x1b" and self.filter:
                self.filter = ""
                self.cursor = 0
                self.status = ""
            elif key == "?":
                self.helping = True
            elif key == "i":
                self.act("install")
            elif key == "r":
                self.act("remove")
            elif key == "R":
                self.rescan()
            return True
        if key == curses.KEY_UP:
            self.move(-1)
        elif key == curses.KEY_DOWN:
            self.move(1)
        elif key == curses.KEY_PPAGE:
            self.move(-self.visible_rows())
        elif key == curses.KEY_NPAGE:
            self.move(self.visible_rows())
        elif key == curses.KEY_HOME:
            self.cursor = 0
        elif key == curses.KEY_END:
            self.cursor = len(self.rows) - 1
        elif key == curses.KEY_RESIZE:
            self.clamp(len(self.rows), self.visible_rows())
        return True

    def handle_filter(self, key) -> bool:
        if isinstance(key, str):
            if key in ("\n", "\r"):
                self.filtering = False
            elif key == "\x1b":
                self.filtering = False
                self.filter = ""
            elif key in ("\x7f", "\b"):
                self.filter = self.filter[:-1]
            elif key.isprintable():
                self.filter += key
        elif key == curses.KEY_BACKSPACE:
            self.filter = self.filter[:-1]
        self.cursor = 0
        return True

    # --- doing something --------------------------------------------------

    def load(self) -> None:
        """Scan with the screen already up, so the two seconds it takes are
        explained rather than looking like a hang. Ticks survive a rescan; the
        list's pre-ticked groups are read the first time only.

        The list is a file that can be edited between scans, so a scan that
        cannot read it says so and keeps the table it had. A tick whose name
        the new list no longer holds goes with it, or the header would count
        ticks that are nowhere on screen."""
        self.message("scanning the system"
                     + (", the -bin and -git names included" if self.variants else "")
                     + " …")
        try:
            catalogue = Catalogue(self.path, verify_aur=self.verify_aur, variants=self.variants)
        except (DataError, OSError) as error:
            if self.catalogue is None:
                raise  # nothing on screen yet: say it on the terminal instead
            # The fact first: a path and a TOML parse error are long enough to
            # fill the line on their own, and the line is clipped at its end.
            self.status = f"the list was not rescanned · {error}"
            return
        self.catalogue = catalogue
        if not self.seeded:
            self.seeded = True
            self.ticked = {entry.name for group in self.catalogue.ticked_groups()
                           for entry in group.entries}
        known = set(self.catalogue.entries) | set(self.catalogue.variant_records)
        dropped = len(self.ticked - known)
        self.ticked &= known
        self.status = (f"{plural(dropped, 'tick')} dropped: no longer in the list"
                       if dropped else "")

    def toggle_variants(self) -> None:
        """v: look for -bin and -git names, or stop showing what was found.

        The first turn-on is what costs the extra scan; after that the answers
        are in hand and it is instant. Turning it off drops any tick that was
        on a hidden row, or i would install something invisible."""
        if self.variants:
            dropped = len(self.ticked & set(self.catalogue.variant_records))
            self.ticked -= set(self.catalogue.variant_records)
            self.variants = False
            self.catalogue.variants_shown = False
            if dropped:
                self.status = (f"-bin/-git packages hidden · "
                               f"{plural(dropped, 'tick')} dropped with them")
            else:
                self.status = "-bin/-git packages hidden · v shows them again without another scan"
            return
        self.variants = True
        if self.catalogue.variants_searched:
            self.catalogue.variants_shown = True
            self.status = ""
            return
        self.load()
        self.cursor = 0

    def rescan(self) -> None:
        self.load()
        self.cursor = 0

    def message(self, text: str) -> None:
        """One line outside the normal flow, for the wait before a scan."""
        self.screen.erase()
        self.bar(0, curses.A_REVERSE | self.pair("title"))
        self.put(0, 0, " Package installer", curses.A_REVERSE | curses.A_BOLD)
        self.put(self.screen.getmaxyx()[0] // 2, 2, text, curses.A_BOLD)
        self.screen.refresh()

    def act(self, what: str) -> None:
        names = self.ticked_names()
        if not names:
            self.status = f"nothing is ticked, so there is nothing to {what}"
            return
        if what == "install":
            commands, problems = install_plan(self.catalogue, names, self.upgrade_first)
        else:
            commands, problems = remove_plan(self.catalogue, names)
        if problems:
            self.status = "; ".join(problems)
            return
        if not commands:
            self.status = f"nothing to {what}: everything ticked is already in that state"
            return
        if not self.confirm(what, commands):
            self.status = f"{what} cancelled"
            return
        self.execute(commands)

    def confirm(self, what: str, commands: list[list[str]]) -> bool:
        """Show the commands before running them. The terminal about to be
        handed to pacman is this same one, so this is the last chance to
        change your mind."""
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        self.put(0, 0, f" {what}", curses.A_BOLD | self.pair("title"))
        self.put(2, 2, "These commands will run in this terminal:", curses.A_BOLD)
        for index, command in enumerate(commands):
            self.put(4 + index, 4, shlex.join(command)[: max(0, width - 6)])
        # The question is the one line that has to be on screen whatever the
        # window is: the keys that answer it are the only way out.
        self.put(min(6 + len(commands), height - 1), 2,
                 "run them?  y / n", curses.A_BOLD | self.pair("warn"))
        self.screen.refresh()
        while True:
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if isinstance(key, str):
                if key in ("y", "Y", "\n", "\r"):
                    return True
                if key in ("n", "N", "q", "\x1b", "\x03"):
                    return False

    def execute(self, commands: list[list[str]]) -> None:
        """Step out of curses so the commands own the terminal: the sudo
        password prompt, pacman's own questions and Ctrl-C all work because
        nothing is sitting between them and the tty."""
        curses.def_prog_mode()
        failure = ""
        try:
            curses.endwin()
            sys.stdout.write("\n")
            for command in commands:
                sys.stdout.write("  >> " + shlex.join(command) + "\n")
                sys.stdout.flush()
                result = subprocess.call(command)
                if result != 0:
                    sys.stdout.write(f"\nstopped: exit {result}\n")
                    # Said on the table as well. What is on this terminal
                    # scrolls away with it, and a tick that is still there
                    # would otherwise be the only thing left of a run that
                    # changed nothing.
                    failure = f"stopped on {shlex.join(command)}: exit {result}"
                    break
            sys.stdout.write("\npress enter to come back to the installer ")
            sys.stdout.flush()
            try:
                input()
            except EOFError:
                pass
        finally:
            curses.reset_prog_mode()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.screen.keypad(True)
            self.screen.clear()
        self.rescan()
        if failure:
            # After the rescan, which sets a status of its own when a tick
            # was dropped with a list that changed.
            self.status = f"{failure} · the rest was not run" + (
                f" · {self.status}" if self.status else "")

    # --- colours ----------------------------------------------------------

    def setup_colours(self) -> bool:
        if not curses.has_colors():
            return False
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(PAIRS["title"], curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(PAIRS["group"], curses.COLOR_CYAN, -1)
            curses.init_pair(PAIRS["ok"], curses.COLOR_GREEN, -1)
            curses.init_pair(PAIRS["missing"], curses.COLOR_YELLOW, -1)
            curses.init_pair(PAIRS["bad"], curses.COLOR_RED, -1)
            curses.init_pair(PAIRS["dim"], curses.COLOR_WHITE, -1)
            curses.init_pair(PAIRS["warn"], curses.COLOR_MAGENTA, -1)
            return True
        except curses.error:
            return False


# -------------------------------------------------------------------- plain


def print_list(catalogue: Catalogue, out=sys.stdout) -> None:
    """The same facts without a screen: for a pipe, a script, or a machine
    with no terminal to hand."""

    def line(record: Record, indent: str) -> None:
        note = f"  # {record.note}" if record.note else ""
        sources = " then ".join(record.sources) or "-"
        if record.votes is not None:
            sources += f" ({plural(record.votes, 'vote')})"
        out.write(f"{indent}{record.kind:7} {record.name:30} {record.state:20} {sources}{note}\n")

    for group in catalogue.groups:
        out.write(f"\n{group.name} - {group.description}\n")
        for entry in group.entries:
            line(catalogue[entry.name], "  ")
            if catalogue.variants_shown:
                # The -bin/-git names are under the name they would replace,
                # the way the TUI draws them.
                for name in catalogue.variants.get(entry.name, []):
                    line(catalogue[name], "      ")
    notice = lookup_notice(catalogue.machine, catalogue.unchecked)
    if notice:
        out.write("\n" + notice + "\n")
        if catalogue.unchecked:
            out.write("  " + ", ".join(catalogue.unchecked) + "\n")
    here, total = catalogue.count
    out.write(f"\n{total} entries, {here} installed, "
              f"{len(catalogue.machine.orphans)} orphans\n")


# --------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", default=str(LIST_DEFAULT), help=f"the list (default: {LIST_DEFAULT})")
    parser.add_argument("--list", action="store_true", help="print the list and its state, no TUI")
    parser.add_argument("--variants", action="store_true",
                        help="also look for -bin and -git names (the TUI's v key)")
    parser.add_argument("--no-aur-check", action="store_true",
                        help="skip the AUR lookup, so the scan works offline")
    args = parser.parse_args(argv)

    path = Path(args.file).expanduser()
    if not path.exists():
        print(f"{path}: no such list", file=sys.stderr)
        return 2

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    try:
        load_list(path)  # a malformed list should fail on the terminal, not inside curses
    except DataError as error:
        print(error, file=sys.stderr)
        return 2

    if args.list:
        print_list(Catalogue(path, verify_aur=not args.no_aur_check, variants=args.variants))
        return 0

    if os.geteuid() == 0:
        print("run this as the user, not as root: pacman asks for sudo itself, and an "
              "AUR helper refuses to run as root.", file=sys.stderr)
        return 2
    if not sys.stdout.isatty():
        print("no terminal here; --list prints the same thing without one.", file=sys.stderr)
        return 2

    verify = not args.no_aur_check
    try:
        return curses.wrapper(
            lambda screen: Tui(screen, path, verify_aur=verify, variants=args.variants).loop())
    except DataError as error:
        # The list became unreadable between the check above and the scan that
        # opens the TUI. curses.wrapper has already put the terminal back.
        print(error, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
