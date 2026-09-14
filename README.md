<!-- ============================================================ -->
<!--                    HEADER BANNER                             -->
<!-- ============================================================ -->

<div align="center">

# ⚙️ InfraForge

### *GitOps-driven, event-driven infrastructure automation platform*

**Transform self-service infrastructure requests into secure, reproducible, and observable infrastructure.**

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Terraform](https://img.shields.io/badge/Terraform-1.7-7B42BC?style=for-the-badge&logo=terraform&logoColor=white)](https://terraform.io)
[![Ansible](https://img.shields.io/badge/Ansible-9-EE0000?style=for-the-badge&logo=ansible&logoColor=white)](https://ansible.com)

<!-- ============================================================ -->
<!--              BUTTONS — VIEW ON GITLAB                        -->
<!-- ============================================================ -->

### 🚀 Links

[![View on GitLab](https://img.shields.io/badge/View_on_GitLab-FC6D26?style=for-the-badge&logo=gitlab&logoColor=white)](https://gitlab.com/yannassiri26/infraforge.git)
[![Documentation](https://img.shields.io/badge/Documentation-4285F4?style=for-the-badge&logo=readthedocs&logoColor=white)](docs/)

**👉 [**View the full GitLab project →**](https://gitlab.com/yannassiri26/infraforge.git)**

</div>

---

## 📖 Table of Contents

- [Overview](#-overview)
- [Architecture](#-architecture)
- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Request Workflow](#-request-workflow)
- [Quick Start](#-quick-start)
- [API Reference](#-api-reference)
- [Project Structure](#-project-structure)
- [Screenshots](#-screenshots)
- [Security](#-security)
- [Documentation](#-documentation)
- [Author](#-author)

---

## 🎯 Overview

**InfraForge** is an **event-driven infrastructure automation platform** that demonstrates how modern infrastructure can be managed through code, policies, and Git — instead of manual operations and tickets.

It's designed to mirror the architecture of a university campus infrastructure platform, similar to what **TU Delft's ICT Directorate** is building.

### The Problem

Traditional infrastructure management is slow, error-prone, and hard to audit:

```
Researcher needs a VM
    ↓
Email IT helpdesk
    ↓
Wait 3-7 days
    ↓
Manual provisioning by an engineer
    ↓
Documentation drifts from reality
```

### The Solution

InfraForge automates the entire lifecycle:

```
Researcher fills a form
    ↓
Policy validation (OPA) → Source of truth (NetBox)
    ↓
Code generation (Terraform + Ansible) → Git commit
    ↓
CI/CD pipeline → Real VM provisioned → Configured → Monitored
    ↓
Ready in minutes, fully auditable
```

---

## 🏗️ Architecture

<p align="center">
  <img width="8168" height="2584" alt="Image" src="https://github.com/user-attachments/assets/18d3bcd8-a084-4f22-9134-e4ebb22fb7cb" />
</p>

## 🏗️ Data Flow
<p>
    <img width="7642" height="992" alt="Image" src="https://github.com/user-attachments/assets/881c9b65-086e-480a-b529-2ff4d46b91bd" />
</p>

<p align="center">
  <a href="architecture/diagrams/high-level.d2">
    <img src="https://img.shields.io/badge/View_Source-D2-FF6B6B?style=flat-square" alt="D2 Source"/>
  </a>
</p>

### Core Components

| Component | Role | Technology |
|-----------|------|------------|
| **Frontend** | Self-service portal | React 18, Vite, Tailwind CSS |
| **Backend** | REST API + WebSocket + orchestration | Python 3.11, FastAPI |
| **Database** | Persistent state | PostgreSQL 15 |
| **Source of Truth** | Infrastructure inventory | NetBox |
| **Event Bus** | Async communication | NATS |
| **Policy Engine** | Compliance enforcement | Open Policy Agent (OPA) |
| **CI/CD** | Pipeline automation | GitLab CI |
| **IaC** | Provisioning + config | Terraform, Ansible |
| **Virtualization** | Real VM provisioning | KVM / Libvirt |
| **Monitoring** | Metrics + dashboards | Prometheus, Grafana |

---

## ✨ Features

### 🎛️ Self-Service Portal
- Researchers request infrastructure without tickets
- Real-time progress via WebSocket
- Policy feedback as they type

### 🔐 Policy-as-Code
- OPA enforces compliance automatically
- Knowledge security requirements built-in
- Network isolation for sensitive data

### 📚 Source of Truth
- NetBox tracks every VM, IP, and VLAN
- Single source of truth for infrastructure
- Fully auditable

### 📝 Infrastructure as Code
- Auto-generated Terraform + Ansible
- Version-controlled, reviewable
- Reproducible deployments

### 🚀 GitOps Workflow
- Everything goes through Git
- Peer review via Merge Requests
- Full audit trail

### 🔄 CI/CD Pipeline
- 8-stage GitLab pipeline
- Validation → Security → Plan → Apply → Configure → Verify
- Manual approval gates

### 🖥️ Real Provisioning
- Actual KVM VMs created on your machine
- No simulation, no mocking
- Working end-to-end

### 📊 Observability
- Prometheus metrics
- Grafana dashboards
- SLO tracking

### 🕵️ Drift Detection
- Compares desired vs actual state
- Alerts on configuration changes
- Automated remediation

---

## 🛠️ Tech Stack

<div align="center">

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | React 18 + Vite + Tailwind CSS | User interface |
| **Backend** | Python 3.11 + FastAPI | REST + WebSocket |
| **Database** | PostgreSQL 15 | Persistence |
| **Source of Truth** | NetBox 4.0 | Infrastructure inventory |
| **Event Bus** | NATS | Async events |
| **Policy** | Open Policy Agent | Compliance |
| **CI/CD** | GitLab CI | Pipeline automation |
| **Provisioning** | Terraform + Libvirt | VM creation |
| **Configuration** | Ansible | Post-provisioning |
| **Monitoring** | Prometheus + Grafana | Observability |

</div>

---

## 🔄 Request Workflow

<p align="center"> 
  <img width="3538" height="4208" alt="Image" src="https://github.com/user-attachments/assets/8162003b-2e0c-424e-bc97-5e8ae9394196" />
</p>

```text
1. User submits request via self-service portal
2. OPA validates policies (compliance, security)
3. NetBox registers VM (source of truth)
4. Terraform + Ansible code generated
5. GitLab branch + Merge Request created
6. CI/CD pipeline triggered
7. Terraform provisions REAL KVM VM
8. Ansible configures the VM
9. Monitoring enabled
10. WebSocket updates user in real-time
11. Infrastructure ready!
```

---

## 🚀 Quick Start

### Prerequisites

```bash
# Required
- Python 3.11+
- Node.js 18+
- PostgreSQL 15+
- Docker + Docker Compose
- KVM / Libvirt

# Optional (for full functionality)
- Terraform 1.7+
- Ansible 9+
- D2 (for architecture diagrams)
```

### Installation

```bash
# Clone the repository
git clone https://gitlab.com/Yann-Assiri/infraforge.git
cd infraforge

# Set up environment
cp .env.example .env
# Edit .env with your tokens (NetBox, GitLab)

# Check dependencies
make check-deps

# Start infrastructure services
make dev-up

# Set up the backend
cd orchestrator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd ..

# Set up the frontend
cd frontend
npm install
cd ..
```

### Running

```bash
# Terminal 1: Backend
make backend

# Terminal 2: Frontend
make frontend
```

### Access Services

| Service | URL | Credentials |
|---------|-----|-------------|
| 🌐 Frontend | http://localhost:3000 | See users below |
| 🔧 Backend API | http://localhost:8001/docs | — |
| 📚 NetBox | http://localhost:8000 | admin / Admin123! |
| 📊 Grafana | http://localhost:3001 | admin / admin |
| 📈 Prometheus | http://localhost:9090 | — |

### Default Users

| Role | Email | Password |
|------|-------|----------|
| Admin | admin@tudelft.nl | Admin123! |
| Engineer | engineer@tudelft.nl | Engineer123! |
| Researcher | researcher@tudelft.nl | Researcher123! |

---

## 📡 API Reference

### Authentication
```
POST   /api/auth/login          Login
POST   /api/auth/register       Register
POST   /api/auth/logout         Logout
GET    /api/auth/me             Current user
```

### Requests
```
POST   /api/requests            Create request
GET    /api/requests            List requests
GET    /api/requests/{id}       Get request
GET    /api/requests/{id}/code  Get generated code
```

### Infrastructure
```
GET    /api/infrastructure      List infrastructure
GET    /api/infrastructure/{id} Get infrastructure
```

### NetBox
```
GET    /api/netbox/health       Connection status
GET    /api/netbox/sites        List sites
GET    /api/netbox/vlans        List VLANs
GET    /api/netbox/vms          List VMs
GET    /api/netbox/ip-addresses List IPs
```

### Policies
```
GET    /api/policies            List policies
POST   /api/policies/validate   Validate request
```

### Pipelines
```
POST   /api/pipeline/trigger    Trigger pipeline
GET    /api/pipeline            Pipeline history
GET    /api/pipeline/{id}       Pipeline status
```

### Monitoring
```
GET    /api/monitoring/health   Health check
GET    /api/monitoring/metrics  Metrics
GET    /api/monitoring/slos     SLOs
GET    /api/monitoring/alerts   Alerts
```

---

## 📁 Project Structure

```
infraforge/
├── README.md                    ← You are here
├── LICENSE
├── Makefile                     ← Run `make help` for commands
├── docker-compose.yml
│
├── architecture/                ← D2 architecture diagrams
│   ├── high-level.d2
│   ├── sequence.d2
│   ├── data-flow.d2
│   └── diagrams/                ← Rendered PNG/SVG
│
├── frontend/                    ← React application
│   ├── src/
│   │   ├── api/                 ← API client
│   │   ├── components/          ← UI components
│   │   ├── pages/               ← Route pages
│   │   ├── hooks/               ← Custom hooks
│   │   └── store/               ← State management
│   └── package.json
│
├── orchestrator/                ← FastAPI backend
│   ├── app/
│   │   ├── api/                 ← REST routes
│   │   ├── services/            ← Business logic
│   │   ├── models/              ← Data models
│   │   ├── events/              ← NATS producer/consumer
│   │   └── websocket/           ← WebSocket manager
│   └── requirements.txt
│
├── terraform/                   ← Terraform IaC
├── ansible/                     ← Ansible playbooks
├── policies/                    ← OPA policies
├── observability/               ← Prometheus + Grafana
├── netbox/                      ← NetBox configuration
├── scripts/                     ← Utility scripts
└── docs/                        ← Documentation
```

---

## 📸 Screenshots

<div align="center">

### Dashboard
<img src="docs/screenshots/dashboard.png" alt="Dashboard" width="800"/>

### Request Form
<img src="docs/screenshots/request-form.png" alt="Request Form" width="800"/>

### Real-time Progress
<img src="docs/screenshots/progress.png" alt="Request Progress" width="800"/>

### Generated Code
<img src="docs/screenshots/generated-code.png" alt="Generated Terraform Code" width="800"/>

### GitLab Pipeline
<img src="docs/screenshots/pipeline.png" alt="GitLab CI Pipeline" width="800"/>

</div>

---

## 🔒 Security

- ✅ **JWT Authentication** with refresh tokens
- ✅ **Role-Based Access Control** (admin, engineer, researcher, viewer)
- ✅ **Policy-as-Code** enforcement via OPA
- ✅ **Network Isolation** for sensitive data (VLANs)
- ✅ **Knowledge Security** compliance ready
- ✅ **Audit Trail** via Git + NetBox
- ✅ **Secrets never in code** (env vars)

See [SECURITY.md](docs/SECURITY.md) for the full threat model.

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System design and data flow |
| [API Reference](docs/API.md) | Complete API documentation |
| [Operations](docs/OPERATIONS.md) | Deployment and troubleshooting |
| [Security](docs/SECURITY.md) | Threat model and controls |
| [Threat Model](architecture/threat-model.md) | STRIDE analysis |
| [Demo Script](docs/DEMO.md) | 5-minute demo walkthrough |

---

## 🎬 Demo

**Watch the 5-minute demo:**
- 📹 [Video walkthrough](docs/demo.md)
- 📝 [Step-by-step demo script](docs/DEMO.md)

**Try it yourself:**

```bash
make dev-up          # Start infrastructure
make backend         # Terminal 1
make frontend        # Terminal 2
make e2e             # Terminal 3 — runs a full test
```

---

## 🗺️ Roadmap

- [x] Self-service portal
- [x] Policy-as-code (OPA)
- [x] Source of truth (NetBox)
- [x] Terraform code generation
- [x] Ansible code generation
- [x] GitLab CI/CD pipeline
- [x] Real KVM provisioning
- [x] WebSocket real-time updates
- [x] Drift detection
- [x] Monitoring (Prometheus + Grafana)
- [ ] Kubernetes support
- [ ] Multi-tenant projects
- [ ] Cost tracking
- [ ] Chaos testing

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'feat: add amazing feature'`
4. Push to the branch: `git push origin feature/amazing-feature`
5. Open a Merge Request on [GitLab](https://gitlab.com/yannassiri26/infraforge.git/-/merge_requests)

---

## 👤 Author

**Yann Assiri**

- 🎓 Engineering student passionate about DevOps and infrastructure automation
- 💼 Building InfraForge to demonstrate modern infrastructure practices
- 📧 Reach out via [GitLab](https://gitlab.com/yannassiri26/)

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- Inspired by the infrastructure automation challenges faced by university ICT departments
- Architecture patterns drawn from real-world GitOps and event-driven systems
- Built as a portfolio project for **TU Delft's Campus Connectivity Team**

---

<div align="center">

**[⬆ Back to top](#-infraforge)**

Made with ❤️ and lots of ☕

</div>




---
