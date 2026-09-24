# Contributing to DeepProf

DeepProf connects local course evidence, controlled teaching policy, learner-state estimates and an auditable Runtime. Contributions should keep those boundaries reviewable.

## Before opening an issue or pull request

- Reproduce the change with a test or a small, non-sensitive fixture.
- Keep A/B groups independent of cross-question BKT state.
- Keep C-group learner updates tied to reliable, versioned grading evidence.
- Preserve EvidenceRef source version and page-level provenance.
- Report constructed fixtures, human-reviewed cases and student data as distinct data classes.

## Checks

Run the relevant Python tests and, for CLI or SDK changes, the CLI type check and tests. Avoid paid provider calls in tests. Update documentation and generated experiment outputs when a source data definition changes.

## Data and credentials

Do not commit credentials, local SQLite files, learner answers, raw provider responses, textbook scans or answer sheets. Issue templates and pull requests should include only redacted output and fixture data that is authorized for sharing.

## Pull requests

Keep changes focused, explain the before/after behavior and record how it was checked. Call out failures and limitations rather than hiding them in summary metrics.
