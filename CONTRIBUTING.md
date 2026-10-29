# Contributing

Use synthetic data only in source, screenshots, tests and issues. Never include NHS credentials, patient records or production database copies.

Before working with new proprietary material, arrange written permission with the maintainer. Public viewing and GitHub platform permissions, the earlier Apache release and third-party licences are unaffected. Read [LICENSE](LICENSE) and [COMMERCIAL.md](COMMERCIAL.md).

Once authorised, run `python -m pip install -e '.[test]'`, `ruff check .` and `pytest`. Set `TEST_DATABASE_URL` only to a disposable PostgreSQL database: tests delete its tables. Add regression tests for changes to clinical transitions, authorisation, identity matching or delivery behaviour.

Discuss code contributions before submitting them. New code contributions must have a separate written agreement establishing the contributor's rights and the maintainer's permission to distribute and commercially license them. Submitting an issue or a pull request does not assign copyright. Do not contribute code whose provenance or licensing is unclear. General feedback and non-sensitive issue reports are welcome.

Explain the problem, resulting behaviour and validation in pull requests. Preserve all applicable copyright and licence notices, including the retained Apache 2.0 notices for earlier material. Changes to intended use, eligibility, prioritisation or clinical instructions need the adopting service's clinical safety review before deployment. Do not describe untested integrations or benefits as established results.
