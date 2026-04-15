"""Guard the EntityRunner.version() upstream-name strip.

Upstream binary prints ``sem 0.3.19``; doctor / help must never show the
``sem`` token. This test stubs a fake binary so we don't require the real
one to be installed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from opentraces.enrichment.entities.runner import EntityRunner


def _mk_fake_binary(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fakebin"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return p


def test_version_strips_sem_prefix(tmp_path):
    bin_ = _mk_fake_binary(tmp_path, 'printf "sem 0.3.19\\n"')
    v = EntityRunner(bin_).version()
    assert v == "0.3.19"


def test_version_case_insensitive_prefix(tmp_path):
    bin_ = _mk_fake_binary(tmp_path, 'printf "SEM 1.0.0\\n"')
    v = EntityRunner(bin_).version()
    assert v == "1.0.0"


def test_version_no_prefix_unchanged(tmp_path):
    bin_ = _mk_fake_binary(tmp_path, 'printf "0.3.19\\n"')
    v = EntityRunner(bin_).version()
    assert v == "0.3.19"


def test_version_missing_binary():
    runner = EntityRunner(Path("/nonexistent/fake-binary-xyz"))
    assert runner.version() is None
