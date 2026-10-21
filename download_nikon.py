import urllib.request, ssl, sys, os, fitz
sys.stdout.reconfigure(encoding='utf-8')
ctx = ssl._create_unverified_context()

os.makedirs('dataset/raw_pdfs', exist_ok=True)
url = 'https://www.jp.nikon.com/content/dam/web-assets/nikoncom/company/local/jp/ja/ir/ir_library/result/pdf/2027/27first_all.pdf'
dest = 'dataset/raw_pdfs/nikon_27first.pdf'

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
try:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=20) as resp, open(dest, 'wb') as f:
        f.write(resp.read())
    doc = fitz.open(dest)
    print(f"Downloaded Nikon: {os.path.getsize(dest)} bytes, Pages: {len(doc)}")
    for i in range(len(doc)):
        txt = doc[i].get_text()
        if '財政状態計算書' in txt or '貸借対照表' in txt:
            print(f"   -> Page {i+1} has Balance Sheet/SFP! First line: {txt.splitlines()[:2]}")
except Exception as e:
    print("Nikon download failed:", e)
