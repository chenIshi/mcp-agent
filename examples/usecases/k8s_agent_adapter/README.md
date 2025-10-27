# Deploy MCP-Agent on K8s

## Publish Docker Image
```
docker build -t ghcr.io/your-org/mcp-agent:latest .
docker push ghcr.io/your-org/mcp-agent:latest
```
# Kubernetes Agent Adapter (K8sAgentAdapter)

This example shows how to provision and manage a Kubernetes Deployment from an MCP Agent using the `K8sAgentAdapter`.
It creates/patches a Deployment (and optionally an HPA), waits for Pod readiness, and cleans up on shutdown.

The adapter is a minimal, extensible starting point — not production-ready out of the box. See notes at the end for
hardening tips.

## What changed in this branch

New files and directories introduced by the adapter example:

- Added: `src/mcp_agent/agents/k8s_agent.py` — the `K8sAgentAdapter` and a small `KubernetesManager` helper.
- Added: `examples/usecases/k8s_agent_adapter/` — runnable example and container assets:
	- `main.py` — example showing how to use `K8sAgentAdapter` with an LLM.
	- `mcp_agent.config.yaml` — sample config (logging, google model defaults, k8s defaults).
	- `mcp_agent.secrets.yaml` — sample secrets file (don’t commit real secrets!).
	- `Dockerfile` — minimal image build for the example.

## Directory layout

```
examples/
	usecases/
		k8s_agent_adapter/
			Dockerfile
			main.py
			mcp_agent.config.yaml
			mcp_agent.secrets.yaml   # example only; do NOT commit real keys
			README.md                # this file
src/
	mcp_agent/
		agents/
			k8s_agent.py
```

## Prerequisites

- Kubernetes access configured locally (your `kubectl` context points to the target cluster)
- Python 3.11+
- Docker (if you want to build and push a container image)
- An LLM provider API key (the example uses Google Gemini via `augmented_llm_google`)

Important: do not commit real API keys. Use `mcp_agent.secrets.yaml` locally only (or environment variables/secret stores).

## Quick start (run locally, provisions to your cluster)

1) Review and edit the example:

- In `examples/usecases/k8s_agent_adapter/main.py`, set the `k8s_image` to an image you control (e.g., `ghcr.io/<org>/<repo>:<tag>`).
- In `examples/usecases/k8s_agent_adapter/mcp_agent.secrets.yaml`, set your provider key(s).

2) Install dependencies (from the repo root):

```bash
pip install -e .
pip install kubernetes google-generativeai
```

3) Run the example (from the repo root):

```bash
python examples/usecases/k8s_agent_adapter/main.py
```

What it does:
- Creates or patches a Deployment using the `k8s_image` you specified
- Optionally creates an HPA if `k8s_autoscale=True`
- Polls for a Ready Pod
- Runs a simple LLM call via the attached LLM
- On shutdown, deletes the HPA/Deployment (unless `connection_persistence=True`)

## Build and push a container image (optional)

If you want to run the same example inside a container, use the Dockerfile in this folder. You have two options:

Option A — Install `uv` inside the image and keep the current CMD:

```bash
docker build -t ghcr.io/sands/mcp-agent:latest --build-arg INSTALL_UV=true .
docker push ghcr.io/sands/mcp-agent:latest
```

Option B — Simplify CMD to use Python directly (recommended for most users):

1. Change the last line of `Dockerfile` from using `uv` to Python:

	 ```Dockerfile
	 CMD ["python", "main.py"]
	 ```

2. Ensure required packages are installed in the image (add a `requirements.txt` alongside the Dockerfile, for example):

	 ```text
	 kubernetes
	 google-generativeai
	 git+https://github.com/lastmile-ai/mcp-agent
	 ```

3. Then build and push:

```bash
docker build -t ghcr.io/your-org/mcp-agent:latest .
docker push ghcr.io/your-org/mcp-agent:latest
```

Update the `k8s_image` field in `main.py` to match your pushed image.

## How the adapter works (high level)

- `K8sAgentAdapter` subclasses `Agent` and overrides `initialize()` and `shutdown()` to manage k8s resources.
- Blocking Kubernetes client calls run in a thread pool via `asyncio.get_event_loop().run_in_executor(...)` so the async
	event loop stays responsive.
- `_wait_for_pod_ready()` polls for a Ready Pod matching the adapter’s labels.
- Optional autoscaling helpers are provided: `enable_autoscale()` and `scale()`.

## Production notes

This is a minimal skeleton. For production:

- Add auth/permissions, retries, exponential backoff, and robust status checks
- Use Secrets/ConfigMaps/ServiceAccounts
- Consider readiness probes and workload-specific health checks
- Secure images and namespaces; apply resource limits/requests appropriately
- Implement a remote LLM RPC if the agent is meant to run entirely in-cluster

## Cleanup

By default, the adapter deletes the Deployment and HPA on shutdown. If you want it to stay running, set
`connection_persistence=True` on the agent instance and implement your desired scale-to-zero behavior.