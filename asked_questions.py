"""Find items the user asked as a question, using only their own verified quotes.

No model call and no reading of meaning: an item counts as asked when the
analysis left it outside the question and action classes and one of its
verified user quotes ends as a question. The graph builder and the timeline
reader share this rule, so the tree's question branches and their answer
leaves agree.
"""
from __future__ import annotations

import re

QUESTION_CLASSES = frozenset(("open_question", "answered_question"))
# Decisions and actions describe what was decided or done; a question in their
# quote is the request that led there, so they stay where the analysis put them.
ACTION_CLASSES = frozenset(("decision", "superseded_decision", "rejected_proposal",
                            "completed_action", "action_item"))
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_CODE = re.compile(r"`[^`\n]*`")
_TRAILING = " \t\r\n.…⋯!~ㅠㅜ\"'”’)]】」』"
_ENDINGS = ("나요", "어때", "어때요", "어때보여", "뭐야", "뭐지", "뭐죠", "뭔가요", "뭔데")
_FINAL_RIEUL, _FINAL_NIEUN = 8, 4


def _final(syllable: str) -> int | None:
    """The final consonant index of a precomposed Hangul syllable, or None."""
    code = ord(syllable) - 0xAC00
    return code % 28 if 0 <= code < 11172 else None


def reads_as_question(text) -> bool:
    """True when a sentence ends in a question mark or a Korean interrogative ending.

    Links and code spans are ignored first, so a `?id=` in a URL is not a question.
    """
    if not isinstance(text, str):
        return False
    cleaned = _CODE.sub(" ", _URL.sub(" ", text)).strip()
    if re.search(r"[?？]\s", cleaned):
        return True
    cleaned = cleaned.rstrip(_TRAILING)
    if cleaned.endswith(("?", "？")) or cleaned.endswith(_ENDINGS):
        return True
    # "-ㄹ까(요)" and "-ㄴ가요" are questions; "-니까" (because) and "가요" (go) are not.
    for ending, final in (("까", _FINAL_RIEUL), ("까요", _FINAL_RIEUL), ("가요", _FINAL_NIEUN)):
        stem = cleaned.removesuffix(ending)
        if stem != cleaned and stem and _final(stem[-1]) == final:
            return True
    return False


def speakers_of(analysis: dict) -> dict[str, str]:
    """Map message ids to speakers from the analysis's own message list; repeated ids are dropped."""
    result, repeated = {}, set()
    for message in analysis.get("messages", []) if isinstance(analysis, dict) else []:
        if isinstance(message, dict) and isinstance(message.get("id"), str):
            if message["id"] in result:
                repeated.add(message["id"])
            result[message["id"]] = message.get("speaker")
    for identifier in repeated:
        result.pop(identifier, None)
    return result


def asked_by_user(node, speakers: dict[str, str] | None = None) -> bool:
    """True for an item outside the question and action classes with a verified user quote that reads as a question."""
    if not isinstance(node, dict) or node.get("kind", "item") != "item":
        return False
    classification = str(node.get("classification") or "unknown")
    if classification in QUESTION_CLASSES or classification in ACTION_CLASSES:
        return False
    for evidence in node.get("evidence") or []:
        if not isinstance(evidence, dict) or evidence.get("verified") is not True:
            continue
        speaker = evidence.get("speaker")
        if speaker is None and speakers:
            speaker = speakers.get(evidence.get("messageId"))
        if speaker == "user" and reads_as_question(evidence.get("quote")):
            return True
    return False
