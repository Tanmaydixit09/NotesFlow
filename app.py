from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session, send_file
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
import sqlite3, hashlib, os, csv, json, io
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'noteflow-secret-2025')
app.config['DATABASE'] = os.environ.get('DATABASE', 'noteflow.db')

# ── Prometheus Metrics ────────────────────────────────────────────────────────
REQUEST_COUNT = Counter('noteflow_requests_total',   'Total HTTP requests',    ['method', 'endpoint'])
NOTES_TOTAL   = Gauge  ('noteflow_notes_total',      'Total active notes')
USERS_TOTAL   = Gauge  ('noteflow_users_total',      'Total registered users')
NOTE_OPS      = Counter('noteflow_note_operations',  'Note CRUD operations',   ['operation'])
LOGIN_COUNT   = Counter('noteflow_login_total',      'Login attempts',         ['status'])

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT    UNIQUE NOT NULL,
            email    TEXT    UNIQUE NOT NULL,
            password TEXT    NOT NULL,
            created  TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            title      TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            category   TEXT    DEFAULT 'General',
            priority   TEXT    DEFAULT 'Medium',
            status     TEXT    DEFAULT 'active',
            pinned     INTEGER DEFAULT 0,
            created_at TEXT    DEFAULT (datetime('now')),
            updated_at TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    ''')
    conn.commit()
    conn.close()

# ── Auth helpers ──────────────────────────────────────────────────────────────
def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

def update_metrics():
    conn   = get_db()
    active = conn.execute("SELECT COUNT(*) FROM notes WHERE status='active'").fetchone()[0]
    users  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    NOTES_TOTAL.set(active)
    USERS_TOTAL.set(users)

# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES
# ══════════════════════════════════════════════════════════════════════════════
BASE_STYLE = """
<style>
:root {
  --bg:#0d0d0d;--bg2:#161616;--bg3:#1e1e1e;--border:#2a2a2a;
  --text:#e0e0e0;--muted:#666;--blue:#4f8ef7;--green:#3ecf8e;
  --red:#e05555;--orange:#f97316;--purple:#a78bfa;
}
body.light {
  --bg:#f5f5f5;--bg2:#ffffff;--bg3:#ebebeb;--border:#ddd;
  --text:#1a1a1a;--muted:#888;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;transition:background .3s,color .3s;}
a{color:var(--blue);text-decoration:none;}
input,textarea,select{background:var(--bg3);border:1.5px solid var(--border);color:var(--text);border-radius:8px;padding:10px 14px;font-size:.9rem;outline:none;font-family:inherit;transition:border-color .2s;width:100%;}
input:focus,textarea:focus,select:focus{border-color:var(--blue);}
button{cursor:pointer;font-family:inherit;border:none;border-radius:8px;font-size:.9rem;transition:all .2s;}
.btn-primary{background:var(--blue);color:#fff;padding:10px 20px;font-weight:600;}
.btn-primary:hover{opacity:.85;}
.btn-sm{background:var(--bg3);color:var(--text);padding:6px 12px;border:1px solid var(--border);font-size:.8rem;}
.btn-sm:hover{border-color:var(--blue);color:var(--blue);}
.btn-danger{background:transparent;color:var(--muted);padding:6px 12px;border:1px solid var(--border);font-size:.8rem;}
.btn-danger:hover{border-color:var(--red);color:var(--red);}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:20px;}
.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.72rem;font-weight:600;}
.badge-high{background:#3b1010;color:#f87171;}
.badge-medium{background:#2d2510;color:#fbbf24;}
.badge-low{background:#0f2d1e;color:#34d399;}
.badge-cat{background:var(--bg3);color:var(--muted);border:1px solid var(--border);}
.badge-archived{background:#1e1e2d;color:#a78bfa;}
body.light .badge-high{background:#fee2e2;color:#dc2626;}
body.light .badge-medium{background:#fef3c7;color:#d97706;}
body.light .badge-low{background:#d1fae5;color:#059669;}
body.light .badge-cat{background:#f3f4f6;color:#6b7280;}
.toast{position:fixed;bottom:24px;right:24px;background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:12px 20px;border-radius:10px;font-size:.88rem;z-index:9999;opacity:0;transform:translateY(10px);transition:all .3s;box-shadow:0 4px 20px rgba(0,0,0,.3);}
.toast.show{opacity:1;transform:translateY(0);}
.navbar{background:var(--bg2);border-bottom:1px solid var(--border);padding:0 24px;height:56px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;}
.nav-brand{font-size:1.1rem;font-weight:700;color:var(--blue);}
.nav-links{display:flex;align-items:center;gap:8px;}
.container{max-width:920px;margin:0 auto;padding:28px 20px;}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px);}to{opacity:1;transform:translateY(0);}}
.fade-in{animation:fadeIn .25s ease;}
</style>
"""

AUTH_TEMPLATE = BASE_STYLE + """
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px">
  <div class="card fade-in" style="width:100%;max-width:400px">
    <h2 style="font-size:1.6rem;font-weight:700;margin-bottom:4px;color:var(--blue)">⚡ NoteFlow</h2>
    <p style="color:var(--muted);font-size:.85rem;margin-bottom:24px">{{ subtitle }}</p>
    {% if error %}
    <div style="background:#3b1010;color:#f87171;padding:10px 14px;border-radius:8px;margin-bottom:16px;font-size:.88rem">{{ error }}</div>
    {% endif %}
    <form method="POST" style="display:flex;flex-direction:column;gap:12px">
      {% if show_username %}<input name="username" placeholder="Username" required>{% endif %}
      {% if show_email %}<input name="email" type="email" placeholder="Email" required>{% endif %}
      <input name="password" type="password" placeholder="Password" required>
      <button type="submit" class="btn-primary" style="margin-top:4px">{{ action }}</button>
    </form>
    <p style="text-align:center;margin-top:16px;font-size:.85rem;color:var(--muted)">
      {{ link_text }} <a href="{{ link_url }}">{{ link_label }}</a>
    </p>
  </div>
</div>
"""

MAIN_TEMPLATE = BASE_STYLE + """
<nav class="navbar">
  <span class="nav-brand">⚡ NoteFlow</span>
  <div class="nav-links">
    <span style="color:var(--muted);font-size:.82rem;display:none" id="userLabel">👤 {{ username }}</span>
    <button class="btn-sm" onclick="toggleTheme()" id="themeBtn">🌙</button>
    <a href="/stats"><button class="btn-sm">📊 Stats</button></a>
    <a href="/export/csv"><button class="btn-sm">⬇ CSV</button></a>
    <a href="/export/json"><button class="btn-sm">⬇ JSON</button></a>
    <a href="/logout"><button class="btn-sm">Logout</button></a>
  </div>
</nav>

<div class="container">

  <!-- Add Note -->
  <div class="card fade-in" style="margin-bottom:24px">
    <h3 style="margin-bottom:16px;font-size:.95rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">New Note</h3>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
      <input id="noteTitle" placeholder="Title *" onkeydown="if(event.key==='Enter')document.getElementById('noteContent').focus()">
      <select id="notePriority">
        <option value="High">🔴 High Priority</option>
        <option value="Medium" selected>🟡 Medium Priority</option>
        <option value="Low">🟢 Low Priority</option>
      </select>
    </div>
    <textarea id="noteContent" placeholder="Write your note here..." rows="3" style="resize:vertical;margin-bottom:12px"></textarea>
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <select id="noteCategory" style="flex:1;min-width:140px">
        <option>General</option><option>Study</option><option>Work</option>
        <option>Personal</option><option>Important</option><option>Ideas</option>
      </select>
      <span id="charCount" style="color:var(--muted);font-size:.8rem;white-space:nowrap">0 chars</span>
      <button class="btn-primary" onclick="addNote()">+ Add Note</button>
    </div>
  </div>

  <!-- Search & Filter -->
  <div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:10px;margin-bottom:20px">
    <input id="searchInput" placeholder="🔍 Search by title or content..." oninput="filterNotes()">
    <select id="filterPriority" onchange="filterNotes()">
      <option value="">All Priorities</option>
      <option value="High">🔴 High</option>
      <option value="Medium">🟡 Medium</option>
      <option value="Low">🟢 Low</option>
    </select>
    <select id="filterCategory" onchange="filterNotes()">
      <option value="">All Categories</option>
      <option>General</option><option>Study</option><option>Work</option>
      <option>Personal</option><option>Important</option><option>Ideas</option>
    </select>
    <select id="sortBy" onchange="filterNotes()">
      <option value="date_desc">Newest First</option>
      <option value="date_asc">Oldest First</option>
      <option value="priority">By Priority</option>
      <option value="pinned">Pinned First</option>
    </select>
  </div>

  <!-- Stats bar -->
  <div id="statsBar" style="display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap"></div>

  <!-- Notes -->
  <div id="notesList"></div>
  <div id="emptyState" style="display:none;text-align:center;padding:60px 0;color:var(--muted)">
    <div style="font-size:3rem;margin-bottom:12px">📝</div>
    <div style="font-size:.95rem">No notes found. Add one above!</div>
  </div>

</div>
<div class="toast" id="toast"></div>

<script>
let allNotes = [];

// Theme
function toggleTheme(){
  document.body.classList.toggle('light');
  const light = document.body.classList.contains('light');
  document.getElementById('themeBtn').textContent = light ? '☀️' : '🌙';
  localStorage.setItem('theme', light ? 'light' : 'dark');
}
if(localStorage.getItem('theme')==='light'){
  document.body.classList.add('light');
  document.getElementById('themeBtn').textContent='☀️';
}
document.getElementById('userLabel').style.display='inline';

// Toast
function showToast(msg, color='var(--green)'){
  const t=document.getElementById('toast');
  t.textContent=msg; t.style.borderLeftColor=color;
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2500);
}

// Char count
document.getElementById('noteContent').addEventListener('input',function(){
  document.getElementById('charCount').textContent=this.value.length+' chars';
});

// Load
async function loadNotes(){
  const res=await fetch('/notes');
  const data=await res.json();
  allNotes=data.notes;
  updateStatsBar(allNotes);
  filterNotes();
}

function updateStatsBar(notes){
  const active=notes.filter(n=>n.status==='active').length;
  const pinned=notes.filter(n=>n.pinned).length;
  const high=notes.filter(n=>n.priority==='High'&&n.status==='active').length;
  const archived=notes.filter(n=>n.status==='archived').length;
  document.getElementById('statsBar').innerHTML=[
    ['📝 Total', active,    'var(--blue)'],
    ['📌 Pinned', pinned,   'var(--orange)'],
    ['🔴 High',  high,      'var(--red)'],
    ['📦 Archived', archived,'var(--purple)'],
  ].map(([label,val,color])=>`
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 16px;font-size:.82rem;color:var(--muted)">
      ${label}: <strong style="color:${color}">${val}</strong>
    </div>`).join('');
}

function priorityOrder(p){return p==='High'?0:p==='Medium'?1:2;}

function filterNotes(){
  const search  =document.getElementById('searchInput').value.toLowerCase();
  const priority=document.getElementById('filterPriority').value;
  const category=document.getElementById('filterCategory').value;
  const sortBy  =document.getElementById('sortBy').value;

  let filtered=allNotes.filter(n=>{
    const ms=!search  ||n.title.toLowerCase().includes(search)||n.content.toLowerCase().includes(search);
    const mp=!priority||n.priority===priority;
    const mc=!category||n.category===category;
    return ms&&mp&&mc;
  });

  if(sortBy==='date_asc')  filtered.sort((a,b)=>a.id-b.id);
  else if(sortBy==='date_desc') filtered.sort((a,b)=>b.id-a.id);
  else if(sortBy==='priority')  filtered.sort((a,b)=>priorityOrder(a.priority)-priorityOrder(b.priority));
  else if(sortBy==='pinned')    filtered.sort((a,b)=>b.pinned-a.pinned);

  renderNotes(filtered);
}

function renderNotes(notes){
  const list =document.getElementById('notesList');
  const empty=document.getElementById('emptyState');
  if(!notes.length){list.innerHTML='';empty.style.display='block';return;}
  empty.style.display='none';
  list.innerHTML=notes.map(n=>`
    <div class="card fade-in" style="margin-bottom:12px;${n.pinned?'border-color:var(--blue);':''}" id="note-${n.id}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
        <div style="flex:1;padding-right:12px">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            ${n.pinned?'<span style="color:var(--blue);font-size:.8rem">📌</span>':''}
            <span style="font-weight:600;font-size:1rem">${escHtml(n.title)}</span>
          </div>
          <div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap">
            <span class="badge badge-${n.priority.toLowerCase()}">${n.priority}</span>
            <span class="badge badge-cat">${n.category}</span>
            ${n.status==='archived'?'<span class="badge badge-archived">Archived</span>':''}
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0">
          <button class="btn-danger" onclick="pinNote(${n.id})">${n.pinned?'Unpin':'📌'}</button>
          <button class="btn-danger" onclick="archiveNote(${n.id})">${n.status==='archived'?'Restore':'📦'}</button>
          <button class="btn-danger" onclick="deleteNote(${n.id})">🗑</button>
        </div>
      </div>
      <p style="font-size:.9rem;color:var(--muted);line-height:1.6;white-space:pre-wrap">${escHtml(n.content)}</p>
      <div style="margin-top:10px;font-size:.75rem;color:var(--muted)">🕐 ${n.created_at}</div>
    </div>
  `).join('');
}

function escHtml(t){
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function addNote(){
  const title   =document.getElementById('noteTitle').value.trim();
  const content =document.getElementById('noteContent').value.trim();
  const priority=document.getElementById('notePriority').value;
  const category=document.getElementById('noteCategory').value;
  if(!title)  {showToast('Title is required!','var(--red)');return;}
  if(!content){showToast('Content is required!','var(--red)');return;}
  const res=await fetch('/notes',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({title,content,priority,category})
  });
  if(res.ok){
    document.getElementById('noteTitle').value='';
    document.getElementById('noteContent').value='';
    document.getElementById('charCount').textContent='0 chars';
    showToast('✅ Note added!');loadNotes();
  }
}

async function deleteNote(id){
  if(!confirm('Delete this note?'))return;
  const res=await fetch('/notes/'+id,{method:'DELETE'});
  if(res.ok){showToast('🗑 Deleted!','var(--red)');loadNotes();}
}

async function pinNote(id){
  const res=await fetch('/notes/'+id+'/pin',{method:'PATCH'});
  if(res.ok){showToast('📌 Updated!','var(--orange)');loadNotes();}
}

async function archiveNote(id){
  const res=await fetch('/notes/'+id+'/archive',{method:'PATCH'});
  if(res.ok){showToast('📦 Updated!','var(--purple)');loadNotes();}
}

loadNotes();
</script>
"""

STATS_TEMPLATE = BASE_STYLE + """
<nav class="navbar">
  <span class="nav-brand">⚡ NoteFlow</span>
  <div class="nav-links">
    <button class="btn-sm" onclick="toggleTheme()" id="themeBtn">🌙</button>
    <a href="/"><button class="btn-sm">← Back to Notes</button></a>
  </div>
</nav>
<div class="container">
  <h2 style="margin-bottom:24px;font-size:1.2rem;font-weight:700">📊 Your Analytics</h2>

  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin-bottom:28px">
    {% for label,val,color in stats_cards %}
    <div class="card" style="text-align:center">
      <div style="font-size:2rem;font-weight:700;color:{{ color }}">{{ val }}</div>
      <div style="color:var(--muted);font-size:.82rem;margin-top:4px">{{ label }}</div>
    </div>
    {% endfor %}
  </div>

  <div class="card" style="margin-bottom:20px">
    <h3 style="margin-bottom:16px;font-size:.9rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">Notes by Category</h3>
    <div id="catChart"></div>
  </div>

  <div class="card">
    <h3 style="margin-bottom:16px;font-size:.9rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">Priority Distribution</h3>
    <div id="priChart"></div>
  </div>
</div>

<script>
function toggleTheme(){
  document.body.classList.toggle('light');
  const light=document.body.classList.contains('light');
  document.getElementById('themeBtn').textContent=light?'☀️':'🌙';
  localStorage.setItem('theme',light?'light':'dark');
}
if(localStorage.getItem('theme')==='light'){
  document.body.classList.add('light');
  document.getElementById('themeBtn').textContent='☀️';
}

function renderBar(containerId, data, colors){
  const max=Math.max(...Object.values(data),1);
  document.getElementById(containerId).innerHTML=
    Object.entries(data).map(([k,v],i)=>`
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
        <div style="width:100px;font-size:.85rem;color:var(--muted);flex-shrink:0">${k}</div>
        <div style="flex:1;background:var(--bg3);border-radius:6px;height:22px;overflow:hidden">
          <div style="width:${(v/max)*100}%;background:${colors[i%colors.length]};height:100%;border-radius:6px;transition:width .6s ease"></div>
        </div>
        <div style="width:20px;text-align:right;font-size:.85rem;font-weight:600">${v}</div>
      </div>`).join('');
}

renderBar('catChart', {{ cat_data }}, ['#4f8ef7','#3ecf8e','#f97316','#a78bfa','#f87171','#fbbf24']);
renderBar('priChart', {{ pri_data }}, ['#f87171','#fbbf24','#34d399']);
</script>
"""

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
@login_required
def index():
    REQUEST_COUNT.labels(method='GET', endpoint='/').inc()
    conn = get_db()
    user = conn.execute('SELECT username FROM users WHERE id=?', (session['user_id'],)).fetchone()
    conn.close()
    return render_template_string(MAIN_TEMPLATE, username=user['username'])

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route('/register', methods=['GET','POST'])
def register_page():
    error = None
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        email    = request.form.get('email','').strip()
        password = request.form.get('password','')
        if not username or not email or not password:
            error = 'All fields are required'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters'
        else:
            try:
                conn = get_db()
                conn.execute('INSERT INTO users (username,email,password) VALUES (?,?,?)',
                             (username, email, hash_password(password)))
                conn.commit(); conn.close()
                update_metrics()
                return redirect(url_for('login_page'))
            except sqlite3.IntegrityError:
                error = 'Username or email already exists'
    return render_template_string(AUTH_TEMPLATE,
        subtitle='Create your account', error=error,
        show_username=True, show_email=True, action='Register',
        link_text='Already have an account?', link_url='/login', link_label='Login')

@app.route('/login', methods=['GET','POST'])
def login_page():
    error = None
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username=? AND password=?',
                            (username, hash_password(password))).fetchone()
        conn.close()
        if user:
            session['user_id']  = user['id']
            session['username'] = user['username']
            LOGIN_COUNT.labels(status='success').inc()
            return redirect(url_for('index'))
        else:
            error = 'Invalid username or password'
            LOGIN_COUNT.labels(status='failed').inc()
    return render_template_string(AUTH_TEMPLATE,
        subtitle='Welcome back', error=error,
        show_username=True, show_email=False, action='Login',
        link_text="Don't have an account?", link_url='/register', link_label='Register')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

# ── Notes API ─────────────────────────────────────────────────────────────────
@app.route('/notes', methods=['GET'])
@login_required
def get_notes():
    REQUEST_COUNT.labels(method='GET', endpoint='/notes').inc()
    conn  = get_db()
    notes = conn.execute(
        'SELECT * FROM notes WHERE user_id=? ORDER BY pinned DESC, id DESC',
        (session['user_id'],)).fetchall()
    conn.close()
    return jsonify({'notes': [dict(n) for n in notes], 'count': len(notes)})

@app.route('/notes', methods=['POST'])
@login_required
def add_note():
    REQUEST_COUNT.labels(method='POST', endpoint='/notes').inc()
    data = request.get_json()
    if not data or not data.get('title','').strip() or not data.get('content','').strip():
        return jsonify({'error': 'Title and content required'}), 400
    conn = get_db()
    cursor = conn.execute(
        'INSERT INTO notes (user_id,title,content,category,priority) VALUES (?,?,?,?,?)',
        (session['user_id'], data['title'].strip(), data['content'].strip(),
         data.get('category','General'), data.get('priority','Medium')))
    note_id = cursor.lastrowid
    conn.commit(); conn.close()
    NOTE_OPS.labels(operation='add').inc()
    update_metrics()
    return jsonify({
        'message': 'Note added',
        'note': {
            'id': note_id,
            'title': data['title'].strip(),
            'content': data['content'].strip(),
            'category': data.get('category','General'),
            'priority': data.get('priority','Medium'),
            'status': 'active',
            'pinned': 0
        }
    }), 201

@app.route('/notes/<int:note_id>', methods=['DELETE'])
@login_required
def delete_note(note_id):
    REQUEST_COUNT.labels(method='DELETE', endpoint='/notes/id').inc()
    conn   = get_db()
    result = conn.execute('DELETE FROM notes WHERE id=? AND user_id=?',
                          (note_id, session['user_id']))
    conn.commit(); conn.close()
    if result.rowcount == 0:
        return jsonify({'error': 'Note not found'}), 404
    NOTE_OPS.labels(operation='delete').inc()
    update_metrics()
    return jsonify({'message': 'Note deleted'}), 200

@app.route('/notes/<int:note_id>/pin', methods=['PATCH'])
@login_required
def pin_note(note_id):
    conn = get_db()
    note = conn.execute('SELECT pinned FROM notes WHERE id=? AND user_id=?',
                        (note_id, session['user_id'])).fetchone()
    if not note:
        conn.close(); return jsonify({'error': 'Not found'}), 404
    conn.execute('UPDATE notes SET pinned=? WHERE id=?', (0 if note['pinned'] else 1, note_id))
    conn.commit(); conn.close()
    NOTE_OPS.labels(operation='pin').inc()
    return jsonify({'message': 'Updated'}), 200

@app.route('/notes/<int:note_id>/archive', methods=['PATCH'])
@login_required
def archive_note(note_id):
    conn = get_db()
    note = conn.execute('SELECT status FROM notes WHERE id=? AND user_id=?',
                        (note_id, session['user_id'])).fetchone()
    if not note:
        conn.close(); return jsonify({'error': 'Not found'}), 404
    new_status = 'active' if note['status'] == 'archived' else 'archived'
    conn.execute('UPDATE notes SET status=? WHERE id=?', (new_status, note_id))
    conn.commit(); conn.close()
    NOTE_OPS.labels(operation='archive').inc()
    return jsonify({'message': 'Updated'}), 200

# ── Stats ─────────────────────────────────────────────────────────────────────
@app.route('/stats')
@login_required
def stats():
    REQUEST_COUNT.labels(method='GET', endpoint='/stats').inc()
    conn     = get_db()
    uid      = session['user_id']
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

    total    = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND status='active'", (uid,)).fetchone()[0]
    high     = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND priority='High' AND status='active'", (uid,)).fetchone()[0]
    medium   = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND priority='Medium' AND status='active'", (uid,)).fetchone()[0]
    low      = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND priority='Low' AND status='active'", (uid,)).fetchone()[0]
    archived = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND status='archived'", (uid,)).fetchone()[0]
    pinned   = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND pinned=1", (uid,)).fetchone()[0]
    week     = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND date(created_at)>=?", (uid, week_ago)).fetchone()[0]
    cats_raw = conn.execute("SELECT category, COUNT(*) cnt FROM notes WHERE user_id=? AND status='active' GROUP BY category", (uid,)).fetchall()
    conn.close()

    cat_data = json.dumps({r['category']: r['cnt'] for r in cats_raw})
    pri_data = json.dumps({'High': high, 'Medium': medium, 'Low': low})
    stats_cards = [
        ('Total Active', total,    'var(--blue)'),
        ('High Priority', high,    'var(--red)'),
        ('Medium Priority', medium,'#fbbf24'),
        ('Low Priority', low,      'var(--green)'),
        ('Archived', archived,     'var(--purple)'),
        ('Pinned', pinned,         'var(--orange)'),
        ('Added This Week', week,  'var(--blue)'),
    ]
    return render_template_string(STATS_TEMPLATE,
        stats_cards=stats_cards, cat_data=cat_data, pri_data=pri_data)

# ── Export ────────────────────────────────────────────────────────────────────
@app.route('/export/csv')
@login_required
def export_csv():
    conn  = get_db()
    notes = conn.execute(
        'SELECT title,content,category,priority,status,pinned,created_at FROM notes WHERE user_id=?',
        (session['user_id'],)).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Title','Content','Category','Priority','Status','Pinned','Created At'])
    for n in notes:
        writer.writerow(list(n))
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()),
                     mimetype='text/csv', as_attachment=True,
                     download_name='noteflow_export.csv')

@app.route('/export/json')
@login_required
def export_json():
    conn  = get_db()
    notes = conn.execute('SELECT * FROM notes WHERE user_id=?', (session['user_id'],)).fetchall()
    conn.close()
    data  = json.dumps([dict(n) for n in notes], indent=2)
    return send_file(io.BytesIO(data.encode()),
                     mimetype='application/json', as_attachment=True,
                     download_name='noteflow_export.json')

# ── System ────────────────────────────────────────────────────────────────────
@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()}), 200

@app.route('/metrics')
def metrics():
    REQUEST_COUNT.labels(method='GET', endpoint='/metrics').inc()
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}

if __name__ == '__main__':
    init_db()
    update_metrics()
    app.run(host='0.0.0.0', port=5000, debug=True)
