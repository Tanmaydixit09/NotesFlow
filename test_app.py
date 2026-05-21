import pytest
import json
import os
import tempfile

# Use temp database for testing — avoids conflicts
TEST_DB = tempfile.mktemp(suffix='.db')

import app as app_module
app_module.DB = TEST_DB

@pytest.fixture(autouse=True)
def setup_db():
    app_module.DB = TEST_DB
    app_module.init_db()
    yield
    try:
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
    except:
        pass

@pytest.fixture
def client():
    app_module.app.config['TESTING'] = True
    app_module.app.config['SECRET_KEY'] = 'test-secret'
    app_module.app.config['RATELIMIT_ENABLED'] = False
    with app_module.app.test_client() as client:
        yield client

def register_and_login(client, username='testuser', password='Test@1234'):
    client.post('/register', data={
        'username': username,
        'email': f'{username}@test.com',
        'password': password
    }, follow_redirects=True)
    client.post('/login', data={
        'username': username,
        'password': password
    }, follow_redirects=True)

# ── Tests ──────────────────────────────────────────────────────────────────────

def test_landing_page(client):
    res = client.get('/')
    assert res.status_code == 200
    assert b'NoteFlow' in res.data

def test_register_page_loads(client):
    res = client.get('/register')
    assert res.status_code == 200

def test_login_page_loads(client):
    res = client.get('/login')
    assert res.status_code == 200

def test_register_user(client):
    res = client.post('/register', data={
        'username': 'tanmay',
        'email': 'tanmay@test.com',
        'password': 'Test@1234'
    }, follow_redirects=True)
    assert res.status_code == 200

def test_login_user(client):
    register_and_login(client)
    res = client.get('/app', follow_redirects=True)
    assert res.status_code == 200

def test_get_notes_authenticated(client):
    register_and_login(client)
    res = client.get('/notes')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert 'notes' in data

def test_add_note(client):
    register_and_login(client)
    res = client.post('/notes',
        data=json.dumps({
            'title': 'Test Note',
            'content': 'Test content',
            'priority': 'High',
            'category': 'Work'
        }),
        content_type='application/json')
    assert res.status_code == 201

def test_add_note_missing_title(client):
    register_and_login(client)
    res = client.post('/notes',
        data=json.dumps({'title': '', 'content': 'some content'}),
        content_type='application/json')
    assert res.status_code == 400

def test_delete_note(client):
    register_and_login(client)
    client.post('/notes',
        data=json.dumps({'title': 'To Delete', 'content': 'delete me'}),
        content_type='application/json')
    notes_res = client.get('/notes')
    notes = json.loads(notes_res.data)['notes']
    assert len(notes) > 0
    note_id = notes[0]['id']
    res = client.delete(f'/notes/{note_id}')
    assert res.status_code == 200

def test_health_endpoint(client):
    res = client.get('/health')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['status'] == 'healthy'

def test_metrics_endpoint(client):
    res = client.get('/metrics')
    assert res.status_code == 200
    assert b'noteflow_requests_total' in res.data

def test_unauthenticated_notes_redirect(client):
    res = client.get('/notes')
    assert res.status_code == 302
