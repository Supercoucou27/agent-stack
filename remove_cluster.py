#!/usr/bin/env python3
"""Remove Bonsai Cluster Control section from index.html"""
import re

with open('/home/tara/agent-stack/index.html', 'r') as f:
    content = f.read()

# Remove badge-cluster line
content = content.replace(
    '    <span id="badge-cluster" class="badge">Cluster: Idle</span>\n',
    ''
)

# Remove cluster HTML section
cluster_section = '''    <!-- 5. Bonsai Cluster Control -->
    <section class="panel">
      <h2>🧩 Bonsai Cluster Control (6 workers)</h2>
      <div style="display:flex; gap:6px; margin-bottom:8px;">
        <input id="cluster-objective" placeholder="Objectif du cluster…" style="flex:1;" />
        <button class="primary" onclick="runCluster()">▶ Lancer</button>
        <button class="info" onclick="stopCluster()">■ Stop</button>
      </div>
      <div id="cluster-status" style="font-size:11px; color:var(--muted);">Status: idle</div>
      <div id="cluster-workers"></div>
      <div class="report-box" id="cluster-report"></div>
    </section>
'''
content = content.replace(cluster_section, '')

# Remove cluster JavaScript functions (starting from // ─── CLUSTER (5001) ───)
# Find the cluster section and remove it
cluster_js_start = content.find('// ─── CLUSTER (5001) ─')
if cluster_js_start != -1:
    # Find the next section after the cluster functions
    cluster_js_end = content.find('// ─── OLLAMA (5001) ─', cluster_js_start)
    if cluster_js_end != -1:
        content = content[:cluster_js_start] + content[cluster_js_end:]
    else:
        # Remove everything after the cluster functions until the next // ─── section
        next_section = content.find('\n// ───', cluster_js_start + 10)
        if next_section != -1:
            content = content[:cluster_js_start] + content[next_section:]
        else:
            content = content[:cluster_js_start]

with open('/home/tara/agent-stack/index.html', 'w') as f:
    f.write(content)

print('Cluster section removed successfully')
