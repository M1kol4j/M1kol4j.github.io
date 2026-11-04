# M1kol4j.github.io

Password-protected results pages, served from GitHub Pages at <https://m1kol4j.github.io/>.

This branch holds the generator only. The plaintext (markdown, images, movies,
passwords and keys) lives in `content/`, which git ignores. `make publish`
encrypts everything and force-pushes the result as a single commit to the
`gh-pages` branch, which Pages serves. Old movie versions therefore never pile
up in the history.

## Use

```sh
make publish                  # build, encrypt, push to gh-pages
make serve                    # preview on http://localhost:8000
make link P=dynamo            # share link that opens a project without its password
make passwd P=dynamo          # new random password (PW='...' to choose one; P=master for all)
make rekey P=dynamo           # new content key: revokes remembered keys and old share links
```

Needs Python 3 with `pyyaml`, `markdown-it-py`, `pillow` and `cryptography`, and
`ffmpeg` / `ffprobe` for movies.

## Content

```
content/
  site.yml                    public: site title, tagline, list of projects (in order)
  .secrets.yml .keys.yml      passwords and content keys (mode 600, created on first build)
  <project>/index.md          front matter + markdown
  <project>/media/...         images and movies
```

Front matter: `title` and `summary` are public (index card and lock screen);
`subtitle`, `tags` and `authors` are encrypted with the page; `draft: true`
lists the project as "in preparation"; `updated` overrides the date, which is
otherwise the newest file of the project.

A paragraph holding only an image becomes a numbered figure, its alt text the
caption. Movies (`.mp4 .mov .webm .mkv .avi`) use the same syntax and are
converted to H.264 when needed; they play muted in a loop while on screen
(title `"still"` turns that off). Title `"wide"` widens a figure.

```markdown
![Caption with *markdown*, B<sub>z</sub> and τ = 1.](media/plot.png "wide")
![A movie.](media/i1xy.mp4)
```

A comparison of two images or two movies of the same size:

    ```compare
    a: media/stagger.mp4 | stagger
    b: media/hllx.mp4    | hllx
    mode: slider          # slider | blink | side | diff
    caption: Temperature at z = 2 Mm.
    ```

Movie pairs are stacked into one file, so both halves stay on the same frame.
Keys in the widget: space play/pause, ←/→ one frame, 1-4 modes, b switch (blink),
+/- zoom, 0 reset; drag pans when zoomed, ctrl + wheel or pinch zooms.

## Run specs

A ```` ```run ```` block becomes a card with the run's box, grid, refinement,
numerics (Boris cap, B0 split, extra diffusion), radiation (RT rays and bins,
genrad terms read from its radtab, incrad), heating limits, physics, boundaries,
output, data (where the snapshots are, how many, MHD, the aux each one holds,
sizes) and initial condition (model, seed field, corona).

The blocks come from the Dispatch-Bifrost tool `experiments/photo_tr/utils/runspec.py`
(`RUNSPEC` in the Makefile), which reads the run's `params.nml`, its input
`<run>.nml` for what the code does not dump, and its snapshot directories:

    make spec RUN=stellar.12k     # the block for a page (a run extended from another
                                  # is followed by the source run, folded)
    make spec                     # the blank template
    runspec.py stellar.12k        # the same summary on screen; --format mkdocs for the docs

Empty values and hints after ` # ` are not shown, `[Section]` starts a box,
any key can be added, and `collapsed: yes` (under `name:`) folds the card.
`make refresh`, which `make publish` runs first, rewrites the lines that change
while a run goes on (status, switched on, the Data box) in every run block of
`content/*/index.md` and adds the folded source run of an extension; everything
typed by hand stays.

## How the protection works

Each project has a random 256-bit content key. Every file of the project is
encrypted with it (AES-256-GCM), and the key itself is stored twice in
`<project>/lock.json`, wrapped under PBKDF2-SHA256 (600 000 rounds) of the
project password and of the master password. The browser derives the key from
the password and decrypts in place; nothing is sent to a server. Anyone can
download the ciphertext, so use passphrases (the default random ones are four
words).
