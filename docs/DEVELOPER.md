# Developer notes

Run the standard-library test suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```

The tests use local fixtures and a local HTTP server. No live website is needed.

The project intentionally has no mandatory third-party runtime dependency. Install `.[render]` only when selectively rendering JavaScript-heavy pages is required, and install `.[dev]` when using pytest.

The next production steps are provider adapters for permitted search APIs, a richer review queue for uncertain language classifications, and signed Windows packaging. Those additions should preserve the storage, URL-policy, and stop-control contracts already covered by the tests.
