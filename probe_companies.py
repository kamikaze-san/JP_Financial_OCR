import urllib.request, re, sys, ssl
sys.stdout.reconfigure(encoding='utf-8')

ctx = ssl._create_unverified_context()

candidates = {
    'capcom': 'https://www.capcom.co.jp/ir/financial/financial.html',
    'toyota': 'https://global.toyota/jp/ir/library/securities/',
    'sony': 'https://www.sony.com/ja/SonyInfo/IR/library/reports.html',
    'nintendo': 'https://www.nintendo.co.jp/ir/library/earnings/index.html',
    'fastretailing': 'https://www.fastretailing.com/jp/ir/library/yuho.html',
    'kewpie': 'https://www.kewpie.com/ir/library/financial/',
    'calbee': 'https://www.calbee.co.jp/ir/library/financial/'
}

for name, url in candidates.items():
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            content = resp.read().decode('utf-8', errors='ignore')
        pdfs = re.findall(r'href=[\'"]([^\'"]+\.pdf)[\'"]', content, re.I)
        print(f"Company: {name} -> Found {len(pdfs)} PDF links")
        for p in pdfs[:3]:
            print(f"   {p}")
    except Exception as e:
        print(f"Company {name} error: {e}")
