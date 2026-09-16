# ADR 0001: Modular monolith

Status: Accepted

Run one FastAPI process over one PostgreSQL database with focused feature modules. The first fulfillment flow crosses product, inventory, sales, and production; one transaction boundary keeps rollback and correctness understandable. Split services only after stable scaling or ownership requirements justify it.
