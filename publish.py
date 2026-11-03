#!/usr/bin/env python3
"""Builds the encrypted results site and publishes it to the gh-pages branch.

The plaintext (markdown, images, movies, passwords) lives in content/, which git
ignores. Only ciphertext reaches _site/ and GitHub: every project page and every
media file is sealed with AES-256-GCM under the project's content key, and the
content key is wrapped under the project password and under the master password
(PBKDF2-SHA256). theme/site.js undoes this in the browser.

  ./publish.py build                     render + encrypt content/ into _site/
  ./publish.py serve [port]              build, then serve _site/ on localhost
  ./publish.py push                      build, then force-push _site/ as one commit to gh-pages
  ./publish.py passwd <slug|master> [pw] set a password (a random passphrase if omitted)
  ./publish.py rekey <slug>              new content key: revokes remembered keys and share links
  ./publish.py link <slug>               print a share link that carries the key
"""
import argparse
import base64
import datetime as dt
import functools
import hashlib
import hmac
import html
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from string import Template

import yaml
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from markdown_it import MarkdownIt
from markdown_it.token import Token
from PIL import Image

ROOT = Path(__file__).resolve().parent
THEME = ROOT / 'theme'
CONTENT = ROOT / 'content'
SITE = ROOT / '_site'

ITER = 600_000                      # PBKDF2-SHA256 rounds (OWASP 2023)
IMAGE = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
         '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml'}
VIDEO = {'.mp4', '.m4v', '.mov', '.webm', '.mkv', '.avi'}
MODES = ('slider', 'blink', 'side', 'diff')
X264 = ['-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart', '-an']
MAX_FILE = 95 * 2**20               # GitHub refuses pushes with files over 100 MB
MAX_SITE = 950 * 2**20              # a Pages site may be at most 1 GB
MAX_DECODE = 4096                   # widest frame browsers decode in hardware

MD = MarkdownIt('commonmark', {'html': True, 'typographer': True}).enable(
    ['table', 'strikethrough', 'replacements', 'smartquotes'])

LOCK_SVG = ('<svg class="ico shut" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="11" width="16" '
            'height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>'
            '<svg class="ico open" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="11" width="16" '
            'height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.6-1.8"/></svg>')
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
           "%3Ccircle cx='8' cy='8' r='6.5' fill='none' stroke='%231a5fb4' stroke-width='2'/%3E"
           "%3Ccircle cx='8' cy='8' r='2.5' fill='%231a5fb4'/%3E%3C/svg%3E")


def die(msg):
    sys.exit(f'publish: {msg}')


def b64(b):
    return base64.b64encode(b).decode()


def unb64(s):
    return base64.b64decode(s)


def esc(s):
    return html.escape(str(s), quote=True)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        die(f'{" ".join(map(str, cmd))}\n{r.stderr.strip()}')
    return r.stdout.strip()


@functools.cache
def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def stamp(path):
    """UTC modification time of a file, to the minute: the 'added' date of a figure."""
    t = dt.datetime.fromtimestamp(Path(path).stat().st_mtime, tz=dt.timezone.utc)
    return t.strftime('%Y-%m-%dT%H:%M')


def nice_date(iso):
    d = dt.date.fromisoformat(str(iso)[:10])
    return f'{d.day} {d:%b %Y}'


# ---------------------------------------------------------------- secrets ---

def load_yaml(path):
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


def save_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        yaml.safe_dump(data, f, sort_keys=False)
    os.chmod(path, 0o600)


def passphrase(words=4):
    try:
        pool = [w for w in Path('/usr/share/dict/words').read_text(errors='ignore').split()
                if re.fullmatch('[a-z]{4,8}', w)]
    except OSError:
        pool = []
    if len(pool) < 1000:
        return secrets.token_urlsafe(12)
    return '-'.join(secrets.choice(pool) for _ in range(words))


class Keyring:
    """Passwords (content/.secrets.yml) and content keys and salts (content/.keys.yml)."""

    def __init__(self):
        self.pw_file, self.key_file = CONTENT / '.secrets.yml', CONTENT / '.keys.yml'
        self.pw, self.keys = load_yaml(self.pw_file), load_yaml(self.key_file)

    def ensure(self, slugs):
        fresh = []
        if not self.pw.get('master'):
            self.pw['master'] = passphrase()
            fresh.append('master')
        self.keys.setdefault('master_salt', b64(os.urandom(16)))
        for s in slugs:
            if not self.pw.get(s):
                self.pw[s] = passphrase()
                fresh.append(s)
            if s not in self.keys:
                self.keys[s] = {'key': b64(os.urandom(32)), 'salt': b64(os.urandom(16))}
        self.save()
        for s in fresh:
            print(f'new password for {s}: {self.pw[s]}   (stored in {self.pw_file})')

    def save(self):
        save_private(self.pw_file, self.pw)
        save_private(self.key_file, self.keys)

    def key(self, slug):
        return unb64(self.keys[slug]['key'])

    def wraps(self, slug):
        """The content key wrapped under the project password, then the master password."""
        ck = self.key(slug)
        return [wrap(self.pw[slug], unb64(self.keys[slug]['salt']), ck, 'project'),
                wrap(self.pw['master'], unb64(self.keys['master_salt']), ck, 'master')]


def kek(password, salt):
    pw = unicodedata.normalize('NFC', password).encode()
    return hashlib.pbkdf2_hmac('sha256', pw, salt, ITER, 32)


def wrap(password, salt, ck, kind):
    k = kek(password, salt)
    iv = hmac.new(k, b'wrap' + ck, 'sha256').digest()[:12]
    return {'kind': kind, 'salt': b64(salt), 'iv': b64(iv), 'ct': b64(AESGCM(k).encrypt(iv, ck, None))}


class Sealer:
    """Deterministic AES-GCM: the IV and the file name are HMACs of the plaintext,
    so an unchanged file gives identical bytes and pushes nothing new."""

    def __init__(self, ck):
        self.aes = AESGCM(ck)
        self.mac = hashlib.sha256(b'results-site/iv' + ck).digest()

    def seal(self, data):
        h = hmac.new(self.mac, data, 'sha256').digest()
        iv = h[:12]
        return h[12:22].hex(), iv + self.aes.encrypt(iv, data, None)


# ------------------------------------------------------------------ media ---

def probe(path):
    out = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_packets', '-show_entries',
               'stream=codec_name,pix_fmt,width,height,avg_frame_rate,r_frame_rate,nb_read_packets',
               '-of', 'json', str(path)])
    s = json.loads(out)['streams'][0]
    fps = 0.0
    for key in ('avg_frame_rate', 'r_frame_rate'):
        num, _, den = s.get(key, '0/0').partition('/')
        if int(den or 0) and int(num):
            fps = int(num) / int(den)
            break
    return {'codec': s.get('codec_name'), 'pix': s.get('pix_fmt'), 'w': s['width'], 'h': s['height'],
            'fps': round(fps or 25.0, 4), 'frames': int(s.get('nb_read_packets') or 0)}


def ffmpeg(*args):
    run(['ffmpeg', '-v', 'error', '-y', *map(str, args)])


def cached(name):
    d = CONTENT / '.cache'
    d.mkdir(exist_ok=True)
    return d / name


def web_video(src):
    """An H.264 / yuv420p / faststart copy of a movie, cached by content hash."""
    out = cached(f'{sha(src)[:20]}.v1.mp4')
    if not out.exists():
        p = probe(src)
        tmp = out.with_suffix('.part.mp4')
        if p['codec'] == 'h264' and p['pix'] == 'yuv420p' and p['w'] % 2 == 0 and p['h'] % 2 == 0:
            ffmpeg('-i', src, '-map', '0:v:0', '-c', 'copy', '-movflags', '+faststart', tmp)
        else:
            print(f'  transcoding {src.name}')
            ffmpeg('-i', src, '-map', '0:v:0', '-vf', 'crop=trunc(iw/2)*2:trunc(ih/2)*2:0:0', *X264, tmp)
        tmp.rename(out)
    return out


def poster(video):
    out = cached(f'{sha(video)[:20]}.poster.jpg')
    if not out.exists():
        ffmpeg('-i', video, '-frames:v', '1', '-q:v', '2', out)
    return out


def stacked(va, vb, layout, names):
    """A and B side by side (h) or on top of each other (v) in one movie: frame-locked."""
    out = cached(f'{sha(va)[:12]}-{sha(vb)[:12]}-{layout}.v1.mp4')
    if not out.exists():
        print(f'  stacking {names[0]} | {names[1]}')
        tmp = out.with_suffix('.part.mp4')
        ffmpeg('-i', va, '-i', vb, '-filter_complex',
               f'[0:v][1:v]{"hstack" if layout == "h" else "vstack"}=inputs=2:shortest=1', *X264, tmp)
        tmp.rename(out)
    return out


# ---------------------------------------------------------------- content ---

def read_page(path):
    text = path.read_text(encoding='utf-8')
    m = re.match(r'---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)', text, re.S)
    if not m:
        return {}, text
    return yaml.safe_load(m.group(1)) or {}, text[m.end():]


def parse_block(text):
    """`key: value` lines; an indented line continues the previous value."""
    out, key = {}, None
    for line in text.splitlines():
        if not line.strip():
            continue
        m = re.match(r'([A-Za-z_]+)\s*:\s?(.*)$', line)
        if m and not line[0].isspace():
            key = m.group(1).lower()
            out[key] = m.group(2).strip()
        elif key:
            out[key] += ' ' + line.strip()
    return out


def parse_spec(text):
    """A ```run``` block: `key: value` lines, `[Section]` lines, indented continuations.
    Lines starting with # and hints after " # " are dropped, and so are empty values."""
    head, sections, last = [], [], None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        m = re.match(r'\s*\[(.+)\]\s*$', line)
        if m:
            sections.append((m.group(1).strip(), []))
            last = None
            continue
        m = re.match(r'([^\s#:][^:]*?)\s*:(.*)$', line)
        if m and not line[0].isspace():
            last = [m.group(1).strip(), m.group(2)]
            (sections[-1][1] if sections else head).append(last)
        elif last is not None:
            last[1] += ' ' + line.strip()
    clean = lambda items: [(k, v) for k, v in ((k, re.sub(r'(?:^|\s+)#(?:\s.*)?$', '', v).strip()) for k, v in items) if v]
    return clean(head), [(name, clean(items)) for name, items in sections]


def run_card(src):
    head, sections = parse_spec(src)
    meta = {k.lower(): v for k, v in head}
    name = meta.get('name', 'run')
    inline = lambda v: MD.renderInline(v).strip()
    status = f'<span class="spec-status">{inline(meta["status"])}</span>' if meta.get('status') else ''
    title = (f'<span class="spec-label">Run</span><code class="spec-name">{esc(name)}</code>{status}')
    rows = lambda items: ''.join(f'<dt>{inline(k)}</dt><dd>{inline(v)}</dd>' for k, v in items)
    extra = [(k, v) for k, v in head if k.lower() not in ('name', 'status', 'collapsed')]
    body = (f'<dl class="spec-top">{rows(extra)}</dl>' if extra else '') + '<div class="spec-grid">' + ''.join(
        f'<div class="spec-sec"><h4>{esc(sec)}</h4><dl>{rows(items)}</dl></div>' for sec, items in sections if items
    ) + '</div>'
    rid = 'run-' + re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    if meta.get('collapsed', '').lower() in ('yes', 'true', '1', 'y'):
        return f'<details class="spec" id="{rid}"><summary class="spec-head">{title}</summary>{body}</details>'
    return f'<section class="spec" id="{rid}"><header class="spec-head">{title}</header>{body}</section>'


def html_block(content):
    t = Token('html_block', '', 0)
    t.content = content + '\n'
    return t


def is_local(ref):
    return bool(ref) and not re.match(r'^([a-z][a-z0-9+.-]*:|#|/)', ref, re.I)


class Project:
    def __init__(self, slug, keyring):
        self.slug, self.dir = slug, CONTENT / slug
        self.meta, self.body = read_page(self.dir / 'index.md')
        self.keyring = keyring
        self.seal = None                # set by build() once the keyring has a key for it
        self.assets, self.blobs = {}, {}
        self.stamps = [stamp(self.dir / 'index.md')]
        self.nfig = 0

    @property
    def draft(self):
        return bool(self.meta.get('draft'))

    @property
    def title(self):
        return str(self.meta.get('title', self.slug))

    @property
    def updated(self):
        return str(self.meta.get('updated') or max(self.stamps)[:10])

    def where(self):
        return f'{self.slug}/index.md'

    def path(self, ref):
        p = (self.dir / ref).resolve()
        if not p.is_file():
            die(f'{self.where()}: no file {ref}')
        self.stamps.append(stamp(p))
        return p

    def add(self, data, **meta):
        name, blob = self.seal.seal(data)
        self.blobs[name] = blob
        self.assets[name] = {**meta, 'bytes': len(data)}
        return name

    def image(self, p):
        meta = {'kind': 'image', 'mime': IMAGE[p.suffix.lower()]}
        if p.suffix.lower() != '.svg':
            with Image.open(p) as im:
                meta['w'], meta['h'] = im.size
        return self.add(p.read_bytes(), **meta), meta

    def video(self, p):
        v = web_video(p)
        info = probe(v)
        meta = {'kind': 'video', 'mime': 'video/mp4', 'w': info['w'], 'h': info['h'],
                'fps': info['fps'], 'frames': info['frames']}
        pid = self.add(poster(v).read_bytes(), kind='image', mime='image/jpeg', w=info['w'], h=info['h'])
        return self.add(v.read_bytes(), poster=pid, **meta), meta

    def file(self, p):
        return self.add(p.read_bytes(), kind='file', mime='application/octet-stream', name=p.name)

    # -- markdown --

    def render(self):
        env = {}
        tokens, out, i = MD.parse(self.body, env), [], 0
        while i < len(tokens):
            t = tokens[i]
            if (t.type == 'paragraph_open' and i + 2 < len(tokens) and tokens[i + 1].type == 'inline'
                    and tokens[i + 2].type == 'paragraph_close'):
                kids = [c for c in tokens[i + 1].children or []
                        if c.type != 'softbreak' and not (c.type == 'text' and not c.content.strip())]
                if len(kids) == 1 and kids[0].type == 'image' and is_local(kids[0].attrGet('src')):
                    out.append(html_block(self.figure(kids[0], env)))
                    i += 3
                    continue
            if t.type == 'fence' and t.info.strip().split()[:1] == ['compare']:
                out.append(html_block(self.compare(t.content)))
                i += 1
                continue
            if t.type == 'fence' and t.info.strip().split()[:1] == ['run']:
                out.append(html_block(run_card(t.content)))
                i += 1
                continue
            if t.type == 'inline':
                self.inline(t.children or [])
            out.append(t)
            i += 1
        return MD.renderer.render(out, MD.options, env)

    def inline(self, children):
        """Images and links inside running text: point them at sealed assets."""
        for c in children:
            if c.type == 'image' and is_local(c.attrGet('src')):
                aid, _ = self.image(self.path(c.attrGet('src')))
                c.attrs.pop('src', None)
                c.attrSet('data-asset', aid)
            elif c.type == 'link_open' and is_local(c.attrGet('href')):
                p = self.path(c.attrGet('href'))
                c.attrSet('href', '#')
                c.attrSet('data-asset-link', self.file(p))
                c.attrSet('data-name', p.name)

    def number(self):
        self.nfig += 1
        return self.nfig

    def caption(self, n, text_html, date):
        return (f'<figcaption><b>Figure {n}.</b> {text_html}'
                f'<time datetime="{date}">added {nice_date(date)}</time></figcaption>')

    def figure(self, img, env):
        p = self.path(img.attrGet('src'))
        opts = (img.attrGet('title') or '').split()
        n = self.number()
        cap = MD.renderer.renderInline(img.children or [], MD.options, env)
        if p.suffix.lower() in VIDEO:
            aid, m = self.video(p)
            auto = '' if 'still' in opts else ' data-autoplay'
            media = (f'<video data-asset="{aid}" data-poster="{self.assets[aid]["poster"]}"{auto} '
                     f'muted loop playsinline controls preload="none"></video>')
        elif p.suffix.lower() in IMAGE:
            aid, m = self.image(p)
            media = f'<img data-asset="{aid}" alt="{esc(img.content)}">'
        else:
            die(f'{self.where()}: {p.name} is neither an image nor a movie')
        ratio = f' style="aspect-ratio:{m["w"]}/{m["h"]}"' if 'w' in m else ''
        cls = 'fig wide' if 'wide' in opts else 'fig'
        return (f'<figure class="{cls}" id="fig-{n}" data-date="{stamp(p)}">'
                f'<div class="media"{ratio}>{media}</div>{self.caption(n, cap, stamp(p)[:10])}</figure>')

    def compare(self, src):
        f = parse_block(src)
        sides = []
        for k in ('a', 'b'):
            if k not in f:
                die(f'{self.where()}: a compare block needs both a: and b:')
            ref, _, label = f[k].partition('|')
            sides.append((self.path(ref.strip()), label.strip() or k.upper()))
        (pa, la), (pb, lb) = sides
        mode = (f.get('mode') or 'slider').split()[0]
        if mode not in MODES:
            die(f'{self.where()}: compare mode {mode!r}, expected one of {", ".join(MODES)}')
        kinds = {p.suffix.lower() in VIDEO for p in (pa, pb)}
        if kinds == {True}:
            va, vb = web_video(pa), web_video(pb)
            a, b = probe(va), probe(vb)
            if (a['w'], a['h']) != (b['w'], b['h']):
                die(f'{self.where()}: compare {pa.name} is {a["w"]}x{a["h"]}, {pb.name} is {b["w"]}x{b["h"]}')
            if a['frames'] != b['frames']:
                print(f'  warning: {pa.name} has {a["frames"]} frames, {pb.name} {b["frames"]}: '
                      f'the comparison stops at the shorter')
            layout = 'h' if 2 * a['w'] <= MAX_DECODE else 'v'
            if layout == 'v' and 2 * a['h'] > MAX_DECODE:
                die(f'{self.where()}: {pa.name} is too large to stack ({a["w"]}x{a["h"]}); scale it down')
            s = stacked(va, vb, layout, (pa.name, pb.name))
            sinfo = probe(s)
            pid = self.add(poster(s).read_bytes(), kind='image', mime='image/jpeg')
            sid = self.add(s.read_bytes(), kind='video', mime='video/mp4')
            cfg = {'kind': 'video', 'src': sid, 'poster': pid, 'layout': layout, 'w': a['w'], 'h': a['h'],
                   'fps': a['fps'], 'frames': min(sinfo['frames'] or a['frames'], a['frames'] or 10**9)}
        elif kinds == {False}:
            (ia, ma), (ib, mb) = self.image(pa), self.image(pb)
            if 'w' not in ma or (ma['w'], ma['h']) != (mb['w'], mb['h']):
                die(f'{self.where()}: compare {pa.name} and {pb.name} must be raster images of equal size')
            cfg = {'kind': 'image', 'a': ia, 'b': ib, 'w': ma['w'], 'h': ma['h']}
        else:
            die(f'{self.where()}: compare {pa.name} with {pb.name}: both images or both movies')
        cfg.update(labels=[la, lb], mode=mode)
        n = self.number()
        date = max(stamp(pa), stamp(pb))
        cap = MD.renderInline(f.get('caption', '')).strip()
        cap = re.sub(r'^<p>(.*)</p>$', r'\1', cap, flags=re.S)
        return (f'<figure class="fig cmp" id="fig-{n}" data-date="{date}" data-cmp="{esc(json.dumps(cfg))}">'
                f'<div class="cmp-stage" style="aspect-ratio:{cfg["w"]}/{cfg["h"]}"></div>'
                f'{self.caption(n, cap, date[:10])}</figure>')

    def build(self, out, theme):
        html_body = self.render()
        page = {k: self.meta[k] for k in ('title', 'subtitle', 'authors', 'tags') if self.meta.get(k)}
        page.update(title=self.title, updated=self.updated, html=html_body, assets=self.assets)
        _, sealed = self.seal.seal(json.dumps(page, sort_keys=True).encode())
        d = out / self.slug
        (d / 'm').mkdir(parents=True, exist_ok=True)
        (d / 'page.bin').write_bytes(sealed)
        for name, blob in self.blobs.items():
            (d / 'm' / f'{name}.bin').write_bytes(blob)
        lock = {'slug': self.slug, 'iter': ITER, 'wraps': self.keyring.wraps(self.slug)}
        (d / 'lock.json').write_text(json.dumps(lock))
        (d / 'index.html').write_text(Template((THEME / 'project.html').read_text()).substitute(
            title=esc(self.title), summary=esc(self.meta.get('summary', '')),
            updated=nice_date(self.updated), lock=script_json(lock), lock_icon=LOCK_SVG,
            favicon=FAVICON, css=theme['css'], js=theme['js']))
        nmedia = len(self.blobs)
        print(f'  {self.slug}: {self.nfig} figures, {nmedia} sealed files, updated {self.updated}')


def script_json(obj):
    return json.dumps(obj).replace('</', '<\\/')


def copy_theme(out):
    names = {}
    (out / 'assets').mkdir(parents=True, exist_ok=True)
    for name in ('site.css', 'site.js'):
        data = (THEME / name).read_bytes()
        stem, ext = name.rsplit('.', 1)
        fn = f'{stem}.{hashlib.sha256(data).hexdigest()[:10]}.{ext}'
        (out / 'assets' / fn).write_bytes(data)
        names[ext] = f'assets/{fn}'
    return names


def build():
    site = load_yaml(CONTENT / 'site.yml')
    if not site:
        die(f'no {CONTENT / "site.yml"}')
    slugs = site.get('projects') or []
    for s in slugs:
        if not (CONTENT / s / 'index.md').is_file():
            die(f'site.yml lists {s}, but there is no content/{s}/index.md')
    keyring = Keyring()
    projects = [Project(s, keyring) for s in slugs]
    keyring.ensure([p.slug for p in projects if not p.draft])
    for p in projects:
        if p.seal is None and not p.draft:
            p.seal = Sealer(keyring.key(p.slug))

    tmp = SITE.with_name(SITE.name + '.tmp')
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    theme = copy_theme(tmp)
    cards = []
    for p in projects:
        summary = f'<p>{esc(p.meta.get("summary", ""))}</p>' if p.meta.get('summary') else ''
        if p.draft:
            cards.append(f'<div class="card draft"><div class="card-main"><h2>{esc(p.title)}</h2>{summary}</div>'
                         f'<div class="card-side"><span class="soon">in preparation</span></div></div>')
            continue
        p.build(tmp, theme)
        cards.append(f'<a class="card" href="{esc(p.slug)}/" data-slug="{esc(p.slug)}">'
                     f'<div class="card-main"><h2>{esc(p.title)}</h2>{summary}</div>'
                     f'<div class="card-side"><time datetime="{p.updated}">{nice_date(p.updated)}</time>'
                     f'{LOCK_SVG}</div></a>')
    index = {'iter': ITER, 'master_salt': keyring.keys['master_salt'],
             'projects': [p.slug for p in projects if not p.draft]}
    (tmp / 'index.html').write_text(Template((THEME / 'index.html').read_text()).substitute(
        title=esc(site.get('title', 'Results')), tagline=esc(site.get('tagline', '')),
        footer=esc(site.get('footer', '')), cards='\n'.join(cards), site=script_json(index),
        favicon=FAVICON, css=theme['css'], js=theme['js']))
    (tmp / '.nojekyll').write_text('')
    if site.get('noindex', True):
        (tmp / 'robots.txt').write_text('User-agent: *\nDisallow: /\n')

    total = 0
    for f in tmp.rglob('*'):
        if f.is_file():
            total += f.stat().st_size
            if f.stat().st_size > MAX_FILE:
                die(f'{f.relative_to(tmp)} is {f.stat().st_size / 2**20:.0f} MB; GitHub refuses files over 100 MB')
    if total > MAX_SITE:
        die(f'the site is {total / 2**20:.0f} MB; GitHub Pages serves at most 1 GB')
    shutil.rmtree(SITE, ignore_errors=True)
    tmp.rename(SITE)
    print(f'built {SITE} ({total / 2**20:.1f} MB)')


# ---------------------------------------------------------------- publish ---

def git(*args, env=None, cwd=ROOT):
    return run(['git', *args], env=env, cwd=cwd)


def push():
    build()
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_DIR=str(ROOT / '.git'), GIT_WORK_TREE=str(SITE),
                   GIT_INDEX_FILE=str(Path(td) / 'index'))
        git('add', '-A', '.', env=env, cwd=SITE)
        tree = git('write-tree', env=env, cwd=SITE)
    git('fetch', '-q', 'origin')
    old = subprocess.run(['git', 'rev-parse', '-q', '--verify', 'origin/gh-pages^{tree}'],
                         capture_output=True, text=True, cwd=ROOT).stdout.strip()
    if old == tree:
        print('gh-pages is already up to date')
        return
    when = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    commit = git('commit-tree', tree, '-m', f'[PUB] Publishes the results site, {when}')
    git('update-ref', 'refs/heads/gh-pages', commit)
    print(git('push', '-f', 'origin', 'gh-pages'))
    print(f'pushed gh-pages {commit[:10]} (one commit, history replaced)')


def serve(port):
    build()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    with http.server.ThreadingHTTPServer(('127.0.0.1', port), handler) as srv:
        print(f'serving {SITE} on http://localhost:{port}/  (ctrl-c stops)')
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


def passwd(slug, password):
    kr = Keyring()
    if slug != 'master' and slug not in kr.keys:
        kr.ensure([slug])
    kr.pw[slug] = password or passphrase()
    kr.save()
    print(f'password for {slug}: {kr.pw[slug]}\nrun ./publish.py push to publish it')


def rekey(slug):
    kr = Keyring()
    kr.keys[slug] = {'key': b64(os.urandom(32)), 'salt': b64(os.urandom(16))}
    kr.save()
    print(f'new content key for {slug}: remembered keys and old share links stop working after the next push')


def link(slug):
    site = load_yaml(CONTENT / 'site.yml')
    key = base64.urlsafe_b64encode(Keyring().key(slug)).rstrip(b'=').decode()
    print(f'{site.get("url", "").rstrip("/")}/{slug}/#k={key}')


def main():
    global CONTENT, SITE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['build', 'serve', 'push', 'passwd', 'rekey', 'link'])
    ap.add_argument('args', nargs='*')
    ap.add_argument('--content', type=Path, default=CONTENT, help='plaintext source (default: content/)')
    ap.add_argument('--site', type=Path, default=SITE, help='build output (default: _site/)')
    a = ap.parse_args()
    CONTENT, SITE = a.content.resolve(), a.site.resolve()
    if a.cmd == 'build':
        build()
    elif a.cmd == 'serve':
        serve(int(a.args[0]) if a.args else 8000)
    elif a.cmd == 'push':
        push()
    elif a.cmd == 'passwd':
        if not a.args:
            die('passwd <slug|master> [password]')
        passwd(a.args[0], a.args[1] if len(a.args) > 1 else None)
    elif a.cmd == 'rekey':
        rekey(a.args[0])
    elif a.cmd == 'link':
        link(a.args[0])


if __name__ == '__main__':
    main()
