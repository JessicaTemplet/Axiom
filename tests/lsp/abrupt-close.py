#!/usr/bin/env python3
"""Every accepted request gets a response when its client disconnects."""
import os
from pathlib import Path
import runpy
import signal
import sys
import tempfile


support = runpy.run_path(str(Path(__file__).with_name('background-analysis.py')))
Client = support['RacingClient']
children = support['children']
freeze_worker = support['freeze_worker']
gone = support['gone']
open_source = support['open_source']


def run(compiler):
    if os.name != 'posix':
        print('skip abrupt-close: native process and signal support required')
        return
    source = ''.join('(:: item%d Int)\n(fn (item%d) %d)\n' % (n, n, n)
                     for n in range(50000))
    with tempfile.TemporaryDirectory(prefix='axiom-lsp-close-') as directory:
        uri = (Path(directory) / 'main.ax').as_uri()
        for ending in ('eof', 'broken', 'exit'):
            client = Client(compiler)
            try:
                client.request('initialize', {'trace': 'verbose'})
                open_source(client, uri, source, 1)
                client.until(lambda: any(p['uri'] == uri for p in client.publications))
                client.traces.clear()
                client.send('textDocument/documentSymbol',
                            {'textDocument': {'uri': uri}}, 'active')
                client.started('request')
                worker = freeze_worker(client)
                identities = ['active']
                if ending == 'exit':
                    # Exit may overtake queued requests. They still need
                    # responses, and shutdown must reap the stopped worker.
                    identities += ['queued/1', 'queued/2']
                    for identity in identities[1:]:
                        client.send('textDocument/documentSymbol',
                                    {'textDocument': {'uri': uri}}, identity)
                    client.send('exit', None)
                else:
                    if ending == 'broken':
                        client.process.stdin.write(b'Content-Length: nope\r\n\r\n')
                        client.process.stdin.flush()
                    client.process.stdin.close()
                assert client.process.wait(timeout=10) == 1, ending
                for identity in identities:
                    response = client.response(identity)
                    assert response.get('error', {}).get('code') == -32800, response
                assert gone(worker), ('worker survived disconnect', ending)
                assert not client.process.stderr.read()
                print('ok   %s answers every accepted request and reaps its worker' % ending)
            finally:
                if client.process.poll() is None:
                    for pid in children(client.process.pid):
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    client.process.kill()
                    client.process.wait()


if __name__ == '__main__':
    run(str(Path(sys.argv[1]).resolve()))
