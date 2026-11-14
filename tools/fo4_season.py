"""Add FIFA Online 4's 26 LIVE and 25 LIVE cards to the game as two new seasons, and WC18.
User: "buatkan session baru untuk musim 2026 bedasarkan database fifa online 4
https://id.fifaaddict.com/fo4db?class=25,live ikuti stat nya dianggap +0 icon ikuti berdasarkan
fifa online 4"; then the 2018 World Cup's best 300 squad players as WC18 (see WC18 below).

    python tools/fo4_season.py fetch [--min-ovr 72]   fifaaddict search -> gamedata/fo4_cards.json
    python tools/fo4_season.py collect                 console script that reads the card pages
    python tools/fo4_season.py import FILE             its output -> gamedata/fo4_cards.json
    python tools/fo4_season.py wc18 [--game DIR]       futwiz + Wikipedia -> gamedata/wc18_cards.json
    python tools/fo4_season.py build                   cards -> gamedata/fifadb.sqlite
    python tools/fo4_season.py install [--game DIR]    fifadb + season icons -> the client
    python tools/fo4_season.py hud [--game DIR]        only the match HUD's season icons (Module.big)
    python tools/fo4_season.py restore [--game DIR]    put the client files back
    python tools/fo4_season.py selftest

Seasons keep FO4's own codes (Nexon's seasonid.json): 300 = "26 LIVE", 325 = "25 LIVE", so a card
is spid = season * 1e6 + player id, like every FO3 card (PlayerListUtil.getSeason is
floor(spid / 1e6) and takes codes over 99). Stats are the fifaaddict card page's attributes,
taken as +0: the client's rating of a card (calcPosOvr) comes out as FO4's OVR, and the grade
bonus goes on top. overallrating, the rating the Market list shows, is the +1 one (PLUS1 above),
as on every FO3 card (user: "mbape 79 adalah +0 sementara +1 adalah 84, digambar harusnya 84").
Players FO3 already knows (same birth date and name) keep their player id,
face and names; the others get a new id from NEW_PID up, a generic head copied from a FO3 player
with the same skin tone, and names in every names table. No card photo for those (silhouette).
A player whose club FO3 does not have (no FO3 card plays there, so the client has neither its
name nor its crest) goes to FO3's Rest of the World team, like FO3's own players of unlicensed
clubs (user: "Jika player session 25 26 klub nya tidak ada di db maka masuk rest of world").

fetch walks fifaaddict's search (100 rows a page, so it splits by rating, then position; one
request every DELAY s, cached in %TEMP%/fo4_cache so a rerun resumes), plus one search per FO3
trait (trait=<slug>) for the cards' Karakteristik. FO4's LIVE cards only carry the newer traits:
of FO3's 30 they have 18 (no power header, swerve, second wind, ...). The stats are not in the
pages' HTML (see COLLECT_JS). After build and install, restart the server.
"""
import argparse, collections, difflib, hashlib, json, os, re, shutil, sqlite3, sys, tempfile, time, unicodedata
import urllib.request
from contextlib import closing

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "re"))

CARDS = os.path.join(ROOT, "gamedata", "fo4_cards.json")
FIFADB = os.path.join(ROOT, "gamedata", "fifadb.sqlite")
BACKUP = os.path.join(ROOT, "backup", "fo4season")
SITE = "https://id.fifaaddict.com"
CLASSES = {"live": 300, "25": 325}
SEASON_NAMES = {300: "26 LIVE", 325: "25 LIVE"}
POSITIONS = "st,lw,rw,cf,cam,lm,rm,cm,cdm,lwb,rwb,lb,rb,cb,gk".split(",")
CAP, DELAY = 100, 2.0
NEW_PID = 700000  # first player id for players FO3 does not have (FO3's are below 300000)
with open(os.path.join(ROOT, "responses", "static_db_PLAYERGRADE.json"), encoding="utf-8") as _f:
    PLUS1 = json.load(_f)["1"]["boost"]["PLAYER_ALLATTRIBUTES_ALL"]  # FO3's +1 all-attribute boost (5)
REST_OF_WORLD = (999998, 9998)  # FO3's (teamid, leagueid) for players of unlicensed clubs (11,000+ cards)
FO3_CLUB = {131682: 44, 115845: 39, 115841: 46, 131681: 47}  # FO4's new ids of clubs FO3 has: Lombardia FC =
# Inter, Bergamo Calcio = Atalanta, SS Lazio = Lazio, Milano FC = AC Milan (an OPEN_CLUBS club)
# Clubs the FO3 ID client lacks though data_5.big still has their kits (user: "Buka semua klub yang tadi
# nya terblokir karena lisensi" + "Dan sesuaikan pemainnya kembali ke klub itu"): {teamid: (FO3 league,
# fifaaddict search "league/team")}. fetch lists their 26 LIVE squads; build keeps FO4 cards there and
# moves FO3's own '17 cards that sit on their national team (nationalteamid = that team) to the club of
# their 2017/18 career; install adds the clubs to the client (engine_clubs, locale_db). Crests: the
# admin page's Aset tab once a club has a full squad.
OPEN_CLUBS = {47: (31, "italy-serie-a/milano-fc"), 110556: (31, "italy-serie-a/genoa"), 55: (31, "italy-serie-a/udinese"),
              189: (31, "italy-serie-a/bologna"), 1842: (31, "italy-serie-a/cagliari"), 1837: (32, "italy-serie-b/sampdoria")}


# --- window.__NUXT__ (a JS function call returning an object literal) -------------------------

class _Ref(str):
    pass


_WORD, _NUM = re.compile(r"void 0|[\w$]+"), re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")


def _js(s, i):
    """Parse one JS literal value at s[i:]; identifiers become _Ref (resolved later)."""
    while s[i] in " \n\t":
        i += 1
    c = s[i]
    if c in "{[":
        obj, end, i = c == "{", "}" if c == "{" else "]", i + 1
        out = {} if obj else []
        while True:
            while s[i] in " \n\t,":
                i += 1
            if s[i] == end:
                return out, i + 1
            if not obj:
                v, i = _js(s, i)
                out.append(v)
                continue
            if s[i] == '"':
                key, i = _js(s, i)
            else:
                m = _WORD.match(s, i)
                key, i = m.group(), m.end()
            assert s[i] == ":", s[i - 20:i + 20]
            out[key], i = _js(s, i + 1)
    if c == '"':
        j = i + 1
        while s[j] != '"':
            j += 2 if s[j] == "\\" else 1
        return json.loads(s[i:j + 1]), j + 1
    m = _NUM.match(s, i)
    if m:
        v = m.group()
        return (float(v) if m.group(1) or m.group(2) else int(v)), m.end()
    w = _WORD.match(s, i).group()
    return {"true": True, "false": False, "null": None, "void 0": None}.get(w, _Ref(w)), i + len(w)


def nuxt(html):
    """window.__NUXT__ of a fifaaddict page as Python data: (function(a,b,..){return {..}}(v,..)),
    or a plain {..} when the page needs no shared values."""
    head = "window.__NUXT__=(function("
    if head not in html:
        return _js(html, html.index("window.__NUXT__=") + len("window.__NUXT__="))[0]
    i = html.index(head) + len(head)
    j = html.index(")", i)
    params = html[i:j].split(",")
    body, k = _js(html, html.index("return", j) + len("return"))
    k = html.index("(", k) + 1
    vals = []
    while True:
        while html[k] in " ,":
            k += 1
        if html[k] == ")":
            break
        v, k = _js(html, k)
        vals.append(v)
    env = dict(zip(params, vals))

    def res(v):
        if isinstance(v, _Ref):
            return env.get(v)
        if isinstance(v, dict):
            return {k: res(x) for k, x in v.items()}
        if isinstance(v, list):
            return [res(x) for x in v]
        return v
    return res(body)


# --- fetch ------------------------------------------------------------------------------------

def fetcher(cache, site=SITE, agent="Mozilla/5.0 (FO3 private server season sync)"):
    """fetch(path or full URL) -> page text, one request every DELAY s, cached in `cache`."""
    os.makedirs(cache, exist_ok=True)
    last = [0.0]

    def fetch(path):
        f = os.path.join(cache, hashlib.sha1(path.encode()).hexdigest() + ".html")
        if os.path.exists(f):
            return open(f, encoding="utf-8").read()
        time.sleep(max(0.0, DELAY - (time.time() - last[0])))
        for attempt in range(4):
            try:
                url = path if path.startswith("http") else site + path
                req = urllib.request.Request(url, headers={"User-Agent": agent})
                body = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
                break
            except OSError as e:
                print("retry", attempt, path, e, flush=True)
                time.sleep(15 * (attempt + 1))
        else:
            raise SystemExit("failed: " + path)
        last[0] = time.time()
        open(f, "w", encoding="utf-8").write(body)
        return body
    return fetch


def listing(fetch, cls, min_ovr):
    """{uid: search row} of a class at displayed rating >= min_ovr."""
    rows = {}

    def page(q):
        items = nuxt(fetch(f"/fo4db?class={cls}{q}"))["data"][0]["items"]
        for it in items:
            rows.setdefault(it["uid"], it)
        return len(items)

    for v in range(99, min_ovr - 1, -1):
        if page(f"&attr=ovr_{v}-{v}") >= CAP:
            for p in POSITIONS:
                if page(f"&attr=ovr_{v}-{v}&pos={p}") >= CAP:
                    print(f"  {cls} ovr {v} pos {p}: over {CAP}, some cards missed", flush=True)
        print(f"{cls} ovr {v}: {len(rows)} cards", flush=True)
    return rows


# trait1 bit order = the client's PlayerListUtil.PLAYER_TRAITS (TRAIT1_START_BIT 0 .. END 29;
# trait2 is not shown); fifaaddict's trait slugs are the same names (crowd-favourite: none)
FO3_TRAITS = ("inflexibility long-throw-in takes-powerful-driven-free-kicks diver injury-prone solid-player "
              "avoids-using-weaker-foot dives-into-tackles tries-to-beat-offside-trap selfish leadership "
              "argues-with-officials early-crosser finesse-shot flair long-passer long-shot-taker speed-dribbler "
              "play-maker pushes-up-for-corners puncher gk-long-thrower power-header gk-one-on-ones giant-throw-in "
              "outside-foot-shot crowd-favourite swerve second-wind acrobatic-clearance").split()


# Style = the badges bits in the client's PlayerListUtil.PLAYER_BADGES order. fifaaddict has no
# such thing for FO4, so they come from the stats, by rules fitted on FO3's own 15,274 2017
# cards (exact for speedster, dribbler, box to box, crosser, tackling, tactician, clinical
# finisher and complete defender; the others miss a few cards).
FO3_BADGES = ("poacher speedster aerial-threat dribbler playmaker box-to-box distance-shooter crosser "
              "fk-specialist tackling tactician agile strength clinical-finisher complete-defender "
              "complete-midfielder complete-forward").split()


def badges(r):
    """badges bits of a players row (dict with stats, weight and workrates)."""
    at = lambda **kv: all(r[k] >= v for k, v in kv.items())
    b = {"poacher": at(finishing=77, positioning=73, headingaccuracy=81),
         "speedster": r["acceleration"] + r["sprintspeed"] >= 154,
         "aerial-threat": at(headingaccuracy=77, strength=69),
         "dribbler": at(dribbling=73, balance=63),
         "playmaker": at(vision=73, shortpassing=74, longpassing=71),
         "box-to-box": r["stamina"] >= 73 and r["attackingworkrate"] == 2 == r["defensiveworkrate"],
         "distance-shooter": at(longshots=72, shotpower=74, ballcontrol=57),
         "crosser": at(crossing=73, curve=68),
         "fk-specialist": at(freekickaccuracy=73, ballcontrol=64, crossing=58),
         "tackling": at(slidingtackle=73, standingtackle=72),
         "tactician": at(reactions=68, interceptions=73),
         "agile": r["agility"] >= 76,
         "strength": at(strength=73, weight=83),
         "clinical-finisher": at(finishing=73, longshots=68)}
    b["complete-defender"] = b["tackling"] and b["tactician"] and (b["aerial-threat"] or b["agile"] or b["strength"])
    b["complete-midfielder"] = b["playmaker"] and (b["crosser"] or b["tackling"] or b["clinical-finisher"])
    b["complete-forward"] = b["clinical-finisher"] and b["speedster"] + b["aerial-threat"] + b["dribbler"] + b["strength"] >= 2
    return sum(1 << i for i, k in enumerate(FO3_BADGES) if b[k])


def search(fetch, cls, lo, hi, extra=""):
    """{uid: row} of a class at displayed rating lo..hi: halves the range while a page is full."""
    items = nuxt(fetch(f"/fo4db?class={cls}{extra}&attr=ovr_{lo}-{hi}"))["data"][0]["items"]
    if len(items) < CAP:
        return {it["uid"]: it for it in items}
    if lo < hi:
        mid = (lo + hi) // 2
        return {**search(fetch, cls, mid + 1, hi, extra), **search(fetch, cls, lo, mid, extra)}
    if "&pos=" in extra:
        print(f"  {cls}{extra} ovr {lo}: over {CAP}, some cards missed", flush=True)
        return {it["uid"]: it for it in items}
    out = {}
    for p in POSITIONS:
        out.update(search(fetch, cls, lo, hi, f"{extra}&pos={p}"))
    return out


def card_traits(fetch, cls, min_ovr, total):
    """{uid: [trait slugs]} from one search per FO3 trait. A search that finds (nearly) every
    one of the class's `total` cards ignored its filter (inflexibility does): left out."""
    out = collections.defaultdict(list)
    for slug in FO3_TRAITS:
        if slug == "crowd-favourite":
            continue
        hits = search(fetch, cls, min_ovr, 99, f"&trait={slug}")
        if total and len(hits) >= 0.9 * total:
            print(f"  {cls}: the {slug} search ignores its filter ({len(hits)} cards), left out", flush=True)
            continue
        for uid in hits:
            out[uid].append(slug)
    return out


def load_cards():
    return json.load(open(CARDS, encoding="utf-8")) if os.path.exists(CARDS) else {}


def save_cards(cards):
    tmp = CARDS + ".tmp"
    json.dump(cards, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, CARDS)


def club_rows(fetch):
    """{uid: search row} of the 26 LIVE cards whose club is an OPEN_CLUBS one (fifaaddict's club
    search also lists players who were there once)."""
    rows = {}
    for tid, (_, slug) in OPEN_CLUBS.items():
        league, team = slug.split("/")
        hits = search(fetch, "live", 40, 99, f"&league={league}&team={team}")
        mine = {u: r for u, r in hits.items() if FO3_CLUB.get(int(r.get("team_id") or 0), int(r.get("team_id") or 0)) == tid
                and int(r["year"]) == CLASSES["live"]}
        print(f"club {tid} ({team}): {len(mine)} cards", flush=True)
        rows.update(mine)
    return rows


def cmd_fetch(a):
    fetch = fetcher(a.cache)
    cards = load_cards()
    for uid, row in club_rows(fetch).items():
        cards.setdefault(uid, {"fo3traits": []}).update(row=row, season=CLASSES["live"])
    for cls, season in CLASSES.items():
        for uid, row in listing(fetch, cls, a.min_ovr).items():
            if int(row["year"]) == season:
                cards.setdefault(uid, {}).update(row=row, season=season)
        traits = card_traits(fetch, cls, a.min_ovr, sum(c["season"] == season for c in cards.values()))
        for uid, c in cards.items():
            if c["season"] == season:
                c["fo3traits"] = traits.get(uid, [])
        print(f"{cls}: traits of {sum(1 for u in traits if u in cards)} cards", flush=True)
    save_cards(cards)
    print(f"{len(cards)} cards listed, {sum('attr' in c for c in cards.values())} with stats -> {CARDS}")


# The card pages load their stats from fifaaddict's own API after a handshake, so the stats are
# read in a browser tab on the site: `collect` prints a script for the tab's console that opens
# each card (in-app navigation, like a click), keeps one line per card in localStorage.fo4rows
# and copies them all at the end; save them to a file and run `import FILE`.
# Line: uid|ROW_FIELDS...|33 two-digit stats in STATS order.
ROW_FIELDS = ("name name_short birthdate height weight pos1 pos2 pos3 pos4 foot_pref foot_weak workrate_att "
              "workrate_def skill_level skintonecode reputation current_ovr nation_id team_id league_id nation_squad_id").split()
COLLECT_JS = """(async () => {
  const todo = %s, F = %s, S = %s, wait = ms => new Promise(r => setTimeout(r, ms));
  const rows = (localStorage.getItem('fo4rows') || '').split('\\n').filter(Boolean);
  const done = new Set(rows.map(r => r.split('|')[0]));
  for (const uid of todo) {
    if (done.has(uid)) continue;
    window.$nuxt.$router.push('/fo4db/pid' + uid);
    let s = null;
    for (let i = 0; i < 80 && !(s && s.attr && s.db); i++) {
      await wait(250);
      s = window.$nuxt.$store.state.foState['foPlayerpid' + uid + '_id'];
    }
    if (s && s.attr && s.db) {
      const st = S.map(k => String(Math.min(99, (s.attr[k] || {}).value || 0)).padStart(2, '0')).join('');
      rows.push([uid, ...F.map(f => String(s.db[f] ?? '').replace(/[|\\n]/g, ' ')), st].join('|'));
      localStorage.setItem('fo4rows', rows.join('\\n'));
    }
    window.fo4progress = rows.length + '/' + todo.length;
    await wait(%d);
  }
  window.fo4progress = 'done ' + rows.length;
  try { copy(rows.join('\\n')); console.log('fo4rows copied: save them to a file, then run import FILE'); } catch (e) {}
})();"""


def collect_js(cards):
    todo = [u for u, c in sorted(cards.items(), key=lambda kv: -kv[1]["row"].get("pos1val", 0)) if "attr" not in c]
    return COLLECT_JS % (json.dumps(todo), json.dumps(ROW_FIELDS), json.dumps(STATS), int(DELAY * 1000)), len(todo)


def cmd_collect(a):
    js, n = collect_js(load_cards())
    print(f"// {n} cards to read: paste into the console of a {SITE}/fo4db tab\n{js}")


def cmd_import(a):
    cards, got = load_cards(), 0
    for line in open(a.file, encoding="utf-8").read().splitlines():
        parts = line.split("|")
        if len(parts) != 2 + len(ROW_FIELDS) or parts[0] not in cards:
            continue
        st = parts[-1]
        cards[parts[0]].update(db=dict(zip(ROW_FIELDS, parts[1:-1])), attr={k: int(st[2 * i:2 * i + 2]) for i, k in enumerate(STATS)})
        got += 1
    save_cards(cards)
    print(f"{got} cards imported, {sum('attr' in c for c in cards.values())}/{len(cards)} with stats")


# --- build: cards -> gamedata/fifadb.sqlite ---------------------------------------------------
# fifaaddict values -> fifadb codes (checked on FO3's own 2017 cards: Messi, Ronaldo, Kante, Alba)
POS = {"GK": 0, "SW": 1, "RWB": 2, "RB": 3, "CB": 5, "LB": 7, "LWB": 8, "CDM": 10, "RM": 12, "CM": 14,
       "LM": 16, "CAM": 18, "RF": 20, "CF": 21, "LF": 22, "RW": 23, "ST": 25, "LW": 27}
WORKRATE = {"mid": 0, "medium": 0, "low": 1, "high": 2}
REPUTATION = {"regular player": 1, "famous player": 2, "top class": 3, "world class": 4, "legendary": 5}
STATS = ("acceleration aggression agility balance ballcontrol crossing curve dribbling finishing freekickaccuracy "
         "gkdiving gkhandling gkkicking gkpositioning gkreflexes headingaccuracy jumping longpassing longshots "
         "marking penalties positioning reactions shortpassing shotpower slidingtackle sprintspeed stamina "
         "standingtackle strength interceptions vision volleys").split()  # FO4's composure has no column
NAME_TABLES = ("nameskor", "nameschi", "namestha", "namesvie", "nameseng", "namesind")
PERM_TABLES = ("permkr", "permcn", "permth", "permvn", "permsg", "permid")
PIDS = os.path.join(ROOT, "gamedata", "fo4_pids.json")
HEADS = os.path.join(ROOT, "gamedata", "fo4_heads.json")  # {new player id: 2017 player whose head he wears}


def plain(s):
    """ASCII form of a name (FO3's own names carry no accents)."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(ch)).replace("ø", "o").replace("Ø", "O")


def _key(s):
    return re.sub(r"[^a-z ]", "", plain(s).lower()).strip()


def _date(fo4):
    d, m, y = (int(x) for x in fo4.split("."))
    return y, m, d


def _same_name(a, b):
    """0..2: name likeness, +1 when the last names are the same (a shared first name is not enough:
    Lionel Messi and L. Gamba share a birthday)."""
    a, b = _key(a), _key(b)
    if not a or not b:
        return 0.0
    ta, tb = a.split(), b.split()
    # FO4's short form of a long FO3 name: "Rodri" / "Rodrigo Hernandez Cascante", "Fabian Ruiz" /
    # "Fabian Ruiz Pena" (every word starts a word of the other name, in order)
    it = iter(tb)
    inside = all(any(w.startswith(x) for w in it) for x in ta)
    return (ta[-1] == tb[-1] or inside) + difflib.SequenceMatcher(None, a, b).ratio()


def new_player_cards():
    """{spid: fifaaddict card id (its uid)} of the FO4 cards of the players FO3 does not have: a card's
    birth date and name give his player id as they did when build made him (PIDS). fifaaddict has
    each card's picture by that id (fa_faces.FA4; user: "pemain baru kenapa tidak di search by nama")."""
    try:
        with open(PIDS) as f:
            pids = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for uid, card in load_cards().items():
        db = card.get("db")
        if db and card.get("season") and db.get("birthdate"):
            pid = pids.get(f"{'-'.join(map(str, _date(db['birthdate'])))}|{_key(db['name'])}")
            if pid:
                out[card["season"] * 1000000 + pid] = uid
    return out


def cmd_build(a):
    cards = {u: c for u, c in load_cards().items() if "attr" in c}
    pids = json.load(open(PIDS)) if os.path.exists(PIDS) else {}
    c = sqlite3.connect(FIFADB); c.row_factory = sqlite3.Row
    cols = [r[1] for r in c.execute("PRAGMA table_info(players)")]
    seasons = (*CLASSES.values(), WC18)
    c.execute(f"DELETE FROM players WHERE spid / 1000000 IN ({','.join('?' * len(seasons))})", seasons)
    for t in PERM_TABLES:
        c.execute(f"DELETE FROM {t} WHERE spid / 1000000 IN ({','.join('?' * len(seasons))})", seasons)
    for t in NAME_TABLES:
        c.execute(f"DELETE FROM {t} WHERE playerid BETWEEN ? AND ?", (NEW_PID, NEW_PID + 99999))
    clubs = {r[0] for r in c.execute("SELECT DISTINCT teamid FROM players")} | set(OPEN_CLUBS)  # the clubs FO3 has
    # FO3's players: birth date -> [(pid, full, short)], and each pid's newest card (its face)
    born, best = collections.defaultdict(list), {}
    names = {r[0]: (r[1], r[2]) for r in c.execute("SELECT playerid, full_eng, short_eng FROM nameseng")}
    for r in c.execute("SELECT * FROM players ORDER BY spid"):
        pid, season = r["playerid"], r["spid"] // 1000000
        m = re.match(r"(\d+)/(\d+)/(\d+)", r["birthdate"] or "")
        if m and pid not in best:
            born[(int(m[3]), int(m[1]), int(m[2]))].append(pid)
        rank = (season == 88, season == 0, season)
        if pid not in best or rank > best[pid][0]:
            best[pid] = (rank, r)
    generic = collections.defaultdict(list)  # skin tone -> 2017 cards with a generic head
    for r in c.execute("SELECT * FROM players WHERE spid / 1000000 = 88 AND headclasscode = 1 ORDER BY spid"):
        generic[r["skintonecode"]].append(r)
    cards.update(wc18_build_cards(best))
    found = new = 0
    heads = {}
    for uid, card in sorted(cards.items(), key=lambda kv: kv[1]["season"]):
        db, attr, season = card["db"], card["attr"], card["season"]
        when = _date(db["birthdate"])
        cands = [(max(_same_name(db["name"], names.get(p, ("", ""))[0]), _same_name(db["name_short"], names.get(p, ("", ""))[1])), p)
                 for p in born.get(when, []) if p < NEW_PID]
        score, pid = (2.0, card["pid"]) if card.get("pid") in best else max(cands, default=(0, None))
        person = f"{'-'.join(map(str, when))}|{_key(db['name'])}"
        if score >= 0.85:  # same last name, or a near-identical spelling (Haland / Haaland)
            base = dict(best[pid][1])
            found += 1
        else:
            pid = pids.get(person) or max([NEW_PID - 1, *pids.values()]) + 1
            pids[person] = pid
            tone = int(db.get("skintonecode") or 2)
            near = min(generic, key=lambda t: (abs(t - tone), t))
            pool = generic[near]
            base = dict(pool[int(hashlib.md5(person.encode()).hexdigest(), 16) % len(pool)])
            heads[pid] = base["playerid"]  # the engine DB row install copies for the 3D player
            base.update(trait1=0, trait2=0)
            new += 1
        spid = season * 1000000 + pid
        poss = [POS[p] for p in (db.get(f"pos{i}") for i in range(1, 5)) if p in POS][:4]
        ovr = int(db.get("current_ovr") or db.get("pos1val") or 0)
        tid = FO3_CLUB.get(int(db.get("team_id") or 0), int(db.get("team_id") or 0))
        team, league = (tid, int(db.get("league_id") or 0)) if tid in clubs else REST_OF_WORLD
        base.update({k: max(1, min(99, int(attr[k]))) for k in STATS if k in attr})
        base.update(spid=spid, playerid=pid, birthdate=f"{when[1]}/{when[2]}/{when[0]} 12:00:00 AM",
                    height=int(db["height"]), weight=int(db["weight"]),
                    preferredfoot=2 if db.get("foot_pref") == "left" else 1,
                    weakfootabilitytypecode=int(db.get("foot_weak") or 3), skillmoves=max(0, int(db.get("skill_level") or 1) - 1),
                    attackingworkrate=WORKRATE.get(db.get("workrate_att"), 0), defensiveworkrate=WORKRATE.get(db.get("workrate_def"), 0),
                    internationalrep=REPUTATION.get((db.get("reputation") or "").lower(), 1),
                    overallrating=ovr + PLUS1, potential=ovr + PLUS1, nationality=int(db["nation_id"]),
                    leagueid=league, teamid=team,
                    nationalteamid=int(db.get("nation_squad_id") or 0) or -1,
                    trait1=sum(1 << FO3_TRAITS.index(t) for t in card.get("fo3traits", [])),  # Karakteristik
                    contractvaliduntil=2027, playerjointeamdate="Tue, 01 Jul 2025 00:00:00 GMT", historyopen=0,
                    **{f"preferredposition{i + 1}": (poss[i] if i < len(poss) else -1) for i in range(4)})
        base.update(card.get("keep", {}))  # WC18: what stays as on the player's own FO3 card
        base["badges"] = badges(base)  # Style
        c.execute(f"INSERT INTO players ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [base[k] for k in cols])
        for t in PERM_TABLES:
            c.execute(f"INSERT INTO {t} (spid, licensed, transferable) VALUES (?, 1, 1)", (spid,))
        if pid >= NEW_PID:
            full, short = plain(db["name"]), plain(db.get("name_short") or db["name"])
            jersey = short.split(". ", 1)[-1]
            for t in NAME_TABLES:
                c.execute(f"INSERT OR REPLACE INTO {t} (playerid, full, short, full_eng, short_eng, jersey) VALUES (?,?,?,?,?,?)",
                          (pid, full, short, full, short, jersey))
    careers = careers_of(c)
    moved, row = open_clubs_move(c, careers), rest_of_world_move(c, careers)
    edited = apply_card_edits(c, seasons)
    sys.path.insert(0, ROOT)
    from http_config import POSOVR  # exactly the client's own +0 rating + PLUS1, as on FO3's cards
    c.execute(f"UPDATE players AS p SET overallrating = {POSOVR} + ?1, potential = {POSOVR} + ?1 "
              f"WHERE spid / 1000000 IN ({','.join(str(s) for s in seasons)})", (PLUS1,))
    c.commit()
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.close()
    json.dump(pids, open(PIDS, "w"), indent=0)
    json.dump(heads, open(HEADS, "w"))
    per = collections.Counter(card["season"] for card in cards.values())
    print(f"{len(cards)} cards ({', '.join(f'{SEASON_NAMES[s]} {n}' for s, n in sorted(per.items()))}): "
          f"{found} players FO3 has, {new} new, {moved} '17 cards back at their club, {row} Rest of the World cards "
          f"back at their Italian club, {edited} edited on the admin page -> {FIFADB}")


def club_in(stints, year=2017):
    """The club of a career [[teamid, joined, left (-1 = still), loan]] in year/year+1: the newest
    one joined by year and left in year+1 or later (else in year), or None."""
    held = [x for x in stints if x[1] <= year and (x[2] in (-1, None) or x[2] > year)] or \
           [x for x in stints if x[1] <= year <= (x[2] if x[2] not in (-1, None) else 9999)]
    return max(held, key=lambda x: x[1])[0] if held else None


def club_joined(stints, year):
    """The club a career joined in year (not a loan, if both), or None (none, or two clubs)."""
    hit = [x for x in stints if x[1] == year]
    hit = [x for x in hit if not x[3]] or hit
    return hit[0][0] if hit and len({x[0] for x in hit}) == 1 else None


def careers_of(c):
    """{playerid: career} from tools/club_history.py's careers, else FO3's own playerhistory."""
    import club_history
    out = collections.defaultdict(list)
    for pid, team, joined, left, loan in c.execute("SELECT playerid, teamid, joinedyear, endyear, loan FROM playerhistory"):
        out[pid].append([team, joined, left, loan])
    out.update({int(k): v for k, v in ((club_history.load() or {}).get("players") or {}).items() if v})
    return out


def open_clubs_move(c, careers):
    """FO3's '17 cards (seasons 0 and 88) on a national team whose player was at an OPEN_CLUBS club
    in 2017/18 go to that club; their national team stays as nationalteamid. Returns how many."""
    moved = 0
    for spid, pid in c.execute("SELECT spid, playerid FROM players WHERE leagueid = 78 AND nationalteamid = teamid "
                               "AND (spid < 1000000 OR spid / 1000000 = 88)").fetchall():
        club = club_in(careers.get(pid, []))
        if club in OPEN_CLUBS:
            c.execute("UPDATE players SET teamid = ?, leagueid = ? WHERE spid = ?", (club, OPEN_CLUBS[club][0], spid))
            moved += 1
    return moved


def open_clubs_back(c):
    """open_clubs_move undone: those cards back on their national team."""
    c.execute(f"UPDATE players SET teamid = nationalteamid, leagueid = 78 WHERE teamid IN ({','.join(map(str, OPEN_CLUBS))}) "
              "AND (spid < 1000000 OR spid / 1000000 = 88)")


# FO3's own seasons -> the club season (year/year+1) of their cards: the year whose careers' clubs
# match FO3's club cards best (75-95%; 16 and '17 ~55%, newer moves missing in the careers)
SEASON_YEAR = {6: 2006, 7: 2007, 8: 2008, 9: 2009, 10: 2010, 11: 2011, 14: 2014, 15: 2015, 16: 2016, 0: 2017, 88: 2017,
               22: 2017, 23: 2017, 24: 2017, 35: 2015, 47: 2009, 53: 2014, 63: 2006, 65: 2006, 67: 2007, 69: 2010, 77: 2013}
ITALY = (31, 32)
ROW_MOVED = os.path.join(ROOT, "gamedata", "row_moved.json")  # [spid] rest_of_world_move took off Rest of the World


def rest_of_world_move(c, careers):
    """FO3's own cards on Rest of the World whose player was at an Italian club of the client (user:
    "pemain2 rest of world Serie A kembalikan ke klubnya yang sesuai") go to it: the career's club
    in the card's season and the club joined in its playerjointeamdate year must both be that club
    (right for 90% of FO3's own Italian club cards; the misses are mostly loans). Returns how many."""
    ita = dict(c.execute(f"SELECT teamid, leagueid FROM players WHERE leagueid IN {ITALY} "
                         "AND (spid < 1000000 OR spid / 1000000 = 88)").fetchall())  # the client's league
    ita.update({t: lg for t, (lg, _) in OPEN_CLUBS.items()})
    moved = set(json.load(open(ROW_MOVED)) if os.path.isfile(ROW_MOVED) else [])
    n = 0
    for spid, pid, joined in c.execute("SELECT spid, playerid, playerjointeamdate FROM players WHERE teamid = ?",
                                       (REST_OF_WORLD[0],)).fetchall():
        year, m, career = SEASON_YEAR.get(spid // 1000000), re.search(r"\d{4}", joined or ""), careers.get(pid, [])
        club = club_in(career, year) if year and m else None
        if club in ita and club == club_joined(career, int(m[0])):
            c.execute("UPDATE players SET teamid = ?, leagueid = ? WHERE spid = ?", (club, ita[club], spid))
            moved.add(spid)
            n += 1
    json.dump(sorted(moved), open(ROW_MOVED, "w"))
    return n


def rest_of_world_back(c):
    """rest_of_world_move undone."""
    if os.path.isfile(ROW_MOVED):
        c.executemany("UPDATE players SET teamid = ?, leagueid = ? WHERE spid = ?",
                      [(*REST_OF_WORLD, s) for s in json.load(open(ROW_MOVED))])
        os.remove(ROW_MOVED)


# The admin page's Database tab (issue #38) edits cards in gamedata/fifadb.sqlite and keeps each
# edited card's values in CARD_EDITS ({spid: {"orig": {column: value}, "new": {column: value}}}).
# Cards of the seasons build makes again get their new values back here.
CARD_EDITS = os.path.join(ROOT, "gamedata", "card_edits.json")


def apply_card_edits(c, seasons):
    """Put the admin page's edits back on the rebuilt cards of seasons (c: row_factory Row); the
    rebuilt values become the ones its Kembalikan button restores. Returns how many cards."""
    try:
        with open(CARD_EDITS) as f:
            edits = json.load(f)
    except (OSError, ValueError):
        return 0
    n = 0
    for spid, e in edits.items():
        r = c.execute("SELECT * FROM players WHERE spid = ?", (int(spid),)).fetchone()
        new = {k: v for k, v in e.get("new", {}).items() if r is not None and k in r.keys()}
        if not new or int(spid) // 1000000 not in seasons:
            continue
        e["orig"] = {k: r[k] for k in new}
        c.execute(f"UPDATE players SET {', '.join(f'{k} = ?' for k in new)} WHERE spid = ?", [*new.values(), int(spid)])
        n += 1
    with open(CARD_EDITS, "w") as f:
        json.dump(edits, f, indent=1)
    return n


# --- WC18: the 2018 World Cup's best 300 squad players ----------------------------------------
# User: "research untuk kartu wc18", then "Pemain top 300 saja wc18, grade 1 = nilai akhir
# turnamen dikurangi 5, pemain yang ada di skuad saja tidak usah legend". FO3 and FO4 never had
# such cards. futwiz keeps FIFA 18's World Cup mode: EA's World Cup ratings as they stood when
# the tournament ended (Hazard 96, Messi and Modric 95, Mbappe 94). Its list rows carry EA's
# player and nation ids, which are FO3's (a card is season * 1e6 + EA id), its card pages the
# stats. The squads are Wikipedia's 32 x 23, the clubs theirs at the time. The WC18_TOP best of
# the players in a squad (icons left out; ties at the cut go to the higher face stats) become
# season 78, next to FO3's own WC14 (77), with an "18" badge in the green of FO3's WC06 / WC10 /
# WC14 ones. A card's stats are the World Cup page's, all moved by one step so that at +1 it
# shows its World Cup rating + WC18_PLUS1 (the step is about -10: +1 adds PLUS1 to each stat).
# The World Cup pages leave out the physical stats (taken from the player's FIFA 18 card, moved
# by the World Cup PHY face stat) and a keeper's everything but his six face stats (DIV HAN KIC
# REF SPD POS: the GK skills; the rest from his FIFA 18 card, its speed moved by SPD). Face, weak
# foot, skills and reputation stay as on the player's own FO3 card; his Karakteristik (traits) are
# that card's and his FIFA 18 card's together (user: "specialist sama traits tiap pemain jangan
# lupa"), his Style (badges, the specialities) follows his stats, as on every card build adds.
WC18 = 78
SEASON_NAMES[WC18] = "WC18"
WC18_CARDS = os.path.join(ROOT, "gamedata", "wc18_cards.json")
WC18_SITE = "https://www.futwiz.com"
WC18_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FO3 private server season sync"
WC18_SQUADS = ("https://en.wikipedia.org/w/api.php?action=parse&page=2018_FIFA_World_Cup_squads"
               "&prop=wikitext&format=json&formatversion=2")
WC18_TOP = 300
WC18_PLUS1 = -5  # a card at +1 shows its World Cup rating - 5
# Wikipedia's squad headings -> EA nation ids (futwiz's flags, fifadb's nationality)
WC18_NATIONS = {"Egypt": 111, "Russia": 40, "Saudi Arabia": 183, "Uruguay": 60, "Iran": 161, "Morocco": 129,
                "Portugal": 38, "Spain": 45, "Australia": 195, "Denmark": 13, "France": 18, "Peru": 59,
                "Argentina": 52, "Croatia": 10, "Iceland": 24, "Nigeria": 133, "Brazil": 54, "Costa Rica": 72,
                "Serbia": 51, "Switzerland": 47, "Germany": 21, "Mexico": 83, "South Korea": 167, "Sweden": 46,
                "Belgium": 7, "England": 14, "Panama": 87, "Tunisia": 145, "Colombia": 56, "Japan": 163,
                "Poland": 37, "Senegal": 136}
_WC18_CARD = re.compile(r'<a href="/fifa18/worldcup/player/([^/"]+)/(\d+)"><div class="card-19-pack '
                        r'card-19-pack-([a-z-]+)\s*">(.*?)</a>', re.S)
_WC18_VERSION = re.compile(r'<li><a href="(/fifa18/player/[^"]+)"><img src="[^"]*/badges/(\d+)\.png"[^>]*>\s*'
                           r'(\d+)\s+(\w+)</a>\s*(?:<span class="pull-right">([^<]*)</span>)?')
# a card page's labels -> fifadb columns (Composure has none); "Pac".."Phy" head a FIFA 18 page's
# groups (a keeper's: DIV HAN KIC REF SPD POS)
WC18_LABELS = {"Acceleration": "acceleration", "Sprint Speed": "sprintspeed", "Positioning": "positioning",
               "Finishing": "finishing", "Long Shots": "longshots", "Penalties": "penalties", "Shot Power": "shotpower",
               "Volleys": "volleys", "Crossing": "crossing", "Curve": "curve", "FK. Acc.": "freekickaccuracy",
               "FK. Accuracy": "freekickaccuracy", "Long Pass": "longpassing", "Long Passing": "longpassing",
               "Short Pass": "shortpassing", "Short Passing": "shortpassing", "Vision": "vision", "Agility": "agility",
               "Balance": "balance", "Ball Control": "ballcontrol", "Dribbling": "dribbling", "Reactions": "reactions",
               "Heading Acc.": "headingaccuracy", "Interceptions": "interceptions", "Marking": "marking",
               "Stand Tackle": "standingtackle", "Slide Tackle": "slidingtackle", "Sliding Tackle": "slidingtackle",
               "Aggression": "aggression", "Jumping": "jumping", "Stamina": "stamina", "Strength": "strength"}
WC18_FACE = ("Pac", "Sho", "Pas", "Dri", "Def", "Phy")
WC18_GK = {"DIV": "gkdiving", "HAN": "gkhandling", "KIC": "gkkicking", "REF": "gkreflexes", "POS": "gkpositioning"}
WC18_PHYSICAL = ("aggression", "jumping", "stamina", "strength")
# words a club's name may carry or not (Deportivo La Coruna / RC Deportivo); not CP: Sporting CP is
# not FO3's R. Sporting (Gijon)
WC18_TEAM_WORDS = {"fc", "cf", "afc", "sc", "ac", "fk", "sk", "club", "de", "the", "rc", "rcd", "cd", "ud", "sd", "ca",
                   "sl", "ss", "ssc", "us", "as", "sv", "vfl", "vfb", "tsg", "pfc", "nk", "bk"}
# a FIFA 18 card's traits (futwiz's names, "Shooting - " / "(CPU AI Only)" cut) -> FO3's trait1 ones
# (Karakteristik). "Long Thrower" is a keeper's (Neuer, De Gea), "Long Throw" an outfield player's
# (Walker, Trippier). FUT's newer ones (Technical Dribbler, Chip Shot, Corner Specialist, Sweeper
# Keeper, Ultimate Professional, Target Forward, Backs Into Player, One Club Player, Takes Finesse
# Style Free Kicks) have no trait1 bit.
WC18_TRAITS = {"Inflexible": "inflexibility", "Long Throw": "long-throw-in", "Long Throw-in": "long-throw-in",
               "Takes Powerful Driven Free Kicks": "takes-powerful-driven-free-kicks", "Power Free-Kick": "takes-powerful-driven-free-kicks",
               "Diver": "diver", "Injury Prone": "injury-prone", "Solid Player": "solid-player", "Injury Free": "solid-player",
               "Avoids Using Weaker Foot": "avoids-using-weaker-foot", "Dives Into Tackles": "dives-into-tackles",
               "Tries To Beat Defensive Line": "tries-to-beat-offside-trap", "Beat Offside Trap": "tries-to-beat-offside-trap",
               "Selfish": "selfish", "Leadership": "leadership", "Argues With Officials": "argues-with-officials",
               "Early Crosser": "early-crosser", "Finesse Shot": "finesse-shot", "Flair": "flair", "Long Passer": "long-passer",
               "Long Shot Taker": "long-shot-taker", "Speed Dribbler": "speed-dribbler", "Playmaker": "play-maker",
               "Pushes Up For Corners": "pushes-up-for-corners", "GK Up For Corners": "pushes-up-for-corners", "Puncher": "puncher",
               "Long Thrower": "gk-long-thrower", "GK Long Throw": "gk-long-thrower", "Power Header": "power-header",
               "GK One On One": "gk-one-on-ones", "Giant Throw-in": "giant-throw-in", "Outside Foot Shot": "outside-foot-shot",
               "Crowd Favourite": "crowd-favourite", "Swerve Pass": "swerve", "Second Wind": "second-wind",
               "Acrobatic Clearance": "acrobatic-clearance"}
WORKRATE_WORD = {"High": "high", "Med": "medium", "Low": "low"}


def wc18_list(fetch):
    """{fid: card} of futwiz's FIFA 18 World Cup players, 25 a page: EA id (the face picture's),
    EA nation id (the flag's), short name, rating, position, kind (gold / silver / bronze / icon)
    and the six face stats."""
    out, page = {}, 0
    while True:
        rows = _WC18_CARD.findall(fetch(f"/fifa18/worldcup/players?page={page}"))
        for slug, fid, kind, body in rows:
            get = lambda rx: (re.search(rx, body, re.S) or [None, None])[1]
            out[int(fid)] = {"fid": int(fid), "slug": slug, "kind": kind, "eaid": int(get(r"/faces/(\d+)\.png")),
                             "nation": int(get(r"/flags/(\d+)\.png")), "name": get(r'pack-name">\s*(.*?)\s*<'),
                             "rating": int(get(r'pack-rating">\s*(\d+)')), "pos": get(r'pack-position">\s*(\w+)'),
                             "face": dict(zip(re.findall(r'attlabel\d">\s*(\w+)', body),
                                              map(int, re.findall(r'attnum\d">\s*(\d+)', body))))}
        if not rows:
            return out
        page += 1


def wc18_page(html):
    """A futwiz card page: {column: value} of its attributes (a label's last value: a World Cup
    page heads its dribbling group with the DRI face stat), the FIFA 18 page's face stats, height,
    weight, foot, workrates ("Med/Med") and the player's FIFA 18 versions [(link, club badge id,
    rating, position, label)], a version with no label being a plain card."""
    i = html.find("WORKRATES")
    ends = [k for k in (html.find(m, i) for m in ("Traits", "World Cup Fixtures", "Player Chemistry", "pchart")) if k > 0]
    toks = [t.strip() for t in re.sub(r"<[^>]+>", "|", html[i:min(ends or [len(html)])]).split("|") if t.strip()]
    stats, face = {}, {}
    for a, b in zip(toks, toks[1:]):
        if b.isdigit() and a in WC18_LABELS:
            stats[WC18_LABELS[a]] = int(b)
        elif b.isdigit() and a in WC18_FACE:
            face[a] = int(b)
    head = [t.strip() for t in re.sub(r"<[^>]+>", "|", html[max(0, i - 3000):i + 9]).split("|") if t.strip()]
    body = re.search(r"(\d{3})cm.*?(\d{2,3})kg", html[:i], re.S)
    listed = re.search(r"Traits:\s*([^<]*)", html)  # a FIFA 18 page: "Traits: Shooting - Finesse Shot, Playmaker"
    traits = [re.sub(r"\s*\(CPU AI Only\)", "", t.split(" - ")[-1]).strip() for t in (listed[1] if listed else "").split(",")]
    return {"stats": stats, "face": face, "height": int(body[1]) if body else None, "weight": int(body[2]) if body else None,
            "foot": toks[1] if toks[1:2] in (["Left"], ["Right"]) else None,
            "workrate": head[-2] if len(head) > 1 and "/" in head[-2] else "Med/Med", "traits": [t for t in traits if t],
            "versions": [(link, int(b), int(r), p, (lab or "").strip()) for link, b, r, p, lab in _WC18_VERSION.findall(html)]}


def wc18_squads(fetch):
    """[{nation, name, dob [y, m, d], clubs (the club's article name and the name shown)}] of
    Wikipedia's 2018 World Cup squads."""
    text = json.loads(fetch(WC18_SQUADS))["parse"]["wikitext"]
    links = lambda s: [x for pair in re.findall(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]", s) for x in pair if x] or [s.strip()]
    out, nation = [], None
    for line in text.splitlines():
        m = re.match(r"===\s*(.+?)\s*===\s*$", line)
        if m:
            nation = m.group(1)
        elif line.startswith("{{nat fs g player") and nation in WC18_NATIONS:
            field = lambda k: (re.search(r"\|" + k + r"=(.*?)(?=\|[a-z]+=|\}\}\s*$)", line) or [None, ""])[1]
            dob = re.search(r"[Bb]irth date and age2\|(?:df=y\|)?2018\|6\|14\|(\d+)\|(\d+)\|(\d+)", line)
            out.append({"nation": nation, "name": links(field("name"))[-1], "clubs": links(field("club")),
                        "dob": [int(x) for x in dob.groups()]})
    return out


def wc18_pick(cards, squads, fo3):
    """(the WC18_TOP best futwiz cards of players in a squad, each with "squad": its entry; how
    many squad players have a card). Pairs by name, and by birth date where FO3 knows the EA id
    (fo3 = {pid: ((y, m, d), full name)}), best pair first, one card a squad place."""
    by_nation = collections.defaultdict(list)
    for i, s in enumerate(squads):
        by_nation[WC18_NATIONS[s["nation"]]].append(i)
    pairs = []
    for card in cards.values():
        if card["kind"] == "icon":
            continue
        born, full = fo3.get(card["eaid"], (None, ""))
        for i in by_nation.get(card["nation"], ()):
            s = squads[i]
            name = max(_same_name(s["name"], full), _same_name(s["name"], card["slug"].replace("-", " ")))
            same_day = born == tuple(s["dob"])
            if (same_day and name >= 0.6) or name >= 1.6:
                pairs.append((name + 2 * same_day, card["fid"], i))
    taken, used, out = set(), set(), []
    for _, fid, i in sorted(pairs, reverse=True):
        if fid not in taken and i not in used:
            taken.add(fid)
            used.add(i)
            out.append(dict(cards[fid], squad=squads[i]))
    out.sort(key=lambda card: (-card["rating"], -sum(card["face"].values()), card["fid"]))
    return out[:WC18_TOP], len(out)


def _team_like(a, b):
    """0..1: likeness of two club names, word by word: 1 the same words, 0.95 one name's words
    inside the other's (Inter Milan / Inter), else the shorter name's words' best likeness, halved
    when one of them has no counterpart (Bayern Munich / FC Bayern Munchen 0.88, Manchester City /
    Manchester United 0.35)."""
    wa, wb = ([w for w in _key(s).split() if w not in WC18_TEAM_WORDS] for s in (a, b))
    if not wa or not wb:
        return 0.0
    if wa == wb:
        return 1.0
    if set(wa) <= set(wb) or set(wb) <= set(wa):
        return 0.95
    short, other = sorted((wa, wb), key=len)
    near = [max(difflib.SequenceMatcher(None, x, y).ratio() for y in other) for x in short]
    return sum(near) / len(near) * (0.5 if min(near) < 0.5 else 1)


def wc18_team(clubs, candidates, teams):
    """FO3 team id of a player's 2018 club (Wikipedia's names for it): the best match among the
    clubs his FIFA 18 cards and his FO3 card show (Bayern Munich / FC Bayern Munchen), else a club
    of the same name (its words in the other's: Inter Milan / Inter) among all FO3 clubs (Porto is
    not Port Vale, Al Ain not Al Batin); None = not in FO3 (build puts him in Rest of the World).
    teams = {teamid: (name, leagueid)}, national teams (league 78) left out."""
    teams = {t: v for t, v in teams.items() if v[1] != 78}
    score = lambda t: max(_team_like(n, teams[t][0]) for n in clubs)
    near = max((t for t in candidates if t in teams), key=score, default=None)
    if near is not None and score(near) >= 0.6:
        return near
    best = max(teams, key=score, default=None)
    return best if best is not None and score(best) >= 0.95 else None


def _fo3_teams(game):
    """{teamid: (name, leagueid)} of FO3's clubs (a club with cards), from the client's eng.db."""
    import bigdir
    data0 = os.path.join(BACKUP, "data_0.big")
    if not os.path.isfile(data0):
        data0 = os.path.join(game, "data", "data_0.big")
    tmp = os.path.join(tempfile.gettempdir(), "fo4_season_teams.sqlite")
    open(tmp, "wb").write(_plain(bigdir.read(data0, "data/db2/eng.db.db2")))
    with closing(sqlite3.connect(tmp)) as c:
        teams = {t: (n, lg) for t, n, lg in c.execute("SELECT teamid, teamname, leagueid FROM team")}
    os.remove(tmp)
    with closing(sqlite3.connect(FIFADB)) as c:
        have = {r[0] for r in c.execute("SELECT DISTINCT teamid FROM players")}
    return {t: v for t, v in teams.items() if t in have}


def cmd_wc18(a):
    fetch = fetcher(a.cache, WC18_SITE, WC18_AGENT)
    squads = wc18_squads(fetch)
    cards = wc18_list(fetch)
    with closing(sqlite3.connect(FIFADB)) as c:
        names = dict(c.execute("SELECT playerid, full_eng FROM nameseng"))
        fo3, own_team = {}, {}
        for pid, born, team, spid in c.execute("SELECT playerid, birthdate, teamid, spid FROM players ORDER BY spid"):
            m = re.match(r"(\d+)/(\d+)/(\d+)", born or "")
            if m and pid not in fo3:
                fo3[pid] = ((int(m[3]), int(m[1]), int(m[2])), names.get(pid, ""))
            if spid // 1000000 == 88:
                own_team[pid] = team
    top, have = wc18_pick(cards, squads, fo3)
    print(f"{len(cards)} World Cup cards, {len(squads)} squad players ({have} with a card); "
          f"the best {len(top)}: {top[0]['rating']}..{top[-1]['rating']}", flush=True)
    teams = _fo3_teams(a.game)
    out = {}
    for n, card in enumerate(top, 1):
        wc = wc18_page(fetch(f"/fifa18/worldcup/player/{card['slug']}/{card['fid']}"))
        # his FIFA 18 card: the plain version rated lowest, the first made of those (a later one at
        # the same rating is a move after the World Cup, a page without stats: Iniesta's Vissel Kobe)
        first = min((v for v in wc["versions"] if not v[4]), key=lambda v: (v[2], int(v[0].rsplit("/", 1)[1])), default=None)
        fifa18 = wc18_page(fetch(first[0])) if first else None
        pos = card["pos"] or (first[3] if first else None)
        squad = card["squad"]
        team = wc18_team(squad["clubs"], {v[1] for v in wc["versions"]} | {own_team.get(card["eaid"])}, teams)
        out[str(card["eaid"])] = {
            "fid": card["fid"], "name": card["name"], "full": squad["name"], "dob": squad["dob"], "nation": card["nation"],
            "rating": card["rating"], "pos": pos, "face": card["face"], "stats": wc["stats"], "height": wc["height"],
            "weight": wc["weight"], "foot": wc["foot"], "workrate": wc["workrate"], "clubs": squad["clubs"],
            "team": team, "league": teams[team][1] if team else None,
            "fifa18": {"stats": fifa18["stats"], "face": fifa18["face"], "traits": fifa18["traits"]} if fifa18 else None}
        if n % 25 == 0:
            print(f"{n}/{len(top)} cards read", flush=True)
    tmp = WC18_CARDS + ".tmp"
    json.dump(out, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, WC18_CARDS)
    lost = [plain(v["full"]) for v in out.values() if v["team"] is None]  # ASCII: a cp1252 console
    print(f"{len(out)} WC18 cards -> {WC18_CARDS}; clubs FO3 lacks (Rest of the World): {len(lost)} {lost}")


def wc18_stats(w, own):
    """EA-scale stats of a WC18 card (w = its wc18_cards.json entry; own = his FO3 card or None):
    the World Cup page's, the FIFA 18 card's for the rest (physical stats moved by the World Cup
    PHY, a keeper's speed by SPD), a keeper's GK skills from the face stats; what neither has from
    his FO3 card one EA step up (FO3 = EA - 6 on average), else a keeper's reactions from his GK
    skills, an outfield player's physical stats from PHY, the rest plain (50; GK skills 10)."""
    keeper = w["pos"] == "GK"
    f18 = w.get("fifa18") or {"stats": {}, "face": {}}
    if own:
        out = {k: own[k] + 6 for k in STATS}
    elif keeper:
        out = {k: 25 for k in STATS}
        out["reactions"] = sum(w["face"].get(k, 50) for k in WC18_GK) // len(WC18_GK) - 5
    else:
        out = {k: 10 if k.startswith("gk") else 50 for k in STATS}
        out.update({k: w["face"].get("PHY", 50) for k in WC18_PHYSICAL})
    out.update(f18["stats"])
    if keeper:
        out.update({col: w["face"][k] for k, col in WC18_GK.items() if k in w["face"]})
        if "SPD" in w["face"] and "Def" in f18["face"]:
            for k in ("acceleration", "sprintspeed"):
                out[k] += w["face"]["SPD"] - f18["face"]["Def"]
    else:
        out.update(w["stats"])
        if "PHY" in w["face"] and "Phy" in f18["face"]:
            for k in WC18_PHYSICAL:
                out[k] += w["face"]["PHY"] - f18["face"]["Phy"]
    return out


def wc18_shift(stats, pos, target):
    """stats all moved by the one step that makes the client's rating at position code pos
    (Enums.calcPosOvr, http_config.ROLE_WEIGHTS) come out at target, kept within 1..99."""
    sys.path.insert(0, ROOT)
    from http_config import ROLE_WEIGHTS, POS_ROLE
    weights = ROLE_WEIGHTS[POS_ROLE[pos]]
    moved = lambda s: {k: max(1, min(99, v + s)) for k, v in stats.items()}
    rate = lambda st: sum(n * st[k] for k, n in weights.items()) // 100
    return moved(min(range(-30, 11), key=lambda s: (abs(rate(moved(s)) - target), abs(s + 10))))


def wc18_build_cards(best):
    """WC18's cards in fetch's shape for cmd_build (forced pid = EA id when FO3 has a card of him;
    keep = what stays as on that card), from gamedata/wc18_cards.json."""
    if not os.path.exists(WC18_CARDS):
        return {}
    tones = collections.defaultdict(list)  # nation -> its 2017 players' skin tones (a new player's head)
    for rank, r in best.values():
        if r["spid"] // 1000000 == 88:
            tones[r["nationality"]].append(r["skintonecode"])
    out = {}
    for eaid, w in json.load(open(WC18_CARDS, encoding="utf-8")).items():
        own = dict(best[int(eaid)][1]) if int(eaid) in best else None
        code = POS.get(w["pos"], (own or {}).get("preferredposition1", POS["CM"]))  # a list row without one: his own
        stats = wc18_shift(wc18_stats(w, own), code, w["rating"] + WC18_PLUS1 - PLUS1)
        others = [p for p in ((own or {}).get(f"preferredposition{i}", -1) for i in range(1, 5)) if p >= 0 and p != code]
        poss = ([code] + others + [-1] * 3)[:4]
        keep = {f"preferredposition{i + 1}": p for i, p in enumerate(poss)}
        if own:
            keep.update({k: own[k] for k in ("trait2", "internationalrep", "nationalteamid", "weakfootabilitytypecode",
                                              "skillmoves", "contractvaliduntil", "playerjointeamdate", "historyopen")})
        else:
            keep.update(contractvaliduntil=2019, playerjointeamdate="Fri, 01 Jun 2018 00:00:00 GMT")
        # Karakteristik: his FO3 card's and his FIFA 18 card's (user: "traits tiap pemain jangan lupa");
        # Style (badges) build works out from the stats, as for every added card
        keep["trait1"] = (own or {}).get("trait1", 0) | sum(
            {1 << FO3_TRAITS.index(WC18_TRAITS[t]) for t in (w.get("fifa18") or {}).get("traits", []) if t in WC18_TRAITS})
        y, m, d = w["dob"]
        if own:  # FO3's own birth date (it can differ from Wikipedia's by a day)
            mm, dd, yy = re.match(r"(\d+)/(\d+)/(\d+)", own["birthdate"]).groups()
            y, m, d = int(yy), int(mm), int(dd)
        words = plain(w["full"]).split()
        att, dfn = (w["workrate"] or "Med/Med").split("/")
        tone = sorted(tones[w["nation"]])[len(tones[w["nation"]]) // 2] if tones[w["nation"]] else 2
        out[f"wc18-{eaid}"] = {
            "season": WC18, "pid": int(eaid) if own else None, "attr": stats, "keep": keep,
            "db": {"name": w["full"], "name_short": f"{words[0][0]}. {' '.join(words[1:])}" if len(words) > 1 else w["full"],
                   "birthdate": f"{d}.{m}.{y}", "height": w["height"] or (own or {}).get("height", 180),
                   "weight": w["weight"] or (own or {}).get("weight", 75), "pos1": w["pos"],
                   "foot_pref": (w["foot"] or "Right").lower(), "foot_weak": 3, "skill_level": 2,
                   "workrate_att": WORKRATE_WORD.get(att, "medium"), "workrate_def": WORKRATE_WORD.get(dfn, "medium"),
                   "current_ovr": w["rating"] + WC18_PLUS1 - PLUS1, "nation_id": w["nation"],
                   "team_id": w["team"] or 0, "league_id": w["league"] or 0, "skintonecode": tone}}
    return out


# --- season icons: data/ui/components/Label.big ------------------------------------------------
# Label.gfx (CFX = zlib SWF) holds the two SeasonIcon clips: sprite 144 "seasonIconBig" and sprite
# 735, one frame per season labelled "<code>season" (SeasonIcon does gotoAndStop(season +
# "season")). Each frame puts a shape at depth 1 whose bitmap fill is a DefineSubImage (GFx tag
# 1008: id, image 0, x1, y1, x2, y2) of the DXT5 atlas Label_I1.dds. A new season gets its FO4
# icon drawn into free atlas space, a copy of the '17 shapes pointing at it, and one more frame
# in both clips.
LABEL_BIG = "data/ui/components/Label.big"
LABEL_GFX, LABEL_DDS = "data/ui/components/Label.gfx", "data/ui/components/Label_I1.dds"
ICON_SPRITES = (144, 735)
ICON_FREE_Y = 640  # the atlas is empty from about y 580 down
DEFINES = {2, 6, 7, 10, 11, 14, 20, 21, 22, 32, 33, 34, 35, 36, 37, 39, 46, 48, 60, 75, 83, 84, 1008}


def _tags(b, p, end):
    out = []
    while p < end:
        h = int.from_bytes(b[p:p + 2], "little"); p += 2
        code, ln = h >> 6, h & 0x3F
        if ln == 0x3F:
            ln = int.from_bytes(b[p:p + 4], "little"); p += 4
        out.append([code, bytes(b[p:p + ln])])
        p += ln
        if code == 0:
            break
    return out


def _join(tags):
    out = bytearray()
    for code, data in tags:
        if len(data) < 0x3F and code not in (6, 20, 21, 35, 36):
            out += ((code << 6) | len(data)).to_bytes(2, "little")
        else:
            out += ((code << 6) | 0x3F).to_bytes(2, "little") + len(data).to_bytes(4, "little")
        out += data
    return bytes(out)


class _Bits:
    def __init__(self, b, p):
        self.b, self.bit = b, p * 8

    def u(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | (self.b[self.bit >> 3] >> (7 - (self.bit & 7)) & 1)
            self.bit += 1
        return v

    def end(self):
        return (self.bit + 7) // 8


def _fills(d):
    """Byte offsets of the bitmap ids of a DefineShape payload's bitmap fill styles."""
    r = _Bits(d, 2); r.u(4 * r.u(5)); p = r.end()  # id, bounds
    out, n, p = [], d[p], p + 1
    for _ in range(n):
        assert 0x40 <= d[p] <= 0x43, f"fill style {d[p]:#x}"
        out.append(p + 1)
        m = _Bits(d, p + 3)
        for _ in range(2):  # scale, rotate
            if m.u(1):
                m.u(2 * m.u(5))
        m.u(2 * m.u(5))  # translate
        p = m.end()
    return out


def _bitmaps(shape):
    return [int.from_bytes(shape[p:p + 2], "little") for p in _fills(shape)]


def _shape_copy(data, new_id, bitmap):
    """A DefineShape payload with a new id and every real bitmap fill (not 0xFFFF) on `bitmap`."""
    d = bytearray(data)
    d[0:2] = new_id.to_bytes(2, "little")
    for p in _fills(d):
        if d[p:p + 2] != b"\xff\xff":
            d[p:p + 2] = bitmap.to_bytes(2, "little")
    return bytes(d)


def _bc3(px):
    """One DXT5 block from 16 RGBA pixels (row-major): min/max endpoints, nearest indices."""
    al = [p[3] for p in px]
    a0, a1 = max(al), min(al)
    out = bytearray([a0, a1])
    bits = 0
    if a0 > a1:
        pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
        for i, a in enumerate(al):
            bits |= min(range(8), key=lambda k: abs(pal[k] - a)) << (3 * i)
    out += bits.to_bytes(6, "little")
    seen = [p for p in px if p[3]] or px
    # endpoints: the two most different pixels (min/max per channel makes colours none of them has)
    hi, lo = max(((p, q) for p in seen for q in seen), key=lambda pq: sum((pq[0][c] - pq[1][c]) ** 2 for c in range(3)))
    pack = lambda c: (c[0] >> 3) << 11 | (c[1] >> 2) << 5 | c[2] >> 3
    c0, c1 = pack(hi), pack(lo)
    unpack = lambda v: ((v >> 11 & 31) * 255 // 31, (v >> 5 & 63) * 255 // 63, (v & 31) * 255 // 31)
    bits = 0
    if c0 != c1:
        if c0 < c1:
            c0, c1 = c1, c0
        e0, e1 = unpack(c0), unpack(c1)
        pal = [e0, e1, tuple((2 * x + y) // 3 for x, y in zip(e0, e1)), tuple((x + 2 * y) // 3 for x, y in zip(e0, e1))]
        for i, p in enumerate(px):
            bits |= min(range(4), key=lambda k: sum((pal[k][c] - p[c]) ** 2 for c in range(3))) << (2 * i)
    return bytes(out + c0.to_bytes(2, "little") + c1.to_bytes(2, "little") + bits.to_bytes(4, "little"))


def _draw(dds, img, x, y):
    """Encode an RGBA PIL image into the DXT5 atlas at (x, y) (multiples of 4, free space)."""
    w = int.from_bytes(dds[16:20], "little")
    for by in range(0, img.height, 4):
        for bx in range(0, img.width, 4):
            px = [img.getpixel((bx + i, by + j)) if bx + i < img.width and by + j < img.height else (0, 0, 0, 0)
                  for j in range(4) for i in range(4)]
            at = 128 + (((y + by) // 4) * (w // 4) + (x + bx) // 4) * 16
            dds[at:at + 16] = _bc3(px)


def _frame_icons(gfx, dds, labels):
    """{label: RGBA PIL image} of SeasonIcon frames of Label.gfx (sprite 144, the big badges)."""
    import io, zlib
    from PIL import Image
    atlas = Image.open(io.BytesIO(bytes(dds))).convert("RGBA")
    body = zlib.decompress(gfx[8:])
    r = _Bits(body, 0); r.u(4 * r.u(5)); start = r.end() + 4
    tags = _tags(body, start, len(body))
    defs = {int.from_bytes(d[:2], "little"): (c, d) for c, d in tags if c in DEFINES}
    sprite = next(d for c, d in tags if c == 39 and int.from_bytes(d[:2], "little") == ICON_SPRITES[0])
    inner = _tags(sprite, 4, len(sprite))
    out = {}
    for i, (c, d) in enumerate(inner):
        if c == 43 and d[:-1].decode() in labels:
            place = next(pd for pc, pd in inner[i:] if pc == 26)
            shape = defs[int.from_bytes(place[3:5], "little")][1]
            ref = next(defs[b][1] for b in _bitmaps(shape) if b in defs and defs[b][0] == 1008)
            out[d[:-1].decode()] = atlas.crop(tuple(int.from_bytes(ref[k:k + 2], "little") for k in (4, 6, 8, 10)))
    return out


def wc18_icon(gfx, dds):
    """A WC18 badge made of FO3's own: its WC10 one ("10" in the World Cup badges' yellow to green,
    frame 47season) with the 0 swapped for the white 8 of the red '08 badge (67season), recoloured
    row by row as the 1."""
    from PIL import Image
    f = _frame_icons(gfx, dds, ("47season", "67season"))
    wc, red = f["47season"], f["67season"]
    px, out = wc.load(), wc.copy()
    o = out.load()
    w, h = wc.size
    digit = lambda p: p[3] > 0 and p[0] + p[1] > p[2] + 90  # yellow / green on the navy box

    def glyphs(cols):  # runs of adjacent columns, 4 wide or more (the box's edge is not one)
        runs = []
        for x in cols:
            if runs and x == runs[-1][-1] + 1:
                runs[-1].append(x)
            else:
                runs.append([x])
        return [g for g in runs if len(g) >= 4]
    one, zero = glyphs([x for x in range(4, w - 4) if any(digit(px[x, y]) for y in range(6, h - 7))])[:2]
    rows = [y for y in range(4, h - 5) if any(digit(px[x, y]) for x in zero)]
    for y in range(rows[0] - 1, rows[-1] + 2):  # the 0 out: the box's colour of that row
        for x in range(zero[0] - 1, zero[-1] + 2):
            o[x, y] = px[one[-1] + 1, y]
    rp, (rw, rh) = red.load(), red.size
    white = lambda p: p[3] > 0 and min(p[:3]) > 150
    eight = glyphs([x for x in range(4, rw - 4) if any(white(rp[x, y]) for y in range(4, rh - 4))])[-1]
    erows = [y for y in range(4, rh - 4) if any(white(rp[x, y]) for x in eight)]
    mask = Image.new("L", (len(eight), len(erows)))
    for x in eight:
        for y in erows:
            mask.putpixel((x - eight[0], y - erows[0]), max(0, min(255, (min(rp[x, y][:3]) - 110) * 3)))
    mask = mask.resize((len(zero), rows[-1] - rows[0] + 1), Image.LANCZOS)
    for y in rows:
        ink = next((px[x, y] for x in one if digit(px[x, y])), (200, 220, 40, 255))
        for x in range(mask.width):
            a = mask.getpixel((x, y - rows[0])) / 255
            if a:
                b = o[zero[0] + x, y]
                o[zero[0] + x, y] = tuple(int(ink[k] * a + b[k] * (1 - a)) for k in range(3)) + (255,)
    return out


def label_with_icons(gfx, dds, icons):
    """(gfx, dds) of Label.big with one more SeasonIcon frame per {season: PIL RGBA icon}."""
    import zlib
    from PIL import Image
    assert gfx[:3] == b"CFX", gfx[:3]
    body = zlib.decompress(gfx[8:])
    r = _Bits(body, 0); r.u(4 * r.u(5)); start = r.end() + 4
    tags = _tags(body, start, len(body))
    atlas = Image.open(__import__("io").BytesIO(bytes(dds))).convert("RGBA")
    dds = bytearray(dds)
    defs = {}
    for code, data in tags:
        if code in DEFINES:
            defs[int.from_bytes(data[:2], "little")] = (code, data)
    next_id = max(defs) + 1
    sprites = {sid: next(t for t in tags if t[0] == 39 and int.from_bytes(t[1][:2], "little") == sid) for sid in ICON_SPRITES}
    new_defs, x = [], 0
    for season, icon in sorted(icons.items()):
        label = f"{season}season".encode() + b"\0"
        if any(c == 43 and d == label for sid in ICON_SPRITES for c, d in _tags(sprites[sid][1], 4, len(sprites[sid][1]))):
            continue  # a game patched before (the season download's Label.big): its icon is there
        for sid in ICON_SPRITES:
            inner = _tags(sprites[sid][1], 4, len(sprites[sid][1]))
            # the '17 frame's shape and its sub-image size
            i = next(k for k, (c, d) in enumerate(inner) if c == 43 and d.startswith(b"17season"))
            place = next(d for c, d in inner[i:] if c == 26)
            shape_id = int.from_bytes(place[3:5], "little")
            sc, shape = defs[shape_id]
            assert sc == 2, sc
            ref = next(defs[b][1] for b in _bitmaps(shape) if b in defs and defs[b][0] == 1008)
            x1, y1, x2, y2 = (int.from_bytes(ref[k:k + 2], "little") for k in (4, 6, 8, 10))
            w, h = x2 - x1, y2 - y1
            fit = icon.copy()
            fit.thumbnail((w, h), Image.LANCZOS)
            cell = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            cell.paste(fit, ((w - fit.width) // 2, (h - fit.height) // 2))
            y = ICON_FREE_Y + (0 if sid == ICON_SPRITES[0] else 48)
            assert atlas.crop((x, y, x + w, y + h)).getextrema()[3] == (0, 0), "atlas space not free"
            _draw(dds, cell, x, y)
            sub, shp = next_id, next_id + 1
            next_id += 2
            new_defs += [[1008, sub.to_bytes(2, "little") + bytes(2) + b"".join(v.to_bytes(2, "little") for v in (x, y, x + w, y + h))],
                         [2, _shape_copy(shape, shp, sub)]]
            frame = [[43, label], [26, bytes([0x03, 1, 0]) + shp.to_bytes(2, "little")], [1, b""]]
            inner = inner[:-1] + frame + inner[-1:]
            nf = int.from_bytes(sprites[sid][1][2:4], "little") + 1
            sprites[sid][1] = sid.to_bytes(2, "little") + nf.to_bytes(2, "little") + _join(inner)
        x += 40
    at = tags.index(sprites[ICON_SPRITES[0]])
    tags[at:at] = new_defs
    body = body[:start] + _join(tags)
    return b"CFX" + gfx[3:4] + (len(body) + 8).to_bytes(4, "little") + zlib.compress(body, 9), bytes(dds)


# --- the match HUD's season icons: data/ui/libs/Module.big -------------------------------------
# User: "Icon session baru tidak muncul di game play" / "benar harusnya 26". Every SeasonIcon shows
# gotoAndStop(PlayerListUtil.getSeason(spid) + "season"), and getSeason gives a spid of season 0
# (a bare player id) CURRENT_SEASON (curseason, '17). In a match native hands the overlays (the
# NIB name boxes, kicker and shootout boards) the id of the card's row in its engine DB
# (fifa_ng_db players, looked up by spid, else by player id); its playerid field has 27 bits, so a
# '17 or FO4 card (88 / 300 / 325 x 1e6 + pid) is there by player id only and every one of them
# showed '17. The patch puts this at the start of getSeason (after its pushscope):
#     if (0 < spid < SEASON_MULTIPLIER && MatchProxy.instance)
#         for side in "left", "right":  (getPlayersDataProvider: a DataProvider, an Array of the
#             for p in that side's players:   side's team.players as the match resource has them)
#                 if (p.spid % SEASON_MULTIPLIER == spid) { spid = p.spid; goto done }
# so a match card gets its own season ('17 cards still show curseason) and anything else is as
# before. Branches in AVM2 are relative, so the old code behind the prefix needs no fixing. A side
# lists every card of the club (the pre-match screen swaps reserves in), so http_config.match_side
# puts the 18 first: a reserve 26 LIVE Mbappe no longer badges the WC18 one playing.
MODULE_BIG, MODULE_GFX = "data/ui/libs/Module.big", "data/ui/libs/Module.gfx"


def _u30(v):
    out = bytearray()
    while True:
        b, v = v & 0x7F, v >> 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


class _Abc:
    """An ABC block parsed far enough to append strings / multinames and to swap a method body:
    the offsets of those sections, the names, the classes' traits and the bodies' spans."""

    def __init__(self, d, p):
        self.d, self.p, self.at = d, p, p + 4
        assert d[p:p + 4] == b"\x10\x00\x2e\x00", "not an ABC block"
        n = self.u30(); [self.u30() for _ in range(max(0, n - 1))]  # ints
        n = self.u30(); [self.u30() for _ in range(max(0, n - 1))]  # uints
        n = self.u30(); self.at += 8 * max(0, n - 1)  # doubles
        self.str_count_at = self.at
        n = self.u30(); self.strs = [""]
        self.str_start = self.at
        for _ in range(max(0, n - 1)):
            ln = self.u30(); self.strs.append(d[self.at:self.at + ln].decode("utf-8", "replace")); self.at += ln
        self.str_end = self.at
        n = self.u30(); self.ns = [None] + [(self.u8(), self.u30()) for _ in range(max(0, n - 1))]
        n = self.u30()
        for _ in range(max(0, n - 1)):
            [self.u30() for _ in range(self.u30())]
        self.mn_count_at = self.at
        n = self.u30(); self.mn = [None]
        self.mn_start = self.at
        for _ in range(max(0, n - 1)):
            k = self.u8()
            if k in (0x07, 0x0D, 0x09, 0x0E):
                self.mn.append((k, self.u30(), self.u30()))
            elif k in (0x0F, 0x10, 0x1B, 0x1C):
                self.mn.append((k, self.u30()))
            elif k in (0x11, 0x12):
                self.mn.append((k,))
            elif k == 0x1D:
                q, c = self.u30(), self.u30(); self.mn.append((k, q, [self.u30() for _ in range(c)]))
            else:
                raise ValueError(f"multiname kind {k:#x}")
        self.mn_end = self.at
        for _ in range(self.u30()):  # methods
            pc = self.u30(); self.u30(); [self.u30() for _ in range(pc)]; self.u30(); fl = self.u8()
            if fl & 0x08:
                for _ in range(self.u30()):
                    self.u30(); self.u8()
            if fl & 0x80:
                [self.u30() for _ in range(pc)]
        for _ in range(self.u30()):  # metadata
            self.u30(); [self.u30() for _ in range(2 * self.u30())]
        n = self.u30(); self.classes = []
        for _ in range(n):
            name = self.u30(); self.u30(); fl = self.u8()
            if fl & 0x08:
                self.u30()
            [self.u30() for _ in range(self.u30())]
            self.u30(); self.classes.append([self.name(name), self.traits()])
        for c in self.classes:
            self.u30(); c.append(self.traits())  # [name, instance traits, static traits]
        for _ in range(self.u30()):  # scripts
            self.u30(); self.traits()
        self.bodies = {}
        for _ in range(self.u30()):
            start = self.at
            m = self.u30(); head = [self.u30() for _ in range(4)]
            ln = self.u30(); code = (self.at, self.at + ln); self.at += ln
            exc = [[self.u30() for _ in range(5)] for _ in range(self.u30())]
            tr = self.at; self.traits()
            self.bodies[m] = (start, self.at, head, code, exc, (tr, self.at))
        self.end = self.at

    def u8(self):
        self.at += 1
        return self.d[self.at - 1]

    def u30(self):
        r = sh = 0
        while True:
            b = self.d[self.at]; self.at += 1
            r |= (b & 0x7F) << sh; sh += 7
            if not b & 0x80:
                return r

    def name(self, i):
        m = self.mn[i]
        return self.strs[m[2]] if m[0] in (0x07, 0x0D) else self.strs[m[1]] if m[0] in (0x09, 0x0E) else None

    def traits(self):
        out = []
        for _ in range(self.u30()):
            name = self.name(self.u30()); k = self.u8(); kind = k & 0x0F
            if kind in (0, 6):
                self.u30(); self.u30()
                if self.u30():
                    self.u8()
                ref = None
            elif kind in (1, 2, 3, 4, 5):
                self.u30(); ref = self.u30()
            else:
                raise ValueError(f"trait kind {kind}")
            if k & 0x40:
                [self.u30() for _ in range(self.u30())]
            out.append((name, kind, ref))
        return out

    def qname(self, ns, name):
        """Index of the QName ns::name, if the pool has it."""
        return next((i for i, m in enumerate(self.mn) if m and m[0] == 0x07 and m[1] == ns and self.name(i) == name), None)


def _asm(ops):
    """Bytecode of ops: bytes, ("@", label) or (branch opcode, label) with an s24 to the label."""
    pos, at = 0, {}
    for op in ops:
        if isinstance(op, bytes):
            pos += len(op)
        elif op[0] == "@":
            at[op[1]] = pos
        else:
            pos += 4
    out = bytearray()
    for op in ops:
        if isinstance(op, bytes):
            out += op
        elif op[0] != "@":
            out += bytes([op[0]]) + (at[op[1]] - (len(out) + 4)).to_bytes(3, "little", signed=True)
    return bytes(out)


def match_seasons_abc(d, p):
    """(start, end, new bytes) of the ABC block at p (PlayerListUtil's) with getSeason patched,
    or None when it has the patch already."""
    a = _Abc(d, p)
    pub = next(i for i, n in enumerate(a.ns) if n and n[0] == 0x16 and a.strs[n[1]] == "")
    cls = next(c for c in a.classes if c[0] == "PlayerListUtil")
    m = next(ref for name, kind, ref in cls[2] if name == "getSeason" and kind == 1)
    start, end, head, (c0, c1), exc, (t0, t1) = a.bodies[m]
    code = a.d[c0:c1]
    assert code[:2] == b"\xd0\x30", "getSeason does not start with getlocal0, pushscope"
    if "getPlayersDataProvider" in a.strs:
        return None
    # names it uses: the class's own SEASON_MULTIPLIER, MatchProxy (fok.net), instance, length,
    # p.spid (a multiname over the open namespaces) and arr[i] (a runtime multiname)
    mult, inst, length = (a.qname(pub, n) for n in ("SEASON_MULTIPLIER", "instance", "length"))
    proxy = next(i for i, x in enumerate(a.mn) if x and x[0] == 0x07 and a.name(i) == "MatchProxy"
                 and a.strs[a.ns[x[1]][1]] == "fok.net")
    spid = next(i for i, x in enumerate(a.mn) if x and x[0] == 0x09 and a.name(i) == "spid")
    late = next(i for i, x in enumerate(a.mn) if x and x[0] == 0x1B)
    assert None not in (mult, inst, length), "PlayerListUtil's names moved"
    new_strs = ["left", "right", "getPlayersDataProvider"]
    s0 = len(a.strs)
    s_left, s_right, s_get = s0, s0 + 1, s0 + 2
    get = len(a.mn)  # the new QName public::getPlayersDataProvider
    L, prop = lambda n: b"\x62" + _u30(n), lambda op, i: bytes([op]) + _u30(i)
    ops = [b"\xd1\x24\x00", (0x16, "done"),  # if (spid <= 0) done
           b"\xd1" + prop(0x5D, mult) + prop(0x66, mult), (0x18, "done"),  # if (spid >= SEASON_MULTIPLIER) done
           prop(0x5D, proxy) + prop(0x66, proxy) + prop(0x66, inst) + b"\x82\x63" + _u30(7),
           L(7), (0x12, "done")]  # local7 = MatchProxy.instance; if (!local7) done
    for side, s in (("left", s_left), ("right", s_right)):
        ops += [L(7) + prop(0x2C, s) + prop(0x46, get) + b"\x01\x82\x63" + _u30(4),  # local4 = local7.getPlayersDataProvider(side)
                L(4), (0x12, f"next{side}"), b"\x24\x00\x63" + _u30(5),  # if (!local4) next; local5 = 0
                ("@", f"loop{side}"), b"\x09" + L(5) + L(4) + prop(0x66, length), (0x18, f"next{side}"),  # while (local5 < local4.length)
                L(4) + L(5) + prop(0x66, late) + prop(0x66, spid) + b"\x82\x63" + _u30(6),  # local6 = local4[local5].spid
                L(6) + prop(0x5D, mult) + prop(0x66, mult) + b"\xa4\xd1", (0x14, f"inc{side}"),  # if (local6 % MULT != spid) inc
                L(6) + b"\x73\xd5", (0x10, "done"),  # spid = int(local6); done
                ("@", f"inc{side}"), b"\xc2" + _u30(5), (0x10, f"loop{side}"), ("@", f"next{side}")]
    ops.append(("@", "done"))
    prefix = _asm(ops)
    code = code[:2] + prefix + code[2:]
    body = (_u30(m) + _u30(max(head[0], 3)) + _u30(max(head[1], 8)) + _u30(head[2]) + _u30(head[3])
            + _u30(len(code)) + code + _u30(len(exc))
            + b"".join(_u30(v + (len(prefix) if k < 3 else 0)) for e in exc for k, v in enumerate(e))
            + a.d[t0:t1])
    strs = b"".join(_u30(len(s.encode())) + s.encode() for s in new_strs)
    out = (a.d[p:a.str_count_at] + _u30(len(a.strs) + len(new_strs)) + a.d[a.str_start:a.str_end] + strs
           + a.d[a.str_end:a.mn_count_at] + _u30(len(a.mn) + 1) + a.d[a.mn_start:a.mn_end]
           + b"\x07" + _u30(pub) + _u30(s_get)
           + a.d[a.mn_end:start] + body + a.d[end:a.end])
    return p, a.end, out


# The Market search's club filter (user: "jika filter klub maka juga filter juga yang histroynya
# sesuai klub tersebut"): TransferUtil.teamidQuery(teamid, leagueid) adds betweenQuery("teamid",
# teamid, teamid) = "AND ( teamid BETWEEN t AND t ) " to the client's own SQL over its fifadb (the
# filter page calls it alone once a club is picked: no league condition). Its club branch now
# passes MARKET_CLUB[0] + t + MARKET_CLUB[1] + t + MARKET_CLUB[2] as the field, so the club's
# former players (table playerhistory: install's club careers) show too. (Putting "{1}" in the
# field does not work: StringUtil.formatStringParams joins only the text around a placeholder's
# first two pieces.)
MARKET_CLUB = ("players.playerid IN (SELECT playerid FROM playerhistory WHERE teamid BETWEEN ", " AND ", ") OR teamid")


def market_club_abc(d, p):
    """(start, end, new bytes) of the ABC block at p (TransferUtil's) with teamidQuery's club
    branch widened to the club's former players, or None when it has the patch already."""
    a = _Abc(d, p)
    if MARKET_CLUB[0] in a.strs:
        return None
    cls = next(c for c in a.classes if c[0] == "TransferUtil")
    m = next(ref for name, kind, ref in cls[1] if name == "teamidQuery" and kind == 1)
    start, end, head, (c0, c1), exc, (t0, t1) = a.bodies[m]
    code = a.d[c0:c1]
    team = a.strs.index("teamid")
    between = next(i for i in range(1, len(a.mn)) if a.name(i) == "betweenQuery")
    call = b"\xd1\xd1\x46" + _u30(between) + b"\x03\x29\x47"  # betweenQuery(field, teamid, teamid); return
    tail = b"\xd0\x2c" + _u30(team) + call  # this.betweenQuery("teamid", ...), the club branch
    j = len(code) - len(tail) - 4  # the national branch's jump over it
    assert code.endswith(tail) and code[j] == 0x10 and int.from_bytes(code[j + 1:j + 4], "little", signed=True) == len(tail) - 1, \
        "TransferUtil.teamidQuery changed (unknown client version)"
    s0 = len(a.strs)
    club = (b"\xd0\x2c" + _u30(s0) + b"\xd1\xa0\x2c" + _u30(s0 + 1) + b"\xa0\xd1\xa0\x2c" + _u30(s0 + 2) + b"\xa0"  # the field
            + call)
    code = code[:j] + b"\x10" + (len(club) - 1).to_bytes(3, "little", signed=True) + club
    assert not exc, "teamidQuery has exception handlers"
    body = (_u30(m) + _u30(max(head[0], 4)) + _u30(head[1]) + _u30(head[2]) + _u30(head[3])
            + _u30(len(code)) + code + b"\x00" + a.d[t0:t1])
    strs = b"".join(_u30(len(s.encode())) + s.encode() for s in MARKET_CLUB)
    out = (a.d[p:a.str_count_at] + _u30(len(a.strs) + len(MARKET_CLUB)) + a.d[a.str_start:a.str_end] + strs
           + a.d[a.str_end:start] + body + a.d[end:a.end])
    return p, a.end, out


def _module_patched(gfx, cls, patch):
    """Module.gfx (CFX) with patch(body, abc offset) applied to the ABC block that holds class cls,
    or None when patch finds it done."""
    import zlib
    assert gfx[:3] == b"CFX", gfx[:3]
    body = zlib.decompress(gfx[8:])
    r = _Bits(body, 0); r.u(4 * r.u(5)); p = r.end() + 4
    while p < len(body):  # the DoABC tag (82: flags, name, ABC) that holds cls
        h = int.from_bytes(body[p:p + 2], "little"); code, ln, hdr = h >> 6, h & 0x3F, 2
        if ln == 0x3F:
            ln, hdr = int.from_bytes(body[p + 2:p + 6], "little"), 6
        q = p + hdr
        abc = body.index(b"\x00", q + 4) + 1 if code == 82 else None
        if abc is not None and any(c[0] == cls for c in _Abc(body, abc).classes):
            hit = patch(body, abc)
            if hit is None:
                return None
            _, end, new = hit
            assert end == q + ln, "the ABC block does not end its tag"
            payload = body[q:abc] + new
            body = body[:p] + (((82 << 6) | 0x3F).to_bytes(2, "little") + len(payload).to_bytes(4, "little")
                               + payload + body[q + ln:])
            return b"CFX" + gfx[3:4] + (len(body) + 8).to_bytes(4, "little") + zlib.compress(body, 9)
        p = q + ln
    raise SystemExit(f"{MODULE_GFX}: {cls} not found (unknown client version)")


def module_with_match_seasons(gfx):
    """Module.gfx (CFX) with PlayerListUtil.getSeason patched, or None when it has the patch."""
    return _module_patched(gfx, "PlayerListUtil", match_seasons_abc)


def module_with_market_clubs(gfx):
    """Module.gfx (CFX) with TransferUtil.teamidQuery patched, or None when it has the patch."""
    return _module_patched(gfx, "TransferUtil", market_club_abc)


# --- install / restore -------------------------------------------------------------------------
# The client reads cards from data_0.big data/db2/fifadb.db.db2 and, per UI language, names /
# club names / league names from data/db2/<lang>.db.db2 (PlayerListUtil: "SELECT * FROM names
# WHERE playerid IN" on the ConnectionPool named Localization.LOCALE; a card whose player has
# no name there gets no staticdata, so its info popup stays empty). Each one is the RefPack of
# a Blowfish-encrypted SQLite (extract_fifadb.py). install rewrites them the same way, keeping
# the shipped first 16 encrypted bytes (they never decrypt to the SQLite magic), over the
# shipped entries (tools/re/bigdir.py in place: the game caches the whole archive, and a
# data_0.big 40 MB longer crashed it 5 s into boot). The first install copies data_0.big and
# Label.big to backup/fo4season/; every install starts from those copies and restore puts
# them back. tools/eatrax_song.py edits data_0.big too: after running it, run install again
# (from the copy, so the song is gone).
FIFADB_ENTRY = "data/db2/fifadb.db.db2"
LOCALES = ("chi", "eng", "ind", "kor", "tha", "vie")
ICON_URL = "https://open.api.nexon.com/static/fconline/meta/seasonid.json"


def _original(path):
    """Put a client file back as it was before the first install (the first one copies it)."""
    os.makedirs(BACKUP, exist_ok=True)
    copy = os.path.join(BACKUP, os.path.basename(path))
    if os.path.isfile(copy):
        shutil.copyfile(copy, path)
    else:
        shutil.copyfile(path, copy)
    return path


def _originals(game):
    """data_0.big, Label.big and Module.big as they were before the first install."""
    return [_original(os.path.join(game, p)) for p in (os.path.join("data", "data_0.big"), LABEL_BIG, MODULE_BIG)]


MODULE_PATCHES = (module_with_match_seasons, module_with_market_clubs)


def _patch_module(module):
    """The match HUD's season icons and the Market's club filter into Module.big
    (MODULE_PATCHES), checked."""
    import bigdir
    gfx = old = bigdir.read(module, MODULE_GFX)
    for patch in MODULE_PATCHES:
        gfx = patch(gfx) or gfx
    changed = gfx is not old
    if changed:
        bigdir.patch(module, {MODULE_GFX: gfx})
    assert all(patch(bigdir.read(module, MODULE_GFX)) is None for patch in MODULE_PATCHES)
    return changed


def _sqlite(plain, edit):
    """SQLite bytes after edit(connection) and a VACUUM."""
    tmp = os.path.join(tempfile.gettempdir(), "fo4_season_edit.sqlite")
    open(tmp, "wb").write(plain)
    c = sqlite3.connect(tmp)
    edit(c)
    c.commit()
    c.execute("VACUUM")
    c.close()
    data = open(tmp, "rb").read()
    os.remove(tmp)
    assert len(data) % 8 == 0
    return data


def client_db():
    """gamedata/fifadb.sqlite as the client gets it. Its packed DB has to fit the shipped one's
    place in data_0.big (with the engine DB too, see cmd_install), so the names of players
    without a card (half of each names table; the client reads names from its <lang>.db
    anyway) are left out, and so are the other regions' card permissions: the client only reads
    perm<Localization.REGION> (TransferUtil's Market search), and the launcher always starts it
    with region id (3.4 MB less). Every card of a player with a club career (table playerhistory) shows
    it: the player info's Karakteristik tab lists it for cards with historyopen = 1 once
    features.seasoncare is on (PR #39), and the shipped DB opens it on seasons 22-24 only.
    The careers come from tools/club_history.py's gamedata/club_history.json (up to the running
    season) where it has the player, else from FO3's own table (up to 2017)."""
    import club_history

    def edit(c):
        for t in NAME_TABLES:
            c.execute(f"DELETE FROM {t} WHERE playerid NOT IN (SELECT playerid FROM players)")
        for t in PERM_TABLES:
            if t != "permid":
                c.execute(f"DELETE FROM {t}")
        # every card on the Market (http_config.market_spids): the client's Market search lists
        # only transferable ones (FO3's event cards were not: Cafu's legends)
        c.execute("UPDATE permid SET transferable = 1 WHERE spid >= 1000000")
        club_history.apply(c)
        c.execute("UPDATE players SET historyopen = 1 WHERE playerid IN (SELECT playerid FROM playerhistory)")
    return _sqlite(open(FIFADB, "rb").read(), edit)


NG_ENTRY, NG_META = "data/db/fifa_ng_db.db", "data/db/fifa_ng_db-meta.xml"
ENGINE_COPY = ("height", "weight", "nationality", "preferredfoot")


def ng_players(db, meta):
    """(Table, {field: (shortname, rangelow)}, {playerid: row}, table index, raw tables, base) of
    the engine DB's players table (a stored value is the field's value - rangelow)."""
    import eadb
    m = re.search(r'<table name="players" shortname="(\w+)".*?</table>', meta, re.S)
    fld = {n: (sn, int(lo)) for n, sn, lo in re.findall(r'<field name="(\w+)" shortname="(\w+)"[^>]*rangelow="(-?\d+)"', m.group())}
    base, names, raws = eadb.db_tables(db)
    i = names.index(m.group(1).encode())
    t = eadb.Table(raws[i])
    return t, fld, {t.get(r, fld["playerid"][0]): r for r in t.rows}, i, raws, base


# The admin page's seasons (issue #38): {code: {"name", "cards": {spid: {"from": the card it was
# copied from, "boost": what it added to every stat}}}}, each with its badge in SEASON_ICONS/<code>.png. A season code is at most 99: the engine DB's
# playerid / spid fields go to 99,999,999, so a card of season 300 or 325 has no row of its own.
CUSTOM_SEASONS = os.path.join(ROOT, "gamedata", "seasons.json")
SEASON_ICONS = os.path.join(ROOT, "gamedata", "season_icons")
NG_MAX = 99999999
# a card's look (card_edits.json "look"): "face" = the engine row whose head it wears (FACE fields
# copied), the LOOK fields set as given
FACE = ("headassetid", "headtypecode", "headclasscode", "hashighqualityhead", "headscalecode", "skintonecode",
        "skintypecode", "eyecolorcode", "eyebrowcode")
LOOK = ("hairtypecode", "haircolorcode", "facialhairtypecode", "facialhaircolorcode", "skintonecode", "eyecolorcode",
        "bodytypecode")


def custom_seasons():
    try:
        with open(CUSTOM_SEASONS) as f:
            return {int(k): v for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def _wear(t, fld, row, face):
    """The row with the FACE fields of row `face` (raw bits: a float too)."""
    for name in FACE:
        ty, bo, dep = t.fields[fld[name][0]]
        row = t.set(row, fld[name][0], int.from_bytes(face, "little") >> bo & (1 << dep) - 1)
    return row


def photo_heads(t, fld, by):
    """The admin page's photo heads (tools/photo_head.py) on the engine DB: a player's id row and
    his other rows with a generic head wear his (its donor's FACE fields, its own head id). The id
    row is what most of his cards show: a 2017 card (season 88) shows it even with a row of its
    own (live-checked: Kroos 88182521). Returns how many rows."""
    import photo_head
    heads = {h["pid"]: (hid, by.get(h["row"])) for hid, h in photo_head.load().items()
             if os.path.isfile(os.path.join(photo_head.DIR, f"{hid}.png"))}  # as photo_head.files()
    sn, n = fld["headassetid"][0], 0
    for k in [k for k in by if k % 1000000 in heads]:
        hid, face = heads[k % 1000000]
        if face is not None and (k < 1000000 or t.get(by[k], fld["headclasscode"][0]) == 1):
            by[k] = t.set(_wear(t, fld, by[k], face), sn, hid - fld["headassetid"][1])
            n += 1
    return n


def card_looks(t, fld, by):
    """The admin page's rows in the engine DB: a card of one of its seasons gets a copy of the
    row of the card it was copied from (so it looks the same), and a card's look edits go on its
    own row (made from its player's row when it has none; a card over NG_MAX: on his player's
    row, which every card of his without a row of its own wears). Returns how many rows."""
    def put(row, name, v):
        sn, lo = fld[name]
        return t.set(row, sn, v - lo) if 0 <= v - lo < 1 << t.fields[sn][2] else row

    def own(key, src):
        if key not in by:
            base = by.get(src, by.get(src % 1000000))
            if base is None:
                return False
            by[key] = put(put(base, "playerid", key), "spid", key)
        return True
    n = 0
    for s in custom_seasons().values():
        for spid, src in s.get("cards", {}).items():
            n += int(spid) not in by and int(spid) <= NG_MAX and own(int(spid), int(src["from"]))
    try:
        with open(CARD_EDITS) as f:
            edits = json.load(f)
    except (OSError, ValueError):
        edits = {}
    for spid, e in edits.items():
        look, spid = e.get("look"), int(spid)
        key = spid if spid <= NG_MAX else spid % 1000000
        if not look or not own(key, spid % 1000000):
            continue
        row, face = by[key], by.get(look.get("face"))
        if face is not None:
            row = _wear(t, fld, row, face)
        for name in LOOK:
            if name in look:
                row = put(row, name, int(look[name]))
        by[key] = row
        n += 1
    return n


def kit_colors(game, teamid, kind):
    """(primary, secondary, tertiary) RGB of a kit (0 home, 1 away, 2 keeper) from its biggest
    texture in data_5.big: the most common colour, then the next ones far enough from it."""
    import struct, numpy as np, bigdir, photo_head
    rx3 = photo_head.unchunk(bigdir.read(os.path.join(game, "data", "data_5.big"), f"data/sceneassets/kit/kit_{teamid}_{kind}_0.rx3"))
    n = struct.unpack("<I", rx3[12:16])[0]
    off = max((o for h, o, _, _ in (struct.unpack("<4I", rx3[16 + 16 * i:32 + 16 * i]) for i in range(n)) if h == photo_head.TEX),
              key=lambda o: struct.unpack("<HH", rx3[o + 8:o + 12]))
    w, h = struct.unpack("<HH", rx3[off + 8:off + 12])
    size = struct.unpack("<4I", rx3[off + 16:off + 32])[2]
    px = np.asarray(photo_head.dxt1_decode(rx3[off + 32:off + 32 + size], w, h)).reshape(-1, 3).astype(int)
    _, inverse, counts = np.unique(px // 32, axis=0, return_inverse=True, return_counts=True)
    picked = []
    for k in np.argsort(-counts):
        colour = tuple(int(v) for v in px[inverse.ravel() == k].mean(0))
        if all(sum(abs(a - b) for a, b in zip(colour, q)) > 90 for q in picked):
            picked.append(colour)
        if len(picked) == 3:
            break
    return tuple((picked * 3)[:3])


def engine_clubs(raws, names, meta, game):
    """The OPEN_CLUBS clubs into the engine DB's teams, teamkits, leagueteamlinks and teamstadiumlinks
    (raws edited in place): each a copy of the first club of its league with its own id, asset id
    (its kit files), name and kit colours. Returns the clubs added."""
    import eadb, club_history
    tabs = {}
    for name in ("teams", "teamkits", "leagueteamlinks", "teamstadiumlinks"):
        m = re.search(r'<table name="%s" shortname="(\w+)".*?</table>' % name, meta, re.S)
        fld = {n: (sn, int(lo)) for n, sn, lo in re.findall(r'<field name="(\w+)" shortname="(\w+)"[^>]*rangelow="(-?\d+)"', m.group())}
        i = names.index(m.group(1).encode())
        tabs[name] = (i, eadb.Table(raws[i]), fld)
    get = lambda name, row, f: tabs[name][1].get(row, tabs[name][2][f][0]) + tabs[name][2][f][1]

    def put(name, row, **kv):
        t, fld = tabs[name][1], tabs[name][2]
        for f, v in kv.items():
            row = t.set(row, fld[f][0], v if isinstance(v, str) else v - fld[f][1])
        return row

    def add(name, row, key):
        t = tabs[name][1]
        t.rows = sorted(t.rows + [row], key=key)

    teams = {get("teams", r, "teamid"): r for r in tabs["teams"][1].rows}
    eng = club_history.ea_teamnames(game)
    light = lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] > 140
    added = []
    for tid, (league, _) in OPEN_CLUBS.items():
        if tid in teams:
            continue
        link = next(r for r in tabs["leagueteamlinks"][1].rows if get("leagueteamlinks", r, "leagueid") == league)
        tpl = get("leagueteamlinks", link, "teamid")
        kits = [kit_colors(game, tid, k) for k in range(3)]
        add("teams", put("teams", teams[tpl], teamid=tid, assetid=tid, teamname=eng.get(str(tid), str(tid)), rivalteam=tpl,
                         **{f"teamcolor{i + 1}{ch}": c[j] for i, c in enumerate((kits[0][0], kits[0][1], kits[1][0]))
                            for j, ch in enumerate("rgb")}), key=lambda r: get("teams", r, "teamid"))
        keys = [get("leagueteamlinks", r, "artificialkey") for r in tabs["leagueteamlinks"][1].rows]
        add("leagueteamlinks", put("leagueteamlinks", link, teamid=tid, artificialkey=max(keys) + 1),
            key=lambda r: get("leagueteamlinks", r, "artificialkey"))
        stadium = next((r for r in tabs["teamstadiumlinks"][1].rows if get("teamstadiumlinks", r, "teamid") == tpl), None)
        if stadium:
            add("teamstadiumlinks", put("teamstadiumlinks", stadium, teamid=tid), key=lambda r: get("teamstadiumlinks", r, "teamid"))
        kid = max(get("teamkits", r, "teamkitid") for r in tabs["teamkits"][1].rows)
        for kind, (prim, sec, tert) in enumerate(kits):
            row = next(r for r in tabs["teamkits"][1].rows if get("teamkits", r, "teamtechid") == tpl
                       and get("teamkits", r, "teamkittypetechid") == kind)
            text = (12, 12, 12) if light(prim) else (220, 220, 220)  # numbercolor 2 = black, 1 = white
            row = put("teamkits", row, teamtechid=tid, teamkitid=kid + kind + 1, numbercolor=2 if light(prim) else 1,
                      shortsnumbercolor=2 if light(prim) else 1,
                      **{f"teamcolor{part}{ch}": c[j] for part, c in (("prim", prim), ("sec", sec), ("tert", tert)) for j, ch in enumerate("rgb")},
                      **{f"jerseynamecolor{ch}": text[j] for j, ch in enumerate("rgb")})
            add("teamkits", row, key=lambda r: (get("teamkits", r, "teamtechid"), get("teamkits", r, "teamkittypetechid")))
        added.append(tid)
    for i, t, _ in tabs.values():
        raws[i] = t.raw()
    return added


def engine_db(db, meta, game=r"D:\FO3ID\32795"):
    """fifa_ng_db (EA DB, tools/re/eadb.py) with a players row for every new player. The 3D
    player (club creation's player view, matches) looks a card up there by spid, then by player
    id ('17 cards have only their player id's row); a player with neither crashed the client.
    The row is a copy of the 2017 player whose generic head build gave him (fo4_heads.json),
    with his own id, height, weight, nationality and foot; rows stay sorted by player id. Then
    the admin page's rows (photo_heads, card_looks)."""
    import struct, eadb
    # none before the first fetch (a fresh install by setup.exe)
    heads = {int(k): v for k, v in json.load(open(HEADS)).items()} if os.path.isfile(HEADS) else {}
    t, fld, by, i, raws, base = ng_players(db, meta)
    pk = fld["playerid"][0]
    generic = next(r for r in t.rows if t.get(r, fld["headclasscode"][0]) == 1)
    with closing(sqlite3.connect(FIFADB)) as c:
        c.row_factory = sqlite3.Row
        mine = {r["playerid"]: r for r in c.execute("SELECT * FROM players WHERE playerid BETWEEN ? AND ? ORDER BY spid",
                                                    (NEW_PID, NEW_PID + 99999))}
    for pid, r in mine.items():
        row = t.set(by.get(heads.get(pid), generic), pk, pid)
        for name in ENGINE_COPY:
            sn, lo = fld[name]
            v = r[name] - lo
            if 0 <= v < 1 << t.fields[sn][2]:
                row = t.set(row, sn, v)
        by[pid] = row
    looks = photo_heads(t, fld, by) + card_looks(t, fld, by)
    t.rows = [by[k] for k in sorted(by)]
    raws[i] = t.raw()
    engine_clubs(raws, eadb.db_tables(db)[1], meta, game)
    out, pos = bytearray(db[:base]), 0  # table offsets, sizes and checksums (as tools/eatrax_song.py)
    for j, raw in enumerate(raws):
        struct.pack_into("<I", out, 0x1C + 8 * j, pos)
        pos += len(raw)
    struct.pack_into("<I", out, base - 4, eadb.crc(out[0x18:base - 4]))
    struct.pack_into("<I", out, 8, base + pos)
    struct.pack_into("<I", out, 0x14, eadb.crc(out[:0x14]))
    return bytes(out) + b"".join(raws), len(mine), looks


def locale_additions():
    """The names rows of the new players for <lang>.db (its clubs are FO3's: build puts the
    players of the others in Rest of the World)."""
    with closing(sqlite3.connect(FIFADB)) as c:
        return c.execute("SELECT playerid, full, short, full_eng, short_eng, jersey FROM nameseng "
                         "WHERE playerid BETWEEN ? AND ?", (NEW_PID, NEW_PID + 99999)).fetchall()


def locale_db(plain_db, names, trim=False, clubs=()):
    """A <lang>.db with the new players' names and the OPEN_CLUBS clubs (clubs: [(teamid, name,
    league)]); trim drops the names of players without a card (when the packed DB would not fit its
    place)."""
    cards = None
    if trim:
        with closing(sqlite3.connect(FIFADB)) as c:
            cards = [r[0] for r in c.execute("SELECT DISTINCT playerid FROM players")]

    def edit(c):
        c.executemany("INSERT OR REPLACE INTO names (playerid, full, short, full_eng, short_eng, jersey) VALUES (?,?,?,?,?,?)", names)
        c.executemany("INSERT OR REPLACE INTO team (teamid, teamname, leagueid) VALUES (?,?,?)", clubs)
        if cards:
            c.execute("CREATE TEMP TABLE keep (playerid INTEGER PRIMARY KEY)")
            c.executemany("INSERT INTO keep VALUES (?)", [(p,) for p in cards])
            c.execute("DELETE FROM names WHERE playerid NOT IN (SELECT playerid FROM keep)")
    return _sqlite(plain_db, edit)


def _game_running():
    out = os.popen('tasklist /FI "IMAGENAME eq fifazf.exe" /NH').read()
    return "fifazf.exe" in out.lower()


def _seasons_built():
    """The added seasons gamedata/fifadb.sqlite has cards of."""
    with closing(sqlite3.connect(FIFADB)) as c:
        return [s for s in (*CLASSES.values(), WC18)
                if c.execute("SELECT 1 FROM players WHERE spid BETWEEN ? AND ? LIMIT 1", (s * 1000000, s * 1000000 + 999999)).fetchone()]


def _icons(gfx, dds):
    """{season: RGBA PIL image} of FO4's season badges (downloaded once to gamedata/fo4_icons) and,
    once built, WC18's (wc18_icon, from the shipped Label.big's gfx and dds), and the admin page's
    seasons that have cards (SEASON_ICONS)."""
    from PIL import Image
    d = os.path.join(ROOT, "gamedata", "fo4_icons")
    os.makedirs(d, exist_ok=True)
    out, meta = {}, None
    if WC18 in _seasons_built():
        out[WC18] = wc18_icon(gfx, dds)
    for season in CLASSES.values():
        f = os.path.join(d, f"{season}.png")
        if not os.path.exists(f):
            if meta is None:
                req = urllib.request.Request(ICON_URL, headers={"User-Agent": "Mozilla/5.0"})
                meta = {s["seasonId"]: s["seasonImg"] for s in json.load(urllib.request.urlopen(req, timeout=60))}
            req = urllib.request.Request(meta[season], headers={"User-Agent": "Mozilla/5.0"})
            open(f, "wb").write(urllib.request.urlopen(req, timeout=60).read())
        out[season] = Image.open(f).convert("RGBA")
    with closing(sqlite3.connect(FIFADB)) as c:  # the admin page's seasons that have cards
        for code, s in custom_seasons().items():
            f = os.path.join(SEASON_ICONS, f"{code}.png")
            if os.path.isfile(f) and c.execute("SELECT 1 FROM players WHERE spid BETWEEN ? AND ? LIMIT 1",
                                               (code * 1000000, code * 1000000 + 999999)).fetchone():
                out[code] = Image.open(f).convert("RGBA")
                SEASON_NAMES.setdefault(code, s["name"])
    return out


def _seal(plain_db, shipped):
    """RefPack of plain_db encrypted like the client's DBs, with the shipped first 16 bytes."""
    from refpack import refpack_blocks
    from extract_fifadb import swap32, KEY
    from cryptography.hazmat.primitives.ciphers import Cipher, modes
    try:
        from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.algorithms import Blowfish
    e = Cipher(Blowfish(KEY), modes.ECB()).encryptor()
    enc = bytearray(swap32(e.update(swap32(plain_db)) + e.finalize()))
    enc[:16] = shipped[:16]
    return refpack_blocks(bytes(enc)), len(enc)


# play.bat runs "install --if-needed" before the game (user, on moving to another PC: "Apakah jika
# pindah PC pada saat di jalan kan akan terpatch sendiri?" -> "Ya"): it installs only when what
# install reads (this server's data, the tools) or the client files it writes are not as the last
# install left them, recorded in STAMP in the game folder (a fresh client has none).
STAMP = "fo3id_install.json"


def _fingerprint(paths):
    """{path: [size, mtime_ns]} of the files at paths (a folder: every file in it); missing ones are left out."""
    out = {}
    for p in paths:
        for f in [os.path.join(d, n) for d, _, ns in os.walk(p) for n in ns] if os.path.isdir(p) else [p]:
            if os.path.isfile(f):
                s = os.stat(f)
                out[os.path.abspath(f)] = [s.st_size, s.st_mtime_ns]
    return out


def install_state(game):
    """{"inputs": what install reads, "client": the client files it writes} as fingerprints."""
    import photo_head, club_history
    code = [os.path.join(HERE, n) for n in ("fo4_season.py", "photo_head.py", "club_history.py")] + \
           [os.path.join(HERE, "re", n) for n in ("bigdir.py", "refpack.py", "eadb.py")] + [os.path.join(ROOT, "extract_fifadb.py")]
    data = [FIFADB, CARD_EDITS, HEADS, CUSTOM_SEASONS, SEASON_ICONS, os.path.join(ROOT, "gamedata", "fo4_icons"),
            photo_head.DIR, club_history.OUT]
    client = [os.path.join("data", "data_0.big"), photo_head.ARCHIVE, MODULE_BIG, LABEL_BIG,
              *(os.path.join("data", "ui", "localization", f"{loc}.big") for loc in LOCALES)]
    return {"inputs": _fingerprint(code + data), "client": _fingerprint([os.path.join(game, p) for p in client])}


def cmd_install(a):
    import bigdir
    from refpack import refpack
    sys.path.insert(0, ROOT)
    from extract_fifadb import decrypt
    stamp = os.path.join(a.game, STAMP)
    if getattr(a, "if_needed", False):
        if not os.path.isfile(FIFADB) or not os.path.isfile(os.path.join(a.game, "data", "data_0.big")):
            print(f"install skipped: no {FIFADB} or no client in {a.game} (README: Setup)")
            return
        try:
            with open(stamp) as f:
                if json.load(f) == install_state(a.game):
                    print(f"{a.game} has this server's latest install")
                    return
        except (OSError, ValueError):
            pass
        print(f"installing into {a.game} (its data or the server's changed since the last install)", flush=True)
    if _game_running():
        raise SystemExit("close the game first (fifazf.exe is running)")
    data0, label, module = _originals(a.game)
    room = {n: size for n, off, size, usize in bigdir.entries(data0)}
    names = locale_additions()
    import club_history
    clubs = {loc: [(t, club_history.ea_teamnames(a.game, loc).get(str(t), str(t)), lg) for t, (lg, _) in OPEN_CLUBS.items()]
             for loc in LOCALES}
    files, want = {}, {}
    for entry, make in [(FIFADB_ENTRY, lambda shipped, trim: client_db())] + [
            (f"data/db2/{loc}.db.db2", lambda shipped, trim, loc=loc: locale_db(_plain(shipped), names, trim, clubs[loc]))
            for loc in LOCALES]:
        shipped = bigdir.read(data0, entry)  # inflated, encrypted
        for trim in (False, True):
            plain_db = make(shipped, trim)
            packed, usize = _seal(plain_db, shipped)
            if len(packed) <= room[entry]:
                break
        else:
            raise SystemExit(f"{entry}: {len(packed)} bytes do not fit its {room[entry]} in data_0.big "
                             "(fewer cards: fetch --min-ovr higher)")
        print(f"{entry}: {usize / 1e6:.2f} MB -> {len(packed) / 1e6:.2f} MB, slot {room[entry] / 1e6:.2f} MB"
              + (" (cardless names left out)" if trim else ""), flush=True)
        files[entry], want[entry] = (packed, usize), plain_db
    # the engine DB: its own slot if it still fits, else the space fifadb left free in its slot
    ng, n_new, n_looks = engine_db(bigdir.read(data0, NG_ENTRY), bigdir.read(data0, NG_META).decode("utf-8", "replace"), a.game)
    packed = refpack(ng)
    assert len(packed) < len(ng)  # stored or literal-only DB entries hang the boot (eatrax_song.py)
    at = {}
    if len(packed) > room[NG_ENTRY]:
        off = {n: o for n, o, s, u in bigdir.entries(data0)}[FIFADB_ENTRY]
        at[NG_ENTRY] = -(-(off + len(files[FIFADB_ENTRY][0])) // 0x800) * 0x800
        if at[NG_ENTRY] + len(packed) > off + room[FIFADB_ENTRY]:
            raise SystemExit(f"{NG_ENTRY}: {len(packed)} bytes fit neither its slot nor fifadb's free space")
    print(f"{NG_ENTRY}: {n_new} new players, {n_looks} admin page rows, {len(packed) / 1e6:.2f} MB"
          + (" (in fifadb's slot)" if at else f", slot {room[NG_ENTRY] / 1e6:.2f} MB"), flush=True)
    files[NG_ENTRY] = (packed, len(ng))
    bigdir.patch(data0, files, in_place=True, at=at)
    assert bigdir.read(data0, NG_ENTRY) == ng
    gfx, dds = bigdir.read(label, LABEL_GFX), bigdir.read(label, LABEL_DDS)
    icons = _icons(gfx, dds)
    gfx, dds = label_with_icons(gfx, dds, icons)
    bigdir.patch(label, {LABEL_GFX: gfx, LABEL_DDS: (refpack(dds), len(dds))})
    _patch_module(module)
    import club_history, photo_head
    clubs = club_history.install_names(a.game)  # the club careers' clubs FO3 has no name for
    heads = 0
    if photo_head.load() or os.path.isfile(os.path.join(BACKUP, "data_7.big")):  # the photo heads' files
        _original(os.path.join(a.game, photo_head.ARCHIVE))
        heads = photo_head.install(a.game, os.path.join(BACKUP, "data_7.big"))
    # check what the client will read
    for entry, plain_db in want.items():
        assert bytes(decrypt(bigdir.read(data0, entry))[16:]) == plain_db[16:], entry
    assert os.path.getsize(data0) == os.path.getsize(os.path.join(BACKUP, "data_0.big"))
    with open(stamp, "w") as f:
        json.dump(install_state(a.game), f)
    print(f"installed into {a.game}: {len(names)} new names, {clubs} new club names, {heads} photo heads, season icons "
          f"{', '.join(SEASON_NAMES[s] for s in sorted(icons))}")


def _plain(shipped):
    sys.path.insert(0, ROOT)
    from extract_fifadb import decrypt
    db = decrypt(bytes(shipped))
    db[:16] = b"SQLite format 3\x00"
    return bytes(db)


def cmd_hud(a):
    """The match HUD's season icons alone, for a client the seasons were installed into before
    them (install does it too): Module.big from its backup copy (made now if there is none)."""
    if _game_running():
        raise SystemExit("close the game first (fifazf.exe is running)")
    _patch_module(_original(os.path.join(a.game, MODULE_BIG)))
    print(f"{MODULE_BIG}: the match HUD shows each card's own season (backup in {BACKUP})")


def cmd_restore(a):
    if _game_running():
        raise SystemExit("close the game first (fifazf.exe is running)")
    if not os.path.isfile(os.path.join(BACKUP, "data_0.big")):
        raise SystemExit("nothing to restore (no backup/fo4season)")
    _originals(a.game)
    if os.path.isfile(os.path.join(a.game, STAMP)):  # the next install --if-needed installs again
        os.remove(os.path.join(a.game, STAMP))
    for lang in LOCALES:  # the TEAMNAMES install added club names to
        big = os.path.join(a.game, "data", "ui", "localization", f"{lang}.big")
        if os.path.isfile(os.path.join(BACKUP, os.path.basename(big))):
            _original(big)
    if os.path.isfile(os.path.join(BACKUP, "data_7.big")):  # the photo heads' files
        _original(os.path.join(a.game, "data", "data_7.big"))
    c = sqlite3.connect(FIFADB)
    open_clubs_back(c)
    rest_of_world_back(c)
    seasons = (*CLASSES.values(), WC18)
    for t in ("players", *PERM_TABLES):
        c.execute(f"DELETE FROM {t} WHERE spid / 1000000 IN ({','.join('?' * len(seasons))})", seasons)
    for t in NAME_TABLES:
        c.execute(f"DELETE FROM {t} WHERE playerid BETWEEN ? AND ?", (NEW_PID, NEW_PID + 99999))
    c.commit()
    print("client files restored; the FO4 seasons and WC18 are out of gamedata/fifadb.sqlite")


def cmd_selftest(a=None):
    d = nuxt('<script>window.__NUXT__=(function(a,b){return {x:[a,b,"c\\u002Fd"],y:{z:void 0,n:-1.5}}}(1,"s"));</script>')
    assert d == {"x": [1, "s", "c/d"], "y": {"z": None, "n": -1.5}}, d
    assert nuxt('window.__NUXT__={a:[]};') == {"a": []}
    assert _same_name("Lionel Messi", "Lionel Messi") > _same_name("Lionel Messi", "Lionel Gamba") >= 0
    assert _same_name("Lionel Messi", "Leandro Gamba") < 0.85 <= _same_name("Erling Haaland", "Erling Haland")
    assert _same_name("Rodri", "Rodrigo Hernandez Cascante") >= 0.85 > _same_name("Harry Kane", "Herbie Kane") - 1
    # trait1 bits as the client's PLAYER_TRAITS (Messi '17: finesse shot, long shot, speed dribbler, playmaker)
    assert sum(1 << FO3_TRAITS.index(t) for t in ("finesse-shot", "long-shot-taker", "speed-dribbler", "play-maker")) == 466944
    if os.path.exists(FIFADB):  # the Style rules against FO3's own 2017 cards
        with closing(sqlite3.connect(FIFADB)) as c:
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute("SELECT * FROM players WHERE spid / 1000000 = 88")]
        hit = sum(badges(r) == r["badges"] for r in rows)
        assert hit >= 0.98 * len(rows), (hit, len(rows))
        print(f"Style rules match {hit}/{len(rows)} 2017 cards exactly")
        tmp = os.path.join(tempfile.gettempdir(), "fo4_season_selftest.sqlite")
        open(tmp, "wb").write(client_db())
        with closing(sqlite3.connect(tmp)) as c:  # club careers on every season (Messi '17 and 22), one region's perms
            got = dict(c.execute("SELECT spid, historyopen FROM players WHERE spid IN (88158023, 22158023)"))
            wrong = c.execute("SELECT COUNT(*) FROM players WHERE historyopen != (playerid IN (SELECT playerid FROM playerhistory))").fetchone()[0]
            perms = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] > 0 for t in PERM_TABLES}
            locked = c.execute("SELECT COUNT(*) FROM permid WHERE spid >= 1000000 AND transferable != 1").fetchone()[0]
        os.remove(tmp)
        assert got == {88158023: 1, 22158023: 1} and wrong == 0, (got, wrong)
        assert locked == 0, locked  # every card can be found on the client's Market (Cafu's event cards)
        assert perms == {t: t == "permid" for t in PERM_TABLES}, perms
    px = [(0, 230, 100, 255)] * 8 + [(60, 64, 70, 255)] * 7 + [(0, 0, 0, 0)]
    b = _bc3(px)
    assert len(b) == 16 and b[0] == 255 and b[1] == 0
    assert _u30(127) == b"\x7f" and _u30(128) == b"\x80\x01" and _u30(300) == b"\xac\x02"
    assert _asm([b"\x09", ("@", "a"), (0x10, "a"), (0x12, "b"), b"\x02", ("@", "b")]) == b"\x09\x10\xfc\xff\xff\x12\x01\x00\x00\x02"
    # WC18: futwiz's list row and card page (cut down), Wikipedia's club names, the rating step
    row = ('<a href="/fifa18/worldcup/player/lionel-messi/90"><div class="card-19-pack card-19-pack-gold\n">'
           '<img src="https://cdn.futwiz.com/assets/img/fifa19/faces/158023.png"><img src="f/flags/52.png">'
           '<div class="card-19-pack-name">Messi</div> <div class="card-19-pack-rating">95</div>'
           '<div class="card-19-pack-position">RW</div><div class="card-19-pack-attnum card-19-pack-attnum1">90</div>'
           '<div class="card-19-pack-attlabel card-19-pack-attlabel1">PAC</div></div></a>')
    got = wc18_list(lambda p: row if p.endswith("page=0") else "")
    assert got == {90: {"fid": 90, "slug": "lionel-messi", "kind": "gold", "eaid": 158023, "nation": 52, "name": "Messi",
                        "rating": 95, "pos": "RW", "face": {"PAC": 90}}}, got
    page = wc18_page('<div>170cm</div><div>72kg</div><div>Med/High</div><div>WORKRATES</div><div>Left</div><div>FOOTED'
                     '</div><div>Dribbling</div><div>97</div><div>Agility</div><div>90</div><div>Dribbling</div><div>98</div>'
                     '<li><a href="/fifa18/player/lionel-messi/37084"><img src="b/badges/241.png" width="20"> 94 RW</a> '
                     '<span class="pull-right">Hero </span></li><li><a href="/fifa18/player/lionel-messi/20181"><img '
                     'src="b/badges/241.png" width="20"> 93 RW</a> </li><div>World Cup Fixtures</div>')
    assert page == {"stats": {"dribbling": 98, "agility": 90}, "face": {}, "height": 170, "weight": 72, "foot": "Left",
                    "workrate": "Med/High", "traits": [], "versions": [("/fifa18/player/lionel-messi/37084", 241, 94, "RW", "Hero"),
                                                                       ("/fifa18/player/lionel-messi/20181", 241, 93, "RW", "")]}, page
    traits = wc18_page('<div>Med/Med</div><div>WORKRATES</div><div class="mb-20">Traits: Shooting - Finesse Shot, '
                       'Passing - Long Passer (CPU AI Only), Flair, Corner Specialist</div>')["traits"]
    assert traits == ["Finesse Shot", "Long Passer", "Flair", "Corner Specialist"], traits
    assert {WC18_TRAITS[t] for t in traits if t in WC18_TRAITS} == {"finesse-shot", "long-passer", "flair"}
    assert set(WC18_TRAITS.values()) <= set(FO3_TRAITS)
    assert _team_like("Inter Milan", "Inter") == 0.95 and _team_like("Bayern Munich", "FC Bayern Munchen") > 0.8
    assert _team_like("Manchester City", "Manchester United") < 0.8
    teams = {241: ("FC Barcelona", 53), 10: ("Manchester City", 13), 11: ("Manchester United", 13)}
    assert wc18_team(["FC Barcelona", "Barcelona"], {241, 10}, teams) == 241
    assert wc18_team(["Manchester United F.C.", "Manchester United"], set(), teams) == 11
    assert wc18_team(["Al Taawoun FC", "Al Taawoun"], set(), teams) is None
    teams.update({1354: ("Portugal", 78), 304: ("Port Vale", 60), 112139: ("Al Batin", 350), 242: ("RC Deportivo", 53)})
    assert wc18_team(["Sporting CP"], {1354}, teams) is None and wc18_team(["FC Porto", "Porto"], set(), teams) is None
    assert wc18_team(["Al Ain FC", "Al Ain"], set(), teams) is None
    assert wc18_team(["Deportivo de La Coruña", "Deportivo La Coruña"], set(), teams) == 242
    moved = wc18_shift({k: 80 for k in STATS}, POS["RW"], 70)  # every weight set sums to 100
    assert set(moved.values()) == {70}, moved
    label = os.path.join(BACKUP, "Label.big")
    if os.path.exists(label):  # the WC18 badge out of the shipped Label.big
        import bigdir
        gfx, dds = bigdir.read(label, LABEL_GFX), bigdir.read(label, LABEL_DDS)
        icon, wc10 = wc18_icon(gfx, dds), _frame_icons(gfx, dds, ("47season",))["47season"]
        assert icon.size == wc10.size and icon.tobytes() != wc10.tobytes()
        print("WC18 badge ok")
    module = os.path.join(r"D:\FO3ID\32795", MODULE_BIG)
    if os.path.exists(module):  # the getSeason patch against the client's Module.gfx, in memory
        import bigdir
        gfx = bigdir.read(module, MODULE_GFX)
        new = module_with_match_seasons(gfx)
        if new is not None:  # an unpatched client: the patched one parses and is patched
            assert module_with_match_seasons(new) is None and len(new) > len(gfx)
        print("Module.gfx getSeason patch ok" + (" (installed)" if new is None else ""))
        new = module_with_market_clubs(gfx)
        if new is not None:
            assert module_with_market_clubs(new) is None
        t = "47"  # the club filter's SQL as TransferUtil builds it: Milan's former players too
        cond = "AND ( {0} BETWEEN {1} AND {2} ) ".replace("{0}", MARKET_CLUB[0] + t + MARKET_CLUB[1] + t + MARKET_CLUB[2])
        with closing(sqlite3.connect(FIFADB)) as c:
            now, ever = (c.execute(f"SELECT COUNT(*) FROM players WHERE 1 {q}").fetchone()[0]
                         for q in ("AND ( teamid BETWEEN 47 AND 47 ) ", cond.replace("{1}", t).replace("{2}", t)))
        assert ever > now > 0, (now, ever)
        print("Module.gfx Market club filter patch ok" + (" (installed)" if new is None else ""))
    d = tempfile.mkdtemp()  # install --if-needed: a file changed, added or gone changes the fingerprint
    f = os.path.join(d, "x")
    open(f, "w").write("a")
    one = _fingerprint([d])
    open(f, "w").write("ab")
    assert _fingerprint([d]) != one and _fingerprint([os.path.join(d, "none")]) == {}
    shutil.rmtree(d)
    state = install_state(r"D:\FO3ID\32795")
    assert FIFADB in state["inputs"] and os.path.abspath(__file__) in state["inputs"]
    assert club_in([[52, 2013, 2017, 0], [47, 2017, 2020, 0]]) == 47 and club_in([[45, 2010, 2017, 0]]) == 45
    assert club_in([[45, 2018, -1, 0]]) is None and club_in([[55, 2008, 2012, 0]], 2010) == 55
    assert club_joined([[47, 2014, 2016, 0], [12, 2014, 2015, 1]], 2014) == 47 and club_joined([[47, 2014, 2016, 0]], 2015) is None
    game = r"D:\FO3ID\32795"
    if os.path.exists(os.path.join(game, "data", "data_5.big")):  # the clubs into an engine DB, in memory
        import bigdir, eadb
        data0 = os.path.join(BACKUP, "data_0.big")
        data0 = data0 if os.path.isfile(data0) else os.path.join(game, "data", "data_0.big")
        db, meta = bigdir.read(data0, NG_ENTRY), bigdir.read(data0, NG_META).decode("utf-8", "replace")
        assert kit_colors(game, 48, 0)[0][2] > 120  # Napoli's home kit is blue
        _, names, raws = eadb.db_tables(db)
        assert sorted(engine_clubs(raws, names, meta, game)) == sorted(OPEN_CLUBS)

        def rows(name):
            m = re.search(r'<table name="%s" shortname="(\w+)".*?</table>' % name, meta, re.S)
            fld = {n: (sn, int(lo)) for n, sn, lo in re.findall(r'<field name="(\w+)" shortname="(\w+)"[^>]*rangelow="(-?\d+)"', m.group())}
            t = eadb.Table(raws[names.index(m.group(1).encode())])
            return [{n: t.get(r, sn) + lo if t.fields[sn][0] else t.get(r, sn) for n, (sn, lo) in fld.items()} for r in t.rows]
        milan = next(r for r in rows("teams") if r["teamid"] == 47)
        assert milan["assetid"] == 47 and milan["teamname"] == "Milan" and max(milan["teamcolor1r"], milan["teamcolor2r"]) > 150 > milan["teamcolor2b"], milan  # red and black
        assert len([k for k in rows("teamkits") if k["teamtechid"] == 47]) == 3
        assert any(r["teamid"] == 47 and r["leagueid"] == 31 for r in rows("leagueteamlinks"))
        print("open clubs ok: engine rows, Milan red and black")
    print("fo4_season selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--min-ovr", type=int, default=72, help="lowest rating fifaaddict shows in its search")
    f.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "fo4_cache"))
    sub.add_parser("collect")
    sub.add_parser("import").add_argument("file")
    w = sub.add_parser("wc18")
    w.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "wc18_cache"))
    sub.add_parser("build")
    sub.add_parser("selftest")
    inst = sub.add_parser("install")
    inst.add_argument("--if-needed", action="store_true", help="only when the client is not as the last install left it")
    for p in (w, inst, *(sub.add_parser(name) for name in ("hud", "restore"))):
        p.add_argument("--game", default=os.environ.get("FO3_GAME", r"D:\FO3ID\32795"))
    a = ap.parse_args()
    {"fetch": cmd_fetch, "collect": cmd_collect, "import": cmd_import, "wc18": cmd_wc18, "build": cmd_build,
     "install": cmd_install, "hud": cmd_hud, "restore": cmd_restore, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    main()
