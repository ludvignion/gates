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
