"""本地帮助图合成：仅依赖 Pillow，无网络请求与第三方配置解析。

对外只暴露 Theme、theme_schema、compose 与 render_help_image 等入口。
"""

from .fonts import describe_font_source, resolve_font_path
from .layout import (
    FEATURE_MARK,
    compose,
    entry_mark,
    is_feature,
    render_help_bytes,
    render_help_image,
)
from .theme import Theme, parse_color, theme_schema

__all__ = [
    "FEATURE_MARK",
    "Theme",
    "compose",
    "describe_font_source",
    "entry_mark",
    "is_feature",
    "parse_color",
    "render_help_bytes",
    "render_help_image",
    "resolve_font_path",
    "theme_schema",
]
