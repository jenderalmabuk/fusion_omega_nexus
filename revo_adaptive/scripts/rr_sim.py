import sqlite3
DB="/freqtrade/user_data/tradesv3_revo_v13914f2_bybit_dynamic_watch_promote.dryrun.sqlite"
conn=sqlite3.connect(DB)
cur=conn.cursor()
cur.execute('SELECT is_short,close_profit*100,open_rate,min_rate,max_rate FROM trades WHERE close_date IS NOT NULL')
T=[]
for r in cur.fetchall():
    s,p,o,mn,mx=r
    mfe=(o-mn)/o*100 if s else (mx-o)/o*100
    mae=(mx-o)/o*100 if s else (o-mn)/o*100
    rr=mfe/mae if mae>0.01 else 99
    T.append((p,rr))
print(f"ALL: n={len(T)} PnL={sum(t[0] for t in T):+.1f}%")
for th in [0.0,0.2,0.3,0.5,0.75,1.0]:
    k=[t for t in T if t[1]>=th]
    w=sum(1 for t in k if t[0]>0);l=len(k)-w
    pnl=sum(t[0] for t in k)
    aw=sum(t[0] for t in k if t[0]>0)/w if w else 0
    al=sum(t[0] for t in k if t[0]<=0)/l if l else -1
    pf=abs(aw*w/(al*l)) if al*l else 99
    wr=w*100//(w+l) if w+l else 0
    print(f"RR>={th:.1f}: n={len(k):3d} W={w:3d} L={l:2d} WR={wr}% PnL={pnl:+6.1f}% PF={pf:.2f} AvgW={aw:+.2f} AvgL={al:+.2f}")
conn.close()
