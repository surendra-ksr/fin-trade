# Evidence Protocol

## Permanent relay rules

- Every relay includes `TOTAL = CORE + ML_ONLY` and the equality is proven by complete,
  per-environment collect-only output. Self-contradicting counts automatically fail.
- Any change to a previously reported number is labeled `CORRECTION:` with before and
  after values. Silent edits are fabrication.
- Continuation/session artifacts belong under `docs/` (never the repository root).

## Atomic Evidence Pack

Before any report, all work MUST be committed and pushed to the session branch. The
working tree MUST be clean (`git status --short` has no output). Every evidence
command MUST run in one shell session against that committed state. Outputs are pasted
unedited: no truncating `head`/`tail`, no elided heredocs, and no narrative substituted
for command output. Any claim without pasted command output is absent; any internal
inconsistency fails the phase.

## Per-phase close-out template

1. `git status --short`, log, and exact HEAD SHA.
2. `wc -l` and docstring greps for every new module.
3. Required demanded-function body dumps.
4. Full `pytest --collect-only -q` output.
5. Two complete suite runs in the pinned clean environment and complete `pip freeze`.
6. No-network test grep.
7. Runtime demos with the complete visible script and stdout.
8. Documentation update proof using `git log -- <document>`.
9. Only after every item is pasted may the phase PR be flipped from draft or called
   merge-safe.

Every commit is pushed immediately after creation. No rebase, squash, amend, filter,
or other history rewrite is permitted after evidence has been reported without an
explicit before/after hash map.
