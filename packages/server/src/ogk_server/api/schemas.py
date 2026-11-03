from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from ogk_contracts.policy import Policy as PolicyDocument


Identifier = Annotated[str, StringConstraints(pattern=r"^[0-9a-f-]{36}$", min_length=36, max_length=36)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(Input):
    email: Annotated[str, StringConstraints(min_length=3, max_length=254, strip_whitespace=True)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=1024)]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.casefold()


class AccessResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class TokenPairResponse(AccessResponse):
    refresh_token: str
    refresh_expires_at: datetime


class RefreshRequest(Input):
    refresh_token: Annotated[str, StringConstraints(min_length=40, max_length=128)]


class CreateEnrollmentRequest(Input):
    child_id: Identifier
    consent: Annotated[bool, Field(strict=True)]

    @field_validator("consent")
    @classmethod
    def require_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Explicit guardian consent is required")
        return value


class EnrollmentCodeResponse(BaseModel):
    code: str
    expires_at: datetime
    expires_in: Literal[600] = 600


class CompleteEnrollmentRequest(Input):
    code: Annotated[str, StringConstraints(pattern=r"^[A-HJ-NP-Z2-9]{8}$")]
    # Agent-generated random installation ID and local SID are hashed locally.
    # No hostname, Windows username, raw SID or hardware inventory is collected.
    installation_ref: Digest
    sid_ref: Digest


class SignedPolicy(BaseModel):
    document: PolicyDocument
    signature: str
    algorithm: Literal["HMAC-SHA256"]


class EnrollmentResponse(TokenPairResponse):
    device_id: str
    binding_id: str
    child_id: str
    data_epoch: int
    policy: SignedPolicy
    policy_verification_key: str
    cleanup_token: str
    heartbeat_interval_sec: Literal[60] = 60


class HeartbeatRequest(Input):
    policy_version: Annotated[int, Field(strict=True, ge=0)]
    quota_used_sec: Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]
    status: Literal["online", "idle", "locked", "paused"] = "online"
    device_id: Identifier | None = None
    binding_id: Identifier | None = None


class HeartbeatResponse(BaseModel):
    policy_version: int
    policy_update_available: bool
    heartbeat_interval_sec: Literal[60] = 60
    server_time: datetime
    data_epoch: int
