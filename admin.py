"""Admin page for the FO3ID private server: http://127.0.0.1:8088/ (localhost only).

Started by http_config.py in its own thread (env FO3_ADMIN_PORT, default 8088, 0 = off). The
page (admin/index.html; its words come from the I18N tables at the end of this file, one per
language, and the page has a language switch) calls the JSON API below, which reads and edits
gamedata/state.json through http_config's own helpers: EP, players (add / remove / grade / Tim
inti-Cadangan), packs, Bag gifts, the daily login reward table, scout cooldowns, the server log,
and the Shop catalog (gamedata/shop.json: every tab, new packs / items / packages, prices, hidden
products; issue #10) with the client's own shop icons, and the card database (gamedata/fifadb.sqlite:
a card's stats, positions, traits and club, club crests, install into the game; issue #38).

Guards: only 127.0.0.1 / localhost may connect, and every change must be a POST carrying the
X-FO3-Admin header (a page on another origin cannot send it without a CORS preflight this server
never grants). ponytail: no login; anyone on this PC can use it, add a password if that matters.
"""
import base64
import binascii
import functools
import hashlib
import http.server
import importlib.util
import io
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from contextlib import closing

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "admin", "index.html")
POS_NAMES = "GK SW RWB RB RCB CB LCB LB LWB RDM CDM LDM RM RCM CM LCM LM RAM CAM LAM RF CF LF RW RS ST LS LW".split()
API = {}
READ_ONLY = {"summary", "players", "search", "packs", "daily", "playtime", "ladder_rewards", "match_rewards", "vm_chat", "missions", "prematch", "economy", "log", "shop",
             "notice", "assets", "card_meta", "card", "card_edits", "crest", "seasons", "mod_export", "teamcolors", "koth", "koth_rewards", "world", "vm_gen", "face", "heads", "photo_head", "staff"}  # the rest must be POST + header
hc = None  # the http_config module, set by start() (or by tests)


def tr(lang, key, *args):
    """The I18N text of key in lang (Indonesian for an unknown lang or key) with {0}.. filled in;
    a tuple arg is (key, *args) of a nested text, anything else goes in as it is."""
    tb = I18N.get(lang) or I18N["id"]
    s = tb.get(key) or I18N["id"].get(key) or key
    return s.format(*(tr(lang, *a) if isinstance(a, tuple) else a for a in args))


class AdminError(Exception):
    """A message for the page: AdminError(key, *args), translated by tr()."""

    def text(self, lang):
        return tr(lang, *self.args)


def api(name):
    def reg(fn):
        API[name] = fn
        return fn
    return reg


def coach(st):
    return hc.my_coach(st)


def team(st):
    return st["teams"][str(hc._default_slot(st))]


def names(spids):
    """{spid: player name} from fifadb."""
    spids = sorted({int(s) for s in spids})
    if not spids:
        return {}
    with closing(hc._db()) as c:
        return {s: n for s, n in c.execute(
            f"""SELECT p.spid, COALESCE(n.full_eng, n.full, n.short) FROM players p JOIN nameseng n ON n.playerid = p.playerid
                WHERE p.spid IN ({','.join('?' * len(spids))})""", spids)}


def row_name(row):
    """The player name of an engine row (a player id or a spid), or the row as text."""
    with closing(hc._db()) as c:
        r = c.execute("SELECT COALESCE(full_eng, full, short) FROM nameseng WHERE playerid = ?", (row % 1000000,)).fetchone()
    return r[0] if r else str(row)


def player(st, pid):
    p = next((x for x in team(st)["players"] if hc.iid(x) == str(pid)), None)
    if p is None:
        raise AdminError("e_player_missing")
    return p


def rel(path):
    """A settings file's path for the page: relative to the server folder when on its drive."""
    return os.path.relpath(path, HERE) if os.path.splitdrive(path)[0] == os.path.splitdrive(HERE)[0] else path


def as_int(v, lo, hi, what):
    """v as an int in lo..hi; what = a field name, or (I18N key,) of the page's label for it."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise AdminError("e_number", what)
    if not lo <= v <= hi:
        raise AdminError("e_range", what, lo, hi)
    return v


@api("summary")
def summary(p):
    st = hc._state(); c = coach(st); slot = hc._default_slot(st)
    d = c.get("daily") or {"last": "", "streak": 0, "total": 0}
    return {"coach": {"name": c.get("name"), "ep": c.get("ep", hc.START_EP), "teamid": c.get("teamid")},
            "vip_until": time.strftime("%Y-%m-%d", time.gmtime(hc.VIP_UNTIL / 1000)), "igr": True,
            "players": len(team(st)["players"]), "max_players": hc.MAX_PLAYERS,
            "team2": sum(1 for x in team(st)["players"] if x.get("team2")),
            "packs": sum(i["remains"] for i in c.get("items", [])), "bag": len(c.get("bag", [])),
            "daily": {**d, "today": hc.today_wib(), "granted_today": d.get("last") == hc.today_wib()},
            "scouts": hc.coach_scouters(st, slot),
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + hc.WIB)) + " WIB"}


@api("players")
def players(p):
    st = hc._state(); t = team(st)
    pos = {str(s.get("id") or s["spid"]): s["pos"] for s in t["squad"]["positions"]}
    nm = names(x["spid"] for x in t["players"])
    out = []
    for x in t["players"]:
        i, g = hc.iid(x), hc.grade_of(x)
        out.append({"id": i, "spid": x["spid"], "name": nm.get(x["spid"], "?"), "season": x["spid"] // 1000000,
                    "ovr": hc.card_ovr(x["spid"]) + hc.GRADE_BOOST[g], "grade": g, "price": hc.card_price(x["spid"], g),
                    "role": "MAIN" if pos.get(i, 99) < hc.POS_SUB else "SUB" if i in pos else "RES",
                    "team2": bool(x.get("team2"))})
    return {"players": out}


@api("search")
def search(p):
    q = str(p.get("q") or "").strip()
    if len(q) < 2:
        return {"rows": []}
    with closing(hc._db()) as c:
        rows = c.execute(f"""SELECT p.spid, COALESCE(n.full_eng, n.full, n.short), {hc.POSOVR}, p.preferredposition1
                             FROM players p JOIN nameseng n ON n.playerid = p.playerid
                             WHERE n.full LIKE ?1 OR n.short LIKE ?1 OR n.full_eng LIKE ?1 OR n.short_eng LIKE ?1
                                   OR p.spid = ?2 OR p.playerid = ?2
                             ORDER BY 3 DESC LIMIT 60""", (f"%{q}%", int(q) if q.isdigit() else -1)).fetchall()
    return {"rows": [{"spid": s, "name": n, "season": s // 1000000, "ovr": o, "pos": POS_NAMES[pp] if 0 <= pp < 28 else "?",
                      "market": hc.on_market(s), "price": hc.card_price(s)} for s, n, o, pp in rows]}


@api("packs")
def packs(p):
    return {"packs": [{"itemid": i, "name": n, "price": hc.pack_price(i)} for i, (n, _) in hc.PACKS.items()]}


@api("ep")
def set_ep(p):
    st = hc._state(); c = coach(st)
    if "set" in p:
        c["ep"] = as_int(p["set"], 0, 10 ** 13, "EP")
    else:
        hc.add_ep(st, as_int(p.get("add"), -10 ** 13, 10 ** 13, "EP"))
        c["ep"] = max(0, c["ep"])
    hc._save(st)
    return {"ep": c["ep"]}


@api("player_add")
def player_add(p):
    st = hc._state(); t = team(st)
    spid, grade = as_int(p.get("spid"), 1, 10 ** 9, "spid"), as_int(p.get("grade", 1), 1, 10, ("grade",))
    if not names([spid]):
        raise AdminError("e_no_spid")
    if len(t["players"]) >= hc.MAX_PLAYERS:
        raise AdminError("e_club_full", hc.MAX_PLAYERS)
    x = hc.add_player(t, spid, grade)
    hc._save(st)
    return {"id": hc.iid(x)}


@api("player_grade")
def player_grade(p):
    st = hc._state()
    player(st, p.get("id"))["grade"] = as_int(p.get("grade"), 0, 10, ("grade",))
    hc._save(st)
    return {"ok": True}


@api("player_remove")
def player_remove(p):
    st = hc._state(); t = team(st); x = player(st, p.get("id"))
    if hc.in_squad(t, x):
        raise AdminError("e_in_squad")
    hc.remove_player(t, x)
    hc._save(st)
    return {"ok": True}


@api("player_team")
def player_team(p):
    st = hc._state(); t = team(st); x = player(st, p.get("id"))
    if p.get("team") == "second":
        if hc.in_squad(t, x):
            raise AdminError("e_squad_first")
        x["team2"] = True
    else:
        x.pop("team2", None)
    hc._save(st)
    return {"ok": True}


@api("team2_clear")
def team2_clear(p):
    """Every Tim Cadangan card back to Tim inti (they went there automatically while Tim inti held 36)."""
    st = hc._state(); n = 0
    for x in team(st)["players"]:
        n += bool(x.pop("team2", None))
    hc._save(st)
    return {"moved": n}


def give_pack(st, itemid, count):
    inv = coach(st).setdefault("items", [])
    stack = next((i for i in inv if i["itemid"] == itemid), None)
    if stack is None:
        stack = {"_id": hc.new_iid(), "itemid": itemid, "remains": 0}
        inv.append(stack)
    stack["remains"] += count


@api("give_pack")
def api_give_pack(p):
    st = hc._state()
    itemid = as_int(p.get("itemid"), 1, 10 ** 10, ("pack",))
    if itemid not in hc.PACKS and itemid not in hc.ITEMS:
        raise AdminError("e_unknown_item")
    give_pack(st, itemid, as_int(p.get("count", 1), 1, 999, ("count",)))
    hc._save(st)
    return {"ok": True}


@api("bag_send")
def bag_send(p):
    """A gift in the Bag (Item saya > Items): EP and/or a pack and/or a player."""
    st = hc._state()
    items = hc.reward_items({"ep": as_int(p.get("ep") or 0, 0, 10 ** 12, "EP"),
                             "pack": as_int(p["pack"], 1, 10 ** 10, ("pack",)) if p.get("pack") else None,
                             "count": as_int(p.get("count") or 1, 1, 999, ("pack_count",))})
    if any(i["itemid"] != hc.EP_ITEM and i["itemid"] not in hc.PACKS for i in items):
        raise AdminError("e_unknown_pack")
    players_ = []
    if p.get("spid"):
        spid = as_int(p["spid"], 1, 10 ** 9, "spid")
        if not names([spid]):
            raise AdminError("e_no_spid")
        players_ = [{"spid": spid, "grade": as_int(p.get("grade") or 1, 1, 10, ("grade",))}]
    if not items and not players_:
        raise AdminError("e_gift_empty")
    e = hc.bag_add(st, str(p.get("title") or "Hadiah admin")[:60], items, players_, msg="Hadiah dari admin server.")
    hc._save(st)
    return {"id": e["_id"]}


# Staff on the account (issue #41: "Can we add staff to account using admin panel?"): Tim >
# Manajemen Staff shows them from the next /coach/me (any screen change).
@functools.lru_cache(maxsize=None)
def staff_names():
    """{stfid: the client's name (STAFF_NAME_<stfid>)}."""
    src = hc.asset_path(hc.LANG_PACK)
    s = hc.client_json(src, hc.LANG_ENTRY) if src else {}
    return {i: s.get(f"STAFF_NAME_{i}") or f"Staff {i}" for i in hc.STAFF_CATALOGUE}


@api("staff")
def staff(p):
    _, staffs = hc.my_staff(hc._state())
    names = staff_names()
    return {"max": hc.STAFF_MAX,
            "catalogue": [{"stfid": i, "name": names[i], "position": c[0], "helper": not c[1]} for i, c in hc.STAFF_CATALOGUE.items()],
            "staffs": [{"id": s["_id"], "stfid": s["stfid"], "name": names.get(s["stfid"], s["stfid"]),
                        "position": hc.STAFF_CATALOGUE[s["stfid"]][0], "grade": s["grade"], "life": s["life"],
                        "status": s.get("status")} for s in staffs]}


@api("staff_add")
def staff_add(p):
    st = hc._state(); _, staffs = hc.my_staff(st)
    stfid = as_int(p.get("stfid"), 1, 10 ** 6, ("st_staff",))
    if stfid not in hc.STAFF_CATALOGUE:
        raise AdminError("e_unknown_staff")
    grade = as_int(p.get("grade") or 1, 1, hc.STAFF_MAX_GRADE, ("grade",))
    count = as_int(p.get("count") or 1, 1, hc.STAFF_MAX, ("count",))
    if len(staffs) + count > hc.STAFF_MAX:
        raise AdminError("e_staff_full", hc.STAFF_MAX - len(staffs))
    staffs.extend(hc.staff_make(stfid, grade) for _ in range(count))
    hc._save(st)
    return {"count": len(staffs)}


@api("staff_remove")
def staff_remove(p):
    """Out of the account; out of its role frame first when appointed."""
    st = hc._state(); team_, staffs = hc.my_staff(st)
    s = hc.staff_find(staffs, str(p.get("id") or ""))
    if s is None:
        raise AdminError("e_no_staff")
    team_["staffslots"] = [None if x == s["_id"] else x for x in hc.staff_slots(team_)]
    staffs.remove(s)
    hc._save(st)
    return {"count": len(staffs)}


@api("daily")
def daily(p):
    st = hc._state()
    return {"table": hc.daily_table(), "status": coach(st).get("daily") or {"last": "", "streak": 0, "total": 0},
            "today": hc.today_wib(), "file": rel(hc.DAILY_FILE)}


@api("daily_set")
def daily_set(p):
    table = []
    for i, r in enumerate(p.get("table") or []):
        try:
            ep = as_int(r.get("ep") or 0, 0, 10 ** 12, "EP")
            pack = as_int(r["pack"], 1, 10 ** 10, ("pack",)) if r.get("pack") else None
            if pack is not None and pack not in hc.PACKS:
                raise AdminError("e_unknown_pack")
            if not ep and pack is None:
                raise AdminError("e_day_empty")
            table.append({**({"ep": ep} if ep else {}),
                          **({"pack": pack, "count": as_int(r.get("count") or 1, 1, 99, ("pack_count",))} if pack else {})})
        except AdminError as e:  # "Day 3: ..."
            raise AdminError("e_day", ("day_n", i + 1), e.args) from None
    if not 1 <= len(table) <= 31:
        raise AdminError("e_table_days")
    with open(hc.DAILY_FILE, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=1)
    return {"table": table}


@api("playtime")
def playtime(p):
    """Play time rewards: the steps, and today's play time and rewards taken."""
    st = hc._state()
    d, secs = hc.played_today(st) if st.get("coaches") else ({"rewarded": 0}, 0)
    return {"table": hc.playtime_table(), "minutes": secs // 60, "rewarded": d["rewarded"], "file": rel(hc.PLAYTIME_FILE)}


@api("playtime_set")
def playtime_set(p):
    """gamedata/playtime_rewards.json: [{minutes, ep, pack, count}], minutes rising."""
    table = []
    for r in p.get("table") or []:
        try:
            minutes, ep = int(r.get("minutes")), int(r.get("ep") or 0)
            pack = int(r["pack"]) if r.get("pack") else None
            count = int(r.get("count") or 1)
        except (TypeError, ValueError):
            raise AdminError("e_ptr_rows")
        if not 1 <= minutes <= 1440 or not 0 <= ep <= 10 ** 12 or pack is not None and pack not in hc.PACKS \
                or not 1 <= count <= 99 or not ep and pack is None or table and minutes <= table[-1]["minutes"]:
            raise AdminError("e_ptr_rows")
        table.append({"minutes": minutes, **({"ep": ep} if ep else {}), **({"pack": pack, "count": count} if pack else {})})
    if not 1 <= len(table) <= 10:
        raise AdminError("e_ptr_rows")
    with open(hc.PLAYTIME_FILE, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=1)
    return {"table": table}


@api("daily_reset")
def daily_reset(p):
    st = hc._state()
    coach(st)["daily"] = {"last": "", "streak": 0, "total": 0}
    hc._save(st)
    return {"ok": True}


@api("daily_grant")
def daily_grant(p):
    """Give the next day's reward now (as if logging in tomorrow), for testing."""
    st = hc._state()
    d = coach(st).setdefault("daily", {"last": "", "streak": 0, "total": 0})
    if d["last"] == hc.today_wib():
        d["last"] = hc.today_wib(time.time() - 86400)  # keep the streak going
    e = hc.daily_login(st)
    hc._save(st)
    return {"title": e["title"] if e else None}


@api("match_rewards")
def match_rewards(p):
    return {**hc.match_reward_cfg(), "file": rel(hc.MATCH_REWARD_FILE)}


@api("ladder_rewards")
def ladder_rewards(p):
    """Rank rewards: each division's season main and sub item, the weekly wins steps."""
    return {**hc.ladder_rewards(), "file": rel(hc.LADDER_REWARDS_FILE)}


def reward_item(it):
    """{itemid, count} of EP (1 .. 10^10) or a pack / item (1 .. 99), else AdminError."""
    try:
        itemid, count = int(it["itemid"]), int(it["count"])
    except (KeyError, TypeError, ValueError):
        raise AdminError("e_lr_rows")
    top = 10 ** 10 if itemid == hc.EP_ITEM else 99 if itemid in hc.PACKS or itemid in hc.ITEMS else 0
    if not 1 <= count <= top:
        raise AdminError("e_lr_rows")
    return {"itemid": itemid, "count": count}


@api("ladder_rewards_set")
def ladder_rewards_set(p):
    """gamedata/ladder_rewards.json: the divisions of the built-in list, [main, sub] each; weekly
    steps of 1 .. 500 wins, rising; reset = the built-in rewards."""
    if p.get("reset"):
        if os.path.isfile(hc.LADDER_REWARDS_FILE):
            os.remove(hc.LADDER_REWARDS_FILE)
        return ladder_rewards(p)
    names = [d["name"] for d in hc.ladder_rewards()["divs"]]
    try:
        divs = [{"name": d["name"], "items": [reward_item(d["items"][0]), reward_item(d["items"][1])]} for d in p["divs"]]
        wins = [{"from": int(w["from"]), "items": [reward_item(i) for i in w["items"]]} for w in p["wins"]]
    except (KeyError, IndexError, TypeError, ValueError):
        raise AdminError("e_lr_rows")
    steps = [w["from"] for w in wins]
    if [d["name"] for d in divs] != names or steps != sorted(set(steps)) or not 1 <= len(wins) <= 20 \
            or not all(1 <= f <= 500 for f in steps) or not all(w["items"] for w in wins):
        raise AdminError("e_lr_rows")
    os.makedirs(os.path.dirname(hc.LADDER_REWARDS_FILE), exist_ok=True)
    with open(hc.LADDER_REWARDS_FILE, "w", encoding="utf-8") as f:
        json.dump({"divs": divs, "wins": wins}, f, indent=1)
    return ladder_rewards(p)


@api("match_rewards_set")
def match_rewards_set(p):
    """EP and exp by result plus the reward cards (gamedata/match_rewards.json, read at every match end)."""
    res = p.get("result") or {}
    result = {k: {"ep": as_int((res.get(k) or {}).get("ep"), 0, 10 ** 9, "EP"),
                  "coach": as_int((res.get(k) or {}).get("coach"), 0, 10 ** 6, ("mr_coach_exp",)),
                  "player": as_int((res.get(k) or {}).get("player"), 0, 10 ** 6, ("mr_player_exp",))}
              for k in ("win", "draw", "loss")}
    cards = [prize_row(w, prize) for w, prize in p.get("cards") or []]
    if not 1 <= len(cards) <= 10:
        raise AdminError("e_prizes")
    cfg = {"result": result, "cards": cards}
    with open(hc.MATCH_REWARD_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    hc.shop_changed()  # a paid Shop prices the League Simulation coupon by these rewards
    return cfg


@api("vm_chat")
def vm_chat(p):
    n = hc.NEWS
    return {**hc.vm_chat_cfg(), "langs": hc.VM_CHAT_LANGS, "file": rel(hc.VM_CHAT_FILE),
            "regions": {k: v[0] for k, v in hc.NEWS_REGIONS.items()},
            "topics": [[(hc.NEWS_REGIONS[r][0] if r else hc.NEWS_GLOBAL[0]), [i["title"] for i in n["items"] if i["region"] == r][:8]]
                       for r in dict.fromkeys(i["region"] for i in n["items"])], "topicsat": n["at"]}


@api("vm_chat_set")
def vm_chat_set(p):
    """The virtual managers' chat: on/off (the Rank opponents'), language, the countries whose football
    news the global chat talks about besides the global news (gamedata/vm_chat.json, read at every line)."""
    if p.get("lang") not in hc.VM_CHAT_LANGS:
        raise AdminError("e_chat_lang")
    regions = p.get("newsregions") or []
    if not isinstance(regions, list) or any(r not in hc.NEWS_REGIONS for r in regions):
        raise AdminError("e_chat_region")
    cfg = {"enabled": bool(p.get("enabled")), "lang": p["lang"], "newsregions": [r for r in hc.NEWS_REGIONS if r in regions]}
    if cfg["newsregions"] != hc.vm_chat_cfg()["newsregions"]:  # the ticked countries' topics now, not at the next round
        threading.Timer(0.5, hc.news_refresh).start()
    os.makedirs(os.path.dirname(hc.VM_CHAT_FILE), exist_ok=True)
    with open(hc.VM_CHAT_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    return cfg


@api("vm_gen")
def vm_gen(p):
    """The virtual managers' generation (http_config.VM_GEN)."""
    return {"gen": hc.VM_GEN, "count": len(hc.virtual_managers())}


@api("vm_regenerate")
def vm_regenerate(p):
    """A new crowd (Generate ulang: the next generation) or the first one again (reset), at once;
    only this button changes it (user: "Hanya lewat admin panel, bukan setiap jalan game")."""
    hc.vm_set_generation(0 if p.get("reset") else hc.VM_GEN + 1)
    return vm_gen(p)


@api("missions")
def missions(p):
    cfg, end = hc.missions_cfg(), hc.week_end()
    week = lambda e: [{"name": n, "stat": s, "todo": t, "gp": g} for n, s, t, g in hc.week_quests(cfg, e)]
    return {**cfg, "langs": hc.MISSION_LANGS, "file": rel(hc.MISSIONS_FILE),
            "stats": {s: hc.MISSION_TEXT[s][cfg["lang"]][1] for s in hc.QUEST_STATS},
            "items": [[i, hc.item_def(i)["name"]] for i in (*hc.PACKS, *hc.ITEMS)],
            "questmax": hc.QUEST_MAX, "gpshopmax": hc.GP_SHOP_MAX,
            "thisweek": week(end), "nextweek": week(end + 7 * 86400), "weekend": hc.w3c(end)}


def save_missions(changes, drop=()):
    """gamedata/missions.json (read at every request) with changes, its other settings kept."""
    try:
        with open(hc.MISSIONS_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    cfg = {k: v for k, v in {**cfg, **changes}.items() if k not in drop}
    os.makedirs(os.path.dirname(hc.MISSIONS_FILE), exist_ok=True)
    with open(hc.MISSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1, ensure_ascii=False)
    return hc.missions_cfg()


@api("missions_set")
def missions_set(p):
    """Missions (issue #33): on/off, the texts' language, target and reward multipliers."""
    try:
        targets, rewards = float(p.get("targets")), float(p.get("rewards"))
    except (TypeError, ValueError):
        raise AdminError("e_mission_value")
    if p.get("lang") not in hc.MISSION_LANGS or not (0.1 <= targets <= 10 and 0 <= rewards <= 10):
        raise AdminError("e_mission_value")
    return save_missions({"enabled": bool(p.get("enabled")), "lang": p["lang"], "targets": targets, "rewards": rewards})


@api("quests_set")
def quests_set(p):
    """The Quest window's weekly quests (the list a week draws from, how many a week) and its reward
    boxes; reset = the built-in ones."""
    if p.get("reset"):
        return save_missions({}, drop=("quests", "questcount", "gpshop"))
    try:
        quests = [{"stat": q["stat"], "todo": int(q["todo"]), "gp": int(q["gp"]), "name": str(q.get("name") or "").strip()}
                  for q in p["quests"]]
        gpshop = [{"itemid": int(b["itemid"]), "gp": int(b["gp"])} for b in p["gpshop"]]
        count = int(p["questcount"])
    except (KeyError, TypeError, ValueError):
        raise AdminError("e_quest_value")
    if not 1 <= count <= min(hc.QUEST_MAX, len(quests)) or len(quests) > 50 \
            or any(q["stat"] not in hc.QUEST_STATS or not 1 <= q["todo"] <= 10000 or not 1 <= q["gp"] <= 10000
                   or len(q["name"]) > 60 for q in quests):
        raise AdminError("e_quest_value")
    if not 1 <= len(gpshop) <= hc.GP_SHOP_MAX or len({b["itemid"] for b in gpshop}) < len(gpshop) \
            or any(b["itemid"] not in hc.PACKS and b["itemid"] not in hc.ITEMS or not 1 <= b["gp"] <= 100000 for b in gpshop):
        raise AdminError("e_quest_shop")
    return save_missions({"quests": quests, "questcount": count, "gpshop": gpshop})


@api("prematch")
def prematch(p):
    return {**hc.match_prefs(hc._state()), "halfLengths": list(hc.MATCH_PREFS["halfLength"]),
            "stadiums": [{"id": s["id"], "name": s["name"]} for s in hc.stadium_choices()],
            "balls": [{"id": b, "name": n} for b, n in hc.ball_names().items()]}


@api("prematch_set")
def prematch_set(p):
    """The next matches' stadium / time of day / weather / length (issue #31): Liga and Cup have no
    control for them in the game, Rank's game setting screen does and saves its choice here too."""
    prefs = {}
    for k, ok in hc.MATCH_PREFS.items():
        v = p.get(k)
        v = int(v) if isinstance(v, str) and v.isdigit() else v
        if isinstance(v, bool) or v not in ok:
            raise AdminError("e_pm_value")
        prefs[k] = v
    st = hc._state()
    st["matchprefs"] = prefs
    hc._save(st)
    return hc.match_prefs(st)


def koth_text(tree):
    """A region set as the editor's text: one "Region: city, city" line per region."""
    return "\n".join(f"{name}: {', '.join(n for _, n in subs)}" for _, name, subs in tree)


@api("koth")
def koth(p):
    """King of the Hill's region set (issue #36): Indonesia, Singapore / Malaysia, Thailand, Vietnam
    or the page's own (custom)."""
    sets = {**hc.KOTH_SETS, "custom": hc.koth_custom()}
    return {"set": hc.koth_set(), "sets": {k: [[name, [n for _, n in subs]] for _, name, subs in v] for k, v in sets.items()},
            "texts": {k: koth_text(v) for k, v in sets.items()}, "file": rel(hc.KOTH_CUSTOM_FILE)}


@api("koth_custom_set")
def koth_custom_set(p):
    """The custom set from the editor's lines ("Region: city, city"; 1..20 regions of 1..30 cities,
    names 1..24 characters, none twice). A region or city of a name it had keeps its id, so a
    manager's region stays his; new ones take free ids (regions 41..99, cities <region>01..99)."""
    rows = []
    for n, line in enumerate(str(p.get("text") or "").splitlines(), 1):
        line = line.replace("：", ":").replace("，", ",").replace("、", ",")  # full-width ones too
        if not line.strip():
            continue
        if ":" not in line:
            raise AdminError("e_koth_line", n)
        name, cities = line.split(":", 1)
        rows.append((name.strip(), [c.strip() for c in cities.split(",") if c.strip()]))
    if not 1 <= len(rows) <= 20 or any(not 1 <= len(cs) <= 30 for _, cs in rows):
        raise AdminError("e_koth_count")
    names = [x for name, cs in rows for x in (name, *cs)]
    bad = next((x for x in names if not 1 <= len(x) <= 24 or names.count(x) > 1), None)
    if bad is not None:
        raise AdminError("e_koth_name", bad or "-")
    old = {name: (mid, dict((n, cid) for cid, n in subs)) for mid, name, subs in hc.koth_custom()}
    kept = {old[name][0] for name, _ in rows if name in old}
    free = (str(i) for i in range(41, 100) if str(i) not in kept)
    out = []
    for name, cs in rows:
        mid, had = old.get(name) or (next(free), {})
        mine = {had[c] for c in cs if c in had}
        spare = (f"{mid}{k:02d}" for k in range(1, 100) if f"{mid}{k:02d}" not in mine)
        out.append({"id": mid, "name": name, "cities": [{"id": had.get(c) or next(spare), "name": c} for c in cs]})
    os.makedirs(os.path.dirname(hc.KOTH_CUSTOM_FILE), exist_ok=True)
    with open(hc.KOTH_CUSTOM_FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    os.replace(hc.KOTH_CUSTOM_FILE + ".tmp", hc.KOTH_CUSTOM_FILE)
    return koth(p)


@api("koth_rewards")
def koth_rewards(p):
    """King of the Hill rewards (http_config.koth_rewards): weekly goal + division items, season
    division items + region ranking."""
    return {**hc.koth_rewards(), "file": rel(hc.KOTH_REWARDS_FILE)}


@api("koth_rewards_set")
def koth_rewards_set(p):
    """gamedata/koth_rewards.json: one item per reward; the divisions of the built-in list (worst
    first); weekly goal 1-100 wins or 1-200 matches, division wins 1-100 a week / 1-500 a season;
    1-20 ranking rows of ranks 1-1000, rising, apart; reset = the built-in rewards."""
    if p.get("reset"):
        if os.path.isfile(hc.KOTH_REWARDS_FILE):
            os.remove(hc.KOTH_REWARDS_FILE)
        return koth_rewards(p)
    names = ["DIVISION_" + d["name"] for d in reversed(hc.ladder_divisions())]
    one = lambda x: [reward_item(x["items"][0])]
    try:
        w, s = p["week"], p["season"]
        week = {"type": w["type"], "win": int(w["win"]), "match": int(w["match"]), "grade": int(w["grade"]), "items": one(w),
                "divs": [{"name": d["name"], "items": one(d)} for d in w["divs"]]}
        season = {"grade": int(s["grade"]), "divs": [{"name": d["name"], "items": one(d)} for d in s["divs"]],
                  "ranking": [{"from": int(r["from"]), "to": int(r["to"]), "items": one(r)} for r in s["ranking"]]}
    except (KeyError, IndexError, TypeError, ValueError):
        raise AdminError("e_kr_rows")
    ranks, last = season["ranking"], 0
    for r in ranks:
        if not last < r["from"] <= r["to"] <= 1000:
            raise AdminError("e_kr_rows")
        last = r["to"]
    if week["type"] not in ("winning", "match") or not 1 <= week["win"] <= 100 or not 1 <= week["match"] <= 200 \
            or not 1 <= week["grade"] <= 100 or not 1 <= season["grade"] <= 500 or not 1 <= len(ranks) <= 20 \
            or [d["name"] for d in week["divs"]] != names or [d["name"] for d in season["divs"]] != names:
        raise AdminError("e_kr_rows")
    os.makedirs(os.path.dirname(hc.KOTH_REWARDS_FILE), exist_ok=True)
    with open(hc.KOTH_REWARDS_FILE, "w", encoding="utf-8") as f:
        json.dump({"week": week, "season": season}, f, indent=1)
    return koth_rewards(p)


@api("koth_set")
def koth_set(p):
    if p.get("set") not in hc.KOTH_SETS and not (p.get("set") == "custom" and hc.koth_custom()):
        raise AdminError("e_koth_empty" if p.get("set") == "custom" else "e_koth_set")
    os.makedirs(os.path.dirname(hc.KOTH_FILE), exist_ok=True)
    with open(hc.KOTH_FILE, "w", encoding="utf-8") as f:
        json.dump({"regions": p["set"]}, f)
    return koth(p)


@api("economy")
def economy(p):
    return {**hc.ECONOMY, "shopfree": hc.SHOP_FREE}


@api("economy_set")
def economy_set(p):
    """The economy (issue #30: harder, configurable): player and pack price multipliers, a new
    manager's EP, the dynamic Market, the strict rules (kept when not given), and the Shop's free
    switch (the Shop tab's too)."""
    try:
        cfg = {"players": float(p.get("players")), "packs": float(p.get("packs")), "start_ep": int(p.get("start_ep"))}
    except (TypeError, ValueError):
        raise AdminError("e_eco_value")
    if not (0.1 <= cfg["players"] <= 100 and 0.1 <= cfg["packs"] <= 100 and 0 <= cfg["start_ep"] <= 10 ** 12):
        raise AdminError("e_eco_value")
    new = hc.set_economy({**cfg, "dynamic": bool(p.get("dynamic")), "strict": bool(p.get("strict", hc.ECONOMY["strict"]))})
    if "shopfree" in p:
        shop_free({"free": p["shopfree"]})  # saves the Shop: the game drops its Shop list
    else:
        hc.shop_changed()  # the prices follow the multipliers
    return {**new, "shopfree": hc.SHOP_FREE}


@api("scouts_reset")
def scouts_reset(p):
    st = hc._state()
    coach(st)["scout_used"] = {}
    hc._save(st)
    return {"ok": True}


@api("log")
def log(p):
    n = as_int(p.get("n", 80), 1, 1000, "n")
    try:
        with open(hc.LOG, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-n:]
    except OSError:
        lines = []
    return {"lines": [ln.rstrip("\n")[:300] for ln in lines]}


# --- The login notice's picture (issue #36) -------------------------------------------------------
# The page fits the chosen JPG / PNG into the popup's 800x720 box in the browser (canvas) and sends
# that PNG as base64; the server takes nothing else, so no image library is needed here.
NOTICE_W, NOTICE_H, NOTICE_MAX = 800, 720, 8 << 20


@api("notice")
def notice(p):
    f = hc.NOTICE_IMAGE
    if not os.path.isfile(f):
        return {"has": False, "w": NOTICE_W, "h": NOTICE_H}
    s = os.stat(f)
    return {"has": True, "w": NOTICE_W, "h": NOTICE_H, "size": s.st_size, "updated": int(s.st_mtime)}


@api("notice_set")
def notice_set(p):
    try:
        raw = base64.b64decode(str(p.get("png") or ""), validate=True)
    except (binascii.Error, ValueError):
        raise AdminError("e_notice_png")
    if len(raw) > NOTICE_MAX:
        raise AdminError("e_notice_size")
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR" or struct.unpack(">II", raw[16:24]) != (NOTICE_W, NOTICE_H):
        raise AdminError("e_notice_png")
    os.makedirs(os.path.dirname(hc.NOTICE_IMAGE), exist_ok=True)
    tmp = hc.NOTICE_IMAGE + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, hc.NOTICE_IMAGE)
    return notice(p)


@api("notice_delete")
def notice_delete(p):
    if os.path.isfile(hc.NOTICE_IMAGE):
        os.remove(hc.NOTICE_IMAGE)
    return notice(p)


# --- Missing pictures (issue #36: crests, league logos, portraits) -------------------------------
# The Aset tab runs tools/fa_faces.py in the background, one kind at a time: it fills the client's
# cache with the pictures it lacks from fifaaddict.com (only when the owner presses the button), and
# its manifests (gamedata/fa_<kind>.json) say what was added or failed. A rerun resumes.
# "careers" is no picture: the players' club careers up to the running season from Wikidata
# (tools/club_history.py, gamedata/club_history.json), which Database > Apply puts in the client.
ASSET_KINDS = {"faces": [], "crests": ["--crests"], "uniforms": ["--uniforms"], "leagues": ["--leagues"], "staffs": ["--staffs"], "careers": None}
ASSET_LOG = os.path.join(HERE, "gamedata", "assets_fetch.log")
ASSET_CMD = [sys.executable, "-I", os.path.join(HERE, "tools", "fa_faces.py")]  # tests swap it
CAREER_CMD = [sys.executable, "-I", os.path.join(HERE, "tools", "club_history.py"), "fetch"]  # tests swap it
CAREERS = os.path.join(HERE, "gamedata", "club_history.json")  # club_history.OUT
ASSET_STOP = os.path.join(HERE, "gamedata", "assets_fetch.stop")  # fa_faces.STOP: it saves its record and quits
ASSET_JOB = {"proc": None, "kind": None}


@api("assets")
def assets(p):
    proc = ASSET_JOB["proc"]
    running = proc is not None and proc.poll() is None
    counts = {}
    for kind in ASSET_KINDS:
        f = CAREERS if kind == "careers" else os.path.join(HERE, "gamedata", f"fa_{kind}.json")
        try:
            with open(f, encoding="utf-8") as fh:
                m = json.load(fh)
            counts[kind] = ({"added": len(m["players"]), "failed": m.get("missing", 0)} if kind == "careers" else
                            {"added": len(m.get("added", [])), "failed": len(m.get("failed", {}))})
        except (OSError, ValueError, KeyError):
            counts[kind] = {"added": 0, "failed": 0}
    try:
        with open(ASSET_LOG, encoding="utf-8", errors="replace") as fh:
            lines = [ln.rstrip("\n")[:200] for ln in fh.readlines()[-12:]]
    except OSError:
        lines = []
    return {"running": ASSET_JOB["kind"] if running else None, "last": ASSET_JOB["kind"], "counts": counts, "lines": lines,
            "exit": None if proc is None or running else proc.returncode}


def run_job(kind, cmd):
    """Start cmd in the background (one job at a time: the Aset downloads, the Database tab's
    install), its output in ASSET_LOG."""
    proc = ASSET_JOB["proc"]
    if proc is not None and proc.poll() is None:
        raise AdminError("e_asset_busy")
    os.makedirs(os.path.dirname(ASSET_LOG), exist_ok=True)
    if os.path.isfile(ASSET_STOP):
        os.remove(ASSET_STOP)
    with open(ASSET_LOG, "w", encoding="utf-8") as log:  # the child keeps its own handle
        ASSET_JOB["proc"] = subprocess.Popen(cmd, cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ASSET_JOB["kind"] = kind


@api("assets_fetch")
def assets_fetch(p):
    """Download one kind, or "all" the pictures one after another (issue #36: "a 'Scan All Missing
    Assets & Download' button")."""
    kind = p.get("kind")
    if kind not in ASSET_KINDS and kind != "all":
        raise AdminError("e_asset_kind")
    run_job(kind, CAREER_CMD if kind == "careers" else ASSET_CMD + (["--all"] if kind == "all" else ASSET_KINDS[kind]))
    return assets(p)


@api("assets_stop")
def assets_stop(p):
    proc = ASSET_JOB["proc"]
    if proc is not None and proc.poll() is None:
        open(ASSET_STOP, "w").close()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:  # a slow download: its last few records are lost, not its files
            proc.terminate()
            proc.wait(10)
        os.remove(ASSET_STOP)
    return assets(p)


# --- Card database (issue #38) -------------------------------------------------------------------
# The Database tab edits one card (spid = season * 1e6 + player id: one season's card of a player)
# in gamedata/fifadb.sqlite: its stats, positions, foot, weak foot, skill moves, work rates,
# Karakteristik (trait1, bits in fo4_season.FO3_TRAITS order) and club (a transfer: teamid and that
# club's league). CARD_EDITS keeps each edited card's original and new values (format in
# tools/fo4_season.py, whose build puts them back on the season cards it makes again); Kembalikan
# puts the original back. overallrating / potential follow the client rating's change. The game
# reads its own copy of the DB in data_0.big: "Terapkan ke game" runs fo4_season.py install in the
# background (game closed; not -I: it needs the user's cryptography and Pillow), and the server
# reads the edits after its restart (play.bat). Club names: the client's own eng.db, table team.
CARD_EDITS = os.path.join(HERE, "gamedata", "card_edits.json")
CARD_STATS = ("acceleration sprintspeed agility balance reactions ballcontrol dribbling positioning finishing shotpower longshots "
              "volleys penalties crossing curve freekickaccuracy shortpassing longpassing vision headingaccuracy marking "
              "standingtackle slidingtackle interceptions aggression jumping stamina strength gkdiving gkhandling gkkicking "
              "gkreflexes gkpositioning").split()
CARD_FIELDS = {**dict.fromkeys(CARD_STATS, (1, 150)), "preferredposition1": (0, 27), "preferredposition2": (-1, 27),
               "preferredposition3": (-1, 27), "preferredposition4": (-1, 27), "preferredfoot": (1, 2),
               "weakfootabilitytypecode": (1, 5), "skillmoves": (0, 4), "attackingworkrate": (0, 2), "defensiveworkrate": (0, 2),
               "trait1": (0, 2 ** 31 - 1), "teamid": (1, 999999)}
INSTALL_CMD = [sys.executable, os.path.join(HERE, "tools", "fo4_season.py"), "install"]  # tests swap it


@functools.lru_cache(maxsize=1)
def fo4_season():
    """tools/fo4_season.py as a module (its trait order)."""
    spec = importlib.util.spec_from_file_location("fo4_season", os.path.join(HERE, "tools", "fo4_season.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@functools.lru_cache(maxsize=1)
def clubs():
    """{teamid: (name, leagueid)} of the client's clubs (its eng.db in data_0.big, table team)."""
    from extract_fifadb import decrypt
    raw = hc.client_big(os.path.join(hc.GAME, "data", "data_0.big"), "data/db2/eng.db.db2")
    if not raw:
        return {}
    db = decrypt(bytes(raw))
    db[:16] = b"SQLite format 3\x00"
    with closing(sqlite3.connect(":memory:")) as c:
        c.deserialize(bytes(db))
        return {t: (n, lg) for t, n, lg in c.execute("SELECT teamid, teamname, leagueid FROM team ORDER BY teamname")}


def load_edits():
    try:
        with open(CARD_EDITS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_edits(edits):
    os.makedirs(os.path.dirname(CARD_EDITS), exist_ok=True)
    with open(CARD_EDITS + ".tmp", "w") as f:
        json.dump(edits, f, indent=1)
    os.replace(CARD_EDITS + ".tmp", CARD_EDITS)


def card_row(c, spid):
    r = c.execute(f"SELECT p.*, {hc.POSOVR} AS ovr FROM players p WHERE p.spid = ?", (spid,)).fetchone()
    if r is None:
        raise AdminError("e_no_spid")
    return r


def write_card(c, r, values):
    """values into card row r, its overallrating / potential moved by the client rating's change."""
    c.execute(f"UPDATE players SET {', '.join(f'{k} = ?' for k in values)} WHERE spid = ?", [*values.values(), r["spid"]])
    moved = card_row(c, r["spid"])["ovr"] - r["ovr"]
    c.execute("UPDATE players SET overallrating = overallrating + ?1, potential = potential + ?1 WHERE spid = ?2", (moved, r["spid"]))
    c.commit()
    hc._OVR.pop(r["spid"], None)  # the server's cached rating and stats of the card
    hc._STATS.pop(r["spid"], None)


@functools.lru_cache(maxsize=1)
def engine_raw():
    """(engine DB bytes, its meta XML) as install starts from them: fo4_season.py's copy of
    data_0.big, else the client's; (None, None) without it."""
    f = fo4_season()
    src = os.path.join(f.BACKUP, "data_0.big")
    src = src if os.path.isfile(src) else os.path.join(hc.GAME, "data", "data_0.big")
    db, meta = hc.client_big(src, f.NG_ENTRY), hc.client_big(src, f.NG_META)
    return (db, meta.decode("utf-8", "replace")) if db and meta else (None, None)


@functools.lru_cache(maxsize=1)
def engine_rows():
    """(Table, {field: (shortname, rangelow)}, {playerid: row}, {player id: [his row ids]}) of the
    engine DB's players as install starts from it (fo4_season.py's copy of data_0.big, else the
    client's); None without it."""
    db, meta = engine_raw()
    if not db:
        return None
    t, fld, by = fo4_season().ng_players(db, meta)[:3]
    mine = {}
    for k in sorted(by):
        mine.setdefault(k % 1000000, []).append(k)
    return t, fld, by, mine


def look_fields():
    """{FACE / LOOK field: (lowest, highest)} the engine DB can hold."""
    (t, fld, *_), f = engine_rows(), fo4_season()
    return {k: (fld[k][1], fld[k][1] + (1 << t.fields[fld[k][0]][2]) - 1) for k in dict.fromkeys(f.FACE + f.LOOK)}


def card_look(spid, pid):
    """The look card spid has before its edits: the engine row it wears (its own; for a card of
    one of the page's seasons, its source card's; else its player's) and the heads it may take."""
    e, f = engine_rows(), fo4_season()
    if e is None:
        return None
    t, fld, by, mine = e
    src = (load_seasons().get(str(spid // 1000000), {}).get("cards", {}).get(str(spid)) or {}).get("from", pid)
    key = next((k for k in (spid, src, pid) if k in by), None)
    if key is None and pid >= f.NEW_PID:  # a new player: install copies the row of his head (fo4_heads.json)
        try:
            with open(f.HEADS) as fh:
                key = json.load(fh).get(str(pid))
        except (OSError, ValueError):
            pass
        if key not in by:
            key = next(k for k, r in by.items() if t.get(r, fld["headclasscode"][0]) == 1)
    if key is None:
        return None
    row = by[key]
    val = lambda k: t.get(row, fld[k][0]) + (fld[k][1] if t.fields[fld[k][0]][0] != 4 else 0)
    faces = mine.get(pid) or [key]
    return {"row": key, "shared": spid > f.NG_MAX, "fields": {k: val(k) for k in look_fields()},
            "faces": [[k, t.get(by[k], fld["headassetid"][0])] for k in faces], "ranges": look_fields()}


@api("heads")
def heads(p):
    """[[engine row, name, season, scanned head]] of the players a name search finds: the 3D
    heads the card dialog's Face field offers (issue #36: "Browse existing 3D faces")."""
    e = engine_rows()
    if e is None:
        return {"rows": []}
    t, fld, by, mine = e
    out = {}
    for r in search(p)["rows"]:
        for k in mine.get(r["spid"] % 1000000, ()):
            out.setdefault(k, [k, r["name"], k // 1000000, t.get(by[k], fld["headclasscode"][0]) == 0])
    return {"rows": list(out.values())}


@api("card_meta")
def card_meta(p):
    return {"traits": fo4_season().FO3_TRAITS, "fields": CARD_FIELDS, "positions": POS_NAMES, "look": list(fo4_season().LOOK),
            "clubs": [[t, n, lg] for t, (n, lg) in clubs().items()], "file": rel(CARD_EDITS)}


@api("card")
def card(p):
    spid = as_int(p.get("spid"), 1, 10 ** 10, "spid")
    with closing(hc._db()) as c:
        r = card_row(c, spid)
    club, e = clubs().get(r["teamid"]), load_edits().get(str(spid), {})
    return {"spid": spid, "playerid": r["playerid"], "season": spid // 1000000, "name": names([spid]).get(spid, "?"),
            "ovr": r["ovr"], "teamid": r["teamid"], "leagueid": r["leagueid"], "club": club[0] if club else None,
            "custom": str(spid // 1000000) in load_seasons(), "fields": {k: r[k] for k in CARD_FIELDS},
            "orig": e.get("orig", {}), "look": card_look(spid, r["playerid"]), "look_edit": e.get("look", {})}


@api("card_edits")
def card_edits(p):
    edits = load_edits()
    nm = names(edits)
    return {"rows": [{"spid": int(s), "name": nm.get(int(s), "?"), "season": int(s) // 1000000,
                      "changed": sorted(e.get("new", {})) + (["look"] if e.get("look") else [])}
                     for s, e in sorted(edits.items(), key=lambda kv: nm.get(int(kv[0]), ""))]}


def set_look(spid, pid, want):
    """The card's look edit from want ({"face": row id or None, LOOK field: value}): only what
    differs from the look it has without it; {} = none."""
    base = card_look(spid, pid)
    if base is None:
        raise AdminError("e_no_engine")
    look = {}
    face = want.get("face")
    if face not in (None, "", base["row"], str(base["row"])):
        face = as_int(face, 1, 10 ** 9, ("f_face",))
        if face not in engine_rows()[2]:
            raise AdminError("e_face", face)
        look["face"] = face
    for k in fo4_season().LOOK:
        if k in want and str(want[k]) != str(base["fields"][k]):
            look[k] = as_int(want[k], *base["ranges"][k], k)
    return look


@api("card_set")
def card_set(p):
    spid = as_int(p.get("spid"), 1, 10 ** 10, "spid")
    want = p.get("fields", {})
    if not isinstance(want, dict) or not isinstance(p.get("look", {}), dict):
        raise AdminError("e_card_field", "fields")
    with closing(hc._db()) as c:
        r = card_row(c, spid)
        new = {}
        for k, v in want.items():
            if k not in CARD_FIELDS:
                raise AdminError("e_card_field", k)
            if str(v) != str(r[k]):  # an unchanged value may be outside the range (a keeper's -3)
                new[k] = as_int(v, *CARD_FIELDS[k], k)
        if "teamid" in new:
            if new["teamid"] not in clubs():
                raise AdminError("e_club", new["teamid"])
            new["leagueid"] = clubs()[new["teamid"]][1]
        look = set_look(spid, r["playerid"], p["look"]) if "look" in p else None
        if new:
            write_card(c, r, new)
    edits = load_edits()
    e = edits.setdefault(str(spid), {"orig": {}, "new": {}})
    for k, v in new.items():
        e["orig"].setdefault(k, r[k])
        e["new"][k] = v
    for k in [k for k in e["new"] if e["new"][k] == e["orig"][k]]:  # set back by hand
        del e["new"][k], e["orig"][k]
    if look is not None:
        e["look"] = look
    if not e["new"] and not e.get("look"):
        del edits[str(spid)]
    save_edits(edits)
    return card(p)


@api("card_revert")
def card_revert(p):
    spid = as_int(p.get("spid"), 1, 10 ** 10, "spid")
    edits = load_edits()
    e = edits.pop(str(spid), None)
    if e is None:
        raise AdminError("e_not_edited")
    orig = {k: v for k, v in e.get("orig", {}).items() if k in CARD_FIELDS or k == "leagueid"}
    if orig:
        with closing(hc._db()) as c:
            write_card(c, card_row(c, spid), orig)
    save_edits(edits)
    return card(p)


# --- Seasons of the page's own (issue #38) ---------------------------------------------------------
# A new season: a code, a name and a badge (gamedata/season_icons/<code>.png, which install puts
# into the client's Label.big); its cards are copies of other cards (stats + boost, perms, the
# source card's 3D row), edited like any other. gamedata/seasons.json, format in fo4_season.py; the
# server lists the seasons (Top 200 pack, season color) from its next start.
CUSTOM_SEASONS = os.path.join(HERE, "gamedata", "seasons.json")
SEASON_ICONS = os.path.join(HERE, "gamedata", "season_icons")
# codes no FO3 card and no client badge uses, at most 99 (fo4_season.NG_MAX)
SEASON_CODES = (50, 52, 58, 60, 62, 64, 66, 68, 70, 72, 74, 75, 76, 80, 94, 1, 2, 3, 4, 5, 18, 19, 25, 34, 36, 46)


def load_seasons():
    try:
        with open(CUSTOM_SEASONS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_seasons(seasons):
    os.makedirs(os.path.dirname(CUSTOM_SEASONS), exist_ok=True)
    with open(CUSTOM_SEASONS + ".tmp", "w", encoding="utf-8") as f:
        json.dump(seasons, f, indent=1, ensure_ascii=False)
    os.replace(CUSTOM_SEASONS + ".tmp", CUSTOM_SEASONS)


def season_icon(code):
    return os.path.join(SEASON_ICONS, f"{code}.png")


def own_season(code):
    code = as_int(code, 1, 99, ("f_season_code",))
    s = load_seasons()
    if str(code) not in s:
        raise AdminError("e_season", code)
    return code, s


@api("world")
def world(p):
    """The game world (issue #38, gen_teams.world): modern or classic FO3; it counts from the
    server's next start."""
    return {"world": hc.gen_teams.world(), "running": hc.gen_teams.WORLD}


@api("world_set")
def world_set(p):
    if p.get("world") not in ("modern", "classic"):
        raise AdminError("e_world")
    os.makedirs(os.path.dirname(hc.gen_teams.WORLD_FILE), exist_ok=True)
    with open(hc.gen_teams.WORLD_FILE, "w", encoding="utf-8") as f:
        json.dump({"world": p["world"]}, f)
    return world(p)


@api("seasons")
def seasons(p):
    s = load_seasons()
    with closing(hc._db()) as c:
        count = dict(c.execute("SELECT spid / 1000000, COUNT(*) FROM players GROUP BY 1"))
    return {"seasons": [{"code": int(k), "name": v["name"], "cards": count.get(int(k), 0), "icon": os.path.isfile(season_icon(k))}
                        for k, v in sorted(s.items(), key=lambda kv: int(kv[0]))],
            "free": [c for c in SEASON_CODES if str(c) not in s], "file": rel(CUSTOM_SEASONS)}


@api("season_set")
def season_set(p):
    code, s = as_int(p.get("code"), 1, 99, ("f_season_code",)), load_seasons()
    if code not in SEASON_CODES and str(code) not in s:
        raise AdminError("e_season_code", code)
    name = text(p.get("name"), ("f_name",), 20)
    raw = None
    if p.get("icon"):
        try:
            raw = base64.b64decode(str(p["icon"]), validate=True)
        except (binascii.Error, ValueError):
            raise AdminError("e_season_icon")
        if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) > 1 << 20:
            raise AdminError("e_season_icon")
    elif not os.path.isfile(season_icon(code)):
        raise AdminError("e_season_icon")
    s.setdefault(str(code), {"cards": {}})["name"] = name
    if raw:
        os.makedirs(SEASON_ICONS, exist_ok=True)
        with open(season_icon(code) + ".tmp", "wb") as fh:
            fh.write(raw)
        os.replace(season_icon(code) + ".tmp", season_icon(code))
    save_seasons(s)
    return seasons(p)


def drop_cards(c, spids):
    """Cards out of fifadb (and their edits)."""
    for t in ("players", *fo4_season().PERM_TABLES):
        c.executemany(f"DELETE FROM {t} WHERE spid = ?", [(x,) for x in spids])
    c.commit()
    edits = load_edits()
    if [edits.pop(str(x)) for x in spids if str(x) in edits]:
        save_edits(edits)
    for x in spids:
        hc._OVR.pop(x, None)
        hc._STATS.pop(x, None)


@api("season_delete")
def season_delete(p):
    code, s = own_season(p.get("code"))
    with closing(hc._db()) as c:
        drop_cards(c, [x for (x,) in c.execute("SELECT spid FROM players WHERE spid / 1000000 = ?", (code,))])
    del s[str(code)]
    save_seasons(s)
    if os.path.isfile(season_icon(code)):
        os.remove(season_icon(code))
    return seasons(p)


@api("season_card_add")
def season_card_add(p):
    code, s = own_season(p.get("code"))
    src, boost = as_int(p.get("spid"), 1, 10 ** 10, "spid"), as_int(p.get("boost") or 0, -50, 50, ("f_boost_stats",))
    with closing(hc._db()) as c:
        r = card_row(c, src)
        spid = code * 1000000 + r["playerid"]
        if c.execute("SELECT 1 FROM players WHERE spid = ?", (spid,)).fetchone():
            raise AdminError("e_card_exists", spid)
        cols = [k for k in r.keys() if k != "ovr"]
        c.execute(f"INSERT INTO players ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                  [spid if k == "spid" else r[k] for k in cols])
        for t in fo4_season().PERM_TABLES:
            perm = c.execute(f"SELECT licensed, transferable FROM {t} WHERE spid = ?", (src,)).fetchone() or (1, 1)
            c.execute(f"INSERT INTO {t} (spid, licensed, transferable) VALUES (?, ?, ?)", (spid, *perm))
        c.commit()
        if boost:
            write_card(c, card_row(c, spid), {k: max(1, min(150, r[k] + boost)) for k in CARD_STATS})
    s[str(code)]["cards"][str(spid)] = {"from": src, "boost": boost}
    save_seasons(s)
    return card({"spid": spid})


@api("season_card_remove")
def season_card_remove(p):
    spid = as_int(p.get("spid"), 1, 10 ** 10, "spid")
    code, s = own_season(spid // 1000000)
    with closing(hc._db()) as c:
        drop_cards(c, [spid])
    s[str(code)]["cards"].pop(str(spid), None)
    save_seasons(s)
    return seasons(p)


# --- Own team colors (user: "bisa tambahkan team color baru"; format in http_config's TC_CUSTOM) -------
TC_TARGETS, TC_STATS = ("", "fw", "mf", "df", "gk"), ("ovr", *CARD_STATS)


@functools.lru_cache(maxsize=1)
def nations():
    """{nation id: name} of the engine DB's nations table."""
    db, meta = engine_raw()
    if not db:
        return {}
    import eadb  # tools/re, on sys.path once fo4_season() is loaded
    lo = int(re.search(r'<table name="nations".*?<field name="nationid"[^>]*rangelow="(-?\d+)"', meta, re.S).group(1))
    return {r["nationid"] + lo: r["nationname"] for r in eadb.rows(db, meta, "nations")}


def load_colors():
    try:
        with open(hc.CUSTOM_COLORS, encoding="utf-8") as f:
            rows = json.load(f)
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def save_colors(rows):
    os.makedirs(os.path.dirname(hc.CUSTOM_COLORS), exist_ok=True)
    with open(hc.CUSTOM_COLORS + ".tmp", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1, ensure_ascii=False)
    os.replace(hc.CUSTOM_COLORS + ".tmp", hc.CUSTOM_COLORS)
    hc.tc_special_sets.cache_clear()  # the server's lists of colors and their cards
    hc.tc_colors.cache_clear()


@api("teamcolors")
def teamcolors(p):
    sets = hc.tc_special_sets()
    seasons = {codes[0]: label for label, codes in hc.tc_seasons()}
    seasons.update({int(k): v["name"] for k, v in load_seasons().items()})
    pids = sorted({x for c in load_colors() for x in c.get("players") or ()})
    with closing(hc._db()) as db:
        who = dict(db.execute(f"SELECT playerid, COALESCE(full_eng, full, short) FROM nameseng WHERE playerid IN ({','.join('?' * len(pids))})", pids))
    return {"player_names": who, "colors": [{**c, "cards": len(sets.get(c["id"], ((), 0))[0]), "nplayers": sets.get(c["id"], ((), 0))[1]}
                       for c in load_colors()],
            "seasons": sorted([k, v] for k, v in seasons.items()),
            "nations": sorted(([k, v] for k, v in nations().items()), key=lambda x: x[1]),
            "clubs": [[t, n] for t, (n, _) in clubs().items()], "stats": TC_STATS, "file": rel(hc.CUSTOM_COLORS)}


@api("teamcolor_set")
def teamcolor_set(p):
    ids = lambda k: [as_int(x, 0, 10 ** 7, ("e_tc_value",)) for x in (p.get(k) or ())]
    c = {"name": text(p.get("name"), ("f_name",), 30), "desc": str(p.get("desc") or "").strip()[:80],
         "seasons": ids("seasons"), "clubs": ids("clubs"), "nations": ids("nations"), "players": ids("players"),
         "min_ovr": as_int(p.get("min_ovr") or 0, 0, 200, ("tc_min_ovr",)), "best": as_int(p.get("best") or 0, 0, 1000, ("tc_best",)),
         "target": str(p.get("target") or ""), "stat": str(p.get("stat") or "ovr"), "amount": as_int(p.get("amount"), 1, 20, ("amount",))}
    if c["target"] not in TC_TARGETS or c["stat"] not in TC_STATS:
        raise AdminError("e_tc_value")
    with closing(hc._db()) as db:
        have = {r[0] for r in db.execute("SELECT DISTINCT spid / 1000000 FROM players")}
        if c["players"]:
            marks = ",".join("?" * len(c["players"]))
            known = {r[0] for r in db.execute(f"SELECT DISTINCT playerid FROM players WHERE playerid IN ({marks})", c["players"])}
            if set(c["players"]) - known:
                raise AdminError("e_player_id", min(set(c["players"]) - known))
    for k, ok, err in (("seasons", have, "e_season"), ("clubs", clubs(), "e_club"), ("nations", nations(), "e_nation")):
        bad = [x for x in c[k] if x not in ok]
        if bad:
            raise AdminError(err, bad[0])
    rows = load_colors()
    if p.get("id"):
        cid = as_int(p["id"], hc.TC_CUSTOM, hc.TC_CUSTOM + 999, "id")
        old = next((r for r in rows if r["id"] == cid), None)
        if old is None:
            raise AdminError("e_color_missing")
        c["id"] = cid
        rows[rows.index(old)] = c
    else:
        c["id"] = max([hc.TC_CUSTOM - 1, *(r["id"] for r in rows)]) + 1
        if c["id"] > hc.TC_CUSTOM + 999:
            raise AdminError("e_tc_value")
        rows.append(c)
    save_colors(rows)
    return {**teamcolors(p), "id": c["id"]}


@api("teamcolor_delete")
def teamcolor_delete(p):
    rows = load_colors()
    cid = as_int(p.get("id"), hc.TC_CUSTOM, hc.TC_CUSTOM + 999, "id")
    if not any(r["id"] == cid for r in rows):
        raise AdminError("e_color_missing")
    save_colors([r for r in rows if r["id"] != cid])
    return teamcolors(p)


# --- Mods: everything the tab made, in one file (issue #38: "importing compatible mod data") -------
# {"fo3id_mod": 1, "seasons": {code: {"name", "icon": base64 PNG, "cards": {spid: {"from", "boost"}}}},
#  "cards": {spid: {"new": {column: value}, "look": {...}}}}; importing it on another server makes the
# same seasons, cards and edits (what it cannot make there, e.g. a card whose source is missing, is
# listed, the rest goes in).
@api("mod_export")
def mod_export(p):
    out = {"fo3id_mod": 1, "seasons": {}, "teamcolors": [{k: v for k, v in c.items() if k != "id"} for c in load_colors()],
           "cards": {s: {"new": e.get("new", {}), "look": e.get("look", {})} for s, e in load_edits().items()}}
    for code, v in load_seasons().items():
        with open(season_icon(code), "rb") as fh:
            out["seasons"][code] = {**v, "icon": base64.b64encode(fh.read()).decode()}
    return out


@api("mod_import")
def mod_import(p):
    mod = p.get("mod")
    if not isinstance(mod, dict) or mod.get("fo3id_mod") != 1:
        raise AdminError("e_mod")
    done, failed = 0, []
    for code, v in sorted((mod.get("seasons") or {}).items(), key=lambda kv: int(kv[0])):
        try:
            season_set({"code": code, "name": v["name"], "icon": v.get("icon")})
        except (AdminError, KeyError, TypeError, ValueError):
            failed.append(f"season {code}")
            continue
        for spid, c in (v.get("cards") or {}).items():
            try:
                with closing(hc._db()) as db:
                    have = db.execute("SELECT 1 FROM players WHERE spid = ?", (int(spid),)).fetchone()
                if not have:
                    season_card_add({"code": code, "spid": c["from"], "boost": c.get("boost", 0)})
                done += 1
            except (AdminError, KeyError, TypeError, ValueError):
                failed.append(str(spid))
    for spid, e in (mod.get("cards") or {}).items():
        try:
            card_set({"spid": spid, "fields": e.get("new") or {}, **({"look": e["look"]} if e.get("look") else {})})
            done += 1
        except (AdminError, KeyError, TypeError, ValueError, AttributeError):
            failed.append(str(spid))
    have = {c["name"]: c["id"] for c in load_colors()}
    for c in mod.get("teamcolors") or []:  # one of the same name is replaced
        try:
            teamcolor_set({**c, "id": have.get(c.get("name"))})
            done += 1
        except (AdminError, KeyError, TypeError, ValueError, AttributeError):
            failed.append("team color " + str(c.get("name") if isinstance(c, dict) else c))
    return {"done": done, "failed": failed}


@api("db_install")
def db_install(p):
    run_job("install", INSTALL_CMD + ["--game", hc.GAME])
    return assets(p)


# Club crests: the client's cache, externalAssets/crests/large|small/s<teamid>.png (256 / 128 px).
# The page fits the chosen picture into both squares (canvas) and sends PNGs; the first upload of a
# club keeps the cache's own pair in gamedata/crest_backup (an empty file: there was none) for
# Kembalikan. The game shows a new crest after it restarts.
CRESTS = {"large": 256, "small": 128}
CREST_BACKUP = os.path.join(HERE, "gamedata", "crest_backup")


def crest_file(size, teamid, root=None):
    return os.path.join(root or os.path.join(hc.ASSETS, "externalAssets", "crests"), size, f"s{teamid}.png")


@api("crest")
def crest(p):
    tid = as_int(p.get("teamid"), 1, 999999, "teamid")
    return {"teamid": tid, "has": {s: os.path.isfile(crest_file(s, tid)) for s in CRESTS},
            "custom": os.path.isfile(crest_file("large", tid, CREST_BACKUP)), "updated": int(time.time())}


def square_png(b64, px, err):
    """The page's base64 PNG of px x px (up to 2 MB) as bytes, else AdminError(err, px)."""
    try:
        raw = base64.b64decode(str(b64 or ""), validate=True)
    except (binascii.Error, ValueError):
        raise AdminError(err, px)
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR" or struct.unpack(">II", raw[16:24]) != (px, px) or len(raw) > 2 << 20:
        raise AdminError(err, px)
    return raw


@api("crest_set")
def crest_set(p):
    tid = as_int(p.get("teamid"), 1, 999999, "teamid")
    if tid not in clubs():
        raise AdminError("e_club", tid)
    pngs = {size: square_png(p.get(size), px, "e_crest_png") for size, px in CRESTS.items()}
    for size, raw in pngs.items():
        f, keep = crest_file(size, tid), crest_file(size, tid, CREST_BACKUP)
        os.makedirs(os.path.dirname(f), exist_ok=True)
        if not os.path.isfile(keep):
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            if os.path.isfile(f):
                shutil.copyfile(f, keep)
            else:
                open(keep, "wb").close()
        with open(f + ".tmp", "wb") as fh:
            fh.write(raw)
        os.replace(f + ".tmp", f)
    return crest(p)


@api("crest_revert")
def crest_revert(p):
    tid = as_int(p.get("teamid"), 1, 999999, "teamid")
    for size in CRESTS:
        f, keep = crest_file(size, tid), crest_file(size, tid, CREST_BACKUP)
        if not os.path.isfile(keep):
            continue
        if os.path.getsize(keep):
            shutil.copyfile(keep, f)
        elif os.path.isfile(f):
            os.remove(f)
        os.remove(keep)
    return crest(p)


# Player pictures (issue #36: "Upload JPG/PNG images ... assign portraits to the correct Player
# IDs ... Restore original portraits"): the page fits the picture into 128x128 and it is saved as
# http_config.FACES/p<id>.png, which the game gets before the cache's. id = a spid: that card
# only; a player id: every card of his without its own. Restoring deletes it.
FACE_PX = 128


def face_id(p):
    i = as_int(p.get("id"), 1, 10 ** 10, "id")
    with closing(hc._db()) as c:
        if not c.execute("SELECT 1 FROM players WHERE spid = ?1 OR playerid = ?1 LIMIT 1", (i,)).fetchone():
            raise AdminError("e_no_spid")
    return i


@api("face")
def face(p):
    i = face_id(p)
    return {"id": i, "has": hc.asset_path(f"externalAssets/players/p{i}.png") is not None,
            "custom": os.path.isfile(os.path.join(hc.FACES, f"p{i}.png")), "updated": time.time_ns()}


@api("face_set")
def face_set(p):
    i, raw = face_id(p), square_png(p.get("png"), FACE_PX, "e_face_png")
    f = os.path.join(hc.FACES, f"p{i}.png")
    os.makedirs(hc.FACES, exist_ok=True)
    with open(f + ".tmp", "wb") as fh:
        fh.write(raw)
    os.replace(f + ".tmp", f)
    return face(p)


FA_FACE = "https://fifaaddict.com/fo3img/players/p{}.png"  # fa_faces.FA: a card's (spid) or a player's picture
FO4_FACE = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/players/p{}.png"  # fa_faces.FO4_FACE: a player's


FA4_FACE = "https://s1.fifaaddict.com/fo4/players/{}.png"  # fa_faces.FA4: an FO4 card's, by fifaaddict's card id
FA4_NONE = "17675d7e0863363f9da1f99c81321594e7056f38c6a9024270219f393f4a2029"  # fa_faces.FA4_NONE: its silhouette


def fetch_face(i):
    """Card or player i's picture from fifaaddict.com, else (a player's) from FC Online (FO4): user
    "jika download dari fo3 tidak ada pindah ke fo4"; a new player's (700000 up) or his card's from his
    FO4 card on fifaaddict (fo4_season.new_player_cards). AdminError when none has it or answers."""
    new = fo4_season().new_player_cards() if i % 1000000 >= 700000 else {}
    own = {s % 1000000: u for s, u in sorted(new.items(), key=lambda kv: kv[0] // 1000000 == 300)}  # 26 LIVE wins
    uid = new.get(i) or own.get(i)
    why = None
    for url in [FA4_FACE.format(uid)] if uid else [FA_FACE.format(i)] + ([FO4_FACE.format(i)] if i < 1000000 else []):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (FO3 private server face)"})
        try:
            body = urllib.request.urlopen(req, timeout=20).read()
            if hashlib.sha256(body).hexdigest() != FA4_NONE:
                return body
        except Exception as e:
            missing = {403, 404} if url.startswith("https://fco.") else {404}  # FO4's CDN answers 403 for none
            if getattr(e, "code", None) not in missing:
                why = str(getattr(e, "code", "") or e)[:80]
    if why:
        raise AdminError("e_face_fetch", why)
    raise AdminError("e_face_none", i)


@api("face_from")
def face_from(p):
    """Card id's picture = id `from`'s (issue #36: "edit images and player IDs separately for each
    season"): the one the game gets for it here, else fifaaddict.com's (its season pictures too)."""
    i, src = face_id(p), as_int(p.get("from"), 1, 10 ** 10, ("face_from",))
    f = hc.asset_path(f"externalAssets/players/p{src}.png")
    if f:
        with open(f, "rb") as fh:
            raw = fh.read()
    else:
        raw = fetch_face(src)
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) > 2 << 20:
        raise AdminError("e_face_none", src)
    os.makedirs(hc.FACES, exist_ok=True)
    dst = os.path.join(hc.FACES, f"p{i}.png")
    if os.path.normcase(os.path.abspath(f or "")) != os.path.normcase(dst):
        with open(dst + ".tmp", "wb") as fh:
            fh.write(raw)
        os.replace(dst + ".tmp", dst)
    return face(p)


@api("face_revert")
def face_revert(p):
    f = os.path.join(hc.FACES, f"p{face_id(p)}.png")
    if os.path.isfile(f):
        os.remove(f)
    return face(p)


# Photo heads (tools/photo_head.py; user: "di admin panel buat 3d model berdasarkan foto close up
# pemain ... untuk pemain2 yang masih generic face"): the page sends the photo (fitted into
# PHOTO_MAX) with the points clicked on it and a scanned head to paint it on (the donor: its mesh
# and hair); the face texture made from them is saved for the player id, and install puts it in
# the client for every card of his that shows his id row or a generic head.
PHOTO_MAX = 1024


@functools.lru_cache(maxsize=1)
def photo_head_mod():
    spec = importlib.util.spec_from_file_location("photo_head", os.path.join(HERE, "tools", "photo_head.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def photo_pid(p):
    pid = as_int(p.get("pid"), 1, 999999, "pid")
    with closing(hc._db()) as c:
        if not c.execute("SELECT 1 FROM players WHERE playerid = ? LIMIT 1", (pid,)).fetchone():
            raise AdminError("e_no_spid")
    return pid


@api("photo_head")
def photo_head(p):
    pid, ph = photo_pid(p), photo_head_mod()
    hid, h = next(((k, v) for k, v in ph.load().items() if v["pid"] == pid), (None, {}))
    return {"pid": pid, "id": hid, "row": h.get("row"), "made": h.get("made"), "updated": time.time_ns(),
            "points": list(ph.POINTS)}


@api("photo_head_make")
def photo_head_make(p):
    pid, ph = photo_pid(p), photo_head_mod()
    from PIL import Image
    import numpy as np
    try:
        raw = base64.b64decode(str(p.get("png") or ""), validate=True)
        img = Image.open(io.BytesIO(raw))
        if img.format != "PNG" or max(img.size) > PHOTO_MAX or len(raw) > 8 << 20:
            raise ValueError
        photo = np.asarray(img.convert("RGB"))
    except Exception:
        raise AdminError("e_ph_png", PHOTO_MAX)
    pts = p.get("points")
    if (not isinstance(pts, list) or len(pts) != len(ph.POINTS)
            or not all(isinstance(q, list) and len(q) == 2 and all(isinstance(v, (int, float)) for v in q)
                       and 0 <= q[0] <= photo.shape[1] and 0 <= q[1] <= photo.shape[0] for q in pts)):
        raise AdminError("e_ph_points", len(ph.POINTS))
    save_photo_head(pid, photo, pts, as_int(p.get("row"), 1, 10 ** 9, ("phd_donor",)))
    return photo_head(p)


def save_photo_head(pid, photo, pts, row):
    """Make player pid's face texture from photo (RGB array) and its points on the scanned head of
    engine row `row`, and keep it (his old one replaced)."""
    from PIL import Image
    ph, e = photo_head_mod(), engine_rows()
    if e is None:
        raise AdminError("e_no_engine")
    t, fld, by, _ = e
    if row not in by or t.get(by[row], fld["headclasscode"][0]) != 0:
        raise AdminError("e_ph_donor", row)
    donor = t.get(by[row], fld["headassetid"][0]) + fld["headassetid"][1]
    try:
        face = ph.donor_files(hc.GAME, donor)[ph.FACE_FILE]
    except (KeyError, OSError):
        raise AdminError("e_ph_files", donor)
    out = ph.make(photo, pts, face)
    heads = ph.load()
    hid = next((k for k, v in heads.items() if v["pid"] == pid), None) or ph.new_id(heads)
    os.makedirs(ph.DIR, exist_ok=True)
    f = os.path.join(ph.DIR, f"{hid}.png")
    Image.fromarray(out).save(f + ".tmp", "PNG")
    os.replace(f + ".tmp", f)
    heads[hid] = {"pid": pid, "row": row, "donor": donor, "made": time.strftime("%Y-%m-%d")}
    ph.save(heads)
    return hid


@functools.lru_cache(maxsize=1)
def photo_donors():
    """{headassetid: [engine row, *look, its nation]} of the scanned heads a photo head may take (the first
    time it reads ~2,000 face textures and pictures, about a minute; then gamedata/photo_heads/donors.json)."""
    t, fld, by, _ = engine_rows()
    rows = {}
    for k in sorted(by, key=lambda k: (k >= 1000000, k)):  # a player id row first
        if t.get(by[k], fld["headclasscode"][0]) == 0:
            rows.setdefault(t.get(by[k], fld["headassetid"][0]) + fld["headassetid"][1], k)
    looks = photo_head_mod().donor_looks(hc.GAME, rows, lambda pid: hc.asset_path(f"externalAssets/players/p{pid}.png"))
    nation = lambda row: t.get(by[row], fld["nationality"][0]) + fld["nationality"][1] if row in by else None
    return {k: v[:7] + [nation(v[0])] for k, v in looks.items()}


def player_nation(pid):
    """A player's nationality (his newest card's), or None."""
    with closing(hc._db()) as c:
        r = c.execute("SELECT nationality FROM players WHERE playerid = ? ORDER BY spid DESC LIMIT 1", (pid,)).fetchone()
    return r[0] if r else None


def icon_face(pid, fetch=True):
    """(player pid's picture as an RGB array, its POINTS or None, the donor row closest to it):
    the picture the game shows (the cache's), else fifaaddict.com's when fetch."""
    ph = photo_head_mod()
    f = hc.asset_path(f"externalAssets/players/p{pid}.png")
    try:
        if f:
            photo = ph.picture(f)
        elif fetch:
            photo = ph.picture(io.BytesIO(fetch_face(pid)))
        else:
            return None, None, None
    except Exception:
        raise AdminError("e_ph_icon", pid)
    pts = ph.landmarks(photo)
    donors = photo_donors()
    head = ph.suggest(ph.look(photo, pts), donors, player_nation(pid)) if pts else None
    return photo, pts, donors[head][0] if head else None


@api("photo_head_icon")
def photo_head_icon(p):
    """Player pid's picture for the page's photo, with its points and a donor found for it
    (user: "bisakah dibuat dari icon yang ada di fifaaddict fo4 atau fo3?")."""
    from PIL import Image
    pid = photo_pid(p)
    photo, pts, row = icon_face(pid)
    b = io.BytesIO()
    Image.fromarray(photo).save(b, "PNG")
    return {"png": base64.b64encode(b.getvalue()).decode(), "points": pts, "row": row,
            "donor": row_name(row) if row else None}


@api("photo_head_club")
def photo_head_club(p):
    """A photo head from his picture for every player of the clubs here who wears a generic head
    and has none yet; with redo, every photo head there is is made again from his picture too
    (user: "face dari gambar banyak belum sesuai": a better donor and teeth after an update; one
    made from an uploaded photo becomes his picture's). Returns who got one and who was left out
    (no picture in the cache, no face)."""
    ph = photo_head_mod()
    have, redo = {v["pid"] for v in ph.load().values()}, bool(p.get("redo"))
    cards = {}
    for club in hc._state().get("teams", {}).values():
        for c in club.get("players", []):
            cards.setdefault(c["spid"] % 1000000, c["spid"])
    for pid in have if redo else ():
        cards.setdefault(pid, pid)
    made, skipped = [], []
    nm = names(list(cards.values()))
    for pid, spid in sorted(cards.items()):
        if pid in have and not redo:
            continue
        if pid not in have:
            look = card_look(spid, pid)
            if not look or look["fields"]["headclasscode"] != 1:
                continue
        try:
            photo, pts, row = icon_face(pid, fetch=False)
        except AdminError:  # a picture that is not one
            photo = pts = None
        if pts is None:
            skipped.append([pid, nm.get(spid, "?"), "phd_skip_icon" if photo is None else "phd_skip_face"])
            continue
        save_photo_head(pid, photo, pts, row)
        made.append([pid, nm.get(spid, "?"), row_name(row)])
    return {"made": made, "skipped": skipped}


@api("photo_head_delete")
def photo_head_delete(p):
    pid, ph = photo_pid(p), photo_head_mod()
    heads = ph.load()
    for hid in [k for k, v in heads.items() if v["pid"] == pid]:
        del heads[hid]
        f = os.path.join(ph.DIR, f"{hid}.png")
        if os.path.isfile(f):
            os.remove(f)
    ph.save(heads)
    return photo_head(p)


# --- Shop (gamedata/shop.json, format in http_config's "Shop catalog" block) ----------------------

def shop_icons():
    """The shop images (externalAssets/shop/<name>.png: the client's own plus the server's draft
    pack icons), the icons a product can use."""
    names = set()
    for root in (hc.OWN_ASSETS, hc.ASSETS):
        try:
            names.update(f[:-4] for f in os.listdir(os.path.join(root, "externalAssets", "shop")) if f.endswith(".png"))
        except OSError:
            pass
    return sorted(names)


def product_tab(kind, itemid):
    return {"packs": "PLAYERPACK", "items": "USE", "monthly": "MONTHLYPACK"}.get(kind) or (
        "CHAINED" if itemid in hc.CHAIN_STEPS else "PACKAGE")


@api("shop")
def shop(p):
    cfg = hc.shop_config()
    prices = cfg.get("prices") or {}
    out = []
    for kind, (table, _, to) in hc.SHOP_KINDS.items():
        base, custom = hc._SHOP_BASE[kind], cfg.get(kind) or {}
        for i, entry in table.items():
            d = hc.item_def(i)
            out.append({"kind": kind, "itemid": i, "tab": product_tab(kind, i), "name": d["name"],
                        "icon": str(d.get("bundleicon") or d.get("itemicon") or i),
                        "price": d["bunches"][0]["price"], "fixed": prices.get(str(i)), "hidden": i in hc.SHOP_HIDDEN,
                        "builtin": i in base, "custom": str(i) in custom, "def": to(entry)})
    lists = {k: list(getattr(hc, n)) for k, n in (("new", "SHOP_NEW"), ("best", "SHOP_BEST"), ("mp", "MP_SHOP"),
                                                  ("special", "SPECIAL"))}
    lists["chained"] = [list(s) for s in hc.CHAINED.values()]
    return {"free": hc.SHOP_FREE, "products": out, "lists": lists, "icons": shop_icons(),
            "item_types": list(hc.SHOP_ITEM_TYPES), "positions": list(hc.POSITIONS),
            "seasons": [{"code": c, "label": label} for label, codes in hc.tc_seasons() for c in codes],
            "ep_item": hc.EP_ITEM, "es_item": hc.ES_ITEM,
            "file": rel(hc.SHOP_FILE)}


def save_shop(cfg):
    """Write gamedata/shop.json and rebuild the Shop; a pack without a single card is refused."""
    old = hc.shop_config()
    hc.apply_shop(cfg)
    empty = [i for i, v in (cfg.get("packs") or {}).items() if len(hc.pack_pool(int(i))) < (v.get("draft") or 1)]
    if empty:
        hc.apply_shop(old)
        raise AdminError("e_pack_empty", empty[0])
    os.makedirs(os.path.dirname(hc.SHOP_FILE), exist_ok=True)
    with open(hc.SHOP_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1, ensure_ascii=False)
    hc.shop_changed()


def known(i, *kinds):
    """itemid i is a product of one of kinds (packs / items / packages / monthly), or EP / ES."""
    i = int(i)
    return any(i in hc.SHOP_KINDS[k][0] for k in kinds if k in hc.SHOP_KINDS) or (
        "ep" in kinds and i in (hc.EP_ITEM, hc.ES_ITEM))


def text(v, what, n=40):
    v = str(v or "").strip()
    if not 1 <= len(v) <= n:
        raise AdminError("e_length", what, n)
    return v


def icon_of(v, required=True):
    v = str(v or "").strip()
    if v and v not in shop_icons():
        raise AdminError("e_no_icon", v)
    if required and not v:
        raise AdminError("e_pick_icon")
    return v


def rows_of(rows, what, kinds, lo=1, hi=10):
    """[[itemid, count]] with every itemid known."""
    out = []
    for r in rows or []:
        i = as_int(r[0], 1, 10 ** 10, "itemid")
        if not known(i, *kinds):
            raise AdminError("e_row_item", what, i)
        out.append([i, as_int(r[1], 1, 10 ** 12, ("count",))])
    if not lo <= len(out) <= hi:
        raise AdminError("e_rows", what, lo, hi)
    return out


def prize_row(w, prize, not_id=None):
    """[weight, {"ep": n} | itemid of a pack or item] from a prize row of the page."""
    w = as_int(w, 1, 1000, ("f_weight",))
    if isinstance(prize, dict):
        return [w, {"ep": as_int(prize.get("ep"), 1, 10 ** 12, "EP")}]
    if int(prize) != not_id and known(prize, "packs", "items"):
        return [w, int(prize)]
    raise AdminError("e_unknown_prize", prize)


def check_def(kind, d, itemid):
    """A clean shop.json entry from the page's form, or AdminError."""
    name = text(d.get("name"), ("f_name",))
    desc = str(d.get("description") or "").strip()[:200]
    if kind == "packs":
        seasons = sorted({as_int(s, 0, 99, ("th_season",)) for s in d.get("seasons") or []})
        pos = str(d.get("pos") or "")
        if pos and pos not in hc.POSITIONS:
            raise AdminError("e_unknown_pos")
        opt = lambda k, lo, hi, w: as_int(d[k], lo, hi, w) if d.get(k) not in (None, "", 0, "0") else None
        draft = opt("draft", 1, 10, ("f_draft",))
        if draft and len(seasons) != 1:  # the draft screen searches one season
            raise AdminError("e_draft_season")
        return {"name": name, "icon": icon_of(d.get("icon"), False), "seasons": seasons, "all": bool(d.get("all")),
                "top": opt("top", 1, 100000, ("f_top",)), "ovr": opt("ovr", 40, 130, ("f_ovr",)),
                "grade": opt("grade", 1, 10, ("f_grade",)), "pos": pos or None,
                "league": opt("league", 1, 10 ** 6, ("f_league",)), "draft": draft}
    if kind == "items":
        t = str(d.get("type") or "")
        if t not in hc.SHOP_ITEM_TYPES:
            raise AdminError("e_unknown_type")
        out = {"name": name, "icon": icon_of(d.get("icon")), "type": t, "description": desc}
        if t == "EP_LOTTERY":
            lo, hi = (as_int(x, 0, 10 ** 12, "EP") for x in (d.get("ep") or [0, 0]))
            if not 0 < lo <= hi:
                raise AdminError("e_ep_range")
            out["ep"] = [lo, hi]
        elif t == "ITEM_LOTTERY":
            prizes = [prize_row(w, prize, itemid) for w, prize in d.get("prizes") or []]
            if not 1 <= len(prizes) <= 10:  # the box screen shows up to 10 (cardCandidate0..9)
                raise AdminError("e_prizes")
            out["prizes"] = prizes
        elif t == "ENHANCEMENT_BOOST":
            out["boost"] = as_int(round(float(d.get("boost") or 0) * 100), 1, 100, ("f_boost",)) / 100
        elif t == "PLAYER_EXP_CAPSULE":
            out["exp"] = as_int(d.get("exp"), 1, 10 ** 7, ("f_exp",))
            if d.get("lv") not in (None, "", 0, "0"):
                out["lv"] = as_int(d["lv"], 1, hc.PLAYER_MAX_LEVEL - 1, ("f_maxlv",))
            if d.get("season") not in (None, "", 0, "0"):
                out["season"] = as_int(d["season"], 1, 99, ("th_season",))
        return out
    if kind == "packages":
        return {"name": name, "icon": icon_of(d.get("icon")), "description": desc,
                "contents": rows_of(d.get("contents"), ("f_contents",), ("packs", "items", "ep"))}
    if kind == "monthly":
        return {"name": name, "icon": icon_of(d.get("icon")), "description": desc,
                "days": as_int(d.get("days"), 1, 365, ("f_days",)),
                "now": rows_of(d.get("now"), ("f_now",), ("packs", "items", "ep"), 0),
                "daily": rows_of(d.get("daily"), ("f_daily",), ("packs", "items", "ep"))}
    raise AdminError("e_unknown_kind")


def kind_of(p):
    k = str(p.get("kind") or "")
    if k not in hc.SHOP_KINDS:
        raise AdminError("e_unknown_kind")
    return k


@api("shop_save")
def shop_save(p):
    """Create (no itemid) or change a product; a built-in one is replaced by the saved copy."""
    kind = kind_of(p); cfg = hc.shop_config()
    table = hc.SHOP_KINDS[kind][0]
    if p.get("itemid"):
        itemid = as_int(p["itemid"], 1, 10 ** 10, "itemid")
        if itemid not in table:
            raise AdminError("e_product_missing")
    else:
        taken = set().union(*(t for t, _, _ in hc.SHOP_KINDS.values()))
        itemid = next(i for i in range(hc.SHOP_ID_BASE[kind] + 1, 10 ** 10) if i not in taken)
    cfg.setdefault(kind, {})[str(itemid)] = check_def(kind, p.get("def") or {}, itemid)
    save_shop(cfg)
    return {"itemid": itemid}


def in_use(itemid):
    """Where another product still uses itemid (prizes, package contents, monthly items)."""
    for i, (_, _, _, eff, _) in hc.ITEMS.items():
        if any(prize == itemid for _, prize in eff.get("prizes", [])):
            return i
    for i, (_, _, contents, _) in hc.PACKAGES.items():
        if any(c == itemid for c, _ in contents):
            return i
    for i, (_, _, _, now, daily, _) in hc.MONTHLY.items():
        if any(c == itemid for c, _ in now + daily):
            return i


@api("shop_delete")
def shop_delete(p):
    """Drop a product's saved entry: a built-in one goes back to the original, a new one is gone."""
    kind = kind_of(p); cfg = hc.shop_config()
    itemid = as_int(p.get("itemid"), 1, 10 ** 10, "itemid")
    if str(itemid) not in (cfg.get(kind) or {}):
        raise AdminError("e_not_changed")
    if itemid not in hc._SHOP_BASE[kind] and (user := in_use(itemid)):
        raise AdminError("e_in_use", user)
    del cfg[kind][str(itemid)]
    if itemid not in hc._SHOP_BASE[kind]:  # gone: out of the lists, prices and hidden too
        cfg.get("prices", {}).pop(str(itemid), None)
        cfg["hidden"] = [i for i in cfg.get("hidden", []) if i != itemid]
        lists = cfg.get("lists") or {}
        for k, v in list(lists.items()):
            lists[k] = [[i for i in c if i != itemid] for c in v] if k == "chained" else [i for i in v if i != itemid]
    save_shop(cfg)
    return {"ok": True}


def product_exists(itemid):
    return any(itemid in t for t, _, _ in hc.SHOP_KINDS.values())


@api("shop_price")
def shop_price(p):
    """A fixed price for one (also while the Shop is free; 0 = free), or none (null) = the default."""
    cfg = hc.shop_config(); itemid = as_int(p.get("itemid"), 1, 10 ** 10, "itemid")
    if not product_exists(itemid):
        raise AdminError("e_product_missing")
    prices = cfg.setdefault("prices", {})
    if p.get("price") in (None, ""):
        prices.pop(str(itemid), None)
    else:
        prices[str(itemid)] = as_int(p["price"], 0, 10 ** 12, ("th_fixed_price",))
    save_shop(cfg)
    return {"ok": True}


@api("shop_hide")
def shop_hide(p):
    cfg = hc.shop_config(); itemid = as_int(p.get("itemid"), 1, 10 ** 10, "itemid")
    if not product_exists(itemid):
        raise AdminError("e_product_missing")
    hidden =[i for i in cfg.get("hidden", []) if i != itemid]
    cfg["hidden"] = hidden + ([itemid] if p.get("hidden") else [])
    save_shop(cfg)
    return {"ok": True}


@api("shop_free")
def shop_free(p):
    cfg = hc.shop_config(); cfg["free"] = bool(p.get("free"))
    save_shop(cfg)
    return {"free": hc.SHOP_FREE}


@api("shop_lists")
def shop_lists(p):
    """Rekomendasi (new, best), MP Shop (mp), Bonus spesial (special), Paket berantai (chained)."""
    cfg = hc.shop_config(); lists = {}
    kinds = {"new": ("packs", "items", "packages", "monthly"), "best": ("packs", "items", "packages", "monthly"),
             "mp": ("packs", "items"), "special": ("packs", "items", "packages")}
    for k, v in (p.get("lists") or {}).items():
        if k in kinds:
            ids = [as_int(i, 1, 10 ** 10, "itemid") for i in v]
            bad = next((i for i in ids if not known(i, *kinds[k])), None)
            if bad is not None:
                raise AdminError("e_list_item", ("list_" + k,), bad)
            lists[k] = list(dict.fromkeys(ids))
        elif k == "chained":
            chains = [[as_int(i, 1, 10 ** 10, "itemid") for i in c] for c in v if c]
            if any(not known(i, "packages") for c in chains for i in c):
                raise AdminError("e_chain_packages")
            lists[k] = chains
    cfg["lists"] = {**(cfg.get("lists") or {}), **lists}
    save_shop(cfg)
    return {"ok": True}


class Admin(http.server.BaseHTTPRequestHandler):
    def _send(self, code, data, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _route(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if self.client_address[0] not in ("127.0.0.1", "::1") or host not in ("127.0.0.1", "localhost", "[::1]"):
            return self._send(403, b"forbidden", "text/plain")
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            with open(PAGE, "rb") as f:  # the string tables go in as JSON (ASCII, no "</" to end the script)
                page = f.read().replace(b"/*I18N*/{}", json.dumps(I18N).replace("</", "<\\/").encode())
            return self._send(200, page, "text/html; charset=utf-8")
        if url.path == "/notice":  # the game's login notice popup (its web renderer opens http_config.notice_url())
            page = (b'<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;height:100%;'
                    b'background:#1b232c;overflow:hidden}img{display:block;width:100%;height:100%;object-fit:contain}'
                    b'</style></head><body><img src="/notice.png?t=' + str(int(time.time())).encode() + b'" alt=""></body></html>')
            return self._send(200, page, "text/html; charset=utf-8")
        if url.path == "/notice.png":  # the login notice's picture, for the popup and the Pengumuman tab's preview
            if not os.path.isfile(hc.NOTICE_IMAGE):
                return self._send(404, b"", "image/png")
            with open(hc.NOTICE_IMAGE, "rb") as fh:
                return self._send(200, fh.read(), "image/png")
        m = re.fullmatch(r"/crest/(large|small)/(\d+)\.png|/season_icon/(\d+)\.png|/face/(\d+)\.png|/photo_head/(\d+)\.png",
                         url.path)  # the Database tab's pictures
        if m:
            f = (crest_file(m.group(1), m.group(2)) if m.group(1) else season_icon(m.group(3)) if m.group(3)
                 else os.path.join(photo_head_mod().DIR, f"{m.group(5)}.png") if m.group(5)
                 else hc.asset_path(f"externalAssets/players/p{m.group(4)}.png") or "")  # the one the game gets
            if not os.path.isfile(f):
                return self._send(404, b"", "image/png")
            with open(f, "rb") as fh:
                return self._send(200, fh.read(), "image/png")
        m = re.fullmatch(r"/icon/([A-Za-z0-9_]+)\.png", url.path)  # the shop icons
        if m:
            f = hc.asset_path(f"externalAssets/shop/{m.group(1)}.png")
            if f is None:
                return self._send(404, b"", "image/png")
            with open(f, "rb") as fh:
                return self._send(200, fh.read(), "image/png")
        name = url.path[len("/api/"):] if url.path.startswith("/api/") else ""
        fn = API.get(name)
        if fn is None:
            return self._send(404, b'{"error": "not found"}')
        if name not in READ_ONLY and (self.command != "POST" or self.headers.get("X-FO3-Admin") != "1"):
            return self._send(403, b'{"error": "changes need POST with the X-FO3-Admin header"}')
        params = dict(urllib.parse.parse_qsl(url.query))
        n = int(self.headers.get("Content-Length") or 0)
        try:
            if n:
                params.update(json.loads(self.rfile.read(n) or b"{}"))
            with hc.STATE_LOCK:  # same lock as the game's requests
                out = fn(params)
        except AdminError as e:
            out = {"error": e.text(self.headers.get("X-FO3-Lang"))}
        except Exception as e:  # keep the page usable; the detail shows in its message bar
            out = {"error": f"{type(e).__name__}: {e}"}
        self._send(200, json.dumps(out).encode())

    do_GET = _route
    do_POST = _route

    def log_message(self, *a):
        pass


def start(module, port):
    """Serve the admin page on 127.0.0.1:<port> in a daemon thread."""
    global hc
    hc = module
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Admin)
    threading.Thread(target=srv.serve_forever, daemon=True, name="admin").start()
    return srv

# --- Strings: one table per language; the page gets all of them and has a language switch ------
# Every table needs the same keys and {0}.. placeholders as "id" (python admin.py checks that).
# The game's own screen names (Tim inti, Item saya, Terima, Formasi, ...) stay as the Indonesian
# client shows them. lang_name labels the switch, locale formats numbers and dates, e_* are the
# API's error messages (AdminError / tr). Adding a language = one more table.
I18N = {
    "id": dict(
        ep_balance="Saldo EP", language="Bahasa", menu="Menu admin",
        nav_ringkasan="Ringkasan", nav_pemain="Pemain klub", nav_tambah="Tambah pemain", nav_item="Pak & Bag", nav_shop="Shop",
        nav_harian="Hadiah harian", nav_scout="Scout", nav_log="Log server",
        status_default="Perubahan langsung tersimpan di server; game menampilkannya setelah dibuka ulang atau setelah layar klub dimuat ulang.",
        sum_title="Ringkasan klub", sum_lead="Keadaan akun di server sekarang.", ep_title="Saldo EP", amount="Jumlah",
        ep_add="Tambah EP", ep_sub="Kurangi EP", ep_set="Jadikan saldo ini", coach_line="{0}, tim {1}",
        f_players="Pemain di klub", f_team2="Di Tim Cadangan", f_packs="Pak di Item saya", f_bag="Hadiah di Bag belum diambil",
        f_streak="Login berturut-turut", days="{0} hari", f_today="Hadiah hari ini", given="sudah diberikan", waiting="menunggu login",
        f_vip="VIP + PC warnet aktif sampai", ep_empty="Isi jumlah EP dulu.", ep_now="Saldo EP sekarang {0}.",
        pl_lead="Ubah grade, pindahkan ke Tim Cadangan (dipakai Tukar Scout), atau keluarkan pemain di luar 18 skuad. Kartu baru dari pak, trade dan Market masuk Tim inti.",
        pl_filter="Cari di daftar", ph_player="nama pemain", pl_team2_clear="Semua Tim Cadangan ke Tim inti",
        th_role="Peran", th_name="Nama", th_season="Musim", th_value="Harga EP", th_team="Tim", th_pos="Posisi",
        reserve="Cadangan", to_first="Ke Tim inti", to_second="Ke Tim Cadangan", first_team="Tim inti", release="Keluarkan",
        no_player="Tidak ada pemain yang cocok.", confirm_release="Keluarkan {0} dari klub? Tidak bisa dibatalkan.",
        grade_saved="Grade disimpan.", moved_second="Pemain pindah ke Tim Cadangan.", moved_first="Pemain kembali ke Tim inti.",
        released="{0} dikeluarkan dari klub.", team2_moved="{0} pemain kembali ke Tim inti.",
        add_lead="Cari kartu di database (rating sama seperti di game dan fifaaddict), lalu masukkan langsung ke klub atau kirim lewat Bag.",
        add_name="Nama pemain", ph_messi="mis. Messi", search="Cari", add_hint="Ketik minimal 2 huruf nama pemain.",
        on_sale="dijual", not_on_sale="tidak dijual", add_club="Masukkan ke klub", add_bag="Kirim lewat Bag",
        no_card="Tidak ada kartu dengan nama itu.", found="{0} kartu ditemukan.", search_failed="Gagal mencari: ",
        added="{0} +{1} masuk ke klub (Tim inti).", gifted="{0} +{1} dikirim ke Bag.", gift_player="Pemain: {0}",
        item_title="Pak pemain & Bag", item_lead="Pak dan item masuk ke Item saya. Hadiah Bag masuk ke Item saya → Items dan diambil dengan tombol Terima.",
        item_give="Beri pak ke Item saya", pack="Pak", count="Jumlah", item_give_btn="Beri", bag_title="Kirim hadiah ke Bag",
        title="Judul", pack_count="Jumlah pak", bag_spid="spid pemain", ph_optional="opsional", bag_send="Kirim ke Bag", no_pack="tanpa pak",
        given_packs="{0}× {1} masuk ke Item saya.", gift_sent="Hadiah \"{0}\" dikirim ke Bag.",
        shop_lead="Atur semua tab Shop: buat pak, item, paket dan paket bulanan baru dengan ikon dari client, ubah atau sembunyikan produk bawaan, dan pasang harga. Tersimpan di {0}; game memakainya saat Shop dibuka lagi.",
        shop_free="Semua produk gratis (0 EP)", shop_free_note="Harga tetap di tabel tetap berlaku.",
        kind_packs_tab="Pak pemain", kind_items_tab="Dapat dikonsumsi", kind_packages_tab="Paket", kind_monthly_tab="Paket bulanan", kind_lists_tab="Daftar tab",
        kind_packs="pak", kind_items="item", kind_packages="paket", kind_monthly="paket bulanan",
        new_kind="Buat {0} baru", edit_kind="Ubah {0}", shop_filter="Cari", ph_shop_filter="nama atau itemid",
        th_icon="Ikon", th_source="Asal", th_shop_price="Harga di Shop", th_fixed_price="Harga tetap", th_shown="Tampil",
        src_builtin="Bawaan", src_changed="Diubah", src_new="Baru", chained="Berantai", edit="Ubah", delete="Hapus", restore="Kembalikan asli",
        ph_default="bawaan", price_saved="Harga {0} disimpan.", price_reset="Harga {0} kembali ke bawaan.",
        hidden_msg="{0} disembunyikan dari Shop.", shown_msg="{0} tampil lagi di Shop.", free_on="Shop gratis.", free_off="Shop memakai harga.",
        saved_product="{0} disimpan (itemid {1}).", deleted="{0} dihapus.", restored="{0} kembali ke aslinya.",
        confirm_delete="Hapus {0}? Salinan yang sudah ada di Item saya tidak bisa dipakai lagi.", confirm_restore="Kembalikan {0} ke aslinya?",
        no_product="Tidak ada produk yang cocok.",
        f_name="Nama", f_icon="Ikon", icon_pick="Pilih ikon", f_desc="Deskripsi", f_seasons="Musim", seasons_hint="kosong = semua musim",
        f_top="Top N kartu terbaik", f_ovr="OVR minimal (kartu +1)", f_grade="Grade maks. (+1 … +N)", f_pos="Posisi", any="semua",
        f_league="ID liga", f_all="Semua kartu Market (bukan hanya lisensi ID)", f_type="Jenis",
        t_EP_LOTTERY="Kartu EP (acak)", t_ITEM_LOTTERY="Kotak acak", t_COACHNAME_CHANGE="Ganti nama manager", t_TEAM_CHANGE="Ganti tim",
        t_ENHANCEMENT_BOOST="Boost upgrade", t_LEAGUE_SIMULATION="Kupon Simulasi Liga",
        t_PLAYER_EXP_CAPSULE="EXP Capsule (EXP pemain)", f_exp="EXP", f_maxlv="Untuk pemain sampai level (kosong = semua)", f_draft="Draft: pilih N pemain, kartu di grade maks. (kosong = pak biasa)",
        e_draft_season="Pak draft harus tepat satu musim", f_ep_min="EP minimal", f_ep_max="EP maksimal", f_prizes="Hadiah (maks. 10)",
        f_weight="Bobot", f_prize="Hadiah", f_ep_amount="Jumlah EP", add_prize="Tambah hadiah", f_boost="Boost peluang upgrade (%)",
        f_contents="Isi", add_row="Tambah baris", f_days="Lama (hari)", f_now="Isi langsung saat dibeli", f_daily="Isi tiap hari",
        remove="Hapus", save="Simpan", cancel="Batal", none_type="Tidak perlu isian lain.", icons_count="{0} ikon dari client", ph_icon="cari nama ikon",
        lists_lead="Urutan produk di tab Rekomendasi, MP Shop, Bonus spesial dan Paket berantai.",
        list_new="Rekomendasi: baru", list_best="Rekomendasi: terlaris", list_mp="MP Shop", list_special="Bonus spesial",
        list_chained="Paket berantai", list_chained_hint="Dibeli berurutan; tiap baris satu rangkaian paket.", list_chain_add="Tambah rangkaian",
        list_add="Tambah", lists_save="Simpan daftar", lists_saved="Daftar tab disimpan.", empty="kosong",
        daily_title="Hadiah login harian", daily_lead="Login pertama tiap hari (WIB) memberi hadiah hari itu ke Bag. Login berturut-turut menaikkan hari; bolos sehari mengulang dari hari 1. Setelah hari terakhir, siklus kembali ke awal.",
        daily_grant="Beri hadiah hari berikutnya sekarang", daily_reset="Reset streak", daily_table="Tabel hadiah", th_day="Hari", day_n="Hari {0}",
        drop_day="Hapus hari", daily_add="Tambah hari", daily_save="Simpan tabel", f_total="Total hari login", f_last="Terakhir dapat hadiah",
        never="belum pernah", f_today_wib="Hari ini (WIB)", daily_saved="Tabel hadiah harian disimpan.", sent_bag="\"{0}\" dikirim ke Bag.",
        confirm_reset_streak="Reset streak login ke 0?", streak_reset="Streak direset.",
        nav_match="Hadiah match", mr_title="Hadiah match", mr_lead="Diberikan setelah setiap match yang dimainkan dan setiap Simulasi Liga: EP dan EXP menurut hasil, lalu tiga kartu hadiah. Kartu yang dibuka pemain diambil acak dari daftar kartu menurut bobotnya. Tersimpan di {0}; berlaku mulai match berikutnya.", mr_result="Menurut hasil", th_result="Hasil", mr_win="Menang", mr_draw="Seri", mr_loss="Kalah", mr_coach_exp="EXP manajer", mr_player_exp="EXP tiap pemain", mr_cards="Kartu hadiah", mr_chance="peluang {0}%", mr_saved="Hadiah match disimpan.",
        scout_title="Scout (Tukar Scout)", scout_lead="Scout yang baru dipakai terkunci sampai cooldown habis.", th_available="Tersedia lagi",
        locked="terkunci", ready="siap dipakai", now="sekarang", no_quest="tanpa quest", scout_reset="Reset cooldown semua scout",
        scouts_ready="Semua scout siap dipakai lagi.", q_value="nilai pemain ≥", q_grade="grade ≥", q_count="jumlah", q_season="musim",
        log_lead="Baris terakhir dari captures/http_config.log.", log_reload="Muat ulang log", log_auto="Muat otomatis tiap 3 detik",
        failed="Gagal: ", load_failed="Gagal memuat: ",
        lang_name="Bahasa Indonesia", locale="id-ID",
        grade="Grade", th_market="Market", th_scout="Scout", th_status="Status", th_quest="Quest", f_item="Item", upgrade_point="Upgrade point",
        e_player_missing="pemain tidak ditemukan", e_number="{0} harus angka", e_range="{0} harus {1}..{2}",
        e_no_spid="spid tidak ada di database", e_club_full="klub penuh ({0} pemain)",
        e_in_squad="pemain ada di 18 skuad; ganti dulu di Formasi", e_squad_first="pemain 18 skuad tetap di Tim inti",
        e_unknown_item="pak atau item tidak dikenal", e_unknown_pack="pak tidak dikenal", e_gift_empty="isi EP, pak, atau pemain",
        e_day="{0}: {1}", e_day_empty="kosong", e_table_days="tabel harus 1..31 hari",
        e_pack_empty="pak {0} tidak berisi kartu satu pun", e_length="{0} harus 1..{1} huruf",
        e_no_icon="ikon {0} tidak ada di client", e_pick_icon="pilih ikon",
        e_row_item="{0}: item {1} tidak dikenal", e_rows="{0} harus {1}..{2} baris",
        e_unknown_pos="posisi tidak dikenal", e_unknown_type="jenis item tidak dikenal",
        e_ep_range="EP minimal harus > 0 dan <= maksimal", e_unknown_prize="hadiah {0} tidak dikenal", e_prizes="hadiah harus 1..10",
        e_unknown_kind="jenis produk tidak dikenal", e_product_missing="produk tidak ditemukan", e_not_changed="produk ini tidak diubah",
        e_in_use="masih dipakai oleh {0}", e_list_item="{0}: item {1} tidak cocok di sini", e_chain_packages="paket berantai hanya berisi paket",
        nav_chat="Chat AI", chat_title="Chat manajer virtual", chat_lead="Lawan Rank (manajer virtual) mengirim pesan di chat match: saat kick-off, saat ada gol di kedua sisi, tembakan meleset, penyelamatan kiper, dan saat kamu mengetik chat. Komentarnya tentang hasil akhir muncul sebagai banner saat kembali ke layar Rank. Tiap manajer punya kepribadian sendiri (ramah, kompetitif, lucu, percaya diri, usil). Tersimpan di {0}; berlaku mulai pesan berikutnya.", chat_on="Aktifkan chat AI", chat_lang="Bahasa pesan", chat_saved="Pengaturan chat AI disimpan.", e_chat_lang="bahasa chat tidak dikenal", nav_missions="Misi", mis_title="Misi dan achievement", mis_lead="Misi harian (baris Misi harian di Manager > Daftar pencapaian, direset tiap hari pukul 00:00 WIB), achievement tetap dengan lencana (level manajer, pertandingan, kemenangan, upgrade, grade, nilai tim, gol, Rank) dan quest mingguan di tombol Quest (hadiah GP untuk kotak hadiahnya; direset Senin pukul 00:00 WIB). Server menghitung pertandingan yang kamu mainkan dan upgrade yang berhasil. Hadiah EP ikut pengali harga pemain di tab Ekonomi. Tersimpan di {0}; bahasa dan tombol Quest berlaku setelah game dibuka ulang.", mis_on="Aktifkan misi", mis_lang="Bahasa misi", mis_targets="Target (x)", mis_rewards="Hadiah (x)", mis_saved="Pengaturan misi disimpan.", e_mission_value="pengaturan misi tidak valid (target 0.1-10, hadiah 0-10, bahasa id/en/vi)", wq_title="Quest mingguan", wq_lead="Daftar quest yang diundi tiap minggu (Senin 00:00 WIB) untuk tombol Quest. Nama boleh kosong (otomatis); {0} di nama = targetnya. Target dan GP juga ikut pengali di atas. Berlaku di permintaan berikutnya; quest yang sudah selesai minggu ini tetap tercatat.", wq_th_stat="Jenis", wq_th_todo="Target", wq_th_name="Nama (opsional)", wq_add="Tambah quest", wq_drop="Hapus", wq_count="Quest per minggu", wq_shop_title="Kotak hadiah GP", wq_th_item="Item", wq_th_price="Harga (GP)", wq_shop_add="Tambah kotak", wq_reset="Kembalikan bawaan", wq_saved="Quest mingguan disimpan.", wq_reset_done="Quest mingguan dan kotak hadiah kembali ke bawaan.", wq_confirm_reset="Kembalikan daftar quest dan kotak hadiah bawaan?", wq_this="Minggu ini (sampai {0})", wq_next="Minggu depan", e_quest_value="daftar quest tidak valid (target dan GP 1-10.000, nama maks. 60 huruf, quest per minggu 1-6 dan tidak lebih dari jumlah quest)", e_quest_shop="kotak hadiah tidak valid (1-5 kotak, item berbeda, GP 1-100.000)", ptr_title="Hadiah waktu bermain", ptr_lead="Hadiah tiap kali total waktu main hari ini mencapai target (hari dihitung dari 00:00 WIB; jeda lebih dari 20 menit tanpa aktivitas tidak dihitung). Hadiah masuk Bag dan muncul sebagai notifikasi event di game. Hari ini: {0} menit, {1} hadiah diambil. Tersimpan di {2}.", ptr_minutes="Menit", ptr_add="Tambah target", ptr_saved="Hadiah waktu bermain disimpan.", e_ptr_rows="tabel waktu bermain tidak valid (1-10 baris, menit 1-1440 dan terus naik, tiap baris berisi EP atau pack)", news_region="Berita negara (opsional)", news_title="Topik global chat", news_lead="Manajer virtual di tab Channel selalu membahas berita sepak bola global, ditambah negara yang dicentang: tren Google hari ini yang soal sepak bola dan berita sepak bola Google News dalam bahasanya (X, Instagram dan TikTok butuh login, Reddit menolak server). Tiap manajer lebih suka membahas klubnya, pemain bintangnya atau negaranya (salah satu yang dicentang) kalau ada di berita. Diperbarui otomatis sehari sekali. Diambil: {0}.", news_none="Belum ada topik (server offline atau baru dinyalakan).", e_chat_region="region berita tidak dikenal", lr_title="Hadiah Rank (ranking)", lr_lead="Hadiah musim Rank (sebulan): hadiah utama dari divisi mode dengan rating terbaik di akhir musim, hadiah tambahan dari tiap mode lain yang dimainkan; masuk Bag saat musim baru mulai. Hadiah mingguan: tiap jumlah kemenangan Rank minggu ini (Senin-Minggu) yang tercapai masuk Bag lewat popup di layar Rank. Layar Rank juga menampilkan hadiah ini. Tersimpan di {0}.", lr_div="Divisi", lr_main="Hadiah utama", lr_sub="Hadiah tambahan", lr_count="Jumlah", lr_weekly="Hadiah mingguan", lr_wins="Menang minggu ini", lr_item="Hadiah", lr_add="Tambah langkah", lr_saved="Hadiah Rank disimpan.", lr_confirm_reset="Kembalikan hadiah Rank bawaan?", lr_reset_done="Hadiah Rank kembali ke bawaan.", e_lr_rows="hadiah Rank tidak valid (EP 1-10.000.000.000, pack/item 1-99; langkah mingguan 1-500 menang, naik terus, maks. 20)", kr_title="Hadiah King of the Hill", kr_lead="Hadiah mingguan (minggu = tanggal 1-7, 8-14, 15-21, 22-28) dan hadiah musim (sebulan). Setelah minggu atau musim yang kamu mainkan selesai, layar hadiah muncul saat King of the Hill dibuka dan hadiah yang tercapai masuk Bag. Mingguan: target menang atau main di minggu itu, plus hadiah divisi bila menang minimal sekian kali. Musim: hadiah divisi akhir musim bila menang minimal sekian kali, plus hadiah peringkat di region. Layar King of the Hill juga menampilkan hadiah ini. Tersimpan di {0}; berlaku langsung.", kr_type="Target mingguan", kr_type_winning="Menang", kr_type_match="Main", kr_win="Menang di minggu itu", kr_match="Main di minggu itu", kr_week_item="Hadiah target mingguan", kr_week_grade="Menang untuk hadiah divisi mingguan", kr_season_grade="Menang untuk hadiah divisi musim", kr_week_div="Hadiah divisi mingguan", kr_season_div="Hadiah divisi musim", kr_ranking="Hadiah peringkat musim (region)", kr_from="Dari peringkat", kr_to="Sampai peringkat", kr_add="Tambah peringkat", kr_saved="Hadiah King of the Hill disimpan.", kr_confirm_reset="Kembalikan hadiah King of the Hill bawaan?", kr_reset_done="Hadiah King of the Hill kembali ke bawaan.", e_kr_rows="hadiah King of the Hill tidak valid (EP 1-10.000.000.000, pack/item 1-99; target 1-100 menang atau 1-200 main, menang divisi 1-100 per minggu dan 1-500 per musim; 1-20 baris peringkat 1-1000, naik dan tidak tumpang tindih)",
        nav_notice='Pengumuman', notice_title='Gambar pengumuman', notice_lead='Gambar ini tampil di popup Pemberitahuan setiap kali login (issue #36). Pilih JPG atau PNG; halaman ini menyesuaikannya ke ukuran popup (800×720) sebelum disimpan. Berlaku saat login berikutnya, tanpa mengubah file client.', notice_file='File gambar (JPG/PNG)', notice_mode='Penyesuaian', notice_fit='Muat utuh (beri latar)', notice_fill='Penuhi kotak (potong tepi)', notice_bg='Warna latar', notice_save='Simpan pengumuman', notice_delete='Hapus pengumuman', notice_current='Pengumuman sekarang', notice_new='Pratinjau baru', notice_none='Belum ada gambar pengumuman: popup tampil kosong.', notice_saved='Gambar pengumuman disimpan; tampil saat login berikutnya.', notice_deleted='Gambar pengumuman dihapus.', notice_confirm_delete='Hapus gambar pengumuman?', notice_pick_first='Pilih file gambar dulu.', e_notice_png='gambar harus PNG 800×720 (pilih ulang file di halaman ini)', e_notice_size='gambar terlalu besar (maks 8 MB)',
        nav_assets='Aset', as_title='Gambar yang belum ada', as_lead='Game menampilkan foto pemain, logo klub, gambar seragam, logo liga dan foto staff dari cache client; yang belum ada tampil sebagai gambar bawaan (siluet, logo kosong). Tombol Unduh mengambil yang belum ada dari fifaaddict.com di latar, satu gambar tiap setengah detik; file yang sudah ada tidak diubah. Foto pemain bisa ribuan dan makan waktu lama; unduhan boleh dihentikan dan dilanjutkan kapan saja. Gambar baru tampil paling lambat setelah game dibuka ulang. Foto pemain yang tidak ada di fifaaddict.com diambil dari FC Online (FO4). Unduh semua mengambil semua jenis sekaligus dan memindai seluruh database kartu; kalau server tidak menjawab, unduhan menunggu makin lama lalu lanjut sendiri.', as_all='Semua gambar', as_fetch_all='Unduh semua yang belum ada', as_kind='Jenis', as_added='Sudah diunduh', as_failed='Tidak tersedia', as_faces='Foto pemain', as_crests='Logo klub', as_uniforms='Gambar seragam', as_leagues='Logo liga', as_staffs='Foto staff', as_careers='Riwayat klub', as_careers_note='Riwayat klub mengambil karir klub pemain sampai musim berjalan dari Wikidata (tahun, klub, pinjaman) untuk tab Karakteristik di info pemain. Selesai, tekan Database > Terapkan ke game. Pemain yang tidak ditemukan tetap memakai riwayat bawaan FO3 (sampai 2017).', as_fetch='Unduh', as_stop='Hentikan', as_running='Sedang mengunduh: {0}', as_idle='Tidak ada unduhan berjalan.', as_started='Unduhan dimulai.', as_stopped='Unduhan dihentikan.', e_asset_kind='jenis gambar tidak dikenal', e_asset_busy='unduhan lain masih berjalan',
        nav_prematch="Pengaturan match", pm_title="Pengaturan match", pm_lead="Stadion, waktu, cuaca dan durasi untuk match berikutnya (Liga, Cup, Rank). Di Multiple > Rank semuanya juga bisa diganti di layar pengaturan match, dan pilihan itu ikut tersimpan di sini; layar Liga dan Cup hanya menampilkannya. Mendung: berawan di siang hari, senja di malam hari (layar Liga/Cup menuliskannya \"Salju\").", pm_stadium="Stadion", pm_tod="Waktu", pm_weather="Cuaca", pm_length="Durasi", pm_home="Kandang tim tuan rumah", pm_random="Acak", pm_day="Siang", pm_night="Malam", pm_dry="Cerah", pm_rain="Hujan", pm_snow="Salju", pm_overcast="Mendung", pm_minutes="{0} menit", pm_ball="Bola", pm_saved="Pengaturan match disimpan.", e_pm_value="nilai pengaturan match tidak valid",
        nav_economy="Ekonomi", eco_title="Ekonomi", eco_lead="Harga dan EP untuk semua manajer. Santai = seperti biasa (Shop gratis). Realistis: Shop berbayar (harga barang = isinya, barang tanpa EP seperti boost atau kapsul EXP punya harga dasar dikali pengali harga pemain; pack = nilai kartunya, +13% dengan pasar dinamis karena pack bisa dibuka di hari mahal) dan pasar dinamis. Sulit: harga pemain dan pack x2, EP awal kecil. Pasar dinamis: harga tiap pemain naik-turun setiap hari, manajer virtual kadang menjual murah, dan kamu boleh memasang harga sampai 105% (lebih lama terjual): beli murah, jual mahal. Aturan ketat (Sulit): Upgrade, Tukar dan Scout tidak mencetak EP: biaya upgrade sebesar nilai yang ditambahkannya, restore dibayar EP, tawaran tidak melebihi nilai kartu yang diserahkan. Tanpa itu (Santai, Realistis) berlaku aturan game asli: biaya upgrade 2% dari nilai kartu, restore dengan poin upgrade, tawaran tanpa batas. EP dari match diatur di tab Hadiah match; EP awal berlaku untuk manajer baru.", eco_preset="Preset", eco_custom="Kustom", eco_easy="Santai (seperti biasa)", eco_real="Realistis", eco_hard="Sulit", eco_shopfree="Shop gratis (0 EP)", eco_players="Harga pemain (x)", eco_packs="Harga pack (x)", eco_start="EP awal manajer baru", eco_dynamic="Pasar dinamis (harga naik-turun, bisa untung dari jual-beli)", eco_strict="Aturan ketat (Upgrade, Tukar dan Scout tidak mencetak EP)", eco_saved="Pengaturan ekonomi disimpan.", e_eco_value="nilai ekonomi tidak valid (pengali 0.1-100, EP awal 0 atau lebih)",
        nav_db='Database', db_title='Database kartu dan klub', db_lead='Ubah kartu pemain per musim: atribut, posisi, kaki, kaki lemah, skill move, work rate, karakteristik dan klub (transfer). Satu kartu = satu spid = musim × 1.000.000 + player id (Messi 158023: kartu musim 91 = 91158023); cari dengan nama, spid atau player id. Perubahan tersimpan di gamedata/fifadb.sqlite dan dicatat di {0} (nilai asli dan baru: bisa dikembalikan, dan build musim FO4 memasangnya lagi). Game memakai salinan DB-nya sendiri: tutup game, tekan Terapkan ke game, tunggu sampai selesai, lalu jalankan play.bat.', db_apply='Terapkan ke game', db_started='Memasang ke game di latar…', db_done='Selesai terpasang. Jalankan play.bat untuk memulai server dan game lagi.', db_failed='Gagal (kode {0}); lihat log di bawah.', as_install='Terapkan ke game', db_search='Cari kartu', db_q='Nama, spid atau player id', db_hint='Ketik minimal 2 huruf nama, atau angka spid / player id.', db_edited='Kartu yang sudah diubah', db_none='Belum ada kartu yang diubah.', th_changed='Yang diubah', f_club='Klub', ph_club='ketik nama klub', f_pos_n='Posisi {0}', f_foot='Kaki utama', foot_right='Kanan', foot_left='Kiri', f_weak='Kaki lemah', f_skill='Skill move', f_wr_att='Work rate menyerang', f_wr_def='Work rate bertahan', wr_low='Rendah', wr_med='Sedang', wr_high='Tinggi', f_stats='Atribut', f_traits='Karakteristik', orig_value='asli: {0}', card_saved='{0} disimpan, OVR sekarang {1}. Tekan Terapkan ke game agar game memakainya.', crest_title='Logo klub', crest_lead='Pilih klub dan gambar (PNG transparan paling bagus); halaman ini menyesuaikannya ke 256×256 dan 128×128 lalu menyimpannya ke cache client. Logo asli disimpan di gamedata/crest_backup dan bisa dikembalikan. Tampil setelah game dibuka ulang.', crest_save='Simpan logo', crest_revert='Kembalikan logo asli', crest_confirm='Kembalikan logo asli klub ini?', crest_now='Logo sekarang', crest_new='Logo baru', crest_none='Belum ada logo klub ini di cache.', crest_pick='Pilih klub dan file gambar dulu.', crest_saved='Logo disimpan; tampil setelah game dibuka ulang.', crest_reverted='Logo asli dikembalikan.', e_card_field='{0} tidak bisa diubah di sini', e_club='klub {0} tidak ada di game', e_not_edited='kartu ini belum diubah', e_crest_png='gambar logo harus PNG {0}×{0} (pilih ulang file di halaman ini)',
        f_look='Tampilan 3D', look_lead='Kode EA dari DB 3D game (fifa_ng_db). Wajah = kepala dari baris 3D mana pun: daftar berisi kartu-kartu pemain ini; ketik nama pemain lain untuk memilih kepalanya dari daftar, atau ketik spid / player id. Terlihat di game setelah Terapkan ke game.', look_generic='Kepala sekarang generik: model dan warna rambut (0 = pirang), jenggot, kulit dan mata bisa diubah.', look_scan='Kepala sekarang hasil scan: rambut dan jenggot ikut kepalanya; untuk rambut lain pilih wajah dari kepala lain.', look_shared='Kartu musim di atas 99 (26 LIVE, 25 LIVE) tidak punya baris 3D sendiri: perubahan ini berlaku untuk semua kartu pemain ini yang tanpa baris sendiri.', look_own='kartu ini', f_face='Wajah', l_hairtypecode='Model rambut', l_haircolorcode='Warna rambut', l_facialhairtypecode='Jenggot/kumis', l_facialhaircolorcode='Warna jenggot', l_skintonecode='Warna kulit', l_eyecolorcode='Warna mata', l_bodytypecode='Bentuk badan', e_no_engine='DB 3D game (data_0.big) tidak terbaca', sn_title='Musim buatan', sn_lead='Buat musim kartu sendiri: kode, nama dan lencana. Isi musim dengan menyalin kartu dari hasil pencarian (statnya bisa ditambah sekaligus), lalu ubah seperti kartu lain. Tersimpan di {0}; Terapkan ke game memasang lencananya, dan setelah play.bat musim ini punya pak Top 200 dan team color sendiri.', f_season_code='Kode musim', th_badge='Lencana', th_cards='Kartu', sn_badge='Lencana (PNG/JPG)', sn_save='Simpan musim', sn_saved='Musim {0} disimpan.', sn_delete='Hapus musim', sn_confirm_delete='Hapus musim {0} beserta {1} kartunya? Kartu musim ini yang sudah dimiliki klub ikut hilang dari database.', sn_deleted='Musim {0} dihapus.', sn_copy_to='Salin ke musim', f_boost_stats='Tambah semua atribut', sn_copy='Salin', sn_copied='{0} disalin ke musim {1} (spid {2}).', sn_none='Belum ada musim buatan.', sn_remove='Hapus kartu', sn_removed='Kartu {0} dihapus.', confirm_card_remove='Hapus kartu {0} (spid {1}) dari musimnya?', sn_pick_badge='Pilih lencana dulu.', mod_title='Mod: ekspor dan impor', mod_lead='Simpan semua musim buatan, kartu salinan, perubahan kartu dan team color buatan ke satu file JSON, atau masukkan file seperti itu dari server lain. Setelah impor, tekan Terapkan ke game.', mod_export='Ekspor mod', mod_import='Impor mod (JSON)', mod_imported='Mod diimpor: {0} berhasil, {1} gagal', e_mod='file bukan mod FO3ID (JSON dari Ekspor mod)', e_season_icon='lencana harus PNG sampai 1 MB (pilih ulang file di halaman ini)', e_face='tidak ada baris 3D {0}', e_season='musim {0} bukan musim buatan', e_season_code='kode musim {0} sudah dipakai game', e_card_exists='kartu {0} sudah ada',
        nav_tc='Team color', tc_title='Team color buatan', tc_lead='Buat team color sendiri: kartu yang masuk dipilih dengan filter (musim, klub sekarang atau dulu, negara, pemain, OVR minimal, N kartu Market terbaik); kartu harus memenuhi semua filter yang diisi. Tingkatnya seperti team color spesial bawaan: 5-6 / 7-8 / 9-11 pemain berbeda di XI (OVR +1/+2/+3, atau sampai jumlah OVR yang dipilih); boost atribut ditambahkan di tiap tingkat. Kurang dari 5 pemain: satu tingkat untuk semuanya. Tersimpan di {0}; berlaku mulai login berikutnya, tanpa restart.', tc_members='Anggota', tc_boost='Boost', tc_new='Team color baru', tc_edit='Ubah {0}', tc_nations='Negara', tc_players='Pemain', tc_min_ovr='OVR minimal (0 = semua)', tc_best='N kartu terbaik (0 = semua)', tc_saved='{0} disimpan: {1} kartu dari {2} pemain.', tc_deleted='{0} dihapus.', tc_confirm_delete='Hapus team color {0}?', tc_none='Belum ada team color buatan.', tc_all_cards='semua kartu', tc_all_pos='semua posisi', ph_pick='ketik lalu pilih', e_nation='negara {0} tidak dikenal', e_player_id='tidak ada pemain dengan player id {0}', e_color_missing='team color tidak ditemukan', e_tc_value='nilai team color tidak valid',
        koth_title='Region King of the Hill', koth_lead='Pilih wilayah yang dipakai di King of the Hill: kota-kota Indonesia, Singapura / Malaysia, Thailand, Vietnam (nama Vietnam dengan huruf Vietnam) atau wilayah kustom buatanmu. Setelah diganti, manajer virtual tersebar ke kota-kota wilayah baru dan region pilihanmu kosong lagi: pilih region baru saat King of the Hill dibuka. Tanpa restart.', koth_regions='Wilayah', koth_id='Indonesia', koth_sgmy='Singapura / Malaysia', koth_th='Thailand', koth_vn='Vietnam', koth_custom='Kustom', koth_saved='Wilayah King of the Hill disimpan.', e_koth_set='wilayah tidak dikenal', koth_custom_title='Wilayah kustom', koth_custom_lead='Tulis wilayahmu sendiri, satu region per baris: Region: kota, kota, kota (contoh: Miền Bắc: Hà Nội, Hải Phòng, Sa Pa). 1-20 region, 1-30 kota per region, nama maksimal 24 huruf dan tidak boleh dobel. Salin dari wilayah bawaan lalu ubah. Kota yang namanya tetap menyimpan id-nya, jadi pemain di kota itu tetap di sana. Simpan, lalu pilih Kustom di atas. Tersimpan di {0}; berlaku langsung.', koth_copy='Salin dari', koth_copy_btn='Salin ke editor', koth_custom_save='Simpan wilayah kustom', koth_custom_saved='Wilayah kustom disimpan: {0} region, {1} kota.', e_koth_line='baris {0}: tulis Region: kota, kota', e_koth_name='nama kosong, lebih dari 24 huruf atau dobel: {0}', e_koth_count='harus 1-20 region dengan 1-30 kota masing-masing', e_koth_empty='wilayah kustom masih kosong: isi dan simpan dulu',
        face_title='Foto pemain', face_lead='Foto kartu di game (128×128). Game mencari foto kartu (p<spid>.png, foto musimnya) dulu, lalu foto pemain (p<player id>.png); kalau tidak ada, tampil wajah bawaan. Tab Aset mengunduh yang belum ada dari fifaaddict.com, termasuk foto tiap musim. Di sini foto bisa diganti untuk semua kartu pemain ini atau kartu ini saja: dengan gambar sendiri (PNG/JPG; halaman ini menyesuaikannya ke 128×128) atau dengan foto spid / player id lain (dari cache, atau langsung dari fifaaddict.com kalau belum ada). Tersimpan di gamedata/faces, file cache client tidak diubah; Kembalikan menghapusnya. Tampil setelah game dibuka ulang.', face_for='Untuk', face_player='Semua kartu pemain ini (player id {0})', face_card='Kartu ini saja (spid {0})', face_save='Simpan foto', face_revert='Kembalikan foto asli', face_confirm='Hapus foto buatan ini dan pakai foto asli lagi?', face_now='Foto di game', face_new='Foto baru', face_none='Belum ada foto: game menampilkan wajah bawaan.', face_pick='Pilih file gambar dulu.', face_saved='Foto disimpan; tampil setelah game dibuka ulang.', face_reverted='Foto asli dikembalikan.', e_face_png='foto harus PNG {0}×{0} (pilih ulang file di halaman ini)', head_scan='kepala scan', head_generic='kepala generik', face_from='Foto dari spid / player id', face_use='Pakai foto ini', face_used='Foto {0} dipakai; tampil setelah game dibuka ulang.', e_face_none='tidak ada foto {0} di cache maupun di fifaaddict.com', e_face_fetch='fifaaddict.com tidak menjawab ({0}); coba lagi nanti',
        phd_title='Wajah 3D dari foto', phd_lead='Untuk pemain berwajah generik: foto close-up dari depan dipasang sebagai kulit wajah pada kepala 3D pemain lain yang sudah di-scan (donor: bentuk kepala dan rambutnya ikut, matanya tetap milik donor). Pilih foto (wajah lurus ke depan, cahaya rata), klik 11 titik berurutan, pilih donor yang mirip (warna kulit, rambut; tanpa janggut kalau pemainnya tidak berjanggut), lalu Buat. Berlaku untuk semua kartu pemain ini yang memakai baris player id-nya atau kepala generik. Tersimpan di gamedata/photo_heads; tampil setelah Database > Terapkan ke game dan game dibuka ulang.', phd_donor='Kepala donor (baris engine)', phd_donor_hint='ketik nama pemain berkepala scan', phd_step='Klik titik {0}/{1}: {2}', phd_all='Ke-{0} titik sudah ditandai; klik dekat sebuah titik untuk menggesernya.', phd_undo='Batalkan titik', phd_reset='Ulang semua titik', phd_photo='Foto dan titik', phd_now='Tekstur wajah 3D', phd_none='Belum ada wajah 3D dari foto.', phd_make='Buat wajah 3D', phd_delete='Hapus wajah 3D', phd_confirm='Hapus wajah 3D dari foto pemain ini?', phd_made='Wajah 3D dibuat. Tekan Database > Terapkan ke game (game ditutup), lalu buka game.', phd_deleted='Wajah 3D dihapus; Terapkan ke game untuk mengeluarkannya dari client.', phd_pick='Pilih foto dulu.', pt_eye_r='tengah mata kanan pemain (kiri di foto)', pt_eye_l='tengah mata kiri pemain (kanan di foto)', pt_nose='ujung hidung', pt_mouth_r='sudut mulut kanan (kiri di foto)', pt_mouth_l='sudut mulut kiri (kanan di foto)', pt_chin='ujung dagu', pt_forehead='tengah dahi', pt_temple_r='pelipis kanan: tepi wajah setinggi alis (kiri di foto)', pt_temple_l='pelipis kiri: tepi wajah setinggi alis (kanan di foto)', pt_jaw_r='rahang kanan: tepi wajah sedikit di bawah mulut (kiri di foto)', pt_jaw_l='rahang kiri: tepi wajah sedikit di bawah mulut (kanan di foto)', e_ph_png='foto harus PNG paling besar {0} piksel (pilih ulang file di halaman ini)', e_ph_points='tandai ke-{0} titik di foto dulu', e_ph_donor='baris {0} bukan kepala scan: pilih pemain berkepala scan sebagai donor', e_ph_files='file kepala {0} tidak ada di client',
        phd_icon='Ambil dari ikon', phd_icon_done='Foto, 11 titik dan donor diisi dari ikon pemain. Periksa titiknya (klik dekat sebuah titik untuk menggesernya), lalu Buat wajah 3D.', phd_icon_nopts='wajah tidak ditemukan di ikon: klik titiknya sendiri', e_ph_icon='tidak ada ikon pemain {0} di cache maupun di fifaaddict.com', phd_club='Buat wajah 3D dari ikon untuk pemain generik di klub', phd_club_lead='Wajah 3D otomatis: setiap pemain klub yang masih berwajah generik dan punya ikon di cache (foto FO3; tab Aset mengunduh yang belum ada) mendapat wajah 3D dari ikonnya. Titik wajah dicari detektor YuNet, donor dipilih menurut warna kulit dan rambut. Pertama kali sekitar satu menit (semua kepala scan dibaca sekali). Hasil tiap pemain bisa diperiksa dan diganti di dialog kartu; lalu Terapkan ke game.', phd_club_busy='Membuat wajah 3D…', phd_club_done='{0} wajah 3D dibuat, {1} dilewati.', phd_skip_icon='tanpa ikon', phd_skip_face='wajah tidak ditemukan',
        world_title='Dunia game', world_lead='Modern: klub AI, pilihan klub dan pak This Season memakai kartu 26 LIVE (jika terpasang). Klasik FO3: semua klub memakai skuad 2017 asli FO3, manajer virtual hanya memakai musim asli FO3, dan musim tambahan (25 LIVE, 26 LIVE, WC18, musim buatan) tidak ada di pak, Market maupun Tukar: hanya bisa diberikan lewat halaman admin (Tambah pemain, Bag). Klub dan koleksi pemainmu tidak berubah. Berlaku setelah server dimulai ulang (play.bat).', world_label='Mode', world_modern='Modern (26 LIVE)', world_classic='Klasik FO3 (2017)', world_saved='Dunia game disimpan.', world_restart='Server masih berjalan dengan mode sebelumnya: jalankan play.bat agar pilihan ini berlaku.', e_world='mode dunia tidak dikenal',
        vm_title='Manajer virtual', vm_lead='Sekitar 2.000 manajer virtual (lawan Rank dan King of the Hill, klub AI, Market) tetap sama setiap kali game dan server dijalankan. Hanya tombol Generate ulang di sini yang membuat kerumunan baru: nama, level, klub, skuad, gaya main, rating Rank, region dan klub AI berganti, langsung tanpa restart (layar game yang sudah terbuka diperbarui saat dibuka lagi). Id mereka tetap, jadi teman, klub dan riwayatmu tidak rusak, tapi orang di baliknya berubah. Kembalikan ke awal memunculkan lagi kerumunan pertama.', vm_regen='Generate ulang', vm_reset='Kembalikan ke awal', vm_gen_now='Generasi {0} ({1} manajer sekarang).', vm_confirm='Buat kerumunan manajer virtual yang baru? Nama, klub dan skuad mereka semua langsung berganti.', vm_regen_done='Kerumunan baru dipakai sekarang.', vm_reset_done='Kerumunan pertama dipakai lagi.',
        st_title='Staff', st_lead='Staff di akun: {0} dari {1}. Staff baru tampil di Tim > Manajemen Staff saat layar berganti.', st_staff='Staff', st_grade='Bintang', st_life='Kontrak', st_status='Status', st_add='Tambah staff', st_added='{0} × {1} ditambahkan.', st_removed='{0} dihapus.', st_remove='Hapus', st_confirm='Hapus staff {0} dari akun?', st_none='Belum ada staff.', st_helper='pembantu', st_appointed='Bertugas', st_studying='Pembinaan', e_unknown_staff='staff tidak dikenal', e_staff_full='staff penuh: sisa tempat {0}', e_no_staff='staff tidak ada',
        phd_redo='Buat ulang semua wajah 3D dari ikon', phd_redo_confirm='Buat ulang semua wajah 3D yang sudah ada dari ikon pemainnya? Donor dipilih ulang (warna kulit dibandingkan dengan foto donor). Wajah yang dibuat dari foto unggahan ikut diganti ikon.',
        t_PLAYER_GROWTH_RESET='Reset poin taktik (Perkembangan pemain)',
    ),
    "en": dict(
        ep_balance="EP balance", language="Language", menu="Admin menu",
        nav_ringkasan="Overview", nav_pemain="Club players", nav_tambah="Add players", nav_item="Packs & Bag", nav_shop="Shop",
        nav_harian="Daily rewards", nav_scout="Scouts", nav_log="Server log",
        status_default="Changes are saved on the server at once; the game shows them after a restart or when the club screen reloads.",
        sum_title="Club overview", sum_lead="The account on the server right now.", ep_title="EP balance", amount="Amount",
        ep_add="Add EP", ep_sub="Take EP", ep_set="Set the balance to this", coach_line="{0}, club {1}",
        f_players="Players in the club", f_team2="In Tim Cadangan", f_packs="Packs in Item saya", f_bag="Bag gifts not collected",
        f_streak="Login streak", days="{0} days", f_today="Today's reward", given="given", waiting="waiting for a login",
        f_vip="VIP + PC café perks until", ep_empty="Enter an EP amount first.", ep_now="EP balance is now {0}.",
        pl_lead="Change a card's grade, move it to Tim Cadangan (Tukar Scout trades from there), or release a card outside the squad's 18. New cards from packs, trades and the Market join Tim inti.",
        pl_filter="Filter the list", ph_player="player name", pl_team2_clear="Move every Tim Cadangan card to Tim inti",
        th_role="Role", th_name="Name", th_season="Season", th_value="EP value", th_team="Team", th_pos="Position",
        reserve="Reserve", to_first="To Tim inti", to_second="To Tim Cadangan", first_team="Tim inti", release="Release",
        no_player="No matching player.", confirm_release="Release {0} from the club? This cannot be undone.",
        grade_saved="Grade saved.", moved_second="Moved to Tim Cadangan.", moved_first="Back in Tim inti.",
        released="{0} released from the club.", team2_moved="{0} cards moved back to Tim inti.",
        add_lead="Search the card database (ratings as in the game and on fifaaddict), then put a card straight into the club or send it through the Bag.",
        add_name="Player name", ph_messi="e.g. Messi", search="Search", add_hint="Type at least 2 letters of a name.",
        on_sale="on sale", not_on_sale="not on sale", add_club="Add to club", add_bag="Send via Bag",
        no_card="No card with that name.", found="{0} cards found.", search_failed="Search failed: ",
        added="{0} +{1} joined the club (Tim inti).", gifted="{0} +{1} sent to the Bag.", gift_player="Player: {0}",
        item_title="Player packs & Bag", item_lead="Packs and items go to Item saya. Bag gifts go to Item saya → Items and are collected with Terima.",
        item_give="Give a pack to Item saya", pack="Pack", count="Count", item_give_btn="Give", bag_title="Send a gift to the Bag",
        title="Title", pack_count="Pack count", bag_spid="Player spid", ph_optional="optional", bag_send="Send to the Bag", no_pack="no pack",
        given_packs="{0}× {1} added to Item saya.", gift_sent="Gift \"{0}\" sent to the Bag.",
        shop_lead="Run every Shop tab: create packs, items, packages and monthly packages with the client's own icons, change or hide the built-in ones, and set prices. Saved in {0}; the game uses it the next time the Shop opens.",
        shop_free="Every product is free (0 EP)", shop_free_note="Fixed prices in the table still apply.",
        kind_packs_tab="Player packs", kind_items_tab="Consumables", kind_packages_tab="Packages", kind_monthly_tab="Monthly packages", kind_lists_tab="Tab lists",
        kind_packs="pack", kind_items="item", kind_packages="package", kind_monthly="monthly package",
        new_kind="New {0}", edit_kind="Edit {0}", shop_filter="Filter", ph_shop_filter="name or itemid",
        th_icon="Icon", th_source="Source", th_shop_price="Shop price", th_fixed_price="Fixed price", th_shown="Shown",
        src_builtin="Built-in", src_changed="Changed", src_new="New", chained="Chained", edit="Edit", delete="Delete", restore="Restore original",
        ph_default="default", price_saved="Price of {0} saved.", price_reset="{0} uses its default price again.",
        hidden_msg="{0} is hidden from the Shop.", shown_msg="{0} shows in the Shop again.", free_on="The Shop is free.", free_off="The Shop charges prices.",
        saved_product="{0} saved (itemid {1}).", deleted="{0} deleted.", restored="{0} is back to the original.",
        confirm_delete="Delete {0}? Copies already in Item saya stop working.", confirm_restore="Restore {0} to the original?",
        no_product="No matching product.",
        f_name="Name", f_icon="Icon", icon_pick="Pick an icon", f_desc="Description", f_seasons="Seasons", seasons_hint="none = every season",
        f_top="Top N best cards", f_ovr="Min OVR (card at +1)", f_grade="Max grade (+1 … +N)", f_pos="Position", any="any",
        f_league="League id", f_all="Every Market card (not only ID-licensed)", f_type="Type",
        t_EP_LOTTERY="EP card (random)", t_ITEM_LOTTERY="Random box", t_COACHNAME_CHANGE="Manager name change", t_TEAM_CHANGE="Club change",
        t_ENHANCEMENT_BOOST="Upgrade boost", t_LEAGUE_SIMULATION="League Simulation coupon",
        t_PLAYER_EXP_CAPSULE="EXP Capsule (player EXP)", f_exp="EXP", f_maxlv="For players up to level (empty = any)", f_draft="Draft: pick N players, card at the max grade (empty = normal pack)",
        e_draft_season="A draft pack needs exactly one season", f_ep_min="Min EP", f_ep_max="Max EP", f_prizes="Prizes (up to 10)",
        f_weight="Weight", f_prize="Prize", f_ep_amount="EP amount", add_prize="Add a prize", f_boost="Upgrade chance boost (%)",
        f_contents="Contents", add_row="Add a row", f_days="Length (days)", f_now="Items at purchase", f_daily="Items every day",
        remove="Remove", save="Save", cancel="Cancel", none_type="Nothing else to fill in.", icons_count="{0} icons from the client", ph_icon="icon name",
        lists_lead="The order of products in the Featured, MP Shop, Special bonus and Chained package tabs.",
        list_new="Featured: new", list_best="Featured: best sellers", list_mp="MP Shop", list_special="Special bonus",
        list_chained="Chained packages", list_chained_hint="Bought in order; one chain of packages per row.", list_chain_add="Add a chain",
        list_add="Add", lists_save="Save lists", lists_saved="Tab lists saved.", empty="empty",
        daily_title="Daily login rewards", daily_lead="The first login each day (WIB) puts that day's reward in the Bag. Logging in on consecutive days moves to the next day; missing a day starts again at day 1. After the last day the cycle starts over.",
        daily_grant="Give the next day's reward now", daily_reset="Reset the streak", daily_table="Reward table", th_day="Day", day_n="Day {0}",
        drop_day="Remove day", daily_add="Add a day", daily_save="Save table", f_total="Days logged in", f_last="Last reward",
        never="never", f_today_wib="Today (WIB)", daily_saved="Daily reward table saved.", sent_bag="\"{0}\" sent to the Bag.",
        confirm_reset_streak="Reset the login streak to 0?", streak_reset="Streak reset.",
        nav_match="Match rewards", mr_title="Match rewards", mr_lead="Given after every played match and every Liga simulation: EP and exp by result, then three reward cards. The card the player opens is drawn from the card list by weight. Saved in {0}; used from the next match.", mr_result="By result", th_result="Result", mr_win="Win", mr_draw="Draw", mr_loss="Loss", mr_coach_exp="Manager exp", mr_player_exp="Exp per player", mr_cards="Reward cards", mr_chance="chance {0}%", mr_saved="Match rewards saved.",
        scout_title="Scouts (Tukar Scout)", scout_lead="A scout just used stays locked until its cooldown ends.", th_available="Available again",
        locked="locked", ready="ready", now="now", no_quest="no quest", scout_reset="Reset every scout's cooldown",
        scouts_ready="Every scout is ready again.", q_value="player value ≥", q_grade="grade ≥", q_count="count", q_season="season",
        log_lead="The last lines of captures/http_config.log.", log_reload="Reload log", log_auto="Reload every 3 seconds",
        failed="Failed: ", load_failed="Could not load: ",
        lang_name="English", locale="en-GB",
        grade="Grade", th_market="Market", th_scout="Scout", th_status="Status", th_quest="Quest", f_item="Item", upgrade_point="Upgrade point",
        e_player_missing="player not found", e_number="{0} must be a number", e_range="{0} must be {1}..{2}",
        e_no_spid="no such spid in the database", e_club_full="the club is full ({0} players)",
        e_in_squad="the player is in the squad's 18; swap him out in Formasi first", e_squad_first="players in the squad's 18 stay in Tim inti",
        e_unknown_item="unknown pack or item", e_unknown_pack="unknown pack", e_gift_empty="fill in EP, a pack or a player",
        e_day="{0}: {1}", e_day_empty="empty", e_table_days="the table must have 1..31 days",
        e_pack_empty="pack {0} holds no card at all", e_length="{0} must be 1..{1} characters",
        e_no_icon="icon {0} is not in the client", e_pick_icon="pick an icon",
        e_row_item="{0}: unknown item {1}", e_rows="{0} must have {1}..{2} rows",
        e_unknown_pos="unknown position", e_unknown_type="unknown item type",
        e_ep_range="EP min must be > 0 and <= max", e_unknown_prize="unknown prize {0}", e_prizes="prizes must be 1..10",
        e_unknown_kind="unknown product kind", e_product_missing="product not found", e_not_changed="this product was not changed",
        e_in_use="still used by {0}", e_list_item="{0}: item {1} does not fit here", e_chain_packages="a chained package holds packages only",
        nav_chat="AI chat", chat_title="Virtual managers' chat", chat_lead="Rank opponents (virtual managers) post in the match chat: at kick-off, on goals both ways, missed shots, keeper saves, and when you type in chat. Their word on the result shows as a banner back on the Rank screen. Each manager has a personality (friendly, competitive, funny, confident, playful). Saved in {0}; used from the next message.", chat_on="AI chat on", chat_lang="Message language", chat_saved="AI chat settings saved.", e_chat_lang="unknown chat language", nav_missions="Missions", mis_title="Missions and achievements", mis_lead="Daily missions (the Daily missions row in Manager > Achievements, reset every day at 00:00 WIB), lasting achievements with badges (manager level, matches, wins, upgrades, grade, team value, goals, Rank) and weekly quests behind the Quest button (GP for its reward boxes; reset on Monday at 00:00 WIB). The server counts the matches you play and the upgrades that succeed. EP rewards follow the player price multiplier on the Economy tab. Saved in {0}; the language and the Quest button apply once the game is restarted.", mis_on="Missions on", mis_lang="Mission language", mis_targets="Targets (x)", mis_rewards="Rewards (x)", mis_saved="Mission settings saved.", e_mission_value="invalid mission setting (targets 0.1-10, rewards 0-10, language id/en/vi)", wq_title="Weekly quests", wq_lead="The quests each week (from Monday 00:00 WIB) draws for the Quest button. A name may be left empty (automatic); {0} in a name is its target. Targets and GP also follow the multipliers above. Used from the next request; quests already done this week stay done.", wq_th_stat="Type", wq_th_todo="Target", wq_th_name="Name (optional)", wq_add="Add quest", wq_drop="Remove", wq_count="Quests a week", wq_shop_title="GP reward boxes", wq_th_item="Item", wq_th_price="Price (GP)", wq_shop_add="Add box", wq_reset="Restore defaults", wq_saved="Weekly quests saved.", wq_reset_done="Weekly quests and reward boxes are back to the defaults.", wq_confirm_reset="Restore the default quests and reward boxes?", wq_this="This week (until {0})", wq_next="Next week", e_quest_value="invalid quest list (target and GP 1-10,000, name up to 60 characters, 1-6 quests a week and no more than the list has)", e_quest_shop="invalid reward boxes (1-5 boxes, different items, GP 1-100,000)", ptr_title="Play time rewards", ptr_lead="A reward each time today's total play time reaches a target (the day starts at 00:00 WIB; a break of over 20 minutes without activity does not count). It goes to the Bag and shows as an event notification in the game. Today: {0} minutes, {1} rewards taken. Saved in {2}.", ptr_minutes="Minutes", ptr_add="Add target", ptr_saved="Play time rewards saved.", e_ptr_rows="invalid play time table (1-10 rows, minutes 1-1440 and rising, each row with EP or a pack)", news_region="Country news (optional)", news_title="Global chat topics", news_lead="The virtual managers in the Channel tab always talk about the global football news, plus the ticked countries': today's Google trends that are about football and Google News football headlines in their language (X, Instagram and TikTok need a login, Reddit refuses a server). Each manager prefers his club, his star player or his country (one of the ticked) when they are in the news. Refreshed by itself once a day. Fetched: {0}.", news_none="No topics yet (the server is offline or just started).", e_chat_region="unknown news region", lr_title="Rank rewards (ranking)", lr_lead="Rank season rewards (a month): the main item of the division of the mode rated best at the season's end, the sub item of every other mode played; they go to the Bag when the new season starts. Weekly rewards: each number of Rank wins this week (Monday to Sunday) reached goes to the Bag with a popup on the Rank screen. The Rank screen shows these rewards too. Saved in {0}.", lr_div="Division", lr_main="Main reward", lr_sub="Sub reward", lr_count="Count", lr_weekly="Weekly rewards", lr_wins="Wins this week", lr_item="Reward", lr_add="Add step", lr_saved="Rank rewards saved.", lr_confirm_reset="Restore the default Rank rewards?", lr_reset_done="Rank rewards are back to the defaults.", e_lr_rows="invalid Rank rewards (EP 1-10,000,000,000, pack/item 1-99; weekly steps 1-500 wins, rising, at most 20)", kr_title="King of the Hill rewards", kr_lead="Weekly rewards (weeks = days 1-7, 8-14, 15-21, 22-28) and season rewards (a month). Once a week or season you played is over, the reward screen shows when King of the Hill opens and what was reached goes to the Bag. Weekly: a goal of wins or matches that week, plus a division reward with at least so many wins. Season: the division reward at the season's end with at least so many wins, plus a reward for the rank in the region. The King of the Hill screen shows these rewards too. Saved in {0}; counts at once.", kr_type="Weekly goal", kr_type_winning="Wins", kr_type_match="Matches", kr_win="Wins that week", kr_match="Matches that week", kr_week_item="Weekly goal reward", kr_week_grade="Wins for the weekly division reward", kr_season_grade="Wins for the season division reward", kr_week_div="Weekly division reward", kr_season_div="Season division reward", kr_ranking="Season rank rewards (region)", kr_from="From rank", kr_to="To rank", kr_add="Add ranks", kr_saved="King of the Hill rewards saved.", kr_confirm_reset="Restore the default King of the Hill rewards?", kr_reset_done="King of the Hill rewards are back to the defaults.", e_kr_rows="invalid King of the Hill rewards (EP 1-10,000,000,000, pack/item 1-99; goal 1-100 wins or 1-200 matches, division wins 1-100 a week and 1-500 a season; 1-20 rank rows within 1-1000, rising and apart)",
        nav_notice='Announcement', notice_title='Announcement image', notice_lead="This image shows in the Notice popup at every login (issue #36). Pick a JPG or PNG; this page fits it to the popup's size (800×720) before saving. Used from the next login, without changing client files.", notice_file='Image file (JPG/PNG)', notice_mode='Fit', notice_fit='Whole image (with background)', notice_fill='Fill the box (crop the edges)', notice_bg='Background colour', notice_save='Save announcement', notice_delete='Delete announcement', notice_current='Current announcement', notice_new='New preview', notice_none='No announcement image: the popup shows empty.', notice_saved='Announcement image saved; it shows at the next login.', notice_deleted='Announcement image deleted.', notice_confirm_delete='Delete the announcement image?', notice_pick_first='Pick an image file first.', e_notice_png='the image must be an 800×720 PNG (pick the file again on this page)', e_notice_size='the image is too big (8 MB at most)',
        nav_assets='Assets', as_title='Missing pictures', as_lead="The game shows player faces, club crests, kit pictures, league logos and staff pictures from the client's cache; a missing one shows as a default picture (a silhouette, a blank crest). Download fetches the missing ones from fifaaddict.com in the background, one picture every half second; files already there are not changed. Player faces can be thousands and take long; a download can be stopped and resumed at any time. New pictures show at the latest after the game is restarted. Player pictures fifaaddict.com doesn't have come from FC Online (FO4). Download all fetches every kind in one go and scans the whole card database; when a server doesn't answer, the download waits longer each time and goes on by itself.", as_all='All pictures', as_fetch_all='Download all missing', as_kind='Kind', as_added='Downloaded', as_failed='Not available', as_faces='Player faces', as_crests='Club crests', as_uniforms='Kit pictures', as_leagues='League logos', as_staffs='Staff pictures', as_careers='Club careers', as_careers_note="Club careers fetches the players' club careers up to the running season from Wikidata (years, clubs, loans) for the player info's Karakteristik tab. When it is done, press Database > Apply to the game. Players it does not find keep FO3's own history (up to 2017).", as_fetch='Download', as_stop='Stop', as_running='Downloading: {0}', as_idle='No download running.', as_started='Download started.', as_stopped='Download stopped.', e_asset_kind='unknown picture kind', e_asset_busy='another download is still running',
        nav_prematch="Match settings", pm_title="Match settings", pm_lead="Stadium, time of day, weather and length for the next matches (Liga, Cup, Rank). In Multiple > Rank they can also be changed on the game setting screen, and that choice is saved here too; the Liga and Cup screens only show them. Overcast: cloudy by day, dusk at night (the Liga/Cup screen labels it \"Salju\").", pm_stadium="Stadium", pm_tod="Time of day", pm_weather="Weather", pm_length="Length", pm_home="Home team's stadium", pm_random="Random", pm_day="Day", pm_night="Night", pm_dry="Clear", pm_rain="Rain", pm_snow="Snow", pm_overcast="Overcast", pm_minutes="{0} min", pm_ball="Ball", pm_saved="Match settings saved.", e_pm_value="invalid match setting",
        nav_economy="Economy", eco_title="Economy", eco_lead="Prices and EP for every manager. Easy = as always (free Shop). Realistic: a paid Shop (a product costs what it gives, one with no EP in it such as a boost or an EXP capsule a set price times the player price setting; a pack the worth of its cards, +13% with the dynamic Market, as a pack can be opened on a dear day) and the dynamic Market. Hard: player and pack prices x2, little starting EP. Dynamic Market: each player's price moves every day, virtual managers sometimes sell cheap, and you may ask up to 105% (it sells slower): buy low, sell high. Strict rules (Hard): Upgrade, Tukar and Scout make no EP: an upgrade costs what it adds, a restore costs EP, and no offer is worth more than the cards given. Without them (Easy, Realistic) the game's own rules: an upgrade costs 2% of the card's value, a restore takes upgrade points, offers have no cap. Match EP is set on the Match rewards tab; starting EP is for new managers.", eco_preset="Preset", eco_custom="Custom", eco_easy="Easy (as always)", eco_real="Realistic", eco_hard="Hard", eco_shopfree="Free Shop (0 EP)", eco_players="Player prices (x)", eco_packs="Pack prices (x)", eco_start="A new manager's EP", eco_dynamic="Dynamic Market (prices move, trading can pay)", eco_strict="Strict rules (Upgrade, Tukar and Scout make no EP)", eco_saved="Economy settings saved.", e_eco_value="invalid economy setting (multipliers 0.1-100, starting EP 0 or more)",
        nav_db='Database', db_title='Card and club database', db_lead="Edit a player's card per season: attributes, positions, foot, weak foot, skill moves, work rates, traits and club (a transfer). One card = one spid = season × 1,000,000 + player id (Messi is 158023: his season 91 card is 91158023); search by name, spid or player id. Changes go into gamedata/fifadb.sqlite and are recorded in {0} (original and new values: they can be restored, and the FO4 season build puts them back). The game reads its own copy of the DB: close the game, press Apply to the game, wait until it finishes, then run play.bat.", db_apply='Apply to the game', db_started='Installing into the game in the background…', db_done='Installed. Run play.bat to start the server and the game again.', db_failed='Failed (code {0}); see the log below.', as_install='Apply to the game', db_search='Find a card', db_q='Name, spid or player id', db_hint='Type at least 2 letters of a name, or a spid / player id.', db_edited='Edited cards', db_none='No card edited yet.', th_changed='Changed', f_club='Club', ph_club='type a club name', f_pos_n='Position {0}', f_foot='Preferred foot', foot_right='Right', foot_left='Left', f_weak='Weak foot', f_skill='Skill moves', f_wr_att='Attacking work rate', f_wr_def='Defensive work rate', wr_low='Low', wr_med='Medium', wr_high='High', f_stats='Attributes', f_traits='Traits', orig_value='original: {0}', card_saved='{0} saved, OVR now {1}. Press Apply to the game so the game uses it.', crest_title='Club crests', crest_lead="Pick a club and a picture (a transparent PNG looks best); this page fits it into 256×256 and 128×128 and saves both into the client's cache. The original crest is kept in gamedata/crest_backup and can be restored. Shows after the game restarts.", crest_save='Save crest', crest_revert='Restore the original crest', crest_confirm="Restore this club's original crest?", crest_now='Current crest', crest_new='New crest', crest_none='This club has no crest in the cache.', crest_pick='Pick a club and a picture first.', crest_saved='Crest saved; it shows after the game restarts.', crest_reverted='Original crest restored.', e_card_field='{0} cannot be changed here', e_club='club {0} is not in the game', e_not_edited='this card has not been edited', e_crest_png='the crest must be a {0}×{0} PNG (pick the file again on this page)',
        f_look='3D look', look_lead="EA codes from the game's 3D DB (fifa_ng_db). Face = the head of any 3D row: the list holds this player's cards; type another player's name to pick his head from the list, or type a spid or player id. Shows in the game after Apply to the game.", look_generic='The head is generic now: hair model and colour (0 = blonde), beard, skin and eyes can be changed.', look_scan='The head is a scan now: hair and beard come with it; for other hair pick the face of another head.', look_shared='A card of a season above 99 (26 LIVE, 25 LIVE) has no 3D row of its own: this changes every card of this player without one.', look_own='this card', f_face='Face', l_hairtypecode='Hair model', l_haircolorcode='Hair colour', l_facialhairtypecode='Beard', l_facialhaircolorcode='Beard colour', l_skintonecode='Skin tone', l_eyecolorcode='Eye colour', l_bodytypecode='Body type', e_no_engine="the game's 3D DB (data_0.big) cannot be read", sn_title='Own seasons', sn_lead='Make a card season of your own: a code, a name and a badge. Fill it by copying cards from the search results (adding to every stat at once if you like), then edit them like any card. Saved in {0}; Apply to the game installs the badge, and after play.bat the season has its own Top 200 pack and team color.', f_season_code='Season code', th_badge='Badge', th_cards='Cards', sn_badge='Badge (PNG/JPG)', sn_save='Save season', sn_saved='Season {0} saved.', sn_delete='Delete season', sn_confirm_delete='Delete season {0} and its {1} cards? Cards of it that clubs own leave the database too.', sn_deleted='Season {0} deleted.', sn_copy_to='Copy to season', f_boost_stats='Add to every stat', sn_copy='Copy', sn_copied='{0} copied to season {1} (spid {2}).', sn_none='No season of your own yet.', sn_remove='Delete card', sn_removed='Card {0} deleted.', confirm_card_remove='Delete card {0} (spid {1}) from its season?', sn_pick_badge='Pick a badge first.', mod_title='Mods: export and import', mod_lead='Save every own season, copied card, card edit and own team color in one JSON file, or load such a file from another server. After an import, press Apply to the game.', mod_export='Export mod', mod_import='Import mod (JSON)', mod_imported='Mod imported: {0} done, {1} failed', e_mod='not an FO3ID mod file (a JSON from Export mod)', e_season_icon='the badge must be a PNG of up to 1 MB (pick the file again on this page)', e_face='no 3D row {0}', e_season='season {0} is not one of your own', e_season_code='season code {0} is used by the game', e_card_exists='card {0} already exists',
        nav_tc='Team colors', tc_title='Own team colors', tc_lead='Make team colors of your own: the cards in one are picked by filters (season, club now or before, nation, player, minimum OVR, best N Market cards), and a card must meet every filter that is set. The tiers are those of the built-in special colors: 5-6 / 7-8 / 9-11 different players in the XI (OVR +1/+2/+3, or up to the OVR amount chosen); a stat boost comes on top in every tier. Fewer than 5 players: one tier for all of them. Saved in {0}; counts from the next login, no restart needed.', tc_members='Members', tc_boost='Boost', tc_new='New team color', tc_edit='Edit {0}', tc_nations='Nations', tc_players='Players', tc_min_ovr='Minimum OVR (0 = any)', tc_best='Best N cards (0 = all)', tc_saved='{0} saved: {1} cards of {2} players.', tc_deleted='{0} deleted.', tc_confirm_delete='Delete team color {0}?', tc_none='No team color of your own yet.', tc_all_cards='every card', tc_all_pos='every position', ph_pick='type, then pick', e_nation='unknown nation {0}', e_player_id='no player with player id {0}', e_color_missing='team color not found', e_tc_value='invalid team color value',
        koth_title='King of the Hill regions', koth_lead="Pick the area King of the Hill uses: cities of Indonesia, Singapore / Malaysia, Thailand, Vietnam (Vietnamese names with Vietnamese letters) or your own custom area. After a switch the virtual managers spread over the new area's cities and your region is empty again: pick a new one when King of the Hill opens. No restart needed.", koth_regions='Area', koth_id='Indonesia', koth_sgmy='Singapore / Malaysia', koth_th='Thailand', koth_vn='Vietnam', koth_custom='Custom', koth_saved='King of the Hill area saved.', e_koth_set='unknown area', koth_custom_title='Custom area', koth_custom_lead='Write your own area, one region per line: Region: city, city, city (e.g. Miền Bắc: Hà Nội, Hải Phòng, Sa Pa). 1-20 regions, 1-30 cities each, names up to 24 characters and none twice. Copy a built-in area and change it. A city that keeps its name keeps its id, so the players there stay. Save, then pick Custom above. Saved in {0}; counts at once.', koth_copy='Copy from', koth_copy_btn='Copy to the editor', koth_custom_save='Save custom area', koth_custom_saved='Custom area saved: {0} regions, {1} cities.', e_koth_line='line {0}: write Region: city, city', e_koth_name='a name is empty, over 24 characters or twice: {0}', e_koth_count='1-20 regions of 1-30 cities each', e_koth_empty='the custom area is empty: write and save it first',
        face_title='Player picture', face_lead="The card's picture in the game (128×128). The game looks for the card's picture (p<spid>.png, its season's) first, then the player's (p<player id>.png); without one it shows a default face. The Assets tab downloads missing ones from fifaaddict.com, season pictures too. Here you can replace it for every card of this player or this card only: with your own picture (PNG/JPG; this page fits it into 128×128) or with the picture of another spid / player id (from the cache, or straight from fifaaddict.com when it is not there). Saved in gamedata/faces; the client's cache files are not changed, and Restore deletes it. Shows after the game restarts.", face_for='For', face_player='Every card of this player (player id {0})', face_card='This card only (spid {0})', face_save='Save picture', face_revert='Restore the original picture', face_confirm='Delete this own picture and use the original again?', face_now='Picture in the game', face_new='New picture', face_none='No picture yet: the game shows a default face.', face_pick='Pick a picture file first.', face_saved='Picture saved; it shows after the game restarts.', face_reverted='Original picture restored.', e_face_png='the picture must be a {0}×{0} PNG (pick the file again on this page)', head_scan='scanned head', head_generic='generic head', face_from='Picture of spid / player id', face_use='Use this picture', face_used='Picture {0} used; it shows after the game restarts.', e_face_none='no picture {0} in the cache or on fifaaddict.com', e_face_fetch='fifaaddict.com did not answer ({0}); try again later',
        phd_title='3D face from a photo', phd_lead="For players with a generic face: a close-up photo from the front becomes the face skin on another player's scanned 3D head (the donor: its head shape and hair come along, its eyes stay). Pick a photo (face straight ahead, even light), click the 11 points in order, pick a donor that looks alike (skin tone, hair; no beard if the player has none), then Make. It applies to every card of this player that shows his player id row or a generic head. Saved in gamedata/photo_heads; shows after Database > Apply to the game and a game restart.", phd_donor='Donor head (engine row)', phd_donor_hint='type the name of a player with a scanned head', phd_step='Click point {0}/{1}: {2}', phd_all='All {0} points are marked; click near a point to move it.', phd_undo='Undo point', phd_reset='Clear all points', phd_photo='Photo and points', phd_now='3D face texture', phd_none='No 3D face from a photo yet.', phd_make='Make 3D face', phd_delete='Delete 3D face', phd_confirm="Delete this player's 3D face from a photo?", phd_made='3D face made. Press Database > Apply to the game (with the game closed), then open the game.', phd_deleted='3D face deleted; Apply to the game to take it out of the client.', phd_pick='Pick a photo first.', pt_eye_r="centre of the player's right eye (left in the photo)", pt_eye_l="centre of the player's left eye (right in the photo)", pt_nose='tip of the nose', pt_mouth_r='right corner of the mouth (left in the photo)', pt_mouth_l='left corner of the mouth (right in the photo)', pt_chin='bottom of the chin', pt_forehead='middle of the forehead', pt_temple_r="right temple: the face's edge at eyebrow height (left in the photo)", pt_temple_l="left temple: the face's edge at eyebrow height (right in the photo)", pt_jaw_r="right jaw: the face's edge just below the mouth (left in the photo)", pt_jaw_l="left jaw: the face's edge just below the mouth (right in the photo)", e_ph_png='the photo must be a PNG of at most {0} pixels (pick the file again on this page)', e_ph_points='mark all {0} points on the photo first', e_ph_donor='row {0} is not a scanned head: pick a player with a scanned head as the donor', e_ph_files="head {0}'s files are not in the client",
        phd_icon='Take from the picture', phd_icon_done="Photo, 11 points and donor filled in from the player's picture. Check the points (click near a point to move it), then Make 3D face.", phd_icon_nopts='no face found in the picture: click the points yourself', e_ph_icon='no picture of player {0} in the cache or on fifaaddict.com', phd_club="Make 3D faces from pictures for the club's generic players", phd_club_lead="Automatic 3D faces: every club player who still has a generic face and a picture in the cache (FO3's; the Aset tab downloads missing ones) gets a 3D face from his picture. The YuNet detector finds the face's points, the donor is picked by skin and hair colour. The first time takes about a minute (every scanned head is read once). Each player's result can be checked and changed in the card dialog; then Apply to the game.", phd_club_busy='Making 3D faces…', phd_club_done='{0} 3D faces made, {1} left out.', phd_skip_icon='no picture', phd_skip_face='no face found',
        world_title='Game world', world_lead="Modern: the AI clubs, the club picker and This Season packs use 26 LIVE cards (when installed). Classic FO3: every club plays FO3's own 2017 squad, the virtual managers use FO3's own seasons only, and the added seasons (25 LIVE, 26 LIVE, WC18, own seasons) are in no pack, Market or trade: only the admin page gives them (Add players, Bag). Your club and collection stay as they are. Takes effect after the server restarts (play.bat).", world_label='Mode', world_modern='Modern (26 LIVE)', world_classic='Classic FO3 (2017)', world_saved='Game world saved.', world_restart='The server still runs the previous mode: run play.bat to apply this choice.', e_world='unknown world mode',
        vm_title='Virtual managers', vm_lead='About 2,000 virtual managers (Rank and King of the Hill opponents, AI clubs, the Market) stay the same every time the game and the server start. Only the Generate again button here makes a new crowd: names, levels, clubs, squads, playing styles, Rank ratings, regions and AI clubs change at once, no restart (a game screen that is already open updates when it opens again). Their ids stay, so your friends, club and history keep working, but the people behind them change. Back to the first crowd brings the first one back.', vm_regen='Generate again', vm_reset='Back to the first crowd', vm_gen_now='Generation {0} ({1} managers now).', vm_confirm='Make a new crowd of virtual managers? All their names, clubs and squads change at once.', vm_regen_done='The new crowd is in use now.', vm_reset_done='The first crowd is in use again.',
        st_title='Staff', st_lead='Staff on the account: {0} of {1}. New staff show in Tim > Manajemen Staff once the screen changes.', st_staff='Staff', st_grade='Stars', st_life='Contract', st_status='Status', st_add='Add staff', st_added='{0} × {1} added.', st_removed='{0} removed.', st_remove='Remove', st_confirm='Remove staff {0} from the account?', st_none='No staff yet.', st_helper='helper', st_appointed='Appointed', st_studying='Studying', e_unknown_staff='unknown staff', e_staff_full='staff full: room for {0}', e_no_staff='no such staff',
        phd_redo='Make every 3D face again from the pictures', phd_redo_confirm="Make every existing 3D face again from its player's picture? The donor is picked again (skin compared with the donor's own picture). A face made from an uploaded photo becomes the picture's too.",
        t_PLAYER_GROWTH_RESET='Tactic point reset (Player Growth)',
    ),
    "zh": dict(
        lang_name="简体中文", locale="zh-CN",
        ep_balance="EP 余额", language="语言", menu="管理菜单",
        nav_ringkasan="概览", nav_pemain="俱乐部球员", nav_tambah="添加球员", nav_item="卡包与 Bag", nav_shop="商城",
        nav_harian="每日奖励", nav_scout="球探", nav_log="服务器日志",
        status_default="修改会立即保存到服务器；游戏重新打开或俱乐部界面重新加载后才会显示。",
        sum_title="俱乐部概览", sum_lead="服务器上当前的账号状态。", ep_title="EP 余额", amount="数量",
        ep_add="增加 EP", ep_sub="扣除 EP", ep_set="设为此余额", coach_line="{0}，球队 {1}",
        f_players="俱乐部球员", f_team2="在 Tim Cadangan（替补队）", f_packs="Item saya 中的卡包", f_bag="Bag 中未领取的礼物",
        f_streak="连续登录", days="{0} 天", f_today="今日奖励", given="已发放", waiting="等待登录",
        f_vip="VIP + 网吧特权有效期至", ep_empty="请先填写 EP 数量。", ep_now="当前 EP 余额：{0}。",
        pl_lead="修改球员卡的强化等级、移到 Tim Cadangan（替补队，Tukar Scout 从这里选球员），或放走 18 人名单以外的球员。卡包、交易和 Market 获得的新球员卡进入 Tim inti（主力队）。",
        pl_filter="在列表中查找", ph_player="球员名", pl_team2_clear="把 Tim Cadangan 的球员全部移回 Tim inti",
        th_role="角色", th_name="名称", th_season="赛季", th_value="EP 身价", th_team="队伍", th_pos="位置",
        reserve="替补队", to_first="移到 Tim inti", to_second="移到 Tim Cadangan", first_team="Tim inti", release="放走",
        no_player="没有匹配的球员。", confirm_release="确定要把 {0} 从俱乐部放走吗？此操作无法撤销。",
        grade_saved="强化等级已保存。", moved_second="已移到 Tim Cadangan。", moved_first="已回到 Tim inti。",
        released="{0} 已离开俱乐部。", team2_moved="{0} 名球员已回到 Tim inti。",
        add_lead="在球员卡数据库中搜索（评分与游戏和 fifaaddict 相同），然后直接加入俱乐部或通过 Bag 发送。",
        add_name="球员名", ph_messi="例如 Messi", search="搜索", add_hint="请输入球员名的至少 2 个字母。",
        on_sale="可交易", not_on_sale="不可交易", add_club="加入俱乐部", add_bag="通过 Bag 发送",
        no_card="没有这个名字的球员卡。", found="找到 {0} 张球员卡。", search_failed="搜索失败：",
        added="{0} +{1} 已加入俱乐部（Tim inti）。", gifted="{0} +{1} 已发送到 Bag。",
        gift_player="Pemain: {0}",  # a Bag title the game shows: kept in the client's language
        item_title="球员卡包与 Bag", item_lead="卡包和道具进入 Item saya。Bag 礼物进入 Item saya → Items，用 Terima 按钮领取。",
        item_give="发放卡包到 Item saya", pack="卡包", count="数量", item_give_btn="发放", bag_title="发送礼物到 Bag",
        title="标题", pack_count="卡包数量", bag_spid="球员 spid", ph_optional="可选", bag_send="发送到 Bag", no_pack="无卡包",
        given_packs="{0}× {1} 已放入 Item saya。", gift_sent="礼物“{0}”已发送到 Bag。",
        shop_lead="管理商城的所有标签页：用客户端自带的图标创建新的卡包、道具、礼包和月卡礼包，修改或隐藏自带商品，设置价格。保存在 {0}；游戏下次打开商城时生效。",
        shop_free="所有商品免费（0 EP）", shop_free_note="表格中的固定价格仍然有效。",
        kind_packs_tab="球员卡包", kind_items_tab="消耗品", kind_packages_tab="礼包", kind_monthly_tab="月卡礼包", kind_lists_tab="标签页列表",
        kind_packs="卡包", kind_items="道具", kind_packages="礼包", kind_monthly="月卡礼包",
        new_kind="新建{0}", edit_kind="编辑{0}", shop_filter="筛选", ph_shop_filter="名称或 itemid",
        th_icon="图标", th_source="来源", th_shop_price="商城价格", th_fixed_price="固定价格", th_shown="显示",
        src_builtin="自带", src_changed="已修改", src_new="新建", chained="连锁", edit="编辑", delete="删除", restore="恢复原版",
        ph_default="默认", price_saved="{0} 的价格已保存。", price_reset="{0} 已恢复默认价格。",
        hidden_msg="{0} 已在商城隐藏。", shown_msg="{0} 已在商城重新显示。", free_on="商城已免费。", free_off="商城已恢复收费。",
        saved_product="{0} 已保存（itemid {1}）。", deleted="{0} 已删除。", restored="{0} 已恢复原版。",
        confirm_delete="确定删除 {0} 吗？Item saya 中已有的该物品将无法使用。", confirm_restore="确定把 {0} 恢复为原版吗？",
        no_product="没有匹配的商品。",
        f_name="名称", f_icon="图标", icon_pick="选择图标", f_desc="说明", f_seasons="赛季", seasons_hint="不选 = 所有赛季",
        f_top="最佳 N 张卡", f_ovr="最低 OVR（+1 时）", f_grade="最高强化等级（+1 … +N）", f_pos="位置", any="全部",
        f_league="联赛 ID", f_all="所有 Market 球员卡（不只是 ID 授权的）", f_type="类型",
        t_EP_LOTTERY="EP 卡（随机）", t_ITEM_LOTTERY="随机箱", t_COACHNAME_CHANGE="经理改名", t_TEAM_CHANGE="更换球队",
        t_ENHANCEMENT_BOOST="强化加成", t_LEAGUE_SIMULATION="联赛模拟券",
        t_PLAYER_EXP_CAPSULE="经验胶囊（球员经验）", f_exp="经验值", f_maxlv="适用球员最高等级（留空 = 不限）", f_draft="选秀：挑选 N 名球员，卡片为最高等级（留空 = 普通卡包）",
        e_draft_season="选秀卡包必须只选一个赛季", f_ep_min="最少 EP", f_ep_max="最多 EP", f_prizes="奖品（最多 10 个）",
        f_weight="权重", f_prize="奖品", f_ep_amount="EP 数量", add_prize="添加奖品", f_boost="强化成功率加成（%）",
        f_contents="内容", add_row="添加一行", f_days="天数", f_now="购买时立即获得", f_daily="每天获得",
        remove="移除", save="保存", cancel="取消", none_type="无需填写其他内容。", icons_count="客户端中的 {0} 个图标", ph_icon="搜索图标名",
        lists_lead="Rekomendasi（推荐）、MP Shop、Bonus spesial（特别奖励）和 Paket berantai（连锁礼包）标签页中的商品顺序。",
        list_new="推荐：新品", list_best="推荐：热卖", list_mp="MP Shop", list_special="特别奖励",
        list_chained="连锁礼包", list_chained_hint="按顺序购买；每行是一组礼包。", list_chain_add="添加一组",
        list_add="添加", lists_save="保存列表", lists_saved="标签页列表已保存。", empty="空",
        daily_title="每日登录奖励", daily_lead="每天（WIB，UTC+7）第一次登录时，当天的奖励会放进 Bag。连续登录时天数递增；断一天则从第 1 天重新开始。最后一天之后重新循环。",
        daily_grant="立即发放下一天的奖励", daily_reset="重置连续登录", daily_table="奖励表", th_day="天", day_n="第 {0} 天",
        drop_day="删除这一天", daily_add="添加一天", daily_save="保存奖励表", f_total="累计登录天数", f_last="上次领奖",
        never="从未", f_today_wib="今天（WIB）", daily_saved="每日奖励表已保存。", sent_bag="“{0}”已发送到 Bag。",
        confirm_reset_streak="把连续登录重置为 0？", streak_reset="连续登录已重置。",
        nav_match="比赛奖励", mr_title="比赛奖励", mr_lead="每场比赛结束后和每次联赛模拟后发放：按结果给 EP 和经验，然后发三张奖励卡。玩家翻开的卡按权重从卡片列表中随机抽取。保存在 {0}；从下一场比赛开始生效。", mr_result="按结果", th_result="结果", mr_win="胜", mr_draw="平", mr_loss="负", mr_coach_exp="经理经验", mr_player_exp="每名球员经验", mr_cards="奖励卡", mr_chance="概率 {0}%", mr_saved="比赛奖励已保存。",
        scout_title="球探（Tukar Scout）", scout_lead="刚用过的球探会锁定，直到冷却结束。", th_available="再次可用",
        locked="已锁定", ready="可用", now="现在", no_quest="无任务", scout_reset="重置所有球探的冷却",
        scouts_ready="所有球探已可再次使用。", q_value="球员身价 ≥", q_grade="强化等级 ≥", q_count="数量", q_season="赛季",
        log_lead="captures/http_config.log 的最后几行。", log_reload="刷新日志", log_auto="每 3 秒自动刷新",
        failed="失败：", load_failed="加载失败：",
        grade="强化等级", th_market="转会市场", th_scout="球探", th_status="状态", th_quest="任务", f_item="道具", upgrade_point="强化点数",
        e_player_missing="找不到该球员", e_number="{0} 必须是数字", e_range="{0} 必须在 {1}..{2} 之间",
        e_no_spid="数据库中没有这个 spid", e_club_full="俱乐部已满（{0} 名球员）",
        e_in_squad="该球员在 18 人名单中；请先在 Formasi 中把他换下", e_squad_first="18 人名单中的球员只能留在 Tim inti",
        e_unknown_item="未知的卡包或道具", e_unknown_pack="未知的卡包", e_gift_empty="请填写 EP、卡包或球员",
        e_day="{0}：{1}", e_day_empty="是空的", e_table_days="奖励表必须有 1..31 天",
        e_pack_empty="卡包 {0} 一张球员卡都没有", e_length="{0} 必须是 1..{1} 个字符",
        e_no_icon="客户端中没有图标 {0}", e_pick_icon="请选择图标",
        e_row_item="{0}：未知物品 {1}", e_rows="{0} 必须有 {1}..{2} 行",
        e_unknown_pos="未知的位置", e_unknown_type="未知的道具类型",
        e_ep_range="最少 EP 必须 > 0 且 <= 最多 EP", e_unknown_prize="未知的奖品 {0}", e_prizes="奖品必须有 1..10 个",
        e_unknown_kind="未知的商品种类", e_product_missing="找不到该商品", e_not_changed="该商品没有被修改过",
        e_in_use="仍被 {0} 使用", e_list_item="{0}：物品 {1} 不能放在这里", e_chain_packages="连锁礼包只能包含礼包",
        nav_chat="AI 聊天", chat_title="虚拟经理聊天", chat_lead="排位对手（虚拟经理）会在比赛聊天中发言：开球时、双方进球时、射门偏出时、门将扑救时，以及你输入聊天时。对比赛结果的评论会在回到排位画面时以横幅显示。每位经理都有自己的性格（友好、好胜、搞笑、自信、调皮）。保存在 {0}；从下一条消息开始生效。", chat_on="启用 AI 聊天", chat_lang="消息语言", chat_saved="AI 聊天设置已保存。", e_chat_lang="未知的聊天语言", nav_missions="任务", mis_title="任务与成就", mis_lead="每日任务（Manager > 成就列表中的“每日任务”一行，每天 00:00 WIB 重置）、带徽章的永久成就（经理等级、比赛、胜场、强化、球员等级、球队价值、进球、排位）以及任务按钮里的每周任务（奖励 GP，用于其奖励箱；每周一 00:00 WIB 重置）。服务器会统计你踢的比赛和成功的强化。EP 奖励随经济页的球员价格倍率变化。保存在 {0}；语言和任务按钮在重新打开游戏后生效。", mis_on="启用任务", mis_lang="任务语言", mis_targets="目标 (x)", mis_rewards="奖励 (x)", mis_saved="任务设置已保存。", e_mission_value="任务设置无效（目标 0.1-10，奖励 0-10，语言 id/en/vi）", wq_title="每周任务", wq_lead="每周（周一 00:00 WIB 起）从这里抽取任务按钮里的任务。名称可留空（自动生成）；名称中的 {0} 表示目标。目标和 GP 也会乘以上面的倍率。从下一次请求开始生效；本周已完成的任务保持完成。", wq_th_stat="类型", wq_th_todo="目标", wq_th_name="名称（可选）", wq_add="添加任务", wq_drop="删除", wq_count="每周任务数", wq_shop_title="GP 奖励箱", wq_th_item="物品", wq_th_price="价格（GP）", wq_shop_add="添加奖励箱", wq_reset="恢复默认", wq_saved="每周任务已保存。", wq_reset_done="每周任务和奖励箱已恢复默认。", wq_confirm_reset="恢复默认的任务和奖励箱？", wq_this="本周（到 {0}）", wq_next="下周", e_quest_value="任务列表无效（目标和 GP 为 1-10,000，名称最多 60 个字符，每周 1-6 个任务且不超过列表数量）", e_quest_shop="奖励箱无效（1-5 个，物品不能重复，GP 1-100,000）", ptr_title="游戏时长奖励", ptr_lead="今天的累计游戏时长每达到一个目标就发放奖励（每天从 00:00 WIB 开始计算；超过 20 分钟没有操作的时间不计入）。奖励进入背包，并在游戏中以活动通知显示。今天：{0} 分钟，已领取 {1} 个奖励。保存在 {2}。", ptr_minutes="分钟", ptr_add="添加目标", ptr_saved="游戏时长奖励已保存。", e_ptr_rows="游戏时长表无效（1-10 行，分钟 1-1440 且递增，每行需有 EP 或卡包）", news_region="国家新闻（可选）", news_title="全局聊天话题", news_lead="Channel 页中的虚拟经理总会讨论全球足球新闻，以及勾选国家的新闻：今天与足球有关的 Google 热门搜索和该语言的 Google 新闻足球标题（X、Instagram 和 TikTok 需要登录，Reddit 拒绝服务器访问）。每位经理更喜欢讨论新闻中出现的自己的俱乐部、球星或国家（勾选中的一个）。每天自动更新一次。获取于：{0}。", news_none="暂无话题（服务器离线或刚启动）。", e_chat_region="未知的新闻地区", lr_title="排位奖励（排名）", lr_lead="排位赛季奖励（每月）：赛季结束时评分最高模式所在段位的主奖励，以及其他每个已玩模式的副奖励；新赛季开始时进入背包。每周奖励：本周（周一至周日）排位胜场每达到一个目标，奖励就通过排位界面的弹窗进入背包。排位界面也会显示这些奖励。保存在 {0}。", lr_div="段位", lr_main="主奖励", lr_sub="副奖励", lr_count="数量", lr_weekly="每周奖励", lr_wins="本周胜场", lr_item="奖励", lr_add="添加档位", lr_saved="排位奖励已保存。", lr_confirm_reset="恢复默认排位奖励？", lr_reset_done="排位奖励已恢复默认。", e_lr_rows="排位奖励无效（EP 1-10,000,000,000，卡包/物品 1-99；每周档位 1-500 胜，递增，最多 20 个）", kr_title="King of the Hill 奖励", kr_lead="每周奖励（每周 = 1-7、8-14、15-21、22-28 日）和赛季奖励（每月）。你参加的一周或一个赛季结束后，打开 King of the Hill 时会出现奖励界面，达成的奖励进入背包。每周：当周的胜场或场次目标，以及胜场达到一定数量时的段位奖励。赛季：赛季结束时的段位奖励（胜场达到一定数量），以及地区排名奖励。King of the Hill 界面也会显示这些奖励。保存在 {0}；立即生效。", kr_type="每周目标", kr_type_winning="胜场", kr_type_match="场次", kr_win="当周胜场", kr_match="当周场次", kr_week_item="每周目标奖励", kr_week_grade="每周段位奖励所需胜场", kr_season_grade="赛季段位奖励所需胜场", kr_week_div="每周段位奖励", kr_season_div="赛季段位奖励", kr_ranking="赛季排名奖励（地区）", kr_from="起始名次", kr_to="结束名次", kr_add="添加名次", kr_saved="King of the Hill 奖励已保存。", kr_confirm_reset="恢复默认的 King of the Hill 奖励？", kr_reset_done="King of the Hill 奖励已恢复默认。", e_kr_rows="King of the Hill 奖励无效（EP 1-10,000,000,000，卡包/物品 1-99；目标 1-100 胜或 1-200 场，段位胜场每周 1-100、每赛季 1-500；1-20 行名次，范围 1-1000，递增且不重叠）",
        nav_notice='公告', notice_title='公告图片', notice_lead='这张图片会在每次登录时的“公告”弹窗中显示（issue #36）。选择 JPG 或 PNG；本页面会在保存前把它调整为弹窗的尺寸（800×720）。下次登录时生效，无需修改客户端文件。', notice_file='图片文件（JPG/PNG）', notice_mode='适配方式', notice_fit='完整显示（加背景）', notice_fill='填满方框（裁去边缘）', notice_bg='背景颜色', notice_save='保存公告', notice_delete='删除公告', notice_current='当前公告', notice_new='新预览', notice_none='还没有公告图片：弹窗显示为空。', notice_saved='公告图片已保存；下次登录时显示。', notice_deleted='公告图片已删除。', notice_confirm_delete='删除公告图片？', notice_pick_first='请先选择图片文件。', e_notice_png='图片必须是 800×720 的 PNG（请在本页面重新选择文件）', e_notice_size='图片太大（最多 8 MB）',
        nav_assets='图片', as_title='缺失的图片', as_lead='游戏从客户端缓存中显示球员头像、俱乐部队徽、球衣图片、联赛标志和职员照片；缺失的会显示为默认图（剪影、空白队徽）。“下载”会在后台从 fifaaddict.com 获取缺失的图片，每半秒一张；已有的文件不会被改动。球员头像可能有几千张，需要较长时间；下载可以随时停止、随时继续。新图片最迟在重新打开游戏后显示。fifaaddict.com 没有的球员头像会从 FC Online（FO4）获取。“全部下载”一次获取所有类型并扫描整个球员卡数据库；服务器没有响应时，下载会等待更久再自动继续。', as_all='全部图片', as_fetch_all='下载全部缺失的图片', as_kind='类型', as_added='已下载', as_failed='无法获取', as_faces='球员头像', as_crests='俱乐部队徽', as_uniforms='球衣图片', as_leagues='联赛标志', as_staffs='职员照片', as_careers='俱乐部经历', as_careers_note='“俱乐部经历”从 Wikidata 获取球员截至当前赛季的俱乐部经历（年份、俱乐部、租借），显示在球员信息的特性页。完成后请点击“数据库 > 应用到游戏”。未找到的球员保留 FO3 自带的经历（至 2017 年）。', as_fetch='下载', as_stop='停止', as_running='正在下载：{0}', as_idle='没有正在进行的下载。', as_started='已开始下载。', as_stopped='已停止下载。', e_asset_kind='未知的图片类型', e_asset_busy='另一个下载仍在进行',
        nav_prematch="比赛设置", pm_title="比赛设置", pm_lead="下一场比赛（联赛、杯赛、排位）的球场、时间、天气和时长。在 Multiple > Rank 的比赛设置画面也可以修改，修改结果也会保存在这里；联赛和杯赛画面只显示这些设置。阴天：白天为多云，夜晚为黄昏（联赛/杯赛画面会显示为“Salju”）。", pm_stadium="球场", pm_tod="时间", pm_weather="天气", pm_length="时长", pm_home="主队球场", pm_random="随机", pm_day="白天", pm_night="夜晚", pm_dry="晴", pm_rain="雨", pm_snow="雪", pm_overcast="阴天", pm_minutes="{0} 分钟", pm_ball="足球", pm_saved="比赛设置已保存。", e_pm_value="无效的比赛设置",
        nav_economy="经济", eco_title="经济", eco_lead="所有经理的价格与 EP。轻松 = 保持原样（商店免费）。真实：商店收费（商品价格 = 其内容的价值，不含 EP 的商品（如强化加成、经验胶囊）按基础价格乘以球员价格倍率；卡包 = 卡包内球员的价值，开启动态市场时 +13%，因为卡包可以留到价格高的日子再开）并开启动态市场。困难：球员和卡包价格 x2，初始 EP 很少。动态市场：每位球员的价格每天涨跌，虚拟经理有时会低价出售，你可以把售价定到 105%（卖出更慢）：低买高卖。严格规则（困难）：强化、交易和球探都无法刷 EP：强化费用等于它增加的价值，恢复需支付 EP，报价不会超过交出球员的价值。不开启时（轻松、真实）按游戏原本的规则：强化费用为球员卡价值的 2%，恢复使用强化点数，报价不设上限。比赛 EP 在“比赛奖励”页设置；初始 EP 只对新经理有效。", eco_preset="预设", eco_custom="自定义", eco_easy="轻松（保持原样）", eco_real="真实", eco_hard="困难", eco_shopfree="商店免费（0 EP）", eco_players="球员价格（倍）", eco_packs="卡包价格（倍）", eco_start="新经理的初始 EP", eco_dynamic="动态市场（价格涨跌，买卖可以获利）", eco_strict="严格规则（强化、交易和球探无法刷 EP）", eco_saved="经济设置已保存。", e_eco_value="无效的经济设置（倍率 0.1-100，初始 EP 不小于 0）",
        nav_db='数据库', db_title='球员卡与俱乐部数据库', db_lead='按赛季编辑球员卡：属性、位置、惯用脚、逆足、花式技巧、攻防积极性、特性和俱乐部（转会）。一张卡 = 一个 spid = 赛季 × 1,000,000 + 球员 ID（梅西为 158023，他的 91 赛季卡是 91158023）；可按名字、spid 或球员 ID 搜索。修改写入 gamedata/fifadb.sqlite 并记录在 {0}（原值和新值：可以恢复，FO4 赛季构建时会重新应用）。游戏读取自己的数据库副本：先关闭游戏，点击“应用到游戏”，等待完成，然后运行 play.bat。', db_apply='应用到游戏', db_started='正在后台安装到游戏…', db_done='安装完成。运行 play.bat 重新启动服务器和游戏。', db_failed='失败（代码 {0}）；请查看下方日志。', as_install='应用到游戏', db_search='查找球员卡', db_q='名字、spid 或球员 ID', db_hint='输入名字的至少 2 个字母，或 spid / 球员 ID。', db_edited='已修改的球员卡', db_none='还没有修改过的球员卡。', th_changed='修改项', f_club='俱乐部', ph_club='输入俱乐部名称', f_pos_n='位置 {0}', f_foot='惯用脚', foot_right='右脚', foot_left='左脚', f_weak='逆足', f_skill='花式技巧', f_wr_att='进攻积极性', f_wr_def='防守积极性', wr_low='低', wr_med='中', wr_high='高', f_stats='属性', f_traits='特性', orig_value='原值：{0}', card_saved='{0} 已保存，现在 OVR {1}。点击“应用到游戏”让游戏使用它。', crest_title='俱乐部队徽', crest_lead='选择俱乐部和图片（透明 PNG 效果最好）；本页会将其缩放到 256×256 和 128×128 并保存到客户端缓存。原队徽保存在 gamedata/crest_backup，可以恢复。重新打开游戏后显示。', crest_save='保存队徽', crest_revert='恢复原队徽', crest_confirm='恢复这个俱乐部的原队徽？', crest_now='当前队徽', crest_new='新队徽', crest_none='缓存中没有这个俱乐部的队徽。', crest_pick='请先选择俱乐部和图片。', crest_saved='队徽已保存；重新打开游戏后显示。', crest_reverted='已恢复原队徽。', e_card_field='{0} 不能在这里修改', e_club='游戏中没有俱乐部 {0}', e_not_edited='这张卡没有修改过', e_crest_png='队徽必须是 {0}×{0} 的 PNG（请在本页重新选择文件）',
        f_look='3D 外观', look_lead='游戏 3D 数据库（fifa_ng_db）中的 EA 代码。脸型 = 任意 3D 行的头部：列表中是该球员的卡；输入其他球员的名字可从列表中选择他的头部，也可输入 spid 或球员 ID。点击“应用到游戏”后在游戏中显示。', look_generic='当前为通用头部：可以修改发型和发色（0 = 金发）、胡须、肤色和眼睛。', look_scan='当前为扫描头部：头发和胡须随头部而定；想换发型请选择其他头部的脸型。', look_shared='赛季代码大于 99 的卡（26 LIVE、25 LIVE）没有自己的 3D 行：此修改会作用于该球员所有没有自己 3D 行的卡。', look_own='本卡', f_face='脸型', l_hairtypecode='发型', l_haircolorcode='发色', l_facialhairtypecode='胡须', l_facialhaircolorcode='胡须颜色', l_skintonecode='肤色', l_eyecolorcode='眼睛颜色', l_bodytypecode='体型', e_no_engine='无法读取游戏的 3D 数据库（data_0.big）', sn_title='自建赛季', sn_lead='创建自己的球员卡赛季：代码、名称和徽章。从搜索结果复制球员卡来填充（可一次性提高所有属性），然后像其他卡一样编辑。保存在 {0}；“应用到游戏”会安装徽章，运行 play.bat 后该赛季会有自己的 Top 200 卡包和团队色。', f_season_code='赛季代码', th_badge='徽章', th_cards='球员卡', sn_badge='徽章（PNG/JPG）', sn_save='保存赛季', sn_saved='赛季 {0} 已保存。', sn_delete='删除赛季', sn_confirm_delete='删除赛季 {0} 及其 {1} 张卡？俱乐部拥有的该赛季球员卡也会从数据库中消失。', sn_deleted='赛季 {0} 已删除。', sn_copy_to='复制到赛季', f_boost_stats='所有属性增加', sn_copy='复制', sn_copied='{0} 已复制到赛季 {1}（spid {2}）。', sn_none='还没有自建赛季。', sn_remove='删除球员卡', sn_removed='球员卡 {0} 已删除。', confirm_card_remove='从赛季中删除球员卡 {0}（spid {1}）？', sn_pick_badge='请先选择徽章。', mod_title='模组：导出和导入', mod_lead='将所有自建赛季、复制的球员卡、球员卡修改和自建团队色保存到一个 JSON 文件，或载入其他服务器的此类文件。导入后点击“应用到游戏”。', mod_export='导出模组', mod_import='导入模组（JSON）', mod_imported='模组已导入：成功 {0}，失败 {1}', e_mod='不是 FO3ID 模组文件（应为“导出模组”生成的 JSON）', e_season_icon='徽章必须是不超过 1 MB 的 PNG（请在本页重新选择文件）', e_face='没有 3D 行 {0}', e_season='赛季 {0} 不是自建赛季', e_season_code='赛季代码 {0} 已被游戏使用', e_card_exists='球员卡 {0} 已存在',
        nav_tc='团队色', tc_title='自建团队色', tc_lead='创建自己的团队色：用筛选条件选择成员卡（赛季、现在或曾经的俱乐部、国家、球员、最低 OVR、市场最佳 N 张卡），卡必须满足所有已填写的条件。等级与内置特殊团队色相同：首发中 5-6 / 7-8 / 9-11 名不同球员（OVR +1/+2/+3，或最高到所选 OVR 数值）；属性加成在每个等级额外叠加。不足 5 名球员时：所有人一个等级。保存在 {0}；下次登录生效，无需重启。', tc_members='成员', tc_boost='加成', tc_new='新团队色', tc_edit='编辑 {0}', tc_nations='国家', tc_players='球员', tc_min_ovr='最低 OVR（0 = 不限）', tc_best='最佳 N 张卡（0 = 全部）', tc_saved='{0} 已保存：{2} 名球员的 {1} 张卡。', tc_deleted='{0} 已删除。', tc_confirm_delete='删除团队色 {0}？', tc_none='还没有自建团队色。', tc_all_cards='所有卡', tc_all_pos='所有位置', ph_pick='输入后选择', e_nation='未知国家 {0}', e_player_id='没有球员 ID 为 {0} 的球员', e_color_missing='找不到团队色', e_tc_value='团队色数值无效',
        koth_title='King of the Hill 地区', koth_lead='选择 King of the Hill 使用的地区：印度尼西亚、新加坡/马来西亚、泰国、越南的城市（越南名称使用越南文字母）或你自建的地区。切换后，虚拟经理会分布到新地区的城市，你选的地区会清空：打开 King of the Hill 时重新选择。无需重启。', koth_regions='地区', koth_id='印度尼西亚', koth_sgmy='新加坡 / 马来西亚', koth_th='泰国', koth_vn='越南', koth_custom='自定义', koth_saved='King of the Hill 地区已保存。', e_koth_set='未知地区', koth_custom_title='自定义地区', koth_custom_lead='每行写一个区域：区域: 城市, 城市, 城市（例如 Miền Bắc: Hà Nội, Hải Phòng, Sa Pa）。1-20 个区域，每个 1-30 个城市，名称最多 24 个字符且不能重复。可以先复制内置地区再修改。名字不变的城市保留其 ID，那里的玩家仍在原处。保存后在上方选择“自定义”。保存在 {0}；立即生效。', koth_copy='复制自', koth_copy_btn='复制到编辑器', koth_custom_save='保存自定义地区', koth_custom_saved='自定义地区已保存：{0} 个区域，{1} 个城市。', e_koth_line='第 {0} 行：请写成 区域: 城市, 城市', e_koth_name='名称为空、超过 24 个字符或重复：{0}', e_koth_count='需要 1-20 个区域，每个 1-30 个城市', e_koth_empty='自定义地区为空：请先填写并保存',
        face_title='球员头像', face_lead='游戏中球员卡的头像（128×128）。游戏先找该卡的头像（p<spid>.png，即该赛季的），再找球员的头像（p<球员 ID>.png）；都没有时显示默认头像。“图片”页会从 fifaaddict.com 下载缺失的头像，包括各赛季的头像。在这里可以替换该球员所有卡或仅本卡的头像：用自己的图片（PNG/JPG，本页会缩放到 128×128），或用其他 spid / 球员 ID 的头像（来自缓存，缓存中没有时直接从 fifaaddict.com 获取）。保存在 gamedata/faces，不改动客户端缓存文件；“恢复”会删除它。重新打开游戏后显示。', face_for='用于', face_player='该球员的所有卡（球员 ID {0}）', face_card='仅本卡（spid {0}）', face_save='保存头像', face_revert='恢复原头像', face_confirm='删除这张自定义头像并恢复原头像？', face_now='游戏中的头像', face_new='新头像', face_none='还没有头像：游戏显示默认头像。', face_pick='请先选择图片文件。', face_saved='头像已保存；重新打开游戏后显示。', face_reverted='已恢复原头像。', e_face_png='头像必须是 {0}×{0} 的 PNG（请在本页重新选择文件）', head_scan='扫描头部', head_generic='通用头部', face_from='使用 spid / 球员 ID 的头像', face_use='使用此头像', face_used='已使用头像 {0}；重新打开游戏后显示。', e_face_none='缓存和 fifaaddict.com 都没有头像 {0}', e_face_fetch='fifaaddict.com 没有响应（{0}）；请稍后再试',
        phd_title='用照片制作 3D 脸', phd_lead='适用于通用脸的球员：把正面特写照片作为脸部皮肤贴到另一名已扫描球员的 3D 头上（供体：头型和头发随之而来，眼睛保留供体的）。选择照片（脸朝正前方、光线均匀），按顺序点击 11 个点，选择相像的供体（肤色、头发；球员没有胡子时选无胡子的），然后点“制作”。适用于该球员所有使用其球员 ID 行或通用头的卡。保存在 gamedata/photo_heads；点击“数据库 > 应用到游戏”并重启游戏后显示。', phd_donor='供体头（引擎行）', phd_donor_hint='输入有扫描头的球员姓名', phd_step='点击第 {0}/{1} 个点：{2}', phd_all='{0} 个点都已标记；点击某个点附近即可移动它。', phd_undo='撤销点', phd_reset='清除所有点', phd_photo='照片和点', phd_now='3D 脸部贴图', phd_none='还没有用照片制作的 3D 脸。', phd_make='制作 3D 脸', phd_delete='删除 3D 脸', phd_confirm='删除该球员用照片制作的 3D 脸？', phd_made='3D 脸已制作。请点击“数据库 > 应用到游戏”（游戏关闭时），然后打开游戏。', phd_deleted='3D 脸已删除；应用到游戏后从客户端移除。', phd_pick='请先选择照片。', pt_eye_r='球员右眼中心（照片左侧）', pt_eye_l='球员左眼中心（照片右侧）', pt_nose='鼻尖', pt_mouth_r='右嘴角（照片左侧）', pt_mouth_l='左嘴角（照片右侧）', pt_chin='下巴底部', pt_forehead='额头中间', pt_temple_r='右太阳穴：眉毛高度的脸部边缘（照片左侧）', pt_temple_l='左太阳穴：眉毛高度的脸部边缘（照片右侧）', pt_jaw_r='右下颌：嘴下方一点的脸部边缘（照片左侧）', pt_jaw_l='左下颌：嘴下方一点的脸部边缘（照片右侧）', e_ph_png='照片必须是最大 {0} 像素的 PNG（请在本页重新选择文件）', e_ph_points='请先在照片上标记全部 {0} 个点', e_ph_donor='第 {0} 行不是扫描头：请选择有扫描头的球员作为供体', e_ph_files='客户端中没有头 {0} 的文件',
        phd_icon='从头像获取', phd_icon_done='已从球员头像填入照片、11 个点和供体。请检查这些点（点击某个点附近即可移动它），然后点“制作 3D 脸”。', phd_icon_nopts='头像中未找到脸：请自己点击这些点', e_ph_icon='缓存和 fifaaddict.com 都没有球员 {0} 的头像', phd_club='为俱乐部中通用脸的球员用头像制作 3D 脸', phd_club_lead='自动 3D 脸：俱乐部中仍是通用脸、且缓存中有头像（FO3 的；“图片”页可下载缺失的）的每名球员，都会用其头像得到 3D 脸。YuNet 检测器找出脸部的点，按肤色和发色选择供体。第一次约需一分钟（所有扫描头读取一次）。每名球员的结果可在球员卡窗口中检查和更改；然后应用到游戏。', phd_club_busy='正在制作 3D 脸…', phd_club_done='已制作 {0} 张 3D 脸，跳过 {1} 名。', phd_skip_icon='没有头像', phd_skip_face='未找到脸',
        world_title='游戏世界', world_lead='现代：AI 俱乐部、俱乐部选择和 This Season 卡包使用 26 LIVE 卡（已安装时）。经典 FO3：所有俱乐部使用 FO3 原版 2017 阵容，虚拟经理只使用 FO3 原有赛季，新增赛季（25 LIVE、26 LIVE、WC18、自建赛季）不会出现在卡包、市场或交易中：只能通过管理后台发放（添加球员、Bag）。你的俱乐部和收藏不变。服务器重启后生效（play.bat）。', world_label='模式', world_modern='现代（26 LIVE）', world_classic='经典 FO3（2017）', world_saved='游戏世界已保存。', world_restart='服务器仍在运行之前的模式：运行 play.bat 使此选择生效。', e_world='未知的世界模式',
        vm_title='虚拟经理', vm_lead='约 2,000 名虚拟经理（Rank 和 King of the Hill 的对手、AI 俱乐部、市场）每次启动游戏和服务器都保持不变。只有这里的“重新生成”按钮会创建一批新人：名字、等级、俱乐部、阵容、打法、Rank 积分、地区和 AI 俱乐部立即改变，无需重启（已打开的游戏界面重新打开后更新）。他们的 ID 不变，所以你的好友、俱乐部和记录不会出错，但背后的人换了。“恢复初始”会找回第一批。', vm_regen='重新生成', vm_reset='恢复初始', vm_gen_now='第 {0} 代（现有 {1} 名经理）。', vm_confirm='生成一批新的虚拟经理？他们的名字、俱乐部和阵容会立即全部改变。', vm_regen_done='新的一批已启用。', vm_reset_done='已重新启用第一批。',
        st_title='职员', st_lead='账号上的职员：{0} / {1}。新职员在画面切换后显示于 Tim > Manajemen Staff。', st_staff='职员', st_grade='星级', st_life='合同', st_status='状态', st_add='添加职员', st_added='已添加 {0} × {1}。', st_removed='已删除 {0}。', st_remove='删除', st_confirm='从账号删除职员 {0}？', st_none='还没有职员。', st_helper='助手', st_appointed='在岗', st_studying='培训中', e_unknown_staff='未知的职员', e_staff_full='职员已满：还能放 {0} 个', e_no_staff='没有这个职员',
        phd_redo='用头像重新生成全部 3D 脸', phd_redo_confirm='用球员头像重新生成所有已有的 3D 脸？会重新挑选模板头（肤色与模板球员的照片比较）。用上传照片做的脸也会换成头像生成的。',
        t_PLAYER_GROWTH_RESET='战术点重置（球员成长）',
    ),
    "th": dict(
        lang_name="ไทย", locale="th-TH",
        ep_balance="ยอด EP", language="ภาษา", menu="เมนูผู้ดูแล",
        nav_ringkasan="ภาพรวม", nav_pemain="นักเตะในสโมสร", nav_tambah="เพิ่มนักเตะ", nav_item="แพ็คและ Bag", nav_shop="ร้านค้า",
        nav_harian="รางวัลรายวัน", nav_scout="แมวมอง", nav_log="บันทึกเซิร์ฟเวอร์",
        status_default="การเปลี่ยนแปลงจะบันทึกบนเซิร์ฟเวอร์ทันที เกมจะแสดงผลหลังเปิดใหม่หรือเมื่อโหลดหน้าสโมสรใหม่",
        sum_title="ภาพรวมสโมสร", sum_lead="สถานะบัญชีบนเซิร์ฟเวอร์ตอนนี้", ep_title="ยอด EP", amount="จำนวน",
        ep_add="เพิ่ม EP", ep_sub="ลด EP", ep_set="ตั้งยอดเป็นค่านี้", coach_line="{0}, ทีม {1}",
        f_players="นักเตะในสโมสร", f_team2="อยู่ใน Tim Cadangan (ทีมสำรอง)", f_packs="แพ็คใน Item saya", f_bag="ของขวัญใน Bag ที่ยังไม่รับ",
        f_streak="ล็อกอินต่อเนื่อง", days="{0} วัน", f_today="รางวัลวันนี้", given="ให้แล้ว", waiting="รอการล็อกอิน",
        f_vip="สิทธิ์ VIP + ร้านเกม ใช้ได้ถึง", ep_empty="กรอกจำนวน EP ก่อน", ep_now="ยอด EP ตอนนี้ {0}",
        pl_lead="เปลี่ยนเกรดการ์ด ย้ายไป Tim Cadangan (ทีมสำรอง ที่ Tukar Scout ใช้เลือกนักเตะ) หรือปล่อยนักเตะที่อยู่นอก 18 คนของสควอด การ์ดใหม่จากแพ็ค การเทรด และ Market จะเข้า Tim inti (ทีมหลัก)",
        pl_filter="ค้นหาในรายการ", ph_player="ชื่อนักเตะ", pl_team2_clear="ย้ายการ์ดทั้งหมดใน Tim Cadangan ไป Tim inti",
        th_role="บทบาท", th_name="ชื่อ", th_season="ซีซั่น", th_value="มูลค่า EP", th_team="ทีม", th_pos="ตำแหน่ง",
        reserve="ทีมสำรอง", to_first="ไป Tim inti", to_second="ไป Tim Cadangan", first_team="Tim inti", release="ปล่อยตัว",
        no_player="ไม่มีนักเตะที่ตรงกัน", confirm_release="ปล่อย {0} ออกจากสโมสร? ยกเลิกไม่ได้",
        grade_saved="บันทึกเกรดแล้ว", moved_second="ย้ายไป Tim Cadangan แล้ว", moved_first="กลับมา Tim inti แล้ว",
        released="ปล่อย {0} ออกจากสโมสรแล้ว", team2_moved="ย้ายการ์ด {0} ใบกลับ Tim inti แล้ว",
        add_lead="ค้นหาการ์ดในฐานข้อมูล (เรตติ้งเหมือนในเกมและ fifaaddict) แล้วใส่เข้าสโมสรทันทีหรือส่งผ่าน Bag",
        add_name="ชื่อนักเตะ", ph_messi="เช่น Messi", search="ค้นหา", add_hint="พิมพ์ชื่อนักเตะอย่างน้อย 2 ตัวอักษร",
        on_sale="มีขาย", not_on_sale="ไม่มีขาย", add_club="ใส่เข้าสโมสร", add_bag="ส่งผ่าน Bag",
        no_card="ไม่มีการ์ดชื่อนี้", found="พบการ์ด {0} ใบ", search_failed="ค้นหาไม่สำเร็จ: ",
        added="{0} +{1} เข้าสโมสรแล้ว (Tim inti)", gifted="ส่ง {0} +{1} ไปที่ Bag แล้ว",
        gift_player="Pemain: {0}",  # a Bag title the game shows: kept in the client's language
        item_title="แพ็คนักเตะและ Bag", item_lead="แพ็คและไอเทมจะเข้า Item saya ของขวัญ Bag จะเข้า Item saya → Items และรับได้ด้วยปุ่ม Terima",
        item_give="ให้แพ็คเข้า Item saya", pack="แพ็ค", count="จำนวน", item_give_btn="ให้", bag_title="ส่งของขวัญไปที่ Bag",
        title="หัวข้อ", pack_count="จำนวนแพ็ค", bag_spid="spid นักเตะ", ph_optional="ไม่บังคับ", bag_send="ส่งไปที่ Bag", no_pack="ไม่มีแพ็ค",
        given_packs="{0}× {1} เข้า Item saya แล้ว", gift_sent="ส่งของขวัญ \"{0}\" ไปที่ Bag แล้ว",
        shop_lead="จัดการทุกแท็บของร้านค้า: สร้างแพ็ค ไอเทม แพ็กเกจ และแพ็กเกจรายเดือนใหม่ด้วยไอคอนของไคลเอนต์ แก้ไขหรือซ่อนสินค้าเดิม และตั้งราคา บันทึกไว้ที่ {0} เกมจะใช้เมื่อเปิดร้านค้าครั้งถัดไป",
        shop_free="สินค้าทั้งหมดฟรี (0 EP)", shop_free_note="ราคาคงที่ในตารางยังมีผล",
        kind_packs_tab="แพ็คนักเตะ", kind_items_tab="ไอเทมใช้แล้วหมด", kind_packages_tab="แพ็กเกจ", kind_monthly_tab="แพ็กเกจรายเดือน", kind_lists_tab="รายการในแท็บ",
        kind_packs="แพ็ค", kind_items="ไอเทม", kind_packages="แพ็กเกจ", kind_monthly="แพ็กเกจรายเดือน",
        new_kind="สร้าง{0}ใหม่", edit_kind="แก้ไข{0}", shop_filter="ค้นหา", ph_shop_filter="ชื่อหรือ itemid",
        th_icon="ไอคอน", th_source="ที่มา", th_shop_price="ราคาในร้าน", th_fixed_price="ราคาคงที่", th_shown="แสดง",
        src_builtin="ของเดิม", src_changed="แก้ไขแล้ว", src_new="ใหม่", chained="ต่อเนื่อง", edit="แก้ไข", delete="ลบ", restore="คืนค่าเดิม",
        ph_default="ค่าเริ่มต้น", price_saved="บันทึกราคา {0} แล้ว", price_reset="{0} กลับไปใช้ราคาเริ่มต้น",
        hidden_msg="ซ่อน {0} จากร้านค้าแล้ว", shown_msg="{0} แสดงในร้านค้าอีกครั้ง", free_on="ร้านค้าฟรีแล้ว", free_off="ร้านค้ากลับมาคิดราคาแล้ว",
        saved_product="บันทึก {0} แล้ว (itemid {1})", deleted="ลบ {0} แล้ว", restored="{0} กลับเป็นของเดิมแล้ว",
        confirm_delete="ลบ {0}? ชิ้นที่อยู่ใน Item saya แล้วจะใช้ไม่ได้อีก", confirm_restore="คืนค่า {0} เป็นของเดิม?",
        no_product="ไม่มีสินค้าที่ตรงกัน",
        f_name="ชื่อ", f_icon="ไอคอน", icon_pick="เลือกไอคอน", f_desc="คำอธิบาย", f_seasons="ซีซั่น", seasons_hint="ไม่เลือก = ทุกซีซั่น",
        f_top="การ์ดดีที่สุด N ใบ", f_ovr="OVR ขั้นต่ำ (การ์ด +1)", f_grade="เกรดสูงสุด (+1 … +N)", f_pos="ตำแหน่ง", any="ทั้งหมด",
        f_league="ID ลีก", f_all="การ์ดทั้งหมดใน Market (ไม่ใช่เฉพาะที่มีลิขสิทธิ์ ID)", f_type="ประเภท",
        t_EP_LOTTERY="การ์ด EP (สุ่ม)", t_ITEM_LOTTERY="กล่องสุ่ม", t_COACHNAME_CHANGE="เปลี่ยนชื่อผู้จัดการ", t_TEAM_CHANGE="เปลี่ยนทีม",
        t_ENHANCEMENT_BOOST="บูสต์อัปเกรด", t_LEAGUE_SIMULATION="คูปองจำลองลีก",
        t_PLAYER_EXP_CAPSULE="แคปซูล EXP (EXP นักเตะ)", f_exp="EXP", f_maxlv="ใช้กับนักเตะเลเวลไม่เกิน (ว่าง = ทั้งหมด)", f_draft="ดราฟต์: เลือกนักเตะ N คน การ์ดได้เกรดสูงสุด (ว่าง = แพ็คปกติ)",
        e_draft_season="แพ็คดราฟต์ต้องมีซีซั่นเดียวเท่านั้น", f_ep_min="EP ขั้นต่ำ", f_ep_max="EP สูงสุด", f_prizes="รางวัล (สูงสุด 10)",
        f_weight="น้ำหนัก", f_prize="รางวัล", f_ep_amount="จำนวน EP", add_prize="เพิ่มรางวัล", f_boost="เพิ่มโอกาสอัปเกรด (%)",
        f_contents="สิ่งที่ได้", add_row="เพิ่มแถว", f_days="ระยะเวลา (วัน)", f_now="ได้ทันทีเมื่อซื้อ", f_daily="ได้ทุกวัน",
        remove="ลบ", save="บันทึก", cancel="ยกเลิก", none_type="ไม่ต้องกรอกอย่างอื่น", icons_count="ไอคอน {0} รูปจากไคลเอนต์", ph_icon="ค้นหาชื่อไอคอน",
        lists_lead="ลำดับสินค้าในแท็บ Rekomendasi (แนะนำ), MP Shop, Bonus spesial (โบนัสพิเศษ) และ Paket berantai (แพ็กเกจต่อเนื่อง)",
        list_new="แนะนำ: ใหม่", list_best="แนะนำ: ขายดี", list_mp="MP Shop", list_special="โบนัสพิเศษ",
        list_chained="แพ็กเกจต่อเนื่อง", list_chained_hint="ซื้อตามลำดับ แต่ละแถวคือแพ็กเกจหนึ่งชุด", list_chain_add="เพิ่มชุด",
        list_add="เพิ่ม", lists_save="บันทึกรายการ", lists_saved="บันทึกรายการในแท็บแล้ว", empty="ว่าง",
        daily_title="รางวัลล็อกอินรายวัน", daily_lead="การล็อกอินครั้งแรกของแต่ละวัน (WIB, UTC+7) จะส่งรางวัลของวันนั้นเข้า Bag ล็อกอินต่อเนื่องจะเลื่อนไปวันถัดไป ขาดหนึ่งวันจะเริ่มใหม่ที่วันที่ 1 หลังวันสุดท้ายจะวนกลับไปเริ่มใหม่",
        daily_grant="ให้รางวัลของวันถัดไปตอนนี้", daily_reset="รีเซ็ตการล็อกอินต่อเนื่อง", daily_table="ตารางรางวัล", th_day="วัน", day_n="วันที่ {0}",
        drop_day="ลบวัน", daily_add="เพิ่มวัน", daily_save="บันทึกตาราง", f_total="จำนวนวันที่ล็อกอิน", f_last="ได้รางวัลล่าสุด",
        never="ยังไม่เคย", f_today_wib="วันนี้ (WIB)", daily_saved="บันทึกตารางรางวัลรายวันแล้ว", sent_bag="ส่ง \"{0}\" ไปที่ Bag แล้ว",
        confirm_reset_streak="รีเซ็ตการล็อกอินต่อเนื่องเป็น 0?", streak_reset="รีเซ็ตการล็อกอินต่อเนื่องแล้ว",
        nav_match="รางวัลหลังแมตช์", mr_title="รางวัลหลังแมตช์", mr_lead="มอบให้หลังทุกแมตช์ที่เล่นและทุกครั้งที่จำลองลีก: EP และ EXP ตามผลการแข่งขัน แล้วมีการ์ดรางวัลสามใบ การ์ดที่ผู้เล่นเปิดจะสุ่มจากรายการการ์ดตามน้ำหนัก บันทึกไว้ที่ {0} ใช้ตั้งแต่แมตช์ถัดไป", mr_result="ตามผลการแข่งขัน", th_result="ผล", mr_win="ชนะ", mr_draw="เสมอ", mr_loss="แพ้", mr_coach_exp="EXP ผู้จัดการ", mr_player_exp="EXP ต่อผู้เล่น", mr_cards="การ์ดรางวัล", mr_chance="โอกาส {0}%", mr_saved="บันทึกรางวัลหลังแมตช์แล้ว",
        scout_title="แมวมอง (Tukar Scout)", scout_lead="แมวมองที่เพิ่งใช้จะถูกล็อกจนกว่าคูลดาวน์จะหมด", th_available="ใช้ได้อีกครั้ง",
        locked="ล็อกอยู่", ready="พร้อมใช้", now="ตอนนี้", no_quest="ไม่มีเควสต์", scout_reset="รีเซ็ตคูลดาวน์ของแมวมองทั้งหมด",
        scouts_ready="แมวมองทั้งหมดพร้อมใช้อีกครั้ง", q_value="มูลค่านักเตะ ≥", q_grade="เกรด ≥", q_count="จำนวน", q_season="ซีซั่น",
        log_lead="บรรทัดล่าสุดของ captures/http_config.log", log_reload="โหลดบันทึกใหม่", log_auto="โหลดอัตโนมัติทุก 3 วินาที",
        failed="ไม่สำเร็จ: ", load_failed="โหลดไม่สำเร็จ: ",
        grade="เกรด", th_market="ตลาด", th_scout="แมวมอง", th_status="สถานะ", th_quest="เควสต์", f_item="ไอเทม", upgrade_point="แต้มอัปเกรด",
        e_player_missing="ไม่พบนักเตะ", e_number="{0} ต้องเป็นตัวเลข", e_range="{0} ต้องอยู่ระหว่าง {1}..{2}",
        e_no_spid="ไม่มี spid นี้ในฐานข้อมูล", e_club_full="สโมสรเต็มแล้ว ({0} คน)",
        e_in_squad="นักเตะอยู่ใน 18 คนของสควอด ให้เปลี่ยนตัวออกใน Formasi ก่อน", e_squad_first="นักเตะใน 18 คนของสควอดต้องอยู่ใน Tim inti",
        e_unknown_item="ไม่รู้จักแพ็คหรือไอเทมนี้", e_unknown_pack="ไม่รู้จักแพ็คนี้", e_gift_empty="กรอก EP แพ็ค หรือนักเตะ",
        e_day="{0}: {1}", e_day_empty="ว่างเปล่า", e_table_days="ตารางต้องมี 1..31 วัน",
        e_pack_empty="แพ็ค {0} ไม่มีการ์ดเลยสักใบ", e_length="{0} ต้องยาว 1..{1} ตัวอักษร",
        e_no_icon="ไม่มีไอคอน {0} ในไคลเอนต์", e_pick_icon="เลือกไอคอน",
        e_row_item="{0}: ไม่รู้จักไอเทม {1}", e_rows="{0} ต้องมี {1}..{2} แถว",
        e_unknown_pos="ไม่รู้จักตำแหน่งนี้", e_unknown_type="ไม่รู้จักประเภทไอเทมนี้",
        e_ep_range="EP ขั้นต่ำต้อง > 0 และ <= EP สูงสุด", e_unknown_prize="ไม่รู้จักรางวัล {0}", e_prizes="รางวัลต้องมี 1..10 รายการ",
        e_unknown_kind="ไม่รู้จักประเภทสินค้านี้", e_product_missing="ไม่พบสินค้า", e_not_changed="สินค้านี้ยังไม่เคยถูกแก้ไข",
        e_in_use="ยังถูกใช้โดย {0}", e_list_item="{0}: ใส่ไอเทม {1} ที่นี่ไม่ได้", e_chain_packages="แพ็กเกจต่อเนื่องใส่ได้เฉพาะแพ็กเกจ",
        nav_chat="แชท AI", chat_title="แชทของผู้จัดการเสมือน", chat_lead="คู่แข่งแรงก์ (ผู้จัดการเสมือน) จะพิมพ์ในแชทระหว่างแมตช์: ตอนเริ่มเกม เมื่อมีการทำประตูของทั้งสองฝ่าย ยิงพลาด ผู้รักษาประตูเซฟ และเมื่อคุณพิมพ์แชท ความเห็นต่อผลการแข่งขันจะแสดงเป็นแบนเนอร์เมื่อกลับมาที่หน้าแรงก์ ผู้จัดการแต่ละคนมีนิสัยของตัวเอง (เป็นมิตร ชอบแข่งขัน ตลก มั่นใจ ขี้เล่น) บันทึกไว้ที่ {0} ใช้ตั้งแต่ข้อความถัดไป", chat_on="เปิดแชท AI", chat_lang="ภาษาของข้อความ", chat_saved="บันทึกการตั้งค่าแชท AI แล้ว", e_chat_lang="ไม่รู้จักภาษาแชทนี้", nav_missions="ภารกิจ", mis_title="ภารกิจและความสำเร็จ", mis_lead="ภารกิจรายวัน (แถวภารกิจรายวันใน Manager > รายการความสำเร็จ รีเซ็ตทุกวันเวลา 00:00 WIB) ความสำเร็จถาวรพร้อมเหรียญตรา (เลเวลผู้จัดการ แมตช์ ชัยชนะ การอัปเกรด เกรด มูลค่าทีม ประตู แรงก์) และเควสต์รายสัปดาห์ที่ปุ่ม Quest (ได้ GP สำหรับกล่องรางวัล รีเซ็ตวันจันทร์เวลา 00:00 WIB) เซิร์ฟเวอร์นับแมตช์ที่คุณเล่นและการอัปเกรดที่สำเร็จ รางวัล EP ปรับตามตัวคูณราคานักเตะในแท็บเศรษฐกิจ บันทึกไว้ที่ {0} ภาษาและปุ่ม Quest มีผลหลังเปิดเกมใหม่", mis_on="เปิดภารกิจ", mis_lang="ภาษาของภารกิจ", mis_targets="เป้าหมาย (x)", mis_rewards="รางวัล (x)", mis_saved="บันทึกการตั้งค่าภารกิจแล้ว", e_mission_value="ค่าภารกิจไม่ถูกต้อง (เป้าหมาย 0.1-10 รางวัล 0-10 ภาษา id/en/vi)", wq_title="เควสต์รายสัปดาห์", wq_lead="รายการเควสต์ที่สุ่มทุกสัปดาห์ (เริ่มวันจันทร์ 00:00 WIB) สำหรับปุ่ม Quest ชื่อเว้นว่างได้ (ตั้งให้อัตโนมัติ) {0} ในชื่อคือเป้าหมาย เป้าหมายและ GP ปรับตามตัวคูณด้านบนด้วย มีผลตั้งแต่คำขอถัดไป เควสต์ที่ทำสำเร็จแล้วในสัปดาห์นี้ยังคงสำเร็จ", wq_th_stat="ประเภท", wq_th_todo="เป้าหมาย", wq_th_name="ชื่อ (ไม่บังคับ)", wq_add="เพิ่มเควสต์", wq_drop="ลบ", wq_count="เควสต์ต่อสัปดาห์", wq_shop_title="กล่องรางวัล GP", wq_th_item="ไอเทม", wq_th_price="ราคา (GP)", wq_shop_add="เพิ่มกล่อง", wq_reset="คืนค่าเริ่มต้น", wq_saved="บันทึกเควสต์รายสัปดาห์แล้ว", wq_reset_done="เควสต์รายสัปดาห์และกล่องรางวัลกลับเป็นค่าเริ่มต้นแล้ว", wq_confirm_reset="คืนค่าเควสต์และกล่องรางวัลเริ่มต้นหรือไม่?", wq_this="สัปดาห์นี้ (ถึง {0})", wq_next="สัปดาห์หน้า", e_quest_value="รายการเควสต์ไม่ถูกต้อง (เป้าหมายและ GP 1-10,000 ชื่อไม่เกิน 60 ตัวอักษร เควสต์ต่อสัปดาห์ 1-6 และไม่เกินจำนวนในรายการ)", e_quest_shop="กล่องรางวัลไม่ถูกต้อง (1-5 กล่อง ไอเทมไม่ซ้ำกัน GP 1-100,000)", ptr_title="รางวัลเวลาเล่น", ptr_lead="ได้รางวัลทุกครั้งที่เวลาเล่นรวมของวันนี้ถึงเป้าหมาย (วันเริ่มนับเวลา 00:00 WIB; ช่วงที่ไม่มีการเล่นนานกว่า 20 นาทีไม่นับ) รางวัลเข้ากระเป๋าและแสดงเป็นการแจ้งเตือนอีเวนต์ในเกม วันนี้: {0} นาที รับรางวัลแล้ว {1} ครั้ง บันทึกไว้ที่ {2}", ptr_minutes="นาที", ptr_add="เพิ่มเป้าหมาย", ptr_saved="บันทึกรางวัลเวลาเล่นแล้ว", e_ptr_rows="ตารางเวลาเล่นไม่ถูกต้อง (1-10 แถว นาที 1-1440 และเพิ่มขึ้นเรื่อย ๆ ทุกแถวต้องมี EP หรือแพ็ก)", news_region="ข่าวประเทศ (ไม่บังคับ)", news_title="หัวข้อแชททั่วโลก", news_lead="ผู้จัดการเสมือนในแท็บ Channel จะคุยข่าวฟุตบอลโลกเสมอ บวกกับประเทศที่ติ๊กไว้: เทรนด์ Google วันนี้ที่เกี่ยวกับฟุตบอลและพาดหัวข่าวฟุตบอลจาก Google News ในภาษานั้น (X, Instagram และ TikTok ต้องล็อกอิน Reddit ปฏิเสธเซิร์ฟเวอร์) ผู้จัดการแต่ละคนชอบคุยเรื่องสโมสร นักเตะคนโปรด หรือประเทศของตัวเอง (หนึ่งในที่ติ๊กไว้) เมื่อมีในข่าว อัปเดตเองวันละครั้ง ดึงเมื่อ: {0}", news_none="ยังไม่มีหัวข้อ (เซิร์ฟเวอร์ออฟไลน์หรือเพิ่งเริ่ม)", e_chat_region="ไม่รู้จักภูมิภาคข่าวนี้", lr_title="รางวัลแรงก์ (อันดับ)", lr_lead="รางวัลซีซันแรงก์ (รายเดือน): รางวัลหลักจากดิวิชันของโหมดที่เรตติ้งดีที่สุดตอนจบซีซัน และรางวัลรองจากทุกโหมดอื่นที่เล่น เข้ากระเป๋าเมื่อซีซันใหม่เริ่ม รางวัลรายสัปดาห์: ทุกครั้งที่จำนวนชัยชนะแรงก์ของสัปดาห์นี้ (จันทร์-อาทิตย์) ถึงเป้า รางวัลเข้ากระเป๋าพร้อมป๊อปอัปในหน้าแรงก์ หน้าแรงก์ก็แสดงรางวัลเหล่านี้ด้วย บันทึกไว้ที่ {0}", lr_div="ดิวิชัน", lr_main="รางวัลหลัก", lr_sub="รางวัลรอง", lr_count="จำนวน", lr_weekly="รางวัลรายสัปดาห์", lr_wins="ชนะสัปดาห์นี้", lr_item="รางวัล", lr_add="เพิ่มขั้น", lr_saved="บันทึกรางวัลแรงก์แล้ว", lr_confirm_reset="คืนค่ารางวัลแรงก์เริ่มต้นหรือไม่?", lr_reset_done="รางวัลแรงก์กลับเป็นค่าเริ่มต้นแล้ว", e_lr_rows="รางวัลแรงก์ไม่ถูกต้อง (EP 1-10,000,000,000 แพ็ก/ไอเทม 1-99; ขั้นรายสัปดาห์ 1-500 ชนะ เพิ่มขึ้นเรื่อย ๆ ไม่เกิน 20 ขั้น)", kr_title="รางวัล King of the Hill", kr_lead="รางวัลรายสัปดาห์ (สัปดาห์ = วันที่ 1-7, 8-14, 15-21, 22-28) และรางวัลซีซัน (รายเดือน) เมื่อสัปดาห์หรือซีซันที่คุณเล่นจบลง หน้ารางวัลจะแสดงเมื่อเปิด King of the Hill และรางวัลที่ทำได้จะเข้ากระเป๋า รายสัปดาห์: เป้าหมายจำนวนชนะหรือจำนวนแมตช์ในสัปดาห์นั้น และรางวัลดิวิชันเมื่อชนะอย่างน้อยตามที่กำหนด ซีซัน: รางวัลดิวิชันตอนจบซีซันเมื่อชนะอย่างน้อยตามที่กำหนด และรางวัลอันดับในภูมิภาค หน้า King of the Hill ก็แสดงรางวัลเหล่านี้ด้วย บันทึกใน {0} มีผลทันที", kr_type="เป้าหมายรายสัปดาห์", kr_type_winning="ชนะ", kr_type_match="แมตช์", kr_win="ชนะในสัปดาห์นั้น", kr_match="แมตช์ในสัปดาห์นั้น", kr_week_item="รางวัลเป้าหมายรายสัปดาห์", kr_week_grade="จำนวนชนะสำหรับรางวัลดิวิชันรายสัปดาห์", kr_season_grade="จำนวนชนะสำหรับรางวัลดิวิชันซีซัน", kr_week_div="รางวัลดิวิชันรายสัปดาห์", kr_season_div="รางวัลดิวิชันซีซัน", kr_ranking="รางวัลอันดับซีซัน (ภูมิภาค)", kr_from="จากอันดับ", kr_to="ถึงอันดับ", kr_add="เพิ่มอันดับ", kr_saved="บันทึกรางวัล King of the Hill แล้ว", kr_confirm_reset="คืนค่ารางวัล King of the Hill เริ่มต้นหรือไม่?", kr_reset_done="รางวัล King of the Hill กลับเป็นค่าเริ่มต้นแล้ว", e_kr_rows="รางวัล King of the Hill ไม่ถูกต้อง (EP 1-10,000,000,000 แพ็ก/ไอเทม 1-99; เป้าหมาย 1-100 ชนะ หรือ 1-200 แมตช์ ชนะเพื่อรางวัลดิวิชัน 1-100 ต่อสัปดาห์ และ 1-500 ต่อซีซัน; อันดับ 1-20 แถว ในช่วง 1-1000 เพิ่มขึ้นและไม่ซ้อนกัน)",
        nav_notice='ประกาศ', notice_title='รูปประกาศ', notice_lead='รูปนี้จะแสดงในป๊อปอัปประกาศทุกครั้งที่ล็อกอิน (issue #36) เลือกไฟล์ JPG หรือ PNG หน้านี้จะปรับให้พอดีกับขนาดป๊อปอัป (800×720) ก่อนบันทึก มีผลตั้งแต่การล็อกอินครั้งถัดไปโดยไม่ต้องแก้ไฟล์ไคลเอนต์', notice_file='ไฟล์รูป (JPG/PNG)', notice_mode='การปรับขนาด', notice_fit='แสดงทั้งรูป (มีพื้นหลัง)', notice_fill='เต็มกรอบ (ตัดขอบ)', notice_bg='สีพื้นหลัง', notice_save='บันทึกประกาศ', notice_delete='ลบประกาศ', notice_current='ประกาศปัจจุบัน', notice_new='ตัวอย่างใหม่', notice_none='ยังไม่มีรูปประกาศ: ป๊อปอัปจะว่างเปล่า', notice_saved='บันทึกรูปประกาศแล้ว จะแสดงในการล็อกอินครั้งถัดไป', notice_deleted='ลบรูปประกาศแล้ว', notice_confirm_delete='ลบรูปประกาศหรือไม่?', notice_pick_first='เลือกไฟล์รูปก่อน', e_notice_png='รูปต้องเป็น PNG ขนาด 800×720 (เลือกไฟล์ใหม่ในหน้านี้)', e_notice_size='รูปใหญ่เกินไป (สูงสุด 8 MB)',
        nav_assets='รูปภาพ', as_title='รูปที่ยังไม่มี', as_lead='เกมแสดงรูปนักเตะ โลโก้สโมสร รูปชุดแข่ง โลโก้ลีก และรูปสตาฟจากแคชของไคลเอนต์ รูปที่ไม่มีจะแสดงเป็นรูปเริ่มต้น (เงาคน โลโก้ว่าง) ปุ่มดาวน์โหลดจะดึงรูปที่ยังไม่มีจาก fifaaddict.com ในเบื้องหลัง ทีละรูปทุกครึ่งวินาที ไฟล์ที่มีอยู่แล้วจะไม่ถูกแก้ไข รูปนักเตะอาจมีหลายพันรูปและใช้เวลานาน หยุดและดาวน์โหลดต่อได้ทุกเมื่อ รูปใหม่จะแสดงอย่างช้าที่สุดหลังเปิดเกมใหม่ รูปนักเตะที่ fifaaddict.com ไม่มีจะดึงจาก FC Online (FO4) ดาวน์โหลดทั้งหมดจะดึงทุกประเภทในครั้งเดียวและสแกนฐานข้อมูลการ์ดทั้งหมด เมื่อเซิร์ฟเวอร์ไม่ตอบ การดาวน์โหลดจะรอนานขึ้นแล้วทำต่อเอง', as_all='รูปทั้งหมด', as_fetch_all='ดาวน์โหลดทั้งหมดที่ยังไม่มี', as_kind='ประเภท', as_added='ดาวน์โหลดแล้ว', as_failed='ไม่มีให้ดาวน์โหลด', as_faces='รูปนักเตะ', as_crests='โลโก้สโมสร', as_uniforms='รูปชุดแข่ง', as_leagues='โลโก้ลีก', as_staffs='รูปสตาฟ', as_careers='ประวัติสโมสร', as_careers_note='ประวัติสโมสรดึงประวัติสโมสรของนักเตะจนถึงฤดูกาลปัจจุบันจาก Wikidata (ปี สโมสร การยืมตัว) สำหรับแท็บคุณลักษณะในข้อมูลนักเตะ เสร็จแล้วกด ฐานข้อมูล > ใช้กับเกม นักเตะที่หาไม่พบจะใช้ประวัติเดิมของ FO3 (ถึงปี 2017)', as_fetch='ดาวน์โหลด', as_stop='หยุด', as_running='กำลังดาวน์โหลด: {0}', as_idle='ไม่มีการดาวน์โหลดที่กำลังทำงาน', as_started='เริ่มดาวน์โหลดแล้ว', as_stopped='หยุดดาวน์โหลดแล้ว', e_asset_kind='ไม่รู้จักประเภทรูป', e_asset_busy='ยังมีการดาวน์โหลดอื่นทำงานอยู่',
        nav_prematch="ตั้งค่าแมตช์", pm_title="ตั้งค่าแมตช์", pm_lead="สนาม ช่วงเวลา สภาพอากาศ และความยาวของแมตช์ถัดไป (ลีก คัพ แรงก์) ใน Multiple > Rank เปลี่ยนได้ที่หน้าตั้งค่าแมตช์เช่นกัน และตัวเลือกนั้นจะถูกบันทึกที่นี่ด้วย หน้าลีกและคัพแสดงค่าเท่านั้น ครึ้มฟ้า: กลางวันมีเมฆมาก กลางคืนเป็นยามพลบค่ำ (หน้าลีก/คัพแสดงเป็น \"Salju\")", pm_stadium="สนาม", pm_tod="ช่วงเวลา", pm_weather="สภาพอากาศ", pm_length="ความยาว", pm_home="สนามของทีมเจ้าบ้าน", pm_random="สุ่ม", pm_day="กลางวัน", pm_night="กลางคืน", pm_dry="แจ่มใส", pm_rain="ฝนตก", pm_snow="หิมะ", pm_overcast="ครึ้มฟ้า", pm_minutes="{0} นาที", pm_ball="ลูกบอล", pm_saved="บันทึกการตั้งค่าแมตช์แล้ว", e_pm_value="ค่าการตั้งค่าแมตช์ไม่ถูกต้อง",
        nav_economy="เศรษฐกิจ", eco_title="เศรษฐกิจ", eco_lead="ราคาและ EP ของผู้จัดการทุกคน ง่าย = เหมือนเดิม (ร้านค้าฟรี) สมจริง: ร้านค้าเสียเงิน (ราคาสินค้า = มูลค่าของที่ได้ สินค้าที่ไม่มี EP เช่นบูสต์หรือแคปซูล EXP ใช้ราคาพื้นฐานคูณตัวคูณราคานักเตะ ราคาแพ็ค = มูลค่าการ์ดในแพ็ค +13% เมื่อเปิดตลาดแบบไดนามิก เพราะเก็บแพ็คไว้เปิดวันที่ราคาสูงได้) และตลาดแบบไดนามิก ยาก: ราคานักเตะและแพ็ค x2 และ EP เริ่มต้นน้อย ตลาดแบบไดนามิก: ราคานักเตะแต่ละคนขึ้นลงทุกวัน ผู้จัดการเสมือนบางครั้งขายถูก และคุณตั้งราคาได้ถึง 105% (ขายได้ช้าลง): ซื้อถูก ขายแพง กฎเข้มงวด (ยาก): การอัปเกรด การแลกเปลี่ยน และแมวมองจะไม่สร้าง EP เพิ่ม: ค่าอัปเกรดเท่ากับมูลค่าที่เพิ่มขึ้น การกู้คืนจ่ายด้วย EP และข้อเสนอจะไม่สูงกว่ามูลค่าการ์ดที่ให้ไป หากไม่เปิด (ง่าย สมจริง) ใช้กฎของเกมเดิม: ค่าอัปเกรด 2% ของมูลค่าการ์ด การกู้คืนใช้แต้มอัปเกรด และข้อเสนอไม่มีเพดาน EP จากแมตช์ตั้งได้ที่แท็บรางวัลหลังแมตช์ EP เริ่มต้นใช้กับผู้จัดการใหม่", eco_preset="พรีเซ็ต", eco_custom="กำหนดเอง", eco_easy="ง่าย (เหมือนเดิม)", eco_real="สมจริง", eco_hard="ยาก", eco_shopfree="ร้านค้าฟรี (0 EP)", eco_players="ราคานักเตะ (เท่า)", eco_packs="ราคาแพ็ค (เท่า)", eco_start="EP เริ่มต้นของผู้จัดการใหม่", eco_dynamic="ตลาดแบบไดนามิก (ราคาขึ้นลง ซื้อขายทำกำไรได้)", eco_strict="กฎเข้มงวด (อัปเกรด แลกเปลี่ยน และแมวมองไม่สร้าง EP)", eco_saved="บันทึกการตั้งค่าเศรษฐกิจแล้ว", e_eco_value="ค่าการตั้งค่าเศรษฐกิจไม่ถูกต้อง (ตัวคูณ 0.1-100, EP เริ่มต้น 0 ขึ้นไป)",
        nav_db='ฐานข้อมูล', db_title='ฐานข้อมูลการ์ดและสโมสร', db_lead='แก้ไขการ์ดนักเตะแต่ละซีซัน: ค่าพลัง ตำแหน่ง เท้าถนัด เท้าข้างไม่ถนัด สกิลมูฟ เวิร์กเรต ทักษะพิเศษ และสโมสร (ย้ายทีม) การ์ดหนึ่งใบ = หนึ่ง spid = ซีซัน × 1,000,000 + player id (เมสซี่คือ 158023: การ์ดซีซัน 91 ของเขาคือ 91158023) ค้นหาด้วยชื่อ spid หรือ player id การแก้ไขบันทึกลง gamedata/fifadb.sqlite และจดไว้ใน {0} (ค่าเดิมและค่าใหม่: กู้คืนได้ และการ build ซีซัน FO4 จะใส่กลับให้) เกมอ่านสำเนาฐานข้อมูลของตัวเอง: ปิดเกม กด ใช้กับเกม รอจนเสร็จ แล้วเปิด play.bat', db_apply='ใช้กับเกม', db_started='กำลังติดตั้งลงเกมเบื้องหลัง…', db_done='ติดตั้งเสร็จแล้ว เปิด play.bat เพื่อเริ่มเซิร์ฟเวอร์และเกมใหม่', db_failed='ล้มเหลว (รหัส {0}) ดูบันทึกด้านล่าง', as_install='ใช้กับเกม', db_search='ค้นหาการ์ด', db_q='ชื่อ, spid หรือ player id', db_hint='พิมพ์ชื่ออย่างน้อย 2 ตัวอักษร หรือ spid / player id', db_edited='การ์ดที่แก้ไขแล้ว', db_none='ยังไม่มีการ์ดที่แก้ไข', th_changed='ที่แก้ไข', f_club='สโมสร', ph_club='พิมพ์ชื่อสโมสร', f_pos_n='ตำแหน่ง {0}', f_foot='เท้าถนัด', foot_right='ขวา', foot_left='ซ้าย', f_weak='เท้าข้างไม่ถนัด', f_skill='สกิลมูฟ', f_wr_att='เวิร์กเรตเกมรุก', f_wr_def='เวิร์กเรตเกมรับ', wr_low='ต่ำ', wr_med='กลาง', wr_high='สูง', f_stats='ค่าพลัง', f_traits='ทักษะพิเศษ', orig_value='ค่าเดิม: {0}', card_saved='บันทึก {0} แล้ว ตอนนี้ OVR {1} กด ใช้กับเกม เพื่อให้เกมใช้ค่านี้', crest_title='โลโก้สโมสร', crest_lead='เลือกสโมสรและรูปภาพ (PNG พื้นใสดีที่สุด) หน้านี้จะปรับเป็น 256×256 และ 128×128 แล้วบันทึกลงแคชของไคลเอนต์ โลโก้เดิมเก็บไว้ใน gamedata/crest_backup และกู้คืนได้ แสดงหลังเปิดเกมใหม่', crest_save='บันทึกโลโก้', crest_revert='กู้คืนโลโก้เดิม', crest_confirm='กู้คืนโลโก้เดิมของสโมสรนี้?', crest_now='โลโก้ปัจจุบัน', crest_new='โลโก้ใหม่', crest_none='ไม่มีโลโก้ของสโมสรนี้ในแคช', crest_pick='เลือกสโมสรและไฟล์รูปภาพก่อน', crest_saved='บันทึกโลโก้แล้ว จะแสดงหลังเปิดเกมใหม่', crest_reverted='กู้คืนโลโก้เดิมแล้ว', e_card_field='แก้ไข {0} ที่นี่ไม่ได้', e_club='ไม่มีสโมสร {0} ในเกม', e_not_edited='การ์ดนี้ยังไม่ได้แก้ไข', e_crest_png='โลโก้ต้องเป็น PNG {0}×{0} (เลือกไฟล์ใหม่ในหน้านี้)',
        f_look='รูปลักษณ์ 3D', look_lead='รหัส EA จากฐานข้อมูล 3D ของเกม (fifa_ng_db) ใบหน้า = ศีรษะจากแถว 3D ใดก็ได้: รายการมีการ์ดของนักเตะคนนี้ พิมพ์ชื่อนักเตะคนอื่นเพื่อเลือกศีรษะของเขาจากรายการ หรือพิมพ์ spid / player id แสดงในเกมหลังกด ใช้กับเกม', look_generic='ศีรษะตอนนี้เป็นแบบทั่วไป: เปลี่ยนทรงผมและสีผม (0 = บลอนด์) หนวดเครา สีผิว และสีตาได้', look_scan='ศีรษะตอนนี้เป็นแบบสแกน: ผมและหนวดเคราติดมากับศีรษะ ถ้าต้องการผมแบบอื่นให้เลือกใบหน้าจากศีรษะอื่น', look_shared='การ์ดซีซันรหัสเกิน 99 (26 LIVE, 25 LIVE) ไม่มีแถว 3D ของตัวเอง: การแก้ไขนี้มีผลกับการ์ดทุกใบของนักเตะคนนี้ที่ไม่มีแถวของตัวเอง', look_own='การ์ดนี้', f_face='ใบหน้า', l_hairtypecode='ทรงผม', l_haircolorcode='สีผม', l_facialhairtypecode='หนวดเครา', l_facialhaircolorcode='สีหนวดเครา', l_skintonecode='สีผิว', l_eyecolorcode='สีตา', l_bodytypecode='รูปร่าง', e_no_engine='อ่านฐานข้อมูล 3D ของเกม (data_0.big) ไม่ได้', sn_title='ซีซันที่สร้างเอง', sn_lead='สร้างซีซันการ์ดของคุณเอง: รหัส ชื่อ และตรา เติมการ์ดด้วยการคัดลอกจากผลการค้นหา (เพิ่มค่าพลังทั้งหมดพร้อมกันได้) แล้วแก้ไขเหมือนการ์ดอื่น บันทึกใน {0} ใช้กับเกม จะติดตั้งตรา และหลัง play.bat ซีซันนี้จะมีแพ็ก Top 200 และทีมคัลเลอร์ของตัวเอง', f_season_code='รหัสซีซัน', th_badge='ตรา', th_cards='การ์ด', sn_badge='ตรา (PNG/JPG)', sn_save='บันทึกซีซัน', sn_saved='บันทึกซีซัน {0} แล้ว', sn_delete='ลบซีซัน', sn_confirm_delete='ลบซีซัน {0} และการ์ด {1} ใบ? การ์ดของซีซันนี้ที่สโมสรมีอยู่จะหายจากฐานข้อมูลด้วย', sn_deleted='ลบซีซัน {0} แล้ว', sn_copy_to='คัดลอกไปซีซัน', f_boost_stats='เพิ่มค่าพลังทั้งหมด', sn_copy='คัดลอก', sn_copied='คัดลอก {0} ไปซีซัน {1} แล้ว (spid {2})', sn_none='ยังไม่มีซีซันที่สร้างเอง', sn_remove='ลบการ์ด', sn_removed='ลบการ์ด {0} แล้ว', confirm_card_remove='ลบการ์ด {0} (spid {1}) ออกจากซีซัน?', sn_pick_badge='เลือกตราก่อน', mod_title='ม็อด: ส่งออกและนำเข้า', mod_lead='บันทึกซีซันที่สร้างเอง การ์ดที่คัดลอก การแก้ไขการ์ด และทีมคัลเลอร์ที่สร้างเองทั้งหมดลงไฟล์ JSON ไฟล์เดียว หรือโหลดไฟล์แบบนี้จากเซิร์ฟเวอร์อื่น หลังนำเข้าให้กด ใช้กับเกม', mod_export='ส่งออกม็อด', mod_import='นำเข้าม็อด (JSON)', mod_imported='นำเข้าม็อดแล้ว: สำเร็จ {0} ล้มเหลว {1}', e_mod='ไม่ใช่ไฟล์ม็อด FO3ID (ต้องเป็น JSON จาก ส่งออกม็อด)', e_season_icon='ตราต้องเป็น PNG ไม่เกิน 1 MB (เลือกไฟล์ใหม่ในหน้านี้)', e_face='ไม่มีแถว 3D {0}', e_season='ซีซัน {0} ไม่ใช่ซีซันที่สร้างเอง', e_season_code='เกมใช้รหัสซีซัน {0} อยู่แล้ว', e_card_exists='มีการ์ด {0} อยู่แล้ว',
        nav_tc='ทีมคัลเลอร์', tc_title='ทีมคัลเลอร์ที่สร้างเอง', tc_lead='สร้างทีมคัลเลอร์ของคุณเอง: เลือกการ์ดที่เป็นสมาชิกด้วยตัวกรอง (ซีซัน สโมสรปัจจุบันหรือเดิม ประเทศ นักเตะ OVR ขั้นต่ำ การ์ดตลาดที่ดีที่สุด N ใบ) การ์ดต้องผ่านทุกตัวกรองที่กรอก ระดับเหมือนทีมคัลเลอร์พิเศษในเกม: นักเตะต่างคน 5-6 / 7-8 / 9-11 คนใน XI (OVR +1/+2/+3 หรือสูงสุดตามค่า OVR ที่เลือก) บูสต์ค่าพลังเพิ่มในทุกระดับ น้อยกว่า 5 คน: ระดับเดียวสำหรับทุกคน บันทึกใน {0} มีผลตั้งแต่ล็อกอินครั้งถัดไป ไม่ต้องรีสตาร์ต', tc_members='สมาชิก', tc_boost='บูสต์', tc_new='ทีมคัลเลอร์ใหม่', tc_edit='แก้ไข {0}', tc_nations='ประเทศ', tc_players='นักเตะ', tc_min_ovr='OVR ขั้นต่ำ (0 = ทั้งหมด)', tc_best='การ์ดที่ดีที่สุด N ใบ (0 = ทั้งหมด)', tc_saved='บันทึก {0} แล้ว: การ์ด {1} ใบจากนักเตะ {2} คน', tc_deleted='ลบ {0} แล้ว', tc_confirm_delete='ลบทีมคัลเลอร์ {0}?', tc_none='ยังไม่มีทีมคัลเลอร์ที่สร้างเอง', tc_all_cards='การ์ดทั้งหมด', tc_all_pos='ทุกตำแหน่ง', ph_pick='พิมพ์แล้วเลือก', e_nation='ไม่รู้จักประเทศ {0}', e_player_id='ไม่มีนักเตะ player id {0}', e_color_missing='ไม่พบทีมคัลเลอร์', e_tc_value='ค่าทีมคัลเลอร์ไม่ถูกต้อง',
        koth_title='ภูมิภาค King of the Hill', koth_lead='เลือกพื้นที่ที่ King of the Hill ใช้: เมืองของอินโดนีเซีย สิงคโปร์/มาเลเซีย ไทย เวียดนาม (ชื่อเวียดนามใช้อักษรเวียดนาม) หรือพื้นที่ที่คุณสร้างเอง หลังเปลี่ยน ผู้จัดการเสมือนจะกระจายไปยังเมืองของพื้นที่ใหม่ และภูมิภาคที่คุณเลือกจะว่าง: เลือกใหม่เมื่อเปิด King of the Hill ไม่ต้องรีสตาร์ต', koth_regions='พื้นที่', koth_id='อินโดนีเซีย', koth_sgmy='สิงคโปร์ / มาเลเซีย', koth_th='ไทย', koth_vn='เวียดนาม', koth_custom='กำหนดเอง', koth_saved='บันทึกพื้นที่ King of the Hill แล้ว', e_koth_set='ไม่รู้จักพื้นที่', koth_custom_title='พื้นที่กำหนดเอง', koth_custom_lead='เขียนพื้นที่ของคุณเอง บรรทัดละหนึ่งภูมิภาค: ภูมิภาค: เมือง, เมือง, เมือง (เช่น Miền Bắc: Hà Nội, Hải Phòng, Sa Pa) 1-20 ภูมิภาค ภูมิภาคละ 1-30 เมือง ชื่อยาวไม่เกิน 24 ตัวอักษรและห้ามซ้ำ คัดลอกพื้นที่ในเกมแล้วแก้ไขได้ เมืองที่ชื่อเดิมจะคงไอดีไว้ ผู้เล่นในเมืองนั้นจึงยังอยู่ที่เดิม บันทึกแล้วเลือก กำหนดเอง ด้านบน บันทึกใน {0} มีผลทันที', koth_copy='คัดลอกจาก', koth_copy_btn='คัดลอกไปยังตัวแก้ไข', koth_custom_save='บันทึกพื้นที่กำหนดเอง', koth_custom_saved='บันทึกพื้นที่กำหนดเองแล้ว: {0} ภูมิภาค {1} เมือง', e_koth_line='บรรทัด {0}: เขียนเป็น ภูมิภาค: เมือง, เมือง', e_koth_name='ชื่อว่าง ยาวเกิน 24 ตัวอักษร หรือซ้ำ: {0}', e_koth_count='ต้องมี 1-20 ภูมิภาค ภูมิภาคละ 1-30 เมือง', e_koth_empty='พื้นที่กำหนดเองยังว่าง: เขียนและบันทึกก่อน',
        face_title='รูปนักเตะ', face_lead='รูปของการ์ดในเกม (128×128) เกมจะหารูปของการ์ด (p<spid>.png รูปของซีซันนั้น) ก่อน แล้วจึงหารูปของนักเตะ (p<player id>.png) ถ้าไม่มีจะแสดงใบหน้าเริ่มต้น แท็บรูปภาพดาวน์โหลดรูปที่ยังไม่มีจาก fifaaddict.com รวมถึงรูปของแต่ละซีซัน ที่นี่เปลี่ยนรูปได้สำหรับการ์ดทุกใบของนักเตะคนนี้หรือเฉพาะการ์ดนี้ ด้วยรูปของคุณเอง (PNG/JPG หน้านี้จะปรับเป็น 128×128) หรือด้วยรูปของ spid / player id อื่น (จากแคช หรือดึงจาก fifaaddict.com โดยตรงถ้ายังไม่มี) บันทึกใน gamedata/faces ไม่แก้ไขไฟล์แคชของไคลเอนต์ และการกู้คืนจะลบรูปนี้ แสดงหลังเปิดเกมใหม่', face_for='ใช้กับ', face_player='การ์ดทุกใบของนักเตะคนนี้ (player id {0})', face_card='เฉพาะการ์ดนี้ (spid {0})', face_save='บันทึกรูป', face_revert='กู้คืนรูปเดิม', face_confirm='ลบรูปที่ใส่เองนี้และใช้รูปเดิม?', face_now='รูปในเกม', face_new='รูปใหม่', face_none='ยังไม่มีรูป: เกมแสดงใบหน้าเริ่มต้น', face_pick='เลือกไฟล์รูปก่อน', face_saved='บันทึกรูปแล้ว จะแสดงหลังเปิดเกมใหม่', face_reverted='กู้คืนรูปเดิมแล้ว', e_face_png='รูปต้องเป็น PNG {0}×{0} (เลือกไฟล์ใหม่ในหน้านี้)', head_scan='ศีรษะสแกน', head_generic='ศีรษะทั่วไป', face_from='รูปของ spid / player id', face_use='ใช้รูปนี้', face_used='ใช้รูป {0} แล้ว จะแสดงหลังเปิดเกมใหม่', e_face_none='ไม่มีรูป {0} ทั้งในแคชและบน fifaaddict.com', e_face_fetch='fifaaddict.com ไม่ตอบ ({0}) ลองใหม่ภายหลัง',
        phd_title='ใบหน้า 3D จากรูปถ่าย', phd_lead='สำหรับนักเตะที่ใช้ใบหน้าทั่วไป: รูปถ่ายระยะใกล้จากด้านหน้าจะกลายเป็นผิวหน้าบนศีรษะ 3D ที่สแกนแล้วของนักเตะอื่น (ผู้ให้: รูปศีรษะและผมติดมาด้วย ส่วนดวงตายังเป็นของผู้ให้) เลือกรูป (หน้าตรง แสงสม่ำเสมอ) คลิก 11 จุดตามลำดับ เลือกผู้ให้ที่ดูคล้าย (สีผิว ผม ไม่มีเคราถ้านักเตะไม่มีเครา) แล้วกดสร้าง ใช้กับการ์ดทุกใบของนักเตะคนนี้ที่ใช้แถว player id หรือศีรษะทั่วไป บันทึกใน gamedata/photo_heads แสดงหลังกด ฐานข้อมูล > ใช้กับเกม และเปิดเกมใหม่', phd_donor='ศีรษะผู้ให้ (แถวเอนจิน)', phd_donor_hint='พิมพ์ชื่อนักเตะที่มีศีรษะสแกน', phd_step='คลิกจุด {0}/{1}: {2}', phd_all='ทำเครื่องหมายครบ {0} จุดแล้ว คลิกใกล้จุดเพื่อย้าย', phd_undo='ยกเลิกจุด', phd_reset='ล้างทุกจุด', phd_photo='รูปและจุด', phd_now='เท็กซ์เจอร์ใบหน้า 3D', phd_none='ยังไม่มีใบหน้า 3D จากรูปถ่าย', phd_make='สร้างใบหน้า 3D', phd_delete='ลบใบหน้า 3D', phd_confirm='ลบใบหน้า 3D จากรูปถ่ายของนักเตะคนนี้?', phd_made='สร้างใบหน้า 3D แล้ว กด ฐานข้อมูล > ใช้กับเกม (ขณะปิดเกม) แล้วเปิดเกม', phd_deleted='ลบใบหน้า 3D แล้ว กดใช้กับเกมเพื่อเอาออกจากไคลเอนต์', phd_pick='เลือกรูปก่อน', pt_eye_r='กลางตาขวาของนักเตะ (ซ้ายในรูป)', pt_eye_l='กลางตาซ้ายของนักเตะ (ขวาในรูป)', pt_nose='ปลายจมูก', pt_mouth_r='มุมปากขวา (ซ้ายในรูป)', pt_mouth_l='มุมปากซ้าย (ขวาในรูป)', pt_chin='ปลายคาง', pt_forehead='กลางหน้าผาก', pt_temple_r='ขมับขวา: ขอบหน้าระดับคิ้ว (ซ้ายในรูป)', pt_temple_l='ขมับซ้าย: ขอบหน้าระดับคิ้ว (ขวาในรูป)', pt_jaw_r='กรามขวา: ขอบหน้าใต้ปากเล็กน้อย (ซ้ายในรูป)', pt_jaw_l='กรามซ้าย: ขอบหน้าใต้ปากเล็กน้อย (ขวาในรูป)', e_ph_png='รูปต้องเป็น PNG ไม่เกิน {0} พิกเซล (เลือกไฟล์ใหม่ในหน้านี้)', e_ph_points='ทำเครื่องหมายให้ครบ {0} จุดบนรูปก่อน', e_ph_donor='แถว {0} ไม่ใช่ศีรษะสแกน: เลือกนักเตะที่มีศีรษะสแกนเป็นผู้ให้', e_ph_files='ไม่มีไฟล์ของศีรษะ {0} ในไคลเอนต์',
        phd_icon='ดึงจากรูปนักเตะ', phd_icon_done='ใส่รูป 11 จุด และผู้ให้จากรูปของนักเตะแล้ว ตรวจจุด (คลิกใกล้จุดเพื่อย้าย) แล้วกดสร้างใบหน้า 3D', phd_icon_nopts='ไม่พบใบหน้าในรูป: คลิกจุดเอง', e_ph_icon='ไม่มีรูปของนักเตะ {0} ทั้งในแคชและบน fifaaddict.com', phd_club='สร้างใบหน้า 3D จากรูปให้นักเตะใบหน้าทั่วไปในสโมสร', phd_club_lead='ใบหน้า 3D อัตโนมัติ: นักเตะในสโมสรทุกคนที่ยังใช้ใบหน้าทั่วไปและมีรูปในแคช (รูป FO3 แท็บรูปภาพดาวน์โหลดรูปที่ขาด) จะได้ใบหน้า 3D จากรูปของเขา ตัวตรวจจับ YuNet หาจุดบนใบหน้า และเลือกผู้ให้ตามสีผิวและสีผม ครั้งแรกใช้เวลาราวหนึ่งนาที (อ่านศีรษะสแกนทั้งหมดหนึ่งครั้ง) ตรวจและเปลี่ยนผลของแต่ละคนได้ในหน้าต่างการ์ด แล้วกดใช้กับเกม', phd_club_busy='กำลังสร้างใบหน้า 3D…', phd_club_done='สร้างใบหน้า 3D แล้ว {0} คน ข้าม {1} คน', phd_skip_icon='ไม่มีรูป', phd_skip_face='ไม่พบใบหน้า',
        world_title='โลกของเกม', world_lead='โมเดิร์น: สโมสร AI การเลือกสโมสร และแพ็ก This Season ใช้การ์ด 26 LIVE (เมื่อติดตั้งแล้ว) คลาสสิก FO3: ทุกสโมสรใช้สกวอด 2017 ดั้งเดิมของ FO3 ผู้จัดการเสมือนใช้เฉพาะซีซันดั้งเดิมของ FO3 และซีซันที่เพิ่มมา (25 LIVE, 26 LIVE, WC18, ซีซันที่สร้างเอง) จะไม่มีในแพ็ก ตลาด หรือการเทรด: ให้ได้ผ่านหน้าแอดมินเท่านั้น (เพิ่มนักเตะ, Bag) สโมสรและคอลเลกชันของคุณไม่เปลี่ยน มีผลหลังรีสตาร์ตเซิร์ฟเวอร์ (play.bat)', world_label='โหมด', world_modern='โมเดิร์น (26 LIVE)', world_classic='คลาสสิก FO3 (2017)', world_saved='บันทึกโลกของเกมแล้ว', world_restart='เซิร์ฟเวอร์ยังใช้โหมดเดิมอยู่: เปิด play.bat เพื่อใช้ตัวเลือกนี้', e_world='ไม่รู้จักโหมดโลก',
        vm_title='ผู้จัดการเสมือน', vm_lead='ผู้จัดการเสมือนราว 2,000 คน (คู่แข่ง Rank และ King of the Hill สโมสร AI ตลาด) คงเดิมทุกครั้งที่เปิดเกมและเซิร์ฟเวอร์ มีเพียงปุ่ม สร้างใหม่ ตรงนี้ที่สร้างกลุ่มใหม่: ชื่อ เลเวล สโมสร สกวอด สไตล์การเล่น เรตติ้ง Rank ภูมิภาค และสโมสร AI เปลี่ยนทันที ไม่ต้องรีสตาร์ต (หน้าจอเกมที่เปิดอยู่จะอัปเดตเมื่อเปิดใหม่) ไอดีคงเดิม เพื่อน สโมสร และประวัติของคุณจึงยังใช้ได้ แต่คนเบื้องหลังเปลี่ยนไป กลับเป็นชุดแรก จะได้กลุ่มแรกคืนมา', vm_regen='สร้างใหม่', vm_reset='กลับเป็นชุดแรก', vm_gen_now='รุ่นที่ {0} (ตอนนี้ {1} คน)', vm_confirm='สร้างผู้จัดการเสมือนกลุ่มใหม่? ชื่อ สโมสร และสกวอดของทุกคนจะเปลี่ยนทันที', vm_regen_done='ใช้กลุ่มใหม่แล้ว', vm_reset_done='ใช้กลุ่มแรกอีกครั้งแล้ว',
        st_title='สตาฟ', st_lead='สตาฟในบัญชี: {0} จาก {1} สตาฟใหม่จะแสดงใน Tim > Manajemen Staff เมื่อเปลี่ยนหน้าจอ', st_staff='สตาฟ', st_grade='ดาว', st_life='สัญญา', st_status='สถานะ', st_add='เพิ่มสตาฟ', st_added='เพิ่ม {0} × {1} แล้ว', st_removed='ลบ {0} แล้ว', st_remove='ลบ', st_confirm='ลบสตาฟ {0} ออกจากบัญชี?', st_none='ยังไม่มีสตาฟ', st_helper='ผู้ช่วย', st_appointed='ประจำตำแหน่ง', st_studying='กำลังฝึก', e_unknown_staff='ไม่รู้จักสตาฟนี้', e_staff_full='สตาฟเต็ม: เหลือที่ {0}', e_no_staff='ไม่มีสตาฟนี้',
        phd_redo='สร้างหน้า 3D ทั้งหมดใหม่จากรูปไอคอน', phd_redo_confirm='สร้างหน้า 3D ที่มีอยู่ทั้งหมดใหม่จากรูปไอคอนของนักเตะ? จะเลือกหัวต้นแบบใหม่ (เทียบสีผิวกับรูปของนักเตะต้นแบบ) หน้าที่สร้างจากรูปที่อัปโหลดก็จะถูกแทนด้วยรูปไอคอน',
        t_PLAYER_GROWTH_RESET='รีเซ็ตแต้มแท็กติก (พัฒนานักเตะ)',
    ),
    "vi": dict(
        lang_name="Tiếng Việt", locale="vi-VN",
        ep_balance="Số dư EP", language="Ngôn ngữ", menu="Menu quản trị",
        nav_ringkasan="Tổng quan", nav_pemain="Cầu thủ CLB", nav_tambah="Thêm cầu thủ", nav_item="Gói & Bag", nav_shop="Cửa hàng",
        nav_harian="Quà hằng ngày", nav_scout="Tuyển trạch viên", nav_log="Nhật ký máy chủ",
        status_default="Thay đổi được lưu ngay trên máy chủ; game hiển thị sau khi mở lại hoặc khi màn hình CLB tải lại.",
        sum_title="Tổng quan CLB", sum_lead="Trạng thái tài khoản trên máy chủ lúc này.", ep_title="Số dư EP", amount="Số lượng",
        ep_add="Thêm EP", ep_sub="Trừ EP", ep_set="Đặt số dư bằng số này", coach_line="{0}, đội {1}",
        f_players="Cầu thủ trong CLB", f_team2="Trong Tim Cadangan (đội dự bị)", f_packs="Gói trong Item saya", f_bag="Quà trong Bag chưa nhận",
        f_streak="Đăng nhập liên tiếp", days="{0} ngày", f_today="Quà hôm nay", given="đã trao", waiting="chờ đăng nhập",
        f_vip="VIP + ưu đãi phòng máy đến", ep_empty="Hãy nhập số EP trước.", ep_now="Số dư EP hiện là {0}.",
        pl_lead="Đổi cấp thẻ, chuyển sang Tim Cadangan (đội dự bị, Tukar Scout chọn cầu thủ từ đây), hoặc giải phóng cầu thủ ngoài 18 người của đội hình. Thẻ mới từ gói, giao dịch và Market vào Tim inti (đội chính).",
        pl_filter="Tìm trong danh sách", ph_player="tên cầu thủ", pl_team2_clear="Chuyển tất cả Tim Cadangan về Tim inti",
        th_role="Vai trò", th_name="Tên", th_season="Mùa", th_value="Giá trị EP", th_team="Đội", th_pos="Vị trí",
        reserve="Dự bị", to_first="Về Tim inti", to_second="Sang Tim Cadangan", first_team="Tim inti", release="Giải phóng",
        no_player="Không có cầu thủ phù hợp.", confirm_release="Giải phóng {0} khỏi CLB? Không thể hoàn tác.",
        grade_saved="Đã lưu cấp thẻ.", moved_second="Đã chuyển sang Tim Cadangan.", moved_first="Đã về Tim inti.",
        released="Đã giải phóng {0} khỏi CLB.", team2_moved="{0} cầu thủ đã về Tim inti.",
        add_lead="Tìm thẻ trong cơ sở dữ liệu (chỉ số giống trong game và fifaaddict), rồi đưa thẳng vào CLB hoặc gửi qua Bag.",
        add_name="Tên cầu thủ", ph_messi="vd. Messi", search="Tìm", add_hint="Gõ ít nhất 2 chữ cái của tên cầu thủ.",
        on_sale="đang bán", not_on_sale="không bán", add_club="Thêm vào CLB", add_bag="Gửi qua Bag",
        no_card="Không có thẻ nào tên đó.", found="Tìm thấy {0} thẻ.", search_failed="Tìm thất bại: ",
        added="{0} +{1} đã vào CLB (Tim inti).", gifted="Đã gửi {0} +{1} vào Bag.",
        gift_player="Pemain: {0}",  # a Bag title the game shows: kept in the client's language
        item_title="Gói cầu thủ & Bag", item_lead="Gói và vật phẩm vào Item saya. Quà Bag vào Item saya → Items và nhận bằng nút Terima.",
        item_give="Tặng gói vào Item saya", pack="Gói", count="Số lượng", item_give_btn="Tặng", bag_title="Gửi quà vào Bag",
        title="Tiêu đề", pack_count="Số gói", bag_spid="spid cầu thủ", ph_optional="không bắt buộc", bag_send="Gửi vào Bag", no_pack="không có gói",
        given_packs="{0}× {1} đã vào Item saya.", gift_sent="Đã gửi quà \"{0}\" vào Bag.",
        shop_lead="Quản lý mọi tab của Cửa hàng: tạo gói, vật phẩm, gói quà và gói tháng mới bằng biểu tượng của client, sửa hoặc ẩn sản phẩm có sẵn, và đặt giá. Lưu trong {0}; game dùng khi mở Cửa hàng lần sau.",
        shop_free="Mọi sản phẩm miễn phí (0 EP)", shop_free_note="Giá cố định trong bảng vẫn được áp dụng.",
        kind_packs_tab="Gói cầu thủ", kind_items_tab="Vật phẩm tiêu hao", kind_packages_tab="Gói quà", kind_monthly_tab="Gói tháng", kind_lists_tab="Danh sách tab",
        kind_packs="gói", kind_items="vật phẩm", kind_packages="gói quà", kind_monthly="gói tháng",
        new_kind="Tạo {0} mới", edit_kind="Sửa {0}", shop_filter="Lọc", ph_shop_filter="tên hoặc itemid",
        th_icon="Biểu tượng", th_source="Nguồn", th_shop_price="Giá trong Cửa hàng", th_fixed_price="Giá cố định", th_shown="Hiển thị",
        src_builtin="Có sẵn", src_changed="Đã sửa", src_new="Mới", chained="Nối tiếp", edit="Sửa", delete="Xóa", restore="Khôi phục bản gốc",
        ph_default="mặc định", price_saved="Đã lưu giá của {0}.", price_reset="{0} dùng lại giá mặc định.",
        hidden_msg="Đã ẩn {0} khỏi Cửa hàng.", shown_msg="{0} hiện lại trong Cửa hàng.", free_on="Cửa hàng miễn phí.", free_off="Cửa hàng tính giá trở lại.",
        saved_product="Đã lưu {0} (itemid {1}).", deleted="Đã xóa {0}.", restored="{0} đã về bản gốc.",
        confirm_delete="Xóa {0}? Các bản đã có trong Item saya sẽ không dùng được nữa.", confirm_restore="Khôi phục {0} về bản gốc?",
        no_product="Không có sản phẩm phù hợp.",
        f_name="Tên", f_icon="Biểu tượng", icon_pick="Chọn biểu tượng", f_desc="Mô tả", f_seasons="Mùa", seasons_hint="để trống = mọi mùa",
        f_top="N thẻ tốt nhất", f_ovr="OVR tối thiểu (thẻ +1)", f_grade="Cấp tối đa (+1 … +N)", f_pos="Vị trí", any="tất cả",
        f_league="ID giải đấu", f_all="Mọi thẻ trên Market (không chỉ thẻ có bản quyền ID)", f_type="Loại",
        t_EP_LOTTERY="Thẻ EP (ngẫu nhiên)", t_ITEM_LOTTERY="Hộp ngẫu nhiên", t_COACHNAME_CHANGE="Đổi tên HLV", t_TEAM_CHANGE="Đổi đội",
        t_ENHANCEMENT_BOOST="Tăng tỉ lệ nâng cấp", t_LEAGUE_SIMULATION="Phiếu Mô phỏng giải đấu",
        t_PLAYER_EXP_CAPSULE="Viên EXP (EXP cầu thủ)", f_exp="EXP", f_maxlv="Cho cầu thủ đến cấp (trống = tất cả)", f_draft="Draft: chọn N cầu thủ, thẻ ở cấp tối đa (trống = gói thường)",
        e_draft_season="Gói draft phải có đúng một mùa giải", f_ep_min="EP tối thiểu", f_ep_max="EP tối đa", f_prizes="Phần thưởng (tối đa 10)",
        f_weight="Trọng số", f_prize="Phần thưởng", f_ep_amount="Số EP", add_prize="Thêm phần thưởng", f_boost="Tăng tỉ lệ nâng cấp (%)",
        f_contents="Nội dung", add_row="Thêm dòng", f_days="Thời hạn (ngày)", f_now="Nhận ngay khi mua", f_daily="Nhận mỗi ngày",
        remove="Xóa", save="Lưu", cancel="Hủy", none_type="Không cần nhập gì thêm.", icons_count="{0} biểu tượng từ client", ph_icon="tìm tên biểu tượng",
        lists_lead="Thứ tự sản phẩm trong các tab Rekomendasi (đề xuất), MP Shop, Bonus spesial (thưởng đặc biệt) và Paket berantai (gói nối tiếp).",
        list_new="Đề xuất: mới", list_best="Đề xuất: bán chạy", list_mp="MP Shop", list_special="Thưởng đặc biệt",
        list_chained="Gói nối tiếp", list_chained_hint="Mua theo thứ tự; mỗi dòng là một chuỗi gói.", list_chain_add="Thêm chuỗi",
        list_add="Thêm", lists_save="Lưu danh sách", lists_saved="Đã lưu danh sách tab.", empty="trống",
        daily_title="Quà đăng nhập hằng ngày", daily_lead="Lần đăng nhập đầu tiên mỗi ngày (WIB, UTC+7) đưa quà của ngày đó vào Bag. Đăng nhập liên tiếp thì sang ngày tiếp theo; bỏ một ngày thì bắt đầu lại từ ngày 1. Sau ngày cuối, chu kỳ quay lại từ đầu.",
        daily_grant="Trao quà ngày kế tiếp ngay", daily_reset="Đặt lại chuỗi đăng nhập", daily_table="Bảng quà", th_day="Ngày", day_n="Ngày {0}",
        drop_day="Xóa ngày", daily_add="Thêm ngày", daily_save="Lưu bảng", f_total="Tổng số ngày đăng nhập", f_last="Lần nhận quà gần nhất",
        never="chưa bao giờ", f_today_wib="Hôm nay (WIB)", daily_saved="Đã lưu bảng quà hằng ngày.", sent_bag="Đã gửi \"{0}\" vào Bag.",
        confirm_reset_streak="Đặt lại chuỗi đăng nhập về 0?", streak_reset="Đã đặt lại chuỗi đăng nhập.",
        nav_match="Thưởng sau trận", mr_title="Thưởng sau trận", mr_lead="Trao sau mỗi trận đã đá và mỗi lần mô phỏng Liga: EP và EXP theo kết quả, rồi ba thẻ thưởng. Thẻ người chơi lật được rút ngẫu nhiên từ danh sách thẻ theo trọng số. Lưu trong {0}; áp dụng từ trận tiếp theo.", mr_result="Theo kết quả", th_result="Kết quả", mr_win="Thắng", mr_draw="Hòa", mr_loss="Thua", mr_coach_exp="EXP huấn luyện viên", mr_player_exp="EXP mỗi cầu thủ", mr_cards="Thẻ thưởng", mr_chance="tỉ lệ {0}%", mr_saved="Đã lưu thưởng sau trận.",
        scout_title="Tuyển trạch viên (Tukar Scout)", scout_lead="Tuyển trạch viên vừa dùng sẽ bị khóa đến khi hết thời gian hồi.", th_available="Dùng lại được lúc",
        locked="đang khóa", ready="sẵn sàng", now="ngay bây giờ", no_quest="không có nhiệm vụ", scout_reset="Đặt lại thời gian hồi của mọi tuyển trạch viên",
        scouts_ready="Mọi tuyển trạch viên đã sẵn sàng.", q_value="giá trị cầu thủ ≥", q_grade="cấp ≥", q_count="số lượng", q_season="mùa",
        log_lead="Các dòng cuối của captures/http_config.log.", log_reload="Tải lại nhật ký", log_auto="Tự tải lại mỗi 3 giây",
        failed="Thất bại: ", load_failed="Không tải được: ",
        grade="Cấp", th_market="Thị trường", th_scout="Tuyển trạch viên", th_status="Trạng thái", th_quest="Nhiệm vụ", f_item="Vật phẩm", upgrade_point="Điểm nâng cấp",
        e_player_missing="không tìm thấy cầu thủ", e_number="{0} phải là số", e_range="{0} phải trong khoảng {1}..{2}",
        e_no_spid="không có spid này trong cơ sở dữ liệu", e_club_full="CLB đã đầy ({0} cầu thủ)",
        e_in_squad="cầu thủ đang trong 18 người của đội hình; hãy thay ra trong Formasi trước", e_squad_first="cầu thủ trong 18 người của đội hình phải ở Tim inti",
        e_unknown_item="không rõ gói hoặc vật phẩm", e_unknown_pack="không rõ gói", e_gift_empty="hãy nhập EP, gói hoặc cầu thủ",
        e_day="{0}: {1}", e_day_empty="trống", e_table_days="bảng phải có 1..31 ngày",
        e_pack_empty="gói {0} không có thẻ nào", e_length="{0} phải dài 1..{1} ký tự",
        e_no_icon="client không có biểu tượng {0}", e_pick_icon="hãy chọn biểu tượng",
        e_row_item="{0}: không rõ vật phẩm {1}", e_rows="{0} phải có {1}..{2} dòng",
        e_unknown_pos="không rõ vị trí", e_unknown_type="không rõ loại vật phẩm",
        e_ep_range="EP tối thiểu phải > 0 và <= EP tối đa", e_unknown_prize="không rõ phần thưởng {0}", e_prizes="phần thưởng phải có 1..10",
        e_unknown_kind="không rõ loại sản phẩm", e_product_missing="không tìm thấy sản phẩm", e_not_changed="sản phẩm này chưa được sửa",
        e_in_use="vẫn đang được {0} dùng", e_list_item="{0}: không đặt được vật phẩm {1} ở đây", e_chain_packages="gói nối tiếp chỉ chứa gói quà",
        nav_chat="Chat AI", chat_title="Chat của HLV ảo", chat_lead="Đối thủ Rank (HLV ảo) nhắn trong khung chat của trận: lúc giao bóng, khi có bàn thắng của hai bên, sút trượt, thủ môn cản phá và khi bạn gõ chat. Lời bình về kết quả hiện thành banner khi quay lại màn hình Rank. Mỗi HLV có tính cách riêng (thân thiện, hiếu thắng, hài hước, tự tin, tinh nghịch). Lưu trong {0}; áp dụng từ tin nhắn tiếp theo.", chat_on="Bật chat AI", chat_lang="Ngôn ngữ tin nhắn", chat_saved="Đã lưu cài đặt chat AI.", e_chat_lang="ngôn ngữ chat không hợp lệ", nav_missions="Nhiệm vụ", mis_title="Nhiệm vụ và thành tích", mis_lead="Nhiệm vụ hằng ngày (dòng Nhiệm vụ hằng ngày trong Manager > Danh sách thành tích, làm mới mỗi ngày lúc 00:00 WIB), thành tích lâu dài kèm huy hiệu (cấp HLV, số trận, số trận thắng, nâng cấp, cấp thẻ, giá trị đội, bàn thắng, Rank) và nhiệm vụ hằng tuần ở nút Quest (GP để đổi hộp quà; làm mới thứ Hai lúc 00:00 WIB). Máy chủ đếm các trận bạn chơi và các lần nâng cấp thành công. Thưởng EP theo hệ số giá cầu thủ ở tab Kinh tế. Lưu trong {0}; ngôn ngữ và nút Quest có hiệu lực sau khi mở lại game.", mis_on="Bật nhiệm vụ", mis_lang="Ngôn ngữ nhiệm vụ", mis_targets="Mục tiêu (x)", mis_rewards="Phần thưởng (x)", mis_saved="Đã lưu cài đặt nhiệm vụ.", e_mission_value="cài đặt nhiệm vụ không hợp lệ (mục tiêu 0.1-10, thưởng 0-10, ngôn ngữ id/en/vi)", wq_title="Nhiệm vụ hằng tuần", wq_lead="Danh sách nhiệm vụ được bốc ngẫu nhiên mỗi tuần (từ thứ Hai 00:00 WIB) cho nút Quest. Tên có thể để trống (tự đặt); {0} trong tên là mục tiêu. Mục tiêu và GP cũng theo hệ số ở trên. Áp dụng từ yêu cầu tiếp theo; nhiệm vụ đã xong trong tuần vẫn được giữ.", wq_th_stat="Loại", wq_th_todo="Mục tiêu", wq_th_name="Tên (tùy chọn)", wq_add="Thêm nhiệm vụ", wq_drop="Xóa", wq_count="Nhiệm vụ mỗi tuần", wq_shop_title="Hộp quà GP", wq_th_item="Vật phẩm", wq_th_price="Giá (GP)", wq_shop_add="Thêm hộp", wq_reset="Khôi phục mặc định", wq_saved="Đã lưu nhiệm vụ hằng tuần.", wq_reset_done="Nhiệm vụ hằng tuần và hộp quà đã về mặc định.", wq_confirm_reset="Khôi phục nhiệm vụ và hộp quà mặc định?", wq_this="Tuần này (đến {0})", wq_next="Tuần sau", e_quest_value="danh sách nhiệm vụ không hợp lệ (mục tiêu và GP 1-10.000, tên tối đa 60 ký tự, 1-6 nhiệm vụ mỗi tuần và không quá số trong danh sách)", e_quest_shop="hộp quà không hợp lệ (1-5 hộp, vật phẩm khác nhau, GP 1-100.000)", ptr_title="Phần thưởng thời gian chơi", ptr_lead="Mỗi khi tổng thời gian chơi hôm nay đạt mục tiêu sẽ nhận thưởng (ngày tính từ 00:00 WIB; khoảng nghỉ quá 20 phút không hoạt động không được tính). Thưởng vào Túi và hiện thành thông báo sự kiện trong game. Hôm nay: {0} phút, đã nhận {1} phần thưởng. Lưu trong {2}.", ptr_minutes="Phút", ptr_add="Thêm mục tiêu", ptr_saved="Đã lưu phần thưởng thời gian chơi.", e_ptr_rows="bảng thời gian chơi không hợp lệ (1-10 dòng, phút 1-1440 và tăng dần, mỗi dòng có EP hoặc gói)", news_region="Tin quốc gia (tùy chọn)", news_title="Chủ đề chat toàn cầu", news_lead="Các HLV ảo ở tab Channel luôn bàn tin bóng đá toàn cầu, cộng thêm các quốc gia được chọn: xu hướng Google hôm nay liên quan đến bóng đá và tiêu đề tin bóng đá Google News bằng ngôn ngữ đó (X, Instagram và TikTok cần đăng nhập, Reddit từ chối máy chủ). Mỗi HLV thích bàn về CLB, cầu thủ ngôi sao hoặc đất nước của mình (một trong các nước được chọn) khi có trong tin. Tự cập nhật mỗi ngày một lần. Lấy lúc: {0}.", news_none="Chưa có chủ đề (máy chủ offline hoặc vừa khởi động).", e_chat_region="khu vực tin tức không hợp lệ", lr_title="Phần thưởng Rank (xếp hạng)", lr_lead="Thưởng mùa Rank (mỗi tháng): thưởng chính theo hạng của chế độ có điểm cao nhất cuối mùa, thưởng phụ của mỗi chế độ khác đã chơi; vào Túi khi mùa mới bắt đầu. Thưởng tuần: mỗi mốc số trận thắng Rank trong tuần (thứ Hai đến Chủ nhật) đạt được sẽ vào Túi kèm popup ở màn hình Rank. Màn hình Rank cũng hiện các phần thưởng này. Lưu trong {0}.", lr_div="Hạng", lr_main="Thưởng chính", lr_sub="Thưởng phụ", lr_count="Số lượng", lr_weekly="Thưởng tuần", lr_wins="Thắng tuần này", lr_item="Phần thưởng", lr_add="Thêm mốc", lr_saved="Đã lưu phần thưởng Rank.", lr_confirm_reset="Khôi phục phần thưởng Rank mặc định?", lr_reset_done="Phần thưởng Rank đã về mặc định.", e_lr_rows="phần thưởng Rank không hợp lệ (EP 1-10.000.000.000, gói/vật phẩm 1-99; mốc tuần 1-500 trận thắng, tăng dần, tối đa 20)", kr_title="Phần thưởng King of the Hill", kr_lead="Thưởng tuần (tuần = ngày 1-7, 8-14, 15-21, 22-28) và thưởng mùa (mỗi tháng). Khi một tuần hoặc mùa bạn đã chơi kết thúc, màn hình phần thưởng hiện ra lúc mở King of the Hill và phần thưởng đạt được vào Túi. Tuần: mục tiêu số trận thắng hoặc số trận trong tuần đó, cộng thưởng hạng khi thắng ít nhất số trận đặt ra. Mùa: thưởng hạng cuối mùa khi thắng ít nhất số trận đặt ra, cộng thưởng thứ hạng trong khu vực. Màn hình King of the Hill cũng hiện các phần thưởng này. Lưu trong {0}; có hiệu lực ngay.", kr_type="Mục tiêu tuần", kr_type_winning="Thắng", kr_type_match="Số trận", kr_win="Thắng trong tuần đó", kr_match="Số trận trong tuần đó", kr_week_item="Thưởng mục tiêu tuần", kr_week_grade="Số trận thắng cho thưởng hạng tuần", kr_season_grade="Số trận thắng cho thưởng hạng mùa", kr_week_div="Thưởng hạng tuần", kr_season_div="Thưởng hạng mùa", kr_ranking="Thưởng thứ hạng mùa (khu vực)", kr_from="Từ hạng", kr_to="Đến hạng", kr_add="Thêm thứ hạng", kr_saved="Đã lưu phần thưởng King of the Hill.", kr_confirm_reset="Khôi phục phần thưởng King of the Hill mặc định?", kr_reset_done="Phần thưởng King of the Hill đã về mặc định.", e_kr_rows="phần thưởng King of the Hill không hợp lệ (EP 1-10.000.000.000, gói/vật phẩm 1-99; mục tiêu 1-100 trận thắng hoặc 1-200 trận, số trận thắng cho hạng 1-100 mỗi tuần và 1-500 mỗi mùa; 1-20 dòng thứ hạng trong khoảng 1-1000, tăng dần và không chồng nhau)",
        nav_notice='Thông báo', notice_title='Ảnh thông báo', notice_lead='Ảnh này hiện trong popup Thông báo mỗi lần đăng nhập (issue #36). Chọn JPG hoặc PNG; trang này chỉnh ảnh theo kích thước popup (800×720) trước khi lưu. Áp dụng từ lần đăng nhập sau, không cần sửa file client.', notice_file='Tệp ảnh (JPG/PNG)', notice_mode='Căn chỉnh', notice_fit='Hiện trọn ảnh (thêm nền)', notice_fill='Lấp đầy khung (cắt mép)', notice_bg='Màu nền', notice_save='Lưu thông báo', notice_delete='Xóa thông báo', notice_current='Thông báo hiện tại', notice_new='Xem trước', notice_none='Chưa có ảnh thông báo: popup hiện trống.', notice_saved='Đã lưu ảnh thông báo; sẽ hiện ở lần đăng nhập sau.', notice_deleted='Đã xóa ảnh thông báo.', notice_confirm_delete='Xóa ảnh thông báo?', notice_pick_first='Hãy chọn tệp ảnh trước.', e_notice_png='ảnh phải là PNG 800×720 (chọn lại tệp trên trang này)', e_notice_size='ảnh quá lớn (tối đa 8 MB)',
        nav_assets='Hình ảnh', as_title='Hình ảnh còn thiếu', as_lead='Game hiển thị ảnh cầu thủ, logo câu lạc bộ, ảnh áo đấu, logo giải đấu và ảnh nhân viên từ bộ nhớ đệm của client; ảnh nào thiếu sẽ hiện ảnh mặc định (bóng người, logo trống). Nút Tải xuống lấy các ảnh còn thiếu từ fifaaddict.com ở chế độ nền, mỗi nửa giây một ảnh; các tệp đã có không bị thay đổi. Ảnh cầu thủ có thể lên tới vài nghìn và mất nhiều thời gian; có thể dừng và tải tiếp bất cứ lúc nào. Ảnh mới hiện ra muộn nhất sau khi mở lại game. Ảnh cầu thủ fifaaddict.com không có sẽ lấy từ FC Online (FO4). Tải tất cả lấy mọi loại trong một lần và quét toàn bộ cơ sở dữ liệu thẻ; khi máy chủ không phản hồi, lượt tải chờ lâu hơn rồi tự tiếp tục.', as_all='Tất cả hình ảnh', as_fetch_all='Tải tất cả ảnh còn thiếu', as_kind='Loại', as_added='Đã tải', as_failed='Không có', as_faces='Ảnh cầu thủ', as_crests='Logo câu lạc bộ', as_uniforms='Ảnh áo đấu', as_leagues='Logo giải đấu', as_staffs='Ảnh nhân viên', as_careers='Lịch sử CLB', as_careers_note='Lịch sử CLB lấy sự nghiệp câu lạc bộ của cầu thủ đến mùa giải hiện tại từ Wikidata (năm, CLB, cho mượn) cho tab Đặc điểm trong thông tin cầu thủ. Xong thì bấm Cơ sở dữ liệu > Áp dụng vào game. Cầu thủ không tìm thấy giữ lịch sử gốc của FO3 (đến 2017).', as_fetch='Tải xuống', as_stop='Dừng', as_running='Đang tải: {0}', as_idle='Không có lượt tải nào đang chạy.', as_started='Đã bắt đầu tải.', as_stopped='Đã dừng tải.', e_asset_kind='loại ảnh không xác định', e_asset_busy='một lượt tải khác vẫn đang chạy',
        nav_prematch="Cài đặt trận", pm_title="Cài đặt trận đấu", pm_lead="Sân, thời gian, thời tiết và thời lượng cho các trận tiếp theo (Liga, Cup, Rank). Trong Multiple > Rank cũng đổi được ở màn hình cài đặt trận, và lựa chọn đó cũng được lưu tại đây; màn hình Liga và Cup chỉ hiển thị. Âm u: trời nhiều mây ban ngày, hoàng hôn ban đêm (màn hình Liga/Cup ghi là \"Salju\").", pm_stadium="Sân vận động", pm_tod="Thời gian", pm_weather="Thời tiết", pm_length="Thời lượng", pm_home="Sân của đội chủ nhà", pm_random="Ngẫu nhiên", pm_day="Ban ngày", pm_night="Ban đêm", pm_dry="Quang đãng", pm_rain="Mưa", pm_snow="Tuyết", pm_overcast="Âm u", pm_minutes="{0} phút", pm_ball="Bóng", pm_saved="Đã lưu cài đặt trận đấu.", e_pm_value="giá trị cài đặt trận đấu không hợp lệ",
        nav_economy="Kinh tế", eco_title="Kinh tế", eco_lead="Giá và EP cho mọi HLV. Dễ = như trước (Shop miễn phí). Thực tế: Shop tính phí (giá vật phẩm = giá trị bên trong, vật phẩm không chứa EP như tăng tỉ lệ hay viên EXP có giá cơ bản nhân hệ số giá cầu thủ; giá gói = giá trị các thẻ, +13% khi thị trường biến động vì gói có thể để dành mở vào ngày giá cao) và thị trường biến động. Khó: giá cầu thủ và giá gói x2, EP ban đầu ít. Thị trường biến động: giá mỗi cầu thủ lên xuống mỗi ngày, HLV ảo đôi khi bán rẻ, và bạn có thể đặt giá đến 105% (bán chậm hơn): mua rẻ, bán đắt. Luật chặt (Khó): Nâng cấp, Trao đổi và Tuyển trạch không tạo thêm EP: phí nâng cấp bằng giá trị nó thêm vào, khôi phục trả bằng EP, và đề nghị không vượt quá giá trị các thẻ đã đưa. Khi tắt (Dễ, Thực tế) dùng luật gốc của game: phí nâng cấp 2% giá trị thẻ, khôi phục bằng điểm nâng cấp, đề nghị không giới hạn. EP từ trận đấu chỉnh ở tab Thưởng sau trận; EP ban đầu áp dụng cho HLV mới.", eco_preset="Mẫu", eco_custom="Tùy chỉnh", eco_easy="Dễ (như trước)", eco_real="Thực tế", eco_hard="Khó", eco_shopfree="Shop miễn phí (0 EP)", eco_players="Giá cầu thủ (x)", eco_packs="Giá gói (x)", eco_start="EP ban đầu của HLV mới", eco_dynamic="Thị trường biến động (giá lên xuống, mua bán có lãi)", eco_strict="Luật chặt (Nâng cấp, Trao đổi và Tuyển trạch không tạo EP)", eco_saved="Đã lưu cài đặt kinh tế.", e_eco_value="giá trị kinh tế không hợp lệ (hệ số 0.1-100, EP ban đầu từ 0 trở lên)",
        nav_db='Cơ sở dữ liệu', db_title='Cơ sở dữ liệu thẻ và câu lạc bộ', db_lead='Sửa thẻ cầu thủ theo từng mùa: chỉ số, vị trí, chân thuận, chân không thuận, kỹ năng, tỷ lệ công/thủ, đặc điểm và câu lạc bộ (chuyển nhượng). Một thẻ = một spid = mùa × 1.000.000 + player id (Messi là 158023: thẻ mùa 91 của anh ấy là 91158023); tìm theo tên, spid hoặc player id. Thay đổi được ghi vào gamedata/fifadb.sqlite và lưu lại trong {0} (giá trị gốc và mới: có thể khôi phục, và bản build mùa FO4 sẽ áp dụng lại). Game đọc bản sao CSDL riêng: đóng game, bấm Áp dụng vào game, chờ xong rồi chạy play.bat.', db_apply='Áp dụng vào game', db_started='Đang cài vào game ở chế độ nền…', db_done='Đã cài xong. Chạy play.bat để khởi động lại server và game.', db_failed='Thất bại (mã {0}); xem nhật ký bên dưới.', as_install='Áp dụng vào game', db_search='Tìm thẻ', db_q='Tên, spid hoặc player id', db_hint='Nhập ít nhất 2 chữ cái của tên, hoặc spid / player id.', db_edited='Thẻ đã sửa', db_none='Chưa có thẻ nào được sửa.', th_changed='Đã sửa', f_club='Câu lạc bộ', ph_club='nhập tên câu lạc bộ', f_pos_n='Vị trí {0}', f_foot='Chân thuận', foot_right='Phải', foot_left='Trái', f_weak='Chân không thuận', f_skill='Kỹ năng', f_wr_att='Tỷ lệ tấn công', f_wr_def='Tỷ lệ phòng ngự', wr_low='Thấp', wr_med='Trung bình', wr_high='Cao', f_stats='Chỉ số', f_traits='Đặc điểm', orig_value='gốc: {0}', card_saved='Đã lưu {0}, OVR hiện là {1}. Bấm Áp dụng vào game để game dùng thay đổi.', crest_title='Logo câu lạc bộ', crest_lead='Chọn câu lạc bộ và ảnh (PNG nền trong suốt là đẹp nhất); trang này chỉnh về 256×256 và 128×128 rồi lưu vào bộ nhớ đệm của client. Logo gốc được giữ trong gamedata/crest_backup và có thể khôi phục. Hiển thị sau khi mở lại game.', crest_save='Lưu logo', crest_revert='Khôi phục logo gốc', crest_confirm='Khôi phục logo gốc của câu lạc bộ này?', crest_now='Logo hiện tại', crest_new='Logo mới', crest_none='Bộ nhớ đệm chưa có logo của câu lạc bộ này.', crest_pick='Hãy chọn câu lạc bộ và tệp ảnh trước.', crest_saved='Đã lưu logo; hiển thị sau khi mở lại game.', crest_reverted='Đã khôi phục logo gốc.', e_card_field='không thể sửa {0} ở đây', e_club='câu lạc bộ {0} không có trong game', e_not_edited='thẻ này chưa được sửa', e_crest_png='logo phải là PNG {0}×{0} (chọn lại tệp trên trang này)',
        f_look='Ngoại hình 3D', look_lead='Mã EA từ CSDL 3D của game (fifa_ng_db). Khuôn mặt = phần đầu của bất kỳ dòng 3D nào: danh sách gồm các thẻ của cầu thủ này; nhập tên cầu thủ khác để chọn đầu của họ từ danh sách, hoặc nhập spid / player id. Hiển thị trong game sau khi Áp dụng vào game.', look_generic='Đầu hiện là loại chung: có thể đổi kiểu và màu tóc (0 = vàng), râu, da và mắt.', look_scan='Đầu hiện là bản quét: tóc và râu đi theo đầu; muốn tóc khác hãy chọn khuôn mặt của đầu khác.', look_shared='Thẻ của mùa có mã trên 99 (26 LIVE, 25 LIVE) không có dòng 3D riêng: thay đổi này áp dụng cho mọi thẻ của cầu thủ này không có dòng riêng.', look_own='thẻ này', f_face='Khuôn mặt', l_hairtypecode='Kiểu tóc', l_haircolorcode='Màu tóc', l_facialhairtypecode='Râu', l_facialhaircolorcode='Màu râu', l_skintonecode='Màu da', l_eyecolorcode='Màu mắt', l_bodytypecode='Vóc dáng', e_no_engine='không đọc được CSDL 3D của game (data_0.big)', sn_title='Mùa tự tạo', sn_lead='Tạo mùa thẻ của riêng bạn: mã, tên và huy hiệu. Thêm thẻ bằng cách sao chép từ kết quả tìm kiếm (có thể cộng thêm cho mọi chỉ số), rồi sửa như các thẻ khác. Lưu trong {0}; Áp dụng vào game sẽ cài huy hiệu, và sau play.bat mùa này có gói Top 200 và màu đội riêng.', f_season_code='Mã mùa', th_badge='Huy hiệu', th_cards='Thẻ', sn_badge='Huy hiệu (PNG/JPG)', sn_save='Lưu mùa', sn_saved='Đã lưu mùa {0}.', sn_delete='Xóa mùa', sn_confirm_delete='Xóa mùa {0} cùng {1} thẻ? Thẻ của mùa này mà CLB đang sở hữu cũng biến mất khỏi CSDL.', sn_deleted='Đã xóa mùa {0}.', sn_copy_to='Sao chép sang mùa', f_boost_stats='Cộng mọi chỉ số', sn_copy='Sao chép', sn_copied='Đã sao chép {0} sang mùa {1} (spid {2}).', sn_none='Chưa có mùa tự tạo.', sn_remove='Xóa thẻ', sn_removed='Đã xóa thẻ {0}.', confirm_card_remove='Xóa thẻ {0} (spid {1}) khỏi mùa của nó?', sn_pick_badge='Hãy chọn huy hiệu trước.', mod_title='Mod: xuất và nhập', mod_lead='Lưu mọi mùa tự tạo, thẻ sao chép, thay đổi thẻ và màu đội tự tạo vào một tệp JSON, hoặc nạp tệp như vậy từ server khác. Sau khi nhập, bấm Áp dụng vào game.', mod_export='Xuất mod', mod_import='Nhập mod (JSON)', mod_imported='Đã nhập mod: {0} thành công, {1} thất bại', e_mod='không phải tệp mod FO3ID (JSON từ Xuất mod)', e_season_icon='huy hiệu phải là PNG tối đa 1 MB (chọn lại tệp trên trang này)', e_face='không có dòng 3D {0}', e_season='mùa {0} không phải mùa tự tạo', e_season_code='game đã dùng mã mùa {0}', e_card_exists='thẻ {0} đã tồn tại',
        nav_tc='Màu đội', tc_title='Màu đội tự tạo', tc_lead='Tạo màu đội của riêng bạn: thẻ thành viên được chọn bằng bộ lọc (mùa, CLB hiện tại hoặc cũ, quốc gia, cầu thủ, OVR tối thiểu, N thẻ Market tốt nhất); thẻ phải thỏa mọi bộ lọc đã điền. Các bậc giống màu đội đặc biệt có sẵn: 5-6 / 7-8 / 9-11 cầu thủ khác nhau trong đội hình chính (OVR +1/+2/+3, hoặc tới mức OVR đã chọn); cộng chỉ số được thêm ở mọi bậc. Dưới 5 cầu thủ: một bậc cho tất cả. Lưu trong {0}; có hiệu lực từ lần đăng nhập sau, không cần khởi động lại.', tc_members='Thành viên', tc_boost='Cộng chỉ số', tc_new='Màu đội mới', tc_edit='Sửa {0}', tc_nations='Quốc gia', tc_players='Cầu thủ', tc_min_ovr='OVR tối thiểu (0 = mọi)', tc_best='N thẻ tốt nhất (0 = tất cả)', tc_saved='Đã lưu {0}: {1} thẻ của {2} cầu thủ.', tc_deleted='Đã xóa {0}.', tc_confirm_delete='Xóa màu đội {0}?', tc_none='Chưa có màu đội tự tạo.', tc_all_cards='mọi thẻ', tc_all_pos='mọi vị trí', ph_pick='gõ rồi chọn', e_nation='không rõ quốc gia {0}', e_player_id='không có cầu thủ có player id {0}', e_color_missing='không tìm thấy màu đội', e_tc_value='giá trị màu đội không hợp lệ',
        koth_title='Khu vực King of the Hill', koth_lead='Chọn khu vực King of the Hill dùng: các thành phố của Indonesia, Singapore / Malaysia, Thái Lan, Việt Nam (tên tiếng Việt có dấu) hoặc khu vực tự tạo của bạn. Sau khi đổi, các quản lý ảo phân bố sang các thành phố của khu vực mới và khu vực bạn đã chọn sẽ trống: hãy chọn lại khi mở King of the Hill. Không cần khởi động lại.', koth_regions='Khu vực', koth_id='Indonesia', koth_sgmy='Singapore / Malaysia', koth_th='Thái Lan', koth_vn='Việt Nam', koth_custom='Tùy chỉnh', koth_saved='Đã lưu khu vực King of the Hill.', e_koth_set='khu vực không xác định', koth_custom_title='Khu vực tùy chỉnh', koth_custom_lead='Viết khu vực của riêng bạn, mỗi dòng một vùng: Vùng: thành phố, thành phố, thành phố (ví dụ Miền Bắc: Hà Nội, Hải Phòng, Sa Pa). 1-20 vùng, mỗi vùng 1-30 thành phố, tên tối đa 24 ký tự và không trùng. Có thể sao chép một khu vực có sẵn rồi sửa. Thành phố giữ tên thì giữ ID, nên người chơi ở đó vẫn ở nguyên. Lưu rồi chọn Tùy chỉnh ở trên. Lưu trong {0}; có hiệu lực ngay.', koth_copy='Sao chép từ', koth_copy_btn='Sao chép vào trình soạn', koth_custom_save='Lưu khu vực tùy chỉnh', koth_custom_saved='Đã lưu khu vực tùy chỉnh: {0} vùng, {1} thành phố.', e_koth_line='dòng {0}: hãy viết Vùng: thành phố, thành phố', e_koth_name='tên trống, quá 24 ký tự hoặc bị trùng: {0}', e_koth_count='cần 1-20 vùng, mỗi vùng 1-30 thành phố', e_koth_empty='khu vực tùy chỉnh đang trống: hãy viết và lưu trước',
        face_title='Ảnh cầu thủ', face_lead='Ảnh của thẻ trong game (128×128). Game tìm ảnh của thẻ (p<spid>.png, ảnh của mùa đó) trước, rồi đến ảnh của cầu thủ (p<player id>.png); nếu không có sẽ hiện khuôn mặt mặc định. Tab Hình ảnh tải các ảnh còn thiếu từ fifaaddict.com, kể cả ảnh từng mùa. Tại đây bạn có thể thay ảnh cho mọi thẻ của cầu thủ này hoặc chỉ thẻ này: bằng ảnh của mình (PNG/JPG; trang này chỉnh về 128×128) hoặc bằng ảnh của spid / player id khác (từ bộ nhớ đệm, hoặc lấy thẳng từ fifaaddict.com nếu chưa có). Lưu trong gamedata/faces, không sửa tệp bộ nhớ đệm của client; Khôi phục sẽ xóa ảnh đó. Hiển thị sau khi mở lại game.', face_for='Áp dụng cho', face_player='Mọi thẻ của cầu thủ này (player id {0})', face_card='Chỉ thẻ này (spid {0})', face_save='Lưu ảnh', face_revert='Khôi phục ảnh gốc', face_confirm='Xóa ảnh tự đặt này và dùng lại ảnh gốc?', face_now='Ảnh trong game', face_new='Ảnh mới', face_none='Chưa có ảnh: game hiện khuôn mặt mặc định.', face_pick='Hãy chọn tệp ảnh trước.', face_saved='Đã lưu ảnh; hiển thị sau khi mở lại game.', face_reverted='Đã khôi phục ảnh gốc.', e_face_png='ảnh phải là PNG {0}×{0} (chọn lại tệp trên trang này)', head_scan='đầu quét', head_generic='đầu chung', face_from='Ảnh của spid / player id', face_use='Dùng ảnh này', face_used='Đã dùng ảnh {0}; hiển thị sau khi mở lại game.', e_face_none='không có ảnh {0} trong bộ nhớ đệm lẫn trên fifaaddict.com', e_face_fetch='fifaaddict.com không phản hồi ({0}); hãy thử lại sau',
        phd_title='Khuôn mặt 3D từ ảnh', phd_lead='Dành cho cầu thủ có khuôn mặt chung: ảnh chụp cận mặt từ phía trước trở thành da mặt trên đầu 3D đã quét của một cầu thủ khác (người cho: hình đầu và tóc đi kèm, mắt vẫn là của người cho). Chọn ảnh (mặt nhìn thẳng, ánh sáng đều), bấm 11 điểm theo thứ tự, chọn người cho trông giống (màu da, tóc; không râu nếu cầu thủ không có râu), rồi bấm Tạo. Áp dụng cho mọi thẻ của cầu thủ này dùng dòng player id của anh ấy hoặc đầu chung. Lưu trong gamedata/photo_heads; hiển thị sau khi bấm Cơ sở dữ liệu > Áp dụng vào game và mở lại game.', phd_donor='Đầu người cho (dòng engine)', phd_donor_hint='gõ tên cầu thủ có đầu quét', phd_step='Bấm điểm {0}/{1}: {2}', phd_all='Đã đánh dấu đủ {0} điểm; bấm gần một điểm để dời nó.', phd_undo='Bỏ điểm', phd_reset='Xóa mọi điểm', phd_photo='Ảnh và các điểm', phd_now='Texture mặt 3D', phd_none='Chưa có khuôn mặt 3D từ ảnh.', phd_make='Tạo mặt 3D', phd_delete='Xóa mặt 3D', phd_confirm='Xóa khuôn mặt 3D từ ảnh của cầu thủ này?', phd_made='Đã tạo mặt 3D. Bấm Cơ sở dữ liệu > Áp dụng vào game (khi game đã đóng), rồi mở game.', phd_deleted='Đã xóa mặt 3D; Áp dụng vào game để gỡ khỏi client.', phd_pick='Hãy chọn ảnh trước.', pt_eye_r='giữa mắt phải của cầu thủ (bên trái ảnh)', pt_eye_l='giữa mắt trái của cầu thủ (bên phải ảnh)', pt_nose='chóp mũi', pt_mouth_r='khóe miệng phải (bên trái ảnh)', pt_mouth_l='khóe miệng trái (bên phải ảnh)', pt_chin='đáy cằm', pt_forehead='giữa trán', pt_temple_r='thái dương phải: mép mặt ngang lông mày (bên trái ảnh)', pt_temple_l='thái dương trái: mép mặt ngang lông mày (bên phải ảnh)', pt_jaw_r='hàm phải: mép mặt ngay dưới miệng (bên trái ảnh)', pt_jaw_l='hàm trái: mép mặt ngay dưới miệng (bên phải ảnh)', e_ph_png='ảnh phải là PNG tối đa {0} điểm ảnh (chọn lại tệp trên trang này)', e_ph_points='hãy đánh dấu đủ {0} điểm trên ảnh trước', e_ph_donor='dòng {0} không phải đầu quét: hãy chọn cầu thủ có đầu quét làm người cho', e_ph_files='client không có tệp của đầu {0}',
        phd_icon='Lấy từ ảnh cầu thủ', phd_icon_done='Đã điền ảnh, 11 điểm và người cho từ ảnh của cầu thủ. Hãy kiểm tra các điểm (bấm gần một điểm để dời nó), rồi Tạo mặt 3D.', phd_icon_nopts='không tìm thấy khuôn mặt trong ảnh: hãy tự bấm các điểm', e_ph_icon='không có ảnh cầu thủ {0} trong bộ nhớ đệm lẫn trên fifaaddict.com', phd_club='Tạo mặt 3D từ ảnh cho các cầu thủ mặt chung trong CLB', phd_club_lead='Mặt 3D tự động: mọi cầu thủ trong CLB vẫn dùng mặt chung và có ảnh trong bộ nhớ đệm (ảnh FO3; tab Hình ảnh tải các ảnh còn thiếu) sẽ có mặt 3D từ ảnh của mình. Bộ dò YuNet tìm các điểm trên mặt, người cho được chọn theo màu da và tóc. Lần đầu mất khoảng một phút (đọc mọi đầu quét một lần). Kết quả của từng cầu thủ có thể kiểm tra và đổi trong hộp thoại thẻ; rồi Áp dụng vào game.', phd_club_busy='Đang tạo mặt 3D…', phd_club_done='Đã tạo {0} mặt 3D, bỏ qua {1}.', phd_skip_icon='không có ảnh', phd_skip_face='không tìm thấy mặt',
        world_title='Thế giới game', world_lead='Hiện đại: các CLB AI, phần chọn CLB và gói This Season dùng thẻ 26 LIVE (khi đã cài). Cổ điển FO3: mọi CLB dùng đội hình 2017 gốc của FO3, các quản lý ảo chỉ dùng mùa gốc của FO3, còn các mùa thêm vào (25 LIVE, 26 LIVE, WC18, mùa tự tạo) không có trong gói, Market hay giao dịch: chỉ trang quản trị mới trao được (Thêm cầu thủ, Bag). CLB và bộ sưu tập của bạn giữ nguyên. Có hiệu lực sau khi khởi động lại server (play.bat).', world_label='Chế độ', world_modern='Hiện đại (26 LIVE)', world_classic='Cổ điển FO3 (2017)', world_saved='Đã lưu thế giới game.', world_restart='Server vẫn chạy chế độ cũ: chạy play.bat để áp dụng lựa chọn này.', e_world='chế độ thế giới không xác định',
        vm_title='Quản lý ảo', vm_lead='Khoảng 2.000 quản lý ảo (đối thủ Rank và King of the Hill, CLB AI, Market) giữ nguyên mỗi lần chạy game và server. Chỉ nút Tạo lại ở đây mới tạo ra nhóm mới: tên, cấp, CLB, đội hình, lối chơi, điểm Rank, khu vực và CLB AI đổi ngay, không cần khởi động lại (màn hình game đang mở sẽ cập nhật khi mở lại). ID của họ giữ nguyên nên bạn bè, CLB và lịch sử của bạn vẫn dùng được, nhưng người đứng sau thì khác. Về nhóm đầu tiên sẽ đưa nhóm ban đầu trở lại.', vm_regen='Tạo lại', vm_reset='Về nhóm đầu tiên', vm_gen_now='Thế hệ {0} (hiện có {1} quản lý).', vm_confirm='Tạo nhóm quản lý ảo mới? Tên, CLB và đội hình của tất cả sẽ đổi ngay.', vm_regen_done='Đang dùng nhóm mới.', vm_reset_done='Đang dùng lại nhóm đầu tiên.',
        st_title='Nhân viên', st_lead='Nhân viên trong tài khoản: {0} / {1}. Nhân viên mới hiện ở Tim > Manajemen Staff khi đổi màn hình.', st_staff='Nhân viên', st_grade='Sao', st_life='Hợp đồng', st_status='Trạng thái', st_add='Thêm nhân viên', st_added='Đã thêm {0} × {1}.', st_removed='Đã xóa {0}.', st_remove='Xóa', st_confirm='Xóa nhân viên {0} khỏi tài khoản?', st_none='Chưa có nhân viên.', st_helper='trợ lý', st_appointed='Đang làm việc', st_studying='Đang đào tạo', e_unknown_staff='nhân viên không xác định', e_staff_full='đã đủ nhân viên: còn {0} chỗ', e_no_staff='không có nhân viên này',
        phd_redo='Tạo lại mọi mặt 3D từ ảnh đại diện', phd_redo_confirm='Tạo lại mọi mặt 3D đã có từ ảnh đại diện của cầu thủ? Đầu mẫu được chọn lại (so màu da với ảnh của cầu thủ mẫu). Mặt làm từ ảnh tải lên cũng bị thay bằng ảnh đại diện.',
        t_PLAYER_GROWTH_RESET='Đặt lại điểm chiến thuật (Phát triển cầu thủ)',
    ),
    "ko": dict(
        lang_name="한국어", locale="ko-KR",
        ep_balance="EP 잔액", language="언어", menu="관리자 메뉴",
        nav_ringkasan="개요", nav_pemain="구단 선수", nav_tambah="선수 추가", nav_item="팩 & Bag", nav_shop="상점",
        nav_harian="출석 보상", nav_scout="스카우트", nav_log="서버 로그",
        status_default="변경 사항은 서버에 바로 저장됩니다. 게임에는 다시 실행하거나 구단 화면을 다시 불러온 뒤 표시됩니다.",
        sum_title="구단 개요", sum_lead="지금 서버에 있는 계정 상태입니다.", ep_title="EP 잔액", amount="수량",
        ep_add="EP 추가", ep_sub="EP 차감", ep_set="이 금액으로 설정", coach_line="{0}, 팀 {1}",
        f_players="구단 선수", f_team2="Tim Cadangan(2군)에 있음", f_packs="Item saya의 팩", f_bag="Bag에서 받지 않은 선물",
        f_streak="연속 로그인", days="{0}일", f_today="오늘의 보상", given="지급됨", waiting="로그인 대기 중",
        f_vip="VIP + PC방 혜택 만료일", ep_empty="먼저 EP 수량을 입력하세요.", ep_now="현재 EP 잔액: {0}",
        pl_lead="카드의 강화 등급을 바꾸거나, Tim Cadangan(2군, Tukar Scout가 여기서 선수를 고름)으로 옮기거나, 18명 스쿼드 밖의 선수를 방출합니다. 팩, 트레이드, Market에서 얻은 새 카드는 Tim inti(1군)에 들어갑니다.",
        pl_filter="목록에서 찾기", ph_player="선수 이름", pl_team2_clear="Tim Cadangan 카드를 모두 Tim inti로",
        th_role="역할", th_name="이름", th_season="시즌", th_value="EP 가치", th_team="팀", th_pos="포지션",
        reserve="2군", to_first="Tim inti로", to_second="Tim Cadangan으로", first_team="Tim inti", release="방출",
        no_player="일치하는 선수가 없습니다.", confirm_release="{0} 선수를 구단에서 방출할까요? 되돌릴 수 없습니다.",
        grade_saved="강화 등급을 저장했습니다.", moved_second="Tim Cadangan으로 옮겼습니다.", moved_first="Tim inti로 돌아왔습니다.",
        released="{0} 선수를 구단에서 방출했습니다.", team2_moved="{0}명이 Tim inti로 돌아왔습니다.",
        add_lead="카드 데이터베이스에서 검색한 뒤(능력치는 게임 및 fifaaddict와 같음) 구단에 바로 넣거나 Bag으로 보냅니다.",
        add_name="선수 이름", ph_messi="예: Messi", search="검색", add_hint="선수 이름을 2글자 이상 입력하세요.",
        on_sale="판매 중", not_on_sale="판매 안 함", add_club="구단에 추가", add_bag="Bag으로 보내기",
        no_card="그 이름의 카드가 없습니다.", found="카드 {0}장을 찾았습니다.", search_failed="검색 실패: ",
        added="{0} +{1} 선수가 구단(Tim inti)에 들어왔습니다.", gifted="{0} +{1} 선수를 Bag으로 보냈습니다.",
        gift_player="Pemain: {0}",  # a Bag title the game shows: kept in the client's language
        item_title="선수 팩 & Bag", item_lead="팩과 아이템은 Item saya로 들어갑니다. Bag 선물은 Item saya → Items에 들어가며 Terima 버튼으로 받습니다.",
        item_give="Item saya에 팩 지급", pack="팩", count="수량", item_give_btn="지급", bag_title="Bag으로 선물 보내기",
        title="제목", pack_count="팩 수량", bag_spid="선수 spid", ph_optional="선택", bag_send="Bag으로 보내기", no_pack="팩 없음",
        given_packs="{0}× {1}: Item saya에 지급했습니다.", gift_sent="선물 \"{0}\"을(를) Bag으로 보냈습니다.",
        shop_lead="상점의 모든 탭을 관리합니다. 클라이언트 아이콘으로 새 팩, 아이템, 패키지, 월정액 패키지를 만들고, 기본 상품을 수정하거나 숨기고, 가격을 정합니다. {0}에 저장되며 게임은 다음에 상점을 열 때 사용합니다.",
        shop_free="모든 상품 무료(0 EP)", shop_free_note="표의 고정 가격은 그대로 적용됩니다.",
        kind_packs_tab="선수 팩", kind_items_tab="소모품", kind_packages_tab="패키지", kind_monthly_tab="월정액 패키지", kind_lists_tab="탭 목록",
        kind_packs="팩", kind_items="아이템", kind_packages="패키지", kind_monthly="월정액 패키지",
        new_kind="새 {0}", edit_kind="{0} 수정", shop_filter="찾기", ph_shop_filter="이름 또는 itemid",
        th_icon="아이콘", th_source="출처", th_shop_price="상점 가격", th_fixed_price="고정 가격", th_shown="표시",
        src_builtin="기본", src_changed="수정됨", src_new="새 상품", chained="연속", edit="수정", delete="삭제", restore="원래대로",
        ph_default="기본값", price_saved="{0} 가격을 저장했습니다.", price_reset="{0}: 기본 가격으로 돌아갔습니다.",
        hidden_msg="{0}: 상점에서 숨겼습니다.", shown_msg="{0}: 상점에 다시 표시됩니다.", free_on="상점이 무료가 되었습니다.", free_off="상점이 다시 가격을 받습니다.",
        saved_product="{0} 저장됨(itemid {1}).", deleted="{0} 삭제됨.", restored="{0}: 원래대로 되돌렸습니다.",
        confirm_delete="{0}을(를) 삭제할까요? Item saya에 이미 있는 것은 더 이상 쓸 수 없습니다.", confirm_restore="{0}을(를) 원래대로 되돌릴까요?",
        no_product="일치하는 상품이 없습니다.",
        f_name="이름", f_icon="아이콘", icon_pick="아이콘 선택", f_desc="설명", f_seasons="시즌", seasons_hint="비우면 모든 시즌",
        f_top="상위 N장", f_ovr="최소 OVR(+1 카드 기준)", f_grade="최대 강화 등급(+1 … +N)", f_pos="포지션", any="전체",
        f_league="리그 ID", f_all="Market의 모든 카드(ID 라이선스 카드만이 아님)", f_type="종류",
        t_EP_LOTTERY="EP 카드(랜덤)", t_ITEM_LOTTERY="랜덤 박스", t_COACHNAME_CHANGE="감독 이름 변경", t_TEAM_CHANGE="팀 변경",
        t_ENHANCEMENT_BOOST="강화 부스트", t_LEAGUE_SIMULATION="리그 시뮬레이션 쿠폰",
        t_PLAYER_EXP_CAPSULE="경험치 캡슐(선수 경험치)", f_exp="경험치", f_maxlv="사용 가능한 선수 최대 레벨(비우면 전체)", f_draft="드래프트: 선수 N명 선택, 카드는 최대 강화 등급(비우면 일반 팩)",
        e_draft_season="드래프트 팩은 시즌을 하나만 골라야 합니다", f_ep_min="최소 EP", f_ep_max="최대 EP", f_prizes="보상(최대 10개)",
        f_weight="가중치", f_prize="보상", f_ep_amount="EP 수량", add_prize="보상 추가", f_boost="강화 확률 부스트(%)",
        f_contents="구성", add_row="행 추가", f_days="기간(일)", f_now="구매 즉시 지급", f_daily="매일 지급",
        remove="삭제", save="저장", cancel="취소", none_type="더 입력할 것이 없습니다.", icons_count="클라이언트 아이콘 {0}개", ph_icon="아이콘 이름 검색",
        lists_lead="Rekomendasi(추천), MP Shop, Bonus spesial(특별 보너스), Paket berantai(연속 패키지) 탭의 상품 순서입니다.",
        list_new="추천: 신상품", list_best="추천: 인기 상품", list_mp="MP Shop", list_special="특별 보너스",
        list_chained="연속 패키지", list_chained_hint="순서대로 구매합니다. 한 행이 패키지 한 묶음입니다.", list_chain_add="묶음 추가",
        list_add="추가", lists_save="목록 저장", lists_saved="탭 목록을 저장했습니다.", empty="비어 있음",
        daily_title="출석 보상", daily_lead="매일(WIB, UTC+7) 첫 로그인 때 그날의 보상이 Bag으로 들어갑니다. 연속으로 로그인하면 다음 날로 넘어가고, 하루를 빠지면 1일차부터 다시 시작합니다. 마지막 날 다음에는 처음부터 다시 시작합니다.",
        daily_grant="다음 날 보상을 지금 지급", daily_reset="연속 로그인 초기화", daily_table="보상 표", th_day="일차", day_n="{0}일차",
        drop_day="이 날 삭제", daily_add="날 추가", daily_save="표 저장", f_total="총 로그인 일수", f_last="마지막 보상",
        never="없음", f_today_wib="오늘(WIB)", daily_saved="출석 보상 표를 저장했습니다.", sent_bag="\"{0}\"을(를) Bag으로 보냈습니다.",
        confirm_reset_streak="연속 로그인을 0으로 초기화할까요?", streak_reset="연속 로그인을 초기화했습니다.",
        nav_match="경기 보상", mr_title="경기 보상", mr_lead="플레이한 모든 경기와 리그 시뮬레이션 뒤에 지급됩니다: 결과에 따른 EP와 경험치, 그리고 보상 카드 3장. 선수가 여는 카드는 카드 목록에서 가중치에 따라 무작위로 뽑힙니다. {0}에 저장되며 다음 경기부터 적용됩니다.", mr_result="결과별", th_result="결과", mr_win="승", mr_draw="무", mr_loss="패", mr_coach_exp="감독 경험치", mr_player_exp="선수당 경험치", mr_cards="보상 카드", mr_chance="확률 {0}%", mr_saved="경기 보상을 저장했습니다.",
        scout_title="스카우트(Tukar Scout)", scout_lead="방금 쓴 스카우트는 쿨다운이 끝날 때까지 잠깁니다.", th_available="다시 사용 가능",
        locked="잠김", ready="사용 가능", now="지금", no_quest="퀘스트 없음", scout_reset="모든 스카우트 쿨다운 초기화",
        scouts_ready="모든 스카우트를 다시 쓸 수 있습니다.", q_value="선수 가치 ≥", q_grade="강화 등급 ≥", q_count="수량", q_season="시즌",
        log_lead="captures/http_config.log의 마지막 줄입니다.", log_reload="로그 새로고침", log_auto="3초마다 자동 새로고침",
        failed="실패: ", load_failed="불러오기 실패: ",
        grade="강화 등급", th_market="이적시장", th_scout="스카우트", th_status="상태", th_quest="퀘스트", f_item="아이템", upgrade_point="강화 포인트",
        e_player_missing="선수를 찾을 수 없습니다", e_number="{0}: 숫자여야 합니다", e_range="{0}: {1}..{2} 사이여야 합니다",
        e_no_spid="데이터베이스에 없는 spid입니다", e_club_full="구단이 가득 찼습니다({0}명)",
        e_in_squad="18명 스쿼드에 있는 선수입니다. 먼저 Formasi에서 교체하세요", e_squad_first="18명 스쿼드의 선수는 Tim inti에 있어야 합니다",
        e_unknown_item="알 수 없는 팩 또는 아이템", e_unknown_pack="알 수 없는 팩", e_gift_empty="EP, 팩 또는 선수를 입력하세요",
        e_day="{0}: {1}", e_day_empty="비어 있음", e_table_days="표는 1..31일이어야 합니다",
        e_pack_empty="팩 {0}에 카드가 한 장도 없습니다", e_length="{0}: 1..{1}자여야 합니다",
        e_no_icon="클라이언트에 아이콘 {0}이(가) 없습니다", e_pick_icon="아이콘을 선택하세요",
        e_row_item="{0}: 알 수 없는 아이템 {1}", e_rows="{0}: {1}..{2}행이어야 합니다",
        e_unknown_pos="알 수 없는 포지션", e_unknown_type="알 수 없는 아이템 종류",
        e_ep_range="최소 EP는 0보다 크고 최대 EP 이하여야 합니다", e_unknown_prize="알 수 없는 보상 {0}", e_prizes="보상은 1..10개여야 합니다",
        e_unknown_kind="알 수 없는 상품 종류", e_product_missing="상품을 찾을 수 없습니다", e_not_changed="수정된 적 없는 상품입니다",
        e_in_use="아직 {0}에서 사용 중입니다", e_list_item="{0}: 아이템 {1}은(는) 여기에 넣을 수 없습니다", e_chain_packages="연속 패키지에는 패키지만 넣을 수 있습니다",
        nav_chat="AI 채팅", chat_title="가상 감독 채팅", chat_lead="랭크 상대(가상 감독)가 경기 채팅에 메시지를 보냅니다: 킥오프, 양 팀의 골, 빗나간 슈팅, 골키퍼 선방, 그리고 당신이 채팅을 입력할 때. 결과에 대한 한마디는 랭크 화면으로 돌아오면 배너로 표시됩니다. 감독마다 성격이 있습니다(친절함, 승부욕, 유머, 자신감, 장난기). {0}에 저장되며 다음 메시지부터 적용됩니다.", chat_on="AI 채팅 켜기", chat_lang="메시지 언어", chat_saved="AI 채팅 설정을 저장했습니다.", e_chat_lang="알 수 없는 채팅 언어", nav_missions="미션", mis_title="미션과 업적", mis_lead="일일 미션(Manager > 업적 목록의 일일 미션 줄, 매일 00:00 WIB에 초기화), 배지가 있는 영구 업적(감독 레벨, 경기, 승리, 강화, 등급, 팀 가치, 골, 랭크), 퀘스트 버튼의 주간 퀘스트(보상 상자에 쓰는 GP, 매주 월요일 00:00 WIB에 초기화). 서버가 직접 플레이한 경기와 성공한 강화를 셉니다. EP 보상은 경제 탭의 선수 가격 배율을 따릅니다. {0}에 저장되며, 언어와 퀘스트 버튼은 게임을 다시 켠 뒤 적용됩니다.", mis_on="미션 켜기", mis_lang="미션 언어", mis_targets="목표 (x)", mis_rewards="보상 (x)", mis_saved="미션 설정을 저장했습니다.", e_mission_value="미션 설정이 올바르지 않음(목표 0.1-10, 보상 0-10, 언어 id/en/vi)", wq_title="주간 퀘스트", wq_lead="매주(월요일 00:00 WIB부터) 퀘스트 버튼에 나올 퀘스트를 이 목록에서 뽑습니다. 이름은 비워 두면 자동으로 정해지며, 이름의 {0}은 목표입니다. 목표와 GP도 위의 배율을 따릅니다. 다음 요청부터 적용되며, 이번 주에 이미 완료한 퀘스트는 완료 상태로 남습니다.", wq_th_stat="종류", wq_th_todo="목표", wq_th_name="이름(선택)", wq_add="퀘스트 추가", wq_drop="삭제", wq_count="주당 퀘스트 수", wq_shop_title="GP 보상 상자", wq_th_item="아이템", wq_th_price="가격(GP)", wq_shop_add="상자 추가", wq_reset="기본값으로", wq_saved="주간 퀘스트를 저장했습니다.", wq_reset_done="주간 퀘스트와 보상 상자를 기본값으로 되돌렸습니다.", wq_confirm_reset="기본 퀘스트와 보상 상자로 되돌릴까요?", wq_this="이번 주({0}까지)", wq_next="다음 주", e_quest_value="퀘스트 목록이 올바르지 않음(목표와 GP 1-10,000, 이름 최대 60자, 주당 1-6개이며 목록 수 이하)", e_quest_shop="보상 상자가 올바르지 않음(1-5개, 아이템 중복 불가, GP 1-100,000)", ptr_title="플레이 시간 보상", ptr_lead="오늘의 총 플레이 시간이 목표에 도달할 때마다 보상을 줍니다(하루는 00:00 WIB부터 계산하며, 20분 넘게 활동이 없는 시간은 제외). 보상은 가방으로 들어가고 게임에서 이벤트 알림으로 표시됩니다. 오늘: {0}분, 받은 보상 {1}개. {2}에 저장됩니다.", ptr_minutes="분", ptr_add="목표 추가", ptr_saved="플레이 시간 보상을 저장했습니다.", e_ptr_rows="플레이 시간 표가 올바르지 않음(1-10줄, 분은 1-1440이며 계속 증가, 각 줄에 EP 또는 팩 필요)", news_region="국가 뉴스(선택)", news_title="글로벌 채팅 주제", news_lead="Channel 탭의 가상 감독들은 항상 세계 축구 뉴스를 이야기하고, 체크한 나라의 뉴스도 더합니다: 오늘 뜨는 구글 검색어 중 축구 관련 항목과 그 언어의 구글 뉴스 축구 헤드라인(X, 인스타그램, 틱톡은 로그인이 필요하고 레딧은 서버 접근을 거부). 감독마다 뉴스에 나온 자기 클럽, 스타 선수, 나라(체크한 나라 중 하나) 이야기를 더 좋아합니다. 하루에 한 번 자동 갱신. 가져온 시각: {0}.", news_none="아직 주제가 없습니다(서버가 오프라인이거나 막 시작됨).", e_chat_region="알 수 없는 뉴스 지역", lr_title="랭크 보상(랭킹)", lr_lead="랭크 시즌 보상(매월): 시즌 종료 시 레이팅이 가장 높은 모드의 디비전 주 보상과, 플레이한 다른 각 모드의 부 보상; 새 시즌이 시작되면 가방으로 들어갑니다. 주간 보상: 이번 주(월요일-일요일) 랭크 승리 수가 목표에 도달할 때마다 랭크 화면의 팝업과 함께 가방으로 들어갑니다. 랭크 화면에도 이 보상이 표시됩니다. {0}에 저장됩니다.", lr_div="디비전", lr_main="주 보상", lr_sub="부 보상", lr_count="수량", lr_weekly="주간 보상", lr_wins="이번 주 승리", lr_item="보상", lr_add="단계 추가", lr_saved="랭크 보상을 저장했습니다.", lr_confirm_reset="기본 랭크 보상으로 되돌릴까요?", lr_reset_done="랭크 보상을 기본값으로 되돌렸습니다.", e_lr_rows="랭크 보상이 올바르지 않음(EP 1-10,000,000,000, 팩/아이템 1-99; 주간 단계 1-500승, 계속 증가, 최대 20개)", kr_title="King of the Hill 보상", kr_lead="주간 보상(주 = 1-7일, 8-14일, 15-21일, 22-28일)과 시즌 보상(매월). 플레이한 주나 시즌이 끝나면 King of the Hill을 열 때 보상 화면이 나오고 달성한 보상이 가방으로 들어갑니다. 주간: 그 주의 승리 수 또는 경기 수 목표, 그리고 일정 승리 이상일 때 디비전 보상. 시즌: 일정 승리 이상일 때 시즌 종료 시 디비전 보상, 그리고 지역 내 순위 보상. King of the Hill 화면에도 이 보상이 표시됩니다. {0}에 저장되며 바로 적용됩니다.", kr_type="주간 목표", kr_type_winning="승리", kr_type_match="경기", kr_win="그 주의 승리", kr_match="그 주의 경기", kr_week_item="주간 목표 보상", kr_week_grade="주간 디비전 보상에 필요한 승리", kr_season_grade="시즌 디비전 보상에 필요한 승리", kr_week_div="주간 디비전 보상", kr_season_div="시즌 디비전 보상", kr_ranking="시즌 순위 보상(지역)", kr_from="시작 순위", kr_to="끝 순위", kr_add="순위 추가", kr_saved="King of the Hill 보상을 저장했습니다.", kr_confirm_reset="기본 King of the Hill 보상으로 되돌릴까요?", kr_reset_done="King of the Hill 보상을 기본값으로 되돌렸습니다.", e_kr_rows="King of the Hill 보상이 올바르지 않음(EP 1-10,000,000,000, 팩/아이템 1-99; 목표 1-100승 또는 1-200경기, 디비전 승리 주간 1-100·시즌 1-500; 순위 1-20행, 1-1000 범위, 증가하며 겹치지 않음)",
        nav_notice='공지', notice_title='공지 이미지', notice_lead='이 이미지는 로그인할 때마다 공지 팝업에 표시됩니다(issue #36). JPG 또는 PNG를 고르면 이 페이지가 저장 전에 팝업 크기(800×720)에 맞춥니다. 다음 로그인부터 적용되며 클라이언트 파일은 바꾸지 않습니다.', notice_file='이미지 파일(JPG/PNG)', notice_mode='맞춤 방식', notice_fit='전체 표시(배경 추가)', notice_fill='상자 채우기(가장자리 자름)', notice_bg='배경색', notice_save='공지 저장', notice_delete='공지 삭제', notice_current='현재 공지', notice_new='새 미리보기', notice_none='공지 이미지가 없습니다: 팝업이 비어 있습니다.', notice_saved='공지 이미지를 저장했습니다. 다음 로그인 때 표시됩니다.', notice_deleted='공지 이미지를 삭제했습니다.', notice_confirm_delete='공지 이미지를 삭제할까요?', notice_pick_first='먼저 이미지 파일을 고르세요.', e_notice_png='이미지는 800×720 PNG여야 합니다(이 페이지에서 파일을 다시 고르세요)', e_notice_size='이미지가 너무 큽니다(최대 8MB)',
        nav_assets='이미지', as_title='없는 이미지', as_lead='게임은 선수 얼굴, 클럽 엠블럼, 유니폼 이미지, 리그 로고, 스태프 사진을 클라이언트 캐시에서 보여 주며, 없는 것은 기본 이미지(실루엣, 빈 엠블럼)로 표시됩니다. 다운로드는 없는 이미지를 fifaaddict.com에서 백그라운드로 0.5초에 한 장씩 가져오며, 이미 있는 파일은 바꾸지 않습니다. 선수 얼굴은 수천 장일 수 있어 오래 걸립니다. 언제든 멈추고 이어서 받을 수 있습니다. 새 이미지는 늦어도 게임을 다시 실행한 뒤 표시됩니다. fifaaddict.com에 없는 선수 사진은 FC Online(FO4)에서 받습니다. 모두 다운로드는 모든 종류를 한 번에 받고 카드 데이터베이스 전체를 훑습니다. 서버가 응답하지 않으면 점점 더 오래 기다렸다가 스스로 이어갑니다.', as_all='모든 이미지', as_fetch_all='없는 것 모두 다운로드', as_kind='종류', as_added='받음', as_failed='없음', as_faces='선수 얼굴', as_crests='클럽 엠블럼', as_uniforms='유니폼 이미지', as_leagues='리그 로고', as_staffs='스태프 사진', as_careers='클럽 경력', as_careers_note='클럽 경력은 Wikidata에서 선수의 현재 시즌까지의 클럽 경력(연도, 클럽, 임대)을 받아 선수 정보의 특성 탭에 보여 줍니다. 끝나면 데이터베이스 > 게임에 적용을 누르세요. 찾지 못한 선수는 FO3 자체 경력(2017년까지)을 그대로 씁니다.', as_fetch='다운로드', as_stop='중지', as_running='다운로드 중: {0}', as_idle='진행 중인 다운로드가 없습니다.', as_started='다운로드를 시작했습니다.', as_stopped='다운로드를 중지했습니다.', e_asset_kind='알 수 없는 이미지 종류', e_asset_busy='다른 다운로드가 아직 진행 중입니다',
        nav_prematch="경기 설정", pm_title="경기 설정", pm_lead="다음 경기(리그, 컵, 랭크)의 경기장, 시간대, 날씨, 경기 시간입니다. Multiple > Rank에서는 경기 설정 화면에서도 바꿀 수 있고, 그 선택도 여기에 저장됩니다. 리그와 컵 화면은 표시만 합니다. 흐림: 낮에는 구름 낀 하늘, 밤에는 해질녘입니다(리그/컵 화면에는 \"Salju\"로 표시됨).", pm_stadium="경기장", pm_tod="시간대", pm_weather="날씨", pm_length="경기 시간", pm_home="홈 팀 경기장", pm_random="무작위", pm_day="낮", pm_night="밤", pm_dry="맑음", pm_rain="비", pm_snow="눈", pm_overcast="흐림", pm_minutes="{0}분", pm_ball="공", pm_saved="경기 설정을 저장했습니다.", e_pm_value="잘못된 경기 설정 값",
        nav_economy="경제", eco_title="경제", eco_lead="모든 감독의 가격과 EP입니다. 쉬움 = 지금과 같음(상점 무료). 현실적: 유료 상점(상품 가격 = 내용물의 가치, EP가 없는 부스트나 EXP 캡슐 등은 기본 가격 × 선수 가격 배율, 팩 = 들어 있는 카드의 가치이며 변동 시장에서는 +13%: 팩을 비싼 날까지 두었다가 열 수 있기 때문)과 변동 시장. 어려움: 선수와 팩 가격 x2, 적은 시작 EP. 변동 시장: 선수 가격이 매일 오르내리고, 가상 감독이 가끔 싸게 팔며, 판매가를 105%까지 정할 수 있습니다(더 늦게 팔림): 싸게 사서 비싸게 파세요. 엄격한 규칙(어려움): 강화, 트레이드, 스카우트로 EP를 불릴 수 없습니다: 강화 비용은 늘어나는 가치만큼이고, 복구는 EP로 내며, 제안은 내준 카드의 가치를 넘지 않습니다. 끄면(쉬움, 현실적) 게임 원래 규칙: 강화 비용은 카드 가치의 2%, 복구는 강화 포인트, 제안은 상한 없음. 경기 EP는 경기 보상 탭에서, 시작 EP는 새 감독에게 적용됩니다.", eco_preset="프리셋", eco_custom="사용자 지정", eco_easy="쉬움(지금과 같음)", eco_real="현실적", eco_hard="어려움", eco_shopfree="상점 무료(0 EP)", eco_players="선수 가격(배)", eco_packs="팩 가격(배)", eco_start="새 감독의 시작 EP", eco_dynamic="변동 시장(가격 변동, 거래로 이익 가능)", eco_strict="엄격한 규칙(강화, 트레이드, 스카우트로 EP를 불릴 수 없음)", eco_saved="경제 설정을 저장했습니다.", e_eco_value="잘못된 경제 설정 값(배율 0.1-100, 시작 EP 0 이상)",
        nav_db='데이터베이스', db_title='선수 카드와 클럽 데이터베이스', db_lead='시즌별 선수 카드를 수정합니다: 능력치, 포지션, 주발, 약발, 개인기, 공수 가담, 특성, 클럽(이적). 카드 한 장 = spid 하나 = 시즌 × 1,000,000 + 선수 ID(메시는 158023: 91 시즌 카드는 91158023)이며, 이름, spid 또는 선수 ID로 찾을 수 있습니다. 변경 사항은 gamedata/fifadb.sqlite에 저장되고 {0}에 기록됩니다(원래 값과 새 값: 되돌릴 수 있고, FO4 시즌 build가 다시 적용합니다). 게임은 자체 DB 사본을 읽습니다: 게임을 닫고 게임에 적용을 누른 뒤 끝날 때까지 기다렸다가 play.bat을 실행하세요.', db_apply='게임에 적용', db_started='백그라운드에서 게임에 설치하는 중…', db_done='설치했습니다. play.bat을 실행해 서버와 게임을 다시 시작하세요.', db_failed='실패(코드 {0}); 아래 로그를 확인하세요.', as_install='게임에 적용', db_search='카드 찾기', db_q='이름, spid 또는 선수 ID', db_hint='이름을 2글자 이상 입력하거나 spid / 선수 ID를 입력하세요.', db_edited='수정한 카드', db_none='아직 수정한 카드가 없습니다.', th_changed='수정 항목', f_club='클럽', ph_club='클럽 이름 입력', f_pos_n='포지션 {0}', f_foot='주발', foot_right='오른발', foot_left='왼발', f_weak='약발', f_skill='개인기', f_wr_att='공격 가담', f_wr_def='수비 가담', wr_low='낮음', wr_med='보통', wr_high='높음', f_stats='능력치', f_traits='특성', orig_value='원래 값: {0}', card_saved='{0} 저장, 현재 OVR {1}. 게임에 적용을 눌러야 게임에 반영됩니다.', crest_title='클럽 엠블럼', crest_lead='클럽과 그림을 고르세요(투명 PNG가 가장 좋습니다). 이 페이지가 256×256과 128×128로 맞춘 뒤 클라이언트 캐시에 저장합니다. 원래 엠블럼은 gamedata/crest_backup에 보관되어 되돌릴 수 있습니다. 게임을 다시 열면 보입니다.', crest_save='엠블럼 저장', crest_revert='원래 엠블럼으로', crest_confirm='이 클럽의 원래 엠블럼으로 되돌릴까요?', crest_now='현재 엠블럼', crest_new='새 엠블럼', crest_none='캐시에 이 클럽의 엠블럼이 없습니다.', crest_pick='먼저 클럽과 그림 파일을 고르세요.', crest_saved='엠블럼을 저장했습니다. 게임을 다시 열면 보입니다.', crest_reverted='원래 엠블럼으로 되돌렸습니다.', e_card_field='{0}은(는) 여기서 바꿀 수 없습니다', e_club='게임에 클럽 {0}이(가) 없습니다', e_not_edited='이 카드는 수정되지 않았습니다', e_crest_png='엠블럼은 {0}×{0} PNG여야 합니다(이 페이지에서 파일을 다시 고르세요)',
        f_look='3D 외형', look_lead='게임 3D DB(fifa_ng_db)의 EA 코드입니다. 얼굴 = 아무 3D 행의 머리: 목록에는 이 선수의 카드가 있고, 다른 선수의 이름을 입력해 목록에서 그 머리를 고르거나 spid나 선수 ID를 입력할 수 있습니다. 게임에 적용 후 게임에서 보입니다.', look_generic='지금 머리는 일반형입니다: 헤어 모델과 색(0 = 금발), 수염, 피부, 눈을 바꿀 수 있습니다.', look_scan='지금 머리는 스캔입니다: 머리카락과 수염은 머리를 따릅니다. 다른 헤어를 원하면 다른 머리의 얼굴을 고르세요.', look_shared='시즌 코드가 99를 넘는 카드(26 LIVE, 25 LIVE)는 자체 3D 행이 없습니다: 이 변경은 자체 행이 없는 이 선수의 모든 카드에 적용됩니다.', look_own='이 카드', f_face='얼굴', l_hairtypecode='헤어 모델', l_haircolorcode='머리색', l_facialhairtypecode='수염', l_facialhaircolorcode='수염 색', l_skintonecode='피부색', l_eyecolorcode='눈 색', l_bodytypecode='체형', e_no_engine='게임 3D DB(data_0.big)를 읽을 수 없습니다', sn_title='직접 만든 시즌', sn_lead='코드, 이름, 배지로 나만의 카드 시즌을 만듭니다. 검색 결과에서 카드를 복사해 채우고(모든 능력치를 한 번에 올릴 수 있음) 다른 카드처럼 수정하세요. {0}에 저장됩니다. 게임에 적용이 배지를 설치하고, play.bat 이후 이 시즌은 자체 Top 200 팩과 팀 컬러를 갖습니다.', f_season_code='시즌 코드', th_badge='배지', th_cards='카드', sn_badge='배지(PNG/JPG)', sn_save='시즌 저장', sn_saved='시즌 {0} 저장.', sn_delete='시즌 삭제', sn_confirm_delete='시즌 {0}과(와) 카드 {1}장을 삭제할까요? 클럽이 가진 이 시즌 카드도 DB에서 사라집니다.', sn_deleted='시즌 {0} 삭제.', sn_copy_to='시즌으로 복사', f_boost_stats='모든 능력치 추가', sn_copy='복사', sn_copied='{0}을(를) 시즌 {1}(으)로 복사했습니다(spid {2}).', sn_none='아직 직접 만든 시즌이 없습니다.', sn_remove='카드 삭제', sn_removed='카드 {0} 삭제.', confirm_card_remove='카드 {0}(spid {1})을(를) 시즌에서 삭제할까요?', sn_pick_badge='먼저 배지를 고르세요.', mod_title='모드: 내보내기와 가져오기', mod_lead='직접 만든 시즌, 복사한 카드, 카드 수정, 직접 만든 팀 컬러를 JSON 파일 하나로 저장하거나 다른 서버의 파일을 불러옵니다. 가져온 뒤 게임에 적용을 누르세요.', mod_export='모드 내보내기', mod_import='모드 가져오기(JSON)', mod_imported='모드 가져옴: 성공 {0}, 실패 {1}', e_mod='FO3ID 모드 파일이 아닙니다(모드 내보내기의 JSON)', e_season_icon='배지는 1 MB 이하의 PNG여야 합니다(이 페이지에서 파일을 다시 고르세요)', e_face='3D 행 {0}이(가) 없습니다', e_season='시즌 {0}은(는) 직접 만든 시즌이 아닙니다', e_season_code='게임이 이미 시즌 코드 {0}을(를) 사용합니다', e_card_exists='카드 {0}이(가) 이미 있습니다',
        nav_tc='팀 컬러', tc_title='직접 만든 팀 컬러', tc_lead='나만의 팀 컬러를 만듭니다: 필터(시즌, 현재 또는 과거 클럽, 국가, 선수, 최소 OVR, 마켓 최고 N장)로 카드를 고르며, 카드는 입력한 모든 필터를 만족해야 합니다. 단계는 기본 스페셜 팀 컬러와 같습니다: 선발 XI의 서로 다른 선수 5-6 / 7-8 / 9-11명(OVR +1/+2/+3, 또는 고른 OVR 값까지). 능력치 부스트는 모든 단계에 더해집니다. 5명 미만이면 전원 한 단계. {0}에 저장되며 다음 로그인부터 적용되고 재시작은 필요 없습니다.', tc_members='구성', tc_boost='부스트', tc_new='새 팀 컬러', tc_edit='{0} 수정', tc_nations='국가', tc_players='선수', tc_min_ovr='최소 OVR(0 = 제한 없음)', tc_best='최고 N장(0 = 전부)', tc_saved='{0} 저장: 선수 {2}명의 카드 {1}장.', tc_deleted='{0} 삭제.', tc_confirm_delete='팀 컬러 {0}을(를) 삭제할까요?', tc_none='아직 직접 만든 팀 컬러가 없습니다.', tc_all_cards='모든 카드', tc_all_pos='모든 포지션', ph_pick='입력 후 선택', e_nation='알 수 없는 국가 {0}', e_player_id='선수 ID {0}인 선수가 없습니다', e_color_missing='팀 컬러를 찾을 수 없습니다', e_tc_value='잘못된 팀 컬러 값',
        koth_title='King of the Hill 지역', koth_lead='King of the Hill에서 쓸 지역을 고르세요: 인도네시아, 싱가포르/말레이시아, 태국, 베트남의 도시(베트남 이름은 베트남 문자) 또는 직접 만든 지역. 바꾸면 가상 감독이 새 지역의 도시로 퍼지고 내가 고른 지역은 비워집니다: King of the Hill을 열 때 다시 고르세요. 재시작은 필요 없습니다.', koth_regions='지역', koth_id='인도네시아', koth_sgmy='싱가포르 / 말레이시아', koth_th='태국', koth_vn='베트남', koth_custom='사용자 지정', koth_saved='King of the Hill 지역을 저장했습니다.', e_koth_set='알 수 없는 지역', koth_custom_title='사용자 지정 지역', koth_custom_lead='한 줄에 한 권역씩 직접 지역을 쓰세요: 권역: 도시, 도시, 도시(예: Miền Bắc: Hà Nội, Hải Phòng, Sa Pa). 권역 1-20개, 권역마다 도시 1-30개, 이름은 24자 이하이며 중복 불가. 기본 지역을 복사해 고쳐도 됩니다. 이름이 그대로인 도시는 ID를 유지하므로 그곳의 선수는 그대로 있습니다. 저장한 뒤 위에서 사용자 지정을 고르세요. {0}에 저장되며 바로 적용됩니다.', koth_copy='복사할 지역', koth_copy_btn='편집기로 복사', koth_custom_save='사용자 지정 지역 저장', koth_custom_saved='사용자 지정 지역 저장: 권역 {0}개, 도시 {1}개.', e_koth_line='{0}번째 줄: 권역: 도시, 도시 형식으로 쓰세요', e_koth_name='이름이 비었거나 24자를 넘거나 중복입니다: {0}', e_koth_count='권역 1-20개, 권역마다 도시 1-30개여야 합니다', e_koth_empty='사용자 지정 지역이 비어 있습니다: 먼저 쓰고 저장하세요',
        face_title='선수 사진', face_lead='게임 속 카드 사진(128×128)입니다. 게임은 카드의 사진(p<spid>.png, 그 시즌의 사진)을 먼저 찾고, 없으면 선수의 사진(p<선수 ID>.png)을 찾으며, 둘 다 없으면 기본 얼굴을 보여 줍니다. 이미지 탭이 없는 사진을 fifaaddict.com에서 받으며 시즌별 사진도 받습니다. 여기서 이 선수의 모든 카드 또는 이 카드만의 사진을 바꿀 수 있습니다: 내 그림(PNG/JPG, 이 페이지가 128×128로 맞춤) 또는 다른 spid / 선수 ID의 사진(캐시에서, 없으면 fifaaddict.com에서 바로)으로. gamedata/faces에 저장되며 클라이언트 캐시 파일은 바꾸지 않고, 되돌리기는 이 사진을 지웁니다. 게임을 다시 열면 보입니다.', face_for='적용 대상', face_player='이 선수의 모든 카드(선수 ID {0})', face_card='이 카드만(spid {0})', face_save='사진 저장', face_revert='원래 사진으로', face_confirm='직접 넣은 이 사진을 지우고 원래 사진을 쓸까요?', face_now='게임 속 사진', face_new='새 사진', face_none='사진이 없습니다: 게임은 기본 얼굴을 보여 줍니다.', face_pick='먼저 그림 파일을 고르세요.', face_saved='사진을 저장했습니다. 게임을 다시 열면 보입니다.', face_reverted='원래 사진으로 되돌렸습니다.', e_face_png='사진은 {0}×{0} PNG여야 합니다(이 페이지에서 파일을 다시 고르세요)', head_scan='스캔 머리', head_generic='일반 머리', face_from='가져올 사진의 spid / 선수 ID', face_use='이 사진 사용', face_used='사진 {0}을(를) 사용합니다. 게임을 다시 열면 보입니다.', e_face_none='캐시와 fifaaddict.com 어디에도 사진 {0}이(가) 없습니다', e_face_fetch='fifaaddict.com이 응답하지 않습니다({0}). 나중에 다시 시도하세요',
        phd_title='사진으로 만든 3D 얼굴', phd_lead='일반 얼굴을 쓰는 선수용: 정면 클로즈업 사진을 다른 선수의 스캔된 3D 머리에 얼굴 피부로 입힙니다(기증자: 머리 모양과 머리카락이 따라오고, 눈은 기증자의 것을 씁니다). 사진을 고르고(정면을 보는 얼굴, 고른 조명), 11개 점을 순서대로 클릭하고, 닮은 기증자(피부색, 머리카락; 선수에게 수염이 없으면 수염 없는 기증자)를 고른 뒤 만들기를 누르세요. 이 선수의 선수 ID 행이나 일반 머리를 쓰는 모든 카드에 적용됩니다. gamedata/photo_heads에 저장되며, 데이터베이스 > 게임에 적용 후 게임을 다시 열면 보입니다.', phd_donor='기증자 머리(엔진 행)', phd_donor_hint='스캔 머리가 있는 선수 이름을 입력', phd_step='점 {0}/{1} 클릭: {2}', phd_all='{0}개 점을 모두 표시했습니다. 점 근처를 클릭하면 옮길 수 있습니다.', phd_undo='점 취소', phd_reset='모든 점 지우기', phd_photo='사진과 점', phd_now='3D 얼굴 텍스처', phd_none='아직 사진으로 만든 3D 얼굴이 없습니다.', phd_make='3D 얼굴 만들기', phd_delete='3D 얼굴 삭제', phd_confirm='이 선수의 사진 3D 얼굴을 삭제할까요?', phd_made='3D 얼굴을 만들었습니다. 데이터베이스 > 게임에 적용을 누른 뒤(게임을 닫은 상태) 게임을 여세요.', phd_deleted='3D 얼굴을 삭제했습니다. 게임에 적용하면 클라이언트에서 빠집니다.', phd_pick='먼저 사진을 고르세요.', pt_eye_r='선수의 오른쪽 눈 중앙(사진의 왼쪽)', pt_eye_l='선수의 왼쪽 눈 중앙(사진의 오른쪽)', pt_nose='코끝', pt_mouth_r='오른쪽 입꼬리(사진의 왼쪽)', pt_mouth_l='왼쪽 입꼬리(사진의 오른쪽)', pt_chin='턱 끝', pt_forehead='이마 가운데', pt_temple_r='오른쪽 관자놀이: 눈썹 높이의 얼굴 가장자리(사진의 왼쪽)', pt_temple_l='왼쪽 관자놀이: 눈썹 높이의 얼굴 가장자리(사진의 오른쪽)', pt_jaw_r='오른쪽 턱: 입 바로 아래 얼굴 가장자리(사진의 왼쪽)', pt_jaw_l='왼쪽 턱: 입 바로 아래 얼굴 가장자리(사진의 오른쪽)', e_ph_png='사진은 최대 {0}픽셀의 PNG여야 합니다(이 페이지에서 파일을 다시 고르세요)', e_ph_points='먼저 사진에 {0}개 점을 모두 표시하세요', e_ph_donor='{0}행은 스캔 머리가 아닙니다: 스캔 머리가 있는 선수를 기증자로 고르세요', e_ph_files='클라이언트에 머리 {0}의 파일이 없습니다',
        phd_icon='선수 사진에서 가져오기', phd_icon_done='선수 사진에서 사진, 11개 점, 기증자를 채웠습니다. 점을 확인하고(점 근처를 클릭하면 옮겨짐) 3D 얼굴 만들기를 누르세요.', phd_icon_nopts='사진에서 얼굴을 찾지 못했습니다: 점을 직접 클릭하세요', e_ph_icon='캐시와 fifaaddict.com 어디에도 선수 {0}의 사진이 없습니다', phd_club='클럽의 일반 얼굴 선수에게 사진으로 3D 얼굴 만들기', phd_club_lead='자동 3D 얼굴: 아직 일반 얼굴이고 캐시에 사진(FO3 사진; 이미지 탭이 없는 사진을 받음)이 있는 클럽 선수마다 그 사진으로 3D 얼굴을 만듭니다. YuNet 검출기가 얼굴의 점을 찾고, 피부색과 머리색으로 기증자를 고릅니다. 처음에는 약 1분 걸립니다(모든 스캔 머리를 한 번 읽음). 선수별 결과는 카드 창에서 확인하고 바꿀 수 있으며, 그다음 게임에 적용하세요.', phd_club_busy='3D 얼굴을 만드는 중…', phd_club_done='3D 얼굴 {0}개를 만들고 {1}명을 건너뛰었습니다.', phd_skip_icon='사진 없음', phd_skip_face='얼굴 못 찾음',
        world_title='게임 세계', world_lead='모던: AI 클럽, 클럽 선택, This Season 팩이 26 LIVE 카드를 씁니다(설치된 경우). 클래식 FO3: 모든 클럽이 FO3 원래의 2017 스쿼드를 쓰고, 가상 감독은 FO3 원래 시즌만 쓰며, 추가된 시즌(25 LIVE, 26 LIVE, WC18, 직접 만든 시즌)은 팩, 마켓, 트레이드에 나오지 않고 관리자 페이지(선수 추가, Bag)로만 줄 수 있습니다. 내 클럽과 컬렉션은 그대로입니다. 서버를 다시 시작하면 적용됩니다(play.bat).', world_label='모드', world_modern='모던(26 LIVE)', world_classic='클래식 FO3(2017)', world_saved='게임 세계를 저장했습니다.', world_restart='서버가 아직 이전 모드로 실행 중입니다: play.bat을 실행해 적용하세요.', e_world='알 수 없는 세계 모드',
        vm_title='가상 감독', vm_lead='약 2,000명의 가상 감독(Rank·King of the Hill 상대, AI 클럽, 마켓)은 게임과 서버를 실행할 때마다 그대로입니다. 여기의 다시 생성 버튼만 새 무리를 만듭니다: 이름, 레벨, 클럽, 스쿼드, 플레이 스타일, Rank 점수, 지역, AI 클럽이 재시작 없이 바로 바뀝니다(이미 열린 게임 화면은 다시 열면 갱신). ID는 그대로라 친구, 클럽, 기록은 계속 작동하지만 그 뒤의 사람은 바뀝니다. 처음으로 되돌리기는 첫 무리를 다시 불러옵니다.', vm_regen='다시 생성', vm_reset='처음으로 되돌리기', vm_gen_now='{0}세대(현재 {1}명).', vm_confirm='가상 감독을 새로 만들까요? 모두의 이름, 클럽, 스쿼드가 바로 바뀝니다.', vm_regen_done='새 무리를 지금 사용합니다.', vm_reset_done='첫 무리를 다시 사용합니다.',
        st_title='스태프', st_lead='계정의 스태프: {0} / {1}. 새 스태프는 화면이 바뀌면 Tim > Manajemen Staff에 나옵니다.', st_staff='스태프', st_grade='별', st_life='계약', st_status='상태', st_add='스태프 추가', st_added='{0} × {1} 추가함.', st_removed='{0} 삭제함.', st_remove='삭제', st_confirm='스태프 {0}을(를) 계정에서 삭제할까요?', st_none='스태프가 없습니다.', st_helper='보조', st_appointed='배치됨', st_studying='연수 중', e_unknown_staff='알 수 없는 스태프', e_staff_full='스태프가 가득 참: {0}자리 남음', e_no_staff='없는 스태프',
        phd_redo='모든 3D 얼굴을 사진으로 다시 만들기', phd_redo_confirm='기존 3D 얼굴을 모두 선수 사진으로 다시 만들까요? 기증 머리를 다시 고릅니다(피부색은 기증 선수의 사진과 비교). 업로드한 사진으로 만든 얼굴도 선수 사진 것으로 바뀝니다.',
        t_PLAYER_GROWTH_RESET='전술 포인트 초기화(선수 성장)',
    ),
}


def check_i18n():
    """Self-check (python admin.py; test_league.py runs it too): every language has the keys and
    {0}.. placeholders of "id", and every key admin/index.html and the errors above name exists."""
    ref, holes = I18N["id"], lambda s: sorted(re.findall(r"\{\d+\}", s))
    for lang, tb in I18N.items():
        assert tb.keys() == ref.keys(), f"{lang}: keys differ from id: {sorted(tb.keys() ^ ref.keys())}"
        bad = [k for k in tb if holes(tb[k]) != holes(ref[k])]
        assert not bad, f"{lang}: {{0}} placeholders differ from id: {bad}"
    with open(PAGE, encoding="utf-8") as f:
        page = f.read()
    with open(__file__, encoding="utf-8") as f:
        me = f.read()
    used = (re.findall(r'data-i18n(?:-ph|-aria)?="(\w+)"', page) + re.findall(r'\bt\("(\w+)"[,)]', page)
            + re.findall(r'AdminError\("(\w+)"', me) + re.findall(r'\("(\w+)",\)', me))
    missing = sorted(set(used) - ref.keys())
    assert not missing, f"keys used but not in the tables: {missing}"


if __name__ == "__main__":
    check_i18n()
    print(f"I18N OK: {', '.join(I18N)} ({len(I18N['id'])} keys each)")
