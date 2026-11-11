# Deploying to AWS EC2 (Jira PMH-19)

One EC2 instance runs the whole stack with docker compose: **Caddy** (HTTPS), **web** (FastAPI),
**worker** (Celery + AI harness + sandbox) and **Redis**. Only ports 80/443 are public; the internal
tool proxy (`/internal/*`) is blocked at Caddy and only reachable inside the server.

Cost: new AWS accounts on the Free plan get Free Tier credits (check aws.amazon.com/free for the
current amount). EC2, EBS and the Elastic IP are paid from those credits. **Stop or terminate the
instance when you no longer need it**, and set a budget alert (step 1).

## 1. AWS account and budget alert
1. Create an account at aws.amazon.com and choose the **Free plan**.
2. Pick region **Asia Pacific (Mumbai) ap-south-1** (top right), closest to Pakistan.
3. Billing and Cost Management → **Budgets** → Create budget → "Zero spend" or a small monthly
   amount, with an email alert.

## 2. Launch the instance
EC2 → **Launch instance**:
| Setting | Value |
|---|---|
| Name | `pmh-server` |
| Image | **Ubuntu Server 24.04 LTS** |
| Instance type | **t3.small** (2 GB RAM). t3.micro (1 GB) may run out of memory |
| Key pair | Create new → `pmh-key` → **.pem** → download and keep it safe |
| Network | Allow **SSH from My IP**, allow **HTTP** and **HTTPS from anywhere** |
| Storage | 20 GB gp3 |

## 3. Give it a fixed address
EC2 → **Elastic IPs** → Allocate → Associate with `pmh-server`. Note the IP, e.g. `13.201.5.7`.
Your free HTTPS hostname is the IP with dashes plus `.sslip.io`: `13-201-5-7.sslip.io`.

## 4. Set up the server
From PowerShell on your laptop (folder containing `pmh-key.pem`):
```powershell
ssh -i pmh-key.pem ubuntu@13.201.5.7
```
On the server (the repo is private, so `git clone` asks for your GitHub username and a
**personal access token** with read access to this repo as the password):
```bash
git clone https://github.com/ZH-ITech/Portfolio-Manager-Harness.git ~/pmh
bash ~/pmh/deploy/setup_ec2.sh https://github.com/ZH-ITech/Portfolio-Manager-Harness.git
```
Then edit the two settings files it tells you about:
```bash
nano ~/pmh/.env          # LLM_API_KEY = a NEW Groq key just for the server; PTC_SHARED_SECRET = long random text
nano ~/pmh/deploy/.env   # DOMAIN=13-201-5-7.sslip.io
newgrp docker
cd ~/pmh/deploy && docker compose -f docker-compose.prod.yml up -d --build
```
The first build takes a few minutes. Open **https://13-201-5-7.sslip.io**. That is the live link.

Useful commands on the server:
```bash
cd ~/pmh/deploy
docker compose -f docker-compose.prod.yml ps          # status
docker compose -f docker-compose.prod.yml logs -f web worker   # logs
bash ~/pmh/deploy/redeploy.sh                          # pull main and restart
```

## 5. Automatic redeploys (optional)
GitHub repo → Settings → Secrets and variables → Actions → New repository secret:
| Secret | Value |
|---|---|
| `EC2_HOST` | Elastic IP, e.g. `13.201.5.7` |
| `EC2_USER` | `ubuntu` |
| `EC2_SSH_KEY` | full contents of `pmh-key.pem` |
| `PUBLIC_HOST` | `13-201-5-7.sslip.io` |

After this, every merge to `main` that passes CI redeploys the server (workflow `Deploy`).
For `git pull` on the server to work non-interactively, add a read-only **deploy key**
(repo → Settings → Deploy keys) or store your token with `git config credential.helper store`.

## Security notes
- Secrets live only in `~/pmh/.env` on the server (permissions 600), never in Git.
- Use separate Groq keys for your laptop and the server so either can be revoked alone.
- The sandbox enforces CPU/memory/time limits inside the worker container. Restricting its
  network to the PTC proxy only is Jira PMH-16.
