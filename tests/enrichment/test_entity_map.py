"""Part D tests: EntityMap named placeholders.

Replaces opaque ``[REDACTED]`` markers with stable named placeholders
like ``[PERSON_1]``, ``[EMAIL_2]`` for reviewer readability. Same entity
text always gets the same placeholder within a map; per-type counters
ensure numbers are independent across entity types.
"""

from __future__ import annotations

import json

import pytest

from opentraces.security.anonymizer import EntityMap, normalize_user_paths


class TestEntityMapBasics:
    def test_first_person_gets_number_1(self) -> None:
        em = EntityMap()
        assert em.get_placeholder("PERSON", "Alice") == "[PERSON_1]"

    def test_same_entity_same_placeholder(self) -> None:
        em = EntityMap()
        first = em.get_placeholder("PERSON", "Alice")
        again = em.get_placeholder("PERSON", "Alice")
        assert first == again == "[PERSON_1]"

    def test_different_persons_get_distinct_numbers(self) -> None:
        em = EntityMap()
        assert em.get_placeholder("PERSON", "Alice") == "[PERSON_1]"
        assert em.get_placeholder("PERSON", "Bob") == "[PERSON_2]"
        assert em.get_placeholder("PERSON", "Alice") == "[PERSON_1]"

    def test_per_type_counters_are_independent(self) -> None:
        em = EntityMap()
        assert em.get_placeholder("PERSON", "Alice") == "[PERSON_1]"
        assert em.get_placeholder("EMAIL", "a@b.com") == "[EMAIL_1]"
        assert em.get_placeholder("PERSON", "Bob") == "[PERSON_2]"
        assert em.get_placeholder("EMAIL", "c@d.com") == "[EMAIL_2]"


class TestApplyAll:
    def test_replaces_registered_entities(self) -> None:
        em = EntityMap()
        em.get_placeholder("PERSON", "Alice")
        em.get_placeholder("EMAIL", "alice@example.com")
        out = em.apply_all("Alice wrote to alice@example.com today")
        assert "[PERSON_1]" in out
        assert "[EMAIL_1]" in out
        assert "Alice" not in out
        assert "alice@example.com" not in out

    def test_longest_first_prevents_partial_clobber(self) -> None:
        # Plan: sort by entity text length descending before substitution,
        # so "sarah" does not clobber "sarah.chen@corp.com" mid-replacement.
        em = EntityMap()
        em.get_placeholder("PERSON", "sarah")
        em.get_placeholder("EMAIL", "sarah.chen@corp.com")
        out = em.apply_all("contact sarah.chen@corp.com or sarah directly")
        assert "[EMAIL_1]" in out
        assert "[PERSON_1]" in out
        # The email must be fully replaced, not "[PERSON_1].chen@corp.com"
        assert "sarah.chen" not in out
        assert "[PERSON_1].chen@corp.com" not in out

    def test_unregistered_text_untouched(self) -> None:
        em = EntityMap()
        em.get_placeholder("PERSON", "Alice")
        assert em.apply_all("Bob said hi") == "Bob said hi"

    def test_empty_map_is_identity(self) -> None:
        em = EntityMap()
        assert em.apply_all("anything goes") == "anything goes"


class TestSaveLoad:
    def test_roundtrip(self, tmp_path) -> None:
        em = EntityMap()
        em.get_placeholder("PERSON", "Alice")
        em.get_placeholder("PERSON", "Bob")
        em.get_placeholder("EMAIL", "a@b.com")

        path = tmp_path / "map.json"
        em.save(path)

        em2 = EntityMap.load(path)
        assert em2.get_placeholder("PERSON", "Alice") == "[PERSON_1]"
        assert em2.get_placeholder("PERSON", "Bob") == "[PERSON_2]"
        assert em2.get_placeholder("EMAIL", "a@b.com") == "[EMAIL_1]"

    def test_load_preserves_counters(self, tmp_path) -> None:
        em = EntityMap()
        em.get_placeholder("PERSON", "Alice")
        em.get_placeholder("PERSON", "Bob")

        path = tmp_path / "map.json"
        em.save(path)

        em2 = EntityMap.load(path)
        # A brand new entity must take the NEXT number, not overwrite Alice/Bob.
        assert em2.get_placeholder("PERSON", "Carol") == "[PERSON_3]"

    def test_load_nonexistent_returns_empty(self, tmp_path) -> None:
        em = EntityMap.load(tmp_path / "does_not_exist.json")
        assert em.get_placeholder("PERSON", "Alice") == "[PERSON_1]"

    def test_save_writes_valid_json(self, tmp_path) -> None:
        em = EntityMap()
        em.get_placeholder("PERSON", "Alice")
        path = tmp_path / "map.json"
        em.save(path)
        data = json.loads(path.read_text())
        # Schema check: top-level dict keyed by entity_type.
        assert "PERSON" in data
        assert "Alice" in data["PERSON"]


class TestUserPathNormalization:
    def test_macos_path(self) -> None:
        assert normalize_user_paths("/Users/rlamers/code/app") == "/Users/user/code/app"

    def test_linux_path(self) -> None:
        assert normalize_user_paths("/home/alice/src/foo") == "/home/user/src/foo"

    def test_username_with_dot(self) -> None:
        assert normalize_user_paths("/Users/sarah.chen/work") == "/Users/user/work"

    def test_username_with_dash(self) -> None:
        assert normalize_user_paths("/Users/jay-farei/repo") == "/Users/user/repo"

    def test_username_with_underscore(self) -> None:
        assert normalize_user_paths("/home/deploy_user/svc") == "/home/user/svc"

    def test_multiple_paths_in_one_string(self) -> None:
        text = "from /Users/alice/a to /home/bob/b"
        out = normalize_user_paths(text)
        assert "/Users/user/a" in out
        assert "/home/user/b" in out

    def test_non_path_text_untouched(self) -> None:
        assert normalize_user_paths("hello world") == "hello world"


class TestApplyAllWithUserPathSpecialCase:
    def test_user_path_entity_type_uses_normalization(self) -> None:
        # Plan: USER_PATH is an EntityMap special case — normalized rather
        # than opaque-replaced, preserving path structure for trace analysis.
        em = EntityMap()
        em.get_placeholder("USER_PATH", "/Users/rlamers/")
        out = em.apply_all("files in /Users/rlamers/code/")
        assert "/Users/user/code/" in out
        # Not a numbered placeholder — structure preserved.
        assert "[USER_PATH" not in out
