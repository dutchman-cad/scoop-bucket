"""Offline checks for release selection, archive verification, and write behavior."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

import bump


def release(tag, prerelease=False):
    return {"tag_name": tag, "prerelease": prerelease, "draft": False}


def missing_latest():
    error = urllib.error.HTTPError('https://api.github.com/latest', 404, 'Not Found', {}, io.BytesIO())
    error.close()
    return error


class ReleaseTests(unittest.TestCase):
    def test_stable_is_used_for_both_products(self):
        for repo in ('dutchman', 'pilot'):
            stable = release('v1.0.0')
            with patch.object(bump, 'api', return_value=stable) as api:
                self.assertIs(bump.resolve_release(repo), stable)
                api.assert_called_once_with(f'{repo}/releases/latest')

    def test_pilot_skips_nightly_and_drafts(self):
        draft = dict(release('v2.0.0'), draft=True)
        beta = release('v1.0.0-beta.2', True)
        with patch.object(bump, 'api', side_effect=[missing_latest(), [release('nightly', True), draft, beta]]):
            self.assertIs(bump.resolve_release('pilot'), beta)

    def test_pilot_paginates_past_nightly(self):
        beta = release('v1.0.0-beta.1', True)
        with patch.object(bump, 'api', side_effect=[missing_latest(), [release('nightly', True)], [beta]]):
            self.assertIs(bump.resolve_release('pilot'), beta)

    def test_convert_does_not_fall_back_to_prereleases(self):
        with patch.object(bump, 'api', side_effect=missing_latest()):
            with self.assertRaises(urllib.error.HTTPError):
                bump.resolve_release('dutchman')

    def test_other_http_errors_do_not_trigger_fallback(self):
        error = urllib.error.HTTPError('https://api.github.com/latest', 403, 'Forbidden', {}, io.BytesIO())
        self.addCleanup(error.close)
        with patch.object(bump, 'api', side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                bump.resolve_release('pilot')

    def test_checksums(self):
        digest = 'ab' * 32
        for value in (digest, f'{digest}  app.zip', f'{digest} *app.zip',
                      f'{digest}  ./app.zip', f'SHA256 (app.zip) = {digest}'):
            self.assertEqual(bump.checksum(value, 'app.zip'), digest)
        with self.assertRaises(ValueError):
            bump.checksum(f'{digest}  other.zip', 'app.zip')

    def test_incorrect_archive_layout_is_rejected(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('wrong/app.exe', b'fixture')
        data.seek(0)
        with self.assertRaises(ValueError):
            bump.inspect_archive(data, 'expected', ('app.exe',))

    def test_bearer_token_only_goes_to_api(self):
        with patch.dict(bump.os.environ, {'GITHUB_TOKEN': 'test-token'}):
            with patch.object(bump.urllib.request, 'urlopen') as urlopen:
                bump.request('https://api.github.com/repos/dutchman-cad/pilot/releases')
                self.assertEqual(urlopen.call_args.args[0].get_header('Authorization'), 'Bearer test-token')
                bump.request('https://github.com/dutchman-cad/pilot/releases/download/v1/app.zip')
                self.assertIsNone(urlopen.call_args.args[0].get_header('Authorization'))


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'bucket').mkdir()
        self.payloads = {}
        self.releases = {}
        for name, repo, pattern, required in bump.PRODUCTS:
            source = bump.ROOT / 'bucket' / f'{name}.json'
            (self.root / 'bucket' / source.name).write_bytes(source.read_bytes())
            folder = pattern.format(version='9.9.9')
            filename = folder + '.zip'
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as archive:
                for exe in required:
                    archive.writestr(f'{folder}/{exe}', b'offline test fixture')
            url = f'https://github.com/dutchman-cad/{repo}/releases/download/v9.9.9/{filename}'
            self.payloads[url] = data.getvalue()
            assets = [{'name': filename, 'browser_download_url': url}]
            # Exercise both checksum verification and computed-only checksums.
            if repo == 'dutchman':
                self.payloads[url + '.sha256'] = (hashlib.sha256(data.getvalue()).hexdigest() + '  ' + filename).encode()
                assets.append({'name': filename + '.sha256', 'browser_download_url': url + '.sha256'})
            self.releases[repo] = dict(release('v9.9.9'), assets=assets)
        self.addCleanup(patch.stopall)
        patch.object(bump, 'ROOT', self.root).start()
        patch.object(bump, 'resolve_release', side_effect=self.releases.__getitem__).start()
        patch.object(bump, 'request', side_effect=lambda url: io.BytesIO(self.payloads[url])).start()

    def run_main(self, *args):
        with patch('sys.argv', ['bump.py', *args]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return bump.main()

    def snapshot(self):
        return {path.name: path.read_bytes() for path in (self.root / 'bucket').glob('*.json')}

    def test_check_reports_changes_without_writing(self):
        before = self.snapshot()
        self.assertEqual(self.run_main('--check'), 1)
        self.assertEqual(self.snapshot(), before)

    def test_update_then_check_is_up_to_date(self):
        self.assertEqual(self.run_main(), 0)
        before = self.snapshot()
        self.assertEqual(self.run_main('--check'), 0)
        self.assertEqual(self.snapshot(), before)
        for path in (self.root / 'bucket').glob('*.json'):
            manifest = json.loads(path.read_text())
            self.assertEqual(manifest['version'], '9.9.9')
            url = manifest['architecture']['64bit']['url']
            self.assertEqual(manifest['architecture']['64bit']['hash'], hashlib.sha256(self.payloads[url]).hexdigest())
            self.assertIn('/v$version/', manifest['autoupdate']['architecture']['64bit']['url'])

    def test_checksum_mismatch_prevents_writes(self):
        before = self.snapshot()
        url = next(url for url in self.payloads if url.endswith('.sha256'))
        self.payloads[url] = b'0' * 64
        self.assertEqual(self.run_main(), 2)
        self.assertEqual(self.snapshot(), before)

    def test_second_product_failure_prevents_all_writes(self):
        before = self.snapshot()
        self.releases['pilot']['assets'] = []
        self.assertEqual(self.run_main(), 2)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
