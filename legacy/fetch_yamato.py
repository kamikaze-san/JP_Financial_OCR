import sys, re, urllib.request
sys.stdout.reconfigure(encoding='utf-8')
req = urllib.request.Request('https://www.yamato-in.co.jp/ir/kessan.html', headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode('cp932', errors='ignore')
    links = re.findall(r'href=[\'"]([^\'"]+\.pdf)[\'"]', html, re.I)
    print('Found PDF links:', len(links))
    for l in links[:15]:
        print(l)
except Exception as e:
    print('Error:', e)
