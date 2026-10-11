"""Only small, interpretable results enter Git; full metrics remain on HF."""
import math
from .errors import ResearchError

KEYS = {
    "dataset": {"accepted_samples", "published_samples", "task_count"},
    "training": {"train_loss", "validation_loss"},
    "evaluation": {"successes", "trials", "success_rate"},
}


def core_metrics(kind, data):
    if not isinstance(data, dict) or set(data) - KEYS[kind]:
        raise ResearchError(f"{kind} core metrics must use only {sorted(KEYS[kind])}")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in data.values()):
        raise ResearchError("core metrics must be finite numbers")
    if kind == "dataset" and any(not isinstance(v, int) or v < 0 for v in data.values()):
        raise ResearchError("dataset counts must be nonnegative integers")
    if kind == "dataset" and data and "accepted_samples" not in data:
        raise ResearchError("dataset core metrics require accepted_samples")
    if kind == "evaluation" and data:
        successes, trials = data.get("successes"), data.get("trials")
        if type(successes) is not int or type(trials) is not int or trials <= 0 or not 0 <= successes <= trials:
            raise ResearchError("evaluation needs integer successes and trials, 0 <= successes <= trials, trials > 0")
        rate = successes / trials
        if "success_rate" in data and not math.isclose(data["success_rate"], rate, rel_tol=1e-9, abs_tol=1e-12):
            raise ResearchError("success_rate must equal successes / trials")
        data = dict(data, success_rate=rate)
    return data
