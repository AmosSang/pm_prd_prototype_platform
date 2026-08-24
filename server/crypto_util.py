"""git token 加解密（T2.3）。

规则（技术方案 §2.7 / AGENTS.md §3.6）：
- token 用 Fernet 加密后存 projects.encrypted_token
- 密钥来自环境变量 PLATFORM_SECRET（部署时注入，不进镜像）
- token 不进代码、不进日志、不进 .git/config
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from server.config import PLATFORM_SECRET


def _derive_key() -> bytes:
    """从 PLATFORM_SECRET 派生 Fernet 密钥。

    Fernet 要求 32 字节 base64 密钥；PLATFORM_SECRET 是任意字符串，
    用 SHA-256 派生（同一 secret 派生结果稳定，无随机盐——secret 本身即机密）。
    """
    return base64.urlsafe_b64encode(hashlib.sha256(PLATFORM_SECRET.encode()).digest())


def encrypt_token(token: str) -> str:
    return Fernet(_derive_key()).encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """解密失败抛 InvalidToken（调用方转 500，不把密文内容带出去）。"""
    return Fernet(_derive_key()).decrypt(encrypted.encode()).decode()


def mask_token(token: str) -> str:
    """token 打码展示（如 glpat-****abcd），仅用于日志排查。"""
    if len(token) <= 8:
        return "****"
    return f"****{token[-4:]}"


__all__ = ["decrypt_token", "encrypt_token", "mask_token", "InvalidToken"]
