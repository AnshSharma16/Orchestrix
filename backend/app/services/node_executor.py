from typing import Any

from app.adapters.factory import AdapterFactory
from app.models.workflow_node import WorkflowNode
from app.core.logging import get_logger

logger = get_logger(__name__)


class NodeExecutor:

    async def execute(
        self,
        node: WorkflowNode,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:

        logger.info(
            "Node execution started",
            extra={
                "node_id": str(node.id),
                "node_type": node.node_type,
            },
        )
        adapter = AdapterFactory.get_adapter(
            node.node_type
        )

        return await adapter.execute(
            node.config,
            input_data,
        )