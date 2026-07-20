"""Regression: marketing/promo posts must NOT become fake signals.

Real-world bug: promo posts ("guaranteed 300% profit", "join now", recruitment
stories) shipped with a mock chart image. parse_signal(text) returned None, so
the orchestrator fell through to the vision path, which read the picture and
fabricated a signal (BTCUSD 34522, INJUSDT 5.07). looks_like_promo() gates the
vision path so these are dropped.
"""

from signal_copy.classifier import looks_like_promo


# Actual promo captions that produced fake signals in production.
PROMOS = [
    (
        "💶Join Now and get guaranteed 300%+ profits daily 💵\n"
        "‼️Limited time only within 30 minutes‼️\n"
        "👇 Hurry and Join now 👇\n"
        "https://t.me/+9TkgmqrK6XI5Nzg9"
    ),
    (
        "✅ Yesterday I was resting with my family ... I opened my phone, "
        "pressed a couple of buttons on our premium signal, and closed the "
        "trade with +$1,200 ... I'll take 5 people ... only 5 spots\n"
        "💬 Private Signals: @Stanislaw_CT"
    ),
    "🔥 Join VIP now — guaranteed daily profits, limited spots!",
    "Hurry, only 3 spots left. Private Signals: @someone  t.me/+abcDEF",
]

# Genuine trade calls — many legitimately mention VIP/subscription handles.
# These must stay parseable (NOT flagged as promo).
REAL_SIGNALS = [
    "#ESPORTS/USDT LONG\nEntry 0.045\nSL 0.042\nTargets 0.046\n▶️FOR VIP Details: @Whales_Pumps_Owner",
    "APTUSDT LONG\nEntry: $0.5850 - $0.6040\nTargets: $0.6210\nStop: $0.5660\n💬 @emirfutures",
    "COIN POSITION LONG\nLEVERAGE 50X\nTRADE ENTRY (0.0007000)\nTRADE STOP LOSS (0.0005800)\nFOR VIP SUBSCRIPTION @Quick_pump_pro_admin",
    "#TOSHI/USDT LONG\nEntry Zone: 0.000129\nTP1 0.000133\nStop Loss: 0.000118",
    "LTCUSDT LONG\nEntry: 46.84\nTP: 48.98, 51.13\nSL: 44.70",
]


def test_promo_detected():
    for p in PROMOS:
        assert looks_like_promo(p), f"promo not detected: {p[:60]!r}"


def test_real_signals_not_flagged():
    for r in REAL_SIGNALS:
        assert not looks_like_promo(r), f"false positive on real signal: {r[:60]!r}"


def test_empty_is_not_promo():
    assert not looks_like_promo("")
    assert not looks_like_promo("   ")
