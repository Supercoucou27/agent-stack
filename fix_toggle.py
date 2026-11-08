#!/usr/bin/env python3
"""Fix the toggle functions in index.html"""
with open('/home/tara/agent-stack/index.html', 'r') as f:
    content = f.read()

old_yolo = """async function toggleYolo() {
  const r = await fetch('/api/hermes/yolo', {method: (await fetch('/api/hermes/yolo').then(x=>x.json())).yolo ? 'POST' : 'GET'});
  const d = await r.json();
  $('yolo-switch').className = 'switch ' + (d.yolo ? 'on' : '');
  $('yolo-label').textContent = d.yolo ? 'ON' : 'OFF';
}
"""

new_yolo = """async function toggleYolo() {
  const r = await fetch('/api/hermes/yolo');
  const d = await r.json();
  const postRes = await fetch('/api/hermes/yolo', {method: 'POST'});
  const postD = await postRes.json();
  $('yolo-switch').className = 'switch ' + (postD.yolo ? 'on' : '');
  $('yolo-label').textContent = postD.yolo ? 'ON' : 'OFF';
}
"""

if old_yolo in content:
    content = content.replace(old_yolo, new_yolo)
    print('Fixed toggleYolo')
else:
    print('toggleYolo pattern not found')

with open('/home/tara/agent-stack/index.html', 'w') as f:
    f.write(content)
