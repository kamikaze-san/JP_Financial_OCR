import os
import fitz
import json

os.makedirs('dataset/benchmark_suite', exist_ok=True)

companies = [
    {
        'id': 'company_01_yamato',
        'name': 'Yamato Mobility & Mfg. (7886)',
        'source_pdf': 'dataset/raw_pdfs/yamato_fs66.pdf',
        'bs_pages': [0], # 0-indexed: Page 1
        'accounting_standard': 'J-GAAP',
        'unit': '千円',
        'layout': 'single_page_t_account'
    },
    {
        'id': 'company_02_calbee',
        'name': 'Calbee, Inc. (2229)',
        'source_pdf': 'dataset/raw_pdfs/calbee_financial_2026.pdf',
        'bs_pages': [6, 7], # 0-indexed: Pages 7 and 8
        'accounting_standard': 'J-GAAP',
        'unit': '百万円',
        'layout': 'two_page_comparative'
    },
    {
        'id': 'company_03_fastretailing',
        'name': 'Fast Retailing Co., Ltd. (9983)',
        'source_pdf': 'dataset/raw_pdfs/fastretailing_yuho_2025.pdf',
        'bs_pages': [155, 156], # 0-indexed: Pages 156 and 157
        'accounting_standard': 'J-GAAP (Individual)',
        'unit': '百万円',
        'layout': 'two_page_comparative'
    },
    {
        'id': 'company_04_nikon',
        'name': 'Nikon Corporation (7731)',
        'source_pdf': 'dataset/raw_pdfs/nikon_tanshin.pdf',
        'bs_pages': [4, 5], # 0-indexed: Pages 5 and 6
        'accounting_standard': 'IFRS (財政状態計算書)',
        'unit': '百万円',
        'layout': 'two_page_comparative'
    }
]

manifest = []

for comp in companies:
    comp_dir = os.path.join('dataset/benchmark_suite', comp['id'])
    os.makedirs(comp_dir, exist_ok=True)
    
    src_doc = fitz.open(comp['source_pdf'])
    new_doc = fitz.open()
    for pno in comp['bs_pages']:
        new_doc.insert_pdf(src_doc, from_page=pno, to_page=pno)
    
    out_pdf = os.path.join(comp_dir, 'balance_sheet.pdf')
    new_doc.save(out_pdf)
    
    # Also render 200 DPI PNGs for VLM input
    images = []
    zoom = 200.0 / 72.0
    mat = fitz.Matrix(zoom, zoom)
    for idx, page in enumerate(new_doc):
        pix = page.get_pixmap(matrix=mat)
        img_path = os.path.join(comp_dir, f'page_{idx+1}.png')
        pix.save(img_path)
        images.append(img_path)
        print(f"[{comp['id']}] Saved page {idx+1}: {pix.width}x{pix.height}px")
        
    comp_meta = {
        'id': comp['id'],
        'name': comp['name'],
        'accounting_standard': comp['accounting_standard'],
        'unit': comp['unit'],
        'layout': comp['layout'],
        'pdf_path': out_pdf,
        'image_paths': images,
        'num_pages': len(comp['bs_pages'])
    }
    manifest.append(comp_meta)

with open('dataset/benchmark_suite/manifest.json', 'w', encoding='utf-8') as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

print("\nBenchmark Suite prepared successfully! Saved to dataset/benchmark_suite/manifest.json")
