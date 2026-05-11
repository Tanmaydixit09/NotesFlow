import os
import json
import sqlite3
import pytest
from app import app, init_db, hash_password

TEST_DB = 'test_noteflow.db'

def init_test_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    app.config['TESTING'] = True
    app.config['DATABASE'] = TEST_DB
    init_db()

def create_test_user():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.execute(
        'INSERT INTO users (username,email,password) VALUES (?,?,?)',
        ('testuser', 'test@example.com', hash_password('secret123')))
    conn.commit()
    user_id = conn.execute('SELECT id FROM users WHERE username=?', ('testuser',)).fetchone()[0]
    conn.close()
    return user_id

@pytest.fixture
def client():
    init_test_db()
    with app.test_client() as client:
        user_id = create_test_user()
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'testuser'
        yield client
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

def test_home_page(client):
    res = client.get('/')
    assert res.status_code == 200

def test_get_notes_empty(client):
    res = client.get('/notes')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert 'notes' in data
    assert data['count'] == 0

def test_add_note(client):
    res = client.post('/notes',
        data=json.dumps({'title': 'Test note', 'content': 'Test note'}),
        content_type='application/json')
    assert res.status_code == 201
    data = json.loads(res.data)
    assert data['note']['content'] == 'Test note'

def test_add_note_empty_content(client):
    res = client.post('/notes',
        data=json.dumps({'title': 'Empty', 'content': ''}),
        content_type='application/json')
    assert res.status_code == 400

def test_delete_note(client):
    res = client.post('/notes',
        data=json.dumps({'title': 'To delete', 'content': 'To delete'}),
        content_type='application/json')
    note_id = json.loads(res.data)['note']['id']
    res = client.delete(f'/notes/{note_id}')
    assert res.status_code == 200

def test_delete_nonexistent_note(client):
    res = client.delete('/notes/99999')
    assert res.status_code == 404

def test_health_endpoint(client):
    res = client.get('/health')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['status'] == 'healthy'

def test_metrics_endpoint(client):
    res = client.get('/metrics')
    assert res.status_code == 200
    assert b'noteflow_requests_total' in res.data
