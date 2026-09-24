"""文字度量与绘制：按像素宽度换行、截断，并支持一行内多色分段。"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont

RGBA = tuple[int, int, int, int]

_WORD_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z_'’.\-]*")

ELLIPSIS = "…"

_NO_LINE_START = set("，。、；：？！）》」』】〕〉·,.!?;:)]}>»”’%")
"""禁止出现在行首的字符（中文避头尾规则）。"""

_NO_LINE_END = set("（《「『【〔〈([{<«“‘")
"""禁止出现在行尾的字符（中文避头尾规则）。"""


def tokenize(text: str) -> list[str]:
    """把文本切成可换行的最小单元。

    连续的拉丁字母数字视为一个整体，避免英文单词被拦腰截断；
    其余字符（含中文、标点、空格）逐字成组，保证中文可任意换行。

    Args:
        text: 原始文本。

    Returns:
        切分后的单元列表。
    """
    tokens: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        match = _WORD_PATTERN.match(text, index)
        if match:
            tokens.append(match.group(0))
            index = match.end()
            continue
        tokens.append(text[index])
        index += 1
    return tokens


def measure(text: str, font: ImageFont.FreeTypeFont) -> float:
    """返回文本在给定字体下的像素宽度。"""
    if not text:
        return 0.0
    return font.getlength(text)


def ascent(font: ImageFont.FreeTypeFont) -> int:
    """返回字体基线到顶部的距离，用于多字号基线对齐。"""
    return font.getmetrics()[0]


def line_height(font: ImageFont.FreeTypeFont) -> int:
    """返回单行文字占用的像素高度。"""
    ascent_value, descent_value = font.getmetrics()
    return ascent_value + descent_value


def ellipsize(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: float,
    suffix: str = ELLIPSIS,
) -> str:
    """把超宽文本截断到指定宽度，末尾补省略号。"""
    if max_width <= 0:
        return ""
    if measure(text, font) <= max_width:
        return text

    budget = max_width - measure(suffix, font)
    if budget <= 0:
        return suffix

    kept = ""
    for token in tokenize(text):
        if measure(kept + token, font) > budget:
            break
        kept += token
    return kept.rstrip() + suffix


def wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: float,
    max_lines: int = 0,
) -> list[str]:
    """按像素宽度做贪心换行。

    Args:
        text: 原始文本，其中 `\\n` 会被当作强制换行。
        font: 用于度量的字体。
        max_width: 每行最大像素宽度。
        max_lines: 最多保留的行数，0 表示不限制；超出时末行加省略号。

    Returns:
        换行后的行列表，至少含一个元素（可能为空串）。
    """
    if max_width <= 0:
        return []

    lines: list[str] = []
    for paragraph in str(text).split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for token in tokenize(paragraph):
            if not current and token.isspace():
                continue
            if current and measure(current + token, font) > max_width:
                if token in _NO_LINE_START:
                    # 避头尾：标点不另起一行，允许轻微悬挂出边界
                    current += token
                    continue
                carry = ""
                if current[-1] in _NO_LINE_END:
                    # 避头尾：左括号不留在行尾，挪到下一行
                    carry = current[-1]
                    current = current[:-1]
                lines.append(current.rstrip())
                current = carry + ("" if token.isspace() else token)
            else:
                current += token
        lines.append(current.rstrip())

    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = ellipsize(lines[-1], font, max_width)

    return lines or [""]


def draw_segments(
    canvas: Image.Image,
    x: float,
    y: float,
    reference_ascent: int,
    segments: Sequence[tuple[str, RGBA, ImageFont.FreeTypeFont]],
    max_width: float,
) -> float:
    """在同一行内绘制多段不同颜色/字号的文本。

    各段按基线对齐；累计宽度超出 max_width 时，最后一段自动加省略号。

    Args:
        canvas: 目标画布（RGBA）。
        x: 起始横坐标。
        y: 行顶纵坐标。
        reference_ascent: 基准字体的 ascent，用于多字号对齐。
        segments: (文本, 颜色, 字体) 三元组序列。
        max_width: 本行可用像素宽度。

    Returns:
        绘制结束后的横坐标，便于紧接着追加内容。
    """
    draw = ImageDraw.Draw(canvas)
    cursor = x
    remaining = max_width

    for index, (text, color, font) in enumerate(segments):
        if remaining <= 0 or not text:
            break
        width = measure(text, font)
        is_last = index == len(segments) - 1
        if width > remaining:
            text = ellipsize(text, font, remaining)
            width = measure(text, font)
            is_last = True
        if text:
            offset = reference_ascent - ascent(font)
            draw.text((cursor, y + offset), text, font=font, fill=color)
        cursor += width
        remaining -= width
        if is_last:
            break

    return cursor


def draw_lines(
    canvas: Image.Image,
    x: float,
    y: float,
    lines: Iterable[str],
    font: ImageFont.FreeTypeFont,
    color: RGBA,
) -> float:
    """逐行绘制同字体同颜色的文本。

    Returns:
        绘制结束后的纵坐标。
    """
    draw = ImageDraw.Draw(canvas)
    step = line_height(font)
    cursor = y
    for line in lines:
        if line:
            draw.text((x, cursor), line, font=font, fill=color)
        cursor += step
    return cursor
