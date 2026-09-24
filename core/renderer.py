"""条目整理与帮助图输出。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from .help_image import (
    FEATURE_MARK,
    Theme,
    entry_mark,
    is_feature,
    render_help_bytes,
    render_help_image,
)


def build_groups(
    entries: list[dict[str, Any]],
    state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """把已排序的条目折叠成分组结构，并附带分组说明。

    Args:
        entries: merge_entries() 的输出，已按分组与手动顺序排好。
        state: HelpStore.state，用于读取分组说明。

    Returns:
        形如 [{"name": 分组名, "description": 说明, "entries": [...]}, ...] 的列表。
    """
    group_meta = (state or {}).get("groups") or {}
    groups: list[dict[str, Any]] = []
    index: dict[str, int] = {}

    for entry in entries:
        name = entry.get("group") or "其他"
        if name not in index:
            meta = group_meta.get(name)
            description = meta.get("description") if isinstance(meta, dict) else ""
            index[name] = len(groups)
            groups.append(
                {"name": name, "description": str(description or ""), "entries": []}
            )
        groups[index[name]]["entries"].append(entry)

    return groups


def build_text(
    groups: list[dict[str, Any]],
    subtitle: str,
    show_group_header: bool = True,
    feature_mark: str = FEATURE_MARK,
) -> str:
    """生成帮助图的纯文本降级内容。

    排版与帮助图保持一致：关闭分组标题栏时不再打印【分组名】，
    非指令条目去掉开头的斜杠并追加标注（逐条设置，没设过用 feature_mark）。

    Args:
        groups: build_groups() 的输出。
        subtitle: 与帮助图一致的副标题。
        show_group_header: 是否打印分组标题行。
        feature_mark: 条目没单独设过标注时使用的默认标注。

    Returns:
        可直接发送的纯文本帮助内容。
    """
    lines = [subtitle]
    for name, entries in _text_blocks(groups, show_group_header):
        lines.append("")
        if name:
            lines.append(f"【{name}】")
        for entry in entries:
            lines.append(_text_entry_head(entry, feature_mark))
            if entry["description"]:
                lines.append(f"  {entry['description']}")
    return "\n".join(lines)


def _text_blocks(
    groups: list[dict[str, Any]],
    show_group_header: bool,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """把分组整理成文本块；关闭标题栏时全部条目合成一块。"""
    if show_group_header:
        return [(str(group.get("name") or "其他"), group["entries"]) for group in groups]
    merged = [entry for group in groups for entry in group["entries"]]
    return [("", merged)] if merged else []


def _text_entry_head(entry: dict[str, Any], feature_mark: str = FEATURE_MARK) -> str:
    """渲染单条条目的标题行：指令带斜杠，标注（逐条设置）追加在末尾。"""
    aliases = " ".join(f"[{alias}]" for alias in entry["aliases"])
    head = str(entry["command"]) if is_feature(entry) else f"/{entry['command']}"
    mark = entry_mark(entry, feature_mark)
    if mark:
        head = f"{head}  {mark}"
    return f"{head} {aliases}".rstrip()


def build_subtitle(keyword: str, count: int) -> str:
    """生成帮助图副标题。"""
    if keyword:
        return f"关键词「{keyword}」· {count} 条"
    return f"共 {count} 条指令"


def resolve_theme(
    appearance: Theme | dict[str, Any],
    subtitle: str | None = None,
) -> Theme:
    """把外观配置规整成 Theme，并在需要时覆盖副标题。

    主题里填了副标题就以用户填的为准，留空才用自动生成的统计文案。

    Args:
        appearance: Theme 对象或外观配置字典。
        subtitle: 自动生成的副标题，None 表示不改动。

    Returns:
        可直接用于合成的主题对象。
    """
    theme = appearance if isinstance(appearance, Theme) else Theme.from_dict(appearance)
    if subtitle and not theme.subtitle:
        return replace(theme, subtitle=subtitle)
    return theme


async def render_help_png(
    groups: list[dict[str, Any]],
    appearance: Theme | dict[str, Any],
    out_path: str | Path,
    subtitle: str | None = None,
) -> Path:
    """合成帮助图 PNG 并落盘。

    Args:
        groups: build_groups() 的输出。
        appearance: Theme 对象或外观配置字典。
        out_path: 输出文件路径。
        subtitle: 自动生成的副标题。

    Returns:
        实际写入的文件路径。
    """
    theme = resolve_theme(appearance, subtitle)
    return await asyncio.to_thread(render_help_image, groups, theme, out_path)


async def render_help_png_bytes(
    groups: list[dict[str, Any]],
    appearance: Theme | dict[str, Any],
    subtitle: str | None = None,
) -> bytes:
    """合成帮助图 PNG 并直接返回字节，供插件页预览。"""
    theme = resolve_theme(appearance, subtitle)
    return await asyncio.to_thread(render_help_bytes, groups, theme)
