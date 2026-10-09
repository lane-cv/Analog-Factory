#!/usr/bin/env python3
"""Rebuild the OTA with the configured SKY130 Magic pcells and explicit routing.

Writes only a new output directory; the committed layout is never overwritten.
The wide, single-row layout prioritizes a readable reference over area/matching.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import os
from pathlib import Path
import re
import subprocess

PCELLS = '''if {[catch {
foreach {name flavor w} {nmos sky130_fd_pr__nfet_01v8 4 pmos sky130_fd_pr__pfet_01v8 8} {
 load $name
 box values 0 0 0 0
 set parameters [sky130::${flavor}_defaults]
 dict set parameters w $w
 dict set parameters l 2
 sky130::${flavor}_draw $parameters
 save $name
}
} error]} {puts stderr $::errorInfo; exit 1}
quit -noprompt
'''
CHECK = '''if {[catch {
load ota5
select top cell
expand
drc style drc(full)
drc ignore none
drc check
drc catchup
set count [drc listall count total]
puts "AF_DRC_COUNT=$count"
puts "AF_DRC_STYLE=[drc list style]"
puts "AF_DRC_WHY=[drc listall why]"
if {$count != 0} {error "Full local DRC did not pass"}
save ota5
puts "AF_GENERATION_COMPLETE=1"
} error]} {puts stderr $::errorInfo; exit 1}
quit -noprompt
'''


def generate(output, magic, pdk_path):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    pdk_path = Path(pdk_path).resolve()
    rc = pdk_path / 'libs.tech/magic' / (pdk_path.name + '.magicrc')
    env = {**os.environ, 'PDK_ROOT': str(pdk_path.parent), 'PDKPATH': str(pdk_path), 'PDK': pdk_path.name}
    def run(name, text):
        (output / name).write_text(text)
        result = subprocess.run([str(Path(magic).resolve()), '-dnull', '-noconsole', '-rcfile', str(rc), name],
                                cwd=output, env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=60)
        (output / (name + '.log')).write_text(result.stdout)
        if result.returncode:
            raise RuntimeError(f'Magic failed; see {output / (name + ".log")}')
        return result.stdout
    run('pcells.tcl', PCELLS)
    layers = defaultdict(list)
    # Saved magscale 1 2: 200 coordinates per micrometre in this PDK.
    devices = [
        ('nmos', 0, {'D': 'nmirror', 'G': 'inp', 'S': 'tail', 'B': 'vss'}),
        ('nmos', 2400, {'D': 'out', 'G': 'inn', 'S': 'tail', 'B': 'vss'}),
        ('pmos', 4800, {'D': 'nmirror', 'G': 'nmirror', 'S': 'vdd', 'B': 'vdd'}),
        ('pmos', 7200, {'D': 'out', 'G': 'nmirror', 'S': 'vdd', 'B': 'vdd'}),
        ('nmos', 9600, {'D': 'tail', 'G': 'bias', 'S': 'vss', 'B': 'vss'})]
    bus = {name: 2000 + index * 240 for index, name in enumerate(
        ['vss', 'vdd', 'tail', 'nmirror', 'out', 'inp', 'inn', 'bias'])}
    terminals = []
    def rect(layer, x1, y1, x2, y2):
        layers[layer].append((int(x1), int(y1), int(x2), int(y2)))
    def pad(layer, x, y, size):
        rect(layer, x - size // 2, y - size // 2, x + size // 2, y + size // 2)
    for kind, offset, nets in devices:
        source = (output / (kind + '.mag')).read_text()
        if 'magscale 1 2\n' not in source:
            raise ValueError('This routing uses the pinned SKY130 5 nm Magic grid')
        ports, layer = {}, None
        for line in source.splitlines():
            if line.startswith('<<'):
                layer = line.split()[1]
            elif line.startswith('rect ') and layer != 'checkpaint':
                x1, y1, x2, y2 = map(int, line.split()[1:])
                rect(layer, x1 + offset, y1, x2 + offset, y2)
            elif line.startswith('rlabel '):
                words = line.split()
                ports[words[-1]] = (int(words[2]) + offset, int(words[3]))
        if set(ports) != {'D', 'G', 'S', 'B'}:
            raise ValueError('PDK transistor ports changed; review the generator')
        for name, (x, y) in ports.items():
            net = nets[name]
            # Bulk guard-ring contacts are local interconnect. Bring them to
            # metal1 and move the vertical riser clear of the gate column.
            if name == 'B':
                pad('viali', x, y, 34)
                pad('metal1', x, y, 72)
                rect('metal1', x, y - 36, offset + 736, y + 36)
                x = offset + 700
            pad('metal1', x, y, 80)
            pad('via1', x, y, 56)
            pad('metal2', x, y, 80)
            rect('metal2', x - 40, y, x + 40, bus[net] + 40)
            pad('metal2', x, bus[net], 100)
            pad('via2', x, bus[net], 64)
            pad('metal3', x, bus[net], 100)
            terminals.append((net, x))
    for net, y in bus.items():
        right = max(x for name, x in terminals if name == net)
        rect('metal3', -500, y - 50, right + 50, y + 50)
    lines = ['magic', 'tech sky130A', 'magscale 1 2', 'timestamp 0']
    for layer, rectangles in layers.items():
        lines.append('<< ' + layer + ' >>')
        lines.extend('rect ' + ' '.join(map(str, box)) for box in rectangles)
    lines.append('<< labels >>')
    for index, (net, y) in enumerate(bus.items()):
        lines.append(f'rlabel metal3 -400 {y} -400 {y} 0 {net}')
        if net not in ('tail', 'nmirror'):
            lines.append(f'port {index + 1} nsew')
    lines.append('<< end >>')
    layout = output / 'ota5.mag'
    layout.write_text('\n'.join(lines) + '\n')
    log = run('check.tcl', CHECK)
    if 'AF_DRC_COUNT=0' not in log or 'AF_GENERATION_COMPLETE=1' not in log:
        raise RuntimeError('Magic did not confirm full local DRC and complete generation')
    layout.write_text(re.sub(r'^timestamp \d+$', 'timestamp 0', layout.read_text(), flags=re.M))
    return layout


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--magic', required=True)
    parser.add_argument('--pdk-path', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(generate(args.output, args.magic, args.pdk_path))
