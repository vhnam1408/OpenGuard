from fastapi import APIRouter, Request

from ogk_server.api.dependencies.auth import Config, CurrentParent, Db, OptionalAgent
from ogk_server.api.schemas import (
    CompleteEnrollmentRequest, CreateEnrollmentRequest, EnrollmentCodeResponse, EnrollmentResponse,
)
from ogk_server.services import enrollment


router = APIRouter(prefix="/enroll", tags=["enrollment"])


@router.post("", response_model=EnrollmentCodeResponse, status_code=201)
def issue_code(payload: CreateEnrollmentRequest, request: Request, db: Db, settings: Config, parent: CurrentParent):
    return enrollment.issue_code(db, settings, request, parent, payload.child_id)


@router.post("/complete", response_model=EnrollmentResponse, status_code=201)
def complete(payload: CompleteEnrollmentRequest, request: Request, db: Db, settings: Config, agent: OptionalAgent):
    # First enrollment is authenticated by the short-lived, rate-limited code.
    # Adding another SID to an existing installation also requires its JWT.
    return enrollment.complete(db, settings, request, payload, agent)
