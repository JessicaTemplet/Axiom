#!/usr/bin/env python3
"""Exercise worker cancellation, revisions and bounded queuing over stdio."""
import json
import os
from pathlib import Path
import queue
import runpy
import signal
import subprocess
import sys
import tempfile
import time

Client = runpy.run_path(str(Path(__file__).with_name('workspace-overlays.py')))['Client']


class RacingClient(Client):
    def __init__(self, compiler):
        super().__init__(compiler)
        self.responses = {}
        self.traces = []

    def receive(self, timeout=45):
        message = self.inbox.get(timeout=timeout)
        if isinstance(message, BaseException):
            raise message
        method = message.get('method')
        if method == 'textDocument/publishDiagnostics':
            self.publications.append(message['params'])
        elif method == '$/logTrace':
            self.traces.append(message['params']['message'])
        elif 'id' in message:
            assert message['id'] not in self.responses, ('duplicate response', message)
            self.responses[message['id']] = message
        else:
            raise AssertionError(message)
        return message

    def until(self, predicate, timeout=45):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.receive(max(0.001, deadline - time.monotonic()))

    def response(self, identity):
        self.until(lambda: identity in self.responses)
        return self.responses[identity]

    def request(self, method, params):
        self.serial += 1
        identity = 'race/%d' % self.serial
        self.send(method, params, identity)
        answer = self.response(identity)
        assert 'error' not in answer, answer
        return answer['result']

    def started(self, kind):
        expected = 'background %s started' % kind
        self.until(lambda: expected in self.traces)
        self.traces.remove(expected)

    def cancel(self, identity, code=-32800):
        self.send('$/cancelRequest', {'id': identity})
        answer = self.response(identity)
        assert answer.get('error', {}).get('code') == code, answer


def children(parent):
    # Process existence is independent of LSP trace and the implementation's
    # result packet. The worker is this server's only child process.
    rows = subprocess.check_output(['ps', '-axo', 'pid=,ppid='], text=True)
    return {int(pid) for pid, ppid in (line.split() for line in rows.splitlines())
            if int(ppid) == parent}


def freeze_worker(client):
    pids = children(client.process.pid)
    assert len(pids) == 1, ('expected a genuinely active worker', pids)
    pid = next(iter(pids))
    # Stop only this test's child. Killing a stopped worker also proves that
    # cancellation does not merely wait for the computation to finish.
    os.kill(pid, signal.SIGSTOP)
    return pid


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def open_source(client, uri, source, version):
    client.send('textDocument/didOpen', {'textDocument': {
        'uri': uri, 'languageId': 'axiom', 'version': version, 'text': source}})


def change_source(client, uri, source, version):
    client.send('textDocument/didChange', {'textDocument': {'uri': uri, 'version': version},
                                       'contentChanges': [{'text': source}]})


def run(compiler):
    if os.name != 'posix':
        print('skip background-analysis: native process and signal support required')
        return
    large = ''.join('(:: item%d Int)\n(fn (item%d) %d)\n' % (n, n, n)
                    for n in range(50000))
    small = '; current 😀\n(:: current Int)\n(fn (current) 42)\n'
    with tempfile.TemporaryDirectory(prefix='axiom-lsp-races-') as temporary:
        uri = (Path(temporary) / 'main.ax').as_uri()
        other = (Path(temporary) / 'other.ax').as_uri()
        client = RacingClient(compiler)
        try:
            client.request('initialize', {'trace': 'verbose'})
            open_source(client, uri, large, 1)
            client.started('analysis')
            obsolete = freeze_worker(client)
            # A queued request is cancellable while diagnostics are still
            # computing, without consuming or killing that analysis job.
            client.send('textDocument/documentSymbol', {'textDocument': {'uri': uri}}, 'queued/😀')
            client.cancel('queued/😀')
            assert not gone(obsolete), 'queued cancellation stopped the wrong job'

            # A new revision kills obsolete analysis before publication.
            change_source(client, uri, small, 2)
            client.started('analysis')
            client.until(lambda: any(p.get('version') == 2 for p in client.publications))
            assert gone(obsolete), 'obsolete analysis was not reaped'
            assert [p['version'] for p in client.publications if p['uri'] == uri] == [2]
            assert [s['name'] for s in client.request('textDocument/documentSymbol',
                    {'textDocument': {'uri': uri}})] == ['current']

            # Cancellation of one document's analysis must not lose the first
            # document when a different document is opened next.
            client.traces.clear()
            change_source(client, uri, large, 3)
            client.started('analysis')
            obsolete = freeze_worker(client)
            open_source(client, other, '(:: other Int)\n(fn (other) 1)\n', 1)
            client.started('analysis')
            client.until(lambda: any(p['uri'] == uri and p.get('version') == 3
                                     for p in client.publications)
                         and any(p['uri'] == other for p in client.publications))
            assert gone(obsolete)
            assert not client.latest(uri)['diagnostics'], client.latest(uri)
            client.traces.clear()

            # Active work is interrupted, not answered after it has completed.
            client.send('textDocument/documentSymbol', {'textDocument': {'uri': uri}}, 'active/😀')
            client.started('request')
            active = freeze_worker(client)
            client.cancel('active/😀')
            assert gone(active), 'cancellation acknowledged before kill and join'
            client.traces.clear()

            # A failed worker must answer its request and leave the session
            # usable, rather than dropping its pending id or publishing junk.
            client.send('textDocument/formatting', {'textDocument': {'uri': uri}}, 'failed-worker')
            client.started('request')
            failed = freeze_worker(client)
            os.kill(failed, signal.SIGKILL)
            answer = client.response('failed-worker')
            assert answer.get('error', {}).get('code') == -32603, answer
            assert gone(failed), 'failed worker was not reaped'
            client.traces.clear()

            # An accepted edit cancels an old request with ContentModified.
            client.send('textDocument/formatting', {'textDocument': {'uri': uri}}, 'old-format')
            client.started('request')
            stale = freeze_worker(client)
            change_source(client, uri, small, 4)
            answer = client.response('old-format')
            assert answer.get('error', {}).get('code') == -32801, answer
            assert gone(stale), 'stale request survived its replacement'
            client.until(lambda: any(p['uri'] == uri and p.get('version') == 4
                                     for p in client.publications))
            assert not client.latest(uri)['diagnostics'], client.latest(uri)

            # Unknown and completed cancellations are ignored. Repeated ids
            # below are distinct JSON strings; every response is checked once.
            client.send('$/cancelRequest', {'id': 'not-pending'})
            client.send('$/cancelRequest', {'id': 'active/😀'})
            identities = ['burst/%d' % n for n in range(96)]
            for identity in identities:
                client.send('textDocument/documentSymbol', {'textDocument': {'uri': uri}}, identity)
            client.until(lambda: all(identity in client.responses for identity in identities))
            for identity in identities:
                answer = client.responses[identity]
                assert 'error' not in answer, answer
                assert [s['name'] for s in answer['result']] == ['current'], answer
            assert [i for i in client.responses if i in identities] == identities
            assert [p['version'] for p in client.publications if p['uri'] == uri] == [2, 3, 4]
            client.close()
            assert not children(client.process.pid), 'shutdown left a worker behind'
        finally:
            if client.process.poll() is None:
                for pid in children(client.process.pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                client.process.kill()
                client.process.wait()

        # EOF must stop and reap an active worker as well as normal shutdown.
        client = RacingClient(compiler)
        try:
            client.request('initialize', {'trace': 'verbose'})
            open_source(client, uri, large, 1)
            client.started('analysis')
            worker = freeze_worker(client)
            client.process.stdin.close()
            assert client.process.wait(timeout=10) == 1
            assert gone(worker), 'EOF left the worker alive'
            assert not client.process.stderr.read()
        finally:
            if client.process.poll() is None:
                for pid in children(client.process.pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                client.process.kill()
                client.process.wait()
        # An exit without a preceding shutdown also interrupts active work.
        client = RacingClient(compiler)
        try:
            client.request('initialize', {'trace': 'verbose'})
            open_source(client, uri, large, 1)
            client.started('analysis')
            worker = freeze_worker(client)
            client.send('exit', {})
            assert client.process.wait(timeout=10) == 1
            assert gone(worker), 'exit left the worker alive'
            assert not client.process.stderr.read()
        finally:
            if client.process.poll() is None:
                for pid in children(client.process.pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                client.process.kill()
                client.process.wait()
        # Client libraries put a short deadline on shutdown. It must stop
        # a suspended worker and answer pending requests before that deadline.
        client = RacingClient(compiler)
        try:
            client.request('initialize', {'trace': 'verbose'})
            open_source(client, uri, large, 1)
            client.started('analysis')
            worker = freeze_worker(client)
            client.send('textDocument/documentSymbol', {'textDocument': {'uri': uri}}, 'shutdown-pending')
            client.send('shutdown', None, 'shutdown-now')
            assert client.response('shutdown-pending').get('error', {}).get('code') == -32800
            assert client.response('shutdown-now').get('result', 'missing') is None
            assert gone(worker), 'shutdown acknowledged before reaping its worker'
            client.send('textDocument/documentSymbol', {'textDocument': {'uri': uri}}, 'after-shutdown')
            assert client.response('after-shutdown').get('error', {}).get('code') == -32600
            client.send('exit', None)
            assert client.process.wait(timeout=10) == 0
            assert not client.process.stderr.read()
        finally:
            if client.process.poll() is None:
                for pid in children(client.process.pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                client.process.kill()
                client.process.wait()
    print('ok   active and queued cancellation, stale revisions, coalesced diagnostics, '
          'FIFO backpressure, worker failure recovery and shutdown/EOF/exit cleanup')


if __name__ == '__main__':
    run(str(Path(sys.argv[1]).resolve()))
