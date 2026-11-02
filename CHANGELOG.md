# Changelog

## 0.1.1 — Unreleased

- Fixed watches remaining without a baseline when Vinted repeats listing IDs across pages or reorders already collected items in the first-page recheck.
- Preserve fail-closed behavior for stalled/full repeated pages, exhausted budgets and recheck IDs outside the collected set. Baselines remain silent and atomic; existing ID/destination suppression history is retained.

## 0.1.0

- Added pinned `vinted-api-kit` Italy/EUR search with strict response/block guards, single-owner SQLite watches and durable Telegram delivery.
- Added MCP stdio and authenticated Streamable HTTP, generic deployment examples and distributable Hermes skill.
- Added conservative paginated coverage, silent atomic baselines, global ID/destination suppression and durable block suspension.
- Completed limited live SDK checks for search, filters, order parameters, page 2, authenticated MCP, silent baseline/rescan and container HTTP on 2026-11-01. Real Telegram delivery and long-term reliability remain unverified.
- Added tested native Linux amd64/arm64 GHCR images and automatic publication after successful master checks.
