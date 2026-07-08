"""
SignalCopyOrchestrator: wires the full pipeline.

    listener(s) ──► handle_incoming_text()
                       │  parse_signal()
                       │  dedup
                       │  get_advanced_metrics()
                       │  validate_signal()
                       ├─ REJECT/WEAK ──► notify (no execution)
                       └─ VALID ──► register confirmation ──► confirm bot prompt
                                          │ user taps Ya
                                          ▼
                                   SignalExecutor.execute()

Dependencies are injected so this can run standalone (run_signal_copy.py) or be
embedded inside the main fusion bot. Anything missing degrades gracefully:
- no metrics provider  -> validation runs on empty metrics (will REJECT safely)
- no confirm bot       -> falls back to auto-execute only if explicitly enabled
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, Tuple

from utils.logger import logger

from .signal_parser import parse_signal
from .signal_schema import SignalSource
from .validation_engine import validate_signal, Verdict
from .confirmation import ConfirmationManager, ConfirmState
from .executor import SignalExecutor
from .report_formatter import (
    build_validation_report,
    build_execution_result,
)
from . import signal_copy_config as scfg
from .channel_performance import get_tracker
from .conviction_sizer import ConvictionSizer
# from .vip_reporter import VIPSessionReporter  # TODO: wire VIP feature later

def _f(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


class SignalCopyOrchestrator:
    def __init__(
        self,
        *,
        metrics_provider: Any = None,     # has async get_advanced_metrics(symbol)
        trader: Any = None,               # has async submit_open(**kwargs)
        risk_mgr: Any = None,             # RiskManager
        confirm_bot: Any = None,          # TelegramConfirmBot (optional)
        notifier: Any = None,             # async callable(text) for plain notices
        risk_pct: float = None,
        dry_run: bool = None,
        auto_execute: bool = None,
    ):
        self.metrics_provider = metrics_provider
        self.trader = trader
        self.risk_mgr = risk_mgr
        self.confirm_bot = confirm_bot
        self.notifier = notifier

        self.risk_pct = scfg.RISK_PCT if risk_pct is None else risk_pct
        self.dry_run = scfg.DRY_RUN if dry_run is None else dry_run
        self.auto_execute = scfg.AUTO_EXECUTE_WITHOUT_CONFIRM if auto_execute is None else auto_execute
        # Learning mode: no channel allowlist configured -> read everything and
        # surface each source's chat id so the user can pick channels one by one.
        self.learning_mode = not bool(scfg.TG_SIGNAL_CHANNELS)
        self._known_signal_sources: Dict[int, str] = {}

        self.confirmations = ConfirmationManager(default_expiry_sec=scfg.CONFIRM_EXPIRY_SEC)
        self.executor = (
            SignalExecutor(trader, risk_mgr, risk_pct=self.risk_pct)
            if (trader is not None and risk_mgr is not None) else None
        )
        self._recent: Dict[str, float] = {}   # dedup key -> ts
        self._sweeper_task: Optional[asyncio.Task] = None
        # limit setups waiting for price to reach the entry: token -> {result, created}
        self._pending_limits: Dict[str, dict] = {}
        # chat ids whose messages must never be parsed as signals (e.g. our own
        # confirm bot, to prevent its prompts from re-triggering the pipeline).
        self.ignore_chat_ids: set = set()
        # whale-accumulation narratives captured for the (upcoming) accumulation
        # pipeline. Tahap 1 only records + (optionally) surfaces them.
        self._accum_log: list = []
        # Discord channels used as a READ-ONLY calibration intake (report only).
        self.calib_channels: set = set(getattr(scfg, "CALIB_DISCORD_CHANNELS", []) or [])
        self.sizer = ConvictionSizer()
        # self.vip_reporter = VIPSessionReporter(self._notify, accum_log=self._accum_log)  # TODO: wire VIP later
        self.vip_reporter = None  # Stub for now
        self.ch_perf = get_tracker()
        self.sizer = ConvictionSizer()
        self._reporter_task = None

    async def _leaderboard_loop(self):
        while True:
            try:
                from .vip_reporter import get_minutes_to_next_anchor
                anchors = get_minutes_to_next_anchor()
                if anchors.get('DAILY_CLOSE', 999) <= 5:
                    rep = self.ch_perf.get_report()
                    await self._notify(f"🏆 <b>DAILY PERFORMANCE SUMMARY</b>\n\n{rep}")
                    await asyncio.sleep(600)
                await asyncio.sleep(60)
            except Exception:
                await asyncio.sleep(60)

    # ---------- notification helper ----------
    async def _notify(self, text: str) -> None:
        if self.notifier is None:
            logger.info("[SIGNAL_COPY][notify] %s", text.replace("\n", " | ")[:300])
            return
        try:
            res = self.notifier(text)
            if asyncio.iscoroutine(res):
                await res
        except Exception as exc:
            logger.warning("[SIGNAL_COPY] notify failed: %s", exc)

    async def start_background_tasks(self):
        if self._reporter_task is None:
            self._reporter_task = asyncio.create_task(self.vip_reporter.loop())
            logger.info("[SIGNAL_COPY] Background reporter task started.")
        asyncio.create_task(self._leaderboard_loop())

    # ---------- accumulation route (Tahap 1 stub) ----------
    async def _handle_accumulation(self, text: str, source_name: str,
                                   source_chat_id: Optional[int], cls) -> None:
        """Route for whale-accumulation narratives.

        Tahap 1: record + (optionally) surface for visibility. It never trades —
        the real accumulation pipeline (find coins accumulated by whales, watch
        gradual accumulation, enter on confirmation) lands in Tahap 3.
        """
        from .parse_report import build_read_report
        logger.info("[ACCUM] %s", build_read_report(text, cls, None))
        try:
            self._accum_log.append({
                "text": (text or "")[:500],
                "source": source_name,
                "chat_id": source_chat_id,
                "reasons": list(cls.reasons),
                "ts": time.time(),
            })
            self._accum_log[:] = self._accum_log[-200:]
        except Exception:
            pass
        if getattr(scfg, "NOTIFY_ACCUM", False):
            preview = " ".join((text or "").split())[:200]
            await self._notify(
                f"📥 <b>Akumulasi terdeteksi</b> ({source_name or '-'})\n"
                f"<i>{preview}</i>\n"
                f"— belum dieksekusi (pipeline akumulasi menyusul)."
            )

    def _merge_vision(self, sig, v: dict) -> list:
        """Fill ONLY missing signal fields from chart-vision data (never override
        values the channel stated explicitly). Returns list of filled keys."""
        filled: list = []
        entry = sig.entry_mid or 0.0

        def _plausible(x) -> bool:
            try:
                x = float(x)
            except (TypeError, ValueError):
                return False
            if x <= 0:
                return False
            if entry <= 0:
                return True
            return entry * 0.1 <= x <= entry * 10.0

        sl = v.get("stop_loss")
        if sig.stop_loss is None and _plausible(sl):
            sig.stop_loss = float(sl)
            sig.sl_source = "vision"
            filled.append("sl")

        if not sig.take_profits:
            tps = [float(t) for t in (v.get("take_profits") or []) if _plausible(t)]
            if entry > 0:
                tps = [t for t in tps if (t > entry if sig.is_long else t < entry)]
            if tps:
                sig.take_profits = sorted(set(tps), reverse=not sig.is_long)
                sig.tp_source = "vision"
                filled.append("tp")

        if not getattr(sig, "timeframe", None) and v.get("timeframe"):
            sig.timeframe = str(v["timeframe"])
            filled.append("tf")

        return filled

    async def read_only_report(self, text: str, source_name: str = "",
                               image: Optional[bytes] = None,
                               source: SignalSource = SignalSource.TELEGRAM) -> str:
        """Analyze a message WITHOUT executing: classify + parse + (optional)
        chart-vision read. Returns an HTML report. Used by the calibration bot
        and calibration Discord channels."""
        from .classifier import classify_message
        from .parse_report import build_read_report_html
        cls = classify_message(text or "")
        sig = parse_signal(text or "", source=source, source_name=source_name)
        vinfo = ""
        if image and getattr(scfg, "VISION_ENABLED", False):
            try:
                from .vision import analyze_chart
                vdata = await analyze_chart(image, symbol=(sig.symbol if sig else ""),
                                            raw_text=text or "")
                if vdata:
                    if sig is not None:
                        filled = self._merge_vision(sig, vdata)
                        vinfo += ("\n🖼️ Vision merge: "
                                  + (", ".join(filled) if filled else "(tak ada field kosong)"))
                    vinfo += (f"\n🖼️ Chart: pair={vdata.get('pair')} tf={vdata.get('timeframe')} "
                              f"side={vdata.get('side')} "
                              f"entry={vdata.get('entry')} sl={vdata.get('stop_loss')} "
                              f"tp={vdata.get('take_profits')} conf={(vdata.get('confidence') or 0):.0%}")
                else:
                    vinfo = "\n🖼️ Vision: tidak ada hasil"
            except Exception as exc:
                vinfo = f"\n🖼️ Vision gagal: {exc}"
        elif image:
            vinfo = "\n🖼️ (gambar diterima; VISION OFF)"
        return build_read_report_html(text or "", cls, sig) + vinfo

    # ---------- dedup ----------
    def _dedup_key(self, sig) -> str:
        return f"{sig.symbol}:{sig.side.value}:{round(sig.entry_mid, 6)}"

    def _is_duplicate(self, sig) -> bool:
        now = time.time()
        key = self._dedup_key(sig)
        # purge old
        for k in [k for k, ts in self._recent.items() if now - ts > scfg.DEDUP_WINDOW_SEC]:
            self._recent.pop(k, None)
        if key in self._recent and now - self._recent[key] < scfg.DEDUP_WINDOW_SEC:
            return True
        self._recent[key] = now
        return False

    # ---------- metrics ----------
    async def _fetch_metrics(self, symbol: str) -> Dict[str, Any]:
        if self.metrics_provider is None:
            return {}
        try:
            m = await self.metrics_provider.get_advanced_metrics(symbol)
            return m or {}
        except Exception as exc:
            logger.warning("[SIGNAL_COPY] metrics fetch failed for %s: %s", symbol, exc)
            return {}

    # ---------- main entry from listeners ----------
    async def handle_incoming_text(
        self,
        text: str,
        source_name: str = "",
        source_chat_id: Optional[int] = None,
        source: SignalSource = SignalSource.TELEGRAM,
        image: Optional[bytes] = None,
    ) -> None:
        # Never parse messages that originate from our own bot/components.
        if source_chat_id is not None and source_chat_id in self.ignore_chat_ids:
            return

        # Calibration channels: read-only — report what we understood, no trade.
        if source_chat_id is not None and source_chat_id in self.calib_channels:
            try:
                rep = await self.read_only_report(text, source_name, image, source)
                await self._notify("🧪 <b>KALIBRASI</b>\n" + rep)
            except Exception as exc:
                logger.warning("[SIGNAL_COPY] calib report failed: %s", exc)
            return

        # --- classify first (routing label + detailed read report) ---
        from .classifier import classify_message, MessageType
        from .parse_report import build_read_report, build_read_report_html
        cls = classify_message(text)

        sig = parse_signal(text, source=source, source_name=source_name, source_chat_id=source_chat_id)
        if sig is None:
            # Not a structured trade call — route by classification.
            if cls.type == MessageType.WHALE_ACCUM:
                await self._handle_accumulation(text, source_name, source_chat_id, cls)
            else:
                logger.info("[SIGNAL_COPY] %s", build_read_report(text, cls, None))
            return  # nothing to execute from a non-signal message

        # Structured trade call: emit a detailed read report (always logged;
        # optionally pushed to Telegram for calibration via SIGNAL_COPY_PARSE_REPORT).
        logger.info("[SIGNAL_COPY] %s", build_read_report(text, cls, sig))
        if self._is_duplicate(sig):
            logger.info("[SIGNAL_COPY] duplicate signal ignored: %s", sig.summary())
            return

        logger.info("[SIGNAL_COPY] parsed: %s", sig.summary())

        # Learning mode: remember + surface the source channel id so the user can
        # add it to the allowlist. Notify once per new source.
        learning_banner = ""
        if self.learning_mode:
            cid = source_chat_id if source_chat_id is not None else 0
            if cid and cid not in self._known_signal_sources:
                self._known_signal_sources[cid] = source_name
                await self._notify(
                    f"🆕 <b>Channel sinyal baru terdeteksi</b>\n"
                    f"Nama: <b>{source_name or '-'}</b>\n"
                    f"ID: <code>{cid}</code>\n"
                    f"Tambahkan ke <code>SIGNAL_COPY_TG_CHANNELS</code> di .env "
                    f"jika ingin memfilter hanya channel ini."
                )
            if cid:
                learning_banner = (f"📡 Channel: {source_name or '-'} "
                                   f"(ID <code>{cid}</code>)\n")

        metrics = await self._fetch_metrics(sig.symbol)
        # --- Multi-timeframe alignment: check 4h/daily trend structure ---
        try:
            from .mtf_aligner import MTFAligner
            mtf = MTFAligner()
            mtf_data = await mtf.analyze(sig.symbol)
            if mtf_data:
                mtf_score = await mtf.get_alignment_score(sig.symbol, sig.side.value)
                metrics["mtf_alignment"] = {
                    "score": mtf_score,
                    "entry_trend": mtf_data.get("entry_tf", {}).get("trend", "FLAT"),
                    "tf4h_trend": mtf_data.get("tf_4h", {}).get("trend", "FLAT"),
                    "d1_trend": mtf_data.get("tf_daily", {}).get("trend", "FLAT"),
                }
                logger.info("[MTF] %s side=%s mtf_score=%.1f 4h=%s d1=%s",
                           sig.symbol, sig.side.value, mtf_score,
                           metrics["mtf_alignment"]["tf4h_trend"],
                           metrics["mtf_alignment"]["d1_trend"])
        except Exception as exc:
            logger.debug("[MTF] analysis failed for %s: %s", sig.symbol, exc)


        # --- TradingView real-time indicator confluence ---
        try:
            from .tradingview_factor import TradingViewFactor
            tv = TradingViewFactor()
            tv_data = await tv.fetch(sig.symbol)
            if tv_data:
                tv_score = tv.compute_confluence(tv_data, sig.side.value)
                metrics["tradingview"] = tv_score
                logger.info("[TV] %s side=%s tv_score=%.1f",
                           sig.symbol, sig.side.value, tv_score["score"])
        except Exception as exc:
            logger.debug("[TV] fetch failed for %s: %s", sig.symbol, exc)

        # --- Tahap 2.5: read the chart image (vision) for EVERY image-bearing
        # signal: (a) fill missing TP/SL/timeframe, and (b) feed a chart
        # confluence factor into validation (agreement strengthens the score). ---
        if image and getattr(scfg, "VISION_ENABLED", False):
            try:
                from .vision import analyze_chart
                vdata = await analyze_chart(image, symbol=sig.symbol, raw_text=text)
                if vdata:
                    metrics["chart_vision"] = vdata
                    filled = self._merge_vision(sig, vdata)
                    if filled:
                        logger.info("[SIGNAL_COPY] vision filled %s -> %s", filled, sig.summary())
            except Exception as exc:
                logger.warning("[SIGNAL_COPY] vision enrich failed: %s", exc)

        # If the signal states a timeframe, recompute RSI/ATR/trend on that TF.
        if getattr(sig, "timeframe", None):
            try:
                from .timeframe import apply_timeframe_metrics
                await apply_timeframe_metrics(sig, metrics)
            except Exception as exc:
                logger.warning("[SIGNAL_COPY] timeframe metrics failed: %s", exc)
        # Make the signal executable even if the channel omitted TP/SL:
        # improvise SL from ATR and TP ladder as 1R/2R/3R from the stop.
        try:
            from .normalizer import normalize_signal
            normalize_signal(sig, metrics)
        except Exception as exc:
            logger.warning("[SIGNAL_COPY] normalize failed: %s", exc)
        result = validate_signal(sig, metrics)
        logger.info("[SIGNAL_COPY] %s -> %s score=%.1f (tp=%s sl=%s entry=%s tf=%s)",
                    sig.symbol, result.verdict.value, result.score,
                    sig.tp_source, sig.sl_source, sig.entry_type, sig.timeframe or "-")
        
        # --- ADVERSARIAL CHECK (only for VALID signals) ---
        if result.verdict == Verdict.VALID and getattr(scfg, "ADVERSARIAL_ENABLED", True):
            try:
                import sys
                from pathlib import Path
                # Import nexus adversarial module
                nexus_root = Path(__file__).parent.parent
                sys.path.insert(0, str(nexus_root))
                from fusionnew.clean_core.adversarial import bull_bear_check
                
                logger.info("[ADVERSARIAL] Running bull/bear debate for %s %s", sig.symbol, sig.side.value)
                
                # Build adversarial context from signal + metrics
                adv_context = {
                    "symbol": sig.symbol,
                    "side": sig.side.value,
                    "entry": sig.entry_mid,
                    "stop_loss": sig.stop_loss,
                    "take_profits": sig.take_profits,
                    "price": metrics.get("price", 0),
                    "rsi": metrics.get("rsi", 50),
                    "regime": metrics.get("regime_label", "UNKNOWN"),
                    "cvd_zscore": metrics.get("cvd_zscore", 0),
                    "validation_score": result.score,
                }
                
                # Run debate (3 LLM calls: bull, bear, judge)
                approved, judge_verdict = await asyncio.to_thread(
                    bull_bear_check, sig.symbol, adv_context
                )
                
                if not approved:
                    logger.warning("[ADVERSARIAL] REJECTED by judge: %s", judge_verdict[:200])
                    result.verdict = Verdict.REJECT
                    result.hard_blocks.append(f"Adversarial: {judge_verdict[:100]}")
                    await self._notify(f"🚫 Adversarial REJECT: {sig.symbol}\n{judge_verdict[:300]}")
                    return
                else:
                    logger.info("[ADVERSARIAL] APPROVED: %s", judge_verdict[:200])
                    
            except Exception as exc:
                logger.warning("[ADVERSARIAL] Check failed (proceeding anyway): %s", exc)
        
        # Combined read + validation report
        # Import now (shared across all branches below)
        from .report_formatter import build_validation_report
        
        if getattr(scfg, "PARSE_REPORT", False):
            from .parse_report import build_read_report_html
            combined = build_read_report_html(text, cls, sig) + "\n\n" + build_validation_report(result)
            try:
                await self._notify(combined)
            except Exception:
                pass
        # --- Stop processing for REJECT / WEAK ---
        if result.verdict == Verdict.REJECT or result.verdict == Verdict.WEAK:
            return

        if self.executor is None:
            await self._notify(build_validation_report(result) +
                               "\n\n⚠️ Executor belum terkonfigurasi (mode notifikasi saja).")
            return

        if self.auto_execute:
            await self.confirmations.register(result)
            await self.confirmations.resolve(result.signal.signal_id, approved=True)
            status = await self._execute_token(result.signal.signal_id)
            await self._notify(build_validation_report(result) + "\n\n" + status)
            return

        # Normal path: register + ask user to confirm via the bot
        await self.confirmations.register(result)
        if self.confirm_bot is not None:
            sent = await self.confirm_bot.prompt(result)
            if not sent:
                await self._notify(build_validation_report(result) +
                                   "\n\n⚠️ Gagal mengirim tombol konfirmasi.")
        else:
            await self._notify(build_validation_report(result) +
                               "\n\n⚠️ Confirm bot tidak aktif; tidak bisa minta konfirmasi.")

    # ---------- execution after confirmation ----------
    async def _execute_token(self, token: str) -> str:
        pc = await self.confirmations.get(token)
        if pc is None:
            return "Sinyal tidak ditemukan."
        if pc.state == ConfirmState.EXPIRED:
            return "⌛ Sinyal kedaluwarsa."
        if pc.state not in (ConfirmState.APPROVED,):
            return f"Status sinyal: {pc.state.value}"
        if self.executor is None:
            return "Executor tidak tersedia."

        # Limit setup: if price hasn't reached the entry yet, wait for it
        # (avoid chasing) — execute automatically when the price touches the limit.
        sig = pc.result.signal
        price = _f(pc.result.metrics_snapshot.get("price"))
        entry = sig.entry_mid
        if self._wait_for_limit(sig, price):
            if True:
                self._pending_limits[token] = {"result": pc.result, "created": time.time()}
                await self.confirmations.mark(token, ConfirmState.APPROVED, note="pending_limit")
                return (f"⏳ Limit setup {sig.symbol} {sig.side.value} @ {entry:g}. "
                        f"Harga sekarang {price:g} — menunggu harga menyentuh limit. "
                        f"Akan dieksekusi otomatis saat tercapai (batas 24 jam).")

        outcome = await self.executor.execute(pc.result, dry_run=self.dry_run, risk_pct=self.sizer.calc(pc.result.signal, pc.result.metrics_snapshot or {}))
        await self.confirmations.mark(
            token,
            ConfirmState.EXECUTED if outcome.ok else ConfirmState.FAILED,
            note=outcome.reason,
        )
        if outcome.ok and outcome.notional > 0:
            try:
                from notifications.telegram_notifier import send_open_trade
                await send_open_trade({
                    "symbol": outcome.symbol,
                    "side": outcome.side,
                    "entry_price": outcome.entry_price,
                    "notional": outcome.notional,
                    "sl": outcome.sl_price,
                    "tp1": outcome.tp1,
                    "tp_full": outcome.tp_full,
                    "risk_amount": outcome.risk_amount,
                    "score": pc.result.score,
                    "regime": "SIGNAL_COPY",
                    "signal_id": pc.result.signal.signal_id,
                })
            except Exception as exc:
                logger.warning("[SIGNAL_COPY] open notify failed: %s", exc)
        return build_execution_result(
            outcome.symbol, outcome.side, outcome.ok, outcome.reason,
            entry=outcome.entry_price, notional=outcome.notional,
            sl=outcome.sl_price, tp1=outcome.tp1, tp_full=outcome.tp_full,
            risk_amount=outcome.risk_amount,
        )

    def _wait_for_limit(self, sig, price: float) -> bool:
        """Decide whether to hold the entry as a pending limit instead of
        chasing at market. Returns True when:
          - the signal is an explicit limit entry not yet reached, OR
          - price has already run past the entry in the profit direction far
            enough that a market fill would inflate the SL distance (and thus
            real risk) beyond the safe cap. In that case we wait for a pullback
            to the signal entry so risk stays ~1%.
        """
        entry = getattr(sig, "entry_mid", 0.0) or 0.0
        if price <= 0 or entry <= 0:
            return False
        if getattr(sig, "entry_type", "market") == "limit":
            return not self._limit_reached(sig, price)
        # Market-typed signal: only wait if price moved in the PROFIT direction
        # past the entry zone (chasing a long higher / a short lower).
        hi = getattr(sig, "entry_high", entry) or entry
        lo = getattr(sig, "entry_low", entry) or entry
        if sig.is_long and price <= hi:
            return False
        if (not sig.is_long) and price >= lo:
            return False
        sl = getattr(sig, "stop_loss", None)
        if sl and sl > 0:
            intended = abs(entry - sl) / entry
            now_dist = abs(price - sl) / price
            return now_dist > max(0.08, intended * 1.3)
        ref = hi if sig.is_long else lo
        return abs(price - ref) / ref > 0.015

    @staticmethod
    def _limit_reached(sig, price: float) -> bool:
        """True when price has reached a limit entry (LONG: dropped to entry; SHORT: risen to entry)."""
        entry = sig.entry_mid
        if entry <= 0 or price <= 0:
            return True
        # small tolerance so a near-touch fills
        tol = entry * 0.0015
        if sig.is_long:
            return price <= entry + tol
        return price >= entry - tol

    async def _check_pending_limits(self) -> None:
        """Execute pending limit setups when price reaches the entry; expire after 24h."""
        if not self._pending_limits:
            return
        now = time.time()
        for token, pl in list(self._pending_limits.items()):
            result = pl["result"]
            sig = result.signal
            if now - pl["created"] > 86400:  # 24h TTL
                self._pending_limits.pop(token, None)
                await self.confirmations.mark(token, ConfirmState.EXPIRED, note="limit_unfilled_24h")
                await self._notify(f"⌛ Limit {sig.symbol} {sig.side.value} @ {sig.entry_mid:g} "
                                   f"tidak tersentuh dalam 24 jam — dibatalkan.")
                continue
            metrics = await self._fetch_metrics(sig.symbol)
            price = _f(metrics.get("price"))
            if price <= 0 or not self._limit_reached(sig, price):
                continue
            # refresh price snapshot so the executor enters near the limit
            result.metrics_snapshot["price"] = price
            self._pending_limits.pop(token, None)
            if self.executor is None:
                continue
            outcome = await self.executor.execute(result, dry_run=self.dry_run, risk_pct=self.sizer.calc(result.signal, result.metrics_snapshot or {}))
            await self.confirmations.mark(
                token,
                ConfirmState.EXECUTED if outcome.ok else ConfirmState.FAILED,
                note=outcome.reason,
            )
            if outcome.ok and outcome.notional > 0:
                try:
                    from notifications.telegram_notifier import send_open_trade
                    await send_open_trade({
                        "symbol": outcome.symbol,
                        "side": outcome.side,
                        "entry_price": outcome.entry_price,
                        "notional": outcome.notional,
                        "sl": outcome.sl_price,
                        "tp1": outcome.tp1,
                        "tp_full": outcome.tp_full,
                        "risk_amount": outcome.risk_amount,
                        "score": result.signal.score,
                        "regime": "SIGNAL_COPY",
                        "signal_id": result.signal.signal_id,
                    })
                except Exception as exc:
                    logger.warning("[SIGNAL_COPY] open notify failed: %s", exc)
                await self._notify(
                    f"🎯 Limit tersentuh — " + build_execution_result(
                        outcome.symbol, outcome.side, outcome.ok, outcome.reason,
                        entry=outcome.entry_price, notional=outcome.notional,
                        sl=outcome.sl_price, tp1=outcome.tp1, tp_full=outcome.tp_full,
                        risk_amount=outcome.risk_amount,
                    )
                )

    async def on_user_decision(self, token: str, approved: bool) -> str:
        """Callback for the confirm bot. Decision already recorded; execute if yes."""
        if not approved:
            return "❌ Sinyal ditolak. Tidak ada posisi dibuka."
        return await self._execute_token(token)

    # ---------- self-test + status (for bot commands) ----------
    SAMPLE_SIGNAL = (
        "🚨 TEST SIGNAL 🚨\n"
        "Pair: ZEC/USDT\n"
        "Position: LONG\n"
        "Leverage: 10X\n"
        "Entry: 358 - 350\n"
        "TP1 365\nTP2 415\n"
        "Stop Loss: 339"
    )

    async def inject_test_signal(self, text: Optional[str] = None) -> str:
        """Run a sample (or provided) signal through the full pipeline. Used by /test.

        When no text is given, build a realistic signal around the CURRENT ZEC
        price so the test demonstrates a true VALID flow (chart + buttons +
        executable) instead of a stale, off-zone call.
        """
        self._recent.clear()  # ensure the test isn't suppressed by dedup
        payload = text
        if payload is None:
            price = 0.0
            try:
                if self.metrics_provider is not None:
                    m = await self.metrics_provider.get_advanced_metrics("ZECUSDT")
                    price = float((m or {}).get("price") or 0.0)
            except Exception:
                price = 0.0
            if price > 0:
                lo = round(price * 0.997, 2)
                hi = round(price * 1.003, 2)
                sl = round(price * 0.975, 2)
                tp1 = round(price * 1.010, 2)
                tp2 = round(price * 1.020, 2)
                tp3 = round(price * 1.030, 2)
                payload = (
                    "🚨 TEST SIGNAL 🚨\n"
                    "Pair: ZEC/USDT\n"
                    "Position: LONG\n"
                    "Leverage: 10X\n"
                    f"Entry: {lo} - {hi}\n"
                    f"TP1 {tp1}\nTP2 {tp2}\nTP3 {tp3}\n"
                    f"Stop Loss: {sl}"
                )
            else:
                payload = self.SAMPLE_SIGNAL
        await self.handle_incoming_text(payload, source_name="SELF_TEST", source_chat_id=None,
                                        source=SignalSource.MANUAL)
        return "🧪 Tes sinyal diproses. Jika VALID, prompt konfirmasi akan muncul di sini."

    def status_text(self) -> str:
        lines = ["📊 <b>Status Signal-Copy</b>"]
        lines.append(f"• Risk/trade: {self.risk_pct*100:.2f}%")
        lines.append(f"• Dry-run: {'ya' if self.dry_run else 'tidak'}")
        lines.append(f"• Mode: {'belajar (semua channel)' if self.learning_mode else 'allowlist'}")
        try:
            from .confirmation import ConfirmState
            n_pending = len([1 for pc in self.confirmations._pending.values()  # noqa
                             if pc.state == ConfirmState.PENDING])
        except Exception:
            n_pending = 0
        lines.append(f"• Konfirmasi tertunda: {n_pending}")
        try:
            positions = getattr(self.trader, "positions", {}) or {}
            lines.append(f"• Posisi terbuka: {len(positions)}")
            for sym, p in list(positions.items())[:8]:
                lines.append(f"   - {sym} {p.get('side','')} qty={p.get('qty')}")
        except Exception:
            pass
        try:
            eq = self.risk_mgr.get_current_equity() if self.risk_mgr else None
            if eq is not None:
                lines.append(f"• Equity: ${eq:.2f}")
        except Exception:
            pass
        if self._known_signal_sources:
            lines.append(f"• Channel pengirim sinyal terdeteksi: {len(self._known_signal_sources)}")
        return "\n".join(lines)

    # ---------- background maintenance ----------
    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(30)
                await self.confirmations.sweep_expired()
                await self.confirmations.purge_finished()
                await self._check_pending_limits()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[SIGNAL_COPY] sweep error: %s", exc)

    def start_background(self) -> None:
        if self._sweeper_task is None or self._sweeper_task.done():
            self._sweeper_task = asyncio.create_task(self._sweep_loop(), name="signal_copy_sweeper")
        if self._reporter_task is None or self._reporter_task.done():
            self._reporter_task = asyncio.create_task(self.vip_reporter.loop(), name="signal_copy_reporter")

    async def stop(self) -> None:
        if self._sweeper_task:
            self._sweeper_task.cancel()
            try:
                await self._sweeper_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._reporter_task:
            self._reporter_task.cancel()
            try:
                await self._reporter_task
            except (asyncio.CancelledError, Exception):
                pass
