#!/usr/bin/env python3
"""Remove Ollama section from index.html"""
with open('/home/tara/agent-stack/index.html', 'r') as f:
    content = f.read()

# Remove badge-ollama line
content = content.replace(
    '    <span id="badge-ollama" class="badge">Ollama: Offline</span>\n',
    ''
)

# Remove ollama HTML section
ollama_section = '''    <!-- 6. Ollama Control -->
    <section class="panel">
      <h2>🐍 Ollama Control</h2>
      <div class="stat-row" style="margin-bottom:6px;"><span>Status</span><span id="ollama-status-badge" class="badge">checking…</span></div>
      <div class="ctrl-row">
        <button class="primary" onclick="ollamaStart()">▶ Start</button>
        <button class="danger" onclick="ollamaStop()">■ Stop</button>
        <button onclick="ollamaRestart()">🔄 Restart</button>
        <button class="info" onclick="ollamaCheckSudo()">⚙ Sudo</button>
      </div>
    </section>
'''
if ollama_section in content:
    content = content.replace(ollama_section, '')
    print('HTML section removed')
else:
    print('HTML section not found - checking partial...')
    # Try alternative structure
    alt = '    <!-- 6. Ollama Control -->\n    <section class="panel">\n      <h2>🐍 Ollama Control</h2>'
    if alt in content:
        # Find and remove from this point
        start = content.find(alt)
        end = content.find('</section>', start)
        if end != -1:
            content = content[:start] + content[end + len('</section>'):]
            print('HTML section removed (alt path)')

# Remove ollama JavaScript functions (from // ─── OLLAMA (5001) to // ─── HERMES CONTROL)
ollama_js_start = content.find('// ─── OLLAMA (5001) ─')
if ollama_js_start != -1:
    ollama_js_end = content.find('// ─── HERMES CONTROL (5001) ─', ollama_js_start)
    if ollama_js_end != -1:
        content = content[:ollama_js_start] + '\n' + content[ollama_js_end:]
        print('JS section removed')
    else:
        # Find end of ollama JS functions
        next_section = content.find('\n// ───', ollama_js_start + 10)
        if next_section != -1:
            content = content[:ollama_js_start] + content[next_section:]
            print('JS section removed (partial)')
        else:
            print('JS section not found')

with open('/home/tara/agent-stack/index.html', 'w') as f:
    f.write(content)

print('Done')
