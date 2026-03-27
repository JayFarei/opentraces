"""Path and username anonymization.

Strips usernames from file paths across all major OS conventions
(macOS, Linux, Windows, WSL, WSL UNC, tilde, hyphen-encoded).
"""

from __future__ import annotations

import hashlib
import os
import re


def hash_username(username: str) -> str:
    """Return 8-char hex SHA-256 prefix for a username."""
    return hashlib.sha256(username.encode("utf-8")).hexdigest()[:8]


def _get_system_username() -> str | None:
    """Best-effort system username detection."""
    try:
        return os.getlogin()
    except OSError:
        return os.environ.get("USER") or os.environ.get("USERNAME")


def _build_patterns(usernames: list[str]) -> list[tuple[re.Pattern, str]]:
    """Build replacement patterns for a list of usernames."""
    patterns: list[tuple[re.Pattern, str]] = []
    for uname in usernames:
        escaped = re.escape(uname)
        hashed = hash_username(uname)

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

        # Hyphen-encoded (e.g., -Users-jayfarei-)
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
    """Anonymize user paths in text.

    Detects and replaces home directory paths containing the system username
    (or provided usernames) with hashed equivalents.

    Args:
        text: The text to anonymize.
        username: Override the system username. If None, auto-detects.
        extra_usernames: Additional usernames to anonymize (e.g., GitHub handles).

    Returns:
        Text with user paths anonymized.
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

    if not usernames:
        return text

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for u in usernames:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    patterns = _build_patterns(unique)

    result = text
    for pattern, replacement in patterns:
        result = pattern.sub(replacement, result)

    return result
