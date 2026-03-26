# Acme Corp Engineering Onboarding Guide

Welcome to Acme Corp engineering! This guide covers everything you need to get productive in your first week.

## Day 1: Environment Setup

### Prerequisites

- macOS 13+ or Ubuntu 22.04+ (Windows via WSL2 is supported but not recommended)
- Homebrew (macOS) or apt (Linux)
- GitHub account added to the `acme-corp` organization (ask your manager)

### Development Tools

Install the core toolchain:

```bash
# macOS
brew install python@3.12 node@20 go@1.22 docker colima

# Start Docker runtime (we use Colima instead of Docker Desktop for licensing)
colima start --memory 8 --cpu 4
```

### Repository Setup

Clone the main repositories:

```bash
# Core services
git clone git@github.com:acme-corp/user-service.git
git clone git@github.com:acme-corp/api-gateway.git
git clone git@github.com:acme-corp/web-app.git

# Shared libraries
git clone git@github.com:acme-corp/acme-sdk-python.git
git clone git@github.com:acme-corp/proto-definitions.git
```

Each repo has a `Makefile` with standard targets:

```bash
make setup    # Install dependencies, create .env from .env.example
make dev      # Start local development server
make test     # Run test suite
make lint     # Run linters (ruff for Python, eslint for TS)
```

### Internal Services

Access these with your Acme SSO credentials:

| Service | URL | Purpose |
|---------|-----|---------|
| GitHub | github.com/acme-corp | Code, PRs, issues |
| Linear | linear.app/acme | Sprint planning, tickets |
| Confluence | acme.atlassian.net | Documentation, RFCs, ADRs |
| Grafana | grafana.acme-corp.internal | Metrics, dashboards, alerts |
| PagerDuty | acme.pagerduty.com | On-call schedules, incidents |
| Slack | acme-corp.slack.com | Communication (#eng-general, #incidents) |
| ArgoCD | argo.acme-corp.internal | Deployment status |

## Repository Structure

Acme uses a polyrepo structure. Key repositories:

### Backend Services (Python)

- **user-service** — User management, authentication, RBAC. Python 3.12, FastAPI, PostgreSQL. Owns the `/v2/users` API.
- **order-service** — Order processing, payments, fulfillment. Python 3.12, FastAPI, PostgreSQL + Redis.
- **notification-service** — Email, push, SMS notifications. Python 3.12, Celery, RabbitMQ.
- **api-gateway** — Kong-based API gateway. Handles routing, rate limiting, JWT validation.

### Frontend (TypeScript)

- **web-app** — Customer-facing dashboard. Next.js 14, React 18, Tailwind CSS.
- **admin-panel** — Internal admin tool. Next.js 14, React 18, shadcn/ui.

### Infrastructure

- **infra-terraform** — All AWS infrastructure as Terraform modules. VPCs, EKS, RDS, ElastiCache.
- **k8s-manifests** — Kubernetes manifests and Helm charts. ArgoCD syncs from this repo.
- **proto-definitions** — Protobuf definitions for inter-service gRPC communication.

## Deployment Process

### Environments

| Environment | Cluster | Auto-deploy | Purpose |
|-------------|---------|-------------|---------|
| `dev` | `eks-dev` | Yes, on merge to `main` | Development testing |
| `staging` | `eks-staging` | Yes, after dev passes smoke tests | Pre-production validation |
| `production` | `eks-prod` | No, manual approval | Live traffic |

### CI/CD Pipeline

1. **PR opened** → GitHub Actions runs: lint, unit tests, integration tests, security scan (Snyk)
2. **PR merged to `main`** → Docker image built, pushed to ECR, deployed to `dev` via ArgoCD
3. **Dev smoke tests pass** → Auto-promoted to `staging`
4. **Staging validation** → Manual approval in ArgoCD promotes to `production`
5. **Production deploy** → Canary rollout (10% → 50% → 100% over 30 minutes)

### Rollback

If production metrics degrade (error rate > 1% or p99 latency > 500ms), ArgoCD auto-rolls back to the previous version. Manual rollback:

```bash
# Via ArgoCD CLI
argocd app rollback user-service
```

## Coding Standards

### Python

- **Formatter:** ruff (replaces black + isort)
- **Linter:** ruff (replaces flake8 + pylint)
- **Type checking:** mypy with strict mode
- **Testing:** pytest with coverage threshold of 80%
- **Dependency management:** pip-tools (`requirements.in` → `requirements.txt`)

Naming conventions:
- Functions and variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private methods: `_leading_underscore`
- Database models: singular nouns (`User`, not `Users`)

### TypeScript

- **Formatter:** Prettier
- **Linter:** ESLint with Acme config (`@acme-corp/eslint-config`)
- **Testing:** Vitest for unit tests, Playwright for E2E
- **Package manager:** pnpm (not npm or yarn)

### Git Workflow

- Branch from `main`, name branches `<type>/<ticket>-<description>` (e.g., `feat/ENG-1234-add-user-export`)
- Squash merge to `main` (one commit per PR)
- PR requires 1 approval from code owner + passing CI
- Commit messages follow Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`
- No force-pushing to `main` (branch protection enabled)

## Team Structure

### Engineering Teams

| Team | Lead | Scope | Slack Channel |
|------|------|-------|---------------|
| Platform | Sarah Chen | API gateway, auth, infra | #team-platform |
| Product | Marcus Rivera | User-facing features, web app | #team-product |
| Data | Priya Patel | Analytics, ML, data pipelines | #team-data |
| Mobile | James Kim | iOS and Android apps | #team-mobile |

### On-Call

All backend engineers join the on-call rotation after their first month. Rotation is weekly, managed in PagerDuty.

On-call responsibilities:
- Respond to pages within 15 minutes
- Triage and resolve or escalate production incidents
- Write incident postmortem within 48 hours
- Hand off open issues at rotation end

Escalation path: On-call engineer → Team lead → VP Engineering (Diane Torres)

## First Week Checklist

- [ ] Set up development environment (Day 1)
- [ ] Complete security training in Confluence (Day 1)
- [ ] Deploy a change to `dev` environment (Day 2)
- [ ] Read ADR-0042 (auth middleware) and ADR-0038 (database sharding) (Day 2)
- [ ] Pair with your buddy on a small ticket (Day 3-4)
- [ ] Join the on-call shadow rotation for one shift (Day 5)
- [ ] Attend Friday engineering all-hands (Day 5)

## Getting Help

- **Stuck on setup?** Ask in #eng-onboarding (Slack)
- **Code questions?** Tag your team lead in the PR
- **Infrastructure issues?** #team-platform (Slack) or page on-call if production
- **HR / admin?** people@acme-corp.com

Your onboarding buddy is assigned by your manager and will be your go-to person for the first two weeks. Don't hesitate to ask questions — everyone was new once.
