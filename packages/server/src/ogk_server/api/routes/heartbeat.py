from fastapi import APIRouter

from ogk_server.api.dependencies.auth import CurrentAgent, Db
from ogk_server.api.schemas import HeartbeatRequest, HeartbeatResponse
from ogk_server.services.heartbeat import heartbeat as record_heartbeat


router = APIRouter(tags=["agent"])


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(payload: HeartbeatRequest, db: Db, agent: CurrentAgent):
    return record_heartbeat(db, agent, payload)
