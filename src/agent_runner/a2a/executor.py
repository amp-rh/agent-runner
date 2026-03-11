"""A2A AgentExecutor: bridge incoming A2A tasks to the Claude Agent SDK."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent, TextPart

if TYPE_CHECKING:
    from agent_runner.agent import AgentRunner


class ClaudeAgentExecutor(AgentExecutor):
    """Execute A2A tasks by delegating to the Claude Agent SDK."""

    def __init__(self, agent_runner: AgentRunner):
        self._runner = agent_runner

    async def execute(self, context, event_queue: EventQueue):
        """Extract prompt from A2A task context, run via AgentRunner, push result."""
        # Extract the user's prompt from the A2A message
        prompt = ""
        if hasattr(context, "message") and context.message:
            for part in context.message.parts:
                if isinstance(part, TextPart):
                    prompt += part.text

        if not prompt:
            prompt = "No prompt provided."

        task_id = getattr(context, "task_id", None) or str(uuid.uuid4())
        context_id = getattr(context, "context_id", None) or str(uuid.uuid4())

        try:
            result = await self._runner.run(prompt)
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    taskId=task_id,
                    contextId=context_id,
                    append=False,
                    artifact={
                        "artifactId": str(uuid.uuid4()),
                        "parts": [{"type": "text", "text": result}],
                    },
                )
            )
        except Exception as exc:
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    taskId=task_id,
                    contextId=context_id,
                    append=False,
                    artifact={
                        "artifactId": str(uuid.uuid4()),
                        "parts": [{"type": "text", "text": f"Error: {exc}"}],
                    },
                )
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        from a2a.types import TaskStatus
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                taskId=context.task_id,
                contextId=context.context_id,
                status=TaskStatus(state=TaskState.canceled),
                final=True,
            )
        )
