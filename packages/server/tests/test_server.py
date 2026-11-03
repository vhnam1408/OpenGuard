"""Synthetic integration/security tests for ENR-01..03, SRS 2.4 and 6.4..6.5."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import base64
import hashlib
import hmac

from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from ogk_contracts.policy import Policy as PolicyDocument
from ogk_server.config import Settings
from ogk_server.db.models import (
    Binding, Child, Device, EnrollmentCode, Event, Parent, Policy, RefreshToken, new_id, utcnow,
)
from ogk_server.main import create_app
from ogk_server.security.passwords import hash_password, verify_password
from ogk_server.security.tokens import secret_hash


PASSWORD = "Synthetic-parent-123"


@pytest.fixture(scope="session")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def server(tmp_path, password_hash):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'server.db').as_posix()}",
                        jwt_secret="j" * 64, policy_secret="p" * 64)
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.sessions.begin() as db:
            parent = Parent(id=new_id(), email="parent@example.test", password_hash=password_hash)
            other_parent = Parent(id=new_id(), email="other@example.test", password_hash=password_hash)
            db.add_all([parent, other_parent])
            db.flush()
            child = Child(id=new_id(), parent_id=parent.id, display_name="Demo child")
            other_child = Child(id=new_id(), parent_id=other_parent.id, display_name="Other demo")
            db.add_all([child, other_child])
        yield app, client, parent.id, child.id, other_child.id


def parent_headers(server):
    response = server[1].post("/api/v1/auth/login", json={"email": "PARENT@example.test", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def issue(server, child_id=None, headers=None):
    response = server[1].post("/api/v1/enroll", headers=headers or parent_headers(server),
                              json={"child_id": child_id or server[3], "consent": True})
    assert response.status_code == 201, response.text
    return response.json()


def complete(server, code, installation="a" * 64, sid="b" * 64, headers=None):
    return server[1].post("/api/v1/enroll/complete", headers=headers or {}, json={
        "code": code, "installation_ref": installation, "sid_ref": sid,
    })


def enroll(server):
    response = complete(server, issue(server)["code"])
    assert response.status_code == 201, response.text
    return response.json()


def agent_headers(credentials):
    return {"Authorization": f"Bearer {credentials['access_token']}"}


def ping(server, credentials, **changes):
    payload = {"policy_version": 1, "quota_used_sec": 60, **changes}
    return server[1].post("/api/v1/heartbeat", headers=agent_headers(credentials), json=payload)


def test_wal_foreign_keys_and_argon2id(server, password_hash):
    with server[0].state.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
    assert password_hash.startswith("$argon2id$")
    assert verify_password(PASSWORD, password_hash)
    assert not verify_password("wrong-password", password_hash)
    assert hash_password(PASSWORD) != password_hash
    with pytest.raises(ValueError):
        hash_password("short")


def test_enrollment_end_to_end_and_policy_signature(server):
    code = issue(server)
    assert len(code["code"]) == 8
    assert code["expires_in"] == 600
    with server[0].state.sessions() as db:
        stored = db.scalar(select(EnrollmentCode))
        assert stored.code_hash != code["code"]
        assert 590 <= (stored.expires_at - utcnow()).total_seconds() <= 600
    response = complete(server, code["code"])
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    data = response.json()
    assert data["expires_in"] == 900
    claims = jwt.decode(data["access_token"], server[0].state.settings.jwt_secret,
                        algorithms=["HS256"], audience="ogk-api", issuer="ogk-server")
    assert claims["exp"] - claims["iat"] == 900
    assert claims["device_id"] == data["device_id"]
    assert claims["binding_id"] == claims["sub"] == data["binding_id"]
    assert claims["child_id"] == server[3]
    policy = PolicyDocument.model_validate(data["policy"]["document"])
    key = base64.urlsafe_b64decode(data["policy_verification_key"])
    signature = hmac.new(key, policy.signing_payload(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, data["policy"]["signature"])
    with server[0].state.sessions() as db:
        token = db.scalar(select(RefreshToken))
        assert token.token_hash == secret_hash(server[0].state.settings, "refresh", data["refresh_token"])
        assert token.token_hash != data["refresh_token"]
        assert db.get(Binding, data["binding_id"]).cleanup_token_hash != data["cleanup_token"]
    assert complete(server, code["code"], installation="c" * 64).status_code == 400
    assert ping(server, data).json()["policy_version"] == 1


def test_expired_replaced_and_unknown_codes(server):
    headers = parent_headers(server)
    first = issue(server, headers=headers)
    second = issue(server, headers=headers)
    assert complete(server, first["code"]).status_code == 400
    with server[0].state.sessions.begin() as db:
        record = db.scalar(select(EnrollmentCode).where(EnrollmentCode.code_hash == secret_hash(server[0].state.settings, "enrollment", second["code"])))
        record.expires_at = utcnow() - timedelta(seconds=1)
    assert complete(server, second["code"]).status_code == 400
    assert complete(server, "ZZZZZZZZ").status_code == 400


def test_ownership_consent_and_required_auth(server):
    headers = parent_headers(server)
    assert server[1].post("/api/v1/enroll", headers=headers, json={"child_id": server[4], "consent": True}).status_code == 404
    assert server[1].post("/api/v1/enroll", headers=headers, json={"child_id": server[3], "consent": False}).status_code == 422
    assert server[1].post("/api/v1/enroll", json={"child_id": server[3], "consent": True}).status_code == 401
    assert server[1].get("/api/v1/policy").status_code == 401
    assert server[1].post("/api/v1/heartbeat", json={"policy_version": 0, "quota_used_sec": 0}).status_code == 401
    assert server[1].post("/api/v1/heartbeat", headers=headers, json={"policy_version": 0, "quota_used_sec": 0}).status_code == 401
    credentials = enroll(server)
    assert server[1].post("/api/v1/enroll", headers=agent_headers(credentials), json={"child_id": server[3], "consent": True}).status_code == 401


def test_heartbeat_scope_latest_version_and_validation(server):
    credentials = enroll(server)
    assert ping(server, credentials, device_id=new_id()).status_code == 403
    assert ping(server, credentials, binding_id=new_id()).status_code == 403
    assert ping(server, credentials, quota_used_sec=-1).status_code == 422
    assert ping(server, credentials, policy_version=True).status_code == 422
    assert ping(server, credentials, child_id=server[4]).status_code == 422
    with server[0].state.sessions.begin() as db:
        old = db.scalar(select(Policy))
        document = deepcopy(old.document)
        document.update(policy_id=new_id(), version=2)
        db.add(Policy(id=document["policy_id"], binding_id=old.binding_id, device_id=old.device_id,
                      child_id=old.child_id, version=2, document=document))
    response = ping(server, credentials)
    assert response.status_code == 200
    assert response.json()["policy_version"] == 2
    assert response.json()["policy_update_available"] is True
    assert response.json()["heartbeat_interval_sec"] == 60
    assert ping(server, credentials, policy_version=2).json()["policy_update_available"] is False
    policy = server[1].get("/api/v1/policy", headers=agent_headers(credentials))
    assert policy.json()["document"]["version"] == 2
    with server[0].state.sessions() as db:
        binding = db.get(Binding, credentials["binding_id"])
        assert binding.quota_used_sec == 60
        assert binding.last_seen_at is not None
        assert db.get(Device, credentials["device_id"]).last_seen_at is not None


@pytest.mark.parametrize("target", ["binding", "device", "child", "parent", "token_version"])
def test_revocation_checked_on_every_agent_route(server, target):
    credentials = enroll(server)
    with server[0].state.sessions.begin() as db:
        if target == "binding":
            db.get(Binding, credentials["binding_id"]).status = "revoked"
        elif target == "device":
            db.get(Device, credentials["device_id"]).is_active = False
        elif target == "child":
            db.get(Child, server[3]).is_active = False
        elif target == "parent":
            db.get(Parent, server[2]).is_active = False
        else:
            db.get(Binding, credentials["binding_id"]).token_version += 1
    assert ping(server, credentials).status_code == 401
    assert server[1].get("/api/v1/policy", headers=agent_headers(credentials)).status_code == 401
    if target != "token_version":
        assert server[1].post("/api/v1/auth/refresh", json={"refresh_token": credentials["refresh_token"]}).status_code == 401


@pytest.mark.parametrize("change", ["expired", "signature", "scope", "device", "missing", "refresh-as-access"])
def test_invalid_tokens(server, change):
    credentials = enroll(server)
    settings = server[0].state.settings
    claims = jwt.decode(credentials["access_token"], options={"verify_signature": False})
    secret = settings.jwt_secret
    if change == "expired":
        claims["exp"] = int((utcnow() - timedelta(seconds=1)).timestamp())
    elif change == "signature":
        secret = "invalid-signature-key" * 3
    elif change == "scope":
        claims["scope"] = ["policy"]
    elif change == "device":
        claims["device_id"] = new_id()
    elif change == "missing":
        del claims["exp"]
    else:
        credentials["access_token"] = credentials["refresh_token"]
    if change != "refresh-as-access":
        credentials["access_token"] = jwt.encode(claims, secret, algorithm="HS256")
    assert ping(server, credentials).status_code == 401


def test_refresh_rotation_reuse_and_expiry(server):
    credentials = enroll(server)
    first = server[1].post("/api/v1/auth/refresh", json={"refresh_token": credentials["refresh_token"]})
    assert first.status_code == 200, first.text
    rotated = first.json()
    assert rotated["refresh_token"] != credentials["refresh_token"]
    assert ping(server, rotated).status_code == 200
    assert server[1].post("/api/v1/auth/refresh", json={"refresh_token": credentials["refresh_token"]}).status_code == 401
    assert ping(server, rotated).status_code == 401
    assert ping(server, credentials).status_code == 401
    assert server[1].post("/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}).status_code == 401


def test_expired_refresh_and_expired_access_can_refresh(server):
    credentials = enroll(server)
    settings = server[0].state.settings
    claims = jwt.decode(credentials["access_token"], options={"verify_signature": False})
    claims["exp"] = int((utcnow() - timedelta(seconds=1)).timestamp())
    expired = {**credentials, "access_token": jwt.encode(claims, settings.jwt_secret, algorithm="HS256")}
    assert ping(server, expired).status_code == 401
    response = server[1].post("/api/v1/auth/refresh", json={"refresh_token": credentials["refresh_token"]})
    assert response.status_code == 200
    with server[0].state.sessions.begin() as db:
        for record in db.scalars(select(RefreshToken)):
            record.expires_at = utcnow() - timedelta(seconds=1)
    assert server[1].post("/api/v1/auth/refresh", json={"refresh_token": response.json()["refresh_token"]}).status_code == 401


def test_parent_login_lock_and_logout(server):
    for _ in range(5):
        assert server[1].post("/api/v1/auth/login", json={"email": "parent@example.test", "password": "wrong"}).status_code == 401
    assert server[1].post("/api/v1/auth/login", json={"email": "parent@example.test", "password": PASSWORD}).status_code == 401
    with server[0].state.sessions.begin() as db:
        parent = db.get(Parent, server[2])
        assert 890 < (parent.locked_until - utcnow()).total_seconds() <= 900
        parent.locked_until = utcnow() - timedelta(seconds=1)
    headers = parent_headers(server)
    assert server[1].post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert server[1].post("/api/v1/enroll", headers=headers, json={"child_id": server[3], "consent": True}).status_code == 401


def test_code_and_parent_rate_limits(server):
    for _ in range(5):
        assert complete(server, "ZZZZZZZZ").status_code == 400
    response = complete(server, "ZZZZZZZZ")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    headers = parent_headers(server)
    for _ in range(5):
        issue(server, headers=headers)
    assert server[1].post("/api/v1/enroll", headers=headers, json={"child_id": server[3], "consent": True}).status_code == 429


def test_existing_installation_requires_token_and_preserves_sid_owner(server):
    credentials = enroll(server)
    headers = parent_headers(server)
    code = issue(server, headers=headers)["code"]
    assert complete(server, code, sid="c" * 64).status_code == 409
    assert complete(server, code, headers=agent_headers(credentials)).status_code == 409
    added = complete(server, code, sid="c" * 64, headers=agent_headers(credentials))
    assert added.status_code == 201, added.text
    assert added.json()["device_id"] == credentials["device_id"]
    assert added.json()["binding_id"] != credentials["binding_id"]
    assert ping(server, credentials).status_code == 200
    assert ping(server, added.json()).status_code == 200
    assert ping(server, credentials, binding_id=added.json()["binding_id"]).status_code == 403


def test_atomic_enrollment_under_concurrent_requests(server):
    code = issue(server)["code"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda installation: complete(server, code, installation), ["a" * 64, "c" * 64]))
    assert sorted(response.status_code for response in responses) == [201, 400]
    with server[0].state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Binding)) == 1
        assert db.scalar(select(func.count()).select_from(Device)) == 1
        assert db.scalar(select(func.count()).select_from(RefreshToken)) == 1


def test_atomic_refresh_under_concurrent_requests(server):
    credentials = enroll(server)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: server[1].post("/api/v1/auth/refresh", json={"refresh_token": credentials["refresh_token"]}), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 401]
    winner = next(response.json() for response in responses if response.status_code == 200)
    assert ping(server, winner).status_code == 401


def test_database_scope_and_active_sid_constraints(server):
    credentials = enroll(server)
    with server[0].state.sessions() as db:
        binding = db.get(Binding, credentials["binding_id"])
        db.add(Binding(device_id=binding.device_id, child_id=binding.child_id, sid_ref=binding.sid_ref,
                       cleanup_token_hash="x" * 64))
        with pytest.raises(IntegrityError):
            db.commit()
    with server[0].state.sessions() as db:
        policy = db.scalar(select(Policy))
        db.add(Event(binding_id=policy.binding_id, device_id=policy.device_id, child_id=server[4],
                     policy_id=policy.id, ts=utcnow(), type="app_start", subject="notepad.exe",
                     duration_sec=0, data_epoch=1, idempotency_key="synthetic-event"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_no_credential_acceptance_in_query_string(server):
    credentials = enroll(server)
    assert server[1].get("/api/v1/policy", params={"access_token": credentials["access_token"]}).status_code == 401


def test_settings_fail_closed():
    with pytest.raises(ValueError):
        Settings("sqlite:///:memory:", "j" * 64, "p" * 64)
    with pytest.raises(ValueError):
        Settings("sqlite:///test.db", "too-short", "p" * 64)
    with pytest.raises(ValueError):
        Settings("sqlite:///test.db", "same" * 16, "same" * 16)
