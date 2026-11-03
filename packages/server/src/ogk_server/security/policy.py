import base64
import hashlib
import hmac

from ogk_contracts.policy import Policy as PolicyDocument
from ogk_server.config import Settings
from ogk_server.db.models import Binding, Policy, new_id


def verification_key(settings: Settings, binding_id: str) -> bytes:
    # Agent receives only its own binding key, never the server master key.
    return hmac.new(settings.policy_secret.encode(), f"policy:{binding_id}".encode(), hashlib.sha256).digest()


def export_key(settings: Settings, binding_id: str) -> str:
    return base64.urlsafe_b64encode(verification_key(settings, binding_id)).decode()


def signed_policy(settings: Settings, policy: Policy) -> dict:
    document = PolicyDocument.model_validate(policy.document)
    if (document.policy_id, document.binding_id, document.device_id, document.child_id, document.version) != (
        policy.id, policy.binding_id, policy.device_id, policy.child_id, policy.version
    ):
        raise ValueError("Stored policy scope/version is inconsistent")
    signature = hmac.new(verification_key(settings, policy.binding_id), document.signing_payload(), hashlib.sha256)
    return {"document": document.model_dump(mode="json"), "signature": signature.hexdigest(), "algorithm": "HMAC-SHA256"}


def initial_policy(binding: Binding) -> Policy:
    """Transparent bootstrap: no restrictions until the parent configures rules.

    No domain is blocked, including emergency/education services. Future policy
    editing must preserve mandatory safety exceptions before adding blockers.
    """
    policy_id = new_id()
    document = PolicyDocument(
        policy_id=policy_id, version=1, binding_id=binding.id,
        device_id=binding.device_id, child_id=binding.child_id,
        timezone="Asia/Ho_Chi_Minh", schedule=[[True] * 48 for _ in range(7)],
        quota_weekday_sec=86400, quota_weekend_sec=86400,
    )
    return Policy(id=policy_id, binding_id=binding.id, device_id=binding.device_id,
                  child_id=binding.child_id, version=1, document=document.model_dump(mode="json"))
