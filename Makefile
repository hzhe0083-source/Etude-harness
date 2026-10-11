# Etude VLA harness v1 — common entry points (docs/design/vla_harness_v1.md §5).
# No tests in this repo: verification = component `--demo` runs on fakes + the AST check.
#
#   make ast-check            frozen forbidden-read AST check (§2 边界落地, §3.5)
#   make split                experiments/splits.json from Eval_Layout file names (§3.2)
#   make views                validate experiments/M*/view*.json (Research OS ViewSpec + R44)
#   make m0 [M0_ARGS=...]     M0 sweep (§5); M0_ARGS="--dry-run" to only print; RESEARCH_RUN=1 wraps in `research run`
#   make aa MILESTONE=M0 NSTAR=25 [AA_ARGS=--dry-run]    A/A re-runs on V (§3.2, §3.3)
#   make harness TASK=stack_bowls SEED=0 [N=25] [HARNESS_ARGS=--dry-run]   one harness evaluation (§5 M1+)
#   make demo                 every component --demo on fakes, the end-to-end fake episode, then split/views/dry-run sweep
#   make fake-episode         scripts/demo_fake_episode.py: real NodeRunner/verifier/memory/trace on fakes
#   make research-bootstrap   print the Research OS init/contract commands (the user runs the human parts)

SHELL := /bin/bash
UV ?= uv
PY := $(UV) run python

TASK ?= stack_bowls
SEED ?= 0
EVAL_NUM ?= 50
N ?=
MILESTONE ?= M0
NSTAR ?=
M0_ARGS ?=
AA_ARGS ?=
HARNESS_ARGS ?=

DEMO_MODULES ?= etude.fakes etude.core.budget etude.executor.vla etude.executor.ee_move \
	etude.executor.gripper etude.backends.pi05_remote etude.backends.pi05_inprocess etude.perceive etude.verifier etude.memory \
	etude.trace etude.mcp etude.planner etude.runner etude.adapter etude.evolution \
	etude.evolution.aa_noise etude.evolution.research_bridge etude.frozen.leakage \
	etude.scorer.audit etude.scorer.results

.PHONY: help ast-check split views m0 aa harness demo fake-episode research-bootstrap

help:
	@sed -n '1,14p' Makefile

ast-check:
	$(PY) -m etude.frozen.ast_check

split:
	$(PY) scripts/make_split.py

views:
	$(PY) experiments/expctl.py check-views

m0:
	bash scripts/m0_sweep.sh $(M0_ARGS)

aa:
	bash scripts/aa_rerun.sh --milestone $(MILESTONE) $(if $(NSTAR),--nstar $(NSTAR)) $(AA_ARGS)

harness:
	bash scripts/run_harness.sh --task $(TASK) --seed $(SEED) --eval-num $(EVAL_NUM) $(if $(N),--n $(N)) $(HARNESS_ARGS)

demo:
	@fail=(); log=$$(mktemp); for m in $(DEMO_MODULES); do \
	  if ! $(PY) -c "import importlib.util,sys; sys.exit(importlib.util.find_spec('$$m') is None)" 2>/dev/null; then \
	    echo "== $$m: not present, skipped"; continue; fi; \
	  echo "== $$m --demo"; \
	  if ! $(PY) -m $$m --demo > "$$log" 2>&1; then \
	    tail -n 20 "$$log"; fail+=("$$m"); else tail -n 2 "$$log"; fi; \
	done; rm -f "$$log"; \
	echo "== scripts/demo_fake_episode.py"; if ! out=$$($(PY) scripts/demo_fake_episode.py 2>&1); then fail+=(demo_fake_episode); fi; \
	echo "$$out" | tail -n 14; \
	echo "== make_split"; $(PY) scripts/make_split.py --print | tail -n 3 || fail+=(make_split); \
	echo "== views"; $(PY) experiments/expctl.py check-views || fail+=(views); \
	echo "== m0 sweep (dry run)"; bash scripts/m0_sweep.sh --dry-run --force --ns 25 --seeds 0 || fail+=(m0_sweep); \
	echo "== aa (dry run)"; bash scripts/aa_rerun.sh --milestone M0 --nstar 25 --reps 1 --dry-run --force || fail+=(aa_rerun); \
	if [[ $${#fail[@]} -gt 0 ]]; then echo "demo FAILED: $${fail[*]}"; exit 1; fi; echo "demo OK"

fake-episode:
	$(PY) scripts/demo_fake_episode.py

research-bootstrap: views
	$(PY) experiments/expctl.py bootstrap
