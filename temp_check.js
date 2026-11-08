
const $ = id => document.getElementById(id);

// ─── STATS ───────────────────────────────────────────────────────────────────
async function updateStats() {
  try {
    const res = await fetch('/api/system/metrics');
    const d = await res.json();
    $('txt-cpu').textContent = Math.round(d.cpu_percent) + '%';
    $('txt-ram').textContent = d.ram.used_gb + ' / ' + d.ram.total_gb + ' GB';
    $('txt-disk').textContent = Math.round(d.storage.percent || 0) + '%';
    if (d.gpus && d.gpus.length) {
      d.gpus.forEach(g => {
        const div = document.getElementById('txt-vram-gpu' + g.index);
        const bar = document.getElementById('bar-vram-gpu' + g.index);
        if (div && bar) {
          div.textContent = g.vram_used_mb + ' / ' + g.vram_total_mb + ' MB (' + g.util + '%)';
          bar.value = g.util;
        }
      });
    }
  } catch(e) {}
}

// ─── LLAMA ────────────────────────────────────────────────────────────────────
async function ping() {
  try {
    const res = await fetch('/api/health?llama_url=http://127.0.0.1:8080');
    const d = await res.json();
    $('badge-llama').textContent = d.llama ? 'LLM: Ready (8080)' : 'LLM: Offline';
    $('badge-llama').className = 'badge ' + (d.llama ? 'ok' : 'err');
    const m = $('badge-llama-model');
    m.textContent = d.llama ? 'model: ' + (d.model || '?') : 'model: ?';
    $('btn-start').disabled = d.llama;
    $('btn-stop').disabled = !d.llama;
  } catch(e) {
    $('badge-llama').textContent = 'LLM: Offline';
    $('badge-llama').className = 'badge err';
    $('badge-llama-model').textContent = 'model: ?';
  }
}

async function loadModels() {
  try {
    const res = await fetch('/api/models');
    const d = await res.json();
    const sel = $('model-select');
    sel.innerHTML = '';
    d.models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.name;
      opt.textContent = m.name + ' (' + m.size_gb + 'G)';
      if (m.active) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch(e) {}
}

async function controlLlama(action) {
  if (action === 'start') $('btn-start').disabled = true;
  if (action === 'stop') $('btn-stop').disabled = true;
  try {
    const res = await fetch(`/api/llama/${action}`, {method: 'POST'});
    const d = await res.json();
    if (d.ok) {
      setTimeout(() => { ping(); }, 1200);
    } else {
      alert(d.error || 'Échec du ' + action + ' : ' + d.state);
    }
  } catch(e) {
    alert('Connexion impossible: ' + e);
  } finally {
    setTimeout(() => { $('btn-start').disabled = false; $('btn-stop').disabled = false; }, 1500);
  }
}

async function switchModel() {
  const model = $('model-select').value;
  await fetch('/api/switch_model', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({model_name: model})
  });
  setTimeout(() => { loadModels(); ping(); }, 3500);
}

// ─── LLM MODEL (solar vs llama) ─────────────────────────────────────────────────
async function saveLlmModel() {
  const model = $('llm-model').value;
  try {
    const res = await fetch('/api/llm/model', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model: model})
    });
    const d = await res.json();
    if (d.ok) {
      const label = $('llm-model-label');
      if (model === 'solar') {
        $('chat-input').placeholder = 'Message pour Solar Mini 4 (free)...';
        label.textContent = 'LLM: Solar Mini 4 (remote)';
        label.className = 'badge ok';
        label.style.color = '#f85149';
      } else {
        $('chat-input').placeholder = 'Message pour le LLM local…';
        label.textContent = 'LLM: Local llama (Qwen3.8-27B)';
        label.className = 'badge info';
        label.style.color = '#7ee787';
      }
      const badge = $('badge-llama');
      badge.textContent = label.textContent;
      setTimeout(() => { loadModels(); }, 2000);
    }
  } catch(e) {
    alert('Erreur: ' + e);
  }
}

// ─── CHAT ────────────────────────────────────────────────────────────────────
async function sendChat() {
  const input = $('chat-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  const out = $('chat-log');
  out.textContent = '⏳ Attente de l\'agent LLM…\n\n';
  try {
    const res = await fetch('/api/llama/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        prompt: msg,
        temperature: parseFloat($('cfg-temp').value) || 0.7,
        model: $('llm-model').value
      })
    });
    const data = await res.json();
    if (data.error) {
      out.textContent += `❌ ${data.error}\n`;
      return;
    }
    const content = (data.choices && data.choices[0] && data.choices[0].message)
      ? data.choices[0].message.content : '';
    out.textContent += '🤖 LLM local :\n' + (content || '(réponse vide)') + '\n';
  } catch(e) {
    out.textContent += `❌ Erreur de connexion: ${e}\n`;
  }
}

// ─── TERMINAL (Claude 8082) ───────────────────────────────────────────────────
let termScrollDone = true;
function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function parseANSI(text) {
  const re = /\x1b\[([0-9;]*)([a-zA-Z])|([^\x1b\x00]+)/g;
  const colors = { '31': 'red', '32': 'green', '33': 'yellow', '34': 'blue', '35': 'magenta', '36': 'cyan', '37': 'white' };
  let html = ''; let last = 0; let open = ''; let m;
  while ((m = re.exec(text)) !== null) {
    if (m[3] !== undefined) {
      if (open) { html += open + escapeHtml(m[3]); open = ''; }
      else { html += escapeHtml(m[3]); }
    } else if (m[1] !== undefined) {
      const code = m[1]; const ch = m[2];
      if (ch === 'm') {
        if (code === '0') open = '';
        else if (code === '1') open += '<strong>';
        else {
          const c = colors[code]; if (c) open += `<span style="color:${c}">`;
        }
      }
    }
    last = re.lastIndex;
  }
  const tail = text.slice(last);
  if (tail) html += open + escapeHtml(tail);
  return html;
}
function termLine(html) {
  const out = $('term-output');
  const div = document.createElement('div');
  div.innerHTML = html;
  out.appendChild(div);
  out.scrollTop = out.scrollHeight;
}
async function sendTerm() {
  const cmd = $('term-input').value.trim();
  if (!cmd) return;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(cmd);
    $('term-input').value = '';
    return;
  }
  const status = $('term-status');
  status.textContent = 'Agent: en cours…';
  termLine(`<span style="color:#7ee787;">user@tara:~$</span> <span style="color:#89dceb;">${escapeHtml(cmd)}</span>`);
  $('term-input').value = '';
  try {
    const res = await fetch('/api/hermes/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: cmd })
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value);
      for (const line of chunk.split('\n')) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === 'stdout') {
              termLine(parseANSI(data.text || ''));
            } else if (data.type === 'exit') {
              termLine(`<span style="color:#f85149;">[code ${data.code}]</span><br>`);
              status.textContent = 'Agent: Prêt';
            }
          } catch (e) { /* ignore malformed chunk */ }
        }
      }
    }
  } catch (e) {
    termLine(`<span style="color:#f85149;">❌ ${escapeHtml(e)}</span>`);
    status.textContent = 'Agent: Erreur';
  }

  // ─── TERMINAL WEBSOCKET (real-time) ────────────────────────────────────────────
  let ws = null;
  async function connectTerminal() {
    try {
      ws = new WebSocket('ws://' + location.host + '/ws/hermes');
      ws.onopen = () => {
        $('term-status').textContent = 'Agent: Connecté';
        $('term-status').style.color = '#7ee787';
      };
      ws.onmessage = (e) => {
        const text = e.data;
        for (const line of text.split('\n')) {
          if (line) termLine(parseANSI(line));
        }
        $('term-output').scrollTop = $('term-output').scrollHeight;
      };
      ws.onclose = () => {
        $('term-status').textContent = 'Agent: hors-ligne';
        $('term-status').style.color = '#f85149';
        setTimeout(connectTerminal, 3000);
      };
      ws.onerror = () => {
        $('term-status').textContent = 'Agent: Erreur';
        $('term-status').style.color = '#f85149';
      };
    } catch (e) {
      $('term-status').textContent = 'Agent: Erreur';
      $('term-status').style.color = '#f85149';
      setTimeout(connectTerminal, 3000);
    }
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.id === 'term-input') {
      e.preventDefault();
      sendTerm();
    }
  });
  document.addEventListener('DOMContentLoaded', () => {
    connectTerminal();
  });

// ─── CLUSTER (5001) ──────────────────────────────────────────────────────────
async function runCluster() {
  const obj = $('cluster-objective').value.trim();
  if (!obj) { alert('Veuillez saisir un objectif.'); return; }
  if (confirm('Lancer le cluster sur cet objectif ?')) {
    try {
      const res = await fetch('/api/cluster/run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({objective: obj})
      });
      const d = await res.json();
      if (d.ok) {
        $('cluster-status').textContent = 'Status: running…';
        $('cluster-workers').innerHTML = '';
        for (let i = 1; i <= 6; i++) addWorkerCard(i);
        $('cluster-report').textContent = 'Lancement…';
        refreshCluster();
      } else {
        alert(d.error || 'Échec');
      }
    } catch(e) { alert('Erreur: ' + e); }
  }
}
async function stopCluster() {
  try {
    const res = await fetch('/api/cluster/run', {method: 'POST', body: JSON.stringify({objective: 'STOP'})}); // will be ignored
    refreshCluster();
  } catch(e) {}
}
function addWorkerCard(i) {
  const card = document.createElement('div');
  card.className = 'worker-card idle';
  card.innerHTML = `<div class="w-name">Worker #${i}</div><div class="w-status">IDLE</div>`;
  $('cluster-workers').appendChild(card);
}
async function refreshCluster() {
  try {
    const res = await fetch('/api/cluster/status');
    const d = await res.json();
    const cards = $('cluster-workers').children;
    for (let i = 0; i < cards.length; i++) {
      const wid = (i + 1).toString();
      const w = d.workers[wid];
      if (w) {
        const c = cards[i];
        c.className = 'worker-card ' + (w.status || 'idle');
        c.querySelector('.w-name').textContent = 'Worker #' + wid + ' — ' + w.task;
        c.querySelector('.w-status').textContent = (w.status || 'pending') + (w.done ? ' ✓' : '');
      }
    }
    $('cluster-report').textContent = d.report || d.error || 'Aucun rapport';
    const badge = $('badge-cluster');
    badge.textContent = 'Cluster: ' + (d.status || 'idle');
    badge.className = 'badge ' + (d.status === 'completed' ? 'ok' : d.status === 'error' ? 'err' : 'info');
    if (d.status !== 'idle' && d.status !== 'running') {
      setTimeout(refreshCluster, 2000);
    }
  } catch(e) {}
}

// ─── OLLAMA (5001) ───────────────────────────────────────────────────────────
async function ollamaStatus() {
  try {
    const res = await fetch('/api/ollama/status');
    const d = await res.json();
    const badge = $('ollama-status-badge');
    badge.textContent = d.active ? 'ON' : 'OFF';
    badge.className = 'badge ' + (d.active ? 'ok' : 'err');
    return d.active;
  } catch(e) { return false; }
}
async function ollamaStart() { const r = await fetch('/api/ollama/start', {method:'POST'}); showOllamaJson(r); }
async function ollamaStop() { const r = await fetch('/api/ollama/stop', {method:'POST'}); showOllamaJson(r); }
async function ollamaRestart() { const r = await fetch('/api/ollama/restart', {method:'POST'}); showOllamaJson(r); }
async function ollamaCheckSudo() {
  const r = await fetch('/api/ollama/check-sudo', {method:'GET'});
  const d = await r.json();
  alert(d.can_sudo ? 'Sudo OK' : 'Sudo FAIL: ' + (d.stderr || d.error));
}
async function showOllamaJson(r) {
  try {
    const d = await r.json();
    const ok = d.ok ? 'OK' : 'ERR';
    const txt = d.action + ': ' + ok + (d.status ? ' (' + JSON.stringify(d.status) + ')' : '') + (d.stderr ? ' ' + d.stderr : '');
    $('ollama-status-badge').textContent = txt;
  } catch(e) {}
}
setInterval(async () => { const a = await ollamaStatus(); $('badge-ollama').textContent = a ? 'Ollama: ON' : 'Ollama: OFF'; $('badge-ollama').className = 'badge ' + (a ? 'ok' : 'err'); }, 4000);

// ─── HERMES CONTROL (5001) ───────────────────────────────────────────────────
async function hermesStatus() {
  try {
    const res = await fetch('/api/hermes/status');
    const d = await res.json();
    $('hermes-status').textContent = 'Status: ' + (d.base_url || 'unknown');
    return d;
  } catch(e) { return {}; }
}
async function hermesSetup() {
  const r = await fetch('/api/hermes/setup', {method:'POST'});
  alert((await r.json()).ok ? 'Setup OK' : 'Setup FAIL');
}
async function hermesRestart() {
  const r = await fetch('/api/hermes/restart', {method:'POST'});
  alert((await r.json()).ok ? 'Restart OK' : 'Restart FAIL');
}
async function toggleYolo() {
  const r = await fetch('/api/hermes/yolo', {method: (await fetch('/api/hermes/yolo').then(x=>x.json())).yolo ? 'POST' : 'GET'});
  const d = await r.json();
  $('yolo-switch').className = 'switch ' + (d.yolo ? 'on' : '');
  $('yolo-label').textContent = d.yolo ? 'ON' : 'OFF';
}
async function toggleGuardrail() {
  const r = await fetch('/api/hermes/guardrail', {method: (await fetch('/api/hermes/guardrail').then(x=>x.json())).unguarded ? 'POST' : 'GET'});
  const d = await r.json();
  $('guardrail-switch').className = 'switch ' + (d.unguarded ? 'on' : '');
  $('guardrail-label').textContent = d.unguarded ? 'Unguarded' : 'Guarded';
}
setInterval(hermesStatus, 4000);

// ─── LLM CONFIG ───────────────────────────────────────────────────────────────
async function applyLlmConfig() {
  const cfg = {
    ctx_size: parseInt($('llm-ctx').value) || 32768,
    n_gpu_layers: parseInt($('llm-ngpu').value) || 99,
    tensor_split: $('llm-ts').value || '1,1',
    threads: parseInt($('llm-threads').value) || 6,
    batch_size: parseInt($('llm-batch').value) || 2048,
    flash_attn: $('llm-flash').value || 'auto',
    temperature: parseFloat($('llm-temp').value) || 0.7,
    max_tokens: parseInt($('llm-maxtok').value) || 2048,
  };
  try {
    const res = await fetch('/api/llama/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(cfg)
    });
    const d = await res.json();
    if (d.ok) {
      alert('LLM config applied and llama-server restarted');
      setTimeout(ping, 2000);
      setTimeout(loadModels, 3500);
    } else {
      alert('Failed: ' + (d.error || d.state));
    }
  } catch(e) { alert('Erreur: ' + e); }
}
async function resetLlmConfig() {
  try {
    const res = await fetch('/api/llm/config');
    const d = await res.json();
    $('llm-ctx').value = d.ctx_size;
    $('llm-ngpu').value = d.n_gpu_layers;
    $('llm-ts').value = d.tensor_split;
    $('llm-threads').value = d.threads;
    $('llm-batch').value = d.batch_size;
    $('llm-flash').value = d.flash_attn;
    $('llm-temp').value = d.temperature;
    $('llm-maxtok').value = d.max_tokens;
  } catch(e) {}
}

// ─── HERMES CONFIG ────────────────────────────────────────────────────────────
async function applyHermesConfig() {
  const cfg = {
    'model.default': $('hermes-model-default').value.trim(),
    'model.base_url': $('hermes-base-url').value.trim(),
    'model.provider': $('hermes-provider').value.trim(),
    'model.context_length': parseInt($('hermes-context').value) || 32768,
    'agent.max_turns': parseInt($('hermes-maxturns').value) || 50,
    'compression.threshold': parseFloat($('hermes-compress').value) || 0.5,
    'terminal.backend': $('hermes-termbackend').value.trim(),
  };
  try {
    const res = await fetch('/api/hermes/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(cfg)
    });
    const d = await res.json();
    if (d.ok) {
      alert('Hermes config applied');
      setTimeout(hermesStatus, 3000);
    } else {
      alert('Failed');
    }
  } catch(e) { alert('Erreur: ' + e); }
}
async function resetHermesConfig() {
  try {
    const res = await fetch('/api/hermes/config');
    const d = await res.json();
    $('hermes-model-default').value = d['model.default'] || '';
    $('hermes-base-url').value = d['model.base_url'] || '';
    $('hermes-provider').value = d['model.provider'] || '';
    $('hermes-context').value = d['model.context_length'] || 32768;
    $('hermes-maxturns').value = d['agent.max_turns'] || 50;
    $('hermes-compress').value = d['compression.threshold'] || 0.5;
    $('hermes-termbackend').value = d['terminal.backend'] || 'ws';
  } catch(e) {}
}

// ─── MCP POINTS ───────────────────────────────────────────────────────────────
async function addMcpServer() {
  const name = $('mcp-name').value.trim();
  if (!name) { alert('Name required'); return; }
  try {
    const res = await fetch('/api/mcp/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, spec: {}})
    });
    alert((await res.json()).ok ? 'Added' : 'Failed');
    listMcpServers();
  } catch(e) { alert('Erreur: ' + e); }
}
async function removeMcpServer() {
  const name = $('mcp-name').value.trim();
  if (!name) { alert('Enter a name first'); return; }
  if (!confirm('Remove ' + name + '?')) return;
  try {
    const res = await fetch('/api/mcp/remove', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name})
    });
    alert((await res.json()).ok ? 'Removed' : 'Failed');
    listMcpServers();
  } catch(e) { alert('Erreur: ' + e); }
}
async function toggleMcpServer() {
  const name = $('mcp-name').value.trim();
  if (!name) { alert('Enter a name first'); return; }
  try {
    const res = await fetch('/api/mcp');
    const d = await res.json();
    const servers = d.servers || {};
    const enabled = !servers[name]?.enabled;
    const r2 = await fetch('/api/mcp/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, enabled: enabled})
    });
    alert((await r2.json()).ok ? 'Toggled' : 'Failed');
    listMcpServers();
  } catch(e) { alert('Erreur: ' + e); }
}
async function reloadMcp() {
  try {
    const r = await fetch('/api/mcp/reload', {method: 'POST'});
    alert((await r.json()).ok ? 'MCP reloaded' : 'Failed');
  } catch(e) { alert('Erreur: ' + e); }
}
async function listMcpServers() {
  try {
    const res = await fetch('/api/mcp');
    const d = await res.json();
    const container = $('mcp-server-list');
    const servers = d.servers || {};
    if (Object.keys(servers).length === 0) {
      container.innerHTML = '<div class="stat-row" style="margin-bottom:6px;"><strong>No servers configured</strong></div>';
      return;
    }
    container.innerHTML = Object.keys(servers).map(name => {
      const s = servers[name];
      return `<div class="stat-row" style="margin-bottom:6px;">
        <span>${name}</span>
        <span>command: ${s.command || s.url || '—'}</span>
        <span style="color:var(--muted);">enabled: ${s.enabled || false}</span>
      </div>`;
    }).join('');
  } catch(e) {}
}

async function loadFiles() {
  try {
    const res = await fetch('/api/files');
    const d = await res.json();
    const c = $('file-list');
    if (!d.files.length) { c.innerHTML = '<div style="padding:4px; color:var(--muted)">Workspace vide.</div>'; return; }
    c.innerHTML = d.files.map(f => `<div class="file-item"><span>${f.rel}</span><span style="color:var(--muted)">${f.size_kb} KB</span></div>`).join('');
  } catch(e) {}
}

// ─── START ───────────────────────────────────────────────────────────────────
updateStats();
setInterval(updateStats, 1500);
ping();
loadModels();
loadFiles();
refreshCluster();
