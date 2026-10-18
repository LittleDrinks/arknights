"""Public Python/CLI contracts; no engine or torch dependency."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import unittest

from arknights.adapter import RULES
from arknights.collect import serve, export

ROOT = Path(__file__).resolve().parents[1]


class CollectTests(unittest.TestCase):
    def viewer(self, health):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(health).encode())

            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port

    def test_reuse_capable_viewer_close_does_not_stop_someone_elses_match_host(self):
        port = self.viewer(dict(arknightsPreview=1, rules=RULES, root=str(ROOT), replayDir=None,
                                collect=dict(supported=True)))
        handle = serve(root=ROOT, port=port)
        self.assertEqual(handle.url, f'http://127.0.0.1:{port}/collect')
        handle.close()
        self.assertEqual(serve(root=ROOT, port=port).url, handle.url)

    def test_occupied_old_viewer_explicitly_refuses_capture_without_restarting(self):
        port = self.viewer(dict(arknightsPreview=1, rules=RULES, root=str(ROOT), replayDir=None))
        with self.assertRaisesRegex(RuntimeError, 'does not support capture'):
            serve(root=ROOT, port=port)

    def test_export_public_dataset_ref_and_persistent_hashes(self):
        with tempfile.TemporaryDirectory(prefix='collect-python-', dir=ROOT / 'results/tests') as directory:
            script = "import {Collections} from './arknights/collect/store.mjs'; console.log(new Collections(process.argv[1]).create('Python API').id)"
            identifier = subprocess.check_output(['node', '--input-type=module', '-e', script, directory], cwd=ROOT, text=True).strip()
            ref = export(identifier, root=directory)
            self.assertEqual(ref.quality['samples'], 0)
            self.assertEqual(ref.sha256, hashlib.sha256((ref.path / 'samples.jsonl').read_bytes()).hexdigest())
            self.assertEqual(json.loads((ref.path / 'dataset.json').read_text())['version'], ref.version)
            self.assertEqual(export(identifier, root=directory).version, ref.version)

    def test_cli_exposes_minimal_collect_commands(self):
        help_text = subprocess.check_output(['python3', '-m', 'arknights', 'collect', '--help'], cwd=ROOT, text=True)
        self.assertIn('serve', help_text)
        self.assertIn('export', help_text)


if __name__ == '__main__':
    unittest.main()
