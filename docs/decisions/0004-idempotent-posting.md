# ADR 0004: Idempotent physical postings

Status: Accepted

Inventory movement POST accepts an organization-scoped Idempotency-Key and request fingerprint. Equivalent retries return the original movement; different content conflicts. This prevents a lost response from duplicating physical stock.
