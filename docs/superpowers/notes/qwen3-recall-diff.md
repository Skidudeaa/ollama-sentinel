# Qwen3 recall diff (sparse)

Before: `nomic-embed-text`
After:  `qwen3-embedding:4b`

Combined neighbor count across both snapshots: 0 (threshold 50).

The violation DB is too sparse to produce a meaningful before/after comparison. This is acceptable for Phase A — the schema swap stands on its own — but capture a richer diff when the DB has accumulated more findings (e.g. after running the watcher against a real project for a few days).
