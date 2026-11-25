"""文献真实性/状态核验模块。

核验结论由确定性代码计算（而非大模型判断），大模型只负责复述带出处的证据。
"""
from litagent.verify.integrity import (
    Evidence,
    VerificationResult,
    Verifier,
    VERDICT_NOT_FOUND,
    VERDICT_RETRACTED,
    VERDICT_SUSPICIOUS,
    VERDICT_UNCERTAIN,
    VERDICT_VERIFIED,
)

__all__ = [
    "Evidence",
    "VerificationResult",
    "Verifier",
    "VERDICT_RETRACTED",
    "VERDICT_VERIFIED",
    "VERDICT_UNCERTAIN",
    "VERDICT_NOT_FOUND",
    "VERDICT_SUSPICIOUS",
]
