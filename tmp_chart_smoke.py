import asyncio, os
from signal_copy.signal_parser import parse_signal
from signal_copy.validation_engine import ValidationResult, Verdict, Factor
from signal_copy.chart_generator import build_chart
text="""#BANK/USDT
SIGNAL Type: Regular (LONG)
Leverage: Cross (50X)
Entry Targets:
1) 0.1100
2) 0.1065
Take-Profit Targets:
1) 0.1160
2) 0.1200
3) 0.1400
Stop Target:
1) 0.1035
"""
sig=parse_signal(text)
sig.timeframe=sig.timeframe or '15m'
res=ValidationResult(sig, Verdict.REJECT, 94.0, [Factor('smoke', 1, 1, True, 'ok')], ['Adversarial: NO - smoke'], {'price':0.1113})
async def main():
    p=await build_chart(res)
    print('CHART', bool(p), p)
    if p:
        print('SIZE', os.path.getsize(p))
        os.remove(p)
asyncio.run(main())
