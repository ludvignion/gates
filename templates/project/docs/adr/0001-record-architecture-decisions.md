# 0001. Record architecture decisions

Status: Accepted
Date: 2026-08-27

## Context
Decisions made in chat are lost. Decisions made in tickets are buried.

## Decision
1. Every non-obvious architectural choice gets an ADR here, numbered, append-only, using the plugin template.
2. The grill's plan lists "Decisions worth an ADR"; the build writes them in the same PR.

## Consequences
- Tickets reference ADR sections (`ADR-0003 §2`), never restate them.
