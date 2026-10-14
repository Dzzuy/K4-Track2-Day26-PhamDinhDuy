"""Focused CP1 checks for the trusted gateway decision boundary."""

from __future__ import annotations

from time import perf_counter

from agent.gateway import Command, Gateway
from agent.telemetry import RecordingGatewayContext


def context(*, scopes=frozenset({"wiki.read"}), credits=100, round_no=1):
    return RecordingGatewayContext(
        act="learner:sv-0417", sub="agent:vlearn-tutor", scopes=scopes,
        credits=credits, round=round_no, call_index=0,
    )


def command(server="slides", tool="query", *, args=None, fields=(), headers=None, lease_id=None, kind="mcp"):
    return Command(
        cmd_id="cmd:1", kind=kind, raw=f"{server}.{tool}", server=server, tool=tool,
        args=args or {}, fields=fields, headers=headers or {}, lease_id=lease_id, call_index=1,
    )


def test_cross_learner_denied_even_for_tutor_identity():
    gw = Gateway(context())
    result = gw.decide(command(args={"learner": "learner:sv-0392"}))
    assert result.verdict == "deny" and result.call is None
    own = gw.decide(command(args={"learner": "Learner:sv-0417"}))
    assert own.verdict == "rewrite"


def test_write_requires_scope_and_preconditions():
    call = command("progress", "record_mastery", args={"learner": "learner:sv-0417", "anchor": "Learner:sv-0417"})
    assert Gateway(context()).decide(call).verdict == "deny"
    gw = Gateway(context(scopes=frozenset({"wiki.read", "wiki.write:progress"})))
    assert gw.decide(call).verdict == "deny"
    call = command("progress", "record_mastery", args=call.args,
                   headers={"if-match": "etag-1", "idempotency-key": "req-1"})
    assert gw.decide(call).verdict == "forward"
    assert gw.decide(call).verdict == "deny"


def test_live_lease_required_and_valid_lease_forwarded():
    ctx = context()
    gw = Gateway(ctx)
    call = command("slides", "get_frame", args={"anchor": "Frame:abcd/w/001"}, lease_id="lease-1")
    assert gw.decide(call).verdict == "deny"
    ctx.leases = ("lease-1",)
    decision = gw.decide(call)
    assert decision.verdict == "rewrite"
    assert decision.call.lease_id == "lease-1"
    assert decision.call.headers["mcp-replica"] == "w"


def test_catalog_mask_and_deprecated_tool_rewritten():
    gw = Gateway(context())
    catalog = gw.decide(command("registry", "list_servers", fields=("*",)))
    assert catalog.verdict == "rewrite" and catalog.call.fields == ("name",)
    search = gw.decide(command("slides", "search", args={"q": "state"}))
    assert search.verdict == "rewrite" and search.call.tool == "query"


def test_a2a_requires_admitted_skill_and_matching_audience():
    gw = Gateway(context())
    call = command("curriculum-analyst", "which_days_cover", kind="a2a", headers={"aud": "curriculum-analyst"})
    assert gw.decide(call).verdict == "deny"
    gw.note_card("curriculum-analyst", {"verified": True, "skills": ["which_days_cover"]})
    assert gw.decide(call).verdict == "forward"
    wrong = command("curriculum-analyst", "which_days_cover", kind="a2a", headers={"aud": "roster"})
    assert gw.decide(wrong).verdict == "deny"
    gw.note_card("roster", {"verified": True, "skills": ["role_of"]})
    roster = command("roster", "lookup_learner", kind="a2a", args={"learner": "Learner:sv-0417"})
    assert gw.decide(roster).verdict == "forward"


def test_rate_window_is_respected_across_rounds():
    ctx = context()
    gw = Gateway(ctx)
    gw.note_card("citation-checker", {"verified": True, "skills": ["verify_source"]})
    call = command("citation-checker", "verify_source", kind="a2a", args={"anchor": "Source:x"})
    assert gw.decide(call).verdict == "forward"
    ctx.round = 2
    assert gw.decide(call).verdict == "forward"
    ctx.round = 3
    assert gw.decide(call).verdict == "deny"
    ctx.round = 4
    assert gw.decide(call).verdict == "forward"


def test_budget_preserves_late_round_calls_and_runs_within_deadline():
    ctx = context(credits=5, round_no=10)
    gw = Gateway(ctx)
    start = perf_counter()
    assert gw.decide(command("glossary", "define", args={"term": "agent"})).verdict == "forward"
    assert (perf_counter() - start) < 0.250
    ctx.credits = 0
    assert gw.decide(command("glossary", "define", args={"term": "agent"})).verdict == "deny"
