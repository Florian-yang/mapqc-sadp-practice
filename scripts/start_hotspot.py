"""Run the course demo on one selected LAN interface, with HTTPS."""
import argparse
from datetime import datetime
from http.server import ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from check_collect.hotspot import create_certificate,certificate_handler


def main():
    p=argparse.ArgumentParser(description='先开启电脑移动热点，再填写该热点网卡IPv4地址')
    p.add_argument('--address',required=True)
    args=p.parse_args()
    address=str(ipaddress.IPv4Address(args.address))
    if not ipaddress.ip_address(address).is_private or address=='0.0.0.0':p.error('请使用热点网卡的私有IPv4')
    # Binding only to the selected interface keeps other network interfaces out.
    for host,port in ((address,8443),(address,8444),('127.0.0.1',14222)):
        with socket.socket() as probe:probe.bind((host,port))
    nats=ROOT/'dist/tools/nats-server-v2.15.2-windows-amd64/nats-server.exe'
    if not nats.is_file():p.error('先执行scripts/get_nats.py准备NATS')
    data=ROOT/'data/hotspot-demo';db=data/'demo.sqlite3'
    if not db.exists():
        subprocess.run([sys.executable,'-X','utf8',str(ROOT/'scripts/create_demo.py'),'--database','data/hotspot-demo/demo.sqlite3'],cwd=ROOT,check=True)
    tls=data/('tls-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    fingerprint=create_certificate(tls,address)
    origin=f'https://{address}:8443'
    env={**os.environ,'PUBLIC_ORIGIN':origin,'CHECK_COLLECT_DB':str(db),'CHECK_COLLECT_NATS_URL':'nats://127.0.0.1:14222'}
    bootstrap=ThreadingHTTPServer((address,8444),certificate_handler(tls/'ca.cer',origin))
    threading.Thread(target=bootstrap.serve_forever,daemon=True).start()
    state={'address':address,'origin':origin,'setup_url':f'http://{address}:8444/',
           'certificate_sha256':fingerprint,'database':str(db),'tls_folder':str(tls),'pid':os.getpid(),'children':[]}
    children=[]
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    stop=data/'stop'
    if stop.exists():stop.unlink()
    try:
        children.append(subprocess.Popen([str(nats),'-js','-a','127.0.0.1','-p','14222','-sd',str(data/'nats')],cwd=ROOT,env=env,creationflags=flags,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
        for _ in range(100):
            if children[0].poll() is not None:raise RuntimeError('NATS未能启动')
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1',14222))==0:break
            time.sleep(.05)
        else:raise RuntimeError('NATS启动超时')
        children.append(subprocess.Popen([sys.executable,'-X','utf8','-m','check_collect.messaging'],cwd=ROOT,env=env,creationflags=flags,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
        children.append(subprocess.Popen([sys.executable,'-X','utf8','-m','check_collect.server','--host',address,'--port','8443','--certfile',str(tls/'server.pem'),'--keyfile',str(tls/'server-key.pem')],cwd=ROOT,env=env,creationflags=flags,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
        state['children']=[c.pid for c in children]
        (data/'running.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(state,ensure_ascii=False,indent=2),flush=True)
        print('手机先打开setup_url安装并信任证书，再打开origin。关闭本窗口或创建data/hotspot-demo/stop停止服务。',flush=True)
        while not stop.exists():
            if any(c.poll() is not None for c in children):raise RuntimeError('演示进程已退出，请检查端口及配置')
            time.sleep(.5)
    finally:
        bootstrap.shutdown();bootstrap.server_close()
        for child in reversed(children):
            if child.poll() is None:child.terminate();child.wait(timeout=10)
        (data/'stopped.json').write_text(json.dumps({'children_stopped':[c.pid for c in children]})+'\n',encoding='utf-8')


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
