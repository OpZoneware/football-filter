import os, math, datetime, requests

KEY = os.environ["APIFOOTBALL_KEY"]
H = "https://v3.football.api-sports.io"
HD = {"x-apisports-key": KEY}
EDGE_MIN = 0.05          # your 5 percentage-point value gate
MAX_FIXTURES = 20        # request budget guard (free API = 100 calls/day)

# Active leagues only (no cups/friendlies). Add IDs via /leagues endpoint.
LEAGUES = {71:"Brasileirao",128:"Liga Profesional",113:"Allsvenskan",
 103:"Eliteserien",88:"Eredivisie",94:"Primeira Liga",106:"Ekstraklasa",
 235:"RPL",218:"Austria",283:"Romania",144:"Belgium",203:"Turkiye",
 210:"Croatia HNL",265:"Chile",253:"MLS",98:"J1 League"}

def get(path, **params):
    r = requests.get(H+path, headers=HD, params=params, timeout=30)
    r.raise_for_status(); return r.json()["response"]

def pois(l, k): return math.exp(-l)*l**k/math.factorial(k)

def match_probs(lh, la):
    ph=pd=pa=0.0
    for i in range(9):
        for j in range(9):
            p=pois(lh,i)*pois(la,j)
            if i>j: ph+=p
            elif i==j: pd+=p
            else: pa+=p
    return ph,pd,pa

def form_pts(s): return 3*s.count("W")+s.count("D")

def main():
    today = datetime.date.today().isoformat()
    year = datetime.date.today().year
    fixtures = [f for f in get("/fixtures", date=today)
                if f["league"]["id"] in LEAGUES and f["fixture"]["status"]["short"]=="NS"]
    fixtures = fixtures[:MAX_FIXTURES]
    
    tables = {}
    for lid in {f["league"]["id"] for f in fixtures}:
        st_data = {}
        # Smart Season Checker: Tries current year, then falls back to previous year
        for s in [year, year-1]:  
            try:
                st = get("/standings", league=lid, season=s)[0]["league"]["standings"]
                if st and len(st[0]) > 0:
                    st_data = {t["team"]["id"]: t for t in st[0]}
                    break
            except Exception: pass
        tables[lid] = st_data

    picks, close, rejected = [], [], []
    for f in fixtures:
        fid = f["fixture"]["id"]; lt = f["league"]["id"]
        th, ta = f["teams"]["home"]["id"], f["teams"]["away"]["id"]
        sh, sa = tables.get(lt,{}).get(th), tables.get(lt,{}).get(ta)
        name = f"{f['teams']['home']['name']} vs {f['teams']['away']['name']}"
        
        # Lowered from <3 to <2 to allow early season matches
        if not sh or not sa or sh["all"]["played"]<2 or sa["all"]["played"]<2:
            rejected.append((name,"Matchday-1/insufficient current-season data")); continue
            
        # injuries -> lineup gate
        try: inj = get("/injuries", fixture=fid); news = bool(inj)
        except Exception: inj, news = [], False
        def avail(tid):
            if not news: return 65
            miss = sum(1 for r in inj if r["team"]["id"]==tid for _ in r["players"])
            return min(100-8*miss, 80)          # lineups not out yet -> cap 80
        avh, ava = avail(th), avail(ta)
        if min(avh,ava) <= 60:
            rejected.append((name,f"lineup gate: availability {avh}/{ava}")); continue
            
        # expected goals from season gf/ga, nudged by last-5 form
        gh, ga = sh["all"]["goals"]["for"]/sh["all"]["played"], sh["all"]["goals"]["against"]/sh["all"]["played"]
        ah, aa = sa["all"]["goals"]["for"]/sa["all"]["played"], sa["all"]["goals"]["against"]/sa["all"]["played"]
        fh, fa = form_pts(sh["form"]), form_pts(sa["form"])
        lh = max(0.2, (gh+aa)/2*1.10*(1+0.04*(fh-8)))
        la = max(0.2, (ah+ga)/2*0.95*(1+0.04*(fa-8)))
        ph,pd,pa = match_probs(lh,la)
        p_ov = 1-sum(pois(lh+la,k) for k in (0,1,2))
        p_btts = (1-pois(lh,0))*(1-pois(la,0))
        p_h2 = sum(pois(lh,i)*sum(pois(la,j) for j in range(i-1)) for i in range(2,9))  # home -1.5
        
        # odds
        try: bk = get("/odds", fixture=fid)[0]["bets"]
        except Exception: bk = []
        def odd(bet, val):
            for b in bk:
                if b["name"]==bet:
                    for v in b["values"]:
                        if v["value"]==val: return float(v["odd"])
            return None
        o_h, o_ov, o_b, o_h2 = odd("Match Winner","Home"), odd("Goals Over/Under","Over 2.5"), \
                               odd("Both Teams Score","Yes"), odd("Home/Away Handicap","Home -1.5")
        ppg_h, ppg_a = sh["points"]/sh["all"]["played"], sa["points"]/sa["all"]["played"]
        facts = (f"ppg {ppg_h:.2f} v {ppg_a:.2f}; form {sh['form']} v {sa['form']}; "
                 f"rank {sh['rank']} v {sa['rank']}; avail {avh}/{ava}")
                 
        for mkt, est, o in [("Home win",ph,o_h),("Over 2.5",p_ov,o_ov),
                            ("BTTS Yes",p_btts,o_b),("Home -1.5",p_h2,o_h2)]:
            if o is None: continue
            imp = 1/o; edge = est-imp
            row = (LEAGUES[lt], name, mkt, o, est, imp, edge, facts)
            if edge >= EDGE_MIN: picks.append(row)
            elif edge >= 0.02: close.append(row)
            
    write_page(picks, close, rejected, today)

def write_page(picks, close, rejected, today):
    os.makedirs("docs", exist_ok=True)
    def rows(lst):
        return "".join(f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]:.2f}</td>"
                       f"<td>{r[4]*100:.1f}%</td><td>{r[5]*100:.1f}%</td><td>+{r[6]*100:.1f}%</td>"
                       f"<td>{r[7]}</td></tr>" for r in sorted(lst, key=lambda x:-x[6])) \
               or "<tr><td colspan=8>Nothing passed today — no forced picks.</td></tr>"
    html = f"""<html><meta name=viewport content="width=device-width"><style>
      body{{font-family:sans-serif;margin:12px;background:#111;color:#eee}}
      table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border:1px solid #444;padding:6px}}
      h2{{color:#7f5}} </style><body><h1>⚽ Filter Report — {today}</h1>
      <h2>✅ Picks (passed every gate)</h2><table><tr><th>League</th><th>Match</th><th>Market</th>
      <th>Odds</th><th>Est</th><th>Impl</th><th>Edge</th><th>Facts relied on</th></tr>{rows(picks)}</table>
      <h2>⚠️ Close (failed 5% edge)</h2><table>{rows(close)}</table>
      <h2>❌ Rejected</h2><ul>{''.join(f'<li>{n} — {r}</li>' for n,r in rejected) or '<li>none</li>'}</ul>
      </body></html>"""
    open("docs/index.html","w").write(html)

main()
