# NoteFlow — Smart Note Management (Flask + DevOps)

NoteFlow is a web-based notes platform with AI-assisted summaries, smart tagging, due date tracking, and a production-grade DevOps toolchain (Docker, Kubernetes, Jenkins, Prometheus, Grafana).

## Features

- **AI summaries & auto-categorization** (Gemini API optional; fallback extractive summarizer if not configured)
- **Smart tagging** (tag cloud + filtering)
- **Due dates & overdue alerts**
- **Pin / Archive** notes
- **Link notes** to build a lightweight relationship graph
- **Analytics dashboard** (category/priority bars + productivity score + activity heatmap)
- **Export** your notes as **CSV** or **JSON**
- **Security**: rate limiting, login lockout, password strength checks
- **Observability**: Prometheus metrics at `/metrics`

## Quick start (local development)

### 1) Install dependencies

```bash
pip install -r requirements.txt
```

### 2) Initialize the database

```bash
python init_db.py
```

### 3) Run the app

```bash
python app.py
```

Open:

- App: http://localhost:5000
- Metrics: http://localhost:5000/metrics

## Docker

Build and run using the provided multi-stage Dockerfile:

```bash
docker build -t noteflow-app:latest .
docker run -p 5000:5000 -e SECRET_KEY=noteflow-prod-secret-2025 noteflow-app:latest
```

Notes:
- The container uses `/app/data` for persistence when configured via Docker Compose.

## Docker Compose

```bash
docker compose up --build
```

Then open:

- http://localhost:5000

## Kubernetes

Manifests are under `k8s/`.

Key files:
- `k8s/deployment.yaml`
- `k8s/service.yaml`
- `k8s/secret.yaml`
- `k8s/prometheus.yaml`
- `k8s/grafana.yaml`

Typical flow:

```bash
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/deployment.yaml
# (optional) monitoring stack
kubectl apply -f k8s/prometheus.yaml
kubectl apply -f k8s/grafana.yaml
```

## Environment variables

- `SECRET_KEY` (required in production) — used to sign Flask sessions
- `GEMINI_API_KEY` (optional) — enables Gemini-based summary generation
- `FLASK_ENV` — set to `production` for production mode

## Prometheus metrics

- **HTTP metrics** are exposed at: `/metrics`

The app also provides app-specific counters/gauges (requests, notes, users, CRUD ops, logins, searches).

## Tests

```bash
pytest -q
```

## Project files

- `app.py` — Flask app + HTML templates + REST endpoints
- `init_db.py` — DB schema initialization
- `Dockerfile` — multi-stage build (builder + runtime)
- `docker-compose.yml` — local persistent container setup
- `Jenkinsfile` — CI/CD pipeline
- `k8s/` — Kubernetes deployments/services/secrets + monitoring

