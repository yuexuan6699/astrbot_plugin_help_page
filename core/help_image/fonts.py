"""字体解析：优先配置路径，其次插件内置圆体，最后回退系统字体。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_BUNDLED_FONT = (
    Path(__file__).resolve().parent.parent.parent / "assets" / "fonts" / "荆南圆体.ttf"
)

_SYSTEM_FONTS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)

_SYSTEM_FONT_DIRS = (
    "C:/Windows/Fonts",
    "/System/Library/Fonts",
    "/usr/share/fonts",
)

_FALLBACK_KEYWORDS = ("msyh", "notosanscjk", "pingfang", "wqy", "simhei", "simsun")


@lru_cache(maxsize=1)
def resolve_font_path(configured: str = "") -> str:
    """定位可用的字体文件。

    Args:
        configured: 配置项中填写的字体路径，可为空。

    Returns:
        字体文件的绝对路径；全部候选都不可用时返回空字符串。
    """
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(_BUNDLED_FONT)
    candidates.extend(Path(path) for path in _SYSTEM_FONTS)

    for path in candidates:
        if path.is_file():
            return str(path)

    for directory in _SYSTEM_FONT_DIRS:
        root = Path(directory)
        if not root.is_dir():
            continue
        for keyword in _FALLBACK_KEYWORDS:
            for suffix in (".ttc", ".ttf", ".otf"):
                for hit in sorted(root.rglob(f"*{keyword}*{suffix}")):
                    return str(hit)

    return ""


@lru_cache(maxsize=128)
def _load_from(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def load_font(size: int, configured: str = "") -> ImageFont.FreeTypeFont:
    """按字号加载字体，结果带缓存。

    Args:
        size: 字号（像素）。
        configured: 配置项中填写的字体路径。

    Returns:
        可用的字体对象；找不到任何字体文件时回退到 Pillow 内置位图字体。
    """
    size = max(8, int(size))
    path = resolve_font_path(configured)
    if path:
        try:
            return _load_from(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def bundled_font_exists() -> bool:
    """内置圆体是否随插件一起分发。"""
    return _BUNDLED_FONT.is_file()


def describe_font_source(configured: str = "") -> str:
    """返回当前实际生效字体的来源描述，便于在插件页提示用户。"""
    path = resolve_font_path(configured)
    if not path:
        return "未找到可用字体（中文可能显示为方框）"
    if _BUNDLED_FONT.is_file() and os.path.samefile(path, _BUNDLED_FONT):
        return f"插件内置圆体 · {Path(path).name}"
    return f"外部字体 · {path}"
