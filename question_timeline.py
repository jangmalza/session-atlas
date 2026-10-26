"""Offline question ordering from existing verified user-message evidence only."""
from __future__ import annotations

from collections import Counter
from html import escape
from urllib.parse import quote

from ui_ko import CLASSIFICATION_KO

QUESTION_CLASSES = frozenset(("open_question", "answered_question"))


def question_order(data: dict) -> dict[str, list[dict]]:
    """Keep one entry per question; anchor it to its earliest verified user turn.

    Indices are the complete transcript's zero-based positions, not positions
    within the smaller set of source messages embedded in a built map. Missing
    timestamps do not invalidate a known message order. Conflicting indices or
    speakers never establish order, and ties do not establish within-turn order.
    """
    supplied = [m for m in data.get("messages", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
    counts = Counter(m["id"] for m in supplied)
    messages = {m["id"]: m for m in supplied if counts[m["id"]] == 1}
    confirmed, unconfirmed = [], []
    for position, node in enumerate(data.get("nodes", [])):
        if node.get("kind") != "item" or node.get("classification") not in QUESTION_CLASSES:
            continue
        entry = {"id": node["id"], "title": node.get("title") or "제목 없는 질문",
                 "classification": node["classification"], "map_position": position}
        references = {}
        for evidence in node.get("evidence", []):
            if evidence.get("verified") is not True or evidence.get("speaker") != "user":
                continue
            message = messages.get(evidence.get("messageId"))
            if not message or message.get("speaker") != "user":
                continue
            index = message.get("index")
            if type(index) is not int or index < 0:
                continue
            if "index" in evidence and (type(evidence["index"]) is not int or evidence["index"] != index):
                continue
            references[message["id"]] = message
        if not references:
            unconfirmed.append(entry)
            continue
        earliest = min(references.values(), key=lambda m: (m["index"], m["id"]))
        timestamp = earliest.get("timestamp")
        confirmed.append({**entry, "message_id": earliest["id"], "message_index": earliest["index"],
                          "timestamp": timestamp if isinstance(timestamp, str) else "", "user_message_count": len(references)})
    confirmed.sort(key=lambda e: (e["message_index"], e["map_position"]))
    ties = Counter(e["message_index"] for e in confirmed)
    for rank, entry in enumerate(confirmed, 1):
        entry["rank"] = rank
        entry["same_message"] = ties[entry["message_index"]] > 1
    return {"confirmed": confirmed, "unconfirmed": unconfirmed}


def render_question_timeline(data: dict) -> str:
    """Render escaped titles and source metadata without copying any quotes."""
    ordered = question_order(data)

    def row(entry: dict, confirmed: bool) -> str:
        identifier = escape(entry["id"], quote=True)
        href = escape("#node=" + quote(entry["id"], safe=""), quote=True)
        title = escape(entry["title"])
        state = "confirmed" if confirmed else "unconfirmed"
        label = escape(CLASSIFICATION_KO[entry["classification"]])
        rank = f'<span class="qo-rank" aria-label="목록 번호 {entry["rank"]}">{entry["rank"]}</span>' if confirmed else ""
        attributes = f' data-question-id="{identifier}" data-question-state="{state}"'
        metadata = f'<span class="qo-classification">{label}</span>'
        if confirmed:
            index = entry["message_index"]
            attributes += f' data-question-index="{index}" data-question-rank="{entry["rank"]}"'
            metadata += f'<span>원문 메시지 #{index + 1}</span>'
            if entry["timestamp"]:
                timestamp = escape(entry["timestamp"], quote=True)
                metadata += f'<time datetime="{timestamp}" data-question-time>{timestamp}</time>'
            else:
                metadata += '<span>시간 정보 없음</span>'
            if entry["user_message_count"] > 1:
                metadata += f'<span>사용자 메시지 {entry["user_message_count"]}개 · 가장 이른 근거 기준</span>'
            if entry["same_message"]:
                metadata += '<span class="qo-note">동일 메시지 · 메시지 안의 질문 순서는 미확정</span>'
        else:
            metadata += '<span class="qo-note">순서 미확정</span>'
        return (f'<li class="qo-item"{attributes}>{rank}<div class="qo-main">'
                f'<a class="qo-link" href="{href}" data-question-link>{title}</a>'
                f'<div class="qo-meta">{metadata}</div></div></li>')

    confirmed = "".join(row(e, True) for e in ordered["confirmed"])
    unconfirmed = "".join(row(e, False) for e in ordered["unconfirmed"])
    if not confirmed:
        confirmed = '<li class="qo-empty">순서를 확인할 수 있는 질문이 없습니다.</li>'
    if not unconfirmed:
        unconfirmed = '<li class="qo-empty">순서 미확정 질문이 없습니다.</li>'
    return f'''<dialog id="cm-question-order" aria-labelledby="cm-question-order-title" aria-describedby="cm-question-order-help">
<header class="qo-head"><h2 id="cm-question-order-title">질문 순서</h2><button type="button" class="btn" data-question-close>닫기</button></header>
<p class="qo-intro" id="cm-question-order-help">각 질문의 가장 이른 사용자 근거 메시지 순서입니다. 목록 번호와 전체 대화의 메시지 번호는 다릅니다. 시간을 이 기기 기준으로 표시합니다. 질문을 선택하면 상세정보로 이동합니다.</p>
<div class="qo-scroll"><section aria-labelledby="cm-questions-confirmed"><h3 id="cm-questions-confirmed">시간순 질문 ({len(ordered['confirmed'])})</h3>
<ol class="qo-list" data-question-list="confirmed">{confirmed}</ol></section>
<section aria-labelledby="cm-questions-unconfirmed"><h3 id="cm-questions-unconfirmed">순서 미확정 ({len(ordered['unconfirmed'])})</h3>
<p class="qo-explanation">질문 시점을 확인할 수 있는 사용자 근거가 없는 항목입니다.</p>
<ul class="qo-list qo-unconfirmed" data-question-list="unconfirmed">{unconfirmed}</ul></section></div>
</dialog>'''
