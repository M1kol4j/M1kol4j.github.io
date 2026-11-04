# Results site. Plaintext lives in content/ (git-ignored); only ciphertext is published.
#
#   make publish                 build, encrypt and force-push the site to gh-pages
#   make serve [PORT=8000]       build and preview on http://localhost:8000
#   make build                   build into _site/ only
#   make link P=dynamo           share link that opens a project without its password
#   make passwd P=dynamo [PW=..] new password for a project (or P=master); random if PW is empty
#   make rekey P=dynamo          new content key: revokes remembered keys and share links
#   make spec [RUN=stellar.12k]  print a run block for a page, filled by RUNSPEC (photo_tr utils/runspec.py in Dispatch-Bifrost)
#                                from the run's namelists and snapshots; without RUN the blank template
#   make refresh                 update status, snapshots, aux and sizes in every run card (publish does it too)
#   make clean                   remove _site/ and the movie cache

PY   ?= python3
PORT ?= 8000
DATA ?= /mn/stornext/d9/data/mikolajs/Dispatch/dispatch-bifrost/experiments/photo_tr/data
RUNSPEC ?= /mn/stornext/d9/data/mikolajs/Dispatch/dispatch-bifrost/experiments/photo_tr/utils/runspec.py

.PHONY: help build serve publish link passwd rekey spec refresh clean
help:
	@sed -n '1,/^$$/p' Makefile | sed 's/^# \{0,1\}//'
build:
	$(PY) publish.py build
serve:
	$(PY) publish.py serve $(PORT)
publish: refresh
	$(PY) publish.py push
link:
	@test -n "$(P)" || { echo 'make link P=<project>'; exit 1; }
	@$(PY) publish.py link $(P)
passwd:
	@test -n "$(P)" || { echo 'make passwd P=<project|master> [PW=...]'; exit 1; }
	$(PY) publish.py passwd $(P) $(if $(PW),'$(PW)')
rekey:
	@test -n "$(P)" || { echo 'make rekey P=<project>'; exit 1; }
	$(PY) publish.py rekey $(P)
spec:
	@$(PY) $(RUNSPEC) --data $(DATA) $(if $(RUN),--format pages $(RUN),--template)
refresh:
	@if [ -f $(RUNSPEC) ]; then $(PY) $(RUNSPEC) --data $(DATA) --refresh $(wildcard content/*/index.md); else echo 'no $(RUNSPEC): run cards not refreshed'; fi
clean:
	rm -rf _site _site.tmp content/.cache
