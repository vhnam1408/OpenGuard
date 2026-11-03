"""Atomic authenticated batch ingestion, including retries after lost ACKs."""
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ogk_contracts.events import Event as EventPayload
from ogk_contracts.sync import EventBatch, EventBatchAck
from ogk_server.api.dependencies.auth import CurrentAgent, Db
from ogk_server.db.models import Event, Policy, utcnow


router = APIRouter(tags=["events"])


@router.post("/events", response_model=EventBatchAck)
def upload_events(payload: EventBatch, db: Db, agent: CurrentAgent):
    if payload.binding_id != agent.binding.id:
        raise HTTPException(403, "Batch target does not match authenticated binding")
    if payload.data_epoch != agent.child.data_epoch:
        raise HTTPException(409, "Stale data epoch; synchronize lifecycle before upload")
    now = utcnow()
    for item in payload.events:
        event = item.event
        if event.device_id != agent.device.id or event.child_id != agent.child.id:
            raise HTTPException(403, "Event target does not match authenticated binding")
        if len(event.subject) > 253:
            raise HTTPException(422, "Event subject is too long")
        if not now - timedelta(days=90) < event.ts <= now + timedelta(minutes=5):
            raise HTTPException(422, "Event is outside the retention/clock window")
        policy = db.get(Policy, event.policy_id)
        if policy is None or policy.binding_id != agent.binding.id:
            raise HTTPException(403, "Event policy does not belong to binding")
        previous = db.scalar(select(Event).where(
            Event.binding_id == payload.binding_id, Event.data_epoch == payload.data_epoch,
            Event.idempotency_key == item.idempotency_key,
        ))
        if previous is not None:
            fields = {name: getattr(previous, name) for name in EventPayload.model_fields}
            if EventPayload.model_validate(fields) != event:
                raise HTTPException(409, "Idempotency key already used for another event")
        else:
            db.add(Event(binding_id=payload.binding_id, data_epoch=payload.data_epoch,
                         idempotency_key=item.idempotency_key, **event.model_dump()))
    # get_db starts BEGIN IMMEDIATE: overlapping batches serialize before reads.
    # Any validation failure rolls back the entire batch, including flushed rows.
    db.commit()
    return EventBatchAck(batch_id=payload.batch_id, binding_id=payload.binding_id,
                         data_epoch=payload.data_epoch, server_time=now,
                         accepted_keys=[item.idempotency_key for item in payload.events])
