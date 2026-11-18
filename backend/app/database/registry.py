"""Model registry: importing this module registers every ORM model on
Base.metadata.

Import this (never individual models) wherever the complete schema must be
visible: Alembic's env.py, schema checks and tests.

Dependency direction: registry -> app.models -> base. Nothing under
app.models may import this module.
"""

from app.database.base import Base
from app.models import (
    NodeExecution,
    User,
    Workflow,
    WorkflowEdge,
    WorkflowExecution,
    WorkflowNode,
    Workspace,
)

__all__ = [
    "Base",
    "NodeExecution",
    "User",
    "Workflow",
    "WorkflowEdge",
    "WorkflowExecution",
    "WorkflowNode",
    "Workspace",
]