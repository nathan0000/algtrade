"""
position_store.py — JSON-backed persistence for open IronCondor positions.

Why this exists:
  launchd spawns a brand-new OS process for every scheduled entry trigger.
  In-memory IronCondor objects die with that process the instant it exits.
  The monitor daemon is a SEPARATE, long-running process that must discover
  whatever positions any entry process has opened.

  This module is the handoff point: entry processes append positions here
  after fills; the monitor daemon polls this file to discover new work and
  updates it as positions close.

Concurrency:
  Entry runs (every 30 min) and the monitor daemon (continuous) both touch
  this file. Writes use a lockfile + atomic replace to avoid corruption if
  they overlap.
"""

from __future__ import annotations

import json
import logging
import os
import time
import fcntl
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .models import (
    OptionLeg, OptionRight, VerticalSpread, IronCondor,
    SpreadSide, SpreadState, CondorState
)

log = logging.getLogger("PositionStore")

DEFAULT_STORE_PATH = Path.home() / ".spx_trader" / "positions.json"
LOCK_SUFFIX = ".lock"


@contextmanager
def _file_lock(lock_path: Path, timeout: float = 10.0):
    """Simple advisory file lock so concurrent processes don't corrupt the store."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    deadline = time.time() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() > deadline:
                fd.close()
                raise TimeoutError(f"Could not acquire lock {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


class PositionStore:
    """
    Append/read/update IronCondor positions in a JSON file shared between
    the entry-trigger process and the monitor daemon process.

    File shape:
        {
          "positions": [
            { "id": "...", "state": "OPEN", "call_spread": {...}, ... },
            ...
          ]
        }
    """

    def __init__(self, path: Path = DEFAULT_STORE_PATH):
        self.path      = path
        self.lock_path = path.with_suffix(path.suffix + LOCK_SUFFIX)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_raw({"positions": []})

    # =========================================================================
    # Public API
    # =========================================================================

    def add_position(self, condor: IronCondor, position_id: str) -> None:
        """Append a newly-entered condor to the store."""
        with _file_lock(self.lock_path):
            data = self._read_raw()
            record = self._condor_to_dict(condor, position_id)
            data["positions"].append(record)
            self._write_raw(data)
        log.info(f"Position stored: {position_id}")

    def update_position(self, position_id: str, condor: IronCondor) -> None:
        """Overwrite an existing position's data (e.g. after a side closes)."""
        with _file_lock(self.lock_path):
            data = self._read_raw()
            found = False
            for i, rec in enumerate(data["positions"]):
                if rec["id"] == position_id:
                    data["positions"][i] = self._condor_to_dict(
                        condor, position_id
                    )
                    found = True
                    break
            if not found:
                log.warning(f"update_position: id {position_id} not found, appending")
                data["positions"].append(self._condor_to_dict(condor, position_id))
            self._write_raw(data)

    def list_open_positions(self) -> list[tuple[str, IronCondor]]:
        """Return [(position_id, IronCondor), ...] for positions not fully closed."""
        with _file_lock(self.lock_path):
            data = self._read_raw()
        result = []
        for rec in data["positions"]:
            condor = self._dict_to_condor(rec)
            if not condor.all_closed:
                result.append((rec["id"], condor))
        return result

    def list_all_positions(self) -> list[tuple[str, IronCondor]]:
        with _file_lock(self.lock_path):
            data = self._read_raw()
        return [(rec["id"], self._dict_to_condor(rec)) for rec in data["positions"]]

    def mark_closed(self, position_id: str) -> None:
        with _file_lock(self.lock_path):
            data = self._read_raw()
            for rec in data["positions"]:
                if rec["id"] == position_id:
                    rec["state"] = CondorState.CLOSED.value
            self._write_raw(data)

    # =========================================================================
    # Serialization helpers
    # =========================================================================

    @staticmethod
    def _leg_to_dict(leg: OptionLeg) -> dict:
        d = asdict(leg)
        d["right"] = leg.right.value   # enum -> str
        return d

    @staticmethod
    def _leg_from_dict(d: dict) -> OptionLeg:
        d = dict(d)
        d["right"] = OptionRight(d["right"])
        return OptionLeg(**d)

    @classmethod
    def _spread_to_dict(cls, spread: Optional[VerticalSpread]) -> Optional[dict]:
        if spread is None:
            return None
        return {
            "side":            spread.side.value,
            "short_leg":       cls._leg_to_dict(spread.short_leg),
            "long_leg":        cls._leg_to_dict(spread.long_leg),
            "quantity":        spread.quantity,
            "multiplier":      spread.multiplier,
            "state":           spread.state.value,
            "entry_order_id":  spread.entry_order_id,
            "close_order_id":  spread.close_order_id,
            "filled_credit":   spread.filled_credit,
            "close_debit":     spread.close_debit,
        }

    @classmethod
    def _spread_from_dict(cls, d: Optional[dict]) -> Optional[VerticalSpread]:
        if d is None:
            return None
        return VerticalSpread(
            side            = SpreadSide(d["side"]),
            short_leg       = cls._leg_from_dict(d["short_leg"]),
            long_leg        = cls._leg_from_dict(d["long_leg"]),
            quantity        = d.get("quantity", 1),
            multiplier      = d.get("multiplier", 100),
            state           = SpreadState(d["state"]),
            entry_order_id  = d.get("entry_order_id", 0),
            close_order_id  = d.get("close_order_id", 0),
            filled_credit   = d.get("filled_credit", 0.0),
            close_debit     = d.get("close_debit", 0.0),
        )

    @classmethod
    def _condor_to_dict(cls, condor: IronCondor, position_id: str) -> dict:
        return {
            "id":           position_id,
            "state":        condor.state.value,
            "call_spread":  cls._spread_to_dict(condor.call_spread),
            "put_spread":   cls._spread_to_dict(condor.put_spread),
            "updated_at":   time.time(),
        }

    @classmethod
    def _dict_to_condor(cls, rec: dict) -> IronCondor:
        return IronCondor(
            call_spread = cls._spread_from_dict(rec.get("call_spread")),
            put_spread  = cls._spread_from_dict(rec.get("put_spread")),
            state       = CondorState(rec.get("state", "OPEN")),
        )

    # =========================================================================
    # Raw file I/O (atomic write)
    # =========================================================================

    def _read_raw(self) -> dict:
        if not self.path.exists():
            return {"positions": []}
        with open(self.path, "r") as f:
            content = f.read().strip()
            if not content:
                return {"positions": []}
            return json.loads(content)

    def _write_raw(self, data: dict) -> None:
        """Atomic write: write to temp file, then os.replace (rename)."""
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self.path)
