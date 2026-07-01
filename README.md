# Agent Gateway Egress to Cloud Run MCP Codelab/Deployment Guide

This repository contains the codebase and deployment instructions for configuring, deploying, and validating a Vertex AI Agent Runtime client agent routed through an **Agent Gateway** to access a private **Cloud Run MCP Server**.

---

## 🏗️ Architecture

```
[Agent Runtime] (AGENT_IDENTITY)
      │
      ▼ (mTLS / Egress Policy via Gateway Attachment)
[Agent Gateway] (AGENT_TO_ANYWHERE)
      │
      ▼ (Private Google Access via VPC Subnet)
[Cloud Run (MCP Weather Server)] (IAM protected)
```

---

## 🛠️ Step 1: GCP Infrastructure Prerequisites

Ensure you have the following GCP resources set up:
1.  **VPC Network & Subnet**: A VPC (`agent-vpc`) with a subnet (`network-attachment-east1`) in `us-east1` having **Private Google Access** enabled (`privateIpGoogleAccess: true`).
2.  **PSC Network Attachment**: Created inside `us-east1` region targeting the subnet.
3.  **Agent Gateway**: Created in `us-east1` region in `AGENT_TO_ANYWHERE` mode, configured with `networkConfig` pointing to your PSC network attachment.
4.  **Agent Registry**: A regional registry in `us-east1`.
5.  **Private Cloud Run MCP Server**: Deployed with IAM Authentication enabled.

---

## 🛠️ Step 2: Register MCP Service & Google APIs

Register the Cloud Run Weather Server URL and target Google APIs inside the regional `us-east1` Agent Registry:

```bash
# 1. Register Cloud Run MCP Server
gcloud alpha agent-registry services create mcp-weather-server \
  --project=YOUR_PROJECT_ID \
  --location=us-east1 \
  --display-name="Weather MCP Server" \
  --endpoint-spec-type=no-spec \
  --interfaces=url=https://YOUR_CLOUD_RUN_SERVICE_URL/mcp,protocolBinding=JSONRPC

# 2. Register required Google APIs (Bootstrap)
gcloud alpha agent-registry services create us-east1-cloudresourcemanager-mtls \
  --project=YOUR_PROJECT_ID \
  --location=us-east1 \
  --display-name="cloudresourcemanager.mtls.googleapis.com" \
  --endpoint-spec-type=no-spec \
  --interfaces=url=https://cloudresourcemanager.mtls.googleapis.com,protocolBinding=JSONRPC

gcloud alpha agent-registry services create us-east1-aiplatform-mtls \
  --project=YOUR_PROJECT_ID \
  --location=us-east1 \
  --display-name="us-east1-aiplatform.mtls.googleapis.com" \
  --endpoint-spec-type=no-spec \
  --interfaces=url=https://us-east1-aiplatform.mtls.googleapis.com,protocolBinding=JSONRPC
```

---

## 🛠️ Step 3: Configure IAM Security & Impersonation

Because the agent runs in `AGENT_IDENTITY` (SPIFFE-based) mode, it receives a federated Security Token Service (STS) token signed by `sts.googleapis.com`. Standard Cloud Run IAM requires standard Google-signed OIDC ID tokens.

We handle this by configuring **Service Account Impersonation** (exchanging the federated STS token for a Service Account OIDC token at runtime).

1.  **Create a dedicated service account**:
    ```bash
    gcloud iam service-accounts create agent-invoker-sa \
      --display-name="Agent Invoker Service Account" \
      --project=YOUR_PROJECT_ID
    ```
2.  **Authorize the Agent Identity PrincipalSet to impersonate the service account**:
    ```bash
    gcloud iam service-accounts add-iam-policy-binding agent-invoker-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
      --role="roles/iam.serviceAccountTokenCreator" \
      --member="principalSet://agents.global.org-YOUR_ORG_ID.system.id.goog/attribute.platformContainer/aiplatform/projects/YOUR_PROJECT_NUMBER" \
      --project=YOUR_PROJECT_ID
    ```
3.  **Grant Invoker permissions to the service account on Cloud Run**:
    ```bash
    gcloud run services add-iam-policy-binding YOUR_CLOUD_RUN_SERVICE_NAME \
      --role="roles/run.invoker" \
      --member="serviceAccount:agent-invoker-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
      --region=YOUR_CLOUD_RUN_REGION \
      --project=YOUR_PROJECT_ID
    ```
4.  **Grant baseline roles to the Agent Identity PrincipalSet**:
    ```bash
    for ROLE in "roles/aiplatform.user" "roles/aiplatform.agentDefaultAccess" "roles/agentregistry.viewer" "roles/logging.logWriter" "roles/monitoring.metricWriter" "roles/browser"; do
        gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
          --member="principalSet://agents.global.org-YOUR_ORG_ID.system.id.goog/attribute.platformContainer/aiplatform/projects/YOUR_PROJECT_NUMBER" \
          --role="${ROLE}"
    done
    ```

---

## 🚀 Step 4: Deploy the Workload (Client Agent)

Deploy the client agent using `deploy_agent.py`. The script configures the Reasoning Engine with the target regional Agent Gateway and registers the deployment:

```bash
uv run python3 deploy_agent.py \
  --project=YOUR_PROJECT_ID \
  --region=us-east1 \
  --src-dir=./agent \
  --staging-bucket=YOUR_STAGING_GCS_BUCKET \
  --display-name="mcp-weather-client" \
  --description="MCP Weather Client Agent" \
  --enable-telemetry \
  --enable-agent-identity \
  --agent-gateway-egress=projects/YOUR_PROJECT_ID/locations/us-east1/agentGateways/YOUR_GATEWAY_NAME \
  --allow-token-sharing
```

---

## 🔍 Step 5: Validate Deployment

Run the streaming validation script `test_agent_stream.py` to verify the routing path:

1.  **Configure environment variables**:
    ```bash
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/key.json # locally
    ```
2.  **Edit `test_agent_stream.py`**: Update `ReasoningEngine("...")` ID with your newly deployed engine ID.
3.  **Run the script**:
    ```bash
    python3 test_agent_stream.py
    ```
4.  **Observe output**: Check that you receive the final streamed response containing the temperature and wind speed for Paris.
