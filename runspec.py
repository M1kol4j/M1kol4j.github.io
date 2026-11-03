#!/usr/bin/env python3
"""Drafts a ```run``` spec block for a DISPATCH run from its params.nml.

  ./runspec.py                   print the blank template (run-template.md)
  ./runspec.py <run data dir>    print the block filled from <dir>/params.nml

Paste the output into content/<project>/index.md and complete it: machine,
purpose, seed field, corona and anything else the namelist does not record.
Lines left empty are not shown on the page.
"""
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / 'run-template.md'
TOKEN = re.compile(r"""\s*(?:(\d+)\*)?('(?:[^']|'')*'?|"[^"]*"?|[^,\s]+)\s*,?""")
X = ' × '
BC = {3: 'characteristic (CBC)'}       # boundaries_mod codes; the others are left as numbers
SUP = str.maketrans('-0123456789', '⁻⁰¹²³⁴⁵⁶⁷⁸⁹')


# ------------------------------------------------------------ namelist file

def convert(tok):
    if tok[0] in '\'"':
        return tok.strip('\'"').rstrip()
    if tok.upper() in ('T', '.TRUE.'):
        return True
    if tok.upper() in ('F', '.FALSE.'):
        return False
    for kind in (int, float):
        try:
            return kind(tok.replace('D', 'E').replace('d', 'e'))
        except ValueError:
            pass
    return tok


def values(text):
    out = []
    for m in TOKEN.finditer(text):
        if m.group(2):
            out += [convert(m.group(2))] * int(m.group(1) or 1)
    return out


def read_nml(path):
    """{group: {key: [values]}}, lower case; handles n*value repeats and continuation lines."""
    groups, cur, key = {}, None, None
    for line in Path(path).read_text(errors='replace').splitlines():
        s = line.strip()
        if s.startswith('&'):
            cur, key = groups.setdefault(s[1:].split()[0].lower(), {}), None
        elif s.startswith('/'):
            cur = None
        elif cur is not None and s:
            m = re.match(r'([A-Za-z_][\w%]*)\s*=\s*(.*)$', s)
            if m:
                key = m.group(1).lower()
                cur[key] = values(m.group(2))
            elif key:
                cur[key] += values(s)
    return groups


class Nml:
    def __init__(self, path):
        self.g = read_nml(path)

    def has(self, group):
        return group in self.g

    def get(self, group, key, default=None):
        v = self.g.get(group, {}).get(key)
        return v[0] if v else default

    def all(self, group, key):
        return self.g.get(group, {}).get(key, [])

    def on(self, group):
        return self.get(group, 'on') is True


# ------------------------------------------------------------- formatting

def num(x, digits=5):
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e6:
        x = int(x)
    s = str(x) if isinstance(x, int) else f'{x:.{digits}g}'
    return s.replace('-', '−')


def sci(x):
    m, _, e = f'{x:.3e}'.partition('e')
    m = m.rstrip('0').rstrip('.')
    return m if int(e) == 0 else f'{m}{X}10{str(int(e)).translate(SUP)}'


def clock(seconds):
    s = round(seconds)
    if s < 60:
        return f'{s} s'
    if s < 3600:
        return f'{s // 60} min' + (f' {s % 60} s' if s % 60 else '')
    m = (s + 30) // 60
    return f'{m // 60} h {m % 60:02d} min'


def km(mm):
    return f'{mm * 1000:.4g} km'


def bounds(llc, urc):
    """Region bounds in Mm, leaving out the open (|x| >= 999) sides."""
    parts = []
    for ax, lo, hi in zip('xyz', llc, urc):
        lo_open, hi_open = lo <= -999, hi >= 999
        if lo_open and hi_open:
            continue
        if lo_open:
            parts.append(f'{ax} ≤ {num(hi)} Mm')
        elif hi_open:
            parts.append(f'{ax} ≥ {num(lo)} Mm')
        else:
            parts.append(f'{num(lo)} ≤ {ax} ≤ {num(hi)} Mm')
    return ', '.join(parts) or 'the whole box'


def snapshot_info(run):
    """(number, time, bytes) of the newest snapshot that has a snapshot.nml."""
    cands = []
    try:
        cands.append(int((run / 'last.snap').read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    cands += sorted((int(d.name) for d in run.iterdir() if d.name.isdigit() and d.is_dir()), reverse=True)
    for n in cands:
        d = run / f'{n:05d}'
        if (d / 'snapshot.nml').exists():
            groups = read_nml(d / 'snapshot.nml')
            time = next((g['time'][0] for g in groups.values() if g.get('time')), None)
            size = sum(f.stat().st_size for f in d.iterdir() if f.is_file())
            return n, time, size
    return None


# ------------------------------------------------------------------ spec

def spec(run):
    run = Path(run)
    n = Nml(run / 'params.nml')
    T = n.get('scaling_nml', 't', 100.0)                    # s per code time
    Bu = n.get('scaling_nml', 'b', 1.0)                     # G per code field
    mm = n.get('scaling_nml', 'l', 1e8) / 1e8               # Mm per code length
    size = [x * mm for x in n.all('cartesian_params', 'size')]
    origin = [x * mm for x in n.all('cartesian_params', 'origin')]
    dims = n.all('cartesian_params', 'dims')
    cells = n.all('patch_params', 'n')
    snap = snapshot_info(run)
    s = {}

    status = ''
    if snap:
        status = f'snapshot {snap[0]}' + (f', t = {num(snap[1])} ({clock(snap[1] * T)})' if snap[1] is not None else '')
    s['head'] = {'name': n.get('restart_params', 'run') or run.name, 'status': status}

    box = s['Box'] = {}
    if len(size) == 3:
        box['size'] = X.join(num(x) for x in size) + ' Mm'
        if len(origin) == 3:
            box['z range'] = f'{num(origin[2])} … {num(origin[2] + size[2])} Mm'
    periodic = n.all('cartesian_params', 'periodic')
    if periodic:
        axes = [a for a, p in zip('xyz', periodic) if p]
        box['lateral'] = f'periodic in {", ".join(axes)}' if axes else 'closed'
    g = n.get('gravity_params', 'constant')
    if n.on('gravity_params') and g:
        box['gravity'] = f'{sci(abs(g))} cm s⁻²' if abs(g) > 100 else f'{num(abs(g))} (code)'

    grid = s['Grid'] = {}
    ok = len(size) == len(dims) == len(cells) == 3
    if ok:
        dx = [a / (d * c) for a, d, c in zip(size, dims, cells)]
        grid['root resolution'] = km(dx[0]) if max(dx) - min(dx) < 1e-9 else X.join(km(x) for x in dx)
        grid['root cells'] = X.join(str(d * c) for d, c in zip(dims, cells))
        per = f'{cells[0]}³' if len(set(cells)) == 1 else X.join(map(str, cells))
        grid['patches'] = f'{X.join(map(str, dims))} root patches of {per} cells'
    mpi = n.all('cartesian_params', 'mpi_dims')
    if mpi:
        ranks = 1
        for m in mpi:
            ranks *= m
        grid['MPI'] = f'{X.join(map(str, mpi))} = {ranks} ranks'

    ref = s['Refinement'] = {}
    if n.on('refine_params') and ok:
        lmin, lmax = n.get('refine_params', 'levelmin', 0), n.get('refine_params', 'levelmax', 0)
        ratio = n.get('refine_params', 'ratio', 2)
        dx_at = lambda lv: km(dx[0] / ratio ** (lv - lmin))
        ref['levels'] = ', '.join(f'{lv} ({"root, " if lv == lmin else ""}{dx_at(lv)})' for lv in range(lmin, lmax + 1))
        llc, urc = n.all('refine_params', 'region_llc'), n.all('refine_params', 'region_urc')
        regs = []
        for i, lv in enumerate(n.all('refine_params', 'region_level')):
            if not isinstance(lv, int) or lv < 0 or len(llc) < 3 * i + 3 or len(urc) < 3 * i + 3:
                break
            regs.append(f'level {lv} ({dx_at(lv)}) for {bounds(llc[3*i:3*i+3], urc[3*i:3*i+3])}')
        if regs:
            forced = ', forced, snapped to patch edges' if n.get('refine_params', 'region_force') else ''
            ref['regions'] = '; '.join(regs) + forced
        rt = n.get('refine_params', 'refine_from_t')
        if rt:
            late = snap and snap[1] is not None and snap[1] < rt
            ref['switched on'] = f't = {num(rt)} ({clock(rt * T)})' + (', not reached yet' if late else '')
        crit = [k for k in ('max_grad', 'max_shock', 'max_contact', 'max_vorticity', 'max_compress', 'min_jeans', 'max_custom')
                if any(isinstance(v, (int, float)) and not isinstance(v, bool) and v for v in n.all('refine_params', k))]
        ref['criteria'] = ', '.join(crit) if crit else 'static regions only'
    else:
        ref['levels'] = 'none (uniform grid)'

    nu = s['Numerics'] = {}
    branch, hsh = n.get('code_params', 'branch'), n.get('code_params', 'hash')
    if hsh:
        nu['code'] = f'`{branch}` @ `{hsh}`' if branch else f'`{hsh}`'
    if n.get('code_params', 'solver'):
        nu['solver'] = f'`{n.get("code_params", "solver")}`'
    if n.get('ms_params', 'recon_type'):
        nu['reconstruction'] = f'{n.get("ms_params", "recon_type").upper()}, Courant {num(n.get("ms_params", "courant", 0))}'
    if n.get('timestep_params', 'time_order'):
        nu['time stepping'] = f'order {n.get("timestep_params", "time_order")}, local time step per patch'
    if n.has('ms_params'):
        nu['flux correction'] = 'reflux on' if n.get('ms_params', 'reflux') else 'reflux off'
    if n.has('divb_params'):
        nu['div B'] = 'cleaner on' if n.on('divb_params') else 'cleaner off'

    ph = s['Physics'] = {}
    if n.get('eos_params', 'file'):
        d = n.get('eos_params', 'dir', '').strip().strip('./')
        ph['EOS'] = f'table `{d + "/" if d else ""}{n.get("eos_params", "file")}.{n.get("eos_params", "file_ext", "eos")}`'
    rad = []
    if n.on('sc_rt_params'):
        rad.append('RT (sc_rt)')
    if n.on('rad_diffusion_params'):
        u = n.all('rad_diffusion_params', 'urc')
        rad.append('diffusion' + (f' below z = {num(u[2] * mm)} Mm' if len(u) == 3 and u[2] < 999 else ''))
    if n.on('genrad_params'):
        tab = Path(n.get('genrad_params', 'radtab_file', '') or '').name
        rad.append(f'genrad losses (`{tab}`)' if tab else 'genrad losses')
    if n.on('incrad_on') or n.on('incrad_params'):
        rad.append('incrad')
    ph['radiation'] = ', '.join(rad) or 'off'
    if n.on('hion_params'):
        he = n.get('hion_params', 'with_he')
        ph['non-LTE H / He'] = f'HION, atom `{n.get("hion_params", "atom_file")}`' + (
            f' + He `{Path(n.get("hion_params", "he_atom_file", "")).name}`' if he else ', no He')
    else:
        ph['non-LTE H / He'] = 'off'
    ph['conduction'] = f'Spitzer ({n.get("spitzer_params", "method", "")})' if n.on('spitzer_params') else 'off'
    if n.on('braginskii_params'):
        ph['viscosity'] = (f'Braginskii, ν<sub>e</sub> {num(n.get("braginskii_params", "nu_e", 0))}, '
                           f'ν<sub>cap</sub> {num(n.get("braginskii_params", "nu_cap", 0))}')
    res = []
    if n.on('joule_params'):
        res.append('Joule heating')
    if n.on('genohm_params'):
        res.append('generalised Ohm' + (', ambipolar' if n.get('genohm_params', 'do_ambipolar') else '')
                   + (', Hall' if n.get('genohm_params', 'do_hall') else ''))
    if res:
        ph['resistivity'] = '; '.join(res)
    bamb = n.get('ambient_field_params', 'b_amb', 0)
    if bamb:
        unit = (n.get('ambient_field_params', 'unit') or 'code').lower()
        ph['ambient field'] = (f'{num(bamb * Bu if unit == "code" else bamb)} G, '
                               f'θ = {num(n.get("ambient_field_params", "theta", 0))}°, '
                               f'φ = {num(n.get("ambient_field_params", "phi", 0))}°, '
                               f'{n.get("ambient_field_params", "mode", "add")}')

    bc = s['Boundaries'] = {}
    lo, hi = n.get('boundary_params', 'lower_bc'), n.get('boundary_params', 'upper_bc')
    if lo is not None:
        bc['bottom'] = BC.get(lo, f'lower_bc {lo}') + (', wavekill on' if n.get('boundary_params', 'wavekill_on') else '')
    if hi is not None:
        bc['top'] = BC.get(hi, f'upper_bc {hi}')
    if n.get('boundary_params', 'teff_target'):
        bc['T<sub>eff</sub> target'] = f'{num(n.get("boundary_params", "teff_target"))} K'

    io = s['Output'] = {}
    ot = n.get('out_params', 'out_time') or n.get('io_params', 'out_time')
    if ot:
        io['cadence'] = f'{num(ot)} ({clock(ot * T)})'
    aux = [a for a in n.all('aux_params', 'select') if isinstance(a, str) and a]
    if aux:
        io['aux'] = ', '.join(f'`{a}`' for a in aux)
    if snap:
        io['snapshot size'] = f'{snap[2] / 2**30:.2f} GB (snapshot {snap[0]})'
    io['time unit'] = f'{num(T)} s'

    src = n.get('extend_params', 'from')
    s['Initial condition'] = {'start': f'extended from `{Path(src).name}` snapshot {n.get("extend_params", "snapshot")}'
                              if src else 'fresh start'}
    return s


# ----------------------------------------------------------------- output

def fill(spec_, body):
    """The template body with every key it shares with `spec_` filled in (case-blind);
    keys only `spec_` has go at the end of their section."""
    head = {k.lower(): v for k, v in spec_.pop('head').items()}
    secs = {name: {k.lower(): (k, v) for k, v in d.items()} for name, d in spec_.items()}
    lines, section, used = [], None, set()

    def extras():
        tail = []
        while lines and not lines[-1].strip():
            tail.append(lines.pop())
        lines.extend(f'{k}: {v}' for lk, (k, v) in secs.get(section, {}).items() if (section, lk) not in used)
        lines.extend(tail)

    for raw in body.splitlines():
        m = re.match(r'\s*\[(.+)\]\s*$', raw)
        if m:
            if section is not None:
                extras()
            section = m.group(1).strip()
            lines.append(raw)
            continue
        m = re.match(r'([^\s#:][^:]*?)\s*:', raw)
        if m:
            key, lk = m.group(1).strip(), m.group(1).strip().lower()
            used.add((section, lk))
            val = head.get(lk) if section is None else secs.get(section, {}).get(lk, (None, None))[1]
            if val:
                lines.append(f'{key}: {val}')
                continue
        lines.append(raw)
    if section is not None:
        extras()
    return '\n'.join(lines).strip('\n')


def main():
    template = TEMPLATE.read_text()
    if len(sys.argv) < 2:
        print(template.rstrip())
        return
    run = Path(sys.argv[1])
    if not (run / 'params.nml').is_file():
        sys.exit(f'runspec: no {run / "params.nml"}')
    body = template.split('```run', 1)[1].rsplit('```', 1)[0]
    print('```run\n' + fill(spec(run), body) + '\n```')


if __name__ == '__main__':
    main()
