"""The drawing as images: render the PDF page, an overview, zoomed regions, view crops.

Claude's vision scales an image to about 1,500 px on its long side, and Claude
Desktop limits a tool result to about 1 MB, so a whole sheet is too coarse to
read small dimension text. Claude reads every value from zoomed regions of the
full-resolution render instead (Docs/KNOWLEDGE-BASE.md §4.3). Boxes are page
fractions [x0, y0, x1, y1], 0 to 1 from the top left, so they mean the same on
any scaled copy of the page.
"""

import io

from ..errors import SolidWorksError

RENDER_DPI = 200
MAX_RENDER_PIXELS = 60_000_000  # a 36 x 48 in sheet at 200 dpi is 69 MP: render such a sheet coarser
SHOWN_MAX_SIDE = 1568           # what Claude's vision keeps of an image's long side
SHOWN_MAX_BYTES = 700_000       # JPEG bytes; base64 makes it ~0.93 MB, inside Claude Desktop's limit
EMBED_MAX_PIXELS = 2_000_000    # each image sent to Voyage is scaled to this, so it costs a known amount
VIEW_MARGIN = 0.10              # added round a view box on each side, as a fraction of its size
MIN_BOX = 0.01


def require_extra():
    try:
        import PIL.Image  # noqa: F401
        import pypdfium2  # noqa: F401
    except ImportError:
        raise SolidWorksError("The knowledge base's drawing tools need the optional extra: "
                              "pip install \"solidworks-mcp[kb]\"", code="KB_EXTRA_MISSING") from None


def render_page(pdf_path, page: int):
    """(image, dpi) of one page, 1-based. A page that is not there is refused, never swapped."""
    require_extra()
    import pypdfium2

    try:
        pdf = pypdfium2.PdfDocument(str(pdf_path))
    except pypdfium2.PdfiumError as exc:
        raise SolidWorksError(f"Could not read {pdf_path} as a PDF: {exc}", code="KB_BAD_PDF") from None
    try:
        count = len(pdf)
        if not 1 <= page <= count:
            raise SolidWorksError(f"The PDF has {count} page(s); there is no page {page}.", code="KB_NO_SUCH_PAGE",
                                  params={"pages": count, "page": page})
        sheet = pdf[page - 1]
        width_in, height_in = sheet.get_width() / 72, sheet.get_height() / 72
        dpi = min(RENDER_DPI, int((MAX_RENDER_PIXELS / (width_in * height_in)) ** 0.5))
        image = sheet.render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        pdf.close()
    return image, dpi


def parse_box(box, label: str = "box") -> tuple:
    """A page-fraction box [x0, y0, x1, y1] checked: numbers in 0..1, x0 < x1, y0 < y1."""
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        raise SolidWorksError(f"{label} is [x0, y0, x1, y1] as fractions of the page, 0 to 1 from the top left "
                              f"(got {box!r}).", code="KB_BAD_BOX") from None
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise SolidWorksError(f"{label} {box!r} must lie within 0..1 with x0 < x1 and y0 < y1.", code="KB_BAD_BOX")
    if x1 - x0 < MIN_BOX or y1 - y0 < MIN_BOX:
        raise SolidWorksError(f"{label} {box!r} is smaller than {MIN_BOX:g} of the page each way.", code="KB_BAD_BOX")
    return x0, y0, x1, y1


def _pixels(image, box) -> tuple:
    width, height = image.size
    x0, y0, x1, y1 = box
    return round(x0 * width), round(y0 * height), round(x1 * width), round(y1 * height)


def with_margin(box, margin: float = VIEW_MARGIN) -> tuple:
    """A view box grown by `margin` of its size on each side, kept on the page:
    Claude's boxes are rough, and a crop that cuts the view off is worse than one with some border."""
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * margin, (y1 - y0) * margin
    return max(0.0, x0 - dx), max(0.0, y0 - dy), min(1.0, x1 + dx), min(1.0, y1 + dy)


def crop(image, box):
    return image.crop(_pixels(image, box))


def scaled_to_pixels(image, max_pixels: int):
    width, height = image.size
    if width * height <= max_pixels:
        return image
    factor = (max_pixels / (width * height)) ** 0.5
    return image.resize((max(1, int(width * factor)), max(1, int(height * factor))))


def shown(image, max_side: int = SHOWN_MAX_SIDE, max_bytes: int = SHOWN_MAX_BYTES) -> tuple:
    """(JPEG bytes, scale) of `image` as Claude is shown it: long side at most
    max_side, then lower quality, then smaller, until it fits max_bytes."""
    from PIL import Image

    scale = min(1.0, max_side / max(image.size))
    while True:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        resized = image if size == image.size else image.resize(size, Image.LANCZOS)
        for quality in (88, 78, 68):
            buffer = io.BytesIO()
            resized.save(buffer, format="JPEG", quality=quality)
            if buffer.tell() <= max_bytes:
                return buffer.getvalue(), scale
        scale *= 0.8


def png_bytes(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
