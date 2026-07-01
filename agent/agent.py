import os
import urllib.request
import urllib.parse
import json
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

from google.auth import default
from google.auth.transport.requests import Request

def get_auth_headers(context=None) -> dict[str, str]:
    target_audience = "https://mcp-weather-server-439077346891.us-central1.run.app"
    try:
        # Get federated credentials from metadata server
        creds, project = default()
        auth_req = Request()
        creds.refresh(auth_req)
        
        # Impersonate service account using iamcredentials REST API
        sa_email = f"agent-invoker-sa@{project}.iam.gserviceaccount.com"
        url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}:generateIdToken"
        
        payload = json.dumps({
            "audience": target_audience,
            "includeEmail": True
        }).encode("utf-8")
        
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            id_token = res_data["token"]
            return {"Authorization": f"Bearer {id_token}"}
            
    except Exception as e:
        import sys
        print(f"Error exchanging federated token for ID token: {e}", file=sys.stderr)
        return {}

# Define the MCP toolset connecting to the remote server with header provider
mcp_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://mcp-weather-server-439077346891.us-central1.run.app/mcp",
    ),
    header_provider=get_auth_headers
)

# Define the root agent
root_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='mcp_weather_client',
    instruction='You are a helpful assistant that can check weather using tools.',
    tools=[mcp_toolset],
)
