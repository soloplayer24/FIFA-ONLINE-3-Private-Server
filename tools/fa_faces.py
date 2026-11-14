"""Fill in the player mini faces (or, with --crests, club crests) the client's cache lacks, from
fifaaddict.com. User: "update semua mini face yang belum ada ambil dari FIFAADDICT"; crests for
GitHub issue #3 ("team emblems display incorrectly": most crests are not in the cache, so the
client shows its default one).

The client shows faces from its cache (_cache/games/fo3id/externalAssets/players, served by
http_config /cdn/): p<spid>.png for a season face, else p<player id>.png. fifaaddict serves
the same 128x128 PNGs at fo3img/players/p<spid>.png and p<player id>.png (byte-identical to the
cached ones; most FO3 cards have a season picture, often shared by some seasons of a player:
issue #36, every Drogba season showed his one p31432.png), so this adds both kinds the cache
lacks. Order: my club's cards, faces the game already asked for, every Market card, dearest first,
then every other card of the DB (issue #36: "Scan the entire player database and all season cards").
A player's picture fifaaddict does not have (or refuses) comes from FC Online (FO4) instead (user:
"jika download dari fo3 tidak ada pindah ke fo4"): Nexon's CDN has the same 128x128 cut-out
PNGs by the same EA player id (FO4_FACE; a missing one answers 403), e.g. Maradona p190042, which
fifaaddict lacks. The server's new players (700000 up, from FO4; user: "pemain baru kenapa tidak di
search by nama") have no EA id here, but fifaaddict has each FO4 card's picture by its own card id
(FA4: the uid of gamedata/fo4_cards.json), and their cards are found by the birth date and name that
gave them their player id (fo4_season.py's gamedata/fo4_pids.json): their cards' and their own
pictures come from there (an unknown id gets a silhouette, FA4_NONE: not there).
Crests are crests/large/s<teamid>.png (256x256) and crests/small/s<teamid>.png (128x128);
fifaaddict has both sizes (fo3img/crests/large/s<id>.png and fo3img/crests/s<id>.png). Order:
crests the game already asked for, then every club with a 2017 squad (the club picker's).
With --staffs, the staff pictures (staffs/<stfid>.png; user: "boleh download"): the cache has 7 of
the server's staff, fifaaddict has them all at fo3img/staffs/<its id>.png (its 10xx..40xx and
9117..9122 are the client's 11xx..41xx and 9500..9505, http_config.STAFF_CATALOGUE).
With --leagues, the league logos of the league and cup selection screens (league/ca/l<id>.png;
issue #36: the cache has one) from fifaaddict's fo3img/league/l<id>.png.
With --uniforms, the kit pictures the club picker shows (uniforms/j0_<teamid>.png home, j1 away, j2
third; issue #43: "where to get crest and uniform?", the cache has 21 clubs') from fifaaddict's
fo3img/uniforms/.
One request every --delay seconds; existing files are never touched; gamedata/fa_faces.json
(fa_crests.json, ...) records what was added or is not there (404) so a rerun resumes, and --remove deletes
only the added files. A refusal or a timeout is not recorded: the run waits longer after each one
in a row (10 s, doubling, up to 5 min) and gives up after TRIES in a row. --all fetches every kind,
the small ones first. The admin page's Aset tab runs this in the background; its Stop button
creates gamedata/assets_fetch.stop, and the run saves its record and quits.

Usage: python -I tools/fa_faces.py [--crests | --uniforms | --staffs | --leagues | --all] [--delay 0.5] [--limit N] [--remove]
"""
import argparse, hashlib, json, os, re, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ARGS, sys.argv = sys.argv[1:], sys.argv[:1]  # http_config reads a port from argv at import
import http_config as hc

EXT = os.path.join(hc.ASSETS, "externalAssets")
TRIES = 12  # errors in a row (other than "not there") before a run gives up; none of them is recorded
FA = "https://fifaaddict.com/fo3img/"
FO4_FACE = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/players/p{}.png"
FA4 = "https://s1.fifaaddict.com/fo4/players/{}.png"  # an FO4 card's picture by fifaaddict's card id
FA4_NONE = "17675d7e0863363f9da1f99c81321594e7056f38c6a9024270219f393f4a2029"  # its silhouette (sha256)
MISSING = {"fifaaddict.com": {404}, "fco.dn.nexoncdn.co.kr": {403, 404}}  # each source's "not there"


def fo4_new_players():
    """{spid: fifaaddict card id} of the server's new players' FO4 cards (fo4_season.new_player_cards)."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import fo4_season
    return fo4_season.new_player_cards()
MANIFESTS = {"faces": os.path.join(ROOT, "gamedata", "fa_faces.json"),
             "crests": os.path.join(ROOT, "gamedata", "fa_crests.json"),
             "uniforms": os.path.join(ROOT, "gamedata", "fa_uniforms.json"),
             "staffs": os.path.join(ROOT, "gamedata", "fa_staffs.json"),
             "leagues": os.path.join(ROOT, "gamedata", "fa_leagues.json")}
STOP = os.path.join(ROOT, "gamedata", "assets_fetch.stop")  # admin.ASSET_STOP


def _asked(pattern):
    """Ids in the /cdn/externalAssets/<pattern> requests the server log holds, in order."""
    with open(hc.LOG, encoding="utf-8", errors="replace") as f:
        return [int(m.group(1)) for m in re.finditer(r"GET /cdn/externalAssets/" + pattern, f.read())]


def _rel(entry):
    """Manifest entry -> path under externalAssets (older face manifests hold bare player ids)."""
    return f"players/p{entry}.png" if str(entry).isdigit() else entry


def wanted_faces():
    """[(path under externalAssets, [urls to try])] of faces in download order (club, asked, Market by
    price, the rest of the DB): each card's own picture p<spid>.png (the game asks for it first) and
    its player's p<player id>.png (its fallback; fifaaddict's, else FO4's). fifaaddict has no picture
    of its own for a 2017 card (gen_teams.LEGACY_SEASON: its page shows the player's) nor for the
    seasons the server adds (FO4 LIVE, WC18, own): only their player's. The players FO3 does not have
    (fo4_season.NEW_PID, 700000 up) get their FO4 cards' pictures from fifaaddict (FA4)."""
    from contextlib import closing
    st = hc._state()
    club = [int(p["spid"]) for t in st["teams"].values() for p in t["players"]]
    fa = hc.fa_prices()
    with closing(hc._db()) as c:
        every = [r[0] for r in c.execute("SELECT spid FROM players ORDER BY spid")]
    skip = {*hc.ADDED_SEASONS, hc.gen_teams.LEGACY_SEASON}
    new = fo4_new_players()  # a new player's own picture: his newest FO4 card's
    own = {s % 1000000: uid for s, uid in sorted(new.items(), key=lambda kv: kv[0] // 1000000 == 300)}  # 26 LIVE last: it wins

    def ids(s):
        if s % 1000000 >= 700000:
            return [i for i in (s, s % 1000000) if i in new or i in own]
        return ([s] if s >= 1000000 and s // 1000000 not in skip else []) + [s % 1000000]

    def urls(i):
        if i in new or i in own:
            return [FA4.format(new.get(i) or own[i])]
        return [f"{FA}players/p{i}.png"] + ([FO4_FACE.format(i)] if i < 1000000 else [])
    cards = club + _asked(r"players/p(\d+)\.png") + sorted(fa, key=lambda s: -fa[s]) + every
    return [(f"players/p{i}.png", urls(i)) for i in dict.fromkeys(i for s in cards for i in ids(s))]


def wanted_crests():
    """[(path under externalAssets, url)] of both crest sizes (asked first, then every club)."""
    clubs = [t for _, leagues in hc.club_index() for _, teams in leagues for t in teams]
    ids = dict.fromkeys(_asked(r"crests/(?:large|small)/s(\d+)\.png") + clubs)
    return [job for t in ids for job in ((f"crests/large/s{t}.png", f"{FA}crests/large/s{t}.png"),
                                         (f"crests/small/s{t}.png", f"{FA}crests/s{t}.png"))]


def wanted_uniforms():
    """[(path under externalAssets, url)] of the club picker's kit pictures, j0 (home), j1 (away) and j2
    (third) of every club (asked first)."""
    clubs = [t for _, leagues in hc.club_index() for _, teams in leagues for t in teams]
    ids = dict.fromkeys(_asked(r"uniforms/j\d_(\d+)\.png") + clubs)
    return [(f"uniforms/j{k}_{t}.png", f"{FA}uniforms/j{k}_{t}.png") for t in ids for k in range(3)]


def wanted_leagues():
    """[(path under externalAssets, url)] of the league logos the league and cup selection screens
    draw (Enums.EXTERNAL_LEAGUE_CA: league/ca/l<leagueid>.png; issue #36, the cache has one):
    fifaaddict's fo3img/league/l<id>.png (147x94; the screens fit it to their box). Asked first,
    then every league with a club the club picker lists."""
    leagues = [lid for _, ls in hc.club_index() for lid, _ in ls]
    ids = dict.fromkeys(_asked(r"league/(?:ca|be|fe|vi)/l(\d+)\.png") + leagues)
    return [(f"league/ca/l{lid}.png", f"{FA}league/l{lid}.png") for lid in ids]


def wanted_staffs():
    """[(path under externalAssets, url)] of every staff picture the server can hand out."""
    fa = lambda s: s - 100 if 1100 < s < 4900 else s - 383 if 9500 <= s <= 9505 else s
    return [(f"staffs/{s}.png", f"{FA}staffs/{fa(s)}.png") for s in hc.STAFF_CATALOGUE]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--crests", action="store_true", help="club crests instead of player faces")
    ap.add_argument("--uniforms", action="store_true", help="the club picker's kit pictures")
    ap.add_argument("--staffs", action="store_true", help="staff pictures instead of player faces")
    ap.add_argument("--leagues", action="store_true", help="league logos (league and cup selection screens)")
    ap.add_argument("--all", action="store_true", help="every kind, the small ones first")
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--remove", action="store_true", help="delete the files this tool added")
    a = ap.parse_args(ARGS)
    kinds = (["staffs", "leagues", "crests", "uniforms", "faces"] if a.all else
             ["crests" if a.crests else "uniforms" if a.uniforms else "staffs" if a.staffs else
              "leagues" if a.leagues else "faces"])
    for kind in kinds:
        if not run(kind, a):
            break


def run(kind, a):
    """Fetch (or with --remove delete) one kind; False when the run stopped (Stop, or no answers)."""
    manifest = MANIFESTS[kind]
    man = json.load(open(manifest)) if os.path.isfile(manifest) else {"added": [], "failed": {}}
    # only "not there" (404) is final: a refusal, a timeout or a busy server is tried again (issue #36:
    # fifaaddict.com refused one user's run after 2 crests and the other 1,049 were never asked again);
    # a player's picture fifaaddict lacked is asked once more, now that FO4 may have it
    fo4 = man.get("fo4") or kind != "faces"
    man = {"added": [_rel(e) for e in man["added"]], "fo4": True,
           "failed": {_rel(k): v for k, v in man["failed"].items() if v == "404" and (fo4 or not re.fullmatch(r"players/p\d{1,6}\.png", _rel(k)))}}
    if a.remove:
        for rel in man["added"]:
            f = os.path.join(EXT, rel)
            if os.path.isfile(f):
                os.remove(f)
        print(len(man["added"]), kind, "removed")
        if os.path.isfile(manifest):
            os.remove(manifest)
        return True
    jobs = {"crests": wanted_crests, "uniforms": wanted_uniforms, "staffs": wanted_staffs, "leagues": wanted_leagues,
            "faces": wanted_faces}[kind]()
    todo = [(rel, urls if isinstance(urls, list) else [urls]) for rel, urls in jobs
            if not os.path.isfile(os.path.join(EXT, rel)) and rel not in man["failed"]]
    if a.limit:
        todo = todo[:a.limit]
    print(len(todo), kind, "to fetch", flush=True)
    def save():
        os.makedirs(os.path.dirname(manifest), exist_ok=True)
        with open(manifest, "w") as f:
            json.dump(man, f)
    done = errors = 0
    going = True
    for i, (rel, urls) in enumerate(todo):
        if os.path.isfile(STOP):
            print("stopped", flush=True)
            going = False
            break
        time.sleep(a.delay + (min(300, 10 * 2 ** (errors - 1)) if errors else 0))  # longer after each error in a row
        body, missing = None, 0
        for url in urls:  # fifaaddict's, then FO4's
            req = urllib.request.Request(url, headers={"User-Agent": f"Mozilla/5.0 (FO3 private server {kind} sync)"})
            try:
                body = urllib.request.urlopen(req, timeout=60).read()
                if not body.startswith(b"\x89PNG"):
                    raise ValueError("not a PNG")
                if hashlib.sha256(body).hexdigest() == FA4_NONE:  # fifaaddict's FO4 silhouette: none
                    body, missing = None, missing + 1
                    continue
                break
            except Exception as e:
                body = None
                if getattr(e, "code", None) in MISSING.get(urllib.parse.urlsplit(url).hostname, {404}):
                    missing += 1  # an answer: not there
                else:
                    print(f"{rel}: {urllib.parse.urlsplit(url).hostname} {str(getattr(e, 'code', '') or e)[:80]}", flush=True)
        if body is not None:
            errors = 0
            path = os.path.join(EXT, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(body)
            man["added"].append(rel)
            done += 1
        elif missing == len(urls):
            man["failed"][rel] = "404"
            errors = 0
        else:
            errors += 1
            if errors == TRIES:
                print(f"no answer {TRIES} times in a row: stopped, press Download again later", flush=True)
                going = False
                break
        if i % 10 == 9:
            save()
            print(f"{i + 1}/{len(todo)} fetched {done}, not there {len(man['failed'])}", flush=True)
    save()
    print(f"{kind} done:", done, "added,", len(man["failed"]), "not there in total", flush=True)
    return going


if __name__ == "__main__":
    main()
