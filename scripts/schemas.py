"""Schemas for the artifact shapes the harness relies on. One model per shape.

Scripts load artifacts through these models and never parse the shapes on their own
(AGENTS.md: "Relied-on artifacts have a schema, a section, and a check"). No third-party
dependency: dataclasses + re, so the hooks can import this with a bare python3.

Shapes here:
- ``Plan``            — a ``<n>.plan.md`` body: its ``## Acceptance criteria`` ids and its
                         ``## Slices`` ids.
- ``TicketCitations`` — the ``(plan <n> AC-<x>[, AC-<y>]...)`` groups inside a ticket's
                         ``## Acceptance criteria`` lines. Only AC lines count; prose or log
                         mentions of a plan AC do not.
- ``ClosedTicketDiff`` — what changed on a closed ticket between two versions.
- ``Attacks``, ``Charter``, ``Ticket`` — the verdict's inputs.
- ``Log``, ``Finding``   — a ticket's ``## Log`` as role-tagged entries; its ``— finding:``
                         entries and the ticket ids each is homed to.
- ``RoutingStamp``    — a plan's ``signals``/``scrutiny``/``backend`` frontmatter stamp.
"""
import re
from dataclasses import dataclass, field

AC_ID_RE = re.compile(r"\bAC-\d+\b")
_SECTION_RE = re.compile(r"^## (?P<title>.+?)\s*$", re.MULTILINE)
_AC_LINE_RE = re.compile(r"^- (?P<id>AC-\d+)\b")
_SLICE_LINE_RE = re.compile(r"^\s*(?:\d+\.|[-*])\s+`?(?P<id>\d+(?:\.\d+)+)`?(?:\s|$)")
_PAREN_RE = re.compile(r"\(([^()]*)\)")
_PLAN_REF_RE = re.compile(r"\bplan (?P<n>\d+)\b")


def section(body: str, title: str) -> str:
    """Text under ``## <title>`` up to the next ``## `` heading; empty if absent."""
    heads = list(_SECTION_RE.finditer(body))
    for i, h in enumerate(heads):
        if h.group("title").strip().lower() == title.lower():
            end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
            return body[h.end():end]
    return ""


def _items(text: str) -> list[str]:
    """List items in a markdown section; continuation lines fold into their item."""
    items: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\s*(?:\d+\.|[-*])\s+", line):
            items.append(line)
        elif items and line.strip():
            items[-1] += " " + line.strip()
    return items


@dataclass(frozen=True)
class Plan:
    """What ``render_board.py`` needs from a plan body."""

    acs: tuple[str, ...] = ()
    slices: tuple[str, ...] = ()

    @classmethod
    def parse(cls, body: str) -> "Plan":
        acs = tuple(
            m.group("id")
            for item in _items(section(body, "Acceptance criteria"))
            if (m := _AC_LINE_RE.match(item))
        )
        slices = tuple(
            m.group("id")
            for item in _items(section(body, "Slices"))
            if (m := _SLICE_LINE_RE.match(item))
        )
        return cls(acs=acs, slices=slices)


@dataclass(frozen=True)
class AcCitation:
    """One ``(plan <n> AC-x, AC-y ...)`` group as written. Amendments such as
    ``AC-7 as amended by R-2`` still cite AC-7 — the citation counts as written."""

    plan: str
    acs: tuple[str, ...]


@dataclass(frozen=True)
class TicketCitations:
    """Plan-AC citations found in a ticket's ``## Acceptance criteria`` lines."""

    groups: tuple[AcCitation, ...] = field(default_factory=tuple)

    @classmethod
    def parse(cls, body: str) -> "TicketCitations":
        groups: list[AcCitation] = []
        for item in _items(section(body, "Acceptance criteria")):
            if not _AC_LINE_RE.match(item):
                continue
            for paren in _PAREN_RE.findall(item):
                refs = list(_PLAN_REF_RE.finditer(paren))
                for i, ref in enumerate(refs):
                    end = refs[i + 1].start() if i + 1 < len(refs) else len(paren)
                    acs = tuple(dict.fromkeys(AC_ID_RE.findall(paren[ref.end():end])))
                    if acs:
                        groups.append(AcCitation(plan=ref.group("n"), acs=acs))
        return cls(groups=tuple(groups))

    def acs_for(self, plan: str) -> set[str]:
        return {ac for g in self.groups if g.plan == plan for ac in g.acs}


# --- Ticket lifecycle -------------------------------------------------------------------
# A ticket in one of these statuses is closed. Closed tickets are immutable: the write guard
# refuses edits (hooks/guard_writes.py) and lint_kanban.py fails on any change outside the
# append-only sections. New work is a new ticket with depends_on; a human reopens by hand.
CLOSED_STATUSES = ("done", "superseded")
APPEND_ONLY_SECTIONS = ("Findings", "Log")


def sections(body: str) -> list[tuple[str, str]]:
    """``[(title, text)]`` for every ``## `` heading, plus ``("", preamble)`` first."""
    heads = list(_SECTION_RE.finditer(body))
    out = [("", body[: heads[0].start()] if heads else body)]
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        out.append((h.group("title").strip(), body[h.end():end]))
    return out


def _is_append_only(title: str) -> bool:
    return any(title.lower().startswith(t.lower()) for t in APPEND_ONLY_SECTIONS)


@dataclass(frozen=True)
class ClosedTicketDiff:
    """What changed on a closed ticket between two versions. Empty ``violations`` = legal."""

    violations: tuple[str, ...]

    @classmethod
    def compare(cls, old: str, new: str) -> "ClosedTicketDiff":
        out: list[str] = []
        old_s, new_s = dict(sections(old)), dict(sections(new))
        if old.split("---", 2)[:2] != new.split("---", 2)[:2]:
            out.append("frontmatter changed")
        for title in old_s.keys() | new_s.keys():
            a, b = old_s.get(title), new_s.get(title)
            label = f"## {title}" if title else "preamble"
            if a is None:
                out.append(f"{label} added")
            elif b is None:
                out.append(f"{label} removed")
            elif _is_append_only(title):
                if not b.startswith(a.rstrip()):
                    out.append(f"{label} is append-only but earlier text changed")
            elif a != b:
                out.append(f"{label} changed")
        return cls(violations=tuple(sorted(out)))


# --- Verdict inputs ----------------------------------------------------------------------
_CHARTER_RE = re.compile(r"^## (?P<n>\d+)\.\s+(?P<title>.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Attacks:
    """The plan's ``## Verdict must attack`` items, mandatory for every verdict."""

    items: tuple[str, ...] = ()

    @classmethod
    def parse(cls, plan_body: str) -> "Attacks":
        items = tuple(
            re.sub(r"^\s*(?:\d+\.|[-*])\s+", "", it).strip()
            for it in _items(section(plan_body, "Verdict must attack"))
        )
        return cls(items=tuple(i for i in items if i and not i.startswith("<")))


@dataclass(frozen=True)
class Charter:
    """``docs/domain-pack/charter.md``: numbered ``## <n>. <title>`` items."""

    items: tuple[tuple[str, str], ...] = ()  # (n, title)

    @classmethod
    def parse(cls, body: str) -> "Charter":
        return cls(items=tuple((m.group("n"), m.group("title")) for m in _CHARTER_RE.finditer(body)))


@dataclass(frozen=True)
class Ticket:
    """What the verdict needs from a ticket body: nothing from the Log but human waivers."""

    acs: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    waivers: tuple[str, ...] = ()

    @classmethod
    def parse(cls, body: str) -> "Ticket":
        acs = tuple(it[2:] for it in _items(section(body, "Acceptance criteria")) if _AC_LINE_RE.match(it))
        oos = tuple(re.sub(r"^\s*[-*]\s+", "", it) for it in _items(section(body, "Out of scope")))
        log = next((t for title, t in sections(body) if title.lower().startswith("log")), "")
        waivers = tuple(
            m.group(0).strip()
            for m in re.finditer(r"^### \[human\][^\n]*\n(?:(?!###)[^\n]*\n?)*", log, re.MULTILINE)
        )
        return cls(acs=acs, out_of_scope=oos, waivers=waivers)


# --- Kanban lint inputs -------------------------------------------------------------------
# What lint_kanban.py needs beyond ClosedTicketDiff: a ticket's ``## Log`` as role-tagged
# entries, the ``— finding:`` entries and where each is homed, and a plan's routing stamp.
TICKET_ID_RE = re.compile(r"(?<![\w$.])[1-9]\d*\.\d+(?:\.\d+)?(?![\w%]|\.\d)")
_LOG_HEAD_RE = re.compile(r"^### \[(?P<role>[^\]]+)\](?P<rest>[^\n]*)$", re.MULTILINE)
_FINDING_RE = re.compile(r"—\s*finding:\s*(?P<title>.+?)\s*$")
_HOME_RE = re.compile(r"\bhomed?\b[^\n\d]{0,20}(?P<id>[1-9]\d*\.\d+(?:\.\d+)?)", re.IGNORECASE)
_SHIP_RE = re.compile(r"—\s*ship\b")
_ID_TOKEN_RE = re.compile(r"\b[A-Z]\d+\b")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("`", "").strip().rstrip(".")).lower()


@dataclass(frozen=True)
class LogEntry:
    """One ``### [role] <timestamp> — <what>`` heading plus the lines under it."""

    role: str
    head: str  # the heading text after ``[role]``
    text: str  # heading and body, as written

    def names(self, needle: str) -> bool:
        return _norm(needle) in _norm(self.text)


@dataclass(frozen=True)
class Finding:
    """A ``— finding:`` Log entry. ``homes`` are the ticket ids after an explicit ``home: <id>``
    or ``homed to <id>`` marker; nothing else homes a finding. ``candidates`` are the other
    ticket ids in the entry, offered as a hint when there is no marker."""

    title: str
    entry: LogEntry
    homes: tuple[str, ...]
    candidates: tuple[str, ...] = ()

    @classmethod
    def from_entry(cls, entry: LogEntry, own_id: str) -> "Finding | None":
        m = _FINDING_RE.search(entry.head)
        if not m:
            return None
        homes = tuple(dict.fromkeys(h.group("id") for h in _HOME_RE.finditer(entry.text) if h.group("id") != own_id))
        candidates = tuple(dict.fromkeys(i for i in TICKET_ID_RE.findall(entry.text) if i != own_id and i not in homes))
        return cls(title=m.group("title"), entry=entry, homes=homes, candidates=candidates)


@dataclass(frozen=True)
class Log:
    """A ticket's ``## Log`` section as entries. Everything before the first ``###`` is dropped."""

    entries: tuple[LogEntry, ...] = ()

    @classmethod
    def parse(cls, body: str) -> "Log":
        text = next((t for title, t in sections(body) if title.lower().startswith("log")), "")
        heads = list(_LOG_HEAD_RE.finditer(text))
        entries = []
        for i, h in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            entries.append(LogEntry(role=h.group("role"), head=h.group("rest").strip(), text=text[h.start():end].rstrip()))
        return cls(entries=tuple(entries))

    @property
    def text(self) -> str:
        return "\n".join(e.text for e in self.entries)

    def findings(self, own_id: str) -> tuple[Finding, ...]:
        return tuple(f for e in self.entries if (f := Finding.from_entry(e, own_id)))

    def waivers(self) -> tuple[LogEntry, ...]:
        return tuple(e for e in self.entries if e.role == "human")

    def closeouts(self) -> tuple[LogEntry, ...]:
        return tuple(e for e in self.entries if e.role == "build" and "close-out" in e.head)

    def shipped(self) -> bool:
        return any(e.role == "verdict" and _SHIP_RE.search(e.head) for e in self.entries)

    def waived_ids(self) -> set[str]:
        """Finding ids (``F1``, ``C2``) named anywhere in a ``[human]`` entry."""
        return {i for e in self.waivers() for i in _ID_TOKEN_RE.findall(e.text)}

    def addresses(self, title: str) -> bool:
        """A ``[human]`` waiver or a build close-out names the finding."""
        return any(e.names(title) for e in self.waivers() + self.closeouts())

    def mentions(self, needle: str) -> bool:
        return any(e.names(needle) for e in self.entries)


ROUTING_SIGNALS = ("spend", "partner_facing", "parallel_ready", "tickets")
ROUTING_FIELDS = ("scrutiny", "backend")
_FM_KEY_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z_]+):(?P<val>[^\n]*)$")


@dataclass(frozen=True)
class RoutingStamp:
    """The plan frontmatter's routing stamp (grill SKILL.md, Routing): ``signals`` with its four
    keys, plus the derived ``scrutiny`` and ``backend``."""

    signals: tuple[str, ...] = ()  # signal keys present under ``signals:``
    has_signals: bool = False
    scrutiny: str = ""
    backend: str = ""

    @classmethod
    def parse(cls, text: str) -> "RoutingStamp":
        if not text.startswith("---"):
            return cls()
        head = text.split("---", 2)[1]
        signals: list[str] = []
        has_signals = False
        fields = {"scrutiny": "", "backend": ""}
        in_signals = False
        for line in head.splitlines():
            m = _FM_KEY_RE.match(line)
            if not m:
                continue
            key, val = m.group("key"), m.group("val").split("#", 1)[0].strip()
            if m.group("indent"):
                if in_signals:
                    signals.append(key)
                continue
            in_signals = key == "signals"
            has_signals = has_signals or in_signals
            if key in fields:
                fields[key] = val
        return cls(signals=tuple(signals), has_signals=has_signals, **fields)

    def missing(self) -> tuple[str, ...]:
        out = []
        if not self.has_signals:
            out.append("signals")
        else:
            out.extend(f"signals.{s}" for s in ROUTING_SIGNALS if s not in self.signals)
        out.extend(f for f in ROUTING_FIELDS if not getattr(self, f))
        return tuple(out)
