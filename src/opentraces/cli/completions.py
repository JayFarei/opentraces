"""`opentraces completions` noun.

Implements cf-style shell completions via dynamic delegation: the installed
shell script delegates every completion query to a hidden ``ot __complete``
resolver. The shell script is tiny and never needs regenerating when new
verbs are added.

Surface:
    ot completions [shell]            Print script (auto-detects $SHELL)
    ot completions install [shell]    Install to shell config
    ot completions uninstall [shell]  Remove installation
"""

from __future__ import annotations

import os
from pathlib import Path

import click

SUPPORTED_SHELLS = ("bash", "zsh", "fish")

EPILOG = """\
\b
EXAMPLES
  $ ot completions                 Output completion script for detected shell
  $ ot completions zsh             Output zsh completion script
  $ ot completions install         Install completions for detected shell
  $ ot completions install zsh     Install zsh completions explicitly
  $ ot completions uninstall       Remove completions
  $ source <(ot completions)       Load completions in current session
"""

ZSH_SCRIPT = """\
#compdef ot
_ot() {
  local completions
  completions=(${(f)"$(ot __complete "${words[@]:1}" 2>/dev/null)"})
  [[ ${#completions[@]} -gt 0 ]] && compadd -a completions
}
compdef _ot ot
"""

BASH_SCRIPT = """\
# bash completion for ot (opentraces)
_ot_completions() {
  local cur words_to_send
  cur="${COMP_WORDS[COMP_CWORD]}"
  words_to_send=("${COMP_WORDS[@]:1}")
  local IFS=$'\\n'
  COMPREPLY=( $(ot __complete "${words_to_send[@]}" 2>/dev/null) )
  return 0
}
complete -F _ot_completions ot
"""

FISH_SCRIPT = """\
# fish completion for ot (opentraces)
function __ot_complete
  set -l tokens (commandline -opc) (commandline -ct)
  set -e tokens[1]
  ot __complete $tokens 2>/dev/null
end
complete -c ot -f -a '(__ot_complete)'
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
        # Prefer .bashrc; some systems use .bash_profile but .bashrc is the
        # canonical interactive-shell file and most users source it.
        return _home() / ".bashrc"
    if shell == "fish":
        return _home() / ".config" / "fish" / "completions" / "ot.fish"
    raise click.UsageError(f"Unsupported shell: {shell}")


def _source_line(shell: str, script: Path) -> str:
    if shell == "fish":
        # fish uses file placement, not a source line.
        return ""
    return f'[ -f "{script}" ] && source "{script}"  # opentraces completions'


class _CompletionsGroup(click.Group):
    """Group that supports ``ot completions [shell]`` bare-print alongside
    ``install``/``uninstall`` subcommands. If the first arg is a supported
    shell name (not a subcommand), print the script for that shell and exit.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] in SUPPORTED_SHELLS and args[0] not in self.commands:
            shell = args[0]
            click.echo(SCRIPTS[shell], nl=False)
            ctx.exit(0)
        return super().parse_args(ctx, args)


@click.group(
    "completions",
    cls=_CompletionsGroup,
    invoke_without_command=True,
    epilog=EPILOG,
    help="Print or install shell completion scripts (cf-style).",
)
@click.pass_context
def completions(ctx: click.Context) -> None:
    """Print completion script for SHELL (defaults to $SHELL detection).

    Usage: ot completions [bash|zsh|fish]
    """
    if ctx.invoked_subcommand is not None:
        return
    # Bare invocation: detect shell and print.
    resolved = _resolve_shell(None)
    click.echo(SCRIPTS[resolved], nl=False)


@completions.command("install")
@click.argument("shell", required=False, type=click.Choice(list(SUPPORTED_SHELLS), case_sensitive=False))
@click.option("-q", "--quiet", is_flag=True, help="Suppress progress output.")
@click.pass_context
def install_cmd(ctx: click.Context, shell: str | None, quiet: bool) -> None:
    """Install completions for SHELL (auto-detects if omitted)."""
    resolved = _resolve_shell(shell)

    script_path = _script_path(resolved)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(SCRIPTS[resolved])

    if resolved == "fish":
        # fish auto-loads completions placed in ~/.config/fish/completions/
        rc = _rc_path(resolved)
        rc.parent.mkdir(parents=True, exist_ok=True)
        rc.write_text(SCRIPTS[resolved])
        if not quiet:
            click.echo(f"Installed fish completions at {rc}")
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
        click.echo(f"Restart your shell or run: source {rc}")


@completions.command("uninstall")
@click.argument("shell", required=False, type=click.Choice(list(SUPPORTED_SHELLS), case_sensitive=False))
@click.option("-q", "--quiet", is_flag=True, help="Suppress progress output.")
@click.pass_context
def uninstall_cmd(ctx: click.Context, shell: str | None, quiet: bool) -> None:
    """Remove completions for SHELL (auto-detects if omitted)."""
    resolved = _resolve_shell(shell)

    script_path = _script_path(resolved)
    if script_path.exists():
        script_path.unlink()
        if not quiet:
            click.echo(f"Removed {script_path}")

    rc = _rc_path(resolved)
    if resolved == "fish":
        if rc.exists():
            rc.unlink()
            if not quiet:
                click.echo(f"Removed {rc}")
        return

    if rc.exists():
        line = _source_line(resolved, script_path)
        text = rc.read_text()
        new_lines = [ln for ln in text.splitlines() if ln.strip() != line.strip()]
        new_text = "\n".join(new_lines)
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        if new_text != text:
            rc.write_text(new_text)
            if not quiet:
                click.echo(f"Removed source line from {rc}")
