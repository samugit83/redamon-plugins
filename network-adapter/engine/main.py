from redamon_plugin_sdk import PluginError


EGRESS_PLAYBOOK = """
## Egress recovery loop (network rotation is governed by RedAmon, never by you)

When tool output during this workflow shows connection failures, resets, or repeated 403/401:
1. Work in small batches of attempts. After EACH batch read the route once with the egress status tool:
   - shared route: `egress_status` with `route_handle="proxy-proof"`;
   - per-project route (tool names carry the suffix `__<route_id>`): the suffixed status tool with `route_handle="<route_id>"`.
2. Report what you saw, never a conclusion: the report tool with `outcome` one of
   `ambiguous_block` (403/refused), `rate_limit` (429), `auth_denied` (401), `success`, `before_send_failure`.
   You NEVER assert an IP block; trusted evidence is minted only by the egress side.
3. If status shows a recovery decision or a revision change, request recovery ONCE with a stable
   `request_id` (reuse the SAME id on retries — it is idempotent), then continue.
4. Poll status until `driver.enforcement_confirmed` is true and the revision matches. Only then start the
   next batch; a pending recovery response does NOT prove the dataplane changed.
5. Co-riders: if `route_revision` changed without your request, another agent rotated this route —
   re-establish long-lived connections and tunnels cleanly, then continue.
6. Discipline: 429 means wait the Retry-After seconds, never rotate. A single 403 is not an IP block.
   Use standard network tools (curl) through your assigned route and never hand-close raw sockets.
   Credentials, tokens and ports are per route: never borrow another route's handle.
   If status enters PAUSED or HOLD, stop sending and report to the operator.
"""


async def propose_playbook(ctx, params):
    """Propose appending the egress recovery playbook to a built-in skill part.

    The platform stores a change set for an admin to review and approve; nothing
    reaches any agent until that approval."""
    skill = params.get('skill', 'brute_force_credential_guess')
    part = params.get('part', 'wordlist_guidance')
    current = await ctx.sdk.override.get('skill.text', skill)
    factory = next((p['text'] for p in current.get('parts', []) if p.get('partId') == part), None)
    if factory is None:
        raise PluginError('part_not_found', 'Skill "%s" không có part "%s".' % (skill, part))
    limit = next((p.get('maxChars') for p in current.get('parts', []) if p.get('partId') == part), None)
    value = factory + EGRESS_PLAYBOOK
    if limit is not None and len(value) > limit:
        raise PluginError('playbook_too_large', 'Vượt giới hạn ký tự của part (%d/%d).' % (len(value), limit))
    out = await ctx.sdk.override.propose(
        'skill.text', skill, {part: value},
        note='Network Adapter: nối thêm hướng dẫn vòng egress recovery (batch → status → observation → recovery idempotent → enforcement_confirmed).',
        scope=params.get('scope', 'user'))
    return {'message': 'Đã tạo bản đề nghị chờ duyệt. Sau khi admin duyệt, mọi phiên agent dùng skill %s sẽ nhận playbook.' % skill,
            'reviewProposal': {'extensionPoint': 'skill.text', 'changeSetId': out['changeSetId']},
            'skill': skill, 'part': part, 'addedChars': len(EGRESS_PLAYBOOK)}
