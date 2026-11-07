"""定義や metadata に埋め込まれた秘密情報らしき値の検出（AWS 非依存の純粋な関数）。

設計書「秘密情報と外部配布物」のとおり、API キー・JWT・パスワードなどの値は Registry にも補助テーブルにも
保存しない。検出したら保存せずに拒否する。検出結果には場所と種類だけを含め、値そのものは返さない。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# 値そのものが秘密情報の形をしているもの（キーの名前に依存しない）
_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"), "JWT"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AWS アクセスキー"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "秘密鍵"),
    (re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE), "Bearer トークン"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "GitHub トークン"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "Slack トークン"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "API キー"),
    (re.compile(r"://[^/\s:@]+:[^/\s@]+@"), "URL に埋め込まれた資格情報"),
)
# `password=...` のように、名前付きで値を代入している文字列。値が参照（プレースホルダー）なら許す
_ASSIGNMENT = re.compile(
    r"\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\b"
    r"""["']?\s*[:=]\s*["']?([^\s"',;&]{8,})""",
    re.IGNORECASE,
)
_QUERY_SECRET = re.compile(
    r"[?&](?:api[_-]?key|access[_-]?token|token|password|passwd|secret)=([^&\s#]{4,})",
    re.IGNORECASE,
)
# JSON のキー名が資格情報を表すもの。値が文字列で、参照でなければ秘密情報とみなす
_SECRET_KEY = re.compile(
    r"(?:password|passwd|secret|api[_-]?key|private[_-]?key"
    r"|(?:access|auth|refresh|id|bearer|session)[_-]?token|token|authorization)$",
    re.IGNORECASE,
)
# 値ではなく、保管先や環境変数への参照・空・状態を表す語
_REFERENCE = re.compile(
    r"^\s*$|^\$\{.+\}$|^\$[A-Za-z_][A-Za-z0-9_]*$|^\{\{.+\}\}$|^<.+>$"
    r"|^(?:arn:|ssm:|secretsmanager:|env:|vault:)|^(?:true|false|null|none|required|optional)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SecretFinding:
    """秘密情報らしき値を見つけた場所と種類。値は持たない。"""

    location: str
    kind: str

    def __str__(self) -> str:
        return f"{self.location}（{self.kind}）"


def _is_reference(value: str) -> bool:
    return bool(_REFERENCE.match(value))


def scan_text(location: str, text: str) -> list[SecretFinding]:
    """自由記述の文字列を検査する。"""
    findings = [
        SecretFinding(location, kind) for pattern, kind in _VALUE_PATTERNS if pattern.search(text)
    ]
    for pattern, kind in (
        (_ASSIGNMENT, "資格情報の代入"),
        (_QUERY_SECRET, "URL のクエリの資格情報"),
    ):
        if any(not _is_reference(match.group(1)) for match in pattern.finditer(text)):
            findings.append(SecretFinding(location, kind))
    return findings


def scan_json(location: str, value: Any) -> list[SecretFinding]:
    """JSON の値を再帰的に検査する。キー名が資格情報を表し、値が参照でない文字列も検出する。"""
    if isinstance(value, str):
        return scan_text(location, value)
    if isinstance(value, Mapping):
        findings: list[SecretFinding] = []
        for key, child in value.items():
            path = f"{location}.{key}"
            if isinstance(child, str) and _SECRET_KEY.search(str(key)) and not _is_reference(child):
                findings.append(SecretFinding(path, "資格情報らしき項目の値"))
            else:
                findings.extend(scan_json(path, child))
        return findings
    if isinstance(value, list):
        return [
            finding
            for index, child in enumerate(value)
            for finding in scan_json(f"{location}[{index}]", child)
        ]
    return []
