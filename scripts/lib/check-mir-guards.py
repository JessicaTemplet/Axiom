#!/usr/bin/env python3
"""Check signed division blocks in emitted LLVM, including safe remainder inputs."""
import re
import sys
from pathlib import Path


DIV = re.compile(r"\s*(\S+) = (sdiv|srem) i64 (.+), (.+)")
LABEL = re.compile(r"^([^\s:]+):\s*(?:;.*)?$")
CMP = re.compile(r"\s*(\S+) = icmp eq i64 (.+), -1")
SELECT = re.compile(r"\s*(\S+) = select i1 (\S+), i64 1, i64 (.+)")


def verify(lines):
    errors, divisions, prepared = [], [], []
    label, instructions = "", []

    def block():
        found = [(i, n, DIV.fullmatch(s)) for i, (n, s) in enumerate(instructions)
                 if DIV.fullmatch(s)]
        divisions.extend(n for _, n, _ in found)
        if not label.startswith("divok_"):
            if found:
                errors.append(f"line {found[0][1] + 1}: division outside a divok_ block")
            return
        if len(found) != 1:
            errors.append(f"{label}: expected exactly one signed division, found {len(found)}")
            return
        index, line, division = found[0]
        if index == 0 and division[2] == "sdiv":
            return
        comparison = CMP.fullmatch(instructions[0][1])
        selection = SELECT.fullmatch(instructions[1][1]) if index >= 2 else None
        if (division[2] == "srem" and index == 2 and comparison and selection
                and selection[2] == comparison[1]
                and selection[3] == comparison[2]
                and division[4] == selection[1]):
            prepared.append((instructions[0][0], instructions[1][0], line, comparison[2]))
        else:
            errors.append(f"line {line + 1}: unexpected instructions before signed division")

    for n, line in enumerate(lines):
        match = LABEL.fullmatch(line)
        if match or line.startswith("define ") or line == "}":
            block()
            label = match[1] if match else ""
            instructions = []
        elif line.strip() and not line.lstrip().startswith(";"):
            instructions.append((n, line))
    block()
    return errors, divisions, prepared


def main(path):
    lines = Path(path).read_text().splitlines()
    errors, divisions, prepared = verify(lines)
    if errors:
        print("\n".join(errors[:8]))
        return 1
    if not divisions or not prepared:
        print("no signed divisions or safe remainder preparations to test")
        return 1
    comparison, selection, remainder, denominator = prepared[0]
    mutants = [
        ("wrong comparison", comparison, lines[comparison].replace(", -1", ", 0")),
        ("unsafe selected divisor", selection, lines[selection].replace(", i64 1,", ", i64 0,")),
        ("bypassed preparation", remainder,
         lines[remainder].rsplit(", ", 1)[0] + ", " + denominator),
        ("missing division", divisions[0],
         re.sub(r"\b(sdiv|srem)\b", "add", lines[divisions[0]], count=1)),
    ]
    for name, index, replacement in mutants:
        mutant = lines.copy()
        assert mutant[index] != replacement, name
        mutant[index] = replacement
        if not verify(mutant)[0]:
            print(f"negative control accepted {name}")
            return 1
    mutant = lines.copy()
    mutant[remainder] = lines[remainder].rsplit(", ", 1)[0] + ", " + denominator
    del mutant[selection]
    del mutant[comparison]
    if not verify(mutant)[0]:
        print("negative control accepted a remainder with its preparation removed")
        return 1
    # Changing the labels leaves the division population untouched.
    mutant = [re.sub(r"^divok_", "unguarded_", line) for line in lines]
    if not verify(mutant)[0]:
        print("negative control accepted divisions outside divok_ blocks")
        return 1
    print(f"{len(divisions)} guarded signed divisions, {len(prepared)} exact remainder preparations; "
          "six corruptions refused")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
