"""Build a source-only course package from an explicit allowlist."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def package(destination=None):
    output=Path(destination) if destination else ROOT/'dist/综合实践.zip'
    files=[]
    for folder in ('check_collect','extras/noodle'):
        files.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in ('.py','.js','.css','.svg','.png','.ps1','.md','.cpp') or p.is_file() and p.name=='LEAFLET-LICENSE')
    files.extend(p for p in (ROOT/'tests').iterdir() if p.suffix in ('.py','.cjs') and p.name!='test_loaders.py')
    files.extend(ROOT/name for name in ('README.md','requirements-web.txt','.env.example','scripts/get_nats.py','scripts/create_demo.py'))
    for name in ('architecture.md','assignment-original.md','requirements.md','plan.md','demo.md','deployment.md','wechat-setup.md','map-ocr-acceptance.md','messaging-acceptance.md','collaboration-acceptance.md'):
        files.append(ROOT/'docs'/name)
    files.extend(p for p in (ROOT/'docs/diagrams').iterdir() if p.suffix=='.puml' or p.name.startswith(('system-components.','requirements.')))
    files.extend(ROOT/'reports'/name for name in ('综合实践报告_2024302131017_杨健淳.docx','综合实践报告.pdf','SADP周小结与课程总结-杨健淳-2024302131017.xlsx'))
    files=sorted(set(files));manifest={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for p in files:
            archive.write(p,'综合实践/'+p.relative_to(ROOT).as_posix())
        archive.writestr('综合实践/文件清单.json',json.dumps({'created_at':datetime.now().astimezone().isoformat(),'version':'1.0.0-rc.1','files':manifest},ensure_ascii=False,indent=2)+'\n')
    return output,manifest


if __name__=='__main__':
    output,manifest=package()
    print(json.dumps({'file':str(output),'files':len(manifest),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()},ensure_ascii=False))
