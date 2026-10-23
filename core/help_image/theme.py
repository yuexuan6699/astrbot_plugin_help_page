"""帮助图外观参数：配色方案、可调项定义与颜色解析。

配色按四层组织，每个字段只承担一个语义角色：

1. 背景层 `bg_top` / `bg_bottom`：竖向渐变，决定整张图的明暗基调。
2. 表面层 `card_bg` / `card_border` / `card_shadow_color`：卡片与其投影。
3. 文字层 `title_color` > `cmd_color` > `desc_color` 三级层次，
   `subtitle_color` / `group_desc_color` / `footer_color` / `version_color` 是弱化文字。
4. 强调层 `cmd_accent` / `group_marker`：斜杠与分组色条。

成套的「配色方案」定义在 PALETTES 里，切换方案即一次性替换全部颜色字段；
单个字段仍可单独覆盖，用于微调。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace
from typing import Any

RGBA = tuple[int, int, int, int]

DEFAULT_PALETTE = "sky"
"""未指定配色方案时使用的方案键。"""

_HEX_PATTERN = re.compile(
    r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"
)
_RGB_PATTERN = re.compile(
    r"^\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(\d{1,3})\s*)?\)$"
)
_RGB_FN_PATTERN = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})"
    r"\s*(?:,\s*(\d{1,3}(?:\.\d+)?)\s*)?\)$",
    re.IGNORECASE,
)
_NAMED_COLORS: dict[str, RGBA] = {
    "transparent": (0, 0, 0, 0),
    "white": (255, 255, 255, 255),
    "black": (0, 0, 0, 255),
}


def _css_alpha(raw: str | None) -> int:
    """把 `rgba()` 里的透明度转成 0-255。

    沿用 CSS 习惯：0-1 视为比例，大于 1 视为 0-255 的整数，缺省为不透明。
    """
    if raw is None:
        return 255
    value = float(raw)
    if value <= 1:
        return max(0, min(255, round(value * 255)))
    return max(0, min(255, round(value)))


def parse_color(value: Any, default: RGBA = (255, 255, 255, 255)) -> RGBA:
    """解析颜色为 RGBA 元组。

    支持 `#RGB`、`#RGBA`、`#RRGGBB`、`#RRGGBBAA`、`rgb(r,g,b)` / `rgba(r,g,b,a)`、
    `(r,g,b[,a])` 以及 white / black / transparent 三个常用色名。
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
    named = _NAMED_COLORS.get(text.lower())
    if named is not None:
        return named

    if _HEX_PATTERN.match(text):
        body = text[1:]
        if len(body) in (3, 4):
            body = "".join(char * 2 for char in body)
        red, green, blue = (int(body[index : index + 2], 16) for index in (0, 2, 4))
        alpha = int(body[6:8], 16) if len(body) == 8 else 255
        return (red, green, blue, alpha)

    match = _RGB_FN_PATTERN.match(text)
    if match:
        red, green, blue = (min(255, int(match.group(index))) for index in (1, 2, 3))
        return (red, green, blue, _css_alpha(match.group(4)))

    match = _RGB_PATTERN.match(text)
    if match:
        red, green, blue = (min(255, int(match.group(index))) for index in (1, 2, 3))
        alpha = int(match.group(4)) if match.group(4) is not None else 255
        return (red, green, blue, min(255, alpha))

    return default


@dataclass(frozen=True)
class Palette:
    """一套完整的配色方案。

    颜色项必须覆盖 PALETTE_COLOR_FIELDS 的全部字段，缺项由 Theme 的默认值补齐。
    """

    key: str
    name: str
    hint: str
    colors: dict[str, str]


PALETTE_COLOR_FIELDS: tuple[str, ...] = (
    "bg_top",
    "bg_bottom",
    "title_color",
    "subtitle_color",
    "group_color",
    "group_desc_color",
    "group_marker",
    "card_bg",
    "card_border",
    "card_shadow_color",
    "cmd_color",
    "cmd_accent",
    "desc_color",
    "footer_color",
    "version_color",
)
"""配色方案必须提供的颜色字段，顺序即面板里的展示顺序。"""

PALETTES: dict[str, Palette] = {
    "sky": Palette(
        key="sky",
        name="晴空",
        hint="天蓝到暖奶油的渐变，白色玻璃卡片，珊瑚橙强调",
        colors={
            "bg_top": "#b9e3f3",
            "bg_bottom": "#fbeed0",
            "title_color": "#20364f",
            "subtitle_color": "#5e7288",
            "group_color": "#20364f",
            "group_desc_color": "#5e7288",
            "group_marker": "#f2765a",
            "card_bg": "#ffffffa8",
            "card_border": "#ffffffd9",
            "card_shadow_color": "#20364f24",
            "cmd_color": "#20364f",
            "cmd_accent": "#f2765a",
            "desc_color": "#5e7288",
            "footer_color": "#6d8095",
            "version_color": "#7b8ea2",
        },
    ),
    "night": Palette(
        key="night",
        name="夜幕",
        hint="深靛蓝渐变配半透明卡片，薄荷绿强调，适合深色群聊",
        colors={
            "bg_top": "#151c2c",
            "bg_bottom": "#262f45",
            "title_color": "#eef3ff",
            "subtitle_color": "#98a7c2",
            "group_color": "#e6edfb",
            "group_desc_color": "#8c9bb6",
            "group_marker": "#5fe0b7",
            "card_bg": "#ffffff12",
            "card_border": "#ffffff2b",
            "card_shadow_color": "#00000080",
            "cmd_color": "#eaf1ff",
            "cmd_accent": "#5fe0b7",
            "desc_color": "#9aa9c4",
            "footer_color": "#7d8ca7",
            "version_color": "#71809a",
        },
    ),
    "forest": Palette(
        key="forest",
        name="森屿",
        hint="薄荷绿到米白，墨绿文字配琥珀色强调",
        colors={
            "bg_top": "#d3e9d9",
            "bg_bottom": "#f8f4e4",
            "title_color": "#1d3a2e",
            "subtitle_color": "#587263",
            "group_color": "#1d3a2e",
            "group_desc_color": "#587263",
            "group_marker": "#e08a3c",
            "card_bg": "#ffffffa6",
            "card_border": "#ffffffd4",
            "card_shadow_color": "#1d3a2e22",
            "cmd_color": "#1d3a2e",
            "cmd_accent": "#d9822f",
            "desc_color": "#587263",
            "footer_color": "#68806f",
            "version_color": "#77897c",
        },
    ),
    "paper": Palette(
        key="paper",
        name="纸页",
        hint="米白纸张质感，炭黑文字配朱红强调，最克制的一套",
        colors={
            "bg_top": "#f7f4ed",
            "bg_bottom": "#e9e3d7",
            "title_color": "#2b2a28",
            "subtitle_color": "#6b655e",
            "group_color": "#2b2a28",
            "group_desc_color": "#6b655e",
            "group_marker": "#c2452d",
            "card_bg": "#fffdf8",
            "card_border": "#dcd5c7",
            "card_shadow_color": "#3a352c1c",
            "cmd_color": "#2b2a28",
            "cmd_accent": "#c2452d",
            "desc_color": "#6b655e",
            "footer_color": "#7b746b",
            "version_color": "#8a8379",
        },
    ),
    "sakura": Palette(
        key="sakura",
        name="樱雾",
        hint="浅粉到奶油白，莓红强调，柔和但对比度仍然够用",
        colors={
            "bg_top": "#ffe1e9",
            "bg_bottom": "#fff5ea",
            "title_color": "#4a2c37",
            "subtitle_color": "#8d6a78",
            "group_color": "#4a2c37",
            "group_desc_color": "#8d6a78",
            "group_marker": "#d44e78",
            "card_bg": "#ffffffb0",
            "card_border": "#ffffffd9",
            "card_shadow_color": "#4a2c3722",
            "cmd_color": "#4a2c37",
            "cmd_accent": "#d44e78",
            "desc_color": "#8d6a78",
            "footer_color": "#9b7b88",
            "version_color": "#a88d97",
        },
    ),
}
"""内置配色方案，键与 Palette.key 一致，顺序即面板里的展示顺序。"""


def palette_key(value: Any) -> str:
    """把任意配置值规整成合法的方案键。

    Args:
        value: 配置里读到的方案键。

    Returns:
        已注册的方案键；无法识别时回落到 DEFAULT_PALETTE。
    """
    key = str(value or "").strip().lower()
    return key if key in PALETTES else DEFAULT_PALETTE


def palette_colors(key: Any) -> dict[str, str]:
    """取某个方案的全部颜色，未知键回落到默认方案。"""
    return dict(PALETTES[palette_key(key)].colors)


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
    "version_size": (9, 36),
}

_BOOL_FIELDS = ("show_group_header", "show_version")

_COLOR_FIELDS = PALETTE_COLOR_FIELDS

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
    "show_version": "显示版本行",
    "version_size": "版本行字号",
    "version_color": "版本行颜色",
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
    "card_shadow_color": "卡片阴影颜色",
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
            "show_version",
            "version_size",
            "version_color",
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
            "card_shadow_color",
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
        {"sections": [{"key", "title", "fields": [...]}, ...],
         "palettes": [{"key", "name", "hint", "colors"}, ...],
         "default_palette": 方案键}，
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

    return {
        "sections": sections,
        "palettes": [
            {
                "key": palette.key,
                "name": palette.name,
                "hint": palette.hint,
                "colors": dict(palette.colors),
            }
            for palette in PALETTES.values()
        ],
        "default_palette": DEFAULT_PALETTE,
    }


@dataclass(frozen=True)
class Theme:
    """帮助图外观配置，全部字段均可由插件页覆盖。

    颜色字段默认值取自 `sky`（晴空）方案：天蓝到暖奶油的竖向渐变，
    半透明白色玻璃卡片配同色系柔和投影，文字用深靛蓝到浅灰蓝四级层次，
    斜杠与分组色条使用珊瑚橙作强调。切换 `palette` 会整体替换这些颜色。
    """

    width: int = 1024
    padding: int = 20
    gap: int = 13

    # 配色方案键，只影响颜色字段，不影响字号与间距
    palette: str = DEFAULT_PALETTE

    bg_top: str = "#b9e3f3"
    bg_bottom: str = "#fbeed0"

    title: str = "功能帮助"
    title_size: int = 45
    title_color: str = "#20364f"
    subtitle: str = ""
    subtitle_size: int = 23
    subtitle_color: str = "#5e7288"

    # 版本行：显示 AstrBot 本体版本与 WebUI 版本。
    # version_text 由插件在运行时注入（外部配置无法写入，故不列入 _TEXT_LIMITS），
    # 只有开关、字号、颜色开放给用户调整。
    show_version: bool = True
    version_size: int = 14
    version_color: str = "#7b8ea2"
    version_text: str = ""

    show_group_header: bool = True
    group_size: int = 18
    group_color: str = "#20364f"
    group_desc_size: int = 10
    group_desc_color: str = "#5e7288"
    group_marker: str = "#f2765a"
    group_marker_width: int = 16
    group_gap_top: int = 12
    group_gap_bottom: int = 12

    columns: int = 3
    card_gap: int = 15
    card_pad_x: int = 13
    card_pad_y: int = 13
    card_radius: int = 14
    card_bg: str = "#ffffffa8"
    card_border: str = "#ffffffd9"
    card_shadow: int = 8
    card_shadow_color: str = "#20364f24"

    cmd_size: int = 20
    cmd_color: str = "#20364f"
    cmd_accent: str = "#f2765a"
    # 非指令条目没单独设过标注时用的默认文字，留空则这些条目不追加标注
    feature_mark: str = ""
    desc_size: int = 15
    desc_color: str = "#5e7288"
    desc_max_lines: int = 3
    line_gap: int = 5

    footer: str = ""
    footer_size: int = 9
    footer_color: str = "#6d8095"
    footer_gap_top: int = 0

    font_path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Theme:
        """用外部配置构造主题，越界与非法值一律回退到默认。

        先按 `palette` 铺一整套餐色，再逐个字段覆盖用户显式提交的值；
        因此只存了方案键的配置也能得到完整主题。

        Args:
            data: 插件页或配置项提交的键值对，允许只给出部分字段。

        Returns:
            校验后的主题对象。
        """
        if not isinstance(data, dict):
            return cls()

        key = palette_key(data.get("palette"))
        base = replace(cls(), palette=key, **palette_colors(key))

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
                overrides[name] = _as_bool(raw, getattr(base, name))
            elif name in _COLOR_FIELDS:
                if isinstance(raw, str) and not raw.strip():
                    continue
                overrides[name] = raw
            elif name in _TEXT_LIMITS:
                text = str(raw).replace("\r", " ").replace("\n", " ").strip()
                overrides[name] = text[: _TEXT_LIMITS[name]]

        return replace(base, **overrides)
