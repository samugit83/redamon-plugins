# Skiller evaluation report

One section per run (README.md, "Run"). Leaks must be 0 in every run.

**Skiller 1.1.0.** The runs below are of Skiller 1.0.0 on the three built-in sessions. 1.1.0 keeps
the built-in prompts byte for byte (a test pins them), so those results stand for it. It adds the
`user_skill` session, on one of the person's own skills: its real-model run is still to do.

## Real models (experiment E6)

### DeepSeek `deepseek/deepseek-flash` (2026-10-11)

On a local RedAmon (platform 1.0.0), with Skiller 1.0.0 installed from the official catalog and
`deepseek/deepseek-flash` chosen for Skiller in Models by feature by the owner's account. Final
run, 17:49 UTC:

| skill | outcome | JSON first try (A/B) | edits applied / dropped | template valid | leaks | mistakes covered | added chars |
|---|---|---|---|---|---|---|---|
| path_traversal | proposed | True/True | 4/0 | True | 0 | 4/4 | 1410 |
| sql_injection | proposed | True/True | 3/0 | True | 0 | 4/4 | 1528 |
| xss | proposed | True/True | 8/0 | True | 0 | 4/4 | 2371 |

- One model call for the diagnosis and one for the edits in every session; no repair round. No
  lesson was dropped by the checks (4, 6 and 7 lessons kept).
- Keyword mapping (`map_lessons`): path_traversal M1→L3, M2→L1, M3→L2, M4→L1; sql_injection
  M1→L2/L6, M2→L1/L4, M3→L1/L3/L5, M4→L1/L3; xss M1→L3, M2→L5/L7, M3→L1/L6, M4→L3/L6/L7. A
  person's review of this mapping is still to do (the proposals were deleted after scoring; re-run
  with `--keep` to read them in the review window).
- The sql_injection proposal had one line the review window flags as risky (`riskFlags` 1): the
  reviewer sees it highlighted before approving.
- **The leak metric was corrected after the first run.** The first full run reported one leak for
  xss, the planted parameter name `lang`, but the harness then matched a substring, so a lesson
  with the word "language" counted. That proposal was deleted, so the hit cannot be checked. A
  re-run of xss with `--keep` had no `lang` anywhere. `leaks()` now uses the whole-identifier
  boundaries of Skiller's own `Identifiers.find`, with a test, and the final run above uses it.
  Skiller lets a plain short parameter name such as `lang` through by design (refusing it would
  refuse ordinary prose), so a real `lang` in a lesson would be counted here, not stopped there.

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
