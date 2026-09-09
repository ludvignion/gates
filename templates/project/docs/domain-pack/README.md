# Domain pack

Everything the agents cannot infer about *this* domain. Swappable per project. Read by grill and verdict.

- `charter.md` — non-negotiables for this domain, each with pattern, anti-pattern, demonstrating test.
- `conventions.md` — naming, units, formats, external-system quirks.
- `references/` — specs and docs the grill cites as evidence. The product spec itself lives in
  `docs/spec/` (one file, indexed by `make intake`); a brief's `spec_refs` are its evidence paths.

Nothing in the plugin or template names this domain. Only files here do.
