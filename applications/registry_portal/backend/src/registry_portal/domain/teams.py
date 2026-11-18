"""所有チーム（Cognito のチームグループ）の名前の規則。

SPA の `OWNER_TEAM_PREFIX`・`OWNER_TEAM_MAX_LENGTH`（frontend/src/administration/api.ts）と揃える。
"""

from __future__ import annotations

import re
from typing import TypeIs

TEAM_GROUP_PREFIX = "team-"
# Cognito のグループ名の上限
OWNER_TEAM_MAX_LENGTH = 128
_OWNER_TEAM = re.compile(
    rf"{re.escape(TEAM_GROUP_PREFIX)}[A-Za-z0-9_./-]"
    rf"{{1,{OWNER_TEAM_MAX_LENGTH - len(TEAM_GROUP_PREFIX)}}}"
)


def is_owner_team(value: object) -> TypeIs[str]:
    """Admin が所有チームとして指定できるグループ名か。"""
    return isinstance(value, str) and _OWNER_TEAM.fullmatch(value) is not None
