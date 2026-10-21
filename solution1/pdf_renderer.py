import os
import fitz  # PyMuPDF
from PIL import Image

def render_pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 300) -> list[str]:
    """
    Renders every page of a PDF file to high-resolution PNG images.
    Returns the list of generated image file paths.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        
    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    image_paths = []
    
    # Calculate zoom factor from DPI (72 DPI is base 1.0)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        pix = page.get_pixmap(matrix=mat, alpha=False)
        out_image_path = os.path.join(output_dir, f"{base_name}_page_{page_idx + 1}.png")
        pix.save(out_image_path)
        image_paths.append(out_image_path)
        print(f"Rendered Page {page_idx + 1} -> {out_image_path} ({pix.width}x{pix.height}px @ {dpi} DPI)")
        
    doc.close()
    return image_paths

if __name__ == "__main__":
    test_pdf = os.path.join(os.path.dirname(__file__), "..", "balance_sheet_after_correction.pdf")
    out_dir = os.path.join(os.path.dirname(__file__), "rendered_images")
    paths = render_pdf_to_images(test_pdf, out_dir)
    print(f"Successfully rendered {len(paths)} pages.")
