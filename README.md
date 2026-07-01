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

## 🛠️ Step 0: Bootstrap Environment Variables

Configure the following environment variables in your terminal to bootstrap coordinates:

```bash
# Get active GCP Project ID dynamically
export PROJ_ID=$(gcloud config list --format="value(core.project)")

# Get GCP Project Number dynamically
export PROJECT_NUMBER=$(gcloud projects describe ${PROJ_ID} --format="value(projectNumber)")

# Specify target region and Agent Gateway name
export REGION="us-east1"
export MREGION="us"
export GATEWAY_NAME="us-east1"

# Specify organization ID (if project is part of a Google Cloud Org)
export ORG_ID="1015654926499" 

# Target Cloud Run MCP Weather Server variables
export CLOUD_RUN_REGION="us-east1"
export CLOUD_RUN_SERVICE_NAME="mcp-weather-server"

# Staging bucket for Agent Runtime build deployment
export STAGING_BUCKET="agent-staging-${PROJECT_NUMBER}"
export RE_AGENT_NAME="mcp-weather-client"
export AGW_URI="projects/${PROJ_ID}/locations/${REGION}/agentGateways/${GATEWAY_NAME}"
```

---

## 🛠️ Step 1: Deploy the Private Cloud Run MCP Server

First, deploy the private Cloud Run Weather Server to obtain its URL. Authentication will be enforced on this service.

1.  Navigate to the weather server folder (`cr_mcp_weather`) and deploy to Cloud Run:
    ```bash
    gcloud run deploy ${CLOUD_RUN_SERVICE_NAME} \
      --source=. \
      --region=${CLOUD_RUN_REGION} \
      --no-allow-unauthenticated \
      --project=${PROJ_ID}
    ```
2.  Extract the generated service URL from the command output and set it in your environment:
    ```bash
    export CLOUD_RUN_URL="https://mcp-weather-server-xxxxxxxxx.us-central1.run.app"
    ```

## 🛠️ Step 2: Create the PSC Google APIs Global Endpoint

Reserve a global internal IP address (`240.0.0.10`) inside the `agent-vpc` network and create a Private Service Connect (PSC) forwarding rule targeting the Google APIs bundle:

```bash
# 1. Reserve the PSC IP address
gcloud compute addresses create psc-google-apis-ip \
  --global \
  --purpose=PRIVATE_SERVICE_CONNECT \
  --addresses=240.0.0.10 \
  --network=agent-vpc \
  --project=${PROJ_ID}

# 2. Create the PSC forwarding rule
gcloud compute forwarding-rules create pscgoogleapis \
  --global \
  --network=agent-vpc \
  --address=psc-google-apis-ip \
  --target-google-apis-bundle=all-apis \
  --project=${PROJ_ID}
```

---

## 🛠️ Step 3: VPC Infrastructure & DNS Configuration

Ensure the VPC network and Agent Gateway DNS Peering are set up:

1.  **VPC Network & Subnet**: A VPC (`agent-vpc`) with a subnet (`network-attachment-east1`) in `us-east1` having **Private Google Access** enabled (`privateIpGoogleAccess: true`).
2.  **PSC Network Attachment**: Created inside `${REGION}` region targeting the subnet.
3.  **Agent Registry**: A regional registry in `${REGION}`.

### 🌐 Configure Private DNS Zone for Cloud Run Egress
Create a private DNS zone inside the `agent-vpc` network mapping `run.app.` to your Private Service Connect (PSC) Google APIs IP (`240.0.0.10`):

```bash
# 1. Create the private DNS zone for run.app.
gcloud dns managed-zones create cloud-run \
  --description="Private DNS zone for Cloud Run" \
  --dns-name="run.app." \
  --visibility=private \
  --networks=agent-vpc \
  --project=${PROJ_ID}

# 2. Map wildcard *.run.app. to your Google APIs PSC Endpoint IP
gcloud dns record-sets create "*.run.app." \
  --zone=cloud-run \
  --type=A \
  --ttl=300 \
  --rrdatas=240.0.0.10 \
  --project=${PROJ_ID}
```

### 🛰️ Configure and Deploy Agent Gateway (with DNS Peering)
Configure the Agent Gateway to peer `run.app.` resolutions to your customer VPC.

1. Generate the gateway configuration file `gateway_config.yaml` dynamically:
```bash
cat <<EOF > gateway_config.yaml
name: us-east1
protocols:
  - MCP
googleManaged:
  governedAccessPath: AGENT_TO_ANYWHERE
registries:
  - "//agentregistry.googleapis.com/projects/\${PROJ_ID}/locations/us-east1"
networkConfig:
  egress:
    networkAttachment: projects/\${PROJ_ID}/regions/us-east1/networkAttachments/agent-attachment-east1
  dnsPeeringConfig:
    domains:
      - run.app.
    targetProject: \${PROJ_ID}
    targetNetwork: projects/\${PROJ_ID}/global/networks/agent-vpc
EOF
```

2. Import the configuration to deploy/update your gateway:
```bash
gcloud alpha network-services agent-gateways import us-east1 \
  --source=gateway_config.yaml \
  --location=${REGION} \
  --project=${PROJ_ID}
```

---

## 🛠️ Step 4: Register MCP Service & Google APIs

Register the Cloud Run Weather Server URL and target Google APIs inside the regional `${REGION}` Agent Registry:

```bash
# 1. Register Cloud Run MCP Server
gcloud alpha agent-registry services create mcp-weather-server \
  --project=${PROJ_ID} \
  --location=${REGION} \
  --display-name="Weather MCP Server" \
  --endpoint-spec-type=no-spec \
  --interfaces=url=${CLOUD_RUN_URL}/mcp,protocolBinding=JSONRPC

# 2. Register required Google APIs (Bootstrap)
# Run the helper script to register the standard set of Google API endpoints:
python3 endpoints/register_endpoints.py \
  --multi-region=${MREGION} \
  --region=${REGION} \
  --mtls-endpoints=include
```

---

## 🛠️ Step 5: Configure IAM Security & Impersonation

Because the agent runs in `AGENT_IDENTITY` (SPIFFE-based) mode, it receives a federated Security Token Service (STS) token signed by `sts.googleapis.com`. Standard Cloud Run IAM requires standard Google-signed OIDC ID tokens.

We handle this by configuring **Service Account Impersonation** (exchanging the federated STS token for a Service Account OIDC token at runtime).

1.  **Create a dedicated service account**:
    ```bash
    gcloud iam service-accounts create agent-invoker-sa \
      --display-name="Agent Invoker Service Account" \
      --project=${PROJ_ID}
    ```
2.  **Authorize the Agent Identity PrincipalSet to impersonate the service account**:
    ```bash
    gcloud iam service-accounts add-iam-policy-binding agent-invoker-sa@${PROJ_ID}.iam.gserviceaccount.com \
      --role="roles/iam.serviceAccountTokenCreator" \
      --member="principalSet://agents.global.org-${ORG_ID}.system.id.goog/attribute.platformContainer/aiplatform/projects/${PROJECT_NUMBER}" \
      --project=${PROJ_ID}
    ```
3.  **Grant Invoker permissions to the service account on Cloud Run**:
    ```bash
    gcloud run services add-iam-policy-binding ${CLOUD_RUN_SERVICE_NAME} \
      --role="roles/run.invoker" \
      --member="serviceAccount:agent-invoker-sa@${PROJ_ID}.iam.gserviceaccount.com" \
      --region=${CLOUD_RUN_REGION} \
      --project=${PROJ_ID}
    ```
4.  **Grant baseline roles to the Agent Identity PrincipalSet**:
    ```bash
    for ROLE in "roles/aiplatform.user" "roles/aiplatform.agentDefaultAccess" "roles/agentregistry.viewer" "roles/logging.logWriter" "roles/monitoring.metricWriter" "roles/browser"; do
        gcloud projects add-iam-policy-binding ${PROJ_ID} \
          --member="principalSet://agents.global.org-${ORG_ID}.system.id.goog/attribute.platformContainer/aiplatform/projects/${PROJECT_NUMBER}" \
          --role="${ROLE}"
    done
    ```

---

## 🚀 Step 6: Deploy the Workload (Client Agent)

1.  **Configure Target Cloud Run URL in Agent Code**:
    Open `agent/agent.py` and ensure the target audience and MCP connection URL are updated to match your dynamic `${CLOUD_RUN_URL}`:
    ```python
    # agent/agent.py
    def get_auth_headers(context=None) -> dict[str, str]:
        target_audience = "https://mcp-weather-server-xxxxxxxxx.us-central1.run.app"
        # ...
    ```

2.  **Deploy the Client Agent**:
    Run `deploy_agent.py` to compile, package, and upload the reasoning engine:
    ```bash
    uv run python3 deploy_agent.py \
      --project=${PROJ_ID} \
      --region=${REGION} \
      --src-dir=./agent \
      --staging-bucket=${STAGING_BUCKET} \
      --display-name="${RE_AGENT_NAME}" \
      --description="MCP Weather Client Agent" \
      --enable-telemetry \
      --enable-agent-identity \
      --agent-gateway-egress=${AGW_URI} \
      --allow-token-sharing
    ```

---

## 🔍 Step 7: Validate Deployment

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
5.  **Verify VPC Flow Logs**:
    To verify that network packets are flowing from the Agent Gateway to the destination endpoint privately within the VPC, run this command:
    ```bash
    gcloud logging read 'resource.type="gce_subnetwork" \
      AND resource.labels.subnetwork_name="network-attachment-east1" \
      AND logName="projects/'"${PROJ_ID}"'/logs/compute.googleapis.com%2Fvpc_flows"' \
      --project=${PROJ_ID} \
      --limit=20 \
      --format="value(timestamp, jsonPayload.connection, jsonPayload.bytes_sent)"
    ```
