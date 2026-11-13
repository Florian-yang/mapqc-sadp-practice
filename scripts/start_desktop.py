from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
import argparse
import ctypes
from ctypes import wintypes
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ADDRESS = '192.168.137.1'
ORIGIN = f'https://{ADDRESS}:8443'
DATA = ROOT / 'data/learning-demo'
TLS = DATA / 'desktop-tls'
LEGACY_TLS = ROOT / 'data/hotspot-demo/tls-20261111-172803-155850'
if (LEGACY_TLS / 'server.pem').exists():
    from cryptography import x509
    legacy_cert = x509.load_pem_x509_certificate((LEGACY_TLS / 'server.pem').read_bytes())
    if legacy_cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(minutes=10):
        TLS = LEGACY_TLS
RUNTIME = DATA / 'desktop-runtime'


def config(filename):
    values = {}
    for line in (ROOT / 'data/local-config' / filename).read_text(encoding='utf-8-sig').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('\"\'')
    return values


def certificates():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    import ipaddress
    now = datetime.now(timezone.utc)
    if (TLS / 'server.pem').exists():
        cert = x509.load_pem_x509_certificate((TLS / 'server.pem').read_bytes())
        if cert.not_valid_after_utc <= now + timedelta(minutes=5):
            raise RuntimeError('演示证书即将到期，请更新证书后重新安装到手机。')
        return
    TLS.mkdir(parents=True, exist_ok=True)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'CheckCollect Desktop CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=366))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ADDRESS)]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ADDRESS))]), False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), True)
            .sign(ca_key, hashes.SHA256()))
    (TLS / 'ca.cer').write_bytes(ca.public_bytes(serialization.Encoding.DER))
    (TLS / 'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (TLS / 'server-key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (TLS / 'server.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))


class Children:
    def __init__(self):
        # Windows closes all owned services when the launch window is closed.
        class BASIC(ctypes.Structure):
            _fields_ = [('a', ctypes.c_longlong), ('b', ctypes.c_longlong), ('flags', wintypes.DWORD),
                        ('min', ctypes.c_size_t), ('max', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ('a', 'b', 'c', 'd', 'e', 'f')]
        class EXTENDED(ctypes.Structure):
            _fields_ = [('basic', BASIC), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.job = self.kernel.CreateJobObjectW(None, None)
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = EXTENDED()
        limits.basic.flags = 0x2000
        if not self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.items = []
        self.logs = []

    def start(self, name, args, env):
        log = (RUNTIME / (name + '.log')).open('ab', buffering=0)
        self.logs.append(log)
        child = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        self.items.append(child)
        if not self.kernel.AssignProcessToJobObject(self.job, wintypes.HANDLE(int(child._handle))):
            child.terminate()
            raise RuntimeError('无法管理子服务，已停止启动。')
        return child

    def close(self):
        for child in reversed(self.items):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        self.kernel.CloseHandle(self.job)
        for log in self.logs:
            log.close()


def wait_port(host, port, child):
    for _ in range(100):
        if child.poll() is not None:
            raise RuntimeError('服务启动失败，请查看 data/learning-demo/desktop-runtime 中的日志。')
        try:
            with socket.create_connection((host, port), timeout=.2):
                return
        except OSError:
            time.sleep(.1)
    raise RuntimeError('等待服务启动超时。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if (RUNTIME / 'running.json').exists():
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:8765/', timeout=2) as response:
                running = response.headers.get('Server', '').startswith('CheckCollect')
            with socket.create_connection(('127.0.0.1', 14222), timeout=2):
                pass
        except OSError:
            running = False
        if running:
            print('系统已经启动。电脑地图：http://127.0.0.1:8765/?desktop=1', flush=True)
            if not args.no_browser and not args.smoke:
                os.startfile('http://127.0.0.1:8765/?desktop=1')
            return
    phone_enabled = True
    try:
        with socket.socket() as probe:
            probe.bind((ADDRESS, 0))
    except OSError:
        phone_enabled = False
    ports = [('127.0.0.1', 14222), ('127.0.0.1', 8765)]
    if phone_enabled:
        ports += [(ADDRESS, 8443), (ADDRESS, 8444)]
    for host, port in ports:
        try:
            with socket.socket() as s:
                s.bind((host, port))
        except OSError as exc:
            if exc.winerror == 10049:
                raise RuntimeError('请先开启电脑的移动热点，再双击本入口。') from None
            raise RuntimeError(f'端口 {port} 已被占用。若系统已经启动，请使用原窗口；本入口不会关闭其他程序。') from None
    if not (DATA / 'demo.sqlite3').is_file():
        raise RuntimeError('没有找到现有数据库，未创建或覆盖任何数据。')
    wechat, ocr = config('微信.txt'), config('百度OCR.txt')
    if not all(wechat.get(k) for k in ('APP_ID', 'APP_SECRET')) or not all(ocr.get(k) for k in ('API_KEY', 'SECRET_KEY')):
        raise RuntimeError('本机微信或百度OCR配置不完整。')
    env = {**os.environ, 'PUBLIC_ORIGIN': ORIGIN, 'CHECK_COLLECT_DB': str(DATA / 'demo.sqlite3'),
           'CHECK_COLLECT_NATS_URL': 'nats://127.0.0.1:14222', 'WECHAT_LOGIN_MODE': 'official_account',
           'WECHAT_APP_ID': wechat['APP_ID'], 'WECHAT_APP_SECRET': wechat['APP_SECRET'],
           'BAIDU_OCR_API_KEY': ocr['API_KEY'], 'BAIDU_OCR_SECRET_KEY': ocr['SECRET_KEY']}
    if phone_enabled:
        certificates()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    from check_collect.hotspot import certificate_handler
    bootstrap = ThreadingHTTPServer((ADDRESS, 8444), certificate_handler(TLS / 'ca.cer', ORIGIN)) if phone_enabled else None
    children = Children()
    try:
        if bootstrap:
            threading.Thread(target=bootstrap.serve_forever, daemon=True).start()
        nats = children.start('nats', [str(ROOT / 'dist/tools/nats-server-v2.15.2-windows-amd64/nats-server.exe'),
            '-js', '-a', '127.0.0.1', '-p', '14222', '-sd', str(DATA / 'nats')], env)
        wait_port('127.0.0.1', 14222, nats)
        worker = children.start('messages', [sys.executable, '-X', 'utf8', '-u', '-m', 'check_collect.messaging'], env)
        if phone_enabled:
            web = children.start('web', [sys.executable, '-X', 'utf8', '-u', '-m', 'check_collect.server',
                '--host', ADDRESS, '--port', '8443', '--certfile', str(TLS / 'server.pem'), '--keyfile', str(TLS / 'server-key.pem')], env)
            wait_port(ADDRESS, 8443, web)
        desktop_env = {**env, 'PUBLIC_ORIGIN': 'http://127.0.0.1:8765', 'WECHAT_APP_ID': '', 'WECHAT_APP_SECRET': ''}
        desktop = children.start('desktop', [sys.executable, '-X', 'utf8', '-u', '-m', 'check_collect.server',
            '--host', '127.0.0.1', '--port', '8765'], desktop_env)
        wait_port('127.0.0.1', 8765, desktop)
        context = ssl.create_default_context(cafile=str(TLS / 'ca.pem')) if phone_enabled else ssl.create_default_context()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
        with opener.open(ORIGIN if phone_enabled else 'http://127.0.0.1:8765/', timeout=5) as response:
            assert response.status == 200
        time.sleep(1)
        if worker.poll() is not None:
            raise RuntimeError('消息处理服务退出，请查看 messages.log。')
        (RUNTIME / 'running.json').write_text(json.dumps({'launcher': os.getpid(), 'children': [p.pid for p in children.items],
            'origin': ORIGIN, 'started': datetime.now().isoformat()}, indent=2), encoding='utf-8')
        print('\n系统已启动：网页、NATS、通知处理服务。', flush=True)
        print('电脑地图：http://127.0.0.1:8765/?desktop=1 （用账号密码登录）', flush=True)
        if phone_enabled:
            print(f'手机连接电脑热点，在微信中打开：{ORIGIN}/', flush=True)
            print(f'首次使用新证书：用Safari打开 http://{ADDRESS}:8444/', flush=True)
            print('安装后到 设置→通用→关于本机→证书信任设置，信任 CheckCollect Desktop CA。', flush=True)
        else:
            print('热点未开启：电脑端可正常使用；需要手机时，先开热点再重新启动本入口。', flush=True)
        print('采集者 test / 12345678；核验者 reviewer / 12345678', flush=True)
        print('微信测试号授权回调域名保持 192.168.137.1:8443，不需要修改。', flush=True)
        print('保留这个窗口；关闭窗口或按 Ctrl+C，会停止本入口启动的服务。\n', flush=True)
        if args.smoke:
            print('SMOKE_OK', flush=True)
            return
        if not args.no_browser:
            os.startfile('http://127.0.0.1:8765/?desktop=1')
        while True:
            if any(p.poll() is not None for p in children.items):
                raise RuntimeError('有服务已退出，系统将停止。请重新启动，日志位于 data/learning-demo/desktop-runtime。')
            time.sleep(1)
    finally:
        if bootstrap:
            bootstrap.shutdown()
            bootstrap.server_close()
        children.close()
        (RUNTIME / 'running.json').unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n系统已停止。')
    except Exception as exc:
        print('\n启动未完成：' + str(exc), flush=True)
        sys.exit(1)
