"""Fetch the pinned Windows NATS runtime; binaries stay outside Git."""
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.request
import zipfile

VERSION='2.15.2'
NAME=f'nats-server-v{VERSION}-windows-amd64'
ARCHIVE_SHA='f6398d5d11093778f870db70242cb50d20a58134c937c805ba8d114e7e398766'
BINARY_SHA='a7785bc14837679c6653a6c8090c79e4c4f7b28a5f7f144f3a734248a302ac41'


def install(root):
    root.mkdir(parents=True,exist_ok=True)
    binary=root/NAME/'nats-server.exe'
    if binary.exists():
        if hashlib.sha256(binary.read_bytes()).hexdigest()!=BINARY_SHA: raise SystemExit('已有程序校验不同，未覆盖')
        print('NATS已存在，SHA256一致');return
    source=f'https://github.com/nats-io/nats-server/releases/download/v{VERSION}/{NAME}.zip'
    archive=root/(NAME+'.zip')
    if archive.exists() and hashlib.sha256(archive.read_bytes()).hexdigest()!=ARCHIVE_SHA:
        raise SystemExit('已有压缩包校验不同，未覆盖')
    if not archive.exists():
        with tempfile.NamedTemporaryFile(dir=root,prefix=NAME+'-',suffix='.download',delete=False) as output:
            partial=Path(output.name)
            try:
                with urllib.request.urlopen(source,timeout=30) as response:
                    while block:=response.read(1024*1024): output.write(block)
            except BaseException:
                output.close();partial.unlink(missing_ok=True)
                raise
        try:
            if hashlib.sha256(partial.read_bytes()).hexdigest()!=ARCHIVE_SHA: raise SystemExit('下载校验失败，未解压')
            partial.rename(archive)
        finally:
            partial.unlink(missing_ok=True)
    with zipfile.ZipFile(archive) as z:
        data=z.read(NAME+'/nats-server.exe')
    if hashlib.sha256(data).hexdigest()!=BINARY_SHA: raise SystemExit('程序校验失败')
    binary.parent.mkdir(exist_ok=True)
    with binary.open('xb') as output:output.write(data)
    (root/'nats-receipt.json').write_text(json.dumps({'source':source,'zip_sha256':ARCHIVE_SHA,
        'binary_sha256':BINARY_SHA,'official_sha256sum_verified':True},indent=2)+'\n',encoding='utf-8')
    print('NATS下载与校验通过，程序位于dist/tools，不入Git')


def main():install(Path(__file__).resolve().parents[1]/'dist/tools')


if __name__=='__main__':main()
