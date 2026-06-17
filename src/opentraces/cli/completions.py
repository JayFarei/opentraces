"""`opentraces completions` noun.

Shell completion for ``ot`` via dynamic delegation: the installed shell
script forwards every completion query to a hidden ``ot __complete``
resolver. The script is intentionally dumb — all command-tree knowledge
lives in Python, so new verbs and options light up automatically.

What the menu shows:
    * subcommand names grouped by section with colored headings
    * option flags (``--foo``) with their help text
    * argument values that teach state — trace IDs with stage + age,
      shell names for ``ot completions``

Surface:
    ot completions [shell]                  Print script (auto-detects $SHELL)
    ot completions install [shell]          Install (auto-detects $SHELL)
        ``--alias NAME``                    Also bind completion to NAME (e.g. otd)
    ot completions uninstall [shell]        Remove installation
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import click

from ._help import OpentracesGroup

SUPPORTED_SHELLS = ("bash", "zsh", "fish")

EPILOG = """\
\b
EXAMPLES
  $ ot completions                 Output completion script for detected shell
  $ ot completions zsh             Output zsh completion script
  $ ot completions install         Install completions for detected shell
  $ ot completions install zsh     Install zsh completions explicitly
  $ ot completions install zsh --alias otd
                                   Also bind completion to a custom name
  $ ot completions uninstall       Remove completions
  $ source <(ot completions)       Load completions in current session
"""

# Script templates. ``__OT_BIN__`` and the command-name blocks are
# substituted at install time so completions keep working for dev
# aliases (otd), pipx shims, or venv-local installs where ``ot`` may
# not be on PATH. Bare-print (``ot completions zsh``) leaves the
# placeholders as ``ot`` for portability.

ZSH_SCRIPT = """\
#compdef __OT_NAMES__
# opentraces shell completions — flat menu, name on left, desc on right.
# Colors come from the list-colors zstyle at the bottom; injecting ANSI
# directly into compadd display strings breaks zsh's width math, so we
# use the canonical zsh coloring path instead.
_ot() {
  local cmd="__OT_BIN__"
  local raw
  raw="$("$cmd" __complete "${words[@]:1}" 2>/dev/null)" || return 1
  [[ -z "$raw" ]] && return 1

  local -a items
  local name desc section
  while IFS=$'\\t' read -r name desc section; do
    [[ -z "$name" ]] && continue
    # Escape colons in names so _describe parses the name:desc split correctly.
    items+=("${name//:/\\\\:}:${desc}")
  done <<< "$raw"
  (( ${#items[@]} )) && _describe 'opentraces' items
}

# Scoped coloring: yellow heading, cyan names, dim descriptions.
# list-colors is zsh's ANSI-aware color engine — unlike raw ANSI in
# compadd display strings, it understands width and doesn't break layout.
#
# Pattern: (#b) enables backreferences. We match ``<name>  --  <desc>``
# which is the format _describe renders into the menu. Colors apply
# positionally: the first color is for the whole match (kept default),
# then one per captured group.
zstyle ':completion:*:*:(ot|otd|opentraces):*:descriptions' \\
  format $'\\e[33m%d\\e[0m'
zstyle ':completion:*:*:(ot|otd|opentraces):*' list-colors \\
  '=(#b)([^ ]##)(*)=0=1;36=2'
# Arrow-key navigation inside the menu once there's more than one match.
# Press TAB to open, arrow keys to move, Enter to insert, Esc to cancel.
zstyle ':completion:*:*:(ot|otd|opentraces):*' menu select=2

compdef _ot __OT_NAMES__
"""

BASH_SCRIPT = """\
# bash completion for ot (opentraces).
# bash can't render descriptions, so we drop them — only names flow through.
__ot_bin="__OT_BIN__"
_ot_completions() {
  local words_to_send
  words_to_send=("${COMP_WORDS[@]:1}")
  local IFS=$'\\n'
  COMPREPLY=( $("$__ot_bin" __complete "${words_to_send[@]}" 2>/dev/null | awk -F'\\t' '$1!="" {print $1}') )
  return 0
}
__OT_BASH_BINDS__
"""

FISH_SCRIPT = """\
# fish completion for ot (opentraces).
set -g __ot_bin "__OT_BIN__"
function __ot_complete
  set -l tokens (commandline -opc) (commandline -ct)
  set -e tokens[1]
  $__ot_bin __complete $tokens 2>/dev/null | awk -F'\\t' '$1!="" { if ($2=="") print $1; else printf "%s\\t%s\\n", $1, $2 }'
end
__OT_FISH_BINDS__
"""

SCRIPTS = {"zsh": ZSH_SCRIPT, "bash": BASH_SCRIPT, "fish": FISH_SCRIPT}


def _detect_shell() -> str:
    """Detect user's shell from $SHELL; default to bash."""
    shell_env = os.environ.get("SHELL", "")
    name = os.path.basename(shell_env).lower()
    if name in SUPPORTED_SHELLS:
        return name
    if "zsh" in name:
        return "zsh"
    if "fish" in name:
        return "fish"
    return "bash"


def _resolve_shell(shell: str | None) -> str:
    if shell is None:
        return _detect_shell()
    if shell not in SUPPORTED_SHELLS:
        raise click.UsageError(
            f"Unsupported shell: {shell!r}. Supported: {', '.join(SUPPORTED_SHELLS)}"
        )
    return shell


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _script_path(shell: str) -> Path:
    return _home() / ".config" / "opentraces" / "completions" / f"_ot.{shell}"


def _rc_path(shell: str) -> Path:
    if shell == "zsh":
        return _home() / ".zshrc"
    if shell == "bash":
        return _home() / ".bashrc"
    if shell == "fish":
        return _home() / ".config" / "fish" / "completions" / "ot.fish"
    raise click.UsageError(f"Unsupported shell: {shell}")


def _source_line(shell: str, script: Path) -> str:
    if shell == "fish":
        return ""
    return f'[ -f "{script}" ] && source "{script}"  # opentraces completions'


def _resolve_bin() -> str:
    """Pick an absolute path to the ``opentraces`` / ``ot`` binary.

    We prefer a sibling of the running Python — that handles dev venvs,
    pipx installs, and tool-specific prefixes where ``ot`` may not yet
    be on PATH. Falls back to a PATH lookup, and finally to the literal
    name for the rare case where neither resolves.
    """
    for candidate in (
        Path(sys.executable).with_name("opentraces"),
        Path(sys.executable).with_name("ot"),
    ):
        if candidate.exists():
            return str(candidate)
    which = shutil.which("opentraces") or shutil.which("ot")
    return which or "ot"


def _render_script(shell: str, bin_path: str, names: tuple[str, ...]) -> str:
    """Substitute placeholders in the template for ``shell``.

    ``names`` is the tuple of command names the completer should bind to
    (``("ot",)`` by default; may include alias names like ``"otd"``).
    """
    template = SCRIPTS[shell]
    if shell == "zsh":
        # ``#compdef`` and ``compdef`` lines accept multiple names separated
        # by whitespace.
        names_joined = " ".join(names)
        return (
            template
            .replace("__OT_BIN__", bin_path)
            .replace("__OT_NAMES__", names_joined)
        )
    if shell == "bash":
        binds = "\n".join(f"complete -F _ot_completions {n}" for n in names)
        return (
            template
            .replace("__OT_BIN__", bin_path)
            .replace("__OT_BASH_BINDS__", binds)
        )
    if shell == "fish":
        binds = "\n".join(f"complete -c {n} -f -a '(__ot_complete)'" for n in names)
        return (
            template
            .replace("__OT_BIN__", bin_path)
            .replace("__OT_FISH_BINDS__", binds)
        )
    raise click.UsageError(f"Unsupported shell: {shell}")


class _CompletionsGroup(OpentracesGroup):
    """Group that supports ``ot completions [shell]`` bare-print alongside
    ``install``/``uninstall`` subcommands. If the first arg is a supported
    shell name (not a subcommand), print the script for that shell and exit.

    Inherits ``OpentracesGroup`` so the ``--help`` screen carries the
    canonical banner + dim-rule layout instead of vanilla Click's plain one.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] in SUPPORTED_SHELLS and args[0] not in self.commands:
            shell = args[0]
            # Bare print: use ``ot`` as the canonical name; users who need
            # alias or path baking should run ``install`` instead.
            click.echo(_render_script(shell, "ot", ("ot", "opentraces")), nl=False)
            ctx.exit(0)
        return super().parse_args(ctx, args)


@click.group(
    "completions",
    cls=_CompletionsGroup,
    invoke_without_command=True,
    epilog=EPILOG,
    help="Print or install shell completion scripts.",
)
@click.pass_context
def completions(ctx: click.Context) -> None:
    """Print completion script for SHELL (defaults to $SHELL detection).

    Usage: ot completions [bash|zsh|fish]
    """
    if ctx.invoked_subcommand is not None:
        return
    resolved = _resolve_shell(None)
    click.echo(_render_script(resolved, "ot", ("ot",)), nl=False)


@completions.command("install")
@click.argument("shell", required=False, type=click.Choice(list(SUPPORTED_SHELLS), case_sensitive=False))
@click.option("--alias", "aliases", multiple=True, metavar="NAME",
              help="Also bind completion to NAME (repeatable). Useful for dev aliases.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress progress output.")
@click.pass_context
def install_cmd(ctx: click.Context, shell: str | None, aliases: tuple[str, ...], quiet: bool) -> None:
    """Install completions for SHELL (auto-detects if omitted)."""
    resolved = _resolve_shell(shell)
    bin_path = _resolve_bin()
    # Bind to both ``ot`` and ``opentraces`` by default — zsh expands shell
    # aliases before looking up compdef, so an alias like ``otd='opentraces'``
    # needs ``opentraces`` registered to pick up this completer.
    names = ("ot", "opentraces") + tuple(aliases)

    script_text = _render_script(resolved, bin_path, names)
    script_path = _script_path(resolved)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script_text)

    if resolved == "fish":
        # fish auto-loads files placed in ~/.config/fish/completions/.
        rc = _rc_path(resolved)
        rc.parent.mkdir(parents=True, exist_ok=True)
        rc.write_text(script_text)
        if not quiet:
            click.echo(f"Installed fish completions at {rc}")
            if aliases:
                click.echo(f"  Bound to: {', '.join(names)}")
        return

    rc = _rc_path(resolved)
    rc.parent.mkdir(parents=True, exist_ok=True)
    line = _source_line(resolved, script_path)
    existing = rc.read_text() if rc.exists() else ""
    if line not in existing:
        sep = "" if existing.endswith("\n") or not existing else "\n"
        with rc.open("a") as f:
            f.write(f"{sep}{line}\n")
        if not quiet:
            click.echo(f"Appended source line to {rc}")
    else:
        if not quiet:
            click.echo(f"Source line already present in {rc}")

    if not quiet:
        click.echo(f"Installed {resolved} completion script at {script_path}")
        if aliases:
            click.echo(f"  Bound to: {', '.join(names)}")
        click.echo(f"Restart your shell or run: source {rc}")


def uninstall_completions(shell: str) -> dict:
    """Remove the completion script + rc source line for one shell.

    Non-Click reusable core of ``opentraces completions uninstall`` (so
    ``opentraces setup uninstall`` can fan out over ``SUPPORTED_SHELLS``
    without invoking Click). ``shell`` must be one of ``SUPPORTED_SHELLS``;
    raises ``ValueError`` (not ``click.UsageError``) otherwise. Idempotent:
    a shell with nothing installed returns ``removed=[]``. Returns
    ``{"shell", "removed": [paths...]}``.
    """
    if shell not in SUPPORTED_SHELLS:
        raise ValueError(
            f"Unsupported shell: {shell!r}. Supported: {', '.join(SUPPORTED_SHELLS)}"
        )
    removed: list[str] = []

    script_path = _script_path(shell)
    if script_path.exists():
        script_path.unlink()
        removed.append(str(script_path))

    rc = _rc_path(shell)
    if shell == "fish":
        # The fish completion lives in its own file (no rc source line to
        # strip). Only remove it if opentraces actually wrote it — a user may
        # keep their own `ot.fish`, and `setup uninstall` fans out across every
        # shell, so a blind unlink would clobber an unrelated file.
        if rc.exists():
            # Ownership signature: the exact header line opentraces generates.
            # A loose "opentraces" substring would also match a user's own
            # ot.fish that merely mentions opentraces.
            signature = FISH_SCRIPT.splitlines()[0]
            try:
                owned = signature in rc.read_text()
            except OSError:
                owned = False
            if owned:
                rc.unlink()
                removed.append(str(rc))
        return {"shell": shell, "removed": removed}

    if rc.exists():
        line = _source_line(shell, script_path)
        text = rc.read_text()
        new_lines = [ln for ln in text.splitlines() if ln.strip() != line.strip()]
        new_text = "\n".join(new_lines)
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        if new_text != text:
            rc.write_text(new_text)
            removed.append(f"{rc} (source line)")
    return {"shell": shell, "removed": removed}


@completions.command("uninstall")
@click.argument("shell", required=False, type=click.Choice(list(SUPPORTED_SHELLS), case_sensitive=False))
@click.option("-q", "--quiet", is_flag=True, help="Suppress progress output.")
@click.pass_context
def uninstall_cmd(ctx: click.Context, shell: str | None, quiet: bool) -> None:
    """Remove completions for SHELL (auto-detects if omitted)."""
    resolved = _resolve_shell(shell)
    result = uninstall_completions(resolved)
    if not quiet:
        for item in result["removed"]:
            click.echo(f"Removed {item}")
        if not result["removed"]:
            click.echo(f"No {resolved} completions installed")
