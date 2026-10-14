# Skiller evaluation

How well Skiller turns one session into a better playbook, measured without real target data.
Four synthetic sessions (`sessions/`), each with four planted mistakes and a list of planted
identifiers: three on built-in skills (`sql_injection`, `path_traversal`, `xss`) and, since
Skiller 1.1.0, `user_skill` on one of the person's own skills. The harness runs Skiller on each
through a local RedAmon, exactly as a person would from the chat (picking the skill in RedAmon's
skill picker), and scores the proposal. Nothing here is packed into the plugin.

## Run

Prerequisites, on the RedAmon host:

1. Skiller installed and enabled (the plugin platform is part of every RedAmon). For the
   `user_skill` session: Skiller 1.1.0 on RedAmon plugin platform 1.1.0, granted "Propose versions
   of your own skills".
2. A user who owns a project, with a model chosen for **Skiller** in Settings → Models by feature.
   Any provider works; for a dry run of the harness itself, use the stub model of RedAmon's
   `testing/e2e/scripts/stub-llm-server.mjs` (a custom OpenAI-compatible provider).
3. From this repository's root (the harness, like every tool here, runs in `python:3.11-slim`):

```bash
mkdir -p /tmp/skiller-eval
docker run --rm --network host -u "$(id -u):$(id -g)" \
  -v "$PWD:/repo:ro" -v "<path to redamon>/.env:/env/.env:ro" -v /tmp/skiller-eval:/out \
  -w /repo/skiller/eval python:3.11-slim \
  python run_eval.py --user <user id> --project <project id> --label "<model name>" --out /out/<model>.json
```

For each session the harness creates a new conversation in the project (the internal key from
`.env`), starts *Improve skill from this session* with the session's skill as the pick (a session
cookie signed with `AUTH_SECRET` from `.env`), follows the job, reads the proposal's review,
scores it, and deletes the proposal and the conversation (`--keep` keeps them, to look at them in
the review window). For `user_skill` it first uploads the session's skill as one of the user's
own (its `userSkill`) and deletes it at the end; the user must have room for one more skill. The two
secrets are read from the file and never printed. The exit code is 1 when any proposal leaked a
planted identifier.

To change a session, edit `build_sessions.py` and run it (`python build_sessions.py`, in the
same container image); a test keeps the JSON files equal to the builder.

## Metrics (per model)

| Metric | How it is measured | Target |
|---|---|---|
| JSON valid at the first try | `stats.modelCalls` of the job result: 1 call for diagnosis, at most 1 for edits | both, every session |
| Edits applied | `stats.edits` (kept) and `stats.droppedEdits` (refused by the checks after the repair round) | most kept |
| Template parts valid | the proposal's template parts checked again with Skiller's template rules (RedAmon's validator accepted them already) | always |
| Leaks | every planted identifier searched (case-insensitive) in the added lines, the note, the summary, the changelog and the lessons | **0** |
| Mistakes covered | each planted mistake mapped to the lessons by the rubric below | 3 or 4 of 4 |
| Added characters | the proposal's growth over the version it replaces | small; no target |

## Rubric

A lesson covers a planted mistake when it names the situation and the better action in general
terms. The harness maps lessons to mistakes by keywords (each mistake's `keywords`, matched at
the start of a word); a person then confirms or corrects the mapping in `REPORT.md`:

| Mistake | Covered when a lesson says, in general terms |
|---|---|
| `hang` | which option or setting made the tool hang or time out, and the bounded way to run it (one parameter, fewer threads, a smaller list, a timeout) |
| `repeat` | stop repeating a blocked payload family and change the family or the encoding |
| `verify` | the evidence this technique needs before claiming success (a boolean pair, file content rather than a status code, execution in a browser rather than reflection) |
| `guidance` | the correction the person gave, turned into a rule for any target |

A lesson that cites a host, a path, a parameter or a value from the session is a **failure** even
when it is otherwise right: Skiller should have dropped it, and the leak metric counts it.

## Report

`REPORT.md` holds one section per run: the date, the model, the table the harness prints, and
the person's review of the mapping.
