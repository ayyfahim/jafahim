"""
High-quality thumbnail generator using Pillow.

Renders at 2x resolution, then downsamples to 1280x720 for smoother text and
cleaner rounded overlays.
"""

from PIL import Image, ImageDraw, ImageFilter, ImageFont
import os
import re


THUMB_W, THUMB_H = 1280, 720
RENDER_SCALE = 2

GOLD = (255, 214, 54, 255)
WHITE = (255, 255, 255, 255)
INK = (8, 10, 14, 255)

SAFE_X = 56
BLOCK_PAD_X = 32
BLOCK_PAD_Y = 24
LINE_GAP_RATIO = 0.16
MAX_LINES = 4


# ── Font catalogue (auto-discovered at import time) ───────────────────────────

_FONTS_DIR = "C:/Windows/Fonts"
_SKIP_STEMS = {
    # decorative / symbol / non-latin fonts unlikely to suit thumbnails
    "webdings", "wingding", "symbol", "marlett", "segmdl2",
    "seguiemj", "seguihis", "seguisym", "mtextra",
}


def _stem_to_label(stem):
    """Turn a filename stem into a readable label, e.g. 'RobotoBold' → 'Roboto Bold'."""
    # Insert a space before every uppercase letter that follows a lowercase letter or digit
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem)
    # Also split on hyphens/underscores
    spaced = re.sub(r"[-_]+", " ", spaced)
    return spaced.strip()


def _build_font_catalogue():
    """Scan the Windows Fonts directory and return {label: path} for every .ttf found."""
    catalogue = {}
    if not os.path.isdir(_FONTS_DIR):
        return catalogue

    for fname in sorted(os.listdir(_FONTS_DIR)):
        if not fname.lower().endswith(".ttf"):
            continue
        stem = os.path.splitext(fname)[0]
        if stem.lower() in _SKIP_STEMS:
            continue
        label = _stem_to_label(stem)
        path  = os.path.join(_FONTS_DIR, fname).replace("\\", "/")
        # Keep the first match so Impact (impact.ttf) wins over IMPACT.TTF etc.
        if label not in catalogue:
            catalogue[label] = path

    return catalogue


# Built once at startup — {friendly_label: absolute_path}
FONT_CATALOGUE: dict[str, str] = _build_font_catalogue()

# Normalize the Impact entry to always appear as "Impact" regardless of filename case.
_impact_key = next((k for k in FONT_CATALOGUE if k.lower() == "impact"), None)
if _impact_key and _impact_key != "Impact":
    FONT_CATALOGUE["Impact"] = FONT_CATALOGUE.pop(_impact_key)
    _impact_key = "Impact"

# "Impact" first (our default), then everything else sorted alphabetically.
FONT_NAMES_ORDERED: list[str] = (
    (["Impact"] if "Impact" in FONT_CATALOGUE else [])
    + sorted(k for k in FONT_CATALOGUE if k != "Impact")
)
DEFAULT_FONT = "Impact" if "Impact" in FONT_CATALOGUE else (FONT_NAMES_ORDERED[0] if FONT_NAMES_ORDERED else "")


def get_available_fonts():
    """Return ordered list of font display names available on this system."""
    return list(FONT_NAMES_ORDERED)


# ─────────────────────────────────────────────────────────────────────────────

def _s(value):
    return int(round(value * RENDER_SCALE))


def _norm_word(word):
    return re.sub(r"[^\w]", "", word.lower())


def _load_font(font_name, size):
    """Load the requested font by name from the catalogue, falling back to Impact."""
    path = FONT_CATALOGUE.get(font_name) or FONT_CATALOGUE.get(DEFAULT_FONT)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            pass
    return ImageFont.load_default()


def _cover_crop(img, target_w, target_h):
    """Resize and center-crop image to exactly fill target dimensions."""
    img_ratio = img.width / img.height
    target_ratio = target_w / target_h

    if img_ratio > target_ratio:
        new_h = target_h
        new_w = int(img.width * (target_h / img.height))
    else:
        new_w = target_w
        new_h = int(img.height * (target_w / img.width))

    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def _text_width(draw, text, font, stroke_width=0):
    if not text:
        return 0
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return bbox[2] - bbox[0]


def _wrap_lines(draw, text, font, max_width, stroke_width):
    lines = []
    for paragraph in text.split("\n"):
        words = [w for w in paragraph.split() if w]
        if not words:
            continue

        current = []
        for word in words:
            candidate = " ".join(current + [word])
            if _text_width(draw, candidate, font, stroke_width) <= max_width or not current:
                current.append(word)
            else:
                lines.append(current)
                current = [word]

        if current:
            lines.append(current)
    return lines


def _layout_text(draw, text, requested_font_size, max_width, font_name="Impact"):
    """Return font, wrapped lines, and line height; shrink if text is too tall."""
    font_size = int(requested_font_size)
    min_size = 42

    while font_size >= min_size:
        font = _load_font(font_name, _s(font_size))
        stroke_width = max(2, _s(font_size * 0.055))
        line_gap = _s(max(8, font_size * LINE_GAP_RATIO))
        bbox = draw.textbbox((0, 0), "THE QUICK", font=font, stroke_width=stroke_width)
        line_height = (bbox[3] - bbox[1]) + line_gap
        lines = _wrap_lines(draw, text, font, max_width, stroke_width)

        if len(lines) <= MAX_LINES:
            return font, lines, line_height, stroke_width
        font_size -= 4

    font = _load_font(font_name, _s(min_size))
    stroke_width = max(2, _s(min_size * 0.055))
    line_height = _s(min_size + 10)
    return font, _wrap_lines(draw, text, font, max_width, stroke_width)[:MAX_LINES], line_height, stroke_width


def _hex_to_rgb(hex_color):
    """Convert '#rrggbb' or 'rrggbb' to (r, g, b)."""
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    return (0, 0, 0)


def _draw_rounded_box(draw, layer, rect, radius, box_color_rgb, box_opacity):
    """Draw the dark background panel with optional shadow."""
    x1, y1, x2, y2 = rect
    r, g, b = box_color_rgb
    alpha = max(0, min(255, int(box_opacity * 255)))

    # Drop shadow
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        [x1, y1 + _s(8), x2, y2 + _s(8)],
        radius=radius,
        fill=(0, 0, 0, 145),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(_s(10)))
    layer.alpha_composite(shadow)

    # Panel fill
    draw.rounded_rectangle(rect, radius=radius, fill=(r, g, b, alpha))


def _draw_word(base, draw, pos, word, font, fill, stroke_width, shadow_mode):
    """
    Draw a word with configurable text effect.

    shadow_mode:
      'shadow'  — soft drop shadow only
      'stroke'  — crisp outline stroke only
      'both'    — shadow + stroke (original default)
      'none'    — plain text, no effect
    """
    x, y = pos

    if shadow_mode in ("shadow", "both"):
        shadow_layer = Image.new("RGBA", (_s(THUMB_W), _s(THUMB_H)), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_layer)
        shadow_draw.text(
            (x + _s(4), y + _s(5)),
            word,
            font=font,
            fill=(0, 0, 0, 210),
            stroke_width=stroke_width if shadow_mode == "both" else 0,
            stroke_fill=(0, 0, 0, 210),
        )
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(_s(1.2)))
        base.alpha_composite(shadow_layer)

    sw = stroke_width if shadow_mode in ("stroke", "both") else 0
    draw.text(
        (x, y),
        word,
        font=font,
        fill=fill,
        stroke_width=sw,
        stroke_fill=INK,
    )


def generate_thumbnail(
    background_path,
    text,
    highlighted_words,
    output_path,
    font_size=72,
    pos_x=50,
    pos_y=72,
    font_name="Impact",
    text_color="#ffffff",
    highlight_color="#ffd636",
    box_color="#000000",
    box_opacity=0.70,
    box_width=52,
    fit_box=True,
    line_scales=None,
    shadow_mode="both",
    text_align="center",
):
    """
    Generate a polished YouTube thumbnail PNG.

    Args:
        background_path: Path to the background image.
        text: Thumbnail text.
        highlighted_words: List of words to render in gold.
        output_path: Where to save the PNG.
        font_size: Base font size (px, at 720p equivalent).
        pos_x: Horizontal position 0–100 (%).
        pos_y: Vertical position 0–100 (%).
        font_name: Font name from FONT_CATALOGUE (default "Impact").
        box_color: Background box color as '#rrggbb' hex string.
        box_opacity: Background box opacity 0.0–1.0.
        shadow_mode: 'shadow' | 'stroke' | 'both' | 'none'.
        text_align: 'left' | 'center' | 'right'.
    """
    render_w, render_h = _s(THUMB_W), _s(THUMB_H)
    bg = Image.open(background_path).convert("RGBA")
    bg = _cover_crop(bg, render_w, render_h)

    text = (text or "").strip()
    if not text:
        bg.resize((THUMB_W, THUMB_H), Image.LANCZOS).convert("RGB").save(output_path, "PNG")
        return output_path

    highlighted_set = {_norm_word(w) for w in highlighted_words if _norm_word(w)}
    text_fill = (*_hex_to_rgb(text_color), 255)
    highlight_fill = (*_hex_to_rgb(highlight_color), 255)
    box_color_rgb = _hex_to_rgb(box_color)

    draw = ImageDraw.Draw(bg)
    try:
        box_width = int(box_width)
    except (TypeError, ValueError):
        box_width = 52
    box_width = max(25, min(90, box_width))
    block_w = int(render_w * (box_width / 100.0))
    inner_w = max(_s(80), block_w - _s(BLOCK_PAD_X * 2))
    if not isinstance(line_scales, list):
        line_scales = []

    rows = [row.strip() for row in text.split("\n") if row.strip()]
    if not rows:
        rows = [text]

    line_layouts = []
    for idx, row in enumerate(rows[:8]):
        words = [w for w in row.split() if w] or [row]
        try:
            scale = float(line_scales[idx]) if idx < len(line_scales) else 1.0
        except (TypeError, ValueError):
            scale = 1.0
        size = max(26, int(round(font_size * scale)))

        while True:
            font = _load_font(font_name, _s(size))
            stroke_width = max(2, _s(size * 0.055))
            space_w = _text_width(draw, " ", font, stroke_width)
            word_widths = [_text_width(draw, word, font, stroke_width) for word in words]
            line_w = sum(word_widths) + max(0, len(words) - 1) * space_w
            if not fit_box or line_w <= inner_w or size <= 22:
                break
            size -= 2

        bbox = draw.textbbox((0, 0), "THE QUICK", font=font, stroke_width=stroke_width)
        line_height = (bbox[3] - bbox[1]) + _s(max(6, size * 0.08))
        line_layouts.append({
            "words": words,
            "font": font,
            "stroke_width": stroke_width,
            "space_w": space_w,
            "line_w": line_w,
            "line_height": line_height,
        })

    block_h = sum(line["line_height"] for line in line_layouts) + _s(BLOCK_PAD_Y * 2)

    max_x = render_w - block_w - _s(24)
    max_y = render_h - block_h - _s(24)
    block_x = max(_s(24), min(max_x, int((pos_x / 100.0) * (render_w - block_w))))
    block_y = max(_s(24), min(max_y, int((pos_y / 100.0) * (render_h - block_h))))

    panel = Image.new("RGBA", (render_w, render_h), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    _draw_rounded_box(
        panel_draw,
        panel,
        [block_x, block_y, block_x + block_w, block_y + block_h],
        _s(18),
        box_color_rgb,
        box_opacity,
    )
    bg = Image.alpha_composite(bg, panel)
    draw = ImageDraw.Draw(bg)

    y = block_y + _s(BLOCK_PAD_Y)
    for line in line_layouts:
        words = line["words"]
        line_w = line["line_w"]
        font = line["font"]
        stroke_width = line["stroke_width"]
        space_w = line["space_w"]
        # Alignment
        if text_align == "left":
            x = block_x + _s(BLOCK_PAD_X)
        elif text_align == "right":
            x = block_x + block_w - line_w - _s(BLOCK_PAD_X)
        else:  # center (default)
            x = block_x + (block_w - line_w) // 2

        for idx, word in enumerate(words):
            clean = _norm_word(word)
            fill = highlight_fill if clean in highlighted_set else text_fill
            _draw_word(bg, draw, (x, y), word, font, fill, stroke_width, shadow_mode)
            x += _text_width(draw, word, font, stroke_width)
            if idx < len(words) - 1:
                x += space_w
        y += line["line_height"]

    final = bg.resize((THUMB_W, THUMB_H), Image.LANCZOS).convert("RGB")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    final.save(output_path, "PNG", optimize=True)
    return output_path
