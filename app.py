from flask import Flask, request, jsonify, render_template_string
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
import time

app = Flask(__name__)

# ── In-memory storage ─────────────────────────────────────────────────────────
notes = []
next_id = 1

# ── Prometheus metrics ────────────────────────────────────────────────────────
REQUEST_COUNT  = Counter('noteflow_requests_total',  'Total HTTP requests',   ['method', 'endpoint'])
NOTES_TOTAL    = Gauge  ('noteflow_notes_total',     'Current number of notes')
NOTE_OPS       = Counter('noteflow_note_operations', 'Note CRUD operations',  ['operation'])

# ── HTML template ─────────────────────────────────────────────────────────────
HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NoteFlow</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', sans-serif;
      background: #0f0f0f;
      color: #e0e0e0;
      min-height: 100vh;
      padding: 40px 20px;
    }
    .container { max-width: 640px; margin: 0 auto; }
    h1 {
      font-size: 2rem;
      font-weight: 700;
      color: #fff;
      letter-spacing: -0.5px;
      margin-bottom: 4px;
    }
    .subtitle { color: #666; font-size: 0.85rem; margin-bottom: 32px; }
    .add-form {
      display: flex;
      gap: 10px;
      margin-bottom: 28px;
    }
    .add-form input {
      flex: 1;
      padding: 12px 16px;
      border-radius: 10px;
      border: 1.5px solid #2a2a2a;
      background: #1a1a1a;
      color: #fff;
      font-size: 0.95rem;
      outline: none;
      transition: border-color 0.2s;
    }
    .add-form input:focus { border-color: #4f8ef7; }
    .add-form button {
      padding: 12px 20px;
      background: #4f8ef7;
      color: #fff;
      border: none;
      border-radius: 10px;
      font-size: 0.95rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
    }
    .add-form button:hover { background: #3a7be0; }
    .notes-list { display: flex; flex-direction: column; gap: 10px; }
    .note-card {
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 12px;
      padding: 16px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      animation: fadeIn 0.25s ease;
    }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; } }
    .note-text { font-size: 0.95rem; color: #ddd; }
    .note-id { font-size: 0.75rem; color: #555; margin-top: 3px; }
    .delete-btn {
      background: none;
      border: 1px solid #3a3a3a;
      color: #888;
      padding: 6px 12px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 0.8rem;
      transition: all 0.2s;
      flex-shrink: 0;
    }
    .delete-btn:hover { border-color: #e05555; color: #e05555; }
    .empty { text-align: center; color: #444; padding: 40px 0; font-size: 0.9rem; }
    .stats {
      margin-top: 32px;
      padding: 14px 18px;
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 12px;
      font-size: 0.8rem;
      color: #555;
    }
    .stats span { color: #4f8ef7; font-weight: 600; }
  </style>
</head>
<body>
  <div class="container">
    <h1>NoteFlow</h1>
    <p class="subtitle">DevOps project — INT377</p>

    <div class="add-form">
      <input type="text" id="noteInput" placeholder="Write a note..." onkeydown="if(event.key==='Enter') addNote()">
      <button onclick="addNote()">Add</button>
    </div>

    <div class="notes-list" id="notesList">
      <div class="empty">No notes yet. Add one above.</div>
    </div>

    <div class="stats">
      Total notes: <span id="count">0</span> &nbsp;|&nbsp;
      <a href="/metrics" target="_blank" style="color:#4f8ef7;">View /metrics</a>
    </div>
  </div>

  <script>
    async function loadNotes() {
      const res = await fetch('/notes');
      const data = await res.json();
      const list = document.getElementById('notesList');
      const count = document.getElementById('count');
      count.textContent = data.notes.length;
      if (data.notes.length === 0) {
        list.innerHTML = '<div class="empty">No notes yet. Add one above.</div>';
        return;
      }
      list.innerHTML = data.notes.map(n => `
        <div class="note-card" id="note-${n.id}">
          <div>
            <div class="note-text">${n.content}</div>
            <div class="note-id">#${n.id} &mdash; ${n.created_at}</div>
          </div>
          <button class="delete-btn" onclick="deleteNote(${n.id})">Delete</button>
        </div>
      `).join('');
    }

    async function addNote() {
      const input = document.getElementById('noteInput');
      const content = input.value.trim();
      if (!content) return;
      await fetch('/notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content })
      });
      input.value = '';
      loadNotes();
    }

    async function deleteNote(id) {
      await fetch('/notes/' + id, { method: 'DELETE' });
      loadNotes();
    }

    loadNotes();
  </script>
</body>
</html>
"""

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    REQUEST_COUNT.labels(method='GET', endpoint='/').inc()
    return render_template_string(HTML)


@app.route('/notes', methods=['GET'])
def get_notes():
    REQUEST_COUNT.labels(method='GET', endpoint='/notes').inc()
    return jsonify({'notes': notes, 'count': len(notes)})


@app.route('/notes', methods=['POST'])
def add_note():
    global next_id
    REQUEST_COUNT.labels(method='POST', endpoint='/notes').inc()

    data = request.get_json()
    if not data or not data.get('content', '').strip():
        return jsonify({'error': 'Content is required'}), 400

    note = {
        'id': next_id,
        'content': data['content'].strip(),
        'created_at': time.strftime('%Y-%m-%d %H:%M')
    }
    notes.append(note)
    next_id += 1

    NOTES_TOTAL.set(len(notes))
    NOTE_OPS.labels(operation='add').inc()

    return jsonify({'message': 'Note added', 'note': note}), 201


@app.route('/notes/<int:note_id>', methods=['DELETE'])
def delete_note(note_id):
    global notes
    REQUEST_COUNT.labels(method='DELETE', endpoint='/notes/id').inc()

    original_count = len(notes)
    notes = [n for n in notes if n['id'] != note_id]

    if len(notes) == original_count:
        return jsonify({'error': 'Note not found'}), 404

    NOTES_TOTAL.set(len(notes))
    NOTE_OPS.labels(operation='delete').inc()

    return jsonify({'message': 'Note deleted'}), 200


@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'notes_count': len(notes)}), 200


@app.route('/metrics')
def metrics():
    REQUEST_COUNT.labels(method='GET', endpoint='/metrics').inc()
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
