#!/usr/bin/env python3
"""Foreign item visibility and module provenance, including failing controls."""
from pathlib import Path
import argparse
import importlib.util
import json
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--axiom', required=True)
parser.add_argument('--work', required=True)
args = parser.parse_args()
work = Path(args.work).resolve() / 'foreign-resolution'
work.mkdir(parents=True, exist_ok=True)
(work / 'mods').mkdir(exist_ok=True)


def write(name, text):
    path = work / name
    path.write_text(text)
    return path


write('mods/Items.ax', '''(pub extern "assurance_demo"
  (add :: (-> Int Int Int) (symbol "assurance_left_add"))
  (product :: (-> Int Int Int) (symbol "assurance_right_add")))
(extern "assurance_demo"
  (secret :: (-> Int Int Int) (symbol "assurance_left_add")))
(pub :: viaPrivate (-> Int Int Int))
;@axiom:effect(io)
;@axiom:effect(unsafe)
(pub fn (viaPrivate a b) (secret a b))
(pub :: viaOwn (-> Int Int Int))
;@axiom:effect(io)
;@axiom:effect(unsafe)
(pub fn (viaOwn a b) (add a b))
''')
write('mods/Middle.ax', '''(import mods.Items (add))
(pub :: viaMiddle (-> Int Int Int))
;@axiom:effect(io)
;@axiom:effect(unsafe)
(pub fn (viaMiddle a b) (add a b))
''')
write('mods/Other.ax', '''(pub extern "assurance_demo"
  (add :: (-> Int Int Int) (symbol "assurance_right_add")))
''')
write('mods/Ambiguous.ax', '''(import mods.Items (add))
(import mods.Other (add))
(pub :: viaAmbiguous (-> Int Int Int))
;@axiom:effect(io)
;@axiom:effect(unsafe)
(pub fn (viaAmbiguous a b) (add a b))
''')
write('mods/Unimported.ax', '''(pub :: noImportedAdd (-> Int Int Int))
;@axiom:effect(io)
;@axiom:effect(unsafe)
(pub fn (noImportedAdd a b) (add a b))
''')

write('mods/DefaultSymbol.ax', '(pub extern "assurance_demo" (add :: (-> Int Int Int)))\n')
write('mods/DefaultMiddle.ax', '''(import mods.DefaultSymbol (add))
(pub :: viaDefault (-> Int Int Int))
;@axiom:effect(io)
;@axiom:effect(unsafe)
(pub fn (viaDefault a b) (add a b))
''')

root_extern = '''(extern "assurance_demo"
  (add :: (-> Bool Bool Bool) (symbol "assurance_bool_add")))
'''


def program(imports, body='0', declarations=''):
    tags = ';@axiom:effect(io)\n' if body != '0' else ''
    if any(token in body for token in ('(add ', '(product ', '(secret ', '::add ', '::product ', '::secret ')) and '(fn (add ' not in declarations:
        tags += ';@axiom:effect(unsafe)\n'
    return imports + '\n' + declarations + '\n(:: main Int)\n' + tags + '(fn (main)\n  ' + body + ')\n'


cases = [
    ('selective', '(import mods.Items (add))', '(if (== (add 20 22) 42) 0 1)'),
    ('qualified', '(import mods.Items (add))', '(if (== (mods.Items::add 20 22) 42) 0 1)'),
    ('repeat-forward', '(import mods.Items (add))\n(import mods.Items (product))',
     '(if (&& (== (add 20 22) 42) (== (product 6 7) 42)) 0 1)'),
    ('repeat-reverse', '(import mods.Items (product))\n(import mods.Items (add))',
     '(if (&& (== (add 20 22) 42) (== (product 6 7) 42)) 0 1)'),
    ('repeat-full', '(import mods.Items (add))\n(import mods.Items)',
     '(if (&& (== (product 6 7) 42) (== (viaPrivate 20 22) 42)) 0 1)'),
    ('full-repeat', '(import mods.Items)\n(import mods.Items (add))',
     '(if (&& (== (product 6 7) 42) (== (viaPrivate 20 22) 42)) 0 1)'),
    ('private-wrapper', '(import mods.Items (viaPrivate))', '(if (== (viaPrivate 20 22) 42) 0 1)'),
    ('own-wins', '(import mods.Items)\n(import mods.Other)',
     '(if (== (viaOwn 20 22) 42) 0 1)'),
]
positives = [(name, write(name + '.ax', program(imports, body))) for name, imports, body in cases]
transitive = write('transitive.ax', program('(import mods.Middle)',
    '(if (&& (add true false) (== (viaMiddle 20 22) 42)) 0 1)', root_extern))
positives.append(('transitive', transitive))
positives.append(('transitive-default-symbol', write('transitive-default-symbol.ax', program(
    '(import mods.DefaultMiddle)', '(if (&& (add true false) (== (viaDefault 20 22) 42)) 0 1)', root_extern))))
root_fn = '''(:: add (-> Bool Bool Bool))
(fn (add a b) (|| a b))
'''
positives.append(('transitive-function', write('transitive-function.ax', program(
    '(import mods.Middle)', '(if (&& (add true false) (== (viaMiddle 20 22) 42)) 0 1)', root_fn))))

negative_specs = [
    ('private-bare', '(import mods.Items)', '(secret 20 22)', '', 'AX3023'),
    ('private-qualified', '(import mods.Items)', '(mods.Items::secret 20 22)', '', 'AX3023'),
    ('private-selective', '(import mods.Items (secret))', '0', '', 'AX3023'),
    ('private-repeat', '(import mods.Items (add))\n(import mods.Items (secret))', '0', '', 'AX3023'),
    ('filtered-bare', '(import mods.Items (add))', '(product 6 7)', '', 'AX3023'),
    ('filtered-qualified', '(import mods.Items (add))', '(mods.Items::product 6 7)', '', 'AX3023'),
    ('missing-selective', '(import mods.Items (absent))', '0', '', 'AX3023'),
    ('ambiguous', '(import mods.Ambiguous)', '0', root_extern, 'AX3014'),
    ('root-capture', '(import mods.Unimported)', '0', root_extern, 'AX3001'),
    ('root-function-capture', '(import mods.Unimported)', '0', root_fn, 'AX3001'),
    ('ambiguous-root-function', '(import mods.Ambiguous)', '0', root_fn, 'AX3014'),
]
checks = 0


def run(*command):
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=work, timeout=120)


def require(condition, label, result=None):
    global checks
    if not condition:
        detail = '' if result is None else '\n' + result.stdout[:2500] + result.stderr[:2500]
        raise AssertionError(label + detail)
    checks += 1
    print('ok   foreign resolution: ' + label)


try:
    for name, path in positives:
        result = run(args.axiom, '--diagnostic-format=ai', 'check', str(path))
        require(result.returncode == 0, name + ' checks', result)
    for name, imports, body, declarations, code in negative_specs:
        path = write(name + '.ax', program(imports, body, declarations))
        result = run(args.axiom, '--diagnostic-format=ai', 'check', str(path))
        require(result.returncode != 0 and ('E ' + code + ' ') in result.stderr + result.stdout,
                name + ' is refused with ' + code, result)

    # The independent C bodies distinguish each linker symbol at run time.
    cfile = write('foreign.c', '''#include <stdint.h>
int64_t assurance_left_add(int64_t a, int64_t b) { return a + b; }
int64_t assurance_right_add(int64_t a, int64_t b) { return a * b; }
int64_t assurance_bool_add(int64_t a, int64_t b) { return a || b; }
''')
    result = run('cc', '-c', str(cfile), '-o', str(work / 'foreign.o'))
    require(result.returncode == 0, 'independent native evidence builds', result)
    default_c = write('default-symbol.c', '#include <stdint.h>\nint64_t add(int64_t a, int64_t b) { return a + b; }\n')
    result = run('cc', '-c', str(default_c), '-o', str(work / 'default-symbol.o'))
    require(result.returncode == 0, 'default linker symbol evidence builds', result)
    result = run('ar', 'rcs', str(work / 'libassurance_demo.a'), str(work / 'foreign.o'),
                 str(work / 'default-symbol.o'))
    require(result.returncode == 0, 'foreign evidence archive builds', result)
    for name, path in positives:
        for level in ('0', '2'):
            binary = work / (name + '-O' + level)
            result = run(args.axiom, '--diagnostic-format=ai', 'build', str(path), '--opt', level,
                         '--link-search', str(work), '-o', str(binary))
            require(result.returncode == 0, name + ' builds at O' + level, result)
            result = run(str(binary))
            require(result.returncode == 0, name + ' runs at O' + level, result)

    repo = Path(__file__).resolve().parents[2]
    report = repo / 'scripts' / 'axiom-report.py'
    spec = importlib.util.spec_from_file_location('axiom_report_probe', report)
    report_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report_module)
    source = report_module.load_source_graph(args.axiom, str(transitive), '')
    require(set(source['mods.Middle$viaMiddle'].calls) == {'mods.Items$add'},
            'transitive call evidence retains the defining module')
    for exceptions, code, refused in [([], 1, {'add', 'mods.Items$add'}),
            (['--allow-foreign', 'add'], 1, {'mods.Items$add'}),
            (['--allow-foreign', 'mods.Items$add'], 1, {'add'}),
            (['--allow-foreign', 'add', '--allow-foreign', 'mods.Items$add'], 0, set())]:
        result = run(sys.executable, str(report), '--axiom', args.axiom, '--format', 'json',
                     str(transitive), '--profile', 'restricted', *exceptions)
        require(result.returncode == code, 'transitive restricted report exit ' + str(code), result)
        evidence = json.loads(result.stdout)
        actual = {r['path'][-1] for r in evidence['refusals'] if r['rule'] == 'RP-3'}
        require(actual == refused, 'reviewed exception preserves exact foreign identity')
    # Select a transitive source root without main: unrelated root externs are not authorised.
    selected = write('selected.ax', program('(import mods.Middle)', '0', root_extern))
    result = run(sys.executable, str(report), '--axiom', args.axiom, '--format', 'json',
                 str(selected), '--profile', 'restricted', '--root', 'mods.Middle$viaMiddle',
                 '--allow-foreign', 'mods.Items$add')
    require(result.returncode == 0, 'selected transitive root uses its reviewed foreign item', result)
    evidence = json.loads(result.stdout)
    require(not evidence['refusals'], 'selected transitive root has no unreviewed foreign calls')
except (AssertionError, subprocess.TimeoutExpired, KeyError, json.JSONDecodeError) as error:
    print('FAIL foreign resolution: ' + str(error), file=sys.stderr)
    sys.exit(1)
print('foreign-resolution checks=' + str(checks))
