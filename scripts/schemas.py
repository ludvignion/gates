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
- ``Packet``          — ``traces/verdict/<id>.input.md``: the one input of the single-call
                         verdict. Frontmatter names the seat (``arm``) and the ``output`` path;
                         the body is ``## `` sections, and the arm says which sections exist.
- ``Verdict``, ``VerdictMeta`` — ``traces/verdict/<id>.json`` and its optional ``meta`` stamp
                         (arm, vendor, plugin_version, prompt_sha, packet_sha).
"""
import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

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
    rules: tuple[tuple[str, str], ...] = ()  # (field, the derivation written after ``#`` on its line)
    values: tuple[tuple[str, str], ...] = ()  # (signal key, value)

    @classmethod
    def parse(cls, text: str) -> "RoutingStamp":
        if not text.startswith("---"):
            return cls()
        head = text.split("---", 2)[1]
        signals: list[str] = []
        values: list[tuple[str, str]] = []
        rules: list[tuple[str, str]] = []
        has_signals = False
        fields = {"scrutiny": "", "backend": ""}
        in_signals = False
        for line in head.splitlines():
            m = _FM_KEY_RE.match(line)
            if not m:
                continue
            raw = m.group("val")
            key, val = m.group("key"), raw.split("#", 1)[0].strip()
            if m.group("indent"):
                if in_signals:
                    signals.append(key)
                    values.append((key, val))
                continue
            in_signals = key == "signals"
            has_signals = has_signals or in_signals
            if key in fields:
                fields[key] = val
                rules.append((key, raw.split("#", 1)[1].strip() if "#" in raw else ""))
        return cls(signals=tuple(signals), has_signals=has_signals, rules=tuple(rules), values=tuple(values), **fields)

    def rule(self, field: str) -> str:
        return next((r for f, r in self.rules if f == field), "")

    def missing(self) -> tuple[str, ...]:
        out = []
        if not self.has_signals:
            out.append("signals")
        else:
            out.extend(f"signals.{s}" for s in ROUTING_SIGNALS if s not in self.signals)
        out.extend(f for f in ROUTING_FIELDS if not getattr(self, f))
        return tuple(out)


# --- Verdict packet ---------------------------------------------------------------------
# The packet is the seam between the harness and whichever vendor renders the verdict. Its
# frontmatter is the seat; its sections are the evidence; ARMS says which sections each seat
# gets. verdict_prep.py renders it, verdict_eval.py re-renders an archived one under another
# arm, and both go through Packet.
ARMS = ("blind", "packet", "repo")
DEFAULT_ARM = "packet"
PACKET_SECTIONS = (
    "Instructions", "Seat", "Acceptance criteria", "Out of scope", "Human waivers (Log)",
    "Plan: verdict must attack", "Charter items", "Previous verdict: blocks to re-run", "CI",
    "Mechanical findings (copy verbatim into findings)",
    "Tests added on this branch that name an AC", "Diff stat", "Diff",
)
# Blind is diff + prompt: nothing derived from the ticket, plan, charter, or a previous verdict.
BLIND_SECTIONS = ("Instructions", "Seat", "CI", "Diff stat", "Diff")
BLIND_FRONTMATTER_DROPPED = ("status", "writes", "ticket_file")
SEAT_LINE = {
    "blind": "Blind seat: this packet holds the diff and CI only. No ticket, plan, charter, or "
             "previous verdict is available, so a block cannot cite `ac` or `charter`; write the "
             "citation only when the diff itself shows it, and expect uncited blocks to be "
             "downgraded to warns at close-out.",
    "packet": "Packet seat: judge from this packet alone.",
    "repo": "Repo seat: you may read the repository read-only to check a claim the packet leaves "
            "open. No edits, no commands that change the tree.",
}


@dataclass(frozen=True)
class Packet:
    """``traces/verdict/<id>.input.md`` as frontmatter plus ordered ``## `` sections."""

    fm: dict
    sections: tuple[tuple[str, str], ...]  # (title, text) in packet order; preamble excluded

    @classmethod
    def parse(cls, text: str) -> "Packet":
        import _fm  # local: keeps schemas importable from the hooks without sys.path games

        fm, body = _fm.parse(text)
        secs = tuple((t, x) for t, x in sections(body) if t)
        return cls(fm=fm, sections=secs)

    @classmethod
    def load(cls, path: Path) -> "Packet":
        return cls.parse(path.read_text(encoding="utf-8"))

    @property
    def arm(self) -> str:
        return self.fm.get("arm") or DEFAULT_ARM

    @property
    def ticket(self) -> str:
        return str(self.fm.get("ticket", ""))

    def section(self, title: str) -> str:
        return next((x for t, x in self.sections if t == title), "")

    def rearm(self, arm: str, **fm_updates: str) -> "Packet":
        """The same evidence under another seat. ``blind`` drops sections and frontmatter keys;
        nothing can be added back, so blind → packet/repo raises."""
        if arm not in ARMS:
            raise ValueError(f"unknown arm {arm!r}; one of {ARMS}")
        if self.arm == "blind" and arm != "blind":
            raise ValueError("a blind packet cannot be widened: the withheld sections are gone")
        keep = BLIND_SECTIONS if arm == "blind" else PACKET_SECTIONS
        fm = {k: v for k, v in self.fm.items() if arm != "blind" or k not in BLIND_FRONTMATTER_DROPPED}
        fm.update({"arm": arm, **fm_updates})
        secs = tuple(
            (t, "\n" + SEAT_LINE[arm] + "\n\n" if t == "Seat" else x)
            for t, x in self.sections if t in keep
        )
        return Packet(fm=fm, sections=secs)

    def render(self) -> str:
        head = "\n".join(f"{k}: {v}" for k, v in self.fm.items())
        title = f"# Verdict packet — ticket {self.ticket} ({self.arm} seat)\n\n"
        body = "".join(f"## {t}\n{x.lstrip(chr(10)) if x.strip() else '- none' + chr(10) + chr(10)}" for t, x in self.sections)
        return f"---\n{head}\n---\n{title}{body}"


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


# --- Verdict -------------------------------------------------------------------------------
# ``traces/verdict/<id>.json``: what the reviewer wrote, plus the ``meta`` stamp the close-out
# adds. runner.py, render_verdict.py, lint_kanban.py and verdict_prep.py all load through
# Verdict; nobody indexes the JSON by hand. ``meta`` is optional so verdicts written before the
# stamp still load.
DECISIONS = ("ship", "reject")
SEVERITIES = ("block", "warn", "note")
STATUSES = ("open", "resolved")
META_FIELDS = ("arm", "vendor", "plugin_version", "prompt_sha", "packet_sha")
META_OPTIONAL = ("cost_usd", "tokens", "seconds", "opik")  # what the runner knew about the call; null when it did not


@dataclass(frozen=True)
class VerdictMeta:
    """Which seat produced the verdict: the seam metadata, one value per META_FIELDS, plus the
    call's cost and token usage when the vendor prints them (claude --output-format json)."""

    arm: str
    vendor: str
    plugin_version: str
    prompt_sha: str
    packet_sha: str
    cost_usd: float | None = None
    tokens: dict | None = None
    seconds: float | None = None  # wall time of the one model call
    opik: str | None = None  # "tracing to <url>" or "untraced (<reason>)", as the runner printed it

    @classmethod
    def from_dict(cls, d: dict | None) -> "VerdictMeta | None":
        if not isinstance(d, dict):
            return None
        num = lambda x: float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None
        tokens = d.get("tokens")
        return cls(**{k: str(d.get(k, "")) for k in META_FIELDS},
                   cost_usd=num(d.get("cost_usd")), tokens=dict(tokens) if isinstance(tokens, dict) else None,
                   seconds=num(d.get("seconds")), opik=str(d["opik"]) if d.get("opik") is not None else None)

    def as_dict(self) -> dict:
        return {**{k: getattr(self, k) for k in META_FIELDS}, **{k: getattr(self, k) for k in META_OPTIONAL}}


@dataclass(frozen=True)
class Verdict:
    """The verdict JSON. ``findings`` keep the finding dicts as written (their shape is fixed by
    verdict-prompt.md); ``raw`` keeps any key this model does not name, so a round trip loses
    nothing."""

    ticket: str = ""
    decision: str = "reject"
    held: tuple[str, ...] = ()
    findings: tuple[dict, ...] = ()
    ci: dict = field(default_factory=dict)
    meta: "VerdictMeta | None" = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Verdict":
        if not isinstance(d, dict):
            raise ValueError("not a verdict JSON object")
        return cls(
            ticket=str(d.get("ticket", "")), decision=str(d.get("decision", "reject")),
            held=tuple(d.get("held") or ()), findings=tuple(f for f in d.get("findings") or () if isinstance(f, dict)),
            ci=dict(d.get("ci") or {}), meta=VerdictMeta.from_dict(d.get("meta")), raw=dict(d),
        )

    @classmethod
    def parse(cls, text: str) -> "Verdict":
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: Path) -> "Verdict":
        return cls.parse(path.read_text(encoding="utf-8"))

    def as_dict(self) -> dict:
        d = dict(self.raw)
        d.update({"ticket": self.ticket, "decision": self.decision, "held": list(self.held),
                  "findings": [dict(f) for f in self.findings], "ci": dict(self.ci)})
        if self.meta:
            d["meta"] = self.meta.as_dict()
        else:
            d.pop("meta", None)
        return d

    def dump(self, path: Path) -> None:
        path.write_text(json.dumps(self.as_dict(), indent=1) + "\n", encoding="utf-8")

    def problems(self) -> tuple[str, ...]:
        """Why this is not a verdict: an unknown decision, or a finding without an id, with an
        unknown severity, or an unknown status. Empty = passes the schema."""
        out = []
        if self.decision not in DECISIONS:
            out.append(f"decision {self.decision!r} not in {DECISIONS}")
        for i, f in enumerate(self.findings):
            label = f.get("id") if isinstance(f.get("id"), str) and f.get("id") else f"findings[{i}]"
            if label == f"findings[{i}]":
                out.append(f"{label} has no id")
            if f.get("severity") not in SEVERITIES:
                out.append(f"{label}: severity {f.get('severity')!r} not in {SEVERITIES}")
            if f.get("status", "open") not in STATUSES:
                out.append(f"{label}: status {f.get('status')!r} not in {STATUSES}")
        return tuple(out)

    @staticmethod
    def is_open(f: dict) -> bool:
        return f.get("status", "open") == "open"

    def open_blocks(self) -> tuple[dict, ...]:
        return tuple(f for f in self.findings if f.get("severity") == "block" and self.is_open(f))

    def blocks(self) -> tuple[dict, ...]:
        return tuple(f for f in self.findings if f.get("severity") == "block")

    @staticmethod
    def cited(f: dict) -> bool:
        return bool(f.get("ac") or f.get("charter"))

    def uncited_blocks(self) -> tuple[dict, ...]:
        return tuple(f for f in self.blocks() if not self.cited(f))

    def with_findings(self, findings: tuple[dict, ...]) -> "Verdict":
        return replace(self, findings=findings)

    def with_meta(self, meta: VerdictMeta) -> "Verdict":
        return replace(self, meta=meta)

    def with_decision(self, decision: str) -> "Verdict":
        return replace(self, decision=decision)
