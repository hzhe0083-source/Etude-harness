"""pi0.5 over the openpi websocket protocol (design §2 P3, §8.1 item 8).

P2 (this process) talks to a separately started ``serve_policy.py`` (P3,
possibly on another GPU/machine) over the ``openpi_client.WebsocketClientPolicy``
protocol: websocket + msgpack-numpy, the server sends one metadata frame on
connect, then each ``infer(obs_dict)`` returns ``{"actions": (H, 14), ...}``.
The client here (:class:`_BoundedWsClient`) speaks the same protocol with
bounded connect/recv waits and reconnects once after a dropped connection.

``openpi_client`` is not a dependency of this repo. It is imported from the
installed environment if present, else from the vendored copy in XPolicyLab
(``$OPENPI_CLIENT_SRC`` or
``~/Documents/RoboDojo/XPolicyLab/policy/Pi_05/openpi/packages/openpi-client/src``).

Server side (user runs it; checkpoint download may still be in progress)::

    cd XPolicyLab/policy/Pi_05/openpi && uv run scripts/serve_policy.py \\
        --port 8000 policy:checkpoint \\
        --policy.config pi05_base_aloha_full_sim_arx-x5_seed_0 \\
        --policy.dir ~/models/robodojo-ckpt/ckpt/RoboDojo/Pi_05/RoboDojo-sim-arx_x5-joint-0/59999
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from etude.backends.base import PolicyBackend, decode_actions, encode_pi05_obs
from etude.core.types import ObsFrame

# websockets>=16 warns when a sync connection is not used as a context manager; the
# long-lived policy socket (like upstream WebsocketClientPolicy) is closed explicitly instead.
warnings.filterwarnings("ignore", message=r"connect\(\) must be used as a context manager", category=DeprecationWarning)

#: Default location of the vendored openpi-client sources (read-only reference).
DEFAULT_OPENPI_CLIENT_SRC = Path.home() / "Documents/RoboDojo/XPolicyLab/policy/Pi_05/openpi/packages/openpi-client/src"


def import_openpi_client() -> Any:
    """Import ``openpi_client`` (installed, or vendored path added to
    ``sys.path``); raise ImportError with instructions otherwise (design §8.1 item 8)."""
    try:
        return importlib.import_module("openpi_client")
    except ImportError:
        pass
    src = Path(os.environ.get("OPENPI_CLIENT_SRC", str(DEFAULT_OPENPI_CLIENT_SRC))).expanduser()
    if (src / "openpi_client").is_dir() and str(src) not in sys.path:
        sys.path.append(str(src))
        try:
            return importlib.import_module("openpi_client")
        except ImportError as e:  # e.g. websockets/msgpack missing
            raise ImportError(
                f"openpi_client found at {src} but failed to import ({e}); it needs websockets>=11, "
                "msgpack, numpy, typing_extensions"
            ) from e
    raise ImportError(
        "openpi_client is not importable. Either `uv pip install -e "
        "<XPolicyLab>/policy/Pi_05/openpi/packages/openpi-client` into this venv, or set "
        f"OPENPI_CLIENT_SRC to its src/ directory (tried {src})."
    )


class _BoundedWsClient:
    """openpi websocket client with bounded waits (design §2 慢回路: every
    get_action must finish well inside the official client's 120 s limit).

    Same wire protocol as ``openpi_client.WebsocketClientPolicy`` (websocket +
    msgpack-numpy; one metadata frame after connect; ``infer(dict) -> dict``),
    but the connect retries only until ``connect_timeout_s`` (upstream loops
    forever on ``ConnectionRefusedError``, which would hold XPolicyLab's model
    lock and wedge the run) and ``recv`` has ``infer_timeout_s``."""

    def __init__(self, uri: str, api_key: str | None, connect_timeout_s: float, infer_timeout_s: float):
        import_openpi_client()
        from openpi_client import msgpack_numpy

        self._mpn = msgpack_numpy
        self._packer = msgpack_numpy.Packer()
        self.uri = uri
        self.api_key = api_key
        self.connect_timeout_s = float(connect_timeout_s)
        self.infer_timeout_s = float(infer_timeout_s)
        self._ws: Any = None
        self.metadata: dict[str, Any] = {}
        self.connect()

    def connect(self) -> None:
        """Open the socket and read the metadata frame, retrying until the deadline."""
        from websockets.sync.client import connect

        deadline = time.monotonic() + self.connect_timeout_s
        headers = {"Authorization": f"Api-Key {self.api_key}"} if self.api_key else None
        last: BaseException | None = None
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RuntimeError(f"openpi policy server {self.uri} not reachable within "
                                   f"{self.connect_timeout_s:g} s (last error: {last!r}); is serve_policy.py up?")
            try:
                ws = connect(self.uri, open_timeout=max(0.5, min(left, 10.0)), compression=None, max_size=None,
                             additional_headers=headers)
                try:
                    self.metadata = dict(self._mpn.unpackb(ws.recv(timeout=max(0.5, left))))
                except BaseException:
                    ws.close()
                    raise
                self._ws = ws
                return
            except (OSError, TimeoutError, EOFError) as e:  # refused / reset / handshake timeout
                last = e
            except Exception as e:  # websockets.InvalidHandshake, ConnectionClosed during handshake
                last = e
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    def infer(self, obs: dict[str, Any]) -> Any:
        self._ws.send(self._packer.pack(obs))
        response = self._ws.recv(timeout=self.infer_timeout_s)
        if isinstance(response, str):
            raise RuntimeError(f"Error in inference server:\n{response}")
        return self._mpn.unpackb(response)

    def close(self) -> None:
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:  # noqa: BLE001
            pass
        self._ws = None

    def get_server_metadata(self) -> dict[str, Any]:
        return self.metadata

    def reset(self) -> None:
        pass


class Pi05RemoteBackend(PolicyBackend):
    """``PolicyBackend`` that calls a remote openpi policy server (design §2 P3).

    The connection is opened lazily on the first :meth:`infer` with a bounded
    retry (``connect_timeout_s``, default 30 s); each request has
    ``infer_timeout_s`` (default 60 s). Both stay well under the official
    client's 120 s per-call cap, so a missing or restarted P3 makes the
    episode fail fast instead of hanging the policy server. A dropped
    connection (P3 restart) is reconnected once per call. ``client`` may be
    injected (anything with ``infer(dict)->dict`` and optionally ``reset()``);
    injected clients are never reconnected.
    """

    def __init__(self, host: str = "localhost", port: int | None = 8000, api_key: str | None = None,
                 client: Any = None, expect_horizon: int | None = None,
                 connect_timeout_s: float = 30.0, infer_timeout_s: float = 60.0):
        """``host``/``port``/``api_key`` as in ``WebsocketClientPolicy``;
        ``expect_horizon`` (e.g. 50) makes :meth:`infer` reject other horizons."""
        self.host = host
        self.port = port
        self.api_key = api_key
        self.expect_horizon = expect_horizon
        self.connect_timeout_s = float(connect_timeout_s)
        self.infer_timeout_s = float(infer_timeout_s)
        self._client = client
        self._owned = client is None
        self.server_metadata: dict[str, Any] | None = None
        self.calls = 0
        self.reconnects = 0
        self.last_latency_s: float | None = None

    @property
    def uri(self) -> str:
        u = self.host if self.host.startswith("ws") else f"ws://{self.host}"
        return u + (f":{self.port}" if self.port is not None else "")

    def connect(self) -> Any:
        """Open the websocket (bounded retry, see class doc) and read metadata."""
        if self._client is None:
            self._client = _BoundedWsClient(self.uri, self.api_key, self.connect_timeout_s, self.infer_timeout_s)
            self._owned = True
            self.server_metadata = dict(self._client.get_server_metadata())
        return self._client

    def _drop(self) -> None:
        if self._owned and self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
            self._client = None

    def infer(self, obs: ObsFrame, prompt: str) -> np.ndarray:
        """Encode like XPolicyLab Pi_05, call the server, return ``(H, 14)``
        absolute joint targets (design §3.3 ``pi05.infer``)."""
        payload = encode_pi05_obs(obs, prompt)
        t0 = time.monotonic()
        for attempt in (0, 1):
            client = self.connect()
            try:
                result = client.infer(payload)
                break
            except TimeoutError as e:
                self._drop()
                raise RuntimeError(f"openpi policy server {self.uri}: no answer within {self.infer_timeout_s:g} s") from e
            except Exception as e:  # websockets ConnectionClosed, OSError: P3 restarted / network drop
                closed = type(e).__name__.startswith("ConnectionClosed") or isinstance(e, OSError)
                if not (closed and self._owned) or attempt == 1:
                    self._drop()
                    raise RuntimeError(f"openpi policy server {self.uri}: {e!r}") from e
                self._drop()
                self.reconnects += 1
        self.last_latency_s = time.monotonic() - t0
        self.calls += 1
        if isinstance(result, str):
            raise RuntimeError(f"policy server error:\n{result}")
        chunk = decode_actions(result)
        if self.expect_horizon is not None and chunk.shape[0] != self.expect_horizon:
            raise ValueError(f"expected horizon {self.expect_horizon}, got {chunk.shape[0]}")
        return chunk

    def reset(self) -> None:
        """Forward ``reset`` to the client (the upstream one is a no-op)."""
        if self._client is not None and hasattr(self._client, "reset"):
            self._client.reset()


# ---------------------------------------------------------------------------
# demo: a local fake openpi server speaking the real protocol
# ---------------------------------------------------------------------------


def _serve_fake(port_box: list[int], ready: threading.Event, stop: threading.Event, horizon: int = 50) -> None:
    """Minimal openpi-protocol server: metadata frame, then (H,14) actions =
    state + small ramp (no model)."""
    from websockets.sync.server import serve

    import_openpi_client()
    from openpi_client import msgpack_numpy as mpn
    packer = mpn.Packer()

    def handler(ws: Any) -> None:
        ws.send(packer.pack({"policy": "fake-pi05", "action_horizon": horizon}))
        for msg in ws:
            obs = mpn.unpackb(msg)
            state = np.asarray(obs["state"], dtype=np.float32)
            assert obs["images"]["cam_high"].shape == (3, 480, 640), obs["images"]["cam_high"].shape
            ramp = np.linspace(0.0, 0.1, horizon, dtype=np.float32)[:, None]
            ws.send(packer.pack({"actions": state[None, :] + ramp, "prompt_echo": obs["prompt"]}))

    with serve(handler, "127.0.0.1", 0, compression=None, max_size=None) as server:
        port_box.append(server.socket.getsockname()[1])
        ready.set()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        stop.wait()
        server.shutdown()


def _demo() -> int:
    """Run the real ``WebsocketClientPolicy`` against a local fake server (no model)."""
    from etude.core.actions import chunk_to_joint_actions
    from etude.fakes import fake_frame

    port_box: list[int] = []
    ready, stop = threading.Event(), threading.Event()
    th = threading.Thread(target=_serve_fake, args=(port_box, ready, stop), daemon=True)
    th.start()
    if not ready.wait(10):
        print("fake server did not start")
        return 1
    be = Pi05RemoteBackend(host="127.0.0.1", port=port_box[0], expect_horizon=50)
    frame = fake_frame(0)
    chunk = be.infer(frame, "pick up the red block")
    acts = chunk_to_joint_actions(chunk, 25)
    print("server metadata:", be.server_metadata)
    print("chunk", chunk.shape, "first row", np.round(chunk[0], 3).tolist())
    print("actions:", len(acts), sorted(acts[0]), f"latency {be.last_latency_s * 1000:.1f} ms")
    # P3 restart: the cached socket is closed under us -> one reconnect inside the call
    be._client._ws.close()
    chunk2 = be.infer(frame, "pick up the red block")
    print("after a dropped connection:", chunk2.shape, "reconnects =", be.reconnects)
    stop.set()
    th.join(timeout=5)
    # P3 not listening: bounded wait, then a clear error (no infinite retry inside get_action)
    import socket

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        dead_port = sk.getsockname()[1]
    dead = Pi05RemoteBackend(host="127.0.0.1", port=dead_port, connect_timeout_s=2.0)
    t0 = time.monotonic()
    try:
        dead.infer(frame, "x")
        print("ERROR: infer against a closed port returned")
        return 1
    except RuntimeError as e:
        print(f"closed port -> RuntimeError after {time.monotonic() - t0:.1f} s: {str(e)[:80]}")
    return 0 if be.reconnects == 1 else 1


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m etude.backends.pi05_remote --demo``."""
    ap = argparse.ArgumentParser(prog="python -m etude.backends.pi05_remote")
    ap.add_argument("--demo", action="store_true")
    if ap.parse_args(argv).demo:
        return _demo()
    ap.print_help()
    return 0


__all__ = ["Pi05RemoteBackend", "import_openpi_client", "DEFAULT_OPENPI_CLIENT_SRC"]

if __name__ == "__main__":
    sys.exit(main())
