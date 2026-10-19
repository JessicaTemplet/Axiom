#!/usr/bin/env python3
"""Keep external indirect callbacks in the assembled stack-bound graph."""
import argparse
import importlib.util
from pathlib import Path


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('tool', type=Path)
parser.add_argument('work', type=Path)
args = parser.parse_args()

spec = importlib.util.spec_from_file_location('axiom_report', args.tool.resolve())
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)
work = args.work.resolve() / 'stack-callbacks'
work.mkdir(parents=True, exist_ok=True)

# Replace only the source-to-IR step. stack_bound still invokes real
# opt and llc, reads the ELF object, and builds its production graph.
emitter = work / 'emit-fixture'
emitter.write_text('''#!/usr/bin/env python3
import argparse
import shutil

parser = argparse.ArgumentParser()
parser.add_argument('command', choices=['emit-llvm'])
parser.add_argument('--target', required=True)
parser.add_argument('source')
parser.add_argument('-o', required=True)
args = parser.parse_args()
shutil.copyfile(args.source, args.o)
''')
emitter.chmod(0o755)

common = '''target triple = "x86_64-unknown-linux-gnu"

@callback_slot = global ptr @callback

define i64 @root(ptr %fn, i64 %value) noinline {
  %result = call i64 %fn(i64 %value)
  ret i64 %result
}

'''
cases = [
    ('external-scalar', 'declare i64 @callback(i64)\n', False, ('i64', ('i64',))),
    ('external-varargs', 'declare i64 @callback(...)\n', False, None),
    ('in-object', '''define i64 @callback(i64 %value) noinline {
  %result = add i64 %value, 1
  ret i64 %result
}
''', True, ('i64', ('i64',))),
]

passed = 0
for opt in (0, 1):
    for name, callback, bounded, abi in cases:
        label = '%s O%d' % (name, opt)

        def require(condition, message):
            if not condition:
                raise SystemExit('FAIL %s: %s' % (label, message))

        source = work / ('%s-O%d.ll' % (name, opt))
        source.write_text(common + callback)
        case_work = work / ('%s-O%d' % (name, opt))
        case_work.mkdir(exist_ok=True)
        stack = report.stack_bound(str(emitter), str(source), 'linux-x86_64',
                                   opt, ['root'], str(case_work))
        result = stack['results']['root']
        facts = report.ir_facts(Path(stack['ir']).read_text())
        require('root' in stack['indirect_sites'], 'the indirect call disappeared')
        require('callback' in stack['indirect_targets'], 'the escaped callback was dropped')
        require('callback' in facts['function_abis'], 'the callback signature was not read')
        require(facts['function_abis'].get('callback') == abi,
                'the fixture no longer exercises its expected callback ABI')
        require(result['bounded'] == bounded, 'unexpected bound: %s' % result)
        require('callback' in result['path'], 'the callback is absent from the witness path')
        if bounded:
            require('callback' in stack['frames'], 'the in-object callback has no frame')
            require(result['bytes'] > 0, 'the bounded path has no stack charge')
            require(stack['undefined'] == [], 'the in-object graph has undefined targets')
            detail = 'bounded through its assembled frame'
        else:
            require('callback' not in stack['frames'], 'the external acquired an object frame')
            require('callback has no frame size' in result['reason'],
                    'the missing external frame is not the refusal reason')
            require(stack['undefined'] == ['callback'],
                    'undefined metadata lost the indirect external edge')
            detail = 'unbounded with missing-frame reason and undefined target'
        passed += 1
        print('ok %s: %s' % (label, detail))

print('stack callbacks: %d assembled cases passed' % passed)
