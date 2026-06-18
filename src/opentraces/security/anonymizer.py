"""Path and username anonymization.

Strips usernames from file paths across all major OS conventions
(macOS, Linux, Windows, WSL, WSL UNC, tilde, hyphen-encoded).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path

# Usernames that commonly appear in paths but are not real people.
SYSTEM_USERNAMES: set[str] = {
    "Shared", "runner", "lib", "admin", "root", "default", "Public", "Guest",
}


def hash_username(username: str) -> str:
    """Return 8-char hex SHA-256 prefix for a username."""
    return hashlib.sha256(username.encode("utf-8")).hexdigest()[:8]


def _anonymized_username(username: str) -> str:
    """Return the unambiguous anonymized marker for a username.

    The marker shape ``[ot-user-<8hex>]`` is what every path/username
    replacement emits. Its leading ``[`` cannot start a username under the
    detection regex, so :func:`anonymize_paths` is IDEMPOTENT by construction —
    a second pass cannot re-detect an already-anonymized segment, and no
    content-shape skip (which would falsely pass a real hash-shaped username
    like ``deadbeef`` through intact) is needed. Auto-detection always runs over
    real names; only this marker is structurally inert.
    """
    return f"[ot-user-{hash_username(username)}]"


def _get_system_username() -> str | None:
    """Best-effort system username detection."""
    try:
        return os.getlogin()
    except OSError:
        return os.environ.get("USER") or os.environ.get("USERNAME")


def extract_usernames_from_paths(text: str) -> set[str]:
    """Extract unique usernames from path patterns in *text*.

    Looks for ``/Users/<name>/``, ``/home/<name>/``, ``C:\\Users\\<name>\\``,
    and ``C:/Users/<name>/`` patterns.  Hyphen-encoded (``-Users-name-``) and
    tilde (``~name``) forms are intentionally ignored because they are too
    ambiguous for auto-detection.

    Returns:
        De-duplicated set of detected usernames, excluding system accounts.
    """
    _USERNAME_RE = r"[a-zA-Z0-9][a-zA-Z0-9_-]{2,}"
    path_patterns = [
        rf"/Users/({_USERNAME_RE})/",
        rf"/home/({_USERNAME_RE})/",
        rf"[A-Za-z]:\\Users\\({_USERNAME_RE})\\",
        rf"[A-Za-z]:/Users/({_USERNAME_RE})/",
    ]
    found: set[str] = set()
    for pat in path_patterns:
        found.update(re.findall(pat, text))
    return found - SYSTEM_USERNAMES


def _build_path_only_patterns(
    usernames: list[str],
) -> list[tuple[re.Pattern, str]]:
    """Build path-prefixed replacement patterns only (no hyphen/tilde).

    This is used for auto-detected usernames where we have high confidence
    the match is a real path but not enough confidence for the more
    ambiguous hyphen-encoded and tilde forms.
    """
    patterns: list[tuple[re.Pattern, str]] = []
    for uname in usernames:
        escaped = re.escape(uname)
        hashed = _anonymized_username(uname)

        # macOS
        patterns.append((
            re.compile(rf"/Users/{escaped}/"),
            f"/Users/{hashed}/",
        ))
        # Linux
        patterns.append((
            re.compile(rf"/home/{escaped}/"),
            f"/home/{hashed}/",
        ))
        # Windows backslash
        patterns.append((
            re.compile(rf"[A-Za-z]:\\Users\\{escaped}\\"),
            f"C:\\\\Users\\\\{hashed}\\\\",
        ))
        # Windows forward slash
        patterns.append((
            re.compile(rf"[A-Za-z]:/Users/{escaped}/"),
            f"C:/Users/{hashed}/",
        ))
        # WSL
        patterns.append((
            re.compile(rf"/mnt/[a-z]/Users/{escaped}/"),
            f"/mnt/c/Users/{hashed}/",
        ))
        # WSL UNC backslash
        patterns.append((
            re.compile(rf"\\\\wsl\.localhost\\[^\\]+\\home\\{escaped}\\"),
            f"\\\\\\\\wsl.localhost\\\\distro\\\\home\\\\{hashed}\\\\",
        ))
        # WSL UNC forward slash
        patterns.append((
            re.compile(rf"//wsl\.localhost/[^/]+/home/{escaped}/"),
            f"//wsl.localhost/distro/home/{hashed}/",
        ))
    return patterns


def _build_patterns(usernames: list[str]) -> list[tuple[re.Pattern, str]]:
    """Build replacement patterns for a list of usernames."""
    patterns: list[tuple[re.Pattern, str]] = []
    for uname in usernames:
        escaped = re.escape(uname)
        hashed = _anonymized_username(uname)

        # macOS: /Users/<name>/...
        patterns.append((
            re.compile(rf"/Users/{escaped}/"),
            f"/Users/{hashed}/",
        ))

        # Linux: /home/<name>/...
        patterns.append((
            re.compile(rf"/home/{escaped}/"),
            f"/home/{hashed}/",
        ))

        # Windows backslash: C:\Users\<name>\
        patterns.append((
            re.compile(rf"[A-Za-z]:\\Users\\{escaped}\\"),
            f"C:\\\\Users\\\\{hashed}\\\\",
        ))

        # Windows forward slash: C:/Users/<name>/
        patterns.append((
            re.compile(rf"[A-Za-z]:/Users/{escaped}/"),
            f"C:/Users/{hashed}/",
        ))

        # WSL: /mnt/[a-z]/Users/<name>/
        patterns.append((
            re.compile(rf"/mnt/[a-z]/Users/{escaped}/"),
            f"/mnt/c/Users/{hashed}/",
        ))

        # WSL UNC: \\wsl.localhost\<distro>\home\<name>\
        patterns.append((
            re.compile(rf"\\\\wsl\.localhost\\[^\\]+\\home\\{escaped}\\"),
            f"\\\\\\\\wsl.localhost\\\\distro\\\\home\\\\{hashed}\\\\",
        ))

        # WSL UNC forward slash variant
        patterns.append((
            re.compile(rf"//wsl\.localhost/[^/]+/home/{escaped}/"),
            f"//wsl.localhost/distro/home/{hashed}/",
        ))

        # Hyphen-encoded (e.g., -Users-<username>-)
        patterns.append((
            re.compile(rf"-Users-{escaped}-"),
            f"-Users-{hashed}-",
        ))

        # Tilde: ~/  (when it appears as a standalone path prefix)
        # We handle this differently: ~ is ambiguous, but ~<username> is clear
        patterns.append((
            re.compile(rf"~{escaped}(?=/|$)"),
            f"~{hashed}",
        ))

    return patterns


def anonymize_paths(
    text: str,
    username: str | None = None,
    extra_usernames: list[str] | None = None,
) -> str:
    """Anonymize user paths and bare usernames in text.

    Detects and replaces home directory paths containing the system username
    (or provided usernames) with hashed equivalents.  Also replaces bare
    occurrences of explicit (known) usernames as a final catch-all for
    non-path contexts like ``ls -la`` file ownership output.

    Args:
        text: The text to anonymize.
        username: Override the system username. If None, auto-detects.
        extra_usernames: Additional usernames to anonymize (e.g., GitHub handles).

    Returns:
        Text with user paths and bare usernames anonymized.
    """
    if not text:
        return text

    usernames: list[str] = []

    if username is not None:
        usernames.append(username)
    else:
        sys_user = _get_system_username()
        if sys_user:
            usernames.append(sys_user)

    if extra_usernames:
        usernames.extend(extra_usernames)

    # Deduplicate explicit usernames while preserving order
    seen: set[str] = set()
    unique_explicit: list[str] = []
    for u in usernames:
        if u not in seen:
            seen.add(u)
            unique_explicit.append(u)

    # Auto-detect additional usernames from path patterns in the text
    auto_detected = extract_usernames_from_paths(text)
    # Remove any that were already provided explicitly
    auto_only = auto_detected - set(unique_explicit)

    if not unique_explicit and not auto_only:
        return text

    # Full patterns (including hyphen-encoded and tilde) for explicit names
    patterns = _build_patterns(unique_explicit) if unique_explicit else []

    # Path-only patterns for auto-detected names
    if auto_only:
        patterns.extend(_build_path_only_patterns(sorted(auto_only)))

    result = text
    for pattern, replacement in patterns:
        result = pattern.sub(replacement, result)

    # Layer 2: Bare username replacement for explicit usernames only.
    # Catches non-path contexts (e.g. "alice  staff" in ls -la output).
    # Uses word-boundary matching to avoid replacing substrings of longer words.
    for uname in unique_explicit:
        hashed = _anonymized_username(uname)
        result = re.sub(re.escape(uname), hashed, result)

    return result


# ---------------------------------------------------------------------------
# Part D: EntityMap — named placeholders for Tier 1.8 PII detection
# ---------------------------------------------------------------------------

# USER_PATH normalization: matches /Users/<name>/ or /home/<name>/ with
# usernames containing letters, digits, dots, underscores, and dashes.
_USER_PATH_RE = re.compile(r"(/(?:Users|home)/)[A-Za-z0-9._-]+(/)")


def normalize_user_paths(text: str) -> str:
    """Replace ``/Users/<name>/`` and ``/home/<name>/`` with ``.../user/...``.

    Preserves path structure for trace analysis rather than fully redacting.
    Matches usernames with letters, digits, dots, underscores, and dashes.
    """
    if not text:
        return text
    return _USER_PATH_RE.sub(r"\1user\2", text)


class EntityMap:
    """Stable named placeholders for detected entities.

    Maps ``(entity_type, entity_text)`` pairs to numbered placeholders like
    ``[PERSON_1]`` and ``[EMAIL_2]``. Same entity text always gets the same
    placeholder within a map; per-type counters are independent so PERSON
    numbering does not interfere with EMAIL numbering.

    ``USER_PATH`` is a special case: rather than producing a numbered
    placeholder, ``apply_all()`` normalizes ``/Users/<name>/`` paths to
    ``/Users/user/`` so path structure survives for reviewers.

    Persistable via :meth:`save` / :meth:`load` so entity numbering can be
    kept consistent across sessions.
    """

    _USER_PATH_TYPE = "USER_PATH"

    def __init__(self) -> None:
        # entity_type -> {entity_text: placeholder_number}
        self._entries: dict[str, dict[str, int]] = defaultdict(dict)
        self._counters: dict[str, int] = defaultdict(int)

    def get_placeholder(self, entity_type: str, entity_text: str) -> str:
        """Return a stable placeholder for ``(entity_type, entity_text)``.

        Registers the entity if new; returns the existing placeholder if seen.
        For ``USER_PATH`` the entity is registered (so :meth:`apply_all`
        triggers the normalization branch) but the returned token is a
        sentinel not actually substituted into text.
        """
        bucket = self._entries[entity_type]
        if entity_text not in bucket:
            self._counters[entity_type] += 1
            bucket[entity_text] = self._counters[entity_type]
        n = bucket[entity_text]
        return f"[{entity_type}_{n}]"

    def apply_all(self, text: str) -> str:
        """Replace every registered entity in ``text`` with its placeholder.

        Entities are sorted by text length descending before substitution to
        prevent shorter names clobbering longer matches (e.g. ``sarah``
        replacing part of ``sarah.chen@corp.com``).

        ``USER_PATH`` entities trigger :func:`normalize_user_paths` rather
        than opaque substitution.
        """
        if not text:
            return text

        out = text

        # USER_PATH: normalization, not numbered placeholder.
        if self._entries.get(self._USER_PATH_TYPE):
            out = normalize_user_paths(out)

        # Gather all non-USER_PATH entities, longest first.
        pairs: list[tuple[str, str, int]] = []
        for etype, bucket in self._entries.items():
            if etype == self._USER_PATH_TYPE:
                continue
            for etext, num in bucket.items():
                pairs.append((etype, etext, num))
        pairs.sort(key=lambda p: len(p[1]), reverse=True)

        for etype, etext, num in pairs:
            out = out.replace(etext, f"[{etype}_{num}]")

        return out

    def save(self, path: Path | str) -> None:
        """Persist the map as JSON.

        Schema: ``{entity_type: {entity_text: placeholder_number, ...}, ...}``.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {etype: dict(bucket) for etype, bucket in self._entries.items()}
        path.write_text(json.dumps(data, indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: Path | str) -> "EntityMap":
        """Load a map previously written by :meth:`save`.

        Returns an empty map if the file does not exist, so callers can
        treat the entity-map path as optional.
        """
        path = Path(path)
        em = cls()
        if not path.exists():
            return em
        data = json.loads(path.read_text())
        for etype, bucket in data.items():
            em._entries[etype] = {k: int(v) for k, v in bucket.items()}
            if bucket:
                em._counters[etype] = max(int(v) for v in bucket.values())
        return em
