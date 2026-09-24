"""卡片效果：圆角矩形、描边与高斯阴影。"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

RGBA = tuple[int, int, int, int]

Box = tuple[float, float, float, float]


def draw_rounded_rect(
    canvas: Image.Image,
    box: Box,
    radius: float,
    fill: RGBA,
    outline: RGBA | None = None,
    width: int = 0,
) -> None:
    """在画布上绘制圆角矩形，半径会自动收敛到不超过半宽/半高。"""
    x0, y0, x1, y1 = (round(value) for value in box)
    box_width = x1 - x0
    box_height = y1 - y0
    if box_width <= 0 or box_height <= 0:
        return

    safe_radius = max(0, min(int(radius), box_width // 2, box_height // 2))
    layer = Image.new("RGBA", (box_width, box_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(
        (0, 0, box_width - 1, box_height - 1),
        radius=safe_radius,
        fill=fill,
        outline=outline,
        width=max(0, int(width)),
    )
    canvas.alpha_composite(layer, (x0, y0))
    layer.close()


def draw_card(
    canvas: Image.Image,
    box: Box,
    radius: float,
    fill: RGBA,
    border: RGBA | None = None,
    border_width: int = 0,
    shadow_blur: int = 0,
    shadow_color: RGBA = (0, 0, 0, 110),
    shadow_offset: tuple[int, int] = (0, 4),
) -> None:
    """绘制带阴影与描边的圆角卡片。

    阴影先于卡片绘制，模糊半径决定外扩留白，因此不会覆盖卡片自身。

    Args:
        canvas: 目标画布（RGBA）。
        box: 卡片矩形 (x0, y0, x1, y1)。
        radius: 圆角半径。
        fill: 卡片填充色。
        border: 描边颜色，None 表示不描边。
        border_width: 描边宽度。
        shadow_blur: 阴影模糊半径，0 表示不画阴影。
        shadow_color: 阴影颜色（含透明度）。
        shadow_offset: 阴影相对卡片的偏移。
    """
    x0, y0, x1, y1 = box
    box_width = round(x1 - x0)
    box_height = round(y1 - y0)
    if box_width <= 0 or box_height <= 0:
        return

    if shadow_blur > 0 and shadow_color[3] > 0:
        offset_x, offset_y = shadow_offset
        pad = int(shadow_blur * 2 + max(abs(offset_x), abs(offset_y)))
        shadow_layer = Image.new(
            "RGBA", (box_width + pad * 2, box_height + pad * 2), (0, 0, 0, 0)
        )
        shadow_draw = ImageDraw.Draw(shadow_layer)
        shadow_draw.rounded_rectangle(
            (
                pad + offset_x,
                pad + offset_y,
                pad + offset_x + box_width,
                pad + offset_y + box_height,
            ),
            radius=radius,
            fill=shadow_color,
        )
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow_blur))
        canvas.alpha_composite(shadow_layer, (round(x0) - pad, round(y0) - pad))
        shadow_layer.close()

    draw_rounded_rect(
        canvas,
        box,
        radius,
        fill,
        outline=border,
        width=border_width if border else 0,
    )


def vertical_gradient(size: tuple[int, int], top: RGBA, bottom: RGBA) -> Image.Image:
    """生成竖向线性渐变背景。

    只逐像素计算 1 像素宽的色带再横向拉伸，避免大画布逐点赋值。

    Args:
        size: 画布尺寸 (宽, 高)。
        top: 顶部颜色。
        bottom: 底部颜色。

    Returns:
        与 size 等大的 RGBA 图像。
    """
    _, height = size
    if height <= 1 or top == bottom:
        return Image.new("RGBA", size, top)

    strip = Image.new("RGBA", (1, height))
    pixels = strip.load()
    for y in range(height):
        ratio = y / (height - 1)
        pixels[0, y] = tuple(
            round(top[channel] + (bottom[channel] - top[channel]) * ratio)
            for channel in range(4)
        )
    gradient = strip.resize(size, Image.Resampling.BILINEAR)
    strip.close()
    return gradient
