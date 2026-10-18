import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from arknights.adapter import RULES
from arknights.replay import _publish, preview


class PublicationTests(unittest.TestCase):
    def bundle(self, root, name):
        path = root / name
        files = {
            'agent/index.json': {'schema': 'test', 'episodes': [
                {'id': name, 'file': f'episodes/{name}.json', 'steps': 1, 'bytes': 2,
                 'battles': [{'name': name, 'file': name + '.json'}]}]},
            'recordings/index.json': [{'name': name, 'file': name + '.json'}],
            f'agent/episodes/{name}.json': {'meta': {'id': name}},
            f'recordings/{name}.json': {'name': name},
        }
        inventory = []
        for name, obj in files.items():
            data = json.dumps(obj).encode()
            p = path / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            inventory.append({'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        (path / 'verification.json').write_text(json.dumps({'files': inventory}))
        return path

    def test_append_preserves_old_entries_and_collision_never_overwrites(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            first, second = self.bundle(root, 'first'), self.bundle(root, 'second')
            _publish(root, first)
            _publish(root, second)
            live = root / 'results/replays/public/dev'
            index = live / 'agent/index.json'
            before = index.read_bytes()
            self.assertEqual([x['id'] for x in json.loads(before)['episodes']], ['first', 'second'])
            with self.assertRaises(ValueError):
                _publish(root, second)
            self.assertEqual(index.read_bytes(), before)
            self.assertEqual(json.loads((live / 'recordings/first.json').read_bytes()), {'name': 'first'})

    def test_corrupt_or_missing_indexed_artifacts_rejected_before_publication(self):
        for corruption in ('index', 'missing', 'unlisted'):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as t:
                root = Path(t)
                _publish(root, self.bundle(root, 'first'))
                live = root / 'results/replays/public/dev'
                before = {str(p.relative_to(live)): p.read_bytes() for p in live.rglob('*') if p.is_file()}
                second = self.bundle(root, 'second')
                if corruption == 'index':
                    (second / 'agent/index.json').write_text('{}')
                elif corruption == 'missing':
                    (second / 'recordings/second.json').unlink()
                else:
                    report = json.loads((second / 'verification.json').read_bytes())
                    report['files'] = [r for r in report['files'] if r['file'] != 'recordings/second.json']
                    (second / 'verification.json').write_text(json.dumps(report))
                with self.assertRaises((ValueError, FileNotFoundError)):
                    _publish(root, second)
                self.assertEqual({str(p.relative_to(live)): p.read_bytes() for p in live.rglob('*') if p.is_file()}, before)

    def test_symlink_ancestors_are_rejected_without_modifying_external_targets(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            outside = root / 'outside'
            outside.mkdir()
            bundle = self.bundle(root, 'second')
            live = root / 'results/replays/public/dev'
            live.mkdir(parents=True)
            (live / 'agent').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'Symlink'):
                _publish(root, bundle)
            self.assertEqual(list(outside.iterdir()), [])

    def test_private_mount_is_not_reused_as_the_live_viewer(self):
        with tempfile.TemporaryDirectory() as t:
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = json.dumps({'arknightsPreview': 1, 'rules': RULES,
                'root': t, 'replayDir': t + '/private'}).encode()
            with patch('arknights.replay.urllib.request.urlopen', return_value=response), \
                    patch('arknights.replay.subprocess.Popen') as launch:
                with self.assertRaisesRegex(RuntimeError, 'occupied'):
                    preview(root=t)
                launch.assert_not_called()

    def test_startup_exception_reaps_only_the_new_viewer(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as t:
            process = Mock(pid=123, poll=Mock(return_value=None))
            with patch('arknights.replay.urllib.request.urlopen', side_effect=urllib.error.URLError('offline')), \
                    patch('arknights.replay.subprocess.Popen', return_value=process), \
                    patch('arknights.replay._atomic', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    preview(root=t)
                process.terminate.assert_called_once()
                process.wait.assert_called_once()


if __name__ == '__main__':
    unittest.main()
