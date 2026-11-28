"""Bounded, killable CPU work; children receive data, never database sessions or clients."""

from __future__ import annotations

import asyncio
import atexit
import multiprocessing
import threading
import time
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any
from weakref import WeakKeyDictionary, WeakSet


def _calculate(operation, payload):
    if operation == "prepare_tree":
        from sag_api.services.tree_publish_service import prepare_tree_candidate

        return prepare_tree_candidate(payload["snapshot"], payload["assignments"])
    if operation == "dedup_candidates":
        from sag_api.services.dedup_and_temporal_service import _dedup_candidates

        return _dedup_candidates(
            [SimpleNamespace(**row) for row in payload["current"]],
            [SimpleNamespace(**row) for row in payload["prior"]],
            payload["signatures"],
            payload["embeddings"],
            payload["version_id"],
        )
    if operation == "minhash":
        from sag_api.services.dedup_and_temporal_service import (
            compute_minhash_signature,
            tokenize_for_minhash,
        )

        return {
            key: compute_minhash_signature(tokenize_for_minhash(text))
            for key, text in payload["texts"].items()
        }
    if operation == "graph":
        from sag_api.services.knowledge_service import KnowledgeConfig, graph_payload

        return graph_payload(
            [SimpleNamespace(**row) for row in payload["units"]], KnowledgeConfig.model_validate(payload["config"])
        )
    if operation == "routing":
        from sag_api.services.routing_tree_service import build_routing_snapshot

        options = dict(payload)
        metadata = options.pop("knowledge_metadata", None)
        precomputed = options.pop("precomputed", None)
        snapshot = build_routing_snapshot(
            **options,
            extra_manifest=metadata,
            precomputed=precomputed,
        )
        return replace(snapshot, metrics=dict(snapshot.metrics), quality_gates=dict(snapshot.quality_gates))
    raise ValueError("Unknown CPU operation")


def _entry(connection, operation, payload):
    try:
        connection.send((True, _calculate(operation, payload)))
    except Exception as error:
        # Error bodies can contain source data. Only the class crosses this boundary.
        connection.send((False, type(error).__name__))
    finally:
        connection.close()


def _worker_loop(connection):
    try:
        import ctypes
        import sys

        if sys.platform == "win32":
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00008000)
    except Exception:
        pass
    preloaded = False
    try:
        while True:
            try:
                item = connection.recv()
            except (EOFError, OSError):
                break
            if item is None:
                break
            operation, payload = item
            if operation == "__warmup__":
                try:
                    from sag_api.services.dedup_and_temporal_service import compute_minhash_signature  # noqa: F401
                    from sag_api.services.knowledge_service import KnowledgeConfig, graph_payload  # noqa: F401
                    from sag_api.services.routing_tree_service import build_routing_snapshot  # noqa: F401
                    from sag_api.services.tree_publish_service import prepare_tree_candidate  # noqa: F401

                    preloaded = True
                except Exception:
                    pass
                continue
            try:
                connection.send((True, _calculate(operation, payload)))
            except Exception as error:
                # Error bodies can contain source data. Only the class crosses this boundary.
                connection.send((False, type(error).__name__))
            if not preloaded and not connection.poll(0.35):
                try:
                    from sag_api.services.knowledge_service import KnowledgeConfig, graph_payload  # noqa: F401
                    from sag_api.services.routing_tree_service import build_routing_snapshot  # noqa: F401
                    from sag_api.services.tree_publish_service import prepare_tree_candidate  # noqa: F401

                    preloaded = True
                except Exception:
                    pass
    finally:
        connection.close()


@dataclass
class _WorkerHandle:
    process: Any
    parent: Any

    def is_alive(self) -> bool:
        return self.process.pid is not None and self.process.is_alive()

    def close_and_reap(self) -> None:
        try:
            self.parent.close()
        except Exception:
            pass
        if self.process.pid is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join()


_SLOTS = WeakKeyDictionary()
_WORKERS: WeakKeyDictionary[Any, _WorkerHandle] = WeakKeyDictionary()
_WRAPPED_LOOPS: WeakSet[Any] = WeakSet()
_WORKER_LOCK = threading.Lock()


def _spawn_worker() -> _WorkerHandle:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=_worker_loop, args=(child,), daemon=True)
    process.start()
    child.close()
    return _WorkerHandle(process=process, parent=parent)


def _get_or_spawn_worker(loop: Any) -> _WorkerHandle:
    with _WORKER_LOCK:
        handle = _WORKERS.get(loop)
        if handle is not None and handle.is_alive():
            return handle
        if handle is not None:
            _WORKERS.pop(loop, None)
    if handle is not None:
        handle.close_and_reap()
    fresh = _spawn_worker()
    with _WORKER_LOCK:
        _WORKERS[loop] = fresh
    return fresh


def _reap_worker(loop: Any, expected: _WorkerHandle | None = None) -> None:
    with _WORKER_LOCK:
        current = _WORKERS.get(loop)
        if current is None:
            handle = expected
        elif expected is None or current is expected:
            _WORKERS.pop(loop, None)
            handle = current
        else:
            handle = expected
    if handle is not None:
        handle.close_and_reap()


def _reap_all_workers() -> None:
    with _WORKER_LOCK:
        handles = list(_WORKERS.values())
        _WORKERS.clear()
    for handle in handles:
        handle.close_and_reap()


atexit.register(_reap_all_workers)


def _ensure_loop_cleanup(loop: Any) -> None:
    if loop in _WRAPPED_LOOPS:
        return
    _WRAPPED_LOOPS.add(loop)
    orig_close = loop.close

    def _close_with_reap():
        _reap_worker(loop)
        return orig_close()

    try:
        loop.close = _close_with_reap
    except Exception:
        pass


def prewarm_cpu_worker() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _ensure_loop_cleanup(loop)
    try:
        worker = _get_or_spawn_worker(loop)
        worker.parent.send(("__warmup__", None))
    except Exception:
        pass


def _execute(loop, operation, payload, timeout, cancelled):
    started = time.monotonic()
    worker = _get_or_spawn_worker(loop)
    try:
        try:
            worker.parent.send((operation, payload))
        except (BrokenPipeError, EOFError, OSError):
            _reap_worker(loop, worker)
            worker = _get_or_spawn_worker(loop)
            worker.parent.send((operation, payload))

        while True:
            if cancelled.is_set():
                raise InterruptedError("CPU operation cancelled")
            if time.monotonic() - started >= timeout:
                raise TimeoutError(f"CPU {operation} exceeded its deadline")
            if worker.parent.poll(0.01):
                break
            if not worker.process.is_alive():
                raise RuntimeError(f"CPU {operation} exited without a result")

        try:
            ok, result = worker.parent.recv()
        except (EOFError, OSError) as exc:
            raise RuntimeError(f"CPU {operation} exited without a result") from exc
    except BaseException:
        _reap_worker(loop, worker)
        raise

    if not ok:
        if result == "ValueError":
            raise ValueError(f"CPU {operation} rejected invalid input")
        if result == "PublishVerificationError":
            from sag_api.services.tree_publish_service import PublishVerificationError

            raise PublishVerificationError(f"CPU {operation} rejected an invalid tree candidate")
        raise RuntimeError(f"CPU {operation} failed ({result})")
    return result


async def run_cpu(operation, payload):
    from sag_api.core.config import settings

    loop = asyncio.get_running_loop()
    _ensure_loop_cleanup(loop)
    slot = _SLOTS.setdefault(loop, asyncio.Semaphore(1))
    deadline = loop.time() + settings.knowledge_graph_timeout_seconds
    await asyncio.wait_for(slot.acquire(), timeout=max(0, deadline - loop.time()))
    try:
        cancelled = threading.Event()
        task = asyncio.create_task(
            asyncio.to_thread(
                _execute,
                loop,
                operation,
                payload,
                max(0, deadline - loop.time()),
                cancelled,
            )
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            # Do not release admission until the child has actually been reaped.
            try:
                await asyncio.shield(task)
            except Exception:
                pass
            raise
    finally:
        slot.release()
