export const HTML = `<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Quota & Cluster Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #06090e;
    --surface: #0c111d;
    --surface-hover: #111827;
    --border: #1a2436;
    --border-subtle: #131c2b;
    --text: #f1f5f9;
    --text-muted: #7c8ba1;
    --text-dim: #475569;
    
    --claude: #d97706;
    --claude-bg: rgba(217, 119, 6, 0.08);
    --claude-border: rgba(217, 119, 6, 0.25);
    
    --openai: #10a37f;
    --openai-bg: rgba(16, 163, 127, 0.08);
    --openai-border: rgba(16, 163, 127, 0.25);
    
    --google: #38bdf8;
    --google-bg: rgba(56, 189, 248, 0.08);
    --google-border: rgba(56, 189, 248, 0.25);
    
    --nvidia: #76b900;
    --nvidia-bg: rgba(118, 185, 0, 0.08);
    --nvidia-border: rgba(118, 185, 0, 0.25);
    
    --kuma: #5cdd8b;
    --kuma-bg: rgba(92, 221, 139, 0.08);
    --kuma-border: rgba(92, 221, 139, 0.25);
    
    
    --agent: #a855f7;
    --agent-bg: rgba(168, 85, 247, 0.08);
    --agent-border: rgba(168, 85, 247, 0.25);
    --ok: #10b981;
    --warn: #f59e0b;
    --bad: #ef4444;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  
  body {
    font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 16px 18px;
    max-width: 1140px;
    margin: 0 auto;
    font-size: 13.5px;
  }

  /* Compact Header */
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
    gap: 12px;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 9px;
  }

  .brand-icon {
    width: 28px;
    height: 28px;
    background: linear-gradient(135deg, #1e293b, #0f172a);
    border: 1px solid var(--border);
    border-radius: 7px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .brand-icon svg { width: 16px; height: 16px; }

  h1 {
    font-size: 1.15em;
    font-weight: 800;
    letter-spacing: -0.02em;
    color: #ffffff;
    display: flex;
    align-items: baseline;
    gap: 8px;
  }

  .subtitle {
    color: var(--text-dim);
    font-size: 0.72em;
    font-weight: 500;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .live-pill {
    display: flex;
    align-items: center;
    gap: 5px;
    background: rgba(16, 185, 129, 0.08);
    border: 1px solid rgba(16, 185, 129, 0.2);
    color: var(--ok);
    font-size: 0.7em;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 12px;
    font-family: 'JetBrains Mono', monospace;
  }

  .pulse-dot {
    width: 5px;
    height: 5px;
    background: var(--ok);
    border-radius: 50%;
    box-shadow: 0 0 6px var(--ok);
  }

  button {
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 4px 10px;
    border-radius: 6px;
    cursor: pointer;
    font-weight: 600;
    font-size: 0.78em;
    font-family: inherit;
    display: flex;
    align-items: center;
    gap: 5px;
    transition: all 0.15s ease;
  }

  button:hover {
    background: var(--surface-hover);
    border-color: #334155;
  }

  button.primary {
    background: #1e40af;
    border-color: #2563eb;
    color: #ffffff;
  }

  button.primary:hover { background: #1d4ed8; }

  button.danger {
    color: #f87171;
    border-color: rgba(239, 68, 68, 0.2);
    background: rgba(239, 68, 68, 0.04);
    padding: 2px 7px;
    font-size: 0.7em;
  }

  button.danger:hover {
    background: rgba(239, 68, 68, 0.12);
    border-color: #ef4444;
  }

  /* Compact KPI Bar */
  .kpi-bar {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    margin-bottom: 12px;
  }

  .kpi-item {
    background: var(--surface);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 7px 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .kpi-label {
    font-size: 0.7em;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
  }

  .kpi-val {
    font-size: 0.95em;
    font-weight: 700;
    font-family: 'JetBrains Mono', monospace;
  }

  /* 2-Column Responsive Card Grid */
  .cards-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }

  .provider-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    transition: border-color 0.15s;
  }

  .provider-card:hover {
    border-color: #2e3e57;
  }

  .provider-card.anthropic { border-left: 3px solid var(--claude); }
  .provider-card.openai-codex { border-left: 3px solid var(--openai); }
  .provider-card.google-antigravity { border-left: 3px solid var(--google); }
  .provider-card.local-vllm { border-left: 3px solid var(--nvidia); }
  .provider-card.uptime-kuma { border-left: 3px solid var(--kuma); }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 9px;
  }

  .provider-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  .logo-box {
    width: 26px;
    height: 26px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .logo-box.anthropic { background: var(--claude-bg); border: 1px solid var(--claude-border); color: var(--claude); }
  .logo-box.openai-codex { background: var(--openai-bg); border: 1px solid var(--openai-border); color: var(--openai); }
  .logo-box.google-antigravity { background: var(--google-bg); border: 1px solid var(--google-border); }
  .logo-box.local-vllm { background: var(--nvidia-bg); border: 1px solid var(--nvidia-border); color: var(--nvidia); }
  .logo-box.uptime-kuma { background: var(--kuma-bg); border: 1px solid var(--kuma-border); color: var(--kuma); }

  .logo-box svg { width: 15px; height: 15px; }

  .provider-title {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  .provider-name {
    font-size: 0.92em;
    font-weight: 700;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .provider-email {
    font-size: 0.68em;
    color: var(--text-muted);
    font-family: 'JetBrains Mono', monospace;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .badge-group {
    display: flex;
    align-items: center;
    gap: 5px;
    flex-shrink: 0;
  }

  .pill {
    font-size: 0.65em;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 4px;
    background: #141c2b;
    color: #94a3b8;
    border: 1px solid #1e2a3f;
    font-family: 'JetBrains Mono', monospace;
  }

  .pill.peak {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .pill-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
  }

  /* Compact Quota Rows */
  .quota-rows {
    display: flex;
    flex-direction: column;
    gap: 7px;
    margin: 4px 0 8px 0;
  }

  .quota-row {
    display: grid;
    grid-template-columns: minmax(135px, 1.3fr) 1fr minmax(75px, auto);
    gap: 8px;
    align-items: center;
  }

  .quota-name {
    font-size: 0.76em;
    font-weight: 600;
    color: #cbd5e1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .quota-track {
    height: 6px;
    background: #080c13;
    border-radius: 3px;
    border: 1px solid var(--border-subtle);
    overflow: hidden;
  }

  .quota-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.4s ease;
  }

  .quota-stat {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72em;
    color: var(--text-muted);
    text-align: right;
    white-space: nowrap;
    display: flex;
    align-items: center;
    gap: 5px;
    justify-content: flex-end;
  }

  .quota-pct {
    font-weight: 700;
    color: var(--text);
    min-width: 38px;
  }

  .countdown-badge {
    font-size: 0.68em;
    padding: 1px 5px;
    border-radius: 3px;
    background: #0a111a;
    border: 1px solid var(--border-subtle);
    color: #7dd3fc;
  }

  .countdown-badge.urgent {
    background: rgba(239, 68, 68, 0.08);
    border-color: rgba(239, 68, 68, 0.25);
    color: #fca5a5;
  }

  /* Compact extra stats bar for vLLM */
  .extra-stats-bar {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px solid var(--border-subtle);
  }

  .mini-stat {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .mini-stat-label {
    font-size: 0.62em;
    font-weight: 600;
    text-transform: uppercase;
    color: var(--text-dim);
  }

  .mini-stat-val {
    font-size: 0.78em;
    font-weight: 700;
    font-family: 'JetBrains Mono', monospace;
  }

  /* Uptime Kuma: list of offline/down monitors */
  .monitor-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-top: 7px;
    /* Max height scrollable after 8 items */
    max-height: 212px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: #2e3e57 transparent;
  }

  .monitor-list::-webkit-scrollbar { width: 6px; }
  .monitor-list::-webkit-scrollbar-thumb { background: #2e3e57; border-radius: 3px; }

  .monitor-row {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 4px 7px;
    background: rgba(239, 68, 68, 0.06);
    border: 1px solid rgba(239, 68, 68, 0.18);
    border-radius: 5px;
  }

  .monitor-dot {
    width: 6px;
    height: 6px;
    flex: 0 0 auto;
    border-radius: 50%;
    background: var(--bad);
    box-shadow: 0 0 6px var(--bad);
  }

  .monitor-name {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7em;
    font-weight: 600;
    color: #fca5a5;
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  a.monitor-name:hover { text-decoration: underline; }

  .monitor-type {
    margin-left: auto;
    flex: 0 0 auto;
    font-size: 0.62em;
    font-weight: 700;
    text-transform: uppercase;
    color: var(--text-dim);
  }

  .monitor-empty {
    margin-top: 7px;
    padding: 5px 8px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72em;
    color: var(--ok);
    background: rgba(16, 185, 129, 0.06);
    border: 1px solid rgba(16, 185, 129, 0.18);
    border-radius: 4px;
  }

  /* Compact Card footer link */
  .card-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px solid rgba(255,255,255,0.03);
  }

  .dash-link {
    color: #38bdf8;
    font-size: 0.7em;
    font-weight: 600;
    text-decoration: none;
  }

  .dash-link:hover { text-decoration: underline; }

  /* Login / Reconnect Panel */
  .pending-panel {
    margin-top: 8px;
    padding: 9px;
    background: #080c13;
    border: 1px solid var(--border);
    border-radius: 6px;
  }

  .pending-panel p {
    font-size: 0.74em;
    color: var(--text-muted);
    margin-bottom: 6px;
    line-height: 1.35;
  }

  .url-box {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.66em;
    color: #38bdf8;
    background: #04070c;
    padding: 5px 8px;
    border-radius: 4px;
    border: 1px solid var(--border-subtle);
    word-break: break-all;
    margin-bottom: 6px;
  }

  .paste-row {
    display: flex;
    gap: 6px;
  }

  .paste-row input {
    flex: 1;
    background: #04070c;
    border: 1px solid var(--border);
    color: var(--text);
    padding: 4px 8px;
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.74em;
  }

  .err-box {
    color: #f87171;
    font-size: 0.72em;
    background: rgba(239,68,68,0.06);
    border: 1px solid rgba(239,68,68,0.18);
    padding: 5px 8px;
    border-radius: 4px;
    margin-top: 6px;
    font-family: 'JetBrains Mono', monospace;
  }

  footer {
    margin-top: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.7em;
    color: var(--text-dim);
  }

  .spinner {
    width: 12px;
    height: 12px;
    border: 2px solid var(--border);
    border-top-color: #38bdf8;
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
    display: inline-block;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  @media (max-width: 800px) {
    .cards-grid { grid-template-columns: 1fr; }
    .kpi-bar { grid-template-columns: repeat(2, 1fr); }
    .quota-row { grid-template-columns: 90px 1fr 100px; }
  }
</style>
</head>
<body>

<header>
  <div class="brand">
    <div class="brand-icon">
      <svg viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
      </svg>
    </div>
    <h1>AI Quota & Cluster Dashboard <span class="subtitle">Subscrições + Hardware Local</span></h1>
  </div>
  
  <div class="header-actions">
    <div class="live-pill">
      <div class="pulse-dot"></div>
      <span>AO VIVO</span>
    </div>
    <button class="primary" onclick="refresh()" id="btnRefresh">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
      Refresh
    </button>
  </div>
</header>

<!-- Compact KPI Bar -->
<div class="kpi-bar">
  <div class="kpi-item">
    <span class="kpi-label">Provedores</span>
    <span class="kpi-val" id="kpiProviders">3 <span style="font-size:0.75em;color:var(--text-dim)">Cloud</span> + 1 <span style="font-size:0.75em;color:var(--text-dim)">Local</span></span>
  </div>
  <div class="kpi-item">
    <span class="kpi-label">Estado Quotas</span>
    <span class="kpi-val" id="kpiHealth"><span style="color:var(--ok)">●</span> Saudável</span>
  </div>
  <div class="kpi-item">
    <span class="kpi-label">Próximo Reset</span>
    <span class="kpi-val" id="kpiNextReset">--:--:--</span>
  </div>
  <div class="kpi-item">
    <span class="kpi-label">Cluster Local</span>
    <span class="kpi-val" id="kpiLocalVllm" style="color:var(--nvidia)">48 GB <span style="font-size:0.75em;color:var(--text-dim)">3× 5060 Ti</span></span>
  </div>
</div>

<!-- 2-Column Responsive Cards Grid -->
<div class="cards-grid" id="cardsContainer">
  <div style="grid-column: 1 / -1; text-align:center; padding:30px 0;"><span class="spinner"></span> A carregar métricas...</div>
</div>

<footer>
  <div>OAuth PKCE + Auto-Refresh em <code>credentials.json</code></div>
  <div id="lastSync">Sincronizado: --:--:--</div>
</footer>

<script>
let state = { providers: [], reports: [] };
let liveTimerInterval = null;

const LOGOS = {
  anthropic: \`<svg viewBox="0 0 24 24" fill="currentColor"><path d="M17.48 3.52c-.44-.3-.98-.44-1.52-.4H8.04c-.54-.04-1.08.1-1.52.4-.44.3-.76.74-.88 1.26L2.08 19.34c-.18.7.06 1.44.6 1.9.54.46 1.28.56 1.94.26l4.9-2.22 4.9 2.22c.66.3 1.4.2 1.94-.26.54-.46.78-1.2.6-1.9L13.4 4.78c-.12-.52-.44-.96-.88-1.26z"/></svg>\`,
  "openai-codex": \`<svg viewBox="0 0 24 24" fill="currentColor"><path d="M22.28 9.87a5.98 5.98 0 0 0-.52-4.92 6.05 6.05 0 0 0-6.6-2.82A5.98 5.98 0 0 0 10.74.5a6.05 6.05 0 0 0-5.8 4.22 5.98 5.98 0 0 0-4.1 2.9 6.05 6.05 0 0 0 .74 7.2 5.98 5.98 0 0 0 .52 4.92 6.05 6.05 0 0 0 6.6 2.82A5.98 5.98 0 0 0 13.26 23.5a6.05 6.05 0 0 0 5.8-4.22 5.98 5.98 0 0 0 4.1-2.9 6.05 6.05 0 0 0-.88-6.51zM13.26 21.9a4.48 4.48 0 0 1-2.86-.98l.14-.08 4.77-2.76a.79.79 0 0 0 .39-.68v-6.73l2.02 1.17v6.18a4.5 4.5 0 0 1-4.46 3.88zm-8.62-4.13a4.47 4.47 0 0 1-.58-2.98l.14.08 4.77 2.76c.24.14.54.14.78 0l5.83-3.37v2.33l-5.36 3.1a4.5 4.5 0 0 1-5.58-1.92zm-2.02-9.6a4.48 4.48 0 0 1 2.28-2l-.01.16v5.52a.79.79 0 0 0 .39.68l5.83 3.37-2.02 1.17-5.35-3.09a4.5 4.5 0 0 1-1.12-5.81zm15.1 3.86-5.83-3.37 2.02-1.17 5.35 3.09a4.5 4.5 0 0 1 .58 8.79v-5.66a.79.79 0 0 0-.39-.68h-.03l-1.7-1zm2.6-2.14a4.48 4.48 0 0 1-.58 2.98l-.14-.08-4.77-2.76a.79.79 0 0 0-.78 0L8.4 12.35v-2.33l5.36-3.1a4.5 4.5 0 0 1 6.56 5.09zM8.32 10.5l3.68-2.13 3.68 2.13v4.25l-3.68 2.13-3.68-2.13z"/></svg>\`,
  "google-antigravity": \`<svg viewBox="0 0 24 24"><path fill="#4285F4" d="M23.745 12.27c0-.7-.06-1.4-.19-2.07H12v4.51h6.6c-.29 1.52-1.14 2.82-2.4 3.68v3.05h3.88c2.27-2.09 3.665-5.17 3.665-9.17z"/><path fill="#34A853" d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.88-3.05c-1.08.72-2.45 1.16-4.05 1.16-3.12 0-5.77-2.1-6.72-4.93H1.25v3.15C3.26 21.36 7.35 24 12 24z"/><path fill="#FBBC05" d="M5.28 14.27c-.25-.72-.38-1.49-.38-2.27s.13-1.55.38-2.27V6.58H1.25C.45 8.18 0 10.04 0 12s.45 3.82 1.25 5.42l4.03-3.15z"/><path fill="#EA4335" d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.35 0 3.26 2.64 1.25 6.58l4.03 3.15c.95-2.83 3.6-4.98 6.72-4.98z"/></svg>\`,
  "local-vllm": \`<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 3a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2H4zm2 4h12v3H6V7zm0 5h7v5H6v-5zm9 0h3v5h-3v-5z"/></svg>\`,
  "uptime-kuma": \`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12h4l2.5-7 3.5 14 3-9 2 2h5"/></svg>\`,
  "ai-agents": \`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/></svg>\`
};

// Escape external monitor names and URLs from Uptime Kuma before HTML insertion
const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, c => ESC_MAP[c]);
}

function formatCountdown(targetMs) {
  if (!targetMs) return null;
  const diff = targetMs - Date.now();
  if (diff <= 0) return "reset agora";
  const h = Math.floor(diff / 3600000);
  const m = Math.floor((diff % 3600000) / 60000);
  const s = Math.floor((diff % 60000) / 1000);
  
  if (h >= 24) {
    const d = Math.floor(h / 24);
    return \`\${d}d \${h % 24}h\`;
  }
  if (h > 0) return \`\${h}h \${m}m \${s}s\`;
  return \`\${m}m \${s}s\`;
}

function getGradient(frac) {
  if (frac >= 0.9) return "linear-gradient(90deg, #f59e0b, #ef4444)";
  if (frac >= 0.7) return "linear-gradient(90deg, #10b981, #f59e0b)";
  return "linear-gradient(90deg, #38bdf8, #10b981)";
}

function getStatusColor(frac) {
  if (frac >= 0.9) return "var(--bad)";
  if (frac >= 0.7) return "var(--warn)";
  return "var(--ok)";
}

function updateCountdowns() {
  const elements = document.querySelectorAll('[data-reset-time]');
  let closestReset = Infinity;

  elements.forEach(el => {
    const targetMs = parseInt(el.getAttribute('data-reset-time'), 10);
    if (targetMs) {
      if (targetMs > Date.now() && targetMs < closestReset) {
        closestReset = targetMs;
      }
      const text = formatCountdown(targetMs);
      if (text) {
        el.textContent = "reset " + text;
        const diff = targetMs - Date.now();
        if (diff < 3600000) el.classList.add('urgent');
      }
    }
  });
  const cooldownEls = document.querySelectorAll('[data-cooldown-time]');
  cooldownEls.forEach(el => {
    const targetMs = parseInt(el.getAttribute('data-cooldown-time'), 10);
    if (targetMs) {
      const text = formatCountdown(targetMs);
      if (text && targetMs > Date.now()) {
        el.textContent = text;
      } else {
        el.textContent = "a expirar...";
      }
    }
  });

  const kpiResetEl = document.getElementById('kpiNextReset');
  if (closestReset !== Infinity) {
    kpiResetEl.textContent = formatCountdown(closestReset);
  } else {
    kpiResetEl.textContent = "Nenhum";
  }
}

async function refresh() {
  const btn = document.getElementById('btnRefresh');
  btn.disabled = true;

  try {
    const [s, u] = await Promise.all([
      fetch('/api/status').then(r => r.json()),
      fetch('/api/usage').then(r => r.json())
    ]);
    state.providers = s.providers || [];
    state.reports = u.reports || [];
    
    document.getElementById('lastSync').textContent = 'Sincronizado: ' + new Date().toLocaleTimeString();
    render();
  } catch (e) {
    document.getElementById('cardsContainer').innerHTML = '<div class="err-box" style="grid-column:1/-1">Erro: ' + e.message + '</div>';
  } finally {
    btn.disabled = false;
  }
}

function render() {
  let html = '';
  let saturatedCount = 0;

  for (const p of state.providers) {
    const report = state.reports.find(r => r.provider === p.id);
    const isVllm = p.id === 'local-vllm';
    
    html += \`<div class="provider-card \${p.id}">\`;
    
    html += \`<div>
      <div class="card-header">
        <div class="provider-meta">
          <div class="logo-box \${p.id}">\${LOGOS[p.id] || ''}</div>
          <div class="provider-title">
            <span class="provider-name">\${p.label}</span>
            <span class="provider-email">\${p.email || 'Não autenticado'}</span>
          </div>
        </div>
        
        <div class="badge-group">\`;
        
        if (p.plan) html += \`<span class="pill">\${p.plan}</span>\`;
        
        if (report && report.limits && report.limits.length) {
          const worst = Math.max(...report.limits.map(l => l.usedFraction));
          const worstPct = Math.round(worst * 100);
          html += \`<span class="pill peak">
            <span class="pill-dot" style="background:\${getStatusColor(worst)}"></span>
            \${worstPct}%
          </span>\`;
        }
        
        if (!isVllm) {
          if (p.connected) {
            html += \`<button class="danger" onclick="logout('\${p.id}')">desligar</button>\`;
          } else {
            html += \`<button class="primary" onclick="login('\${p.id}')">Login</button>\`;
          }
        }
        
    html += \`</div></div>\`;

    if (p.login && p.login.status === 'pending') {
      html += \`<div class="pending-panel">
        <p>Abre o URL abaixo para autorizar:</p>
        <div class="url-box">\${p.login.url}</div>
        <div class="paste-row">
          <input id="code-\${p.id}" placeholder="Código ou code#state">
          <button class="primary" onclick="submitCode('\${p.id}')">OK</button>
        </div>
      </div>\`;
    }

    if (p.login && p.login.status === 'error') {
      html += \`<div class="err-box">\${p.login.message || 'Erro no login'}</div>\`;
    }

    // Quotas progress bars
    if (report && report.limits && report.limits.length) {
      html += \`<div class="quota-rows">\`;
      for (const l of report.limits) {
        if (l.usedFraction >= 0.99) saturatedCount++;
        const pct = Math.min(100, Math.max(0, l.usedFraction * 100));
        html += \`<div class="quota-row">
          <span class="quota-name">\${l.label}</span>
          <div class="quota-track">
            <div class="quota-fill" style="width:\${pct}%;background:\${getGradient(l.usedFraction)}"></div>
          </div>
          <div class="quota-stat">
            <span class="quota-pct" style="color:\${getStatusColor(l.usedFraction)}">\${pct.toFixed(1)}%</span>
            \${l.resetsAt ? \`<span class="countdown-badge" data-reset-time="\${l.resetsAt}">reset ...</span>\` : ''}
          </div>
        </div>\`;
      }
      html += \`</div>\`;
    }

    if (report && report.error) {
      if (report.cooldownUntil && report.cooldownUntil > Date.now()) {
        html += \`<div class="err-box" style="display:flex;align-items:center;justify-content:space-between;gap:6px">
          <span>⚠️ Rate limit na consulta de quota</span>
          <span class="countdown-badge urgent" style="font-weight:600">cooldown: <strong data-cooldown-time="\${report.cooldownUntil}">...</strong></span>
        </div>\`;
      } else {
        html += \`<div class="err-box">\${report.error}</div>\`;
      }
    }
    
    html += \`</div>\`; // end top group

    if (p.connected && report && report.dashboardUrl) {
      html += \`<div class="card-footer">
        <a class="dash-link" href="\${report.dashboardUrl}" target="_blank" rel="noreferrer">
          Dashboard Oficial ↗
        </a>
      </div>\`;
    }

    html += \`</div>\`;
  }

  // Local vLLM Card
  const vllmReport = state.reports.find(r => r.provider === 'local-vllm');
  if (vllmReport && !state.providers.some(p => p.id === 'local-vllm')) {
    html += \`<div class="provider-card local-vllm">
      <div>
        <div class="card-header">
          <div class="provider-meta">
            <div class="logo-box local-vllm">\${LOGOS['local-vllm']}</div>
            <div class="provider-title">
              <span class="provider-name">\${vllmReport.label}</span>
              <span class="provider-email">\${vllmReport.email}</span>
            </div>
          </div>
          <div class="badge-group">
            <span class="pill">\${vllmReport.plan}</span>
            <span class="pill peak"><span class="pill-dot" style="background:var(--nvidia)"></span>Local</span>
          </div>
        </div>\`;

        if (vllmReport.limits && vllmReport.limits.length) {
          html += \`<div class="quota-rows">\`;
          for (const l of vllmReport.limits) {
            const pct = Math.min(100, Math.max(0, l.usedFraction * 100));
            const isVram = l.id === 'vllm:vram_allocation';
            const barGradient = isVram ? 'linear-gradient(90deg, #76b900, #84cc16)' : 'linear-gradient(90deg, #0284c7, #10b981)';
            const statColor = isVram ? 'var(--nvidia)' : '#38bdf8';
            html += \`<div class="quota-row">
              <span class="quota-name">\${l.label}</span>
              <div class="quota-track">
                <div class="quota-fill" style="width:\${pct}%;background:\${barGradient}"></div>
              </div>
              <div class="quota-stat"><span class="quota-pct" style="color:\${statColor}">\${pct.toFixed(1)}%</span></div>
            </div>\`;
          }
          html += \`</div>\`;
        }

        if (vllmReport.extraStats) {
          html += \`<div class="extra-stats-bar">
            <div class="mini-stat"><span class="mini-stat-label">Ativos</span><span class="mini-stat-val" style="color:#38bdf8">\${vllmReport.extraStats.runningReqs || 0} reqs</span></div>
            <div class="mini-stat"><span class="mini-stat-label">Fila</span><span class="mini-stat-val">\${vllmReport.extraStats.waitingReqs || 0}</span></div>
            <div class="mini-stat"><span class="mini-stat-label">Cache Hit</span><span class="mini-stat-val" style="color:#10b981">\${vllmReport.extraStats.cacheHitRate}</span></div>
            <div class="mini-stat"><span class="mini-stat-label">Tokens</span><span class="mini-stat-val">\${(vllmReport.extraStats.tokensGenerated || 0).toLocaleString()}</span></div>
          </div>\`;
        }
        if (vllmReport.error) {
          html += \`<div class="err-box">\${vllmReport.error}</div>\`;
        }
        if (vllmReport.dashboardUrl) {
          html += \`<div class="card-footer"><a class="dash-link" href="\${vllmReport.dashboardUrl}" target="_blank" rel="noreferrer">vLLM API ↗</a></div>\`;
        }
      html += \`</div></div>\`;
  }

  // Uptime Kuma Card — monitors with DOWN status
  const kumaReport = state.reports.find(r => r.provider === 'uptime-kuma');
  if (kumaReport) {
    const downList = kumaReport.downMonitors || [];
    const stats = kumaReport.extraStats || {};
    const downColor = downList.length ? 'var(--bad)' : 'var(--ok)';
    html += \`<div class="provider-card uptime-kuma">
      <div>
        <div class="card-header">
          <div class="provider-meta">
            <div class="logo-box uptime-kuma">\${LOGOS['uptime-kuma']}</div>
            <div class="provider-title">
              <span class="provider-name">\${esc(kumaReport.label)}</span>
              <span class="provider-email">\${esc(kumaReport.email)}</span>
            </div>
          </div>
          <div class="badge-group">
            <span class="pill peak">
              <span class="pill-dot" style="background:\${downColor}"></span>
              \${downList.length} desligado\${downList.length === 1 ? '' : 's'}
            </span>
          </div>
        </div>
        <div class="extra-stats-bar">
          <div class="mini-stat"><span class="mini-stat-label">Monitores</span><span class="mini-stat-val">\${stats.total || 0}</span></div>
          <div class="mini-stat"><span class="mini-stat-label">Ligados</span><span class="mini-stat-val" style="color:var(--ok)">\${stats.up || 0}</span></div>
          <div class="mini-stat"><span class="mini-stat-label">Desligados</span><span class="mini-stat-val" style="color:var(--bad)">\${stats.down || 0}</span></div>
          <div class="mini-stat"><span class="mini-stat-label">Pendentes</span><span class="mini-stat-val" style="color:var(--warn)">\${stats.pending || 0}</span></div>
        </div>\`;

        if (downList.length) {
          html += \`<div class="monitor-list">\`;
          for (const m of downList) {
            const name = m.url
              ? \`<a class="monitor-name" href="\${esc(m.url)}" target="_blank" rel="noreferrer">\${esc(m.name)}</a>\`
              : \`<span class="monitor-name">\${esc(m.name)}</span>\`;
            html += \`<div class="monitor-row">
              <span class="monitor-dot"></span>
              \${name}
              <span class="monitor-type">\${esc(m.type)}</span>
            </div>\`;
          }
          html += \`</div>\`;
        } else if (!kumaReport.error) {
          html += \`<div class="monitor-empty">Nenhum monitor desligado</div>\`;
        }

        if (kumaReport.error) {
          html += \`<div class="err-box">\${esc(kumaReport.error)}</div>\`;
        }
        html += \`<div class="card-footer"><a class="dash-link" href="\${esc(kumaReport.dashboardUrl)}" target="_blank" rel="noreferrer">Uptime Kuma ↗</a></div>\`;
      html += \`</div></div>\`;
  }

  // Autonomous AI Agents & SRE Card
  const agentsReport = state.reports.find(r => r.provider === 'ai-agents');
  if (agentsReport) {
    const list = agentsReport.agentItems || [];
    const stats = agentsReport.extraStats || {};
    html += \`<div class="provider-card ai-agents" style="border-color:var(--agent-border)">
      <div>
        <div class="card-header">
          <div class="provider-meta">
            <div class="logo-box" style="color:var(--agent);background:var(--agent-bg)">\${LOGOS['ai-agents']}</div>
            <div class="provider-title">
              <span class="provider-name">\${esc(agentsReport.label)}</span>
              <span class="provider-email">\${esc(agentsReport.email)}</span>
            </div>
          </div>
          <div class="badge-group">
            <span class="pill" style="background:var(--agent-bg);color:var(--agent);border:1px solid var(--agent-border)">
              <span class="pill-dot" style="background:var(--ok)"></span>
              \${list.length} ativos
            </span>
          </div>
        </div>
        <div class="extra-stats-bar">
          <div class="mini-stat"><span class="mini-stat-label">Agentes</span><span class="mini-stat-val" style="color:var(--agent)">\${stats.agentsCount ?? list.length}</span></div>
          \${stats.k3sNodes === undefined ? '' : \`<div class="mini-stat"><span class="mini-stat-label">K3s Nós</span><span class="mini-stat-val" style="color:var(--ok)">\${esc(String(stats.k3sNodes))}</span></div>\`}
          \${stats.proxmoxVms === undefined ? '' : \`<div class="mini-stat"><span class="mini-stat-label">VMs</span><span class="mini-stat-val" style="color:#38bdf8">\${esc(String(stats.proxmoxVms))}</span></div>\`}
          <div class="mini-stat"><span class="mini-stat-label">Online</span><span class="mini-stat-val" style="color:var(--nvidia)">\${stats.activeSandboxes ?? 0}</span></div>
        </div>
        <div class="monitor-list" style="max-height:220px">\`;
        for (const ag of list) {
          html += \`<div class="monitor-row" style="padding:6px 0;border-bottom:1px solid var(--border-subtle)">
            <span class="monitor-dot" style="background:var(--ok)"></span>
            <div style="flex:1;min-width:0">
              <div style="font-weight:600;font-size:0.9em;color:#fff">\${esc(ag.name)} <span style="font-weight:400;color:var(--text-muted);font-size:0.82em">(\${esc(ag.role)})</span></div>
              <div style="font-size:0.75em;color:var(--text-dim)">\${esc(ag.detail)}</div>
            </div>
            <span class="pill" style="font-size:0.68em;padding:2px 6px;background:rgba(16, 185, 129, 0.1);color:var(--ok)">\${esc(ag.status)}</span>
          </div>\`;
        }
        html += \`</div>
        <div class="card-footer"><a class="dash-link" href="\${esc(agentsReport.dashboardUrl)}" target="_blank" rel="noreferrer">Repositório & Workflows ↗</a></div>
      </div></div>\`;
  }

  document.getElementById('cardsContainer').innerHTML = html;
  
  if (saturatedCount > 0) {
    document.getElementById('kpiHealth').innerHTML = \`<span style="color:var(--bad)">●</span> \${saturatedCount} Saturada\${saturatedCount>1?'s':''}\`;
  } else {
    document.getElementById('kpiHealth').innerHTML = \`<span style="color:var(--ok)">●</span> 100% OK\`;
  }

  updateCountdowns();
}

async function login(id) {
  const res = await fetch('/api/login/' + id, { method: 'POST' });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  window.open(data.url, '_blank');
  await refresh();
  const poll = setInterval(async () => {
    const s = await fetch('/api/status').then(r => r.json());
    const p = (s.providers || []).find(x => x.id === id);
    if (p && (p.connected || (p.login && p.login.status !== 'pending'))) {
      clearInterval(poll);
      refresh();
    }
  }, 2500);
  setTimeout(() => clearInterval(poll), 300000);
}

async function submitCode(id) {
  const input = document.getElementById('code-' + id);
  const res = await fetch('/api/login/' + id + '/code', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ code: input.value })
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  refresh();
}

async function logout(id) {
  if (!confirm('Remover credenciais deste provedor?')) return;
  await fetch('/api/logout/' + id, { method: 'POST' });
  refresh();
}

refresh();
if (!liveTimerInterval) liveTimerInterval = setInterval(updateCountdowns, 1000);
setInterval(refresh, 30000);
</script>
</body>
</html>`;
