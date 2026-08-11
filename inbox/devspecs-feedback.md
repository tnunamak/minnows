# DevSpecs feedback

## 2026-07-27 — `ds task` scan lock

While creating a bounded three-slice task for the `convo` session-semantics handoff,
`ds task` created task artifacts but its automatic index scan failed with SQLite
`database is locked (5)`. The suggested `--path` scope was already the repository root,
so it did not identify the competing process or provide a retry path. The generated task
artifacts were not used for the implementation and will remain uncommitted.
