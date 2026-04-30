"""Trigger detection + intent chain derivation (Cluster E / Plan 54).

These tests pin the contract for ``opentraces.core.intent``:

* ``is_trigger`` — short imperative authorising a discussed action; spec
  content past the trigger phrase disqualifies it; image placeholders
  never match.
* ``derive_intent_chain`` — given the user_instruction nodes and the
  burst start step, returns ``{trigger, most_substantive_spec,
  spec_chain}`` with triggers stripped from the chain and image
  placeholders skipped.
"""
from __future__ import annotations

from opentraces_schema import TraceMapNode


def _user_instruction(
    *,
    step_index: int,
    text: str,
    trace_id: str = "t-intent",
) -> TraceMapNode:
    return TraceMapNode(
        node_id=f"tmn:{trace_id}:{step_index}",
        trace_id=trace_id,
        unit_id=f"tu:{trace_id}:{step_index}",
        action_type="user_instruction",
        step_index=step_index,
        start_step_index=step_index,
        end_step_index=step_index,
        text_preview=text,
    )


# ─── is_trigger ────────────────────────────────────────────────────────────


def test_is_trigger_short_imperatives_match():
    """The canonical trigger phrases used in entry #6 must be detected."""
    from opentraces.core.intent import is_trigger

    assert is_trigger("lets go ahead and make a commit about this fix")
    assert is_trigger("go ahead")
    assert is_trigger("yes")
    assert is_trigger("yes.")
    assert is_trigger("yes!")
    assert is_trigger("ok")
    assert is_trigger("ok let's do it")
    assert is_trigger("go ahead and commit")
    assert is_trigger("why don't we call them that then?")
    assert is_trigger("why not call them that")
    assert is_trigger("ship it")
    assert is_trigger("commit it")
    assert is_trigger("make the commit")
    assert is_trigger("proceed")


def test_is_trigger_long_messages_with_imperative_prefix_dont_match():
    """A trigger phrase followed by substantive content is a spec, not a trigger."""
    from opentraces.core.intent import is_trigger

    assert not is_trigger(
        "yes implement the redis migration with TLS and add a benchmark"
    )
    assert not is_trigger(
        "yes please add the redis migration including TLS support and a benchmark suite"
    )
    assert not is_trigger("How do I reference a given dataset?")
    assert not is_trigger(
        "Okay let me challenge a few of those baseline, which I think there's still too many."
    )
    # Substantive question — doesn't start with a trigger phrase.
    assert not is_trigger(
        "I'm interested in your apply command: clone an existing dataset and import its rows."
    )


def test_is_trigger_image_placeholders_dont_match():
    """Image placeholders ([Image: ...]) are not text and never count as triggers."""
    from opentraces.core.intent import is_trigger

    assert not is_trigger("[Image: source: /tmp/screenshot.png]")
    assert not is_trigger("[Image #1]")
    assert not is_trigger("[Image: pasted screenshot]")


def test_is_trigger_handles_empty_and_none_text():
    """Empty / None / whitespace-only text is never a trigger."""
    from opentraces.core.intent import is_trigger

    assert not is_trigger(None)
    assert not is_trigger("")
    assert not is_trigger("   ")
    assert not is_trigger("\n\t")


def test_is_trigger_respects_length_cap():
    """Stripped text past the configured length cap is never a trigger."""
    from opentraces.core.intent import TRIGGER_MAX_LEN, is_trigger

    long_yes = "yes " + ("ok " * 200)
    assert len(long_yes.strip()) > TRIGGER_MAX_LEN
    assert not is_trigger(long_yes)


# ─── derive_intent_chain ───────────────────────────────────────────────────


def test_derive_intent_chain_filters_triggers():
    """The trigger right before the burst should populate ``trigger``; the
    most substantive spec is the latest non-trigger before the burst."""
    from opentraces.core.intent import derive_intent_chain

    nodes = [
        _user_instruction(step_index=1, text="Please review this dataset surface"),
        _user_instruction(step_index=12, text="Let me challenge: too many verbs"),
        _user_instruction(
            step_index=19,
            text="how is that different from pull? I'm interested in your apply command.",
        ),
        _user_instruction(step_index=24, text="why don't we call them that then?"),
        _user_instruction(step_index=26, text="lets go ahead and make a commit about this fix"),
    ]
    intent = derive_intent_chain(nodes, burst_step_start=32)

    assert intent["trigger"] == {
        "step": 26,
        "text": "lets go ahead and make a commit about this fix",
    }
    spec = intent["most_substantive_spec"]
    assert spec is not None
    assert spec["step"] == 19
    assert "apply command" in spec["text"]


def test_derive_intent_chain_includes_full_history_in_order():
    """spec_chain must include every non-trigger user_instruction from start
    of trace up to and including most_substantive_spec, in step order."""
    from opentraces.core.intent import derive_intent_chain

    nodes = [
        _user_instruction(step_index=1, text="Please review this dataset surface"),
        _user_instruction(step_index=12, text="Let me challenge: too many verbs"),
        _user_instruction(
            step_index=19,
            text="how is that different from pull? I'm interested in your apply command.",
        ),
        _user_instruction(step_index=24, text="why don't we call them that then?"),
        _user_instruction(step_index=26, text="lets go ahead and make a commit"),
    ]
    intent = derive_intent_chain(nodes, burst_step_start=32)

    chain = intent["spec_chain"]
    # Steps 1, 12, 19 are non-triggers and ≤ most_substantive_spec.step (19).
    chain_steps = [item["step"] for item in chain]
    assert chain_steps == [1, 12, 19]
    # Trigger steps (24 = "why don't we...", 26 = "lets go ahead") MUST be excluded.
    assert 24 not in chain_steps
    assert 26 not in chain_steps


def test_derive_intent_chain_skips_image_placeholders():
    """Pure ``[Image: ...]`` messages (no substantive text after the
    placeholder block) are filtered. Hybrid messages keep their text."""
    from opentraces.core.intent import derive_intent_chain

    nodes = [
        _user_instruction(step_index=1, text="[Image: src=/tmp/foo.png]"),
        _user_instruction(step_index=2, text="[Image #1] Look at this UI question"),
        _user_instruction(step_index=3, text="[Image: source: /tmp/x.png]"),
        _user_instruction(
            step_index=10,
            text="please refactor the dataset surface to be more obvious",
        ),
        _user_instruction(step_index=15, text="ok"),
    ]
    intent = derive_intent_chain(nodes, burst_step_start=20)

    # Pure image placeholders (steps 1, 3) are filtered. A hybrid
    # message that starts with [Image: ...] but carries substantive
    # text afterwards (step 2) is kept — the image markers themselves
    # are stripped for purposes of trigger detection but the message
    # remains on the chain.
    chain_steps = [item["step"] for item in intent["spec_chain"]]
    assert chain_steps == [2, 10]
    assert intent["trigger"]["step"] == 15
    assert intent["most_substantive_spec"]["step"] == 10


def test_derive_intent_chain_returns_none_when_no_specs():
    """When every user_instruction before the burst is a trigger, spec is None."""
    from opentraces.core.intent import derive_intent_chain

    nodes = [
        _user_instruction(step_index=1, text="ok"),
        _user_instruction(step_index=2, text="go ahead"),
        _user_instruction(step_index=3, text="yes!"),
    ]
    intent = derive_intent_chain(nodes, burst_step_start=10)

    assert intent["most_substantive_spec"] is None
    assert intent["spec_chain"] == []
    # Latest trigger still wins.
    assert intent["trigger"]["step"] == 3


def test_derive_intent_chain_empty_nodes():
    """Empty user_instruction list yields all-None / empty intent."""
    from opentraces.core.intent import derive_intent_chain

    intent = derive_intent_chain([], burst_step_start=0)
    assert intent == {"trigger": None, "most_substantive_spec": None, "spec_chain": []}


def test_derive_intent_chain_truncates_long_spec_text():
    """spec_chain entries (and most_substantive_spec) clamp to 500 chars."""
    from opentraces.core.intent import SPEC_TEXT_DISPLAY_LIMIT, derive_intent_chain

    long_text = "x" * 2000
    nodes = [
        _user_instruction(step_index=1, text=long_text),
    ]
    intent = derive_intent_chain(nodes, burst_step_start=10)
    assert intent["most_substantive_spec"] is not None
    assert len(intent["most_substantive_spec"]["text"]) <= SPEC_TEXT_DISPLAY_LIMIT
    assert intent["spec_chain"][0]["text"] == intent["most_substantive_spec"]["text"]


def test_derive_intent_chain_only_includes_steps_at_or_before_burst_start():
    """User instructions after burst_step_start MUST NOT enter the chain."""
    from opentraces.core.intent import derive_intent_chain

    nodes = [
        _user_instruction(step_index=1, text="please refactor"),
        _user_instruction(step_index=20, text="add the feature"),
        _user_instruction(step_index=50, text="now revert that change"),
    ]
    intent = derive_intent_chain(nodes, burst_step_start=30)

    chain_steps = [item["step"] for item in intent["spec_chain"]]
    assert 50 not in chain_steps
    assert chain_steps == [1, 20]
