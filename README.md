# ⚡ NoteFlow — DevOps-Driven Notes Management System

> **INT377 — Cloud Computing and DevOps Essentials**  
> Lovely Professional University | Session 2025-26

---

## 📌 Project Overview

NoteFlow is a full-stack notes management web application deployed using a complete, production-grade DevOps pipeline. The project demonstrates end-to-end software delivery — from source code to live deployment and monitoring.

The application is built with Python Flask and includes AI-powered summaries, user authentication, smart tagging, due dates, note linking, analytics dashboard, and CSV/JSON export. The DevOps pipeline covers containerization, CI/CD automation, container orchestration, infrastructure as code, and real-time monitoring.

---

## 🚀 Tech Stack

| Category | Tool | Purpose |
|----------|------|---------|
| Application | Python Flask | Backend web framework |
| Database | SQLite | Persistent data storage |
| Version Control | Git + GitHub | Source code management |
| Containerization | Docker | Package app into containers |
| CI/CD | Jenkins | Automated build, test, deploy |
| Orchestration | Kubernetes (Minikube) | Container deployment & scaling |
| IaC | Terraform | AWS EC2 infrastructure provisioning |
| Monitoring | Prometheus | Metrics collection |
| Visualization | Grafana | Real-time dashboards |

---

## ✨ Application Features

- 🔐 **User Authentication** — Register, login, logout with session management
- 🔒 **Security** — Rate limiting, account lockout after 5 failed attempts, password strength meter
- 🗄️ **SQLite Database** — Persistent storage with 5 tables (users, notes, tags, note_tags, note_links)
- 🤖 **AI Assist** — Auto-summarize notes and suggest categories
- 🏷️ **Smart Tagging** — Custom tags, tag cloud, filter by tag
- 📅 **Due Dates** — Set deadlines, overdue alerts in red
- 🔗 **Note Linking** — Link related notes together
- 📊 **Analytics Dashboard** — GitHub-style heatmap, productivity score, charts
- 📌 **Pin & Archive** — Pin important notes, archive instead of delete
- ⬇️ **Export** — Download all notes as CSV or JSON
- 🌙 **Dark/Light Mode** — Toggle with memory
- 📈 **Prometheus Metrics** — Custom metrics per endpoint and operation

---

## 🏗️ Project Structure

```
NotesFlow/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── Dockerfile             # Multi-stage Docker build
├── docker-compose.yml     # Local Docker setup
├── Jenkinsfile            # CI/CD pipeline definition
├── test_app.py            # Unit tests (12 tests)
├── .dockerignore          # Docker ignore rules
├── k8s/
│   ├── deployment.yaml    # Kubernetes deployment (2 replicas)
│   ├── service.yaml       # Kubernetes NodePort service
│   ├── secret.yaml        # Kubernetes secrets
│   ├── prometheus.yaml    # Prometheus deployment + config
│   └── grafana.yaml       # Grafana deployment
└── terraform/
    ├── main.tf            # AWS EC2 + Security Group + EIP
    ├── variables.tf       # Input variables
    └── outputs.tf         # Output values (IPs, URLs)
```

---

## ⚙️ CI/CD Pipeline

Every `git push` to the `main` branch triggers the Jenkins pipeline:

```
Code Push → GitHub
     ↓
Jenkins Webhook Triggered
     ↓
Stage 1: Checkout — Pull latest code
     ↓
Stage 2: Install Dependencies — pip install
     ↓
Stage 3: Run Tests — pytest (12/12 tests)
     ↓
Stage 4: Docker Build — Multi-stage image
     ↓
Stage 5: Docker Push — Push to Docker Hub
     ↓
Stage 6: K8s Deploy — kubectl apply + rollout
     ↓
App Live on Kubernetes ✅
```

---

## 🐳 Docker

**Multi-stage build:**
- **Stage 1 (Builder):** Install gcc + Python dependencies
- **Stage 2 (Runtime):** Copy only what's needed — smaller, secure image

```bash
# Build image
docker build -t noteflow-app .

# Run container
docker run -p 5000:5000 -e SECRET_KEY=your-secret noteflow-app
```

**Docker Hub:** `tanmaydixit09/noteflow-app:latest`

---

## ☸️ Kubernetes

Deployed on Minikube with 2 replicas, health checks, resource limits, and secrets management.

```bash
# Apply all configs
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# Access app
minikube service noteflow-service --url

# Check pods
kubectl get pods
```

---

## 📈 Monitoring

**Prometheus** scrapes metrics from `/metrics` endpoint every 15 seconds.

**Custom metrics:**
- `noteflow_requests_total` — HTTP requests per endpoint
- `noteflow_notes_total` — Active notes count
- `noteflow_users_total` — Registered users
- `noteflow_note_operations_total` — CRUD operations

**Grafana Dashboard:** 4 panels showing live data

```bash
# Access Prometheus
minikube service prometheus-service --url

# Access Grafana (admin/admin123)
minikube service grafana-service --url
```

---

## 🏗️ Terraform (IaC)

Provisions AWS EC2 instance for production deployment.

```bash
cd terraform/
terraform init
terraform plan
terraform apply
```

**Creates:**
- EC2 `t2.medium` instance (Ubuntu 22.04) in Mumbai region
- Security group with ports 5000, 30500, 30300, 30900
- Elastic IP for static access
- Auto-installs Docker, kubectl, Minikube on startup

---

## 🧪 Running Tests

```bash
pip install pytest
pytest test_app.py -v
```

**12 tests covering:**
- Landing page, Register, Login
- Add note, Get notes, Delete note
- Health endpoint, Metrics endpoint
- Authentication redirect

---

## 📋 Syllabus Coverage

| Unit | Topic | Implementation |
|------|-------|---------------|
| Unit I | Git & DevOps | GitHub repo, branching, commits |
| Unit II | Docker & Kubernetes | Multi-stage Dockerfile, K8s Deployment |
| Unit III | Terraform IaC | AWS EC2 provisioning scripts |
| Unit IV | Jenkins CI/CD | Automated 6-stage pipeline |
| Unit V | Prometheus + Grafana | Live monitoring dashboard |
| Unit VI | DevSecOps | Rate limiting, secrets, image security |

---

## 👨‍💻 Author

**Tanmay Dixit**  
Bachelor of Technology — Computer Science and Engineering  
Lovely Professional University | Section: 2OM58  
GitHub: [@Tanmaydixit09](https://github.com/Tanmaydixit09)

---

*NoteFlow — Built with Flask, Docker, Kubernetes, Jenkins, Terraform, Prometheus & Grafana*
