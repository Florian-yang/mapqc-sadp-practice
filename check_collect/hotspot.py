"""Short-lived certificates and a certificate-only LAN setup page."""
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import plistlib
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler


def create_certificate(folder, address):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    ip=ipaddress.ip_address(address)
    if ip.version!=4 or not (ip.is_private or ip.is_loopback) or ip.is_unspecified:
        raise ValueError('只接受本机热点的IPv4地址')
    folder=Path(folder)
    if folder.exists() and any(folder.iterdir()):
        raise ValueError('证书目录非空，未覆盖；请使用新目录')
    folder.mkdir(parents=True,exist_ok=True)
    now=datetime.now(timezone.utc)
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'CheckCollect Demo CA')])
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
        .not_valid_after(now+timedelta(days=3)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),False)
        .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),True)
        .sign(ca_key,hashes.SHA256()))
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    leaf=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,address)]))
        .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),False)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip),x509.IPAddress(ipaddress.ip_address('127.0.0.1')),x509.DNSName('localhost')]),False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),False)
        .add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),True)
        .sign(ca_key,hashes.SHA256()))
    # The CA signing key is discarded, and only the server key remains on disk.
    (folder/'ca.cer').write_bytes(ca.public_bytes(serialization.Encoding.DER))
    (folder/'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (folder/'server.pem').write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    private=folder/'server-key.pem'
    with private.open('xb') as f:
        private.chmod(0o600)
        f.write(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    return hashlib.sha256((folder/'ca.cer').read_bytes()).hexdigest()


def certificate_handler(public_certificate, origin):
    data=Path(public_certificate).read_bytes()
    fingerprint=hashlib.sha256(data).hexdigest()
    identity=str(uuid.uuid5(uuid.NAMESPACE_URL,origin+fingerprint))
    profile=plistlib.dumps({'PayloadType':'Configuration','PayloadVersion':1,
        'PayloadIdentifier':'local.checkcollect.demo.'+identity,'PayloadUUID':identity,
        'PayloadDisplayName':'CheckCollect Demo CA','PayloadRemovalDisallowed':False,
        'PayloadDescription':'仅用于本次check@collect热点演示，演示结束后请移除。',
        'PayloadContent':[{'PayloadType':'com.apple.security.root','PayloadVersion':1,
            'PayloadIdentifier':'local.checkcollect.certificate.'+identity,
            'PayloadUUID':str(uuid.uuid5(uuid.NAMESPACE_URL,identity)),
            'PayloadDisplayName':'CheckCollect Demo CA','PayloadCertificateFileName':'ca.cer','PayloadContent':data}]})
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/ca.cer':
                body=data;kind='application/x-x509-ca-cert';status=200
            elif self.path=='/demo.mobileconfig':
                body=profile;kind='application/x-apple-aspen-config';status=200
            elif self.path=='/':
                body=(f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                      f'<h1>check@collect 手机设置</h1><p><a href="/demo.mobileconfig">iPhone安装演示证书</a></p><p><a href="/ca.cer">安卓下载证书</a></p>'
                      f'<p>SHA256：{fingerprint}</p><p>安装后在iPhone的“设置→通用→关于本机→证书信任设置”中信任CheckCollect Demo CA。</p>'
                      f'<p><a href="{origin}">打开采集系统</a></p><p>演示结束后移除此证书。</p>').encode()
                kind='text/html; charset=utf-8';status=200
            else:body=b'Not found';kind='text/plain';status=404
            self.send_response(status);self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(body)
    return Handler
