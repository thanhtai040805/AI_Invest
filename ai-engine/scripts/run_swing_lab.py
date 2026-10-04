"""Bounded, preregistered local swing research; stdlib only, no model loading.

register --manifest FILE freezes a hypothesis, inputs, code and criteria.
start --id ID runs only the frozen allowlisted local argv. record recomputes
cash ledger evidence from complete CSVs, never a winning-trade excerpt.
Offline records cannot confirm actual broker profitability.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / "lab/protocol.json"
TERMINAL = {"REJECTED", "DIAGNOSTIC_ONLY", "NEEDS_DATA", "FORWARD_CANDIDATE"}


def now():
    return datetime.now(timezone.utc).isoformat()


def number(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a financial number")
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError("Financial numbers must be finite")
    return value


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def constant(value):
        raise ValueError(f"Nonfinite JSON value: {value}")
    with Path(path).open(encoding="utf-8-sig") as handle:
        return json.load(handle, parse_float=Decimal, parse_constant=constant, object_pairs_hook=pairs)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n").encode("utf-8")


def write_new(path, value):
    """Publish a complete immutable file without overwriting an earlier record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def replace_config(path, value):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local_path(value, base=ROOT):
    path = (base / str(value)).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"Path must stay in ai-engine: {value}")
    return path


@contextmanager
def lock(ledger, name="registry"):
    ledger.mkdir(parents=True, exist_ok=True)
    with (ledger / f".{name}.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(f"Another LAB process holds the {name} lock") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def process_alive(pid, started=None):
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            if started:
                times = [wintypes.FILETIME() for _ in range(4)]
                if api.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
                    created = ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) / 10_000_000 - 11_644_473_600
                    if created > datetime.fromisoformat(started).timestamp() + 5:
                        return False  # The OS reused an earlier experiment's PID.
            code = wintypes.DWORD()
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def events(directory):
    return [read_json(path) for path in sorted((directory / "events").glob("*.json"))]


def event(directory, action, **fields):
    previous = events(directory)
    value = {"time": now(), "action": action, **fields}
    write_new(directory / "events" / f"{len(previous) + 1:06d}.json", value)
    return value


def experiments(ledger):
    result = []
    for path in sorted((ledger / "experiments").glob("*/manifest.json")):
        manifest = read_json(path)
        history = events(path.parent)
        if history and file_hash(path) != history[0]["manifest_sha256"]:
            raise ValueError(f"Immutable preregistration was modified: {manifest['id']}")
        state = history[-1] if history else {"status": "NEEDS_DATA", "reason": "Incomplete registration cannot execute"}
        result.append({"id": manifest["id"], "family": manifest["family"], "kind": manifest["kind"],
                       "status": state.get("status", "REGISTERED"), "state": state, "manifest": manifest})
    return result


def family_state(protocol, records):
    states = []
    budget = int(protocol["max_failed_iterations_per_family"])
    for family in sorted(protocol["families"], key=lambda item: item["priority"]):
        own = [record for record in records if record["family"] == family["id"]]
        failed = sum(record["status"] == "REJECTED" for record in own)
        blocked = family.get("retired", False) or failed >= budget
        gap_records = [record for record in own if record["status"] == "NEEDS_DATA"]
        needs_data = (not family.get("data_available") or (gap_records and not family.get("data_resolution")))
        states.append({**family, "failed_iterations": failed, "remaining_failed_iterations": max(0, budget - failed),
                       "state": "RETIRED" if blocked else ("NEEDS_DATA" if needs_data else "READY"),
                       "observed_data_gaps": [r["state"].get("evidence", {}).get("data_gaps", []) for r in gap_records],
                       "registered_or_running": [r["id"] for r in own if r["status"] in {"REGISTERED", "RUNNING", "AWAITING_RECORD"}]})
    return states


def initialize(ledger, protocol_path, update=False):
    protocol = read_json(protocol_path)
    if number(protocol["initial_nav_vnd"]) != 1_000_000_000:
        raise ValueError("Current user mandate is a 1 billion VND research NAV")
    if protocol.get("live_capital_authorization") is not None or protocol.get("live_risk_authorization") is not None:
        raise ValueError("This local LAB cannot install live trading authorization")
    if not (0 < number(protocol["default_max_wall_seconds"]) <= number(protocol["maximum_max_wall_seconds"])):
        raise ValueError("Invalid wall time limits")
    if not 1 <= int(protocol["max_failed_iterations_per_family"]) <= 2:
        raise ValueError("At most two failed iterations are allowed per family")
    snapshot = ledger / "protocol.json"
    if snapshot.exists():
        if encoded(read_json(snapshot)) != encoded(protocol):
            if not update:
                raise ValueError("Explicit init --update-protocol required; earlier protocol versions and family history are preserved")
            if any(record["status"] == "RUNNING" for record in experiments(ledger)):
                raise ValueError("Do not change the active protocol during an experiment")
            old_hash = file_hash(snapshot)
            old_version = ledger / "protocol_versions" / f"{old_hash}.json"
            if not old_version.exists():
                write_new(old_version, read_json(snapshot))
            replace_config(snapshot, protocol)
            event(ledger, "PROTOCOL_UPDATED", old_sha256=old_hash, new_sha256=file_hash(snapshot))
    else:
        write_new(snapshot, protocol)
    version = ledger / "protocol_versions" / f"{file_hash(snapshot)}.json"
    if not version.exists():
        write_new(version, protocol)
    return protocol


def freeze_files(paths):
    if not isinstance(paths, list) or not paths:
        raise ValueError("Declare nonempty source_paths and code_paths")
    return {str(local_path(path)): file_hash(local_path(path)) for path in paths}


def register(ledger, protocol, value, imported=False):
    value = dict(value)
    identifier = value["id"]
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", identifier):
        raise ValueError("Use a simple experiment id of at most 80 characters")
    directory = ledger / "experiments" / identifier
    if directory.exists():
        raise ValueError(f"Experiment {identifier} already exists; prior records are immutable")
    records = experiments(ledger)
    family = next((item for item in family_state(protocol, records) if item["id"] == value["family"]), None)
    if family is None:
        raise ValueError("Family must be one of the protocol's economic mechanisms")
    if not imported and family["state"] != "READY":
        raise ValueError(f"Family {family['id']} is {family['state']}: {family['required_data']}")
    if not imported and len(family["registered_or_running"]) >= family["remaining_failed_iterations"]:
        raise ValueError("Pending registrations already consume this family's remaining failure budget")
    if value.get("kind") not in ({"imported"} if imported else {"diagnostic", "economic_model"}):
        raise ValueError("kind must be diagnostic or economic_model")
    for key in ("hypothesis", "falsification", "next_decision"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"Missing preregistration field: {key}")
    value["source_sha256"] = freeze_files(value["source_paths"])
    value["code_sha256"] = freeze_files(value["code_paths"])
    value["protocol_sha256"] = file_hash(ledger / "protocol.json")
    value["controller_sha256"] = file_hash(Path(__file__))
    value["criteria"] = protocol["criteria"]
    value["initial_nav_vnd"] = protocol["initial_nav_vnd"]
    value["inspected_periods"] = protocol["inspected_periods"]
    value["registered_at"] = now()
    periods = value.get("evaluation_periods")
    if not isinstance(periods, list) or not periods:
        raise ValueError("Declare evaluation_periods before executing")
    for period in periods:
        if date.fromisoformat(period["start"]) > date.fromisoformat(period["end"]):
            raise ValueError("Invalid evaluation period")
        if period["role"] not in {"development", "diagnostic", "forward"}:
            raise ValueError("Unknown evaluation role")
        if period["role"] == "forward" and inspected(period, protocol["inspected_periods"]):
            raise ValueError("An inspected period cannot be registered as fresh forward evidence")
        if period["role"] == "forward" and date.fromisoformat(period["start"]) <= datetime.now(timezone(timedelta(hours=7))).date():
            raise ValueError("Forward evidence must begin after preregistration in Vietnam time; past observations are diagnostic")
    fingerprint = hashlib.sha256(encoded({key: value[key] for key in ("family", "hypothesis", "source_sha256", "code_sha256", "evaluation_periods")})).hexdigest()
    if any(record["manifest"].get("hypothesis_fingerprint") == fingerprint for record in records):
        raise ValueError("This hypothesis, data and code were already registered")
    value["hypothesis_fingerprint"] = fingerprint
    if not imported:
        maximum = int(value.get("max_wall_seconds", protocol["default_max_wall_seconds"]))
        if not 0 < maximum <= int(protocol["maximum_max_wall_seconds"]):
            raise ValueError("Experiment exceeds the protocol wall time bound")
        value["max_wall_seconds"] = maximum
        output = local_path(value["output_dir"])
        if not output.is_relative_to(ledger / "runs"):
            raise ValueError("Outputs must stay within this LAB's runs directory")
        result = local_path(value["results_path"])
        if not result.is_relative_to(output):
            raise ValueError("results_path must stay in output_dir")
        argv = value.get("command")
        if not isinstance(argv, list) or len(argv) < 2 or not all(isinstance(item, str) for item in argv):
            raise ValueError("command must be a Python argv list, never shell text")
        entrypoint = local_path(argv[1])
        if entrypoint not in {local_path(path) for path in protocol["allowed_entrypoints"]}:
            raise ValueError("Only allowlisted offline research scripts may execute")
        if str(entrypoint) not in value["code_sha256"]:
            raise ValueError("The entrypoint must be included in code_paths")
        if "--output" not in argv or local_path(argv[argv.index("--output") + 1]) != output:
            raise ValueError("Command --output must match the preregistered output_dir")
        if "--bars" not in argv or str(local_path(argv[argv.index("--bars") + 1])) not in value["source_sha256"]:
            raise ValueError("Command --bars must be one of the frozen source_paths")
        value["command"] = [sys.executable, str(entrypoint), *argv[2:]]
    write_new(directory / "manifest.json", value)
    event(directory, "REGISTERED", status="REGISTERED", manifest_sha256=file_hash(directory / "manifest.json"))
    return {"id": identifier, "status": "REGISTERED", "manifest": str(directory / "manifest.json")}


def inspected(period, known):
    return any(period["start"] <= item["end"] and period["end"] >= item["start"] for item in known)


def import_existing(ledger, protocol):
    imported = []
    for item in protocol["existing_rejections"]:
        directory = ledger / "experiments" / item["id"]
        if (directory / "manifest.json").exists():
            imported.append({"id": item["id"], "status": "ALREADY_IMPORTED"})
            continue
        root = local_path(item["directory"])
        report = read_json(root / item["results"])
        old = read_json(root / "protocol.json")
        if report.get(item["expected_status_key"]) != item["expected_status"]:
            raise ValueError(f"Existing rejection evidence disagrees for {item['id']}")
        value = {"id": item["id"], "family": item["family"], "kind": "imported",
                 "hypothesis": "Preserve the previously evaluated rejected family; no retrospective preregistration claim",
                 "falsification": "Existing cash ledger profitability gates failed",
                 "next_decision": "Retire this family and prioritize an independent economic mechanism",
                 "source_paths": [str(root / "protocol.json"), str(root / item["results"])],
                 "code_paths": [str(Path(__file__).resolve())],
                 "evaluation_periods": [{"start": "2023-01-01", "end": "2026-10-02", "role": "diagnostic"}],
                 "original_protocol": old, "original_results": report,
                 "retrospective_import": True}
        register(ledger, protocol, value, imported=True)
        event(directory, "IMPORTED_REJECTION", status="REJECTED", original_status=item["expected_status"],
              reason="Previously rejected profitability evidence; full original report retained")
        imported.append({"id": item["id"], "status": "REJECTED"})
    return imported


def verify(manifest):
    if file_hash(Path(__file__)) != manifest["controller_sha256"]:
        raise ValueError("Frozen LAB controller changed; register a new experiment")
    for group in ("source_sha256", "code_sha256"):
        for path, expected in manifest[group].items():
            if file_hash(local_path(path)) != expected:
                raise ValueError(f"Frozen {group} changed: {path}; register a new experiment")


def terminate_pid(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    else:
        import signal
        os.killpg(pid, signal.SIGKILL)


def reconcile_orphans(ledger):
    for record in experiments(ledger):
        state = record["state"]
        if record["status"] != "RUNNING" or process_alive(state.get("controller_pid"), state.get("time")):
            continue
        alive = process_alive(state.get("child_pid"), state.get("time"))
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(state["time"])).total_seconds()
        if alive and elapsed <= int(state["max_wall_seconds"]):
            continue
        directory = ledger / "experiments" / record["id"]
        if alive:
            terminate_pid(int(state["child_pid"]))
            event(directory, "ORPHAN_TIMEOUT", status="REJECTED", reason="Interrupted controller's child exceeded the frozen wall time")
        else:
            present = local_path(record["manifest"]["results_path"]).is_file()
            event(directory, "ORPHAN_RECOVERED", status="AWAITING_RECORD" if present else "REJECTED",
                  reason="Execution owner and child are gone; reconcile complete output" if present else "Interrupted execution left no complete result")


def start(ledger, protocol, identifier):
    directory = ledger / "experiments" / identifier
    with lock(ledger, "execution"):
        with lock(ledger):
            records = experiments(ledger)
            selected = next(record for record in records if record["id"] == identifier)
            if selected["status"] != "REGISTERED":
                raise ValueError("Only a newly REGISTERED experiment can execute")
            for record in records:
                if record["status"] == "RUNNING":
                    state = record["state"]
                    if process_alive(state.get("controller_pid"), state.get("time")) or process_alive(state.get("child_pid"), state.get("time")):
                        raise ValueError(f"Experiment {record['id']} is still running; do not overlap rounds")
                    event(ledger / "experiments" / record["id"], "ORPHAN_RECOVERED", status="AWAITING_RECORD",
                          reason="Execution owner and child are gone; reconcile existing output before proceeding")
            manifest = selected["manifest"]
            family = next(item for item in family_state(protocol, records) if item["id"] == manifest["family"])
            if family["state"] != "READY":
                raise ValueError("Family budget or data availability no longer permits execution")
            verify(manifest)
            version = ledger / "protocol_versions" / f"{manifest['protocol_sha256']}.json"
            if not version.exists() or file_hash(version) != manifest["protocol_sha256"]:
                raise ValueError("Frozen experiment protocol is missing or changed")
            if local_path(manifest["output_dir"]).exists():
                raise ValueError("Output directory already exists; never overwrite an earlier or partial run")
            event(directory, "LAUNCHING", status="RUNNING", controller_pid=os.getpid(), child_pid=None,
                  max_wall_seconds=manifest["max_wall_seconds"])
            try:
                with (directory / "stdout.log").open("xb") as stdout, (directory / "stderr.log").open("xb") as stderr:
                    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
                    process = subprocess.Popen(manifest["command"], cwd=ROOT, stdout=stdout, stderr=stderr, **options)
            except OSError as error:
                event(directory, "LAUNCH_FAILED", status="REJECTED", reason=str(error))
                raise
            try:
                event(directory, "STARTED", status="RUNNING", controller_pid=os.getpid(), child_pid=process.pid,
                      max_wall_seconds=manifest["max_wall_seconds"], python=sys.version)
            except OSError:
                terminate_pid(process.pid)
                process.wait(timeout=15)
                raise
        print(json.dumps({"id": identifier, "status": "RUNNING", "pid": process.pid}), flush=True)
        began = time.monotonic()
        try:
            code = process.wait(timeout=manifest["max_wall_seconds"])
            failed = code != 0
            reason = f"Research process exited with code {code}" if failed else "Reconcile immutable result evidence with record"
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            terminate_pid(process.pid)
            process.wait(timeout=15)
            code, failed, reason = process.returncode, True, "Predeclared wall time exhausted or execution interrupted"
        with lock(ledger):
            event(directory, "FINISHED", status="REJECTED" if failed else "AWAITING_RECORD", returncode=code,
                  elapsed_seconds=round(time.monotonic() - began, 3), reason=reason)
        return {"id": identifier, "status": "REJECTED" if failed else "AWAITING_RECORD", "reason": reason}


def ledger_metrics(period, manifest, prefix=""):
    output = local_path(manifest["output_dir"])
    paths = [local_path(period[f"{prefix}trades_path"]), local_path(period[f"{prefix}nav_path"])]
    if any(not path.is_relative_to(output) for path in paths):
        raise ValueError("Financial evidence must be inside the preregistered run output")
    with paths[0].open(encoding="utf-8-sig", newline="") as handle:
        trades = list(csv.DictReader(handle))
    with paths[1].open(encoding="utf-8-sig", newline="") as handle:
        nav = list(csv.DictReader(handle))
    if not nav:
        raise ValueError("Missing full cash NAV ledger")
    pnl, returns, keys = [], [], set()
    for row in trades:
        entry_day, exit_day = date.fromisoformat(row["entry_date"][:10]), date.fromisoformat(row["exit_date"][:10])
        if exit_day <= entry_day:
            raise ValueError("Closed trades must exit after the entry decision; session settlement remains a separate contract")
        cost, credit = number(row["entry_cash_cost"]), number(row["exit_cash_credit"])
        if cost <= 0 or credit < 0:
            raise ValueError("Invalid trade cash reconciliation")
        gain, ret = number(row["net_pnl_vnd"]), number(row["net_return"])
        if abs(gain - (credit - cost)) > Decimal("0.01") or abs(ret - (credit / cost - 1)) > Decimal("0.000001"):
            raise ValueError("Trade cash amounts do not reconcile to net PnL and return")
        if row["entry_date"][:10] < period["start"] or row["exit_date"][:10] > period["end"]:
            raise ValueError("Trade evidence is outside the declared evaluation period")
        key = (row["ticker"], row["entry_date"], row["exit_date"])
        if key in keys:
            raise ValueError("Duplicate completed trade evidence")
        keys.add(key)
        pnl.append(gain)
        returns.append(ret)
    initial = number(manifest["initial_nav_vnd"])
    peak, drawdown, stale = initial, Decimal(0), 0
    previous = None
    for row in nav:
        day = date.fromisoformat(row["date"][:10])
        if previous is not None and day <= previous:
            raise ValueError("NAV must contain complete, strictly increasing session rows")
        previous = day
        if not period["start"] <= day.isoformat() <= period["end"]:
            raise ValueError("NAV evidence is outside the declared evaluation period")
        cash, receivable, equity, total = (number(row[key]) for key in ("cash", "unsettled_receivable", "positions_value", "total_nav"))
        if min(cash, receivable, equity, total) < 0 or abs(total - cash - receivable - equity) > Decimal("0.01"):
            raise ValueError("NAV must reconcile settled cash, unsettled proceeds and positions")
        positions = number(row["open_positions"])
        if positions < 0 or positions != int(positions) or (positions == 0 and equity != 0):
            raise ValueError("Position count and marked position value disagree")
        marks = number(row["stale_marks"])
        if marks != int(marks) or marks < 0:
            raise ValueError("Invalid stale mark count")
        stale += int(marks)
        peak = max(peak, total)
        drawdown = max(drawdown, 1 - total / peak)
    profit = sum((value for value in pnl if value > 0), Decimal(0))
    loss = -sum((value for value in pnl if value < 0), Decimal(0))
    last = nav[-1]
    if number(last["open_positions"]) == 0 and abs(number(last["total_nav"]) - initial - sum(pnl, Decimal(0))) > Decimal("0.01"):
        raise ValueError("Completed trade PnL does not reconcile to full NAV; do not omit losing trades")
    return {"closed_trades": len(trades), "closed_net_pnl_vnd": sum(pnl, Decimal(0)),
            "total_return": number(last["total_nav"]) / initial - 1,
            "final_nav": number(last["total_nav"]), "final_settled_cash": number(last["cash"]),
            "final_unsettled_receivable": number(last["unsettled_receivable"]),
            "profit_factor": profit / loss if loss else None, "net_profit_vnd": profit, "net_loss_vnd": loss,
            "expectancy_net_return": sum(returns, Decimal(0)) / len(returns) if returns else None,
            "max_drawdown": drawdown, "stale_marks": stale, "open_positions": int(number(last["open_positions"])),
            "evidence_sha256": {str(path): file_hash(path) for path in paths}, "nav_sessions": len(nav)}


def evaluate(result, manifest):
    if manifest["kind"] == "diagnostic":
        return "DIAGNOSTIC_ONLY", {"reason": "Predeclared feasibility/data audit; no profitable model claim", "complete_results": result}
    periods = result.get("periods")
    if not isinstance(periods, list) or not periods:
        return "NEEDS_DATA", {"reason": "Provide full preregistered period trade and cash NAV ledgers, including stress"}
    criteria = manifest["criteria"]
    outcomes, failures, data_gaps = [], [], []
    declared = {(p["start"], p["end"], p["role"]) for p in manifest["evaluation_periods"]}
    supplied = {(p["start"], p["end"], p["role"]) for p in periods}
    if supplied != declared or len(supplied) != len(periods):
        raise ValueError("Results must cover every predeclared period exactly once; losers cannot be omitted")
    for period in periods:
        base, stress = ledger_metrics(period, manifest), ledger_metrics(period, manifest, "stress_")
        if set(base["evidence_sha256"]).intersection(stress["evidence_sha256"]) or set(base["evidence_sha256"].values()).intersection(stress["evidence_sha256"].values()):
            raise ValueError("Stress evidence must come from a separate cost run, not reused base ledgers")
        label = period.get("name", period["start"])
        outcomes.append({"name": label, "role": period["role"], "base": base, "stress": stress})
        for mode, metric in (("base", base), ("stress", stress)):
            if metric["stale_marks"] > int(criteria["maximum_stale_marks"]) or metric["open_positions"] > int(criteria["maximum_open_positions"]) or metric["final_unsettled_receivable"] > number(criteria["maximum_unsettled_receivable_vnd"]):
                data_gaps.append(f"{label}/{mode}: stale marks, unresolved positions or unsettled proceeds")
            if metric["total_return"] <= 0 or metric["closed_net_pnl_vnd"] <= 0:
                failures.append(f"{label}/{mode}: nonpositive net NAV or closed cash PnL")
            if metric["max_drawdown"] > number(criteria["maximum_drawdown"]):
                failures.append(f"{label}/{mode}: drawdown exceeds frozen research limit")
        if base["net_profit_vnd"] <= 0 or (base["profit_factor"] is not None and base["profit_factor"] < number(criteria["minimum_profit_factor"])):
            failures.append(f"{label}: inadequate net profit factor")
        if base["expectancy_net_return"] is None or base["expectancy_net_return"] < number(criteria["minimum_expectancy_net_return"]):
            failures.append(f"{label}: inadequate net expectancy")
    development = sum(p["base"]["closed_trades"] for p in outcomes if p["role"] == "development")
    evaluation = sum(p["base"]["closed_trades"] for p in outcomes if p["role"] != "development")
    if development < int(criteria["minimum_development_closed_trades"]) or evaluation < int(criteria["minimum_evaluation_closed_trades"]):
        failures.append("Insufficient development/evaluation closed trade samples")
    evidence = {"periods": outcomes, "failures": failures, "data_gaps": data_gaps,
                "win_rate_gate": False, "actual_broker_profit_confirmed": False}
    if failures:
        return "REJECTED", evidence
    if data_gaps:
        return "NEEDS_DATA", evidence
    evidence["self_declared_data_evidence"] = result.get("data_evidence", {})
    evidence["promotion_gate"] = "CLOSED_ACCOUNTING_AND_SOURCE_CONTRACT_VALIDATION_REQUIRED"
    evidence["data_gaps"].append("Complete session coverage, independent stress runs, verified point-in-time coverage and entitlement/fill contracts still require validation; supplied booleans cannot unlock promotion")
    return "NEEDS_DATA", evidence


def record(ledger, identifier, results_path=None):
    directory = ledger / "experiments" / identifier
    manifest = read_json(directory / "manifest.json")
    history = events(directory)
    if not history or file_hash(directory / "manifest.json") != history[0]["manifest_sha256"]:
        raise ValueError("Immutable preregistration is missing or was modified")
    state = history[-1]
    if state["status"] in TERMINAL:
        raise ValueError("Final records cannot be overwritten; register a new experiment")
    if state["status"] == "RUNNING" and (process_alive(state.get("controller_pid"), state.get("time")) or process_alive(state.get("child_pid"), state.get("time"))):
        raise ValueError("Cannot record partial output while the experiment is running")
    if not any(item["action"] == "STARTED" for item in history):
        raise ValueError("Results require an executed preregistration; use import-existing for older research")
    path = local_path(results_path or manifest["results_path"])
    if path != local_path(manifest["results_path"]):
        raise ValueError("Record only the preregistered results_path")
    verify(manifest)
    result = read_json(path)
    status, evidence = evaluate(result, manifest)
    write_new(directory / "evaluation.json", {"status": status, "results_sha256": file_hash(path),
                                               "complete_results": result, "evidence": evidence})
    event(directory, "RECORDED", status=status, results_sha256=file_hash(path), evidence=evidence)
    return {"id": identifier, "status": status, "evidence": evidence}


def status(ledger, protocol):
    records = experiments(ledger)
    return {"ledger": str(ledger), "initial_nav_vnd": protocol["initial_nav_vnd"],
            "live_capital_authorization": None, "profit_confirmed": False,
            "experiments": [{key: record[key] for key in ("id", "family", "kind", "status")} for record in records],
            "families": family_state(protocol, records), "inspected_periods": protocol["inspected_periods"]}


def next_action(ledger, protocol):
    records = experiments(ledger)
    active = [r for r in records if r["status"] in {"RUNNING", "AWAITING_RECORD", "REGISTERED"}]
    if active:
        selected = sorted(active, key=lambda r: {"RUNNING": 0, "AWAITING_RECORD": 1, "REGISTERED": 2}[r["status"]])[0]
        action = {"RUNNING": "Observe bounded execution; do not start another round", "AWAITING_RECORD": "record", "REGISTERED": "start"}[selected["status"]]
        if selected["status"] == "RUNNING" and not (process_alive(selected["state"].get("controller_pid"), selected["state"].get("time")) or process_alive(selected["state"].get("child_pid"), selected["state"].get("time"))):
            action = "record"
        return {"action": action, "id": selected["id"], "status": selected["status"]}
    ready = [f for f in family_state(protocol, records) if f["state"] == "READY"
             and not (f["id"] == "feasibility-audit" and any(r["family"] == f["id"] for r in records))]
    missing = [f for f in family_state(protocol, records) if f["state"] == "NEEDS_DATA"]
    return {"action": "Preregister the next independent economic hypothesis" if ready else "Collect required data or declare the remaining families exhausted",
            "family": ready[0] if ready else None, "missing_data_families": missing,
            "rule": "Two failed iterations retire a family; missing data in one family does not stop other actionable research"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--ledger", type=Path)
    commands = parser.add_subparsers(dest="action", required=True)
    initialization = commands.add_parser("init")
    initialization.add_argument("--update-protocol", action="store_true")
    for action in ("import-existing", "status", "next"):
        commands.add_parser(action)
    registration = commands.add_parser("register")
    registration.add_argument("--manifest", type=Path, required=True)
    for action in ("start", "record"):
        command = commands.add_parser(action)
        command.add_argument("--id", required=True)
        if action == "record":
            command.add_argument("--results", type=Path)
    args = parser.parse_args()
    try:
        source = read_json(args.protocol)
        ledger = local_path(args.ledger or source["ledger_directory"])
        if args.action == "start":
            with lock(ledger):
                protocol = initialize(ledger, args.protocol)
                reconcile_orphans(ledger)
            result = start(ledger, protocol, args.id)
        else:
            with lock(ledger):
                protocol = initialize(ledger, args.protocol, update=getattr(args, "update_protocol", False))
                reconcile_orphans(ledger)
                if args.action in {"init", "import-existing"}:
                    result = {"imported": import_existing(ledger, protocol), **status(ledger, protocol)}
                elif args.action == "register":
                    result = register(ledger, protocol, read_json(args.manifest))
                elif args.action == "record":
                    result = record(ledger, args.id, args.results)
                elif args.action == "status":
                    result = status(ledger, protocol)
                else:
                    result = next_action(ledger, protocol)
        print(encoded(result).decode("utf-8"), end="")
    except (OSError, ValueError, KeyError, StopIteration, RuntimeError, ArithmeticError) as error:
        parser.exit(2, f"LAB: {error}\n")


if __name__ == "__main__":
    main()
