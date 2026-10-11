# Skiller evaluation report

One section per run (README.md, "Run"). Leaks must be 0 in every run.

## Real models (experiment E6)

Not run yet: it needs a real provider key for the test user (manual checkpoint MC-02 of the
plugin platform plan). The harness is ready: choose the model for Skiller in Models by feature,
then run `run_eval.py` with that model's name as `--label`, and add a section here with the
table it prints and a person's review of the lesson-to-mistake mapping.

## Dry run: the stub model (2026-10-11)

The harness itself, run on a local RedAmon (platform 1.0.0, Skiller 1.0.0 installed from files)
against RedAmon's scripted stub model (`testing/e2e/scripts/stub-llm-server.mjs`), which always
answers one canned lesson and one appended rule. It proves the path (seed, job, progress,
proposal, review, scoring, clean-up), not the quality of any model.

| skill | outcome | JSON first try (A/B) | edits applied / dropped | template valid | leaks | mistakes covered | added chars |
|---|---|---|---|---|---|---|---|
| path_traversal | proposed | True/True | 1/0 | True | 0 | 2/4 | 107 |
| sql_injection | proposed | True/True | 1/0 | True | 0 | 1/4 | 107 |
| xss | proposed | True/True | 1/0 | True | 0 | 1/4 | 107 |

Every job named its skill and "Current version: v1" first, then reached "Saving the proposal".
"Mistakes covered" counts keyword matches of the stub's single canned lesson and says nothing
about a model; a person's review applies only to real-model runs.
