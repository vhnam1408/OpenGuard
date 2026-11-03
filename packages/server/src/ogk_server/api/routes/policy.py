from fastapi import APIRouter

from ogk_server.api.dependencies.auth import Config, CurrentAgent, Db
from ogk_server.api.schemas import SignedPolicy
from ogk_server.security.policy import signed_policy
from ogk_server.services.heartbeat import latest_policy


router = APIRouter(tags=["agent"])


@router.get("/policy", response_model=SignedPolicy)
def get_policy(db: Db, settings: Config, agent: CurrentAgent):
    return signed_policy(settings, latest_policy(db, agent.binding.id))
