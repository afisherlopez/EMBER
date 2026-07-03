# ADR 0001: DuckDB + Parquet over PostGIS

## Status
Accepted

## Context
EMBER is read-only at runtime and accesses precomputed outputs. We need low ops burden and portable data artifacts for local and cloud use.

## Decision
Use DuckDB with Parquet/GeoParquet datasets as the runtime catalog layer.

## Consequences
- No always-on database server.
- Fast local dev and easy fixture-based tests.
- Schema extensibility through tidy metric rows.
- Direct object storage reads with `gcsfs` (native `gs://`, service-account credentials).
