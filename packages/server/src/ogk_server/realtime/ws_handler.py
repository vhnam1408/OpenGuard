"""FastAPI WebSocket delivery with a SQLite outbox shared across workers.

Poll every 0.5s, retry unacknowledged commands every 1s with the same command_id.
No long-lived ORM sessions or blocking DB work run on the ASGI event loop.
The Agent must atomically apply/deduplicate command_id before sending Applied.
WSS/TLS authenticates command delivery; bearer credentials never enter URLs.
"""
from __future__ import annotations

from datetime import timedelta
import json

import anyio
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.exc import OperationalError
from starlette.concurrency import run_in_threadpool

from ogk_contracts.sync import CommandAck, CommandRequest
from ogk_server.api.dependencies.auth import CurrentParent, Db, authenticate_agent
from ogk_server.db.models import Binding, Child, Command, Device, utcnow


router = APIRouter(tags=["commands"])
ws_router = APIRouter()
POLL_SECONDS = 0.5
RETRY_SECONDS = 1
SEND_TIMEOUT_SECONDS = 2


def command_view(command: Command) -> dict:
    return {
        "type": "command", "command_id": command.id, "binding_id": command.binding_id,
        "command": command.type, "seconds": command.seconds,
        "status": "Expired" if command.status == "Pending" and command.expires_at <= utcnow() else command.status,
        "created_at": command.created_at.isoformat(), "expires_at": command.expires_at.isoformat(),
    }


def owned_binding(db, parent_id: str, binding_id: str):
    row = db.execute(select(Binding, Child, Device).join(Child, Child.id == Binding.child_id)
                     .join(Device, Device.id == Binding.device_id)
                     .where(Binding.id == binding_id, Child.parent_id == parent_id)).one_or_none()
    if row is None:
        raise HTTPException(404, "Binding not found")
    binding, child, device = row
    if binding.status != "active" or not child.is_active or not device.is_active:
        raise HTTPException(409, "Binding is not active")
    return binding


@router.post("/commands", status_code=202)
def queue_command(payload: CommandRequest, db: Db, parent: CurrentParent):
    owned_binding(db, parent.parent.id, payload.binding_id)
    command = db.scalar(select(Command).where(Command.binding_id == payload.binding_id,
                                             Command.idempotency_key == payload.idempotency_key))
    if command is not None:
        if (command.type, command.seconds, command.ttl_seconds) != (payload.type, payload.seconds, payload.ttl_seconds):
            raise HTTPException(409, "Idempotency key already used for another command")
    else:
        command = Command(binding_id=payload.binding_id, idempotency_key=payload.idempotency_key,
                          type=payload.type, seconds=payload.seconds, ttl_seconds=payload.ttl_seconds,
                          expires_at=utcnow() + timedelta(seconds=payload.ttl_seconds))
        db.add(command)
    db.commit()  # The outbox exists before any worker can send the command.
    return command_view(command)


@router.get("/commands/{command_id}")
def get_command(command_id: str, db: Db, parent: CurrentParent):
    command = db.get(Command, command_id)
    if command is None:
        raise HTTPException(404, "Command not found")
    owned_binding(db, parent.parent.id, command.binding_id)
    return command_view(command)


def authenticated_binding(websocket: WebSocket, token: str) -> str:
    with websocket.app.state.sessions() as db:
        return authenticate_agent(db, websocket.app.state.settings, token).binding.id


def pending_commands(websocket: WebSocket, token: str) -> list[dict]:
    with websocket.app.state.sessions() as db:
        db.connection(execution_options={"sqlite_transaction_mode": "IMMEDIATE"})
        agent = authenticate_agent(db, websocket.app.state.settings, token)
        now = utcnow()
        db.execute(update(Command).where(Command.binding_id == agent.binding.id,
                                         Command.status == "Pending", Command.expires_at <= now)
                   .values(status="Expired"))
        commands = list(db.scalars(select(Command).where(
            Command.binding_id == agent.binding.id, Command.status == "Pending",
            Command.expires_at > now,
            or_(Command.last_sent_at.is_(None), Command.last_sent_at <= now - timedelta(seconds=RETRY_SECONDS)),
        ).order_by(Command.created_at, Command.id).limit(32)))
        for command in commands:
            command.last_sent_at = now
            command.attempts += 1
        messages = [command_view(command) for command in commands]
        db.commit()
        return messages


def record_ack(websocket: WebSocket, token: str, ack: CommandAck) -> bool:
    with websocket.app.state.sessions() as db:
        db.connection(execution_options={"sqlite_transaction_mode": "IMMEDIATE"})
        agent = authenticate_agent(db, websocket.app.state.settings, token)
        command = db.get(Command, ack.command_id)
        if command is None or command.binding_id != agent.binding.id or command.last_sent_at is None:
            return False
        if command.status != "Pending":
            return command.status == ack.status  # Repeated ACKs never change terminal state.
        if command.expires_at <= utcnow():
            command.status = "Expired"
            db.commit()
            return ack.status == "Expired"
        command.status = ack.status
        command.ack_at = utcnow()
        db.commit()
        return True


@ws_router.websocket("/ws/agent")
async def ws_handler(websocket: WebSocket):
    authorization = websocket.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or websocket.query_params:
        await websocket.close(code=1008)
        return
    try:
        binding_id = await run_in_threadpool(authenticated_binding, websocket, token)
    except HTTPException:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    send_lock = anyio.Lock()

    async def send(message):
        async with send_lock:
            with anyio.fail_after(SEND_TIMEOUT_SECONDS):
                await websocket.send_json(message)

    await send({"type": "ready", "binding_id": binding_id, "protocol_version": 1})

    async def deliver():
        while True:
            try:
                messages = await run_in_threadpool(pending_commands, websocket, token)
            except OperationalError as error:
                if getattr(error.orig, "sqlite_errorcode", None) not in {5, 6, 517}:
                    raise
                messages = []  # Keep outbox intact; retry after transient lock contention.
            for message in messages:
                await send(message)
            await anyio.sleep(POLL_SECONDS)

    async def receive():
        while True:
            frame = await websocket.receive()
            if frame["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(frame.get("code", 1000))
            raw = frame.get("text")
            if not isinstance(raw, str):
                raise ValueError("ACK must be a JSON text frame")
            if len(raw.encode("utf-8")) > 4096:
                raise ValueError("ACK frame too large")
            ack = CommandAck.model_validate(json.loads(raw))
            accepted = await run_in_threadpool(record_ack, websocket, token, ack)
            await send({"type": "ack_received", "command_id": ack.command_id, "accepted": accepted})

    # Structured cancellation also waits for in-flight DB threads before the
    # socket handler exits; disconnects cannot leave delivery workers behind.
    async with anyio.create_task_group() as group:
        async def supervise(worker):
            try:
                await worker()
            except (HTTPException, ValidationError, ValueError):
                await websocket.close(code=1008)
            except (WebSocketDisconnect, OSError, TimeoutError):
                pass
            finally:
                group.cancel_scope.cancel()

        group.start_soon(supervise, deliver)
        group.start_soon(supervise, receive)
