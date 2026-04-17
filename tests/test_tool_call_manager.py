"""Tests for ToolCallManager."""

import pytest

from anam._tool_call_manager import ToolCallManager
from anam.types import (
    AnamEvent,
    ToolCallCompletedPayload,
    ToolCallFailedPayload,
    ToolCallStartedPayload,
)


def _make_started_event(**overrides):
    """Create a toolCallStarted event dict."""
    event = {
        "event_uid": "evt-1",
        "session_id": "sess-1",
        "tool_call_id": "tc-1",
        "tool_name": "redirect",
        "tool_type": "client",
        "tool_subtype": None,
        "arguments": {"url": "https://example.com"},
        "timestamp": "2025-01-01T00:00:00+00:00",
        "timestamp_user_action": "2025-01-01T00:00:00+00:00",
        "user_action_correlation_id": "corr-1",
        "used_outside_engine": True,
    }
    event.update(overrides)
    return event


def _make_completed_event(**overrides):
    """Create a toolCallCompleted event dict."""
    event = _make_started_event()
    event.update(
        {
            "result": "success",
            "timestamp": "2025-01-01T00:00:01+00:00",  # 1 second later
        }
    )
    event.update(overrides)
    return event


def _make_failed_event(**overrides):
    """Create a toolCallFailed event dict."""
    event = _make_started_event()
    event.update(
        {
            "error_message": "something went wrong",
            "timestamp": "2025-01-01T00:00:02+00:00",  # 2 seconds later
        }
    )
    event.update(overrides)
    return event


class TestToolCallManager:
    """Tests for ToolCallManager."""

    @pytest.fixture
    def emitted_events(self):
        return []

    @pytest.fixture
    def sent_tool_results(self):
        return []

    @pytest.fixture
    def manager(self, emitted_events, sent_tool_results):
        async def emit(event, *args, **kwargs):
            emitted_events.append((event, args, kwargs))

        def send_tool_result(
            session_id,
            tool_call_id,
            user_action_correlation_id,
            timestamp_user_action,
            result,
            error_message,
        ):
            sent_tool_results.append(
                {
                    "session_id": session_id,
                    "tool_call_id": tool_call_id,
                    "user_action_correlation_id": user_action_correlation_id,
                    "timestamp_user_action": timestamp_user_action,
                    "result": result,
                    "error_message": error_message,
                }
            )

        mgr = ToolCallManager(emit=emit, send_tool_result=send_tool_result)
        mgr.set_active_session("sess-1")
        return mgr

    @pytest.mark.asyncio
    async def test_started_event_emits_public_event(self, manager, emitted_events):
        """Test that processing a started event emits TOOL_CALL_STARTED."""
        await manager.process_started_event(_make_started_event())

        assert len(emitted_events) == 1
        event_type, args, _ = emitted_events[0]
        assert event_type == AnamEvent.TOOL_CALL_STARTED
        payload = args[0]
        assert isinstance(payload, ToolCallStartedPayload)
        assert payload.tool_call_id == "tc-1"
        assert payload.tool_name == "redirect"
        assert payload.tool_type == "client"
        assert payload.arguments == {"url": "https://example.com"}

    @pytest.mark.asyncio
    async def test_completed_event_emits_public_event(self, manager, emitted_events):
        """Test that processing a completed event emits TOOL_CALL_COMPLETED."""
        # First process started to track pending call
        await manager.process_started_event(_make_started_event())
        emitted_events.clear()

        await manager.process_completed_event(_make_completed_event())

        assert len(emitted_events) == 1
        event_type, args, _ = emitted_events[0]
        assert event_type == AnamEvent.TOOL_CALL_COMPLETED
        payload = args[0]
        assert isinstance(payload, ToolCallCompletedPayload)
        assert payload.tool_call_id == "tc-1"
        assert payload.result == "success"

    @pytest.mark.asyncio
    async def test_failed_event_emits_public_event(self, manager, emitted_events):
        """Test that processing a failed event emits TOOL_CALL_FAILED."""
        await manager.process_started_event(_make_started_event())
        emitted_events.clear()

        await manager.process_failed_event(_make_failed_event())

        assert len(emitted_events) == 1
        event_type, args, _ = emitted_events[0]
        assert event_type == AnamEvent.TOOL_CALL_FAILED
        payload = args[0]
        assert isinstance(payload, ToolCallFailedPayload)
        assert payload.tool_call_id == "tc-1"
        assert payload.error_message == "something went wrong"

    @pytest.mark.asyncio
    async def test_execution_time_calculation(self, manager, emitted_events):
        """Test that execution time is calculated from started to completed."""
        await manager.process_started_event(_make_started_event())
        emitted_events.clear()

        await manager.process_completed_event(_make_completed_event())

        payload = emitted_events[0][1][0]
        # Started at T+0, completed at T+1s = 1000ms
        assert payload.execution_time == pytest.approx(1000.0, abs=1.0)

    @pytest.mark.asyncio
    async def test_execution_time_on_failure(self, manager, emitted_events):
        """Test that execution time is calculated on failure."""
        await manager.process_started_event(_make_started_event())
        emitted_events.clear()

        await manager.process_failed_event(_make_failed_event())

        payload = emitted_events[0][1][0]
        # Started at T+0, failed at T+2s = 2000ms
        assert payload.execution_time == pytest.approx(2000.0, abs=1.0)

    @pytest.mark.asyncio
    async def test_client_tool_auto_complete_on_handler_return(
        self, manager, emitted_events, sent_tool_results
    ):
        """Test that client tools auto-complete when handler returns a string."""

        class MyHandler:
            async def on_start(self, payload):
                return "my_result"

            async def on_complete(self, payload):
                pass

            async def on_fail(self, payload):
                pass

        manager.register_handler("redirect", MyHandler())
        await manager.process_started_event(_make_started_event())

        # Should emit STARTED then COMPLETED
        assert len(emitted_events) == 2
        assert emitted_events[0][0] == AnamEvent.TOOL_CALL_STARTED
        assert emitted_events[1][0] == AnamEvent.TOOL_CALL_COMPLETED
        completed_payload = emitted_events[1][1][0]
        assert completed_payload.result == "my_result"

        # Should send the result back to the engine over the data channel
        assert len(sent_tool_results) == 1
        sent = sent_tool_results[0]
        assert sent["session_id"] == "sess-1"
        assert sent["tool_call_id"] == "tc-1"
        assert sent["user_action_correlation_id"] == "corr-1"
        assert sent["timestamp_user_action"] == "2025-01-01T00:00:00+00:00"
        assert sent["result"] == "my_result"
        assert sent["error_message"] is None

    @pytest.mark.asyncio
    async def test_client_tool_no_result_not_sent(
        self, manager, emitted_events, sent_tool_results
    ):
        """Returning None from on_start should NOT send a tool_result or auto-complete."""

        class MyHandler:
            async def on_start(self, payload):
                return None

        manager.register_handler("redirect", MyHandler())
        await manager.process_started_event(_make_started_event())

        # Only STARTED is emitted, no auto-complete and no tool_result sent
        assert [e[0] for e in emitted_events] == [AnamEvent.TOOL_CALL_STARTED]
        assert sent_tool_results == []

    @pytest.mark.asyncio
    async def test_client_tool_auto_fail_on_handler_exception(
        self, manager, emitted_events, sent_tool_results
    ):
        """Test that client tools auto-fail when handler raises."""

        class MyHandler:
            async def on_start(self, payload):
                raise ValueError("handler error")

            async def on_complete(self, payload):
                pass

            async def on_fail(self, payload):
                pass

        manager.register_handler("redirect", MyHandler())
        await manager.process_started_event(_make_started_event())

        # Should emit STARTED then FAILED
        assert len(emitted_events) == 2
        assert emitted_events[0][0] == AnamEvent.TOOL_CALL_STARTED
        assert emitted_events[1][0] == AnamEvent.TOOL_CALL_FAILED
        failed_payload = emitted_events[1][1][0]
        assert "handler error" in failed_payload.error_message

        # Should send the error back to the engine
        assert len(sent_tool_results) == 1
        sent = sent_tool_results[0]
        assert sent["result"] is None
        assert "handler error" in sent["error_message"]

    @pytest.mark.asyncio
    async def test_failed_call_blocks_subsequent_completed(
        self, manager, emitted_events
    ):
        """A completed event arriving after a failure should be ignored."""

        class MyHandler:
            async def on_start(self, payload):
                raise ValueError("handler error")

        manager.register_handler("redirect", MyHandler())
        await manager.process_started_event(_make_started_event())
        emitted_events.clear()

        # Stale completed event for the same tool_call_id should be ignored
        await manager.process_completed_event(_make_completed_event())
        assert emitted_events == []

    @pytest.mark.asyncio
    async def test_events_filtered_by_active_session(self, manager, emitted_events):
        """Events whose session_id doesn't match the active session are dropped."""
        await manager.process_started_event(_make_started_event(session_id="sess-other"))
        await manager.process_completed_event(_make_completed_event(session_id="sess-other"))
        await manager.process_failed_event(_make_failed_event(session_id="sess-other"))

        assert emitted_events == []

    @pytest.mark.asyncio
    async def test_clear_session_state_drops_subsequent_events(
        self, manager, emitted_events
    ):
        """After clear_session_state, subsequent events are ignored until a new session is set."""
        manager.clear_session_state()
        await manager.process_started_event(_make_started_event())
        assert emitted_events == []

    @pytest.mark.asyncio
    async def test_server_tool_does_not_auto_complete(self, manager, emitted_events):
        """Test that server tools don't auto-complete from handler return."""

        class MyHandler:
            async def on_start(self, payload):
                return "ignored_for_server"

            async def on_complete(self, payload):
                pass

            async def on_fail(self, payload):
                pass

        manager.register_handler("search", MyHandler())
        await manager.process_started_event(
            _make_started_event(tool_name="search", tool_type="server")
        )

        # Should only emit STARTED (no auto-complete for server tools)
        assert len(emitted_events) == 1
        assert emitted_events[0][0] == AnamEvent.TOOL_CALL_STARTED

    @pytest.mark.asyncio
    async def test_register_handler_returns_unsubscribe(self, manager, emitted_events):
        """Test that register_handler returns a working unsubscribe function."""
        call_count = 0

        class MyHandler:
            async def on_start(self, payload):
                nonlocal call_count
                call_count += 1
                return "result"

            async def on_complete(self, payload):
                pass

            async def on_fail(self, payload):
                pass

        unsubscribe = manager.register_handler("redirect", MyHandler())
        await manager.process_started_event(_make_started_event())
        assert call_count == 1

        unsubscribe()
        emitted_events.clear()
        await manager.process_started_event(_make_started_event(tool_call_id="tc-2"))
        # Handler should not be called after unsubscribe
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_completed_handler_called(self, manager, emitted_events):
        """Test that on_complete handler is called for completed events."""
        completed_payloads = []

        class MyHandler:
            async def on_start(self, payload):
                pass

            async def on_complete(self, payload):
                completed_payloads.append(payload)

            async def on_fail(self, payload):
                pass

        manager.register_handler("redirect", MyHandler())
        await manager.process_started_event(_make_started_event())
        await manager.process_completed_event(_make_completed_event())

        assert len(completed_payloads) == 1
        assert completed_payloads[0].result == "success"

    @pytest.mark.asyncio
    async def test_failed_handler_called(self, manager, emitted_events):
        """Test that on_fail handler is called for failed events."""
        failed_payloads = []

        class MyHandler:
            async def on_start(self, payload):
                pass

            async def on_complete(self, payload):
                pass

            async def on_fail(self, payload):
                failed_payloads.append(payload)

        manager.register_handler("redirect", MyHandler())
        await manager.process_started_event(_make_started_event())
        await manager.process_failed_event(_make_failed_event())

        assert len(failed_payloads) == 1
        assert failed_payloads[0].error_message == "something went wrong"

    @pytest.mark.asyncio
    async def test_clear_pending_calls(self, manager, emitted_events):
        """Test that clear_pending_calls removes tracked calls."""
        await manager.process_started_event(_make_started_event())
        assert len(manager._pending_calls) == 1

        manager.clear_pending_calls()
        assert len(manager._pending_calls) == 0

    @pytest.mark.asyncio
    async def test_no_handler_still_emits_events(self, manager, emitted_events):
        """Test that events are emitted even without a registered handler."""
        await manager.process_started_event(_make_started_event())
        await manager.process_completed_event(_make_completed_event())
        await manager.process_failed_event(_make_failed_event())

        event_types = [e[0] for e in emitted_events]
        assert AnamEvent.TOOL_CALL_STARTED in event_types
        assert AnamEvent.TOOL_CALL_COMPLETED in event_types
        assert AnamEvent.TOOL_CALL_FAILED in event_types

    @pytest.mark.asyncio
    async def test_tool_subtype_passed_through(self, manager, emitted_events):
        """Test that tool_subtype is included in payloads."""
        await manager.process_started_event(
            _make_started_event(tool_type="server", tool_subtype="webhook")
        )

        payload = emitted_events[0][1][0]
        assert payload.tool_subtype == "webhook"

    @pytest.mark.asyncio
    async def test_documents_accessed_on_completed(self, manager, emitted_events):
        """Test that documents_accessed is included in completed payloads."""
        await manager.process_started_event(_make_started_event())
        emitted_events.clear()

        await manager.process_completed_event(
            _make_completed_event(documents_accessed=["doc1.pdf", "doc2.pdf"])
        )

        payload = emitted_events[0][1][0]
        assert payload.documents_accessed == ["doc1.pdf", "doc2.pdf"]


class TestAnamClientToolCallIntegration:
    """Test tool call integration in AnamClient."""

    def test_register_tool_call_handler(self):
        """Test that register_tool_call_handler works on AnamClient."""
        from anam import AnamClient

        client = AnamClient(api_key="test-key", persona_id="test-persona")

        class MyHandler:
            async def on_start(self, payload):
                return "result"

            async def on_complete(self, payload):
                pass

            async def on_fail(self, payload):
                pass

        unsubscribe = client.register_tool_call_handler("redirect", MyHandler())
        assert callable(unsubscribe)

        # Verify handler is registered
        assert "redirect" in client._tool_call_manager._handlers

        # Unsubscribe and verify
        unsubscribe()
        assert "redirect" not in client._tool_call_manager._handlers

    @pytest.mark.asyncio
    async def test_handle_data_message_tool_call_started(self):
        """Test that _handle_data_message routes toolCallStarted messages."""
        from anam import AnamClient

        client = AnamClient(api_key="test-key", persona_id="test-persona")
        # Simulate a connected session so the manager accepts events
        client._tool_call_manager.set_active_session("sess-1")

        received = []

        @client.on(AnamEvent.TOOL_CALL_STARTED)
        async def handler(payload):
            received.append(payload)

        await client._handle_data_message(
            {
                "messageType": "toolCallStarted",
                "data": _make_started_event(),
            }
        )

        assert len(received) == 1
        assert received[0].tool_name == "redirect"

    @pytest.mark.asyncio
    async def test_handle_data_message_tool_call_completed(self):
        """Test that _handle_data_message routes toolCallCompleted messages."""
        from anam import AnamClient

        client = AnamClient(api_key="test-key", persona_id="test-persona")
        # Simulate a connected session so the manager accepts events
        client._tool_call_manager.set_active_session("sess-1")

        received = []

        @client.on(AnamEvent.TOOL_CALL_COMPLETED)
        async def handler(payload):
            received.append(payload)

        # First start, then complete
        await client._handle_data_message(
            {
                "messageType": "toolCallStarted",
                "data": _make_started_event(),
            }
        )
        await client._handle_data_message(
            {
                "messageType": "toolCallCompleted",
                "data": _make_completed_event(),
            }
        )

        assert len(received) == 1
        assert received[0].result == "success"

    @pytest.mark.asyncio
    async def test_handle_data_message_tool_call_failed(self):
        """Test that _handle_data_message routes toolCallFailed messages."""
        from anam import AnamClient

        client = AnamClient(api_key="test-key", persona_id="test-persona")
        # Simulate a connected session so the manager accepts events
        client._tool_call_manager.set_active_session("sess-1")

        received = []

        @client.on(AnamEvent.TOOL_CALL_FAILED)
        async def handler(payload):
            received.append(payload)

        await client._handle_data_message(
            {
                "messageType": "toolCallStarted",
                "data": _make_started_event(),
            }
        )
        await client._handle_data_message(
            {
                "messageType": "toolCallFailed",
                "data": _make_failed_event(),
            }
        )

        assert len(received) == 1
        assert received[0].error_message == "something went wrong"
