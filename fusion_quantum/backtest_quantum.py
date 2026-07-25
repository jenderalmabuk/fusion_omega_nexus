from __future__ import annotations
import json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fusionnew"))
from backtest.faithful_imbalance import _atr, _trend, _valid_obs, _imbalances, _manage_exit

DATA = None

def fetch_klines(symbol, timeframe, days):
    global DATA
    if DATA is None:
        raw = pd.read_parquet("/tmp/fq_klines.parquet")
        DATA = {(s, tf): g.reset_index(drop=True) for (s, tf), g in raw.groupby(["symbol", "timeframe"], sort=False)}
    direct = DATA.get((symbol, timeframe))
    if direct is not None: return direct
    source, rule = {"4h": ("1h", "4h"), "15m": ("5m", "15min")}.get(timeframe, (None, None))
    return resample_ohlcv(DATA.get((symbol, source), pd.DataFrame()), rule) if source else pd.DataFrame()


def resample_ohlcv(df, rule):
    if df.empty: return df
    return (df.set_index("open_time").resample(rule, origin="epoch")
            .agg({"open":"first", "high":"max", "low":"min", "close":"last", "volume":"sum"})
            .dropna().reset_index())

FIBS = (0.50, 0.618)
FIB_EXPIRY = 12
CONFIRM_WINDOW = 24
RR = 2.0
FEE_SLIP = 0.0007
TIERS = {"H1": ("1h", "5m"), "M30": ("30m", "3m")}


class ZoneLifecycle:
    def __init__(self):
        self.touched = set()
        self.found_zones = set()
        self.funnel = {k: 0 for k in ("zones_found", "zones_touched", "mss_confirmed", "pending_orders", "fills", "expired_orders")}

    @staticmethod
    def key(side, zone):
        t = zone["t"]
        if isinstance(t, pd.Timestamp): t = t.value
        return side, int(t), float(zone["zlow"]), float(zone["zhigh"])

    def found(self, side, zone):
        key = self.key(side, zone)
        if key not in self.found_zones:
            self.found_zones.add(key); self.funnel["zones_found"] += 1

    def start_retest(self, side, zone):
        key = self.key(side, zone)
        if key in self.touched: return False
        self.touched.add(key); self.funnel["zones_touched"] += 1
        return True

    def mark(self, event):
        self.funnel[event] += 1


def pnl_r(price_pnl, risk):
    return float(price_pnl / risk)


def pivots(df):
    h, l = df.high.to_numpy(), df.low.to_numpy(); out=[]
    for i in range(2, len(df)-2):
        if h[i] > h[i-1] and h[i] > h[i-2] and h[i] >= h[i+1] and h[i] >= h[i+2]: out.append((i,"H",float(h[i])))
        if l[i] < l[i-1] and l[i] < l[i-2] and l[i] <= l[i+1] and l[i] <= l[i+2]: out.append((i,"L",float(l[i])))
    return out


def mss_confirm(ltf, zone, side, start, end):
    p = pivots(ltf); last_h = [x for x in p if x[0] < start and x[1] == "H"]
    last_l = [x for x in p if x[0] < start and x[1] == "L"]
    if not last_h or not last_l: return None
    lh, ll = last_h[-1], last_l[-1]
    for i in range(max(start+2, 4), min(end, len(ltf)-1)):
        # sweep/rejection must happen before structure break
        prior = ltf.iloc[start:i]
        swept = (float(prior.low.min()) < ll[2]) if side == "BULL" else (float(prior.high.max()) > lh[2])
        if side == "BULL" and swept and float(ltf.close.iloc[i]) > lh[2]:
            return {"i":i,"sweep":float(prior.low.min()),"disp":float(ltf.high.iloc[i]),"side":side}
        if side == "BEAR" and swept and float(ltf.close.iloc[i]) < ll[2]:
            return {"i":i,"sweep":float(prior.high.max()),"disp":float(ltf.low.iloc[i]),"side":side}
    return None


def simulate(sym, tier, days=180):
    ztf, ltf_tf = TIERS[tier]; z=fetch_klines(sym,ztf,days); l=fetch_klines(sym,ltf_tf,days)
    if len(z) < 50 or len(l) < 300: return {"symbol":sym,"short":True}
    atr=_atr(l); trades=[]; lifecycle=ZoneLifecycle()
    lows=l.low.to_numpy(); highs=l.high.to_numpy(); closes=l.close.to_numpy()
    for side in ("BULL","BEAR"):
        obs=_valid_obs(z,side); imbs=_imbalances(l,side)
        for im in imbs:
            ce=int(im["ce"])
            prior=[ob for ob in obs if ob["t"] < im["t"] and im["leg_low"]<=ob["zhigh"] and im["leg_high"]>=ob["zlow"]]
            if not prior: continue
            zone=prior[-1]; lifecycle.found(side,zone)
            entry0=(im["leg_low"]+0.618*(im["leg_high"]-im["leg_low"])) if side=="BULL" else (im["leg_high"]-0.618*(im["leg_high"]-im["leg_low"]))
            start=None
            for touch in range(ce+1,min(ce+CONFIRM_WINDOW+1,len(l))):
                if (side=="BULL" and lows[touch]<=entry0) or (side=="BEAR" and highs[touch]>=entry0): start=touch; break
            if start is None: continue
            if not lifecycle.start_retest(side,zone): continue
            conf=mss_confirm(l,zone,side,start,min(start+CONFIRM_WINDOW,len(l)))
            if not conf: continue
            lifecycle.mark("mss_confirmed")
            lo,hi=(conf["sweep"],conf["disp"]) if side=="BULL" else (conf["disp"],conf["sweep"])
            entry=(lo+0.559*(hi-lo)) if side=="BULL" else (hi-0.559*(hi-lo))
            risk=(entry-(lo-0.5*atr[conf["i"]])) if side=="BULL" else ((hi+0.5*atr[conf["i"]])-entry)
            if not np.isfinite(risk) or risk<=0: continue
            sl=entry-risk if side=="BULL" else entry+risk; tp=entry+RR*risk if side=="BULL" else entry-RR*risk
            lifecycle.mark("pending_orders")
            fill=None
            for f in range(conf["i"]+1,min(conf["i"]+FIB_EXPIRY+1,len(l))):
                if (side=="BULL" and lows[f]<=entry) or (side=="BEAR" and highs[f]>=entry): fill=f;break
            if fill is None: lifecycle.mark("expired_orders"); continue
            lifecycle.mark("fills")
            pu,reason=_manage_exit(side,entry,sl,tp,lows,highs,closes,atr,fill,"fixed")
            trades.append({"symbol":sym,"side":side,"entry_time":str(l.open_time.iloc[fill]),"pnl_unit":pnl_r(pu,risk),"reason":reason})
    return {"symbol":sym,"trades":trades,"funnel":lifecycle.funnel}


def metrics(ts):
    pn=[x["pnl_unit"] for x in ts]; w=[x for x in pn if x>0]; lo=[x for x in pn if x<=0]
    eq=np.cumsum(pn); peak=np.maximum.accumulate(np.r_[0,eq]); dd=float(np.max(peak-np.r_[0,eq])) if pn else 0
    return {"n":len(pn),"wr":round(100*len(w)/len(pn),1) if pn else 0,"pf":round(sum(w)/abs(sum(lo)),2) if sum(lo) else (999 if w else 0),"net_unit":round(sum(pn),4),"maxdd_unit":round(dd,4)}


def walk_forward(ts, folds=4):
    ordered=sorted(ts,key=lambda x:x["entry_time"]); step=len(ordered)//(folds+2)
    out=[]
    if step == 0: return out
    for i in range(folds):
        cut=(i+2)*step
        while cut < len(ordered) and ordered[cut]["entry_time"] == ordered[cut-1]["entry_time"]: cut += 1
        end=min(cut+step,len(ordered))
        while end < len(ordered) and ordered[end]["entry_time"] == ordered[end-1]["entry_time"]: end += 1
        train, test=ordered[:cut], ordered[cut:end]
        if not test: break
        out.append({"train_end":train[-1]["entry_time"],"test_start":test[0]["entry_time"],"test_end":test[-1]["entry_time"],"train":metrics(train),"test":metrics(test)})
    return out


def concentration(ts):
    by={}
    for x in ts: by[x["symbol"]]=by.get(x["symbol"],0)+x["pnl_unit"]
    gross=sum(abs(v) for v in by.values()) or 1
    top=sorted(by.items(), key=lambda x: abs(x[1]), reverse=True)[:10]
    return {"top10_abs_share_pct":round(100*sum(abs(v) for _,v in top)/gross,1),"top10":[{"symbol":s,"net_r":round(v,3)} for s,v in top]}


def main():
    universe=Path("/app/runtime/revo/canonical_universe.txt")
    if not universe.exists(): universe=ROOT/"runtime/revo/canonical_universe.txt"
    syms=[x.strip() for x in open(universe) if x.strip()]; allr={}
    fetch_klines("BTCUSDT", "1h", 180)  # preload once before worker threads
    for tier in TIERS:
        t0=time.time(); rows=[]
        with ThreadPoolExecutor(max_workers=10) as ex:
            fs={ex.submit(simulate,s,tier):s for s in syms}
            for n,f in enumerate(as_completed(fs),1):
                try: rows.append(f.result())
                except Exception as e: rows.append({"symbol":fs[f],"error":repr(e)})
                if n%50==0: print(tier,n,"/",len(syms),flush=True)
        ts=[x for r in rows for x in r.get("trades",[])]; ts.sort(key=lambda x:x["entry_time"])
        cutoff=ts[int(len(ts)*.6)]["entry_time"] if ts else None
        iset=[x for x in ts if x["entry_time"] < cutoff] if cutoff else []
        oset=[x for x in ts if x["entry_time"] >= cutoff] if cutoff else []
        funnel={k:sum(r.get("funnel",{}).get(k,0) for r in rows) for k in ZoneLifecycle().funnel}
        allr[tier]={"elapsed":round(time.time()-t0,1),"symbols":len(syms),"short":sum(r.get("short",False) for r in rows),"errors":[r for r in rows if r.get("error")],"date_range":{"start":ts[0]["entry_time"] if ts else None,"cutoff":cutoff,"end":ts[-1]["entry_time"] if ts else None},"funnel":funnel,"concentration":concentration(ts),"walk_forward":walk_forward(ts),"all":metrics(ts),"is":metrics(iset),"oos":metrics(oset),"long":metrics([x for x in ts if x["side"]=="BULL"]),"short_side":metrics([x for x in ts if x["side"]=="BEAR"])}
        print(tier,json.dumps(allr[tier]),flush=True)
    Path("/tmp/fusion_quantum_results.json").write_text(json.dumps(allr,indent=2)); print(json.dumps(allr,indent=2))

if __name__=="__main__": main()
