#!/usr/bin/env python3
"""Queued document changes collapse to the newest one per document.

While a worker is busy and a request is already queued, a stream of
edits for one document must not each earn a full analysis: only the
newest queued text is checked, no diagnostics are published for the
superseded versions, and a queued close drops the document's queued
edits. The worker is frozen with SIGSTOP so the queue's shape does
not depend on timing.
"""
import os
from pathlib import Path
import runpy
import signal
import sys
import tempfile

_here = Path(__file__)
_bg = runpy.run_path(str(_here.with_name('background-analysis.py')))
RacingClient = _bg['RacingClient']
freeze_worker = _bg['freeze_worker']
gone = _bg['gone']
open_source = _bg['open_source']
change_source = _bg['change_source']


def versions(client, uri):
    return [p.get('version') for p in client.publications if p['uri'] == uri]


def run(compiler):
    if os.name != 'posix':
        print('skip queued-changes: native process and signal support required')
        return
    large = ''.join('(:: item%d Int)\n(fn (item%d) %d)\n' % (n, n, n)
                    for n in range(50000))
    bad = '(:: current Int)\n(fn (current) (missing 1))\n'
    good = '(:: current Int)\n(fn (current) 42)\n'
    with tempfile.TemporaryDirectory(prefix='axiom-lsp-queued-') as temporary:
        uri = (Path(temporary) / 'main.ax').as_uri()
        client = RacingClient(compiler)
        try:
            client.request('initialize', {'trace': 'verbose'})
            open_source(client, uri, large, 1)
            client.started('analysis')
            worker = freeze_worker(client)

            # A request queues behind the frozen analysis; two edits
            # queue behind the request. The second replaces the first.
            client.send('textDocument/documentSymbol', {'textDocument': {'uri': uri}}, 'queued/symbols')
            change_source(client, uri, bad, 2)
            change_source(client, uri, good, 3)
            os.kill(worker, signal.SIGCONT)

            client.until(lambda: any(v == 3 for v in versions(client, uri)))
            assert versions(client, uri) == [1, 3], versions(client, uri)
            latest = next(p for p in reversed(client.publications) if p['uri'] == uri)
            assert latest['diagnostics'] == [], latest
            symbols = client.response('queued/symbols')
            assert 'result' in symbols, symbols

            # A queued close drops the document's queued edits: the
            # superseded bad text is never analysed or published.
            client.traces.clear()
            change_source(client, uri, large, 4)
            client.started('analysis')
            worker = freeze_worker(client)
            client.send('textDocument/documentSymbol', {'textDocument': {'uri': uri}}, 'queued/before-close')
            change_source(client, uri, bad, 5)
            client.send('textDocument/didClose', {'textDocument': {'uri': uri}})
            os.kill(worker, signal.SIGCONT)
            client.response('queued/before-close')
            client.until(lambda: any(
                p['uri'] == uri and not p['diagnostics'] and p.get('version') is None
                for p in client.publications))
            assert 5 not in versions(client, uri), versions(client, uri)
            assert 2 not in versions(client, uri), versions(client, uri)

            # Reopening starts a fresh version sequence and answers.
            open_source(client, uri, good, 1)
            client.until(lambda: versions(client, uri)[-1] == 1)
            assert client.request('textDocument/documentSymbol',
                                  {'textDocument': {'uri': uri}})[0]['name'] == 'current'
            client.request('shutdown', {})
            client.send('exit', {})
            assert client.process.wait(timeout=10) == 0
        finally:
            if client.process.poll() is None:
                client.process.kill()
                client.process.wait()
    print('ok   queued changes collapse to the newest per document, '
          'and a queued close drops queued edits')


if __name__ == '__main__':
    run(str(Path(sys.argv[1]).resolve()))
