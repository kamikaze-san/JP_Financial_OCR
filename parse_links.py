import os
import re

path = r'C:\Users\NewGr\.gemini\antigravity-ide\brain\04421cb5-cc77-44ac-9153-e2c24b695c06\.system_generated\steps\405\content.md'
if os.path.exists(path):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    links = re.findall(r'href=[\'"]([^\'"]+\.pdf)[\'"]', content, re.IGNORECASE)
    print("Found links:", len(links))
    for l in links[:15]:
        print("  ", l)
else:
    print("File not found")
