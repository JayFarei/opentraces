// pages-account.jsx — Account settings, reached from the avatar deck.
//   ProfileSettingsPage — the personal account: profile, connections,
//   API keys, danger zone.
//   OrgSettingsPage — OpenMake admin: org profile, RBAC members with
//   last sign-ins, danger zone. Classic admin layout, house style.

/* ── Personal account ────────────────────────────────────────── */
const PA_ACCOUNT = {
  name: "Jay Farei", handle: "jayfarei", email: "jay@farei.dev",
  joined: "March 2025", avatar: "assets/avatar-jf.png",
};
const PA_KEYS = [
  { name: "laptop-cli", prefix: "ot_live_····4f2a", scopes: "capture · push", created: "Mar 2026", lastUsed: "12 min ago" },
  { name: "ci-runner",  prefix: "ot_live_····9c81", scopes: "capture",        created: "Jan 2026", lastUsed: "3h ago" },
  { name: "scratch",    prefix: "ot_test_····07bd", scopes: "read",           created: "Nov 2025", lastUsed: "5 months ago" },
];

function ProfileSettingsPage() {
  return (
    <div className="landing landing-repo gs-page pa-page">
      <header className="repo-hero gs-hero">
        <img className="gs-avatar" src={PA_ACCOUNT.avatar} alt="" width="44" height="44" />
        <div className="rh-title">
          <h1 className="rh-name"><span className="nm">Your profile</span></h1>
          <p className="rh-desc"><strong>{PA_ACCOUNT.name}</strong> · @{PA_ACCOUNT.handle} · member since {PA_ACCOUNT.joined}</p>
        </div>
        <div className="rh-meta">
          <div className="rh-stat"><div className="k">Connections</div><div className="v mono">2</div></div>
          <div className="rh-stat"><div className="k">API keys</div><div className="v mono">{PA_KEYS.length}</div></div>
        </div>
      </header>

      <div className="rs-wrap">
        <RsSection id="pa-profile" icon="user" title="Profile" desc="How you appear across the hub — on traces, run records, and reviews.">
          <RsRow title="Name" desc="Shown on everything you author.">
            <div className="pa-value"><span>{PA_ACCOUNT.name}</span><button className="tool-btn"><Icon name="pen" size={11} /><span>Edit</span></button></div>
          </RsRow>
          <RsRow title="Email" desc="Sign-in and notifications. Changing it sends a confirmation to both addresses.">
            <div className="pa-value"><span className="mono">{PA_ACCOUNT.email}</span><button className="tool-btn"><Icon name="pen" size={11} /><span>Change email</span></button></div>
          </RsRow>
          <RsRow title="Avatar" desc="PNG or JPG, at least 128×128.">
            <div className="pa-value">
              <img className="pa-face" src={PA_ACCOUNT.avatar} alt="" width="28" height="28" />
              <button className="tool-btn"><Icon name="refresh" size={11} /><span>Replace</span></button>
              <button className="tool-btn"><Icon name="x" size={11} /><span>Remove</span></button>
            </div>
          </RsRow>
        </RsSection>

        <RsSection id="pa-connections" icon="link" title="Connections" desc="Accounts the hub acts through. Disconnecting never touches data already captured.">
          <RsRow title="GitHub" desc="Repo access for trace capture and PR trails · connected March 2025.">
            <div className="pa-value">
              <span className="pa-conn ok"><span className="pa-conn-dot"></span>connected as <span className="mono">jayfarei</span></span>
              <button className="tool-btn"><Icon name="x" size={11} /><span>Disconnect</span></button>
            </div>
          </RsRow>
          <RsRow title="Hugging Face" desc="Datasets, remotes and the bucket live under this account · device flow, authorized 3 weeks ago.">
            <div className="pa-value">
              <span className="pa-conn ok"><span className="pa-conn-dot"></span>connected as <span className="mono">jayfarei</span></span>
              <button className="tool-btn"><Icon name="refresh" size={11} /><span>Re-authenticate</span></button>
            </div>
          </RsRow>
          <RsCli cmd="opentraces auth login" />
        </RsSection>

        <RsSection id="pa-keys" icon="key" title="API keys" desc="Tokens for the CLI and harness hooks. Keys are shown once at creation; only the suffix is kept.">
          <div className="pa-keys">
            <div className="pa-key-head"><span>key</span><span>token</span><span>scopes</span><span>created</span><span>last used</span><span></span></div>
            {PA_KEYS.map(k => (
              <div key={k.name} className="pa-key-row">
                <span className="nm">{k.name}</span>
                <span className="mono tok">{k.prefix}</span>
                <span className="sc">{k.scopes}</span>
                <span className="dim">{k.created}</span>
                <span className="dim">{k.lastUsed}</span>
                <button className="tool-btn pa-revoke"><Icon name="x" size={11} /><span>Revoke</span></button>
              </div>
            ))}
          </div>
          <div className="gs-btn-row pa-keys-foot">
            <button className="tool-btn"><Icon name="plus" size={11} /><span>Create key</span></button>
          </div>
          <RsCli cmd="opentraces auth token create --scopes capture,push" />
        </RsSection>

        <RsSection id="pa-danger" icon="alert" title="Danger zone" tone="danger" desc="Removal is scoped to the hub — local buckets on your machines are never touched.">
          <RsRow title="Export your data" desc="One archive: traces, datasets, run records, and account metadata.">
            <button className="tool-btn"><Icon name="down-line" size={11} /><span>Export archive</span></button>
          </RsRow>
          <RsRow title="Delete account" desc="Deletes your hub account and everything synced under @jayfarei. Organisation data owned by OpenMake stays.">
            <button className="tool-btn pa-danger-btn"><Icon name="trash" size={11} /><span>Delete account…</span></button>
          </RsRow>
        </RsSection>
      </div>
    </div>
  );
}

/* ── Organisation admin ──────────────────────────────────────── */
const OA_ORG = {
  name: "OpenMake", handle: "openmake", domain: "openmake.ai",
  plan: "Enterprise", seats: { used: 48, total: 60 },
  sso: "Google Workspace · @openmake.ai", created: "August 2024",
};
const OA_ROLES = [
  { id: "owner",   label: "Owner",          desc: "Billing, members, delete org — everything.", n: 2 },
  { id: "admin",   label: "Admin",          desc: "Manage members, projects, datasets, workflows.", n: 6 },
  { id: "member",  label: "Member",         desc: "Capture traces, run benches, edit datasets.", n: 37 },
  { id: "billing", label: "Billing viewer", desc: "Usage and invoices only.", n: 3 },
];
const OA_MEMBERS = [
  { name: "Jay Farei",     email: "jay@openmake.ai",     role: "owner",   twoFa: true,  last: "2 min ago",   status: "active" },
  { name: "Mira Chen",     email: "mira@openmake.ai",    role: "owner",   twoFa: true,  last: "35 min ago",  status: "active" },
  { name: "Tomás Ribeiro", email: "tomas@openmake.ai",   role: "admin",   twoFa: true,  last: "1h ago",      status: "active" },
  { name: "Priya Nair",    email: "priya@openmake.ai",   role: "admin",   twoFa: true,  last: "3h ago",      status: "active" },
  { name: "Sam Okafor",    email: "sam@openmake.ai",     role: "member",  twoFa: true,  last: "12 min ago",  status: "active" },
  { name: "Lena Vogel",    email: "lena@openmake.ai",    role: "member",  twoFa: false, last: "yesterday",   status: "active" },
  { name: "Diego Álvarez", email: "diego@openmake.ai",   role: "member",  twoFa: true,  last: "2 days ago",  status: "active" },
  { name: "Ana Sørensen",  email: "ana@openmake.ai",     role: "billing", twoFa: true,  last: "4 days ago",  status: "active" },
  { name: "Rui Tanaka",    email: "rui@openmake.ai",     role: "member",  twoFa: false, last: "—",           status: "invited" },
  { name: "Old CI bot",    email: "ci-legacy@openmake.ai", role: "member", twoFa: false, last: "3 months ago", status: "suspended" },
];
const OA_ROLE_LABEL = { owner: "Owner", admin: "Admin", member: "Member", billing: "Billing" };
const OA_STATUS_TONE = { active: "ok", invited: "warn", suspended: "dim" };

function OaInitials({ name }) {
  const parts = name.split(" ");
  return <span className="oa-initials">{(parts[0][0] || "") + (parts[1] ? parts[1][0] : "")}</span>;
}

function OrgSettingsPage() {
  const [roleFilter, setRoleFilter] = React.useState("all");
  const members = roleFilter === "all" ? OA_MEMBERS : OA_MEMBERS.filter(m => m.role === roleFilter);
  return (
    <div className="landing landing-repo gs-page oa-page">
      <header className="repo-hero gs-hero">
        <span className="oa-mark"><span className="om-sq"></span></span>
        <div className="rh-title">
          <h1 className="rh-name"><span className="nm">{OA_ORG.name}</span></h1>
          <p className="rh-desc"><span className="mono">@{OA_ORG.handle}</span> · {OA_ORG.domain} · {OA_ORG.plan} · since {OA_ORG.created}</p>
        </div>
        <div className="rh-meta">
          <div className="rh-stat"><div className="k">Seats</div><div className="v mono">{OA_ORG.seats.used} / {OA_ORG.seats.total}</div></div>
          <div className="rh-stat"><div className="k">Roles</div><div className="v mono">{OA_ROLES.length}</div></div>
          <div className="rh-stat"><div className="k">SSO</div><div className="v mono">enforced</div></div>
        </div>
      </header>

      <div className="rs-wrap">
        <RsSection id="oa-org" icon="globe" title="Organisation" desc="Identity and sign-in policy for everyone under the domain.">
          <RsRow title="Name" desc="Shown on the workspace and every shared artifact.">
            <div className="pa-value"><span>{OA_ORG.name}</span><button className="tool-btn"><Icon name="pen" size={11} /><span>Edit</span></button></div>
          </RsRow>
          <RsRow title="Single sign-on" desc="Anyone with a verified @openmake.ai address joins as Member.">
            <div className="pa-value"><span className="pa-conn ok"><span className="pa-conn-dot"></span>{OA_ORG.sso}</span><button className="tool-btn"><Icon name="settings" size={11} /><span>Configure</span></button></div>
          </RsRow>
          <RsRow title="Default role" desc="Applied to new members joining via SSO or invite link.">
            <span className="oa-role mono">member</span>
          </RsRow>
        </RsSection>

        <RsSection id="oa-members" icon="users" title="Members" desc="Role-based access with per-member last sign-ins. Owners and admins can change roles; at least one owner must remain.">
          <div className="oa-roles">
            {OA_ROLES.map(r => (
              <div key={r.id} className="oa-role-card">
                <div className="t"><span className="oa-role mono" data-role={r.id}>{r.label}</span><span className="n mono">{r.n}</span></div>
                <div className="d">{r.desc}</div>
              </div>
            ))}
          </div>

          <div className="rs-subhead oa-members-head">
            <div className="oa-filter">
              <button className={"v2-filter" + (roleFilter === "all" ? "" : "")} aria-current={roleFilter === "all"} onClick={() => setRoleFilter("all")}>All · {OA_MEMBERS.length}</button>
              {OA_ROLES.map(r => (
                <button key={r.id} className="v2-filter" aria-current={roleFilter === r.id} onClick={() => setRoleFilter(r.id)}>{r.label}</button>
              ))}
            </div>
            <button className="tool-btn"><Icon name="plus" size={11} /><span>Invite members</span></button>
          </div>

          <div className="oa-table">
            <div className="oa-thead"><span>member</span><span>role</span><span>2fa</span><span>last sign-in</span><span>status</span></div>
            {members.map(m => (
              <div key={m.email} className="oa-tr" data-status={m.status}>
                <span className="oa-member">
                  <OaInitials name={m.name} />
                  <span className="oa-who"><span className="nm">{m.name}</span><span className="em mono">{m.email}</span></span>
                </span>
                <span><span className="oa-role mono" data-role={m.role}>{OA_ROLE_LABEL[m.role]}</span></span>
                <span className={"oa-2fa " + (m.twoFa ? "on" : "off")}>{m.twoFa ? <Icon name="check" size={12} /> : "—"}</span>
                <span className="oa-last mono">{m.last}</span>
                <span><span className={"oa-status st-" + OA_STATUS_TONE[m.status]}><span className="dot"></span>{m.status}</span></span>
              </div>
            ))}
          </div>
          <RsCli cmd="opentraces org members --org openmake --format table" />
        </RsSection>

        <RsSection id="oa-danger" icon="alert" title="Danger zone" tone="danger" desc="Both actions require a second owner to confirm.">
          <RsRow title="Transfer ownership" desc="Hand the org to another owner and step down to Admin.">
            <button className="tool-btn"><Icon name="arrow-right" size={11} /><span>Transfer…</span></button>
          </RsRow>
          <RsRow title="Delete organisation" desc="Deletes the org, its projects, datasets, and run records for all 48 members.">
            <button className="tool-btn pa-danger-btn"><Icon name="trash" size={11} /><span>Delete organisation…</span></button>
          </RsRow>
        </RsSection>
      </div>
    </div>
  );
}

window.ProfileSettingsPage = ProfileSettingsPage;
window.OrgSettingsPage = OrgSettingsPage;
