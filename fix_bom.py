import codecs

path = '/app/app/services/rag.py'
with codecs.open(path, 'r', 'utf-8') as f:
    content = f.read()

content = content.replace('\ufeff', '')

with codecs.open(path, 'w', 'utf-8') as f:
    f.write(content)
print("BOM removed")
