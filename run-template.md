Run spec template. Copy the block into content/<project>/index.md and fill it in,
or let `make spec RUN=<run data dir>` fill what params.nml records.
Empty lines are not shown on the page; text after " # " is a hint and is dropped.
Keys can be renamed, added or removed; [Section] lines start a new box.
Put `collapsed: yes` under `name:` to show the run folded.

```run
name:                     # run name, as in data/<name>
status:                   # e.g. running, snapshot 43, t = 430 (11 h 57 min)
machine:                  # e.g. Betzy, 16 nodes, 0.5 t.u. per wall hour
purpose:                  # one sentence: what this run is for

[Box]
size:                     # x × y × z in Mm
z range:                  # bottom … top, tau = 1 near z = 0
lateral:                  # periodic in x, y
gravity:

[Grid]
root resolution:
root cells:
patches:                  # root patches × cells per patch
MPI:                      # ranks

[Refinement]
levels:                   # level (dx), ...
regions:                  # where each level is forced
switched on:              # time refinement starts
criteria:                 # adaptive criteria, or static regions only

[Numerics]
code:                     # branch @ hash
solver:
reconstruction:
time stepping:
flux correction:
div B:

[Physics]
EOS:
radiation:                # RT, diffusion, optically thin / chromospheric losses
non-LTE H / He:           # HION atom, He, height ranges
conduction:               # Spitzer
viscosity:
resistivity:
ambient field:
other:

[Boundaries]
bottom:                   # e.g. field-free inflows
top:
T<sub>eff</sub> target:

[Output]
cadence:                  # snapshot interval, code units (solar time)
time unit:
kept:                     # e.g. 3-D every 10th snapshot, 2-D maps every one

[Data]
location:                 # directory with the snapshots
also on:                  # e.g. Betzy /cluster/work/users/<user>/photo.tr/data/<run>
snapshots:                # complete ones, their times, incomplete ones
primary:                  # variables in each .dat
aux:                      # extra variables in each snapshot, 3-D then 2-D maps
snapshot size:
total size:

[Initial condition]
start:                    # fresh start, or extended from <run> snapshot <n>
seed field:
corona:
```
