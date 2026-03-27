/* opentraces review - Frontend interactivity (vanilla JS) */

/**
 * Approve a session via the API.
 */
async function approveSession(traceId) {
    try {
        const resp = await fetch(`/api/session/${traceId}/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const data = await resp.json();
        if (resp.ok) {
            updateBadge('session-badge', 'approved');
            updateRowBadge(traceId, 'approved');
            showNotification('Session approved', 'success');
        } else {
            showNotification(data.error || 'Failed to approve', 'error');
        }
    } catch (err) {
        showNotification('Network error: ' + err.message, 'error');
    }
}

/**
 * Reject a session via the API.
 */
async function rejectSession(traceId) {
    try {
        const resp = await fetch(`/api/session/${traceId}/reject`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const data = await resp.json();
        if (resp.ok) {
            updateBadge('session-badge', 'rejected');
            updateRowBadge(traceId, 'rejected');
            showNotification('Session rejected', 'success');
        } else {
            showNotification(data.error || 'Failed to reject', 'error');
        }
    } catch (err) {
        showNotification('Network error: ' + err.message, 'error');
    }
}

/**
 * Redact a step's content via the API.
 */
async function redactStep(traceId, stepIndex) {
    if (!confirm(`Redact step ${stepIndex}? This will hide the content during review.`)) {
        return;
    }

    try {
        const resp = await fetch(`/api/session/${traceId}/step/${stepIndex}/redact`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const data = await resp.json();
        if (resp.ok) {
            const stepEl = document.getElementById(`step-${stepIndex}`);
            if (stepEl) {
                stepEl.classList.add('ot-redacted');
                const content = stepEl.querySelector('.ot-step-content');
                if (content) {
                    content.innerHTML = '<div class="ot-redacted-content"><em>[Content redacted during review]</em></div>';
                }
                const reasoning = stepEl.querySelector('.ot-reasoning');
                if (reasoning) reasoning.remove();
                const toolCalls = stepEl.querySelector('.ot-tool-calls');
                if (toolCalls) toolCalls.remove();
                const snippets = stepEl.querySelector('.ot-snippets');
                if (snippets) snippets.remove();

                // Replace redact button with badge
                const controls = stepEl.querySelector('.ot-step-controls');
                if (controls) {
                    controls.innerHTML = '<span class="ot-badge ot-badge-redacted">REDACTED</span>';
                }
            }
            showNotification(`Step ${stepIndex} redacted`, 'success');
        } else {
            showNotification(data.error || 'Failed to redact', 'error');
        }
    } catch (err) {
        showNotification('Network error: ' + err.message, 'error');
    }
}

/**
 * Push all approved sessions to HF Hub.
 */
async function pushApproved() {
    if (!confirm('Push all approved sessions to HuggingFace Hub?')) {
        return;
    }

    try {
        const resp = await fetch('/api/push', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const data = await resp.json();
        if (resp.ok) {
            showNotification(data.message || `Pushed ${data.count} sessions`, 'success');
        } else {
            showNotification(data.error || 'Push failed', 'error');
        }
    } catch (err) {
        showNotification('Network error: ' + err.message, 'error');
    }
}

/**
 * Update a badge element's text and class.
 */
function updateBadge(elementId, status) {
    const badge = document.getElementById(elementId);
    if (badge) {
        badge.textContent = status;
        badge.className = `ot-badge ot-badge-${status}`;
    }
}

/**
 * Update the badge for a row in the session list.
 */
function updateRowBadge(traceId, status) {
    const badge = document.getElementById(`badge-${traceId}`);
    if (badge) {
        badge.textContent = status;
        badge.className = `ot-badge ot-badge-${status}`;
    }
    const row = document.querySelector(`tr[data-trace-id="${traceId}"]`);
    if (row) {
        row.className = `ot-row-${status}`;
    }
}

/**
 * Show a temporary notification.
 */
function showNotification(message, type) {
    const existing = document.querySelector('.ot-notification');
    if (existing) existing.remove();

    const notif = document.createElement('div');
    notif.className = `ot-notification ot-notification-${type}`;
    notif.textContent = message;
    notif.style.cssText = `
        position: fixed;
        top: 1rem;
        right: 1rem;
        padding: 0.75rem 1.5rem;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.9rem;
        z-index: 9999;
        animation: slideIn 0.3s ease;
        cursor: pointer;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    `;

    if (type === 'success') {
        notif.style.background = '#22c55e';
        notif.style.color = '#fff';
    } else {
        notif.style.background = '#ef4444';
        notif.style.color = '#fff';
    }

    notif.onclick = () => notif.remove();
    document.body.appendChild(notif);

    setTimeout(() => {
        if (notif.parentNode) {
            notif.style.opacity = '0';
            notif.style.transition = 'opacity 0.3s';
            setTimeout(() => notif.remove(), 300);
        }
    }, 3000);
}

/* Inject notification animation */
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from { opacity: 0; transform: translateX(20px); }
        to { opacity: 1; transform: translateX(0); }
    }
`;
document.head.appendChild(style);
