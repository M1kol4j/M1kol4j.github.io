#!/usr/bin/env python3
"""Drafts ```run``` spec blocks for DISPATCH runs from their params.nml and snapshots.

  ./runspec.py                            print the blank template (run-template.md)
  ./runspec.py <run>                      print the filled block; a run extended from
                                          another is followed by the source run, folded
  ./runspec.py --refresh <index.md> ...   update the changing lines (status, snapshots,
                                          aux, sizes) of every run block in place, and add
                                          the folded source run of an extension if missing

<run> is a run directory or a name under --data. Paste the output into
content/<project>/index.md and complete what the namelist does not record:
machine, purpose, seed field, corona. Empty lines are not shown on the page.
"""
import argparse
import os
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / 'run-template.md'
DATA = Path(os.environ.get('RUN_DATA', '/mn/stornext/d9/data/mikolajs/Dispatch/dispatch-bifrost/experiments/photo_tr/data'))
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


def aux_names(path, limit=1000, whole=False):
    """Variable names in a .aux / .aux2d file: each entry is five Fortran records (name,
    two ints, a string, the payload). 3-D aux sit on every patch, so the first repeated
    name ends the scan; 2-D maps sit on some patches only, so `whole` reads every name."""
    names = []
    try:
        with open(path, 'rb') as f:
            for _ in range(limit if not whole else 10**7):
                name = None
                for k in range(5):
                    head = f.read(4)
                    if len(head) < 4:
                        return names
                    size = int.from_bytes(head, 'little', signed=True)
                    if k == 0:
                        name = f.read(size).decode(errors='replace').strip()
                    else:
                        f.seek(size, 1)
                    f.seek(4, 1)
                if name == '__record__':
                    continue
                if name in names:
                    if whole:
                        continue
                    break
                names.append(name)
    except OSError:
        pass
    return names


def scan(run):
    """Every snapshot directory: number, complete (all rank .done markers, or a
    snapshot.nml in runs from before the markers), time, bytes, (3-D aux, 2-D aux)."""
    out = []
    for d in sorted(d for d in run.iterdir() if re.fullmatch(r'\d{5}', d.name) and d.is_dir()):
        files = [f for f in d.iterdir() if f.is_file()]
        names = {f.name for f in files}
        dones = [f for f in files if f.name.endswith('.done')]
        if dones:
            try:
                need = int(dones[0].read_text().split()[0])
            except (OSError, ValueError, IndexError):
                need = len(dones)
            complete = len(dones) >= need
        else:
            complete = 'snapshot.nml' in names and any(x.endswith('.dat') for x in names)
        time = None
        if complete and 'snapshot.nml' in names:
            time = next((g['time'][0] for g in read_nml(d / 'snapshot.nml').values() if g.get('time')), None)
        aux = (tuple(aux_names(d / 'snapshot_00000.aux')), tuple(sorted(aux_names(d / 'snapshot_00000.aux2d', whole=True)))) if complete else None
        out.append({'n': int(d.name), 'complete': complete, 'time': time,
                    'size': sum(f.stat().st_size for f in files), 'aux': aux})
    return out


def ranges(nums):
    """[0, 1, 2, 5, 7, 8] -> '0–2, 5, 7–8'."""
    out, nums = [], sorted(nums)
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f'{nums[i]}\u2013{nums[j]}')
        i = j + 1
    return ', '.join(out)


def aux_summary(snaps):
    groups = []
    for x in snaps:
        if x['aux'] is None:
            continue
        if groups and groups[-1][0] == x['aux']:
            groups[-1][1].append(x['n'])
        else:
            groups.append((x['aux'], [x['n']]))
    code = lambda vs: ', '.join(f'`{v}`' for v in vs)
    fmt = lambda a: ' + '.join(filter(None, [code(a[0]), f'2-D maps {code(a[1])}' if a[1] else ''])) or 'none'
    if len(groups) == 1:
        return fmt(groups[0][0]) + ' (every snapshot)'
    return '; '.join(f'snapshot{"s" if len(ns) > 1 else ""} {ranges(ns)}: {fmt(a)}' for a, ns in groups)


def gb(b):
    return f'{b / 2**30:.1f} GB' if b >= 2**30 else f'{b / 2**20:.0f} MB'


def source(run):
    """The run this one was extended from (a name), or None."""
    frm = Nml(Path(run) / 'params.nml').get('extend_params', 'from')
    return Path(frm).name if frm else None


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
    snaps = scan(run)
    done = [x for x in snaps if x['complete']]
    snap = (done[-1]['n'], done[-1]['time'], done[-1]['size']) if done else None
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
    io['time unit'] = f'{num(T)} s'

    da = s['Data'] = {'location': f'`{run.resolve()}`'}
    if done:
        times = [x['time'] for x in done if x['time'] is not None]
        txt = f'{len(done)} complete: {ranges([x["n"] for x in done])}'
        if times:
            txt += f', t = {num(min(times))}\u2013{num(max(times))} (up to {clock(max(times) * T)})'
        bad = [x['n'] for x in snaps if not x['complete']]
        da['snapshots'] = txt + (f'; {ranges(bad)} incomplete' if bad else '')
    nv = n.get('patch_params', 'nv')
    if nv:
        da['primary'] = (f'{nv} per cell: density, momentum, energy, magnetic field' if nv == 8 else f'{nv} per cell')
    sel = [a for a in n.all('aux_params', 'select') if isinstance(a, str) and a]
    da['aux'] = aux_summary(done) if any(x['aux'] for x in done) else ', '.join(f'`{a}`' for a in sel)
    if snap:
        da['snapshot size'] = f'{gb(snap[2])} (snapshot {snap[0]})'
    if snaps:
        da['total size'] = gb(sum(x['size'] for x in snaps))

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


# ---------------------------------------------------------------- refresh

KEY = re.compile(r'([^\s#:][^:]*?)\s*:(.*)$')
DYNAMIC = {None: {'status'}, 'Refinement': {'switched on'},
           'Data': {'location', 'snapshots', 'primary', 'aux', 'snapshot size', 'total size'}}


def block_name(block):
    m = re.search(r'^name:\s*(\S+)', block, re.M)
    return m.group(1) if m else None


def refreshed(block, run):
    """`block` with its changing lines replaced from the run on disk; keys it lacks are
    added at the end of their section, and a missing [Data] section after [Output]."""
    new = spec(run)
    head = new.pop('head')
    values = {None: {k.lower(): v for k, v in head.items()}}
    values.update({sec: {k.lower(): (k, v) for k, v in d.items()} for sec, d in new.items()})
    sections, cur = [(None, [])], None
    for line in block.splitlines():
        m = re.match(r'\s*\[(.+)\]\s*$', line)
        if m:
            cur = m.group(1).strip()
            sections.append((cur, [line]))
        else:
            sections[-1][1].append(line)
    out = []
    for sec, lines in sections:
        keys = DYNAMIC.get(sec, set())
        seen = set()
        for i, line in enumerate(lines):
            m = KEY.match(line)
            if not m or m.group(1).strip().lower() not in keys:
                continue
            key, lk, old = m.group(1).strip(), m.group(1).strip().lower(), m.group(2).strip()
            seen.add(lk)
            val = values.get(sec, {}).get(lk)
            val = val if sec is None else (val[1] if val else None)
            if not val:
                continue
            if lk == 'status':                       # keep what follows " · " (running, machine, ...)
                if old and not re.match(r'snapshot \d+', old):
                    continue
                rest = old.split(' \u00B7 ', 1)[1] if ' \u00B7 ' in old else ''
                val = val + (f' \u00B7 {rest}' if rest else '')
            if lk == 'switched on' and old and not old.startswith('t = '):
                continue
            lines[i] = f'{key}: {val}'
        if sec is not None:
            missing = [f'{k}: {v}' for lk, (k, v) in values.get(sec, {}).items() if lk in keys and lk not in seen]
            while lines and not lines[-1].strip():
                lines.pop()
            lines += missing + ['']
        out.append((sec, lines))
    if 'Data' not in [sec for sec, _ in out]:
        data = ['[Data]'] + [f'{k}: {v}' for k, v in new.get('Data', {}).items()] + ['']
        at = next((i + 1 for i, (sec, _) in enumerate(out) if sec == 'Output'), len(out))
        out.insert(at, ('Data', data))
    text = '\n'.join(line for _, lines in out for line in lines)
    return re.sub(r'\n{3,}', '\n\n', text).strip('\n') + '\n'


def folded(run, template):
    body = template.split('```run', 1)[1].rsplit('```', 1)[0]
    text = fill(spec(run), body)
    lines = [ln for ln in text.splitlines()
             if not (KEY.match(ln) and not re.sub(r'(?:^|\s+)#(?:\s.*)?$', '', KEY.match(ln).group(2)).strip())]
    out = []
    for ln in lines:
        out.append(ln)
        if ln.startswith('name:'):
            out.append('collapsed: yes')
    return '```run\n' + re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip('\n') + '\n```'


def find(name, data):
    p = Path(name)
    if (p / 'params.nml').is_file():
        return p
    if (data / name / 'params.nml').is_file():
        return data / name
    return None


def refresh(paths, data, template):
    for md in paths:
        text = md.read_text()
        names = set(re.findall(r'```run\n(?:.*\n)*?name:\s*(\S+)', text))
        pieces, pos, changed = [], 0, False
        for m in re.finditer(r'```run\n(.*?)```', text, re.S):
            pieces.append(text[pos:m.start()])
            block, name = m.group(1), block_name(m.group(1))
            run = find(name, data) if name else None
            if run is None:
                if name:
                    print(f'runspec: {md}: no run {name} under {data}, left as it is', file=sys.stderr)
                pieces.append(m.group(0))
            else:
                new = '```run\n' + refreshed(block, run) + '```'
                pieces.append(new)
                src = source(run)
                while src and src not in names and find(src, data):
                    pieces.append('\n\n' + folded(find(src, data), template))
                    print(f'runspec: {md}: added {src}, the source of {name}, folded')
                    names.add(src)
                    src = source(find(src, data))
            pos = m.end()
        pieces.append(text[pos:])
        new_text = ''.join(pieces)
        if new_text != text:
            md.write_text(new_text)
            print(f'runspec: refreshed {md}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run', nargs='?', help='run directory or name under --data')
    ap.add_argument('--data', type=Path, default=DATA, help=f'directory of runs (default {DATA})')
    ap.add_argument('--refresh', nargs='+', type=Path, metavar='index.md')
    a = ap.parse_args()
    template = TEMPLATE.read_text()
    if a.refresh:
        refresh([p for p in a.refresh if p.is_file()], a.data, template)
        return
    if not a.run:
        print(template.rstrip())
        return
    run = find(a.run, a.data)
    if run is None:
        sys.exit(f'runspec: no params.nml in {a.run} or {a.data / a.run}')
    body = template.split('```run', 1)[1].rsplit('```', 1)[0]
    print('```run\n' + fill(spec(run), body) + '\n```')
    src, seen = source(run), {run.name}
    while src and src not in seen and find(src, a.data):
        print('\n' + folded(find(src, a.data), template))
        seen.add(src)
        src = source(find(src, a.data))


if __name__ == '__main__':
    main()
