#!/usr/bin/env python3
"""Exercise the IRQ stack reader against assembled register-save mutants."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

spec = importlib.util.spec_from_file_location('axiom_report', sys.argv[1])
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)
stack = json.loads(Path(sys.argv[2]).read_text())['stack']
original = Path(stack['obj'])
source = original.with_suffix('.ll').read_text()
frame = stack['irq_stub']['frame_bytes']
assert frame > 16
sub = 'module asm "sub sp, sp, #%d"' % frame
add = 'module asm "add sp, sp, #%d"' % frame
call = 'module asm "bl __axiom_irq_dispatch"'
assert source.count(sub) == source.count(add) == source.count(call) == 1
cases = [
    ('original', source, frame),
    ('larger frame', source.replace(sub, sub.replace(str(frame), str(frame + 16))).replace(add, add.replace(str(frame), str(frame + 16))), frame + 16),
    ('save beyond frame', source.replace(sub, sub.replace(str(frame), str(frame - 16))).replace(add, add.replace(str(frame), str(frame - 16))), None),
    ('mismatched restore', source.replace(add, add.replace(str(frame), str(frame + 16))), None),
    ('hidden stack adjustment', source.replace(sub, sub + '\nmodule asm "sub sp, sp, #16"'), None),
    ('unknown call', source.replace(call, 'module asm "bl __axiom_cpu_exception"'), None),
]
with tempfile.TemporaryDirectory(prefix='axiom-irq-mutants.') as work:
    base = Path(work)
    for i, (name, ir, expected) in enumerate(cases):
        ll, obj = base / ('case%d.ll' % i), base / ('case%d.o' % i)
        ll.write_text(ir)
        p = subprocess.run(['llc', str(ll), '-filetype=obj', '-o', str(obj), '-O1'], capture_output=True, text=True)
        assert p.returncode == 0, (name, p.stderr)
        try:
            got = report.irq_stub_frame(str(obj), report.Elf(str(obj)))
        except report.ReportError:
            assert expected is None, name
        else:
            assert expected is not None and got['frame_bytes'] == expected, (name, got)
        print('ok', name)
print('IRQ stack reader: %d assembled cases passed' % len(cases))
