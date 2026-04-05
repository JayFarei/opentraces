"""OpenTraces TUI application shell."""

from __future__ import annotations

import logging
from pathlib import Path

from textual.app import App
from textual.binding import Binding

from ...config import get_project_state_path, load_project_config
from ...state import StateManager
from .messages import RefreshRequested
from .screens.inbox import InboxScreen
from .store import TraceStore
from .utils import _project_dir_from_staging

logger = logging.getLogger(__name__)


class OpenTracesApp(App):
    """Textual TUI for the repo-local OpenTraces inbox."""

    TITLE = "opentraces"
    SUB_TITLE = "repo inbox"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
    ]

    def __init__(self, staging_dir: Path, fullscreen: bool = False) -> None:
        super().__init__()
        self.staging_dir = staging_dir
        self.project_dir = _project_dir_from_staging(staging_dir)
        self.project_name = self.project_dir.name

        # State
        state_path = get_project_state_path(self.project_dir)
        self.state = StateManager(
            state_path=state_path if state_path.parent.exists() else None,
        )

        # Data
        self.store = TraceStore(staging_dir, self.state)

        # Launch options
        self._launch_fullscreen = fullscreen

        # Remote name resolved on mount
        self.remote_name: str = "remote not set"

    # ── Lifecycle ─────────────────────────────────────────────

    def on_mount(self) -> None:
        self._load_project_context()
        self.push_screen(InboxScreen())
        self.set_interval(0.5, self._check_dirty)

    def _load_project_context(self) -> None:
        try:
            proj_config = load_project_config(self.project_dir)
            self.remote_name = proj_config.get("remote") or "remote not set"
        except Exception:
            self.remote_name = "remote not set"

    # ── Auto-refresh ──────────────────────────────────────────

    def _check_dirty(self) -> None:
        """Periodically check whether the store needs reloading."""
        if self.store.check_and_reload():
            self.post_message(RefreshRequested())

    # ── Message handlers ──────────────────────────────────────

    def on_refresh_requested(self, message: RefreshRequested) -> None:
        """Reload the store and let the active screen know."""
        self.store.check_and_reload()

    # ── Actions ───────────────────────────────────────────────

    def action_toggle_help(self) -> None:
        """Toggle help overlay visibility."""
        # Delegated to the active screen's HelpOverlay widget
        pass
