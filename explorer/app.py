"""
opentraces Explorer, a Gradio app for browsing community-contributed
AI agent traces on HuggingFace Hub.

Deployed at: huggingface.co/spaces/opentraces/explorer
"""

from __future__ import annotations

import gradio as gr
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from explorer.data import (
    get_community_stats,
    get_contributor_stats,
    get_filter_options,
    get_trace_by_id,
    is_using_sample_data,
    search_traces,
)

# ---------------------------------------------------------------------------
# Theme & constants
# ---------------------------------------------------------------------------

THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.green,
    secondary_hue=gr.themes.colors.blue,
    neutral_hue=gr.themes.colors.gray,
    font=gr.themes.GoogleFont("Inter"),
)

CSS = """
.trace-user { background: #dbeafe; border-radius: 12px; padding: 12px 16px; margin: 6px 0; }
.trace-assistant { background: #f3f4f6; border-radius: 12px; padding: 12px 16px; margin: 6px 0; }
.trace-reasoning { background: #f9fafb; border-radius: 8px; padding: 10px 14px; margin: 6px 0; opacity: 0.7; font-style: italic; border-left: 3px solid #d1d5db; }
.trace-tool { background: #f0fdf4; border-radius: 8px; padding: 10px 14px; margin: 6px 0; font-family: monospace; font-size: 0.9em; border-left: 3px solid #22c55e; }
.trace-subagent { margin-left: 24px; border-left: 2px solid #6366f1; padding-left: 12px; }
.sample-banner { background: #fef3c7; border: 1px solid #fbbf24; border-radius: 8px; padding: 10px 16px; margin-bottom: 12px; font-size: 0.9em; }
.metric-card { text-align: center; padding: 16px; }
.metric-card h2 { margin: 0; font-size: 2em; color: #16a34a; }
.metric-card p { margin: 4px 0 0; color: #6b7280; font-size: 0.9em; }
footer { display: none !important; }
"""

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=40, r=20, t=40, b=40),
    font=dict(family="Inter, sans-serif"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(text: str, length: int = 60) -> str:
    if len(text) <= length:
        return text
    return text[:length - 3] + "..."


def _short_model(model: str) -> str:
    if "opus" in model.lower():
        return "claude-opus-4"
    if "sonnet" in model.lower():
        return "claude-sonnet-4"
    if "haiku" in model.lower():
        return "claude-haiku-3.5"
    return model


def _sample_banner() -> str:
    if is_using_sample_data():
        return (
            '<div class="sample-banner">'
            "No opentraces-tagged datasets found on HuggingFace Hub yet. "
            "Showing <strong>sample data</strong> to demonstrate the full experience. "
            'Contribute your traces at <a href="https://opentraces.ai">opentraces.ai</a>.'
            "</div>"
        )
    return ""


# ---------------------------------------------------------------------------
# Tab 1: Search & Browse
# ---------------------------------------------------------------------------

def do_search(query: str, ecosystem: str, model: str, agent: str, outcome: str):
    """Run search and return a results dataframe + trace IDs for selection."""
    filters = {
        "ecosystem": ecosystem,
        "model": model,
        "agent": agent,
        "outcome": outcome,
    }
    results = search_traces(query, filters)

    if not results:
        empty_df = pd.DataFrame(columns=[
            "trace_id", "task", "model", "steps", "tokens", "outcome", "date",
        ])
        return empty_df, gr.update(choices=[], value=None)

    rows = []
    trace_ids = []
    for r in results:
        tid = r["trace_id"]
        trace_ids.append(tid)
        tokens = r.get("tokens", {})
        total_tok = tokens.get("input", 0) + tokens.get("output", 0)
        rows.append({
            "trace_id": tid[:12] + "...",
            "task": _truncate(r.get("task", ""), 55),
            "model": _short_model(r.get("model", "")),
            "steps": r.get("n_steps", 0),
            "tokens": f"{total_tok:,}",
            "outcome": "Committed" if r.get("outcome", {}).get("committed") else "---",
            "date": r.get("timestamp", "")[:10],
        })

    df = pd.DataFrame(rows)
    choices = [f"{tid[:12]}... | {_truncate(r.get('task', ''), 40)}" for tid, r in zip(trace_ids, results)]
    return df, gr.update(choices=choices, value=choices[0] if choices else None)


def load_trace_from_selection(selection: str | None):
    """Load a trace selected from the search results dropdown."""
    if not selection:
        return "<p>Select a trace from the search results above.</p>"
    trace_id = selection.split(" | ")[0].replace("...", "").strip()
    # Find by prefix
    from explorer.data import _get_traces
    for t in _get_traces():
        if t["trace_id"].startswith(trace_id):
            return render_trace(t)
    return "<p>Trace not found.</p>"


# ---------------------------------------------------------------------------
# Tab 2: Trace Viewer
# ---------------------------------------------------------------------------

def render_trace(trace: dict) -> str:
    """Render a full trace as styled HTML."""
    parts: list[str] = []

    # Header
    parts.append(f'<h3>{trace.get("task", "Untitled session")}</h3>')
    parts.append(
        f'<p style="color:#6b7280; font-size:0.9em;">'
        f'Model: <strong>{_short_model(trace.get("model", ""))}</strong> | '
        f'Agent: <strong>{trace.get("agent", "")}</strong> | '
        f'Ecosystem: <strong>{trace.get("ecosystem", "")}</strong> | '
        f'Date: <strong>{trace.get("timestamp", "")[:10]}</strong>'
        f'</p>'
    )

    # Token summary
    tokens = trace.get("tokens", {})
    parts.append(
        f'<p style="font-size:0.85em; color:#9ca3af;">'
        f'Tokens: {tokens.get("input", 0):,} in / {tokens.get("output", 0):,} out | '
        f'Cache read: {tokens.get("cache_read", 0):,} | '
        f'Steps: {trace.get("n_steps", 0)}'
        f'</p>'
    )
    parts.append("<hr>")

    # Conversation
    for turn in trace.get("conversation", []):
        role = turn.get("role", "")
        content = turn.get("content", "")
        msg_type = turn.get("type", "")

        if role == "user":
            parts.append(f'<div class="trace-user"><strong>User</strong><br>{_escape(content)}</div>')

        elif role == "assistant" and msg_type == "reasoning":
            parts.append(
                f'<div class="trace-reasoning">'
                f'<strong>Reasoning</strong><br>{_escape(content)}'
                f'</div>'
            )

        elif role == "assistant":
            parts.append(f'<div class="trace-assistant"><strong>Assistant</strong><br>{_escape(content)}</div>')

        elif role == "tool":
            is_sub = turn.get("is_sub_agent", False)
            wrapper_cls = "trace-subagent" if is_sub else ""
            label = f'Sub-agent: {turn.get("tool_name", "")}' if is_sub else turn.get("tool_name", "Tool")
            parts.append(
                f'<div class="{wrapper_cls}">'
                f'<details class="trace-tool">'
                f'<summary><strong>{_escape(label)}</strong></summary>'
                f'<pre style="white-space:pre-wrap; margin:6px 0;">{_escape(str(turn.get("input", "")))}</pre>'
                f'</details>'
                f'</div>'
            )

    # Attribution
    attribution = trace.get("attribution", [])
    if attribution:
        parts.append("<hr><h4>Attribution</h4>")
        for attr in attribution:
            parts.append(
                f'<div class="trace-tool">'
                f'<strong>{attr.get("file", "")}</strong> '
                f'(lines {attr.get("lines", [0, 0])[0]}-{attr.get("lines", [0, 0])[1]}) '
                f'- <em>{attr.get("action", "")}</em>'
                f'</div>'
            )

    # Outcome / Diff
    outcome = trace.get("outcome", {})
    if outcome:
        committed = outcome.get("committed", False)
        status = "Committed" if committed else "Not committed"
        color = "#16a34a" if committed else "#9ca3af"
        parts.append(f'<hr><h4>Outcome: <span style="color:{color}">{status}</span></h4>')
        diff = outcome.get("patch_diff", "")
        if diff:
            parts.append(
                f'<details><summary>Patch diff</summary>'
                f'<pre style="background:#1e293b; color:#e2e8f0; padding:12px; '
                f'border-radius:8px; overflow-x:auto; font-size:0.85em;">{_escape(diff)}</pre>'
                f'</details>'
            )

    return "\n".join(parts)


def view_trace_by_id(trace_id_input: str) -> str:
    """Look up and render a trace by its ID."""
    tid = trace_id_input.strip()
    if not tid:
        return "<p>Enter a trace ID above.</p>"
    trace = get_trace_by_id(tid)
    if trace:
        return render_trace(trace)
    # Try prefix match
    from explorer.data import _get_traces
    for t in _get_traces():
        if t["trace_id"].startswith(tid):
            return render_trace(t)
    return f'<p>No trace found with ID starting with <code>{_escape(tid)}</code>.</p>'


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Tab 3: Contributor Dashboard
# ---------------------------------------------------------------------------

def render_contributor_dashboard(username: str):
    """Build dashboard charts for a given contributor."""
    username = username.strip()
    if not username:
        return (
            "<p>Enter a HuggingFace username above.</p>",
            go.Figure(), go.Figure(), go.Figure(), go.Figure(),
        )

    stats = get_contributor_stats(username)
    if not stats.get("found"):
        return (
            f"<p>No traces found for user <strong>{_escape(username)}</strong>.</p>",
            go.Figure(), go.Figure(), go.Figure(), go.Figure(),
        )

    # Summary HTML
    summary = (
        f'<div style="display:grid; grid-template-columns:repeat(4,1fr); gap:12px;">'
        f'<div class="metric-card"><h2>{stats["total_sessions"]}</h2><p>Sessions</p></div>'
        f'<div class="metric-card"><h2>{stats["cache_hit_rate"]:.0%}</h2><p>Cache Hit Rate</p></div>'
        f'<div class="metric-card"><h2>${stats["estimated_cost"]:.2f}</h2><p>Est. Cost</p></div>'
        f'<div class="metric-card"><h2>{stats["success_rate"]:.0%}</h2><p>Success Rate</p></div>'
        f'</div>'
    )

    # Sessions over time
    dates = stats.get("sessions_by_date", {})
    if dates:
        fig_sessions = px.line(
            x=list(dates.keys()), y=list(dates.values()),
            labels={"x": "Date", "y": "Sessions"},
            title="Sessions Over Time",
        )
        fig_sessions.update_traces(line_color="#16a34a", fill="tozeroy", fillcolor="rgba(22,163,106,0.1)")
    else:
        fig_sessions = go.Figure()
    fig_sessions.update_layout(**PLOTLY_LAYOUT)

    # Model distribution
    models = stats.get("model_distribution", {})
    fig_models = px.pie(
        names=list(models.keys()), values=list(models.values()),
        title="Model Distribution",
        color_discrete_sequence=["#16a34a", "#2563eb", "#f59e0b", "#8b5cf6"],
    )
    fig_models.update_layout(**PLOTLY_LAYOUT)

    # Token usage
    tokens = stats.get("tokens", {})
    fig_tokens = go.Figure(data=[
        go.Bar(
            x=["Input", "Output", "Cache Read", "Cache Creation"],
            y=[tokens.get("input", 0), tokens.get("output", 0),
               tokens.get("cache_read", 0), tokens.get("cache_creation", 0)],
            marker_color=["#16a34a", "#2563eb", "#22d3ee", "#f59e0b"],
        )
    ])
    fig_tokens.update_layout(title="Token Usage Breakdown", **PLOTLY_LAYOUT)

    # Tool usage
    tools = stats.get("tool_usage", {})
    fig_tools = go.Figure(data=[
        go.Bar(
            y=list(tools.keys()),
            x=list(tools.values()),
            orientation="h",
            marker_color="#16a34a",
        )
    ])
    fig_tools.update_layout(title="Tool Usage", **PLOTLY_LAYOUT, yaxis=dict(autorange="reversed"))

    return summary, fig_sessions, fig_models, fig_tokens, fig_tools


# ---------------------------------------------------------------------------
# Tab 4: Community Stats
# ---------------------------------------------------------------------------

def render_community_stats():
    """Build community overview charts."""
    stats = get_community_stats()

    # Summary
    summary = (
        f'<div style="display:grid; grid-template-columns:repeat(4,1fr); gap:12px;">'
        f'<div class="metric-card"><h2>{stats["total_traces"]}</h2><p>Total Traces</p></div>'
        f'<div class="metric-card"><h2>{stats["active_contributors"]}</h2><p>Contributors</p></div>'
        f'<div class="metric-card"><h2>{stats["attribution_pct"]}%</h2><p>With Attribution</p></div>'
        f'<div class="metric-card"><h2>{stats["outcome_pct"]}%</h2><p>With Outcome</p></div>'
        f'</div>'
    )

    # Agent distribution
    agents = stats.get("agent_distribution", {})
    fig_agents = px.pie(
        names=list(agents.keys()), values=list(agents.values()),
        title="Agent Distribution",
        color_discrete_sequence=["#16a34a", "#2563eb", "#f59e0b", "#8b5cf6", "#ec4899"],
    )
    fig_agents.update_layout(**PLOTLY_LAYOUT)

    # Model distribution
    models = stats.get("model_distribution", {})
    fig_models = px.pie(
        names=list(models.keys()), values=list(models.values()),
        title="Model Distribution",
        color_discrete_sequence=["#16a34a", "#2563eb", "#f59e0b", "#8b5cf6"],
    )
    fig_models.update_layout(**PLOTLY_LAYOUT)

    # Ecosystem bar chart
    ecos = stats.get("ecosystem_distribution", {})
    fig_ecos = go.Figure(data=[
        go.Bar(
            x=list(ecos.keys()), y=list(ecos.values()),
            marker_color="#16a34a",
        )
    ])
    fig_ecos.update_layout(title="Language Ecosystems", **PLOTLY_LAYOUT)

    # Recent activity table
    recent = stats.get("recent_activity", [])
    recent_html = '<h4>Recent Activity</h4><table style="width:100%; border-collapse:collapse; font-size:0.9em;">'
    recent_html += (
        '<tr style="border-bottom:2px solid #e5e7eb; text-align:left;">'
        "<th style='padding:8px;'>Date</th>"
        "<th style='padding:8px;'>Contributor</th>"
        "<th style='padding:8px;'>Task</th>"
        "<th style='padding:8px;'>Model</th>"
        "</tr>"
    )
    for item in recent:
        recent_html += (
            f'<tr style="border-bottom:1px solid #f3f4f6;">'
            f'<td style="padding:8px;">{item["date"]}</td>'
            f'<td style="padding:8px;">{item["contributor"]}</td>'
            f'<td style="padding:8px;">{_truncate(item["task"], 50)}</td>'
            f'<td style="padding:8px;">{item["model"]}</td>'
            f'</tr>'
        )
    recent_html += "</table>"

    return summary, fig_agents, fig_models, fig_ecos, recent_html


# ---------------------------------------------------------------------------
# Build the Gradio app
# ---------------------------------------------------------------------------

def build_app() -> gr.Blocks:
    filters = get_filter_options()

    with gr.Blocks(title="opentraces Explorer") as app:
        gr.HTML(
            '<div style="text-align:center; padding:12px 0;">'
            '<h1 style="margin:0;">opentraces Explorer</h1>'
            '<p style="color:#6b7280; margin:4px 0 0;">Browse, search, and analyze community-contributed AI agent traces</p>'
            "</div>"
        )
        gr.HTML(_sample_banner())

        # ------------------------------------------------------------------
        # Tab 1: Search & Browse
        # ------------------------------------------------------------------
        with gr.Tab("Search & Browse", id="search"):
            with gr.Row():
                search_input = gr.Textbox(
                    label="Search",
                    placeholder="Username, task description, or keyword...",
                    scale=3,
                )
                eco_filter = gr.Dropdown(choices=filters["ecosystems"], value="All", label="Ecosystem", scale=1)
                model_filter = gr.Dropdown(choices=filters["models"], value="All", label="Model", scale=1)
                agent_filter = gr.Dropdown(choices=filters["agents"], value="All", label="Agent", scale=1)
                outcome_filter = gr.Dropdown(choices=filters["outcomes"], value="All", label="Outcome", scale=1)

            search_btn = gr.Button("Search", variant="primary")
            results_table = gr.Dataframe(
                headers=["trace_id", "task", "model", "steps", "tokens", "outcome", "date"],
                label="Results",
                interactive=False,
            )
            trace_selector = gr.Dropdown(label="Select a trace to view", choices=[], interactive=True)
            trace_detail_html = gr.HTML(label="Trace Detail")

            search_btn.click(
                fn=do_search,
                inputs=[search_input, eco_filter, model_filter, agent_filter, outcome_filter],
                outputs=[results_table, trace_selector],
            )
            search_input.submit(
                fn=do_search,
                inputs=[search_input, eco_filter, model_filter, agent_filter, outcome_filter],
                outputs=[results_table, trace_selector],
            )
            trace_selector.change(
                fn=load_trace_from_selection,
                inputs=[trace_selector],
                outputs=[trace_detail_html],
            )

        # ------------------------------------------------------------------
        # Tab 2: Trace Viewer
        # ------------------------------------------------------------------
        with gr.Tab("Trace Viewer", id="viewer"):
            gr.Markdown("Enter a trace ID to view the full session.")
            with gr.Row():
                trace_id_input = gr.Textbox(label="Trace ID", placeholder="e.g. a1b2c3d4e5f6...", scale=3)
                view_btn = gr.Button("View Trace", variant="primary", scale=1)
            viewer_html = gr.HTML(label="Trace")

            view_btn.click(fn=view_trace_by_id, inputs=[trace_id_input], outputs=[viewer_html])
            trace_id_input.submit(fn=view_trace_by_id, inputs=[trace_id_input], outputs=[viewer_html])

        # ------------------------------------------------------------------
        # Tab 3: Contributor Dashboard
        # ------------------------------------------------------------------
        with gr.Tab("Contributor Dashboard", id="dashboard"):
            gr.Markdown("View analytics for a specific contributor.")
            with gr.Row():
                username_input = gr.Textbox(label="HuggingFace Username", placeholder="e.g. alice-dev", scale=3)
                dash_btn = gr.Button("Load Dashboard", variant="primary", scale=1)

            dash_summary = gr.HTML()
            with gr.Row():
                dash_sessions = gr.Plot(label="Sessions Over Time")
                dash_models = gr.Plot(label="Model Distribution")
            with gr.Row():
                dash_tokens = gr.Plot(label="Token Usage")
                dash_tools = gr.Plot(label="Tool Usage")

            dash_btn.click(
                fn=render_contributor_dashboard,
                inputs=[username_input],
                outputs=[dash_summary, dash_sessions, dash_models, dash_tokens, dash_tools],
            )
            username_input.submit(
                fn=render_contributor_dashboard,
                inputs=[username_input],
                outputs=[dash_summary, dash_sessions, dash_models, dash_tokens, dash_tools],
            )

        # ------------------------------------------------------------------
        # Tab 4: Community Stats
        # ------------------------------------------------------------------
        with gr.Tab("Community Stats", id="community"):
            refresh_btn = gr.Button("Refresh Stats", variant="secondary")
            comm_summary = gr.HTML()
            with gr.Row():
                comm_agents = gr.Plot(label="Agent Distribution")
                comm_models = gr.Plot(label="Model Distribution")
            with gr.Row():
                comm_ecos = gr.Plot(label="Language Ecosystems")
                comm_recent = gr.HTML(label="Recent Activity")

            # Load on page open via a hidden trigger
            app.load(
                fn=render_community_stats,
                outputs=[comm_summary, comm_agents, comm_models, comm_ecos, comm_recent],
            )
            refresh_btn.click(
                fn=render_community_stats,
                outputs=[comm_summary, comm_agents, comm_models, comm_ecos, comm_recent],
            )

        # Footer
        gr.HTML(
            '<div style="text-align:center; padding:16px 0; color:#9ca3af; font-size:0.85em;">'
            'Powered by <a href="https://opentraces.ai" style="color:#16a34a;">opentraces.ai</a> '
            "| Schema-first, community-owned agent trace data"
            "</div>"
        )

    return app


demo = build_app()

if __name__ == "__main__":
    demo.launch(theme=THEME, css=CSS)
