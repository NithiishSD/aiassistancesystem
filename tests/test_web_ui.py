"""The local browser UI (OpenSpec change: local-web-ui). No real model or network."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

import confirmation
import web_ui

PORT = 8765
TOKEN = "test-token"
HOST = {"host": f"127.0.0.1:{PORT}"}
AUTH = {**HOST, "x-zedek-token": TOKEN}


def _client(turn_runner=None, command_runner=None, confirm_timeout=5):
    session = web_ui.UiSession(turn_runner=turn_runner or (lambda text, stream=None: f"echo: {text}"),
                               command_runner=command_runner or (lambda text: None),
                               confirm_timeout=confirm_timeout)
    return TestClient(web_ui.create_app(session, TOKEN, PORT)), session


def _wait_for(client, kind, since=0, timeout=5):
    deadline, position, seen = time.time() + timeout, since, []
    while time.time() < deadline:
        data = client.get(f"/api/events?since={position}&wait=0.2", headers=AUTH).json()
        seen.extend(data["events"])
        position = data["next"]
        for event in seen:
            if event["type"] == kind:
                return event, seen, position
    raise AssertionError(f"no {kind!r} event; saw {[e['type'] for e in seen]}")


class TestAccess:
    def test_page_is_served_with_strict_headers(self):
        client, _ = _client()
        response = client.get("/", headers=HOST)
        assert response.status_code == 200 and "<title>Zedek</title>" in response.text
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"
        assert TOKEN not in response.text

    def test_page_never_builds_html_from_text(self):
        assert "innerHTML" not in web_ui.PAGE and "textContent" in web_ui.PAGE

    @pytest.mark.parametrize("headers", [HOST, {**HOST, "x-zedek-token": "wrong"}, {**HOST, "x-zedek-token": ""}])
    def test_api_needs_the_token(self, headers):
        client, session = _client()
        assert client.get("/api/events", headers=headers).status_code == 401
        assert client.post("/api/message", json={"text": "hi"}, headers=headers).status_code == 401
        assert not session.busy

    @pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1:9999", "192.168.1.5:8765"])
    def test_foreign_host_refused_even_with_the_token(self, host):
        client, _ = _client()
        assert client.get("/", headers={"host": host}).status_code == 403
        assert client.post("/api/message", json={"text": "hi"},
                           headers={"host": host, "x-zedek-token": TOKEN}).status_code == 403

    @pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:9999", "null"])
    def test_cross_site_origin_refused(self, origin):
        client, session = _client()
        response = client.post("/api/message", json={"text": "hi"}, headers={**AUTH, "origin": origin})
        assert response.status_code == 403 and not session.busy

    def test_own_origin_allowed(self):
        client, _ = _client()
        ok = client.post("/api/message", json={"text": "hi"}, headers={**AUTH, "origin": f"http://localhost:{PORT}",
                                                                      "host": f"localhost:{PORT}"})
        assert ok.status_code == 200

    def test_request_allowed_table(self):
        assert web_ui.request_allowed("127.0.0.1:8765", None, 8765)
        assert web_ui.request_allowed("LOCALHOST:8765", "http://localhost:8765", 8765)
        assert not web_ui.request_allowed(None, None, 8765)
        assert not web_ui.request_allowed("127.0.0.1:8765", "http://127.0.0.1:8765.evil.example", 8765)


class TestTurns:
    def test_message_streams_then_answers(self):
        def runner(text, stream=None):
            stream.delta("Hello ")
            stream.delta("there.")
            return "Hello there."

        client, _ = _client(turn_runner=runner)
        assert client.post("/api/message", json={"text": "hi"}, headers=AUTH).json() == {"ok": True}
        answer, seen, _ = _wait_for(client, "answer")
        assert [e["type"] for e in seen] == ["user", "delta", "delta", "answer"]
        assert answer["text"] == "Hello there."

    def test_provider_restart_is_forwarded(self):
        def runner(text, stream=None):
            stream.delta("Half")
            stream.restart()
            stream.delta("Whole.")
            return "Whole."

        client, _ = _client(turn_runner=runner)
        client.post("/api/message", json={"text": "hi"}, headers=AUTH)
        _, seen, _ = _wait_for(client, "answer")
        assert [e["type"] for e in seen] == ["user", "delta", "restart", "delta", "answer"]

    def test_one_turn_at_a_time(self):
        release = threading.Event()
        client, session = _client(turn_runner=lambda text, stream=None: release.wait(5) and "done")
        assert client.post("/api/message", json={"text": "first"}, headers=AUTH).status_code == 200
        assert client.post("/api/message", json={"text": "second"}, headers=AUTH).status_code == 409
        release.set()
        _wait_for(client, "answer")
        assert client.post("/api/message", json={"text": "third"}, headers=AUTH).status_code == 200

    @pytest.mark.parametrize("body,status", [({"text": "   "}, 400), ({}, 400), ({"text": "x" * 4001}, 413)])
    def test_bad_messages(self, body, status):
        client, session = _client()
        assert client.post("/api/message", json=body, headers=AUTH).status_code == status
        assert not session.busy

    def test_failing_turn_still_ends(self):
        def runner(text, stream=None):
            raise RuntimeError("provider down")

        client, session = _client(turn_runner=runner)
        client.post("/api/message", json={"text": "hi"}, headers=AUTH)
        answer, _, _ = _wait_for(client, "answer")
        assert "provider down" in answer["text"] and not session.busy

    def test_repl_commands_bypass_the_pipeline(self):
        ran = []
        client, _ = _client(turn_runner=lambda text, stream=None: ran.append(text) or "x",
                            command_runner=lambda text: "Your inbox is empty." if text == "inbox" else None)
        client.post("/api/message", json={"text": "inbox"}, headers=AUTH)
        answer, _, _ = _wait_for(client, "answer")
        assert answer["text"] == "Your inbox is empty." and ran == []

    def test_event_log_is_bounded(self):
        session = web_ui.UiSession(turn_runner=lambda *a, **k: "", command_runner=lambda t: None)
        for n in range(web_ui.MAX_EVENTS + 50):
            session.publish("notice", text=str(n))
        events, position = session.events_since(0)
        assert len(events) == web_ui.MAX_EVENTS and position == web_ui.MAX_EVENTS + 50
        assert session.events_since(position)[0] == []


class TestConfirmation:
    def _runner(self, outcome):
        def runner(text, stream=None):
            answer = confirmation.ask("Delete the file?")
            outcome.append(answer)
            confirmation.notify("done asking")
            return "deleted" if answer else "Cancelled."
        return runner

    def test_approve_in_the_page(self):
        outcome = []
        client, _ = _client(turn_runner=self._runner(outcome))
        client.post("/api/message", json={"text": "delete it"}, headers=AUTH)
        request, _, position = _wait_for(client, "confirm")
        assert request["message"] == "Delete the file?"
        assert client.post("/api/confirm", json={"id": request["id"], "reply": "y"}, headers=AUTH).status_code == 200
        answer, seen, _ = _wait_for(client, "answer", since=position)
        assert answer["text"] == "deleted" and outcome == [confirmation.Answer(True)]
        assert {"type": "notice", "text": "done asking"} in seen

    def test_decline_with_a_reason(self):
        outcome = []
        client, _ = _client(turn_runner=self._runner(outcome))
        client.post("/api/message", json={"text": "delete it"}, headers=AUTH)
        request, _, _ = _wait_for(client, "confirm")
        client.post("/api/confirm", json={"id": request["id"], "reply": "no keep the backup"}, headers=AUTH)
        _wait_for(client, "answer")
        assert outcome == [confirmation.Answer(False, "keep the backup")]

    def test_no_answer_is_a_refusal(self):
        outcome = []
        client, _ = _client(turn_runner=self._runner(outcome), confirm_timeout=0.2)
        client.post("/api/message", json={"text": "delete it"}, headers=AUTH)
        answer, _, _ = _wait_for(client, "answer")
        assert answer["text"] == "Cancelled." and outcome == [confirmation.Answer(False)]

    def test_unknown_or_stale_request(self):
        client, _ = _client()
        assert client.post("/api/confirm", json={"id": "nope", "reply": "y"}, headers=AUTH).status_code == 404

    def test_confirm_needs_the_token(self):
        outcome = []
        client, _ = _client(turn_runner=self._runner(outcome), confirm_timeout=0.5)
        client.post("/api/message", json={"text": "delete it"}, headers=AUTH)
        request, _, _ = _wait_for(client, "confirm")
        assert client.post("/api/confirm", json={"id": request["id"], "reply": "y"}, headers=HOST).status_code == 401
        _wait_for(client, "answer")
        assert outcome == [confirmation.Answer(False)]

    def test_terminal_channel_is_back_after_the_turn(self):
        client, _ = _client(turn_runner=self._runner([]), confirm_timeout=0.1)
        client.post("/api/message", json={"text": "x"}, headers=AUTH)
        _wait_for(client, "answer")
        assert isinstance(confirmation.get_channel(), confirmation.TerminalChannel)
