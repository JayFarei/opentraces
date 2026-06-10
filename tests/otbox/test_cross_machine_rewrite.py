"""Cross-machine snapshot path-rewrite (otbox 2.0, nightly hardening).

A capture artifact made on one machine (e.g. /Users/... on a mac) must
restore cleanly on another (e.g. /home/runner/... on CI). The first on-main
nightly's entire restored-world failure cluster traced to the rewrite keying
on the CURRENT machine's boxes_root, so foreign-prefix box paths in trace
records / index SQLite were never repointed -> trace query found nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

from .snapshot import _ANY_BOX_ROOT_RE, _replace_box_roots


def test_rewrites_foreign_machine_prefix():
    new_root = "/home/runner/work/opentraces/opentraces/.otbox/boxes/otb_NEW"
    text = (
        '{"trace": "/Users/jayfarei/src/tries/proj/.otbox/boxes/otb_30812f5f/home/t.jsonl", '
        '"repo": "/Users/someone/.otbox/boxes/otb_abc123/project"}'
    )
    out = _replace_box_roots(
        text,
        old_root="/home/runner/x/.otbox/boxes/otb_unused",
        new_root=new_root,
        boxes_root=Path("/home/runner/x/.otbox/boxes"),
    )
    # no foreign box-paths survive; every one became the new root
    assert not re.search(r"/Users/[^\"]*?/\.otbox/boxes/otb_", out)
    assert out.count(new_root) == 2


def test_regex_does_not_overmatch_across_delimiters():
    # two distinct paths on one line must not be merged into one match
    text = '"/a/.otbox/boxes/otb_1" "/b/.otbox/boxes/otb_2"'
    matches = _ANY_BOX_ROOT_RE.findall(text)
    assert len(matches) == 2
    assert matches[0].endswith("otb_1")
    assert matches[1].endswith("otb_2")


def test_same_machine_rewrite_still_works():
    boxes = Path("/Users/x/.otbox/boxes")
    out = _replace_box_roots(
        "/Users/x/.otbox/boxes/otb_old/home/c.json",
        old_root="/Users/x/.otbox/boxes/otb_old",
        new_root="/Users/x/.otbox/boxes/otb_new",
        boxes_root=boxes,
    )
    assert "otb_old" not in out
    assert "otb_new" in out
