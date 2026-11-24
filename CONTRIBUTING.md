# Contributing

```bash
pip install -e ".[dev]"
pytest -q
```

- Keep changes small and add a test for each behaviour change.
- Tests must not touch the network; mock `get` in the source module.
- New literature sources go in `src/papersift/sources/` and register in `sources/__init__.py` with `count`, `search` and `with_title`.
- Update `CHANGELOG.md` for user-visible changes.
