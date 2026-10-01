"""生成 tabBar 图标（纯 Python 写 PNG，无第三方依赖）
莫兰迪配色：未选中 #B4BAB5，选中 #8FA68E
"""
import zlib
import struct
import math
import os

SIZE = 81
SS = 4  # 超采样倍数，用于抗锯齿

GRAY = (0xB4, 0xBA, 0xB5)
GREEN = (0x8F, 0xA6, 0x8E)


def write_png(path, width, height, rgba_rows):
    raw = b"".join(b"\x00" + bytes(row) for row in rgba_rows)

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as f:
        f.write(png)


def sdf_round_rect(px, py, cx, cy, hw, hh, r):
    dx = abs(px - cx) - (hw - r)
    dy = abs(py - cy) - (hh - r)
    ax, ay = max(dx, 0.0), max(dy, 0.0)
    return math.hypot(ax, ay) + min(max(dx, dy), 0.0) - r


def sdf_circle(px, py, cx, cy, r):
    return math.hypot(px - cx, py - cy) - r


def inside_calendar(x, y):
    # 主体
    if sdf_round_rect(x, y, 0.5, 0.56, 0.34, 0.30, 0.10) >= 0:
        return False
    # 表头分隔线（挖空）
    if abs(y - 0.425) < 0.032 and 0.16 < x < 0.84:
        return False
    # 上方两个挂环
    if sdf_round_rect(x, y, 0.325, 0.165, 0.048, 0.10, 0.048) < 0:
        return True
    if sdf_round_rect(x, y, 0.675, 0.165, 0.048, 0.10, 0.048) < 0:
        return True
    return True


def inside_person(x, y):
    # 头部
    if sdf_circle(x, y, 0.5, 0.295, 0.185) < 0:
        return True
    # 肩部（圆角矩形，底部裁平）
    if y < 0.955 and sdf_round_rect(x, y, 0.5, 0.80, 0.30, 0.19, 0.185) < 0:
        return True
    return False


def render(shape_fn, color):
    rows = []
    inv = 1.0 / (SS * SS)
    for py in range(SIZE):
        row = []
        for px in range(SIZE):
            hits = 0
            for sy in range(SS):
                for sx in range(SS):
                    x = (px + (sx + 0.5) / SS) / SIZE
                    y = (py + (sy + 0.5) / SS) / SIZE
                    if shape_fn(x, y):
                        hits += 1
            alpha = int(round(hits * inv * 255))
            row.extend((color[0], color[1], color[2], alpha))
        rows.append(row)
    return rows


def main():
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "images")
    os.makedirs(out_dir, exist_ok=True)
    targets = [
        ("tab-course.png", inside_calendar, GRAY),
        ("tab-course-on.png", inside_calendar, GREEN),
        ("tab-mine.png", inside_person, GRAY),
        ("tab-mine-on.png", inside_person, GREEN),
    ]
    for name, fn, color in targets:
        rows = render(fn, color)
        write_png(os.path.join(out_dir, name), SIZE, SIZE, rows)
        print("generated", name)


if __name__ == "__main__":
    main()
