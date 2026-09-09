"""API Key 落盘加密（存储加密；纯标准库实现）。

威胁模型：providers.json 落盘内容不得出现 API Key 明文（备份 / 泄露 / 误提交
仓库时密钥不直接暴露）。内存中明文仍存在（适配器调用 API 必需），网络层由
认证网关保护；「查看密钥」需显式调用受认证保护的 reveal 端点并记审计日志。

算法（Encrypt-then-MAC，无第三方依赖）：
- 每条密文独立 16 字节随机 nonce；
- 主密钥先归一化为 32 字节（SHA-256），再以 HKDF-SHA256（salt=nonce）派生
  出独立的加密密钥与 MAC 密钥（info 区分域，杜绝跨用途复用）；
- 加密：HMAC-SHA256 计数器模式 keystream 与明文异或（流加密）；
- 校验：HMAC-SHA256 over (nonce || ciphertext)，密文篡改 / 主密钥不匹配
  一律拒绝（恒定时间比较）。

密文格式：``enc1$<nonce_hex>$<ciphertext_hex>$<tag_hex>``；非该前缀的值视为
历史明文（加载时原样返回并自动迁移为加密格式）。

主密钥来源：``settings.PROVIDERS_ENC_SECRET``（优先），缺省回退
``settings.AUTH_JWT_SECRET``。**更换主密钥后已存密钥将无法解密**（校验失败
即抛错，不静默吞掉），需重新在各供应商配置里填写 Key。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_MAGIC = "enc1"
_NONCE_BYTES = 16
_KEY_LEN = 32
_INFO_ENC = b"futurebi.providers.apikey/enc"
_INFO_MAC = b"futurebi.providers.apikey/mac"


def normalize_master(secret: str) -> bytes:
    """把任意长度的口令串归一化为 32 字节主密钥。"""
    return hashlib.sha256(str(secret).encode("utf-8")).digest()


def _hkdf_sha256(master: bytes, salt: bytes, info: bytes) -> bytes:
    """HKDF-SHA256 派生（Extract + Expand，输出 32 字节；RFC 5869 简化实现）。"""
    prk = hmac.new(salt, master, hashlib.sha256).digest()
    okm = b""
    t = b""
    counter = 1
    while len(okm) < _KEY_LEN:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:_KEY_LEN]


def _keystream_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """HMAC-SHA256 计数器模式 keystream 异或（流加密 / 解密共用）。"""
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        chunk = data[len(out) : len(out) + len(block)]
        # 末块 chunk 短于 block：按较短一侧对齐（strict=False 即设计语义）
        out += bytes(a ^ b for a, b in zip(chunk, block, strict=False))
        counter += 1
    return bytes(out)


def is_encrypted(value: str) -> bool:
    """判断值是否为本模块加密格式（enc1$ 前缀）。"""
    return str(value).startswith(_MAGIC + "$")


def encrypt_secret(plaintext: str, master: bytes) -> str:
    """加密字符串 -> enc1 密文；空串原样返回（无密可加密）。"""
    if not plaintext:
        return ""
    nonce = secrets.token_bytes(_NONCE_BYTES)
    enc_key = _hkdf_sha256(master, nonce, _INFO_ENC)
    mac_key = _hkdf_sha256(master, nonce, _INFO_MAC)
    ciphertext = _keystream_xor(enc_key, nonce, plaintext.encode("utf-8"))
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).hexdigest()
    return f"{_MAGIC}${nonce.hex()}${ciphertext.hex()}${tag}"


def decrypt_secret(token: str, master: bytes) -> str:
    """解密 enc1 密文；非加密格式（历史明文）原样返回；校验失败抛 ValueError。"""
    value = str(token)
    if not value:
        return ""
    if not is_encrypted(value):
        return value  # 兼容历史明文（加载时自动迁移）
    parts = value.split("$")
    if len(parts) != 4:
        raise ValueError("API Key 密文格式非法")
    try:
        nonce = bytes.fromhex(parts[1])
        ciphertext = bytes.fromhex(parts[2])
    except ValueError as exc:
        raise ValueError("API Key 密文编码非法") from exc
    mac_key = _hkdf_sha256(master, nonce, _INFO_MAC)
    expected = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts[3]):
        raise ValueError("API Key 密文校验失败（主密钥不匹配或数据被篡改）")
    enc_key = _hkdf_sha256(master, nonce, _INFO_ENC)
    return _keystream_xor(enc_key, nonce, ciphertext).decode("utf-8")


__all__ = [
    "decrypt_secret",
    "encrypt_secret",
    "is_encrypted",
    "normalize_master",
]
