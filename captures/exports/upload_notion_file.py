import base64
import json
from pathlib import Path
import sys
import urllib.request
import uuid

path = Path(sys.argv[1])
spec = json.loads(base64.b64decode(sys.argv[2]))
boundary = 'notion-' + uuid.uuid4().hex
body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        'Content-Type: application/zip\r\n\r\n').encode() + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
headers = dict(spec['upload_headers'])
headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
request = urllib.request.Request(spec['upload_url'], data=body, headers=headers, method='POST')
with urllib.request.urlopen(request, timeout=90) as response:
    result = json.load(response)
out = path.with_suffix('.upload.json')
out.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result))
