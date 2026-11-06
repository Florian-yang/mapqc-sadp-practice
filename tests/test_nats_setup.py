import hashlib
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from scripts import get_nats


class NatsSetupTests(unittest.TestCase):
    def test_interrupted_download_can_retry_without_deleting_unknown_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            unknown=root/(get_nats.NAME+'.download');unknown.write_bytes(b'unknown existing file')
            with patch.object(get_nats.urllib.request,'urlopen',side_effect=OSError('synthetic interrupted download')):
                with self.assertRaises(OSError):get_nats.install(root)
            self.assertEqual(list(root.iterdir()),[unknown])
            archive=io.BytesIO();binary=b'synthetic executable fixture'
            with zipfile.ZipFile(archive,'w') as z:z.writestr(get_nats.NAME+'/nats-server.exe',binary)
            with patch.object(get_nats,'ARCHIVE_SHA',hashlib.sha256(archive.getvalue()).hexdigest()),patch.object(get_nats,'BINARY_SHA',hashlib.sha256(binary).hexdigest()),patch.object(get_nats.urllib.request,'urlopen',return_value=io.BytesIO(archive.getvalue())):
                with redirect_stdout(io.StringIO()): get_nats.install(root)
            self.assertEqual((root/get_nats.NAME/'nats-server.exe').read_bytes(),binary)
            self.assertEqual(unknown.read_bytes(),b'unknown existing file')

    def test_unrecognized_existing_binary_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);binary=root/get_nats.NAME/'nats-server.exe';binary.parent.mkdir()
            binary.write_bytes(b'unknown program')
            with self.assertRaises(SystemExit):get_nats.install(root)
            self.assertEqual(binary.read_bytes(),b'unknown program')
