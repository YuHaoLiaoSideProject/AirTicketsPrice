#!/usr/bin/env python3
"""PWA 圖示產生（dev-only，一次性；Pillow 不需進 requirements.txt，commit PNG 後即可移除）

對照：docs/development/PWA.md §2.3（圖示）、§8 步驟 1（T1）
      docs/test-plans/PWA測試計畫.md F-18（圖示存在且尺寸正確）

設計：深藍底（#1a73e8 → #1557b0 漸層，對齊 styles.css --accent token）+
      白色上升趨勢折線（3 點，呼應「票價趨勢」）+ 幾何紙飛機（沿末段上升方向起飛）。
      maskable 版：背景滿版出血，主體縮放至中央 80% safe zone 內切圓內（MAN-12 / F-18）。

用法：
    python scripts/gen_icons.py            # 產生 web/icons/ 四個 PNG（192/512/512-maskable/180）
    python scripts/gen_icons.py --check    # 驗證已產生圖示尺寸（F-18 / CI 輔助，回傳非 0 = 失敗）
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ACCENT = (26, 115, 232)        # #1a73e8（web/styles.css --accent）
ACCENT_DARK = (21, 87, 176)    # #1557b0（web/styles.css --accent-hover，漸層底部）
WHITE = (255, 255, 255)

ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = ROOT / "web" / "icons"

# (檔名, 尺寸, maskable) — 對照 manifest icons + apple-touch-icon(180)
SPECS = [
    ("icon-192.png", 192, False),
    ("icon-512.png", 512, False),
    ("icon-512-maskable.png", 512, True),
    ("apple-touch-icon.png", 180, False),
]

# ── 主體幾何（正規化座標 0..1，以 canvas 中心為原點語意：座標即縮放前位置）──
TREND = [(0.25, 0.66), (0.50, 0.44), (0.72, 0.26)]   # 上升三點折線
PLANE_CENTROID = (0.78, 0.16)                        # 紙飛機質心位置
PLANE_ROT_DEG = -43                                  # 沿折線末段上升方向（約 -39°）起飛
# 紙飛機（未旋轉，指向右；nose 在右）
PLANE = {
    "nose": (0.78, 0.22),
    "upper_back": (0.60, 0.12),
    "lower_back": (0.60, 0.44),
    "mid_back": (0.60, 0.28),
}


def _rotate_point(p, center, deg):
    """繞 center 旋轉 deg 度（Pillow 同向：正 = 逆時針）。"""
    a = math.radians(deg)
    x, y = p[0] - center[0], p[1] - center[1]
    return (center[0] + x * math.cos(a) - y * math.sin(a),
            center[1] + x * math.sin(a) + y * math.cos(a))


def _gradient(size: int) -> Image.Image:
    """滿版垂直漸層：#1a73e8（上）→ #1557b0（下）；maskable 由邊緣出血（覆蓋全 canvas）。"""
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(size - 1, 1)
        color = tuple(round(ACCENT[i] + (ACCENT_DARK[i] - ACCENT[i]) * t) for i in range(3))
        d.line([(0, y), (size, y)], fill=color)
    return img


def _build_subject(size: int) -> Image.Image:
    """主體圖層（透明底）：趨勢折線 + 資料點 + 紙飛機（已旋轉），1 unit = size px。"""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    stroke = max(2, round(0.045 * size))
    dot_r = max(2, round(0.050 * size))

    # 上升趨勢折線（round join）+ 三資料點
    pts = [(x * size, y * size) for x, y in TREND]
    d.line(pts, fill=WHITE, width=stroke, joint="curve")
    for px, py in pts:
        d.ellipse([px - dot_r, py - dot_r, px + dot_r, py + dot_r], fill=WHITE)

    # 紙飛機（幾何：上下翼兩三角形，繞自身質心旋轉後置於質心位置）
    c = tuple((PLANE["nose"][i] + PLANE["upper_back"][i] + PLANE["lower_back"][i] + PLANE["mid_back"][i]) / 4
              for i in range(2))
    rp = {k: _rotate_point(v, c, PLANE_ROT_DEG) for k, v in PLANE.items()}
    shift = (PLANE_CENTROID[0] - c[0], PLANE_CENTROID[1] - c[1])
    plane = {k: (v[0] + shift[0], v[1] + shift[1]) for k, v in rp.items()}
    s = lambda p: (p[0] * size, p[1] * size)  # noqa: E731
    d.polygon([s(plane["nose"]), s(plane["upper_back"]), s(plane["mid_back"])], fill=WHITE)
    d.polygon([s(plane["nose"]), s(plane["mid_back"]), s(plane["lower_back"])], fill=WHITE)
    return layer


def make_icon(size: int, maskable: bool) -> Image.Image:
    """滿版漸層背景 + 主體；maskable=True → 主體縮放至中央 80% safe zone 內切圓內（出血滿版）。"""
    img = _gradient(size)
    layer = _build_subject(size)
    bbox = layer.getbbox()
    if bbox is None:
        return img
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if maskable:
        # 主體外接圓半徑 ≤ 0.4·size（80% safe zone 內切圓），任何裁切皆不損主體（MAN-12）
        k = 0.80 * size / math.hypot(w, h)
    else:
        k = min(0.86 * size / w, 0.86 * size / h)   # 留邊 ~7%
    if k > 1:
        k = 1.0
    sub = layer.crop(bbox).resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    img.paste(sub, (round((size - sub.width) / 2), round((size - sub.height) / 2)), sub)
    return img


def check() -> int:
    """--check：驗證四圖示存在且尺寸正確（F-18 / CI）；任一失敗回傳非 0。"""
    ok = True
    for name, size, _ in SPECS:
        p = ICONS_DIR / name
        if not p.exists():
            print(f"MISSING  {name}（{size}x{size} 應存在）")
            ok = False
            continue
        with Image.open(p) as im:
            w, h = im.size
        good = (w, h) == (size, size)
        ok = ok and good
        print(f"{'OK      ' if good else 'MISMATCH'} {name}: {w}x{h}（expect {size}x{size}）")
    if ok:
        print("全部圖示尺寸正確")
    return 0 if ok else 1


def main() -> int:
    if "--check" in sys.argv:
        return check()
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    for name, size, maskable in SPECS:
        make_icon(size, maskable).save(ICONS_DIR / name, "PNG")
        print(f"wrote {ICONS_DIR / name}（{size}x{size}{'，maskable' if maskable else ''}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
