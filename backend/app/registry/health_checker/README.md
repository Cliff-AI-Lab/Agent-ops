# Health Checker Component

Owner: TBD / 2026-05

## What
Periodically runs `health_check.fixture_path` for each atom. Updates `health_check.status` (green/yellow/red).

## Phase
Phase 2 (W4-5). Phase 1 atoms are manually marked green at YAML write time.

## Schedule
`every_24h` (cron in Phase 2 deployment).
