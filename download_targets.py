import urllib.request, ssl, sys, os, fitz
sys.stdout.reconfigure(encoding='utf-8')
ctx = ssl._create_unverified_context()

os.makedirs('dataset/raw_pdfs', exist_ok=True)

targets = {
    'calbee_financial_2026': 'https://www.calbee.co.jp/ir/pdf/2026/financial_20260730.pdf',
    'fastretailing_shihanki_2025': 'https://www.fastretailing.com/jp/ir/library/pdf/shihanki202504.pdf',
    'fastretailing_yuho_2025': 'https://www.fastretailing.com/jp/ir/library/pdf/yuho202508.pdf'
}

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

for name, url in targets.items():
    dest = f'dataset/raw_pdfs/{name}.pdf'
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp, open(dest, 'wb') as f:
            f.write(resp.read())
        size = os.path.getsize(dest)
        doc = fitz.open(dest)
        print(f"Downloaded {name}: {size} bytes, Pages: {len(doc)}")
        for i in range(len(doc)):
            txt = doc[i].get_text()
            if '貸借対照表' in txt:
                print(f"   -> Page {i+1} has 貸借対照表! First line: {txt.splitlines()[:2]}")
    except Exception as e:
        print(f"Failed {name}: {e}")
