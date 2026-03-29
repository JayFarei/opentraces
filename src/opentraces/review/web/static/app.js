/* opentraces web - Frontend interactivity (vanilla JS) */

/**
 * Approve a session via the API.
 */
async function approveSession(traceId) {
    const approveBtn = document.getElementById('btn-approve');
    const rejectBtn = document.getElementById('btn-reject');

    try {
        if (approveBtn) { approveBtn.disabled = true; approveBtn.textContent = 'Approving...'; }
        if (rejectBtn) { rejectBtn.disabled = true; }

        const resp = await fetch(`/api/session/${traceId}/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const data = await resp.json();
        if (resp.ok) {
            updateBadge('session-badge', 'ready');
            updateRowBadge(traceId, 'ready');
            showNotification('Session moved to Ready', 'success');
            showNextPendingLink();
            updateNavPushBtn();
        } else {
            showNotification(data.error || 'Failed to approve', 'error');
            if (approveBtn) { approveBtn.disabled = false; approveBtn.textContent = 'Mark Ready'; }
            if (rejectBtn) { rejectBtn.disabled = false; }
        }
    } catch (err) {
        showNotification('Network error: ' + err.message, 'error');
        if (approveBtn) { approveBtn.disabled = false; approveBtn.textContent = 'Mark Ready'; }
        if (rejectBtn) { rejectBtn.disabled = false; }
    }
}

/**
 * Reject a session via the API.
 */
async function rejectSession(traceId) {
    const approveBtn = document.getElementById('btn-approve');
    const rejectBtn = document.getElementById('btn-reject');

    try {
        if (rejectBtn) { rejectBtn.disabled = true; rejectBtn.textContent = 'Rejecting...'; }
        if (approveBtn) { approveBtn.disabled = true; }

        const resp = await fetch(`/api/session/${traceId}/reject`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const data = await resp.json();
        if (resp.ok) {
            updateBadge('session-badge', 'rejected');
            updateRowBadge(traceId, 'rejected');
            showNotification('Session rejected', 'success');
            showNextPendingLink();
        } else {
            showNotification(data.error || 'Failed to reject', 'error');
            if (rejectBtn) { rejectBtn.disabled = false; rejectBtn.textContent = 'Reject Session'; }
            if (approveBtn) { approveBtn.disabled = false; }
        }
    } catch (err) {
        showNotification('Network error: ' + err.message, 'error');
        if (rejectBtn) { rejectBtn.disabled = false; rejectBtn.textContent = 'Reject Session'; }
        if (approveBtn) { approveBtn.disabled = false; }
    }
}

/**
 * Redact a step's content via the API.
 */
async function redactStep(traceId, stepIndex) {
    if (!confirm("This will permanently remove this step's content from the local inbox file. This cannot be undone. Continue?")) {
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
                // Remove step flags when redacted
                const stepFlags = stepEl.querySelectorAll('.ot-step-flag');
                stepFlags.forEach(function(flag) { flag.remove(); });

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
 * Push ready or committed sessions to HF Hub.
 */
async function pushApproved() {
    if (!confirm('Push ready sessions to HuggingFace Hub?')) {
        return;
    }

    const navBtn = document.getElementById('nav-push-btn');
    const statsBtn = document.getElementById('stats-push-btn');
    var navOrigText, statsOrigText;

    try {
        if (navBtn) {
            navOrigText = navBtn.textContent;
            navBtn.disabled = true;
            navBtn.textContent = 'Pushing...';
        }
        if (statsBtn) {
            statsOrigText = statsBtn.textContent;
            statsBtn.disabled = true;
            statsBtn.textContent = 'Pushing...';
        }

        const resp = await fetch('/api/push', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const data = await resp.json();
        if (resp.ok) {
            showNotification(data.message || `Pushed ${data.count} sessions`, 'success');
            if (navBtn) { navBtn.textContent = navOrigText; }
            if (statsBtn) { statsBtn.textContent = statsOrigText; }
        } else {
            showNotification(data.error || 'Push failed', 'error');
            if (navBtn) { navBtn.disabled = false; navBtn.textContent = navOrigText; }
            if (statsBtn) { statsBtn.disabled = false; statsBtn.textContent = statsOrigText; }
        }
    } catch (err) {
        showNotification('Network error: ' + err.message, 'error');
        if (navBtn) { navBtn.disabled = false; navBtn.textContent = navOrigText; }
        if (statsBtn) { statsBtn.disabled = false; statsBtn.textContent = statsOrigText; }
    }
}

/**
 * Show the "Next pending" link after a verdict.
 */
function showNextPendingLink() {
    var linkContainer = document.getElementById('verdict-next-link');
    if (linkContainer) {
        linkContainer.style.display = 'block';
    }
}

/**
 * Enable the nav push button if there are ready sessions.
 */
function updateNavPushBtn() {
    var navBtn = document.getElementById('nav-push-btn');
    if (navBtn) {
        navBtn.disabled = false;
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
 * Show a temporary notification with aria-live for accessibility.
 */
function showNotification(message, type) {
    var container = document.getElementById('ot-notification-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'ot-notification-container';
        container.setAttribute('aria-live', 'polite');
        container.setAttribute('aria-atomic', 'true');
        container.style.cssText = 'position:fixed;top:1rem;right:1rem;z-index:9999;';
        document.body.appendChild(container);
    }

    const existing = container.querySelector('.ot-notification');
    if (existing) existing.remove();

    const notif = document.createElement('div');
    notif.className = `ot-notification ot-notification-${type}`;
    notif.textContent = message;
    notif.style.cssText = `
        padding: 0.75rem 1.5rem;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.9rem;
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

    notif.onclick = function() { notif.remove(); };
    container.appendChild(notif);

    setTimeout(function() {
        if (notif.parentNode) {
            notif.style.opacity = '0';
            notif.style.transition = 'opacity 0.3s';
            setTimeout(function() { notif.remove(); }, 300);
        }
    }, 3000);
}

/**
 * Truncate tool input code blocks longer than 500 characters with a "Show more" toggle.
 */
function truncateToolInputs() {
    var toolSections = document.querySelectorAll('.ot-tool-section');
    toolSections.forEach(function(section) {
        var strong = section.querySelector('strong');
        if (!strong || strong.textContent.trim().toLowerCase() !== 'input:') return;

        var codeBlock = section.querySelector('.ot-code-block');
        if (!codeBlock) return;

        var code = codeBlock.querySelector('code');
        var textContent = code ? code.textContent : codeBlock.textContent;
        if (textContent.length <= 500) return;

        var truncated = textContent.substring(0, 500);
        var fullContent = textContent;

        if (code) {
            code.textContent = truncated + '...';
        } else {
            codeBlock.textContent = truncated + '...';
        }

        var toggle = document.createElement('button');
        toggle.className = 'ot-show-more-btn';
        toggle.textContent = 'Show more';
        toggle.setAttribute('type', 'button');

        var expanded = false;
        toggle.addEventListener('click', function() {
            expanded = !expanded;
            if (code) {
                code.textContent = expanded ? fullContent : truncated + '...';
            } else {
                codeBlock.textContent = expanded ? fullContent : truncated + '...';
            }
            toggle.textContent = expanded ? 'Show less' : 'Show more';
        });

        section.appendChild(toggle);
    });
}

/**
 * On page load, check if nav push button should be enabled,
 * and apply tool input truncation.
 */
document.addEventListener('DOMContentLoaded', function() {
    /* Enable nav push button if there are ready badges on the page */
    var approvedBadges = document.querySelectorAll('.ot-badge-ready');
    if (approvedBadges.length > 0) {
        var navBtn = document.getElementById('nav-push-btn');
        if (navBtn) navBtn.disabled = false;
    }

    /* Apply tool input truncation */
    truncateToolInputs();
});

/* Inject notification animation */
var style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from { opacity: 0; transform: translateX(20px); }
        to { opacity: 1; transform: translateX(0); }
    }
`;
document.head.appendChild(style);
