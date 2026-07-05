# Reports

Quality and conformance reports are published here automatically on every push
to `main`.

## Published Reports

The `main` workflow publishes:

- [pytest HTML report](pytest/report.html)
- [coverage HTML report](coverage/index.html)
- [coverage JSON](coverage/coverage.json)
- [conformance matrix](conformance.html) — Appendix B vectors §15.1–§15.38
- [scripted examples](examples.html) — every `examples/*/run.sh --scripted` run
- tool summaries for ruff, mypy, bandit, and pip-audit

Reports are stamped with the commit SHA and build date on the index page.

## Local Preview

After a local `make check`, generate the same report pages with:

```bash
uv run python scripts/build_reports.py --reports-dir reports-out --site-reports-dir site/reports
```

The static site build copies this stub; the `main` workflow overwrites
`site/reports/index.html` with the generated report hub during deployment.
