# Skiller

Skiller learns from one finished agent chat session and proposes a better, **session-agnostic**
version of a skill the agent used in it: a built-in skill (an attack playbook, for example SQL
injection) or one of your own skills (a `.md` workflow you uploaded). Nothing changes until a
person approves the proposal in RedAmon.

It adds two items to the chat's gear menu, in a group labelled with the plugin badge:

- **Improve skill from this session**: first opens RedAmon's **skill picker**, which lists the
  skills the agent used in the session, built-in and your own, with how many turns used each.
  You pick one; then the analysis below runs as a background job. The job window first names
  the skill and the version the agent runs now, then shows the stages, and opens RedAmon's
  review window on the proposal when it is ready.
- **Skill versions**: opens RedAmon's versions window for the session's skill (view, review
  again, delete old versions, reset to factory).

## How it works

1. **Read the session.** Refused while the agent is still working in it, or when RedAmon cannot
   confirm that it stopped.
2. **Pick the skill.** The one you picked; without a pick (RedAmon older than 1.1.0 never sends
   one), the skill whose text was in the agent's instructions for the most turns (ties go to the
   latest), counted from the `phase_update` records of the session. Sessions recorded before
   RedAmon logged that (`skill_workflow`) fall back to the skill label, with a warning in the
   analysis that the text may not have been in every labelled turn. A session with fewer than
   three turns or no tool call under the skill is refused.
3. **Read the current version** of that skill, the one the agent runs for this project: for a
   built-in skill the factory text or the approved version, for your own skill its current text.
   If five proposals for the skill already wait for a decision, Skiller stops here, before any
   model call.
4. **Build a digest** of the session: facts, computed signals (failed calls, repeated calls,
   failure streaks, productivity warnings, deep-think triggers, every human correction), a
   compact timeline of the turns under the skill, the work of parallel specialists and of the
   exploit-path search. The whole digest is fenced as untrusted data.
5. **Diagnose** (model call 1): general lessons, each "When <situation>, do <action>, because
   <reason>.", and whether the session fit the skill at all. A session that did not fit ends
   here with no proposal.
6. **Edit** (model call 2): small anchored edits to the current playbook (insert after or
   before a sentence, replace one sentence, append), never a rewrite.
7. **Check** every edit in code: the anchor exists exactly once, growth and deletion limits,
   template placeholders intact, and a **leak guard** on the added text (below). Problems get one
   repair round; edits that still fail are dropped and counted.
8. **Propose** the new version as a complete snapshot (the current version's changes carried
   forward). RedAmon validates it again and stores it as a proposal. If the text changed while
   Skiller worked (a RedAmon update, or you editing your own skill), the same edits are redone
   once on the new text, with no new model call.

Your own skill reaches the agent another way than a built-in one (its whole text, in every phase,
under "User Attack Skill"), so the diagnosis uses a prompt that says so; everything else is the
same.

## What it reads

- The chat session it was started from, through `session.read`: the agent's events and what
  people wrote. RedAmon masks credential-shaped values first; **target hosts, addresses and
  findings stay readable**, because a session without them cannot be analysed.
- The skill's text and RedAmon's limits, through `override.get`: for a built-in skill its factory
  text and the version that applies; for your own skill its current text. RedAmon gives Skiller
  only your own skills that this session used, and never one whose name, description or text
  holds a key or token (a private key, a JWT, an API token; remove it from the skill to use
  Skiller on it).
- The model the user chose for Skiller in **Settings → Models by feature**; calls go through
  RedAmon and count against Skiller's daily limit, not the user's.

## What it stores

**Nothing of the session.** The transcript, the digest and the model's evidence stay in memory
for the duration of the job. Skiller writes no plugin data. The only thing it leaves behind is the
proposal, held by RedAmon: the new text of the changed parts, a note (summary and changelog for
the reviewer), and metadata with the generalized lessons, the guard's counts, the session id it
came from and the model used.

## Keeping the skill session-agnostic

A playbook runs against every future target, so nothing specific to this session may enter it:

- both prompts forbid hostnames, addresses, URLs, paths, parameter names and values, file and
  user names, credentials, tokens, flags and target-specific facts;
- lessons that contain any identifier seen in the session are dropped before the edit stage, and
  the edit stage never sees the evidence behind a lesson;
- the guard refuses added text that contains an identifier from the session (unless the factory
  text already has it, or it is a documentation placeholder such as `example.com`), names a tool
  the playbook does not already name, tells the agent to send data anywhere, names any URL or
  host, or weakens the scope guardrail, the Rules of Engagement or a confirmation step;
- the session digest is fenced with a random marker and the model is told to treat it as data,
  never as instructions, because it contains text from the target.

## How approval works

RedAmon's review window shows the proposal next to the text it replaces, with the lines that look
risky flagged, and says what approving does in plain words.

**A built-in skill.** Skiller proposes at **user scope**: an approved version applies to every
project the user owns, from the next message or resume (a run in progress keeps the text it
started with). Approving makes it the active version; the previous one is kept as superseded.
**Reset to factory** in the versions window works even if Skiller is later disabled or
uninstalled, and approved versions stop applying while Skiller is not installed and enabled.
Version numbers are never reused.

**Your own skill.** Approving (**Save as new skill**) never changes the skill the proposal was
built on: it saves the proposed text as a **new skill** of yours, exactly as if you had uploaded a
new `.md`.
- Its name is filled in as the old name with the next ` vN` suffix (`My SQLi flow` → `My SQLi flow
  v2`), and you can type another one; names are unique among your skills.
- Opened from a project, the window offers to use the new skill in that project **instead of** the
  old one (on by default); sessions that used the old one continue with the new one from their
  next message or resume.
- The saved skill is an ordinary skill of yours: listed in Global Settings, toggled per project,
  downloadable, deletable. It stays if Skiller is disabled or uninstalled.
- Lines flagged as risky must be acknowledged before saving.
- Saving needs Skiller enabled and at the version that made the proposal; a proposal from an older
  or unavailable version is run again, not saved.
- If the plugin catalog revokes the Skiller version that proposed a skill you saved, RedAmon turns
  that skill off everywhere, and every version saved from it since, and quarantines them until you
  read them and release them in Global Settings.

## Limits

- One job per session at a time; the job may run up to 600 seconds and makes two to four model
  calls (a retry when the answer is not valid JSON, and one repair round).
- At most five proposals per skill waiting for a decision.
- Per edit at most 4,000 characters; per part at most max(1,500, 25 % of the part) added and 600
  removed by a replacement; RedAmon's per-part and whole-skill limits always apply (for your own
  skill, 51,200 characters counted as RedAmon counts them, in UTF-16 code units).
- The session digest is capped at 120,000 characters: unflagged turns shrink to one line first,
  then the middle unflagged turns are dropped (and counted).

## Errors you may see

| Code | Meaning |
|---|---|
| `session_running` | The agent is still working in this session; try again when it stops. |
| `session_state_unverified` | RedAmon could not confirm that the agent stopped; try again in a moment. |
| `no_skill` | The session used no skill Skiller can work on (an unclassified request, or a skill whose text was never in the prompt). |
| `skill_not_used` | The picked skill was not used in this session. |
| `skill_not_found` | The skill is no longer one Skiller can read for this session (deleted, quarantined, not yours, or not a skill RedAmon offers). |
| `skill_contains_credentials` | Your skill holds a key or token (a private key, a JWT, an API token), so RedAmon does not give it to Skiller. |
| `session_too_short` | Fewer than three turns, or no tool call, under the skill. |
| `session_format_unsupported` | The session was recorded in a format Skiller cannot read. |
| `too_many_pending` | Five proposals already wait for a decision on this skill. |
| `factory_changed` | The skill's text changed while Skiller worked and its edits no longer fit; run Skiller again. |
| `llm_bad_output` | The model did not answer valid JSON twice in a row. |
| `model_required` | Choose a model for Skiller in Models by feature. |

A result of `no_improvement` is not an error: the session held no lesson worth a new version,
or did not fit the skill.

## Tested against RedAmon

On 2026-10-11, Skiller 1.0.0 was installed from files (signed with a development key) on a
local RedAmon with platform 1.0.0 and driven end to end by RedAmon's
`testing/e2e/tests/pluginsSkiller.spec.ts`, with a scripted stub model: a session analysed from
the chat's gear menu (the job named the skill and "v1" first), the proposal reviewed and
approved, a second run on top of it, the versions window (delete, reset to factory), a third
run numbered after every number already used, the model chooser when no model is set, and
uninstall keeping the data, reinstall restoring the approved version, uninstall deleting
everything. The evaluation harness ran on the same stack (see `eval/REPORT.md`). A real-model
evaluation is still to do.

On 2026-10-13, Skiller 1.1.0 was installed the same way on a local RedAmon with platform 1.1.0,
and the same spec passed with every run started from RedAmon's skill picker. Its new test
improves one of the person's own skills: the picker lists it as "Your skill", the job names it
"(your skill)", the review offers "<name> v2" and refuses a taken name, the saved skill is
switched on in the project in place of its source, and a session on v2 suggests v3, with the
picker and the review checked at phone width. A real-model evaluation of 1.1.0 is still to do.

## Development

```bash
tools/run_tests.sh skiller            # from the repository root, in python:3.11-slim
python tools/validate.py skiller
```

The tests use the platform SDK's `FakeSdk` and `ScriptedLlm` and synthetic sessions only
(`example.com`, `192.0.2.0/24`, `198.51.100.0/24`, `2001:db8::/32`).
