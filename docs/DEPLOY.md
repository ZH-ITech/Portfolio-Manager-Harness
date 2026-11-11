# Deploying to AWS EC2 (Jira PMH-19)

Live: **https://3-105-220-208.sslip.io** (Sydney, ap-southeast-2).

One EC2 instance runs the whole stack with docker compose: **Caddy** (HTTPS), **web** (FastAPI),
**worker** (Celery + AI harness + sandbox) and **Redis**. Only ports 80/443 are public; the internal
tool proxy (`/internal/*`) is blocked at Caddy and only reachable inside the server.

Cost: new AWS accounts on the Free plan get Free Tier credits (check aws.amazon.com/free for the
current amount). EC2, EBS and the Elastic IP are paid from those credits. **Stop or terminate the
instance when you no longer need it**, and set a budget alert (step 1).

## 1. AWS account and budget alert
1. Create an account at aws.amazon.com and choose the **Free plan**.
2. Pick a region near your users (top right), e.g. Mumbai ap-south-1. Accounts created with the newer
   Free plan sign-up may be locked to one region (ours: Sydney ap-southeast-2); use that one rather
   than upgrading the plan.
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
From PowerShell on your laptop (folder containing `pmh-key.pem`). Windows OpenSSH refuses key files
other users can read, so restrict it once:
```powershell
icacls .\pmh-key.pem /inheritance:r
icacls .\pmh-key.pem /remove "NT AUTHORITY\Authenticated Users" "BUILTIN\Users" "Everyone"
icacls .\pmh-key.pem /grant:r "$($env:USERNAME):(R)"
ssh -i .\pmh-key.pem ubuntu@13.201.5.7
```
The repository is private, so give the server a read-only **deploy key** (no passwords or tokens
to paste):
```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/github_deploy -C pmh-server
cat ~/.ssh/github_deploy.pub     # copy this line
```
GitHub repo → Settings → Deploy keys → Add deploy key → paste, leave write access unticked. Then:
```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
ssh-keyscan github.com >> ~/.ssh/known_hosts
git clone git@github.com:ZH-ITech/Portfolio-Manager-Harness.git ~/pmh
bash ~/pmh/deploy/setup_ec2.sh git@github.com:ZH-ITech/Portfolio-Manager-Harness.git
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
The deploy key from step 4 lets the server pull `main` without a password.

## Security notes
- Secrets live only in `~/pmh/.env` on the server (permissions 600), never in Git.
- Use separate Groq keys for your laptop and the server so either can be revoked alone.
- The sandbox enforces CPU/memory/time limits inside the worker container. Restricting its
  network to the PTC proxy only is Jira PMH-16.
