"""Generate editable SPICE analyses from validated graphical form fields."""
import re


def token(value, label='SPICE value'):
    result = str(value)
    if not re.fullmatch(r'[A-Za-z0-9_.+\-]+', result):
        raise ValueError(f'{label} must be a single SPICE token')
    return result


def configure(source, analysis, parameters, directives=''):
    p = parameters or {}
    def v(name, default):
        return token(p.get(name, default), name)
    if analysis == 'op':
        command = '.op'
    elif analysis == 'dc':
        command = f'.dc {v("source", "VIN")} {v("start", 0)} {v("stop", 3.3)} {v("step", .01)}'
    elif analysis == 'ac':
        command = f'.ac dec {v("points", 40)} {v("start", 1)} {v("stop", "1Meg")}'
    elif analysis == 'tran':
        command = f'.tran {v("step", "2u")} {v("stop", "5m")}'
    elif analysis == 'noise':
        command = f'.noise v({v("output", "out")}) {v("source", "VIN")} dec {v("points", 40)} {v("start", 1)} {v("stop", "1Meg")}'
    else:
        raise ValueError('Supported analyses: op, dc, ac, tran, noise')
    lines = source.splitlines()
    retained = [line for line in lines if not re.match(r'^\s*\.(op|dc|ac|tran|noise|meas|measure|end)\b', line, re.I)]
    return '\n'.join(retained) + '\n' + command + '\n' + directives.strip() + '\n.end\n'
