"""AWS architecture diagram for Portfolio Manager Harness (Jira PMH-23).

Regenerate:  pip install diagrams  (needs Graphviz)  then  python aws_architecture.py
Output: aws_architecture.png in this folder."""
from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import EC2ElasticIpAddress
from diagrams.aws.database import ElasticacheForRedis, RDSPostgresqlInstance
from diagrams.aws.general import Users
from diagrams.aws.management import CloudwatchLogs, SystemsManagerParameterStore
from diagrams.aws.network import ALB, Route53
from diagrams.aws.compute import ElasticContainerService
from diagrams.aws.storage import ElasticBlockStoreEBSVolume
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.inmemory import Redis
from diagrams.onprem.network import Internet
from diagrams.onprem.queue import Celery
from diagrams.onprem.vcs import Github
from diagrams.programming.framework import Fastapi
from diagrams.programming.language import Python

graph = {"fontsize": "20", "fontname": "Helvetica", "pad": "0.4", "nodesep": "0.55", "ranksep": "0.9",
         "label": "Portfolio Manager Harness: AWS architecture\nSolid = planned deployment (Jira PMH-19): one EC2 instance running docker compose   "
                  "Dashed grey = scale-out path (not deployed)", "labelloc": "t", "splines": "spline"}
node = {"fontsize": "12", "fontname": "Helvetica"}
edge = {"fontsize": "11", "fontname": "Helvetica"}
flow = dict(color="#1f4e8c", penwidth="1.8")
data = dict(color="#2e7d32", penwidth="1.6")
ops = dict(color="#888888", style="dashed")

with Diagram("", filename="aws_architecture", show=False, direction="LR",
             graph_attr=graph, node_attr=node, edge_attr=edge):
    users = Users("Users\n(browser chat UI)")

    with Cluster("External integrations"):
        llm = Internet("Groq API (free tier)\nopen-weight LLM\nGPT-OSS 120B / 20B")
        market = Internet("Yahoo Finance\n(yfinance, delayed)\nprices, statements, news")

    with Cluster("Delivery"):
        repo = Github("GitHub repo\nPRs + reviews")
        ci = GithubActions("GitHub Actions\nlint, tests, docker build")

    with Cluster("AWS Cloud (region, e.g. ap-south-1)"):
        dns = Route53("Route 53\nDNS")
        params = SystemsManagerParameterStore("Secrets: .env on server (chmod 600)\nSSM Parameter Store: planned")
        logs = CloudwatchLogs("Logs: docker (rotated)\nCloudWatch Logs: planned")

        with Cluster("VPC  /  public subnet  /  security group: 443 only"):
            eip = EC2ElasticIpAddress("Elastic IP")
            with Cluster("EC2 instance  (docker compose)"):
                api = Fastapi("web: FastAPI\nchat API, UI\n(behind Caddy HTTPS;\n/internal blocked)")
                queue = Redis("redis\ntask queue")
                worker = Celery("worker: Celery\nAI harness\n(model, context,\npermissions)")
                mcp = Python("MCP server\nmarket data +\ncompany info")
                sandbox = Python("Python sandbox\nisolated container, no secrets,\nnetwork: PTC proxy only (PMH-16)")
            disk = ElasticBlockStoreEBSVolume("EBS volume\nsaved results,\nconversations, audit log")

        with Cluster("Scale-out path (not deployed)"):
            alb = ALB("ALB")
            ecs = ElasticContainerService("ECS tasks\n(web, workers)")
            cache = ElasticacheForRedis("ElastiCache\nRedis")
            rds = RDSPostgresqlInstance("RDS\nPostgreSQL")

    # request path (numbered)
    users >> Edge(label="1 HTTPS", **flow) >> dns >> Edge(**flow) >> eip >> Edge(label="2", **flow) >> api
    api >> Edge(label="3 enqueue job", **flow) >> queue >> Edge(label="4 consume", **flow) >> worker
    worker >> Edge(label="5 model calls\n(tool calling)", **flow) >> llm
    worker >> Edge(label="6 data tools", **data) >> mcp >> Edge(label="7 fetch", **data) >> market
    worker >> Edge(label="8 run analysis code", **flow) >> sandbox
    sandbox >> Edge(label="PTC: call_tool", style="dashed", color="#2e7d32") >> api
    worker >> Edge(label="9 progress, retries,\nsaved results", **data) >> disk
    api >> Edge(label="10 read progress\n+ answer for UI", **data) >> disk

    # operations
    params >> Edge(label="secrets at start", **ops) >> worker
    worker >> Edge(**ops) >> logs
    repo >> Edge(**ops) >> ci >> Edge(label="deploy (SSH)", **ops) >> eip

    # scale-out (not deployed)
    sc = dict(color="#bbbbbb", style="dashed")
    alb >> Edge(**sc) >> ecs >> Edge(**sc) >> cache
    ecs >> Edge(**sc) >> rds
