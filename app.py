from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session, send_file
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import sqlite3, hashlib, os, csv, json, io, re
from datetime import datetime, timedelta
from functools import wraps
import jwt as pyjwt

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'noteflow-ultra-secret-2025')
DB = 'noteflow.db'

# ── TESTING MODE ──────────────────────────────────────────────────────────────
TESTING = os.environ.get("TESTING") == "1"

# ── Rate Limiter ──────────────────────────────────────────────────────────────
if TESTING:
    limiter = Limiter(
        get_remote_address,
        app=app,
        enabled=False
    )
else:
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["200 per day", "50 per hour"]
    )

# ── Prometheus Metrics ────────────────────────────────────────────────────────
REQUEST_COUNT = Counter('noteflow_requests_total',  'Total HTTP requests',   ['method', 'endpoint'])
NOTES_TOTAL   = Gauge  ('noteflow_notes_total',     'Total active notes')
USERS_TOTAL   = Gauge  ('noteflow_users_total',     'Total registered users')
NOTE_OPS      = Counter('noteflow_note_operations', 'Note CRUD operations',  ['operation'])
LOGIN_COUNT   = Counter('noteflow_login_total',     'Login attempts',        ['status'])
SEARCH_COUNT  = Counter('noteflow_searches_total',  'Total searches made')

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            email         TEXT    UNIQUE NOT NULL,
            password      TEXT    NOT NULL,
            login_attempts INTEGER DEFAULT 0,
            locked_until  TEXT    DEFAULT NULL,
            created       TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            title      TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            summary    TEXT    DEFAULT NULL,
            category   TEXT    DEFAULT 'General',
            priority   TEXT    DEFAULT 'Medium',
            status     TEXT    DEFAULT 'active',
            pinned     INTEGER DEFAULT 0,
            due_date   TEXT    DEFAULT NULL,
            created_at TEXT    DEFAULT (datetime('now')),
            updated_at TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS tags (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name    TEXT    NOT NULL,
            UNIQUE(user_id, name)
        );
        CREATE TABLE IF NOT EXISTS note_tags (
            note_id INTEGER NOT NULL,
            tag_id  INTEGER NOT NULL,
            PRIMARY KEY (note_id, tag_id)
        );
        CREATE TABLE IF NOT EXISTS note_links (
            note_id    INTEGER NOT NULL,
            linked_id  INTEGER NOT NULL,
            PRIMARY KEY (note_id, linked_id)
        );
    ''')
    conn.commit(); conn.close()

# ── Helpers ───────────────────────────────────────────────────────────────────
def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()

def password_strength(p):
    score = 0
    if len(p) >= 8:   score += 1
    if re.search(r'[A-Z]', p): score += 1
    if re.search(r'[0-9]', p): score += 1
    if re.search(r'[^A-Za-z0-9]', p): score += 1
    return score  # 0-4

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
    NOTES_TOTAL.set(active); USERS_TOTAL.set(users)

def highlight(text, keyword):
    if not keyword: return text
    escaped = re.escape(keyword)
    return re.sub(f'({escaped})', r'<mark style="background:#fbbf24;color:#000;border-radius:3px;padding:0 2px">\1</mark>',
                  text, flags=re.IGNORECASE)

def get_note_tags(conn, note_id):
    rows = conn.execute(
        'SELECT t.name FROM tags t JOIN note_tags nt ON t.id=nt.tag_id WHERE nt.note_id=?',
        (note_id,)).fetchall()
    return [r['name'] for r in rows]

# ── AI Summary (Gemini free) ──────────────────────────────────────────────────
def ai_summarize(content):
    try:
        import requests as req
        api_key = os.environ.get('GEMINI_API_KEY', '')
        if not api_key:
            # Fallback: simple extractive summary (first 2 sentences)
            sentences = content.replace('!','.').replace('?','.').split('.')
            sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
            return '. '.join(sentences[:2]) + '.' if sentences else content[:150]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": f"Summarize this note in 1-2 sentences:\n{content}"}]}]}
        res = req.post(url, json=payload, timeout=5)
        data = res.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except:
        sentences = content.replace('!','.').replace('?','.').split('.')
        sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
        return '. '.join(sentences[:2]) + '.' if sentences else content[:150]

def ai_suggest_category(title, content):
    text = (title + ' ' + content).lower()
    rules = {
        'Study':     ['study','exam','lecture','notes','class','course','assignment','homework','university'],
        'Work':      ['work','meeting','project','deadline','client','office','task','report','email'],
        'Personal':  ['personal','diary','feel','mood','family','friend','life','goal','dream'],
        'Important': ['important','urgent','critical','must','asap','deadline','priority'],
        'Ideas':     ['idea','plan','think','concept','brainstorm','what if','maybe','could'],
    }
    for cat, keywords in rules.items():
        if any(k in text for k in keywords):
            return cat
    return 'General'

# ── Activity heatmap data ─────────────────────────────────────────────────────
def get_heatmap_data(conn, user_id):
    rows = conn.execute(
        "SELECT date(created_at) as d, COUNT(*) as cnt FROM notes WHERE user_id=? AND created_at >= date('now','-364 days') GROUP BY d",
        (user_id,)).fetchall()
    return {r['d']: r['cnt'] for r in rows}

# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES
# ══════════════════════════════════════════════════════════════════════════════
BASE_STYLE = """
<style>
:root{--bg:#0d0d0d;--bg2:#161616;--bg3:#1e1e1e;--border:#2a2a2a;--text:#e0e0e0;--muted:#555;--blue:#4f8ef7;--green:#3ecf8e;--red:#e05555;--orange:#f97316;--purple:#a78bfa;--yellow:#fbbf24;}
body.light{--bg:#f0f2f5;--bg2:#ffffff;--bg3:#e8eaed;--border:#d1d5db;--text:#111827;--muted:#6b7280;}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;transition:background .3s,color .3s;}
a{color:var(--blue);text-decoration:none;}
input,textarea,select{background:var(--bg3);border:1.5px solid var(--border);color:var(--text);border-radius:8px;padding:10px 14px;font-size:.9rem;outline:none;font-family:inherit;transition:border-color .2s;width:100%;}
input:focus,textarea:focus,select:focus{border-color:var(--blue);}
button{cursor:pointer;font-family:inherit;border:none;border-radius:8px;font-size:.88rem;transition:all .2s;}
.btn-primary{background:var(--blue);color:#fff;padding:10px 22px;font-weight:600;}
.btn-primary:hover{opacity:.85;}
.btn-sm{background:var(--bg3);color:var(--text);padding:6px 12px;border:1px solid var(--border);}
.btn-sm:hover{border-color:var(--blue);color:var(--blue);}
.btn-danger{background:transparent;color:var(--muted);padding:5px 10px;border:1px solid var(--border);font-size:.78rem;}
.btn-danger:hover{border-color:var(--red);color:var(--red);}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:18px 20px;}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:.7rem;font-weight:600;}
.badge-high{background:#3b1010;color:#f87171;}.badge-medium{background:#2d2510;color:#fbbf24;}.badge-low{background:#0f2d1e;color:#34d399;}
.badge-cat{background:var(--bg3);color:var(--muted);border:1px solid var(--border);}
.badge-tag{background:#1e2d3b;color:var(--blue);border:1px solid #2a4a6b;font-size:.7rem;padding:2px 8px;border-radius:20px;}
.badge-overdue{background:#3b1010;color:#f87171;}
.badge-due-soon{background:#2d2510;color:#fbbf24;}
body.light .badge-high{background:#fee2e2;color:#dc2626;}
body.light .badge-medium{background:#fef3c7;color:#d97706;}
body.light .badge-low{background:#d1fae5;color:#059669;}
body.light .badge-tag{background:#dbeafe;color:#1d4ed8;border-color:#bfdbfe;}
.toast{position:fixed;bottom:24px;right:24px;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:12px 20px;border-radius:10px;font-size:.88rem;z-index:9999;opacity:0;transform:translateY(10px);transition:all .3s;box-shadow:0 4px 24px rgba(0,0,0,.4);min-width:200px;}
.toast.show{opacity:1;transform:translateY(0);}
.navbar{background:var(--bg2);border-bottom:1px solid var(--border);padding:0 20px;height:54px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;}
.nav-brand{font-size:1.05rem;font-weight:700;color:var(--blue);letter-spacing:-.3px;}
.nav-links{display:flex;align-items:center;gap:6px;flex-wrap:wrap;}
.container{max-width:980px;margin:0 auto;padding:24px 16px;}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
.grid-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:translateY(0);}}
.fade-in{animation:fadeIn .2s ease;}
.strength-bar{height:4px;border-radius:2px;transition:width .3s,background .3s;margin-top:6px;}
mark{background:var(--yellow);color:#000;border-radius:3px;padding:0 2px;}
.overdue-card{border-color:var(--red)!important;}
.due-soon-card{border-color:var(--yellow)!important;}
.pinned-card{border-color:var(--blue)!important;}
</style>
"""

LANDING_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NoteFlow — Smart Note Management</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0a0f; color: #e2e8f0; overflow-x: hidden; }
a { text-decoration: none; color: inherit; }

/* Nav */
nav {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0 48px; height: 60px;
  background: rgba(10,10,15,0.8);
  border-bottom: 1px solid #1e2530;
  position: sticky; top: 0; z-index: 100;
  backdrop-filter: blur(12px);
}
.nav-brand { display: flex; align-items: center; gap: 8px; font-size: 16px; font-weight: 700; color: #f0f6fc; }
.nav-icon { width: 28px; height: 28px; background: linear-gradient(135deg,#388bfd,#7c3aed); border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 13px; }
.nav-links { display: flex; gap: 10px; }
.btn-ghost { padding: 8px 18px; border: 1px solid #30363d; color: #8b949e; border-radius: 8px; font-size: 13px; font-weight: 500; cursor: pointer; background: transparent; transition: all .2s; }
.btn-ghost:hover { border-color: #388bfd; color: #388bfd; background: rgba(56,139,253,.05); }
.btn-solid { padding: 8px 18px; background: linear-gradient(135deg,#388bfd,#7c3aed); color: #fff; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; transition: opacity .2s, transform .15s; box-shadow: 0 2px 12px rgba(56,139,253,.3); }
.btn-solid:hover { opacity: .9; transform: translateY(-1px); }

/* Hero */
.hero {
  min-height: 90vh;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-align: center; padding: 80px 24px 60px;
  background:
    radial-gradient(ellipse 60% 50% at 20% 40%, rgba(56,139,253,.06) 0%, transparent 100%),
    radial-gradient(ellipse 50% 60% at 80% 30%, rgba(124,58,237,.05) 0%, transparent 100%),
    radial-gradient(ellipse 40% 50% at 60% 80%, rgba(63,185,80,.04) 0%, transparent 100%);
  position: relative;
}
.hero-badge {
  display: inline-flex; align-items: center; gap: 6px;
  background: rgba(56,139,253,.08); border: 1px solid rgba(56,139,253,.2);
  color: #388bfd; padding: 5px 14px; border-radius: 20px;
  font-size: 12px; font-weight: 500; margin-bottom: 24px;
  animation: fadeDown .5s ease;
}
.hero h1 {
  font-size: clamp(2rem,5vw,3.5rem); font-weight: 800;
  color: #f0f6fc; line-height: 1.1; margin-bottom: 18px;
  letter-spacing: -1px; animation: fadeDown .55s ease .05s both;
}
.hero h1 .grad { background: linear-gradient(135deg,#388bfd,#a78bfa,#3fb950); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.hero-p {
  font-size: 1rem; color: #7d8590; max-width: 520px;
  line-height: 1.8; margin-bottom: 36px;
  animation: fadeDown .55s ease .1s both;
}
.hero-btns { display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; animation: fadeDown .55s ease .15s both; }
.btn-hero-p {
  padding: 13px 30px; background: linear-gradient(135deg,#388bfd,#7c3aed);
  color: #fff; border: none; border-radius: 9px; font-size: 14px;
  font-weight: 700; cursor: pointer; box-shadow: 0 4px 20px rgba(56,139,253,.35);
  transition: all .2s; letter-spacing: -.2px;
}
.btn-hero-p:hover { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(56,139,253,.45); }
.btn-hero-s {
  padding: 13px 30px; background: transparent;
  color: #c9d1d9; border: 1px solid #30363d; border-radius: 9px;
  font-size: 14px; font-weight: 600; cursor: pointer; transition: all .2s;
}
.btn-hero-s:hover { border-color: #8b949e; color: #f0f6fc; }

/* App preview mockup */
.mockup-wrap {
  margin: 60px auto 0; max-width: 700px; width: 100%;
  animation: fadeUp .7s ease .2s both; padding: 0 20px;
}
.mockup-window {
  background: #0d1117; border: 1px solid #30363d; border-radius: 14px;
  overflow: hidden; box-shadow: 0 30px 60px rgba(0,0,0,.5), 0 0 0 1px rgba(255,255,255,.03);
}
.mockup-bar {
  background: #161b22; border-bottom: 1px solid #21262d;
  padding: 11px 16px; display: flex; align-items: center; gap: 7px;
}
.dot { width: 10px; height: 10px; border-radius: 50%; }
.mockup-url { font-size: 11px; color: #484f58; margin-left: 8px; }
.mockup-body { padding: 18px; }
.m-note { background: #161b22; border: 1px solid #21262d; border-radius: 10px; padding: 12px 14px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
.m-note.pin { border-color: rgba(56,139,253,.3); }
.m-note-l .m-title { font-size: 13px; font-weight: 500; color: #c9d1d9; margin-bottom: 5px; }
.m-tags { display: flex; gap: 5px; }
.m-tag { font-size: 10px; padding: 2px 7px; border-radius: 20px; border: 1px solid #30363d; color: #8b949e; }
.m-tag.r { background: rgba(248,81,73,.08); border-color: rgba(248,81,73,.3); color: #f85149; }
.m-tag.b { background: rgba(56,139,253,.08); border-color: rgba(56,139,253,.3); color: #388bfd; }
.m-tag.g { background: rgba(63,185,80,.08); border-color: rgba(63,185,80,.3); color: #3fb950; }
.m-stats { display: flex; gap: 8px; margin-top: 10px; }
.m-stat { flex: 1; background: #0d1117; border: 1px solid #21262d; border-radius: 8px; padding: 8px; text-align: center; }
.m-stat-v { font-size: 16px; font-weight: 600; }
.m-stat-l { font-size: 10px; color: #484f58; }

/* Features */
.features { padding: 100px 24px; background: #0d1117; border-top: 1px solid #1e2530; border-bottom: 1px solid #1e2530; }
.sec-title { text-align: center; font-size: clamp(1.4rem,3vw,1.9rem); font-weight: 700; color: #f0f6fc; margin-bottom: 8px; letter-spacing: -.5px; }
.sec-sub { text-align: center; color: #7d8590; font-size: 14px; margin-bottom: 52px; }
.feat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px,1fr)); gap: 16px; max-width: 860px; margin: 0 auto; }
.feat-card {
  background: #161b22; border: 1px solid #21262d; border-radius: 12px;
  padding: 22px; transition: all .25s; cursor: default;
}
.feat-card:hover { border-color: #388bfd44; background: #1c2128; transform: translateY(-3px); }
.feat-card-icon { font-size: 22px; margin-bottom: 10px; }
.feat-card-title { font-size: 14px; font-weight: 600; color: #c9d1d9; margin-bottom: 6px; }
.feat-card-desc { font-size: 12.5px; color: #7d8590; line-height: 1.6; }

/* Tech stack */
.stack { padding: 80px 24px; text-align: center; }
.stack-tags { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; max-width: 600px; margin: 0 auto; }
.stack-tag { padding: 6px 16px; background: #161b22; border: 1px solid #30363d; border-radius: 20px; font-size: 12.5px; color: #8b949e; }

/* CTA */
.cta { padding: 100px 24px; text-align: center; }
.cta-inner {
  max-width: 580px; margin: 0 auto;
  background: linear-gradient(135deg, rgba(56,139,253,.06), rgba(124,58,237,.06));
  border: 1px solid rgba(56,139,253,.15); border-radius: 18px; padding: 56px 40px;
}
.cta h2 { font-size: clamp(1.4rem,3vw,2rem); font-weight: 700; color: #f0f6fc; margin-bottom: 10px; letter-spacing: -.5px; }
.cta p { color: #7d8590; font-size: 14px; margin-bottom: 28px; }

footer { text-align: center; padding: 24px; border-top: 1px solid #1e2530; font-size: 12px; color: #484f58; }

@keyframes fadeDown { from { opacity:0; transform:translateY(-16px); } to { opacity:1; transform:translateY(0); } }
@keyframes fadeUp   { from { opacity:0; transform:translateY(24px);  } to { opacity:1; transform:translateY(0); } }
</style>
</head>
<body>

<nav>
  <div class="nav-brand">
    <div class="nav-icon">⚡</div>
    NoteFlow
  </div>
  <div class="nav-links">
    <a href="/login"><button class="btn-ghost">Sign in</button></a>
    <a href="/register"><button class="btn-solid">Get started</button></a>
  </div>
</nav>

<!-- Hero -->
<section class="hero">
  <div class="hero-badge">✦ Built for INT377 — Cloud Computing & DevOps</div>
  <h1>Capture ideas.<br><span class="grad">Ship them smarter.</span></h1>
  <p class="hero-p">NoteFlow is a full-stack notes platform with AI summaries, smart tagging, due date tracking, and a production-grade DevOps pipeline — Docker, Kubernetes, Jenkins, Prometheus, Grafana.</p>
  <div class="hero-btns">
    <a href="/register"><button class="btn-hero-p">🚀 Start for free</button></a>
    <a href="/login"><button class="btn-hero-s">Sign in →</button></a>
  </div>

  <div class="mockup-wrap">
    <div class="mockup-window">
      <div class="mockup-bar">
        <div class="dot" style="background:#ff5f57"></div>
        <div class="dot" style="background:#febc2e"></div>
        <div class="dot" style="background:#28c840"></div>
        <span class="mockup-url">localhost:5000 — NoteFlow</span>
      </div>
      <div class="mockup-body">
        <div class="m-note pin">
          <div class="m-note-l">
            <div class="m-title">📌 DevOps Pipeline Setup — Jenkins + K8s</div>
            <div class="m-tags">
              <span class="m-tag r">High</span>
              <span class="m-tag b">#docker</span>
              <span class="m-tag b">#jenkins</span>
              <span class="m-tag r">Due: Tomorrow</span>
            </div>
          </div>
        </div>
        <div class="m-note">
          <div class="m-note-l">
            <div class="m-title">Kubernetes deployment.yaml — NoteFlow</div>
            <div class="m-tags">
              <span class="m-tag">Medium</span>
              <span class="m-tag b">#k8s</span>
              <span class="m-tag g">🤖 AI summary ready</span>
            </div>
          </div>
        </div>
        <div class="m-note">
          <div class="m-note-l">
            <div class="m-title">Project Synopsis — NoteFlow DevOps</div>
            <div class="m-tags">
              <span class="m-tag">Low</span>
              <span class="m-tag">Personal</span>
              <span class="m-tag g">✓ Archived</span>
            </div>
          </div>
        </div>
        <div class="m-stats">
          <div class="m-stat"><div class="m-stat-v" style="color:#388bfd">12</div><div class="m-stat-l">Active</div></div>
          <div class="m-stat"><div class="m-stat-v" style="color:#3fb950">87%</div><div class="m-stat-l">Productivity</div></div>
          <div class="m-stat"><div class="m-stat-v" style="color:#f85149">3</div><div class="m-stat-l">Overdue</div></div>
          <div class="m-stat"><div class="m-stat-v" style="color:#a78bfa">8</div><div class="m-stat-l">Tags</div></div>
          <div class="m-stat"><div class="m-stat-v" style="color:#d29922">5</div><div class="m-stat-l">Linked</div></div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- Features -->
<section class="features">
  <div class="sec-title">Everything in one place</div>
  <div class="sec-sub">Powerful features. Clean interface.</div>
  <div class="feat-grid">
    <div class="feat-card">
      <div class="feat-card-icon">🤖</div>
      <div class="feat-card-title">AI-powered summaries</div>
      <div class="feat-card-desc">Auto-summarize long notes and detect category using Gemini AI. One click to get the gist.</div>
    </div>
    <div class="feat-card">
      <div class="feat-card-icon">🏷️</div>
      <div class="feat-card-title">Smart tagging & tag cloud</div>
      <div class="feat-card-desc">Add custom tags, visualize a tag cloud, and filter notes by clicking any tag instantly.</div>
    </div>
    <div class="feat-card">
      <div class="feat-card-icon">📅</div>
      <div class="feat-card-title">Due dates & overdue alerts</div>
      <div class="feat-card-desc">Set deadlines. Get real-time overdue alerts highlighted in red on your dashboard.</div>
    </div>
    <div class="feat-card">
      <div class="feat-card-icon">🔗</div>
      <div class="feat-card-title">Link related notes</div>
      <div class="feat-card-desc">Build a knowledge graph by linking any two notes together with one click.</div>
    </div>
    <div class="feat-card">
      <div class="feat-card-icon">📊</div>
      <div class="feat-card-title">Analytics & heatmap</div>
      <div class="feat-card-desc">GitHub-style activity heatmap, productivity score, and category bar charts.</div>
    </div>
    <div class="feat-card">
      <div class="feat-card-icon">🔐</div>
      <div class="feat-card-title">Secure by design</div>
      <div class="feat-card-desc">Rate limiting, account lockout after 5 failed attempts, password strength meter.</div>
    </div>
  </div>
</section>

<!-- Tech stack -->
<section class="stack">
  <div class="sec-title" style="margin-bottom:8px">Backed by a real DevOps pipeline</div>
  <div class="sec-sub" style="margin-bottom:28px">Every commit triggers a full CI/CD cycle</div>
  <div class="stack-tags">
    <span class="stack-tag">⚙️ Python Flask</span>
    <span class="stack-tag">🐳 Docker</span>
    <span class="stack-tag">☸️ Kubernetes</span>
    <span class="stack-tag">🔧 Jenkins CI/CD</span>
    <span class="stack-tag">🏗️ Terraform</span>
    <span class="stack-tag">📈 Prometheus</span>
    <span class="stack-tag">📊 Grafana</span>
    <span class="stack-tag">🗄️ SQLite</span>
    <span class="stack-tag">🔀 Git + GitHub</span>
  </div>
</section>

<!-- CTA -->
<section class="cta">
  <div class="cta-inner">
    <div style="font-size:2rem;margin-bottom:12px">⚡</div>
    <h2>Ready to get organized?</h2>
    <p>Create your free account and start building your knowledge base today.</p>
    <a href="/register"><button class="btn-hero-p">Create free account →</button></a>
  </div>
</section>

<footer>
  NoteFlow · Built with Flask, Docker, Kubernetes, Jenkins, Prometheus & Grafana · INT377 DevOps Project
</footer>

</body>
</html>
"""


AUTH_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NoteFlow — {{ action }}</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0a0f; color: #e2e8f0; min-height: 100vh; display: flex; overflow: hidden; }

.nf-left {
  flex: 1;
  background: #0d1117;
  border-right: 1px solid #1e2530;
  padding: 36px 40px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  position: relative;
  overflow: hidden;
}
.nf-left::before {
  content: '';
  position: absolute;
  width: 500px; height: 500px;
  background: radial-gradient(circle, rgba(56,139,253,0.07) 0%, transparent 70%);
  top: -100px; left: -100px;
  pointer-events: none;
}
.nf-left::after {
  content: '';
  position: absolute;
  width: 400px; height: 400px;
  background: radial-gradient(circle, rgba(124,58,237,0.05) 0%, transparent 70%);
  bottom: -80px; right: -60px;
  pointer-events: none;
}

.brand { display: flex; align-items: center; gap: 8px; }
.brand-icon { width: 28px; height: 28px; background: linear-gradient(135deg, #388bfd, #7c3aed); border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 13px; }
.brand-name { font-size: 15px; font-weight: 600; color: #e2e8f0; letter-spacing: -0.3px; }

.hero-section { flex: 1; display: flex; flex-direction: column; justify-content: center; padding: 32px 0; }
.hero-h1 { font-size: 28px; font-weight: 700; color: #f0f6fc; line-height: 1.25; margin-bottom: 12px; letter-spacing: -0.5px; }
.hero-h1 span { background: linear-gradient(135deg, #388bfd, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.hero-p { font-size: 13.5px; color: #7d8590; line-height: 1.7; margin-bottom: 28px; max-width: 340px; }

.feat { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.feat-dot { width: 24px; height: 24px; border-radius: 6px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; font-size: 11px; }
.feat-text { font-size: 13px; color: #8b949e; }
.feat-text strong { color: #c9d1d9; font-weight: 500; }

.note-cards { display: flex; flex-direction: column; gap: 8px; }
.nc { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 12px 14px; }
.nc.pinned { border-color: #388bfd44; }
.nc-title { font-size: 12.5px; font-weight: 500; color: #c9d1d9; margin-bottom: 6px; }
.nc-tags { display: flex; gap: 5px; flex-wrap: wrap; }
.nc-tag { font-size: 10.5px; padding: 2px 7px; border-radius: 20px; border: 1px solid #30363d; color: #8b949e; }
.nc-tag.r { background: rgba(248,81,73,0.1); border-color: rgba(248,81,73,0.3); color: #f85149; }
.nc-tag.b { background: rgba(56,139,253,0.1); border-color: rgba(56,139,253,0.3); color: #388bfd; }
.nc-tag.g { background: rgba(63,185,80,0.1); border-color: rgba(63,185,80,0.3); color: #3fb950; }
.stats-row { display: flex; gap: 8px; margin-top: 8px; }
.stat { flex: 1; background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 8px 10px; text-align: center; }
.stat-v { font-size: 17px; font-weight: 600; }
.stat-l { font-size: 10px; color: #7d8590; margin-top: 1px; }

/* Right panel */
.nf-right {
  width: 420px;
  flex-shrink: 0;
  background: #0a0a0f;
  padding: 48px 44px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.form-heading { font-size: 20px; font-weight: 700; color: #f0f6fc; margin-bottom: 4px; letter-spacing: -0.3px; }
.form-sub { font-size: 13px; color: #7d8590; margin-bottom: 30px; }

.err-box { background: rgba(248,81,73,0.08); border: 1px solid rgba(248,81,73,0.25); color: #f85149; padding: 10px 14px; border-radius: 8px; margin-bottom: 18px; font-size: 13px; }

.fld { margin-bottom: 16px; }
.fld label { display: block; font-size: 11px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: .06em; margin-bottom: 6px; }
.fld input {
  width: 100%;
  background: #161b22;
  border: 1px solid #30363d;
  color: #e2e8f0;
  border-radius: 8px;
  padding: 11px 14px;
  font-size: 13.5px;
  outline: none;
  font-family: inherit;
  transition: border-color .2s, box-shadow .2s;
}
.fld input:focus { border-color: #388bfd; box-shadow: 0 0 0 3px rgba(56,139,253,0.12); }

.str-row { display: flex; gap: 4px; margin-top: 7px; }
.s-seg { flex: 1; height: 3px; border-radius: 2px; background: #21262d; transition: background .3s; }
.str-lbl { font-size: 11px; color: #7d8590; margin-top: 4px; }

.submit-btn {
  width: 100%;
  padding: 12px;
  background: linear-gradient(135deg, #388bfd, #7c3aed);
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  margin-top: 4px;
  font-family: inherit;
  letter-spacing: -0.2px;
  transition: opacity .2s, transform .15s;
}
.submit-btn:hover { opacity: 0.9; transform: translateY(-1px); }
.submit-btn:active { transform: translateY(0); }

.or-div { text-align: center; position: relative; margin: 18px 0; }
.or-div::before { content: ''; position: absolute; top: 50%; left: 0; right: 0; height: 1px; background: #21262d; }
.or-div span { background: #0a0a0f; padding: 0 10px; position: relative; font-size: 12px; color: #484f58; }

.foot-lnk { text-align: center; font-size: 13px; color: #7d8590; }
.foot-lnk a { color: #388bfd; font-weight: 500; text-decoration: none; }
.foot-lnk a:hover { text-decoration: underline; }

@media (max-width: 768px) {
  .nf-left { display: none; }
  .nf-right { width: 100%; background: #0d1117; }
}
</style>
</head>
<body>

<div class="nf-left">
  <div class="brand">
    <div class="brand-icon">⚡</div>
    <span class="brand-name">NoteFlow</span>
  </div>

  <div class="hero-section">
    <h1 class="hero-h1">Your notes,<br><span>intelligently organized.</span></h1>
    <p class="hero-p">AI summaries, smart tagging, due dates, and a complete DevOps pipeline behind every feature.</p>

    <div class="feat">
      <div class="feat-dot" style="background:rgba(56,139,253,.15)">🤖</div>
      <div class="feat-text"><strong>AI summaries</strong> &amp; auto-categorization</div>
    </div>
    <div class="feat">
      <div class="feat-dot" style="background:rgba(63,185,80,.15)">📊</div>
      <div class="feat-text"><strong>Analytics dashboard</strong> with activity heatmap</div>
    </div>
    <div class="feat">
      <div class="feat-dot" style="background:rgba(210,153,34,.15)">📅</div>
      <div class="feat-text"><strong>Due dates</strong> &amp; overdue alerts</div>
    </div>
    <div class="feat">
      <div class="feat-dot" style="background:rgba(248,81,73,.15)">🔐</div>
      <div class="feat-text"><strong>Rate limiting</strong> &amp; account lockout security</div>
    </div>
    <div class="feat">
      <div class="feat-dot" style="background:rgba(167,139,250,.15)">🔗</div>
      <div class="feat-text"><strong>Link notes</strong>, add tags, export CSV &amp; JSON</div>
    </div>
  </div>

  <div class="note-cards">
    <div class="nc pinned">
      <div class="nc-title">📌 DevOps Pipeline Setup</div>
      <div class="nc-tags">
        <span class="nc-tag r">High priority</span>
        <span class="nc-tag b">#docker</span>
        <span class="nc-tag r">Due tomorrow</span>
      </div>
    </div>
    <div class="nc">
      <div class="nc-title">Kubernetes deployment YAML</div>
      <div class="nc-tags">
        <span class="nc-tag">Medium</span>
        <span class="nc-tag b">#k8s</span>
        <span class="nc-tag g">AI summary ready</span>
      </div>
    </div>
    <div class="stats-row">
      <div class="stat"><div class="stat-v" style="color:#388bfd">12</div><div class="stat-l">Active</div></div>
      <div class="stat"><div class="stat-v" style="color:#3fb950">87%</div><div class="stat-l">Productivity</div></div>
      <div class="stat"><div class="stat-v" style="color:#f85149">3</div><div class="stat-l">Overdue</div></div>
      <div class="stat"><div class="stat-v" style="color:#a78bfa">8</div><div class="stat-l">Tags</div></div>
    </div>
  </div>
</div>

<div class="nf-right">
  <div class="form-heading">{{ action }}</div>
  <div class="form-sub">{{ subtitle }}</div>

  {% if error %}
  <div class="err-box">⚠️ {{ error }}</div>
  {% endif %}

  <form method="POST">
    {% if show_username %}
    <div class="fld">
      <label>Username</label>
      <input name="username" placeholder="Enter your username" required autocomplete="username">
    </div>
    {% endif %}
    {% if show_email %}
    <div class="fld">
      <label>Email</label>
      <input name="email" type="email" placeholder="you@example.com" required>
    </div>
    {% endif %}
    <div class="fld">
      <label>Password</label>
      <input name="password" type="password" id="passInput" placeholder="Enter your password" required oninput="checkStr(this.value)">
      {% if show_email %}
      <div class="str-row">
        <div class="s-seg" id="sg1"></div>
        <div class="s-seg" id="sg2"></div>
        <div class="s-seg" id="sg3"></div>
        <div class="s-seg" id="sg4"></div>
      </div>
      <div class="str-lbl" id="strLbl"></div>
      {% endif %}
    </div>
    <button type="submit" class="submit-btn">{{ action }} →</button>
  </form>

  <div class="or-div"><span>or</span></div>
  <div class="foot-lnk">{{ link_text }} <a href="{{ link_url }}">{{ link_label }}</a></div>
  <div class="foot-lnk" style="margin-top:8px"><a href="/">← Back to home</a></div>
</div>

<script>
function checkStr(p) {
  const lbl = document.getElementById('strLbl');
  if (!lbl) return;
  let s = 0;
  if (p.length >= 8) s++;
  if (/[A-Z]/.test(p)) s++;
  if (/[0-9]/.test(p)) s++;
  if (/[^A-Za-z0-9]/.test(p)) s++;
  const colors = ['#f85149','#d29922','#d29922','#3fb950'];
  const labels = ['Weak','Fair','Good','Strong 💪'];
  [1,2,3,4].forEach(i => {
    const el = document.getElementById('sg'+i);
    el.style.background = i <= s ? colors[s-1] : '#21262d';
  });
  lbl.textContent = p.length > 0 ? labels[s-1] || 'Weak' : '';
  lbl.style.color = colors[s-1] || '#f85149';
}
</script>
</body>
</html>
"""


MAIN_TEMPLATE = BASE_STYLE + """
<nav class="navbar">
  <span class="nav-brand">⚡ NoteFlow</span>
  <div class="nav-links">
    <span style="color:var(--muted);font-size:.8rem">👤 {{ username }}</span>
    <button class="btn-sm" onclick="toggleTheme()" id="themeBtn">🌙</button>
    <a href="/stats"><button class="btn-sm">📊 Stats</button></a>
    <a href="/export/csv"><button class="btn-sm">⬇ CSV</button></a>
    <a href="/export/json"><button class="btn-sm">⬇ JSON</button></a>
    <a href="/logout"><button class="btn-sm">Logout</button></a>
  </div>
</nav>

<div class="container">

  <!-- Add Note Form -->
  <div class="card fade-in" style="margin-bottom:20px">
    <h3 style="font-size:.85rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:14px">✏️ New Note</h3>
    <div class="grid-2" style="margin-bottom:10px">
      <input id="noteTitle" placeholder="Title *">
      <select id="notePriority">
        <option value="High">🔴 High Priority</option>
        <option value="Medium" selected>🟡 Medium Priority</option>
        <option value="Low">🟢 Low Priority</option>
      </select>
    </div>
    <textarea id="noteContent" placeholder="Write your note..." rows="3" style="resize:vertical;margin-bottom:10px" oninput="updateCharCount()"></textarea>
    <div class="grid-3" style="margin-bottom:10px">
      <select id="noteCategory">
        <option>General</option><option>Study</option><option>Work</option>
        <option>Personal</option><option>Important</option><option>Ideas</option>
      </select>
      <input id="noteTags" placeholder="Tags (comma separated)">
      <input id="noteDueDate" type="date" title="Due date (optional)">
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
      <div style="display:flex;gap:8px;align-items:center">
        <span id="charCount" style="font-size:.78rem;color:var(--muted)">0 chars</span>
        <button class="btn-sm" onclick="autoFill()" title="AI: suggest category & summarize">🤖 AI Assist</button>
      </div>
      <button class="btn-primary" onclick="addNote()">+ Add Note</button>
    </div>
  </div>

  <!-- Overdue Alert -->
  <div id="overdueAlert" style="display:none;background:#3b1010;border:1px solid #7f1d1d;border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:.88rem;color:#f87171"></div>

  <!-- Tag Cloud -->
  <div id="tagCloud" style="margin-bottom:16px"></div>

  <!-- Search & Filter -->
  <div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:8px;margin-bottom:16px">
    <input id="searchInput" placeholder="🔍 Search notes..." oninput="filterNotes()">
    <select id="filterPriority" onchange="filterNotes()">
      <option value="">All Priorities</option>
      <option value="High">🔴 High</option><option value="Medium">🟡 Medium</option><option value="Low">🟢 Low</option>
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
      <option value="due_date">By Due Date</option>
      <option value="pinned">Pinned First</option>
    </select>
  </div>

  <!-- Stats bar -->
  <div id="statsBar" style="display:flex;gap:10px;margin-bottom:18px;flex-wrap:wrap"></div>

  <!-- Notes List -->
  <div id="notesList"></div>
  <div id="emptyState" style="display:none;text-align:center;padding:60px 0;color:var(--muted)">
    <div style="font-size:3rem;margin-bottom:10px">📝</div>
    <div>No notes found.</div>
  </div>

</div>
<div class="toast" id="toast"></div>

<!-- Link Note Modal -->
<div id="linkModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;align-items:center;justify-content:center">
  <div class="card" style="width:100%;max-width:420px;margin:20px">
    <h3 style="margin-bottom:12px;font-size:.95rem">🔗 Link Note</h3>
    <select id="linkTarget" style="margin-bottom:12px"></select>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" onclick="confirmLink()">Link</button>
      <button class="btn-sm" onclick="closeLinkModal()">Cancel</button>
    </div>
  </div>
</div>

<script>
let allNotes=[], currentLinkNoteId=null, activeTagFilter=null;

// ── Theme ─────────────────────────────────────────────────────────────────────
function toggleTheme(){
  document.body.classList.toggle('light');
  const l=document.body.classList.contains('light');
  document.getElementById('themeBtn').textContent=l?'☀️':'🌙';
  localStorage.setItem('theme',l?'light':'dark');
}
if(localStorage.getItem('theme')==='light'){document.body.classList.add('light');document.getElementById('themeBtn').textContent='☀️';}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(msg,color='var(--green)'){
  const t=document.getElementById('toast');
  t.innerHTML=msg;t.style.borderLeftColor=color;
  t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2800);
}

function updateCharCount(){
  const len=document.getElementById('noteContent').value.length;
  const el=document.getElementById('charCount');
  el.textContent=len+' chars';
  el.style.color=len>500?'var(--orange)':len>1000?'var(--red)':'var(--muted)';
}

// ── AI Assist ─────────────────────────────────────────────────────────────────
async function autoFill(){
  const content=document.getElementById('noteContent').value.trim();
  if(!content){showToast('Write some content first!','var(--red)');return;}
  showToast('🤖 AI thinking...');
  const res=await fetch('/ai/assist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content})});
  const data=await res.json();
  if(data.category) document.getElementById('noteCategory').value=data.category;
  if(data.summary)  showToast('🤖 AI: '+data.summary.substring(0,80)+'...');
}

// ── Load & Render ─────────────────────────────────────────────────────────────
async function loadNotes(){
  const res=await fetch('/notes');
  const data=await res.json();
  allNotes=data.notes;
  renderTagCloud(allNotes);
  checkOverdue(allNotes);
  updateStatsBar(allNotes);
  filterNotes();
}

function renderTagCloud(notes){
  const tagCount={};
  notes.forEach(n=>{if(n.tags)n.tags.forEach(t=>{tagCount[t]=(tagCount[t]||0)+1;});});
  const el=document.getElementById('tagCloud');
  if(!Object.keys(tagCount).length){el.innerHTML='';return;}
  el.innerHTML='<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center"><span style="font-size:.78rem;color:var(--muted)">🏷️ Tags:</span>'+
    Object.entries(tagCount).sort((a,b)=>b[1]-a[1]).map(([tag,cnt])=>
      `<span class="badge badge-tag" style="cursor:pointer;${activeTagFilter===tag?'background:var(--blue);color:#fff':''}" onclick="filterByTag('${tag}')">${tag} <span style="opacity:.7">${cnt}</span></span>`
    ).join('')+'<span style="font-size:.78rem;color:var(--muted);cursor:pointer;margin-left:4px" onclick="clearTagFilter()">✕ clear</span></div>';
}

function filterByTag(tag){activeTagFilter=tag;filterNotes();renderTagCloud(allNotes);}
function clearTagFilter(){activeTagFilter=null;filterNotes();renderTagCloud(allNotes);}

function checkOverdue(notes){
  const today=new Date().toISOString().split('T')[0];
  const overdue=notes.filter(n=>n.due_date&&n.due_date<today&&n.status==='active');
  const el=document.getElementById('overdueAlert');
  if(overdue.length){
    el.style.display='block';
    el.innerHTML='⚠️ <strong>'+overdue.length+' overdue note'+(overdue.length>1?'s':'')+':</strong> '+overdue.map(n=>'<em>'+escHtml(n.title)+'</em>').join(', ');
  } else el.style.display='none';
}

function updateStatsBar(notes){
  const active=notes.filter(n=>n.status==='active').length;
  const overdue=notes.filter(n=>n.due_date&&n.due_date<new Date().toISOString().split('T')[0]&&n.status==='active').length;
  const pinned=notes.filter(n=>n.pinned).length;
  const high=notes.filter(n=>n.priority==='High'&&n.status==='active').length;
  const archived=notes.filter(n=>n.status==='archived').length;
  document.getElementById('statsBar').innerHTML=[
    ['📝 Active',active,'var(--blue)'],['🔴 High',high,'var(--red)'],
    ['⏰ Overdue',overdue,'var(--orange)'],['📌 Pinned',pinned,'var(--purple)'],
    ['📦 Archived',archived,'var(--muted)'],
  ].map(([l,v,c])=>`<div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:6px 14px;font-size:.8rem;color:var(--muted)">${l}: <strong style="color:${c}">${v}</strong></div>`).join('');
}

function getDueStatus(due_date){
  if(!due_date) return null;
  const today=new Date(); today.setHours(0,0,0,0);
  const due=new Date(due_date);
  const diff=Math.ceil((due-today)/(1000*60*60*24));
  if(diff<0)  return {label:'Overdue by '+Math.abs(diff)+'d',cls:'badge-overdue',cardCls:'overdue-card'};
  if(diff<=3) return {label:'Due in '+diff+'d',cls:'badge-due-soon',cardCls:'due-soon-card'};
  return {label:'Due '+due_date,cls:'badge-cat',cardCls:''};
}

function priorityOrder(p){return p==='High'?0:p==='Medium'?1:2;}

function filterNotes(){
  const search  =document.getElementById('searchInput').value.toLowerCase();
  const priority=document.getElementById('filterPriority').value;
  const category=document.getElementById('filterCategory').value;
  const sortBy  =document.getElementById('sortBy').value;
  if(search) SEARCH_COUNT_local=(SEARCH_COUNT_local||0)+1;

  let filtered=allNotes.filter(n=>{
    const ms=!search||(n.title+' '+n.content+' '+(n.tags||[]).join(' ')).toLowerCase().includes(search);
    const mp=!priority||n.priority===priority;
    const mc=!category||n.category===category;
    const mt=!activeTagFilter||((n.tags||[]).includes(activeTagFilter));
    return ms&&mp&&mc&&mt;
  });

  if(sortBy==='date_asc')       filtered.sort((a,b)=>a.id-b.id);
  else if(sortBy==='date_desc') filtered.sort((a,b)=>b.id-a.id);
  else if(sortBy==='priority')  filtered.sort((a,b)=>priorityOrder(a.priority)-priorityOrder(b.priority));
  else if(sortBy==='pinned')    filtered.sort((a,b)=>b.pinned-a.pinned);
  else if(sortBy==='due_date')  filtered.sort((a,b)=>{
    if(!a.due_date) return 1; if(!b.due_date) return -1;
    return a.due_date.localeCompare(b.due_date);
  });

  renderNotes(filtered, search);
}
let SEARCH_COUNT_local=0;

function renderNotes(notes, search=''){
  const list=document.getElementById('notesList');
  const empty=document.getElementById('emptyState');
  if(!notes.length){list.innerHTML='';empty.style.display='block';return;}
  empty.style.display='none';
  list.innerHTML=notes.map(n=>{
    const dueStatus=getDueStatus(n.due_date);
    const cardClass=[n.pinned?'pinned-card':'',dueStatus?dueStatus.cardCls:''].filter(Boolean).join(' ');
    const titleHl=search?highlight(escHtml(n.title),search):escHtml(n.title);
    const contentHl=search?highlight(escHtml(n.content),search):escHtml(n.content);
    const tagsHtml=(n.tags||[]).map(t=>`<span class="badge badge-tag" style="cursor:pointer" onclick="filterByTag('${t}')">${t}</span>`).join('');
    return `
    <div class="card fade-in ${cardClass}" style="margin-bottom:10px" id="note-${n.id}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
        <div style="flex:1;padding-right:10px">
          <div style="font-weight:600;font-size:.98rem;margin-bottom:6px">
            ${n.pinned?'<span style="color:var(--blue);margin-right:4px">📌</span>':''}${titleHl}
          </div>
          <div style="display:flex;gap:5px;flex-wrap:wrap">
            <span class="badge badge-${n.priority.toLowerCase()}">${n.priority}</span>
            <span class="badge badge-cat">${n.category}</span>
            ${n.status==='archived'?'<span class="badge" style="background:#1e1e2d;color:#a78bfa">Archived</span>':''}
            ${dueStatus?`<span class="badge ${dueStatus.cls}">${dueStatus.label}</span>`:''}
            ${tagsHtml}
          </div>
        </div>
        <div style="display:flex;gap:4px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end">
          <button class="btn-danger" onclick="summarizeNote(${n.id})">🤖</button>
          <button class="btn-danger" onclick="openLinkModal(${n.id})">🔗</button>
          <button class="btn-danger" onclick="pinNote(${n.id})">${n.pinned?'Unpin':'📌'}</button>
          <button class="btn-danger" onclick="archiveNote(${n.id})">${n.status==='archived'?'Restore':'📦'}</button>
          <button class="btn-danger" onclick="deleteNote(${n.id})">🗑</button>
        </div>
      </div>
      <p style="font-size:.88rem;color:var(--muted);line-height:1.6;white-space:pre-wrap;margin-bottom:8px">${contentHl}</p>
      ${n.summary?`<div style="background:var(--bg3);border-left:3px solid var(--blue);padding:8px 12px;border-radius:0 6px 6px 0;font-size:.82rem;color:var(--muted);margin-bottom:8px">🤖 <em>${escHtml(n.summary)}</em></div>`:''}
      ${n.linked_notes&&n.linked_notes.length?`<div style="font-size:.78rem;color:var(--muted);margin-bottom:4px">🔗 Linked: ${n.linked_notes.map(l=>`<span style="color:var(--blue)">${escHtml(l.title)}</span>`).join(', ')}</div>`:''}
      <div style="font-size:.75rem;color:var(--muted);margin-top:6px">🕐 ${n.created_at}${n.due_date?' &nbsp;|&nbsp; 📅 Due: '+n.due_date:''}</div>
    </div>`;
  }).join('');
}

function escHtml(t){return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

// ── CRUD ──────────────────────────────────────────────────────────────────────
async function addNote(){
  const title   =document.getElementById('noteTitle').value.trim();
  const content =document.getElementById('noteContent').value.trim();
  const priority=document.getElementById('notePriority').value;
  const category=document.getElementById('noteCategory').value;
  const tags    =document.getElementById('noteTags').value.split(',').map(t=>t.trim()).filter(Boolean);
  const due_date=document.getElementById('noteDueDate').value||null;
  if(!title)  {showToast('⚠️ Title required!','var(--red)');return;}
  if(!content){showToast('⚠️ Content required!','var(--red)');return;}
  const res=await fetch('/notes',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({title,content,priority,category,tags,due_date})});
  if(res.ok){
    ['noteTitle','noteContent','noteTags','noteDueDate'].forEach(id=>document.getElementById(id).value='');
    document.getElementById('charCount').textContent='0 chars';
    showToast('✅ Note added!');loadNotes();
  }
}

async function deleteNote(id){
  if(!confirm('Delete this note?'))return;
  await fetch('/notes/'+id,{method:'DELETE'});
  showToast('🗑 Deleted!','var(--red)');loadNotes();
}
async function pinNote(id){await fetch('/notes/'+id+'/pin',{method:'PATCH'});showToast('📌 Updated!','var(--orange)');loadNotes();}
async function archiveNote(id){await fetch('/notes/'+id+'/archive',{method:'PATCH'});showToast('📦 Updated!','var(--purple)');loadNotes();}

async function summarizeNote(id){
  showToast('🤖 Summarizing...');
  const res=await fetch('/notes/'+id+'/summarize',{method:'POST'});
  if(res.ok){showToast('🤖 Summary added!','var(--blue)');loadNotes();}
}

// ── Link Modal ────────────────────────────────────────────────────────────────
function openLinkModal(id){
  currentLinkNoteId=id;
  const sel=document.getElementById('linkTarget');
  sel.innerHTML=allNotes.filter(n=>n.id!==id).map(n=>`<option value="${n.id}">${escHtml(n.title)}</option>`).join('');
  const modal=document.getElementById('linkModal');
  modal.style.display='flex';
}
function closeLinkModal(){document.getElementById('linkModal').style.display='none';}
async function confirmLink(){
  const targetId=document.getElementById('linkTarget').value;
  await fetch('/notes/'+currentLinkNoteId+'/link',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({linked_id:parseInt(targetId)})});
  showToast('🔗 Notes linked!','var(--blue)');closeLinkModal();loadNotes();
}

loadNotes();
</script>
"""

STATS_TEMPLATE = BASE_STYLE + """
<nav class="navbar">
  <span class="nav-brand">⚡ NoteFlow</span>
  <div class="nav-links">
    <button class="btn-sm" onclick="toggleTheme()" id="themeBtn">🌙</button>
    <a href="/app"><button class="btn-sm">← Back</button></a>
  </div>
</nav>
<div class="container">
  <h2 style="font-size:1.15rem;font-weight:700;margin-bottom:20px">📊 Analytics Dashboard</h2>

  <!-- Stat Cards -->
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:24px">
    {% for label,val,color in stats_cards %}
    <div class="card" style="text-align:center;padding:14px">
      <div style="font-size:1.8rem;font-weight:700;color:{{ color }}">{{ val }}</div>
      <div style="color:var(--muted);font-size:.78rem;margin-top:3px">{{ label }}</div>
    </div>
    {% endfor %}
  </div>

  <!-- Charts -->
  <div class="grid-2" style="margin-bottom:20px">
    <div class="card">
      <h3 style="font-size:.82rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px">By Category</h3>
      <div id="catChart"></div>
    </div>
    <div class="card">
      <h3 style="font-size:.82rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px">By Priority</h3>
      <div id="priChart"></div>
    </div>
  </div>

  <!-- Productivity Score -->
  <div class="card" style="margin-bottom:20px">
    <h3 style="font-size:.82rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px">🏆 Productivity Score</h3>
    <div style="display:flex;align-items:center;gap:16px">
      <div style="font-size:3rem;font-weight:700;color:var(--green)">{{ score }}%</div>
      <div>
        <div style="font-size:.88rem;color:var(--text);margin-bottom:4px">{{ score_label }}</div>
        <div style="font-size:.78rem;color:var(--muted)">Based on active vs archived notes ratio + weekly activity</div>
        <div style="background:var(--bg3);border-radius:6px;height:8px;margin-top:8px;width:200px">
          <div style="width:{{ score }}%;background:var(--green);height:100%;border-radius:6px;transition:width .6s"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- Activity Heatmap -->
  <div class="card">
    <h3 style="font-size:.82rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px">📅 Activity Heatmap (Last 12 Weeks)</h3>
    <div id="heatmap" style="overflow-x:auto"></div>
    <div style="display:flex;gap:6px;align-items:center;margin-top:10px;font-size:.75rem;color:var(--muted)">
      Less
      {% for shade in ['#1e2d1e','#2d5a2d','#3ecf8e80','#3ecf8e'] %}
      <div style="width:12px;height:12px;background:{{ shade }};border-radius:2px"></div>
      {% endfor %}
      More
    </div>
  </div>
</div>

<script>
function toggleTheme(){
  document.body.classList.toggle('light');
  const l=document.body.classList.contains('light');
  document.getElementById('themeBtn').textContent=l?'☀️':'🌙';
  localStorage.setItem('theme',l?'light':'dark');
}
if(localStorage.getItem('theme')==='light'){document.body.classList.add('light');document.getElementById('themeBtn').textContent='☀️';}

function renderBar(id,data,colors){
  const max=Math.max(...Object.values(data),1);
  document.getElementById(id).innerHTML=Object.entries(data).map(([k,v],i)=>`
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
      <div style="width:90px;font-size:.82rem;color:var(--muted);flex-shrink:0">${k}</div>
      <div style="flex:1;background:var(--bg3);border-radius:4px;height:20px;overflow:hidden">
        <div style="width:${(v/max)*100}%;background:${colors[i%colors.length]};height:100%;border-radius:4px;transition:width .6s"></div>
      </div>
      <div style="width:18px;text-align:right;font-size:.82rem;font-weight:600">${v}</div>
    </div>`).join('');
}
renderBar('catChart',{{ cat_data }},['#4f8ef7','#3ecf8e','#f97316','#a78bfa','#f87171','#fbbf24']);
renderBar('priChart',{{ pri_data }},['#f87171','#fbbf24','#34d399']);

// Heatmap
const heatData={{ heatmap_data }};
const weeks=12, days=7;
const today=new Date(); today.setHours(0,0,0,0);
let cells='<div style="display:flex;gap:3px">';
for(let w=weeks-1;w>=0;w--){
  cells+='<div style="display:flex;flex-direction:column;gap:3px">';
  for(let d=0;d<days;d++){
    const date=new Date(today);
    date.setDate(today.getDate()-(w*7+d));
    const key=date.toISOString().split('T')[0];
    const cnt=heatData[key]||0;
    const alpha=cnt===0?'#1e1e1e':cnt===1?'#1e2d1e':cnt<=3?'#2d5a2d':cnt<=6?'#3ecf8e80':'#3ecf8e';
    cells+=`<div style="width:12px;height:12px;background:${alpha};border-radius:2px" title="${key}: ${cnt} notes"></div>`;
  }
  cells+='</div>';
}
cells+='</div>';
document.getElementById('heatmap').innerHTML=cells;
</script>
"""

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def landing():
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template_string(LANDING_TEMPLATE)

@app.route('/app')
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
        elif password_strength(password) < 2:
            error = 'Password too weak — add numbers or symbols'
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
@limiter.limit("10 per minute")
def login_page():
    error = None
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if user:
            # Check if locked
            if user['locked_until']:
                locked = datetime.fromisoformat(user['locked_until'])
                if datetime.now() < locked:
                    mins = int((locked - datetime.now()).seconds / 60) + 1
                    error = f'Account locked. Try again in {mins} minute(s).'
                    conn.close()
                    LOGIN_COUNT.labels(status='locked').inc()
                    return render_template_string(AUTH_TEMPLATE, subtitle='Welcome back',
                        error=error, show_username=True, show_email=False, action='Login',
                        link_text="Don't have an account?", link_url='/register', link_label='Register')
            if user['password'] == hash_password(password):
                conn.execute('UPDATE users SET login_attempts=0, locked_until=NULL WHERE id=?', (user['id'],))
                conn.commit(); conn.close()
                session['user_id']  = user['id']
                session['username'] = user['username']
                LOGIN_COUNT.labels(status='success').inc()
                return redirect(url_for('index'))
            else:
                attempts = user['login_attempts'] + 1
                locked_until = None
                if attempts >= 5:
                    locked_until = (datetime.now() + timedelta(minutes=15)).isoformat()
                    error = 'Too many failed attempts. Account locked for 15 minutes.'
                else:
                    error = f'Invalid password. {5 - attempts} attempts remaining.'
                conn.execute('UPDATE users SET login_attempts=?, locked_until=? WHERE id=?',
                             (attempts, locked_until, user['id']))
                conn.commit(); conn.close()
                LOGIN_COUNT.labels(status='failed').inc()
        else:
            conn.close()
            error = 'User not found'
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
    uid   = session['user_id']
    notes = conn.execute(
        'SELECT * FROM notes WHERE user_id=? ORDER BY pinned DESC, id DESC', (uid,)).fetchall()
    result = []
    for n in notes:
        note = dict(n)
        note['tags'] = get_note_tags(conn, n['id'])
        linked = conn.execute(
            'SELECT n.id, n.title FROM notes n JOIN note_links nl ON n.id=nl.linked_id WHERE nl.note_id=?',
            (n['id'],)).fetchall()
        note['linked_notes'] = [dict(l) for l in linked]
        result.append(note)
    conn.close()
    return jsonify({'notes': result, 'count': len(result)})

@app.route('/notes', methods=['POST'])
@login_required
def add_note():
    REQUEST_COUNT.labels(method='POST', endpoint='/notes').inc()
    data = request.get_json()
    if not data or not data.get('title','').strip() or not data.get('content','').strip():
        return jsonify({'error': 'Title and content required'}), 400
    conn = get_db()
    cur  = conn.execute(
        'INSERT INTO notes (user_id,title,content,category,priority,due_date) VALUES (?,?,?,?,?,?)',
        (session['user_id'], data['title'].strip(), data['content'].strip(),
         data.get('category','General'), data.get('priority','Medium'),
         data.get('due_date') or None))
    note_id = cur.lastrowid
    # Handle tags
    for tag_name in data.get('tags', []):
        tag_name = tag_name.strip().lower()
        if not tag_name: continue
        conn.execute('INSERT OR IGNORE INTO tags (user_id,name) VALUES (?,?)', (session['user_id'], tag_name))
        tag = conn.execute('SELECT id FROM tags WHERE user_id=? AND name=?', (session['user_id'], tag_name)).fetchone()
        conn.execute('INSERT OR IGNORE INTO note_tags (note_id,tag_id) VALUES (?,?)', (note_id, tag['id']))
    conn.commit(); conn.close()
    NOTE_OPS.labels(operation='add').inc()
    update_metrics()
    return jsonify({'message': 'Note added', 'id': note_id}), 201

@app.route('/notes/<int:note_id>', methods=['DELETE'])
@login_required
def delete_note(note_id):
    REQUEST_COUNT.labels(method='DELETE', endpoint='/notes/id').inc()
    conn   = get_db()
    result = conn.execute('DELETE FROM notes WHERE id=? AND user_id=?', (note_id, session['user_id']))
    conn.execute('DELETE FROM note_tags WHERE note_id=?', (note_id,))
    conn.execute('DELETE FROM note_links WHERE note_id=? OR linked_id=?', (note_id, note_id))
    conn.commit(); conn.close()
    if result.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    NOTE_OPS.labels(operation='delete').inc()
    update_metrics()
    return jsonify({'message': 'Deleted'}), 200

@app.route('/notes/<int:note_id>/pin', methods=['PATCH'])
@login_required
def pin_note(note_id):
    conn = get_db()
    note = conn.execute('SELECT pinned FROM notes WHERE id=? AND user_id=?', (note_id, session['user_id'])).fetchone()
    if not note: conn.close(); return jsonify({'error': 'Not found'}), 404
    conn.execute('UPDATE notes SET pinned=? WHERE id=?', (0 if note['pinned'] else 1, note_id))
    conn.commit(); conn.close()
    NOTE_OPS.labels(operation='pin').inc()
    return jsonify({'message': 'Updated'}), 200

@app.route('/notes/<int:note_id>/archive', methods=['PATCH'])
@login_required
def archive_note(note_id):
    conn = get_db()
    note = conn.execute('SELECT status FROM notes WHERE id=? AND user_id=?', (note_id, session['user_id'])).fetchone()
    if not note: conn.close(); return jsonify({'error': 'Not found'}), 404
    new_status = 'active' if note['status'] == 'archived' else 'archived'
    conn.execute('UPDATE notes SET status=? WHERE id=?', (new_status, note_id))
    conn.commit(); conn.close()
    NOTE_OPS.labels(operation='archive').inc()
    return jsonify({'message': 'Updated'}), 200

@app.route('/notes/<int:note_id>/summarize', methods=['POST'])
@login_required
def summarize_note(note_id):
    conn = get_db()
    note = conn.execute('SELECT * FROM notes WHERE id=? AND user_id=?', (note_id, session['user_id'])).fetchone()
    if not note: conn.close(); return jsonify({'error': 'Not found'}), 404
    summary = ai_summarize(note['content'])
    conn.execute('UPDATE notes SET summary=? WHERE id=?', (summary, note_id))
    conn.commit(); conn.close()
    NOTE_OPS.labels(operation='summarize').inc()
    return jsonify({'message': 'Summarized', 'summary': summary}), 200

@app.route('/notes/<int:note_id>/link', methods=['POST'])
@login_required
def link_note(note_id):
    data = request.get_json()
    linked_id = data.get('linked_id')
    if not linked_id or linked_id == note_id:
        return jsonify({'error': 'Invalid link'}), 400
    conn = get_db()
    conn.execute('INSERT OR IGNORE INTO note_links (note_id,linked_id) VALUES (?,?)', (note_id, linked_id))
    conn.execute('INSERT OR IGNORE INTO note_links (note_id,linked_id) VALUES (?,?)', (linked_id, note_id))
    conn.commit(); conn.close()
    NOTE_OPS.labels(operation='link').inc()
    return jsonify({'message': 'Linked'}), 200

# ── AI Assist ─────────────────────────────────────────────────────────────────
@app.route('/ai/assist', methods=['POST'])
@login_required
def ai_assist():
    data    = request.get_json()
    content = data.get('content','')
    return jsonify({
        'category': ai_suggest_category('', content),
        'summary':  ai_summarize(content)
    })

# ── Stats ─────────────────────────────────────────────────────────────────────
@app.route('/stats')
@login_required
def stats():
    REQUEST_COUNT.labels(method='GET', endpoint='/stats').inc()
    conn     = get_db()
    uid      = session['user_id']
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    today    = datetime.now().strftime('%Y-%m-%d')

    total    = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND status='active'", (uid,)).fetchone()[0]
    high     = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND priority='High' AND status='active'", (uid,)).fetchone()[0]
    medium   = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND priority='Medium' AND status='active'", (uid,)).fetchone()[0]
    low      = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND priority='Low' AND status='active'", (uid,)).fetchone()[0]
    archived = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND status='archived'", (uid,)).fetchone()[0]
    pinned   = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND pinned=1", (uid,)).fetchone()[0]
    week     = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND date(created_at)>=?", (uid, week_ago)).fetchone()[0]
    overdue  = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=? AND due_date<? AND due_date IS NOT NULL AND status='active'", (uid, today)).fetchone()[0]
    tag_count= conn.execute("SELECT COUNT(*) FROM tags WHERE user_id=?", (uid,)).fetchone()[0]
    linked   = conn.execute("SELECT COUNT(DISTINCT note_id) FROM note_links nl JOIN notes n ON nl.note_id=n.id WHERE n.user_id=?", (uid,)).fetchone()[0]

    cats_raw = conn.execute("SELECT category, COUNT(*) cnt FROM notes WHERE user_id=? AND status='active' GROUP BY category", (uid,)).fetchall()
    heatmap  = get_heatmap_data(conn, uid)
    conn.close()

    # Productivity score
    total_all = total + archived
    score = 0
    if total_all > 0:
        score = min(100, int((archived / total_all) * 50 + (week / max(total_all, 1)) * 50 * 7 + 20))
    score_labels = {range(0,40): 'Keep going! 💪', range(40,70): 'Good progress! 📈', range(70,90): 'Great work! 🌟', range(90,101): 'Outstanding! 🏆'}
    score_label  = next((v for k, v in score_labels.items() if score in k), 'Keep going! 💪')

    stats_cards = [
        ('Total Active', total, 'var(--blue)'), ('High Priority', high, 'var(--red)'),
        ('Medium', medium, 'var(--yellow)'),    ('Low Priority', low, 'var(--green)'),
        ('Archived', archived, 'var(--purple)'),('Pinned', pinned, 'var(--orange)'),
        ('This Week', week, 'var(--blue)'),     ('Overdue', overdue, 'var(--red)'),
        ('Tags Used', tag_count, 'var(--blue)'),('Linked Notes', linked, 'var(--green)'),
    ]
    cat_data = json.dumps({r['category']: r['cnt'] for r in cats_raw})
    pri_data = json.dumps({'High': high, 'Medium': medium, 'Low': low})
    return render_template_string(STATS_TEMPLATE,
        stats_cards=stats_cards, cat_data=cat_data, pri_data=pri_data,
        score=score, score_label=score_label,
        heatmap_data=json.dumps(heatmap))

# ── Export ────────────────────────────────────────────────────────────────────
@app.route('/export/csv')
@login_required
def export_csv():
    conn  = get_db()
    uid   = session['user_id']
    notes = conn.execute('SELECT * FROM notes WHERE user_id=?', (uid,)).fetchall()
    result = []
    for n in notes:
        row = dict(n)
        row['tags'] = ','.join(get_note_tags(conn, n['id']))
        result.append(row)
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Title','Content','Summary','Category','Priority','Status','Pinned','Tags','Due Date','Created At'])
    for n in result:
        writer.writerow([n['title'], n['content'], n.get('summary',''), n['category'],
                        n['priority'], n['status'], n['pinned'], n['tags'],
                        n.get('due_date',''), n['created_at']])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv',
                     as_attachment=True, download_name='noteflow_export.csv')

@app.route('/export/json')
@login_required
def export_json():
    conn  = get_db()
    uid   = session['user_id']
    notes = conn.execute('SELECT * FROM notes WHERE user_id=?', (uid,)).fetchall()
    result = []
    for n in notes:
        row = dict(n)
        row['tags'] = get_note_tags(conn, n['id'])
        result.append(row)
    conn.close()
    data = json.dumps(result, indent=2)
    return send_file(io.BytesIO(data.encode()), mimetype='application/json',
                     as_attachment=True, download_name='noteflow_export.json')

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
