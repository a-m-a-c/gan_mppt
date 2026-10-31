from pathlib import Path
import hashlib
import json
import math
import zipfile

folder=Path(__file__).resolve().parent
restore='''from pathlib import Path
import hashlib
import json

folder=Path(__file__).resolve().parent
for manifest_path in sorted(folder.glob('*_parts.json')):
    manifest=json.loads(manifest_path.read_text())
    parts=[folder/name for name in manifest['parts']]
    missing=[p.name for p in parts if not p.exists()]
    if missing:
        raise SystemExit('Missing parts: '+', '.join(missing))
    output=folder/manifest['archive']
    with output.open('xb') as out:
        for part in parts:
            with part.open('rb') as source:
                while data:=source.read(1024*1024):
                    out.write(data)
    with output.open('rb') as source:
        digest=hashlib.file_digest(source,'sha256').hexdigest()
    if digest!=manifest['sha256']:
        raise SystemExit('Checksum mismatch: '+str(output))
    print('Restored and verified:',output)
'''
inventory=[]
for name in ('measure_5','measure_23_new'):
    archive=folder/f'{name}.zip'
    partdir=folder/f'{name}_notion_parts'
    partdir.mkdir(exist_ok=True)
    count=math.ceil(archive.stat().st_size/4_500_000)
    pieces=[f'{name}.zip.{i:03}' for i in range(1,count+1)]
    with archive.open('rb') as stream:
        digest=hashlib.file_digest(stream,'sha256').hexdigest()
    manifest={'archive':archive.name,'sha256':digest,'parts':pieces}
    instructions=f'''Complete archive: {archive.name}
Download ALL {count} numbered part ZIPs from Notion. Each is an ordinary ZIP containing one binary fragment and these instructions.
Extract every part ZIP into ONE shared folder (allow duplicate README/restore/manifest files to merge).
Run: python RESTORE.py (Python 3.11+, standard library only).
This recreates and SHA-256 verifies {archive.name}; then unzip that full archive normally.
Do not open individual .zip.001 fragments as ZIP archives.
The full ZIP is also available locally in captures/exports/{archive.name} on the capture PC.
'''
    with archive.open('rb') as stream:
        for i,piece in enumerate(pieces,1):
            dest=partdir/f'{name}.part{i:03}-of-{count:03}.zip'
            with zipfile.ZipFile(dest,'x',compression=zipfile.ZIP_STORED) as z:
                z.writestr(piece,stream.read(4_500_000))
                z.writestr('RESTORE.py',restore)
                z.writestr(f'{name}_parts.json',json.dumps(manifest,indent=2))
                z.writestr(f'{name}_README.txt',instructions)
            assert dest.stat().st_size < 5_000_000
            inventory.append({'archive':name,'path':str(dest.resolve()),'filename':dest.name})
    # Verify reconstruction directly from the packaged fragments, without another extracted copy.
    check=hashlib.sha256()
    for i,piece in enumerate(pieces,1):
        with zipfile.ZipFile(partdir/f'{name}.part{i:03}-of-{count:03}.zip') as z:
            check.update(z.read(piece))
    assert check.hexdigest()==digest
    print(name,count,'parts; reconstruction hash verified')
(folder/'notion_upload_inventory.json').write_text(json.dumps(inventory,indent=2)+'\n',encoding='utf-8')
