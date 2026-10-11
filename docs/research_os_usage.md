# Research OS in Etude: how the ledger records M0–M3 and evolution rounds

The design spec is docs/design/vla_harness_v1.md (§3.2 splits, §3.3 the evolution round and ε, §4 receipts, §5 milestones, §6 falsification conditions). The rules come from research/AGENTS.md, which is borrowed from a2r. The record shapes are in research/SCHEMA.json. The command generator is `etude/evolution/research_bridge.py`. The view files are owned by the experiments component (`experiments/<M>/{config.yaml, view*.json}`).

The Research OS records **authorised scientific facts**: the ledger holds who pre-registered which contract, which Run executed which commit, and the core result (successes/trials). It keeps no logs, no per-episode results and no narrative. traces/, keyframes, `_result.json` and gate aggregates stay with their own components, and the raw records of each Run are archived to HF.

## 0 Roles and authorisation (who runs what)

| Action | Agent may run it | Needs the human's verbatim words |
|---|---|---|
| `research init` | no | yes, `--human/--quote/--channel` |
| `hypothesis create`, `experiment create --mode committed`, `authority request` | yes | no |
| `authority grant` (contract, Decision, Claim supported/refuted, Run marked valid after the fact, view extension, Policy) | **never** | yes |
| `research run`, `observation create`, `decision propose`, `claim propose` | yes | no |
| `decision make`, `claim transition --to supported/refuted` | yes, but only with an existing grant/Policy | the grant was given by a human |
| `claim verify` | the verifier must not be the Claim's author | — |

The bridge only generates commands. It never writes the ledger. Every human field stays a placeholder (`<HUMAN_ID>`, `<人类原话，逐字>`, `<CHANNEL>`), and agents never fill it in. Writes require `RESEARCH_ACTOR=agent:<id>`.

## 1 One-time initialisation

```bash
uv run python -m etude.evolution.research_bridge --check-views          # every view passes ViewSpec + R44
# The user sets these 4 variables in their own words, then runs:
RESEARCH_HUMAN=<id> RESEARCH_QUOTE='<你的原话>' RESEARCH_CHANNEL=<渠道> RESEARCH_GOAL='<目标>' \
RESEARCH_ACTOR=agent:<id> bash scripts/research_bootstrap.sh
```

Script order: init → H-1/H-2/H-3 → 7 committed Experiments (each followed by its `authority request --scope experiment_contract`) → `pending-authority`. Then the user grants each contract and commits and pushes `research/ledger.json`. Write the aliases the CLI actually assigned back into `research.experiment / aa_experiment / t_experiment` in `experiments/<M>/config.yaml`. The bridge resolves aliases from these fields first and falls back to the default order below.

Regenerate the script after a config change: `uv run python -m etude.evolution.research_bridge --bootstrap-script > scripts/research_bootstrap.sh`.

## 2 View format (`experiments/<M>/view*.json`)

A view is the `value` of SCHEMA.json `$defs/ViewSpec`, and it becomes part of the contract when the Experiment is created with `--view`.

```json
{"rows": {"key": "<slot key>", "values": ["<str>", ...]},
 "cols": {"key": "<slot key>", "values": ["<str>", ...]},
 "cell": {"match": {"<slot key>": "<str>"}, "metric": "success_rate",
          "replicates": {"key": "<slot key>", "values": ["<str>", ...]}}}
```

Conventions (`research_bridge.validate_view` checks each one):

1. A key matches `^[a-z][a-z0-9_]*$`. **Every value is a string** (`"25"`, not `25`). The rows, cols, replicates and match keys are all distinct, and match must not fix an axis key (R44).
2. `cell.metric` is always `success_rate`. This is the evaluation core metric, and `successes/trials` are written alongside it.
3. A cell's slot = `cell.match` ∪ {rows.key: r, cols.key: c, replicates.key: one replicate value}. **A Run's slot must equal it exactly**, with no extra and no missing keys. Otherwise the cell will not show the Run.
4. Every Experiment has exactly one view. Because of that, A/A and T are separate Experiments: `view_aa.json` uses `cell.match = {"split":"V"}` and `view_T.json` uses `{"split":"T"}`.
5. `stage` is reserved for **auxiliary slots** and must not be used as an axis key. An auxiliary slot = `{"stage": <name>}` ∪ that stage's keys. Its axis values must still lie inside the view (`slot_outside_view`), but it never equals a cell, so `research cell` does not show it. Current auxiliary stages: `verifier_audit` in E-M0 (cell keys) and `L1` in E-M3 (`surface, candidate`).
6. Adding values to a committed view requires `experiment extend` plus human authorisation (A6). Never fill a cell before its value exists.

## 3 Mapping: hypotheses, Experiments, cells, Runs

| Logical name | Default alias (fresh ledger) | Hypothesis | kind | view | rows / cols / replicates (current files) |
|---|---|---|---|---|---|
| H-cond2 | H-1 | condition 2: H1 beats the best N | — | — | — |
| H-cond3 | H-2 | condition 3: read-only node-sequence memory | — | — | — |
| H-cond4 | H-3 | condition 4: surface-by-surface evolution, H* beats frozen H2 | — | — | — |
| E-M0 | E-1 | H-1 (baseline: condition 1) | search | M0/view.json | task(4) / n∈{5,10,15,25,50} / seed{0,1}; held-out rows fill only the n=N* column |
| E-M0-AA | E-2 | H-1 | replication | M0/view_aa.json | task(dev 2) / condition{pi05_nstar} / rep{1,2,3}; match split=V |
| E-M1 | E-3 | H-1 | main | M1/view.json | task(4) / condition{h1,h1_nstar} / seed{0,1} |
| E-M1-AA | E-4 | H-1 | replication | M1/view_aa.json | task(dev 2) / condition{h1} / rep{1,2,3}; match split=V |
| E-M2 | E-5 | H-2 | main | M2/view.json | task(dev 2) / condition{h2,shuffled} / seed{0,1} |
| E-M3 | E-6 | H-3 | search | M3/view.json | surface{planner_prompt,recovery_rules,skill_md} / candidate{incumbent,r1..r4} / seed{0,1} |
| E-M3-T | E-7 | H-3 | main | M3/view_T.json | task(4) / condition{h2_frozen,h_star} / seed{0,1,2}; match split=T |

The kinds follow `research.kind` in each config. When a config gives none, the default is `main`. The view axes are authoritative in the files; this table is only a snapshot.

**Decision rules** (`--decision-rule`) are always built from verbatim text of design §3/§5/§6 (`research_bridge.decision_rule()`). `verify_rules_against_design()` re-checks the quotes on every demo run.
- E-M0: the §5 M0 pre-registered pass condition.
- E-M1: the §5 M1 pass condition, plus the §6 rows "条件 2 不超过最好的 N" and "收益全来自缩短重查步长".
- E-M2: the §5 M2 pass condition, plus the §6 row "条件 3 在留出上缩水". That row uses seed 2, which belongs to T, so it is checked when M3 ends.
- E-M3: the §5 M3 pass condition, plus the §3.1 gate chain, the §5 M3 blocker, and the §6 stop rule.
- E-M3-T: the §5 M3 pass condition, plus the §6 rows "进化后 seed 2 不超过冻结 harness" and "同回合同 VLM 预算 M3 ≤ M2".
- A/A: the §3.2 A/A row, plus the §3.3 ε formula, plus the §5 M3 blocker (ε ≤ 15).

**What counts as one Run** (R50; the slot is unique):
- One evaluation sweep cell: E-M0 `task×n×seed`, E-M1/E-M2 `task×condition×seed`, E-M3-T `task×condition×seed`. Each is 50 episodes, one per layout.
- One A/A rerun: E-M*-AA `task×condition×rep`.
- One gate of one candidate:
  - L3 is the E-M3 cell `surface×candidate(r_k)×seed=1`, with 100 paired V episodes.
  - L2 is the cell with `seed=0`, using regression set F, ≤20 episodes.
  - L1 is the auxiliary slot `stage=L1, surface, candidate`, with ≤5 medoid episodes.
  - The incumbent's cache on V is the `candidate=incumbent, seed=1` cell for that surface.
  - L0 is a static check and needs no Run. Its result goes into the receipt.
- Verifier audit (M0's FPR/FNR): E-M0 auxiliary slot `stage=verifier_audit`. Its metric uses the same evaluation keys, but in this slot `successes` counts verifier Pass with an official fail, `trials` counts official fails, and `success_rate` therefore reads as FPR. The parameters field `metric_semantics` states this.
- The candidate's real id (`c0018`) and its branch `cand/<id>` go in `--parameters`, not in the slot.

**Failure and rework**:
- A failed Run is never deleted.
- Interrupted from outside with the definition unchanged: run `research run-interrupt`, then `research run --resume/--restart`.
- Code had to change: mark the old Run invalid (`run-validity --to invalid --reason …`), then create a new one in the same slot with `--supersedes <old Run>` (`run_command(..., supersedes=...)`).
- Archiving logs does not raise validity. A slot can be superseded only when its current Run is invalid or one of its dependencies has gone invalid (R41).

## 4 Run command and preconditions

```python
from etude.evolution.research_bridge import run_command, check_run_preconditions
plan = run_command("E-M0", slot={"task": "stack_bowls", "n": 25, "seed": 0},
                   args=["--task", "stack_bowls", "--seed", "0", "--eval-num", "50", "--n", "25"],
                   checkpoint="model=<OWNER>/robodojo-pi05-arx_x5-joint-0@<40hex>:ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999")
plan.write_parameters()          # runs/params/E-1/....json (runs/ is git-ignored, so the tree stays clean)
print(plan.command.shell())      # research run --experiment E-1 --kind evaluation --archive-to model=... --slot ... -- bash scripts/run_m0.sh ...
```

- **`--parameters` is a file path.** `cmd_runs._plan_fields` calls `Path(args.parameters).read_text()`, so inline JSON fails. The file holds at most 20 scalar values.
- **Checkpoint.** The official π0.5 weights live in an HF **dataset** repo: `dataset=RoboDojo-Benchmark/RoboDojo@35efbc7dedfdbeeb6e95fb749bd885d73d483e41:ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999`. The revision comes from the local download cache. The vendored rule `rules._plan_violations` requires an evaluation's `--checkpoint` to be `model=` and `--archive-to` to name the same repo. Before the first evaluation Run, the user therefore does the following once:
  1. Create a model repo of their own, `<OWNER>/robodojo-pi05-arx_x5-joint-0`.
  2. Upload the official checkpoint to it at the same path, and record the source dataset revision above in its README.
  3. Pin the model repo's 40-character commit.
  4. Archive raw Run records to that same model repo.

  The alternative is to change the vendored rules, which is not this component's call. Keep `experiments/<M>/config.yaml model.checkpoint_hf` as the official source pin.
- **Entrypoint.** It must be a Git-tracked script, run as `bash scripts/run_m0.sh …` or `bash scripts/run_harness.sh …` (`research.entry` in config). `-c/-lc/-e` are forbidden. Any `.json/.yaml` argument is either a tracked file or a checksummed file declared with `--input`.
- **Metrics.** The entry script writes `$RESEARCH_METRICS` = `{"successes":k,"trials":n,"success_rate":k/n}` (`research_bridge.write_metrics`). Products are written to `$RESEARCH_OUTPUTS` as `{name: "model|dataset=OWNER/NAME@REV[:path]"}` (`write_outputs`). Details go to `$RESEARCH_RUN_DIR`, and raw logs land in `runs/research/<Run>/attempt-N/`.
- **Preconditions** (`check_run_preconditions(repo, entrypoint=…, candidate=…, fetch=False)` reports each failure in readable form; `--preconditions` on the CLI):
  1. The work tree is clean, untracked files included. Only `research/ledger.json`, `WORK` and `WORKSTATE` are exempt.
  2. HEAD exists and has been pushed to an `origin/*` branch. With `fetch=True` the check runs the real `capture.git_capture`, which fetches.
  3. The entry script is tracked.
  4. `research/ledger.json` exists.
  5. `huggingface_hub` is installed. Use the pinned version in research/requirements.txt; pyproject.toml stays unchanged.
- **Candidates** (M3):
  1. P6 commits each candidate to the `cand/<id>` branch in `evolution/worktrees/<id>` and runs `git push origin cand/<id>`.
  2. Run `research run` from that worktree with `--root <main checkout>/research`, or set `RESEARCH_OS_ROOT`. Otherwise the run writes into the worktree's own copy of the ledger.
  3. `check_run_preconditions(..., candidate="<id>")` checks the branch name and that it has been pushed.

## 5 Receipts → Observation / Decision

When an evolution round ends (§3.3 `evolve_round`), set `Receipt.research_run` to the Run label of L3, or of the highest gate reached. Then:

```python
for c in receipt_to_observation(receipt, extra_runs=["E6-R010", "E6-R011"]):   # L1/L2 Runs
    print(c.who, c.shell())
```

The output has five steps:
1. `observation create`, run by an agent. It quotes the receipt's numbers as fact and draws no conclusion.
2. `decision propose`, run by an agent. On accept it proposes merging the candidate; on reject it proposes keeping the incumbent.
3. `authority request --scope decision --to made`, run by an agent.
4. `authority grant`, run **only by the user**, quoting their own words.
5. `decision make --grant <event_id>`.

Any Decision that cites a Run, Observation or Claim needs authority (A3). Milestone conclusions are Claims (`claim propose` with supporting/contradicting evidence). The user grants `supported/refuted`, and `claim verify` comes from a verifier who is not the Claim's author.

## 6 Bridge API overview

`experiment_commands(M)`, `experiment_command(E)`, `bootstrap_commands()`, `bootstrap_script()`, `init_command()`, `hypothesis_command(H)`, `decision_rule(E)`, `verify_rules_against_design()`, `view_path/load_view/validate_view/check_views`, `slot_for(E, stage=…, **coords)`, `run_command(...) -> RunPlan`, `checkpoint_ref(...)`, `write_metrics(path, successes, trials)`, `write_outputs(path, outputs)`, `receipt_statement(r)`, `receipt_to_observation(r)`, `check_run_preconditions(repo, ...)`, `syntax_check(commands)`. This last one parses each generated command with the vendored CLI's argparse and executes nothing.

Demo: `uv run python -m etude.evolution.research_bridge --demo`.
