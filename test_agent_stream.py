import json
import vertexai
from vertexai.preview import reasoning_engines
from google.cloud.aiplatform_v1beta1 import types as aip_types
from vertexai.reasoning_engines import _utils

vertexai.init(project="deepakmichaelstage", location="us-east1")

print("Loading reasoning engine...")
reasoning_engine = reasoning_engines.ReasoningEngine("5555014218501062656")

execution_client = reasoning_engine.execution_api_client

input_data = {
    "message": "What is the weather in Paris?",
    "user_id": "test_user"
}

print("Sending StreamQueryReasoningEngineRequest...")
request = aip_types.StreamQueryReasoningEngineRequest(
    name=reasoning_engine.resource_name,
    input=input_data,
    class_method="stream_query"
)

response_stream = execution_client.stream_query_reasoning_engine(request=request)

print("\n--- STREAMING DEPLOYED AGENT OUTPUT ---")
for chunk in response_stream:
    print(f"Raw chunk: {chunk}")
    try:
        for parsed_json in _utils.yield_parsed_json(chunk):
            if parsed_json is not None:
                print(json.dumps(parsed_json, indent=2))
    except Exception as parse_err:
        print(f"Parsing failed: {parse_err}")
print("\n---------------------------------------")
