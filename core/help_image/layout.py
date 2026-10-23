"""帮助图布局：标题区 + 分组标题条 + 卡片网格 + 页脚。

先测量一遍得到总高度，再按同一份计划绘制，保证两趟完全对齐。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image

from .effects import draw_card, draw_rounded_rect, vertical_gradient
from .fonts import load_font
from .text import (
    ascent,
    draw_lines,
    draw_segments,
    ellipsize,
    line_height,
    measure,
    wrap_text,
)
from .theme import Theme, parse_color

RGBA = tuple[int, int, int, int]
Entry = dict[str, Any]
Group = dict[str, Any]

FEATURE_MARK = Theme().feature_mark
"""非指令条目的默认标注文字。

实际渲染取 `theme.feature_mark`，插件页可以改写甚至清空它，这里只保留一份默认值。
"""

TRIGGER_SEP = "、"
"""同一行里多个触发词之间的连接符。"""


def is_feature(entry: Entry) -> bool:
    """判断条目是否为「非指令功能」。"""
    return str(entry.get("kind") or "command") == "feature"


def entry_mark(entry: Entry, feature_default: str = FEATURE_MARK) -> str:
    """条目实际追加的标注文字。

    标注逐条设置，指令与非指令都能用：条目自己带了 `mark` 就用它
    （空串表示这条不标注）；没带时非指令条目回落到 `feature_default`
    （外观参数 feature_mark），指令条目本来就不带标注，因此为空。

    Args:
        entry: 单个条目。
        feature_default: 非指令条目没单独设过时使用的默认标注。

    Returns:
        去掉首尾空白后的标注文字，可能是空串。
    """
    own = entry.get("mark")
    if own is not None:
        return str(own).strip()
    return feature_default.strip() if is_feature(entry) else ""


def entry_triggers(entry: Entry) -> list[str]:
    """条目的全部触发词：主指令在前，别名按原顺序去重跟在后面。

    帮助图里每个触发词都会单独带一个斜杠，所以别名必须和主指令放在同一批里处理。

    Args:
        entry: 单个条目。

    Returns:
        去重后的触发词列表，可能为空。
    """
    triggers: list[str] = []
    seen: set[str] = set()
    for value in [entry.get("command"), *(entry.get("aliases") or [])]:
        text = str(value or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        triggers.append(text)
    return triggers


def _visible_entries(group: Group) -> list[Entry]:
    """过滤掉没有指令名的空条目。"""
    entries = group.get("entries") or []
    return [entry for entry in entries if str(entry.get("command") or "").strip()]


def _build_blocks(groups: list[Group], theme: Theme) -> list[tuple[Group | None, list[Entry]]]:
    """把分组整理成绘制块。

    关闭分组标题栏时所有条目合并成一块，卡片跨分组连排；此时块的分组为 None，
    绘制阶段不会再画标题条。

    Args:
        groups: 分组条目。
        theme: 外观配置。

    Returns:
        形如 [(分组或 None, 该块条目), ...] 的列表，已剔除空块。
    """
    if not theme.show_group_header:
        merged = [entry for group in groups for entry in _visible_entries(group)]
        return [(None, merged)] if merged else []

    blocks: list[tuple[Group | None, list[Entry]]] = []
    for group in groups:
        entries = _visible_entries(group)
        if entries:
            blocks.append((group, entries))
    return blocks


def _measure_card(
    entry: Entry,
    theme: Theme,
    fonts: dict[str, Any],
    text_width: float,
) -> tuple[int, list[str]]:
    """测量单张卡片的像素高度。

    Returns:
        (卡片高度, 已换行的描述文本行)
    """
    command_font = fonts["cmd"]
    description_font = fonts["desc"]

    head_height = line_height(command_font)
    description = str(entry.get("description") or "").strip()
    description_lines = (
        wrap_text(description, description_font, text_width, theme.desc_max_lines)
        if description
        else []
    )
    body_height = (
        theme.line_gap + len(description_lines) * line_height(description_font)
        if description_lines
        else 0
    )
    return theme.card_pad_y * 2 + head_height + body_height, description_lines


def _build_plan(
    groups: list[Group],
    theme: Theme,
    fonts: dict[str, Any],
    columns: int,
) -> tuple[list[tuple], int, int, int]:
    """测量整体布局，生成绘制计划。

    每个计划项都自带 y_advance，绘制阶段直接照搬即可与测量阶段完全一致。

    Returns:
        (计划项列表, 画布总高度, 卡片宽度, 卡片内文字可用宽度)
    """
    padding = theme.padding
    content_width = max(160, theme.width - padding * 2)
    card_width = max(60, (content_width - theme.card_gap * (columns - 1)) // columns)
    text_width = max(40, card_width - theme.card_pad_x * 2)

    plan: list[tuple] = []
    total = padding

    title_height = line_height(fonts["title"])
    subtitle_height = line_height(fonts["subtitle"]) if theme.subtitle else 0
    version_text = theme.version_text.strip()
    version_height = (
        line_height(fonts["version"]) if theme.show_version and version_text else 0
    )
    header_body = title_height
    if subtitle_height:
        header_body += theme.line_gap + subtitle_height
    if version_height:
        header_body += theme.line_gap + version_height
    plan.append(
        ("header", 0, header_body, title_height, subtitle_height, version_height)
    )
    total += header_body

    for group, entries in _build_blocks(groups, theme):
        if group is not None:
            group_height = max(
                line_height(fonts["group"]), line_height(fonts["group_desc"])
            )
            lead = theme.group_gap_top
            body = group_height + theme.group_gap_bottom
            plan.append(("group", lead, body, group, group_height))
            total += lead + body

        rows = [
            entries[index : index + columns]
            for index in range(0, len(entries), columns)
        ]
        for row_index, row in enumerate(rows):
            measured = [_measure_card(entry, theme, fonts, text_width) for entry in row]
            row_height = max(item[0] for item in measured)
            lead = theme.group_gap_top if (group is None and row_index == 0) else 0
            body = row_height + (theme.card_gap if row_index < len(rows) - 1 else 0)
            plan.append(("row", lead, body, list(zip(row, measured)), row_height))
            total += lead + body

    if theme.footer:
        lead = theme.footer_gap_top
        body = line_height(fonts["footer"])
        plan.append(("footer", lead, body))
        total += lead + body

    return plan, total + padding, card_width, text_width


def _load_fonts(theme: Theme) -> dict[str, Any]:
    """按主题字号一次性加载全部字体。"""
    return {
        "title": load_font(theme.title_size, theme.font_path),
        "subtitle": load_font(theme.subtitle_size, theme.font_path),
        "version": load_font(theme.version_size, theme.font_path),
        "group": load_font(theme.group_size, theme.font_path),
        "group_desc": load_font(theme.group_desc_size, theme.font_path),
        "cmd": load_font(theme.cmd_size, theme.font_path),
        "desc": load_font(theme.desc_size, theme.font_path),
        "footer": load_font(theme.footer_size, theme.font_path),
    }


def compose(groups: list[Group], theme: Theme) -> Image.Image:
    """把分组条目渲染成帮助图。

    Args:
        groups: 形如 [{"name": 分组名, "description": 说明, "entries": [...]}, ...]。
        theme: 外观配置。

    Returns:
        RGBA 画布；调用方负责关闭。
    """
    fonts = _load_fonts(theme)
    columns = max(1, int(theme.columns))
    plan, total_height, card_width, text_width = _build_plan(
        groups, theme, fonts, columns
    )

    canvas = vertical_gradient(
        (theme.width, total_height),
        parse_color(theme.bg_top),
        parse_color(theme.bg_bottom),
    )

    padding = theme.padding
    content_width = max(160, theme.width - padding * 2)
    version_text = theme.version_text.strip()
    y = float(padding)

    for item in plan:
        kind = item[0]

        if kind == "header":
            _, lead, body, title_height, subtitle_height, version_height = item
            y += lead
            draw_segments(
                canvas,
                padding,
                y,
                ascent(fonts["title"]),
                [(theme.title, parse_color(theme.title_color), fonts["title"])],
                content_width,
            )
            if subtitle_height:
                draw_segments(
                    canvas,
                    padding,
                    y + title_height + theme.line_gap,
                    ascent(fonts["subtitle"]),
                    [
                        (
                            theme.subtitle,
                            parse_color(theme.subtitle_color),
                            fonts["subtitle"],
                        )
                    ],
                    content_width,
                )
            if version_height:
                version_y = y + title_height + theme.line_gap
                if subtitle_height:
                    version_y += subtitle_height + theme.line_gap
                draw_segments(
                    canvas,
                    padding,
                    version_y,
                    ascent(fonts["version"]),
                    [
                        (
                            version_text,
                            parse_color(theme.version_color),
                            fonts["version"],
                        )
                    ],
                    content_width,
                )
            y += body

        elif kind == "group":
            _, lead, body, group, group_height = item
            y += lead
            text_x = float(padding)

            if theme.group_marker_width > 0:
                marker_top = y + max(
                    0.0, (group_height - line_height(fonts["group"])) / 2
                )
                draw_rounded_rect(
                    canvas,
                    (
                        padding,
                        marker_top,
                        padding + theme.group_marker_width,
                        marker_top + max(8, group_height - 4),
                    ),
                    theme.group_marker_width / 2,
                    parse_color(theme.group_marker),
                )
                text_x = padding + theme.group_marker_width + 10

            reference_ascent = ascent(fonts["group"])
            end_x = draw_segments(
                canvas,
                text_x,
                y,
                reference_ascent,
                [
                    (
                        str(group.get("name") or "其他"),
                        parse_color(theme.group_color),
                        fonts["group"],
                    )
                ],
                content_width - (text_x - padding),
            )

            description = str(group.get("description") or "").strip()
            remaining = content_width - (end_x - padding) - 12
            if description and remaining > 24:
                draw_segments(
                    canvas,
                    end_x + 12,
                    y,
                    reference_ascent,
                    [
                        (
                            description,
                            parse_color(theme.group_desc_color),
                            fonts["group_desc"],
                        )
                    ],
                    remaining,
                )
            y += body

        elif kind == "row":
            _, lead, body, items, row_height = item
            y += lead
            command_font = fonts["cmd"]
            for index, (entry, (_, description_lines)) in enumerate(items):
                x0 = padding + index * (card_width + theme.card_gap)
                draw_card(
                    canvas,
                    (x0, y, x0 + card_width, y + row_height),
                    theme.card_radius,
                    parse_color(theme.card_bg),
                    parse_color(theme.card_border),
                    1,
                    theme.card_shadow,
                    parse_color(theme.card_shadow_color),
                )

                text_x = x0 + theme.card_pad_x
                text_y = y + theme.card_pad_y
                triggers = entry_triggers(entry)
                if is_feature(entry):
                    # 非指令功能整条都不带斜杠，避免看起来像可用的触发词
                    segments: list[tuple[str, RGBA, Any]] = [
                        (
                            TRIGGER_SEP.join(triggers),
                            parse_color(theme.cmd_color),
                            command_font,
                        ),
                    ]
                else:
                    segments = []
                    for index, trigger in enumerate(triggers):
                        if index:
                            segments.append(
                                (
                                    TRIGGER_SEP,
                                    parse_color(theme.desc_color),
                                    command_font,
                                )
                            )
                        segments.append(
                            ("/", parse_color(theme.cmd_accent), command_font)
                        )
                        segments.append(
                            (trigger, parse_color(theme.cmd_color), command_font)
                        )

                # 标注对指令与非指令一视同仁：这条写了就追加，留空就不追加
                mark = entry_mark(entry, theme.feature_mark)
                if mark:
                    segments.append(
                        (
                            "  " + mark,
                            parse_color(theme.desc_color),
                            fonts["desc"],
                        )
                    )
                draw_segments(
                    canvas,
                    text_x,
                    text_y,
                    ascent(command_font),
                    segments,
                    text_width,
                )

                if description_lines:
                    draw_lines(
                        canvas,
                        text_x,
                        text_y + line_height(command_font) + theme.line_gap,
                        description_lines,
                        fonts["desc"],
                        parse_color(theme.desc_color),
                    )
            y += body

        elif kind == "footer":
            _, lead, body = item
            y += lead
            footer_font = fonts["footer"]
            text = ellipsize(theme.footer, footer_font, content_width)
            width = measure(text, footer_font)
            draw_segments(
                canvas,
                padding + max(0.0, (content_width - width) / 2),
                y,
                ascent(footer_font),
                [(text, parse_color(theme.footer_color), footer_font)],
                content_width,
            )
            y += body

    return canvas


def render_help_bytes(groups: list[Group], theme: Theme) -> bytes:
    """合成帮助图并返回 PNG 字节，不落盘。

    插件页预览直接拿字节转 base64，避免为每次预览写临时文件。

    Args:
        groups: 分组条目。
        theme: 外观配置。

    Returns:
        PNG 编码后的字节串。
    """
    canvas = compose(groups, theme)
    buffer = io.BytesIO()
    try:
        canvas.convert("RGB").save(buffer, "PNG", optimize=True)
    finally:
        canvas.close()
    return buffer.getvalue()


def render_help_image(
    groups: list[Group],
    theme: Theme,
    out_path: str | Path,
) -> Path:
    """合成帮助图并保存为 PNG。

    绘图是同步阻塞操作，调用方在异步环境中应通过 asyncio.to_thread 执行。

    Args:
        groups: 分组条目。
        theme: 外观配置。
        out_path: 输出文件路径，父目录会自动创建。

    Returns:
        实际写入的文件路径。
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_help_bytes(groups, theme))
    return path
