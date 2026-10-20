"""Single-tick TESTNET supervisor integrating durable risk state and recovery.

This is deliberately run-once orchestration, not a daemon or production bot.
It never chooses a signal, never enables live trading, and never retries an
ambiguous entry. Existing exposure is recovered only when explicit protective
levels are supplied; otherwise it fails closed.
"""
import hashlib
import json
from dataclasses import asdict, is_dataclass

from execution.runtime_guard import RuntimeRiskConfig, validate_runtime_state
from execution.runtime_state_store import RuntimeStateStore
from execution.safety import ExecutionConfig
from execution.testnet_entry_flow import execute_guarded_testnet_entry
from execution.testnet_recovery import recover_testnet_position


class TestnetSupervisor:
    __test__ = False  # Prevent pytest from treating this runtime class as a test case.
    def __init__(self, adapter, state_store: RuntimeStateStore,
                 runtime_config=RuntimeRiskConfig(),
                 execution_config=ExecutionConfig(mode="testnet"),
                 max_snapshot_age_seconds=30.0):
        self.adapter = adapter
        self.state_store = state_store
        self.runtime_config = runtime_config
        self.execution_config = execution_config
        self.max_snapshot_age_seconds = max_snapshot_age_seconds

    def run_once(self, symbol, intent=None, client_order_id=None,
                 client_order_prefix=None, recovery_stop=None, recovery_target=None):
        """Refresh exchange risk metrics, recover existing exposure, optionally enter once."""
        if self.execution_config.mode != "testnet" or self.execution_config.live_trading_enabled:
            return {"ok": False, "reason": "testnet_mode_required"}
        if not isinstance(symbol, str) or not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            return {"ok": False, "reason": "invalid_symbol"}
        try:
            preflight = self.adapter.account_preflight()
            if not isinstance(preflight, dict) or not preflight.get("can_trade"):
                self.state_store.record_execution_result(False)
                return {"ok": False, "reason": "account_preflight_failed", "detail": preflight}
            metrics = self.adapter.account_risk_metrics(symbol)
            if not isinstance(metrics, dict) or not metrics.get("ok"):
                self.state_store.record_execution_result(False)
                return {"ok": False, "reason": "exchange_risk_metrics_unavailable", "detail": metrics}
            self.state_store.update_exchange_metrics(
                metrics["open_positions"], metrics["symbol_notional_usdt"],
                metrics["total_notional_usdt"]
            )
        except Exception as exc:
            self.state_store.record_execution_result(False)
            return {"ok": False, "reason": "exchange_refresh_failed",
                    "error_type": type(exc).__name__, "entry_submitted": False}

        snapshot = self.state_store.load_snapshot()
        # Protection/recovery for already-open exposure takes precedence over the
        # new-entry position-count gate and emergency stop. Never open new risk here.
        try:
            position = self.adapter.testnet_position_snapshot(symbol)
        except Exception as exc:
            self.state_store.record_execution_result(False)
            return {"ok": False, "reason": "position_snapshot_unavailable",
                    "error_type": type(exc).__name__, "entry_submitted": False,
                    "requires_manual_reconciliation": True}
        if not isinstance(position, dict) or not position.get("ok"):
            self.state_store.record_execution_result(False)
            return {"ok": False, "reason": "position_snapshot_unavailable",
                    "entry_submitted": False, "requires_manual_reconciliation": True}
        try:
            amount = float(position["position_amount"])
        except (KeyError, TypeError, ValueError, OverflowError):
            amount = float("nan")
        if not __import__("math").isfinite(amount):
            self.state_store.record_execution_result(False)
            return {"ok": False, "reason": "position_amount_invalid",
                    "entry_submitted": False, "requires_manual_reconciliation": True}
        if amount != 0:
            if recovery_stop is None or recovery_target is None or not client_order_prefix:
                self.state_store.record_execution_result(False)
                return {"ok": False, "reason": "existing_position_requires_protection_metadata",
                        "entry_submitted": False, "requires_manual_reconciliation": True}
            recovery = recover_testnet_position(self.adapter, symbol, recovery_stop,
                                                recovery_target, client_order_prefix)
            self.state_store.record_execution_result(bool(recovery.get("ok")))
            return {"ok": bool(recovery.get("ok")), "mode": "testnet_supervisor_recovery",
                    "recovery": recovery, "entry_submitted": False}

        decision = validate_runtime_state(snapshot, self.runtime_config,
                                          self.max_snapshot_age_seconds)
        if not decision.allowed:
            self.state_store.record_execution_result(False)
            return {"ok": False, "reason": decision.reason, "stage": "runtime_gate",
                    "entry_submitted": False}

        if intent is None:
            self.state_store.record_execution_result(True)
            return {"ok": True, "mode": "testnet_supervisor_monitor_only",
                    "symbol": symbol, "entry_submitted": False, "risk_snapshot_refreshed": True}
        if intent.symbol != symbol or not client_order_id or not client_order_prefix:
            self.state_store.record_execution_result(False)
            return {"ok": False, "reason": "entry_metadata_missing_or_symbol_mismatch",
                    "entry_submitted": False}
        # Serialize entry decisions across processes sharing this SQLite database.
        # If this process crashes or sees an ambiguous result, keep the lock so that
        # a human must reconcile the exchange before any later entry can proceed.
        try:
            lock_claimed = self.state_store.claim_entry_lock(client_order_id)
        except Exception as exc:
            self.state_store.record_execution_result(False)
            return {"ok": False, "reason": "entry_lock_error",
                    "error_type": type(exc).__name__, "entry_submitted": False}
        if not lock_claimed:
            return {"ok": False, "reason": "entry_lock_held",
                    "active_lock": self.state_store.get_entry_lock(),
                    "entry_submitted": False, "requires_reconciliation": True}

        release_lock = False
        try:
            # Re-read exchange risk and position only after taking the lock. This
            # closes the stale-preflight race between concurrent supervisor calls.
            metrics = self.adapter.account_risk_metrics(symbol)
            if not isinstance(metrics, dict) or not metrics.get("ok"):
                self.state_store.record_execution_result(False)
                release_lock = True
                return {"ok": False, "reason": "exchange_risk_metrics_unavailable_under_lock",
                        "entry_submitted": False}
            self.state_store.update_exchange_metrics(
                metrics["open_positions"], metrics["symbol_notional_usdt"],
                metrics["total_notional_usdt"]
            )
            snapshot = self.state_store.load_snapshot()
            decision = validate_runtime_state(snapshot, self.runtime_config,
                                              self.max_snapshot_age_seconds)
            if not decision.allowed:
                self.state_store.record_execution_result(False)
                release_lock = True
                return {"ok": False, "reason": decision.reason, "stage": "runtime_gate_under_lock",
                        "entry_submitted": False}
            try:
                position = self.adapter.testnet_position_snapshot(symbol)
            except Exception as exc:
                self.state_store.record_execution_result(False)
                return {"ok": False, "reason": "position_recheck_failed_under_lock",
                        "error_type": type(exc).__name__, "entry_submitted": False,
                        "requires_reconciliation": True}
            if not isinstance(position, dict) or not position.get("ok"):
                self.state_store.record_execution_result(False)
                return {"ok": False, "reason": "position_recheck_failed_under_lock",
                        "entry_submitted": False, "requires_reconciliation": True}
            try:
                current_amount = float(position["position_amount"])
            except (KeyError, TypeError, ValueError, OverflowError):
                current_amount = float("nan")
            if not __import__("math").isfinite(current_amount):
                self.state_store.record_execution_result(False)
                return {"ok": False, "reason": "position_amount_invalid_under_lock",
                        "entry_submitted": False, "requires_reconciliation": True}
            if current_amount != 0:
                self.state_store.record_execution_result(False)
                return {"ok": False, "reason": "position_opened_by_concurrent_activity",
                        "entry_submitted": False, "requires_reconciliation": True}

            try:
                intent_payload = asdict(intent) if is_dataclass(intent) else vars(intent)
                fingerprint = hashlib.sha256(json.dumps(
                    intent_payload, sort_keys=True, separators=(",", ":"), default=str
                ).encode("utf-8")).hexdigest()
                claimed = self.state_store.claim_order_attempt(client_order_id, fingerprint)
            except Exception as exc:
                self.state_store.record_execution_result(False)
                release_lock = True
                return {"ok": False, "reason": "order_attempt_reservation_failed",
                        "error_type": type(exc).__name__, "entry_submitted": False}
            if not claimed:
                previous = self.state_store.get_order_attempt(client_order_id)
                self.state_store.record_execution_result(False)
                release_lock = previous is not None and previous.get("status") in {"COMPLETED", "REJECTED"}
                return {"ok": False, "reason": "client_order_id_already_reserved",
                        "previous_attempt": previous, "entry_submitted": False,
                        "requires_reconciliation": not release_lock}

            try:
                result = execute_guarded_testnet_entry(
                    self.adapter, intent, client_order_id, client_order_prefix, snapshot,
                    runtime_config=self.runtime_config, execution_config=self.execution_config
                )
                if result.get("ok"):
                    status = "COMPLETED"
                elif result.get("requires_reconciliation") or "ambiguous" in str(result.get("reason", "")):
                    status = "RECONCILIATION_REQUIRED"
                elif result.get("reason") == "market_order_rejected":
                    status = "REJECTED"
                else:
                    status = "SUBMITTED" if result.get("entry_submitted") else "REJECTED"
                self.state_store.update_order_attempt(client_order_id, status)
                self.state_store.record_execution_result(bool(result.get("ok")))
            except Exception as exc:
                # Once an order attempt is reserved, an adapter or persistence
                # exception is not proof that no exchange-side action occurred.
                # Preserve the durable entry lock and make the attempt explicitly
                # require reconciliation; never permit an automatic replay.
                try:
                    self.state_store.update_order_attempt(
                        client_order_id, "RECONCILIATION_REQUIRED"
                    )
                except Exception:
                    pass
                try:
                    self.state_store.record_execution_result(False)
                except Exception:
                    pass
                return {
                    "ok": False,
                    "reason": "state_persistence_failed_requires_reconciliation",
                    "error_type": type(exc).__name__,
                    "entry_submitted": "unknown",
                    "requires_reconciliation": True,
                }
            release_lock = not (
                result.get("requires_reconciliation")
                or "ambiguous" in str(result.get("reason", ""))
            )
            return result
        finally:
            if release_lock:
                self.state_store.release_entry_lock(client_order_id)
