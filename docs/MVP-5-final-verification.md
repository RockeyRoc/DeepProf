# MVP-5 final verification

Verified on 2026-09-22:

- `python -m pytest -q`: passed. The command requires permission to create Windows temporary directories in this environment.
- `npm run typecheck --prefix apps/cli`: passed.
- `npm run build --prefix apps/cli`: passed.
- `npm run test --prefix apps/cli`: passed (3 tests).
- `npm run typecheck --prefix apps/desktop`: passed.
- `npm run build --prefix apps/desktop`: passed with the approved child-process execution required by esbuild.
- `npm run validate --prefix apps/pet`: passed.
- CLI Mock Runtime smoke: JSON `new -> ask -> resume -> tree -> compact`, including `sequence` and safe transcript output.
- Cross-surface smoke: Desktop-created session resumed by CLI; transcript, surface audit, and provider-default restart persistence verified.
- Evaluation direct checks: TTFT, provider latency, E2E latency, usage/cost, unknown pricing, failure, and multiple model calls passed.

The sandboxed Node test runner can report `spawn EPERM`; the accepted verification run used the approved child-process execution path.
