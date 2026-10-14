# Changelog

All notable changes to Skiller. Versions follow `x.y.z`; a release is the tag `skiller-v<x.y.z>`.

## [1.1.0] - 2026-10-14

- Works on the person's own skills too (the `.md` workflows they upload), not only built-in
  ones. Needs RedAmon's plugin platform 1.1.0 and the new permission `override:user_skill.text`;
  an instance on 1.0.0 stays on 1.0.0 until an admin accepts it.
- "Improve skill from this session" first opens RedAmon's skill picker: the skills the agent used
  in the session, built-in and your own. Skiller works on the one you pick.
- For one of your skills, Skiller proposes a new text; approving it in RedAmon saves a NEW skill
  (named `<name> vN` by default) and never changes the one it was built on.
- Error codes: `no_skill` replaces `no_builtin_skill`; new `skill_not_found` and
  `skill_contains_credentials` (RedAmon never gives a plugin a skill that holds a key or token,
  such as a private key, a JWT or an API token).

## [1.0.0] - 2026-10-11

- First version: from a finished chat session, picks the built-in skill the agent used, reads
  the version of that skill the agent runs, and proposes a better, session-agnostic next version
  for a person to review in RedAmon. Stores nothing.
