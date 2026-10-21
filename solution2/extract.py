"""
Stage 1 for the solution2 pipeline: a vision-language model reads a page image into Stage-1 JSON.

The prompt is solution1's own STAGE1_SYSTEM_PROMPT, imported unchanged. An extractor is just a callable
`image_path -> model text (or None on failure)`, so tests and other backends can be swapped in.
"""
from pathlib import Path
from typing import Callable, Optional

Extractor = Callable[[str], Optional[str]]


def render_page(pdf_path, page_index: int, out_dir, dpi: int = 200) -> Path:
    """Render one PDF page to PNG (same PyMuPDF approach as solution1.pdf_renderer, but one page at a time)."""
    import fitz
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as doc:
        out = out_dir / f"{Path(pdf_path).stem}_page_{page_index + 1}_{dpi}dpi.png"
        doc[page_index].get_pixmap(dpi=dpi, alpha=False).save(out)
    return out


EXTRA_COLUMNS_NOTE = """
COLUMNS: list EVERY numeric column of the table in "periods", in left-to-right order. A column such as
増減 (change) that has no date still gets an entry: use its heading as "raw_label" and "" as "raw_date".
A row that has numbers has exactly one string in "values" per entry in "periods". A heading row without
numbers has "values": [] (never repeat its label there).
"""


def stage1_prompt() -> str:
    """solution1's Stage-1 prompt, unchanged, plus a note so that undated columns (増減) are not dropped."""
    from solution1.vlm_interface import STAGE1_SYSTEM_PROMPT
    return STAGE1_SYSTEM_PROMPT + EXTRA_COLUMNS_NOTE


def ollama_extractor(model: str = "qwen3-vl:4b-instruct", num_ctx: int = 16384, num_predict: int = 8192) -> Extractor:
    prompt = stage1_prompt()

    def extract(image_path: str) -> Optional[str]:
        import ollama
        try:
            resp = ollama.chat(
                model=model, format="json",
                messages=[{"role": "user", "content": prompt, "images": [str(image_path)]}],
                options={"num_ctx": num_ctx, "num_predict": num_predict, "temperature": 0.0})
        except Exception:
            return None
        return resp["message"]["content"]

    return extract
