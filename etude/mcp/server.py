"""P2's MCP server: the 7 tools P1 (pi-mono) may call (design §2 P1/P2, §4 MCP 工具表).

| tool | signature (design §4) | here |
|---|---|---|
| ``observe`` | ``(views=["head","wrist_l","wrist_r"], frame="latest") → images+state`` | PNG image blocks + JSON state |
| ``inspect`` | ``(frame_ref, region{x,y,w,h}, zoom≤4) → image`` | + optional ``view`` (extension) |
| ``perceive`` | ``(query, view) → {found, bbox, conf}`` | injected :class:`Perceiver`, no VLM |
| ``memory_lookup`` | ``(task, instruction) → [sequence]`` | read-only, top-k = ``budget.memory_top_k`` |
| ``submit_sequence`` | ``(nodes[]) → {ok, errors[]}`` | :func:`etude.mcp.validate.check_sequence` |
| ``get_verdict`` | ``(node_id) → verdict`` | verdict JSON only, never official scores |
| ``budget`` | ``() → {calls_left, chunks_left}`` | from ``step_lim`` |

Transport: MCP streamable-http on localhost (``http://127.0.0.1:<port>/mcp``),
served from a background thread (:class:`McpServerThread`) so that the P2
policy-server thread can block in ``get_action`` while pi talks to us (design
§2 慢回路). The tool bodies live in :class:`HarnessTools` (plain dicts) so the
runner can call them without MCP; :func:`build_server` wraps them.

Note: design §2 table says "MCP(stdio)"; the task spec for this component fixes
streamable-http because pi (P1) is spawned by P2 and must reach P2's in-process
state, which a stdio server spawned by pi could not share.
"""
from __future__ import annotations

import base64
import io
import json
import socket
import threading
import time
from typing import Any, Mapping

import numpy as np
from mcp_types import CallToolResult, ImageContent, TextContent

from etude.core.config import load_forbidden
from etude.core.types import ARMS, MCP_TOOLS, VIEW_TO_CAM, ObsFrame
from etude.mcp.state import HarnessState
from etude.mcp.validate import check_sequence

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MCP_PATH = "/mcp"
MAX_ZOOM = 4.0
MAX_INSPECT_SIDE_PX = 1280

# Descriptions shown to the planner LLM (kept free of design-doc jargon).
TOOL_DESCRIPTIONS: dict[str, str] = {
    "observe": (
        "Return camera images and robot state. views: subset of ['head','wrist_l','wrist_r'] "
        "(default all three). frame: 'latest' (default) or a keyframe ref listed in the prompt."
    ),
    "inspect": (
        "Zoom into a region of a stored frame. frame_ref: 'latest' or a keyframe ref; "
        "region: {x,y,w,h} in pixels of the 640x480 image; zoom: 1..4; view: head|wrist_l|wrist_r "
        "(default head; ignored for a .png ref)."
    ),
    "perceive": (
        "Open-vocabulary object detector on the latest frame of one view (no language model). "
        "query: short visual description, e.g. 'upright bowl, leftmost'. view: head|wrist_l|wrist_r. "
        "Returns {found, bbox:[x,y,w,h] px, conf}. Use it to confirm every target you plan to use."
    ),
    "memory_lookup": (
        "Read-only lookup of previously verified node sequences for (task, instruction). "
        "Returns up to 3 sequences; adapt them, do not copy blindly."
    ),
    "submit_sequence": (
        "Submit the node sequence (list of node objects). Returns {ok, errors}. If ok is false, "
        "fix every error and resubmit. Only the last accepted submission is executed."
    ),
    "get_verdict": "Return the verifier verdict JSON for a node id (e.g. 'n2').",
    "budget": "Return {calls_left, chunks_left}: remaining planner calls and action chunks this episode.",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def encode_png(img: np.ndarray) -> bytes:
    """Lossless PNG bytes of an HxWx3 uint8 image (design §4 inspect "无损")."""
    from PIL import Image

    arr = np.asarray(img)
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr[..., :3]).save(buf, format="PNG")
    return buf.getvalue()


def _r(x: Any, nd: int) -> Any:
    return [round(float(v), nd) for v in np.asarray(x, dtype=np.float64).reshape(-1)]


def state_summary(frame: ObsFrame) -> dict[str, Any]:
    """Compact per-arm state (joints, measured ee pose, gripper echo and
    measured width) for ``observe`` (design §2 "允许" list)."""
    arms: dict[str, Any] = {}
    for arm in ARMS:
        a: dict[str, Any] = {}
        if f"{arm}_arm_joint_state" in frame.state:
            a["joints_rad"] = _r(frame.joints(arm), 3)
        if f"{arm}_ee_pose" in frame.state:
            a["ee_pose_xyz_qwxyz"] = _r(frame.ee_pose(arm), 4)
        if f"{arm}_ee_joint_state" in frame.state:
            a["gripper_cmd_echo"] = round(frame.gripper_cmd_echo(arm), 3)
        w = frame.gripper_width_measured(arm)
        a["gripper_width_measured_m"] = None if w is None else round(w, 4)
        arms[arm] = a
    return {"t": int(frame.t), "instruction": frame.instruction, "arms": arms}


def _norm_view(view: str) -> str:
    v = (view or "head").strip()
    if v in VIEW_TO_CAM:
        return v
    inv = {c: k for k, c in VIEW_TO_CAM.items()}
    if v in inv:
        return inv[v]
    raise ValueError(f"unknown view {view!r}; use one of {sorted(VIEW_TO_CAM)}")


# ---------------------------------------------------------------------------
# tool bodies (plain python; design §4)
# ---------------------------------------------------------------------------


class HarnessTools:
    """The 7 MCP tool bodies over a :class:`HarnessState` (design §4 MCP 工具表).

    Each method returns JSON-safe dicts (``observe``/``inspect`` additionally
    return PNG bytes). Errors are returned as ``{"error": ...}`` rather than
    raised, so the planner can recover.
    """

    def __init__(self, state: HarnessState):
        self.state = state
        self._forbidden = load_forbidden(state.root)
        self._deny_paths = [str(p).replace("*", "") for p in (self._forbidden.get("deny_paths") or [])]

    # -- observe ----------------------------------------------------------
    def observe(self, views: list[str] | None = None, frame: str = "latest") -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
        """Images + state of ``frame`` (design §4 observe; default only the latest frame)."""
        views = list(views) if views else list(VIEW_TO_CAM)
        f = self.state.frame(frame)
        if f is None:
            meta = {"error": f"unknown frame {frame!r}", "keyframes": self.state.keyframe_refs()}
            self.state.log_tool("observe", False, frame=frame)
            return meta, []
        images: list[tuple[str, bytes]] = []
        missing: list[str] = []
        try:
            vnames = [_norm_view(v) for v in views]
        except ValueError as e:
            self.state.log_tool("observe", False, frame=frame)
            return {"error": str(e)}, []
        for v in vnames:
            cam = VIEW_TO_CAM[v]
            if cam in f.images:
                images.append((v, encode_png(f.images[cam])))
            else:
                missing.append(v)
        meta = {"frame_ref": frame or "latest", "views": [v for v, _ in images], "missing_views": missing}
        meta.update(state_summary(f))
        self.state.log_tool("observe", True, frame=frame, views=meta["views"])
        return meta, images

    # -- inspect ------------------------------------------------------------
    def _resolve_ref_image(self, frame_ref: str, view: str) -> np.ndarray:
        if frame_ref.endswith(".png"):
            # design §3.5 跨回合泄漏: only this episode's keyframes; one generic error for
            # missing, foreign-episode and out-of-tree refs (no existence oracle).
            unavailable = ValueError(f"frame_ref {frame_ref!r} is not available in this episode")
            kdir = self.state.keyframe_dir()
            if kdir is None or any(p and p in frame_ref for p in self._deny_paths):
                raise unavailable
            root = self.state.traces_root.resolve()
            try:
                kroot = kdir.resolve()
                p = (root / frame_ref).resolve()
                p.relative_to(kroot)
            except (ValueError, OSError):
                raise unavailable from None
            if p.parent != kroot or not p.is_file():
                raise unavailable
            from PIL import Image

            with Image.open(p) as im:
                return np.asarray(im.convert("RGB"))
        f = self.state.frame(frame_ref)
        if f is None:
            raise ValueError(f"unknown frame_ref {frame_ref!r}; known: {['latest', *self.state.keyframe_refs()]}")
        cam = VIEW_TO_CAM[_norm_view(view)]
        if cam not in f.images:
            raise ValueError(f"view {view!r} not in frame {frame_ref!r}")
        return np.asarray(f.images[cam])

    def inspect(
        self, frame_ref: str, region: Mapping[str, float], zoom: float = 2.0, view: str = "head"
    ) -> tuple[dict[str, Any], bytes | None]:
        """Zoomed crop of a stored frame (design §4 inspect; VISTA 2610.02200 §3.1)."""
        try:
            img = self._resolve_ref_image(frame_ref, view)
            h, w = img.shape[:2]
            x, y = float(region.get("x", 0)), float(region.get("y", 0))
            rw, rh = float(region.get("w", w)), float(region.get("h", h))
            if rw <= 0 or rh <= 0:
                raise ValueError("region w and h must be > 0")
            z = float(zoom)
            if not (1.0 <= z <= MAX_ZOOM):
                raise ValueError(f"zoom must be in [1, {MAX_ZOOM:g}]")
            x0, y0 = int(max(0, min(w - 1, np.floor(x)))), int(max(0, min(h - 1, np.floor(y))))
            x1, y1 = int(max(x0 + 1, min(w, np.ceil(x + rw)))), int(max(y0 + 1, min(h, np.ceil(y + rh))))
            crop = img[y0:y1, x0:x1]
            ow, oh = int(round((x1 - x0) * z)), int(round((y1 - y0) * z))
            s = min(1.0, MAX_INSPECT_SIDE_PX / max(ow, oh))
            ow, oh = max(1, int(ow * s)), max(1, int(oh * s))
            from PIL import Image

            out = np.asarray(Image.fromarray(np.ascontiguousarray(crop[..., :3])).resize((ow, oh), Image.LANCZOS))
            meta = {
                "frame_ref": frame_ref,
                "view": None if frame_ref.endswith(".png") else _norm_view(view),
                "region_px": [x0, y0, x1 - x0, y1 - y0],
                "zoom": z,
                "out_size": [ow, oh],
            }
            self.state.log_tool("inspect", True, frame_ref=frame_ref, region=meta["region_px"], zoom=z)
            return meta, encode_png(out)
        except (ValueError, KeyError, OSError) as e:
            self.state.log_tool("inspect", False, frame_ref=frame_ref)
            return {"error": str(e)}, None

    # -- perceive -----------------------------------------------------------
    def perceive(self, query: str, view: str = "head") -> dict[str, Any]:
        """Local open-set detection on the latest frame (design §4 perceive, no VLM)."""
        if self.state.perceiver is None:
            self.state.log_tool("perceive", False, query=query)
            return {"error": "no perceiver attached", "found": False, "bbox": None, "conf": 0.0}
        f = self.state.latest()
        try:
            v = _norm_view(view)
        except ValueError as e:
            self.state.log_tool("perceive", False, query=query)
            return {"error": str(e), "found": False, "bbox": None, "conf": 0.0}
        if f is None or VIEW_TO_CAM[v] not in f.images:
            self.state.log_tool("perceive", False, query=query, view=v)
            return {"error": "no observation for that view yet", "found": False, "bbox": None, "conf": 0.0}
        det = self.state.perceiver.perceive(query, f.images[VIEW_TO_CAM[v]], v)
        conf_min = float(self.state.thresholds.get("perceive.conf_min", 0.6))
        found = bool(det.found and det.conf >= conf_min)
        out = {
            "found": found,
            "bbox": [round(float(b), 1) for b in det.bbox] if (found and det.bbox is not None) else None,
            "conf": round(float(det.conf), 3),
        }
        self.state.log_tool("perceive", True, query=query, view=v, found=found)
        return out

    # -- memory_lookup ------------------------------------------------------
    def memory_lookup(self, task: str, instruction: str) -> list[dict[str, Any]]:
        """Read-only top-k sequences (design §4 memory_lookup, top-3).

        Only ``seq_id``, ``instruction`` and ``nodes`` are returned; ``lineage``
        and ``evidence_refs`` (episode ids, trace paths) are withheld because
        they can encode seeds/layouts (design §3.5)."""
        if self.state.memory is None:
            self.state.log_tool("memory_lookup", True, task=task, n=0)
            return []
        k = int(self.state.thresholds.get("budget.memory_top_k", 3))
        entries = self.state.memory.lookup(task, instruction, k=k)[:k]
        out = [{"seq_id": e.seq_id, "instruction": e.instruction, "nodes": [n.to_dict() for n in e.nodes]} for e in entries]
        self.state.log_tool("memory_lookup", True, task=task, n=len(out))
        return out

    # -- submit_sequence ----------------------------------------------------
    def submit_sequence(self, nodes: Any) -> dict[str, Any]:
        """Validate and store a node sequence (design §4 submit_sequence: schema +
        leakage, failure blocks)."""
        attempt = self.state.note_submit_attempt()
        seq, errors = check_sequence(nodes, root=self.state.root)
        if seq is None:
            self.state.log_tool("submit_sequence", False, attempt=attempt, n_errors=len(errors))
            return {"ok": False, "errors": errors}
        self.state.accept_sequence(seq)
        self.state.log_tool("submit_sequence", True, attempt=attempt, node_ids=[n.id for n in seq])
        return {"ok": True, "errors": []}

    # -- get_verdict --------------------------------------------------------
    def get_verdict(self, node_id: str) -> dict[str, Any]:
        """Verdict JSON for ``node_id`` (design §4 get_verdict; no official score)."""
        v = self.state.verdict(node_id)
        if v is None:
            self.state.log_tool("get_verdict", False, node_id=node_id)
            return {"error": f"no verdict for node {node_id!r}", "known": [x.node for x in self.state.verdicts()]}
        self.state.log_tool("get_verdict", True, node_id=node_id, kind=v.kind)
        return v.to_dict()

    # -- budget -------------------------------------------------------------
    def budget(self) -> dict[str, int]:
        """``{calls_left, chunks_left}`` (design §4 budget)."""
        b = self.state.budget()
        self.state.log_tool("budget", True, **b)
        return b


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


def _json_text(obj: Any) -> TextContent:
    return TextContent(type="text", text=json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def _png_block(data: bytes) -> ImageContent:
    return ImageContent(type="image", data=base64.b64encode(data).decode("ascii"), mime_type="image/png")


def _result(obj: Any, images: list[bytes] = (), is_error: bool = False) -> CallToolResult:
    structured = obj if isinstance(obj, dict) else {"result": obj}
    content = [_json_text(obj)] + [_png_block(b) for b in images]
    return CallToolResult(content=content, structured_content=structured, is_error=is_error)


def build_server(state: HarnessState, *, name: str = "etude") -> Any:
    """Create an ``MCPServer`` (mcp 2.x; formerly FastMCP) exposing exactly
    :data:`etude.core.types.MCP_TOOLS` over ``state`` (design §4 MCP 工具表)."""
    from mcp.server.mcpserver import MCPServer

    tools = HarnessTools(state)
    server = MCPServer(
        name=name,
        instructions="Etude harness tools for planning robot node sequences.",
        log_level="WARNING",
    )

    def observe(views: list[str] | None = None, frame: str = "latest") -> CallToolResult:
        meta, imgs = tools.observe(views, frame)
        return _result(meta, [b for _, b in imgs], is_error="error" in meta)

    def inspect(frame_ref: str, region: dict[str, float], zoom: float = 2.0, view: str = "head") -> CallToolResult:
        meta, img = tools.inspect(frame_ref, region, zoom, view)
        return _result(meta, [img] if img is not None else [], is_error=img is None)

    def perceive(query: str, view: str = "head") -> CallToolResult:
        out = tools.perceive(query, view)
        return _result(out, is_error="error" in out)

    def memory_lookup(task: str, instruction: str) -> CallToolResult:
        return _result({"sequences": tools.memory_lookup(task, instruction)})

    def submit_sequence(nodes: list[dict[str, Any]] | str) -> CallToolResult:
        return _result(tools.submit_sequence(nodes))

    def get_verdict(node_id: str) -> CallToolResult:
        out = tools.get_verdict(node_id)
        return _result(out, is_error="error" in out)

    def budget() -> CallToolResult:
        return _result(tools.budget())

    fns = {
        "observe": observe,
        "inspect": inspect,
        "perceive": perceive,
        "memory_lookup": memory_lookup,
        "submit_sequence": submit_sequence,
        "get_verdict": get_verdict,
        "budget": budget,
    }
    assert tuple(fns) == MCP_TOOLS, "tool set must equal etude.core.types.MCP_TOOLS"
    for tool_name, fn in fns.items():
        server.add_tool(fn, name=tool_name, description=TOOL_DESCRIPTIONS[tool_name])
    server.etude_tools = tools  # type: ignore[attr-defined]
    return server


def free_port(host: str = DEFAULT_HOST) -> int:
    """Pick an unused TCP port on ``host``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


class McpServerThread:
    """Run :func:`build_server` over streamable-http in a daemon thread
    (design §2: P2 blocks in ``get_action`` while P1 calls the tools).

    ``port=0`` picks a free port. ``url`` is ``http://<host>:<port>/mcp``.
    """

    def __init__(self, state: HarnessState, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, log_level: str = "warning"):
        self.state = state
        self.host = host
        self.port = int(port) if port else free_port(host)
        self.log_level = log_level
        self._server: Any = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{MCP_PATH}"

    def start(self, timeout: float = 10.0) -> "McpServerThread":
        """Start serving; returns once uvicorn reports ``started``."""
        import uvicorn

        app = build_server(self.state).streamable_http_app(streamable_http_path=MCP_PATH, host=self.host)
        cfg = uvicorn.Config(app, host=self.host, port=self.port, log_level=self.log_level, lifespan="on")
        self._server = uvicorn.Server(cfg)
        self._thread = threading.Thread(target=self._server.run, name=f"etude-mcp-{self.port}", daemon=True)
        self._thread.start()
        t0 = time.monotonic()
        while not getattr(self._server, "started", False):
            if not self._thread.is_alive():
                raise RuntimeError(f"MCP server thread exited during startup (port {self.port} in use?)")
            if time.monotonic() - t0 > timeout:
                raise TimeoutError(f"MCP server did not start within {timeout}s")
            time.sleep(0.02)
        return self

    def stop(self, timeout: float = 5.0) -> None:
        """Ask uvicorn to exit and join the thread."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
        self._server = None
        self._thread = None

    def __enter__(self) -> "McpServerThread":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()


def serve(state: HarnessState, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Blocking streamable-http serve in the current thread (manual pi testing)."""
    build_server(state).run("streamable-http", host=host, port=port, streamable_http_path=MCP_PATH)


__all__ = [
    "DEFAULT_HOST", "DEFAULT_PORT", "MCP_PATH", "TOOL_DESCRIPTIONS", "HarnessTools", "McpServerThread",
    "build_server", "encode_png", "free_port", "serve", "state_summary",
]
