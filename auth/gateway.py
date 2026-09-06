"""鉴权网关：从 HTTP 请求解析并**服务端强制绑定** principal（P0）。

安全约束：
- principal 由服务端从"已认证身份 -> IdentityStore"映射；客户端传入的任何
  principal / 角色声明一律忽略（本模块根本不读取这类字段）；
- 支持两种凭证：
  1. JWT：Authorization: Bearer <token>（主，无状态、可跨进程）；
  2. Session：X-Session-ID 请求头 或 session Cookie（登出即失效、浏览器友好）。
- settings.AUTH_ENABLED=False 时（本地开发 / 演示）仍不信任客户端：
  一律回退到 settings.AUTH_DEFAULT_PRINCIPAL / AUTH_DEFAULT_USER 的
  服务端默认身份，保持"principal 永远服务端决定"的不变式。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from http.cookies import SimpleCookie

from auth.errors import AuthenticationError, TokenError
from auth.identity import IdentityStore, User
from auth.session import Session, default_session_store
from auth.tokens import decode_token
from config import settings

_BEARER_PREFIX = "Bearer "
_SESSION_COOKIE = "session"


@dataclass(frozen=True)
class AuthContext:
    """一次认证请求解析出的服务端身份上下文。"""

    username: str
    principal: str
    display_name: str
    roles: frozenset[str] = frozenset()
    auth_type: str = "jwt"  # jwt | session | default
    session_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        """序列化身份上下文供业务层消费（roles 已排序，保证确定性输出）。"""
        return {
            "username": self.username,
            "principal": self.principal,
            "display_name": self.display_name,
            "roles": sorted(self.roles),
            "auth_type": self.auth_type,
        }


_default_identity: IdentityStore | None = None
_identity_lock = threading.Lock()


def default_identity_store() -> IdentityStore:
    """进程内复用的默认身份库（服务端映射的唯一事实来源）。"""
    global _default_identity
    if _default_identity is None:
        with _identity_lock:
            if _default_identity is None:
                _default_identity = IdentityStore()
    return _default_identity


def _require_user(store: IdentityStore, username: str) -> User:
    return store.require_user(username)


def _authenticate_jwt(header_value: str, store: IdentityStore) -> AuthContext:
    token = header_value
    if token.startswith(_BEARER_PREFIX):
        token = token[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise AuthenticationError("缺少令牌")
    try:
        claims = decode_token(
            token,
            settings.AUTH_JWT_SECRET,
            issuer=settings.AUTH_JWT_ISSUER,
            audience=settings.AUTH_JWT_AUDIENCE,
        )
    except TokenError as exc:
        raise AuthenticationError("令牌校验失败: " + str(exc)) from exc
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("令牌缺少主体标识")
    user = _require_user(store, subject)
    # principal 由服务端从身份库重新映射——令牌载荷中的任何主体声明都不被信任
    return AuthContext(
        username=user.username,
        principal=user.principal,
        display_name=user.display_name,
        roles=user.roles,
        auth_type="jwt",
    )


def _authenticate_session(session_id: str, store: IdentityStore) -> AuthContext:
    session: Session | None = default_session_store().get(session_id)
    if session is None:
        raise AuthenticationError("会话无效或已过期")
    user = _require_user(store, session.username)
    return AuthContext(
        username=user.username,
        principal=user.principal,
        display_name=user.display_name,
        roles=user.roles,
        auth_type="session",
        session_id=session.session_id,
    )


def _cookie_session_id(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    try:
        jar = SimpleCookie()
        jar.load(cookie_header)
        morsel = jar.get(_SESSION_COOKIE)
        return morsel.value if morsel else None
    except Exception:
        return None


def authenticate(headers: Mapping[str, str]) -> AuthContext:
    """从请求头解析认证上下文；未认证抛 AuthenticationError（HTTP 401）。

    顺序：Authorization(Bearer JWT) -> X-Session-ID / session Cookie。
    """
    if not settings.AUTH_ENABLED:
        return AuthContext(
            username=settings.AUTH_DEFAULT_USER,
            principal=settings.AUTH_DEFAULT_PRINCIPAL,
            display_name=settings.AUTH_DEFAULT_DISPLAY,
            roles=frozenset(),
            auth_type="default",
        )

    store = default_identity_store()
    authz = headers.get("Authorization", "")
    if authz:
        return _authenticate_jwt(authz, store)
    sid = headers.get("X-Session-ID") or _cookie_session_id(headers.get("Cookie"))
    if sid:
        return _authenticate_session(sid, store)
    raise AuthenticationError("缺少身份凭证（需要 Bearer 令牌或会话）")


def create_session(user: User) -> Session:
    """为已认证用户签发服务端会话（登出可失效）。"""
    return default_session_store().create(user.username, user.principal)
