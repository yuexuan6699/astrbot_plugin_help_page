"""帮助图外观参数：定义全部可调项、取值范围与颜色解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace
from typing import Any

RGBA = tuple[int, int, int, int]

_HEX_PATTERN = re.compile(
    r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"
)
_RGB_PATTERN = re.compile(
    r"^\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(\d{1,3})\s*)?\)$"
)


def parse_color(value: Any, default: RGBA = (255, 255, 255, 255)) -> RGBA:
    """解析颜色为 RGBA 元组。

    支持 `#RGB`、`#RGBA`、`#RRGGBB`、`#RRGGBBAA` 与 `(r,g,b[,a])` 两种写法，
    无法识别时回退到 default，避免非法配置导致合成中断。

    Args:
        value: 待解析的颜色值。
        default: 解析失败时使用的兜底颜色。

    Returns:
        形如 (r, g, b, a) 的四元组，各通道取值 0-255。
    """
    if isinstance(value, (tuple, list)) and len(value) in (3, 4):
        try:
            channels = [max(0, min(255, int(channel))) for channel in value]
        except (TypeError, ValueError):
            return default
        return (
            channels[0],
            channels[1],
            channels[2],
            channels[3] if len(channels) == 4 else 255,
        )

    if not isinstance(value, str):
        return default

    text = value.strip()
    if _HEX_PATTERN.match(text):
        body = text[1:]
        if len(body) in (3, 4):
            body = "".join(char * 2 for char in body)
        red, green, blue = (int(body[index : index + 2], 16) for index in (0, 2, 4))
        alpha = int(body[6:8], 16) if len(body) == 8 else 255
        return (red, green, blue, alpha)

    match = _RGB_PATTERN.match(text)
    if match:
        red, green, blue = (min(255, int(match.group(index))) for index in (1, 2, 3))
        alpha = int(match.group(4)) if match.group(4) is not None else 255
        return (red, green, blue, min(255, alpha))

    return default


_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "width": (480, 2000),
    "padding": (0, 120),
    "gap": (0, 120),
    "title_size": (14, 80),
    "subtitle_size": (10, 48),
    "group_size": (12, 48),
    "group_desc_size": (10, 40),
    "group_marker_width": (0, 20),
    "group_gap_top": (0, 80),
    "group_gap_bottom": (0, 60),
    "columns": (1, 5),
    "card_gap": (0, 40),
    "card_pad_x": (4, 60),
    "card_pad_y": (4, 60),
    "card_radius": (0, 40),
    "card_shadow": (0, 30),
    "cmd_size": (10, 48),
    "desc_size": (9, 36),
    "desc_max_lines": (1, 8),
    "line_gap": (0, 30),
    "footer_size": (9, 32),
    "footer_gap_top": (0, 80),
}

_BOOL_FIELDS = ("show_group_header",)

_COLOR_FIELDS = (
    "bg_top",
    "bg_bottom",
    "title_color",
    "subtitle_color",
    "group_color",
    "group_desc_color",
    "group_marker",
    "card_bg",
    "card_border",
    "cmd_color",
    "cmd_accent",
    "desc_color",
    "footer_color",
)

_TEXT_LIMITS = {
    "title": 40,
    "subtitle": 100,
    "footer": 100,
    "feature_mark": 20,
    "font_path": 260,
}

_FIELD_LABELS: dict[str, str] = {
    "width": "画布宽度",
    "padding": "外边距",
    "gap": "行间距",
    "bg_top": "背景顶部色",
    "bg_bottom": "背景底部色",
    "title": "主标题",
    "title_size": "主标题字号",
    "title_color": "主标题颜色",
    "subtitle": "副标题",
    "subtitle_size": "副标题字号",
    "subtitle_color": "副标题颜色",
    "show_group_header": "显示分组标题栏",
    "group_size": "分组字号",
    "group_color": "分组颜色",
    "group_desc_size": "分组说明字号",
    "group_desc_color": "分组说明颜色",
    "group_marker": "分组色条颜色",
    "group_marker_width": "分组色条宽度",
    "group_gap_top": "分组上方间距",
    "group_gap_bottom": "分组下方间距",
    "columns": "每行列数",
    "card_gap": "卡片横向间距",
    "card_pad_x": "卡片左右内边距",
    "card_pad_y": "卡片上下内边距",
    "card_radius": "卡片圆角",
    "card_bg": "卡片背景色",
    "card_border": "卡片描边色",
    "card_shadow": "卡片阴影模糊",
    "cmd_size": "指令字号",
    "cmd_color": "指令颜色",
    "cmd_accent": "斜杠颜色",
    "feature_mark": "默认标注",
    "desc_size": "说明字号",
    "desc_color": "说明颜色",
    "desc_max_lines": "说明最多行数",
    "line_gap": "指令与说明间距",
    "footer": "页脚文字",
    "footer_size": "页脚字号",
    "footer_color": "页脚颜色",
    "footer_gap_top": "页脚上方间距",
    "font_path": "字体文件路径",
}

_SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("canvas", "画布与留白", ("width", "padding", "gap")),
    ("background", "背景", ("bg_top", "bg_bottom")),
    (
        "header",
        "标题区",
        (
            "title",
            "title_size",
            "title_color",
            "subtitle",
            "subtitle_size",
            "subtitle_color",
        ),
    ),
    (
        "group",
        "分组标题条",
        (
            "show_group_header",
            "group_size",
            "group_color",
            "group_desc_size",
            "group_desc_color",
            "group_marker",
            "group_marker_width",
            "group_gap_top",
            "group_gap_bottom",
        ),
    ),
    (
        "card",
        "卡片",
        (
            "columns",
            "card_gap",
            "card_pad_x",
            "card_pad_y",
            "card_radius",
            "card_bg",
            "card_border",
            "card_shadow",
        ),
    ),
    (
        "text",
        "指令文字",
        (
            "cmd_size",
            "cmd_color",
            "cmd_accent",
            "feature_mark",
            "desc_size",
            "desc_color",
            "desc_max_lines",
            "line_gap",
        ),
    ),
    (
        "footer",
        "页脚",
        ("footer", "footer_size", "footer_color", "footer_gap_top"),
    ),
    ("font", "字体", ("font_path",)),
)


def _field_kind(name: str) -> str:
    """推断字段在面板中的控件类型。"""
    if name in _INT_BOUNDS:
        return "int"
    if name in _BOOL_FIELDS:
        return "bool"
    if name in _COLOR_FIELDS:
        return "color"
    return "text"


def _as_bool(value: Any, default: bool) -> bool:
    """把配置里的布尔值规整成 bool，兼容字符串写法。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "0", "false", "no", "off"}:
            return False
        if text in {"1", "true", "yes", "on"}:
            return True
    return default


def theme_schema() -> dict[str, Any]:
    """输出外观参数的面板描述，供插件页动态生成配置控件。

    Returns:
        {"sections": [{"key", "title", "fields": [...]}, ...]}，
        每个 field 含 name、label、kind、default，int 类型额外带 min/max。
    """
    defaults = Theme()
    sections: list[dict[str, Any]] = []
    for key, title, names in _SECTIONS:
        fields_out: list[dict[str, Any]] = []
        for name in names:
            field: dict[str, Any] = {
                "name": name,
                "label": _FIELD_LABELS.get(name, name),
                "kind": _field_kind(name),
                "default": getattr(defaults, name),
            }
            if name in _INT_BOUNDS:
                low, high = _INT_BOUNDS[name]
                field["min"] = low
                field["max"] = high
            fields_out.append(field)
        sections.append({"key": key, "title": title, "fields": fields_out})
    return {"sections": sections}


@dataclass(frozen=True)
class Theme:
    """帮助图外观配置，全部字段均可由插件页覆盖。

    默认值取自「晴空冰淇淋」配色：背景为天蓝到暖黄奶油的竖向渐变，
    卡片是浅天蓝底、半透明白描边，文字用深灰蓝与浅灰蓝，强调位用活力珊瑚橙。
    """

    width: int = 1024
    padding: int = 20
    gap: int = 13

    bg_top: str = "#ade8f4"
    bg_bottom: str = "#ffedbc"

    title: str = "功能帮助"
    title_size: int = 45
    title_color: str = "#324664"
    subtitle: str = ""
    subtitle_size: int = 23
    subtitle_color: str = "#8291a5"

    show_group_header: bool = True
    group_size: int = 18
    group_color: str = "#324664"
    group_desc_size: int = 10
    group_desc_color: str = "#8291a5"
    group_marker: str = "#ff8c6e"
    group_marker_width: int = 16
    group_gap_top: int = 12
    group_gap_bottom: int = 12

    columns: int = 3
    card_gap: int = 15
    card_pad_x: int = 13
    card_pad_y: int = 13
    card_radius: int = 14
    card_bg: str = "#c8f0fa"
    card_border: str = "#ffffff50"
    card_shadow: int = 8

    cmd_size: int = 20
    cmd_color: str = "#324664"
    cmd_accent: str = "#ff8c6e"
    # 非指令条目没单独设过标注时用的默认文字，留空则这些条目不追加标注
    feature_mark: str = ""
    desc_size: int = 15
    desc_color: str = "#8291a5"
    desc_max_lines: int = 3
    line_gap: int = 5

    footer: str = ""
    footer_size: int = 9
    footer_color: str = "#8291a5"
    footer_gap_top: int = 0

    font_path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Theme:
        """用外部配置构造主题，越界与非法值一律回退到默认。

        Args:
            data: 插件页或配置项提交的键值对，允许只给出部分字段。

        Returns:
            校验后的主题对象。
        """
        if not isinstance(data, dict):
            return cls()

        overrides: dict[str, Any] = {}
        for field in fields(cls):
            name = field.name
            if name not in data:
                continue
            raw = data[name]

            if name in _INT_BOUNDS:
                low, high = _INT_BOUNDS[name]
                try:
                    value = int(raw)
                except (TypeError, ValueError):
                    continue
                overrides[name] = max(low, min(high, value))
            elif name in _BOOL_FIELDS:
                overrides[name] = _as_bool(raw, getattr(cls(), name))
            elif name in _COLOR_FIELDS:
                if isinstance(raw, str) and not raw.strip():
                    continue
                overrides[name] = raw
            elif name in _TEXT_LIMITS:
                text = str(raw).replace("\r", " ").replace("\n", " ").strip()
                overrides[name] = text[: _TEXT_LIMITS[name]]

        return replace(cls(), **overrides)
