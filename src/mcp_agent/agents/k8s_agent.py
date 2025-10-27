# Minimal Kubernetes-backed Agent adapter (subclass + adapter pattern)
# Intended as a clear, extensible starting point for production features:
#  - override initialize/shutdown for precise control
#  - support Deployment creation for autoscaling later
#  - provide scale()/create_hpa()/delete_hpa() hooks
#  - integrate config via Agent fields (image, namespace, replicas, resources)
#
# NOTE: This is a foundational skeleton and not production-ready out of the box.
#       See TODOs throughout for required production concerns (auth, retries, security).

from __future__ import annotations
from typing import Optional, Dict, Any, Callable
import asyncio
import logging

from pydantic import Field, ConfigDict
from kubernetes import client as k8s_client  # may raise ImportError
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException

from mcp_agent.agents.agent import Agent
from mcp_agent.logging.logger import get_logger

logger = get_logger(__name__)

# Simple k8s helper that manages a Deployment and can create an HPA later.
class KubernetesManager:
    def __init__(self, namespace: str = "default", kubeconfig: Optional[str] = None):
        self.namespace = namespace
        self.kubeconfig = kubeconfig
        self.apps_api: Optional[k8s_client.AppsV1Api] = None
        self.core_api: Optional[k8s_client.CoreV1Api] = None
        self.autoscaling_api: Optional[k8s_client.AutoscalingV1Api] = None
        self._ensure_client()

    def _ensure_client(self):
        try:
            # Try in-cluster first, fallback to kubeconfig file
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config(self.kubeconfig)
            self.apps_api = k8s_client.AppsV1Api()
            self.core_api = k8s_client.CoreV1Api()
            self.autoscaling_api = k8s_client.AutoscalingV1Api()
        except Exception as e:
            raise RuntimeError(
                "Failed to initialize Kubernetes client. "
                "Install 'kubernetes' package and make sure kubeconfig or in-cluster config is available."
            ) from e

    def create_deployment(
        self,
        name: str,
        image: str,
        labels: Dict[str, str],
        container_port: Optional[int] = None,
        replicas: int = 1,
        env: Optional[Dict[str, str]] = None,
        resources: Optional[Dict[str, Any]] = None,
    ):
        """
        Create a Deployment (idempotent-ish). If a deployment exists, this will attempt to patch it.
        Keep this simple — in production you'd handle race conditions, backoffs, and robust status checks.
        """
        metadata = k8s_client.V1ObjectMeta(name=name, labels=labels)
        env_list = []
        if env:
            env_list = [k8s_client.V1EnvVar(name=k, value=v) for k, v in env.items()]

        container = k8s_client.V1Container(
            name=name,
            image=image,
            env=env_list,
            resources=k8s_client.V1ResourceRequirements(**(resources or {})),
        )
        if container_port:
            container.ports = [k8s_client.V1ContainerPort(container_port=container_port)]

        pod_spec = k8s_client.V1PodSpec(containers=[container], restart_policy="Always")
        template = k8s_client.V1PodTemplateSpec(
            metadata=k8s_client.V1ObjectMeta(labels=labels), spec=pod_spec
        )
        spec = k8s_client.V1DeploymentSpec(replicas=replicas, template=template, selector=k8s_client.V1LabelSelector(match_labels=labels))
        deployment = k8s_client.V1Deployment(api_version="apps/v1", kind="Deployment", metadata=metadata, spec=spec)

        try:
            existing = self.apps_api.read_namespaced_deployment(name=name, namespace=self.namespace)
            # Patch simple fields (replicas, template)
            logger.info("Patching existing deployment %s", name)
            self.apps_api.patch_namespaced_deployment(name=name, namespace=self.namespace, body=deployment)
        except ApiException as e:
            if e.status == 404:
                logger.info("Creating deployment %s", name)
                self.apps_api.create_namespaced_deployment(namespace=self.namespace, body=deployment)
            else:
                raise

    def delete_deployment(self, name: str, propagate: bool = True):
        try:
            self.apps_api.delete_namespaced_deployment(name=name, namespace=self.namespace)
        except ApiException as e:
            if e.status == 404:
                logger.debug("Deployment %s not found during delete", name)
            else:
                logger.warning("Failed to delete deployment %s: %s", name, e)

    def scale_deployment(self, name: str, replicas: int):
        body = {"spec": {"replicas": replicas}}
        try:
            self.apps_api.patch_namespaced_deployment_scale(name=name, namespace=self.namespace, body=body)
        except ApiException as e:
            logger.error("Failed to scale deployment %s: %s", name, e)
            raise

    def create_hpa(self, name: str, min_replicas: int, max_replicas: int, cpu_utilization: int = 80):
        """
        Create a HorizontalPodAutoscaler pointing to the deployment.
        Note: apps/v1 HPA details and versioning differ; keep this minimal.
        """
        target = k8s_client.V1CrossVersionObjectReference(api_version="apps/v1", kind="Deployment", name=name)
        spec = k8s_client.V1HorizontalPodAutoscalerSpec(
            max_replicas=max_replicas,
            min_replicas=min_replicas,
            scale_target_ref=target,
            target_cpu_utilization_percentage=cpu_utilization,
        )
        hpa = k8s_client.V1HorizontalPodAutoscaler(metadata=k8s_client.V1ObjectMeta(name=name), spec=spec)
        try:
            self.autoscaling_api.create_namespaced_horizontal_pod_autoscaler(namespace=self.namespace, body=hpa)
        except ApiException as e:
            if e.status == 409:
                logger.info("HPA %s already exists", name)
            else:
                logger.error("Failed to create HPA %s: %s", name, e)
                raise

    def delete_hpa(self, name: str):
        try:
            self.autoscaling_api.delete_namespaced_horizontal_pod_autoscaler(name=name, namespace=self.namespace)
        except ApiException as e:
            if e.status == 404:
                logger.debug("HPA %s not found during delete", name)
            else:
                logger.warning("Failed to delete HPA %s: %s", name, e)


class K8sAgentAdapter(Agent):
    """
    Adapter subclass of Agent that provisions a Kubernetes Deployment to host the agent process.
    Provide production hooks for autoscaling, readiness checks, and secrets injection.

    Usage:
      agent = K8sAgentAdapter(
          name="my-agent",
          instruction="You are...",
          server_names=["filesystem"],
          k8s_image="ghcr.io/org/my-agent:latest",
          k8s_namespace="agents",
          k8s_replicas=1,
      )

      async with agent:
          llm = await agent.attach_llm(OpenAIAugmentedLLM)
          result = await llm.generate_str("Hello")
    """

    k8s_image: Optional[str] = None
    k8s_namespace: str = "default"
    k8s_replicas: int = 1
    k8s_labels: Dict[str, str] = Field(default_factory=dict)
    k8s_env: Dict[str, str] = Field(default_factory=dict)
    k8s_resources: Dict[str, Any] = Field(default_factory=dict)
    k8s_port: Optional[int] = None
    k8s_autoscale: bool = False
    k8s_hpa_min_replicas: int = 1
    k8s_hpa_max_replicas: int = 3

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def __init__(self, **data):
        super().__init__(**data)
        # Initialize Kubernetes manager lazily so config is available
        self._k8s_manager: Optional[KubernetesManager] = None
        self._deployment_name = f"mcp-agent-{self.name}"
        # Hook: determine labels if none provided
        if not self.k8s_labels:
            self.k8s_labels = {"app": self._deployment_name}

    def _ensure_k8s_manager(self):
        if not self._k8s_manager:
            self._k8s_manager = KubernetesManager(namespace=self.k8s_namespace)

    async def initialize(self, force: bool = False):
        """
        Override initialize to provision k8s Deployment and wait for readiness.
        This intentionally avoids calling super().initialize() by default because we
        assume the agent process runs inside the pod and will establish MCP connections itself.
        If you need local aggregator initialization, call super().initialize() at the end.
        """
        if self.initialized and not force:
            return

        logger.info("K8sAgentAdapter initializing agent %s: provisioning deployment %s", self.name, self._deployment_name)
        tracer = None
        # optional: integrate tracer from context if needed

        # Provision k8s deployment
        self._ensure_k8s_manager()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._k8s_manager.create_deployment(
                name=self._deployment_name,
                image=self.k8s_image or "",
                labels=self.k8s_labels,
                container_port=self.k8s_port,
                replicas=self.k8s_replicas,
                env=self.k8s_env,
                resources=self.k8s_resources,
            ),
        )

        # Optionally create HPA
        if self.k8s_autoscale:
            await loop.run_in_executor(
                None,
                lambda: self._k8s_manager.create_hpa(
                    name=self._deployment_name,
                    min_replicas=self.k8s_hpa_min_replicas,
                    max_replicas=self.k8s_hpa_max_replicas,
                ),
            )

        # Wait for at least one Pod Ready - implement a simple polling check here.
        await self._wait_for_pod_ready(timeout_seconds=120)

        # Mark initialized (we do not call super().initialize() by default)
        self.initialized = True
        logger.info("K8sAgentAdapter provisioning complete for %s", self.name)

    async def _wait_for_pod_ready(self, timeout_seconds: int = 120):
        """
        Simple readiness wait: checks Pod statuses for matching labels and looks for Ready condition.
        Production: replace with richer checks and exponential backoff.
        """
        self._ensure_k8s_manager()
        start = asyncio.get_event_loop().time()
        while True:
            if asyncio.get_event_loop().time() - start > timeout_seconds:
                raise TimeoutError("Timed out waiting for pod to become ready")
            try:
                pods = self._k8s_manager.core_api.list_namespaced_pod(namespace=self.k8s_namespace, label_selector=",".join([f"{k}={v}" for k, v in self.k8s_labels.items()]))
                for p in pods.items:
                    conds = p.status.conditions or []
                    for c in conds:
                        if c.type == "Ready" and c.status == "True":
                            logger.info("Found ready pod %s for deployment %s", p.metadata.name, self._deployment_name)
                            return
            except Exception as e:
                logger.debug("Waiting for pod ready: %s", e)
            await asyncio.sleep(2)

    async def shutdown(self):
        """
        Override shutdown to delete HPA and deployment if desired. Respect connection_persistence.
        """
        logger.info("K8sAgentAdapter shutting down agent %s", self.name)
        # If connection_persistence is desired, we may opt to keep deployment running; otherwise delete it.
        if getattr(self, "connection_persistence", False):
            logger.info("connection_persistence=True, leaving deployment %s running", self._deployment_name)
            # Optionally we could scale to 0 or leave running based on config
            return

        # Delete HPA if present and delete deployment
        if self._k8s_manager:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._k8s_manager.delete_hpa(self._deployment_name))
            await loop.run_in_executor(None, lambda: self._k8s_manager.delete_deployment(self._deployment_name))
        self.initialized = False

    # Expose autoscaling helpers
    async def scale(self, replicas: int):
        self._ensure_k8s_manager()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: self._k8s_manager.scale_deployment(self._deployment_name, replicas))

    async def enable_autoscale(self, min_replicas: int = 1, max_replicas: int = 3, cpu_utilization: int = 80):
        self.k8s_autoscale = True
        self.k8s_hpa_min_replicas = min_replicas
        self.k8s_hpa_max_replicas = max_replicas
        self.k8s_hpa_cpu = cpu_utilization
        self._ensure_k8s_manager()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: self._k8s_manager.create_hpa(self._deployment_name, min_replicas, max_replicas, cpu_utilization))

    # Hook: override attach_llm to customize behavior when the agent is remote vs local
    async def attach_llm(self, llm_factory: Optional[Callable[..., Any]] = None, llm: Any | None = None):
        """
        By default we call super().attach_llm which will create a local LLM attached to this agent instance.
        Many production setups will want the agent inside the pod to attach LLMs itself and not create them locally.
        You can override behavior by passing a custom llm_factory or handling attach differently.
        """
        # Default behavior: forward to parent (local LLM). If you want remote LLM, implement RPC/connector.
        return await super().attach_llm(llm_factory=llm_factory, llm=llm)