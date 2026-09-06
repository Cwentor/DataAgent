"""身份库：用户 -> 角色 -> 主体(principal) 的服务端映射（P0）。

安全约束：principal 只能由服务端从"已认证身份"映射而来（IdentityStore），
客户端永远无法指定自己的 principal。口令以 PBKDF2-SHA256 哈希存储，比对使用
恒定时间比较（hmac.compare_digest），杜绝时序侧信道。

用户注册表来源（优先级）：
1. settings.AUTH_USERS_FILE 指向的 JSON 文件（存在则加载，便于运维改动）；
2. 否则使用内置 DEFAULT_USERS（与仓库内 auth/users.json 内容一致）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auth.errors import AuthenticationError
from config import settings

# 口令哈希参数（与 auth/users.json 内哈希保持一致）
_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16
_HASH_PREFIX = "pbkdf2_sha256"
# 旧版固定盐（仅用于兼容升级前写入的 64 位十六进制哈希条目，不再用于新哈希）
_LEGACY_SALT = b"futurebi-salt-v1"


def _pbkdf2(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def hash_password(password: str, salt: bytes | None = None) -> str:
    """PBKDF2-SHA256 口令哈希（P0-5：每用户随机盐，杜绝固定盐彩虹表预计算）。

    返回格式 pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>：
    - 盐由 secrets.token_bytes(16) 每次随机生成；同一口令两次哈希结果不同；
    - 迭代次数与盐都编码进哈希字符串，便于未来升级迭代参数而不破坏既有条目。
    """
    salt = salt if salt is not None else secrets.token_bytes(_SALT_BYTES)
    digest = _pbkdf2(password, salt, _PBKDF2_ITERATIONS)
    return f"{_HASH_PREFIX}${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """恒定时间口令比对；空哈希一律拒绝。

    兼容两种哈希格式：
    - 新格式 pbkdf2_sha256$iter$salt_hex$hash_hex（每用户随机盐）；
    - 旧格式 64 位十六进制（固定盐时代写入的 users.json 条目，向后兼容）。
    """
    if not password_hash:
        return False
    parts = password_hash.split("$")
    if len(parts) == 4 and parts[0] == _HASH_PREFIX:
        try:
            iterations = int(parts[1])
            salt = bytes.fromhex(parts[2])
            expected = bytes.fromhex(parts[3])
        except ValueError:
            return False
        computed = _pbkdf2(password, salt, iterations)
        return hmac.compare_digest(computed, expected)
    # 旧版固定盐哈希（向后兼容）
    legacy = _pbkdf2(password, _LEGACY_SALT, _PBKDF2_ITERATIONS).hex()
    return hmac.compare_digest(legacy.encode("ascii"), password_hash.encode("ascii"))


@dataclass(frozen=True)
class User:
    """一个已登记用户（身份库条目）。"""

    username: str
    display_name: str
    # 服务端映射的策略主体（security.policy.POLICIES 的键）——只读派生，客户端不可改
    principal: str
    roles: frozenset[str] = frozenset()
    password_hash: str = ""
    enabled: bool = True


# 内置用户注册表（与 auth/users.json 一致）：
#   admin   -> 主体 admin（全表无限制）
#   analyst -> 主体 analyst（全表但仅 5 省 RLS）
#   bob     -> 主体 restricted（无退款表、无敏感列、仅广东）
DEFAULT_USERS: dict[str, dict[str, Any]] = {
    "admin": {
        "display_name": "系统管理员",
        "principal": "admin",
        "roles": ["admin"],
        "password_hash": "pbkdf2_sha256$200000$fc55e14ae117d5027cfbbbf94ea0d19f$bed75f72cb74d17b0fe2b16421ce4cd27c176bab7357d351073ade0bd7e42846",
    },
    "analyst": {
        "display_name": "分析师",
        "principal": "analyst",
        "roles": ["analyst"],
        "password_hash": "pbkdf2_sha256$200000$27b3ce72cf390f3a7cbcfeb06028a564$8eb1244dd94f853669345f7eadabb2273433a9ef5d6c46b3c24fe1ee4b56a7e8",
    },
    "bob": {
        "display_name": "受限运营",
        "principal": "restricted",
        "roles": ["ops"],
        "password_hash": "pbkdf2_sha256$200000$daf5134f66c2061ec3ae1481688ea83b$1d28e3652ceb7020a6979217b0554a988258db553949c8d242411fe0becfdf3f",
    },
}


class IdentityStore:
    """服务端身份库：认证与 principal 映射的唯一事实来源。"""

    def __init__(self, users_file: Path | str | None = None) -> None:
        """加载身份库：users_file 存在则读取 JSON，否则回退内置 DEFAULT_USERS。"""
        self._users: dict[str, User] = {}
        self._load(Path(users_file) if users_file else settings.AUTH_USERS_FILE)

    def _load(self, path: Path | None) -> None:
        raw: dict[str, Any]
        if path is not None and path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("users", data)
        else:
            raw = DEFAULT_USERS
        for username, info in raw.items():
            self._users[str(username)] = User(
                username=str(username),
                display_name=str(info.get("display_name", username)),
                principal=str(info["principal"]),
                roles=frozenset(str(r) for r in info.get("roles", [])),
                password_hash=str(info.get("password_hash", "")),
                enabled=bool(info.get("enabled", True)),
            )

    def get(self, username: str) -> User | None:
        """按用户名查询用户；不存在返回 None（Lookup by username）。"""
        return self._users.get(username)

    def require_user(self, username: str) -> User:
        """按用户名取用户；不存在或已停用抛 AuthenticationError（fail-closed）。"""
        user = self._users.get(username)
        if user is None:
            raise AuthenticationError(f"用户不存在: {username!r}")
        if not user.enabled:
            raise AuthenticationError(f"用户已停用: {username!r}")
        return user

    def authenticate(self, username: str, password: str) -> User:
        """用户名 + 口令认证；失败抛 AuthenticationError（拒绝而非放行）。"""
        user = self.require_user(username)
        if not verify_password(password, user.password_hash):
            raise AuthenticationError("用户名或口令错误")
        return user

    def principal_for(self, username: str) -> str:
        """服务端把身份映射为策略主体（principal）。"""
        return self.require_user(username).principal
