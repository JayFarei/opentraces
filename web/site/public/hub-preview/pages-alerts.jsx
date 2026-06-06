// Alerts page — rules that fire on trace patterns and push to Slack.

function AlertsPage() {
  const [alerts, setAlerts] = React.useState(ALERTS);

  const toggle = (id) => setAlerts(prev => prev.map(a =>
    a.id === id ? { ...a, status: a.status === "active" ? "paused" : "active" } : a
  ));

  return (
    <div className="landing landing-page">
      <PageHero
        kicker="Conversation intelligence"
        title="Alerts"
        subtitle="Trigger on trace-level signals — failure rate, context overflow, tool thrash, SOP violations. Push to Slack with one click."
        actions={
          <div className="ph-action-group">
            <ToolBtn icon="settings" label="Report config" />
            <ToolBtn icon="external" label="Connect Slack" />
            <ToolBtn icon="plus" label="Create alert" primary />
          </div>
        }
      />

      <div className="alerts-table">
        <div className="at-head">
          <div className="at-c at-c-name">Alert</div>
          <div className="at-c at-c-chan">Channel</div>
          <div className="at-c at-c-every">Every</div>
          <div className="at-c at-c-last">Last triggered</div>
          <div className="at-c at-c-status">Status</div>
          <div className="at-c at-c-actions" />
        </div>

        {alerts.map(a => {
          const paused = a.status === "paused";
          return (
            <div key={a.id} className={"at-row" + (paused ? " paused" : "")}>
              <div className="at-c at-c-name">
                <div className="at-name">{a.name}</div>
                <div className="at-cond">
                  <span className="at-cond-k">If:</span> {a.condIf}
                </div>
                <div className="at-cond">
                  <span className="at-cond-k">Then:</span> {a.condThen}
                </div>
              </div>
              <div className="at-c at-c-chan">
                <span className="at-chan mono">
                  <span className="at-chan-dot" />
                  {a.channel}
                </span>
              </div>
              <div className="at-c at-c-every mono">{a.every}</div>
              <div className="at-c at-c-last">{a.last}</div>
              <div className="at-c at-c-status">
                <span className={"at-status st-" + a.status}>
                  {a.status === "active" ? <Icon name="activity" size={11} /> : <span className="at-pause-glyph">‖</span>}
                  <span>{a.status === "active" ? "Active" : "Paused"}</span>
                </span>
              </div>
              <div className="at-c at-c-actions">
                <button className="at-act" aria-label="Edit"><Icon name="settings" size={13} /></button>
                <button className="at-act" aria-label={paused ? "Resume" : "Pause"} onClick={() => toggle(a.id)}>
                  {paused ? <span className="at-pause-glyph play">▶</span> : <span className="at-pause-glyph">‖</span>}
                </button>
                <button className="at-act danger" aria-label="Delete"><Icon name="x" size={13} /></button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="alerts-foot rail-card">
        <div className="rail-h">Slack integration</div>
        <div className="alerts-slack">
          <div className="as-conn">
            <div className="as-icon"><Icon name="external" size={14} /></div>
            <div>
              <div className="as-h">slack/jay</div>
              <div className="as-s mono">connected · 5 alerts piped · last delivery 3h ago</div>
            </div>
          </div>
          <div className="as-actions">
            <ToolBtn icon="settings" label="Manage channels" />
            <ToolBtn icon="x" label="Disconnect" />
          </div>
        </div>
      </div>
    </div>
  );
}

window.AlertsPage = AlertsPage;
