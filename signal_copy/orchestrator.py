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
from .signal_schema import SignalSource, ParsedSignal, SignalSide
from .validation_engine import validate_signal, Verdict
from .confirmation import ConfirmationManager, ConfirmState
from .executor import SignalExecutor, ExecutionOutcome
from .report_formatter import (
    build_validation_report,
    build_execution_result,
)
from .telegram_formatter import (
    build_parser_report,
    build_execution_message,
    build_close_message,
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
        # channels that get parsed + notified but NEVER auto-executed
        # (calibration / forward-test mode)
        self.calib_channels: set = set()
        # whale-accumulation narratives captured for the (upcoming) accumulation
        # pipeline. Tahap 1 only records + (optionally) surfaces them.
        self._accum_log: list = []
        # Discord channels used as a READ-ONLY calibration intake (report only).
        self.calib_channels: set = set(getattr(scfg, "CALIB_DISCORD_CHANNELS", []) or [])
        self.sizer = ConvictionSizer()
        # self.vip_reporter = VIPSessionReporter(self._notify, accum_log=self._accum_log)  # TODO: wire VIP later
        self.vip_reporter = None  # Stub for now
        self.ch_perf = get_tracker()
        self._reporter_task = None
        
        # --- Dual Telegram Channels Notification Transport ---
        from .telegram_transport import (
            send_parser_notification,
            send_trades_notification,
        )
        
        self._send_parser = send_parser_notification
        self._send_trades = send_trades_notification
        self._signals_chat_id = getattr(scfg, "SIGNALS_CHAT_ID", 0)
        self._trades_chat_id = getattr(scfg, "TRADES_CHAT_ID", 0)
        
        # Post-init wiring for confirm bot (needs internal objects)
        if confirm_bot is not None:
            # Inject missing deps into TelegramConfirmBot
            if hasattr(confirm_bot, "confirmations") and confirm_bot.confirmations is None:
                confirm_bot.confirmations = self.confirmations
            if hasattr(confirm_bot, "on_decision") and confirm_bot.on_decision is None:
                confirm_bot.on_decision = self._execute_token

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

    # --- Dual channel notification methods using new transports ---
    async def _notify_signals_channel(self, text: str) -> bool:
        """Send signal validation report to the signals channel (all verdicts)."""
        from .telegram_transport import send_parser_notification
        return await send_parser_notification(text)

    async def _notify_trades_channel(self, text: str) -> bool:
        """Send execution result to the trades channel (executed only)."""
        from .telegram_transport import send_trades_notification
        return await send_trades_notification(text)

    async def start_background_tasks(self):
        if self._reporter_task is None and self.vip_reporter is not None:
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

    async def _signal_from_vision(self, image, *, text: str = "",
                                  source: SignalSource = SignalSource.TELEGRAM,
                                  source_name: str = "",
                                  source_chat_id: Optional[int] = None):
        """Build a ParsedSignal purely from a chart image when there is no
        parseable text. Needs at least a pair + side; entry falls back to the
        chart entry, else 0.0 (market) so the normalizer can fill from live price.
        Returns None if vision can't extract an actionable pair+side."""
        try:
            from .vision import analyze_chart
            v = await analyze_chart(image, symbol="", raw_text=text or "")
        except Exception as exc:
            logger.warning("[SIGNAL_COPY] vision-only analyze failed: %s", exc)
            return None
        if not v:
            return None
        pair = (v.get("pair") or "").upper().strip()
        side_raw = (v.get("side") or "").upper().strip()
        if not pair or side_raw not in ("LONG", "SHORT"):
            return None
        from .signal_parser import _normalize_symbol
        symbol = _normalize_symbol(pair, None)
        if not symbol:
            return None

        def _num(x):
            try:
                x = float(x)
                return x if x > 0 else None
            except (TypeError, ValueError):
                return None

        entry = _num(v.get("entry")) or 0.0
        sl = _num(v.get("stop_loss"))
        tps = [t for t in (_num(x) for x in (v.get("take_profits") or [])) if t]
        sig = ParsedSignal(
            symbol=symbol,
            side=SignalSide(side_raw),
            entry_low=entry,
            entry_high=entry,
            stop_loss=sl,
            take_profits=tps,
            timeframe=(str(v["timeframe"]) if v.get("timeframe") else None),
            source=source,
            source_name=source_name,
            source_chat_id=source_chat_id,
            raw_text=(text or "")[:2000],
            tp_source="vision" if tps else "signal",
            sl_source="vision" if sl else "signal",
        )
        return sig

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

        # Calibration channels: parse + validate + notify, but NEVER auto-execute.
        # Falls through to normal pipeline; execution is blocked later by
        # checking calib_channels before _execute_token.
        calib = source_chat_id is not None and source_chat_id in self.calib_channels

        # --- classify first (routing label + detailed read report) ---
        from .classifier import classify_message, MessageType
        from .parse_report import build_read_report, build_read_report_html
        cls = classify_message(text)

        sig = parse_signal(text, source=source, source_name=source_name, source_chat_id=source_chat_id)
        if sig is None and image and getattr(scfg, "VISION_ENABLED", False):
            # Chart-only / image-only signal: no parseable text, so read the
            # chart with vision and build the signal from what it extracts.
            # BUT skip the vision path entirely when the caption is
            # marketing/promo spam (guaranteed profits, join-now, invite links).
            from .classifier import looks_like_promo
            if not looks_like_promo(text or ""):
                sig = await self._signal_from_vision(
                    image, text=text, source=source,
                    source_name=source_name, source_chat_id=source_chat_id,
                )
                if sig is not None:
                    logger.info("[SIGNAL_COPY] vision-only signal built: %s", sig.summary())
            else:
                logger.info(
                    "[SIGNAL_COPY] promo/recruitment caption — vision path blocked: %s",
                    (text or "").replace("\n", "  ")[:120],
                )
        if sig is None:
            # Not a structured trade call — route by classification.
            if cls.type == MessageType.WHALE_ACCUM:
                await self._handle_accumulation(text, source_name, source_chat_id, cls)
            elif calib and image:
                # Calibration forward we couldn't read: still reply so the user
                # knows it was received (avoids silent no-response).
                await self._notify(
                    "🖼️ Chart diterima di channel kalibrasi tapi tidak bisa dibaca "
                    "(vision tidak mengembalikan pair/side/entry). Coba kirim chart "
                    "dengan pair & level yang jelas."
                )
            else:
                logger.info("[SIGNAL_COPY] %s", build_read_report(text, cls, None))
            return  # nothing to execute from a non-signal message

        # Structured trade call: emit a detailed read report (always logged;
        # optionally pushed to Telegram for calibration via SIGNAL_COPY_PARSE_REPORT).
        logger.info("[SIGNAL_COPY] %s", build_read_report(text, cls, sig))
        if not calib and self._is_duplicate(sig):
            logger.info("[SIGNAL_COPY] duplicate signal ignored: %s", sig.summary())
            return

        logger.info("[SIGNAL_COPY] parsed: %s", sig.summary())

        # (parse summary collected via build_parser_report after validation)

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

        if not getattr(sig, "timeframe", None):
            sig.timeframe = "15m"
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
            if tv_data and tv_data.get("_ta"):
                tv_score = tv.compute_confluence(tv_data, sig.side.value)
                metrics["tradingview"] = tv_score
                logger.info("[TV] %s side=%s tv_score=%.1f",
                           sig.symbol, sig.side.value, tv_score.get("score", 0))
            else:
                logger.warning("[TV] no data returned for %s (rate limited?)", sig.symbol)
        except Exception as exc:
            logger.warning("[TV] fetch failed for %s: %s", sig.symbol, exc)

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

        # --- Build ONE consolidated report and send ---
        from .telegram_formatter import build_parser_report
        _adv_verdict = ""
        chart_path = None

        # --- ADVERSARIAL CHECK (only for VALID signals) ---
        if result.verdict == Verdict.VALID and getattr(scfg, "ADVERSARIAL_ENABLED", True):
            try:
                import sys
                from pathlib import Path
                nexus_root = Path(__file__).parent.parent
                sys.path.insert(0, str(nexus_root))
                from fusionnew.clean_core.adversarial import bull_bear_check
                
                logger.info("[ADVERSARIAL] Running bull/bear debate for %s %s", sig.symbol, sig.side.value)
                
                mtf = metrics.get("mtf_alignment", {}) if isinstance(metrics.get("mtf_alignment"), dict) else {}
                tv = metrics.get("tradingview", {}) if isinstance(metrics.get("tradingview"), dict) else {}
                adv_context = {
                    "symbol": sig.symbol,
                    "side": sig.side.value,
                    "entry": sig.entry_mid,
                    "stop_loss": sig.stop_loss,
                    "take_profits": sig.take_profits,
                    "price": metrics.get("price", 0),
                    "timeframe": getattr(sig, "timeframe", None),
                    "leverage": getattr(sig, "leverage", None),
                    "rsi": metrics.get("rsi", 50),
                    "regime": metrics.get("regime_label", "UNKNOWN"),
                    "cvd_zscore": metrics.get("cvd_zscore", 0),
                    # Feed deterministic flow data already computed upstream.
                    "oi_change_5m_pct": metrics.get("oi_change_5m_pct"),
                    "oi_change_15m_pct": metrics.get("oi_change_15m_pct"),
                    "oi_change_1h_pct": metrics.get("oi_change_1h_pct"),
                    "funding_rate": metrics.get("funding_rate"),
                    "flow_direction": metrics.get("flow_direction"),
                    "qvol_5m": metrics.get("qvol_5m"),
                    "data_quality": metrics.get("data_quality"),
                    "mtf_score": mtf.get("score"),
                    "tv_score": tv.get("score"),
                    "validation_score": result.score,
                }

                approved, judge_verdict = await asyncio.to_thread(
                    bull_bear_check, sig.symbol, adv_context
                )

                _adv_mode = getattr(scfg, "ADVERSARIAL_MODE", "soft")
                _adv_floor = getattr(scfg, "ADVERSARIAL_SOFT_FLOOR", 75.0)
                if not approved:
                    _adv_verdict = judge_verdict
                    if _adv_mode == "hard":
                        # Legacy: judge NO hard-blocks the trade.
                        logger.warning("[ADVERSARIAL] REJECTED (hard) by judge: %s", judge_verdict[:200])
                        result.verdict = Verdict.REJECT
                        result.hard_blocks.append(f"Adversarial: {judge_verdict[:250]}")
                    elif _adv_mode == "off":
                        # Advisory only: never changes the verdict, just annotate.
                        logger.info("[ADVERSARIAL] NO (advisory, mode=off) — verdict kept: %s", judge_verdict[:200])
                    else:
                        # "soft" (default): high-conviction setups ride through; only
                        # weak-but-VALID ones get downgraded (not rejected) to WEAK.
                        if float(result.score) >= float(_adv_floor):
                            logger.info(
                                "[ADVERSARIAL] NO overridden — score=%.1f >= floor=%.1f, entry allowed: %s",
                                result.score, _adv_floor, judge_verdict[:160])
                        else:
                            logger.warning(
                                "[ADVERSARIAL] NO -> downgrade to WEAK (score=%.1f < floor=%.1f): %s",
                                result.score, _adv_floor, judge_verdict[:160])
                            result.verdict = Verdict.WEAK
                            # _adv_verdict (set above) carries the note into the
                            # consolidated report; no separate soft-flag field exists.
                else:
                    logger.info("[ADVERSARIAL] APPROVED: %s", judge_verdict[:200])
                   
            except Exception as exc:
                logger.warning("[ADVERSARIAL] Check failed (proceeding anyway): %s", exc)
        
        # --- Build ONE consolidated report and send ---
        consolidated = build_parser_report(
            sig, result, cls, source_name,
            calib=calib,
            adversarial_verdict=_adv_verdict,
        )
        try:
            from .chart_generator import build_chart
            chart_path = await build_chart(result)
        except Exception as exc:
            logger.warning("[SIGNAL_COPY] chart build failed: %s", exc)
        try:
            from .telegram_transport import send_parser_notification
            await send_parser_notification(consolidated, chart_path=chart_path)
        except Exception as exc:
            logger.error("❌ Consolidated notify failed: %s", exc)
        finally:
            if chart_path:
                try:
                    import os
                    os.remove(chart_path)
                except Exception:
                    pass

        # --- Stop processing for REJECT / WEAK ---
        if result.verdict == Verdict.REJECT or result.verdict == Verdict.WEAK:
            return

        # Calibration channel: validate only, never execute
        if calib:
            logger.info("[SIGNAL_COPY] calib channel — skip execution for %s", sig.symbol)
            return

        if self.executor is None:
            return  # consolidated report already sent above

        if self.auto_execute:
            await self.confirmations.register(result)
            await self.confirmations.resolve(result.signal.signal_id, approved=True)
            status = await self._execute_token(result.signal.signal_id)
            # Execution status sent via trades bot, not parser bot
            return

        # Normal path: register + ask user to confirm via the bot
        await self.confirmations.register(result)
        if self.confirm_bot is not None:
            # Confirmation bot handles the interactive prompt
            pass

    # ---------- execution path ----------
    async def _execute_token(self, signal_id: str) -> str:
        """Execute a confirmed signal (called by confirm bot or auto-exec)."""
        pc = await self.confirmations.get(signal_id)
        if pc is None:
            return f"Sinyal {signal_id} tidak ditemukan."
        if pc.state != ConfirmState.APPROVED:
            return f"Status sinyal: {pc.state.value}"

        if self.executor is None:
            return "Executor tidak tersedia."

        # Limit setup: if price hasn't reached the entry yet, wait for it
        # (avoid chasing) — execute automatically when the price touches the limit.
        sig = pc.result.signal
        price = _f(pc.result.metrics_snapshot.get("price"))
        entry = getattr(sig, "active_entry", None) or sig.entry_mid
        _regime = (pc.result.metrics_snapshot or {}).get("regime_label", "")
        if self._wait_for_limit(sig, price, regime=_regime):
            self._pending_limits[signal_id] = {"result": pc.result, "created": time.time()}
            await self.confirmations.mark(
                signal_id,
                ConfirmState.APPROVED,
                note="pending_limit",
            )
            pending_msg = (
                f"⏳ Limit setup {sig.symbol} {sig.side.value} @ {entry:g}. "
                f"Harga sekarang {price:g} — menunggu harga menyentuh limit. "
                f"Akan dieksekusi otomatis saat tercapai (batas 1 jam)."
            )
            # Entry notification for the pending-limit path: the orchestrator
            # discards the returned string (auto_execute path), so send it here
            # or the user gets no entry confirmation until the limit fills.
            try:
                await self._notify_trades_channel(pending_msg)
            except Exception as exc:
                logger.error("❌ Pending-limit notify failed: %s", exc)
            return pending_msg

        outcome = await self.executor.execute(
            pc.result,
            dry_run=self.dry_run,
            risk_pct=self.sizer.calc(pc.result.signal, pc.result.metrics_snapshot or {})
        )
        await self.confirmations.mark(
            signal_id,
            ConfirmState.EXECUTED if outcome.ok else ConfirmState.FAILED,
            note=outcome.reason,
        )
        if outcome.ok and outcome.notional > 0:
            # Build rich execution message for trades channel
            exec_payload = {
                "symbol": outcome.symbol,
                "side": outcome.side,
                "entry_price": outcome.entry_price,
                "notional": outcome.notional,
                "tp1": outcome.tp1,
                "tp_full": outcome.tp_full,
                "sl": outcome.sl_price,
                "risk_amount": outcome.risk_amount,
                "score": pc.result.score,
                "regime": "SIGNAL_COPY",
                "signal_id": pc.result.signal.signal_id,
            }
            # Add market data if available
            metrics = pc.result.metrics_snapshot or {}
            exec_payload.update({
                "price": metrics.get("price"),
                "cvd": metrics.get("cvd"),
                "oi_15m": metrics.get("oi_change_15m_pct") or metrics.get("oi_15m"),
                "oi_1h": metrics.get("oi_change_1h_pct") or metrics.get("oi_1h"),
                "funding": metrics.get("funding_rate") or metrics.get("funding_rate_pct") or metrics.get("funding"),
                "poc": metrics.get("poc") or metrics.get("poc_price"),
                "vol_ratio": metrics.get("vol_ratio"),
                "rsi": metrics.get("rsi"),
                "regime": metrics.get("regime_label") or "SIGNAL_COPY",
                "quadrant": metrics.get("quadrant") or "UNKNOWN",
            })
            
            # Send execution outcome via Telegram trades bot (fallback to parser bot)
            from signal_copy.telegram_transport import send_trades_notification
            from signal_copy.telegram_formatter import build_execution_message
            execution_msg = build_execution_message(outcome, pc.result.signal, pc.result)
            try:
                await send_trades_notification(execution_msg)
            except Exception as exc:
                logger.error(f"❌ Telegram trades notify failed: {exc}")
            
            return execution_msg
        
        # Failed execution
        return f"❌ Eksekusi gagal: {outcome.reason}"

    @staticmethod
    def _entry_ref(sig) -> float:
        """Reference entry price used for both the wait decision and the
        limit-reached trigger. Prefers the active entry (closest to price)
        over the zone midpoint so RR/validation stay consistent."""
        return float(getattr(sig, "active_entry", None) or getattr(sig, "entry_mid", 0.0) or 0.0)

    def _wait_for_limit(self, sig, price: float, regime: str = "") -> bool:
        """Regime-aware entry-style decision. Returns True to HOLD as a pending
        limit (wait for pullback), False to fill NOW at market.

        Drift = how far price has run past the signal entry in the PROFIT
        direction (the 'chasing' scenario), measured in R (entry->SL distance):
          - Fresh   (<= ENTRY_DRIFT_FRESH_R): market now.
          - Lagging (fresh..ENTRY_DRIFT_MAX_R): market ONLY in a chase regime
            (trending); otherwise wait for a pullback.
          - Too far (> ENTRY_DRIFT_MAX_R): always wait for a pullback.
        Explicit limit-typed signals still wait until the entry is reached.
        Note: the executor re-sizes notional from the ACTUAL fill, so chasing
        keeps risk ~constant; the drift band protects R:R, not risk."""
        entry = self._entry_ref(sig)
        sl = float(getattr(sig, "stop_loss", 0.0) or 0.0)
        if price <= 0 or entry <= 0:
            return False

        # Explicit limit entry: wait until the entry price is touched.
        if getattr(sig, "entry_type", "market") == "limit":
            return not self._limit_reached(sig, price)

        # Market-typed: only consider waiting if price ran in the PROFIT
        # direction. At/better than entry -> fill now.
        drift_abs = (price - entry) if sig.is_long else (entry - price)
        if drift_abs <= 0:
            return False

        # Drift in R units (fallback to 1% proxy if SL missing/degenerate).
        r_dist = abs(entry - sl) if sl > 0 else entry * 0.01
        if r_dist <= 0:
            return False
        drift_r = drift_abs / r_dist

        fresh = float(getattr(scfg, "ENTRY_DRIFT_FRESH_R", 0.25))
        far = float(getattr(scfg, "ENTRY_DRIFT_MAX_R", 0.50))
        chase_regimes = getattr(scfg, "ENTRY_CHASE_REGIMES", {"TRENDING"})
        reg = str(regime or "").upper()

        if drift_r <= fresh:
            return False                    # fresh — market now
        if drift_r <= far:
            return reg not in chase_regimes # lagging — chase only in trend
        return True                         # too far — wait for pullback

    def _limit_reached(self, sig, price: float) -> bool:
        ref = self._entry_ref(sig)
        if sig.is_long:
            return price <= ref
        else:
            return price >= ref

    # ---------- public: handle pending limits (call periodically) ----------
    async def check_pending_limits(self) -> None:
        """Poll pending limits and execute when price reaches the zone."""
        for token, data in list(self._pending_limits.items()):
            pc = data.get("result")
            if not pc:
                continue
            if time.time() - float(data.get("created", 0.0)) > scfg.CONFIRM_EXPIRY_SEC:
                self._pending_limits.pop(token, None)
                await self.confirmations.mark(token, ConfirmState.EXPIRED, note="limit_expired_1h")
                continue
            sig = pc.signal
            metrics = await self._fetch_metrics(sig.symbol)
            price = _f(metrics.get("price"))
            if price <= 0:
                continue
            if self._limit_reached(sig, price):
                self._pending_limits.pop(token, None)
                # Re-validate on pullback fill: the setup may have decayed while
                # waiting (price/flow moved against the thesis). If it is no
                # longer VALID, cancel the pending entry instead of chasing a
                # stale signal. Toggle via SIGNAL_COPY_ENTRY_REVALIDATE_ON_FILL.
                if getattr(scfg, "ENTRY_REVALIDATE_ON_FILL", True):
                    try:
                        revalid = validate_signal(sig, metrics)
                        if revalid.verdict != Verdict.VALID:
                            logger.warning(
                                "[PENDING] %s pullback filled but re-validation=%s "
                                "(score=%.1f) — cancel entry (stale setup)",
                                sig.symbol, revalid.verdict.value, revalid.score)
                            await self.confirmations.mark(
                                token, ConfirmState.EXPIRED,
                                note=f"revalidate_failed:{revalid.verdict.value}:{revalid.score:.0f}")
                            await self._notify_trades_channel(
                                f"🚫 Batal entry {sig.symbol} {sig.side.value}: harga kembali ke "
                                f"limit tapi sinyal sudah tidak valid "
                                f"({revalid.verdict.value}, score {revalid.score:.0f}).")
                            continue
                        # refresh snapshot so sizing/report use current metrics
                        pc.metrics_snapshot = revalid.metrics_snapshot or metrics
                    except Exception as exc:
                        logger.warning("[PENDING] re-validation error for %s: %s "
                                       "(proceeding with entry)", sig.symbol, exc)
                await self.confirmations.mark(
                    token,
                    ConfirmState.APPROVED,
                    note="limit_reached",
                )
                outcome = await self.executor.execute(
                    pc,
                    dry_run=self.dry_run,
                    risk_pct=self.sizer.calc(pc.signal, pc.metrics_snapshot or {}),
                )
                await self.confirmations.mark(
                    token,
                    ConfirmState.EXECUTED if outcome.ok else ConfirmState.FAILED,
                    note=outcome.reason,
                )
                if outcome.ok and outcome.notional > 0:
                    exec_payload = {
                        "symbol": outcome.symbol,
                        "side": outcome.side,
                        "entry_price": outcome.entry_price,
                        "notional": outcome.notional,
                        "tp1": outcome.tp1,
                        "tp_full": outcome.tp_full,
                        "sl": outcome.sl_price,
                        "risk_amount": outcome.risk_amount,
                        "score": pc.score,
                        "regime": "SIGNAL_COPY",
                        "signal_id": pc.signal.signal_id,
                    }
                    metrics = pc.metrics_snapshot or {}
                    exec_payload.update({
                        "price": metrics.get("price"),
                        "cvd": metrics.get("cvd"),
                        "oi_15m": metrics.get("oi_change_15m_pct") or metrics.get("oi_15m"),
                        "oi_1h": metrics.get("oi_change_1h_pct") or metrics.get("oi_1h"),
                        "funding": metrics.get("funding_rate") or metrics.get("funding_rate_pct") or metrics.get("funding"),
                        "poc": metrics.get("poc") or metrics.get("poc_price"),
                        "vol_ratio": metrics.get("vol_ratio"),
                        "rsi": metrics.get("rsi"),
                        "regime": metrics.get("regime_label") or "SIGNAL_COPY",
                        "quadrant": metrics.get("quadrant") or "UNKNOWN",
                    })
                    
                    execution_msg = build_execution_message(outcome, pc.signal, pc)
                    await self._notify_trades_channel(execution_msg)