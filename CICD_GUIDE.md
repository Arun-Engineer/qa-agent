# CI/CD Pipeline — Setup Guide

## What's Included

```
.github/workflows/
  ci.yml              ← Lint → Test → Build → Push to ECR
  deploy.yml          ← Staging (auto) → Production (manual approval)

deploy/
  Dockerfile          ← Multi-stage, non-root, health checked
  docker-compose.yml  ← Local dev with hot reload
  ecs-task-def.json   ← Fargate task template

requirements.txt      ← Production dependencies
requirements-dev.txt  ← Dev + test dependencies
ruff.toml             ← Linting config
.dockerignore         ← Keep Docker images small
```

## Pipeline Flow

```
Developer pushes code
        ↓
┌─ CI (ci.yml) ──────────────────────────┐
│  1. Ruff lint + format check           │
│  2. pytest (unit + orchestrator tests)  │
│  3. Docker build + smoke test          │
│  4. Push to ECR (main branch only)     │
└────────────────────────────────────────┘
        ↓ (main branch, CI passes)
┌─ CD (deploy.yml) ─────────────────────┐
│  5. Deploy to Staging (automatic)      │
│  6. Smoke test staging /health         │
│  7. Deploy to Production (manual ✋)   │
│  8. Smoke test production /health      │
└────────────────────────────────────────┘
```

## Installation

```bash
# Copy files to your project root
cp -r .github/ /d/Automation/qa_agent/.github/
cp -r deploy/ /d/Automation/qa_agent/deploy/
cp requirements.txt /d/Automation/qa_agent/
cp requirements-dev.txt /d/Automation/qa_agent/
cp ruff.toml /d/Automation/qa_agent/
cp .dockerignore /d/Automation/qa_agent/
```

## GitHub Secrets Required

Go to: GitHub repo → Settings → Secrets and variables → Actions

| Secret | Description |
|--------|-------------|
| `OPENAI_API_KEY` | Your OpenAI API key |
| `AWS_ACCESS_KEY_ID` | AWS IAM user for ECR + ECS |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM secret |
| `AWS_REGION` | e.g., `ap-south-1` |
| `ECR_REPOSITORY` | ECR repo name, e.g., `aiqa-platform` |
| `STAGING_DATABASE_URL` | PostgreSQL connection string for staging |
| `STAGING_SESSION_SECRET` | Random string for staging sessions |
| `STAGING_URL` | e.g., `https://staging.aiqaplatform.com` |
| `PROD_DATABASE_URL` | PostgreSQL connection string for prod |
| `PROD_SESSION_SECRET` | Random string for prod sessions |
| `PROD_URL` | e.g., `https://app.aiqaplatform.com` |

## GitHub Environments

Go to: GitHub repo → Settings → Environments

1. Create **staging** environment (no rules needed)
2. Create **production** environment → Add "Required reviewers" → Add yourself

This means production deploys need your manual approval in GitHub.

## Local Docker Development

```bash
# Build and run locally
cd /d/Automation/qa_agent
docker-compose -f deploy/docker-compose.yml up --build

# Or just build the image
docker build -f deploy/Dockerfile -t aiqa-platform .
docker run -p 8000:8000 -e OPENAI_API_KEY=your-key aiqa-platform
```

## AWS Setup (one-time)

```bash
# 1. Create ECR repository
aws ecr create-repository --repository-name aiqa-platform

# 2. Create ECS cluster
aws ecs create-cluster --cluster-name aiqa-cluster

# 3. Create task definition
# Edit deploy/ecs-task-def.json: replace ACCOUNT_ID and REGION
aws ecs register-task-definition --cli-input-json file://deploy/ecs-task-def.json

# 4. Create services
aws ecs create-service \
  --cluster aiqa-cluster \
  --service-name aiqa-staging \
  --task-definition aiqa-platform \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=ENABLED}"

# 5. Store secrets in SSM Parameter Store
aws ssm put-parameter --name "/aiqa/database-url" --value "postgresql://..." --type SecureString
aws ssm put-parameter --name "/aiqa/session-secret" --value "$(openssl rand -hex 32)" --type SecureString
aws ssm put-parameter --name "/aiqa/openai-api-key" --value "sk-..." --type SecureString
```

## Running Tests Locally

```bash
pip install -r requirements-dev.txt
pytest tests/ -v --tb=short
```
