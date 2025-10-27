# Example usage of K8sAgentAdapter
import asyncio
from mcp_agent.app import MCPApp
from mcp_agent.agents.k8s_agent import K8sAgentAdapter

# Import an LLM type used in examples (adjust to your project's LLM implementation)
from mcp_agent.workflows.llm.augmented_llm_google import GoogleAugmentedLLM

async def main():
    async with MCPApp(name="k8s-adapter-demo").run() as app:
        # Create an adapter instance that will provision a Deployment
        agent = K8sAgentAdapter(
            name="k8s-demo-agent",
            instruction="You are a helpful assistant running in Kubernetes",
            server_names=["filesystem"],
            k8s_image="ghcr.io/sands/mcp-agent:latest",  # TODO: build/push image in CI
            k8s_namespace="default",
            k8s_replicas=1,
            k8s_autoscale=False,
            k8s_env={"EXAMPLE_VAR": "value"},
        )

        # Use as async context manager (overrides initialize/shutdown)
        async with agent:
            # Optionally attach a local LLM if desired (or implement remote LLM RPC)
            llm = await agent.attach_llm(GoogleAugmentedLLM)
            result = await llm.generate_str("Say hi in one sentence.")
            print("LLM result:", result)

if __name__ == "__main__":
    asyncio.run(main())