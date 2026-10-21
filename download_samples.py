import os
import urllib.request

os.makedirs('dataset/raw_pdfs', exist_ok=True)

# URLs from search results
urls = {
    'calbee_2024': 'https://www.calbee.co.jp/ir/pdf/2024/20240510_financial.pdf',
    'bandainamco_2024': 'https://www.bandainamco.co.jp/files/ir/financial/pdf/20240509_1.pdf',
    'suzuki_2023': 'https://www.suzuki.co.jp/ir/library/financialresults/pdf/2023_all.pdf',
}

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

for name, url in urls.items():
    dest = f'dataset/raw_pdfs/{name}.pdf'
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp, open(dest, 'wb') as f:
            f.write(resp.read())
        print(f"Downloaded {name}: {os.path.getsize(dest)} bytes")
    except Exception as e:
        print(f"Failed {name} from {url}: {e}")
