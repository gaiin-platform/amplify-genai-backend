"""Render .pptx slides to PNG with headless LibreOffice + poppler.

LibreOffice output approximates PowerPoint; it is used for visual review, not
as the delivered artifact. Approach follows the preview tooling in
aws-samples/sample-strands-agent-with-agentcore (MIT).
"""

import io
import os
import shutil
import subprocess
import tempfile
from typing import List, Sequence

from pdf2image import convert_from_path
from PIL import Image, ImageDraw

RENDER_TIMEOUT_SECONDS = 180


def soffice_path() -> str:
    for candidate in ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        found = shutil.which(candidate) or (candidate if os.path.exists(candidate) else None)
        if found:
            return found
    raise RuntimeError("LibreOffice (soffice) is not installed")


def render_slides(pptx_bytes: bytes, dpi: int = 110, max_side: int = 1600) -> List[bytes]:
    """Return one PNG per slide, in order."""
    with tempfile.TemporaryDirectory() as workdir:
        source = os.path.join(workdir, "deck.pptx")
        with open(source, "wb") as f:
            f.write(pptx_bytes)
        # A private profile dir avoids lock contention between concurrent renders.
        profile = f"-env:UserInstallation=file://{workdir}/lo-profile"
        subprocess.run(
            [soffice_path(), profile, "--headless", "--convert-to", "pdf", "--outdir", workdir, source],
            check=True,
            capture_output=True,
            timeout=RENDER_TIMEOUT_SECONDS,
        )
        pdf = os.path.join(workdir, "deck.pdf")
        pages = convert_from_path(pdf, dpi=dpi)
        images = []
        for page in pages:
            page.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            page.save(buf, format="PNG", optimize=True)
            images.append(buf.getvalue())
        return images


def montage(slides: Sequence[bytes], columns: int = 4, tile_width: int = 480) -> bytes:
    """Contact sheet with slide numbers, for a deck-level visual pass."""
    tiles = [Image.open(io.BytesIO(s)).convert("RGB") for s in slides]
    if not tiles:
        raise ValueError("no slides to montage")
    ratio = tiles[0].height / tiles[0].width
    tile_height = int(tile_width * ratio)
    pad = 16
    columns = max(1, min(columns, len(tiles)))
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * (tile_width + pad) + pad, rows * (tile_height + pad + 22) + pad), "white")
    draw = ImageDraw.Draw(sheet)
    for i, tile in enumerate(tiles):
        tile = tile.resize((tile_width, tile_height))
        x = pad + (i % columns) * (tile_width + pad)
        y = pad + (i // columns) * (tile_height + pad + 22)
        draw.text((x, y), f"Slide {i + 1}", fill="black")
        sheet.paste(tile, (x, y + 20))
        draw.rectangle([x - 1, y + 19, x + tile_width, y + 20 + tile_height], outline="#999999")
    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def thumbnail(png: bytes, width: int = 480) -> bytes:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img.thumbnail((width, width))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
