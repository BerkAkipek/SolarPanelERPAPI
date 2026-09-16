# ADR 0002: PostgreSQL owns ledger invariants

Status: Accepted

Persist immutable movement headers and lines. Derive physical on-hand as incoming minus outgoing in a database view, and derive available separately from active reservations. Constraints, triggers, and locks protect every writer, including future ones.
