"""
OBS Stream Control Panel
Web server that exposes a browser-based UI to start/stop the OBS stream
and update URLs of URL/API data sources.

Usage:
    python stream_server.py [--obs-host localhost] [--obs-port 4455]
                            [--obs-password secret] [--port 5000]

Dependencies:
    pip install flask obs-websocket-py
"""

import argparse
import contextlib
import io
import pathlib
import re
import sys
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template_string, request, Response

try:
    import obswebsocket
    from obswebsocket import obsws
    from obswebsocket import requests as obsrequests
except ImportError:
    print("Error: obs-websocket-py is not installed.")
    print("Install it with: pip install obs-websocket-py")
    sys.exit(1)

# Import the URL-change logic from the sibling module
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from stream_config import change_source_url  # noqa: E402

app = Flask(__name__)

obs_config: dict = {
    "host": "localhost",
    "port": 4455,
    "password": "gutzdcev",
    "obs2k_url": "file:///home/gutz/GutzDcStreamControl/OBS2K.html",
}


# ---------------------------------------------------------------------------
# OBS helpers
# ---------------------------------------------------------------------------

def _connect() -> obsws:
    ws = obsws(obs_config["host"], obs_config["port"], obs_config["password"])
    ws.connect()
    return ws


def _call(req):
    """Open a connection, make one call, disconnect, return the response."""
    ws = _connect()
    try:
        return ws.call(req)
    finally:
        with contextlib.suppress(Exception):
            ws.disconnect()


def _set_browser_source_url(source_name: str, new_url: str) -> bool:
    """Set the URL on a browser source."""
    try:
        _call(obsrequests.SetInputSettings(
            inputName=source_name,
            inputSettings={"url": new_url},
        ))
        return True
    except Exception:
        return False


def _reload_all_browser_sources() -> list:
    """Hide then show every browser_source in every scene to force a reload."""
    import time
    ws = _connect()
    try:
        scenes = ws.call(obsrequests.GetSceneList()).getScenes()
        toggled = []
        for scene in scenes:
            sn = scene["sceneName"]
            items = ws.call(obsrequests.GetSceneItemList(sceneName=sn)).getSceneItems()
            for item in items:
                if item.get("inputKind") == "browser_source":
                    sid = item["sceneItemId"]
                    try:
                        ws.call(obsrequests.SetSceneItemEnabled(sceneName=sn, sceneItemId=sid, sceneItemEnabled=False))
                        toggled.append((sn, sid))
                    except Exception:
                        pass
        if toggled:
            time.sleep(0.3)
            for sn, sid in toggled:
                with contextlib.suppress(Exception):
                    ws.call(obsrequests.SetSceneItemEnabled(sceneName=sn, sceneItemId=sid, sceneItemEnabled=True))
        return [f"{sn}/{sid}" for sn, sid in toggled]
    except Exception:
        return []
    finally:
        with contextlib.suppress(Exception):
            ws.disconnect()


# ---------------------------------------------------------------------------
# HTML template (single-page control panel)
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GUTZ DC Stream Control</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:       #0d0d14;
    --surface:  #16161f;
    --border:   #2a2a3a;
    --text:     #e0e0f0;
    --muted:    #7070a0;
    --live:     #e03535;
    --offline:  #3a3a50;
    --accent:   #5b8ef0;
    --success:  #35c47a;
    --warn:     #e09035;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 14px;
    min-height: 100vh;
  }

  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  .header-brand {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
  }
  .header-brand img {
    height: 40px;
    width: 40px;
    object-fit: contain;
  }
  header h1 { font-size: 16px; font-weight: 600; letter-spacing: .05em; }

  .conn-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .conn-bar input {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    padding: 5px 10px;
    font-size: 13px;
    width: 140px;
  }
  .conn-bar input[placeholder="Port"] { width: 70px; }
  .conn-bar input[placeholder="Password"] { width: 120px; }
  .conn-bar input[placeholder^="file:"] { width: 280px; font-family: 'Consolas', 'Fira Code', monospace; font-size: 12px; }
  .conn-bar .conn-label { font-size: 11px; color: var(--muted); white-space: nowrap; }

  .conn-bar button {
    background: var(--accent);
    border: none;
    border-radius: 6px;
    color: #fff;
    cursor: pointer;
    font-size: 13px;
    padding: 6px 14px;
    white-space: nowrap;
  }
  .conn-bar button:hover { filter: brightness(1.15); }

  main {
    max-width: 800px;
    margin: 32px auto;
    padding: 0 24px;
    display: flex;
    flex-direction: column;
    gap: 24px;
  }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px 24px;
  }

  .card-title {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 16px;
  }

  .card-title.collapsible {
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
    user-select: none;
    margin-bottom: 16px;
  }
  .card-title.collapsible:hover { color: var(--text); }

  .chevron {
    font-style: normal;
    font-size: 10px;
    transition: transform .2s;
    display: inline-block;
  }
  .collapsed .chevron { transform: rotate(-90deg); }

  .collapsible-body {
    overflow: hidden;
    transition: max-height .25s ease, opacity .2s ease;
    max-height: 2000px;
    opacity: 1;
  }
  .collapsed .collapsible-body {
    max-height: 0;
    opacity: 0;
  }
  .collapsed .card-title.collapsible { margin-bottom: 0; }

  /* --- Stream status card --- */
  .stream-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  .status-badge {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 18px;
    font-weight: 700;
  }

  .dot {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--offline);
    flex-shrink: 0;
    transition: background .3s;
  }
  .dot.live {
    background: var(--live);
    box-shadow: 0 0 8px var(--live);
    animation: pulse 1.5s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { box-shadow: 0 0 6px var(--live); }
    50%       { box-shadow: 0 0 16px var(--live); }
  }

  .btn-stream {
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-size: 15px;
    font-weight: 600;
    padding: 10px 28px;
    transition: filter .15s, background .3s;
  }
  .btn-stream.start { background: var(--success); color: #000; }
  .btn-stream.stop  { background: var(--live);    color: #fff; }
  .btn-stream:hover { filter: brightness(1.15); }
  .btn-stream:disabled { opacity: .45; cursor: default; filter: none; }

  /* --- URL Sources card --- */
  .source-list { display: flex; flex-direction: column; gap: 14px; }

  .source-item {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
  }

  .source-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--accent);
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .source-kind {
    font-size: 10px;
    font-weight: 400;
    color: var(--muted);
    background: var(--border);
    border-radius: 4px;
    padding: 1px 6px;
  }

  .url-row {
    display: flex;
    gap: 8px;
  }

  .url-row input {
    flex: 1;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-size: 13px;
    padding: 6px 10px;
    font-family: 'Consolas', 'Fira Code', monospace;
  }
  .url-row input:focus { outline: none; border-color: var(--accent); }

  .url-row button {
    background: var(--accent);
    border: none;
    border-radius: 6px;
    color: #fff;
    cursor: pointer;
    font-size: 13px;
    padding: 6px 14px;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .url-row button:hover { filter: brightness(1.15); }
  .url-row button:disabled { opacity: .45; cursor: default; filter: none; }

  .source-msg {
    font-size: 12px;
    margin-top: 6px;
    height: 16px;
    color: var(--success);
  }
  .source-msg.err { color: var(--live); }

  /* --- Preview card --- */
  .preview-wrap {
    position: relative;
    background: #000;
    border-radius: 6px;
    overflow: hidden;
    aspect-ratio: 16/9;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .preview-wrap img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }
  .preview-overlay {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--muted);
    font-size: 13px;
  }
  .preview-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 10px;
    font-size: 12px;
    color: var(--muted);
  }
  .preview-footer button {
    background: none;
    border: 1px solid var(--border);
    border-radius: 5px;
    color: var(--muted);
    cursor: pointer;
    font-size: 11px;
    padding: 3px 10px;
  }
  .preview-footer button:hover { border-color: var(--accent); color: var(--accent); }

  /* --- Tournament card --- */
  .t-input-row {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
    margin-top: 12px;
  }

  .t-input-row input {
    flex: 1;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-size: 13px;
    padding: 7px 10px;
    font-family: 'Consolas', 'Fira Code', monospace;
  }
  .t-input-row input:focus { outline: none; border-color: var(--accent); }

  .t-input-row button {
    background: var(--warn);
    border: none;
    border-radius: 6px;
    color: #000;
    cursor: pointer;
    font-size: 13px;
    font-weight: 600;
    padding: 7px 18px;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .t-input-row button:hover { filter: brightness(1.12); }
  .t-input-row button:disabled { opacity: .45; cursor: default; filter: none; }

  .t-meta {
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 10px;
  }
  .t-meta b { color: var(--text); }

  .t-results { display: flex; flex-direction: column; }

  .t-row {
    display: grid;
    grid-template-columns: 18px 1fr 2fr;
    align-items: baseline;
    gap: 8px;
    padding: 5px 0;
    font-size: 12px;
    border-bottom: 1px solid var(--border);
  }
  .t-row:last-child { border-bottom: none; }
  .t-icon { font-weight: bold; text-align: center; }
  .t-row.ok  .t-icon { color: var(--success); }
  .t-row.err .t-icon { color: var(--live); }
  .t-source { font-weight: 600; }
  .t-url { color: var(--muted); font-family: 'Consolas', 'Fira Code', monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* --- Empty / error states --- */
  .empty { color: var(--muted); font-size: 13px; padding: 8px 0; }

  /* --- Toast --- */
  #toast {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%) translateY(20px);
    background: #222235;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font-size: 13px;
    padding: 10px 20px;
    opacity: 0;
    pointer-events: none;
    transition: opacity .25s, transform .25s;
    z-index: 100;
  }
  #toast.show {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
  #toast.ok  { border-color: var(--success); color: var(--success); }
  #toast.err { border-color: var(--live);    color: var(--live); }
</style>
</head>
<body>

<header>
  <div class="header-brand">
    <img src="/logo" alt="GUTZ DC Logo">
    <h1>GUTZ DC Stream Control</h1>
  </div>
  <div class="conn-bar">
    <input id="cfgHost" placeholder="Host" value="localhost">
    <input id="cfgPort" placeholder="Port" value="4455">
    <input id="cfgPass" placeholder="Password" type="password">
    <button onclick="saveConfig()">Connect</button>
    <span class="conn-label">OBS2K&nbsp;path</span>
    <input id="cfgObs2kUrl" placeholder="file:///path/to/OBS2K.html" spellcheck="false">
  </div>
</header>

<main>

  <!-- Stream control -->
  <div class="card" id="streamCard">
    <div class="card-title">Stream</div>
    <div class="stream-row">
      <div class="status-badge">
        <div class="dot" id="statusDot"></div>
        <span id="statusText">—</span>
      </div>
      <button class="btn-stream start" id="btnStream" onclick="toggleStream()" disabled>
        Loading…
      </button>
    </div>
  </div>

  <!-- Stream preview -->
  <div class="card collapsed" id="previewCard">
    <div class="card-title collapsible" onclick="togglePreview()">
      Stream Preview
      <span class="chevron">&#9660;</span>
    </div>
    <div class="collapsible-body">
      <div class="preview-wrap">
        <img id="previewImg" style="display:none">
        <div class="preview-overlay" id="previewOverlay">Open to start preview</div>
      </div>
      <div class="preview-footer">
        <span id="previewMeta">—</span>
        <button onclick="fetchPreview()">Refresh now</button>
      </div>
    </div>
  </div>

  <!-- Tournament setup -->
  <div class="card">
    <div class="card-title">Tournament Setup</div>
    <p>Hier URL der 3K Live Übersichtseite mit allen Boards einfügen</p>
    <div class="t-input-row">
      <input id="tournamentUrl"
             placeholder="https://live.3k-darts.com/event/{dbID}/{tournamentID}"
             spellcheck="false">
      <button onclick="applyTournament(this)">Apply All</button>
    </div>
    <div id="tMeta" class="t-meta"></div>
    <div id="tResults" class="t-results"></div>
  </div>

  <!-- URL Sources -->
  <div class="card collapsed" id="sourcesCard">
    <div class="card-title collapsible" onclick="toggleSources()">
      URL / API Sources
      <span class="chevron">&#9660;</span>
    </div>
    <div class="collapsible-body">
      <div id="sourceList" class="source-list">
        <div class="empty">Loading sources…</div>
      </div>
    </div>
  </div>

</main>

<div id="toast"></div>

<script>
// ---- Config ---------------------------------------------------------------

async function saveConfig() {
  const host     = document.getElementById('cfgHost').value.trim();
  const port     = parseInt(document.getElementById('cfgPort').value) || 4455;
  const password = document.getElementById('cfgPass').value;
  const obs2k_url = document.getElementById('cfgObs2kUrl').value.trim();
  const r = await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({host, port, password, obs2k_url}),
  });
  if (r.ok) {
    toast('Config saved — ' + host + ':' + port, 'ok');
    refreshAll();
  } else {
    toast('Failed to save config', 'err');
  }
}

async function loadConfig() {
  const r = await fetch('/api/config');
  if (!r.ok) return;
  const d = await r.json();
  document.getElementById('cfgHost').value     = d.host     || 'localhost';
  document.getElementById('cfgPort').value     = d.port     || 4455;
  document.getElementById('cfgObs2kUrl').value = d.obs2k_url || '';
}

// ---- Stream ---------------------------------------------------------------

let streamActive = false;

async function refreshStatus() {
  try {
    const r = await fetch('/api/stream/status');
    const d = await r.json();
    if (d.error) { setStatus(null); return; }
    streamActive = d.active;
    setStatus(d.active);
  } catch {
    setStatus(null);
  }
}

function setStatus(active) {
  const dot  = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  const btn  = document.getElementById('btnStream');
  if (active === null) {
    dot.className  = 'dot';
    text.textContent = 'OBS not reachable';
    btn.disabled = true;
    btn.textContent = '—';
    return;
  }
  btn.disabled = false;
  if (active) {
    dot.className  = 'dot live';
    text.textContent = 'LIVE';
    btn.className  = 'btn-stream stop';
    btn.textContent = 'Stop Stream';
  } else {
    dot.className  = 'dot';
    text.textContent = 'Offline';
    btn.className  = 'btn-stream start';
    btn.textContent = 'Start Stream';
  }
}

async function toggleStream() {
  const btn = document.getElementById('btnStream');
  btn.disabled = true;
  const endpoint = streamActive ? '/api/stream/stop' : '/api/stream/start';
  try {
    const r = await fetch(endpoint, {method: 'POST'});
    const d = await r.json();
    if (d.error) {
      toast('Error: ' + d.error, 'err');
    } else {
      toast(streamActive ? 'Stream stopped' : 'Stream started', 'ok');
      // Poll until status actually changes (OBS takes a moment)
      for (let i = 0; i < 10; i++) {
        await sleep(600);
        await refreshStatus();
        if (streamActive !== (endpoint === '/api/stream/stop')) break;
      }
    }
  } catch (e) {
    toast('Request failed', 'err');
  }
  btn.disabled = false;
}

// ---- URL Sources ----------------------------------------------------------

async function refreshSources() {
  const list = document.getElementById('sourceList');
  list.innerHTML = '<div class="empty">Loading…</div>';
  try {
    const r = await fetch('/api/sources');
    const d = await r.json();
    if (d.error) {
      list.innerHTML = '<div class="empty" style="color:var(--live)">' + escHtml(d.error) + '</div>';
      return;
    }
    if (!d.length) {
      list.innerHTML = '<div class="empty">No URL sources found in OBS.</div>';
      return;
    }
    list.innerHTML = d.map(s => sourceCard(s)).join('');
  } catch {
    list.innerHTML = '<div class="empty" style="color:var(--live)">Could not load sources.</div>';
  }
}

function sourceCard(s) {
  const id = 'src_' + btoa(s.name).replace(/[^a-zA-Z0-9]/g, '_');
  return `
  <div class="source-item">
    <div class="source-name">
      ${escHtml(s.name)}
      <span class="source-kind">${escHtml(s.kind)}</span>
    </div>
    <div class="url-row">
      <input id="${id}" value="${escHtml(s.url)}" spellcheck="false">
      <button onclick="applyUrl('${escHtml(s.name)}', '${id}', this)">Apply</button>
    </div>
    <div class="source-msg" id="${id}_msg"></div>
  </div>`;
}

async function applyUrl(sourceName, inputId, btn) {
  const url  = document.getElementById(inputId).value.trim();
  const msg  = document.getElementById(inputId + '_msg');
  if (!url) { showMsg(msg, 'URL cannot be empty', true); return; }
  btn.disabled = true;
  showMsg(msg, 'Updating…', false);
  try {
    const r = await fetch('/api/source/url', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source: sourceName, url}),
    });
    const d = await r.json();
    if (d.error) {
      showMsg(msg, 'Error: ' + d.error, true);
    } else {
      showMsg(msg, 'Updated', false);
      toast('URL updated for "' + sourceName + '"', 'ok');
    }
  } catch {
    showMsg(msg, 'Request failed', true);
  }
  btn.disabled = false;
}

// ---- Stream Preview -------------------------------------------------------

let previewTimer = null;

function togglePreview() {
  const card = document.getElementById('previewCard');
  card.classList.toggle('collapsed');
  if (card.classList.contains('collapsed')) {
    clearInterval(previewTimer);
    previewTimer = null;
  } else {
    fetchPreview();
    previewTimer = setInterval(fetchPreview, 3000);
  }
}

async function fetchPreview() {
  try {
    const r = await fetch('/api/preview');
    const d = await r.json();
    const img     = document.getElementById('previewImg');
    const overlay = document.getElementById('previewOverlay');
    const meta    = document.getElementById('previewMeta');
    if (d.error) {
      img.style.display = 'none';
      overlay.textContent = d.error;
    } else {
      img.src = d.image;
      img.style.display = 'block';
      overlay.textContent = '';
      const now = new Date().toLocaleTimeString();
      meta.textContent = `Scene: ${d.scene}  ·  ${now}`;
    }
  } catch {
    document.getElementById('previewOverlay').textContent = 'Request failed';
  }
}

// ---- URL Sources toggle ---------------------------------------------------

function toggleSources() {
  document.getElementById('sourcesCard').classList.toggle('collapsed');
}

// ---- Tournament Setup -----------------------------------------------------

async function applyTournament(btn) {
  const url     = document.getElementById('tournamentUrl').value.trim();
  const meta    = document.getElementById('tMeta');
  const results = document.getElementById('tResults');

  if (!url) { toast('Paste a tournament URL first', 'err'); return; }

  meta.innerHTML    = '';
  results.innerHTML = '<div class="empty">Updating 13 sources…</div>';
  btn.disabled      = true;

  try {
    const r = await fetch('/api/tournament', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url}),
    });
    const d = await r.json();
    if (d.error) {
      results.innerHTML = `<div class="empty" style="color:var(--live)">${escHtml(d.error)}</div>`;
      toast('Parse error', 'err');
    } else {
      meta.innerHTML = `DB: <b>${escHtml(d.db_id)}</b> &nbsp;·&nbsp; Tournament: <b>${escHtml(d.tournament_id)}</b>`;
      const htmlRow = `<div class="t-row ${d.html_updated ? 'ok' : 'err'}">
           <span class="t-icon">${d.html_updated ? '✓' : '✗'}</span>
           <span class="t-source">OBS2K.html</span>
           <span class="t-url">${d.html_updated ? 'eventId updated on disk' : escHtml(d.html_error || 'failed')}</span>
         </div>`;
      results.innerHTML = d.results.map(res =>
        `<div class="t-row ${res.ok ? 'ok' : 'err'}">
           <span class="t-icon">${res.ok ? '✓' : '✗'}</span>
           <span class="t-source">${escHtml(res.source)}</span>
           <span class="t-url">${escHtml(res.url)}</span>
         </div>`
      ).join('') + htmlRow;
      const fails = d.results.filter(res => !res.ok).length + (d.html_updated ? 0 : 1);
      toast(fails ? `${fails} item(s) failed` : 'All 13 sources + OBS2K.html updated', fails ? 'err' : 'ok');
      refreshSources();  // reflect new URLs in the sources card below
    }
  } catch {
    results.innerHTML = '<div class="empty" style="color:var(--live)">Request failed</div>';
    toast('Request failed', 'err');
  }
  btn.disabled = false;
}

function showMsg(el, text, isErr) {
  el.textContent = text;
  el.className = 'source-msg' + (isErr ? ' err' : '');
}

// ---- Helpers --------------------------------------------------------------

function refreshAll() {
  refreshStatus();
  refreshSources();
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

let toastTimer = null;
function toast(msg, kind) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'show ' + (kind || '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.className = '', 2800);
}

// ---- Init -----------------------------------------------------------------

loadConfig();
refreshAll();
setInterval(refreshStatus, 5000);  // keep stream status in sync
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(_HTML)


@app.route("/logo")
def logo():
    svg_path = pathlib.Path(__file__).parent / "GUTZDC_Logo_border.svg"
    return Response(svg_path.read_bytes(), mimetype="image/svg+xml")


@app.route("/api/preview")
def api_preview():
    try:
        ws = _connect()
    except obswebsocket.exceptions.ConnectionFailure:
        return jsonify({"error": "Cannot connect to OBS"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    try:
        scene_name = ws.call(obsrequests.GetCurrentProgramScene()).getCurrentProgramSceneName()
        image_data = ws.call(obsrequests.GetSourceScreenshot(
            sourceName=scene_name,
            imageFormat="jpeg",
            imageWidth=640,
            imageHeight=360,
            imageCompressionQuality=70,
        )).getImageData()
        return jsonify({"image": image_data, "scene": scene_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        with contextlib.suppress(Exception):
            ws.disconnect()


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        obs_config["host"]      = str(data.get("host",      obs_config["host"])).strip() or "localhost"
        obs_config["port"]      = int(data.get("port",      obs_config["port"]))
        obs_config["password"]  = str(data.get("password",  obs_config["password"]))
        obs_config["obs2k_url"] = str(data.get("obs2k_url", obs_config["obs2k_url"])).strip()
        return jsonify({"ok": True})
    # Never send the password back to the browser
    return jsonify({"host": obs_config["host"], "port": obs_config["port"], "obs2k_url": obs_config["obs2k_url"]})


@app.route("/api/stream/status")
def api_stream_status():
    try:
        resp = _call(obsrequests.GetStreamStatus())
        return jsonify({"active": resp.getOutputActive()})
    except obswebsocket.exceptions.ConnectionFailure:
        return jsonify({"error": "Cannot connect to OBS"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stream/start", methods=["POST"])
def api_stream_start():
    try:
        _call(obsrequests.StartStream())
        return jsonify({"ok": True})
    except obswebsocket.exceptions.ConnectionFailure:
        return jsonify({"error": "Cannot connect to OBS"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stream/stop", methods=["POST"])
def api_stream_stop():
    try:
        _call(obsrequests.StopStream())
        return jsonify({"ok": True})
    except obswebsocket.exceptions.ConnectionFailure:
        return jsonify({"error": "Cannot connect to OBS"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sources")
def api_sources():
    """Return all OBS inputs that have a 'url' field in their settings."""
    try:
        ws = _connect()
    except obswebsocket.exceptions.ConnectionFailure:
        return jsonify({"error": "Cannot connect to OBS"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    try:
        inputs = ws.call(obsrequests.GetInputList()).getInputs()
        url_sources = []
        for inp in inputs:
            try:
                settings = ws.call(
                    obsrequests.GetInputSettings(inputName=inp["inputName"])
                ).getInputSettings()
                if "url" in settings:
                    url_sources.append({
                        "name": inp["inputName"],
                        "url": settings["url"],
                        "kind": inp.get("inputKind", ""),
                    })
            except Exception:
                pass
        return jsonify(url_sources)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        with contextlib.suppress(Exception):
            ws.disconnect()


@app.route("/api/source/url", methods=["POST"])
def api_source_url():
    data = request.get_json(force=True) or {}
    source_name = (data.get("source") or "").strip()
    new_url = (data.get("url") or "").strip()

    if not source_name:
        return jsonify({"error": "source is required"}), 400
    if not new_url:
        return jsonify({"error": "url is required"}), 400

    # Suppress the print() calls inside change_source_url
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        success = change_source_url(
            obs_config["host"],
            obs_config["port"],
            obs_config["password"],
            source_name,
            new_url,
        )

    if success:
        return jsonify({"ok": True})
    return jsonify({"error": "Failed to update source URL — check server log"}), 500


@app.route("/api/tournament", methods=["POST"])
def api_tournament():
    """Apply a tournament URL to all 7 OBS URL sources (General + Board 1-6)."""
    data = request.get_json(force=True) or {}
    tournament_url = (data.get("url") or "").strip()

    if not tournament_url:
        return jsonify({"error": "url is required"}), 400

    parsed = urlparse(tournament_url)
    m = re.search(r"/event/([^/]+)/([^/?#]+)", parsed.path)
    if not m:
        return jsonify({
            "error": "Could not parse dbID / tournamentID. "
                     "Expected path: /event/{dbID}/{tournamentID}"
        }), 400

    db_id         = m.group(1)
    tournament_id = m.group(2)
    base          = f"{parsed.scheme}://{parsed.netloc}"

    sources = [("URL Source General", f"{base}/dartsscorer-liveticker/api/v1/match/{db_id}/0/{tournament_id}")] + [
        (
            f"URL Source Board {i}",
            f"{base}/dartsscorer-liveticker/api/v1/match/{db_id}/0/{tournament_id}/board/{i}",
        )
        for i in range(1, 7)
    ]

    results = []
    for source_name, url in sources:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = change_source_url(
                obs_config["host"],
                obs_config["port"],
                obs_config["password"],
                source_name,
                url,
            )
        results.append({"source": source_name, "url": url, "ok": ok})

    # Update eventId in OBS2K.html BEFORE reloading browser sources
    html_file = pathlib.Path(__file__).parent / "OBS2K.html"
    html_update_ok = False
    html_update_error = None
    try:
        html = html_file.read_text(encoding="utf-8")
        html_new, n1 = re.subn(
            r'(const eventId\s*=\s*")[^"]*(")',
            rf'\g<1>{db_id}/{tournament_id}\2',
            html,
        )
        html_new, n2 = re.subn(
            r'(https?://)[^/]+((?:/tv/))',
            rf'\g<1>{parsed.netloc}\2',
            html_new,
        )
        if n1 == 0:
            html_update_error = "eventId not found in OBS2K.html"
        else:
            html_file.write_text(html_new, encoding="utf-8")
            html_update_ok = True
    except Exception as e:
        html_update_error = str(e)

    # Update "Browser Board N" URLs then reload all browser sources
    obs2k_base = obs_config["obs2k_url"].rstrip("?").split("?")[0]
    for i in range(1, 7):
        board_url = f"{obs2k_base}?board={i}"
        ok = _set_browser_source_url(f"Browser Board {i}", board_url)
        results.append({"source": f"Browser Board {i}", "url": board_url, "ok": ok})

    _reload_all_browser_sources()

    return jsonify({
        "results": results,
        "db_id": db_id,
        "tournament_id": tournament_id,
        "html_updated": html_update_ok,
        "html_error": html_update_error,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="OBS Stream Control Panel")
    parser.add_argument("--obs-host",   default="localhost", help="OBS WebSocket host (default: localhost)")
    parser.add_argument("--obs-port",   type=int, default=4455, help="OBS WebSocket port (default: 4455)")
    parser.add_argument("--obs-password", default="gutzdcev", help="OBS WebSocket password")
    parser.add_argument("--port",       type=int, default=5000, help="Web server port (default: 5000)")
    args = parser.parse_args()

    obs_config["host"] = args.obs_host
    obs_config["port"] = args.obs_port
    obs_config["password"] = args.obs_password

    print(f"OBS Stream Control Panel → http://localhost:{args.port}")
    print(f"OBS WebSocket target     → {args.obs_host}:{args.obs_port}")
    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
