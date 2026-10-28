# Contributing

Use synthetic data only in source, screenshots, tests and issues. Never include NHS credentials, patient records or production database copies.

Run `python -m pip install -e '.[test]'`, `ruff check .` and `pytest`. Set `TEST_DATABASE_URL` only to a disposable PostgreSQL database: tests delete its tables. Add regression tests for changes to clinical transitions, authorisation, identity matching or delivery behaviour.

Explain the problem, resulting behaviour and validation in pull requests. Preserve the Apache 2.0 licence and attribution. Changes to intended use, eligibility, prioritisation or clinical instructions need the adopting service's clinical safety review before deployment. Do not describe untested integrations or benefits as established results.
