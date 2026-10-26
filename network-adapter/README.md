# Network Adapter

Proposes an **egress recovery playbook** for RedAmon's agent, as a change set a
person approves. Built for operators who run their own egress layer (a separate
MCP server the operator deploys and governs, outside RedAmon) so that repeated
403/429 during brute-force and scanning workflows leads to a governed IP
rotation instead of the agent giving up — or worse, improvising one.

## What it does

One action, started from the plugin's page (**Network Adapter** in the nav):

- `propose_playbook` reads the factory text of a built-in skill's part
  (`brute_force_credential_guess` / `wordlist_guidance` by default) and
  **proposes** appending the recovery loop to it:

  > work in small batches → after each batch read the route once with the egress
  > status tool → report what was seen, never a conclusion (`ambiguous_block`,
  > `rate_limit`, `auth_denied`…) → request recovery **once** with a stable
  > idempotent `request_id` → poll until `enforcement_confirmed` → only then the
  > next batch; 429 means wait the Retry-After seconds, never rotate; a single
  > 403 is not an IP block; co-riders re-establish long-lived connections after
  > a rotation they did not request.

- Nothing reaches any agent until an administrator approves the proposal in the
  review UI; **Reset to factory** always restores the original text.

## What it reads, stores and controls

- **Reads:** the factory text of the target skill part, through
  `override:skill.text`'s `get`. Nothing else — no session data, no transcript.
- **Stores:** nothing. The proposal lives in the platform's change-set review,
  not in plugin data.
- **Controls:** nothing. The playbook refers to egress tools (`egress_status`,
  observations, recovery requests) that belong to the operator's separately
  deployed egress MCP server; this plugin never talks to it, holds no
  credentials, and changes no network state. The decision to rotate an exit is
  the egress layer's, under the operator's policy — the playbook only teaches
  the agent the disciplined loop.

## Permissions

- `override:skill.text` — the only permission: read a built-in skill's factory
  text and propose a new version of it, which a person approves.

The playbook text is **target-agnostic**: it is written once, holds for any
target, and contains nothing learned from a session.
