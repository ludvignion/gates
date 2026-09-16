# Interfaces

Paths at another system's edge — a schema, a serialized shape, an interface a partner or another
service reads. `scripts/triage.py`'s check 2 routes a brief `full` when a path it names reaches
one of these globs, parsed the same way as `docs/domain-pack/charter.md`'s `Applies to:` line
(`fnmatch.fnmatchcase`; `*` crosses `/`).

Add an item in the charter's own shape:

    ## <n>. <title>
    Applies to: <glob>[, <glob>...]

No items yet — this project's first interface or schema glob lands with a future ticket.
