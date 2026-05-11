import pytest
import json
from app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_home_page(client):
    res = client.get('/')
    assert res.status_code == 200

def test_get_notes_empty(client):
    res = client.get('/notes')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert 'notes' in data

def test_add_note(client):
    res = client.post('/notes',
        data=json.dumps({'content': 'Test note'}),
        content_type='application/json')
    assert res.status_code == 201
    data = json.loads(res.data)
    assert data['note']['content'] == 'Test note'

def test_add_note_empty_content(client):
    res = client.post('/notes',
        data=json.dumps({'content': ''}),
        content_type='application/json')
    assert res.status_code == 400

def test_delete_note(client):
    # Add first
    res = client.post('/notes',
        data=json.dumps({'content': 'To delete'}),
        content_type='application/json')
    note_id = json.loads(res.data)['note']['id']
    # Delete
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
