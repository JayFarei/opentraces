"""Registry-level tests for the security/privacy tool registry.

Verifies that each registered tool:

* satisfies the structural Protocol it claims via ``kind``
* exposes a stable ``name`` and ``display_name``
* honours ``enabled(cfg)`` against the new per-tool config flags
* survives ``describe(cfg)`` and yields a well-formed ToolInfo
"""

from __future__ import annotations

import pytest

from opentraces.core.config import Config
from opentraces.security.tools import (
    Detector,
    ToolInfo,
)
from opentraces.security.tools._registry import (
    all_tools,
    describe_all,
    get,
    iter_enabled,
    iter_tools,
)


EXPECTED_TOOL_ORDER = (
    "regex",
    "entropy",
    "trufflehog",
    "privacy_filter",
    "llm_pii",
    "business_logic",
    "path_anonymizer",
    "capsule_scope",
    "classifier",
)


class TestRegistryShape:
    def test_canonical_order(self) -> None:
        names = tuple(t.name for t in iter_tools())
        assert names == EXPECTED_TOOL_ORDER

    def test_all_tools_returns_sequence(self) -> None:
        tools = all_tools()
        assert len(tools) == len(EXPECTED_TOOL_ORDER)

    def test_get_unknown_raises(self) -> None:
        with pytest.raises(KeyError):
            get("not_a_tool")

    def test_get_returns_singleton(self) -> None:
        a = get("regex")
        b = get("regex")
        assert a is b

    def test_every_tool_has_shared_surface(self) -> None:
        for tool in iter_tools():
            assert hasattr(tool, "name")
            assert hasattr(tool, "kind")
            assert callable(getattr(tool, "enabled", None))
            assert callable(getattr(tool, "apply", None))
            assert callable(getattr(tool, "describe", None))

    def test_kind_field_matches_registry_position(self) -> None:
        for tool in iter_tools():
            assert tool.kind in ("detector", "judge", "transformer"), (
                f"{tool.name} has unknown kind {tool.kind!r}"
            )

    def test_per_field_detectors_implement_find(self) -> None:
        """Detectors that participate in ``sanitize_text``/``sanitize_dict``
        must expose ``find``. TruffleHog and llm_pii are detector-kind but
        scan record-shape, so they are intentionally excluded — those paths
        run only via ``sanitize_record``."""
        per_field = {"regex", "entropy", "privacy_filter"}
        for tool in iter_tools():
            if tool.name in per_field:
                assert isinstance(tool, Detector), (
                    f"{tool.name} must satisfy Detector protocol"
                )


class TestEnableState:
    def test_default_config_enables_no_tools(self) -> None:
        """A bare ``Config()`` has no automatic security tools enabled."""
        cfg = Config()
        enabled = {t.name for t in iter_enabled(cfg)}
        assert enabled == set()

    def test_regex_and_entropy_enable_flags_toggle_iter_enabled(self) -> None:
        cfg = Config()
        cfg.security.regex.enabled = True
        cfg.security.entropy.enabled = True
        assert {"regex", "entropy"} <= {t.name for t in iter_enabled(cfg)}

    def test_trufflehog_enable_flag_toggles_iter_enabled(self) -> None:
        cfg = Config()
        cfg.security.trufflehog.enabled = True
        assert "trufflehog" in {t.name for t in iter_enabled(cfg)}

    def test_path_anonymizer_can_be_disabled(self) -> None:
        cfg = Config()
        cfg.security.path_anonymizer.enabled = False
        assert "path_anonymizer" not in {t.name for t in iter_enabled(cfg)}

    def test_classifier_can_be_disabled(self) -> None:
        cfg = Config()
        cfg.security.classifier.enabled = False
        assert "classifier" not in {t.name for t in iter_enabled(cfg)}

    def test_business_logic_and_capsule_scope_enable_flags_toggle_iter_enabled(self) -> None:
        cfg = Config()
        cfg.security.business_logic.enabled = True
        cfg.security.capsule_scope.enabled = True
        enabled = {t.name for t in iter_enabled(cfg)}
        assert {"business_logic", "capsule_scope"} <= enabled

    def test_business_logic_config_defaults_disabled(self) -> None:
        cfg = Config()
        assert cfg.security.business_logic.enabled is False

    def test_capsule_scope_config_defaults(self) -> None:
        cfg = Config()
        assert cfg.security.capsule_scope.enabled is False
        # The default field-exclusion set protects prompt-bearing fields.
        assert cfg.security.capsule_scope.exclude == [
            "context_resume_packet.system_layer",
            "slice.steps.*.reasoning_content",
        ]

    def test_no_cfg_yields_no_enabled_tools(self) -> None:
        """iter_enabled(None) has no implicit always-on tools."""
        assert {t.name for t in iter_enabled(None)} == set()


class TestDescribe:
    def test_describe_all_returns_tool_info_list(self) -> None:
        cfg = Config()
        out = describe_all(cfg)
        assert len(out) == len(EXPECTED_TOOL_ORDER)
        for info in out:
            assert isinstance(info, ToolInfo)
            assert info.name in EXPECTED_TOOL_ORDER

    def test_describe_default_states(self) -> None:
        cfg = Config()
        infos = {i.name: i for i in describe_all(cfg)}
        assert infos["regex"].state == "disabled"
        assert infos["entropy"].state == "disabled"
        assert infos["trufflehog"].state == "disabled"
        assert infos["privacy_filter"].state == "disabled"
        assert infos["llm_pii"].state == "disabled"
        assert infos["business_logic"].state == "disabled"
        assert infos["path_anonymizer"].state == "disabled"
        assert infos["capsule_scope"].state == "disabled"
        assert infos["classifier"].state == "disabled"

    def test_describe_includes_setup_commands_for_optin_tools(self) -> None:
        infos = {i.name: i for i in describe_all(Config())}
        assert infos["trufflehog"].setup_cmd == "opentraces setup trufflehog"
        assert infos["trufflehog"].disable_cmd == "opentraces setup trufflehog --disable"
        assert infos["llm_pii"].setup_cmd is None
        assert "no setup command" in (infos["llm_pii"].detail or "")
