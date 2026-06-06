// Icons & helpers for OpenTraces Hub prototype

const Icon = ({ name, size = 16, className = "" }) => {
  const props = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.75, strokeLinecap: "round", strokeLinejoin: "round", className };
  switch (name) {
    case "grid": return <svg {...props}><rect x="3" y="3" width="7" height="7" rx="1.2"/><rect x="14" y="3" width="7" height="7" rx="1.2"/><rect x="3" y="14" width="7" height="7" rx="1.2"/><rect x="14" y="14" width="7" height="7" rx="1.2"/></svg>;
    case "git-branch": return <svg {...props}><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><path d="M18 8a4 4 0 0 1-4 4 4 4 0 0 0-4 4"/></svg>;
    case "workflows": return <svg {...props}><path d="M5 5h6v6H5z"/><path d="M13 13h6v6h-6z"/><path d="M11 8h2a3 3 0 0 1 3 3v2"/></svg>;
    case "datasets": return <svg {...props}><ellipse cx="12" cy="5" rx="8" ry="2.5"/><path d="M4 5v6c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5V5"/><path d="M4 11v6c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5v-6"/></svg>;
    case "repo": return <svg {...props}><path d="M4 4h12a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3V4z"/><path d="M4 17a3 3 0 0 1 3-3h12"/></svg>;
    case "settings": return <svg {...props}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3 1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8 1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>;
    case "chevron-down": return <svg {...props}><polyline points="6 9 12 15 18 9"/></svg>;
    case "chevron-right": return <svg {...props}><polyline points="9 6 15 12 9 18"/></svg>;
    case "chevron-left": return <svg {...props}><polyline points="15 18 9 12 15 6"/></svg>;
    case "search": return <svg {...props}><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>;
    case "bell": return <svg {...props}><path d="M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10.3 21a2 2 0 0 0 3.4 0"/></svg>;
    case "sun": return <svg {...props}><circle cx="12" cy="12" r="4"/><line x1="12" y1="2" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="22"/><line x1="2" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="22" y2="12"/><line x1="4.93" y1="4.93" x2="6.34" y2="6.34"/><line x1="17.66" y1="17.66" x2="19.07" y2="19.07"/><line x1="4.93" y1="19.07" x2="6.34" y2="17.66"/><line x1="17.66" y1="6.34" x2="19.07" y2="4.93"/></svg>;
    case "moon": return <svg {...props}><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>;
    case "plus": return <svg {...props}><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>;
    case "user": return <svg {...props}><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>;
    case "bot": return <svg {...props}><rect x="4" y="7" width="16" height="12" rx="2.5"/><circle cx="9" cy="13" r="1" fill="currentColor"/><circle cx="15" cy="13" r="1" fill="currentColor"/><path d="M12 3v4"/><circle cx="12" cy="3" r="1" fill="currentColor"/></svg>;
    case "brain": return <svg {...props}><path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 2 5v1a3 3 0 0 0 6 0V4a3 3 0 0 0-3 0z"/><path d="M15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-2 5v1a3 3 0 0 1-6 0V4a3 3 0 0 1 3 0z"/></svg>;
    case "tool": return <svg {...props}><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 0 5.4-5.4l-3 3-2-2 3-3z"/></svg>;
    case "git-commit": return <svg {...props}><circle cx="12" cy="12" r="3.5"/><line x1="3" y1="12" x2="8.5" y2="12"/><line x1="15.5" y1="12" x2="21" y2="12"/></svg>;
    case "trail": return <svg {...props}><rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/></svg>;
    case "conversation": return <svg {...props}><path d="M3 11a8 8 0 0 1 8-8h2a8 8 0 0 1 0 16h-1.5L7 22v-3.5A8 8 0 0 1 3 11z"/></svg>;
    case "x": return <svg {...props}><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>;
    case "expand": return <svg {...props}><polyline points="9 4 4 4 4 9"/><polyline points="15 4 20 4 20 9"/><polyline points="4 15 4 20 9 20"/><polyline points="20 15 20 20 15 20"/></svg>;
    case "down-line": return <svg {...props}><line x1="12" y1="4" x2="12" y2="20"/><polyline points="6 14 12 20 18 14"/></svg>;
    case "up-line": return <svg {...props}><line x1="12" y1="20" x2="12" y2="4"/><polyline points="6 10 12 4 18 10"/></svg>;
    case "inbox": return <svg {...props}><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.5 5l-2.5 7v6a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-6l-2.5-7a2 2 0 0 0-1.8-1.2H7.3A2 2 0 0 0 5.5 5z"/></svg>;
    case "back": return <svg {...props}><polyline points="15 18 9 12 15 6"/></svg>;
    case "external": return <svg {...props}><path d="M14 5h5v5"/><line x1="19" y1="5" x2="11" y2="13"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/></svg>;
    case "clock": return <svg {...props}><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 13.5"/></svg>;
    case "rows": return <svg {...props}><rect x="3" y="5" width="18" height="4" rx="1"/><rect x="3" y="11" width="18" height="4" rx="1"/><rect x="3" y="17" width="18" height="3" rx="1"/></svg>;
    case "activity": return <svg {...props}><polyline points="3 12 7 12 10 5 14 19 17 12 21 12"/></svg>;
    case "tag": return <svg {...props}><path d="M20.6 13.5L13.5 20.6a2 2 0 0 1-2.8 0L3 12.8V3h9.8l7.8 7.8a2 2 0 0 1 0 2.8z"/><circle cx="8" cy="8" r="1.5" fill="currentColor"/></svg>;
    case "shield": return <svg {...props}><path d="M12 3l8 3v6c0 5-3.5 8.5-8 9-4.5-.5-8-4-8-9V6l8-3z"/><polyline points="9 12 11 14 15 10"/></svg>;
    case "sparkles": return <svg {...props}><path d="M12 4l1.7 4.3L18 10l-4.3 1.7L12 16l-1.7-4.3L6 10l4.3-1.7L12 4z"/><path d="M19 14l.7 1.7L21.4 16.4l-1.7.7L19 18.8l-.7-1.7L16.6 16.4l1.7-.7L19 14z" fill="currentColor" stroke="none"/></svg>;
    case "heart-pulse": return <svg {...props}><path d="M20.4 4.6a5.5 5.5 0 0 0-7.8 0L12 5.2l-.6-.6a5.5 5.5 0 0 0-7.8 7.8l8.4 8.4 8.4-8.4a5.5 5.5 0 0 0 0-7.8z"/><polyline points="3 13 7 13 9 10 12 16 14 12 17 13 21 13"/></svg>;
    case "capsule": return <svg {...props}><rect x="2.5" y="8" width="19" height="8" rx="4"/><line x1="12" y1="8" x2="12" y2="16"/></svg>;
    case "check": return <svg {...props}><polyline points="20 6 9 17 4 12"/></svg>;
    case "copy": return <svg {...props}><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>;
    case "link": return <svg {...props}><path d="M10 13a5 5 0 0 0 7.1 0l2.4-2.4a5 5 0 0 0-7.1-7.1L11 5"/><path d="M14 11a5 5 0 0 0-7.1 0l-2.4 2.4a5 5 0 0 0 7.1 7.1L13 19"/></svg>;
    case "share": return <svg {...props}><circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><line x1="8.2" y1="10.8" x2="15.8" y2="6.3"/><line x1="8.2" y1="13.2" x2="15.8" y2="17.7"/></svg>;
    case "play": return <svg {...props}><polygon points="7 4 20 12 7 20" fill="currentColor" stroke="none"/></svg>;
    case "lock": return <svg {...props}><rect x="4.5" y="11" width="15" height="9" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/></svg>;
    case "unlock": return <svg {...props}><rect x="4.5" y="11" width="15" height="9" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 7.6-1.7"/></svg>;
    case "eye": return <svg {...props}><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>;
    case "eye-off": return <svg {...props}><line x1="3" y1="3" x2="21" y2="21"/><path d="M10.6 6.2A10.9 10.9 0 0 1 12 6c6.4 0 10 6 10 6a18 18 0 0 1-3.2 3.7"/><path d="M6.7 6.7A18 18 0 0 0 2 12s3.6 7 10 7a10.6 10.6 0 0 0 3.4-.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>;
    case "globe": return <svg {...props}><circle cx="12" cy="12" r="9"/><line x1="3" y1="12" x2="21" y2="12"/><path d="M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18z"/></svg>;
    case "alert": return <svg {...props}><path d="M12 3.5l9.5 16.5H2.5z"/><line x1="12" y1="10" x2="12" y2="14.5"/><circle cx="12" cy="17.5" r="0.7" fill="currentColor" stroke="none"/></svg>;
    case "replay": return <svg {...props}><path d="M3 12a9 9 0 1 0 2.6-6.3"/><polyline points="3 3.5 3 9 8.5 9"/></svg>;
    case "issue": return <svg {...props}><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.3" fill="currentColor" stroke="none"/></svg>;
    case "file": return <svg {...props}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><polyline points="14 3 14 8 19 8"/></svg>;
    case "box": return <svg {...props}><path d="M21 8.5l-9-5-9 5v7l9 5 9-5z"/><polyline points="3 8.5 12 13.5 21 8.5"/><line x1="12" y1="13.5" x2="12" y2="21"/></svg>;
    case "snapshot": return <svg {...props}><rect x="3" y="6" width="18" height="14" rx="2"/><circle cx="12" cy="13" r="3.2"/><path d="M8.5 6l1.3-2.2h4.4L15.5 6"/></svg>;
    case "send": return <svg {...props}><line x1="21" y1="3" x2="10.5" y2="13.5"/><polygon points="21 3 14.5 21 10.5 13.5 3 9.5 21 3" /></svg>;
    case "dot": return <svg {...props}><circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/></svg>;
    case "node": return <svg {...props}><circle cx="6" cy="6" r="2.4"/><circle cx="6" cy="18" r="2.4"/><circle cx="18" cy="12" r="2.4"/><path d="M8.4 6.6l7 4M8.4 17.4l7-4"/></svg>;
    case "arrow-right": return <svg {...props}><line x1="4" y1="12" x2="19" y2="12"/><polyline points="13 6 19 12 13 18"/></svg>;
    case "git": return <svg {...props}><circle cx="6" cy="6" r="2.4"/><circle cx="6" cy="18" r="2.4"/><circle cx="17" cy="9" r="2.4"/><path d="M17 11.4a5 5 0 0 1-5 5H6"/><line x1="6" y1="8.4" x2="6" y2="15.6"/></svg>;
    default: return null;
  }
};

window.Icon = Icon;
