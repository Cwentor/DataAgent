"""统一身份认证（P0）单元测试：身份库 / JWT / 会话 / 网关 principal 绑定。"""

from __future__ import annotations

import time

import pytest

from auth.errors import AuthenticationError, TokenError
from auth.gateway import AuthContext, authenticate
from auth.identity import IdentityStore, hash_password, verify_password
from auth.session import SessionStore
from auth.tokens import create_token, decode_token
from config import settings


# --------------------------------------------------------------------------- #
# 身份库：口令哈希 + 认证 + principal 服务端映射
# --------------------------------------------------------------------------- #
def test_password_hash_roundtrip():
    digest = hash_password("s3cret")
    assert digest and digest != "s3cret"
    assert verify_password("s3cret", digest)
    assert not verify_password("wrong", digest)
    assert not verify_password("s3cret", "")


def test_password_hash_uses_per_user_salt():
    """P0-5：同一口令两次哈希必须不同（每用户随机盐，杜绝彩虹表预计算）。"""
    a, b = hash_password("s3cret"), hash_password("s3cret")
    assert a != b
    # 新格式：pbkdf2_sha256$iter$salt_hex$hash_hex（4 段，盐与迭代次数编码其中）
    parts = a.split("$")
    assert len(parts) == 4 and parts[0] == "pbkdf2_sha256"
    assert int(parts[1]) >= 100_000
    assert len(parts[2]) == 32  # 16 字节盐的 hex
    assert len(parts[3]) == 64  # SHA-256 摘要 hex


def test_password_hash_verifies_legacy_fixed_salt_format():
    """P0-5：旧版固定盐 64 位十六进制哈希仍可校验（向后兼容）。"""
    import hashlib

    legacy = hashlib.pbkdf2_hmac("sha256", b"s3cret", b"futurebi-salt-v1", 200_000).hex()
    assert verify_password("s3cret", legacy)
    assert not verify_password("wrong", legacy)


def test_identity_store_authenticates_default_users():
    store = IdentityStore()
    user = store.authenticate("admin", "admin123")
    assert user.principal == "admin"
    assert store.authenticate("bob", "bob123").principal == "restricted"


def test_identity_store_rejects_wrong_password():
    store = IdentityStore()
    with pytest.raises(AuthenticationError):
        store.authenticate("admin", "nope")


def test_identity_store_rejects_unknown_user():
    store = IdentityStore()
    with pytest.raises(AuthenticationError):
        store.authenticate("ghost", "x")


def test_principal_mapping_is_server_side():
    """principal 只由服务端从身份映射，用户对象上不存在任何可被客户端注入的路径。"""
    store = IdentityStore()
    for username, expected in (("admin", "admin"), ("analyst", "analyst"), ("bob", "restricted")):
        assert store.principal_for(username) == expected
        assert store.get(username).principal == expected


# --------------------------------------------------------------------------- #
# JWT：签发 / 校验 / 过期 / 篡改 / 签发者与受众
# --------------------------------------------------------------------------- #
def test_jwt_roundtrip():
    token = create_token(
        "bob",
        "secret",
        issuer="dataagent",
        audience="dataagent-web",
        ttl_seconds=3600,
    )
    claims = decode_token(token, "secret", issuer="dataagent", audience="dataagent-web")
    assert claims["sub"] == "bob"
    assert claims["iss"] == "dataagent"
    assert claims["aud"] == "dataagent-web"


def test_jwt_expired_rejected():
    now = time.time()
    token = create_token("bob", "secret", issuer="i", audience="a", ttl_seconds=10, now=now)
    # 用超过 ttl 的时间点校验 -> 过期
    with pytest.raises(TokenError, match="过期"):
        decode_token(token, "secret", issuer="i", audience="a", now=now + 100)


def test_jwt_tampered_signature_rejected():
    token = create_token("bob", "secret", issuer="i", audience="a", ttl_seconds=3600)
    head, payload, _ = token.split(".")
    forged = f"{head}.{payload}.AAAA"
    with pytest.raises(TokenError, match="签名"):
        decode_token(forged, "secret", issuer="i", audience="a")


def test_jwt_wrong_secret_rejected():
    token = create_token("bob", "secret", issuer="i", audience="a", ttl_seconds=3600)
    with pytest.raises(TokenError, match="签名"):
        decode_token(token, "other-secret", issuer="i", audience="a")


def test_jwt_issuer_and_audience_enforced():
    token = create_token(
        "bob", "secret", issuer="dataagent", audience="dataagent-web", ttl_seconds=3600
    )
    with pytest.raises(TokenError, match="签发者"):
        decode_token(token, "secret", issuer="evil", audience="dataagent-web")
    with pytest.raises(TokenError, match="受众"):
        decode_token(token, "secret", issuer="dataagent", audience="evil")


def test_jwt_never_carries_principal_claim():
    """令牌只携带身份标识，绝不携带 principal —— 权限由服务端每次重新映射。"""
    token = create_token(
        "bob", "secret", issuer="dataagent", audience="dataagent-web", ttl_seconds=3600
    )
    claims = decode_token(token, "secret", issuer="dataagent", audience="dataagent-web")
    assert "principal" not in claims
    assert "role" not in claims


# --------------------------------------------------------------------------- #
# 会话：创建 / 读取 / 吊销 / 过期
# --------------------------------------------------------------------------- #
def test_session_store_create_get_revoke():
    store = SessionStore(ttl_seconds=600)
    session = store.create("bob", "restricted")
    assert store.get(session.session_id).username == "bob"
    assert store.get(session.session_id).principal == "restricted"
    assert store.revoke(session.session_id)
    assert store.get(session.session_id) is None


def test_session_expiry():
    store = SessionStore(ttl_seconds=1)
    session = store.create("bob", "restricted")
    time.sleep(1.1)
    assert store.get(session.session_id) is None


def test_session_revoke_returns_false_when_missing():
    store = SessionStore()
    assert not store.revoke("no-such-id")


def test_sqlite_session_store_persists_across_instances(tmp_path):
    """P0-4：SQLite 会话存储重启不丢 —— 新实例可读到旧实例签发的会话。"""
    from auth.session import SqliteSessionStore

    db = tmp_path / "sessions.sqlite3"
    store1 = SqliteSessionStore(db, ttl_seconds=600)
    session = store1.create("bob", "restricted")
    store1.close()

    store2 = SqliteSessionStore(db, ttl_seconds=600)
    try:
        restored = store2.get(session.session_id)
        assert restored is not None
        assert restored.username == "bob"
        assert restored.principal == "restricted"
        assert store2.revoke(session.session_id)
        assert store2.get(session.session_id) is None
    finally:
        store2.close()


def test_sqlite_session_store_expiry(tmp_path):
    """SQLite 会话同样按 TTL 惰性失效。"""
    from auth.session import SqliteSessionStore

    db = tmp_path / "sessions.sqlite3"
    store = SqliteSessionStore(db, ttl_seconds=1)
    session = store.create("bob", "restricted")
    time.sleep(1.1)
    try:
        assert store.get(session.session_id) is None
    finally:
        store.close()


def test_sqlite_session_store_prune(tmp_path):
    from auth.session import SqliteSessionStore

    db = tmp_path / "sessions.sqlite3"
    store = SqliteSessionStore(db, ttl_seconds=-1)  # 立即过期
    store.create("bob", "restricted")
    store.create("admin", "admin")
    try:
        assert store.prune() >= 2
        assert store.prune() == 0
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 网关：authenticate 从请求头解析并强制绑定 principal
# --------------------------------------------------------------------------- #
def test_gateway_requires_credentials():
    with pytest.raises(AuthenticationError, match="凭证"):
        authenticate({})


def test_gateway_authenticates_jwt():
    token = create_token(
        "bob",
        settings.AUTH_JWT_SECRET,
        issuer=settings.AUTH_JWT_ISSUER,
        audience=settings.AUTH_JWT_AUDIENCE,
        ttl_seconds=600,
    )
    ctx = authenticate({"Authorization": f"Bearer {token}"})
    assert isinstance(ctx, AuthContext)
    assert ctx.username == "bob"
    assert ctx.principal == "restricted"  # 服务端映射，而非客户端声明
    assert ctx.auth_type == "jwt"


def test_gateway_rejects_invalid_jwt():
    with pytest.raises(AuthenticationError):
        authenticate({"Authorization": "Bearer not.a.jwt"})


def test_gateway_authenticates_session_header():
    from auth.gateway import create_session
    from auth.identity import IdentityStore

    user = IdentityStore().get("analyst")
    session = create_session(user)
    ctx = authenticate({"X-Session-ID": session.session_id})
    assert ctx.username == "analyst"
    assert ctx.principal == "analyst"
    assert ctx.auth_type == "session"


def test_gateway_authenticates_session_cookie():
    from auth.gateway import create_session
    from auth.identity import IdentityStore

    session = create_session(IdentityStore().get("bob"))
    ctx = authenticate({"Cookie": f"session={session.session_id}; Path=/"})
    assert ctx.username == "bob"
    assert ctx.principal == "restricted"


def test_gateway_rejects_expired_session():
    store = SessionStore(ttl_seconds=1)
    session = store.create("bob", "restricted")
    # 直接把过期会话写入默认存储后再探测
    from auth import session as auth_session

    auth_session._default_store = store  # type: ignore[attr-defined]
    try:
        time.sleep(1.1)
        with pytest.raises(AuthenticationError, match="会话"):
            authenticate({"X-Session-ID": session.session_id})
    finally:
        auth_session._default_store = None  # type: ignore[attr-defined]
