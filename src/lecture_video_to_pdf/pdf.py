from __future__ import annotations

from pathlib import Path

import img2pdf
from PIL import Image


def images_to_pdf(image_paths: list[str | Path], output_pdf: str | Path) -> Path:
    output = Path(output_pdf)
    paths = [Path(p) for p in image_paths if Path(p).exists()]
    if not paths:
        raise RuntimeError("No slide images were produced, PDF cannot be created.")

    try:
        with output.open("wb") as fh:
            fh.write(img2pdf.convert([str(p) for p in paths]))
    except Exception:
        # Fallback for environments where img2pdf cannot parse an image file.
        pil_images = []
        for path in paths:
            img = Image.open(path).convert("RGB")
            pil_images.append(img)
        first, rest = pil_images[0], pil_images[1:]
        first.save(output, save_all=True, append_images=rest)
    return output

