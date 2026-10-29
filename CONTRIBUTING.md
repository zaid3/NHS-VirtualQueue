# Contributing

Use synthetic data only in source, screenshots, tests and issues. Never include NHS credentials, patient records or production database copies.

The project is open source under [Apache License 2.0](LICENSE). You do not need a paid agreement to use, evaluate, modify or contribute to this release. Optional paid services are described in [COMMERCIAL.md](COMMERCIAL.md).

Run `python -m pip install -e '.[test]'`, `ruff check .` and `pytest`. Set `TEST_DATABASE_URL` only to a disposable PostgreSQL database: tests delete its tables. Add regression tests for changes to clinical transitions, authorisation, identity matching or delivery behaviour.

Discuss substantial changes before submitting them. Unless explicitly stated otherwise or covered by a separate agreement, contributions intentionally submitted for inclusion are offered under Apache 2.0, as described in section 5 of the licence. Submitting an issue or pull request does not assign copyright. Contribute only work you have the right to submit and identify any third-party material and its licence. General feedback and non-sensitive issue reports are welcome.

Explain the problem, resulting behaviour and validation in pull requests. Preserve all applicable copyright and licence notices. Changes to intended use, eligibility, prioritisation or clinical instructions need the adopting service's clinical safety review before deployment. Do not describe untested integrations or benefits as established results.
