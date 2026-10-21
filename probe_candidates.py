import urllib.request, ssl, sys, re
sys.stdout.reconfigure(encoding='utf-8')
ctx = ssl._create_unverified_context()

candidates = {
    'nikon': 'https://www.jp.nikon.com/company/ir/ir_library/result/',
    'ajinomoto': 'https://www.ajinomoto.co.jp/company/jp/ir/library/financial.html',
    'kagome': 'https://www.kagome.co.jp/company/ir/data/tanshin/',
    'pigeon': 'https://www.pigeon.co.jp/ir/library/financial/',
    'monotaro': 'https://www.monotaro.com/main/ir/library/results/',
    'zozo': 'https://corp.zozo.com/ir/library/financial/'
}

for name, url in candidates.items():
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
            content = resp.read().decode('utf-8', errors='ignore')
        pdfs = re.findall(r'href=[\'"]([^\'"]+\.pdf)[\'"]', content, re.I)
        print(f"Company: {name} -> Found {len(pdfs)} PDF links")
        for p in pdfs[:3]:
            print(f"   {p}")
    except Exception as e:
        print(f"Company {name} error: {e}")
