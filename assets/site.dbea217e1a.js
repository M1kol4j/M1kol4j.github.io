/* Results site: unlocks a project with its password (or the master password),
   then decrypts the page and its media in the browser. WebCrypto only:
   PBKDF2-SHA256 unwraps the project's content key, AES-256-GCM opens the files. */
'use strict';
(() => {
  const te = new TextEncoder(), td = new TextDecoder();
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const el = (tag, attrs = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') e.className = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) e.setAttribute(k, v === true ? '' : v);
    }
    e.append(...kids);
    return e;
  };

  // ------------------------------------------------------------ bytes / keys
  const fromB64 = s => {
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(s + '='.repeat((4 - s.length % 4) % 4)), c => c.charCodeAt(0));
  };
  const toB64 = u8 => {
    let s = '';
    for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode(...u8.subarray(i, i + 0x8000));
    return btoa(s);
  };
  const toB64url = u8 => toB64(u8).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

  const PREFIX = 'results:';
  const store = {
    _get(s, k) { try { return s.getItem(PREFIX + k); } catch { return null; } },
    get(k) { return this._get(sessionStorage, k) || this._get(localStorage, k); },
    remembered(k) { return !!this._get(localStorage, k); },
    set(k, v, persist) {
      this.del(k);
      try { (persist ? localStorage : sessionStorage).setItem(PREFIX + k, v); } catch { /* private mode */ }
    },
    del(k) { for (const s of [sessionStorage, localStorage]) try { s.removeItem(PREFIX + k); } catch { } },
    keys() {
      const out = new Set();
      for (const s of [sessionStorage, localStorage])
        try { for (let i = 0; i < s.length; i++) if (s.key(i).startsWith(PREFIX)) out.add(s.key(i).slice(PREFIX.length)); } catch { }
      return [...out];
    },
  };

  const subtle = () => {
    if (!window.crypto || !crypto.subtle) throw new Error('this browser cannot decrypt here (it needs https)');
    return crypto.subtle;
  };
  const aesKey = raw => subtle().importKey('raw', raw, 'AES-GCM', false, ['decrypt']);
  async function kekBits(pw, salt, iter) {
    const base = await subtle().importKey('raw', te.encode(pw.normalize('NFC')), 'PBKDF2', false, ['deriveBits']);
    const bits = await subtle().deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: fromB64(salt), iterations: iter }, base, 256);
    return new Uint8Array(bits);
  }
  async function unwrap(bits, w) {
    try { return new Uint8Array(await subtle().decrypt({ name: 'AES-GCM', iv: fromB64(w.iv) }, await aesKey(bits), fromB64(w.ct))); }
    catch { return null; }
  }
  const openBox = (key, buf) => {
    const u = new Uint8Array(buf);
    return subtle().decrypt({ name: 'AES-GCM', iv: u.subarray(0, 12) }, key, u.subarray(12));
  };
  async function tryPassword(lock, pw) {
    for (const salt of [...new Set(lock.wraps.map(w => w.salt))]) {
      const bits = await kekBits(pw, salt, lock.iter);
      for (const w of lock.wraps.filter(w => w.salt === salt)) {
        const ck = await unwrap(bits, w);
        if (ck) return { ck, master: w.kind === 'master' ? bits : null };
      }
    }
    return null;
  }

  async function fetchBytes(url, onProgress, fresh) {
    const r = await fetch(url, fresh ? { cache: 'no-cache' } : {});
    if (!r.ok) throw new Error(`${r.status} ${url}`);
    const total = +r.headers.get('content-length') || 0;
    if (!onProgress || !r.body) return r.arrayBuffer();
    const reader = r.body.getReader(), parts = [];
    let got = 0;
    for (; ;) {
      const { done, value } = await reader.read();
      if (done) break;
      parts.push(value);
      got += value.length;
      onProgress(total ? Math.min(1, got / total) : -1);
    }
    const out = new Uint8Array(got);
    let o = 0;
    for (const p of parts) { out.set(p, o); o += p.length; }
    return out.buffer;
  }

  const fmtDate = iso => new Date(iso.slice(0, 10) + 'T12:00:00Z')
    .toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
  function toast(msg) {
    const t = el('div', { class: 'toast', role: 'status' }, msg);
    document.body.append(t);
    setTimeout(() => t.remove(), 3200);
  }
  function shake(form) { form.classList.remove('shake'); void form.offsetWidth; form.classList.add('shake'); }

  // ------------------------------------------------------------------ media
  class Media {
    constructor(key, assets) { this.key = key; this.assets = assets; this.urls = new Map(); }
    url(id, onProgress) {
      if (!this.urls.has(id)) {
        const p = (async () => {
          const plain = await openBox(this.key, await fetchBytes(`m/${id}.bin`, onProgress));
          return URL.createObjectURL(new Blob([plain], { type: this.assets[id]?.mime || 'application/octet-stream' }));
        })();
        p.catch(() => this.urls.delete(id));
        this.urls.set(id, p);
      }
      return this.urls.get(id);
    }
    async image(id, onProgress) {
      const img = new Image();
      img.src = await this.url(id, onProgress);
      await img.decode();
      return img;
    }
  }

  function lazy(nodes, fn, margin = '600px') {
    const io = new IntersectionObserver(es => {
      for (const e of es) if (e.isIntersecting) { io.unobserve(e.target); fn(e.target); }
    }, { rootMargin: margin });
    nodes.forEach(n => io.observe(n));
  }
  // plays a movie while at least 30 % of it is on screen, unless the reader paused it
  const onScreen = new IntersectionObserver(es => { for (const e of es) e.target._vis?.(e.isIntersecting); }, { threshold: 0.3 });

  function loadBar(box) {
    const bar = el('div', { class: 'load' }, el('i'));
    box.append(bar);
    return {
      set: f => { bar.firstChild.style.width = (f < 0 ? 35 : f * 100) + '%'; bar.classList.toggle('busy', f < 0); },
      done: () => bar.remove(),
      fail: msg => { bar.replaceWith(el('div', { class: 'load-err' }, msg)); },
    };
  }

  function wireImages(root, media) {
    lazy($$('img[data-asset]', root), async img => {
      const box = img.closest('.media') || img.parentElement, bar = box.classList.contains('media') ? loadBar(box) : null;
      try {
        img.src = await media.url(img.dataset.asset, bar?.set);
        bar?.done();
      } catch (e) { bar ? bar.fail('could not load: ' + e.message) : console.error(e); }
    });
    root.addEventListener('click', e => {
      const img = e.target.closest('.fig .media img');
      if (!img || !img.src) return;
      const lb = el('div', { class: 'lightbox', onclick: () => lb.remove() }, el('img', { src: img.src, alt: img.alt }));
      document.body.append(lb);
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') $('.lightbox')?.remove(); });
  }

  function wireVideos(root, media) {
    lazy($$('video[data-asset]', root), async v => {
      const bar = loadBar(v.parentElement);
      try {
        if (v.dataset.poster) v.poster = await media.url(v.dataset.poster);
        v.src = await media.url(v.dataset.asset, bar.set);
        bar.done();
      } catch (e) { bar.fail('could not load: ' + e.message); return; }
      if (!v.hasAttribute('data-autoplay')) return;
      v.addEventListener('pause', () => { if (!v._auto) v._held = true; v._auto = false; });
      v.addEventListener('play', () => { v._held = false; });
      v._vis = on => {
        if (on && !v._held) v.play().catch(() => { });
        else if (!on && !v.paused) { v._auto = true; v.pause(); }
      };
      onScreen.observe(v);
    }, '200px');
  }

  function wireLinks(root, media) {
    root.addEventListener('click', async e => {
      const a = e.target.closest('a[data-asset-link]');
      if (!a) return;
      e.preventDefault();
      const url = await media.url(a.dataset.assetLink);
      el('a', { href: url, download: a.dataset.name || 'download' }).click();
    });
  }

  // ---------------------------------------------------------- compare widget
  const MODES = { slider: 'Slider', blink: 'Blink', side: 'Side by side', diff: 'Difference' };
  const ICON = { play: '▶', pause: '❚❚' };

  class Compare {
    constructor(fig, media) {
      this.fig = fig; this.media = media;
      this.cfg = JSON.parse(fig.dataset.cmp);
      this.isVideo = this.cfg.kind === 'video';
      this.split = 0.5; this.which = 0; this.period = 500;
      this.zoom = 1; this.cx = 0.5; this.cy = 0.5;
      this.stage = $('.cmp-stage', fig);
      this.canvas = el('canvas', { class: 'cmp-canvas' });
      this.ctx = this.canvas.getContext('2d');
      this.divider = el('div', { class: 'cmp-divider' }, el('span'));
      this.labels = el('div', { class: 'cmp-labels' }, el('span'), el('span'));
      this.stage.append(this.canvas, this.divider, this.labels);
      this.bar = el('div', { class: 'cmp-bar' });
      this.stage.after(this.bar);
      fig.tabIndex = 0;
      this.controls();
      this.events();
      this.setMode(Object.hasOwn(MODES, this.cfg.mode) ? this.cfg.mode : 'slider');
    }

    async start() {
      const c = this.cfg, bar = loadBar(this.stage);
      try {
        if (this.isVideo) {
          if (c.poster) { this.frame = await this.media.image(c.poster); this.redraw(); }
          const v = this.video = el('video', { class: 'cmp-video', muted: true, loop: true, playsinline: true, preload: 'auto' });
          v.muted = true;
          this.stage.prepend(v);
          const ready = new Promise((ok, bad) => {
            v.addEventListener('loadeddata', ok, { once: true });
            v.addEventListener('error', () => bad(new Error('the browser cannot play this movie')), { once: true });
          });
          v.src = await this.media.url(c.src, bar.set);
          await ready;
          this.frame = v;
          v.addEventListener('seeked', () => { this.redraw(); this.sync(); });
          v.addEventListener('play', () => { this.sync(); this.loop(); });
          v.addEventListener('pause', () => this.sync());
          this.fig._vis = on => {
            if (on && !this.held) v.play().catch(() => { });
            else if (!on && !v.paused) v.pause();
          };
          onScreen.observe(this.fig);
        } else {
          this.imgs = await Promise.all([c.a, c.b].map(id => this.media.image(id, bar.set)));
        }
        bar.done();
        this.redraw();
      } catch (e) { bar.fail('could not load: ' + e.message); }
    }

    // -- drawing
    source(i) {
      const { w, h, layout } = this.cfg;
      if (this.isVideo) return [this.frame, layout === 'h' ? i * w : 0, layout === 'v' ? i * h : 0];
      return [this.imgs?.[i], 0, 0];
    }
    blit(i, dx) {
      const [src, x0, y0] = this.source(i);
      if (!src) return;
      const { w, h } = this.cfg, sw = w / this.zoom, sh = h / this.zoom;
      this.ctx.drawImage(src, x0 + (this.cx * w - sw / 2), y0 + (this.cy * h - sh / 2), sw, sh, dx, 0, w, h);
    }
    draw() {
      const { w, h } = this.cfg, ctx = this.ctx, W = this.mode === 'side' ? 2 * w : w;
      if (this.canvas.width !== W || this.canvas.height !== h) { this.canvas.width = W; this.canvas.height = h; }
      ctx.imageSmoothingEnabled = this.zoom < 3;
      ctx.globalCompositeOperation = 'source-over';
      ctx.clearRect(0, 0, W, h);
      if (this.mode === 'side') { this.blit(0, 0); this.blit(1, w); }
      else if (this.mode === 'blink') this.blit(this.which, 0);
      else if (this.mode === 'diff') {
        this.blit(0, 0);
        ctx.globalCompositeOperation = 'difference';
        this.blit(1, 0);
        ctx.globalCompositeOperation = 'source-over';
      } else {
        this.blit(1, 0);
        ctx.save();
        ctx.beginPath();
        ctx.rect(0, 0, this.split * w, h);
        ctx.clip();
        this.blit(0, 0);
        ctx.restore();
      }
    }
    redraw() {
      this.draw();
      this.divider.style.left = (this.split * 100) + '%';
      const [la, lb] = this.labels.children, [A, B] = this.cfg.labels;
      if (this.mode === 'blink') { la.textContent = this.which ? B : A; lb.textContent = ''; }
      else if (this.mode === 'diff') { la.textContent = `|${A} − ${B}|`; lb.textContent = ''; }
      else { la.textContent = A; lb.textContent = B; }
      lb.hidden = !lb.textContent;
      this.zoomLabel.textContent = this.zoom === 1 ? '1×' : this.zoom.toFixed(this.zoom < 10 ? 1 : 0) + '×';
      this.canvas.classList.toggle('zoomed', this.zoom > 1);
    }
    loop() {
      if (this.raf) return;
      const tick = () => {
        this.draw();
        this.sync();
        this.raf = this.video && !this.video.paused ? requestAnimationFrame(tick) : 0;
      };
      this.raf = requestAnimationFrame(tick);
    }

    // -- transport
    get fi() { return this.video ? clamp(Math.floor(this.video.currentTime * this.cfg.fps + 1e-3), 0, this.cfg.frames - 1) : 0; }
    sync() {
      if (!this.video) return;
      const f = this.fi;
      this.scrub.value = f;
      this.flabel.textContent = `${f + 1} / ${this.cfg.frames}`;
      this.playBtn.textContent = this.video.paused ? ICON.play : ICON.pause;
    }
    toggle() {
      const v = this.video;
      if (!v) return;
      if (v.paused) { this.held = false; v.play().catch(() => { }); } else { this.held = true; v.pause(); }
    }
    seekFrame(f) {
      const v = this.video;
      if (!v) return;
      this.held = true;
      v.pause();
      v.currentTime = (clamp(f, 0, this.cfg.frames - 1) + 0.5) / this.cfg.fps;
    }
    step(d) { this.seekFrame(this.fi + d); }

    // -- view
    setMode(m) {
      this.mode = m;
      this.fig.dataset.mode = m;
      this.modeBtns.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === m)));
      this.stage.style.aspectRatio = `${(m === 'side' ? 2 : 1) * this.cfg.w} / ${this.cfg.h}`;
      this.rateSel.hidden = m !== 'blink';
      this.which = 0;
      this.blinkTimer();
      this.redraw();
    }
    blinkTimer() {
      clearInterval(this.timer);
      if (this.mode === 'blink' && this.period > 0)
        this.timer = setInterval(() => { this.which ^= 1; this.redraw(); }, this.period);
    }
    clampView() {
      const hw = 0.5 / this.zoom;
      this.cx = clamp(this.cx, hw, 1 - hw);
      this.cy = clamp(this.cy, hw, 1 - hw);
    }
    zoomAt(f, p = { x: 0.5, y: 0.5 }) {
      const z = clamp(this.zoom * f, 1, 32);
      const sx = this.cx + (p.x - 0.5) / this.zoom, sy = this.cy + (p.y - 0.5) / this.zoom;
      this.zoom = z;
      this.cx = sx - (p.x - 0.5) / z;
      this.cy = sy - (p.y - 0.5) / z;
      this.clampView();
      this.redraw();
    }
    resetZoom() { this.zoom = 1; this.cx = this.cy = 0.5; this.redraw(); }
    pt(e) {
      const r = this.canvas.getBoundingClientRect();
      let x = (e.clientX - r.left) / r.width;
      const y = (e.clientY - r.top) / r.height;
      if (this.mode === 'side') x = (x * 2) % 1;
      return { x, y };
    }

    controls() {
      const btn = (text, title, fn, extra = {}) => el('button', { type: 'button', title, 'aria-label': title, onclick: fn, ...extra }, text);
      const b = this.bar;
      if (this.isVideo) {
        this.playBtn = btn(ICON.play, 'Play / pause (space)', () => this.toggle(), { class: 'play' });
        this.scrub = el('input', { type: 'range', class: 'scrub', min: 0, max: this.cfg.frames - 1, value: 0, step: 1, 'aria-label': 'Frame' });
        this.scrub.addEventListener('input', () => this.seekFrame(+this.scrub.value));
        this.flabel = el('span', { class: 'flabel' }, `1 / ${this.cfg.frames}`);
        const speed = el('select', { 'aria-label': 'Speed', title: 'Speed' },
          ...[['0.25', '¼×'], ['0.5', '½×'], ['1', '1×'], ['2', '2×']]
            .map(([v, t]) => el('option', { value: v, selected: v === '1' }, t)));
        speed.addEventListener('change', () => { if (this.video) this.video.playbackRate = +speed.value; });
        b.append(el('div', { class: 'row' },
          el('div', { class: 'grp' }, btn('‹', 'Previous frame (←)', () => this.step(-1)), this.playBtn,
            btn('›', 'Next frame (→)', () => this.step(1))),
          this.scrub, this.flabel, speed));
      }
      this.modeBtns = Object.entries(MODES).map(([m, t], i) => btn(t, `${t} (${i + 1})`, () => this.setMode(m), { 'data-mode': m }));
      this.rateSel = el('select', { 'aria-label': 'Blink interval', title: 'Blink interval (b switches by hand)' },
        ...[['0', 'by hand'], ['250', '0.25 s'], ['500', '0.5 s'], ['1000', '1 s']]
          .map(([v, t]) => el('option', { value: v, selected: v === '500' }, t)));
      this.rateSel.addEventListener('change', () => { this.period = +this.rateSel.value; this.blinkTimer(); });
      this.zoomLabel = el('span', { class: 'zlabel' });
      b.append(el('div', { class: 'row' },
        el('div', { class: 'seg', role: 'group', 'aria-label': 'Comparison mode' }, ...this.modeBtns),
        this.rateSel, el('span', { class: 'sp' }),
        el('div', { class: 'grp' }, btn('−', 'Zoom out (−)', () => this.zoomAt(1 / 1.5)), this.zoomLabel,
          btn('+', 'Zoom in (+); ctrl + wheel or pinch zooms at the pointer', () => this.zoomAt(1.5)),
          btn('↺', 'Reset zoom (0, or double-click)', () => this.resetZoom()))));
    }

    events() {
      const c = this.canvas;
      let drag = null;
      c.addEventListener('pointerdown', e => {
        this.fig.focus({ preventScroll: true });
        const p = this.pt(e);
        if (this.mode === 'slider' && (this.zoom === 1 || Math.abs(p.x - this.split) < 0.04)) {
          drag = 'split';
          this.split = clamp(p.x, 0, 1);
        } else if (this.zoom > 1) {
          drag = { x: e.clientX, y: e.clientY, cx: this.cx, cy: this.cy };
        } else if (this.mode === 'blink') {
          this.which ^= 1;
        } else return;
        c.setPointerCapture(e.pointerId);
        this.redraw();
      });
      c.addEventListener('pointermove', e => {
        if (!drag) return;
        if (drag === 'split') this.split = clamp(this.pt(e).x, 0, 1);
        else {
          const r = c.getBoundingClientRect(), pw = this.mode === 'side' ? r.width / 2 : r.width;
          this.cx = drag.cx - (e.clientX - drag.x) / pw / this.zoom;
          this.cy = drag.cy - (e.clientY - drag.y) / r.height / this.zoom;
          this.clampView();
        }
        this.redraw();
      });
      const end = () => { drag = null; };
      c.addEventListener('pointerup', end);
      c.addEventListener('pointercancel', end);
      c.addEventListener('wheel', e => {
        if (!(e.ctrlKey || e.metaKey)) return;
        e.preventDefault();
        this.zoomAt(Math.exp(-e.deltaY * 0.0025), this.pt(e));
      }, { passive: false });
      c.addEventListener('dblclick', () => this.resetZoom());
      this.fig.addEventListener('keydown', e => {
        if (e.target.matches('select, input') || e.metaKey || e.ctrlKey || e.altKey) return;
        const k = e.key;
        if (k === ' ' && e.target.matches('button')) return;
        if (k === ' ' && this.isVideo) this.toggle();
        else if (k === 'ArrowLeft' && this.isVideo) this.step(-1);
        else if (k === 'ArrowRight' && this.isVideo) this.step(1);
        else if (/^[1-4]$/.test(k)) this.setMode(Object.keys(MODES)[+k - 1]);
        else if (k === 'b' && this.mode === 'blink') { this.which ^= 1; this.redraw(); }
        else if (k === '+' || k === '=') this.zoomAt(1.5);
        else if (k === '-') this.zoomAt(1 / 1.5);
        else if (k === '0') this.resetZoom();
        else return;
        e.preventDefault();
      });
    }
  }

  // ------------------------------------------------------------ project page
  async function projectPage() {
    const lock = JSON.parse($('#lock').textContent), K = 'ck:' + lock.slug;
    const box = fetchBytes('page.bin', null, true);
    box.catch(() => { });
    const m = location.hash.match(/[#&]k=([\w-]+)/);
    if (m) {
      store.set(K, toB64(fromB64(m[1])), store.remembered(K));
      history.replaceState(null, '', location.pathname + location.search);
    }
    try {
      const saved = store.get(K);
      if (saved && await show(lock, box, fromB64(saved), store.remembered(K))) return;
      const mb = store.get('master'), mw = lock.wraps.find(w => w.kind === 'master');
      if (mb && mw) {
        const ck = await unwrap(fromB64(mb), mw);
        if (ck && await show(lock, box, ck, store.remembered('master'))) return;
      }
    } catch (e) { return gate(lock, box, e.message); }
    store.del(K);
    gate(lock, box);
  }

  function gate(lock, box, problem) {
    const g = $('#gate'), form = $('form', g), input = $('input[type=password]', g);
    const remember = $('input[type=checkbox]', g), msg = $('.gate-msg', g), btn = $('button', g);
    g.hidden = false;
    if (problem) { msg.textContent = problem; msg.className = 'gate-msg err'; }
    input.focus();
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      if (!input.value) return;
      btn.disabled = true;
      msg.className = 'gate-msg';
      msg.textContent = 'Unlocking…';
      try {
        const res = await tryPassword(lock, input.value);
        if (res && await show(lock, box, res.ck, remember.checked)) {
          if (res.master) store.set('master', toB64(res.master), remember.checked);
          g.hidden = true;
          return;
        }
        msg.textContent = 'Wrong password.';
      } catch (e) { msg.textContent = e.message; }
      msg.className = 'gate-msg err';
      btn.disabled = false;
      shake(form);
      input.select();
    });
  }

  async function show(lock, box, ckRaw, persist) {
    const key = await aesKey(ckRaw);
    let page;
    try { page = JSON.parse(td.decode(await openBox(key, await box))); }
    catch (e) {
      if (e instanceof Error && /^\d{3} /.test(e.message)) throw new Error('Could not load this project (' + e.message + ').');
      return false;
    }
    store.set('ck:' + lock.slug, toB64(ckRaw), persist);
    render(lock, page, key, ckRaw);
    return true;
  }

  function render(lock, page, key, ckRaw) {
    document.title = page.title;
    const hero = $('.hero');
    hero.append(el('h1', {}, page.title));
    if (page.subtitle) hero.append(el('p', { class: 'subtitle' }, page.subtitle));
    if (page.authors?.length) hero.append(el('p', { class: 'authors' }, page.authors.join(' · ')));
    const meta = el('p', { class: 'meta' });
    for (const t of page.tags || []) meta.append(el('span', { class: 'chip' }, t));
    meta.append(el('span', { class: 'updated' }, 'Updated ' + fmtDate(page.updated)));
    hero.append(meta);

    const main = $('#content');
    main.innerHTML = page.html;
    for (const h of $$('h2, h3', main)) {
      if (!h.id) h.id = h.textContent.trim().toLowerCase().replace(/[^\w]+/g, '-').replace(/^-|-$/g, '');
    }
    const media = new Media(key, page.assets);
    wireImages(main, media);
    wireVideos(main, media);
    wireLinks(main, media);
    const cmps = $$('figure.cmp', main).map(f => new Compare(f, media));
    lazy(cmps.map(c => c.fig), f => cmps.find(c => c.fig === f).start(), '400px');
    markNew(lock.slug, main);

    const acts = $('.foot .acts');
    acts.append(
      el('button', {
        type: 'button', class: 'linkbtn', title: 'A link that opens this page without the password',
        onclick: async () => {
          const url = `${location.origin}${location.pathname}#k=${toB64url(ckRaw)}`;
          try { await navigator.clipboard.writeText(url); toast('Link copied. Anyone with it can read this page.'); }
          catch { prompt('Share link (anyone with it can read this page):', url); }
        },
      }, 'Copy share link'),
      el('button', {
        type: 'button', class: 'linkbtn', title: 'Forget the key on this device',
        onclick: () => { store.del('ck:' + lock.slug); location.reload(); },
      }, 'Lock'));
    $('#app').hidden = false;
    if (location.hash.length > 1) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
  }

  function markNew(slug, root) {
    const k = 'seen:' + slug;
    let seen = null;
    try { seen = localStorage.getItem(PREFIX + k); } catch { }
    if (seen) for (const f of $$('figure[data-date]', root)) if (f.dataset.date > seen) f.classList.add('is-new');
    try { localStorage.setItem(PREFIX + k, new Date().toISOString().slice(0, 16)); } catch { }
  }

  // -------------------------------------------------------------- index page
  function indexPage() {
    const site = JSON.parse($('#site').textContent);
    const relock = $('.relock');
    const refresh = () => {
      let any = false;
      for (const c of $$('[data-slug]')) {
        const open = !!store.get('ck:' + c.dataset.slug);
        c.classList.toggle('unlocked', open);
        any ||= open;
      }
      relock.hidden = !any && !store.get('master');
    };
    refresh();
    $('button', relock).addEventListener('click', () => {
      for (const k of store.keys()) if (k.startsWith('ck:') || k === 'master') store.del(k);
      refresh();
      toast('Locked on this device.');
    });
    const form = $('.unlock-all form'), input = $('input[type=password]', form);
    const remember = $('input[type=checkbox]', form), msg = $('.gate-msg', form), btn = $('button', form);
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      btn.disabled = true;
      msg.className = 'gate-msg';
      msg.textContent = 'Unlocking…';
      try {
        const bits = await kekBits(input.value, site.master_salt, site.iter);
        let n = 0;
        for (const slug of site.projects) {
          const lock = await (await fetch(`${slug}/lock.json`, { cache: 'no-cache' })).json();
          const w = lock.wraps.find(x => x.kind === 'master');
          const ck = w && await unwrap(bits, w);
          if (ck) { store.set('ck:' + slug, toB64(ck), remember.checked); n++; }
        }
        if (n) {
          store.set('master', toB64(bits), remember.checked);
          msg.textContent = `Unlocked ${n} project${n > 1 ? 's' : ''}.`;
          input.value = '';
          refresh();
        } else { msg.textContent = 'Wrong password.'; msg.className = 'gate-msg err'; shake(form); }
      } catch (e) { msg.textContent = e.message; msg.className = 'gate-msg err'; }
      btn.disabled = false;
    });
  }

  if (document.body.dataset.page === 'project') projectPage();
  else if (document.body.dataset.page === 'index') indexPage();
})();
