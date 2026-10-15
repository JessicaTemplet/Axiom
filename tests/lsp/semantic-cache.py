#!/usr/bin/env python3
"""Check cache reuse and invalidation independently over the LSP pipe."""
import json
import os
from pathlib import Path
import sys
import runpy
import tempfile

Client = runpy.run_path(str(Path(__file__).with_name("workspace-overlays.py")))["Client"]


class TracedClient(Client):
    def __init__(self, compiler):
        super().__init__(compiler)
        self.traces = []

    def request(self, method, params):
        self.serial += 1
        identity = 'cache/%d' % self.serial
        self.send(method, params, identity)
        while True:
            message = self.inbox.get(timeout=45)
            if isinstance(message, BaseException):
                raise message
            if message.get('method') == 'textDocument/publishDiagnostics':
                self.publications.append(message['params'])
            elif message.get('method') == '$/logTrace':
                self.traces.append(message['params']['message'])
            else:
                assert message.get('id') == identity, message
                assert 'error' not in message, message
                return message['result']

    def hover(self, uri, expected, reuse, line=2, character=16):
        self.traces.clear()
        result = self.request('textDocument/hover', {
            'textDocument': {'uri': uri}, 'position': {'line': line, 'character': character}})
        assert expected in json.dumps(result), result
        assert self.traces == ['semantic cache ' + ('hit' if reuse else 'miss')], self.traces
        return result


def run(compiler):
    with tempfile.TemporaryDirectory(prefix='axiom-lsp-cache-') as temporary:
        base = Path(temporary)
        root, vendor, other = (base / name for name in ('project', 'vendor', 'other'))
        for directory in (root, vendor, other):
            directory.mkdir()
        original_path = os.environ.get('AXIOM_PATH')
        os.environ['AXIOM_PATH'] = str(vendor)
        try:
            helper = vendor / 'Helper.ax'
            helper.write_text('(pub :: value Int)\n(pub fn (value) 7)\n')
            source = '(import Helper)\n(:: consume Int)\n(fn (consume) value)\n'
            uri = (root / 'main.ax').as_uri()
            client = TracedClient(compiler)
            client.request('initialize', {'trace': 'verbose'})
            client.opening(uri, source)
            client.hover(uri, 'Int', reuse=False)
            client.hover(uri, 'Int', reuse=True)

            # A dependency edit on disk, without an editor notification,
            # invalidates the exact observed source bytes.
            helper.write_text('(pub :: value String)\n(pub fn (value) "changed")\n')
            client.hover(uri, 'String', reuse=False)
            client.hover(uri, 'String', reuse=True)

            # Failed higher-priority candidates are inputs too. Creating a
            # project module switches the winner even though the vendor file
            # and the requesting document did not change.
            shadow = root / 'Helper.ax'
            shadow.write_text('(pub :: value Float)\n(pub fn (value) 1.5)\n')
            client.hover(uri, 'Float', reuse=False)
            shadow.unlink()
            client.hover(uri, 'String', reuse=True)

            # A changed package search root invalidates cached resolution.
            (other / 'Helper.ax').write_text('(pub :: value Bool)\n(pub fn (value) true)\n')
            manifest = root / 'axiom.pkg'
            manifest.write_text('depend ../other\n')
            client.hover(uri, 'Bool', reuse=False)
            manifest.unlink()
            client.hover(uri, 'String', reuse=True)
            client.hover(uri, 'String', reuse=True)

            # Open source takes precedence over disk, and each accepted
            # workspace revision invalidates previous derived results.
            helper_uri = helper.as_uri()
            overlay = '(pub :: value Float)\n(pub fn (value) 2.5)\n'
            client.opening(helper_uri, overlay, version=5)
            client.hover(uri, 'Float', reuse=False)
            client.hover(uri, 'Float', reuse=True)
            client.change(helper_uri, '', 4)  # stale; leaves cache and source intact
            client.hover(uri, 'Float', reuse=True)
            client.change(helper_uri, overlay, 6)
            client.hover(uri, 'Float', reuse=False)
            client.send('textDocument/didClose', {'textDocument': {'uri': helper_uri}})
            client.barrier()
            client.hover(uri, 'String', reuse=False)

            # The cache is bounded independently of the number of cursor
            # positions requested. A ninth distinct key evicts the old set.
            client.hover(uri, 'String', reuse=True)
            for character in range(10):
                client.request('textDocument/hover', {
                    'textDocument': {'uri': uri}, 'position': {'line': 0, 'character': character}})
            client.hover(uri, 'String', reuse=False)

            # Saved and watched disk edits also refresh diagnostics and
            # generated-name analysis, even when the importer version is stable.
            def macro(kind, body):
                return ('(pub macro deriveTag ((deriveTag T)\n'
                        ' (pub :: (syntax/join tag T) %s)\n'
                        ' (pub fn ((syntax/join tag T)) %s)))\n') % (kind, body)
            helper.write_text(macro('Int', '7'))
            generated = ('(import Helper)\n(data Shape (One))\n(deriveTag Shape)\n'
                         '(:: consume Int)\n(fn (consume) tagShape)\n')
            client.change(uri, generated, 20)
            client.hover(uri, 'Int', reuse=False, line=4)
            client.hover(uri, 'Int', reuse=True, line=4)
            helper.write_text(macro('Float', '7.5'))
            client.send('workspace/didChangeWatchedFiles', {'changes': [
                {'uri': helper_uri, 'type': 2}]})
            client.barrier()
            assert client.latest(uri)['version'] == 20
            assert 'AX3004' in [d['code'] for d in client.latest(uri)['diagnostics']]
            client.hover(uri, 'Float', reuse=False, line=4)
            helper.write_text(macro('Int', '8'))
            client.send('textDocument/didSave', {'textDocument': {'uri': helper_uri}})
            client.barrier()
            assert not client.latest(uri)['diagnostics'], client.latest(uri)
            client.hover(uri, 'Int', reuse=False, line=4)

            # Tracing is off unless requested and can be turned off again.
            client.send('$/setTrace', {'value': 'off'})
            client.traces.clear()
            client.request('textDocument/hover', {
                'textDocument': {'uri': uri}, 'position': {'line': 2, 'character': 16}})
            assert not client.traces, client.traces
            client.close()
            print('ok   semantic cache reuse, exact file inputs, search precedence, package roots, revisions and bounds')
        finally:
            if original_path is None:
                os.environ.pop('AXIOM_PATH', None)
            else:
                os.environ['AXIOM_PATH'] = original_path


if __name__ == '__main__':
    run(os.path.abspath(sys.argv[1]))
