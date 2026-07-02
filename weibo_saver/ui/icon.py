"""托盘图标生成器：用 Pillow 程序化生成图标."""

from __future__ import annotations

import struct
import zlib
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def generate_tray_icon(size: int = 64) -> "Image.Image | None":
    """生成一个简单的微博存档图标.

    Returns:
        PIL Image 对象，或 None（如果 Pillow 不可用）
    """
    if not HAS_PIL:
        return None

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景圆
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(231, 76, 60, 255),  # 微博红
    )

    # "W" 字样
    center = size // 2
    text_size = size // 3
    draw.rectangle(
        [
            center - text_size // 2,
            center - text_size // 3,
            center + text_size // 2,
            center + text_size // 3,
        ],
        fill=(255, 255, 255, 255),
    )

    # 左下角小圆点（存档标记）
    dot_r = size // 8
    draw.ellipse(
        [
            size - margin - dot_r * 2 - 2,
            size - margin - dot_r * 2 - 2,
            size - margin - 2,
            size - margin - 2,
        ],
        fill=(46, 204, 113, 255),  # 绿色
    )

    return img


def generate_ico_bytes(sizes: list[int] | None = None) -> bytes:
    """生成 ICO 格式的字节数据.

    Args:
        sizes: 要包含的图标尺寸列表，默认 [16, 32, 48, 64]

    Returns:
        ICO 文件的字节数据
    """
    if not HAS_PIL:
        # 返回一个最小的有效 ICO（1x1 透明像素）
        return _make_minimal_ico()

    if sizes is None:
        sizes = [16, 32, 48, 64]

    images: list[Image.Image] = []
    for s in sizes:
        img = generate_tray_icon(s)
        if img:
            images.append(img)

    if not images:
        return _make_minimal_ico()

    # 保存为 ICO
    buf = BytesIO()
    images[0].save(
        buf,
        format="ICO",
        sizes=[(img.width, img.height) for img in images],
    )
    return buf.getvalue()


def save_icon_to_file(path: str | Path, sizes: list[int] | None = None) -> Path:
    """保存图标到文件.

    Args:
        path: 目标路径 (.ico 或 .png)
        sizes: 尺寸列表

    Returns:
        保存的文件路径
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() == ".ico":
        data = generate_ico_bytes(sizes)
        path.write_bytes(data)
    elif path.suffix.lower() == ".png":
        img = generate_tray_icon(128)
        if img:
            img.save(path, format="PNG")
    else:
        # 默认 ICO
        data = generate_ico_bytes(sizes)
        path = path.with_suffix(".ico")
        path.write_bytes(data)

    return path


def _make_minimal_ico() -> bytes:
    """生成最小的有效 ICO 文件（1x1 蓝色像素）."""
    # ICO header + 1 entry + minimal BMP data
    # 这只是一个格式正确的占位符
    width = 16
    height = 16
    # XOR mask: 16x16 blue pixels (BGRA)
    xor_size = width * height * 4
    header = struct.pack("<HHH", 0, 1, 1)  # reserved, type=ICO, count=1

    # BMP info header
    bmp_size = 40 + xor_size + (width * height // 8)  # header + pixels + AND mask
    entry = struct.pack(
        "<BBBBHHII",
        width, height, 0, 0,  # 0 = no color palette
        1, 32,  # planes=1, bpp=32
        bmp_size, 22,  # size, offset=22 (6+16)
    )

    # BMP data
    bmp_header = struct.pack(
        "<IiiHHIIiiII",
        40,  # header size
        width, height * 2,  # width, height (doubled for ICO)
        1, 32,  # planes, bpp
        0, xor_size,  # compression=BI_RGB, size
        0, 0, 0, 0,  # resolution, colors
    )

    # Blue pixels
    pixels = b""
    for y in range(height):
        for x in range(width):
            pixels += struct.pack("BBBB", 231, 76, 60, 255)  # RGBA red

    # AND mask (transparency)
    and_mask = b"\xff" * (width * height // 8)

    return header + entry + bmp_header + pixels + and_mask
