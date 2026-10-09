"""Runnable educational devices and explicitly PDK-dependent starting designs."""

AMPLIFIER = '''* Educational common-source amplifier; not a foundry device model
.model AFNMOS NMOS (LEVEL=1 VTO=0.7 KP=200u LAMBDA=0.02)
VDD vdd 0 3.3
VIN in 0 DC 1.2 SIN(1.2 0.01 1k) AC 1
RLOAD vdd out {RD}
M1 out in 0 0 AFNMOS W=10u L=1u
'''

PRE = '''Analog-factory educational amplifier - real ngspice transient
.param RD=3300
.include "netlists/amplifier.cir"
.tran 2u 5m
.meas tran vout_mean AVG v(out) FROM=1m TO=5m
.meas tran vout_pp PP v(out) FROM=1m TO=5m
.meas tran vin_pp PP v(in) FROM=1m TO=5m
.meas tran supply_current AVG i(VDD) FROM=1m TO=5m
.meas tran gain PARAM='vout_pp/vin_pp'
.meas tran power PARAM='-3.3*supply_current'
.end
'''

SKY130 = '''* SKY130 common-source amplifier; requires a prepared sky130A PDK
* Set model library path in testbenches/pre.cir before simulation.
VDD vdd 0 1.8
VIN in 0 DC 0.8 AC 1
RLOAD vdd out {RD}
X1 out in 0 0 sky130_fd_pr__nfet_01v8 w=2 l=0.5
'''

SKY_PRE = '''Analog-factory SKY130 amplifier - requires prepared PDK
* Replace this explicit path with your installed model library.
.lib "/REPLACE_WITH_PDK_ROOT/sky130A/libs.tech/ngspice/sky130.lib.spice" tt
.param RD=10000
.include "netlists/amplifier.cir"
.tran 2u 5m
.meas tran vout_mean AVG v(out) FROM=1m TO=5m
.end
'''

def tuning_workflow():
    types = ['specification', 'parameter', 'simulate', 'measure', 'condition', 'report']
    return {
        'schema_version': 1, 'name': 'Amplifier bias tuning',
        'nodes': [{'id': f'n{i}', 'type': kind, 'position': {'x': 40 + (i % 3) * 230, 'y': 40 + (i // 3) * 170}, 'config': {}} for i, kind in enumerate(types)],
        'edges': [{'source': f'n{i}', 'target': f'n{i+1}'} for i in range(5)],
        'limits': {'iterations': 8, 'runtime_seconds': 120},
        'tuning': {'parameter': 'RD', 'minimum': 1000, 'maximum': 10000,
                   'target': 1.5, 'tolerance': 0.05, 'metric': 'vout_mean',
                   'netlist': 'testbenches/pre.cir'},
    }


def files(template, config=None):
    if template == 'ota5':
        from .ota5_reference import materialize
        return materialize(config)
    if template not in ('amplifier', 'sky130'):
        raise ValueError('Template must be amplifier, sky130 or ota5')
    import json
    sky = template == 'sky130'
    return {
        'netlists/amplifier.cir': SKY130 if sky else AMPLIFIER,
        'testbenches/pre.cir': SKY_PRE if sky else PRE,
        'specifications.json': json.dumps({'metric': 'vout_mean', 'target': 1.5 if not sky else 0.9, 'tolerance': 0.05, 'units': 'V', 'conditions': {'temperature_c': 27, 'model': 'sky130_fd_pr' if sky else 'educational level-1'}}, indent=2),
        'schematics/README.txt': 'Create or import a schematic in xschem. Use the workspace launch action.\nThe educational netlist is runnable without a schematic editor.\nSchematic-to-netlist equivalence has not yet been validated.\n',
        'layout/README.txt': 'Create a matching layout in magic using the prepared PDK technology.\nRun DRC, extract, and LVS against the schematic. No layout or verification\nresult is supplied as a substitute for actual tool execution.\n',
        'reports/README.txt': 'Reports are generated from execution records. Original evidence is in .af/runs/.\n',
        'workflows/bias-tuning.json': json.dumps(tuning_workflow(), indent=2),
    }
