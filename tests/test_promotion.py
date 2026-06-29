"""Tests for the promotion filter + provenance helpers (pure logic)."""

from __future__ import annotations

from gingugu.promote import (
    already_promoted_ids,
    contains_secret,
    is_promotable,
    provenance,
)


def _mem(**kw) -> dict:
    base = {
        "id": "m1",
        "type": "fact",
        "title": "t",
        "content": "some durable knowledge",
        "confidence": "verified",
        "tags": [],
    }
    base.update(kw)
    return base


# --- is_promotable: the locked filter --------------------------------------


def test_verified_durable_knowledge_promotes():
    assert is_promotable(_mem(type="pattern", tags=["aws", "terraform"]))


def test_non_verified_is_dropped():
    assert not is_promotable(_mem(confidence="inferred"))


def test_type_is_not_a_gate_preference_jewel_promotes():
    # the crown-jewel "verify the tag" rule is typed `preference`
    assert is_promotable(_mem(type="preference", tags=["helm", "rule", "verification"]))


def test_episodic_tags_drop_even_if_verified():
    assert not is_promotable(_mem(type="workflow", tags=["session", "resume"]))


def test_personal_tags_drop():
    assert not is_promotable(_mem(type="preference", tags=["beepboop", "identity"]))


def test_key_rotation_tag_drops():
    assert not is_promotable(_mem(tags=["key-rotation"]))


def test_secret_in_content_drops():
    assert not is_promotable(_mem(content="new key sk-3OMHdo6yU5tIzfBGqDLpUg issued"))


# --- contains_secret --------------------------------------------------------


def test_contains_secret_matches():
    assert contains_secret("token sk-AAAAAAAAAAAA")
    assert contains_secret("AKIAIOSFODNN7EXAMPLE creds")
    assert contains_secret("secret_access_key = wJalr")
    assert contains_secret("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0")
    assert contains_secret("hash 88dc28d0f030c55ed4ab77ed8faf098196cb1c05df778539800c9f1243fe6b4b")
    assert contains_secret("password = hunter2")


def test_contains_secret_clean_text():
    assert not contains_secret("Always run docker manifest inspect before committing a tag.")
    assert not contains_secret("")


# --- provenance + dedup -----------------------------------------------------


def test_provenance_shape():
    mem = _mem(id="src-42", _source_namespace="devex")
    stamp = provenance(
        mem, instance="laptop", contributor="brian", when="2026-06-29T00:00:00+00:00"
    )
    pf = stamp["promoted_from"]
    assert pf == {
        "instance": "laptop",
        "namespace": "devex",
        "id": "src-42",
        "contributor": "brian",
        "promoted_at": "2026-06-29T00:00:00+00:00",
    }


def test_already_promoted_ids_reads_metadata():
    target = [
        {"metadata": '{"promoted_from": {"id": "a1"}}'},
        {"metadata": {"promoted_from": {"id": "b2"}}},  # already a dict
        {"metadata": None},  # native memory, no provenance
        {"metadata": "not json"},  # malformed, skipped
    ]
    assert already_promoted_ids(target) == {"a1", "b2"}
