"""发布意图识别：守卫（PreToolUse）与发布目标（ThreadLedger）共用同一套规则。"""

from __future__ import annotations

import re

# 发布是对外可见的动作：用户本轮消息里有发布意图、且没有明确说"不要发布"，就放行。
# 守卫是兜底（防止模型拿发布去验证写入），CLAUDE.md 的规则才是第一道；所以只做意图匹配，不做句式白名单。
PUBLISH_INTENT = re.compile(r"发布|上线|publish|deploy", re.IGNORECASE)
PUBLISH_NEGATION = re.compile(
    r"(?:不要|别|不用|无需|先不|暂不|禁止)\s*(?:重新)?"
    r"(?:发布|上线|\b(?:publish|deploy)\b)"
    r"|(?:do\s+not|don't|dont|never|not)\s+(?:ever\s+)?(?:re\s*)?"
    r"(?:发布|上线|\b(?:publish|deploy)\b)",
    re.IGNORECASE,
)


def user_requests_publish(text: str) -> bool:
    return bool(PUBLISH_INTENT.search(text)) and not PUBLISH_NEGATION.search(text)


def user_negates_publish(text: str) -> bool:
    return bool(PUBLISH_NEGATION.search(text))
