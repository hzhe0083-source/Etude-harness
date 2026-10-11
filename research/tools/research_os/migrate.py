"""One-time explicit conversion of an exported v0 state after HF readback verification."""
import json
from pathlib import Path

from . import store, hf
from .capture import compact
from .common import emit
from .state import State
from .validate import state_errors
from .errors import ResearchError


def cmd_migrate(args):
    root = Path(args.root or "research").resolve()
    if (root / store.LEDGER_FILE).exists():
        raise ResearchError("ledger already exists; migration never overwrites it")
    old = json.loads(Path(args.source).read_text())
    mapping = json.loads(Path(args.hf_map).read_text())
    for ref in mapping.values():
        hf.verify_ref(ref)
    for ident, record in old["records"].items():
        kind = old["kinds"][ident]
        if kind == "run":
            record["run_kind"] = "dataset" if old["records"][record["experiment"]]["kind"] == "dataset" else "training"
            record["parameters"] = {}
            record["archive_target"] = {k: mapping[ident][k] for k in ("type", "repo")}
            record["attempt"] = 1 + len(record.get("resumes", [])) + len(record.get("restarts", []))
            record["evidence"] = mapping[ident]
            record["archive_status"] = "pending" if record["status"] == "running" else "uploaded"
            record["provenance_gaps"] = list(record.get("capture_gaps", []))
            if "capture" in record:
                capture = record["capture"]
                if capture["git"].get("dirty"):
                    record["provenance_gaps"].append("historical_uncommitted_code")
                capture["git"] = {"repo": args.code_repo, "commit": capture["git"]["commit"]}
                record["capture"] = compact(capture)
            # Retain only results the source actually contains. Do not reinterpret steps as loss.
            from .metrics import KEYS
            record["metrics"] = {k:v for k,v in record["metrics"].items() if k in KEYS[record["run_kind"]]}
            if record["run_kind"] == "training" and not record["metrics"]:
                record["provenance_gaps"].append("core_loss_not_recorded")
            for key in ("artifacts", "validity_history", "resumes", "restarts", "interruptions"):
                record.pop(key, None)
            if "dataset_result" in record:
                record["dataset_result"]["evidence"] = mapping[ident]
        elif kind == "experiment":
            if "dataset_card" in record:
                record["dataset_card"]["evidence"] = mapping[ident]
                record["dataset_card"]["task_count"] = args.dataset_tasks
            if record["kind"] != "dataset" and record.get("view"):
                record["view"]["cell"]["metric"] = "train_loss"
                if record.get("contract", {}).get("view"):
                    record["contract"]["view"]["value"]["cell"]["metric"] = "train_loss"
    old["events"] = {k:v for k,v in old["events"].items() if v["type"].startswith("authority_")}
    old["consumed"] = set(old["consumed"])
    state = State(**old)
    errors = state_errors(state)
    if errors:
        raise ResearchError("\n".join(errors))
    with store.locked(root):
        store.save_state(root, state)
    return emit({"records": len(state.records), "bytes": (root / store.LEDGER_FILE).stat().st_size})


def register(sub, common):
    p = sub.add_parser("migrate-ledger", parents=[common], help="one-time conversion after uploading legacy evidence")
    p.add_argument("--source", required=True, help="exported v0 State JSON")
    p.add_argument("--hf-map", required=True, help="record ID to verified pinned HF execution.json")
    p.add_argument("--code-repo", required=True)
    p.add_argument("--dataset-tasks", type=int, required=True, help="verified task count from pinned dataset metadata")
    p.set_defaults(func=cmd_migrate)
