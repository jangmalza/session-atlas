#!/usr/bin/env python3
"""Localize a built Conversation Map's UI without changing its embedded data.

Uses only Python's standard library. No model, network, database, or upstream
checkout is needed. The output retains the original Content Security Policy.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from ui_ko import UI_TRANSLATIONS, TEMPLATE_TRANSLATIONS
from question_timeline import render_question_timeline

_SCRIPTS = re.compile(r"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script\s*>", re.I | re.S)
_STYLE = re.compile(r"<style\b[^>]*>(?P<body>.*?)</style\s*>", re.I | re.S)
_DATA_ID = re.compile(r"\bid\s*=\s*['\"]cm-data['\"]", re.I)
_FONT = ':root{--font:"Malgun Gothic","Apple SD Gothic Neo","Noto Sans KR",ui-sans-serif,system-ui,sans-serif}'
# Desktop hides the pane tabs, leaving two in-flow grid children. A third
# empty row otherwise absorbs space and lets inspector content resize the map.
_LAYOUT = '@media screen and (min-width:901px){.app{grid-template-rows:auto minmax(0,1fr)}}'
_TIMELINE_PANEL = re.compile(r'<dialog\b[^>]*\bid="cm-question-order"[^>]*>.*?</dialog\s*>', re.I | re.S)
_TIMELINE_SCRIPT_START = '\n/* context-graph question timeline script: start */\n'
_TIMELINE_SCRIPT_END = '\n/* context-graph question timeline script: end */\n'
_TIMELINE_STYLE_START = '\n/* context-graph question timeline style: start */\n'
_TIMELINE_STYLE_END = '\n/* context-graph question timeline style: end */\n'
_FRAGMENTS = re.compile("|".join(re.escape(k) for k in sorted(TEMPLATE_TRANSLATIONS, key=len, reverse=True)))
_LITERALS = {**TEMPLATE_TRANSLATIONS, **UI_TRANSLATIONS}


def _static_text(value: str) -> str:
    # Bundled templates spell punctuation such as the middle dot as \xB7.
    # Decode display Unicode, preserving JS delimiters and line terminators.
    def unicode_display(match: re.Match) -> str:
        point = int(match[1] or match[2], 16)
        return (chr(point) if point >= 0xA0 and point not in (0x2028, 0x2029)
                and not 0xD800 <= point <= 0xDFFF else match[0])
    decoded = re.sub(r"(?<!\\)\\(?:x([0-9a-fA-F]{2})|u([0-9a-fA-F]{4}))", unicode_display, value)
    translated = UI_TRANSLATIONS.get(decoded)
    if translated is None:
        translated = _FRAGMENTS.sub(lambda match: TEMPLATE_TRANSLATIONS[match[0]], decoded)
    return value if translated == decoded else translated


def _string_end(source: str, start: int) -> int:
    quote, index = source[start], start + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
        elif source[index] == quote:
            return index + 1
        else:
            index += 1
    raise ValueError("Unterminated viewer string")


def _regex_end(source: str, start: int) -> int | None:
    prefix = source[:start].rstrip()
    if prefix and prefix[-1] not in "([{:;,=!?&|+-*%^~<>" and not re.search(r"\b(return|throw|case|yield)\s*$", prefix):
        return None
    index, in_class = start + 1, False
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            index += 1
            while index < len(source) and source[index].isalpha():
                index += 1
            return index
        elif char in "\r\n":
            return None
        index += 1
    return None


def _template(source: str, start: int) -> tuple[str, int]:
    output, index, text_start = ["`"], start + 1, start + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source.startswith("${", index):
            output.append(_static_text(source[text_start:index]))
            expression, end = _walk(source, index + 2, stop_brace=True)
            if re.fullmatch(r"[A-Za-z_$][\w$]*\.coverage\.status", expression):
                expression = '({complete:"전체",partial:"일부"})[' + expression + ']??' + expression
            output.extend(("${", expression, "}"))
            index = text_start = end + 1
        elif source[index] == "`":
            output.extend((_static_text(source[text_start:index]), "`"))
            return "".join(output), index + 1
        else:
            index += 1
    raise ValueError("Unterminated viewer template")


def _walk(source: str, start: int = 0, stop_brace: bool = False) -> tuple[str, int]:
    output, index, depth = [], start, 0
    while index < len(source):
        char = source[index]
        if source.startswith("//", index):
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
            output.append(source[index:end])
            index = end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise ValueError("Unterminated viewer comment")
            output.append(source[index:end + 2])
            index = end + 2
        elif char in "\"'":
            end = _string_end(source, index)
            token = source[index:end]
            try:
                value = ast.literal_eval(token)
            except (ValueError, SyntaxError):
                value = None
            output.append(json.dumps(_LITERALS[value], ensure_ascii=False)
                          if value in _LITERALS else token)
            index = end
        elif char == "`":
            translated, index = _template(source, index)
            output.append(translated)
        elif char == "/" and (end := _regex_end(source, index)) is not None:
            output.append(source[index:end])
            index = end
        elif stop_brace and char == "}" and depth == 0:
            return "".join(output), index
        else:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            output.append(char)
            index += 1
    if stop_brace:
        raise ValueError("Unterminated viewer expression")
    return "".join(output), index


def translate_viewer_script(script: str) -> str:
    """Translate display literals, preserving selectors, key codes and data enums."""
    return _walk(script)[0]


class _Outline(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.output: list[str] = []
        self.stack: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        original = self.get_starttag_text()
        if tag == "section":
            original = original.replace('aria-label="Static outline"', 'aria-label="정적 개요"')
        self.output.append(original)
        self.stack.append((tag, dict(attrs).get("class") or ""))

    def handle_endtag(self, tag: str) -> None:
        self.output.append(f"</{tag}>")
        if self.stack:
            self.stack.pop()

    def handle_data(self, value: str) -> None:
        classes = self.stack[-1][1].split() if self.stack else []
        if any(c in classes for c in ("so-tag", "so-flag", "so-note")):
            value = " · ".join(UI_TRANSLATIONS.get(part, part) for part in value.split(" · "))
        elif "so-who" in classes:
            value = re.sub(r"^(Unknown|User|Assistant|System|Tool|Other), message ",
                           lambda match: UI_TRANSLATIONS[match[1]] + ", 메시지 ", value)
            value = value.replace(" (quote not found in source)", " (원문에서 인용을 찾지 못함)")
        self.output.append(value)

    def handle_entityref(self, name: str) -> None:
        self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.output.append(f"&#{name};")


def _hash(value: str) -> str:
    return base64.b64encode(hashlib.sha256(value.encode("utf-8")).digest()).decode("ascii")


def _replace_timeline_asset(value: str, start: str, end: str, filename: str) -> str:
    if start in value or end in value:
        if value.count(start) != 1 or value.count(end) != 1:
            raise ValueError("Invalid question timeline asset markers")
        first, last = value.index(start), value.index(end)
        if last < first:
            raise ValueError("Invalid question timeline asset order")
        value = value[:first] + value[last + len(end):]
    asset = Path(__file__).with_name(filename).read_text(encoding="utf-8").strip()
    return value + start + asset + end


def localize_html(document: str) -> str:
    scripts = list(_SCRIPTS.finditer(document))
    data = [match for match in scripts if _DATA_ID.search(match["attrs"])]
    executable = [match for match in scripts if not match["attrs"].strip()]
    style = _STYLE.search(document)
    if len(data) != 1 or len(executable) != 1 or style is None:
        raise ValueError("Expected a self-contained Conversation Map HTML")
    analysis = json.loads(data[0]["body"])
    script = executable[0]
    new_script = translate_viewer_script(script["body"])
    new_script = _replace_timeline_asset(new_script, _TIMELINE_SCRIPT_START, _TIMELINE_SCRIPT_END, "question_timeline.js")
    new_style = style["body"]
    for presentation_rule in (_FONT, _LAYOUT):
        if presentation_rule not in new_style:
            new_style += presentation_rule
    new_style = _replace_timeline_asset(new_style, _TIMELINE_STYLE_START, _TIMELINE_STYLE_END, "question_timeline.css")
    replacements = [(script.start("body"), script.end("body"), new_script),
                    (style.start("body"), style.end("body"), new_style)]
    outline = re.search(r'<section\b[^>]*\bid="static-outline"[^>]*>.*?</section>', document, re.S)
    if outline:
        parser = _Outline()
        parser.feed(outline[0])
        parser.close()
        replacements.append((outline.start(), outline.end(), "".join(parser.output)))
    panel = _TIMELINE_PANEL.search(document)
    timeline = render_question_timeline(analysis)
    if panel:
        replacements.append((panel.start(), panel.end(), timeline))
    else:
        replacements.append((data[0].start(), data[0].start(), timeline + "\n"))
    result = document
    for start, end, replacement in sorted(replacements, reverse=True):
        result = result[:start] + replacement + result[end:]
    result = re.sub(r'(<html\b[^>]*\blang=)["\'][^"\']*["\']', r'\1"ko"', result, count=1, flags=re.I)
    result = result.replace('<p class="boot" role="status">Loading interactive map…</p>',
                            '<p class="boot" role="status">대화형 지도를 불러오는 중…</p>')
    policy = re.search(r'<meta\b[^>]*http-equiv="Content-Security-Policy"[^>]*>', result, re.I)
    if policy is None:
        raise ValueError("Conversation Map Content Security Policy is missing")
    updated_policy = policy[0]
    for old, new in ((script["body"], new_script), (style["body"], new_style)):
        expected = "sha256-" + _hash(old)
        if expected not in updated_policy:
            raise ValueError("Viewer asset does not match its Content Security Policy")
        updated_policy = updated_policy.replace(expected, "sha256-" + _hash(new))
    result = result[:policy.start()] + updated_policy + result[policy.end():]
    new_data = next(match["body"] for match in _SCRIPTS.finditer(result) if _DATA_ID.search(match["attrs"]))
    if data[0]["body"] != new_data:
        raise ValueError("Embedded analysis data must remain byte-for-byte unchanged")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Localize Conversation Map UI into Korean without model calls")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.input.resolve() == args.output.resolve():
            raise ValueError("Choose a separate output to preserve the original HTML")
        with args.input.open("r", encoding="utf-8", newline="") as source:
            result = localize_html(source.read())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8", newline="\n")
        print("Korean UI written; embedded analysis preserved; model calls: 0")
        return 0
    except (OSError, ValueError):
        print("Localization failed; check the input format and separate output path")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
