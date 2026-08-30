# My Todo List — architecture (V1.3 modular → V2 online)

## Goals
- **V1.3**: desktop app with local `tasks.json`, same behaviour as the monolith.
- **V2**: online hosted dataset, desktop + mobile clients — without rewriting business rules.

## Layers

```
┌─────────────────────────────────────────┐
│  UI (mytodo.ui)                         │
│  app.py shell + mixins:                 │
│    mindmap / task_list / dialogs        │
│  CustomTkinter desktop only             │
│  Talks to TodoStore + domain helpers    │
└─────────────────┬───────────────────────┘
                  │ load() / save(doc)
┌─────────────────▼───────────────────────┐
│  Storage (mytodo.storage)               │
│  TodoStore ABC                          │
│  JsonFileStore  ← V1                    │
│  ApiStore       ← V2 (future)           │
└─────────────────┬───────────────────────┘
                  │ document dict
┌─────────────────▼───────────────────────┐
│  Domain (mytodo.domain)                 │
│  DATA_VERSION, recurrence, clamps,      │
│  spawn helpers — pure Python, no UI/IO  │
└─────────────────────────────────────────┘
```

## Document schema
Single JSON (or API) document versioned by `DATA_VERSION`.
Migrations live in `mytodo.storage.migrate` so every client sees the same shape.

## How to run
```bash
python todo.py
# or
python -m mytodo
```

## V2 migration path
1. Keep `domain/` stable (or publish as shared package).
2. Implement `ApiStore(TodoStore)` with auth + sync.
3. Desktop: `TodoApp(store=ApiStore(...))`.
4. Mobile: new UI against the same store contract + domain logic.

## Packaging
Point PyInstaller at `todo.py` or `mytodo.__main__`.
`JsonFileStore` resolves `tasks.json` next to the executable (or LocalAppData fallback).
