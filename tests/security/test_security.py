"""Comprehensive tests for the opentraces security pipeline."""

from __future__ import annotations

import logging
import os

from opentraces_schema.models import (
    Agent,
    Observation,
    Outcome,
    Step,
    ToolCall,
    TraceRecord,
)

from opentraces.security.anonymizer import (
    anonymize_paths,
    extract_usernames_from_paths,
    hash_username,
)
from opentraces.security.classifier import (
    classify_content,
    classify_trace_record,
)
from opentraces.security.redactor import RedactingFilter
from opentraces.security.scanner import (
    FieldType,
    scan_content,
    scan_trace_record,
    two_pass_scan,
)
from opentraces.security.secrets import (
    redact_text,
    scan_text,
    shannon_entropy,
    _luhn_check,
)

TEST_USERNAME = os.getenv("OPENTRACES_TEST_USERNAME", "testuser")


# ===================================================================
# secrets.py -- Regex patterns (positive + negative)
# ===================================================================


class TestJWT:
    def test_detects_jwt(self):
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        matches = scan_text(token)
        assert any(m.pattern_name == "jwt_token" for m in matches)

    def test_ignores_short_eyj(self):
        matches = scan_text("eyJhbG.eyJzd.short")
        assert not any(m.pattern_name == "jwt_token" for m in matches)


class TestAnthropicKey:
    def test_detects_anthropic_key(self):
        matches = scan_text("sk-ant-api03-abcdefghijklmnopqrstuvwxyz")
        assert any(m.pattern_name == "anthropic_api_key" for m in matches)

    def test_severity_is_critical(self):
        matches = scan_text("sk-ant-api03-abcdefghijklmnopqrstuvwxyz")
        anthropic = [m for m in matches if m.pattern_name == "anthropic_api_key"]
        assert anthropic[0].severity == "critical"


class TestOpenAIKey:
    def test_detects_project_key(self):
        matches = scan_text("sk-proj-abcdefghijklmnopqrstuvwxyz")
        assert any(m.pattern_name == "openai_project_key" for m in matches)

    def test_detects_generic_key(self):
        matches = scan_text("sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234")
        assert any(m.pattern_name == "openai_api_key" for m in matches)

    def test_ignores_dummy_key(self):
        matches = scan_text("sk-test-abcdefghijklmnopqrstuvwxyz")
        assert not any(m.pattern_name == "openai_api_key" for m in matches)


class TestHuggingFaceToken:
    def test_detects_hf_token(self):
        matches = scan_text("hf_ABCDEFGHIJKLMNOPQRSTuvwxyz")
        assert any(m.pattern_name == "huggingface_token" for m in matches)


class TestGitHubTokens:
    def test_detects_ghp(self):
        matches = scan_text("ghp_ABCDEFGHIJKLMNOPQRSTuvwxyz")
        assert any(m.pattern_name == "github_token" for m in matches)

    def test_detects_gho(self):
        matches = scan_text("gho_ABCDEFGHIJKLMNOPQRSTuvwxyz")
        assert any(m.pattern_name == "github_token" for m in matches)

    def test_detects_ghs(self):
        matches = scan_text("ghs_ABCDEFGHIJKLMNOPQRSTuvwxyz")
        assert any(m.pattern_name == "github_token" for m in matches)

    def test_detects_ghu(self):
        matches = scan_text("ghu_ABCDEFGHIJKLMNOPQRSTuvwxyz")
        assert any(m.pattern_name == "github_token" for m in matches)

    def test_detects_github_pat(self):
        matches = scan_text("github_pat_ABCDEFGHIJKLMNOPQRSTuvwxyz")
        assert any(m.pattern_name == "github_pat" for m in matches)


class TestPyPIToken:
    def test_detects_pypi_token(self):
        matches = scan_text("pypi-AgEIcHlwaS5vcmcCJGY5NjVm")
        assert any(m.pattern_name == "pypi_token" for m in matches)


class TestNPMToken:
    def test_detects_npm_token(self):
        matches = scan_text("npm_ABCDEFGHIJKLMNOPQRSTuvwxyz")
        assert any(m.pattern_name == "npm_token" for m in matches)


class TestAWSKey:
    def test_detects_aws_access_key(self):
        matches = scan_text("AKIAIOSFODNN7EXAMPLE")
        assert any(m.pattern_name == "aws_access_key" for m in matches)

    def test_ignores_non_akia_prefix(self):
        matches = scan_text("ASIA1234567890123456")
        assert not any(m.pattern_name == "aws_access_key" for m in matches)


class TestSlackToken:
    def test_detects_xoxb(self):
        matches = scan_text("xoxb-12345678901-abcdefghij")
        assert any(m.pattern_name == "slack_token" for m in matches)

    def test_detects_xoxp(self):
        matches = scan_text("xoxp-12345678901-abcdefghij")
        assert any(m.pattern_name == "slack_token" for m in matches)

    def test_detects_xoxe(self):
        matches = scan_text("xoxe-12345678901-abcdefghij")
        assert any(m.pattern_name == "slack_token" for m in matches)


class TestDiscordWebhook:
    def test_detects_discord_webhook(self):
        url = "https://discord.com/api/webhooks/123456789/abcdef_GHIJKL"
        matches = scan_text(url)
        assert any(m.pattern_name == "discord_webhook" for m in matches)

    def test_detects_discordapp_webhook(self):
        url = "https://discordapp.com/api/webhooks/123456789/abcdef_GHIJKL"
        matches = scan_text(url)
        assert any(m.pattern_name == "discord_webhook" for m in matches)


class TestPrivateKey:
    def test_detects_rsa_private_key(self):
        matches = scan_text("-----BEGIN RSA PRIVATE KEY-----")
        assert any(m.pattern_name == "private_key" for m in matches)

    def test_detects_ec_private_key(self):
        matches = scan_text("-----BEGIN EC PRIVATE KEY-----")
        assert any(m.pattern_name == "private_key" for m in matches)

    def test_detects_openssh_private_key(self):
        matches = scan_text("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert any(m.pattern_name == "private_key" for m in matches)


class TestDatabaseURL:
    def test_detects_postgresql(self):
        matches = scan_text("postgresql://user:pass@host:5432/dbname")
        assert any(m.pattern_name == "database_url" for m in matches)

    def test_detects_mysql(self):
        matches = scan_text("mysql://root:secret@mysql.internal:3306/app")
        assert any(m.pattern_name == "database_url" for m in matches)

    def test_detects_mongodb(self):
        matches = scan_text("mongodb://admin:p4ss@mongo.prod:27017/mydb")
        assert any(m.pattern_name == "database_url" for m in matches)

    def test_detects_redis(self):
        matches = scan_text("redis://default:pw@redis.internal:6379/0")
        assert any(m.pattern_name == "database_url" for m in matches)

    def test_ignores_example_url(self):
        matches = scan_text("postgresql://user:pass@localhost:5432/dbname")
        assert not any(m.pattern_name == "database_url" for m in matches)


class TestBearerToken:
    def test_detects_bearer(self):
        matches = scan_text("Bearer eyJhbGciOiJSUzI1NiIs")
        assert any(m.pattern_name == "bearer_token" for m in matches)

    def test_ignores_dummy_bearer(self):
        matches = scan_text("Bearer $TOKEN_VAR_PLACEHOLDER_XYZ")
        assert not any(m.pattern_name == "bearer_token" for m in matches)


class TestIPAddresses:
    def test_detects_ipv4(self):
        matches = scan_text("Server is at 203.0.113.42 running")
        assert any(m.pattern_name == "ipv4_address" for m in matches)

    def test_ignores_private_ipv4(self):
        matches = scan_text("Server is at 192.168.1.1 running")
        assert not any(m.pattern_name == "ipv4_address" for m in matches)

    def test_ignores_loopback(self):
        matches = scan_text("Listening on 127.0.0.1 port 8080")
        assert not any(m.pattern_name == "ipv4_address" for m in matches)

    def test_detects_ipv6(self):
        matches = scan_text("address is 2001:0db8:85a3:0000:0000:8a2e:0370:7334 ok")
        assert any(m.pattern_name == "ipv6_address" for m in matches)


class TestEmailAddress:
    def test_detects_email(self):
        matches = scan_text("Contact user@company.com for help")
        assert any(m.pattern_name == "email_address" for m in matches)

    def test_ignores_noreply(self):
        matches = scan_text("From noreply@github.com")
        assert not any(m.pattern_name == "email_address" for m in matches)

    def test_ignores_example_com(self):
        matches = scan_text("test@example.com")
        assert not any(m.pattern_name == "email_address" for m in matches)


class TestCreditCard:
    def test_detects_valid_visa(self):
        # 4111111111111111 passes Luhn
        matches = scan_text("Card: 4111111111111111")
        assert any(m.pattern_name == "credit_card" for m in matches)

    def test_detects_valid_with_spaces(self):
        matches = scan_text("Card: 4111 1111 1111 1111")
        assert any(m.pattern_name == "credit_card" for m in matches)

    def test_ignores_invalid_luhn(self):
        # 1234567890123456 does not pass Luhn
        matches = scan_text("Number: 1234567890123456")
        assert not any(m.pattern_name == "credit_card" for m in matches)


class TestSSN:
    def test_detects_ssn(self):
        matches = scan_text("SSN: 123-45-6789")
        assert any(m.pattern_name == "ssn" for m in matches)

    def test_ignores_invalid_area(self):
        # 000-xx-xxxx is invalid
        matches = scan_text("SSN: 000-12-3456")
        assert not any(m.pattern_name == "ssn" for m in matches)

    def test_ignores_666_area(self):
        matches = scan_text("SSN: 666-12-3456")
        assert not any(m.pattern_name == "ssn" for m in matches)


class TestPhoneNumber:
    def test_detects_standard(self):
        matches = scan_text("Call (555) 123-4567 for info")
        assert any(m.pattern_name == "phone_number" for m in matches)

    def test_detects_with_country_code(self):
        matches = scan_text("Call +1 555-123-4567")
        assert any(m.pattern_name == "phone_number" for m in matches)

    def test_detects_dotted(self):
        matches = scan_text("Phone: 555.123.4567")
        assert any(m.pattern_name == "phone_number" for m in matches)


# ===================================================================
# secrets.py -- Shannon entropy
# ===================================================================


class TestShannonEntropy:
    def test_empty_string(self):
        assert shannon_entropy("") == 0.0

    def test_single_char(self):
        assert shannon_entropy("aaaa") == 0.0

    def test_high_entropy(self):
        # Random-looking string should have high entropy
        ent = shannon_entropy("aB3$xY9@kL2!mN5^pQ8&rT1")
        assert ent > 4.0

    def test_low_entropy(self):
        ent = shannon_entropy("aaabbbccc")
        assert ent < 2.0

    def test_entropy_flag_in_scan(self):
        # High-entropy random string that is not caught by regex
        high_ent = "Xk9mZr3pWq7vNt2sLf6yBh4jCe8gAa5d"
        matches = scan_text(high_ent, include_entropy=True, entropy_threshold=3.5)
        assert any(m.pattern_name == "high_entropy_string" for m in matches)

    def test_entropy_excluded_when_disabled(self):
        high_ent = "Xk9mZr3pWq7vNt2sLf6yBh4jCe8gAa5d"
        matches = scan_text(high_ent, include_entropy=False)
        assert not any(m.pattern_name == "high_entropy_string" for m in matches)


# ===================================================================
# secrets.py -- Luhn validation
# ===================================================================


class TestLuhnCheck:
    def test_valid_visa(self):
        assert _luhn_check("4111111111111111") is True

    def test_valid_mastercard(self):
        assert _luhn_check("5500000000000004") is True

    def test_invalid_number(self):
        assert _luhn_check("1234567890123456") is False

    def test_too_short(self):
        assert _luhn_check("12345") is False


# ===================================================================
# secrets.py -- Redaction
# ===================================================================


class TestRedaction:
    def test_redact_single(self):
        text = "Key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz here"
        matches = scan_text(text)
        redacted = redact_text(text, matches)
        assert "sk-ant-" not in redacted
        assert "[REDACTED]" in redacted

    def test_redact_multiple(self):
        text = "Key1: ghp_ABCDEFGHIJKLMNOPQRSTuvwxyz and Key2: hf_ABCDEFGHIJKLMNOPQRSTuvwxyz"
        matches = scan_text(text)
        redacted = redact_text(text, matches)
        assert "ghp_" not in redacted
        assert "hf_" not in redacted
        assert redacted.count("[REDACTED]") >= 2

    def test_redact_empty_matches(self):
        text = "nothing secret here"
        assert redact_text(text, []) == text


# ===================================================================
# secrets.py -- Allowlist
# ===================================================================


class TestAllowlist:
    def test_decorator_not_flagged(self):
        matches = scan_text("@property")
        assert not any(m.pattern_name == "email_address" for m in matches)

    def test_noreply_not_flagged(self):
        matches = scan_text("noreply@github.com")
        assert not any(m.pattern_name == "email_address" for m in matches)

    def test_example_email_not_flagged(self):
        matches = scan_text("test@example.com")
        assert not any(m.pattern_name == "email_address" for m in matches)

    def test_private_ip_not_flagged(self):
        matches = scan_text("Server at 10.0.0.1 listening")
        assert not any(m.pattern_name == "ipv4_address" for m in matches)

    def test_172_private_ip_not_flagged(self):
        matches = scan_text("Gateway 172.16.0.1 running")
        assert not any(m.pattern_name == "ipv4_address" for m in matches)

    def test_example_db_url_not_flagged(self):
        matches = scan_text("postgresql://user:pass@example.com:5432/db")
        assert not any(m.pattern_name == "database_url" for m in matches)


# ===================================================================
# anonymizer.py -- Path anonymization
# ===================================================================


class TestHashUsername:
    def test_deterministic(self):
        h1 = hash_username(TEST_USERNAME)
        h2 = hash_username(TEST_USERNAME)
        assert h1 == h2

    def test_length(self):
        assert len(hash_username("alice")) == 8

    def test_hex_chars(self):
        h = hash_username("bob")
        assert all(c in "0123456789abcdef" for c in h)


class TestAnonymizePaths:
    USERNAME = TEST_USERNAME

    def test_macos_path(self):
        text = f"/Users/{TEST_USERNAME}/src/project/file.py"
        result = anonymize_paths(text, username=self.USERNAME)
        expected_hash = hash_username(self.USERNAME)
        assert f"/Users/{expected_hash}/" in result
        assert TEST_USERNAME not in result

    def test_linux_path(self):
        text = f"/home/{TEST_USERNAME}/projects/app.py"
        result = anonymize_paths(text, username=self.USERNAME)
        expected_hash = hash_username(self.USERNAME)
        assert f"/home/{expected_hash}/" in result
        assert TEST_USERNAME not in result

    def test_windows_backslash_path(self):
        text = rf"C:\Users\{TEST_USERNAME}\Documents\code.py"
        result = anonymize_paths(text, username=self.USERNAME)
        expected_hash = hash_username(self.USERNAME)
        assert f"C:\\Users\\{expected_hash}\\" in result
        assert TEST_USERNAME not in result

    def test_windows_forward_slash_path(self):
        text = f"C:/Users/{TEST_USERNAME}/Documents/code.py"
        result = anonymize_paths(text, username=self.USERNAME)
        assert TEST_USERNAME not in result

    def test_wsl_path(self):
        text = f"/mnt/c/Users/{TEST_USERNAME}/code/app.py"
        result = anonymize_paths(text, username=self.USERNAME)
        expected_hash = hash_username(self.USERNAME)
        assert f"/mnt/c/Users/{expected_hash}/" in result
        assert TEST_USERNAME not in result

    def test_wsl_unc_path(self):
        text = rf"\\wsl.localhost\Ubuntu\home\{TEST_USERNAME}\project"
        result = anonymize_paths(text, username=self.USERNAME)
        assert TEST_USERNAME not in result

    def test_hyphen_encoded_path(self):
        text = f"-Users-{TEST_USERNAME}-src-project"
        result = anonymize_paths(text, username=self.USERNAME)
        expected_hash = hash_username(self.USERNAME)
        assert f"-Users-{expected_hash}-" in result
        assert TEST_USERNAME not in result

    def test_tilde_path(self):
        text = f"~{TEST_USERNAME}/documents/file.txt"
        result = anonymize_paths(text, username=self.USERNAME)
        expected_hash = hash_username(self.USERNAME)
        assert f"~{expected_hash}" in result
        assert TEST_USERNAME not in result

    def test_extra_usernames(self):
        text = f"/home/{TEST_USERNAME}/code and /home/ghuser/code"
        result = anonymize_paths(text, username=self.USERNAME, extra_usernames=["ghuser"])
        assert TEST_USERNAME not in result
        assert "ghuser" not in result

    def test_empty_text(self):
        assert anonymize_paths("", username=self.USERNAME) == ""

    def test_no_username_available(self):
        # When no username provided and system detection fails
        text = "/some/path/file.py"
        result = anonymize_paths(text, username=None, extra_usernames=None)
        # Should be unchanged if no username found (may detect system user)
        assert "file.py" in result


# ===================================================================
# scanner.py -- Context-aware scanning
# ===================================================================


class TestFieldTypeScan:
    def test_tool_input_includes_entropy(self):
        high_ent = "Xk9mZr3pWq7vNt2sLf6yBh4jCe8gAa5d"
        result = scan_content(high_ent, FieldType.TOOL_INPUT)
        assert any(m.pattern_name == "high_entropy_string" for m in result.matches)

    def test_tool_result_excludes_entropy(self):
        high_ent = "Xk9mZr3pWq7vNt2sLf6yBh4jCe8gAa5d"
        result = scan_content(high_ent, FieldType.TOOL_RESULT)
        assert not any(m.pattern_name == "high_entropy_string" for m in result.matches)

    def test_reasoning_excludes_entropy(self):
        high_ent = "Xk9mZr3pWq7vNt2sLf6yBh4jCe8gAa5d"
        result = scan_content(high_ent, FieldType.REASONING)
        assert not any(m.pattern_name == "high_entropy_string" for m in result.matches)

    def test_general_includes_entropy(self):
        high_ent = "Xk9mZr3pWq7vNt2sLf6yBh4jCe8gAa5d"
        result = scan_content(high_ent, FieldType.GENERAL)
        assert any(m.pattern_name == "high_entropy_string" for m in result.matches)

    def test_tool_result_still_catches_regex(self):
        text = "Result contains sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
        result = scan_content(text, FieldType.TOOL_RESULT)
        assert any(m.pattern_name == "anthropic_api_key" for m in result.matches)

    def test_reasoning_still_catches_regex(self):
        text = "Thinking about sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
        result = scan_content(text, FieldType.REASONING)
        assert any(m.pattern_name == "anthropic_api_key" for m in result.matches)


class TestScanTraceRecord:
    def _make_record(self, **kwargs) -> TraceRecord:
        defaults = {
            "trace_id": "test-001",
            "session_id": "sess-001",
            "agent": Agent(name="test-agent"),
        }
        defaults.update(kwargs)
        return TraceRecord(**defaults)

    def test_scans_step_content(self):
        record = self._make_record(
            steps=[
                Step(
                    step_index=0,
                    role="agent",
                    content="Here is the key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
                )
            ]
        )
        result = scan_trace_record(record)
        assert result.redaction_count > 0

    def test_scans_tool_call_input(self):
        record = self._make_record(
            steps=[
                Step(
                    step_index=0,
                    role="agent",
                    tool_calls=[
                        ToolCall(
                            tool_call_id="tc-1",
                            tool_name="Bash",
                            input={"command": "export API_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz"},
                        )
                    ],
                )
            ]
        )
        result = scan_trace_record(record)
        assert result.redaction_count > 0

    def test_scans_observations(self):
        record = self._make_record(
            steps=[
                Step(
                    step_index=0,
                    role="agent",
                    observations=[
                        Observation(
                            source_call_id="tc-1",
                            content="ghp_ABCDEFGHIJKLMNOPQRSTuvwxyz found in output",
                        )
                    ],
                )
            ]
        )
        result = scan_trace_record(record)
        assert result.redaction_count > 0

    def test_scans_reasoning_content(self):
        record = self._make_record(
            steps=[
                Step(
                    step_index=0,
                    role="agent",
                    reasoning_content="The user has key sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
                )
            ]
        )
        result = scan_trace_record(record)
        assert result.redaction_count > 0

    def test_scans_system_prompts(self):
        record = self._make_record(
            system_prompts={"abc123": "Config: ghp_ABCDEFGHIJKLMNOPQRSTuvwxyz"},
        )
        result = scan_trace_record(record)
        assert result.redaction_count > 0

    def test_scans_outcome_patch(self):
        record = self._make_record(
            outcome=Outcome(patch="+API_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz"),
        )
        result = scan_trace_record(record)
        assert result.redaction_count > 0

    def test_empty_record_no_matches(self):
        record = self._make_record()
        result = scan_trace_record(record)
        assert result.redaction_count == 0


# ===================================================================
# scanner.py -- Two-pass scan
# ===================================================================


class TestTwoPassScan:
    def test_pass1_catches_field_secret(self):
        record = TraceRecord(
            trace_id="test-2pass",
            session_id="sess-2pass",
            agent=Agent(name="test"),
            steps=[
                Step(
                    step_index=0,
                    role="agent",
                    content="Key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
                )
            ],
        )
        pass1, pass2 = two_pass_scan(record)
        assert pass1.redaction_count > 0

    def test_pass2_scans_serialized(self):
        record = TraceRecord(
            trace_id="test-2pass",
            session_id="sess-2pass",
            agent=Agent(name="test"),
            steps=[
                Step(
                    step_index=0,
                    role="agent",
                    content="Key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
                )
            ],
        )
        _pass1, pass2 = two_pass_scan(record)
        # Pass 2 scans the full JSON, should also find the key
        assert pass2.redaction_count > 0

    def test_clean_record_no_flags(self):
        record = TraceRecord(
            trace_id="clean",
            session_id="sess-clean",
            agent=Agent(name="test"),
            steps=[
                Step(step_index=0, role="user", content="Hello world"),
            ],
        )
        pass1, _pass2 = two_pass_scan(record)
        assert pass1.redaction_count == 0


# ===================================================================
# classifier.py -- Heuristic classification
# ===================================================================


class TestClassifierInternalHostname:
    def test_detects_internal_hostname(self):
        result = classify_content("Connect to db.internal for production data")
        assert any(f.pattern_name == "internal_hostname" for f in result.flags)

    def test_detects_corp_hostname(self):
        result = classify_content("api.corp is the main endpoint")
        assert any(f.pattern_name == "internal_hostname" for f in result.flags)

    def test_detects_local_hostname(self):
        result = classify_content("service.local is running")
        assert any(f.pattern_name == "internal_hostname" for f in result.flags)

    def test_no_flag_for_normal_domain(self):
        result = classify_content("Visit example.com for docs")
        assert not any(f.pattern_name == "internal_hostname" for f in result.flags)


class TestClassifierAWSAccountID:
    def test_detects_aws_account_in_arn(self):
        result = classify_content("arn:aws:s3:us-east-1:123456789012:bucket/data")
        assert any(f.pattern_name == "aws_account_id" for f in result.flags)

    def test_no_flag_without_arn(self):
        result = classify_content("The number 123456789012 appeared")
        assert not any(f.pattern_name == "aws_account_id" for f in result.flags)


class TestClassifierDBConnectionString:
    def test_detects_jdbc(self):
        result = classify_content("jdbc:postgresql://db.internal:5432/mydb")
        assert any(f.pattern_name == "db_connection_string" for f in result.flags)

    def test_detects_mongodb_srv(self):
        result = classify_content("mongodb+srv://user:pass@cluster.mongodb.net/db")
        assert any(f.pattern_name == "db_connection_string" for f in result.flags)


class TestClassifierInternalURL:
    def test_detects_jira(self):
        result = classify_content("See https://jira.company.com/browse/PROJ-123")
        assert any(f.pattern_name == "internal_url" for f in result.flags)

    def test_detects_confluence(self):
        result = classify_content("Docs at https://confluence.company.com/wiki/page")
        assert any(f.pattern_name == "internal_url" for f in result.flags)

    def test_detects_atlassian(self):
        result = classify_content("https://myteam.atlassian.net/browse/TASK-1")
        assert any(f.pattern_name == "internal_url" for f in result.flags)

    def test_detects_slack_archives(self):
        result = classify_content("https://mycompany.slack.com/archives/C01234567")
        assert any(f.pattern_name == "internal_url" for f in result.flags)


class TestClassifierIdentifierDensity:
    def test_high_uuid_density(self):
        uuids = " ".join(
            f"id-{i} 550e8400-e29b-41d4-a716-44665544000{i}" for i in range(5)
        )
        text = f"Processing records: {uuids}"
        result = classify_content(text, sensitivity="high")
        assert any(f.pattern_name == "identifier_density" for f in result.flags)

    def test_low_density_ok(self):
        text = "One uuid 550e8400-e29b-41d4-a716-446655440000 in long text " * 10
        result = classify_content(text, sensitivity="low")
        # With low sensitivity, only high-confidence matters
        density_flags = [f for f in result.flags if f.pattern_name == "identifier_density"]
        # Low density should not trigger
        assert len(density_flags) == 0


class TestClassifierFilePathDepth:
    def test_deep_path_flagged(self):
        text = "/very/deep/nested/path/to/some/internal/system/config/file.yaml"
        result = classify_content(text, sensitivity="high")
        assert any(f.pattern_name == "deep_file_path" for f in result.flags)


class TestClassifierSensitivity:
    def test_low_sensitivity_fewer_flags(self):
        text = "service.local and https://jira.company.com/browse/T-1"
        result_low = classify_content(text, sensitivity="low")
        result_high = classify_content(text, sensitivity="high")
        assert len(result_high.flags) >= len(result_low.flags)


class TestClassifyTraceRecord:
    def test_classifies_step_content(self):
        record = TraceRecord(
            trace_id="cls-test",
            session_id="sess-cls",
            agent=Agent(name="test"),
            steps=[
                Step(
                    step_index=0,
                    role="agent",
                    content="Deploy to api.internal using jdbc:postgresql://db:5432/prod",
                )
            ],
        )
        result = classify_trace_record(record)
        assert len(result.flags) > 0
        assert result.risk_score > 0.0


class TestClassifierRiskScore:
    def test_risk_score_bounded(self):
        text = (
            "arn:aws:s3:us-east-1:123456789012:bucket "
            "db.internal api.corp service.local "
            "jdbc:postgresql://db.internal:5432/mydb "
            "https://jira.company.com/browse/T-1"
        )
        result = classify_content(text)
        assert 0.0 <= result.risk_score <= 1.0


# ===================================================================
# redactor.py -- RedactingFilter
# ===================================================================


class TestRedactingFilter:
    def test_redacts_secret_in_log_message(self):
        filt = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="API key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
            args=None,
            exc_info=None,
        )
        filt.filter(record)
        assert "sk-ant-" not in record.msg
        assert "[REDACTED]" in record.msg

    def test_redacts_secret_in_args_tuple(self):
        filt = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="Key: %s",
            args=("ghp_ABCDEFGHIJKLMNOPQRSTuvwxyz",),
            exc_info=None,
        )
        filt.filter(record)
        assert "ghp_" not in record.args[0]

    def test_redacts_secret_in_args_dict(self):
        filt = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="%(key)s",
            args=({"key": "hf_ABCDEFGHIJKLMNOPQRSTuvwxyz"},),
            exc_info=None,
        )
        # LogRecord unpacks single-element mapping tuples; manually set dict args
        record.args = {"key": "hf_ABCDEFGHIJKLMNOPQRSTuvwxyz"}
        filt.filter(record)
        assert "hf_" not in record.args["key"]

    def test_returns_true_always(self):
        filt = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="clean message",
            args=None,
            exc_info=None,
        )
        assert filt.filter(record) is True

    def test_no_entropy_by_default(self):
        filt = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="Xk9mZr3pWq7vNt2sLf6yBh4jCe8gAa5d",
            args=None,
            exc_info=None,
        )
        original_msg = record.msg
        filt.filter(record)
        # Without entropy scanning, high-entropy string should not be redacted
        assert record.msg == original_msg

    def test_entropy_when_enabled(self):
        filt = RedactingFilter(include_entropy=True)
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="Xk9mZr3pWq7vNt2sLf6yBh4jCe8gAa5d",
            args=None,
            exc_info=None,
        )
        filt.filter(record)
        assert "[REDACTED]" in record.msg


# ===================================================================
# anonymizer.py -- Username auto-detection from paths
# ===================================================================


class TestExtractUsernamesFromPaths:
    def test_macos_path(self):
        text = "Found file at /Users/alice/src/project/main.py"
        result = extract_usernames_from_paths(text)
        assert "alice" in result

    def test_linux_path(self):
        text = "Config at /home/developer/config.yml"
        result = extract_usernames_from_paths(text)
        assert "developer" in result

    def test_windows_backslash_path(self):
        text = r"File: C:\Users\bobsmith\Documents\file.txt"
        result = extract_usernames_from_paths(text)
        assert "bobsmith" in result

    def test_windows_forward_slash_path(self):
        text = "Path: C:/Users/charlie/code/app.py"
        result = extract_usernames_from_paths(text)
        assert "charlie" in result

    def test_multiple_usernames(self):
        text = "/Users/alice/src and /home/bob123/code and C:/Users/charlie_dev/file"
        result = extract_usernames_from_paths(text)
        assert result == {"alice", "bob123", "charlie_dev"}

    def test_system_usernames_filtered(self):
        text = "/Users/Shared/data and /home/runner/work and /Users/admin/config"
        result = extract_usernames_from_paths(text)
        assert "Shared" not in result
        assert "runner" not in result
        assert "admin" not in result

    def test_no_paths(self):
        text = "Just some normal text with no file paths anywhere."
        result = extract_usernames_from_paths(text)
        assert result == set()

    def test_short_names_excluded(self):
        # Min 3 chars, must start with letter
        text = "/Users/ab/file and /Users/x/file"
        result = extract_usernames_from_paths(text)
        assert "ab" not in result
        assert "x" not in result

    def test_numeric_id_names(self):
        """Numeric IDs like 06506792 (employee IDs) should be captured."""
        text = "/Users/06506792/dotfiles"
        result = extract_usernames_from_paths(text)
        assert "06506792" in result
        # If not caught, we need to adjust the regex.

    def test_deduplicated(self):
        text = "/Users/alice/a and /Users/alice/b and /home/alice/c"
        result = extract_usernames_from_paths(text)
        assert result == {"alice"}

    def test_does_not_extract_hyphen_encoded(self):
        text = f"-Users-{TEST_USERNAME}-src-project"
        result = extract_usernames_from_paths(text)
        assert TEST_USERNAME not in result

    def test_does_not_extract_tilde(self):
        text = f"~{TEST_USERNAME}/documents/file.txt"
        result = extract_usernames_from_paths(text)
        assert TEST_USERNAME not in result


class TestAutoDetectAnonymization:
    def test_foreign_username_anonymized(self):
        """A username found in /Users/<name>/ but not passed explicitly gets anonymized."""
        text = "Found at /Users/foreign_user/src/main.py"
        result = anonymize_paths(text, username=TEST_USERNAME)
        assert "foreign_user" not in result
        expected_hash = hash_username("foreign_user")
        assert f"/Users/{expected_hash}/" in result

    def test_both_explicit_and_detected(self):
        """Both the explicit username and auto-detected ones get anonymized."""
        text = f"/Users/{TEST_USERNAME}/src/a.py and /Users/colleague/src/b.py"
        result = anonymize_paths(text, username=TEST_USERNAME)
        assert TEST_USERNAME not in result
        assert "colleague" not in result

    def test_explicit_gets_full_patterns(self):
        """Explicit username gets hyphen/tilde patterns, auto-detected does not."""
        text = f"-Users-{TEST_USERNAME}-src and -Users-foreign-src"
        result = anonymize_paths(text, username=TEST_USERNAME)
        assert TEST_USERNAME not in result
        # foreign is auto-detected but hyphen pattern not applied for auto-detected
        # However, "foreign" only appears in hyphen-encoded, not in /Users/foreign/
        # So it won't even be auto-detected
        assert "-Users-foreign-src" in result  # unchanged

    def test_hash_stability(self):
        """Auto-detected username hashes identically to explicit."""
        text = "/Users/testuser123/file.py"
        auto_result = anonymize_paths(text, username="other")
        explicit_result = anonymize_paths(text, username="testuser123")
        # Both should produce the same hash for testuser123
        expected_hash = hash_username("testuser123")
        assert f"/Users/{expected_hash}/" in auto_result
        assert f"/Users/{expected_hash}/" in explicit_result

    def test_system_path_not_anonymized(self):
        """/Users/Shared/ should not be anonymized."""
        text = "/Users/Shared/data/file.csv"
        result = anonymize_paths(text, username=TEST_USERNAME)
        assert "/Users/Shared/" in result  # unchanged

    def test_many_auto_detected_usernames(self):
        """Many auto-detected usernames should all be anonymized."""
        names = [f"user{i:03d}" for i in range(15)]
        paths = " ".join(f"/Users/{n}/file.py" for n in names)
        text = f"/Users/{TEST_USERNAME}/a.py {paths}"
        result = anonymize_paths(text, username=TEST_USERNAME)
        assert TEST_USERNAME not in result
        for name in names:
            assert f"/Users/{name}/" not in result, f"Expected {name} to be anonymized"


class TestSecurityPipelineDispatch:
    """Test that the security pipeline runs scan, redact, and classify."""

    def _make_trace(self) -> TraceRecord:
        return TraceRecord(
            trace_id="pipeline-dispatch-test",
            session_id="test-session",
            agent=Agent(name="claude-code", version="2.0"),
            steps=[
                Step(
                    step_index=0,
                    role="user",
                    content="Fix the bug in auth.py",
                ),
                Step(
                    step_index=1,
                    role="agent",
                    content="I'll look at auth.py",
                    tool_calls=[
                        ToolCall(
                            tool_call_id="tc1",
                            tool_name="Read",
                            input={"file_path": "/Users/testuser/src/auth.py"},
                        ),
                    ],
                    observations=[
                        Observation(
                            source_call_id="tc1",
                            content="def authenticate(user):\n    pass",
                        ),
                    ],
                ),
            ],
            outcome=Outcome(),
        )

    def test_scan_and_redact(self):
        """Pipeline should run two_pass_scan + apply_redactions."""
        from opentraces.security.scanner import two_pass_scan, apply_redactions
        record = self._make_trace()
        pass1, pass2 = two_pass_scan(record)
        _redactions = apply_redactions(record)
        assert record.security is not None
        assert record.security.redactions_applied >= 0

    def test_classifier(self):
        """Pipeline should run the classifier."""
        from opentraces.security.classifier import classify_trace_record, ClassifierResult
        record = self._make_trace()
        result = classify_trace_record(record, "medium")
        assert isinstance(result, ClassifierResult)
        assert isinstance(result.flags, list)

    def test_fresh_record_has_defaults(self):
        """A fresh record before pipeline should have default security fields."""
        record = self._make_trace()
        assert record.security.scanned is False
        assert record.security.redactions_applied == 0
        assert record.security.classifier_version is None
        assert record.security.flags_reviewed == 0


class TestAdversarialSecurityPipeline:
    """Test security pipeline with synthetic traces containing known secrets."""

    def _make_hostile_trace(self) -> TraceRecord:
        """Create a trace full of embedded secrets for testing."""
        secret_content = """
Here's the .env file:
ANTHROPIC_API_KEY=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
DATABASE_URL=postgresql://admin:supersecret@prod-db.internal.corp:5432/maindb
SLACK_TOKEN=xoxb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx

Also found this SSH key:
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGcY5unA
-----END RSA PRIVATE KEY-----

Credit card: 4111111111111111
Email: john.doe@internal.corp
Internal URL: https://jira.internal.corp/browse/SEC-123
"""
        return TraceRecord(
            trace_id="adversarial-test",
            session_id="adversarial-test",
            agent=Agent(name="claude-code", version="2.0"),
            steps=[
                Step(step_index=0, role="user", content="Show me the .env file"),
                Step(
                    step_index=1,
                    role="agent",
                    content=secret_content,
                    tool_calls=[
                        ToolCall(
                            tool_call_id="tc1",
                            tool_name="Bash",
                            input={"command": f"cat /Users/victim/project/.env"},
                        ),
                    ],
                    observations=[
                        Observation(
                            source_call_id="tc1",
                            content=secret_content,
                        ),
                    ],
                ),
            ],
            outcome=Outcome(),
        )

    def test_redacts_secrets(self):
        """All secrets should be auto-redacted by the pipeline."""
        from opentraces.security.scanner import two_pass_scan, apply_redactions
        record = self._make_hostile_trace()
        pass1, pass2 = two_pass_scan(record)
        redactions = apply_redactions(record)
        assert redactions > 0, "Expected redactions for embedded secrets"
        serialized = record.to_jsonl_line()
        assert "sk-ant-api03" not in serialized
        assert "AKIAIOSFODNN7EXAMPLE" not in serialized
        assert "BEGIN RSA PRIVATE KEY" not in serialized
        assert "supersecret" not in serialized

    def test_flags_internal_urls(self):
        """Classifier should flag internal hostnames/URLs."""
        from opentraces.security.scanner import two_pass_scan, apply_redactions
        from opentraces.security.classifier import classify_trace_record
        record = self._make_hostile_trace()
        two_pass_scan(record)
        apply_redactions(record)
        result = classify_trace_record(record, "medium")
        # Should flag internal.corp hostname or jira URL
        assert len(result.flags) > 0, "Expected classifier flags for internal URLs"

    def test_unprocessed_record_retains_secrets(self):
        """A record that hasn't been through the pipeline retains raw content."""
        record = self._make_hostile_trace()
        serialized = record.to_jsonl_line()
        assert "sk-ant-api03" in serialized
        assert "AKIAIOSFODNN7EXAMPLE" in serialized

    def test_path_anonymization_catches_foreign_username(self):
        """Auto-detect should catch /Users/victim/ even though it's not the current user."""
        record = self._make_hostile_trace()
        serialized = record.to_jsonl_line()
        result = anonymize_paths(serialized, username=TEST_USERNAME)
        assert "/Users/victim/" not in result
        expected_hash = hash_username("victim")
        assert f"/Users/{expected_hash}/" in result
