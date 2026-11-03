"""Synthetic realtime/batch integration tests; no real managed sessions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import socket
import threading
import time

from fastapi import WebSocketDisconnect
import pytest
from sqlalchemy import func, select

from ogk_server.db.models import Binding, Child, Command, Event, utcnow
from test_server import (agent_headers, complete, enroll, issue, parent_headers,
                         password_hash, server)


def command(server, credentials, *, key="command-1", type="lock_now", seconds=0, headers=None):
    return server[1].post("/api/v1/commands", headers=headers or parent_headers(server), json={
        "binding_id": credentials["binding_id"], "idempotency_key": key,
        "type": type, "seconds": seconds,
    })


def receive_type(ws, type):
    # Commands may be retried concurrently with an ACK confirmation.
    for _ in range(20):
        message = ws.receive_json()
        if message["type"] == type:
            return message
    raise AssertionError(f"No {type} frame")


@pytest.mark.parametrize("type,seconds", [("lock_now", 0), ("add_time", 900)])
def test_online_push_under_five_seconds_and_applied_ack(server, type, seconds):
    credentials = enroll(server)
    headers = parent_headers(server)
    with server[1].websocket_connect("/ws/agent", headers=agent_headers(credentials)) as ws:
        assert ws.receive_json()["type"] == "ready"
        started = time.monotonic()
        response = command(server, credentials, type=type, seconds=seconds, headers=headers)
        assert response.status_code == 202, response.text
        message = receive_type(ws, "command")
        assert time.monotonic() - started < 5
        assert message["command"] == type and message["seconds"] == seconds
        command_id = message["command_id"]
        assert server[1].get(f"/api/v1/commands/{command_id}", headers=headers).json()["status"] == "Pending"
        ack = {"type": "ack", "command_id": command_id, "status": "Applied"}
        ws.send_json(ack)
        assert receive_type(ws, "ack_received")["accepted"] is True
        ws.send_json(ack)
        assert receive_type(ws, "ack_received")["accepted"] is True
        ws.send_json({**ack, "status": "Rejected"})
        assert receive_type(ws, "ack_received")["accepted"] is False
        assert server[1].get(f"/api/v1/commands/{command_id}", headers=headers).json()["status"] == "Applied"


def test_outbox_survives_reconnect_and_retries_same_id(server):
    credentials = enroll(server)
    response = command(server, credentials)
    command_id = response.json()["command_id"]
    with server[1].websocket_connect("/ws/agent", headers=agent_headers(credentials)) as ws:
        assert ws.receive_json()["type"] == "ready"
        first = receive_type(ws, "command")
        retry = receive_type(ws, "command")
        assert retry["command_id"] == first["command_id"] == command_id
    with server[1].websocket_connect("/ws/agent", headers=agent_headers(credentials)) as ws:
        assert ws.receive_json()["type"] == "ready"
        assert receive_type(ws, "command")["command_id"] == command_id


def test_actual_uvicorn_websocket_transport(server):
    """Exercise the installed Uvicorn WebSocket backend over loopback TCP."""
    import uvicorn
    from websockets.sync.client import connect

    credentials = enroll(server)
    headers = parent_headers(server)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    runner = uvicorn.Server(uvicorn.Config(server[0], lifespan="off", access_log=False, log_level="error"))
    worker = threading.Thread(target=runner.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not runner.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert runner.started
        with connect(f"ws://127.0.0.1:{port}/ws/agent", additional_headers=agent_headers(credentials),
                     open_timeout=3, proxy=None) as ws:
            import json
            assert json.loads(ws.recv(timeout=3))["type"] == "ready"
            started = time.monotonic()
            response = command(server, credentials, headers=headers)
            assert response.status_code == 202
            message = json.loads(ws.recv(timeout=3))
            assert message["command_id"] == response.json()["command_id"]
            latency = time.monotonic() - started
            assert latency < 5
            print(f"Uvicorn loopback command push: {latency:.3f}s")
            ws.send(json.dumps({"type": "ack", "command_id": message["command_id"], "status": "Applied"}))
            while True:
                reply = json.loads(ws.recv(timeout=3))
                if reply["type"] == "ack_received":
                    assert reply["accepted"] is True
                    break
    finally:
        runner.should_exit = True
        worker.join(timeout=5)
        listener.close()
        assert not worker.is_alive()


def test_command_http_idempotency_validation_and_ownership(server):
    credentials = enroll(server)
    first = command(server, credentials)
    assert command(server, credentials).json()["command_id"] == first.json()["command_id"]
    assert command(server, credentials, type="add_time", seconds=900).status_code == 409
    assert command(server, credentials, key="new", type="add_time").status_code == 422
    assert command(server, credentials, key="new", seconds=900).status_code == 422
    other = server[1].post("/api/v1/auth/login", json={"email": "other@example.test", "password": "Synthetic-parent-123"}).json()
    headers = {"Authorization": f"Bearer {other['access_token']}"}
    assert command(server, credentials, headers=headers).status_code == 404
    assert server[1].get(f"/api/v1/commands/{first.json()['command_id']}", headers=headers).status_code == 404
    assert command(server, credentials, headers=agent_headers(credentials)).status_code == 401


def test_expired_command_not_accepted_as_applied(server):
    credentials = enroll(server)
    command_id = command(server, credentials).json()["command_id"]
    with server[1].websocket_connect("/ws/agent", headers=agent_headers(credentials)) as ws:
        ws.receive_json()
        receive_type(ws, "command")
        with server[0].state.sessions.begin() as db:
            db.connection(execution_options={"sqlite_transaction_mode": "IMMEDIATE"})
            db.get(Command, command_id).expires_at = utcnow() - timedelta(seconds=1)
        ws.send_json({"type": "ack", "command_id": command_id, "status": "Applied"})
        assert receive_type(ws, "ack_received")["accepted"] is False
    with server[0].state.sessions() as db:
        assert db.get(Command, command_id).status == "Expired"


def test_ws_auth_query_credentials_revocation_and_bad_ack(server):
    credentials = enroll(server)
    for path, headers in [("/ws/agent", {}), ("/ws/agent", parent_headers(server)),
                          (f"/ws/agent?token={credentials['access_token']}", {})]:
        with pytest.raises(WebSocketDisconnect):
            with server[1].websocket_connect(path, headers=headers):
                pass
    with server[1].websocket_connect("/ws/agent", headers=agent_headers(credentials)) as ws:
        ws.receive_json()
        ws.send_json({"type": "ack", "command_id": "missing", "status": "Applied"})
        assert receive_type(ws, "ack_received")["accepted"] is False
        ws.send_text("not-json")
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with server[1].websocket_connect("/ws/agent", headers=agent_headers(credentials)) as ws:
        ws.receive_json()
        with server[0].state.sessions.begin() as db:
            db.connection(execution_options={"sqlite_transaction_mode": "IMMEDIATE"})
            db.get(Binding, credentials["binding_id"]).status = "revoked"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_other_binding_cannot_ack_command(server):
    first = enroll(server)
    second = complete(server, issue(server)["code"], sid="c" * 64, headers=agent_headers(first)).json()
    command_id = command(server, first).json()["command_id"]
    with server[1].websocket_connect("/ws/agent", headers=agent_headers(first)) as ws:
        ws.receive_json()
        receive_type(ws, "command")
    with server[1].websocket_connect("/ws/agent", headers=agent_headers(second)) as ws:
        ws.receive_json()
        ws.send_json({"type": "ack", "command_id": command_id, "status": "Applied"})
        assert receive_type(ws, "ack_received")["accepted"] is False


def batch(credentials, *, key="event-1", batch_id="batch-1"):
    return {"protocol_version": 1, "batch_id": batch_id, "binding_id": credentials["binding_id"],
            "data_epoch": 1, "events": [{"idempotency_key": key, "event": {
                "ts": utcnow().isoformat(), "device_id": credentials["device_id"],
                "child_id": credentials["child_id"], "type": "app_start", "subject": "notepad.exe",
                "duration_sec": 0, "policy_id": credentials["policy"]["document"]["policy_id"],
            }}]}


def test_batch_retries_concurrently_only_store_once(server):
    credentials = enroll(server)
    payload = batch(credentials)
    def upload(_):
        return server[1].post("/api/v1/events", headers=agent_headers(credentials), json=payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(upload, range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["accepted_keys"] == ["event-1"]
    with server[0].state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 1
    payload["events"][0]["event"]["subject"] = "calculator.exe"
    assert upload(0).status_code == 409


@pytest.mark.parametrize("change,expected", [("binding", 403), ("child", 403), ("epoch", 409),
                                             ("policy", 403), ("old", 422), ("key", 422)])
def test_batch_scope_epoch_retention_and_atomic_rollback(server, change, expected):
    credentials = enroll(server)
    payload = batch(credentials)
    bad = batch(credentials, key="event-2")["events"][0]
    payload["events"].append(bad)
    if change == "binding":
        payload["binding_id"] = "wrong"
    elif change == "child":
        bad["event"]["child_id"] = server[4]
    elif change == "epoch":
        payload["data_epoch"] = 2
    elif change == "policy":
        bad["event"]["policy_id"] = "wrong"
    elif change == "old":
        bad["event"]["ts"] = (utcnow() - timedelta(days=91)).isoformat()
    else:
        bad["idempotency_key"] = "event-1"
    response = server[1].post("/api/v1/events", headers=agent_headers(credentials), json=payload)
    assert response.status_code == expected, response.text
    with server[0].state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 0


def test_offline_queue_lost_server_response_end_to_end(server, tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "agent" / "src"))
    from ogk_agent.storage.offline_queue import OfflineQueue

    credentials = enroll(server)
    path = tmp_path / "agent.db"
    queue = OfflineQueue(path)
    queue.configure_binding(credentials["binding_id"], device_id=credentials["device_id"],
                            child_id=credentials["child_id"], data_epoch=1)
    queue.enqueue(batch(credentials)["events"][0]["event"], binding_id=credentials["binding_id"], data_epoch=1)
    def upload(batch):
        response = server[1].post("/api/v1/events", headers=agent_headers(credentials), json=batch.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        return response.json()
    def lost_response(batch):
        upload(batch)
        raise TimeoutError("Synthetic response loss after server commit")
    try:
        with pytest.raises(TimeoutError):
            queue.sync_once(credentials["binding_id"], lost_response, prepare_sync=lambda: True)
        persisted = queue.next_batch(credentials["binding_id"])
    finally:
        queue.close()
    with OfflineQueue(path) as queue:
        assert queue.next_batch(credentials["binding_id"]) == persisted
        assert queue.sync_once(credentials["binding_id"], upload, prepare_sync=lambda: True) == 1
        assert queue.pending_count(credentials["binding_id"]) == 0
    with server[0].state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 1
