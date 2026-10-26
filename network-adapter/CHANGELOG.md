# Changelog

## [1.0.0]

- First public release. One action: `propose_playbook` proposes appending the
  egress recovery loop (batch → status → observation → idempotent recovery →
  `enforcement_confirmed` → next batch, 429 backs off, co-riders re-establish
  after a rotation) to the `wordlist_guidance` part of the built-in
  `brute_force_credential_guess` skill, as a change set a person approves.
- Permission: `override:skill.text` only. The plugin reads the skill's factory
  text and proposes a new version; it stores nothing and touches no network
  infrastructure — the egress tools the playbook refers to are deployed and
  governed by the operator, outside RedAmon.
