#!/usr/bin/env python3
"""Reuse a successful CI gate only when its recorded inputs still match."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

SCHEMA = 1
MAX_AGE = 7 * 24 * 60 * 60
KNOWN_TEST_ROOTS = frozenset('agent axir axqlite compat crypto ddc diagnostics docs embedded ffi fmt frontend fuzz litmus lsp mir net obfuscation parallel profile region repl replcomp selfhost stdlib tailpos testrunner tools'.split())
# Reviewed fixture boundaries. Compiler sources and shared helpers remain
# inputs. Unlisted gates and new test directories use the whole tree.
FIXTURES = {
    'check-axqlite.sh': {'axqlite'},
    'check-contracts.sh': {'diagnostics'},
    'check-crypto.sh': {'crypto'},
    'check-diagnostic-coverage.sh': {'diagnostics'},
    'check-diagnostics.sh': {'diagnostics', 'selfhost', 'stdlib'},
    'check-obfuscation.sh': {'obfuscation'},
    'check-render-selfhost.sh': {'diagnostics', 'selfhost'},
    'check-repl-selfhost.sh': {'repl'},
    'check-self-host.sh': {'selfhost'},
    'check-stdlib-selfhost.sh': {'selfhost', 'stdlib'},
    'check-tail-position.sh': {'tailpos'},
    'check-test-runner.sh': {'testrunner'},
    'check-vscode.sh': {'lsp'},
}
PROSE_INDEPENDENT = {'check-self-host.sh', 'check-stdlib-selfhost.sh',
                     'check-diagnostics.sh', 'check-render-selfhost.sh',
                     'check-vscode.sh'}
ROOT_PROSE = {'README.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md'}

# Artifact producers and cache/coverage audits always run.
ALWAYS_RUN = {'check-ci-coverage.sh', 'check-gate-lib.sh', 'check-macro-demand.sh',
              'check-ffi.sh', 'check-windows-hello.sh', 'check-bootstrap.sh',
              'check-poisoned-alloc.sh', 'check-seed-provenance.sh',
              'check-seed-lineage.sh', 'check-seed-supply-chain.sh',
              'check-reproducible.sh', 'check-replcomp.sh'}


def tracked_paths(root):
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=root, check=True, stdout=subprocess.PIPE)
    return sorted(set(os.fsdecode(p) for p in result.stdout.split(b'\0') if p))


def input_paths(root, gate):
    paths = tracked_paths(root)
    selected = FIXTURES.get(gate)
    if selected is None:
        return paths
    # A new reference outside the reviewed boundary disables selective reuse.
    mentioned = set(re.findall(r'tests/([a-zA-Z0-9_-]+)/', (root / 'scripts' / gate).read_text()))
    if not mentioned <= selected:
        return paths
    gates = {gate}
    todo = [gate]
    while todo:
        current = todo.pop()
        source = (root / 'scripts' / current).read_text()
        source = '\n'.join(line for line in source.splitlines() if not line.lstrip().startswith('#'))
        if re.search(r'check-[$*{]|gate_prose_docs', source) or (
                gate in PROSE_INDEPENDENT and re.search(
                    r'docs/|[\"\']docs[\"\']|README\.md|CHANGELOG\.md|CONTRIBUTING\.md|SECURITY\.md', source)):
            return paths
        for dependency in re.findall(r'check-[a-z0-9-]+\.sh', source):
            if dependency not in gates and (root / 'scripts' / dependency).is_file():
                gates.add(dependency)
                todo.append(dependency)
    def needed(p):
        if p.startswith('scripts/check-') and p.endswith('.sh'):
            return Path(p).name in gates
        if gate in PROSE_INDEPENDENT and (p in ROOT_PROSE or p.startswith('docs/')):
            return False
        if p.startswith('tests/') and len(p.split('/')) >= 3:
            return p.split('/')[1] not in KNOWN_TEST_ROOTS or p.split('/')[1] in selected
        return True
    return [p for p in paths if needed(p)]


def add(h, label, data):
    label = os.fsencode(label)
    h.update(len(label).to_bytes(8, 'big') + label)
    h.update(len(data).to_bytes(8, 'big') + data)


def file_hash(h, root, name):
    p = root / name
    if p.is_symlink():
        add(h, name + ':link', os.fsencode(os.readlink(p)))
    if p.is_file():
        add(h, name + ':mode', str(p.stat().st_mode & 0o777).encode())
        add(h, name, p.read_bytes())
    else:
        add(h, name, b'<missing>')


def tool_context():
    result = {'platform': [platform.system(), platform.machine(), platform.release()],
              'image': [os.environ.get('ImageOS'), os.environ.get('ImageVersion')],
              'tools': {}}
    for tool in ('opt', 'llc', 'llvm-extract', 'llvm-readobj', 'llvm-objdump',
                 'llvm-nm', 'cc', 'clang', 'ld.lld', 'lld-link', 'ar', 'nm',
                 'python3', 'node', 'npm', 'cargo', 'rustc', 'rustfmt'):
        path = shutil.which(tool)
        if path is None:
            result['tools'][tool] = None
            continue
        r = subprocess.run([path, '--version'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=15)
        result['tools'][tool] = [str(Path(path).resolve()), r.returncode,
                                 r.stdout.decode('utf-8', errors='replace'),
                                 hashlib.sha256(Path(path).resolve().read_bytes()).hexdigest()]
    for path in ('tree-sitter-axiom/node_modules/tree-sitter-cli/tree-sitter',):
        p = Path(__file__).resolve().parent.parent / path
        result['tools'][path] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    return result


def fingerprint(root, gate, args, context):
    h = hashlib.sha256()
    add(h, 'schema', str(SCHEMA).encode())
    add(h, 'command', json.dumps([gate, *args]).encode())
    add(h, 'toolchain', json.dumps(context, sort_keys=True).encode())
    env = {k: v for k, v in os.environ.items()
           if (k.startswith(('AXIOM_', 'RUST', 'CARGO_', 'LLVM', 'LC_')) or
               k in ('LANG', 'CC', 'CXX', 'CI')) and
           k not in ('AXIOM_CI_CACHE', 'AXIOM_CI_FRESH')}
    # Gate-specific switches such as SELF_TEST also affect the verdict.
    source = (root / 'scripts' / gate).read_text()
    helper = root / 'scripts/lib/gate.sh'
    if helper.is_file():
        source += helper.read_text()
    names = set(re.findall(r'\$\{?([A-Z_][A-Z0-9_]*)', source))
    for name in names - {'AXIOM_CI_CACHE', 'AXIOM_CI_FRESH'}:
        env[name] = os.environ.get(name)
    add(h, 'environment', json.dumps(env, sort_keys=True).encode())
    if gate not in FIXTURES:
        # Unreviewed gates can inspect Git history, not just source bytes.
        for args in (['rev-parse', 'HEAD'], ['ls-files', '--stage']):
            r = subprocess.run(['git', *args], cwd=root, check=True, stdout=subprocess.PIPE)
            add(h, 'git:' + ' '.join(args), r.stdout)
    for p in input_paths(root, gate):
        file_hash(h, root, p)
    for p in sorted({'.axiom-bin/axiom', '.axiom-shared/axc',
                     os.environ.get('AXIOM') or '.axiom-bin/axiom',
                     os.environ.get('AXIOM_AXC') or '.axiom-shared/axc'}):
        file_hash(h, root, p)
    return h.hexdigest()


def valid_record(path, key, gate, now):
    try:
        r = json.loads(path.read_text())
        return (r['schema'] == SCHEMA and r['key'] == key and r['gate'] == gate and
                r['result'] == 'passed' and
                re.fullmatch(r'[0-9]+', str(r['run_id'])) is not None and
                re.fullmatch(r'[0-9a-f]{40}', r['sha']) is not None and
                0 <= now - r['completed_at'] < MAX_AGE)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def run(root, command):
    gate = Path(command[0]).name
    cache = os.environ.get('AXIOM_CI_CACHE')
    if not cache or gate in ALWAYS_RUN:
        return subprocess.run(command, cwd=root).returncode
    try:
        context = tool_context()
        key = fingerprint(root, gate, command[1:], context)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print(f'ci: cannot identify inputs; running {gate}: {e}', flush=True)
        return subprocess.run(command, cwd=root).returncode
    record = Path(cache) / f'{key}.json'
    if os.environ.get('AXIOM_CI_FRESH') != '1' and valid_record(record, key, gate, time.time()):
        proof = json.loads(record.read_text())
        message = f"Reused {gate}: unchanged inputs; passed in run {proof['run_id']} at {proof['sha'][:12]}."
        print(message, flush=True)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a') as out:
                out.write(message + '\n\n')
        return 0
    # An expired/corrupt success cannot survive a subsequent failure.
    try:
        record.unlink(missing_ok=True)
    except OSError as e:
        print(f'ci: cannot update cached result; running {gate}: {e}', flush=True)
        return subprocess.run(command, cwd=root).returncode
    rc = subprocess.run(command, cwd=root).returncode
    try:
        # A gate that leaves its inputs changed earns no reusable record.
        if rc == 0 and fingerprint(root, gate, command[1:], context) != key:
            print(f'ci: {gate} changed its inputs; success will run again', flush=True)
            return 0
        record.parent.mkdir(parents=True, exist_ok=True)
        proof = dict(schema=SCHEMA, key=key, gate=gate, result='passed' if rc == 0 else 'failed',
                     sha=os.environ.get('GITHUB_SHA', ''),
                     run_id=os.environ.get('GITHUB_RUN_ID', 'local'), completed_at=time.time())
        with tempfile.NamedTemporaryFile('w', dir=record.parent, delete=False) as out:
            json.dump(proof, out)
        os.replace(out.name, record)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print(f'ci: {gate} finished but its result could not be saved: {e}', flush=True)
    return rc


if __name__ == '__main__':
    if len(sys.argv) < 2 or not re.fullmatch(r'\./scripts/check-[a-z0-9-]+\.sh', sys.argv[1]):
        sys.exit('usage: ci-gate.py ./scripts/check-NAME.sh [args]')
    root = Path(__file__).resolve().parent.parent
    rc = run(root, sys.argv[1:])
    sys.exit(rc if rc >= 0 else 128 - rc)
