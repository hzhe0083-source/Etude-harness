"""Local open-set detectors behind ``perceive(query, view)`` (design §4 MCP
``perceive``, §8.3 E row 1 "后置 = RGB 差分 + 开集检测器谓词", §8.4 #6).

Two implementations of :class:`etude.core.interfaces.Perceiver`:

* :class:`OwlV2Perceiver` -- OWLv2 open-vocabulary detector via
  ``transformers`` (optional import; loaded lazily on first call). This is the
  production detector (design §8.3: ~1-2 GB VRAM, 0 VLM calls).
* :class:`ColorBlobPerceiver` -- dependency-free colour-word blob finder used
  by demos on synthetic images (``etude.fakes.make_image``) and as a smoke
  stand-in. It tolerates small RGB noise (unlike ``etude.fakes.FakePerceiver``,
  which needs an exact RGB match) and honours ``leftmost``/``rightmost``/
  ``largest``/``smallest``/``top``/``bottom`` modifiers in the query.

Confidence convention (both detectors, matching the ABC docstring): the raw
best score is always returned in ``Detection.conf``; ``found`` is True only if
that score is >= ``perceive.conf_min`` (frozen/params/thresholds.yaml). So
``found=False, conf>0`` means "seen but not confident" (the verifier maps it
to InsufficientEvidence) and ``found=False, conf==0`` means "absent".
"""
from __future__ import annotations

import re
from typing import Any, Callable

import numpy as np

from etude.core.config import load_thresholds
from etude.core.interfaces import Perceiver
from etude.core.types import IMAGE_HW, VIEW_TO_CAM, Detection

__all__ = [
    "COLOR_REFS",
    "ColorBlobPerceiver",
    "OwlV2Perceiver",
    "perceive_conf_min",
    "normalize_view",
    "query_color",
]


def perceive_conf_min(root: Any = None) -> float:
    """Frozen ``perceive.conf_min`` (design §3.5 "只收紧")."""
    return float(load_thresholds(root)["perceive.conf_min"])


def normalize_view(view: str) -> str:
    """Accept an MCP view (``head``/``wrist_l``/``wrist_r``) or a RoboDojo cam
    name and return the MCP view name (design §4 ``perceive(query, view)``)."""
    if view in VIEW_TO_CAM:
        return view
    for v, cam in VIEW_TO_CAM.items():
        if cam == view:
            return v
    raise ValueError(f"unknown view {view!r}; expected one of {sorted(VIEW_TO_CAM)}")


# ---------------------------------------------------------------------------
# Colour-blob detector (demo / smoke)
# ---------------------------------------------------------------------------

#: Reference RGB per colour word. The six first entries equal etude.fakes.COLORS.
COLOR_REFS: dict[str, tuple[int, int, int]] = {
    "red": (220, 30, 30),
    "green": (30, 200, 60),
    "blue": (30, 60, 220),
    "yellow": (230, 210, 40),
    "white": (245, 245, 245),
    "black": (15, 15, 15),
    "orange": (240, 130, 30),
    "purple": (130, 50, 180),
    "pink": (240, 130, 180),
    "brown": (120, 70, 30),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
}
_MODIFIERS = ("leftmost", "rightmost", "largest", "smallest", "top", "topmost", "bottom", "bottommost")
_WORD_RE = re.compile(r"[a-z]+")


def query_color(query: str) -> str | None:
    """First colour word in ``query`` (lower-cased), or None (design §4 perceive)."""
    for w in _WORD_RE.findall(query.lower()):
        if w in COLOR_REFS:
            return w
    return None


def _query_modifier(query: str) -> str | None:
    for w in _WORD_RE.findall(query.lower()):
        if w in _MODIFIERS:
            return w
    return None


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Connected components of a boolean mask as ``(x, y, w, h, area)``.
    Uses OpenCV when available, else scipy.ndimage, else one global extent."""
    try:
        import cv2  # type: ignore

        n, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
        return [tuple(int(v) for v in stats[i, :5]) for i in range(1, n)]  # type: ignore[misc]
    except ImportError:
        pass
    try:
        from scipy import ndimage  # type: ignore

        lab, n = ndimage.label(mask)
        out = []
        for i, sl in enumerate(ndimage.find_objects(lab), start=1):
            if sl is None:
                continue
            ys, xs = sl
            area = int((lab[sl] == i).sum())
            out.append((xs.start, ys.start, xs.stop - xs.start, ys.stop - ys.start, area))
        return out
    except ImportError:
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            return []
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        return [(x0, y0, x1 - x0, y1 - y0, int(mask.sum()))]


class ColorBlobPerceiver(Perceiver):
    """Colour-word blob detector (design §4 ``perceive``; demo stand-in for OWLv2).

    The first colour word in ``query`` (see :data:`COLOR_REFS`) selects pixels
    whose max per-channel distance to the reference RGB is <= ``tol``. Among
    connected components with area >= ``min_area_px`` the one chosen is the
    largest, or as directed by a modifier word (``leftmost`` etc.).

    ``conf`` = fill ratio of the chosen component inside its bbox (1.0 for a
    solid rectangle, lower for scattered matches), scaled by
    ``base_conf``. Queries without a colour word return ``found=False, conf=0``.
    """

    def __init__(
        self,
        tol: int = 40,
        min_area_px: int = 30,
        base_conf: float = 0.95,
        conf_min: float | None = None,
        root: Any = None,
    ):
        self.tol = int(tol)
        self.min_area_px = int(min_area_px)
        self.base_conf = float(base_conf)
        self.conf_min = perceive_conf_min(root) if conf_min is None else float(conf_min)
        self.calls: list[tuple[str, str]] = []

    def perceive(self, query: str, image: np.ndarray, view: str) -> Detection:
        """Detect the colour named in ``query`` in ``image`` from ``view`` (design §4)."""
        view = normalize_view(view)
        self.calls.append((query, view))
        color = query_color(query)
        if color is None:
            return Detection(found=False, bbox=None, conf=0.0, query=query, view=view)
        img = np.asarray(image)[..., :3].astype(np.int16)
        ref = np.array(COLOR_REFS[color], dtype=np.int16)
        mask = np.abs(img - ref).max(axis=-1) <= self.tol
        comps = [c for c in _components(mask) if c[4] >= self.min_area_px]
        if not comps:
            return Detection(found=False, bbox=None, conf=0.0, query=query, view=view)
        mod = _query_modifier(query)
        key: Callable[[tuple[int, int, int, int, int]], float]
        if mod == "leftmost":
            key = lambda c: -(c[0] + c[2] / 2)
        elif mod == "rightmost":
            key = lambda c: c[0] + c[2] / 2
        elif mod in ("top", "topmost"):
            key = lambda c: -(c[1] + c[3] / 2)
        elif mod in ("bottom", "bottommost"):
            key = lambda c: c[1] + c[3] / 2
        elif mod == "smallest":
            key = lambda c: -c[4]
        else:
            key = lambda c: c[4]
        x, y, w, h, area = max(comps, key=key)
        conf = float(self.base_conf * min(1.0, area / float(max(w * h, 1))))
        bbox = [float(x), float(y), float(w), float(h)]
        if conf < self.conf_min:
            return Detection(found=False, bbox=None, conf=conf, query=query, view=view)
        return Detection(found=True, bbox=bbox, conf=conf, query=query, view=view)


# ---------------------------------------------------------------------------
# OWLv2 (production)
# ---------------------------------------------------------------------------


class OwlV2Perceiver(Perceiver):
    """OWLv2 open-vocabulary detector (design §4 perceive, §8.3 E row 1, §8.4 #6).

    ``transformers`` and ``torch`` are optional imports (not in
    pyproject.toml); construction succeeds without them and :meth:`load` (or
    the first :meth:`perceive`) raises ``ImportError`` with an install hint.
    ``harness_build.make_perceiver`` calls :meth:`load` at build time so that
    weights are never loaded inside a ``get_action`` call (the official client
    caps every call at 120 s). ``local_files_only=True`` forbids a Hub download
    at that point; fetch the weights beforehand with ``hf download``.

    OWLv2 pads the input to a square, so boxes are post-processed against a
    ``(S, S)`` target size with ``S = max(H, W)`` and clipped to the image.
    Raw OWLv2 scores are typically far below 0.6; ``calibrate`` maps a raw
    score to the probability scale used by ``perceive.conf_min``. The default
    is the identity; a calibration must be fitted offline in M0 (design §8.4
    #6) and may not be used to loosen the frozen threshold.
    """

    DEFAULT_MODEL = "google/owlv2-base-patch16-ensemble"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str | None = None,
        conf_min: float | None = None,
        calibrate: Callable[[float], float] | None = None,
        root: Any = None,
        local_files_only: bool = True,
    ):
        self.model_name = model_name
        self.device = device
        self.local_files_only = bool(local_files_only)
        self.conf_min = perceive_conf_min(root) if conf_min is None else float(conf_min)
        self.calibrate = calibrate or (lambda s: float(s))
        self._processor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def available() -> bool:
        """True if ``transformers`` and ``torch`` can be imported."""
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError:
            return False
        return True

    def load(self) -> None:
        """Load processor and weights now (idempotent). Called by
        ``make_perceiver`` so loading cannot land inside the 120 s client window."""
        self._load()

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch  # type: ignore
            from transformers import Owlv2ForObjectDetection, Owlv2Processor  # type: ignore
        except ImportError as e:  # pragma: no cover - depends on env
            raise ImportError(
                "OwlV2Perceiver needs `transformers` and `torch` (not in pyproject.toml); "
                "install them in the harness-server env, or use ColorBlobPerceiver for demos"
            ) from e
        self._torch = torch
        dev = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.device = dev
        kw = {"local_files_only": self.local_files_only}
        self._processor = Owlv2Processor.from_pretrained(self.model_name, **kw)
        self._model = Owlv2ForObjectDetection.from_pretrained(self.model_name, **kw).to(dev).eval()

    def perceive(self, query: str, image: np.ndarray, view: str) -> Detection:
        """Best OWLv2 box for text ``query`` in ``image`` from ``view`` (design §4)."""
        view = normalize_view(view)
        self.calls.append((query, view))
        self._load()
        torch = self._torch
        img = np.asarray(image)[..., :3].astype(np.uint8)
        h, w = img.shape[:2]
        inputs = self._processor(text=[[query]], images=img, return_tensors="pt").to(self.device)
        with torch.no_grad():
            out = self._model(**inputs)
        s = max(h, w)
        res = self._processor.post_process_object_detection(
            outputs=out, threshold=0.0, target_sizes=torch.tensor([[s, s]], device=self.device)
        )[0]
        scores = res["scores"].detach().float().cpu().numpy()
        if scores.size == 0:
            return Detection(found=False, bbox=None, conf=0.0, query=query, view=view)
        i = int(scores.argmax())
        x0, y0, x1, y1 = (float(v) for v in res["boxes"][i].detach().float().cpu().numpy())
        x0, x1 = max(0.0, min(x0, w)), max(0.0, min(x1, w))
        y0, y1 = max(0.0, min(y0, h)), max(0.0, min(y1, h))
        conf = float(self.calibrate(float(scores[i])))
        if conf < self.conf_min or x1 <= x0 or y1 <= y0:
            return Detection(found=False, bbox=None, conf=conf, query=query, view=view)
        return Detection(found=True, bbox=[x0, y0, x1 - x0, y1 - y0], conf=conf, query=query, view=view)


def _demo() -> int:
    """Smoke run of ColorBlobPerceiver on the default fake scene (design §4)."""
    from etude.fakes import fake_frame

    fr = fake_frame()
    p = ColorBlobPerceiver()
    for q, v in [("red block", "head"), ("blue bowl, rightmost", "head"), ("red block", "wrist_l"),
                 ("green cup", "head"), ("the bowl", "head")]:
        print(f"perceive({q!r}, {v}) ->", p.perceive(q, fr.image(v), v).to_dict())
    print("OwlV2Perceiver.available():", OwlV2Perceiver.available(), "| image size", IMAGE_HW)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_demo())
