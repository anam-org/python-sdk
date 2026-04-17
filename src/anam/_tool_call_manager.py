"""Tool call lifecycle manager.

Handles the started → completed/failed lifecycle for tool calls received
over the WebRTC data channel, including handler dispatch and execution
time tracking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .types import (
    AnamEvent,
    ToolCallCompletedPayload,
    ToolCallFailedPayload,
    ToolCallHandler,
    ToolCallStartedPayload,
)

logger = logging.getLogger(__name__)

# Type for the emit callback
EmitCallback = Callable[..., Awaitable[None]]

# Type for the send_tool_result callback (session_id, tool_call_id,
# user_action_correlation_id, timestamp_user_action, result, error_message)
SendToolResultCallback = Callable[[str, str, str, str, str | None, str | None], None]


@dataclass
class _PendingToolCall:
    """Tracks an in-flight tool call for execution time calculation."""

    tool_call_id: str
    tool_name: str
    started_at: datetime


class ToolCallManager:
    """Manages tool call lifecycle events.

    Responsibilities:
    - Maintains a registry of per-tool-name handlers.
    - Tracks pending (in-flight) tool calls for execution time calculation.
    - Converts wire-format (snake_case) events to public payload dataclasses.
    - For client tools, auto-completes or auto-fails based on handler result,
      and sends the result/error back to the engine over the data channel.
    - Filters events by active session to avoid processing stale events.
    """

    def __init__(
        self,
        emit: EmitCallback,
        send_tool_result: SendToolResultCallback | None = None,
    ) -> None:
        self._emit = emit
        self._send_tool_result = send_tool_result
        self._handlers: dict[str, ToolCallHandler] = {}
        self._pending_calls: dict[str, _PendingToolCall] = {}
        self._failed_calls: dict[str, ToolCallFailedPayload] = {}
        self._active_session_id: str | None = None

    def set_send_tool_result(self, send_tool_result: SendToolResultCallback | None) -> None:
        """Set the callback used to send tool results back to the engine."""
        self._send_tool_result = send_tool_result

    def set_active_session(self, session_id: str) -> None:
        """Set the active session ID. Events with a different session_id are ignored."""
        self._active_session_id = session_id
        self.clear_pending_calls()
        self.clear_failed_calls()

    def clear_session_state(self) -> None:
        """Clear all session-scoped state (active session, pending + failed calls)."""
        self._active_session_id = None
        self.clear_pending_calls()
        self.clear_failed_calls()

    def register_handler(self, tool_name: str, handler: ToolCallHandler) -> Callable[[], None]:
        """Register a handler for a specific tool name.

        Args:
            tool_name: The name of the tool to handle.
            handler: A :class:`ToolCallHandler` subclass instance.

        Returns:
            An unsubscribe function that removes the handler when called.
        """
        self._handlers[tool_name] = handler

        def unsubscribe() -> None:
            if self._handlers.get(tool_name) is handler:
                del self._handlers[tool_name]

        return unsubscribe

    async def process_started_event(self, event: dict[str, Any]) -> None:
        """Process a toolCallStarted data channel message.

        For client tools with a registered handler:
        - If on_start returns a string, send the result back to the engine
          and auto-complete the call.
        - If on_start raises, send the error back to the engine and auto-fail.
        """
        session_id = event.get("session_id", "")
        if self._active_session_id is None or self._active_session_id != session_id:
            logger.debug(
                "Ignoring toolCallStarted for inactive session (event=%s, active=%s)",
                session_id,
                self._active_session_id,
            )
            return

        tool_call_id = event.get("tool_call_id", "")
        tool_name = event.get("tool_name", "")
        tool_type = event.get("tool_type", "")
        timestamp_str = event.get("timestamp", "")

        # Track the pending call (only if we have a non-empty tool_call_id)
        try:
            started_at = datetime.fromisoformat(timestamp_str)
        except (ValueError, TypeError):
            started_at = datetime.now(timezone.utc)

        if tool_call_id:
            self._pending_calls[tool_call_id] = _PendingToolCall(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                started_at=started_at,
            )
        else:
            logger.warning(
                "Received toolCallStarted event without a valid tool_call_id; "
                "skipping pending-call tracking."
            )

        # Build public payload
        payload = self._to_started_payload(event)

        # Emit public event
        await self._emit(AnamEvent.TOOL_CALL_STARTED, payload)

        # Dispatch to handler
        handler = self._handlers.get(tool_name)
        if handler and hasattr(handler, "on_start") and handler.on_start is not None:
            if tool_type == "client":
                # For client tools, send result/error back to engine and auto-complete/fail
                try:
                    result = await handler.on_start(payload)
                    self._send_client_tool_result(event, result=result, error_message=None)
                    # Auto-complete with the returned result
                    completed_payload = self._build_completed_payload(event, result=result)
                    await self._emit(AnamEvent.TOOL_CALL_COMPLETED, completed_payload)
                    if tool_call_id:
                        self._pending_calls.pop(tool_call_id, None)
                    # Dispatch to handler's on_complete callback
                    if hasattr(handler, "on_complete") and handler.on_complete is not None:
                        try:
                            await handler.on_complete(completed_payload)
                        except Exception as cb_err:
                            logger.error(
                                "Error in on_complete handler for tool '%s': %s",
                                tool_name,
                                cb_err,
                            )
                except Exception as e:
                    # Auto-fail: send error back to engine
                    error_message = f"Error in handler: {e}"
                    self._send_client_tool_result(event, result=None, error_message=error_message)
                    failed_payload = self._build_failed_payload(event, error_message=str(e))
                    await self._emit(AnamEvent.TOOL_CALL_FAILED, failed_payload)
                    if tool_call_id:
                        self._pending_calls.pop(tool_call_id, None)
                    # Track the failed call so a stale completed event doesn't double-process it
                    if tool_call_id:
                        self._failed_calls[tool_call_id] = failed_payload
                    # Dispatch to handler's on_fail callback
                    if hasattr(handler, "on_fail") and handler.on_fail is not None:
                        try:
                            await handler.on_fail(failed_payload)
                        except Exception as cb_err:
                            logger.error(
                                "Error in on_fail handler for tool '%s': %s",
                                tool_name,
                                cb_err,
                            )
            else:
                # For server tools, just call on_start informally
                try:
                    await handler.on_start(payload)
                except Exception as e:
                    logger.error(
                        "Error in on_start handler for server tool '%s': %s",
                        tool_name,
                        e,
                    )

    async def process_completed_event(self, event: dict[str, Any]) -> None:
        """Process a toolCallCompleted data channel message."""
        session_id = event.get("session_id", "")
        if self._active_session_id is None or self._active_session_id != session_id:
            logger.debug(
                "Ignoring toolCallCompleted for inactive session (event=%s, active=%s)",
                session_id,
                self._active_session_id,
            )
            return

        tool_call_id = event.get("tool_call_id", "")
        tool_name = event.get("tool_name", "")

        # If this call was previously marked as failed, do not process it as completed
        if tool_call_id and tool_call_id in self._failed_calls:
            del self._failed_calls[tool_call_id]
            return

        # Build payload (calculates execution time from pending call)
        payload = self._to_completed_payload(event)

        # Clean up pending call
        self._pending_calls.pop(tool_call_id, None)

        # Emit public event
        await self._emit(AnamEvent.TOOL_CALL_COMPLETED, payload)

        # Dispatch to handler
        handler = self._handlers.get(tool_name)
        if handler and hasattr(handler, "on_complete") and handler.on_complete is not None:
            try:
                await handler.on_complete(payload)
            except Exception as e:
                logger.error(
                    "Error in on_complete handler for tool '%s': %s",
                    tool_name,
                    e,
                )

    async def process_failed_event(self, event: dict[str, Any]) -> None:
        """Process a toolCallFailed data channel message."""
        session_id = event.get("session_id", "")
        if self._active_session_id is None or self._active_session_id != session_id:
            logger.debug(
                "Ignoring toolCallFailed for inactive session (event=%s, active=%s)",
                session_id,
                self._active_session_id,
            )
            return

        tool_call_id = event.get("tool_call_id", "")
        tool_name = event.get("tool_name", "")

        # Build payload (calculates execution time from pending call)
        payload = self._to_failed_payload(event)

        # Mark the call as failed so a late completed event is ignored
        if tool_call_id:
            self._failed_calls[tool_call_id] = payload

        # Clean up pending call
        self._pending_calls.pop(tool_call_id, None)

        # Emit public event
        await self._emit(AnamEvent.TOOL_CALL_FAILED, payload)

        # Dispatch to handler
        handler = self._handlers.get(tool_name)
        if handler and hasattr(handler, "on_fail") and handler.on_fail is not None:
            try:
                await handler.on_fail(payload)
            except Exception as e:
                logger.error(
                    "Error in on_fail handler for tool '%s': %s",
                    tool_name,
                    e,
                )

    def clear_pending_calls(self) -> None:
        """Clear all pending tool calls (e.g., on session close)."""
        self._pending_calls.clear()

    def clear_failed_calls(self) -> None:
        """Clear all tracked failed calls (e.g., on session close)."""
        self._failed_calls.clear()

    # --- Internal helpers ---

    def _send_client_tool_result(
        self,
        started_event: dict[str, Any],
        result: str | None,
        error_message: str | None,
    ) -> None:
        """Send a tool_result data channel message for a client tool."""
        if self._send_tool_result is None:
            logger.debug(
                "No send_tool_result callback configured; skipping tool_result send."
            )
            return

        session_id = started_event.get("session_id", "")
        tool_call_id = started_event.get("tool_call_id", "")
        user_action_correlation_id = started_event.get("user_action_correlation_id", "")
        timestamp_user_action = started_event.get("timestamp_user_action", "")

        try:
            self._send_tool_result(
                session_id,
                tool_call_id,
                user_action_correlation_id,
                timestamp_user_action,
                result,
                error_message,
            )
        except Exception as e:
            logger.error("Failed to send tool_result for tool_call_id=%s: %s", tool_call_id, e)

    # --- Payload conversion helpers ---

    def _calculate_execution_time(self, tool_call_id: str, timestamp_str: str) -> float:
        """Calculate execution time in ms from pending call start to the given timestamp."""
        pending = self._pending_calls.get(tool_call_id)
        if not pending:
            return 0.0
        try:
            end_time = datetime.fromisoformat(timestamp_str)
        except (ValueError, TypeError):
            end_time = datetime.now(timezone.utc)
        # Normalize both datetimes to UTC if they are timezone-naive to avoid TypeError
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        started_at = pending.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        delta = end_time - started_at
        return delta.total_seconds() * 1000

    @staticmethod
    def _to_started_payload(event: dict[str, Any]) -> ToolCallStartedPayload:
        return ToolCallStartedPayload(
            event_uid=event.get("event_uid", ""),
            session_id=event.get("session_id", ""),
            tool_call_id=event.get("tool_call_id", ""),
            tool_name=event.get("tool_name", ""),
            tool_type=event.get("tool_type", ""),
            tool_subtype=event.get("tool_subtype"),
            arguments=event.get("arguments", {}),
            timestamp=event.get("timestamp", ""),
        )

    def _to_completed_payload(self, event: dict[str, Any]) -> ToolCallCompletedPayload:
        tool_call_id = event.get("tool_call_id", "")
        timestamp = event.get("timestamp", "")
        return ToolCallCompletedPayload(
            event_uid=event.get("event_uid", ""),
            session_id=event.get("session_id", ""),
            tool_call_id=tool_call_id,
            tool_name=event.get("tool_name", ""),
            tool_type=event.get("tool_type", ""),
            tool_subtype=event.get("tool_subtype"),
            result=event.get("result"),
            execution_time=self._calculate_execution_time(tool_call_id, timestamp),
            timestamp=timestamp,
            documents_accessed=event.get("documents_accessed"),
        )

    def _to_failed_payload(self, event: dict[str, Any]) -> ToolCallFailedPayload:
        tool_call_id = event.get("tool_call_id", "")
        timestamp = event.get("timestamp", "")
        return ToolCallFailedPayload(
            event_uid=event.get("event_uid", ""),
            session_id=event.get("session_id", ""),
            tool_call_id=tool_call_id,
            tool_name=event.get("tool_name", ""),
            tool_type=event.get("tool_type", ""),
            tool_subtype=event.get("tool_subtype"),
            error_message=event.get("error_message", ""),
            execution_time=self._calculate_execution_time(tool_call_id, timestamp),
            timestamp=timestamp,
        )

    def _build_completed_payload(
        self, started_event: dict[str, Any], result: Any
    ) -> ToolCallCompletedPayload:
        """Build a completed payload from a started event (for client tool auto-complete)."""
        tool_call_id = started_event.get("tool_call_id", "")
        completion_time = datetime.now(timezone.utc).isoformat()
        return ToolCallCompletedPayload(
            event_uid=started_event.get("event_uid", ""),
            session_id=started_event.get("session_id", ""),
            tool_call_id=tool_call_id,
            tool_name=started_event.get("tool_name", ""),
            tool_type=started_event.get("tool_type", ""),
            tool_subtype=started_event.get("tool_subtype"),
            result=result,
            execution_time=self._calculate_execution_time(tool_call_id, completion_time),
            timestamp=completion_time,
        )

    def _build_failed_payload(
        self, started_event: dict[str, Any], error_message: str
    ) -> ToolCallFailedPayload:
        """Build a failed payload from a started event (for client tool auto-fail)."""
        tool_call_id = started_event.get("tool_call_id", "")
        completion_time = datetime.now(timezone.utc).isoformat()
        return ToolCallFailedPayload(
            event_uid=started_event.get("event_uid", ""),
            session_id=started_event.get("session_id", ""),
            tool_call_id=tool_call_id,
            tool_name=started_event.get("tool_name", ""),
            tool_type=started_event.get("tool_type", ""),
            tool_subtype=started_event.get("tool_subtype"),
            error_message=error_message,
            execution_time=self._calculate_execution_time(tool_call_id, completion_time),
            timestamp=completion_time,
        )
