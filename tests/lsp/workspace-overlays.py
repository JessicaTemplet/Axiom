#!/usr/bin/env python3
"""Exercise unsaved dependencies over the real stdio protocol."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading


class Client:
    def __init__(self, compiler):
        self.process = subprocess.Popen([compiler, '--diagnostic-format=ai', 'lsp'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.inbox = queue.Queue()
        self.publications = []
        self.serial = 0
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            while True:
                header = self.process.stdout.readline()
                if not header:
                    raise EOFError('server closed stdout')
                assert header.startswith(b'Content-Length: '), header
                length = int(header.split(b':', 1)[1])
                assert self.process.stdout.readline() == b'\r\n'
                body = b''
                while len(body) < length:
                    part = self.process.stdout.read(length - len(body))
                    if not part:
                        raise EOFError('truncated message')
                    body += part
                self.inbox.put(json.loads(body))
        except BaseException as error:
            self.inbox.put(error)

    def send(self, method, params, identity=None):
        message = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if identity is not None:
            message['id'] = identity
        body = json.dumps(message, ensure_ascii=False).encode()
        self.process.stdin.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
        self.process.stdin.flush()

    def request(self, method, params):
        self.serial += 1
        identity = 'request/%d' % self.serial
        self.send(method, params, identity)
        while True:
            message = self.inbox.get(timeout=45)
            if isinstance(message, BaseException):
                raise message
            if message.get('method') == 'textDocument/publishDiagnostics':
                self.publications.append(message['params'])
            else:
                assert message.get('id') == identity, message
                assert 'error' not in message, message
                return message['result']

    def barrier(self):
        return self.request('workspace/symbol', {'query': '__missing_barrier__'})

    def opening(self, uri, source, version=1):
        self.send('textDocument/didOpen', {'textDocument': {
            'uri': uri, 'languageId': 'axiom', 'version': version, 'text': source}})
        self.barrier()

    def change(self, uri, source, version):
        self.send('textDocument/didChange', {'textDocument': {'uri': uri, 'version': version},
            'contentChanges': [{'text': source}]})
        self.barrier()

    def latest(self, uri):
        return next(item for item in reversed(self.publications) if item['uri'] == uri)

    def close(self):
        self.request('shutdown', {})
        self.send('exit', {})
        self.process.stdin.close()
        assert self.process.wait(timeout=10) == 0, self.process.stderr.read().decode()


def run(compiler):
    with tempfile.TemporaryDirectory(prefix='axiom-lsp-workspace-') as temporary:
        root = Path(temporary) / 'unsaved π space'
        root.mkdir()
        helper = root / 'Helper.ax'
        disk = '(pub :: value Int)\n(pub fn (value) 7)\n'
        helper.write_text(disk)
        facade = root / 'Facade.ax'
        facade_source = '(import Helper)\n(pub :: answer String)\n(pub fn (answer) value)\n'
        facade.write_text(facade_source)
        main = root / 'main.ax'
        main_source = '(import Facade)\n(:: consume String)\n(fn (consume) answer)\n'
        main.write_text(main_source)
        helper_uri, facade_uri, uri = map(Path.as_uri, (helper, facade, main))
        text = '; unsaved 😀\n\n(pub :: value String)\n(pub fn (value) "overlay")\n'
        client = Client(compiler)
        client.request('initialize', {})
        client.opening(helper_uri, text)
        client.opening(facade_uri, facade_source)
        client.opening(uri, main_source)
        assert not client.latest(facade_uri)['diagnostics'], client.latest(facade_uri)
        assert not client.latest(uri)['diagnostics'], client.latest(uri)

        # Navigation reads the same overlay as the checker, including its spans.
        params = {'textDocument': {'uri': facade_uri}, 'position': {'line': 2, 'character': 20}}
        hover = client.request('textDocument/hover', params)
        assert 'String' in json.dumps(hover), hover
        location = client.request('textDocument/definition', params)
        assert location['uri'] == helper_uri, location
        assert location['range']['start']['line'] == 3, location

        client.change(helper_uri, disk, 2)
        failure = client.latest(facade_uri)
        assert failure['version'] == 1, failure
        assert 'AX3004' in [d['code'] for d in failure['diagnostics']], failure
        client.change(helper_uri, text, 3)
        assert not client.latest(facade_uri)['diagnostics'], client.latest(facade_uri)
        assert helper.read_text() == disk, 'overlay was written to disk'

        # A module need not have a disk file, and an empty overlay wins too.
        fresh = root / 'Fresh.ax'
        fresh_uri = fresh.as_uri()
        fresh_source = '(pub :: value String)\n(pub fn (value) "new")\n'
        client.opening(fresh_uri, fresh_source)
        direct = '(import Fresh)\n(:: consume String)\n(fn (consume) value)\n'
        client.change(uri, direct, 2)
        assert not client.latest(uri)['diagnostics'], client.latest(uri)
        client.change(fresh_uri, '', 2)
        assert 'AX3001' in [d['code'] for d in client.latest(uri)['diagnostics']]
        assert not fresh.exists(), 'unsaved module acquired a disk file'
        client.change(fresh_uri, fresh_source, 3)
        assert not client.latest(uri)['diagnostics'], client.latest(uri)

        # Malformed dependency text cannot take the CLI's fatal parse path.
        client.change(fresh_uri, '(pub fn (value)\n', 4)
        assert any(d['code'].startswith('AX2') for d in client.latest(fresh_uri)['diagnostics'])
        assert client.request('textDocument/definition', {
            'textDocument': {'uri': uri}, 'position': {'line': 2, 'character': 20}}) is None
        client.change(fresh_uri, fresh_source, 5)
        assert not client.latest(uri)['diagnostics'], client.latest(uri)

        # Closing returns dependants to disk; reopening restores the overlay.
        client.send('textDocument/didClose', {'textDocument': {'uri': helper_uri}})
        client.barrier()
        assert 'AX3004' in [d['code'] for d in client.latest(facade_uri)['diagnostics']]
        client.opening(helper_uri, text, version=-4)
        assert not client.latest(facade_uri)['diagnostics'], client.latest(facade_uri)
        client.close()
        print('ok   unsaved dependencies, transitive invalidation, malformed recovery and close/reopen')


if __name__ == '__main__':
    run(os.path.abspath(sys.argv[1]))
