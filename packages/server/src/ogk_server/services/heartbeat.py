from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ogk_server.api.dependencies.auth import AgentContext
from ogk_server.api.schemas import HeartbeatRequest
from ogk_server.db.models import Policy, utcnow


def latest_policy(db: Session, binding_id: str) -> Policy:
    policy = db.scalar(select(Policy).where(Policy.binding_id == binding_id).order_by(Policy.version.desc()).limit(1))
    if policy is None:
        raise HTTPException(409, "Binding has no policy")
    return policy


def heartbeat(db: Session, agent: AgentContext, payload: HeartbeatRequest) -> dict:
    binding = agent.binding
    if ((payload.device_id is not None and payload.device_id != binding.device_id)
            or (payload.binding_id is not None and payload.binding_id != binding.id)):
        raise HTTPException(403, "Heartbeat target does not match authenticated binding")
    policy = latest_policy(db, binding.id)
    now = utcnow()
    binding.last_seen_at = now
    agent.device.last_seen_at = now
    binding.reported_policy_version = payload.policy_version
    # Telemetry only: this value must never grant/reset the enforcement budget.
    binding.quota_used_sec = payload.quota_used_sec
    binding.agent_status = payload.status
    db.commit()
    return {"policy_version": policy.version, "policy_update_available": payload.policy_version < policy.version,
            "heartbeat_interval_sec": 60, "server_time": now, "data_epoch": agent.child.data_epoch}
