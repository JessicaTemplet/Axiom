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
                if message['params']['message'].startswith('semantic cache '):
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

    def cached(self, method, params, reuse):
        self.traces.clear()
        result = self.request(method, params)
        assert self.traces == ['semantic cache ' + ('hit' if reuse else 'miss')], (
            method, self.traces)
        return result

    def publication(self, uri, version):
        """Wait for analysis without putting a barrier query in the cache."""
        while True:
            message = self.inbox.get(timeout=45)
            if isinstance(message, BaseException):
                raise message
            if message.get('method') == 'textDocument/publishDiagnostics':
                self.publications.append(message['params'])
                if (message['params']['uri'] == uri
                        and message['params'].get('version') == version):
                    return message['params']
            else:
                assert message.get('method') == '$/logTrace', message


def retention(compiler, directory):
    directory.mkdir()
    uri = (directory / 'main.ax').as_uri()
    source = '(:: value Int)\n(fn (value) 7)\n'
    document = {'textDocument': {'uri': uri}}
    client = TracedClient(compiler)
    try:
        client.request('initialize', {'trace': 'verbose'})
        client.send('textDocument/didOpen', {'textDocument': {
            'uri': uri, 'languageId': 'axiom', 'version': 1, 'text': source}})
        client.publication(uri, 1)

        def point(character):
            return {**document, 'position': {'line': 0, 'character': character}}

        # Nine tiny entries exercise the count limit independently of bytes.
        # FIFO removes only the oldest, so all eight survivors must still hit.
        for character in range(9):
            client.cached('textDocument/hover', point(character), reuse=False)
        for character in range(1, 9):
            client.cached('textDocument/hover', point(character), reuse=True)
        client.cached('textDocument/hover', point(0), reuse=False)

        # Eight reusable query replies fill the original capacity. A cursor
        # query must bypass retention without evicting any of them.
        protected = [{'query': '__protected_%d__' % n} for n in range(9)]
        for params in protected[:8]:
            assert client.cached('workspace/symbol', params, reuse=False) == []
        for _ in range(2):
            client.cached('textDocument/hover', point(42), reuse=False)
        for params in protected[:8]:
            assert client.cached('workspace/symbol', params, reuse=True) == []
        client.cached('workspace/symbol', protected[0], reuse=True)

        # A ninth reusable reply evicts only the oldest reusable entry.
        client.cached('workspace/symbol', protected[8], reuse=False)
        for params in protected[1:]:
            client.cached('workspace/symbol', params, reuse=True)
        client.cached('workspace/symbol', protected[0], reuse=False)

        # A new revision starts empty. Repeated cursor traffic must leave
        # unrelated whole-document replies reusable, with unchanged answers.
        client.send('textDocument/didChange', {'textDocument': {'uri': uri, 'version': 2},
            'contentChanges': [{'text': source}]})
        client.publication(uri, 2)
        whole = [
            ('textDocument/documentSymbol', document),
            ('textDocument/foldingRange', document),
            ('textDocument/formatting', {**document,
                'options': {'tabSize': 2, 'insertSpaces': True}}),
        ]
        answers = [client.cached(method, params, reuse=False) for method, params in whole]
        for character in range(12):
            client.cached('textDocument/hover', point(character), reuse=False)
            for (method, params), expected in zip(whole, answers):
                assert client.cached(method, params, reuse=True) == expected
        client.cached('textDocument/hover', point(11), reuse=True)
        client.cached('textDocument/hover', point(0), reuse=False)

        changed = source.replace('7', '8')
        client.send('textDocument/didChange', {'textDocument': {'uri': uri, 'version': 3},
            'contentChanges': [{'text': changed}]})
        client.publication(uri, 3)
        revised = [client.cached(method, params, reuse=False) for method, params in whole]
        assert revised[2] != answers[2], (answers[2], revised[2])
        client.send('textDocument/didChange', {'textDocument': {'uri': uri, 'version': 2},
            'contentChanges': [{'text': source}]})
        for (method, params), expected in zip(whole, revised):
            assert client.cached(method, params, reuse=True) == expected

        # Each observed dependency contributes just over 900 KB to an entry,
        # below the one-MiB entry limit. Four fit within four MiB, five do not;
        # six keys stay below the eight-entry count limit. This isolates byte
        # eviction and requires surviving entries to remain reusable.
        bulk = directory / 'Bulk.ax'
        padding = ';' + 'x' * 900000 + '\n'
        bulk.write_text('(pub :: boundValue Int)\n(pub fn (boundValue) 7)\n' + padding)
        imported = '(import Bulk)\n(:: consume Int)\n(fn (consume) boundValue)\n'
        client.send('textDocument/didChange', {'textDocument': {'uri': uri, 'version': 4},
            'contentChanges': [{'text': imported}]})
        client.publication(uri, 4)
        start = imported.splitlines()[2].index('boundValue')
        points = [{**document, 'position': {'line': 2, 'character': start + n}}
                  for n in range(6)]
        for params in points:
            assert 'Int' in json.dumps(client.cached('textDocument/hover', params, reuse=False))
        for params in points[2:]:
            client.cached('textDocument/hover', params, reuse=True)
        client.cached('textDocument/hover', points[1], reuse=False)

        # Protected replies still validate exact disk inputs on every reuse.
        symbols = {'query': 'boundValue'}
        answer = client.cached('workspace/symbol', symbols, reuse=False)
        assert 'boundValue' in json.dumps(answer), answer
        assert client.cached('workspace/symbol', symbols, reuse=True) == answer
        bulk.write_text('(pub :: boundValue String)\n(pub fn (boundValue) "changed")\n' + padding)
        client.cached('workspace/symbol', symbols, reuse=False)
        client.cached('workspace/symbol', symbols, reuse=True)
        client.close()
    finally:
        if client.process.poll() is None:
            client.process.kill()
            client.process.wait()


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
            # positions requested. Cursor entries expire individually.
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

            # Oversized keys bypass retained storage without evicting an
            # ordinary key that was already cached at this revision.
            client.hover(uri, 'Int', reuse=True, line=4)
            client.traces.clear()
            client.request('textDocument/hover', {
                'textDocument': {'uri': uri}, 'position': {'line': 4, 'character': 16},
                'padding': 'x' * 5000})
            assert not client.traces, client.traces
            client.hover(uri, 'Int', reuse=True, line=4)

            # A large outline remains correct but is not repeatedly encoded
            # into the bounded cache or escaped into an IPC JSON string.
            large_uri = (root / 'large.ax').as_uri()
            large = ''.join('(:: item%d Int)\n(fn (item%d) %d)\n' % (n, n, n)
                            for n in range(2500))
            client.opening(large_uri, large)
            for _ in range(2):
                client.traces.clear()
                outline = client.request('textDocument/documentSymbol', {
                    'textDocument': {'uri': large_uri}})
                assert len(outline) == 2500, len(outline)
                assert client.traces == ['semantic cache miss'], client.traces
            client.send('textDocument/didClose', {'textDocument': {'uri': large_uri}})
            client.barrier()

            # Tracing is off unless requested and can be turned off again.
            client.send('$/setTrace', {'value': 'off'})
            client.traces.clear()
            client.request('textDocument/hover', {
                'textDocument': {'uri': uri}, 'position': {'line': 2, 'character': 16}})
            assert not client.traces, client.traces
            client.close()
            retention(compiler, base / 'retention')
            print('ok   semantic cache reuse, mixed providers, FIFO count/byte bounds, exact file inputs, search precedence, package roots and revisions')
        finally:
            if original_path is None:
                os.environ.pop('AXIOM_PATH', None)
            else:
                os.environ['AXIOM_PATH'] = original_path


if __name__ == '__main__':
    run(os.path.abspath(sys.argv[1]))
