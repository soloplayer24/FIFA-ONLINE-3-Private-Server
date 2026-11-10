"""HTTP config/publisher service stub for FO3ID.

The client requests these relative paths (found in client memory):
    /info/server/config        -> expected: server list / endpoints config
    /game/my/ipaddress         -> expected: client's public IP echo
    /publisher/garena/sign     -> publisher token sign/verify
    /publisher/nexon/2ndauth   -> secondary auth
    /telemetry/gameconnection  -> telemetry sink (can be a no-op)
    /shop/cash/charge/url, /shop/cash/history/url -> cash shop URLs

Phase 1: log every request (method, path, query, headers, body) and return a minimal
placeholder so we can see which endpoint the client hits first and what it expects.
The real response shapes are unknown until captured — every handler is a guess.

Usage: python -I http_config.py [port=8080] [--tls]
Default port 8080 to avoid colliding with a local Apache on :80.

--tls serves HTTPS with certs/server.pem. fifazf.exe calls a hardcoded
https://APSEODESK172 (port 443) for /info/server/locale etc. — no launch arg
overrides it — so run `http_config.py 443 --tls` and map APSEODESK172 to
127.0.0.1 in the hosts file.
"""
import bisect, calendar, collections, copy, functools, glob, gzip, html, http.server, math, os, random, re, socket, sqlite3, ssl, statistics, sys, threading, time, urllib.parse
from contextlib import closing

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # `python -I` leaves the script dir off sys.path
import gen_teams
TLS = "--tls" in sys.argv
_args = [a for a in sys.argv[1:] if a != "--tls"]
PORT = int(_args[0]) if _args and _args[0].isdigit() else 8080  # (imported by tools: their own args)
LOG = os.path.join(HERE, "captures", "http_config.log")
CERT = os.path.join(HERE, "certs", "server.pem")  # self-signed, CN/SAN APSEODESK172
os.makedirs(os.path.dirname(LOG), exist_ok=True)

import json

# Where this service tells the client the game servers live.
REDIR_HOST, REDIR_PORT = "127.0.0.1", 28000
NOTI_HOST, NOTI_PORT = "127.0.0.1", 28001

# --------------------------------------------------------------------------- #
# Endpoint bodies — TENTATIVE. The real response format (XML vs JSON, field
# names, the token/sign scheme) is unknown: the client has not been captured
# reaching these (XignCode blocks it earlier — see README). These return minimal
# plausible JSON so the client has *something* to parse; correct each shape from
# a capture. Every request is logged regardless, which is the point for now.
# --------------------------------------------------------------------------- #

def _json(obj):
    return ("application/json", json.dumps(obj).encode())


def ep_server_config(query, body):
    return _json({
        "redirector": {"host": REDIR_HOST, "port": REDIR_PORT},
        "notification": {"host": NOTI_HOST, "port": NOTI_PORT},
        "region": "ID",
        "status": "ok",
    })


def ep_locale(query, body):
    # First call fifazf.exe makes. Body "OK" -> JSON_PARSING_ERROR in-game; {"result": true}
    # passes. `language` must be a WebCache.setBaseUrl key (kor eng chi vie tha ind jpn,
    # default kor): "id" crashed the UI (the old black screen); "ind" = Indonesian, its
    # externalAssets/language_ind.big is in the client cache. Publisher (Garena) is chosen
    # by launch args `region id token <t>`, not by this response.
    # `url` = WebCache base for externalAssets (crests, uniforms, player cards...). Without
    # it the client uses the dead Garena CDN, every refresh fails and crests show
    # "PLACE HOLDER" even though the files sit in its own _cache. Served by cdn_file().
    return _json({"result": True, "language": LANGUAGE, "url": f"https://APSEODESK172/{CDN_PREFIX}"})


LANGUAGE = os.environ.get("FO3_LANGUAGE", "ind")


GAME = os.environ.get("FO3_GAME", r"D:\FO3ID\32795")  # the FO3 ID client install
# The client's own downloaded copy of the Garena CDN (externalAssets/...).
ASSETS = os.environ.get("FO3_ASSETS", os.path.join(GAME, "_cache", "games", "fo3id"))
CDN_PREFIX = "cdn/"


# The server's own images, served first: the draft pack icons (tools/draft_icons.py; the client
# cache has no draft pictures).
OWN_ASSETS = os.path.join(HERE, "assets")
# The login notice popup's picture (issue #36: "upload, replace, or delete announcement images"),
# an 800x720 PNG the admin page's Pengumuman tab saves here. StartupNoticeScreen.configUI shows the
# notice two ways: this client (LOCALE not KOR) GETs /game/inweburl/notice and opens its url in the
# popup's web renderer (LoadWebAddress), so the url is the admin page's /notice (the picture fitted
# to the window); the Korean client loads externalAssets/notice/notice.png instead (stretched to
# 800x720), served from here too. Without a picture the popup stays empty (about:blank).
NOTICE_IMAGE = os.path.join(HERE, "gamedata", "notice.png")
ADMIN_PORT = int(os.environ.get("FO3_ADMIN_PORT", "8088"))  # the admin page (admin.py); 0 = none


def notice_url():
    """The login notice popup's page: the admin page's picture, or nothing."""
    if ADMIN_PORT and os.path.isfile(NOTICE_IMAGE):
        return f"http://127.0.0.1:{ADMIN_PORT}/notice?t={int(os.path.getmtime(NOTICE_IMAGE))}"
    return "about:blank"


# Own player pictures (issue #36), the admin Database tab's uploads: FACES/p<id>.png, 128x128,
# served before the cache's. ExternalAssetLoaderUtil.loadExternalPlayerPicture asks for
# p<spid>.png for a card of any season but curseason's (season 0, a bare player id) and, when that
# fails, p<player id>.png: so p<spid> is one card's picture and p<player id> every card's.
FACES = os.path.join(HERE, "gamedata", "faces")


def asset_path(rel):
    """FACES' picture, OWN_ASSETS/rel or ASSETS/rel, whichever exists, else None. A club crest the cache lacks
    in one size is its other size (issue #29: the cache has 190 small crests but 49 large ones,
    and the cup screens ask for the large one: Napoli, Bayern, Man United showed none). A Rekrut
    pack tile (shop/<icon>, STAFF_PACKS) is its own picture when OWN_ASSETS has one (issue #41),
    else a staff's: the client has no staff pack art.
    Issue #36: the game setting screen's "Default ball" (ball 0) has no picture ("NOT FOUND"): it
    shows the plain EA SPORTS ball's (ball 6); a small flag (countries/smallFlags, none cached) is
    the nation's big one."""
    if rel == "externalAssets/notice/notice.png":
        return NOTICE_IMAGE if os.path.isfile(NOTICE_IMAGE) else None
    m = re.fullmatch(r"externalAssets/players/(p\d+\.png)", rel)
    if m and os.path.isfile(os.path.join(FACES, m[1])):
        return os.path.join(FACES, m[1])
    m = re.fullmatch(r"externalAssets/shop/(\w+)\.png", rel)
    staff = m and STAFF_PACK_PICTURES.get(m[1])
    rel = re.sub(r"^externalAssets/balls/ball_0\.png$", "externalAssets/balls/ball_6.png", rel)
    rel = re.sub(r"^externalAssets/countries/smallFlags/(\d+\.png)$", r"externalAssets/countries/\1", rel)
    other = re.sub(r"crests/(large|small)/", lambda m: f"crests/{'small' if m[1] == 'large' else 'large'}/", rel)
    for r in dict.fromkeys(x for x in (rel, other, staff and f"externalAssets/staffs/{staff}.png") if x):
        for root in (OWN_ASSETS, ASSETS):
            f = os.path.normpath(os.path.join(root, r))
            if f.startswith(os.path.normpath(root) + os.sep) and os.path.isfile(f):
                return f
    return None


def cdn_file(rel):
    """(content-type, bytes) of an asset, or None (-> 404, client shows its default)."""
    f = asset_path(rel)
    if f is None:
        return None
    if rel == LANG_PACK:  # with the game setting screens' labels fixed (issue #31)
        f = language_pack(f)
    ctype = {".png": "image/png", ".jpg": "image/jpeg", ".swf": "application/x-shockwave-flash"}
    with open(f, "rb") as fh:
        return (ctype.get(os.path.splitext(f)[1].lower(), "application/octet-stream"), fh.read())


def ep_server_time(query, body):
    # TimerUtil.getTime: retries every second (and holds the bootflow) until `timestamp`
    # (ms, fed to new Date()) is present. timezone guessed as JS getTimezoneOffset() for
    # WIB (UTC+7) — the backend looked like Node/Express.
    return _json({"timestamp": int(time.time() * 1000), "timezone": -420})


def ep_my_ip(query, body):
    # real HTTP PUT from the lobby; every reply is JSON-parsed
    return _json({"result": True, "ipaddress": "127.0.0.1"})


def ep_garena_sign(query, body):
    # Accept any token for local offline use; echo a success + a fake signed token.
    return _json({"result": "ok", "token": "LOCAL-SIGNED-TOKEN", "uid": 1})


def ep_nexon_2ndauth(query, body):
    return _json({"result": "ok", "passport": "LOCAL-PASSPORT"})


def ep_telemetry(query, body):
    # Client JSON-parses every reply; plain "OK" -> JSON_PARSING_ERROR in-game.
    return _json({"result": True})


# The starter song (music/starter.mp3) lives in the game's own EA Trax playlist as TrackID 1,
# put there by tools/eatrax_song.py; the server does not play music.


def ep_empty(query, body):
    return _json({})


def ep_shop_url(query, body):
    return _json({"url": "about:blank"})


# ponytail: one local account kept in a JSON file; a real multi-user store comes later.
STATE = os.path.join(HERE, "gamedata", "state.json")
# Handlers read-modify-write state.json; game requests and the admin page (admin.py) take this
# so one never reads a half-written file or overwrites the other's change.
STATE_LOCK = threading.Lock()


def _state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"coaches": [], "defaultcoachid": "", "teams": {}}


def _save(st):
    """Write state.json whole or not at all (a temp file, then a rename): background writers
    (market_loop, the Rank timers) save while the user may close the server window."""
    fold_playtime(st)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    for _ in range(40):  # Windows: someone reading state.json blocks the rename for a moment
        try:
            return os.replace(tmp, STATE)
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, STATE)


def coach_id(slot):
    return f"{slot + 1:024x}"  # Mongo-style _id


def front_player(me):
    """coachlist[].frontplayer: the 3D player on the manager select screen. Native LoadFrontPlayers
    (0x9A09E0) reads staticdata.preferredposition1 (0 = goalie -> kit.goalie), kit.home|goalie.id,
    spid, id (asCString: a string) and jersey; with no frontplayer the game shows its own default
    (a Barcelona player). The club's most valuable player (user: "pemain yang muncul dengan harga
    tertinggi")."""
    team = me["team"]
    p = max(team["players"], key=lambda x: x.get("price", 0), default=None)
    if p is None:
        return None
    with closing(_db()) as c:
        row = c.execute("SELECT preferredposition1 FROM players WHERE spid = ?", (int(p["spid"]),)).fetchone()
    return {"id": str(p["_id"]), "spid": int(p["spid"]), "jersey": p.get("jersey", 0),
            "staticdata": {"preferredposition1": row[0] if row else 25},
            "kit": {"home": {"id": me["teamid"] << 5}, "goalie": {"id": me["teamid"] << 5 | 2}}}


def ep_coaches(query, body):
    # StartupCoachCreationScreen: coachlist entries need _id, slot, name, state "active"
    # ("postpone" = deleted, restorable until hideat; recoveryperiod s goes in the delete notice).
    # initializeMyCoach also shows team name/crest from summary and teamvalue.val, same
    # values as the lobby HUD (coach_me); native LoadFrontPlayers reads teamid + frontplayer.
    st = _state()
    if purge_deleted_coaches(st):
        _save(st)
    def entry(c):
        me = coach_me(st, c["slot"])
        fp = front_player(me)
        return dict(c, summary=me["summary"], teamvalue={"val": sum(p["price"] for p in me["team"]["players"])},
                    levelinfo=me["levelinfo"], teamid=me["teamid"], **({"frontplayer": fp} if fp else {}),
                    recoveryperiod=COACH_RECOVERY, **({"hideat": w3c(c["deleteat"])} if "deleteat" in c else {}))
    usable = {c["_id"] for c in st["coaches"] if c.get("state", "active") == "active"}
    return _json({"coachlist": [entry(c) for c in st["coaches"]],
                  "defaultcoachid": st["defaultcoachid"] if st["defaultcoachid"] in usable else ""})


def ep_new_team(slot, query, body):
    # POST /coach/<slot>/new/step/3 {"id": teamid, "spids": [...]}: the client passes this
    # body on as the new club and reads data.team.players ("could not take the random
    # players!" otherwise). Built from gen_teams.py's roster file for that team.
    req = json.loads(body or b"{}")
    chosen = set(req.get("spids", []))
    team = roster(int(req.get("id") or 0))["team"]
    team["players"] = [p for p in team["players"] if p["spid"] in chosen]
    team["squad"]["positions"] = [p for p in team["squad"]["positions"] if p["spid"] in chosen]
    fix_formation(team, int(req.get("id") or 0))
    st = _state(); st["teams"][str(slot)] = team; _save(st)
    return _json({"result": True, "team": team})


def ep_create_coach(slot, query, body):
    # POST /coach/<slot> {"id": teamid, "coachName"}: needs body.result
    req = json.loads(body or b"{}")
    st = _state()
    purge_deleted_coaches(st)
    st["coaches"] = [c for c in st["coaches"] if c["slot"] != slot] + [{
        "_id": coach_id(slot), "slot": slot, "name": req.get("coachName", ""), "state": "active",
        # StartupCoachCreationScreen.initializeMyCoach reads teamvalue.val, levelinfo.level, summary
        "teamid": req.get("id"), "teamvalue": {"val": 0}, "levelinfo": {"level": 1}, "summary": {},
        "appat": w3c(time.time()),  # coach_me career.appat: the manager's start date
        "ep": ECONOMY["start_ep"]}]  # (managers made before keep START_EP, the default for no "ep")
    _save(st)
    return _json({"result": True})


# Deleting a manager (StartupCoachCreationScreen > Delete): PUT /coach/<slot>/postponed puts it in
# state "postpone" until hideat; "Kembalikan manager" = DELETE /coach/<_id>/postponed undoes it.
# Once hideat has passed CoachUtil.shouldDeleteCoach shows the slot as free ("Buat manager baru").
COACH_RECOVERY = 300  # s a deleted manager can still be restored (the real game: 72 h)


def purge_deleted_coaches(st):
    """Drop managers whose restore time is over, with their club. The account-wide club data
    (league, cups, market offers, last match, trade) belongs to the default manager's club."""
    gone = [c for c in st["coaches"] if c.get("state") == "postpone" and c.get("deleteat", 0) <= time.time()]
    ds = _default_slot(st) if gone else 0  # Market offers are each manager's own (slot_offers)
    for c in gone:
        st["coaches"].remove(c)
        st["teams"].pop(str(c["slot"]), None)
        st["offers"] = [o for o in st.get("offers", []) if o.get("slot", ds) != c["slot"]]
        if c["_id"] == st["defaultcoachid"]:
            st["defaultcoachid"] = ""
            for k in ("league2", "cups", "match", "trade", "club"):
                st.pop(k, None)
    if not st.get("offers"):
        st.pop("offers", None)
    return gone


def ep_coach_postponed(key, query, body):
    """PUT /coach/<slot>/postponed: delete a manager; DELETE /coach/<_id>/postponed: restore it."""
    st = _state()
    purge_deleted_coaches(st)
    c = next((c for c in st["coaches"] if key in (str(c["slot"]), c["_id"])), None)
    if c is None:
        reply = {"result": False, "reason": "already removed"}  # restore: COACH_CREATION_RESTORE_ERROR
    elif json.loads(body or b"{}").get("_method") == "DELETE":
        c["state"] = "active"
        c.pop("deleteat", None)
        reply = {"result": True}
    else:
        c["state"] = "postpone"
        c["deleteat"] = time.time() + COACH_RECOVERY
        reply = {"result": True}
    _save(st)
    return _json(reply)


POS_SUB = 28  # Enums.PLAYERPOSITION: 0..27 pitch positions, 28 SUB, 29.. RESERVE


# OptionManager.applySoundSetting hands native volume * Master (each 0..10) for Commentary,
# PAVolume, PlayerCalls, SFX, Crowd and BGM; a missing key is NaN = silent (no music).
SOUND_DEFAULTS = {"Master": 10, "Commentary": 10, "PAVolume": 10, "PlayerCalls": 10, "SFX": 10, "Crowd": 10, "BGM": 10}

# Opsi > Kamera setting (OptionCameraSettingScreen) shows and steps OptionManager.cameraOption
# as-is: Camera 0..6 (F3..F9: TELE_BROADCAST, TELE, COOP, DYNAMIC, ENDTOEND, PRO, BROADCAST),
# CamDetail (Customise off/on), CamHeight/CamZoom (0..20, the custom gauges of every camera but Pro) and
# CamProZoom/CamProSpeed/CamProSwing (0..20, Pro only). OptionManager fills none of them: a
# missing key showed no value and could not be stepped (NaN + 1, issue #24). Defaults are
# OptionManager.OPTION_DEFAULT_CAM_* (10); Camera 0 = what native got from the missing key.
CAMERA_DEFAULTS = {"Camera": 0, "CamDetail": 0, "CamHeight": 10, "CamZoom": 10,
                   "CamProZoom": 10, "CamProSpeed": 10, "CamProSwing": 10}

# Opsi > Kontrol (OptionControlSettingScreen) edits OptionManager.controlOption in place and
# Konfirmasi applies it (applyControlSetting -> native onOptionControlSettingChanged) and sends it
# back. With no controlOption it edits a throwaway copy, so key changes were never applied or
# saved; with {} (no key lists) the native input thread crashes. So /account always carries a full
# one: the screen's own defaults (Controller 0 = keyboard; OptionManager.setDefaultControllerSetting,
# slots 5-8 copied from 1-4 like the screen does). Key codes are Flash keyCodes, 256-258 mouse.
_WASD = [87, 65, 68, 83, 81, 90, 67, 69]
_ARROWS = [38, 40, 37, 39, 256, 257, 258]
_KEY_ATT = (_WASD, _WASD, [69, 65, 68, 83, 81, 90, 67, 87], [80, 75, 186, 76, 73, 190, 191, 79])
_KEY_DEF = (_WASD, [87, 65, 83, 81, 68, 90, 67, 69], _KEY_ATT[2], _KEY_ATT[3],
            [87, 65, 68, 32, 81, 90, 67, 69], [87, 65, 83, 32, 68, 90, 67, 69],
            [69, 65, 68, 32, 81, 90, 67, 87], [80, 75, 186, 32, 73, 190, 191, 79])
_KEY_MOV = (_ARROWS, _ARROWS, _ARROWS, [87, 83, 65, 68, 256, 257, 258])
_JOY = ([4, 3, 2, 1, 5, 7, 6, 8], [4, 2, 3, 1, 5, 6, 8, 7], [4, 3, 2, 1, 5, 7, 6, 8])
_JOY_MOV = ([9, 11, 10], [9, 11, 10], [11, 9, 10])
CONTROL_DEFAULTS = {"Controller": 0, "Type": 0,
                    **{f"KeyAttType{i + 1}": _KEY_ATT[i % 4] for i in range(8)},
                    **{f"KeyDefType{i + 1}": _KEY_DEF[i] for i in range(8)},
                    **{f"KeyMovType{i + 1}": _KEY_MOV[i % 4] for i in range(8)},
                    **{f"Joy{k}Type{i + 1}": (_JOY_MOV if k == "Mov" else _JOY)[i] for k in ("Att", "Def", "Mov") for i in range(3)}}


# Each device's own preset (issue #32, user's report: keyboard "FO2 New", gamepad "Wing" kept
# resetting each other). controlOption holds ONE Type, the preset of the device in use: keyboard
# 0..7 (FO2, FO2 Baru, FO3, FO3 Tangan kiri; +4 = the same with "Pergantian pemain terpisah"),
# gamepad 0..2 (Default, Tipe W, DPAD). The Kontrol screen remembers the other device's preset
# only while it is open: its first device switch in a visit sets Type 0
# (OptionControlSettingScreen.onControllerSelectorChanged, _lastTypeChoice -1). So the server keeps
# st["controlType"] {device: last preset} and gives a device its preset back when a Konfirmasi
# switched to it with that reset (Type 0 and its preset-0 keys untouched), or with a Type out of
# its range (the beginner's guide flips Controller and keeps the other device's Type). The reply
# carries it into OptionManager (the screen shows it next time; native gets it at the next match
# start, ZoneManager.startBE -> applyControlSetting). controlOption itself keeps the client's shape:
# native onOptionControlSettingChanged gets the whole object (it ignores other fields).
# The reporter still saw the default after a switch (it only came back after Konfirmasi), so
# tools/control_presets.py patches the screen: a switch takes the new device's preset from
# controlOption.selectedIndex (keyboard) or .index (gamepad), leaves the old one's there and sets
# .selected; the server fills both from st["controlType"] and keeps what comes back. Those names
# are the screen's own (its constant pool has them), so the patch keeps the handler's length.
CONTROL_TYPE_MAX = {0: 7, 1: 2}
CONTROL_PRESET_FIELDS = {0: "selectedIndex", 1: "index"}
CONTROL_SWAPPED = "selected"  # set by the patched screen on a switch: its Type is the player's choice


def remember_control_type(st, ctl, prev, swapped=False):
    """ctl (a complete controlOption from the client) with this device's remembered preset when
    the screen reset it (see CONTROL_TYPE_MAX); prev = the controlOption saved before. A patched
    screen (swapped) never resets it, so its Type stays, preset 0 included."""
    types = st.setdefault("controlType", {})
    pc, pt = prev["Controller"], prev["Type"]
    for d in (pc, 1 - pc):  # a Type out of prev's range is the other device's (a guide flip)
        if 0 <= pt <= CONTROL_TYPE_MAX.get(d, 0):
            types.setdefault(str(d), pt)
            break
    c, t = ctl["Controller"], ctl["Type"]
    top = CONTROL_TYPE_MAX.get(c, 0)
    keys = lambda o: [o.get(f"{'Key' if c == 0 else 'Joy'}{k}Type1") for k in ("Att", "Def", "Mov")]
    # not a remap of preset 0: its keys as before, or as Initialize sets them (it leaves Type alone)
    reset = not swapped and c != prev["Controller"] and t == 0 and keys(ctl) in (keys(prev), keys(CONTROL_DEFAULTS))
    if reset or not 0 <= t <= top:
        t = types.get(str(c), 0)
    t = t if 0 <= t <= top else 0
    types[str(c)] = t
    return {**ctl, "Type": t}


def control_option_ok(c):
    """True if `c` has every controlOption field with the right shape (ints, non-empty int lists)."""
    def ok(v, want):
        if isinstance(want, list):
            return isinstance(v, list) and bool(v) and all(isinstance(x, int) for x in v)
        return isinstance(v, int)
    return isinstance(c, dict) and all(ok(c.get(k), v) for k, v in CONTROL_DEFAULTS.items())


def account_option():
    """The account's `option`: responses/account_base.json's, with the options the client saved
    through PUT /account/option layered over it."""
    with open(os.path.join(RESPONSES, "account_base.json"), encoding="utf-8") as f:
        opt = json.load(f)["option"]
    opt.update(_state().get("option", {}))
    opt["soundOption"] = {**SOUND_DEFAULTS, **(opt.get("soundOption") or {})}
    # a gauge stepped while it was NaN came back as null/NaN: such a value gets the default
    opt["cameraOption"] = {**CAMERA_DEFAULTS, **{k: v for k, v in (opt.get("cameraOption") or {}).items()
                                                 if isinstance(v, (int, float)) and math.isfinite(v)}}
    if not control_option_ok(opt.get("controlOption")):
        opt["controlOption"] = CONTROL_DEFAULTS
    c = opt["controlOption"]
    types = _state().get("controlType") or {}
    if not 0 <= c["Type"] <= CONTROL_TYPE_MAX.get(c["Controller"], 0):  # saved before issue #32's fix
        c = {**c, "Type": types.get(str(c["Controller"]), 0)}
    # each device's preset for the patched Kontrol screen (tools/control_presets.py)
    pick = lambda d: c["Type"] if d == c["Controller"] else types.get(str(d), 0)
    opt["controlOption"] = {**c, **{f: pick(d) if 0 <= pick(d) <= CONTROL_TYPE_MAX[d] else 0
                                    for d, f in CONTROL_PRESET_FIELDS.items()}}
    return opt


def ep_account(query, body):
    """GET /account (AccountProxy.accountData)."""
    with open(os.path.join(RESPONSES, "account_base.json"), encoding="utf-8") as f:
        acc = json.load(f)
    acc["option"] = account_option()
    if missions_cfg()["enabled"]:  # the Quest window's menu button (missions)
        acc["features"].setdefault("freeroambuttons", {})["quest"] = {}
    # Multiple > Persahabatan's buttons (MultiplayQuickMatchScreen.checkMatchFeature reads
    # featuresAt("/friendlymatch"); none = an empty screen): against AI, see room_kind
    acc["features"]["friendlymatch"] = {"quickmatch": {}, "randommatch": {}, "custommatch": {}}
    acc["features"].setdefault("esports", {})["regionalleague"] = {}  # King of the Hill (issue #36)
    if live_boost():  # Tim > Live Boost (the Team of the Week)
        acc["features"].setdefault("livecenter", {})["boost"] = {}
    # guides = the Beginner's Guide chapters done (BeginnersGuideManager.isChapterCompleted). Until
    # "setcontroller" is done MenuScreen.onFreeRoamLoadingFinished starts that guide instead of
    # attaching the chat window, so the top bar's chat button did nothing (user: "global chat belum
    # ada"); the controller is chosen in Opsi anyway
    acc["guides"] = sorted({*acc.get("guides", []), "setcontroller"})
    return _json(acc)


def ep_account_option(query, body):
    """PUT /account/option {soundOption, screenOption, controlOption, chatHotKeyOption, aiOption,
    cameraOption} (Opsi > Konfirmasi, leaving a match). A controlOption is kept only when it is
    complete (see CONTROL_DEFAULTS); otherwise the one saved before stays.
    The reply must carry the saved options as resources[0].body (AccountCommand.saveGameOption
    sets accountProxy.gameOptions from it, and that runs OptionManager.setOptions, which also
    takes the backup restoreOptions goes back to on every zone change). With no resources the
    callback threw TypeError #1010, so entering or leaving a match put back the options from
    login: keyboard instead of the gamepad, the old camera (issue #21)."""
    req = json.loads(body or b"{}")
    st = _state()
    old = st.get("option", {}).get("controlOption")
    st["option"] = {k: v for k, v in req.items() if k.endswith("Option") and k != "controlOption"}
    ctl = req.get("controlOption") if control_option_ok(req.get("controlOption")) else old
    if ctl is not old:  # a new one from the client: each device keeps its own preset (issue #32)
        extra = (*CONTROL_PRESET_FIELDS.values(), CONTROL_SWAPPED)
        sent = {k: ctl.get(k) for k in extra}
        ctl = {k: v for k, v in ctl.items() if k not in extra}  # account_option adds them again
        ctl = remember_control_type(st, ctl, old if control_option_ok(old) else CONTROL_DEFAULTS,
                                    sent[CONTROL_SWAPPED] is True)
        d = 1 - ctl["Controller"]  # the other device's preset as the screen left it
        v = sent[CONTROL_PRESET_FIELDS[d]]
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= CONTROL_TYPE_MAX[d]:
            st["controlType"][str(d)] = v
    if ctl:
        st["option"]["controlOption"] = ctl
    _save(st)
    return _json({"result": True, "resources": [{"uri": "/account/option", "body": account_option()}]})


def apply_squad(team, req):
    """Save a squad PUT {squad:{positions,formation,workrate}, kickers, captain} (instance ids)
    to a stored team. A client still holding old instance ids (e.g. from before a card
    migration) must not wipe the XI: False, team unchanged, unless it has 11 known starters."""
    sq = req.get("squad") or {}
    spid_of = {iid(p): p["spid"] for p in team["players"]}
    positions = [{"id": str(p["id"]), "spid": spid_of[str(p["id"])], "pos": p["pos"],
                  **({"starting": p["starting"]} if p.get("starting") else {})}
                 for p in sq.get("positions") or [] if str(p.get("id")) in spid_of and p.get("pos", 99) <= POS_SUB]
    if sum(p["pos"] < POS_SUB for p in positions) != 11:
        return False
    idx = req.get("presetidx")
    if isinstance(idx, int) and 0 <= idx < PRESET_SQUADS and idx != team.get("presetidx", 0):
        # a switch to another of Squad A / B / C (SquadScreenHandler sends the picked one with
        # its presetidx): the squad being left keeps its slot
        saved = (team.get("presets") or []) + [None] * PRESET_SQUADS
        saved[team.get("presetidx", 0)] = copy.deepcopy(team["squad"])
        team["presets"], team["presetidx"] = saved[:PRESET_SQUADS], idx
    team["squad"]["positions"] = positions
    for k in ("formation", "workrate"):
        if k in sq:
            team["squad"][k] = sq[k]
    if str(req.get("captain")) in spid_of:
        team["squad"]["captain"] = str(req["captain"])
    if req.get("kickers") and all(str(k) in spid_of for k in req["kickers"] if k):
        # a slot per KICKER_TYPES entry: dropping an empty one shifted the penalty taker (issue #8)
        team["squad"]["kickers"] = [str(k) if k else "" for k in req["kickers"]][:KICKERS]
    return True


def ep_coach_squad(query, body):
    """PUT /coach/me/squad {squad:{positions,formation,...}, kickers, captain} from Tim > Formasi."""
    st = _state(); slot = _default_slot(st); team = st["teams"].get(str(slot))
    if team is not None and apply_squad(team, json.loads(body or b"{}")):
        _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


def ep_match_squad(mid, side, query, body):
    """PUT /match/<id>/side/<side>/squad {squad, kickers, captain} from the pre-match squad screen
    (SquadScreenHandler.sendSquadToServer: a swap, a sub, a formation). It had no handler, so the
    generic {result: true} brought no new squad back and every change undid itself (issue #16).
    Saved to my club like Tim > Formasi; my side is rebuilt from it and comes back in the match
    resource, which the screen reads (side.<mine>.team.squad). result false shows reason."""
    st = _state(); slot = _default_slot(st); match = st.get("match") or {}
    s = (match.get("side") or {}).get(side) if match.get("id") == mid else None
    exhibition = match.get("type") == "Exhibition_PvP"  # the club of that side, not mine
    team = (s or {}).get("team") if exhibition else st["teams"].get(str(slot))
    if not s or not s["players"] or team is None or not apply_squad(team, json.loads(body or b"{}")):
        return _json({"result": False, "reason": "PLAYERLIST_CHANGEPLAYER_FAIL_POPUP_INFO_UNUSABLEPLAYER"})
    s.update(match_side(team, s["team"]["teamid"], s["players"][0], s["tactics"]), ready=s["ready"])
    if exhibition:
        s["team"]["teamcolors"] = []
    _save(st)
    return _json({"result": True, "resources": [{"uri": f"/match/{mid}", "body": match}] + coach_me_resources(st, slot)})


KICKERS = 5  # squad.kickers slots, Enums.KICKER_TYPES: left corner, right corner, free kick, penalty, free kick 2


def ep_team_role(kicker, query, body):
    """PUT /game/my/team/captain {id} and PUT /game/my/team/kickers/<KICKER_TYPES index> {id}
    from Tim > Peran (TeamManagementPlayerRolesTabPage.onPlayerSelect; issue #8: both were
    missing, so a new captain or penalty taker was never saved). id = a card instance of my club."""
    req = json.loads(body or b"{}")
    st = _state(); slot = _default_slot(st); team = st["teams"].get(str(slot))
    pid = str(req.get("id"))
    if team is None or pid not in {iid(p) for p in team["players"]}:
        return _json({"result": False})
    sq = team["squad"]
    if kicker is None:
        sq["captain"] = pid
    else:
        sq["kickers"] = ([str(k) for k in sq.get("kickers") or []] + [""] * KICKERS)[:KICKERS]
        sq["kickers"][kicker] = pid
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


def roster(teamid):
    """{summary, team} of a club: the gen_teams.py roster file if there is one (2017 squads, so
    not while 26 LIVE is the current season), else built the same way straight from fifadb."""
    f = os.path.join(RESPONSES, f"static_info_authentic_team_all_{teamid}.json")
    if os.path.isfile(f) and gen_teams.CURRENT_SEASON == gen_teams.LEGACY_SEASON:
        with open(f) as fh:
            return json.load(fh)
    with closing(_db()) as c:
        return gen_teams.team(c, teamid) or {"summary": {}, "team": {"players": [], "squad": {"positions": [], "kickers": []}}}


def roster_team(teamid):
    """A club as a stored team, for AI opponents."""
    return roster(teamid)["team"]


def match_side(raw, teamid, user=None, tactics=None):
    """match.side.<left|right>: PlayerListUtil.generateCaptainFromMatchInfo reads side.team
    {teamid, squad, players}, side.players (the users: id, name, levelinfo), MatchProxy
    fetchMatchSide* read squad, tactics, ready. tactics is the preset ARRAY (as
    /game/my/tactic/all): native 0x949170 iterates it ({id, bus, cc, def, teammentality})."""
    team = owned_team(raw)
    # the 18 first, the reserves after: in a match native names a card by its player id and the
    # HUD's season badge takes the first card of that player here (tools/fo4_season.py's getSeason
    # patch), so a reserve 26 LIVE Mbappe came before the WC18 one on the pitch and showed 26 (user:
    # "Pada saat in game badge session pemain sudah sesuai?"). The pre-match screen still gets them all.
    in18 = {str(s.get("id") or s["spid"]) for s in team["squad"]["positions"]}
    team["players"].sort(key=lambda p: p["_id"] not in in18)
    # team color for the user's side only: an AI side is a whole club (+4 for free)
    team["teamcolors"] = active_teamcolors(team) if user else []
    if user:  # my working staff: native boosts the players from these two lists
        team.update(staff_match(staff_team(raw)))
    # the pre-match screens' squad list (Attr) shows each rating without team color (card, grade,
    # level at his slot), so do their FW / MF / DF bars (user: "Hitungan stats home tidak sesuai
    # dengan skuad": 86 over a list of 79-83); the colors and staff still apply in the match
    team.update(teamid=teamid, summary=team_summary(dict(team, teamcolors=[], staffroles=[]), teamid), kit=team_kit(teamid))
    for p in team["players"]:  # "Pilihan terbaik" in a Rank squad screen: SquadUtil's best-XI filter
        p["rankedcards"] = None  # reads rankedcards.red/.yellow unless it is null (missing = TypeError)
    if user:  # the pre-match squad screen's formation list (side.<mine>.team.formations; was empty, issue #16)
        team["formations"] = team_formations(teamid)
    # ready: a user side starts unready -- MatchController.sendReady skips the PUT .../ready
    # (no kick-off) when isMySideReady is already true; AI sides are always ready.
    return {"team": team, "squad": team["squad"], "players": [user] if user else [],
            "tactics": tactics or [default_tactic(0)], "ready": user is None, "kit": team_kit(teamid),
            # the multiplay game setting screen sends club ? club.clubname : "" to native
            # PopulateGameSettings, which reads it back with asCString (a missing one crashes)
            "club": {"clubname": ""}}


# gamesettings.halfLength, in 1/10 minute per half (the game setting screen shows
# halfLength/10*2 "menit"): 30 = a 6-minute match.
HALF_LENGTH = int(os.environ.get("FO3_HALF_LENGTH", "30"))

# Pre-match settings (issue #31): stadium, time of day, weather, match length. Native
# ApplyGameSettings / PopulateGameSettings read gamesettings stadium, tod, weather, halfLength as
# ints. Only Multiple > Rank's game setting screen has steppers for them (MultiplayGameSettingScreen,
# each enabled by gamesettings stadiumchange / timechange / weatherchange / matchtimechange; the
# stadium list is GET /stadium/list; a change is PUT /match/<id>/gamesettings); Liga and Cup show
# them as labels only, so there they come from the player's last choice (st["matchprefs"], also
# set on the admin page's Match tab). Values, from the client's archives and fifazf:
# - a stadium needs its model, day + night textures, crowd placement, a stadiumdb entry and a DB
#   row; native does not check the id (no DB row = light type 5, no files = a broken scene).
STADIUMS_SAFE = (1, 2, 5, 13, 14, 26, 28, 29, 30, 32, 33, 34, 35, 41, 100, 112, 113, 115, 116, 135, 137,
                 147, 149, 153, 156, 157, 158, 175, 176, 178, 179, 180, 181, 182, 183, 192, 193, 194, 195,
                 196, 197, 212, 228, 229, 246, 247, 248, 249, 261, 262, 264, 265, 326, 327, 329, 330, 331,
                 332, 333, 335, 336, 337, 341, 343, 344, 345, 347, 348, 349, 355)
STADIUM_DEFAULT = 112  # the lobby's stadium: what every match used before
# - tod picks the stadium's light set: 1 day, 3 night (every stadium has only these two; 0, 2, 4
#   render as day, 5 has no files). The client's labels: 1 "Waktu" (its COMMON_TIME_DAY), 3 "Malam".
TODS = (1, 3)
# - weather: 0 clear, 1 rain, 2 snow, 3 overcast by day / dusk by night (every stadium renders
#   all four). Labels: Rank's stepper 3 = "Malam"; Liga/Cup's screen shows 3 as "Salju".
WEATHERS = (0, 1, 2, 3)
WEATHERS_RANDOM = (0, 1, 3)  # "random": clear, rain or overcast (snow only when picked)
HALF_LENGTHS = (30, 40, 50, 60)  # 6, 8, 10, 12 minutes: the Rank stepper's list
# balls with a model (data_2.big ball_<id>.rx3; native plays ball 88 for a missing one), a name
# (BALL_NAME_TYPE<id>) and a picture (externalAssets/balls/ball_<id>.png; 0 shows "not found")
BALLS = (0, 6, 23, 34, 39, 40, 41, 45, 69)
MATCH_PREFS = {"stadium": STADIUMS_SAFE + ("home", "random"), "tod": TODS + ("random",),
               "weather": WEATHERS + ("random",), "halfLength": tuple(sorted({*HALF_LENGTHS, HALF_LENGTH})), "ball": BALLS}


def match_prefs(st):
    """The player's pre-match choices: stadium id | "home" (the home club's) | "random", tod |
    "random", weather | "random", halfLength, ball. Unset = what every match had before (112, day,
    clear, HALF_LENGTH, ball 0)."""
    p = {"stadium": STADIUM_DEFAULT, "tod": TODS[0], "weather": 0, "halfLength": HALF_LENGTH, "ball": 0}
    p.update({k: v for k, v in (st.get("matchprefs") or {}).items()
              if not isinstance(v, bool) and v in MATCH_PREFS.get(k, ())})
    return p


def apply_prematch(st, match, rnd=random):
    """A new match's stadium / tod / weather / halfLength from match_prefs, and the Rank screen's
    steppers on. Call after gamesettings.home is set ("home" = the home side's club stadium)."""
    p, gs = match_prefs(st), match["gamesettings"]
    stadium = p["stadium"]
    if stadium == "home":
        stadium = club_stadium(match["side"]["left" if gs.get("home", True) else "right"]["team"]["teamid"])
    elif stadium == "random":
        stadium = rnd.choice(STADIUMS_SAFE)
    pick = lambda v, opts: rnd.choice(opts) if v == "random" else v
    gs.update(stadium=stadium if stadium in STADIUMS_SAFE else STADIUM_DEFAULT,
              tod=pick(p["tod"], TODS), weather=pick(p["weather"], WEATHERS_RANDOM), halfLength=p["halfLength"],
              ball={"id": p["ball"], "availables": list(BALLS)},  # ints: MatchProxy maps each to BALL_NAME_TYPE<id>
              # a FO3_HALF_LENGTH off the stepper's list keeps the length fixed
              stadiumchange=True, timechange=True, weatherchange=True, matchtimechange=p["halfLength"] in HALF_LENGTHS)


def ep_stadium_list(query, body):
    """GET /stadium/list (MatchProxy.requestStadiumInfo, read once a session): the Rank stadium
    stepper's list, [{id, name, todWeatherAvailables {"<tod>": [weather...]}}] (labelField name;
    the tod and weather steppers are built from todWeatherAvailables)."""
    return _json(stadium_choices())


def stadium_choices():
    """[{id, name, todWeatherAvailables}] of STADIUMS_SAFE by name: the client's own names (what the
    Liga/Cup screen and the match intro show), else the DB's without its "(old name)" notes."""
    names, db = client_stadium_names(), stadium_table()[0]
    rows = [{"id": s, "name": names.get(str(s)) or re.sub(r"\s*\(.*\)$", "", (db.get(s) or {}).get("name") or f"Stadium {s}"),
             "todWeatherAvailables": {str(t): list(WEATHERS) for t in TODS}} for s in STADIUMS_SAFE]
    return sorted(rows, key=lambda r: r["name"].lower())


def client_big(path, name):
    """An entry of one of the client's BIg4 archives (tools/re/bigdir.py), or None (also for a
    damaged archive: bigdir asserts and unpacks)."""
    try:
        if os.path.join(HERE, "tools", "re") not in sys.path:
            sys.path.insert(0, os.path.join(HERE, "tools", "re"))
        import bigdir
        return bigdir.read(path, name)
    except Exception as e:
        print(f"client archive {name}: {e!r}", flush=True)
        return None


def client_json(path, name):
    """A JSON object entry of a client archive, {} when missing or unreadable."""
    raw = client_big(path, name)
    try:
        d = json.loads(raw.decode("utf-8-sig")) if raw else {}
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


@functools.lru_cache(maxsize=None)
def ball_names():
    """{ball id: the client's name (BALL_NAME_TYPE<id> in its language pack)} of BALLS."""
    src = asset_path(LANG_PACK)
    s = client_json(src, LANG_ENTRY) if src else {}
    return {b: s.get(f"BALL_NAME_TYPE{b}") or f"Ball {b}" for b in BALLS}


@functools.lru_cache(maxsize=None)
def client_stadium_names():
    """{"<id>": name} of data/ui/localization/STADIUMNAMES_IND.json (Localization.STADIUMNAME)."""
    return client_json(os.path.join(GAME, "data", "ui", "localization", "ind.big"), "data/ui/localization/STADIUMNAMES_IND.json")


# The client's Indonesian strings come from the server at every start (externalAssets/
# language_ind.big, one entry LANG_ENTRY). Two labels of the game setting screens are wrong there:
# COMMON_TIME_DAY reads "Waktu" ("time") for day, and COMMON_WEATHER_DUSK reads "Malam" for weather
# 3 (overcast by day, dusk by night). The server serves a copy with LANG_FIXES (built once, again
# when the original or LANG_FIXES change: LANG_FIXED + ".key"; only those two keys use them).
# Liga/Cup's screen still shows weather 3 as "Salju": its code reuses the snow label.
# Another language's pack (FO3_LANGUAGE; Localization.loadEx: language_<locale>.big, entry
# LANGUAGESTRING_<LOCALE>.json) gets the keys it lacks: LANG_ADD's, else the Indonesian pack's
# (issue #41: the Thai one has no names for Rekrut packs 11-13 and showed STAFFPACK_NAME_11).
LANG_PACK = f"externalAssets/language_{LANGUAGE}.big"
LANG_ENTRY = f"data/ui/external/LANGUAGESTRING_{LANGUAGE.upper()}.json"
LANG_IND = ("externalAssets/language_ind.big", "data/ui/external/LANGUAGESTRING_IND.json")
LANG_ADD = {"STAFFPACK_NAME_11": "Technical Director Pack<BR>(3.0~5.0 star)",
            "STAFFPACK_NAME_12": "Premium Staff Pack<BR>(3.0~5.0 star)",
            "STAFFPACK_NAME_13": "Premium Medical Pack<BR>(1.0~3.0 star)",
            "STAFFPACK_DESC_11": 'You can get 1 technical director.<br><br><FONT color="#d8a300">Grade: 3.0~5.0 star</font>',
            "STAFFPACK_DESC_12": 'You can get 1 premium staff.<br><br><FONT color="#d8a300">Staff: fitness coach, '
                                 'technical director, head coach<br>Grade: 3.0~5.0 star</font>',
            "STAFFPACK_DESC_13": 'You can get 1 fitness coach.<br><br><FONT color="#d8a300">Grade: 1.0~3.0 star</font>'}
LANG_FIXES = {b'"COMMON_TIME_DAY": "Waktu"': b'"COMMON_TIME_DAY": "Siang"',
              b'"COMMON_WEATHER_DUSK": "Malam"': b'"COMMON_WEATHER_DUSK": "Mendung"',
              # Live Boost's one group (LIVEBOOST_GROUP) is the Team of the Week
              b'"LIVEBOOST_CUSTOM_2900": "Europe Weekly Best"': b'"LIVEBOOST_CUSTOM_2900": "Team of the Week"'}
LANG_FIXED = os.path.join(HERE, "gamedata", f"language_{LANGUAGE}.big")
LANG_LOCK = threading.Lock()


def lang_missing(raw):
    """raw (a LANGUAGESTRING json) with the keys it lacks put first: LANG_ADD's, else the Indonesian pack's."""
    have = json.loads(raw.decode("utf-8-sig"))
    ind = asset_path(LANG_IND[0])
    add = {k: v for k, v in {**(client_json(ind, LANG_IND[1]) if ind else {}), **LANG_ADD}.items() if k not in have}
    if not add:
        return raw
    i = raw.index(b"{") + 1
    return raw[:i] + b"".join(json.dumps({k: v}, ensure_ascii=False)[1:-1].encode() + b"," for k, v in add.items()) + raw[i:]


def language_pack(src):
    """The path to serve for the client's language pack at src: the fixed copy, or src itself
    when it needs no fix (or the copy cannot be made: the client gets its own pack, never no reply)."""
    try:
        s = os.stat(src)
        ind = asset_path(LANG_IND[0])
        si = os.stat(ind) if ind else None
        key = repr((os.path.abspath(src), s.st_size, s.st_mtime_ns, si and (si.st_size, si.st_mtime_ns),
                    sorted(LANG_FIXES.items()), sorted(LANG_ADD.items()))).encode()
        with LANG_LOCK:
            if os.path.isfile(LANG_FIXED) and os.path.isfile(LANG_FIXED + ".key"):
                with open(LANG_FIXED + ".key", "rb") as f:
                    if f.read() == key:
                        return LANG_FIXED
            raw = client_big(src, LANG_ENTRY)
            fixed = lang_missing(raw) if raw else b""
            for old, new in LANG_FIXES.items():
                fixed = fixed.replace(old, new)
            if not raw or fixed == raw:
                return src
            import bigdir  # client_big put tools/re on the path
            tmp = LANG_FIXED + ".tmp"
            os.makedirs(os.path.dirname(LANG_FIXED), exist_ok=True)
            with open(src, "rb") as a, open(tmp, "wb") as b:
                b.write(a.read())
            bigdir.patch(tmp, {LANG_ENTRY: fixed})  # stored (not RefPacked), appended
            os.replace(tmp, LANG_FIXED)
            with open(LANG_FIXED + ".key", "wb") as f:
                f.write(key)
            return LANG_FIXED
    except Exception as e:
        print(f"language pack fix: {e!r}", flush=True)
        return src


def new_match(mtype, left, right, **extra):
    """A /match/<id> resource (MatchProxy, base uri /match). MatchController.onMatchChangeReceived
    runs processMatch* over: side.X {ready, club, players, squad, team{teamid, players, captain,
    kit, summary.league}}, elo.X.rating, gamesettings {kit, ball, stadium, tod, weather,
    halfLength, difficulty}, result, reward, boostweekid, stage (created|squad|playing|canceled).
    These run inside a native->AS3 callback: a TypeError there aborts the client (0xDEAD)."""
    mid = f"{int(time.time()):08x}{os.urandom(8).hex()}"  # Mongo-style id
    lt, rt = left["team"]["teamid"], right["team"]["teamid"]
    match = {"id": mid, "type": mtype, "created": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
             "stage": "created", "side": {"left": left, "right": right},
             "elo": {"left": {"rating": 1000}, "right": {"rating": 1000}},
             "result": None, "reward": None, "boostweekid": live_boost_weekid(),  # the client's LIVEBOOST weekid
             # native ApplyGameSettings gets this object as-is.
             # kit: [left uniform id, right uniform id] as STRINGS: AS3 reads them in
             # processMatchSideStandingPlayerResource, native (0x9480D0) with asCString
             # home: Boolean (MatchProxy.isLeftTeamHome = gamesettings.home as Boolean)
        "gamesettings": {"home": True, "simulated": False, "canHaveReferee": False,
                              "stadiumchange": False, "timechange": False, "weatherchange": False,
                              "matchtimechange": False, "cardstrictness": 0, "foulstrictness": 0,
                              # kit: per side the KIT TYPE ("home"/"away"); native (0x948260) loads
                              # side kit.availables[<type>].id -- uniform ids here = checkerboard kits.
                              # ball.availables feeds MatchProxy.ballsDataProvider; empty -> the
                              # game setting screen's ball stepper reads item 0 = null (UI error).
                              # 0 = the ball the practice match loads (id -> db.ball model).
                              "kit": ["home", "away"],
                              "ball": {"id": 0, "availables": [{"id": 0}]},
                              # stadium 112 = the lobby stadium (data/sceneassets/stadium/stadium_112.rx3
                              # is known to load); null -> crash while loading the GAME scene
                              "stadium": 112, "tod": 0, "weather": 0, "difficulty": 0,
                              "halfLength": HALF_LENGTH,
                              # the rest is what native ApplyGameSettings (0x946FD0) reads; values are
                              # its own defaults (0x959050). Missing gameSpeed broke the match clock.
                              # officials: rows of the engine DB's referee table (ids 52..306;
                              # native falls back to 112 for the referee unless all three
                              # exist). Assistants 0 (no such row) were drawn from an all-zero
                              # record, with heads, hair and bodies that do not exist: the
                              # deformed officials at the line-up (issue #33)
                              "gameSpeed": 1, "crowd": 100, "pitchWear": 1, "refereeIdSelector": 112,
                              "subRefereeIds": [178, 157], "disableMomentum": False, "bookingsOn": True,
                              "offsidesOn": True, "injuriesOn": True, "foulsOn": True, "fatigueOn": True,
                              "subOnBench": 5, "availableSubs": 3, "drawMode": 0, "gameType": mtype,
                              "shootoutmode": False, "prevScore": {"home": 0, "away": 0}}}
    match.update(extra)
    return match


def ep_match_campaign(query, body):
    """POST /match/campaign (tutorial > Training Ground): MatchController
    .onMatchPracticeResponseReceived registers resources[] and fires ON_PRACTICE_STARTED with
    the match body -> ApplyGameSettings + StartSingleMatch (a local match in the client)."""
    st = _state(); slot = _default_slot(st); me = coach_me(st, slot)
    user = {"id": me["id"], "name": me["name"], "levelinfo": me["levelinfo"]}
    ai = 243 if me["teamid"] != 243 else 241
    # type must be a non-PvP one (MatchController.isPvPMatchType: League_league, Cup_PvE,
    # WorldCup_PvE, WorldCup_Live, League_2_league, League_2_league_MGR); anything else makes
    # stage "created" call connectToTheSession -> native LobbyManager::ConnectSession, which
    # needs relayServer.address (crash without it).
    match = new_match("League_league", match_side(st["teams"][str(slot)], me["teamid"], user, _tactics(st)[1]),
                      match_side(roster_team(ai), ai))
    return _json({"result": True, "resources": [{"uri": f"/match/{match['id']}", "body": match}]})


def roster_summary(teamid):
    """Static team info (name, league, nation, rival) written by gen_teams.py."""
    s = roster(teamid).get("summary", {})
    return {k: s[k] for k in ("name", "league", "nation", "rival") if k in s}


# --------------------------------------------------------------------------- #
# Player cards. Ratings come from the client's own fifadb, EP prices from fifaaddict.com's
# FO3 DB (Indonesia server, grade +1; gamedata/fa_prices.json, see tools/fa_sync.py).
# --------------------------------------------------------------------------- #
# all-attribute boost by grade +0..+10 (= responses/static_db_PLAYERGRADE.json): fifaaddict's
# playerEnchantList, e.g. World Best Neymar 83 base shows 88 at +1 and 93 at +5
GRADE_BOOST = (0, 5, 6, 7, 8, 10, 12, 14, 17, 20, 24)
# EP price of grade g relative to +1: geometric mean over 16 fifaaddict cards (Indonesia server)
GRADE_PRICE = (0, 1, 2.65, 7.1, 19.6, 53, 162, 420, 1090, 2680, 6290)
FA_PRICES = os.path.join(HERE, "gamedata", "fa_prices.json")
_FA = None
_OVR = {}


def fa_prices():
    """{spid: Indonesia-server EP price at +1 (0 = listed on fifaaddict without a price)};
    {} when the file is missing."""
    global _FA
    if _FA is None:
        try:
            with open(FA_PRICES) as f:
                _FA = {int(k): int(v) for k, v in json.load(f).items()}
        except OSError:
            _FA = {}
    return _FA


# The rating the client shows for a card ("Atribut"/OVR, as on fifaaddict) is Enums.calcPosOvr
# at its preferredposition1: floor(sum(stat * weight) / 100) with STAT_POSOVRS weights below
# (fifadb's overallrating column runs ~5 higher for 2017 cards). POS_ROLE[Enums.PLAYERPOSITION].
ROLE_WEIGHTS = {
    "GK": {"gkdiving": 21, "gkhandling": 21, "gkkicking": 5, "gkpositioning": 21, "gkreflexes": 21, "reactions": 11},
    "SW": {"aggression": 8, "ballcontrol": 5, "headingaccuracy": 10, "jumping": 4, "marking": 15, "reactions": 5,
           "shortpassing": 5, "slidingtackle": 15, "standingtackle": 15, "strength": 10, "interceptions": 8},
    "WB": {"acceleration": 4, "ballcontrol": 8, "crossing": 12, "dribbling": 4, "marking": 7, "reactions": 8, "shortpassing": 10,
           "slidingtackle": 11, "sprintspeed": 6, "stamina": 10, "standingtackle": 8, "interceptions": 12},
    "FB": {"acceleration": 5, "ballcontrol": 7, "crossing": 9, "headingaccuracy": 4, "marking": 8, "reactions": 8, "shortpassing": 7,
           "slidingtackle": 14, "sprintspeed": 7, "stamina": 8, "standingtackle": 11, "interceptions": 12},
    "CB": {"aggression": 7, "ballcontrol": 4, "headingaccuracy": 10, "jumping": 3, "marking": 14, "reactions": 5, "shortpassing": 5,
           "slidingtackle": 17, "sprintspeed": 2, "standingtackle": 10, "strength": 10, "interceptions": 13},
    "DM": {"aggression": 5, "ballcontrol": 10, "longpassing": 10, "marking": 9, "reactions": 7, "shortpassing": 14, "slidingtackle": 5,
           "stamina": 6, "standingtackle": 12, "strength": 4, "interceptions": 14, "vision": 4},
    "WM": {"acceleration": 7, "ballcontrol": 13, "crossing": 10, "dribbling": 15, "finishing": 6, "longpassing": 5, "positioning": 8,
           "reactions": 7, "shortpassing": 11, "sprintspeed": 6, "stamina": 5, "vision": 7},
    "CM": {"ballcontrol": 14, "dribbling": 7, "finishing": 2, "longpassing": 13, "longshots": 4, "positioning": 6, "reactions": 8,
           "shortpassing": 17, "stamina": 6, "standingtackle": 5, "interceptions": 5, "vision": 13},
    "AM": {"acceleration": 4, "agility": 3, "ballcontrol": 15, "dribbling": 13, "finishing": 7, "longpassing": 4, "longshots": 5,
           "positioning": 9, "reactions": 7, "shortpassing": 16, "sprintspeed": 3, "vision": 14},
    "CF": {"acceleration": 5, "ballcontrol": 15, "dribbling": 14, "finishing": 11, "headingaccuracy": 2, "longshots": 4, "positioning": 13,
           "reactions": 9, "shortpassing": 9, "shotpower": 5, "sprintspeed": 5, "vision": 8},
    "W": {"acceleration": 7, "agility": 3, "ballcontrol": 14, "crossing": 9, "dribbling": 16, "finishing": 10, "longshots": 4,
          "positioning": 9, "reactions": 7, "shortpassing": 9, "sprintspeed": 6, "vision": 6},
    "ST": {"acceleration": 4, "ballcontrol": 10, "dribbling": 7, "finishing": 18, "headingaccuracy": 10, "longshots": 3, "positioning": 13,
           "reactions": 8, "shortpassing": 5, "shotpower": 10, "sprintspeed": 5, "strength": 5, "volleys": 2},
}
POS_ROLE = "GK SW WB FB CB CB CB FB WB DM DM DM WM CM CM CM WM AM AM AM CF CF CF W ST ST ST W".split()
POSOVR = "(CASE p.preferredposition1 {} END)".format(" ".join(  # SQL: client rating of row p
    f"WHEN {i} THEN ({' + '.join(f'{w} * p.{k}' for k, w in ROLE_WEIGHTS[r].items())}) / 100" for i, r in enumerate(POS_ROLE)))


def card_ratings(spids):
    """{spid: (client rating, fifadb overallrating)} (cached)."""
    spids = [int(s) for s in spids]
    need = [s for s in set(spids) if s not in _OVR]
    if need:
        with closing(_db()) as c:
            for i in range(0, len(need), 500):
                chunk = need[i:i + 500]
                rows = {s: (o, d) for s, o, d in c.execute(
                    f"SELECT p.spid, {POSOVR}, p.overallrating FROM players p WHERE p.spid IN ({','.join('?' * len(chunk))})", chunk)}
                _OVR.update({s: rows.get(s, (0, 0)) for s in chunk})
    return {s: _OVR[s] for s in spids}


STAT_COLS = sorted(set().union(*ROLE_WEIGHTS.values()))
_STATS = {}


def card_stats(spids):
    """{spid: {stat: fifadb value}} of the stats any position rating uses (cached)."""
    need = [s for s in {int(s) for s in spids} if s not in _STATS]
    if need:
        with closing(_db()) as c:
            for i in range(0, len(need), 500):
                chunk = need[i:i + 500]
                rows = {r[0]: dict(zip(STAT_COLS, r[1:])) for r in c.execute(
                    f"SELECT spid, {', '.join(STAT_COLS)} FROM players WHERE spid IN ({','.join('?' * len(chunk))})", chunk)}
                _STATS.update({s: rows.get(s, dict.fromkeys(STAT_COLS, 0)) for s in chunk})
    return {int(s): _STATS[int(s)] for s in spids}


def card_ovrs(spids):
    """{spid: rating the client shows}."""
    return {s: r[0] for s, r in card_ratings(spids).items()}


def card_ovr(spid):
    return card_ovrs([spid])[int(spid)]


def grade_of(p):
    """+0..+10. A club's starting players (stored without a grade) are +0: no market value
    (CoachProxy.expandTeam counts only grade > 0), not sellable; bought/packed cards start at +1."""
    return min(10, max(0, int(p.get("grade") or 0)))


def player_ovr(p):
    """Card rating plus its enhancement boost."""
    return card_ovr(p["spid"]) + GRADE_BOOST[grade_of(p)]


def sig3(x):
    x = int(x)
    return round(x, 2 - len(str(x))) if x >= 1000 else x


def price_formula(ovr):
    """Fallback without gamedata/fa_prices.json: an early least-squares fit of fifaaddict's
    Indonesia-server prices on the client rating: exp(2.53 + 0.1113 * ovr), 65 ~ 17k, 85 ~ 160k."""
    return sig3(max(1000, math.exp(2.53 + 0.1113 * ovr)))


@functools.lru_cache(maxsize=None)
def market_spids():
    """Every tradeable card (Market, packs, trades): every card with a name (namesind; season 0 =
    engine cards) of any season, plus each one fifaaddict lists. fifaaddict alone left out 6,599
    cards, 4,313 of them '17 (user: "jangan sampai ada pemain yang tidak ada di pack, trade, dan
    market"); FO3's untradeable event cards (permid.transferable = 0: Cafu's legends, season 26)
    came in later (user: "session special tidak ada dimarket pack dan trading sepeti cafu"):
    fo4_season's install marks them transferable in the client too, which its Market search needs
    (TransferUtil.searchInLocalDatabase). Prices come from the rating either way (card_price). In
    the classic world none of the added seasons (user: "Jika classic tidak ada session tambahan di
    pack market trading"): only the admin page gives them."""
    with closing(_db()) as c:
        own = {s for (s,) in c.execute("""SELECT spid FROM players
            WHERE spid >= 1000000 AND playerid IN (SELECT playerid FROM namesind)""")}
    added = ADDED_SEASONS if gen_teams.WORLD == "classic" else ()
    return frozenset(s for s in own | set(fa_prices()) if s // 1000000 not in added)


def on_market(spid):
    return int(spid) in market_spids()


@functools.lru_cache(maxsize=None)
def rating_prices():
    """{client rating: EP price at +1}: the median fifaaddict price of all listed cards with that
    rating (ratings with 5+ priced cards), each at least 5% above the rating below; {} without
    fa_prices.json."""
    fa = fa_prices()
    by = {}
    for s, o in card_ovrs([s for s, p in fa.items() if p]).items():
        if o:
            by.setdefault(o, []).append(fa[s])
    med = {o: statistics.median(v) for o, v in by.items() if len(v) >= 5}
    table, top = {}, 0
    for o in range(min(med), max(med) + 1) if med else ():
        top = max(top * 1.05, med.get(o, 0))
        table[o] = top
    return table


def stat_price(ovr):
    """EP price at +1 of a card rated ovr."""
    t = rating_prices()
    if len(t) < 6:
        return price_formula(ovr)
    lo, hi = min(t), max(t)
    if ovr > hi:  # above every listed rating: keep the top five ratings' growth per point
        return t[hi] * (t[hi] / t[hi - 5]) ** ((ovr - hi) / 5)
    return t[max(lo, ovr)]


def card_price(spid, grade=1):
    """EP value from the card's rating and grade only (user: "fix harga yang tidak sesua
    statsnya"): fifaaddict's own prices put scarce seasons (Legend, EL, UL, WL, CC, LP...) at up
    to 50,000x a card of the same rating, and some '17 cards rated 76 at 1,000 EP. Times the admin
    page's player price setting (ECONOMY "players")."""
    return sig3(stat_price(card_ovr(spid)) * GRADE_PRICE[min(10, max(0, int(grade)))] * ECONOMY["players"])  # +0 = 0 EP


def iid(p):
    """Player instance id: str(spid) for squads made by gen_teams, a hex id for cards bought,
    opened or upgraded here (so two cards of one player can coexist, e.g. for upgrades)."""
    return str(p.get("_id") or p["spid"])


def new_iid():
    return os.urandom(12).hex()  # Mongo-style


# Levels (user: "akun dan pemain belum bisa dilevel up"). Both levelinfo = {level, exp within
# the level, to = exp the level needs}. Players: the client adds level - 1 to every attribute
# (PlayerListUtil.fillPlayerStaticData: totalBonus = level - 1 + grade boost; fifaaddict lists
# LV.1-23); EXPPLAYER {level: {requiredplayerexp}}, max level = rows - 1 (getMaxLevel). Coach:
# EXPCOACH {level: {totalexp}} = exp to finish levels 1..level (TeamManagementCoachExpScreen),
# max from STATICVALUES/max_coachlevel. The original tables were server data; curves made up.
PLAYER_MAX_LEVEL, COACH_MAX_LEVEL = 23, 100


def player_need(level):
    return 100 * level


def coach_need(level):
    return 500 + 100 * (level - 1)


EXPPLAYER = {str(lv): {"requiredplayerexp": player_need(lv)} for lv in range(1, PLAYER_MAX_LEVEL + 2)}
EXPCOACH = {str(lv): {"totalexp": sum(coach_need(k) for k in range(1, lv + 1))} for lv in range(1, COACH_MAX_LEVEL + 1)}


def make_levelinfo(info, need, top):
    level = min(top, max(1, int((info or {}).get("level") or 1)))
    return {"level": level, "exp": 0 if level >= top else int((info or {}).get("exp") or 0), "to": need(level)}


def player_levelinfo(p):
    return make_levelinfo(p.get("levelinfo"), player_need, PLAYER_MAX_LEVEL)


def coach_levelinfo(coach):
    return make_levelinfo(coach.get("levelinfo"), coach_need, COACH_MAX_LEVEL)


def gain_exp(owner, n, need, top):
    """Add n exp to owner["levelinfo"] (levels up, stops at top); (prev, current) levelinfo."""
    prev = make_levelinfo(owner.get("levelinfo"), need, top)
    level, exp = prev["level"], prev["exp"] + n
    while level < top and exp >= need(level):
        exp -= need(level)
        level += 1
    owner["levelinfo"] = {"level": level, "exp": 0 if level >= top else exp}
    return prev, make_levelinfo(owner["levelinfo"], need, top)


# --------------------------------------------------------------------------- #
# Player Growth ("Perkembangan", Tim > Manajemen pemain; user: "...fo3player_growth.php?id=88020801
# apakah fitur ini bisa diaktifkan kembali", "jangan lupa implementasikan untuk virtual manager juga").
# The client's page (TeamManagementPlayerGrowthExpansionTabPage, feature
# teammanagement.playermanagement.expansion) lists max-level cards (STATICVALUES max_playerlevel); each
# +1 step of a stat costs tactic points ("poin taktik", coach sp) by the tier of the stat's value v =
# fifadb base + GROWTH_BASE + the card's growth so far (PlayerListUtil.convertStatValueForPlayerGrowth:
# re-based to max level and grade +1), GROWTH_TIERS = fifaaddict's FO3 growth simulator table, one
# group for every stat; a card takes GROWTH_PLAYER_LIMIT points at most. A card's growth is
# sp {used, stats {stat: +n}}: with the feature on the client adds sp.stats to the stats it shows and
# hands the match engine (staticdata.statusAttribute), so a grown card plays grown. Tactic points
# come from live matches' records (growth_after_match), GROWTH_CONVERSION exp a point, until
# GROWTH_COACH_LIMIT have been earned; a PLAYER_GROWTH_RESET item gives a card's points back.
GROWTH_STATS = ("strength stamina acceleration sprintspeed jumping agility balance slidingtackle dribbling ballcontrol "
                "marking standingtackle crossing shortpassing finishing longpassing shotpower headingaccuracy longshots "
                "volleys curve freekickaccuracy penalties interceptions positioning vision reactions aggression gkkicking "
                "gkdiving gkhandling gkpositioning gkreflexes").split()  # PlayerListUtil.PLAYER_STATS
GROWTH_TIERS = ((0, 59, 4), (60, 79, 5), (80, 89, 7), (90, 99, 10), (100, 109, 12), (110, 119, 15),
                (120, 129, 20), (130, 139, 30), (140, 9999, 60))  # (lowest v, highest v, points for +1)
GROWTH_BASE = PLAYER_MAX_LEVEL - 1 + GRADE_BOOST[1]  # v = fifadb value + this + growth
GROWTH_PLAYER_LIMIT, GROWTH_COACH_LIMIT, GROWTH_CONVERSION, GROWTH_MATCH_LIMIT = 60, 1200, 500, 1000
GROWTH_IGR = GROWTH_VIP = 1.1  # x the exp of a match, for a warnet player and a VIP (always both here)
# record of the match stats: (its name, exp each); the records are capped at GROWTH_MATCH_LIMIT a
# match (the client caps at 1000 too), the result's exp comes on top
GROWTH_RECORDS = {"score": ("PGE_RECORD_GOALS", 100), "assists": ("PGE_RECORD_ASSISTS", 80),
                  "passes_inbox": ("PGE_RECORD_CREATE_CHANCE", 40), "intercepts_inbox": ("PGE_RECORD_KEY_CUT", 40),
                  "saves": ("PGE_RECORD_SAVE", 40), "shotsOnGoal": ("PGE_RECORD_SHOTONTARGET", 25),
                  "dribble_inbox": ("PGE_RECORD_KEY_DRIBBLE", 25), "tackles_inbox": ("PGE_RECORD_KEY_TACKLE", 25),
                  "tackles": ("PGE_RECORD_TACKLE_SUCCESS", 8), "shotAttempt": ("PGE_RECORD_SHOOTING", 5),
                  "passes": ("PGE_RECORD_PASS_SUCCESS", 2), "dribbles": ("PGE_RECORD_DRIBBLE_SUCCESS", 4),
                  "intercepts": ("PGE_RECORD_CUT_SUCCESS", 8)}
GROWTH_RESULTS = {"win": ("PGE_RECORD_WIN", 300), "draw": ("PGE_RECORD_DRAW", 150), "lose": ("PGE_RECORD_LOSE", 60)}


def growth_db():
    """/static/db/PLAYER_EXTRA_GROWTH: StaticData reads it at every login and hands it to native
    SetPlayerGrowthExtData (an object: [] crashed the client, null stalls the login). Page:
    config.point player_limit, statgroups, growth tiers (numbers); post-match: conversion; the match
    HUD: match_limit, records (names start PGE_), record.show_*. Not config.show_attainment (the
    HUD's "x%" steps): the client quit at login with it, as ints or as strings (tried live)."""
    return {"config": {"point": {"coach_limit": GROWTH_COACH_LIMIT, "player_limit": GROWTH_PLAYER_LIMIT,
                                 "conversion": GROWTH_CONVERSION, "match_limit": GROWTH_MATCH_LIMIT,
                                 "igr": GROWTH_IGR, "vip": GROWTH_VIP},
                       "record": {"show_time_growth": 2, "show_limit_growth": 25}},
            "records": {k: {"name": name, "standard": exp, "3on3": exp} for k, (name, exp) in {**GROWTH_RECORDS, **GROWTH_RESULTS}.items()},
            "statgroups": dict.fromkeys(GROWTH_STATS, 1),
            "growth": {"1": [{"min": lo, "max": hi, "require": n} for lo, hi, n in GROWTH_TIERS]}}


def growth_cost(v):
    """Points for +1 of a stat whose value is v (GROWTH_BASE in)."""
    return next(n for lo, hi, n in GROWTH_TIERS if lo <= v <= hi)


@functools.lru_cache(maxsize=4096)
def growth_base(spid):
    """{stat: fifadb value} of a card, all GROWTH_STATS."""
    with closing(_db()) as c:
        r = c.execute(f"SELECT {', '.join(GROWTH_STATS)} FROM players WHERE spid = ?", (int(spid),)).fetchone()
    return dict(zip(GROWTH_STATS, r)) if r else dict.fromkeys(GROWTH_STATS, 0)


def growth_add(spid, sp, steps, budget):
    """Put +1 steps on a card's sp {used, stats} in order (steps: [stat, ...]) while the next one
    costs no more than budget (and the card's limit) allows; (points spent, {stat: steps taken})."""
    base, spent, done = growth_base(spid), 0, {}
    stats = sp.setdefault("stats", {})
    for k in steps:
        cost = growth_cost(base[k] + GROWTH_BASE + stats.get(k, 0))
        if spent + cost > budget or sp.get("used", 0) + cost > GROWTH_PLAYER_LIMIT:
            continue
        stats[k] = stats.get(k, 0) + 1
        sp["used"] = sp.get("used", 0) + cost
        spent += cost
        done[k] = done.get(k, 0) + 1
    return spent, done


def coach_sp(coach):
    """My tactic points: {total earned, used (on the cards), exp toward the next}."""
    sp = coach.setdefault("growth", {})
    for k in ("total", "used", "exp"):
        sp.setdefault(k, 0)
    return sp


def coach_sp_out(coach):
    """/coach/me sp (CoachProxy.fetchCoachMeSPResource): total, used, exp."""
    sp = coach.get("growth") or {}
    return {"total": int(sp.get("total", 0)), "used": int(sp.get("used", 0)), "exp": int(sp.get("exp", 0))}


def match_outcome(stats):
    """1 won, 0 drawn, -1 lost for my side of matchStats; a penalty shootout decides a level score."""
    k = lambda s: tuple(int((stats.get(s) or {}).get(f) or 0) for f in ("score", "shootout"))
    me, opp = k(stats.get("myTeam", "home")), k(stats.get("opponentTeam", "away"))
    return (me > opp) - (me < opp)


def growth_after_match(st, stats, played=True):
    """match.reward.sp (PostMatchReward setCoachSP: descriptions {record: {count, exp}}, prev and
    current {total, exp}; without it the screen broke with the feature on) and the exp it gives:
    my side's records (count x exp, capped at GROWTH_MATCH_LIMIT) plus the result's, x the warnet and
    VIP bonus, until GROWTH_COACH_LIMIT points were earned. Not played (an exhibition of another's
    side): nothing gained."""
    sp = coach_sp(my_coach(st))
    prev = {"total": sp["total"], "exp": sp["exp"]}
    me, opp = stats.get("myTeam", "home"), stats.get("opponentTeam", "away")
    side = stats.get(me) or {}
    count = lambda v: int(v[0] if isinstance(v, list) and v else v or 0)  # [succeeded, tried] or a count
    desc = {k: {"count": count(side.get(k)), "exp": count(side.get(k)) * e} for k, (_, e) in GROWTH_RECORDS.items()
            if count(side.get(k)) > 0} if played else {}
    result = ("lose", "draw", "win")[match_outcome(stats) + 1]
    if played:
        desc[result] = {"count": 1, "exp": GROWTH_RESULTS[result][1]}
    gain = min(GROWTH_MATCH_LIMIT, sum(d["exp"] for k, d in desc.items() if k in GROWTH_RECORDS)) + \
        sum(d["exp"] for k, d in desc.items() if k in GROWTH_RESULTS)
    if sp["total"] < GROWTH_COACH_LIMIT:
        sp["exp"] += round(gain * GROWTH_IGR * GROWTH_VIP)
        sp["total"] = min(GROWTH_COACH_LIMIT, sp["total"] + sp["exp"] // GROWTH_CONVERSION)
        sp["exp"] = sp["exp"] % GROWTH_CONVERSION if sp["total"] < GROWTH_COACH_LIMIT else 0
    return {"descriptions": desc, "prev": prev, "current": {"total": sp["total"], "exp": sp["exp"]}}


def ep_player_growth(card_id, query, body):
    """PUT /player/<id>/extra/growth {stats: {stat: +n}} (the Growth button; a POST with _method
    PUT): the steps a max-level card of mine can still take within my free points and its limit.
    The page always says "Pemain X sudah berkembang" and redraws from the pushed /coach/me only, so
    a refused step simply does not show."""
    req = json.loads(body or b"{}")
    st = _state(); slot = _default_slot(st); coach = my_coach(st)
    p = next((p for p in st["teams"][str(slot)]["players"] if iid(p) == card_id), None)
    spent, done = 0, {}
    if p is not None and player_levelinfo(p)["level"] >= PLAYER_MAX_LEVEL:
        sp, csp = p.get("sp") or {"used": 0, "stats": {}}, coach_sp(coach)
        steps = [k for k, n in (req.get("stats") or {}).items() if k in GROWTH_STATS and isinstance(n, int) and n > 0
                 for _ in range(min(n, 99))]
        spent, done = growth_add(p["spid"], sp, steps, csp["total"] - csp["used"])
        if spent:
            p["sp"] = sp
            csp["used"] += spent
            _save(st)
    return _json({"result": True, "usedsp": {"point": spent, "detail": done}, "resources": coach_me_resources(st, slot)})


@functools.lru_cache(maxsize=4096)
def card_role(spid):
    """The rating role (POS_ROLE) of a card's own position."""
    with closing(_db()) as c:
        r = c.execute("SELECT preferredposition1 FROM players WHERE spid = ?", (int(spid),)).fetchone()
    return POS_ROLE[r[0]] if r and 0 <= r[0] < len(POS_ROLE) else "CM"


def vm_growth(vid, spid, role, budget):
    """A virtual manager's card's growth (user: "jangan lupa implementasikan untuk virtual manager
    juga"): budget points, each +1 on a stat of the card's role drawn by its weight in the rating
    (seeded by the manager and the card), as a manager grows his own; None for no budget."""
    if budget <= 0:
        return None
    r = random.Random(vmseed(f"{vid}/growth/{spid}"))
    w = ROLE_WEIGHTS[role]
    sp = {"used": 0, "stats": {}}
    growth_add(spid, sp, r.choices(list(w), list(w.values()), k=60), budget)
    return sp if sp["used"] else None


# Tim > Formasi combobox = coach team.formations (SquadScreenHandler.initializeFormationComboBox:
# entry 0 is shown with the club's name; picking one runs changeFormation: positions[i] -> squad
# slot i, offsets[i] -> starting x/y, then SquadUtil.getBestSquadPositions). Entries come from the
# client's own formations table (data_0.big data/db/fifa_ng_db.db): teamid 1 holds the 33 common
# shapes (their sweeper variants left out, like FORMATIONS_COMMON_WITHOUT_SWEEPER), other teams
# their own. That table numbers positions without SW: client Enums.PLAYERPOSITION = code + 1 (GK 0).
FORMATIONS_CACHE = os.path.join(HERE, "gamedata", "formations.json")
_FORMATIONS = None


def formation_table():
    """{"common": [entry], "teams": {teamid: entry}}; entry = {formationid, formationname,
    positions, offsets}. Read once from the client, then cached in gamedata/formations.json."""
    global _FORMATIONS
    if _FORMATIONS is None:
        try:
            with open(FORMATIONS_CACHE) as f:
                _FORMATIONS = json.load(f)
        except OSError:
            _FORMATIONS = {"common": [], "teams": {}}
            try:
                sys.path.insert(0, os.path.join(HERE, "tools", "re"))
                import bigdir, eadb
                big = os.path.join(GAME, "data", "data_0.big")
                rows = eadb.rows(bigdir.read(big, "data/db/fifa_ng_db.db"),
                                 bigdir.read(big, "data/db/fifa_ng_db-meta.xml").decode("utf-8", "replace"), "formations")
            except (OSError, KeyError, ImportError) as e:
                print("formations unavailable:", e, flush=True)
                return _FORMATIONS
            entry = lambda r: {"formationid": r["formationid"], "formationname": r["formationname"],
                               "positions": [r[f"position{i}"] + (r[f"position{i}"] > 0) for i in range(11)],
                               "offsets": [{"x": round(r[f"offset{i}x"], 4), "y": round(r[f"offset{i}y"], 4)} for i in range(11)]}
            _FORMATIONS["common"] = sorted((entry(r) for r in rows if r["teamid"] == 1 and not r["issweeper"]),
                                           key=lambda e: e["formationname"])
            _FORMATIONS["teams"] = {str(r["teamid"]): entry(r) for r in rows if r["teamid"] != 1 and not r["issweeper"]}
            os.makedirs(os.path.dirname(FORMATIONS_CACHE), exist_ok=True)
            with open(FORMATIONS_CACHE, "w") as f:
                json.dump(_FORMATIONS, f)
    return _FORMATIONS


STADIUMS_CACHE = os.path.join(HERE, "gamedata", "stadiums.json")


@functools.lru_cache(maxsize=None)
def stadium_table():
    """({stadium id: {name, yearbuilt, capacity}}, {club teamid: stadium id}) from the client's
    own fifa_ng_db, cached in gamedata/stadiums.json. stadiums.yearbuilt counts years after 1800
    (Bernabeu 147 = 1947), a "_" name is a generic stadium (an unlicensed club's); a club's
    stadium is the one with its hometeamid, else its teamstadiumlinks row (teamid there = real - 1)."""
    try:
        with open(STADIUMS_CACHE, encoding="utf-8") as f:
            t = json.load(f)
    except OSError:
        try:
            sys.path.insert(0, os.path.join(HERE, "tools", "re"))
            import bigdir, eadb
            big = os.path.join(GAME, "data", "data_0.big")
            db = bigdir.read(big, "data/db/fifa_ng_db.db")
            meta = bigdir.read(big, "data/db/fifa_ng_db-meta.xml").decode("utf-8", "replace")
            rows, links = eadb.rows(db, meta, "stadiums"), eadb.rows(db, meta, "teamstadiumlinks")
        except (OSError, KeyError, ImportError) as e:
            print("stadiums unavailable:", e, flush=True)
            return {}, {}
        t = {"stadiums": {str(r["stadiumid"]): {"name": r["name"].lstrip("_"), "yearbuilt": 1800 + r["yearbuilt"],
                                                "capacity": r["capacity"]} for r in rows},
             "teams": {**{str(r["teamid"] + 1): r["stadiumid"] for r in links},
                       **{str(r["hometeamid"]): r["stadiumid"] for r in rows if r["hometeamid"]}}}
        os.makedirs(os.path.dirname(STADIUMS_CACHE), exist_ok=True)
        with open(STADIUMS_CACHE, "w", encoding="utf-8") as f:
            json.dump(t, f, ensure_ascii=False)
    return {int(k): v for k, v in t["stadiums"].items()}, {int(k): v for k, v in t["teams"].items()}


def club_stadium(teamid):
    """A club's home stadium id (0: none in the client's database, e.g. a national team)."""
    return stadium_table()[1].get(int(teamid or 0), 0)


def ep_stadium(sid, query, body):
    """GET /stadium/<id> (TeamStaticInfo.onStadiumChanged via StaticData.getData): the home
    stadium's name and "yearbuilt / capacity" line in Tim > Klub > Informasi tim (issue #9)."""
    return _json(stadium_info(sid))


def stadium_info(sid):
    return {"id": int(sid), **(stadium_table()[0].get(int(sid)) or {"name": "-", "yearbuilt": "-", "capacity": "-"})}


def ep_team_summary(teamid, query, body):
    """GET /static/info/team/summary/<id> {summary, stadium, kit}: Item > Ganti tim
    (TeamChangeScreenSelectPage.onReceivedTeamInfo) shows summary name, league.id, rival.name and
    the stadium's name and "yearbuilt / capacity", and dresses its 3D player in kit.availables
    (no kit = no 3D player, birth date, height or weight; issue #23). The Cup and Boxing Day club
    pickers read summary.league from the same body."""
    t = int(teamid)
    return _json({"id": t, "summary": roster(t)["summary"], "stadium": stadium_info(club_stadium(t)), "kit": team_kit(t)})


def team_formations(teamid):
    """The club's own formation first (else 4-4-2(1), the shape of the gen_teams XI), then the
    common ones."""
    t = formation_table()
    own = t["teams"].get(str(teamid)) or next((e for e in t["common"] if e["formationname"] == "4-4-2(1)"), None)
    return ([own] if own else []) + [e for e in t["common"] if own is None or e["formationid"] != own["formationid"]]


def fix_formation(team, teamid):
    """A stored team built by gen_teams carries formations = [its 4-4-2, id 1] and squad
    .formation 1, which is 3-4-1-2 in the client's table: point it at the club's own entry."""
    if team.pop("formations", None) is not None and team["squad"].get("formation") in (None, 1):
        forms = team_formations(teamid)
        if forms:
            team["squad"]["formation"] = forms[0]["formationid"]


# Team color (user: "belum ada bonus stats pada saat memenuhi syarat tim/negara/ sesion").
# TeamColorController (data/ui/libs/Module.big) runs only with features.teamcolor
# (responses/account_base.json) and GET /static/info/teamcolor {teamColors, teamColorSpecials}.
# A color: {teamcolorid, type club|nation|special, name, description (both go through
# getLocalizedString, which returns an unknown key as-is), teamid (-1 = any club/nation),
# boostgroups [{count_min, count_max, boosts [{type "ovr" = every attribute, amount}]}]}.
# The server picks the colors the starting XI meets: team.teamcolors [{teamcolorid, boostindex,
# teamid}] in coach/me and the user's match side (isValidTeamData needs the array). The client
# then boosts the XI players that fit (availableTeamColor: club = staticdata.teamid or a club in
# clubhistory, nation = nationality, special = spid in teamColorSpecials[teamid]); per player only
# the best of club / nation / special applies, they do not stack. 17 and 131 are the client's own
# "Single team" / "Satu Negara" (LANGUAGESTRING TEAMCOLOR_ATTRIBUTES_TEAM<id>).
TC_CLUB, TC_NATION, TC_SEASON = 17, 131, 50000  # a season color is 50000 + its first season code


def _tc_groups(*tiers):
    return [{"count_min": lo, "count_max": hi, "boosts": [{"type": "ovr", "amount": n}]} for lo, hi, n in tiers]


TC_CLUB_GROUPS = _tc_groups((4, 5, 1), (6, 7, 2), (8, 9, 3), (10, 11, 4))
TC_GROUPS = _tc_groups((5, 6, 1), (7, 8, 2), (9, 11, 3))  # nation, season
# Upgrade colors (user: "tambah tim color untuk upgrade"): type "grade" = XI cards of at least
# that grade (availableTeamColor: player.grade >= color.grade; native 0x94EB70 reads color.grade
# as an int, no teamid). Names are the client's own; the boosts are ours.
TC_GRADE = (  # (teamcolorid, grade, name key, boostgroups)
    (10002, 2, "TEAMCOLOR_ATTRIBUTES_EVOLVE24", TC_GROUPS),
    (10005, 5, "TEAMCOLOR_ATTRIBUTES_EVOLVE10011", _tc_groups((5, 6, 2), (7, 8, 3), (9, 11, 4))),
    (10008, 8, "TEAMCOLOR_ATTRIBUTES_EVOLVE810", _tc_groups((5, 6, 3), (7, 8, 4), (9, 11, 5))),
)
# The client's single-grade colors (user: "Grade +3…+10 dulu", then "Bonus kecilkan dulu"):
# "+3 club" … "+10 Golden summit", all 11 starters at that grade or higher; one bonus per name
# tier (club / silver / Golden passion / Golden summit). A player gets only his best grade color
# (ChooseTeamColorBoostsPerPlayer keeps one __gradeTeamColor, added to his best club / nation /
# season color), so each tier tops the range color above it at 9-11 starters (+3 / +4 / +5).
TC_GRADE += tuple((10100 + g, g, f"TEAMCOLOR_ATTRIBUTES_EVOLVE{g}", _tc_groups((11, 11, n)))
                  for g, n in ((3, 4), (4, 4), (5, 5), (6, 5), (7, 5), (8, 6), (9, 6), (10, 7)))


def tc_seasons():
    """(label, card season codes) of every season color: fifaaddict's seasons and '07."""
    return (("07", (7,)),) + tuple((label, codes) for label, codes, _ in FA_SEASONS)


def tc_season_of(code):
    """Season color id of a card season code (None: no color, e.g. engine cards)."""
    return next((TC_SEASON + codes[0] for _, codes in tc_seasons() if code in codes), None)


# The client's own named colors (user: "pakai semua team color yang belum masuk"): names are its
# LANGUAGESTRING TEAMCOLOR_ATTRIBUTES_TEAM<id>. Their boosts and player lists were server data
# and are gone; what the client still has is one line per color, TOOLTIP<id>, so each named
# color gets our base tiers plus that boost and beats the generic color it overlaps (a player
# gets his best club / nation / special color: getBoostStatPoint sums the boosts that fit his
# position, an OVR one counting once per stat). A boost {type, amount, target} adds amount to that
# stat (type "ovr": every stat) of players in that position group (target fw/mf/df/gk, none = all;
# ApplyTeamColorBoostStat); the native half-time recheck (0x94EB70) reads only type, teamid,
# season, grade and __teamid, never boosts. Boosts are (target, stat, amount); "Grade +N" and
# "{0} +{1}" tooltips (stat not known) count as OVR. Left out: the [Test] colors 70000-70015,
# "Upgraded team (+2~7)" (the same as +2~4), "14 WC Single Nation" (the 14 WC nations cover it)
# and the newer copies of Atletico, Roma, Napoli and Monaco (20055-20058).
TC_NAMED_CLUBS = (  # (TEAM<id>, club teamid; a player counts for his club now or in his history)
    (1, 243, ("fw", "sprintspeed", 2)), (2, 241, ("mf", "agility", 3)), (3, 9, ("fw", "balance", 1)),
    (4, 10, ("fw", "longpassing", 2)), (5, 11, ("fw", "reactions", 2)), (6, 1, ("mf", "stamina", 2)),
    (7, 5, ("mf", "dribbling", 1)), (8, 18, ("mf", "acceleration", 2)), (9, 22, ("mf", "positioning", 2)),
    (10, 32, ("fw", "sprintspeed", 8)), (11, 21, ("mf", "finishing", 2)), (12, 47, ("fw", "positioning", 1)),
    (13, 45, ("df", "stamina", 2)), (14, 44, ("fw", "dribbling", 1)), (15, 73, ("fw", "ballcontrol", 2)),
    (16, 982, ("mf", "stamina", 12)), (43, 240, ("", "ovr", 1)), (44, 52, ("", "ovr", 1)),
    (45, 236, ("", "ballcontrol", 3)), (46, 69, ("", "stamina", 2)), (47, 110374, ("", "positioning", 3)),
    (48, 48, ("", "dribbling", 2)), (146, 981, ("", "ovr", 1)), (885, 110955, ("", "headingaccuracy", 4)),
    (20054, 112172, ("", "ovr", 1)), (20059, 72, ("", "ovr", 1)), (20060, 1477, ("", "ovr", 1)),
)
TC_NAMED_NATIONS = (  # (TEAM<id>, nationality)
    (101, 45, ("mf", "ballcontrol", 2)), (102, 21, ("fw", "positioning", 3)), (103, 54, ("fw", "dribbling", 2)),
    (104, 38, ("fw", "dribbling", 2)), (105, 52, ("fw", "balance", 2)), (106, 60, ("fw", "agility", 2)),
    (107, 56, ("mf", "shotpower", 3)), (108, 27, ("mf", "shortpassing", 3)), (109, 14, ("mf", "stamina", 1)),
    (110, 7, ("mf", "ballcontrol", 2)), (111, 95, ("fw", "jumping", 2)), (112, 34, ("fw", "reactions", 1)),
    (113, 18, ("fw", "positioning", 2)), (114, 10, ("fw", "headingaccuracy", 2)), (115, 40, ("fw", "ballcontrol", 2)),
    (116, 83, ("fw", "reactions", 2)), (117, 108, ("mf", "reactions", 2)), (118, 117, ("mf", "shortpassing", 2)),
    (119, 167, ("mf", "aggression", 2)), (120, 163, ("mf", "shortpassing", 2)), (121, 195, ("fw", "positioning", 2)),
    (122, 55, ("fw", "sprintspeed", 2)), (123, 49, ("fw", "finishing", 1)), (124, 50, ("fw", "reactions", 2)),
    (125, 46, ("fw", "shotpower", 2)), (126, 13, ("fw", "headingaccuracy", 2)), (127, 51, ("fw", "jumping", 2)),
    (128, 103, ("fw", "acceleration", 2)), (129, 133, ("fw", "sprintspeed", 1)), (130, 12, ("fw", "positioning", 2)),
)
# season colors the client names: card season code -> (TEAM<id> or a name of ours, boost); a season
# color keeps at least its +3 (2002 Legends, '16 Europe/Copa and EU Legends say +1). A name the
# client has no string for shows as given (Localization.getLocalizedString returns such a key itself):
# WC18 (tools/fo4_season.py) is named as FO3's own "2014 WC All-Star" (TEAM42) and boosts as it does
# (user: "Tambah tem color wc18")
TC_SEASON_NAMES = {91: (18, ("", "ovr", 5)), 93: (100, ("", "ovr", 3)), 95: (19, ("", "ovr", 3)),
                   77: (42, ("", "reactions", 1)), 65: (140, ("", "ovr", 3)), 53: (141, ("", "ovr", 3)),
                   47: (142, ("", "ovr", 3)), 63: (143, ("", "ovr", 3)), 69: (137, ("", "ovr", 3)),
                   67: (138, ("", "ovr", 3)), 35: (145, ("", "ovr", 3)), 27: (20044, ("", "ovr", 3)),
                   78: ("2018 WC All-Star", ("", "reactions", 1))}
# WC18's nations, named as FO3's "14 WC <nation>" colors (TEAM20..39, their spelling where they have
# one): color 60000 + EA nation id (below the client's [Test] colors at 70000), the nation's WC18
# cards, OVR +1 / +2 / +3 at 5-6 / 7-8 / 9-11 (a nation with fewer cards: all of them, +3)
TC_WC18_NATIONS = {45: "Spain", 21: "Germany", 54: "Brazil", 38: "Portugal", 52: "Argentina", 60: "Uruguay",
                   56: "Colombia", 14: "England", 7: "Belgium", 18: "Perancis", 10: "Kroasia", 40: "Russia",
                   83: "Mexico", 167: "Korea Selatan", 163: "Japan", 13: "Denmark", 37: "Poland", 47: "Switzerland",
                   46: "Sweden", 129: "Morocco", 24: "Iceland", 136: "Senegal", 51: "Serbia", 111: "Egypt",
                   72: "Costa Rica", 133: "Nigeria", 161: "Iran", 59: "Peru", 145: "Tunisia", 195: "Australia",
                   183: "Saudi Arabia", 87: "Panama"}
TC_WC18 = 60000
TC_LEGENDS = (93, 30, 27, 71, 51, 61, 89, 48, 55, 49)  # WL UL EL ML TL VL CL MUL MUA TCO92
TC_YEARS = (6, 7, 8, 9, 10, 11, 14, 15, 16, 88)


def _in(ids):
    return ",".join(map(str, ids))


def _S(*codes):  # card season
    return f"p.spid / 1000000 IN ({_in(codes)})"


def _CLUB(*ids):  # club now or in the player's history
    return f"(p.teamid IN ({_in(ids)}) OR p.playerid IN (SELECT playerid FROM playerhistory WHERE teamid IN ({_in(ids)})))"


def _PIDS(*ids):  # these players, any card
    return f"p.playerid IN ({_in(ids)})"


_SPECIAL = f"p.spid >= 1000000 AND p.spid / 1000000 NOT IN ({_in(TC_YEARS + TC_LEGENDS)})"  # special seasons


_TC_OVR = ("", "ovr", 3)
TC_NAMED_SPECIALS = (  # (TEAM<id>, description, SQL condition on players p, boost, best N Market cards)
    *((tid, "Pemain 14 WC dari negara ini", f"{_S(77)} AND p.nationality = {n}", b, None) for tid, n, b in (
        (20, 45, ("", "ovr", 2)), (21, 21, ("", "ovr", 3)), (22, 54, ("", "ballcontrol", 2)),
        (23, 38, ("", "positioning", 2)), (24, 52, ("", "dribbling", 2)), (25, 60, ("fw", "longshots", 3)),
        (26, 56, ("", "strength", 1)), (27, 27, ("", "agility", 2)), (28, 14, ("", "longpassing", 2)),
        (29, 7, ("", "shortpassing", 2)), (30, 95, ("mf", "stamina", 2)), (31, 34, ("", "ballcontrol", 2)),
        (32, 18, ("", "strength", 1)), (33, 10, ("fw", "acceleration", 2)), (34, 40, ("", "positioning", 2)),
        (35, 83, ("", "headingaccuracy", 2)), (36, 108, ("", "marking", 3)), (37, 117, ("", "reactions", 2)),
        (38, 167, ("", "reactions", 2)), (39, 163, ("", "shortpassing", 2)))),
    *((TC_WC18 + n, "Pemain WC18 dari negara ini", f"{_S(78)} AND p.nationality = {n}", _TC_OVR, None) for n in TC_WC18_NATIONS),
    *((tid, "50 kartu terbaik negara ini", f"p.nationality = {n}", _TC_OVR, 50) for tid, n in (
        (20007, 21), (20008, 45), (20009, 18), (20010, 27), (20011, 14), (20012, 54), (20013, 52),
        (20014, 7), (20015, 38), (20016, 56), (20017, 60))),
    *((tid, "Pemain EC / Copa dari negara ini", f"{_S(35, 24)} AND p.nationality = {n}", _TC_OVR, None) for tid, n in (
        (20018, 21), (20019, 45), (20020, 18), (20021, 27), (20022, 14), (20023, 54), (20024, 52),
        (20025, 7), (20026, 38), (20027, 56))),
    *((tid, "Pemain season '10 klub ini", f"{_S(10)} AND {_CLUB(club)}", b, None) for tid, club, b in (
        (133, 241, ("fw", "positioning", 5)), (134, 47, ("fw", "sprintspeed", 5)),
        (135, 22, ("mf", "aggression", 5)), (136, 240, ("mf", "vision", 5)))),
    (132, "Pemain season spesial (bukan tahunan, bukan legenda)", _SPECIAL, ("", "ovr", 2), None),
    (139, "Juara liga 2016/17 (season '17)", f"{_S(88)} AND p.teamid IN (5, 243, 21, 45, 69)", ("", "ovr", 3), None),
    (41, "30 kartu terbaik", "1", ("", "shortpassing", 2), 30),
    (2014, "Kartu OVR 85 ke atas", f"{POSOVR} >= 85", ("", "ovr", 4), None),
    (886, "Pemain legenda dari China", f"p.nationality = 155 AND {_S(*TC_LEGENDS)}", ("", "ovr", 3), None),
    (20002, "Messi, Suarez, Neymar", _PIDS(158023, 176580, 190871), _TC_OVR, None),
    (20003, "Bale, Benzema, Cristiano Ronaldo", _PIDS(173731, 165153, 20801), _TC_OVR, None),
    (3059, "Messi dan Cristiano Ronaldo", _PIDS(158023, 20801), _TC_OVR, None),
    (3060, "Ramos, Pepe, Marcelo, Carvajal, Varane, Navas", _PIDS(155862, 120533, 176676, 204963, 201535, 193041), _TC_OVR, None),
    (3058, "Leicester City juara 2015/16", _PIDS(208830, 204485, 215914, 186132, 136113, 137785, 163587, 183130,
                                                 194365, 169600, 139313), _TC_OVR, None),
    (3064, "Pemain AC Milan dan Inter", _CLUB(47, 44), _TC_OVR, None),
    (34024, "Pemain AC Milan (rossoneri)", _CLUB(47), _TC_OVR, None),
    (20030, "Arsenal 2003/04 tak terkalahkan", _PIDS(805, 388, 119152, 34079, 6975, 1419, 1605, 4000, 1625,
                                                      140526, 111093), _TC_OVR, None),
    (20031, "Shevchenko dan Kaka", _PIDS(13128, 138449), _TC_OVR, None),
    (20032, "Terry, Lampard, Drogba, Cech", _PIDS(13732, 5471, 31432, 48940), _TC_OVR, None),
    (20033, "Figo, Ronaldo, Beckham, Raul, Roberto Carlos, Casillas", _PIDS(5589, 37576, 250, 45661, 1040, 5479), _TC_OVR, None),
    (20028, "Pemain season '17", _S(88), _TC_OVR, None),
    (20029, "Pemain season apa saja", "1", ("", "ovr", 1), None),
    (20043, "Pemain season legenda", _S(*TC_LEGENDS), ("", "ovr", 4), None),
    (40001, "50 kartu terbaik", "1", _TC_OVR, 50),
    (40002, "100 kartu terbaik", "1", _TC_OVR, 100),
    (40003, "100 kartu terbaik season spesial", _SPECIAL, _TC_OVR, 100),
    (40004, "100 kartu terbaik season '17", _S(88), _TC_OVR, 100),
    (40005, "100 kartu terbaik season legenda", _S(*TC_LEGENDS), _TC_OVR, 100),
    (20063, "Pemain Brasil skill 4 bintang ke atas", "p.nationality = 54 AND p.skillmoves >= 3", ("", "ballcontrol", 5), None),
    (20064, "Short pass dan vision 85 ke atas", "p.shortpassing >= 85 AND p.vision >= 85", ("", "shortpassing", 5), None),
    (20065, "Pemain Real Madrid dan Barcelona", _CLUB(243, 241), ("", "dribbling", 5), None),
    (20066, "Tackle 85 ke atas", "p.standingtackle >= 85", ("", "aggression", 5), None),
    (20067, "Work rate menyerang dan bertahan tinggi", "p.attackingworkrate = 2 AND p.defensiveworkrate = 2",
     ("", "positioning", 5), None),
    (20068, "Bek tinggi 188 cm ke atas", "p.height >= 188 AND p.preferredposition1 BETWEEN 1 AND 8", ("", "strength", 5), None),
    (20069, "Lahir 1995 ke atas", "CAST(substr(p.birthdate, instr(p.birthdate, ' ') - 4, 4) AS INTEGER) >= 1995",
     ("", "longshots", 5), None),
    (20070, "Heading 85 ke atas", "p.headingaccuracy >= 85", ("", "strength", 5), None),
    (20071, "Reputasi internasional 4 bintang ke atas", "p.internationalrep >= 4", ("", "ovr", 4), None),
)
TC_KEYS = {34024: "TEAMCOLOR_ATTRIBUTES_EVOLVE34024",  # every other name is TEAMCOLOR_ATTRIBUTES_TEAM<id>
           **{TC_WC18 + n: f"18 WC {name}" for n, name in TC_WC18_NATIONS.items()}}  # names of ours, shown as given
# The admin page's own colors (user: "bisa tambahkan team color baru"): gamedata/teamcolors.json,
# [{id (TC_CUSTOM + n), name, desc, seasons, clubs, nations, players (player ids), min_ovr, best,
# target (fw/mf/df/gk, "" = all), stat ("ovr" or a stat), amount}]; a card belongs when it meets
# every filter given (best N: of the Market cards, by rating), its tiers are a named special's
# (_tc_special_groups). A new or changed one counts from the next login.
CUSTOM_COLORS = os.path.join(HERE, "gamedata", "teamcolors.json")
TC_CUSTOM = 81000


def tc_condition(c):
    """SQL on players p of an own color's filters (all must hold; none = every card)."""
    ints = lambda k: [int(x) for x in c.get(k) or ()]
    parts = ([_S(*ints("seasons"))] if ints("seasons") else []) + ([_CLUB(*ints("clubs"))] if ints("clubs") else [])
    parts += [f"p.nationality IN ({_in(ints('nations'))})"] if ints("nations") else []
    parts += [_PIDS(*ints("players"))] if ints("players") else []
    parts += [f"{POSOVR} >= {int(c['min_ovr'])}"] if c.get("min_ovr") else []
    return " AND ".join(parts) or "1"


def tc_custom():
    """[(id, name, description, SQL condition, boost, best N)] of the admin page's colors."""
    try:
        with open(CUSTOM_COLORS, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for c in rows if isinstance(rows, list) else ():
        try:
            out.append((int(c["id"]), str(c["name"]), str(c.get("desc") or c["name"]), tc_condition(c),
                        (str(c.get("target") or ""), str(c["stat"]), int(c["amount"])), int(c.get("best") or 0) or None))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def tc_specials():
    """TC_NAMED_SPECIALS and the admin page's colors: (id, description, condition, boost, best N)."""
    return TC_NAMED_SPECIALS + tuple((cid, desc, cond, b, best) for cid, _, desc, cond, b, best in tc_custom())


def _tc_boost(groups, boost):
    """groups with a TOOLTIP boost (target, stat, amount) added to every tier."""
    target, stat, n = boost
    extra = {"type": stat, "amount": n, **({"target": target} if target else {})}
    return [dict(g, boosts=g["boosts"] + [extra]) for g in groups]


def _tc_special_groups(boost, players=5):
    """A special color's tiers: 5-6 / 7-8 / 9-11 players, OVR up to the tooltip's OVR (else +3,
    with a stat tooltip on top). A set of fewer than 5 players has one tier: all of them."""
    target, stat, n = boost
    top = n if stat == "ovr" else 3
    groups = (_tc_groups((players, 11, top)) if players < 5
              else _tc_groups((5, 6, max(1, top - 2)), (7, 8, max(1, top - 1)), (9, 11, top)))
    return groups if stat == "ovr" else _tc_boost(groups, boost)


@functools.lru_cache(maxsize=None)
def tc_special_sets():
    """{special color id: (frozenset of its spids, number of players)}: season colors by card
    season, the named ones by their condition ("best N": of the Market cards, by rating)."""
    sets = {}
    with closing(_db()) as c:
        rows = c.execute("SELECT spid, playerid FROM players").fetchall()
        for _, codes in tc_seasons():
            sets[TC_SEASON + codes[0]] = [(s, p) for s, p in rows if s // 1000000 in codes]
        for cid, _, cond, _, best in tc_specials():
            got = c.execute(f"SELECT p.spid, p.playerid FROM players p WHERE {cond} ORDER BY {POSOVR} DESC").fetchall()
            sets[cid] = [r for r in got if on_market(r[0])][:best] if best else got
    return {cid: (frozenset(s for s, _ in got), len({p for _, p in got})) for cid, got in sets.items()}


@functools.lru_cache(maxsize=None)
def tc_colors():
    """Every color (GET /static/info/teamcolor teamColors): the generic club / nation ones, the
    upgrade ones, the named clubs and nations, the season colors and the named specials (one
    with no card in fifadb is left out)."""
    own = {cid: name for cid, name, *_ in tc_custom()}
    key = lambda cid: own.get(cid) or TC_KEYS.get(cid, f"TEAMCOLOR_ATTRIBUTES_TEAM{cid}")
    club = {"teamcolorid": TC_CLUB, "type": "club", "teamid": -1, "name": key(TC_CLUB),
            "description": "Pemain dari klub yang sama", "boostgroups": TC_CLUB_GROUPS}
    nation = {"teamcolorid": TC_NATION, "type": "nation", "teamid": -1, "name": key(TC_NATION),
              "description": "Pemain dari negara yang sama", "boostgroups": TC_GROUPS}
    grades = [{"teamcolorid": cid, "type": "grade", "grade": g, "teamid": -1, "name": name,
               "description": f"Pemain grade +{g} ke atas", "boostgroups": groups} for cid, g, name, groups in TC_GRADE]
    clubs = [{"teamcolorid": cid, "type": "club", "teamid": t, "name": key(cid), "description": "Pemain klub ini (sekarang atau dulu)",
              "boostgroups": _tc_boost(TC_CLUB_GROUPS, b)} for cid, t, b in TC_NAMED_CLUBS]
    nations = [{"teamcolorid": cid, "type": "nation", "teamid": n, "name": key(cid), "description": "Pemain dari negara ini",
                "boostgroups": _tc_boost(TC_GROUPS, b)} for cid, n, b in TC_NAMED_NATIONS]
    # a special color's teamid is a STRING: TeamColorController copies teamid to __teamid and hands
    # the list to native (setTeamColorList); at half time native re-checks the colors (0x94EB70) and
    # reads a special one's __teamid with asCString -- a number there crashed every second half.
    sets = tc_special_sets()
    specials = []
    for label, codes in tc_seasons():
        cid = TC_SEASON + codes[0]
        tid, b = TC_SEASON_NAMES.get(codes[0], (None, None))
        name = tid if isinstance(tid, str) else key(tid) if tid else f"Season {label}"
        specials.append({"teamcolorid": cid, "type": "special", "teamid": str(cid), "name": name,
                         "description": f"Pemain season {label}", "boostgroups": _tc_special_groups(b) if b else TC_GROUPS})
    for cid, desc, _, b, _ in tc_specials():
        if sets[cid][1]:
            specials.append({"teamcolorid": cid, "type": "special", "teamid": str(cid), "name": key(cid),
                             "description": desc, "boostgroups": _tc_special_groups(b, min(5, sets[cid][1]))})
    return [club, nation] + grades + clubs + nations + specials


def ep_teamcolor(query, body):
    """GET /static/info/teamcolor (once per login). A special color lists its cards by spid; only
    the cards I own are listed (every card would be MBs): one bought later counts on the server
    at once and gets its boost in the client from the next login."""
    st = _state()
    owned = {int(p["spid"]) for p in st["teams"].get(str(_default_slot(st)), {}).get("players", [])}
    sets = tc_special_sets()
    specials = {c["teamid"]: sorted(owned & sets[c["teamcolorid"]][0]) for c in tc_colors() if c["type"] == "special"}
    return _json({"teamColors": tc_colors(), "teamColorSpecials": specials})


CLUB_HISTORY = os.path.join(HERE, "gamedata", "club_history.json")  # tools/club_history.py's careers


@functools.lru_cache(maxsize=None)
def tc_static():
    """({spid: (playerid, teamid, nationality)}, {playerid: {former club ids}}) from fifadb. A
    player's former clubs are his career in CLUB_HISTORY where it has him (up to the running season,
    PR #39), else FO3's own playerhistory (up to 2017): what install puts in the client's
    clubhistory, which TeamColorController counts on the squad screen (user: "apakah karir klub
    sudah mempengaruhi team color?": the server still counted FO3's)."""
    with closing(_db()) as c:
        cards = {s: (p, t, n) for s, p, t, n in c.execute("SELECT spid, playerid, teamid, nationality FROM players")}
        hist = {}
        for p, t in c.execute("SELECT playerid, teamid FROM playerhistory"):
            hist.setdefault(p, set()).add(t)
    try:
        with open(CLUB_HISTORY, encoding="utf-8") as f:
            hist.update({int(p): {r[0] for r in rs} for p, rs in (json.load(f).get("players") or {}).items()})
    except (OSError, ValueError, AttributeError):
        pass
    return cards, hist


def _tc_tier(groups, n):
    return next((i for i, g in enumerate(groups) if g["count_min"] <= n <= g["count_max"]), None)


def active_teamcolors(team):
    """team.teamcolors: the colors the starting XI meets, with enough distinct players (as
    TeamColorController.updatePlayerCount counts them): one generic club / nation color per club
    and nation, the named ones, the specials and the upgrade colors."""
    cards, hist = tc_static()
    sets = tc_special_sets()
    counts, seen = {}, set()
    for p in team["squad"]["positions"]:
        c = cards.get(int(p["spid"])) if p.get("pos", POS_SUB) < POS_SUB else None
        if c is None or c[0] in seen:
            continue
        seen.add(c[0])
        pid, teamid, nation = c
        keys = [(TC_CLUB, t) for t in {teamid} | hist.get(pid, set())] + [(TC_NATION, nation)]
        keys += [("special", cid) for cid, (spids, _) in sets.items() if int(p["spid"]) in spids]
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
    out = []
    for (cid, teamid), n in sorted((k, n) for k, n in counts.items() if k[0] != "special"):
        i = _tc_tier(TC_CLUB_GROUPS if cid == TC_CLUB else TC_GROUPS, n)
        if i is not None and teamid > 0:
            out.append({"teamcolorid": cid, "boostindex": i, "teamid": teamid})
    for c in tc_colors():
        if c["type"] == "grade" or c["teamid"] == -1:
            continue
        n = counts.get(("special", c["teamcolorid"]) if c["type"] == "special"
                       else (TC_CLUB if c["type"] == "club" else TC_NATION, c["teamid"]), 0)
        i = _tc_tier(c["boostgroups"], n)
        if i is not None:
            # special colors carry their teamid as a string, like tc_colors()
            out.append({"teamcolorid": c["teamcolorid"], "boostindex": i, "teamid": c["teamid"]})
    grade = {iid(p): grade_of(p) for p in team["players"]}
    xi = [grade.get(str(p.get("id") or p["spid"]), 0) for p in team["squad"]["positions"] if p.get("pos", POS_SUB) < POS_SUB]
    for cid, g, _, groups in TC_GRADE:
        i = _tc_tier(groups, sum(x >= g for x in xi))
        if i is not None:
            out.append({"teamcolorid": cid, "boostindex": i, "teamid": -1})
    return out


def fill_squad(sq, players):
    """11 starters + 7 subs (Enums.PLAYERPOSITION "SUB"; without a bench the squad screen says
    "Tidak cukup pemain"). A hole in the XI (issue #7: an upgrade of two starters of one
    player) takes the best sub; the bench is topped up with the best Tim inti cards. One card
    per player in the 18, like the client (PLAYERLIST_CHANGEPLAYER_FAIL_POPUP_INFO_DUPLICATIONPLAYER).
    ponytail: by rating, not by position; the manager re-arranges in Formasi."""
    pos = sq["positions"]
    card = {p["id"]: p for p in players}
    best = lambda ps: sorted(ps, key=lambda p: -player_ovr(card.get(p["id"], p)))
    taken = {int(s["spid"]) % 1000000 for s in pos}
    spare = []
    for p in best(p for p in players if p.get("state") == "first"):  # Tim inti only
        if int(p["spid"]) % 1000000 not in taken:
            taken.add(int(p["spid"]) % 1000000)
            spare.append({"spid": p["spid"], "pos": POS_SUB, "id": p["id"]})
    forms = formation_table()
    form = next((f for f in forms["common"] + list(forms["teams"].values()) if f["formationid"] == sq.get("formation")),
                {"positions": [f[0] for f in gen_teams.FORMATION], "offsets": [{"x": f[1], "y": f[2]} for f in gen_teams.FORMATION]})
    shape = form["positions"]
    used = {s["pos"] for s in pos if s["pos"] < POS_SUB}
    holes = [x for x in shape if x not in used][:max(0, 11 - len(used))]
    for hole, s in zip(holes, best(s for s in pos if s["pos"] == POS_SUB) + spare):
        s["pos"] = hole
        s.pop("starting", None)
        if s not in pos:
            pos.append(s)
    pos += [s for s in spare if s not in pos][:max(0, 7 - sum(s["pos"] == POS_SUB for s in pos))]
    # a starter needs starting [attacking, defensive participation, x, y]: without it the client
    # moves him to the bench (PlayerListUtil, issue #7 again: filled holes had none, and Formasi
    # then saved them without it). x/y = his slot in the formation.
    spot = dict(zip(shape, form["offsets"]))
    for s in pos:
        if s["pos"] < POS_SUB and len(s.get("starting") or []) < 4:
            xy = spot.get(s["pos"], {"x": 0.5, "y": 0.5})
            s["starting"] = [gen_teams.PARTICIPATION, gen_teams.PARTICIPATION, xy["x"], xy["y"]]


# Formasi's squad picker (SquadScreenHandler, issue #20: it was an empty box): STATICVALUES
# limit.preset_squad_count entries named MULTIPLE_SQUAD_SLOT1.. = Squad A, B, C; a slot i is
# locked below coach level limit["preset_squad_level<i>"] (none = open). Picking one sends
# PUT /coach/me/squad {squad, kickers, captain, presetidx} (apply_squad).
PRESET_SQUADS = 3


def preset_squads(team):
    """coach.teamsquadlist.preset of an owned team (owned_team): one squad per slot. The slot in
    use (presetidx) is the squad itself; the others as they were left, without cards that are
    gone and topped up like the squad (a slot never used starts as a copy of the squad)."""
    # like selectPresetSquad, which keeps only Tim inti cards
    saved, ids, out = team.get("presets") or [], {p["id"] for p in team["players"] if p.get("state") == "first"}, []
    for i in range(PRESET_SQUADS):
        if i == team.get("presetidx", 0) or i >= len(saved) or not saved[i]:
            out.append(team["squad"])
            continue
        sq = copy.deepcopy(saved[i])
        sq["positions"] = [s for s in sq["positions"] if str(s.get("id")) in ids]
        fill_squad(sq, team["players"])
        out.append(sq)
    return out


def owned_team(raw):
    """A stored team ({players:[{spid,...}], squad:{positions,captain,kickers,...}}) in the
    shape the client expects for a club it can show and play with (coach.team, match side.team)."""
    team = dict(raw)
    # An owned club refers to player *instances*: PlayerListUtil.getBestEleven maps
    # squad.positions[].id -> players[]._id (spid alone gives an empty XI -> null -> the
    # MenuScreen constructor throws = black FREEROAM).
    # CoachProxy.expandTeam: teamvalue = sum(price) of players with state "first" and grade > 0
    # getBestEleven matches positions[].id on players[]._id, attachSquadInfo and
    # generateFormatedPlayerList (Tim > Formasi / Peran pemain) on players[].id: send both.
    # Instance defaults: life = remaining contract matches (SquadUtil.needToReject drops <= 0),
    # levelinfo.level (FirstTeamInfo.updateAvgInfo average level). price = EP market value.
    # state "first" = Tim inti; no state = Tim Cadangan (Enums.getPlayerState: undefined ->
    # SECONDTEAM), set by /player/<id>/move/secondteam (stored as team2); Tukar Scout only
    # lists Tim Cadangan cards. A card in the 18 is always Tim inti (Formasi hides the others;
    # a state.json from before 5ffbfd8 can hold such a card, issue #7).
    # stamina 0..1: Liga's player lists draw it as a bar (PlayerListItemTeamColorRenderer in
    # ZONE_LEAGUE; without it the bar was empty, issue #16) and SquadUtil.getPositionScore weighs
    # it for the best XI. ponytail: always full, no fatigue kept between Liga matches.
    in18 = {str(s.get("id") or s["spid"]) for s in team["squad"]["positions"]}
    team["players"] = [{**({} if p.get("team2") and iid(p) not in in18 else {"state": "first"}), "grade": 0, "life": 100, "stamina": 1,
                        **{k: v for k, v in p.items() if k != "team2"}, "levelinfo": player_levelinfo(p),
                        "_id": iid(p), "id": iid(p), "price": card_price(p["spid"], grade_of(p))}
                       for p in team["players"]]
    sq = dict(team["squad"])
    sq["positions"] = [dict(p, id=str(p.get("id") or p["spid"])) for p in sq["positions"]]
    fill_squad(sq, team["players"])
    sq["captain"] = str(sq["captain"]) if sq.get("captain") else ""
    # all KICKER_TYPES slots, each a starter: an empty or stale one (saves before issue #8 dropped
    # empty slots; a kicker moved to the bench) takes gen_teams' default, the last 5 starters
    xi = [s["id"] for s in sq["positions"] if s["pos"] < POS_SUB]
    kick = ([str(k) for k in sq.get("kickers") or []] + [""] * KICKERS)[:KICKERS]
    sq["kickers"] = [k if k in xi or not xi else xi[-KICKERS:][i % len(xi[-KICKERS:])] for i, k in enumerate(kick)]
    team["squad"] = sq
    team.update({"staffs": [], "staffroles": [], "staffroleidx": 1, "maxstaff": 0, "staffsmc": 0})  # mine: staff_team
    # PlayerListUtil.generateCaptainFromCoachInfo (Tim > Formasi) reads these at team level
    team.update({"captain": sq["captain"], "kickers": sq["kickers"], "presetidx": team.get("presetidx", 0)})
    team.setdefault("formations", [])
    return team


def xi_ratings(team):
    """{player id: rating} of an owned_team's XI as the squad lists show it (PlayerListUtil __ovr):
    Enums.calcPosOvr at his slot, on stats + grade boost + level - 1 (fillPlayerStaticData) +
    his team color boosts (TeamColorController over team.teamcolors: the best club / nation /
    special color by getBoostStatPoint plus the best grade color; "ovr" = every stat, a boost
    with a target only in that position group) + the boosts of the working staff of his group or
    of every group (applyStaffBoostStat)."""
    card = {iid(p): p for p in team["players"]}
    staff = [(s["position"], staff_stat(s)) for s in staff_working(team)]
    xi = [(str(s.get("id") or s["spid"]), s["pos"]) for s in team["squad"]["positions"]
          if s.get("pos", POS_SUB) < POS_SUB and str(s.get("id") or s["spid"]) in card]
    stats = card_stats([card[i]["spid"] for i, _ in xi])
    colors = {c["teamcolorid"]: c for c in tc_colors()}
    cards, hist = tc_static()
    sets = tc_special_sets()
    out = {}
    for i, pos in xi:
        p = card[i]
        spid, grp = int(p["spid"]), "gk" if pos == 0 else "df" if pos <= 8 else "mf" if pos <= 19 else "fw"
        pid, club, nation = cards.get(spid, (None, None, None))
        def fits(e, c):
            return {"grade": lambda: grade_of(p) >= c["grade"], "nation": lambda: e["teamid"] == nation,
                    "club": lambda: e["teamid"] in {club} | hist.get(pid, set()),
                    "special": lambda: spid in sets[c["teamcolorid"]][0]}[c["type"]]()
        best = {}  # "base" / "grade": (point, boosts)
        for e in team.get("teamcolors") or []:
            c = colors[e["teamcolorid"]]
            if fits(e, c):
                bs = [b for b in c["boostgroups"][e["boostindex"]]["boosts"] if b.get("target", grp) == grp]
                point = sum(b["amount"] * (len(STAT_COLS) if b["type"] == "ovr" else 1) for b in bs)
                kind = "grade" if c["type"] == "grade" else "base"
                if point > best.get(kind, (0,))[0]:
                    best[kind] = (point, bs)
        add = dict.fromkeys(STAT_COLS, GRADE_BOOST[grade_of(p)] + player_levelinfo(p)["level"] - 1)
        for k, n in ((p.get("sp") or {}).get("stats") or {}).items():  # his growth (checkGrowthBoostPlayer)
            if k in add:
                add[k] += int(n)
        boosts = [(b["type"], b["amount"]) for _, bs in best.values() for b in bs] + \
                 [kn for kind, stat in staff if kind in STAFF_FIT or kind == grp.upper() for kn in stat.items()]
        for kind, n in boosts:
            for k in (STAT_COLS if kind == "ovr" else [kind] if kind in add else []):
                add[k] += n
        w = ROLE_WEIGHTS[POS_ROLE[pos]]
        out[i] = sum(n * (stats[spid][k] + add[k]) for k, n in w.items()) // 100
    return out


def team_summary(team, teamid):
    # MenuScreen TeamInfoNew.setData: TEAMNAME(summary.id) + loadTeamCrest(summary.id), setOVR(ovr);
    # TeamStaticInfo (Tim > Klub) also reads name, league.id, nation{id,name}, rival.name.
    # TeamInfoNew.setOVR reads ovr.FW/MF/DF (rating bars) as sent: the average of the ratings the
    # squad list shows (issue #18: card ratings alone, without level, slot and team color, ran
    # ~12 under them). Enums.PLAYERPOSITION index ranges.
    ovr = xi_ratings(team)
    lines = {k: r for k, r in LINES.items() if k != "GK"}
    def line_ovr(r):
        v = [ovr.get(str(p.get("id") or p["spid"]), 0) for p in team["squad"]["positions"] if p.get("pos") in r]
        return round(sum(v) / len(v)) if v else 0
    return dict(roster_summary(teamid), id=teamid, ovr={k: line_ovr(r) for k, r in lines.items()})


def team_kit(teamid):
    # kit ids go to native as-is (freeroam kicker, 3D previews, match sides); {} = default goalie kit.
    # kit id = teamid << 5 | kittype (0 home, 1 away, 2 goalie): the match side reader (0x9480D0)
    # builds that itself for a side with no kit id, and the kit loader splits id >> 5 / id & 31 into
    # teamkits (teamtechid, teamkittypetechid) -> data/sceneassets/kit/kit_<team>_<type>_0.rx3.
    # teamid << 10 only looked right in FREEROAM (that path takes the team from elsewhere); in a
    # match it pointed at team (teamid << 5) = a checkerboard (GitHub issue #3).
    return {"availables": {k: {"id": teamid << 5 | t} for k, t in (("home", 0), ("away", 1), ("goalie", 2))}}


START_EP = 1000000

# The economy (issue #30, the reporter: "make player prices and player card packs more expensive
# and realistic ... configurable through the GM Admin Panel"). gamedata/economy.json, set on the
# admin page's Ekonomi tab; the default is the easy game this server always had:
#   players   x on every card's value (card_price: the Market, selling, team value, Tukar, packs)
#   packs     x on the Shop's pack prices (when the Shop is not free: the Shop tab's switch)
#   start_ep  the EP a new manager starts with
#   dynamic   the Market moves (market_value): a card's price drifts day by day, managers list
#             bargains and you may ask up to MARKET_ASK_MAX of the price, so buying low and selling
#             high pays; off = a fixed Market where nothing is gained by trading back and forth
# A Shop that costs EP (SHOP_FREE off) sells at worth, rounded up (product_price; packs at their
# cards' dearest swing: pack_worth).
#   strict    with a paid Shop, nothing turns EP into more EP (strict_economy, Sulit): an Upgrade costs
#             at least what it adds (enhance_cost; a restore costs EP), a Tukar v2 or Scout offer is
#             worth at most the cards given (trade_cap). Off (Santai, Realistis): the game's own
#             rules, a 2% Upgrade fee, a restore for upgrade points, offers without a cap (user:
#             "harga upgrade pemain terlalu mahal", "ikuti aturan game aslinya saja untuk Realistis")
ECONOMY_FILE = os.path.join(HERE, "gamedata", "economy.json")
ECONOMY_DEFAULT = {"players": 1.0, "packs": 1.0, "start_ep": START_EP, "dynamic": False, "strict": False}


def economy_clean(cfg):
    """cfg's valid settings over ECONOMY_DEFAULT."""
    out = dict(ECONOMY_DEFAULT)
    for k in ("players", "packs"):
        v = cfg.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0.1 <= v <= 100:
            out[k] = float(v)
    v = cfg.get("start_ep")
    if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10 ** 12:
        out["start_ep"] = v
    for k in ("dynamic", "strict"):
        if isinstance(cfg.get(k), bool):
            out[k] = cfg[k]
    return out


def strict_economy():
    """The strict rules (ECONOMY_DEFAULT): on and a Shop that costs EP."""
    return ECONOMY["strict"] and not SHOP_FREE


def load_economy():
    try:
        with open(ECONOMY_FILE, encoding="utf-8") as f:
            return economy_clean(json.load(f))
    except (OSError, ValueError, AttributeError):
        return dict(ECONOMY_DEFAULT)


ECONOMY = load_economy()


def set_economy(cfg):
    """Save and apply new economy settings; the prices cached at the old ones are dropped."""
    global ECONOMY
    new = economy_clean(cfg)
    os.makedirs(os.path.dirname(ECONOMY_FILE), exist_ok=True)
    with open(ECONOMY_FILE, "w", encoding="utf-8") as f:
        json.dump(new, f, indent=1)
    ECONOMY = new
    pack_ev.cache_clear()
    vm_value.cache_clear()
    trade2_pool.cache_clear()
    _T3_POOL.clear()
    return new


# Enums.VIP_DETAIL_*: NONE 0, VIP 1<<1, VVIP 1<<2, VVIP_SEA 1<<4
VIP_DETAIL = 2
VIP_UNTIL = 2051222400000  # 2035-01-01, ms


def add_ep(st, n):
    """Credit match/season/cup EP to the active coach (shown by /coach/me money.ep)."""
    coach = next(c for c in st["coaches"] if c["slot"] == _default_slot(st))
    coach["ep"] = coach.get("ep", START_EP) + int(n)


# Manager > Info Manager (issue #33: its Jum.Rekor tab showed only "-"). CoachTotalRecordTabPage
# draws once /coach/me has all of createdat (W3C), playtime (s), career and statistics, and reads
# statistics .league, .friendly (with its custom/quick/vscom rows), .simulation, .club and
# .ladder.highrecords {count, rank [i], winningstreak [i]} without guards: one missing stops the
# rest. coach["record"] keeps what this server can count (league, cup and Rank matches, league
# titles and best level, cups, the Market); friendlies, simulations and clubs do not exist here.
CUP_LEVELS = 5  # statistics.cup.cupwins [AI level 0..4]


def coach_record(coach, part):
    """A coach's record of one kind (league, cup, ladder, market), created empty."""
    return coach.setdefault("record", {}).setdefault(part, {})


def record_match(st, part, gf, ga):
    """A finished league or cup match of mine (my goals gf, theirs ga)."""
    r = coach_record(my_coach(st), part)
    for k in ("wins", "draws", "losses", "goals", "goalsconceded", "streak", "maxstreak"):
        r.setdefault(k, 0)
    res = "wins" if gf > ga else "losses" if gf < ga else "draws"
    r[res] += 1
    r["goals"] += gf
    r["goalsconceded"] += ga
    r["streak"] = r["streak"] + 1 if res == "wins" else 0
    r["maxstreak"] = max(r["maxstreak"], r["streak"])


def record_trade(coach, kind, spid, amount, now=None):
    """A card bought (kind "buy") or sold ("sell") on the Market: the count and the dearest."""
    r = coach_record(coach, "market")
    r[kind] = r.get(kind, 0) + 1
    top = r.get("max" + kind)
    if not top or amount > top["amount"]:
        r["max" + kind] = {"amount": int(amount), "spid": int(spid), "name": card_name(spid),
                           "date": w3c(time.time() if now is None else now)}


def coach_statistics(st, coach, slot):
    """/coach/me statistics: Rank (ladder_statistics plus the record's count, best rank and best
    win streak per ladder), league, cup, and the empty friendly / simulation / club records."""
    rec = coach.get("record", {})
    lg, cup, lad = rec.get("league", {}), rec.get("cup", {}), rec.get("ladder", {})
    ladder = (ladder_statistics(st)[0] if slot == _default_slot(st) else
              {"seasons": [], "highrecords": {"elo": [LADDER_START] * LADDER_COUNT, "division": [None] * LADDER_COUNT}})
    ladder["highrecords"].update(count=lad.get("count", 0), rank=lad.get("rank", [0] * LADDER_COUNT),
                                 winningstreak=lad.get("winningstreak", [0] * LADDER_COUNT))
    rows = lambda: {k: [0] * 5 for k in ("wins", "draws", "losses")}  # 1 vs 1 .. 5 vs 5
    fr = coach.get("record", {}).get("friendly", {})  # friendly_record
    frows = lambda k: {**rows(), **fr.get(k, {})}
    return {"ladder": ladder,
            # noramlbest (sic) / ultimatebest: the level shown is it - 10 / - 20; leaguewins: an
            # array the titles count sums (shown only when not empty)
            "league": {**{k: lg.get(k, 0) for k in ("wins", "draws", "losses", "goals", "goalsconceded", "normalwins",
                                                    "noramlbest", "ultimatewins", "ultimatebest")},
                       "winningstreak": {"max": lg.get("maxstreak", 0)}, "leaguewins": [0]},
            "friendly": {"custom": frows("custom"), "quick": {**frows("quick"), "winningstreak": {"max": [fr.get("best", 0)]}},
                         "vscom": frows("vscom")},
            "simulation": {k: {"wins": 0, "draws": 0, "losses": 0} for k in ("custom", "quick", "vscom")},
            "winstreak": [], "club": {}, "esports2": koth_statistics(st, coach),
            "cup": {**{k: cup.get(k, 0) for k in ("wins", "draws", "losses", "abandonments")},
                    "cupwins": cup.get("cupwins", [0] * CUP_LEVELS), **({"highrecords": cup["best"]} if cup.get("best") else {})}}


def coach_career(coach, team):
    m = coach.get("record", {}).get("market", {})
    return {"appat": coach.setdefault("appat", w3c(time.time())), "clubs": 1, "maxplayers": len(team["players"]),
            "buyplayers": m.get("buy", 0), "sellplayers": m.get("sell", 0),
            **{k: m[k] for k in ("maxbuy", "maxsell") if k in m}}  # none = "Tidak ada pertukaran"


# Play time (Info Manager's "Waktu bermain"): the gaps between the game's requests, up to
# PLAYTIME_GAP (a whole match sends nothing for its length); a longer silence is time away. Kept
# in memory and added to the active coach whenever the state is saved.
PLAYTIME_GAP = 1200
PLAYTIME = {"last": 0.0, "pending": 0.0}


def note_activity(now=None):
    now = time.time() if now is None else now
    if 0 < now - PLAYTIME["last"] <= PLAYTIME_GAP:
        PLAYTIME["pending"] += now - PLAYTIME["last"]
    PLAYTIME["last"] = now


def fold_playtime(st):
    if PLAYTIME["pending"] >= 1 and st.get("coaches"):
        coach = my_coach(st)
        coach["playtime"] = coach.get("playtime", 0) + int(PLAYTIME["pending"])
        PLAYTIME["pending"] -= int(PLAYTIME["pending"])


def coach_me(st, slot):
    """Body of the /coach/me resource (CoachProxy). Field paths are the ones its
    fetchCoach* methods read; values are a fresh account. startFreeRoam waits for this
    resource to validate before signing on."""
    coach = next(c for c in st["coaches"] if c["slot"] == slot)
    raw = st["teams"].get(str(slot), {"squad": {"positions": [], "kickers": []}, "players": []})
    team = owned_team(raw)
    team.update(staff_team(raw))  # Tim > Manajemen Staff; summary.ovr below counts the working ones
    teamid = coach["teamid"]
    team["formations"] = team_formations(teamid)
    team["teamcolors"] = active_teamcolors(team)
    if team["formations"] and team["squad"].get("formation") is None:
        team["squad"]["formation"] = team["formations"][0]["formationid"]
    return {
        # _id: the Peringkat manajer tab finds my own row by it; hiderank: its "hide me" box
        "id": coach["_id"], "_id": coach["_id"], "hiderank": coach.get("hiderank", False),
        "name": coach["name"], "slot": slot, "teamid": coach["teamid"], "team": team,
        # CoachProxy.expandMoney parses money.mpdata.until as a W3C date; an exception there
        # silently aborts onChangeCoachMeResource before squad/players are read.
        "money": {"ep": coach.get("ep", START_EP), "es": coach.get("es", 0), "cash": 0, "mp": 0, "useable_mp": 0,
                  "mpdata": {"point": 0, "until": "2030-01-01T00:00:00.000Z"}},
        # always a VIP member playing from an official PC warnet (IGR / GCA): CoachProxy.igr,
        # isVip() = vipDetail > 0 and vipExpireAt in the future; vipStatus.VIP1 = VIP expiry
        # after a VVIP period (updateVipExpireAt)
        "igr": True, "vipDetail": VIP_DETAIL, "vipExpireAt": VIP_UNTIL, "vipStatus": {"VIP1": VIP_UNTIL},
        # the Quest window's GP (this week's; CoachProxy.expandMoney: money.gp = gpdata.point, cleared
        # once until passes) and the achievement badges (missions)
        "sp": {"point": 0, **coach_sp_out(coach)}, "gpdata": {"point": mission_gp(coach), "until": w3c(week_end())},
        "badges": coach.get("missions", {}).get("badges", []), "currentbadge": coach.get("missions", {}).get("currentbadge", 0),
        # league.leagueid -> CoachProxy.leagueId: Single > Liga builds the league from it
        # statistics.ladder: Multiple > Rank's record tab (LadderMatchController.rebuildLadderRecordInfo
        # reads seasons [{sid, ...}] and highrecords {elo [i], division [i]}, i = ladder index;
        # without it the screen stopped on a UI error): my ladder record (ladder_statistics)
        "levelinfo": coach_levelinfo(coach),
        # Manager > Info Manager (issue #33): createdat + playtime + career + statistics (see
        # coach_statistics); profile {description, picture, views} for its Summary tab
        "statistics": coach_statistics(st, coach, slot),
        "createdat": coach.setdefault("appat", w3c(time.time())), "playtime": int(coach.get("playtime", 0)),
        "profile": {"description": "", "views": 0, **coach.get("profile", {})},
        "league": {"leagueid": roster_summary(teamid).get("league", {}).get("id", 0)},
        # limits.cups -> CupProxy.playMax (cups you can run at once); limits.players/baseplayers
        # -> the market's player-slot check (TransferPurchaseTabPage._limitPlayerCount)
        # limits.firstTeam: Skuad tab moveToFirstTeam reads it first and silently gives up when
        # it is missing, so Tim Cadangan cards (packs, trades) could never reach Tim inti/Formasi
        "club": {"clubid": club_id(st)}, "uimode": 0, "limits": {"cups": 3, "players": MAX_PLAYERS, "baseplayers": MAX_PLAYERS,
                                             "firstTeam": FIRST_TEAM_MAX},
        "transferfavorites": [], "currdivision": "",
        # Manager > Info Manager > "Hasil tanding multiplayer": my Rank matches (ladder_record)
        "multiplay": coach.get("multiplay", []),
        # playlimit {modeid: {playcount}}: Liga's Mulai/Simulasi buttons compare playcount with
        # PLAYLIMIT.maxplay; without the key playcount stays undefined and they stay disabled
        "playlimit": {},
        "kit": team_kit(teamid), "summary": team_summary(team, teamid),
        # Tim > Klub > Informasi tim (issue #9): TeamStaticInfo reads career.appat (start date,
        # W3C; a manager from before this field gets the day it is first sent) and stadium {id};
        # TeamManagementTeamInfoTabPage draws its player panel only once stadium is defined
        "career": coach_career(coach, team),
        "stadium": {"id": club_stadium(teamid)},
        # cups -> CupProxy.cupPlayList: {metaid, cupid} of cups not left yet (front screen resumes
        # them; an ended one shows its won/eliminated screen with the reward button -> /leave)
        "items": coach_items(st, slot), "matchstats": [], "scouters": coach_scouters(st, slot),
        "cups": [{"metaid": c["metaid"], "cupid": c["_id"]} for c in (st.get("cups") or {}).values()],
        # SquadScreenHandler: formation combobox reads /coach/me?path=formations
        "formations": team["formations"],
        # Formasi opens preset squad team.presetidx (STATICVALUES has `limit`): generateCaptainFrom
        # CoachInfo copies THIS coach-level teamsquadlist into its team and SquadUtil.selectPresetSquad
        # reads .preset[i] {formation, positions, kickers, captain}; none (was []) = "Tidak cukup
        # pemain". The client re-fills holes / bench and saves it.
        "teamsquadlist": {"preset": preset_squads(team)},
    }


def coach_me_resources(st, slot):
    """The /coach/me resource for a reply's `resources`. CoachProxy's fetchCoach* read
    getData("/coach/me?path=X"), which NetEvent.getDataEx resolves by walking this body; pushing
    every ?path= part too re-ran onChangeCoachMeResource per part and nested the client's
    resource chain until AS3 "Error #1023: Stack overflow" at login."""
    return [{"uri": "/coach/me", "body": coach_me(st, slot)}]


def _default_slot(st):
    return next((c["slot"] for c in st["coaches"] if c["_id"] == st["defaultcoachid"]),
                st["coaches"][0]["slot"] if st["coaches"] else 0)  # default manager deleted: the other one


def ep_coach_me(query, body):
    st = _state()
    return _json(coach_me(st, _default_slot(st)))


def ep_coach_onready(query, body):
    # POST /coach/me/onready comes right after CoachProxy.invalidate() -> reset() (which nulls
    # _squad); without /coach/me in this reply the menu is built with squad null (black FREEROAM).
    # /inbox/my/notifications (an Array) readies SystemMessageProxy, its only feed: until then
    # MenuScreenMediator.onReady never runs, so the Messenger screen is never attached and the
    # Messenger button does nothing (issue #30, presence). It carries the login notice of the
    # original game (CoachProxy.checkTransfermarketOfferList, unused in this build) while a sold
    # offer waits to be collected: "Seorang pemain telah dijual di bursa transfer."
    st = _state()
    if settle_offers(st):  # sold while I was away
        _save(st)
    notes = [note("transfermarket", "list", {"title": "TRANSFERMARKET_LOGIN_ALARM"})
             for _ in range(any(o["status"] == "sold" for o in slot_offers(st)))]
    return _json({"result": True, "resources": coach_me_resources(st, _default_slot(st))
                  + [{"uri": "/inbox/my/notifications", "body": notes}]})


# The account-wide club data (Liga, Cup, the last match, Tukar, the club) is the default manager's.
# Issue #40 ("Can't play league match when using team in other league than la liga"): a second
# manager got the first one's Liga, his own club missing from it (no HOME side, "-1 of 18 matches",
# nothing to play). Now the data goes with its manager while another one is played.
CLUB_DATA = ("league2", "cups", "match", "trade", "club")


def switch_default_coach(st, cid):
    old = next((c for c in st["coaches"] if c["_id"] == st.get("defaultcoachid")), None)
    if old is None or old["_id"] != cid:
        if old is not None:
            old["clubdata"] = {k: st.pop(k) for k in CLUB_DATA if k in st}
        new = next((c for c in st["coaches"] if c["_id"] == cid), {})
        st.update(new.pop("clubdata", {}))
    st["defaultcoachid"] = cid


def ep_default_coach(slot, query, body):
    # PUT /coach/<slot>/default: the client calls setLocalCoachId(body.resources[0].body.id);
    # resources are also fed to cached resources, so /coach/me (CoachProxy) goes first.
    st = _state(); switch_default_coach(st, coach_id(slot))
    daily_login(st)  # first login of the day: today's reward goes to the Bag
    if str(slot) in st["teams"]:
        balance_first_team(st["teams"][str(slot)])
        fix_formation(st["teams"][str(slot)], next(c["teamid"] for c in st["coaches"] if c["slot"] == slot))
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


TACTIC_SLOTS = 6


def default_tactic(i):
    # TacticsController.setCurrentTactics fields; sliders 1..100, teammentality 1..5,
    # positioning (1 organised, 2 free form) / defenderline (1 cover, 2 offside trap) are 1|2
    # radios (TwoRadioButton value = index + 1; native stores value - 1, so 0 was 0xFF).
    # name "" -> client fills the localized preset name.
    return {"id": i, "name": "", "teammentality": 3,
            "bus": {"buildupspeed": 50, "passing": 50, "positioning": 1},
            "cc": {"passing": 50, "crossing": 50, "shooting": 50, "positioning": 1},
            "def": {"mentality": 50, "aggression": 50, "teamwidth": 50, "defenderline": 1}}


def fix_radios(t):
    """A tactic with its 1|2 radios in range (ones saved before the fix hold 0)."""
    for part, key in (("bus", "positioning"), ("cc", "positioning"), ("def", "defenderline")):
        if isinstance(t.get(part), dict):
            t[part][key] = min(2, max(1, int(t[part].get(key) or 1)))
    return t


def _tactics(st):
    slot = str(_default_slot(st))
    saved = st.setdefault("tactics", {}).setdefault(slot, {})
    return saved, [fix_radios(saved.get(str(i)) or default_tactic(i)) for i in range(TACTIC_SLOTS)]


def ep_tactic_all(query, body):
    # GET /game/my/tactic/all -> Array of 6 tactics (TeamManagementTeamTacticsTabPage.onReceived)
    return _json(_tactics(_state())[1])


def ep_tactic(i, query, body):
    """Save (PUT, body = tactic), reset (DELETE -> {result, resources:[{body: default}]}), or GET:
    the in-match tactic manager's "Reset" takes the whole reply as the tactic to apply (it used to
    land in the reset branch, wiping the saved slot and sending {result, resources})."""
    req = json.loads(body or b"{}")
    st = _state(); saved, tactics = _tactics(st)
    if "bus" in req:
        saved[str(i)] = fix_radios(dict({k: v for k, v in req.items() if k != "_method"}, id=i))
        _save(st)
        return _json({"result": True})
    if req.get("_method") == "DELETE":
        saved.pop(str(i), None); _save(st)
        return _json({"result": True, "resources": [{"uri": f"/game/my/tactic/{i}", "body": default_tactic(i)}]})
    return _json(tactics[int(i)] if 0 <= int(i) < TACTIC_SLOTS else default_tactic(int(i)))


# --------------------------------------------------------------------------- #
# Single > Liga (League 2.0). LeagueCupProxy builds the whole calendar client-side from
# /league2/me: league.teams + openingday, one match per round from createSchedule (ported
# below as league_round). The server keeps results and the table:
#   creation screen: GET /league2/me (difficultybest/difficultyselect), /league2/reward,
#     POST /league2/sponsor/<difficulty>, POST /league2/calendar {difficulty, sponsor}
#   a round: POST /league2/match (-> /match/<id> resource, stage created) ->
#     PUT /match/<id>/side/<s>/ready (-> stage playing -> StartSingleMatch) ->
#     PUT /match/<id>/end {home:{id,score}, away:{id,score}} -> leagueparam + /league2/me
#   or POST /league2/match/sim (no play), POST /league2/sleep (bye round)
# laststatus "league:<round>" tells the client which day it is (LeagueCupProxy.setToday).
# --------------------------------------------------------------------------- #
LEAGUE_SIZE = 10  # the reference size a season's EP is set for (the creation screen shows 10 crests)
# Issue #40 follow-up ("how to get full team in league table (not 10 team) and full match
# schedule"; user: "Sesuaikan dengan jumlah klub di liga tersebut"): a season has every club of my
# league with a full squad (Premier League 20 -> 38 rounds; at most LEAGUE_MAX). The client builds
# any size (createSchedule, monthly calendars, scrolling tables; the creation screen shows 10 of
# the crests). A season's EP and EXP by place are scaled to the size, the winner's as with 10.
LEAGUE_MAX = 24


def _db():
    c = sqlite3.connect(gen_teams.DB); c.row_factory = sqlite3.Row
    return c


def league_teams(leagueid, mine):
    """My club + the other clubs of its league with a full squad, strongest first: the club picker's
    (club_index: this season's cards, 2017 ones for players without one), so a club opened by
    tools/fo4_season.py (OPEN_CLUBS: AC Milan & co., 26 LIVE squads) plays the Liga too."""
    teams = next((ts for _, leagues in club_index() for lid, ts in leagues if lid == leagueid), [])
    return [mine] + [t for t in teams if t != mine][:LEAGUE_MAX - 1]


def league_round(teams, rnd):
    """Fixtures [(home, away)] of round rnd: LeagueCupProxy.createSchedule/getRotatedIndexAt
    (circle method; second half swaps home/away). None = bye (odd team count)."""
    n = len(teams); size = n + n % 2; per = size - 1
    flip, r = (rnd // per) % 2 == 1, rnd % per

    def rot(i):
        k = 0 if i == 0 else (i - 1 + r) % per + 1
        return teams[k] if k < n else None
    pairs = []
    for k in range(size // 2):
        a, b = rot(k), rot(size - 1 - k)
        f = (not flip) if (k == 0 and r % 4 > 1) else flip
        pairs.append((b, a) if f else (a, b))
    return pairs


def _poisson(rng, lam):
    lim, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= lim:
            return k
        k += 1


def team_strength(st, teamid):
    """Average rating (+ grade boost) of the best 11."""
    raw = st["teams"].get(str(_default_slot(st))) if teamid == my_teamid(st) else None
    ovrs = sorted((player_ovr(p) for p in (raw or roster_team(teamid))["players"]), reverse=True)[:11]
    return sum(ovrs) / len(ovrs) if ovrs else 60


def sim_score(rng, home, away):
    """ponytail: Poisson goals from the rating gap (+3 home edge); no match engine."""
    d = (home + 3 - away) / 10
    return _poisson(rng, max(0.3, 1.4 + 0.6 * d)), _poisson(rng, max(0.2, 1.1 - 0.6 * d))


def league_stats(teams, records, prev=None):
    """league.statistics {teamid: {wins, draws, losses, goals, goalsconceded, awaygoals,
    point, rank, before{rank, point}}} (LeagueCupProxy.makeLeagueRanking)."""
    s = {str(t): {"wins": 0, "draws": 0, "losses": 0, "goals": 0, "goalsconceded": 0,
                  "awaygoals": 0, "point": 0} for t in teams}
    for m in records:
        h, a = s[str(m["home"])], s[str(m["away"])]
        gh, ga = m["goals"]["home"], m["goals"]["away"]
        h["goals"] += gh; h["goalsconceded"] += ga
        a["goals"] += ga; a["goalsconceded"] += gh; a["awaygoals"] += ga
        if gh != ga:
            win, lose = (h, a) if gh > ga else (a, h)
            win["wins"] += 1; win["point"] += 3; lose["losses"] += 1
        else:
            h["draws"] += 1; a["draws"] += 1; h["point"] += 1; a["point"] += 1
    order = sorted(teams, key=lambda t: tuple(-v for v in (
        s[str(t)]["point"], s[str(t)]["goals"] - s[str(t)]["goalsconceded"], s[str(t)]["goals"], s[str(t)]["awaygoals"])))
    for i, t in enumerate(order):
        s[str(t)]["rank"] = i + 1
    for t, v in s.items():
        p = (prev or {}).get(t) or v
        v["before"] = {"rank": p["rank"], "point": p["point"]}
    return s


def my_teamid(st):
    return next(c["teamid"] for c in st["coaches"] if c["slot"] == _default_slot(st))


def l2_me(st):
    """GET /league2/me body; LeagueCupProxy.fetch* read each key as a path."""
    teamid = my_teamid(st)
    lg = st.get("league2") or {}
    if lg.get("league") and teamid not in lg["league"]["teams"]:  # another club's (saved before issue #40)
        lg = {"difficultyselect": lg.get("difficultyselect", 10)}
    me = {"teamid": teamid, "leagueid": lg.get("leagueid") or roster_summary(teamid).get("league", {}).get("id", 0),
          # creation screen: selectable = difficultybest - 10 (24 unlocks all 10 normal + 5
          # ultimate levels), preselected = difficultyselect - 10
          "difficultybest": 24, "difficultyselect": lg.get("difficultyselect", 10),
          # no cupqualifier/cupfinal keys: any object there makes fetchCupQualifier build
          # cup groups (makeLeagueCupGroupTeams -> TypeError on missing teamgroups)
          "laststatus": lg.get("laststatus", ""), "cupinfo": {"entered": False},
          "leader": {"league": {}, "cup": {}},
          # MenuScreen: season.status started/finished -> league front, else creation
          "sponsoroffice": lg.get("sponsoroffice", {"season": {"status": "none"}})}
    if lg.get("league"):
        me.update(openingday=lg["openingday"], league=lg["league"])
    return me


def l2_resources(st):
    return [{"uri": "/league2/me", "body": l2_me(st)}]


def ep_l2_me(query, body):
    return _json(l2_me(_state()))


def ep_l2_reward(query, body):
    # LeagueCupCreationScreen.onCalendarRewardsResourceReceived: Array indexed by difficulty
    # level (0-9 normal, 10-14 ultimate) of {ep, items[]}
    return _json([{"ep": 20000 * (i + 1), "items": []} for i in range(15)])


# sponsorid -> its mission id; LEAGUE2SPONSOR.mission[mid] = League2_sponsor_title_<mid> with
# the target in "lv1": 2 = finish in the top N, 3 = N wins in a row, 4 = N clean sheets in a row
SPONSORS = {"1": "2", "2": "3", "3": "4"}
MISSION_TARGET = {"2": 3, "3": 3, "4": 2}  # keep in sync with LEAGUE2SPONSOR lv1


def ep_l2_sponsor(difficulty, query, body):
    """POST /league2/sponsor/<difficulty>: offers. OnReceivedSponsorList reads level, maxround
    and sponsors[] {_id, sponsorid, missions[{mid}]} (names/logos/goals from LEAGUE2SPONSOR)."""
    st = _state(); teamid = my_teamid(st)
    n = len(league_teams(roster_summary(teamid).get("league", {}).get("id", 0), teamid))
    return _json({"result": True, "level": 1, "maxround": 2 * (n - 1) - 1,
                  "sponsors": [{"_id": sid, "sponsorid": sid, "missions": [{"mid": mid}]} for sid, mid in SPONSORS.items()]})


def mission_progress(st, mid):
    """(progress args, achieved) of a sponsor mission from my league results so far."""
    lg = st.get("league2") or {}; league = lg.get("league") or {}
    me = my_teamid(st)
    mine = sorted((m for m in league.get("matchrecords", []) if me in (m["home"], m["away"])), key=lambda m: m["round"])
    goals = [(m["goals"]["home"], m["goals"]["away"]) if m["home"] == me else (m["goals"]["away"], m["goals"]["home"]) for m in mine]

    def best_run(ok):
        best = run = 0
        for g in goals:
            run = run + 1 if ok(g) else 0; best = max(best, run)
        return best
    target = MISSION_TARGET.get(mid, 1)
    if mid == "2":
        rank = league.get("statistics", {}).get(str(me), {}).get("rank", 0)
        done = len(mine) > league.get("maxround", 0)  # season over
        return [rank], done and 0 < rank <= target
    value = best_run((lambda g: g[0] > g[1]) if mid == "3" else (lambda g: g[1] == 0))
    return [value], value >= target


def ep_l2_achievement(query, body):
    """PUT /league2/sponsor/achievement -> the chosen sponsor's goals for the calendar tab
    (setSponsorGoalList: missions[{mid, achievement, progress[args]}]; getProgressString fills
    LEAGUE2SPONSOR.mission[mid].progress with progress)."""
    st = _state(); lg = st.get("league2") or {}
    mid = SPONSORS.get(str(lg.get("sponsor")), "2")
    progress, done = mission_progress(st, mid)
    return _json({"result": True, "level": 1, "missions": [{"mid": mid, "achievement": done, "progress": progress}]})


# Club creation > "Tim lain" (StartupTeamCreationScreen + TeamSelectionUtil): nation -> league ->
# club steppers from SORTNATION [nationid], SORTLEAGUE {nationid: [leagueid]} and SORTTEAM
# {leagueid: [teamid]}; the picked club's squad is /static/info/authentic/team/licensed/<id>
# ({teamid, summary, team}, built by gen_teams.team like the six recommended clubs).
CLUB_MIN_PLAYERS = 16  # licensed 2017 cards a club needs to be listed
REST_OF_WORLD = 999998  # FO3's team for players of unlicensed clubs (26 LIVE has 91 such cards): not a club
_CLUBS, _CLUBS_KIT = [], []


@functools.lru_cache(maxsize=1)
def kit_teams():
    """The teamids with a kit in the client (data/sceneassets/kit/kit_<id>_*.rx3 in its data_*.big),
    or None when the client cannot be read. A match against a club without one crashed the game
    before it loaded (user: Ansan Greeners; six K League Challenge clubs have none)."""
    try:
        if os.path.join(HERE, "tools", "re") not in sys.path:
            sys.path.insert(0, os.path.join(HERE, "tools", "re"))
        import bigdir
        kits = {int(m.group(1)) for big in glob.glob(os.path.join(GAME, "data", "data_*.big"))
                for n, *_ in bigdir.entries(big) if (m := re.match(r"data/sceneassets/kit/kit_(\d+)_", n))}
    except Exception as e:
        print(f"client kits: {e!r}", flush=True)
        return None
    return kits or None


def has_kit(teamid):
    kits = kit_teams()
    return kits is None or int(teamid) in kits


def club_index():
    """all_clubs() without the clubs that have no kit in the client (has_kit): the club picker,
    Liga, the vs COM picker. A league or nation left empty is left out."""
    if not _CLUBS_KIT:
        _CLUBS_KIT[:] = [(n, ls) for n, ls in ((n, [(lid, ts) for lid, ts in ((lid, [t for t in ts if has_kit(t)])
                                                                             for lid, ts in leagues) if ts])
                                               for n, leagues in all_clubs()) if ls]
    return _CLUBS_KIT


def all_clubs():
    """[(nationid, [(leagueid, [teamid])])] of clubs with a full 2017 squad, strongest first (by
    their best eleven); a league's nation is its players' most common nationality. National
    teams are league 78 with their players' nationalteamid as the squad (gen_teams.team)."""
    if not _CLUBS:
        with closing(_db()) as c:  # current-season cards + 2017 cards of the players without one
            rows = c.execute(f"""SELECT p.spid, p.leagueid, p.teamid, p.nationalteamid, p.nationality, {POSOVR}
                                 FROM players p JOIN permid m ON m.spid = p.spid AND m.licensed = 1
                                 WHERE p.spid / 1000000 = ?1 OR (p.spid / 1000000 = ?2 AND p.playerid NOT IN
                                       (SELECT playerid FROM players WHERE spid / 1000000 = ?1))""",
                             (gen_teams.CURRENT_SEASON, gen_teams.LEGACY_SEASON)).fetchall()
        squads, nats = {}, {}
        for spid, lid, tid, ntid, nat, ovr in rows:
            squads.setdefault((lid, tid), {})[spid] = ovr
            if ntid > 0:  # -1 / 0 = none
                squads.setdefault((gen_teams.NATIONAL_LEAGUE, ntid), {})[spid] = ovr
            nats.setdefault(lid, collections.Counter())[nat] += 1
        power = {k: sum(sorted(v.values())[-11:]) / 11 for k, v in squads.items()
                 if len(v) >= CLUB_MIN_PLAYERS and k[1] != REST_OF_WORLD}
        leagues = {}
        for (lid, tid), p in power.items():
            leagues.setdefault(lid, []).append((p, tid))
        by_nation = {}
        for lid, teams in leagues.items():
            teams.sort(reverse=True)
            by_nation.setdefault(nats[lid].most_common(1)[0][0], []).append((teams[0][0], lid, [t for _, t in teams]))
        # the national teams' nation last (they made it the strongest; the picker opens on the first).
        # One assignment: two first builds at once (a request and presence_loop) give the same list
        _CLUBS[:] = [(n, [(lid, ts) for _, lid, ts in sorted(ls, reverse=True)])
                     for n, ls in sorted(by_nation.items(), key=lambda kv: (
                         any(l[1] == gen_teams.NATIONAL_LEAGUE for l in kv[1]), -max(l[0] for l in kv[1])))]
    return _CLUBS


def ep_sortnation(query, body):
    return _json([n for n, _ in club_index()])


def ep_sortleague(query, body):
    return _json({str(n): [lid for lid, _ in ls] for n, ls in club_index()})


def ep_sortteam(query, body):
    """/static/db/SORTTEAM {leagueid: [teamid]}: the club picker above; Liga's creation screen
    counts my own league's clubs (the ones its calendar will use)."""
    st = _state()
    out = {str(lid): ts for _, ls in club_index() for lid, ts in ls}
    if st["coaches"]:
        teamid = my_teamid(st)
        lid = roster_summary(teamid).get("league", {}).get("id", 0)
        out[str(lid)] = league_teams(lid, teamid)
    return _json(out)


def ep_authentic_team(kind, teamid, query, body):
    """/static/info/authentic/team/licensed|all/<teamid>: a club's starter squad {teamid, summary,
    team} (responses/ files override for the recommended clubs). StartupPlayerSelectionScreen
    (after "Tim lain") looks up team.captain among team.players by id and dresses its 3D player
    in kit.availables.home/goalie."""
    r = roster(int(teamid))
    team = dict(r["team"], players=[dict(p, id=str(p["spid"])) for p in r["team"]["players"]])
    team["captain"] = str(team["squad"].get("captain") or "")
    return _json({**r, "team": team, "teamid": int(teamid), "kit": team_kit(int(teamid))})


def ep_recommendation(query, body):
    """/static/info/authentic/team/recommendation: the six quick-creation clubs, built from
    fifadb (responses/'s gen_teams.py file serves them while 2017 is the current season)."""
    with closing(_db()) as c:
        return _json([t for t in (gen_teams.team(c, i) for i in gen_teams.DEFAULT_TEAMS) if t])


def ep_l2_calendar(query, body):
    """POST /league2/calendar {difficulty, sponsor}: start a season."""
    req = json.loads(body or b"{}")
    st = _state(); teamid = my_teamid(st)
    lid = roster_summary(teamid).get("league", {}).get("id", 0)
    teams = league_teams(lid, teamid)
    day = int(time.time() // 86400 * 86400 * 1000)  # today 00:00 UTC, ms
    st["league2"] = {"leagueid": lid, "difficultyselect": int(req.get("difficulty") or 10),
                     "sponsor": req.get("sponsor"), "laststatus": "opening", "openingday": day,
                     "sponsoroffice": {"season": {"status": "started"}},
                     "league": {"teams": teams, "maxround": 2 * (len(teams) - 1) - 1, "nextround": 0,
                                "matchrecords": [], "statistics": league_stats(teams, [])}}
    _save(st)
    return _json({"result": True, "resources": l2_resources(st)})


def league_play_round(st, rnd, mine=None, seed=None):
    """Play league round rnd: my fixture gets `mine` (home, away goals) or is simulated,
    the rest is simulated. Updates table + laststatus, returns the client's leagueparam."""
    lg = st["league2"]; league = lg["league"]
    if any(m["round"] == rnd for m in league["matchrecords"]):
        return {"matchtype": "league", "round": rnd, "leaguematches": [m for m in league["matchrecords"] if m["round"] == rnd]}
    rng = random.Random(seed)
    me = my_teamid(st); played = []
    for h, a in league_round(league["teams"], rnd):
        if h is None or a is None:
            continue
        if mine is not None and me in (h, a):
            gh, ga = mine
        else:
            gh, ga = sim_score(rng, team_strength(st, h), team_strength(st, a))
        if me in (h, a):  # played or simulated: Info Manager's league record
            record_match(st, "league", *((gh, ga) if h == me else (ga, gh)))
        played.append({"type": "league", "matchtype": "league", "round": rnd, "home": h, "away": a,
                       "goals": {"home": gh, "away": ga}})
    league["matchrecords"] += played
    league["statistics"] = league_stats(league["teams"], league["matchrecords"], league["statistics"])
    league["nextround"] = rnd + 1
    lg["laststatus"] = f"league:{rnd}"
    return {"matchtype": "league", "round": rnd, "leaguematches": played}


def league_match_body(st, req, playtype):
    me = coach_me(st, _default_slot(st)); teamid = me["teamid"]
    opp = req["away"] if req["home"] == teamid else req["home"]
    user = {"id": me["id"], "name": me["name"], "levelinfo": me["levelinfo"]}
    lg = st["league2"]
    match = new_match("League_2_league_MGR" if playtype == "mgr" else "League_2_league",
                      match_side(st["teams"][str(_default_slot(st))], teamid, user, _tactics(st)[1]),
                      match_side(roster_team(opp), opp))
    gs = match["gamesettings"]
    gs["home"] = req["home"] == teamid  # I am always the left side
    gs["kit"] = ["home", "away"] if gs["home"] else ["away", "home"]
    apply_prematch(st, match)
    # league level 10..24: the in-match overlay shows LEAGUE2_DIFFICULTY_<difficulty - 9>
    gs["difficulty"] = lg.get("difficultyselect", 10)
    # MatchProxy.leagueparam: LeagueCupGameSettingScreen.onPlaying reads leagueparam.matchtype
    # unguarded (`p != null && t == "cupqualifier" || p.matchtype == "cupfinal"`) -> required
    match["leagueparam"] = {"matchtype": req.get("matchtype", "league"), "round": req.get("round", 0),
                            "home": req["home"], "away": req["away"], "leagueid": lg.get("leagueid")}
    return match


def sim_events(match, mine, opp, rng=random):
    """eventlist for Liga's simulation screen (LeagueCupFrontScreenSimulation.rebuildSimulationEvent):
    [{time 1..90, team 0 = mine / 1, player = a starter's spid, event}] in time order. GOAL_EVENT
    adds to the score it shows, so there is one per goal of the result (an ASSIST_EVENT before
    some); SHOT_ON_GOAL_EVENT is a miss unless the keeper's SAVE_EVENT follows; a second
    YELLOW_CARD_EVENT for a player shows red. The screen adds kick-off, half time and full time.
    It was sent empty, so a simulated match showed 0-0 and no play whatever the result."""
    xi = [[(int(p["spid"]), p["pos"]) for p in match["side"][s]["team"]["squad"]["positions"] if p["pos"] < POS_SUB]
          for s in ("left", "right")]
    keeper = [next((s for s, pos in x if pos == 0), x[0][0]) for x in xi]
    def outfield(t, w=(1, 3, 6)):  # DF / MF / FW: forwards score and shoot most
        field = [(s, w[0] if pos <= 8 else w[1] if pos <= 19 else w[2]) for s, pos in xi[t] if pos]
        return rng.choices([s for s, _ in field], [n for _, n in field])[0]
    ev = []
    for t, goals in ((0, mine), (1, opp)):
        for _ in range(goals):
            minute, scorer = rng.randint(1, 90), outfield(t)
            if rng.random() < 0.6:
                ev.append({"time": minute, "team": t, "player": outfield(t, (2, 4, 2)), "event": "ASSIST_EVENT"})
            ev.append({"time": minute, "team": t, "player": scorer, "event": "GOAL_EVENT"})
    for _ in range(rng.randint(3, 7)):
        t, minute = rng.randint(0, 1), rng.randint(1, 90)
        ev.append({"time": minute, "team": t, "player": outfield(t), "event": "SHOT_ON_GOAL_EVENT"})
        if rng.random() < 0.6:
            ev.append({"time": minute, "team": 1 - t, "player": keeper[1 - t], "event": "SAVE_EVENT"})
    for _ in range(rng.randint(0, 3)):
        t = rng.randint(0, 1)
        ev.append({"time": rng.randint(1, 90), "team": t, "player": outfield(t, (4, 3, 1)), "event": "YELLOW_CARD_EVENT"})
    return sorted(ev, key=lambda e: e["time"])  # stable: an assist stays before its goal, a save after its shot


def sim_result(match, events, my_home, rng=random):
    """match.result for a simulated Liga match, shaped like the report a played match posts at
    /match/<id>/end, which PostMatchResultDefaultScene.onResultReceived reads (scores, the
    formation's player stats, possession, attack direction, MOTM). Built from the simulation's
    events; it was missing, so the result screen after a simulation stayed blank. id/dbid are
    player ids (spid % 1e6) as in the client's own report, spid the card's (fill_players); y counts
    from the far goal (keeper at 0.98)."""
    pair = lambda lo, hi, rate: (lambda n: [round(n * rate), n])(rng.randint(lo, hi))
    teams = []
    for t, s in ((0, "left"), (1, "right")):
        pos = match["side"][s]["team"]["squad"]["positions"]
        players = []
        for p in (p for p in pos if p["pos"] < POS_SUB):
            mine = [e["event"] for e in events if e["team"] == t and e["player"] == int(p["spid"])]
            goals, assists, shots = mine.count("GOAL_EVENT"), mine.count("ASSIST_EVENT"), mine.count("SHOT_ON_GOAL_EVENT")
            saves, yellow = mine.count("SAVE_EVENT"), mine.count("YELLOW_CARD_EVENT")
            pid, start = int(p["spid"]) % 1000000, p.get("starting") or [1, 1, 0.5, 0.5]
            players.append({
                # role = the squad position (0 = keeper put every player at the goal) and spid = the
                # card's, whose season the labels show (issue #26); id/dbid stay the player id
                "id": str(pid), "spid": int(p["spid"]), "dbid": pid, "role": p["pos"], "x": start[2], "y": round(1 - start[3], 4),
                "rating": round(min(10.0, max(4.0, 6.0 + goals + 0.5 * assists + 0.3 * saves - 0.4 * yellow
                                                   + rng.uniform(-0.4, 0.9))), 1),
                "goals": goals, "assists": assists, "saves": saves, "shotsOnGoal": goals + shots,
                "shotAttempt": goals + shots + rng.randint(0, 1) * bool(p["pos"]),
                "passes": pair(20, 50, rng.uniform(0.7, 0.92)) if p["pos"] else pair(5, 15, 0.8),
                "dribbles": pair(0, 6, 0.6), "tackles": pair(0, 5, 0.6) if p["pos"] else [0, 0],
                "cards": [min(yellow, 1), int(yellow >= 2)], "injure": [0, 0],
                "runDistanceMeter": round(rng.uniform(8000, 12000) if p["pos"] else rng.uniform(3000, 5000), 1),
                "longTermStamina": 1})
        total = lambda k: sum(p[k] for p in players)
        sums = lambda k: [sum(p[k][0] for p in players), sum(p[k][1] for p in players)]
        teams.append({
            "id": match["side"][s]["team"]["teamid"], "score": total("goals"), "goals": total("goals"),
            "assists": total("assists"), "saves": total("saves"), "shotsOnGoal": total("shotsOnGoal"),
            "shotAttempt": total("shotAttempt"), "passes": sums("passes"), "dribbles": sums("dribbles"),
            "tackles": sums("tackles"), "cards": sums("cards"), "crossPasses": pair(5, 20, 0.35),
            "throughPasses": pair(2, 10, 0.4), "blocks": pair(0, 5, 0.5), "fouls": sums("cards")[0] + rng.randint(2, 8),
            "offsides": rng.randint(0, 3), "clearances": rng.randint(5, 20), "intercepts": rng.randint(5, 20),
            "tackles_inbox": 0, "passes_inbox": 0, "intercepts_inbox": 0, "dribble_inbox": 0,
            "rating": round(sum(p["rating"] for p in players) / len(players), 2), "runDistanceKm": round(total("runDistanceMeter") / 1000, 1),
            "players": players, "substitutes": [], "users": [], "shootout": 0, "injuredPlayers": 0,
            "reserved": [{"id": str(int(p["spid"]) % 1000000), "rating": 0, "dbid": int(p["spid"]) % 1000000}
                         for p in pos if p["pos"] == POS_SUB]})
    mine_poss = rng.randint(40, 60)  # possession totals add up to 100; left / center / right likewise
    for team, share in zip(teams, (mine_poss, 100 - mine_poss)):
        left = rng.randint(15, 45); center = rng.randint(20, 100 - left - 10)
        team["possession"] = {"total": share, "left": left, "center": center, "right": 100 - left - center,
                              "df": rng.randint(10, 30), "mf": rng.randint(40, 60), "fw": rng.randint(10, 30)}
    me, opp = ("home", "away") if my_home else ("away", "home")
    side = {me: teams[0], opp: teams[1]}
    team_of, best = max(((name, p) for name, team in side.items() for p in team["players"]), key=lambda x: x[1]["rating"])
    return {"myTeam": me, "opponentTeam": opp, "home": side["home"], "away": side["away"], "matchFinishedTime": 5400,
            "forfeits": 0, "motm": {"team": team_of, "id": best["id"], "spid": best["spid"], "dbid": best["dbid"],
                                    "rating": best["rating"], "name": "XXDISPLAYNAMEXX"}}


def ep_l2_match(query, body, sim=False):
    """POST /league2/match {matchtype, playtype, round, home, away, date} -> a /match/<id>
    resource (MatchProxy.registerByResource). /match/sim plays the round right away."""
    req = json.loads(body or b"{}")
    st = _state(); lg = st.get("league2")
    if not lg or not lg.get("league"):
        return _json({"result": False, "reason": "LEAGUE_FAIL"})
    coupon = None
    if sim:  # one League Simulation coupon (any item of that type, as the client counts them)
        coupon = next((i for i in my_coach(st).get("items", []) if i["remains"] > 0 and i["itemid"] in ITEMS
                       and ITEMS[i["itemid"]][2] == "LEAGUE_SIMULATION"), None)
        if coupon is None:
            return _json({"result": False, "reason": "NOT_ENOUGH_ITEM"})
        coupon["remains"] -= 1
        my_coach(st)["items"] = [i for i in my_coach(st)["items"] if i["remains"] > 0]
    match = league_match_body(st, req, req.get("playtype"))
    st["match"] = match
    lg["pending"] = {"id": match["id"], "round": req.get("round", 0), "home": req["home"], "away": req["away"]}
    out = {"result": True}
    if sim:
        param = league_play_round(st, req.get("round", 0))
        mine = next((m for m in param["leaguematches"] if my_teamid(st) in (m["home"], m["away"])), None)
        match.update(stage="closed", simulated=True)
        match["gamesettings"]["simulated"] = True
        g = mine["goals"] if mine else {"home": 0, "away": 0}
        mine_g, opp_g = (g["home"], g["away"]) if mine and mine["home"] == my_teamid(st) else (g["away"], g["home"])
        events = sim_events(match, mine_g, opp_g)
        match["result"] = sim_result(match, events, match["gamesettings"]["home"])  # the result screen after it
        # then the reward screen: EXP and EP as after a played match (without it the screen stayed
        # empty and the way back to the Liga menu stopped there)
        match["reward"] = match_reward(st, match["result"], live=False)  # no tactic points for a simulation
        add_ep(st, match["reward"]["money"]["reward"]["total"])
        lg.pop("pending", None)  # the round is played already
        out.update(leagueparam=param, eventlist=events, score=mine and mine["goals"])
    _save(st)
    out["resources"] = ([{"uri": f"/match/{match['id']}", "body": match}]
                        + (l2_resources(st) + coach_me_resources(st, _default_slot(st)) if sim else []))  # coupon count
    return _json(out)


def ep_l2_sleep(query, body):
    """POST /league2/sleep {matchtype, round}: my bye round; the others still play."""
    req = json.loads(body or b"{}")
    st = _state(); lg = st.get("league2")
    param = league_play_round(st, req.get("round", 0)) if lg and lg.get("league") else {}
    _save(st)
    return _json({"result": True, "leagueparam": param, "resources": l2_resources(st)})


def ep_l2_finish(query, body):
    """PUT /league2/abandon: give up the season (back to the creation screen)."""
    st = _state()
    lg = st.get("league2") or {}
    st["league2"] = {"difficultyselect": lg.get("difficultyselect", 10)}
    _save(st)
    return _json({"result": True, "resources": l2_resources(st)})


LINES = {"GK": range(0, 1), "DF": range(1, 9), "MF": range(9, 20), "FW": range(20, 28)}  # Enums.PLAYERPOSITION
STAT_LEADERS = 3  # players per stat on the Rekor gabungan tab


def league_record(st, stats):
    """Add a played league match to my players' season record (st.league2.playerrecords, reset by
    a new season): what Rekor liga > Rekorku shows. Unused subs have no rating; passes and tackles
    are [done, tried] in matchStats; role = the position he played (Enums.PLAYERPOSITION)."""
    recs = st["league2"].setdefault("playerrecords", {})
    for p, s in match_cards(st, stats):
        if not s.get("rating"):
            continue
        r = recs.setdefault(iid(p), dict.fromkeys(("matches", "ratings", "goals", "assists", "passes",
                                                     "passtries", "tackles", "saves"), 0))
        pair = lambda k: (list(s.get(k) or []) + [0, 0])[:2]
        r.update(id=iid(p), spid=p["spid"], role=int(s.get("role") or 0))
        r["matches"] += 1
        r["ratings"] = round(r["ratings"] + float(s["rating"]), 2)
        for k in ("goals", "assists", "saves"):
            r[k] += int(s.get(k) or 0)
        r["passes"] += int(pair("passes")[0]); r["passtries"] += int(pair("passes")[1])
        r["tackles"] += int(pair("tackles")[0])


def ep_l2_history_players(query, body):
    """GET /league2/history/players/league: [{id: card _id, matches, ratings (sum), goals, assists,
    passes, tackles, saves}] (LeagueCupRecordTabPage.joinPlayerData; a card with none shows 0)."""
    return _json(list(((_state().get("league2") or {}).get("playerrecords") or {}).values()))


def ep_l2_leaders(query, body):
    """PUT /league2/leader/league -> Rekor gabungan (LeagueCupTotalRecordTabPage): best XI by line
    (my formation's slots, best average rating of the players who played there) and the top
    players per stat, each {spid, teamid, value}, from my players' season records. The page
    joins every key of the reply into one player list, so it holds nothing else. ponytail: my
    players only (AI players' stats are not kept)."""
    st = _state(); me = my_teamid(st)
    recs = [r for r in ((st.get("league2") or {}).get("playerrecords") or {}).values() if r["matches"]]
    avg = lambda r: round(r["ratings"] / r["matches"], 1)
    entry = lambda r, v: {"spid": r["spid"], "teamid": me, "value": v}
    squad = (st["teams"].get(str(_default_slot(st))) or {"squad": {"positions": []}})["squad"]["positions"]
    out = {}
    for line, rng in LINES.items():
        slots = sum(s["pos"] in rng for s in squad)
        out[line] = [entry(r, avg(r)) for r in sorted((r for r in recs if r["role"] in rng), key=avg, reverse=True)[:slots]]
    stat = {"ratings": avg, "goals": lambda r: r["goals"], "assists": lambda r: r["assists"],
            "tackles": lambda r: r["tackles"], "saves": lambda r: r["saves"],
            "passes": lambda r: round(r["passes"] / r["passtries"], 2) if r["passtries"] else 0}  # success rate
    for k, f in stat.items():
        out[k] = [entry(r, f(r)) for r in sorted(recs, key=f, reverse=True) if f(r)][:STAT_LEADERS]
    return _json(out)


def season_leaders(st, me, rank, goals):
    """leaderRewards for LeagueCupLeagueRewardScreen: best XI by line (FW/MF/DF/GK, 11 in
    all) + one leader per stat. ponytail: no per-player season stats are kept, so these
    come from my own squad (rating = card rating, stat values derived from team goals)."""
    team = owned_team(st["teams"][str(_default_slot(st))])
    rating = {p["spid"]: player_ovr(p) for p in team["players"]}
    lines = LINES
    starters = [p for p in team["squad"]["positions"] if p["pos"] < POS_SUB]
    entry = lambda spid, value, ep=0: {"spid": spid, "teamid": me, "value": value, "reward": {"count": ep}}
    out = {line: [entry(p["spid"], round(min(9.9, 6 + (rating.get(p["spid"], 80) - 80) / 25), 1))
                  for p in starters if p["pos"] in r] for line, r in lines.items()}
    best = lambda line: max((p["spid"] for p in starters if p["pos"] in lines[line]),
                            key=lambda s: rating.get(s, 0), default=0)
    played, ep = 2 * (len(((st.get("league2") or {}).get("league") or {}).get("teams") or [0] * LEAGUE_SIZE) - 1), LEADER_EP
    out.update(ratings=[entry(best("FW"), 7.5, ep["ratings"])], goals=[entry(best("FW"), max(1, goals // 2), ep["goals"])],
               assists=[entry(best("MF"), max(1, goals // 3), ep["assists"])], tackles=[entry(best("DF"), played * 3, ep["tackles"])],
               passes=[entry(best("MF"), 0.85, ep["passes"])],  # pass success rate; the UI shows value*100 %
               saves=[entry(best("GK"), played * 2, ep["saves"])])
    return out


LEADER_EP = {"ratings": 3000, "goals": 3000, "assists": 2000, "tackles": 2000, "passes": 1000, "saves": 1000}
PLACE_EP = 2000  # a season's EP per place above the bottom one (ep_l2_end)


def season_top_ep():
    """The most EP a League season's end pays (ep_l2_end): 1st place, the top sponsor reward and
    every stat leader's."""
    with open(os.path.join(RESPONSES, "static_db_LEAGUE2REWARD.json")) as f:
        sponsor = max((v.get("count", 0) for m in json.load(f)["sponsor"].values() for v in m.values()
                       if isinstance(v, dict)), default=0)
    return LEAGUE_SIZE * PLACE_EP + sponsor + sum(LEADER_EP.values())


def league_promote(difficulty):
    """{promote_rank, demote_rank} of a league level: how many clubs go up / down at season end.
    /static/db/LEAGUE2DIFFICULTY: league2difficulty {level: {groupId}} + league2promote {groupId:
    ...}; the standings tab (LeagueCupRankTabPage) colours its rows by it and threw on a missing
    league2difficulty, leaving the table empty (issue #19). Normal 10-19 only go up (19 is the
    top), ultimate 20-24 also go down."""
    with open(os.path.join(RESPONSES, "static_db_LEAGUE2DIFFICULTY.json")) as f:
        t = json.load(f)
    group = t["league2difficulty"].get(str(difficulty), {}).get("groupId")
    return t["league2promote"].get(str(group), {"promote_rank": 0, "demote_rank": 0})


def ep_l2_end(query, body):
    """PUT /league2/end (closing day): season result for LeagueCupLeagueRewardScreen
    {promotion{current,next}, rewards{coach,money.reward{total,detail}}, sponsorRewards,
    leaderRewards, seasonRewards{items}}. The season stays (the reward screen reads it) but
    is marked finished: back on the front, the closing day then has seasonoff -> ON_CLOSED
    -> the creation screen, whose POST /league2/calendar starts the next season."""
    st = _state(); lg = st.get("league2") or {}; league = lg.get("league")
    if not league:
        return _json({"result": False, "reason": "LEAGUE_FAIL"})
    me = my_teamid(st); stats = league["statistics"][str(me)]; rank = stats["rank"]
    cur = lg.get("difficultyselect", 10)
    rule = league_promote(cur)
    nxt = cur + 1 if rank <= rule["promote_rank"] else cur - 1 if rank > len(league["teams"]) - rule["demote_rank"] else cur
    mid = SPONSORS.get(str(lg.get("sponsor")), "2")
    progress, done = mission_progress(st, mid)
    with open(os.path.join(RESPONSES, "static_db_LEAGUE2REWARD.json")) as f:
        sponsor_ep = json.load(f)["sponsor"].get(str(int(mid) - 1), {}).get(str(cur), {}).get("count", 0) if done else 0
    leaders = season_leaders(st, me, rank, stats["goals"])
    leader_ep = sum(e["reward"]["count"] for k in ("ratings", "goals", "assists", "tackles", "passes", "saves")
                    for e in leaders[k])
    share = (len(league["teams"]) - rank + 1) * LEAGUE_SIZE / len(league["teams"])  # 1st: as in a 10-club league
    base_ep = round(share * PLACE_EP)
    season_exp = round(share * 100)
    cprev, ccur = coach_gain_exp(st, season_exp)
    out = {"result": True, "promotion": {"current": cur, "next": nxt},
           # setRewardRankExp: coach.prev/current/reward.total; setRewardRankEP: money.prev +
           # reward.detail.season; the sponsor/leader scenes read detail.sponsor/.leader
           "rewards": {"coach": {"prev": cprev, "current": ccur, "reward": {"total": season_exp}},
                       "money": {"prev": coach_me(st, _default_slot(st))["money"]["ep"],
                                 "reward": {"total": base_ep + sponsor_ep + leader_ep,
                                            "detail": {"season": base_ep, "sponsor": sponsor_ep, "leader": leader_ep}}}},
           "sponsorRewards": {"sponsorid": str(lg.get("sponsor")), "level": 1, "achievement": done,
                              "missions": [{"mid": mid, "achievement": done, "progress": progress}]},
           "leaderRewards": leaders, "seasonRewards": {"items": []}}
    if (lg.get("sponsoroffice") or {}).get("season", {}).get("status") != "finished":  # once a season
        r = coach_record(my_coach(st), "league")
        track, best = ("ultimate", "ultimatebest") if cur >= 20 else ("normal", "noramlbest")
        r[track + "wins"] = r.get(track + "wins", 0) + (rank == 1)
        r[best] = max(r.get(best, 0), max(cur, nxt) + 1)  # Info Manager shows it - 10 / - 20: the level's number
    lg.update(difficultyselect=nxt, sponsoroffice={"season": {"status": "finished"}})
    add_ep(st, out["rewards"]["money"]["reward"]["total"])
    _save(st)
    out["resources"] = l2_resources(st)
    return _json(out)


def match_step(mid, body, step):
    """Apply step(match, request) to the live league match (state["match"]) and answer with
    the updated /match/<id> resource; MatchController re-runs processMatchStageResource."""
    req = json.loads(body or b"{}")
    st = _state(); match = st.get("match")
    if not match or match["id"] != mid:
        return _json({"result": False})
    step(match, req)
    _save(st)
    return _json({"result": True, "resources": [{"uri": f"/match/{mid}", "body": match}]})


def _side_ready(side):
    def step(match, req):
        if side in match["side"]:
            match["side"][side]["ready"] = bool(req.get("ready", True))
        if req.get("ready", True):
            match["stage"] = "playing"
    return step


def ep_match_ready(mid, side, query, body):
    """PUT /match/<id>/side/<side>/ready {ready, stage}: single-player, so the match starts
    (stage playing -> LeagueCupGameSettingScreen.onPlaying -> StartSingleMatch). A Rank match is
    PvP: the multiplay game setting screen first (stage setting), then playing."""
    ladder = (_state().get("ladder_pending") or {}).get("id") == mid
    return match_step(mid, body, _pvp_side_ready(side) if ladder else _side_ready(side))


def _pvp_side_ready(side):
    """squad -> setting (my ready cleared: MatchController.sendReady skips a side already
    ready, so the setting screen could not send its own), setting -> playing."""
    def step(match, req):
        ready = bool(req.get("ready", True))
        match["side"][side]["ready"] = ready
        if ready and match["stage"] == "squad":
            match["stage"] = "setting"
            match["side"][side]["ready"] = False
        elif ready and match["stage"] == "setting":
            match["stage"] = "playing"
    return step


NEXT_STAGE = {"created": "squad", "squad": "setting"}


def _player_ready(match, req):
    if req.get("ready", True) and req.get("stage", match["stage"]) == match["stage"]:
        match["stage"] = NEXT_STAGE.get(match["stage"], match["stage"])


def ep_match_player_ready(mid, query, body):
    """PUT /match/<id>/player/ready {ready, stage}: created -> squad (ON_SQUADING: squad screen),
    squad -> setting (LeagueCupSquadSettingHandler.updateStage: game setting screen)."""
    return match_step(mid, body, _player_ready)


def match_cards(st, stats):
    """[(my card, its matchStats player)] for my side's players. In-match spid = the card's
    player id (spid % 1e6); a card in the squad wins over another card of the same player."""
    team, done, out = st["teams"].get(str(_default_slot(st))) or {"players": []}, set(), []
    for s in (stats.get(stats.get("myTeam", "home")) or {}).get("players") or []:
        pid = int(s.get("spid") or 0) % 1000000
        mine = [p for p in team["players"] if int(p["spid"]) % 1000000 == pid and iid(p) not in done]
        p = next((p for p in mine if in_squad(team, p)), mine[0] if mine else None)
        if p is not None:
            done.add(iid(p))
            out.append((p, s))
    return out


# Post-match reward cards (issue #26): PostMatchRewardDefaultScene deals cards 1-3 ("normal";
# card 4 is the PC-bang one) and turns no request on a click: the pick is the server's. The
# clicked card shows extra.availables[extra.selected.normal[0]] (RewardCardFront.setItemData:
# staticdata name/unit/itemicon, count) and openRestCards shows the others on the rest; the
# top-level selected {type: [index]} takes the picked ones out of that rest.
# The admin page edits all of it (gamedata/match_rewards.json): EP and exp (coach, each of my
# players) by result, and the cards [[weight, itemid | {"ep": amount}]] as in shop.json's boxes.
MATCH_REWARD_FILE = os.path.join(HERE, "gamedata", "match_rewards.json")
MATCH_REWARD_DEFAULT = {
    "result": {"win": {"ep": 2000, "coach": 300, "player": 150}, "draw": {"ep": 1000, "coach": 200, "player": 100},
               "loss": {"ep": 500, "coach": 100, "player": 60}},
    "cards": [[30, {"ep": 1000}], [25, {"ep": 3000}], [15, {"ep": 5000}], [8, {"ep": 10000}],
              [10, 92000000], [7, 92000014], [5, 501]]}  # Semua Pemain, '14 Top 200, Lucky EP


def match_reward_cfg():
    try:
        with open(MATCH_REWARD_FILE, encoding="utf-8") as f:
            return {**MATCH_REWARD_DEFAULT, **json.load(f)}
    except (OSError, ValueError):
        return MATCH_REWARD_DEFAULT


def match_cards_reward(st):
    """extra/selected of match.reward: three cards, the first is mine (credited here). A card
    whose product is gone from the Shop is skipped (its staticdata would fail)."""
    pool = [(w, ("ep", p["ep"]) if isinstance(p, dict) else p) for w, p in match_reward_cfg()["cards"]
            if isinstance(p, dict) or p in ITEMS or p in PACKS]
    if not pool:
        return {"extra": {"selected": {}, "availables": []}, "selected": {}}
    cards = random.choices([p for _, p in pool], [w for w, _ in pool], k=3)
    if isinstance(cards[0], tuple):
        add_ep(st, cards[0][1])
    else:
        inventory_add(my_coach(st), cards[0], 1)
    pick = {"normal": [0]}
    return {"extra": {"selected": pick, "availables": [prize_item(p) for p in cards]}, "selected": pick}


def match_reward(st, stats, own=True, live=True):
    """match.reward for PostMatchRewardDefaultScene.onRewardReceived (paths it reads) and its
    player list (PlayerRewardListItemRenderer: id, spid, prev, current, reward.detail.extra).
    Credits EP, coach exp and exp for every player of mine in matchStats (in-match spid = the
    card's player id, i.e. spid % 1e6), plus a reward card, and the tactic points of a live match
    of my own side (growth_after_match). ponytail: flat exp by result + goals/assists."""
    r = match_reward_cfg()["result"][("loss", "draw", "win")[match_outcome(stats) + 1]]
    ep, cexp, pexp = r["ep"], r["coach"], r["player"]
    boost = club_boosts(st)  # my club's points of this week (issue #36)
    reward = lambda n, kind: {"total": n + round(n * boost.get(kind, 0)), "detail": {"base": n, "extra": round(n * boost.get(kind, 0))}}
    coach = reward(cexp, "MATCH_PLUS_COACHEXP")
    prev, cur = coach_gain_exp(st, coach["total"])
    players = []
    for p, s in match_cards(st, stats) if own else ():
        n = reward(pexp + 20 * int(s.get("goals") or 0) + 10 * int(s.get("assists") or 0), "MATCH_PLUS_PLAYEREXP_ALL")
        pprev, pcur = gain_exp(p, n["total"], player_need, PLAYER_MAX_LEVEL)
        players.append({"id": iid(p), "spid": p["spid"], "prev": pprev, "current": pcur, "reward": n})
    return {"matchRewardBackType": 0, **match_cards_reward(st),
            "coach": {"reward": coach, "prev": prev, "current": cur},
            "money": {"reward": reward(ep, "MATCH_PLUS_GAMEMONEY")},
            "players": players, "missions": [], "exptankinfo": {}, "sp": growth_after_match(st, stats, played=own and live)}


def ep_match_get(mid, query, body):
    """GET /match/<id>: the post-match screens re-read result/reward from the match."""
    match = _state().get("match") or {}
    return _json(match if match.get("id") == mid else {})


def ep_match_gamesettings(mid, query, body):
    """PUT /match/<id>/gamesettings from the game setting screen: {kit: home|away, side: 0 left|1
    right} (the kit buttons; gamesettings.kit is [left, right], MatchController
    .processMatchGamesettingsKitResource: mine = kit[mySide == left ? 0 : 1]), and on Rank's screen
    {stadium}, {tod}, {weather}, {halfLength}, {ball: {id}} (its steppers; queued requests to one uri
    merge, so one body can carry several). The steppers move only when the updated match resource
    comes back; native uses gamesettings at kick-off. A stadium / tod / weather / length chosen here
    is the player's choice for the next matches too (match_prefs, issue #31)."""
    req = json.loads(body or b"{}")
    st = _state(); match = st.get("match") or {}
    if match.get("id") != mid:  # e.g. a Training Ground match (not stored): as before
        return _json({"result": True})
    gs = match["gamesettings"]
    if req.get("kit") in ("home", "away") and req.get("side") in (0, 1):
        gs["kit"][req["side"]] = req["kit"]
    for key, ok in (("stadium", STADIUMS_SAFE), ("tod", TODS), ("weather", WEATHERS), ("halfLength", HALF_LENGTHS)):
        v = req.get(key)
        v = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) or str(v).isdigit() else None
        if v in ok:
            gs[key] = st.setdefault("matchprefs", {})[key] = v
    ball = req.get("ball").get("id") if isinstance(req.get("ball"), dict) else None
    if not isinstance(ball, bool) and ball in [b.get("id") if isinstance(b, dict) else b for b in gs["ball"]["availables"]]:
        gs["ball"]["id"] = ball
        if ball in BALLS:
            st.setdefault("matchprefs", {})["ball"] = ball
    _save(st)
    return _json({"result": True, "resources": [{"uri": f"/match/{mid}", "body": match}]})


def fill_players(match, stats):
    """The game sends matchStats players with role 0 and the player id (spid % 1e6) as spid,
    though x/y are right. The result screen's pitch (GameResultPlayerInfo) puts role 0 = GK at
    the goal, so all eleven were stacked there, and its labels take the season from the spid
    (SeasonIcon.setSeasonBySpid), so every card showed the current season (issue #26).
    players[i] is squad slot i: role = that slot's pos; spid = the card's full spid (players,
    substitutes, MOTM)."""
    sides = match.get("side") or {}
    if not sides:
        return
    for key, default in (("home", "left"), ("away", "right")):
        t = stats.get(key) or {}
        hits = [s for s in sides if str(sides[s]["team"]["teamid"]) == str(t.get("id"))]
        pos = sides[hits[0] if len(hits) == 1 else default]["squad"]["positions"]
        for p, slot in zip(t.get("players") or [], pos):
            if not p.get("role"):
                p["role"] = slot["pos"]
        full = {int(s["spid"]) % 1000000: int(s["spid"]) for s in pos}
        mine = (t.get("players") or []) + (t.get("substitutes") or [])
        if (stats.get("motm") or {}).get("team") == key:
            mine.append(stats["motm"])
        for e in mine:
            if isinstance(e, dict) and e.get("spid"):
                e["spid"] = full.get(int(e["spid"]) % 1000000, e["spid"])


def ep_match_end(mid, query, body):
    """PUT /match/<id>/end matchStats {home:{id, score, players...}, away:{...}, myTeam,
    opponentTeam, motm, ...}. It comes back as match.result (PostMatchResultDefaultScene
    .onResultReceived reads exactly these fields) plus a match.reward."""
    req = json.loads(body or b"{}")
    st = _state(); lg = st.get("league2") or {}; p = lg.get("pending")
    match = st.get("match") if (st.get("match") or {}).get("id") == mid else None
    out = {"result": True}
    if match is not None and match.get("stage") != "closed":  # credit once
        fill_players(match, req)
        match["result"] = {k: v for k, v in req.items() if k != "_method"}
        exhibition = (st.get("ladder_pending") or {}).get("id") == mid and st["ladder_pending"].get("kind") == "exhibition"
        match["reward"] = match_reward(st, req, own=not exhibition)
        add_ep(st, match["reward"]["money"]["reward"]["total"])
        staff_after_match(st)
        ladder_result(st, mid, req)
        score = lambda side: int((req.get(side) or {}).get("score") or 0)  # missions count it
        gf = score(req.get("myTeam", "home"))
        rank, won = str(match.get("type", "")).startswith("Ladder"), match_outcome(req)
        mission_event(st, match=1, win=int(won > 0), goal=gf, rank=int(rank), rankwin=int(rank and won > 0))
        mtype = str(match.get("type", ""))
        club_after_match(st, won, "RANKED" if rank or mtype == "Regional_League" else "FRIENDLY" if mtype.startswith("Friendly")
                         else "CUP" if (st.get("cup_pending") or {}).get("id") == mid else "LEAGUE")
    cp = st.get("cup_pending") or {}
    if cp.get("id") == mid and cp.get("cupid") in (st.get("cups") or {}):
        h, a = req.get("home") or {}, req.get("away") or {}
        gh, ga = int(h.get("score") or 0), int(a.get("score") or 0)
        sh, sa = int(h.get("shootout") or 0), int(a.get("shootout") or 0)
        if str(h.get("id")) == str(cp["away"]):
            gh, ga, sh, sa = ga, gh, sa, sh
        st["cups"][cp["cupid"]]["_mine"] = (gh, ga, sh, sa)  # applied by PUT /cup/<id>/next
        st.pop("cup_pending")
    if p and p["id"] == mid:
        h, a = req.get("home") or {}, req.get("away") or {}
        gh, ga = int(h.get("score") or 0), int(a.get("score") or 0)
        if str(h.get("id")) == str(p["away"]):  # matchStats home/away are match sides
            gh, ga = ga, gh
        out["leagueparam"] = league_play_round(st, p["round"], (gh, ga))
        league_record(st, req)
        lg.pop("pending", None)
        out["resources"] = l2_resources(st)
    if match is not None:
        match["stage"] = "closed"
        out["resources"] = [{"uri": f"/match/{mid}", "body": match}] + out.get("resources", []) + achievement_resources(st)
    _save(st)
    return _json(out)


# --------------------------------------------------------------------------- #
# Single > Cup (PvE cups; also the "Eropa/Amerika Selatan" shortcut). CupProxy/CupController.
#   GET /cup/list -> metas the front screen lists; GET /cup/record -> my cup records.
# --------------------------------------------------------------------------- #
def cup_meta(metaid, name, trophy, quality, matchcount, teams, reward):
    """A /cup/list entry: CupFrontScreen.onCupDetailInternal + CupProxy.onCupListReceived
    fields. difficulty = minimum AI level index (0 amateur .. 4 legendary)."""
    # visible: Boolean (isVisibleValue indexes it per condition otherwise); joinable.allteams
    # skips the teamids/leagueids whitelist ("Tim ini tidak dapat bergabung")
    return {"metaid": metaid, "_id": str(metaid), "name": name, "type": "pve", "teamselect": "user", "visible": True,
            "difficulty": 0, "quality": quality, "matchcount": matchcount, "teamcount_desc": teams,
            "proviso_desc": "PVECUP_INFO_OPTION_NO", "trophy": trophy, "skin": str(metaid),
            # onCupDetailInternal sums bestrewards[-1][*].count where itemid == 1 (EP)
            "bestrewards": [[{"itemid": 1, "count": reward}]],
            "startat": "2020-01-01T00:00:00.000Z", "endat": "2030-12-31T00:00:00.000Z",
            "joinable": {"fee": 0, "igr": False, "vip": 0, "allteams": True, "dayofweek": [1, 1, 1, 1, 1, 1, 1]}}


CUPS = [cup_meta(1, "European Champions Cup", "66", 4, 6, 16, 60000),  # 3 group + QF/SF/F
        cup_meta(2, "South American Cup", "300000", 3, 6, 16, 40000)]


def ep_cup_list(query, body):
    return _json([] if "type=wcpve" in query else CUPS)  # wcpve = World Cup PvE list


def cup_pool(metaid, mine):
    """15 AI clubs: the strongest clubs in fifadb (cup 1) or the next tier (cup 2)."""
    with closing(_db()) as c:
        rows = c.execute("""SELECT p.teamid FROM players p JOIN permid m ON m.spid = p.spid AND m.licensed = 1
                            WHERE p.spid < 1000000 GROUP BY p.teamid HAVING COUNT(*) >= 16
                            ORDER BY MAX(p.overallrating) DESC LIMIT 60""").fetchall()
    ids = [r[0] for r in rows if r[0] != mine]
    return ids[:15] if metaid == 1 else ids[15:30]


def cup_team(st, teamid):
    me = my_teamid(st)
    return {"teamid": teamid, "coach": coach_id(_default_slot(st)) if teamid == me else None}


def new_cup(st, meta, difficulty):
    """A cup instance (CupProxy.updateCurrentCupData): 16 clubs, 4 round-robin groups of 4
    (3 rounds), then an 8-club knock-out. pairs[].legs[leg] = {homeidx, awayidx, score?,
    shootout?} index into that group's teams; rounds[].shuffle -> the draw screen."""
    me = my_teamid(st)
    teams = [me] + cup_pool(meta["metaid"], me)
    random.shuffle(teams)
    groups = [{"teams": [cup_team(st, t) for t in teams[g * 4:g * 4 + 4]],
               "rounds": [{"shuffle": r == 0, "pairs": [{"legs": [{"homeidx": h, "awayidx": a}]}
                                                         for h, a in league_round([0, 1, 2, 3], r)]}
                          for r in range(3)]} for g in range(4)]
    cupid = f"{int(time.time()):08x}{os.urandom(8).hex()}"
    cup = {"_id": cupid, "metaid": meta["metaid"], "name": meta["name"], "type": "pve",
           "difficulty": difficulty, "current": {"phase": 0, "round": 0, "leg": 0},
           "phases": [{"system": "round-robin", "name": "Fase grup", "maxrounds": 3, "groups": groups}],
           "coaches": [{"_id": coach_id(_default_slot(st)), "state": "playing"}],
           "teams": [cup_team(st, t) for t in teams]}
    cup_refresh_stats(cup)
    return cup


def cup_my_pair(cup):
    """(group, leg) of my fixture in the current round, or (None, None)."""
    cur = cup["current"]
    for g in cup["phases"][cur["phase"]]["groups"]:
        for pair in g["rounds"][cur["round"]]["pairs"]:
            leg = pair["legs"][cur["leg"]]
            idx = [leg.get("homeidx"), leg.get("awayidx")]
            if any(i is not None and g["teams"][i]["coach"] for i in idx):
                return g, leg
    return None, None


def group_table(g):
    """[(team index, points, goal diff, goals)] best first."""
    t = {i: [0, 0, 0] for i in range(len(g["teams"]))}
    for rnd in g["rounds"]:
        for pair in rnd["pairs"]:
            leg = pair["legs"][0]
            if "score" not in leg:
                continue
            gh, ga = leg["score"]["home"], leg["score"]["away"]
            for i, f, a in ((leg["homeidx"], gh, ga), (leg["awayidx"], ga, gh)):
                t[i][0] += 3 if f > a else 1 if f == a else 0
                t[i][1] += f - a; t[i][2] += f
    return sorted(t, key=lambda i: tuple(-v for v in t[i]))


def cup_refresh_stats(cup):
    """Per-team W/D/L over the whole cup + group points/rank on every team object (group
    tables, "Juara - <rank> (W/D/L)" next-match panel, "Grup C - Peringkat <rank>")."""
    wdl = {}
    for phase in cup["phases"]:
        for g in phase["groups"]:
            for rnd in g["rounds"]:
                for pair in rnd["pairs"]:
                    leg = pair["legs"][0]
                    if "score" not in leg:
                        continue
                    for i, f, a in ((leg["homeidx"], leg["score"]["home"], leg["score"]["away"]),
                                    (leg["awayidx"], leg["score"]["away"], leg["score"]["home"])):
                        r = wdl.setdefault(g["teams"][i]["teamid"], [0, 0, 0])
                        r[0 if f > a else 1 if f == a else 2] += 1
    for phase in cup["phases"]:
        for g in phase["groups"]:
            order = group_table(g) if phase["system"] == "round-robin" else range(len(g["teams"]))
            for rank, i in enumerate(order):
                t = g["teams"][i]; w, d, l = wdl.get(t["teamid"], [0, 0, 0])
                t.update(win=w, draw=d, lose=l, point=3 * w + d, rank=rank + 1)


def cup_play_round(st, cup, mine=None):
    """Play the current round (my fixture = `mine` (home, away) if given, the rest simulated),
    then advance current / build the knock-out / settle my state."""
    cur = cup["current"]; phase = cup["phases"][cur["phase"]]; ko = phase["system"] == "knock-out"
    rng = random.Random()
    for g in phase["groups"]:
        for pair in g["rounds"][cur["round"]]["pairs"]:
            leg = pair["legs"][cur["leg"]]
            if "score" in leg or leg.get("homeidx") is None:
                continue
            h, a = g["teams"][leg["homeidx"]], g["teams"][leg["awayidx"]]
            played = mine is not None and (h["coach"] or a["coach"])
            if played:
                gh, ga = mine[:2]
            else:
                gh, ga = sim_score(rng, team_strength(st, h["teamid"]), team_strength(st, a["teamid"]))
            leg["score"] = {"home": gh, "away": ga}
            if h["coach"] or a["coach"]:  # Info Manager's cup record
                record_match(st, "cup", *((gh, ga) if h["coach"] else (ga, gh)))
            if ko and gh == ga and played and len(mine) > 3 and mine[2] != mine[3]:  # shootout played in game
                leg["shootout"] = {"home": mine[2], "away": mine[3]}
            elif ko and gh == ga:  # penalties
                ph = rng.randint(3, 5); pa = ph + rng.choice((-1, 1)) if ph < 5 else ph - 1
                leg["shootout"] = {"home": ph, "away": pa}
    me_state = cup["coaches"][0]
    if not ko:
        if cur["round"] + 1 < len(phase["groups"][0]["rounds"]):
            cur["round"] += 1
            return
        # groups done: A1-B2, B1-A2, C1-D2, D1-C2
        tables = [group_table(g) for g in phase["groups"]]
        qual = []
        for x, y in ((0, 1), (2, 3)):
            qual += [phase["groups"][x]["teams"][tables[x][0]], phase["groups"][y]["teams"][tables[y][1]],
                     phase["groups"][y]["teams"][tables[y][0]], phase["groups"][x]["teams"][tables[x][1]]]
        cup["phases"].append({"system": "knock-out", "name": "Babak gugur", "maxrounds": 3, "groups": [{"teams": qual, "rounds": [
            {"shuffle": True, "pairs": [{"legs": [{"homeidx": i, "awayidx": i + 1}]} for i in range(0, 8, 2)]},
            {"pairs": [{"legs": [{}]} for _ in range(2)]}, {"pairs": [{"legs": [{}]}]}]}]})
        cup.update(current={"phase": cur["phase"] + 1, "round": 0, "leg": 0})
        if not any(t["coach"] for t in qual):
            me_state["state"] = "eliminated"
        return
    g = phase["groups"][0]
    winners = []
    for pair in g["rounds"][cur["round"]]["pairs"]:
        leg = pair["legs"][0]; s = leg.get("shootout") or leg["score"]
        winners.append(leg["homeidx"] if s["home"] > s["away"] else leg["awayidx"])
    my_idx = next(i for i, t in enumerate(g["teams"]) if t["coach"]) if any(t["coach"] for t in g["teams"]) else None
    if my_idx is not None and me_state["state"] == "playing" and my_idx not in winners and \
            any(my_idx in (p["legs"][0].get("homeidx"), p["legs"][0].get("awayidx")) for p in g["rounds"][cur["round"]]["pairs"]):
        me_state["state"] = "eliminated"
    if cur["round"] + 1 < len(g["rounds"]):
        for k, pair in enumerate(g["rounds"][cur["round"] + 1]["pairs"]):
            pair["legs"][0] = {"homeidx": winners[2 * k], "awayidx": winners[2 * k + 1]}
        cur["round"] += 1
    else:  # final played
        cup["finished"] = True
        if me_state["state"] == "playing":
            me_state["state"] = "won" if my_idx in winners else "eliminated"  # CupMainScreen._cupState


def cup_resources(cup):
    """The cup with its meta's trophy and skin (issue #36: CupMainScreen draws
    trophies/t<currentCup.trophy>.png, and cups saved without them asked for tundefined.png)."""
    uri = f"/cup/{cup['_id']}"
    meta = next((m for m in CUPS if m["metaid"] == cup.get("metaid")), {})
    return [{"resource": uri, "uri": uri, "body": {"trophy": meta.get("trophy"), "skin": meta.get("skin"), **cup}}]


def ep_cup_new(query, body):
    """POST /cup/new {metaid, difficulty, teamid, lineup}."""
    req = json.loads(body or b"{}")
    meta = next((m for m in CUPS if m["metaid"] == int(req.get("metaid") or 0)), None)
    if meta is None:
        return _json({"result": False, "reason": "invalid metaid"})
    st = _state()
    cup = new_cup(st, meta, int(req.get("difficulty") or 0))
    st.setdefault("cups", {})[cup["_id"]] = cup
    r = coach_record(my_coach(st), "cup")
    r["totalchallenges"] = r.get("totalchallenges", 0) + 1
    _save(st)
    return _json({"result": True, "resources": cup_resources(cup)})


def cup_step(cupid, fn):
    st = _state(); cup = (st.get("cups") or {}).get(cupid)
    if cup is None:
        return _json({"result": False, "reason": "not exist cupid"})
    out = fn(st, cup) or {}
    _save(st)
    return _json(dict({"result": True, "resources": cup_resources(cup)}, **out))


def ep_cup_resume(cupid, query, body):
    return cup_step(cupid, lambda st, cup: None)


def ep_cup_next(cupid, query, body):
    """PUT /cup/<id>/next: play out the current round (my result from the match just played,
    if any) and advance (CupController.onMatchEndReceived -> ON_CUP_SCHEDULE_NEXT_ROUND)."""
    def step(st, cup):
        if cup.get("finished"):
            return
        cup_play_round(st, cup, cup.pop("_mine", None))
        cup_refresh_stats(cup)
    return cup_step(cupid, step)


def cup_reward_ep(cup):
    """EP for an ended cup: the meta's prize for the champion, a share of it by the round
    reached otherwise (group exit 10%, QF 20%, SF 35%, final 50%); 0 while still playing."""
    state = cup["coaches"][0]["state"]
    meta = next((m for m in CUPS if m["metaid"] == cup["metaid"]), CUPS[0])
    prize = meta["bestrewards"][-1][0]["count"]
    if state == "won":
        return prize
    if state != "eliminated":
        return 0
    ko = cup["phases"][1]["groups"][0] if len(cup["phases"]) > 1 else None
    mine = next((i for i, t in enumerate(ko["teams"]) if t["coach"]), None) if ko else None
    if mine is None:
        return prize // 10
    played = sum(1 for r in ko["rounds"] for p in r["pairs"]
                 if mine in (p["legs"][0].get("homeidx"), p["legs"][0].get("awayidx")) and "score" in p["legs"][0])
    return prize * (20, 35, 50)[max(0, min(played, 3) - 1)] // 100


def ep_cup_leave(cupid, query, body):
    """PUT /cup/<id>/leave: give up, or collect an ended cup's reward. CupReward*Scene.onItemExpand
    reads body.rewards[]: itemid 1 = EP (count), anything else is an item card."""
    st = _state(); cup = (st.get("cups") or {}).pop(cupid, None)
    ep = cup_reward_ep(cup) if cup else 0
    add_ep(st, ep)
    if cup:  # Info Manager's cup record: given up, won (by AI level), prize money and the best
        r, state = coach_record(my_coach(st), "cup"), cup["coaches"][0]["state"]
        r["abandonments"] = r.get("abandonments", 0) + (state == "playing")
        if state == "won":
            r["totalwins"] = r.get("totalwins", 0) + 1
            wins = r.setdefault("cupwins", [0] * CUP_LEVELS)
            wins[min(CUP_LEVELS - 1, max(0, int(cup.get("difficulty", 0))))] += 1
        r["totalprize"] = r.get("totalprize", 0) + ep
        if ep > (r.get("best") or {}).get("prize", 0):
            r["best"] = {"prize": ep, "metaid": cup["metaid"], "date": w3c(time.time())}
    _save(st)
    return _json({"result": True, "rewards": [{"itemid": 1, "count": ep}] if ep else []})


def ep_cup_match(cupid, query, body):
    """POST /cup/<id>/match -> my fixture of the current round as a /match/<id> resource."""
    st = _state(); cup = (st.get("cups") or {}).get(cupid)
    g, leg = cup_my_pair(cup) if cup else (None, None)
    if leg is None or "score" in leg:
        return _json({"result": False, "reason": "already played match"})
    me = coach_me(st, _default_slot(st)); teamid = me["teamid"]
    home = g["teams"][leg["homeidx"]]["teamid"]; away = g["teams"][leg["awayidx"]]["teamid"]
    opp = away if home == teamid else home
    user = {"id": me["id"], "name": me["name"], "levelinfo": me["levelinfo"]}
    match = new_match("Cup_PvE", match_side(st["teams"][str(_default_slot(st))], teamid, user, _tactics(st)[1]),
                      match_side(roster_team(opp), opp))
    gs = match["gamesettings"]
    gs["home"] = home == teamid
    gs["kit"] = ["home", "away"] if gs["home"] else ["away", "home"]
    apply_prematch(st, match)
    gs["difficulty"] = cup.get("difficulty", 0)  # AI level 0..4
    if cup["phases"][cup["current"]["phase"]]["system"] == "knock-out":
        # native maps drawMode 1/2/3 to end rules 9/6/7 (0 = a draw stands). 2 (aggregate, prevScore
        # 0-0) plays extra time and then penalties; cup_play_round settles a level score it never got
        gs["drawMode"] = 2
    st["match"] = match
    st["cup_pending"] = {"id": match["id"], "cupid": cupid, "home": home, "away": away}
    _save(st)
    return _json({"result": True, "resources": [{"uri": f"/match/{match['id']}", "body": match}]})


def ep_cup_record(query, body):
    """GET /cup/record: CupProxy.onCupRecordResourceChanged takes resources[] entries whose
    `resource` is /cup/record and body._id is my coach id."""
    st = _state(); coach = my_coach(st)
    r = coach.get("record", {}).get("cup", {})
    return _json({"result": True, "resources": [{"resource": "/cup/record", "uri": "/cup/record",
                  "body": {"_id": coach["_id"], "recordsbymetaid": [],
                           **{k: r.get(k, 0) for k in ("totalchallenges", "totalwins", "totalprize")}}}]})


# --------------------------------------------------------------------------- #
# Market (Bursa transfer): an NPC market over the cards fifaaddict lists, at their Indonesia-
# server prices (card_price). Offer rows are {spid, expandme: "player", grade, price, count};
# the client fills staticdata (names, position, ratings) from its own DB via `expandme`.
# --------------------------------------------------------------------------- #
LINES = {"GK": range(0, 1), "DF": range(1, 9), "MF": range(9, 20), "FW": range(20, 28)}  # Enums.PLAYERPOSITION
MAX_PLAYERS = 200  # coach.limits.players (squad size; user: "max skuad naikan", was 60)
# Tim inti (player state "first": Formasi lists only these) can hold the whole club ("Tim inti
# x/200" in Skuad). Skuad's Tim column shows MAIN/SUB for the 18 of the squad, a green RES button
# for the rest of Tim inti and "-" for Tim Cadangan; the button moves a card between the two.
FIRST_TEAM_MAX = MAX_PLAYERS


def first_team_count(team):
    return sum(1 for p in team["players"] if not p.get("team2"))


def balance_first_team(team):
    """Tim inti over FIRST_TEAM_MAX (clubs from before the cap): the least valuable cards outside
    the 18 of the squad move to Tim Cadangan."""
    extra = first_team_count(team) - FIRST_TEAM_MAX
    spare = [p for p in team["players"] if not p.get("team2") and not in_squad(team, p)]
    for p in sorted(spare, key=lambda p: card_price(p["spid"], grade_of(p)))[:max(0, extra)]:
        p["team2"] = True


# The virtual managers trade on it (issue #30, stage 3; user: "manajer market virtual berjalan").
# Client shapes (TransferMarketProxy and the Transfer* screens): every element of a search / top
# list is its own row (several prices and grades of one card are fine), "Kuantitas" = count, and a
# purchase posts that row's spid / grade / price. Rows: the +1..+3 stock at the market price
# (MARKET_STOCK an hour, never sold out), plus the managers' own listings of the hour
# (VM_LIST_ROWS, grades VM_LIST_GRADES) a little under the market price (market_factor; never
# below 0.96 of it, so buying there and selling back at the market price gains nothing after the
# fee). What I buy comes off the hour's counts (MARKET_BOUGHT, kept in memory).
MARKET_STOCK = (3, 30)
VM_LIST_ROWS = (0, 0, 1, 1, 2, 3)  # one is drawn per card and hour
VM_LIST_GRADES = (1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 7, 8)
MARKET_BOUGHT = {}  # (spid, grade, price, hour key) -> cards bought from that row this hour


def nice_price(x):
    """EP rounded to 3 significant digits (46,300; 1,230,000)."""
    x = int(x)
    return round(x, 3 - len(str(x))) if x >= 1000 else x


def nice_floor(x):
    """EP rounded down to 3 significant digits (a cap nice_price could round over)."""
    x = int(x)
    k = 10 ** max(0, len(str(x)) - 3)
    return x // k * k


def nice_ceil(x):
    """EP rounded up to 3 significant digits (a price that must not fall short of x)."""
    x = math.ceil(x)
    k = 10 ** max(0, len(str(abs(x))) - 3)
    return -(-x // k) * k


def market_factor(spid, grade, day):
    """The virtual market's price of a card on crowd day `day`, as a share of its market value:
    a slow seeded wave between about 0.955 and 0.995 (the price graph, the managers' listings)."""
    r = random.Random(f"price/{spid}/{grade}")
    p1, p2, a, b = r.uniform(9, 23), r.uniform(3, 7), r.uniform(0, 2 * math.pi), r.uniform(0, 2 * math.pi)
    noise = random.Random(f"price/{spid}/{grade}/{day}").uniform(-0.003, 0.003)
    return 0.975 + 0.012 * math.sin(2 * math.pi * day / p1 + a) + 0.006 * math.sin(2 * math.pi * day / p2 + b) + noise


# The dynamic Market (ECONOMY "dynamic", the admin page's Ekonomi tab; the reporter of issue #30:
# "buy players at lower prices and resell them for a profit"): a player's price drifts day by day
# (market_swing, the same for everyone), the managers' listings go from bargains to a little over
# it (MARKET_BARGAIN), and a seller may ask up to MARKET_ASK_MAX of it, waiting longer for a buyer
# (vm_buyer). The fee (sale_net: 5% with the PC warnet and VIP discounts) takes that premium back,
# so a card bought from the stock, which never runs out, cannot be resold at a profit: only a
# bargain or a cheaper day can. Off, the Market stays as described above.
MARKET_SWING = 0.12
MARKET_SWING_MAX = 1 + MARKET_SWING + 0.01  # the most market_swing gives (both waves at their top, the noise too)
MARKET_BARGAIN = (0.86, 1.02)
MARKET_ASK_MAX = 1.05


def market_swing(spid, day):
    """A player's dynamic Market price on crowd day `day`, as a share of his cards' value: a slow
    seeded wave of about +-MARKET_SWING (every grade of him moves together)."""
    r = random.Random(f"swing/{spid}")
    p1, p2, a, b = r.uniform(12, 30), r.uniform(4, 9), r.uniform(0, 2 * math.pi), r.uniform(0, 2 * math.pi)
    noise = random.Random(f"swing/{spid}/{day}").uniform(-0.01, 0.01)
    return 1 + MARKET_SWING * (0.7 * math.sin(2 * math.pi * day / p1 + a) + 0.3 * math.sin(2 * math.pi * day / p2 + b)) + noise


def market_value(spid, grade, t=None):
    """A card's Market price now (or at t): its value (card_price), moved by market_swing when
    the dynamic Market is on."""
    p = card_price(spid, grade)
    if not ECONOMY["dynamic"] or not p:
        return p
    return nice_price(p * market_swing(spid, vm_day(time.time() if t is None else t)))


def sell_band(spid, grade):
    """(lower, upper, Market price) of what a listing of this card may ask now (Jual's band)."""
    v = market_value(spid, grade)
    return v * 8 // 10, nice_floor(v * MARKET_ASK_MAX) if ECONOMY["dynamic"] else v, v


def market_rows(spid, t=None):
    """The Market's rows of one card now: [{spid, expandme, grade, price, count}], the +1..+3
    stock first; [] if it is not tradeable."""
    if not on_market(spid):
        return []
    now = time.time() if t is None else t
    day, key = vm_day(now), f"{vm_day(now)}/{time.gmtime(now).tm_hour}"
    r = random.Random(f"market/{spid}/{key}")
    rows = {(g, market_value(spid, g, now)): r.randint(*MARKET_STOCK) for g in (1, 2, 3)}
    for _ in range(r.choice(VM_LIST_ROWS)):
        g = r.choice(VM_LIST_GRADES)
        p = card_price(spid, g)
        if ECONOMY["dynamic"]:  # from bargains to a little over the day's price
            price = nice_price(market_value(spid, g, now) * r.uniform(*MARKET_BARGAIN))
        else:
            price = min(p, max(nice_price(0.96 * p), nice_price(p * market_factor(spid, g, day) * r.uniform(0.99, 1.01))))
        rows[g, price] = rows.get((g, price), 0) + r.choice((1, 1, 1, 2, 3))
    out = []
    for (g, price), n in rows.items():
        n -= MARKET_BOUGHT.get((spid, g, price, key), 0)
        stock = g <= 3 and price == market_value(spid, g, now)
        if n > 0 or stock:
            out.append({"spid": spid, "expandme": "player", "grade": g, "price": price, "count": max(1, n)})
    return out


def market_buy(spid, grade, price, t=None):
    """The row I buy from: (price paid, row) or None when no such row is left this hour (a +1..+3
    at the market price always is)."""
    now = time.time() if t is None else t
    key = f"{vm_day(now)}/{time.gmtime(now).tm_hour}"
    row = next((r for r in market_rows(spid, now) if r["grade"] == grade and r["price"] == price), None)
    if row is None and on_market(spid) and grade in (1, 2, 3) and price == market_value(spid, grade, now):
        row = {"grade": grade, "price": price}  # the stock, seen in an older list (the client caches 30 s)
    if row is None:
        return None
    for k in [k for k in MARKET_BOUGHT if k[3] != key]:
        del MARKET_BOUGHT[k]  # past hours
    MARKET_BOUGHT[spid, grade, price, key] = MARKET_BOUGHT.get((spid, grade, price, key), 0) + 1
    return row["price"]


def ep_transfer_top(position, query, body):
    """GET /transfer/top/50[/position/<GK|DF|MF|FW>]: the 50 priciest tradeable cards (their +1
    row)."""
    rng = LINES.get(str(position or "").upper())
    where = f"AND p.preferredposition1 BETWEEN {rng.start} AND {rng.stop - 1}" if rng else ""
    with closing(_db()) as c:
        both = c.execute(f"""SELECT p.spid, {POSOVR}, p.overallrating FROM players p
                             WHERE 1 {where}""").fetchall()  # every Market card, licensed or not
    _OVR.update((s, (o, d)) for s, o, d in both)
    rows = [(s, o) for s, o, _ in both]
    spids = [s for s, ovr in sorted((r for r in rows if on_market(r[0])), key=lambda r: (-r[1], r[0]))[:50]]
    return _json([market_rows(s)[0] for s in spids])


def ep_transfer_search(spid, query, body):
    """GET /transfer/search/<spid>: that card's rows (market_rows); [] if not tradeable."""
    return _json(market_rows(int(spid)))


def add_player(team, spid, grade=1):
    """A new card instance in my club, in Tim Cadangan ("-" in Skuad, ready for Tukar Scout; user:
    "neymar baru di pack harusnya jangan masuk res"): packs, trades, the Market, Bag gifts and the
    admin page all add through here. Returns it."""
    with closing(_db()) as c:
        r = c.execute("SELECT playerid FROM players WHERE spid = ?", (int(spid),)).fetchone()
        name = gen_teams.jersey_name(c, r[0]) if r else ""
    used = {p.get("jersey") for p in team["players"]}
    p = {"_id": new_iid(), "spid": int(spid), "expandme": "player", "grade": int(grade),
         "jersey": next((n for n in range(12, 100) if n not in used), 99), "jerseyname": name, "team2": True}
    team["players"].append(p)
    return p


def remove_player(team, p):
    """Drop a card instance from my club and from the squad, captain and kickers."""
    team["players"].remove(p)
    sq, pid = team["squad"], iid(p)
    sq["positions"] = [s for s in sq["positions"] if str(s.get("id") or s["spid"]) != pid]
    sq["kickers"] = ["" if str(k) == pid else k for k in sq.get("kickers", [])]  # slots stay in place
    if str(sq.get("captain")) == pid:
        sq["captain"] = next((str(s.get("id") or s["spid"]) for s in sq["positions"] if s["pos"]), "")


def is_starter(team, p):
    return any(str(s.get("id") or s["spid"]) == iid(p) and s["pos"] < POS_SUB for s in team["squad"]["positions"])


def in_squad(team, p):
    """Starter or sub (the 18 on the team sheet); reserves are not."""
    return any(str(s.get("id") or s["spid"]) == iid(p) for s in team["squad"]["positions"])


def ep_transfer_purchase(spid, grade, price, query, body):
    """POST /transfer/purchase/<spid>/<grade>/<price>/purchase: buy from the row the path names
    (market_buy: its price) and add a new instance of the card to my players (reserves). A row
    gone since the list was fetched is NO_SUCH_OFFER ("Pemain tidak ada."; the client refreshes)."""
    spid, grade, price = int(spid), int(grade), int(price)
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]
    if len(team["players"]) >= MAX_PLAYERS:
        return _json({"result": False, "reason": "Exceed plyer limit"})
    if coach_me(st, slot)["money"]["ep"] < price:
        return _json({"result": False, "reason": "NOT_ENOUGH_EP"})
    cost = market_buy(spid, grade, price)
    if cost is None:
        return _json({"result": False, "reason": "NO_SUCH_OFFER"})
    add_ep(st, -cost)
    add_player(team, spid, grade)
    record_trade(my_coach(st), "buy", spid, cost)
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


# Selling: a listing waits ("waiting" = Dijual; the client knows no other word for it) until a
# virtual manager buys it, sooner the cheaper it is (VM_SALE: at 80% of the market price .. at
# 100%), then shows as "sold" (Terjual) in Daftar jual saya until its EP is collected (price
# minus SALE_FEE). The sale is pushed to the game: the list (the open tab redraws) and a
# notification (toast "<card> terjual seharga <price> EP." and an entry naming the buyer); both
# go out from market_loop, or from the next request that touches the offers. Batal takes a
# waiting card back, Ulang lists it again at a new price. Asking more than the market price (the
# dynamic Market: than MARKET_ASK_MAX of it) is refused, so buying back is never cheaper than selling.
SALE_FEE = 0.10
VM_SALE = (20, 300)
VM_SALE_SLOW = 48  # dynamic Market: asking MARKET_ASK_MAX waits 48 x 300 s = 4 h for a buyer
# listings a manager can hold (waiting + sold, not collected); more is the client's own
# TRANSFERMARKET_MAX_REGISTERED ("Silahkan hapus daftar penjualan"). Keeps the pushed list (about
# 270 bytes an offer) far under the 64 KB a messenger push can carry.
OFFERS_MAX = 100
OFFER_PRIVATE = ("card", "buyat", "buyer", "slot")  # the card, when and who buys it, whose club: not sent
# transfer-fee discounts (TransferMarketProxy.modifyOffersFee: floor(factor * completefee.base)):
# PC warnet (IGR) 20%, VIP 30% -- fifaaddict's FO3 tax calculator; both always active here
NO_BOOST = {"igr": 0.2, "vip": 0.3, "event": 0, "item": 0, "discountlimit": 0}


def sale_net(o):
    fee = o["completefee"]["base"]
    return o["price"] - fee + int(NO_BOOST["igr"] * fee) + int(NO_BOOST["vip"] * fee)


def slot_offers(st):
    """The default manager's offers: each manager lists, sells and collects for his own club (an
    offer from before slots were kept is his)."""
    slot = _default_slot(st)
    return [o for o in st.get("offers", []) if o.get("slot", slot) == slot]


def offer_list(st):
    """/transfer/my/offer/list (TransferMarketProxy.fetch*): offers[] {_id, status
    waiting|sold|expired, issuedat (W3C), completefee.base (fee in EP), spid, grade, price}."""
    return {"offerexpireoffset": 86400000, "boost": NO_BOOST,
            "offers": [{k: v for k, v in o.items() if k not in OFFER_PRIVATE} for o in slot_offers(st)]}


def offer_resources(st):
    return [{"uri": "/transfer/my/offer/list", "body": offer_list(st)}] + coach_me_resources(st, _default_slot(st))


def ep_transfer_price(spid, grade, query, body):
    """GET /transfer/price/<spid>/<grade>: the Jual tab's price band and default; lowest = the
    cheapest row of that grade on the Market now ("Harga terendah di bursa transfer adalah ..."),
    0 if none ("Tidak ada pemain yang dijual")."""
    g = int(grade)
    lower, upper, p = sell_band(int(spid), g)
    lowest = min((r["price"] for r in market_rows(int(spid)) if r["grade"] == g), default=0)
    return _json({"lower": lower, "upper": upper, "default": p, "lowest": lowest, "registrationFee": 0})


def vm_buyer(o, now):
    """Pick the manager who buys offer o and when (buyat, s): VM_SALE by its price against the
    Market's; one asking more (the dynamic Market) waits up to VM_SALE_SLOW times longer, at
    MARKET_ASK_MAX."""
    r = random.Random(f"{o['_id']}/{o['price']}")
    share = o["price"] / max(1, market_value(o["spid"], o["grade"], now))
    lo, hi = VM_SALE
    wait = (lo + (hi - lo) * min(1, max(0, (share - 0.8) / 0.2)) ** 2 if share <= 1
            else hi * VM_SALE_SLOW ** ((share - 1) / (MARKET_ASK_MAX - 1)))
    o["buyat"] = now + wait * r.uniform(0.6, 1.4)
    o["buyer"] = r.choice(virtual_managers())["_id"]


def settle_offers(st, now=None):
    """The waiting offers whose buyer has come are sold: [those offers]."""
    now = time.time() if now is None else now
    sold = [o for o in st.get("offers", []) if o["status"] == "waiting" and o.get("buyat", 0) <= now]
    for o in sold:
        o["status"], o["soldat"] = "sold", w3c(now)
        seller = next((c for c in st["coaches"] if c["slot"] == o.get("slot", _default_slot(st))), None)
        if seller:  # its manager's Info Manager: cards sold, the dearest
            record_trade(seller, "sell", o["spid"], o["price"], now)
    return sold


@functools.lru_cache(maxsize=4096)
def card_name(spid):
    """The card's short name (namesind), as the client shows it."""
    with closing(_db()) as c:
        r = c.execute("SELECT n.short FROM players p JOIN namesind n ON n.playerid = p.playerid WHERE p.spid = ?",
                      (int(spid),)).fetchone()
    return r[0] if r else str(spid)


NOTE_MS = [0]  # the last notification's time: AlarmWindow drops one not later than the last


def note(category, kind, details, now=None):
    """A system notification (SystemMessageProxy: /inbox/my/notifications[/latest]); dates
    strictly increase. details.title is the dropdown entry's text (a localization key or text)."""
    ms = NOTE_MS[0] = max(int((time.time() if now is None else now) * 1000), NOTE_MS[0] + 1)
    date = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms // 1000)) + f".{ms % 1000:03d}Z"
    return {"_id": new_iid(), "category": category, "type": kind, "date": date, "details": details}


def announce_sales(st, sold):
    """Tell the game about the playing manager's offers just sold: the new list and one
    notification each. stackable: SystemMessageProxy.appendNotification keeps any other category
    than bag / boxingday in the dropdown only with it (else just the toast shows)."""
    slot = _default_slot(st)
    sold = [o for o in sold if o.get("slot", slot) == slot]
    if not sold:
        return
    messenger_push("/transfer/my/offer/list", offer_list(st))
    for o in sold:  # price as text: the toast prints it as given (TRANSFERMARKET_LIVE_ALARM)
        vm = vm_by_id(o.get("buyer")) or {"name": "?"}
        messenger_push("/inbox/my/notifications/latest", {**note("transfermarket", "sold", {
            "spid": o["spid"], "expandme": "player", "grade": o["grade"], "price": f"{o['price']:,}",
            "title": f"{card_name(o['spid'])} terjual ke {vm['name']}"}), "stackable": True})


def my_offers(st):
    """The playing manager's offers after the sales due by now (every manager's are settled;
    state saved, his pushed to the game)."""
    sold = settle_offers(st)
    if sold:
        _save(st)
        announce_sales(st, sold)
    return slot_offers(st)


def market_loop():
    """The server's thread: sells the waiting offers when their buyer comes (every 5 s)."""
    while True:
        time.sleep(5)
        try:
            with STATE_LOCK:
                my_offers(_state())
        except Exception as e:  # a bad state file must not end the market
            print(f"market loop: {e!r}", flush=True)


def ep_offer_list(query, body):
    st = _state()
    my_offers(st)
    return _json(offer_list(st))


def ep_transfer_offer(query, body):
    """POST /transfer/offer {playerobjectid, price}: list one of my players (not a starter)."""
    req = json.loads(body or b"{}")
    st = _state(); team = st["teams"][str(_default_slot(st))]
    pid = str(req.get("playerobjectid"))
    p = next((p for p in team["players"] if iid(p) == pid), None)
    price = int(req.get("price") or 0)
    if p is None or is_starter(team, p):
        return _json({"result": False, "reason": "TRANSFERMARKET_SELECT_SELLABLE_PLAYER"})
    if not 0 < price <= sell_band(p["spid"], grade_of(p))[1]:
        return _json({"result": False, "reason": "TRANSFERMARKET_LOWERLIMIT"})
    if len(slot_offers(st)) >= OFFERS_MAX:
        return _json({"result": False, "reason": "TRANSFERMARKET_MAX_REGISTERED"})
    remove_player(team, p)
    now = time.time()
    o = {"_id": f"{int(now):08x}{os.urandom(8).hex()}", "status": "waiting", "issuedat": w3c(now),
         "spid": p["spid"], "expandme": "player", "grade": grade_of(p), "price": price,
         "playerobjectid": pid, "completefee": {"base": int(price * SALE_FEE)}, "card": p,
         "slot": _default_slot(st)}
    vm_buyer(o, now)
    st.setdefault("offers", []).append(o)
    _save(st)
    return _json({"result": True, "resources": offer_resources(st)})


def ep_offer_cancel(status, offerid, query, body):
    """DELETE /transfer/my/offer/<waiting|expired>/<id> (Batal): the card comes back (Tim
    Cadangan) unless the club is full ("Exceed plyer limit" = TRANSFERMARKET_FULLSLOT_DESC, as
    for a purchase). Bought meanwhile: OFFER_NOT_EXIST_ERROR (the client reloads the list)."""
    st = _state()
    o = next((o for o in my_offers(st) if o["_id"] == offerid and o["status"] in ("waiting", "expired")), None)
    if o is None:
        return _json({"result": False, "reason": "OFFER_NOT_EXIST_ERROR"})
    team = st["teams"][str(_default_slot(st))]
    if len(team["players"]) >= MAX_PLAYERS:
        return _json({"result": False, "reason": "Exceed plyer limit"})
    if o.get("card"):
        team["players"].append(o["card"])
    else:
        add_player(team, o["spid"], o["grade"])
    st["offers"].remove(o)
    _save(st)
    return _json({"result": True, "resources": offer_resources(st)})


def ep_offer_reoffer(offerid, query, body):
    """POST /transfer/offer/from/<id> {price} (Ulang): the waiting card at a new price."""
    req = json.loads(body or b"{}")
    st = _state()
    o = next((o for o in my_offers(st) if o["_id"] == offerid and o["status"] in ("waiting", "expired")), None)
    if o is None:
        return _json({"result": False, "reason": "OFFER_NOT_EXIST_ERROR"})
    price = int(req.get("price") or 0)
    if not 0 < price <= sell_band(o["spid"], o["grade"])[1]:
        return _json({"result": False, "reason": "TRANSFERMARKET_LOWERLIMIT"})
    now = time.time()
    o.update(status="waiting", issuedat=w3c(now), price=price, completefee={"base": int(price * SALE_FEE)})
    vm_buyer(o, now)
    _save(st)
    return _json({"result": True, "resources": offer_resources(st)})


def ep_transfer_detail(spid, grade, query, body):
    """GET /transfer/<spid>/<grade>/detail: the card window's Market price graph
    (PlayerDetailPopup, features.transfermarket.playerpricegraph): {history {basePrice, startat,
    endat, data [[time ms, price > 0]]}}, one point a day for PRICE_DAYS days: the dynamic Market's
    price of each day (market_value), else the managers' listings under the fixed price
    (market_factor)."""
    spid, grade = int(spid), max(1, int(grade))
    p, today = market_value(spid, grade), vm_day()
    days = range(today - PRICE_DAYS + 1, today + 1)
    at = lambda d: VM_EPOCH + d * 86400
    day_price = (lambda d: market_value(spid, grade, at(d) + 43200)) if ECONOMY["dynamic"] else \
        (lambda d: nice_price(p * market_factor(spid, grade, d)))
    data = [[at(d) * 1000, max(1, day_price(d))] for d in days]
    return _json({"history": {"basePrice": p, "startat": w3c(at(days[0])), "endat": w3c(at(today)), "data": data}})


PRICE_DAYS = 31  # the graph labels endat and every 5 days back to 30 days before it, startat first


def ep_offer_collect(offerid, coupon, query, body):
    """DELETE /transfer/my/offer/completed/<id|all>[/<coupon>]: collect sold offers' EP."""
    st = _state(); offers = my_offers(st)
    done = [o for o in offers if o["status"] == "sold" and offerid in ("all", o["_id"])]
    if not done:
        return _json({"result": False, "reason": "NO_SUCH_TRANSFERRED_OFFER_ERROR"})
    ep = sum(sale_net(o) for o in done)
    add_ep(st, ep)
    st["offers"] = [o for o in st["offers"] if o not in done]  # the other manager's stay
    _save(st)
    return _json({"result": True, "ep": ep, "resources": offer_resources(st)})


# --------------------------------------------------------------------------- #
# Tim > Manajemen pemain > Upgrade (enhancement v1, TeamManagementPlayerEnhancementOldTabPage
# + TeamManagementPlayerEnhancingPopupScreen): two cards of the same player, the lower-grade
# one (material) is consumed. Buy a second copy on the Market to have a pair.
# --------------------------------------------------------------------------- #
ENHANCE_RATE = (1.0, 1.0, 0.81, 0.64, 0.50, 0.26, 0.15, 0.07, 0.04, 0.02, 0)  # +g -> +(g+1) chance
ENHANCE_DROP_FROM = 5  # a failed upgrade from +5 or higher drops one grade


def enhance_pair(team, a, b):
    pa = next((p for p in team["players"] if iid(p) == str(a)), None)
    pb = next((p for p in team["players"] if iid(p) == str(b)), None)
    if pa is None or pb is None or pa is pb or pa["spid"] != pb["spid"]:
        return None, None
    if grade_of(pa) >= 10 and grade_of(pb) > 0:  # a +10 may take only a +0 (onListItemClick)
        return None, None
    return pa, pb


def enhance_grade(target, material):
    """Grade after a successful upgrade: a +0 material (a club's own card) adds none (issue #5)."""
    return grade_of(target) + (grade_of(material) > 0)


def enhance_rate(target, material):
    """Base chance by target grade times the bonus the Upgrade tab shows as "Rasio bonus
    keberhasilan" (TeamManagementPlayerEnhancementOldTabPage.updatePreviewText): material +1 or
    lower = Kosong, at least 2/3 of the target grade = High, at least 1/3 = Normal, else Low.
    The multipliers are ours (user: "Ya, samakan"); the original ones were server data.
    A +0 material keeps the grade, so it always succeeds."""
    tg, mg = grade_of(target), grade_of(material)
    if mg == 0:
        return 1.0
    bonus = 1.0 if mg <= 1 else 1.5 if mg >= tg * 2 / 3 else 1.25 if mg >= tg / 3 else 1.1
    return min(1.0, ENHANCE_RATE[tg] * bonus)


def enhance_cost(target, material, rate):
    """EP for one try: 2% of the target's value (at least 1,000). With the strict rules (Sulit) at
    least what the try adds to the card's Market value on average at its odds (rate, boosts
    included): else the +1..+3 stock, which never runs out, makes EP (a +3 tried with a cheap +1 at
    74% doubled its money; free boosts did more), as in the game itself."""
    g = grade_of(target)
    base = max(1000, sig3(card_price(target["spid"], g) // 50))
    if not strict_economy():
        return base
    v = lambda x: market_value(target["spid"], x)  # the material is the same player
    fail = g - 1 if g >= ENHANCE_DROP_FROM else g
    return max(base, nice_ceil(rate * v(enhance_grade(target, material)) + (1 - rate) * v(fail) - v(g) - v(grade_of(material))))


def merge_into(team, keep, gone):
    """The consumed card's squad slot (starter > sub) passes to the kept card of the same
    player, so an upgrade never leaves a hole in the XI; then the consumed card goes."""
    sq, k, g = team["squad"], iid(keep), iid(gone)
    slot = {str(s.get("id") or s["spid"]): s for s in sq["positions"]}
    if g in slot and (k not in slot or slot[g]["pos"] < POS_SUB <= slot[k]["pos"]):
        if k in slot:
            sq["positions"].remove(slot[k])
        slot[g]["id"] = k
        keep.pop("team2", None)  # the 18 of the squad stay in Tim inti (ep_player_move)
        if str(sq.get("captain")) == g:
            sq["captain"] = k
        sq["kickers"] = [k if str(x) == g else x for x in sq.get("kickers", [])]
    remove_player(team, gone)


def ep_enhance_preview(a, b, query, body):
    """GET /player/<target>/enhance/<material>/preview -> {grade, levelinfo, price, enhancepay}."""
    st = _state(); team = st["teams"][str(_default_slot(st))]
    pa, pb = enhance_pair(team, a, b)
    if pa is None:
        return _json({"result": False, "reason": "INVALID_PARAM"})
    g, rate = enhance_grade(pa, pb), enhance_rate(pa, pb)
    return _json({"result": True, "grade": g, "levelinfo": pa.get("levelinfo", {"level": 1, "exp": 0}),
                  "price": card_price(pa["spid"], g), "enhancepay": enhance_cost(pa, pb, rate),
                  "probability": round(rate * 100)})


def ep_enhance(a, b, boost, guard, query, body):
    """PUT /player/<target>/enhance/<material>[/<boostitem>/<guarditem>] -> {success, grade,
    rgrade, rprice, attribPlus} (TeamManagementPlayerEnhancingPopupScreen.onEnhanceComplete)."""
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]
    pa, pb = enhance_pair(team, a, b)
    if pa is None:
        return _json({"result": False, "reason": "INVALID_PARAM"})
    rate = enhance_rate(pa, pb)
    cost = enhance_cost(pa, pb, rate)
    if coach_me(st, slot)["money"]["ep"] < cost:
        return _json({"result": False, "reason": "Invalid target ep price"})
    add_ep(st, -cost)
    before = grade_of(pa)
    success = random.random() < rate
    after = enhance_grade(pa, pb) if success else before - 1 if before >= ENHANCE_DROP_FROM else before
    pa["grade"] = after
    merge_into(team, pa, pb)
    mission_event(st, upgrade=int(after > before))  # a grade up (a +0 material adds none)
    _save(st)
    return _json({"result": True, "success": success, "grade": after, "rgrade": after,
                  "rprice": card_price(pa["spid"], after), "attribPlus": GRADE_BOOST[after] - GRADE_BOOST[before],
                  "resources": coach_me_resources(st, slot) + achievement_resources(st)})


# --------------------------------------------------------------------------- #
# Upgrade 2.5 (features.teammanagement.playermanagement.enhancement.v2_5 ->
# TeamManagementPlayerEnhancement2_5TabPage: EnhanceControlPanel, EnhancementInfoPanel, popup
# version "v2_5"). Same pairs, grades and drop as v1, plus (user: "di upgrade belum ada tambahan
# item dan point upgrade"):
#   upgrade points (money.es): a failed upgrade grants enhance_es(target) points; "Gunakan poin"
#     spends that many for +ES_BOOST chance (PE2STATICVALUES bonus.use = ES_BOOST in 1/100 %);
#   boost items (coach items of type ENHANCEMENT_BOOST, sent by itemid): +their chance, one used;
#   a failure that dropped a grade shows "Coba pengembalian lagi": PUT /player/enhance2.5/restore
#     gives the grade back for usepoint.price points; when EP is scarce for EP, the Market value it
#     gives back (usepoint.currency "EP": the popup checks money.ep then; points are free in the Shop).
# The original numbers were server data; these are ours.
# --------------------------------------------------------------------------- #
ES_BOOST = 0.10
ES_RESTORE = 3  # restoring a dropped grade costs the points of this many failures
# GET /static/db/PE2STATICVALUES. The popup's constructor reads all three keys (a missing one
# threw #1010 there, so the popup came up half-built with dead buttons): bonus.use = the point
# boost in 1/100 %; simplify_step.use = cards cheaper than this restore without the button
# (isSimplify; 0 = never); effect_price {0..4} = EP thresholds, highest first, for the card
# glow effect6..effect2 (setPlayerCardBackEffect; below the last one = effect1).
PE2STATICVALUES = {"bonus": {"use": round(ES_BOOST * 10000)}, "simplify_step": {"use": 0},
                   "effect_price": {str(i): v for i, v in enumerate((10 ** 10, 10 ** 9, 10 ** 8, 10 ** 7, 10 ** 6))}}
# A never-ending Upgrade event (user: "aktifkan terus event upgrade"), GET /events for
# InGameEventProxy. InGameEventController keeps an event whose condition.type is "enhance" and
# that is active with no reason; condition.timeset dates are YYYYMMDD, times HHMM;
# targetuser_igr 255 = every user; enhance.season [] = every season. reward is a list of lists
# of {boosts} (getEnhanceGradeRange types each entry as Array: a flat list raised #1034 and
# stopped the preview); boost key BOOST_PLUS_ENHANCE without a grade suffix = targets +1..+9.
# The panel then sends its eventid with the upgrade.
EVENT_BOOST = 0.10
ENHANCE_EVENT = {
    "eventid": 1001, "active": True, "reason": [],
    "condition": {"type": "enhance", "enhance": {"season": []},
                  "timeset": {"startdate": "20140101", "enddate": "20351231", "starttime": "0000", "endtime": "2359"},
                  "targetuser": {"targetuser_igr": 255, "targetuser_vip": 255}},
    "reward": [[{"boosts": {"BOOST_PLUS_ENHANCE": round(EVENT_BOOST * 100)}}]],
    "messages": {"main": {"name": "Event Upgrade", "description": "Peluang sukses upgrade +10% untuk target +1 sampai +9."}},
}


def enhance_es(target):
    """Points a failed upgrade of this card grants, and what "Gunakan poin" costs on it."""
    return 10 * (grade_of(target) + 1)


def add_es(st, n):
    coach = my_coach(st)
    coach["es"] = coach.get("es", 0) + int(n)


def boost_stack(st, itemid):
    """The coach's stack of that boost item, or None (-1 = none chosen, or not owned)."""
    return next((i for i in my_coach(st).get("items", []) if str(i["itemid"]) == str(itemid) and i["remains"] > 0
                 and i["itemid"] in ITEMS and ITEMS[i["itemid"]][2] == "ENHANCEMENT_BOOST"), None)


def enhance25_rate(pa, pb, use_point, item, eventid):
    rate = enhance_rate(pa, pb)
    if grade_of(pb) > 0:  # a +0 material always succeeds: nothing to boost
        event = eventid == str(ENHANCE_EVENT["eventid"]) and 1 <= grade_of(pa) <= 9
        rate += ES_BOOST * use_point + (ITEMS[item["itemid"]][3]["boost"] if item else 0) + EVENT_BOOST * event
    return min(1.0, rate)


def ep_enhance25_preview(a, b, use_point, itemid, event, query, body):
    """GET /player/<target>/enhance2.5/<material>/preview/<usePoint 0|1>/<boost itemid|-1>/<eventid|-1>
    -> {grade, levelinfo, price, probability, enhancepay, needes} (EnhanceControlPanel.requestPreview)."""
    st = _state(); team = st["teams"][str(_default_slot(st))]
    pa, pb = enhance_pair(team, a, b)
    if pa is None:
        return _json({"result": False, "reason": "INVALID_PARAM"})
    g = enhance_grade(pa, pb)
    rate = enhance25_rate(pa, pb, use_point == "1", boost_stack(st, itemid), event)
    return _json({"result": True, "grade": g, "levelinfo": player_levelinfo(pa), "price": card_price(pa["spid"], g),
                  "probability": round(rate * 100), "enhancepay": enhance_cost(pa, pb, rate), "needes": enhance_es(pa)})


def enhance25_result(st, slot, p, success, before, grant, usepoint):
    """Popup v2_5 reply: on a failure it shows the card broken, then restores it to rgrade
    (grade after a restore); grantes = points earned, usepoint = what a restore costs."""
    g = grade_of(p)
    return _json({"result": True, "success": success, "grade": g, "rgrade": g, "rprice": card_price(p["spid"], g),
                  "attribPlus": GRADE_BOOST[g] - GRADE_BOOST[before], "grantes": grant,
                  "usepoint": usepoint, "getitems": [], "resources": coach_me_resources(st, slot) + achievement_resources(st)})


def ep_enhance25(a, b, use_point, itemid, event, query, body):
    """PUT /player/<target>/enhance2.5/<material>/<usePoint 0|1>/<boost itemid|-1>/<eventid|-1>."""
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]
    pa, pb = enhance_pair(team, a, b)
    if pa is None:
        return _json({"result": False, "reason": "INVALID_PARAM"})
    boostable = grade_of(pb) > 0
    use_point = use_point == "1" and boostable
    item = boost_stack(st, itemid) if boostable else None
    if boostable and itemid != "-1" and item is None:
        return _json({"result": False, "reason": "expired item"})  # popup: ENHANCEMENT_25_ERROR_ITEM01
    rate = enhance25_rate(pa, pb, use_point, item, event)
    cost, need = enhance_cost(pa, pb, rate), enhance_es(pa)
    if coach_me(st, slot)["money"]["ep"] < cost:
        return _json({"result": False, "reason": "Invalid target ep price"})
    if use_point and my_coach(st).get("es", 0) < need:
        return _json({"result": False, "reason": "NOT_ENOUGH_ES"})
    add_ep(st, -cost)
    add_es(st, -need if use_point else 0)
    if item:
        item["remains"] -= 1
        if not item["remains"]:
            my_coach(st)["items"].remove(item)
    before = grade_of(pa)
    success = random.random() < rate
    pa["grade"] = enhance_grade(pa, pb) if success else before - 1 if before >= ENHANCE_DROP_FROM else before
    merge_into(team, pa, pb)
    mission_event(st, upgrade=int(grade_of(pa) > before))
    grant = 0 if success else need
    add_es(st, grant)
    st.pop("enhance_restore", None)
    usepoint = {"currency": "ES", "price": ES_RESTORE * need}
    if grade_of(pa) < before:
        if strict_economy():  # the game's own: upgrade points
            usepoint = {"currency": "EP", "price": nice_ceil(market_value(pa["spid"], before) - market_value(pa["spid"], grade_of(pa)))}
        st["enhance_restore"] = {"id": iid(pa), "grade": before, **usepoint}
    _save(st)
    return enhance25_result(st, slot, pa, success, before, grant, usepoint)


def ep_enhance25_restore(query, body):
    """PUT /player/enhance2.5/restore ("Coba pengembalian lagi" after a failure that dropped a
    grade): pay the points, the card gets that grade back. The popup reads it as another failed
    result whose grade is the restored one."""
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]
    r = st.get("enhance_restore")
    p = next((x for x in team["players"] if r and iid(x) == r["id"]), None)
    if p is None:
        return _json({"result": False, "reason": "INVALID_PARAM"})
    ep = r.get("currency") == "EP"
    if (coach_me(st, slot)["money"]["ep"] if ep else my_coach(st).get("es", 0)) < r["price"]:
        return _json({"result": False, "reason": "NOT_ENOUGH_EP" if ep else "NOT_ENOUGH_ES"})
    (add_ep if ep else add_es)(st, -r["price"])
    before, p["grade"] = grade_of(p), r["grade"]
    del st["enhance_restore"]
    _save(st)
    return enhance25_result(st, slot, p, False, before, 0, {"currency": "EP" if ep else "ES", "price": r["price"]})


# --------------------------------------------------------------------------- #
# Tim > Manajemen pemain > Tukar (player trade v1, TeamManagementPlayerRecycleTabPage +
# TeamManagementPlayerRecyclePopupScreen). Give 2..10 reserves; clubs offer Market cards
# rated at least avg(card OVR) + playertrade_cardovrbonus[n] (the tab's "expected OVR"):
# playertrade_normal[n] offers plus one each for PC warnet and VIP (the popup has 5 slots).
# An offer is a card, a card plus EP, or EP only (no spid). Tables: static_db_STATICVALUES.json.
# --------------------------------------------------------------------------- #
_TRADE_POOL = {}


def static_values():
    with open(os.path.join(RESPONSES, "static_db_STATICVALUES.json"), encoding="utf-8") as f:
        return json.load(f)


def trade_grades(gsum):
    """PLAYERTRADE.playergrade[sum of the given cards' grades] -> {grade: weight}: +1 always,
    one more possible grade per 8 grade points traded (the tab shows its first..last key)."""
    return {str(g): 0.5 ** (g - 1) for g in range(1, min(10, 1 + int(gsum) // 8) + 1)}


def trade_pool():
    """{client rating: [(spid, teamid)]} of every Market card, any season (cached). It used to
    take only cards licensed for the ID region, which left out most old and special seasons
    (user: "trading munculkan semua playernya")."""
    if not _TRADE_POOL:
        with closing(_db()) as c:
            for s, t, o in c.execute(f"SELECT p.spid, p.teamid, {POSOVR} FROM players p"):
                if on_market(s):
                    _TRADE_POOL.setdefault(o, []).append((s, t))
    return _TRADE_POOL


def trade_props(give, rng):
    sv = static_values()
    lim, n = sv["playertrade_limit"], len(give)
    avg = sum(card_ovr(p["spid"]) for p in give) / n
    lo = int(min(max(avg + sv["playertrade_cardovrbonus"][str(n)], lim["CardMinValue"]), lim["CardMaxValue"]))
    total = sum(card_price(p["spid"], grade_of(p)) for p in give)
    weights = trade_grades(sum(grade_of(p) for p in give))
    pool = trade_pool()
    props = []
    for i, boost in enumerate([None] * int(sv["playertrade_normal"][str(n)]) + ["igr", "vip"]):
        ovr = lo
        while ovr < min(lo + 8, lim["CardMaxValue"]) and rng.random() < 0.55:  # sometimes better
            ovr += 1
        s, t = rng.choice(pool[min(pool, key=lambda r: (abs(r - ovr), -r))])
        if i == 1 and boost is None:  # a club buys the lot for EP
            props.append({"teamid": t, "ep": sig3(total * rng.uniform(0.9, 1.15))})
            continue
        g = int(rng.choices(list(weights), list(weights.values()))[0])
        offer = {"spid": s, "grade": g, "teamid": t, "price": card_price(s, g)}
        if i == 2 and boost is None:  # card plus a top-up
            offer["ep"] = sig3(total * rng.uniform(0.05, 0.2))
        if boost:
            offer["boost"] = boost  # always a PC warnet + VIP coach here
        props.append(offer)
    return props


def trade_proposal(body, make_props):
    """{players: [iid...]} -> {result, props}: Tukar v1 and v2 share the checks."""
    req = json.loads(body or b"{}")
    st = _state(); team = st["teams"][str(_default_slot(st))]
    ids = [str(i) for i in req.get("players") or []]
    give = [p for p in team["players"] if iid(p) in ids]
    if not 2 <= len(give) <= 10 or len(give) != len(set(ids)) or any(in_squad(team, p) for p in give):
        return _json({"result": False, "reason": "invalid playerid"})  # the tab lists reserves only
    props = make_props(give, random.Random())
    st["trade"] = {"players": ids, "props": props}
    _save(st)
    return _json({"result": True, "props": props})


def ep_trade_proposal(query, body):
    """POST /players/trade/proposal {players: [iid...]} -> {result, props}."""
    return trade_proposal(body, trade_props)


def ep_trade_contract(query, body):
    """POST /players/trade/contract {select: offer index}: the given players leave, the
    offer's card and/or EP arrive. With the trade3 feature on, the popup (shared with the
    Scout tab) posts /trade3/contract {slotnum, selectnum} instead, also for this tab."""
    req = json.loads(body or b"{}")
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]
    tr = st.get("trade") or {}
    props, ids = tr.get("props") or [], tr.get("players") or []
    sel = int(req.get("select", req.get("selectnum", -1)))
    give = [p for p in team["players"] if iid(p) in ids]
    if not 0 <= sel < len(props) or len(give) != len(ids):
        return _json({"result": False, "reason": "invalid playerid"})
    for p in give:
        remove_player(team, p)
    offer = props[sel]
    if "spid" in offer:
        add_player(team, offer["spid"], offer["grade"])
    add_ep(st, offer.get("ep", 0))
    st.pop("trade")
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


# --------------------------------------------------------------------------- #
# Tim > Manajemen pemain > Tukar #2 (trade3 "Scout": TeamManagementPlayerTrade3TabPage +
# ScouterController). Pick a scout, give 2..10 reserves: offers are Market cards worth about
# their total value (fifaaddict prices) times the scout's range. Scouts (coach/me `scouters`)
# differ in range and cooldown; meeting a scout's quest shifts the range up (when EP is scarce,
# up to the given cards' worth: trade_cap). Same popup and /trade3/contract as Tukar v1 (the
# shared popup runs in trade3 mode while the feature is on).
# --------------------------------------------------------------------------- #
NO_LIMIT = 9_000_000_000_000_000  # "unlimited": below 2^53, so exact as an AS3 Number
TRADE3_STATIC = {  # GET /trade3/staticvalues -> ScouterController.staticvalues
    # total EP given / reward cap; user: "buat tidak terbatas" (was 30 / 10 billion EP: the tab
    # warned "Hanya dapat memperoleh pemain maximum 10,000,000,000 EP")
    "playertrade3_limit": {"max_trade": NO_LIMIT, "max": NO_LIMIT},
    "vvip_playertrade3_limit": {"max_trade": NO_LIMIT, "max": NO_LIMIT},
    "playertrade3_normal": {str(n): 1 + (n >= 4) + (n >= 7) for n in range(2, 11)},  # offers by #players
    "playertrade3_effect_price": [10_000_000, 3_000_000, 1_000_000],  # popup premium effect tiers
}
LEGEND_SEASONS = (93, 30, 27, 71, 51, 61, 89, 48, 31, 33)  # WL UL EL ML TL VL CL MUL CEL
SCOUTS = (  # (key = scoutid, string id, cooldown s, offer value x total, quest); always PC + VIP here
    # no cooldowns (user: "buat scout tidak ada reset"; were 1 day / 1 hour / 1 hour)
    # At most 5: the list shows 5 rows and, once it scrolls, updateQuestAchievePossibility lights
    # getRendererAt(selected index) without the scroll offset (another scout's or none). Scout
    # Amatir ("default_A01", no quest, 0.8-1.1) left for the two below; Profesional covers it.
    ("default_B01", "STR_SCOUTER_KR_DEFAULT_B01", 0, (0.9, 1.3), {"require_value": 100000}),
    ("igr", "STR_SCOUTER_KR_IGR_A01", 0, (0.9, 1.4), {"require_grade": 2, "require_grade_count": 2}),
    ("vip", "STR_SCOUTER_KR_VIP_A01", 0, (1.0, 1.5), {"require_season": "88", "require_season_count": 2}),
    # the client's other two (user: "Ya, tambahkan"); ScouterSelectedItemRenderer picks the
    # portrait from the key (scouter_event.png / scouter_vvip.png are not in the client cache:
    # it shows its default), an id with "vvip" uses vvip_playertrade3_limit
    ("event", "STR_SCOUTER_KR_EVENT_A01", 0, (1.0, 1.5),
     {"require_season": ",".join(map(str, LEGEND_SEASONS)), "require_season_count": 2}),
    ("vvip", "STR_SCOUTER_KR_VVIP_A01", 0, (1.1, 1.6), {"require_value": 1_000_000_000}),
)
QUEST_BONUS = 0.2  # quest met: the offer value range moves up by this x total
_T3_POOL = []


def w3c(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


def coach_scouters(st, slot):
    """coach/me `scouters` (CoachProxy.tradeScouters): a scout on cooldown is locked and carries
    `cooltime` (W3C) for the countdown (ScouterController re-asks /trade3/info when it ends)."""
    used, now = next(c for c in st["coaches"] if c["slot"] == slot).get("scout_used", {}), time.time()
    out = []
    for key, sid, cool, _, quest in SCOUTS:
        until = used.get(key, 0) + cool
        out.append({"key": key, "scoutid": key, "scoutername": sid + "_NAME", "scouterdesc": sid + "_DESC",
                    "lock": until > now, **({"cooltime": w3c(until)} if until > now else {}),
                    **({"quest": quest} if quest else {})})
    return out


def quest_met(quest, give):
    """The tab's quest checks: total value, cards of grade >= G, cards of the listed seasons."""
    if not quest:
        return False
    if sum(card_price(p["spid"], grade_of(p)) for p in give) < quest.get("require_value", 0):
        return False
    if "require_grade" in quest and sum(grade_of(p) >= quest["require_grade"] for p in give) < quest["require_grade_count"]:
        return False
    seasons = {int(s) for s in str(quest.get("require_season", "")).split(",") if s}
    return not seasons or sum(int(p["spid"]) // 1000000 in seasons for p in give) >= quest["require_season_count"]


def trade3_pool():
    """[(price, spid, teamid, grade)] of Market cards at every grade +1..+10, cheapest first
    (cached). Grade +1 only capped an offer at the dearest +1 card (~20 billion EP), so a trade
    of ~71 billion got far less back (user: "pertukaran tidak sebanding dengan nilai yang diawal")."""
    if not _T3_POOL:
        rows = [r for o in trade_pool().values() for r in o]
        card_ovrs([s for s, _ in rows])  # one bulk query instead of one per unpriced card
        _T3_POOL.extend(sorted((card_price(s, g), s, t, g) for s, t in rows for g in range(1, 11)))
    return _T3_POOL


def trade_cap(give):
    """With the strict rules (Sulit), the most a Tukar v2 or Scout offer may be worth, (value,
    Market value): the given cards' own. Their bonuses and ranges gave back up to about twice, and
    the best of the offers could be asked again without end. None = no cap (the game's own)."""
    if not strict_economy():
        return None
    return (sum(card_price(p["spid"], grade_of(p)) for p in give), sum(market_value(p["spid"], grade_of(p)) for p in give))


def trade_fit(pool, j, cap, ep=0):
    """pool[j] (a trade pool row, cheapest first), or under a cap the dearest row up to it that
    fits with ep EP on top. ponytail: none fits (a lot of the club's own +0 cards, worth 0): the
    cheapest card still comes."""
    if cap is None:
        return pool[j]
    j = min(j, bisect.bisect_right(pool, (min(cap) - ep, math.inf)) - 1)
    while j > 0 and market_value(pool[j][1], pool[j][3]) + ep > cap[1]:
        j -= 1
    return pool[max(0, j)]


def trade3_props(give, scout, rng):
    """playertrade3_normal[n] cards worth total x the scout's range (capped at the reward max and
    trade_cap; the last of them adds an EP top-up) + PC warnet and VIP extras."""
    lo, hi = scout[3]
    if quest_met(scout[4], give):
        lo, hi = lo + QUEST_BONUS, hi + QUEST_BONUS
    total = sum(card_price(p["spid"], grade_of(p)) for p in give)
    pool, normal, cap = trade3_pool(), TRADE3_STATIC["playertrade3_normal"][str(len(give))], trade_cap(give)
    props = []
    for i, boost in enumerate([None] * normal + ["igr", "vip"]):
        target = min(TRADE3_STATIC["playertrade3_limit"]["max"], total * rng.uniform(lo, hi))
        k = bisect.bisect_left(pool, (target,))
        j = rng.randrange(max(0, k - 10), min(len(pool), k + 10))  # one of the 20 cards nearest in value
        ep = sig3(max(1000, total * rng.uniform(0.05, 0.15))) if i == normal - 1 and normal > 1 else 0
        price, s, t, gr = trade_fit(pool, j, cap, ep)
        if cap:  # the top-up shrinks to what the cap leaves
            ep = max(0, min(ep, cap[0] - price, cap[1] - market_value(s, gr)))
        offer = {"spid": s, "grade": gr, "teamid": t, "price": price}
        if ep:
            offer["ep"] = ep
        if boost:
            offer["boost"] = boost
        props.append(offer)
    return props


def ep_trade3_proposal(query, body):
    """POST /trade3/proposal {id: scout key, slotnum, playerids: [{id, spid, grade, price}]}
    -> {result, props} (tab.onResponseRecycle opens the popup; it posts /trade3/contract)."""
    req = json.loads(body or b"{}")
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]; coach = my_coach(st)
    scout = next((s for s in SCOUTS if s[0] == req.get("id")), None)
    sent = {str(p.get("id")): p for p in req.get("playerids") or []}
    give = [p for p in team["players"] if iid(p) in sent]
    if (scout is None or not 2 <= len(give) <= 10 or len(give) != len(sent)
            or any(in_squad(team, p) or not p.get("team2") for p in give)):  # Tim Cadangan only
        return _json({"result": False, "reason": "invalid playerid"})
    if any(int(sent[iid(p)].get("price") or 0) != card_price(p["spid"], grade_of(p)) for p in give):
        return _json({"result": False, "reason": "TRADE3_NOTICE_PLAYERPRICE03"})  # client shows a recheck notice
    used = coach.setdefault("scout_used", {})
    if used.get(scout[0], 0) + scout[2] > time.time():
        return _json({"result": False, "reason": "TRADE2_ERROR_TOO_MANY_TRY"})  # still cooling down
    if sum(card_price(p["spid"], grade_of(p)) for p in give) >= TRADE3_STATIC["playertrade3_limit"]["max_trade"]:
        return _json({"result": False, "reason": "invalid playerid"})  # the tab blocks this first
    props = trade3_props(give, scout, random.Random())
    if scout[2]:
        used[scout[0]] = time.time()  # a negotiation uses the scout, picked or not
    st["trade"] = {"players": [iid(p) for p in give], "props": props}
    _save(st)
    return _json({"result": True, "props": props, "resources": coach_me_resources(st, slot)})


# --------------------------------------------------------------------------- #
# Tim > Manajemen pemain > Tukar v2 (features.teammanagement.playermanagement.trade2; same
# TeamManagementPlayerRecycleTabPage and popup; user: "Ya, buat trade2"). The tab rates the
# chosen reserves by TP = sum(card price, x bonusplayergroup.bonus for a special card of at
# least that grade) x playertrade2_bonus[n] x IGR x VIP, then shows from GET /static/db/TRADE
# the level (tp: minimum TP -> possible offer grades, highest level = 15) and the number of
# offers, and from GET /players/trade2/info what another manager got "yesterday" at that TP
# (TRADE2_ESTIMATE "{0} merekrut {1} kemarin."). Special cards (/static/cache/
# TRADE_SPECIALPLAYERS, {spid: 1}) are the legend seasons' cards. Offers are cards worth about
# the TP, graded up to the level's maximum. No daily limit (usage maxcount 0) and no value cap,
# but when EP is scarce no offer is worth more than the given cards (trade_cap).
# The original numbers were server data; these are ours.
# --------------------------------------------------------------------------- #
TRADE2_LEVELS = (0, 10 ** 6, 3 * 10 ** 6, 10 ** 7, 3 * 10 ** 7, 10 ** 8, 3 * 10 ** 8, 10 ** 9, 2 * 10 ** 9,
                 4 * 10 ** 9, 8 * 10 ** 9, 15 * 10 ** 9, 30 * 10 ** 9, 60 * 10 ** 9, 120 * 10 ** 9)  # levels 1..15
TRADE2_TABLE = {
    "bonusplayergroup": {"bonus": 1.5, "grade": 1},
    "playertrade2_bonus": {**{str(n): round(1 + 0.02 * (n - 2), 2) for n in range(2, 11)}, "IGR": 1.05, "VIP": 1.05},
    "legendgroup": list(LEGEND_SEASONS),
    "playertrade2_normal": {str(n): 1 + (n >= 4) + (n >= 7) for n in range(2, 11)},  # + PC warnet and VIP offers
    "tp": {str(tp): {"value": list(range(1, 2 + round(9 * i / 14)))} for i, tp in enumerate(TRADE2_LEVELS)},
    "trade2_limit": {"max": NO_LIMIT, "max_trade": NO_LIMIT},
    "trade_effect_price": [10 ** 10, 10 ** 9, 10 ** 8],  # popup premium effect tiers
}
TRADE2_MANAGERS = ("Budi", "Andi", "Rizky", "Dimas", "Fajar", "Yoga", "Agus", "Hendra", "Bayu", "Eko")


@functools.lru_cache(maxsize=None)
def trade2_special():
    return {str(s): 1 for rows in trade_pool().values() for s, _ in rows if s // 1000000 in LEGEND_SEASONS}


def trade2_tp(give):
    w, b = TRADE2_TABLE["bonusplayergroup"], TRADE2_TABLE["playertrade2_bonus"]
    tp = sum(card_price(p["spid"], grade_of(p)) * (w["bonus"] if str(p["spid"]) in trade2_special()
                                                    and grade_of(p) >= w["grade"] else 1) for p in give)
    return tp * b.get(str(len(give)), 1) * b["IGR"] * b["VIP"]  # always a PC warnet + VIP coach here


def trade2_max_grade(tp):
    level = TRADE2_LEVELS[max(0, bisect.bisect_right(TRADE2_LEVELS, tp) - 1)]
    return TRADE2_TABLE["tp"][str(level)]["value"][-1]


@functools.lru_cache(maxsize=None)
def trade2_pool(max_grade):
    return [r for r in trade3_pool() if r[3] <= max_grade]


def trade2_pick(target, max_grade, rng, cap=None):
    """(price, spid, teamid, grade): a card of at most that grade worth within 25% of the
    target, else the nearest one (low values come in sparse steps, so "the 20 nearest in the
    list" could be worth twice the target); within cap (trade_fit)."""
    pool = trade2_pool(max_grade)
    lo, hi = bisect.bisect_left(pool, (target / 1.25,)), bisect.bisect_right(pool, (target * 1.25, math.inf))
    k = bisect.bisect_left(pool, (target,))
    j = rng.randrange(lo, hi) if lo < hi else rng.randrange(max(0, k - 1), min(len(pool), k + 1))
    return trade_fit(pool, j, cap)


def trade2_props(give, rng):
    tp, cap = trade2_tp(give), trade_cap(give)
    props = []
    for boost in [None] * TRADE2_TABLE["playertrade2_normal"][str(len(give))] + ["igr", "vip"]:
        price, s, t, g = trade2_pick(tp * rng.uniform(0.85, 1.15), trade2_max_grade(tp), rng, cap)
        props.append({"spid": s, "grade": g, "teamid": t, "price": price, **({"boost": boost} if boost else {})})
    return props


def ep_trade2_info(query, body):
    """GET /players/trade2/info {minimum TP: a card a manager got at that level "yesterday"}
    (the same for the whole day; expandme lets the client fill its staticdata)."""
    rng = random.Random(time.strftime("%Y%m%d"))
    out = {}
    for tp in TRADE2_LEVELS[1:]:
        price, s, _, g = trade2_pick(tp * 1.1, trade2_max_grade(tp), rng)
        out[str(tp)] = {"spid": s, "grade": g, "expandme": "player", "price": price, "coach": rng.choice(TRADE2_MANAGERS)}
    return _json(out)


def ep_player_move(pid, where, query, body):
    """POST /player/<_id>/move/firstteam|secondteam (Tim > Manajemen pemain > Skuad): Tim inti <->
    Tim Cadangan. Players in the 18 of the squad stay in Tim inti."""
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]
    p = next((x for x in team["players"] if iid(x) == pid), None)
    if p is None or (where == "secondteam" and in_squad(team, p)):
        return _json({"result": False, "reason": "TRANSFERMARKET_SELL_ERROR_FIRSTTEAM"})
    if where == "firstteam" and p.get("team2") and first_team_count(team) >= FIRST_TEAM_MAX:
        return _json({"result": False, "reason": "FIRSTTEAM_REGISTRATION_FAIL_POPUP_TITLE"})  # Tim inti full
    if where == "secondteam":
        p["team2"] = True
    else:
        p.pop("team2", None)
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


def ep_trade3_info(query, body):
    """POST /trade3/info {}: the client only reads the pushed resources (scouters)."""
    st = _state()
    return _json({"result": True, "resources": coach_me_resources(st, _default_slot(st))})


# --------------------------------------------------------------------------- #
# Locker room (Tim > Locker room, issue #13) dresses a Tim inti player and changes shirt numbers.
# ItemController fills its panels from GET /static/db/LOCKERROOMITEM {lockerroomid: item}: Atas
# takes type TOP, Bawah PANTS, Boots SHOES, each only with visible true; LockerRoomController
# localizes itemname and splits color at ","; each stepper is sorted by order. With no table every
# stepper was empty and the boots panel threw (ItemPanelShoes.onStepperBrandChanged). Steppers:
#   TOP: TATTOO (tattocode), UNIFORMKIND (jerseystylecode; option FIT sets jerseyfit),
#        UNIFORMARM (jerseysleevlengthcode; option WINTERGEAR), WRIST, GLOVE
#   PANTS: SOCKS (socklengthcode), KNEE
#   SHOES: shoecode; option = brand, the brand stepper's LOCKERROOM_SHOES_BRAND_<option>
# WRIST, KNEE and GLOVE option PLAYERGLOVE are accessory meshes (0 = none): id [asset],
# [asset + 1] or both for left, right or both, colour = the LOCKERROOM_COLOR_<c> picked from
# `color`, minus 1. GLOVE option GKGLOVE (keepers only): mesh [asset, asset + 1], texture color[0].
# Only the client's own meshes are used (its default look: wrist 67, glove 6, knee 1, keeper
# glove 18 with the LOCKERROOM_GLOVEGK_<gkglovetypecode> textures); the ids of the other wrist,
# glove and knee kinds are not in the client.
# A look is saved as the card's lr {lockerroomid: {id, pos?, clr?}} (PUT /lockerroom/<_id>/itemset
# on player or panel change and on exit), which getPlayerAssetsFromLockerRoomItems turns into the
# 3D player in matches, only while the coach holds a running ticket: the first item with itemid
# 801 (LockerRoomController._lockerRoomItemID), createat set and expireat (ms) in the future. The
# shirt number needs the ticket too (LOCKERROOM_TICKET_BACKNUMBER_OVER).
# --------------------------------------------------------------------------- #
LR_TICKET = 801  # the client's own icon externalAssets/shop/801.png (a shirt and boots)
LR_TICKET_ITEM = {"_id": "lockerroomticket", "id": "lockerroomticket", "itemid": LR_TICKET, "remains": 1, "count": 1,
                  "expireable": True, "expired": False, "createat": 1483228800000, "expireat": VIP_UNTIL,
                  "staticdata": {"itemid": LR_TICKET, "productid": LR_TICKET, "name": "Tiket locker room",
                                 "category": "LOCKERROOM", "type": "LOCKERROOM", "unit": "COUNT",
                                 "itemicon": LR_TICKET}}
LR_COLORS = ",".join(map(str, range(1, 11)))  # LOCKERROOM_COLOR_1..10
LR_GK_GLOVES = (*range(1, 12), *range(91, 119))  # LOCKERROOM_GLOVEGK_<gkglovetypecode>
LR_SHOES = {  # LOCKERROOM_SHOES_<shoecode> by brand (LOCKERROOM_SHOES_BRAND_<brand>; the rest ETC)
    "ADIDAS": ('12 21-36 71-72 74-78 80-81 83-84 87 89 91-93 126-150 198-205 207-211 14085-14086 '
               '14088 14090 14126-14129 14131-14158 14216 14500-14503 15021-15023 15026 15029 '
               '15031-15032 15034-15035 15129-15160 15205-15213 15215-15227 16131 16147-16148 '
               '17130-17142 17144-17158 18019-18025 18027-18034 18038-18044 18099 18108-18109 '
               '18115 18123 18143 18209-18227'),
    "NIKE": ('38-45 117 161 224 226 228-232 14101 14168-14181 15045-15047 15049-15062 15168-15181 '
             '15230-15237 17173-17174 17176 17178-17180 17183 17185 18045-18048 18050-18058 18063 '
             '18080 18181 18235-18243 18253 18343'),
    "PUMA": ('47-51 109 170-178 14107 14185-14187 15063 15065 15184-15187 15238-15244 17189-17195 '
             '18065 18067-18069 18232-18234'),
    "MIZUNO": ('60-63 95-96 154-160 220 14162 14164 14167 15039-15042 15162 15164 15167 '
               '15203-15204 15255 17167-17170 18093-18094 18096-18097 18244-18245'),
    "UMBRO": ('52-57 70 182-183 185-186 188 212-217 14188-14193 15067-15070 15072-15073 '
              '15188-15193 15245-15251 17196-17204 18071-18073 18077-18079 18250-18252'),
    "UNDER ARMOUR": ('58-59 189-194 14194-14196 15075-15076 15078-15079 15081 15194-15196 '
                     '15252-15254 17205-17208 18083-18086'),
    "NEW BALANCE": ('15043-15044 17171-17172 18087-18090 18246-18249'),
    "ASICS": ('17159-17161 18105-18106'),
    "HUMMEL": ('15228-15229 18037'),
    "JOMA": ('17163-17166 18036 18111-18112 18128'),
    "LOTTO": ('37 73 152 14161 15161 17162 18018'),
    "PIRMA": ('17186-17188 18016-18017'),
    "ETC": ('0 15-20 46 64-69 153 196-197 218-219 14182-14183 14197-14202 15015-15020 15182-15183 '
            '15197-15202'),
}


def _spans(text):
    """'1-3 7' -> 1, 2, 3, 7."""
    for s in text.split():
        a, _, b = s.partition("-")
        yield from range(int(a), int(b or a) + 1)


def _lr_item(kind, category, name, asset, order, **extra):
    return {"type": kind, "visible": True, "category": category, "itemname": "LOCKERROOM_" + name,
            "asset": asset, "order": order, **extra}


@functools.lru_cache(maxsize=None)
def lockerroom_items():
    """GET /static/db/LOCKERROOMITEM. Ids stay fixed (saved looks refer to them): the rows below
    1-17, keeper gloves 1000 + texture, boots 100000 + shoecode."""
    rows = (("TOP", "TATTOO", "TOP_TATTOOSHOW", 0, {}), ("TOP", "TATTOO", "TOP_TATTOOHIDDEN", -1, {}),
            ("TOP", "UNIFORMKIND", "TOP_OUTSTYLE", 0, {}), ("TOP", "UNIFORMKIND", "TOP_INSTYLE", 1, {}),
            ("TOP", "UNIFORMKIND", "TOP_TECHSTYLE", 1, {"option": "FIT"}),
            ("TOP", "UNIFORMARM", "TOP_UNISHORT", 0, {}), ("TOP", "UNIFORMARM", "TOP_UNILONG", 1, {}),
            ("TOP", "UNIFORMARM", "TOP_UNIWINTERGEAR", 1, {"option": "WINTERGEAR"}),
            ("TOP", "WRIST", "TOP_WRISTHIDDEN", 0, {}), ("TOP", "WRIST", "TOP_WRISTSHORT", 67, {"color": LR_COLORS}),
            ("TOP", "GLOVE", "TOP_GLOVEHIDDEN", 0, {"option": "PLAYERGLOVE"}),
            ("TOP", "GLOVE", "TOP_GLOVEFIELD", 6, {"option": "PLAYERGLOVE", "color": LR_COLORS}),
            ("PANTS", "SOCKS", "PANTS_SOCKSNORMAL", 0, {}), ("PANTS", "SOCKS", "PANTS_SOCKSSHORT", 1, {}),
            ("PANTS", "SOCKS", "PANTS_SOCKSLONG", 2, {}),
            ("PANTS", "KNEE", "PANTS_KNEEHIDDEN", 0, {}), ("PANTS", "KNEE", "PANTS_KNEEPAD", 1, {"color": LR_COLORS}))
    items = {str(i): _lr_item(*row[:4], i, **row[4]) for i, row in enumerate(rows, 1)}
    for i, n in enumerate(LR_GK_GLOVES):
        items[str(1000 + n)] = _lr_item("TOP", "GLOVE", f"GLOVEGK_{n}", 18, i, option="GKGLOVE", color=str(n))
    for b, (brand, codes) in enumerate(LR_SHOES.items()):
        for k, code in enumerate(_spans(codes)):
            items[str(100_000 + code)] = _lr_item("SHOES", "SHOES", f"SHOES_{code}", code, b * 1000 + k, option=brand)
    return items


def _my_card(pid):
    st = _state(); slot = _default_slot(st)
    return st, slot, next((p for p in st["teams"][str(slot)]["players"] if iid(p) == pid), None)


def ep_lockerroom_itemset(pid, query, body):
    """PUT /lockerroom/<_id>/itemset {itemset: {lockerroomid: {id, pos?, clr?}}} ({} = Reset): the
    card's look. The reply's /coach/me gives the client the new lr, which it reads back when the
    player is picked again."""
    st, slot, p = _my_card(pid)
    if p is None:
        return _json({"result": False})
    items = lockerroom_items()
    itemset = json.loads(body or b"{}").get("itemset") or {}
    lr = {k: v for k, v in itemset.items() if k in items and isinstance(v, dict)}
    if lr:
        p["lr"] = lr
    else:
        p.pop("lr", None)
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


def ep_lockerroom_jersey(pid, query, body):
    """PUT /lockerroom/<_id>/jersey {desired, giverplayerid}: shirt number 1-99; the player who
    wears it gets this player's old number."""
    st, slot, p = _my_card(pid)
    n = int(json.loads(body or b"{}").get("desired") or 0)
    if p is None or not 1 <= n <= 99:
        return _json({"result": False})
    giver = next((x for x in st["teams"][str(slot)]["players"] if x is not p and x.get("jersey") == n), None)
    if giver is not None:
        giver["jersey"] = p.get("jersey", 0)
    p["jersey"] = n
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


# --------------------------------------------------------------------------- #
# Shop > Pemain sells player packs for EP; Item saya > Pemain opens them one card at a time
# (TeamManagementPlayerCardScreen: POST /item/<_id>/use -> {player}). Icons are the real FO3 ID
# shop images in the client's CDN cache (externalAssets/shop/<itemid>.png). A pack draws a
# Market card from its pool, cheaper cards more likely (weight price^-0.75: ~0.5% for a card
# worth 10x the pool median); its price is the expected card value at fifaaddict prices.
# --------------------------------------------------------------------------- #
POSITIONS = {"GK": (0, 0), "DF": (1, 8), "MF": (9, 19), "FW": (20, 27)}  # preferredposition1
PACKS = {  # itemid (= shop icon): (name, pool)
    28001: ("Premier League 65+", {"league": 13, "ovr": 65}),
    28002: ("Bundesliga 65+", {"league": 19, "ovr": 65}),
    28003: ("LaLiga 65+", {"league": 53, "ovr": 65}),
    28004: ("Serie A 65+", {"league": 31, "ovr": 65}),
    28005: ("Ligue 1 65+", {"league": 16, "ovr": 65}),
    28006: ("FW 60+", {"pos": "FW", "ovr": 60}),
    28007: ("MF 60+", {"pos": "MF", "ovr": 60}),
    28008: ("DF 60+", {"pos": "DF", "ovr": 60}),
    28009: ("GK 60+", {"pos": "GK", "ovr": 60}),
    8223600: ("FW 65+", {"pos": "FW", "ovr": 65}),
    8223601: ("MF 65+", {"pos": "MF", "ovr": 65}),
    8223602: ("DF 65+", {"pos": "DF", "ovr": 65}),
    8223603: ("GK 65+", {"pos": "GK", "ovr": 65}),
    8223508: ("FW NEW 70+ 2017", {"pos": "FW", "ovr": 70, "season": 88}),
    8223509: ("MF NEW 70+ 2017", {"pos": "MF", "ovr": 70, "season": 88}),
    8223510: ("DF NEW 70+ 2017", {"pos": "DF", "ovr": 70, "season": 88}),
    82214068: ("World Best 300 2017", {"top": 300, "season": 88}),
    82214180: ("World Best 50 2017", {"top": 50, "season": 88}),
    # This Season = the current season: 26 LIVE once tools/fo4_season.py installed it, else
    # 2017 (spid season 88 = the client's DEFAULT_SEASON_ID, badge '17)
    45011: ("This Season Top 10", {"top": 10, "season": gen_teams.CURRENT_SEASON}),
    45012: ("This Season Top 20", {"top": 20, "season": gen_teams.CURRENT_SEASON}),
    45013: ("This Season Top 30", {"top": 30, "season": gen_teams.CURRENT_SEASON}),  # (Top 50 = World Best 50 2017)
    45097: ("This Season Top 30 +8", {"top": 30, "season": gen_teams.CURRENT_SEASON, "grade": 8}),
    # older seasons; fifaaddict season codes: 2006..2011 = 6..11, 2014 = 14, 2015 = 15
    45004: ("06-11 Player Top 10", {"top": 10, "seasons": (6, 7, 8, 9, 10, 11)}),
    45006: ("06-11 Player Top 30", {"top": 30, "seasons": (6, 7, 8, 9, 10, 11)}),
    45007: ("06-11 Player Top 50", {"top": 50, "seasons": (6, 7, 8, 9, 10, 11)}),
    82214356: ("All '07", {"season": 7}),
    739: ("'08 Top 3", {"top": 3, "season": 8}),
    15031: ("'14 Top 30 +5", {"top": 30, "season": 14, "grade": 5}),
    16001: ("'15 Top 30 +5", {"top": 30, "season": 15, "grade": 5}),
    # special seasons: wc2010 = 47, ucl2010 = 69, wc2014 = 77, tots2014 = 53, 16ec = 35, lp = 23,
    # wb (World Best) = 91, wl (World Legend) = 93
    7622: ("World Cup '10 Top 30", {"top": 30, "season": 47}),
    7618: ("Europe '10 Top 30 +3", {"top": 30, "season": 69, "grade": 3}),
    880047: ("World Cup '14 Top 50", {"top": 50, "season": 77}),
    45020: ("Special Season '14 Top 30", {"top": 30, "season": 77}),
    45063: ("Special Season '14 Top 50 +5", {"top": 50, "season": 77, "grade": 5}),
    45026: ("Team of the Season '14 Top 20", {"top": 20, "season": 53}),
    45027: ("Team of the Season '14 Top 30", {"top": 30, "season": 53}),
    45028: ("Team of the Season '14 Top 50", {"top": 50, "season": 53}),
    45130: ("Team of the Season '14 All", {"season": 53}),
    8157: ("EC Top 10", {"top": 10, "season": 35}),
    82232124: ("EC Top 30 +3", {"top": 30, "season": 35, "grade": 3}),
    8135: ("Best EC 30 +5", {"top": 30, "season": 35, "grade": 5}),
    8329: ("LP Top 50 +3", {"top": 50, "season": 23, "grade": 3}),
    8334: ("World Best 5", {"top": 5, "season": 91}),
    8336: ("World Best 1", {"top": 1, "season": 91}),
    82231307: ("World Best +5", {"season": 91, "grade": 5}),
    8194: ("World Legend Top 30", {"top": 30, "season": 93}),
    8335: ("World Legend Top 3", {"top": 3, "season": 93}),
    # every season
    82214007: ("Top 300", {"top": 300}),
    8223562: ("Best 30", {"top": 30}),
    # user: "pack baru khusus all session grade 1-8", then "all session pack  top 50 ganti menjadi top 200",
    # then "all session top 200 pack ganti ke all session top 300" and "(OVR 81– sampai tertinggi)":
    # every Market card rated 81 and up (353 cards; a top-300 cut kept only 43 of the 96 81s)
    7675: ("All Season Top 300 +8", {"ovr": 81 + GRADE_BOOST[1], "grade": 8, "all": True}),
}
# "All <season>" like All '07 for every season fifaaddict lists (user: "yang keluar cuma 07,
# saya mau semua session"): (fifaaddict label, card season codes, closest shop icon). Codes
# checked against fifaaddict's own listings (e.g. tco92 = 49, mufclegend = 55, cel = 31 + 33).
FA_SEASONS = (
    ("06", (6,), 45004), ("06U", (63,), 7618), ("06WC", (65,), 7622), ("08", (8,), 739), ("08E", (67,), 8157),
    ("09", (9,), 45006), ("10", (10,), 45007), ("10U", (69,), 7618), ("10WC", (47,), 7622), ("11", (11,), 45004),
    ("14", (14,), 15031), ("14WC", (77,), 880047), ("14T", (53,), 45026), ("15", (15,), 16001), ("16", (16,), 16001),
    ("17", (88,), 45011), ("16EC", (35,), 8157), ("16TH", (45,), 8545), ("WB", (91,), 8334), ("WL", (93,), 8194),
    ("UB", (28,), 8336), ("UL", (30,), 8335), ("EL", (27,), 8194), ("LP", (23,), 8329), ("CP", (22,), 8549),
    ("CC", (24,), 8547), ("17TH", (44,), 8545), ("TCO92", (49,), 8546), ("MUA", (55,), 8547), ("ML", (71,), 8549),
    ("TL", (51,), 8549), ("VL", (61,), 8549), ("CL", (89,), 8549), ("KL02", (95,), 8546), ("CEL", (31, 33), 8335),
    ("U23", (29,), 8545), ("KXI", (97,), 8546), ("MUL", (48,), 8547), ("VS", (73,), 2000006),
    # FIFA Online 4's 26 LIVE / 25 LIVE and the 2018 World Cup's best 300 squad players (tools/fo4_season.py;
    # empty until it is installed)
    ("26 LIVE", (300,), 45011), ("25 LIVE", (325,), 45013), ("WC18", (78,), 880047),
)
# the admin page's own seasons (issue #38: gamedata/seasons.json, tools/fo4_season.py install adds
# their badges): a Top 200 pack and a season color each, from the next start
CUSTOM_SEASONS = os.path.join(HERE, "gamedata", "seasons.json")
CUSTOM_SEASON_CODES = ()
try:
    with open(CUSTOM_SEASONS, encoding="utf-8") as _f:
        _own = sorted(json.load(_f).items(), key=lambda kv: int(kv[0]))
    FA_SEASONS += tuple((s["name"], (int(code),), 45011) for code, s in _own)
    CUSTOM_SEASON_CODES = tuple(int(code) for code, _ in _own)
except (OSError, ValueError, KeyError, AttributeError):
    pass
# the seasons this server added to FO3's own (tools/fo4_season.py: 26 LIVE 300, 25 LIVE 325, WC18 78;
# the admin page's own): in the classic world (gen_teams.world) the virtual managers leave them out
ADDED_SEASONS = frozenset((300, 325, 78, *CUSTOM_SEASON_CODES))
# each one the season's 200 best cards (user: "all session pack  top 50 ganti menjadi top 200")
PACKS.update({92000000 + codes[0]: (f"{label} Top 200", {"seasons": codes, "icon": icon, "top": 200, "all": True})
              for label, codes, icon in FA_SEASONS})
PACKS[82214356] = ("07 Top 200", {"season": 7, "top": 200, "all": True})  # the original All '07 item
# every Market card of every season, so each one is in some pack (the season packs keep only
# their 200 best; '17 alone has 15,000+ cards); the "ALL" icon of All Season Top 300 +8
ALL_PLAYERS_PACK = 92000000
PACKS[ALL_PLAYERS_PACK] = ("Semua Pemain", {"all": True, "icon": 7675})
# Draft packs (issue #27, "Pilih pak pemain", category DRAFTPACK): Item saya > Pemain > Gunakan
# opens TeamManagementDraftPackScreen for one season (staticdata.draft {season, grade, availables}).
# GET /item/draftpack/<itemid>/players/<n> = the pack's n most valuable cards: the first call
# (n = availables) fills the pick list, the second (n = 100) is the search list; a searched card
# outside those is priced by POST /transfer/price/default/<grade>. "Konfirmasi" opens the card
# screen, which posts /item/<_id>/use {id, spids}: one of the picked cards, at the pack's grade.
# Icons 99000xx are the server's own (assets/, tools/draft_icons.py: season tag + picks).
PACKS.update({
    8223735: ("06 Draft Pick 3", {"season": 6, "all": True, "draft": 3, "icon": 9900006}),
    200005: ("2017 Draft Pick 5", {"season": 88, "all": True, "draft": 5, "icon": 9900088}),
    97093: ("World Legend Draft Pick 3", {"season": 93, "all": True, "draft": 3, "icon": 9900093}),
})
# the manager level rewards' packs FO3 had and the Shop has not (LEVEL_ITEMS): not sold in the Shop
REWARD_PACKS = {
    98000001: ("Top 100", {"top": 100, "icon": 82214007}), 98000002: ("Best 50", {"top": 50, "icon": 45007}),
    98000003: ("Best 20", {"top": 20, "icon": 45012}), 98000004: ("Best 10", {"top": 10, "icon": 45011}),
    98000005: ("Best 7", {"top": 7, "icon": 8334}), 98000006: ("DF 70+", {"pos": "DF", "ovr": 70, "icon": 8223510}),
    98000007: ("MF 70+", {"pos": "MF", "ovr": 70, "icon": 8223509}), 98000008: ("FW 70+", {"pos": "FW", "ovr": 70, "icon": 8223508}),
    98000009: ("Best 30 +5", {"top": 30, "grade": 5, "icon": 45097}), 98000010: ("Best 20 +5", {"top": 20, "grade": 5, "icon": 45012}),
    98000011: ("Best 7 +5", {"top": 7, "grade": 5, "icon": 8334}), 98000012: ("'09 Best 10", {"top": 10, "season": 9, "icon": 45006}),
    98000013: ("'07 Best 10", {"top": 10, "season": 7, "icon": 82214356}), 98000014: ("'06 Best 10", {"top": 10, "season": 6, "icon": 45004}),
}
PACKS.update(REWARD_PACKS)
PACK_BUNCHES = (1, 10)  # buy 1 or 10 (10 packs at a fixed price 5% off: product_price)
_PACK_POOL = {}


def pack_pool(itemid):
    """[(spid, weight)] of the Market cards a pack can give (cached)."""
    if itemid not in _PACK_POOL:
        f = PACKS[itemid][1]
        lo, hi = POSITIONS.get(f.get("pos"), (0, 27))
        seasons = f.get("seasons") or ((f["season"],) if "season" in f else None)
        # "65+" = the rating the card shows once opened (POSOVR base + its grade's boost, as on fifaaddict)
        # "all" packs take every Market card; the others only cards licensed for the ID region
        # (permid), which leaves e.g. 37 of the 395 '06 cards
        with closing(_db()) as c:
            rows = c.execute(f"""SELECT p.spid, {POSOVR} AS ovr FROM players p LEFT JOIN permid m ON m.spid = p.spid
                                 WHERE ovr >= ?1 AND p.preferredposition1 BETWEEN ?2 AND ?3
                                 AND (?4 IS NULL OR p.leagueid = ?4) AND (?5 OR m.licensed = 1)""",
                             (f.get("ovr", 0) - GRADE_BOOST[1], lo, hi, f.get("league"), bool(f.get("all")))).fetchall()
        rows = sorted(((s, o) for s, o in rows if on_market(s) and (not seasons or s // 1000000 in seasons)),
                      key=lambda r: -r[1])[:f.get("top")]
        # a price far below the pool's (odd fifaaddict data, e.g. a 120k World Best card among 20M+
        # ones) would win almost every draw: weigh anything under a tenth of the median as that tenth
        card_ovrs([s for s, _ in rows])  # one batch: card by card took 12 s for Semua Pemain's 44,703
        prices = [card_price(s) for s, _ in rows]
        floor = sorted(prices)[len(prices) // 2] / 10 if prices else 0  # no card: an empty pool
        _PACK_POOL[itemid] = [(s, max(p, floor) ** -0.75) for (s, _), p in zip(rows, prices)]
    return _PACK_POOL[itemid]


def pack_grades(itemid):
    """[(grade, weight)]: a "+N" pack's card comes at a random grade +1..+N, lower grades more
    likely (weight N-g+1: for +5 that is 33/27/20/13/7%); other packs give +1. A draft pack's
    cards come at its grade (the draft screen shows their prices at it)."""
    f = PACKS[itemid][1]
    n = f.get("grade", 1)
    return [(n, 1)] if f.get("draft") else [(g, n - g + 1) for g in range(1, n + 1)]


def draft_top(itemid, n):
    """A draft pack's n most valuable cards (the default picks and the search list)."""
    return sorted((s for s, _ in pack_pool(itemid)), key=lambda s: (-card_price(s), s))[:n]


def pack_grade(itemid):
    grades = pack_grades(itemid)
    return random.choices([g for g, _ in grades], [w for _, w in grades])[0]


@functools.lru_cache(maxsize=None)
def pack_ev(itemid):
    """The expected value of the card a pack gives, unrounded (cached; set_economy and a changed
    pack drop it)."""
    draft = PACKS[itemid][1].get("draft")
    if draft:  # the default picks (the n best cards) at the pack's grade: it gives one of the n picked
        g = pack_grades(itemid)[0][0]
        top = draft_top(itemid, draft)
        return sum(card_price(s, g) for s in top) / len(top) if top else 0
    pool, grades = pack_pool(itemid), pack_grades(itemid)
    if not pool:
        return 0
    gw = sum(w for _, w in grades)
    return sum(card_price(s, g) * w * gwt for s, w in pool for g, gwt in grades) / sum(w for _, w in pool) / gw


def pack_price(itemid):
    return sig3(pack_ev(itemid) * ECONOMY["packs"])  # the admin page's pack price setting on top of the cards' worth


def pack_worth(itemid):
    """The most a pack fetches on the Market, unrounded (a paid Shop's price): its cards' worth
    times the pack setting, with the dynamic Market at their dearest swing: a pack is kept until
    opened, so at any lower price it made EP on its players' dear days (a one-player pack up to 11%,
    91 of the 92 packs on some day)."""
    return pack_ev(itemid) * ECONOMY["packs"] * (MARKET_SWING_MAX if ECONOMY["dynamic"] else 1)


def warm_packs():
    """Build every pack pool and price and the trade pools once (~20 s) so the first Shop or
    Tukar request does not stall the client. Under STATE_LOCK, like a request: an economy save
    (set_economy drops these caches) cannot land between a price and its caching."""
    for itemid in list(PACKS):
        with STATE_LOCK:
            if itemid in PACKS:
                pack_price(itemid)
    with STATE_LOCK:
        trade3_pool()


SHOP_FREE = True  # user: "shop jadi 0 semua" -> every Shop product costs 0 EP (admin page: shop.json "free")


def item_def(itemid):
    """Item staticdata (POST /static/cache/item, shop products, coach items)."""
    if itemid == EP_ITEM:
        return EP_ITEM_DEF
    if itemid == ES_ITEM:
        return ES_ITEM_DEF
    if itemid in ITEMS:
        return use_item_def(itemid)
    if itemid in PACKAGES or itemid in MONTHLY:
        return package_def(itemid)
    name, f = PACKS[itemid]
    d = {"itemid": itemid, "productid": itemid, "name": name, "category": "PLAYERPACK",
         "type": "LEAGUE" if "league" in f else "POSITION" if "pos" in f else "CUSTOM",
         "unit": "COUNT", "itemicon": f.get("icon", itemid), "currency": "ep",
         **({"bundleicon": f["icon"]} if "icon" in f else {}),  # several packs share one icon
         "bunches": [{"bunch": n, "price": product_price(itemid, n)} for n in PACK_BUNCHES],
         **({"draw": {"year": 2017}} if f.get("season") == 88 else {})}
    if f.get("draft"):  # one season: the draft screen's name search filters on it
        d.update(category="DRAFTPACK", type="DRAFTPACK", draft={
            "season": (f.get("seasons") or (f.get("season"),))[0], "grade": f.get("grade", 1), "availables": f["draft"]})
    return d


# --------------------------------------------------------------------------- #
# Shop items besides player packs (icons = the real FO3 ID shop images in the client cache).
# Category USE (Shop > Dapat dikonsumsi; Item saya > Dapat dikonsumsi): "Gunakan" opens the
# item's screen (GeneralInventoryTileItemRenderer.useUseItem by staticdata.type), which posts
# /item/<_id>/use (ItemUtil.postItemUse):
#   EP_LOTTERY (TeamManagementEPCardScreen) {id} -> {result, ep}
#   ITEM_LOTTERY (TeamManagementRandomCardScreen) {id} -> {result, ep} | {result, item:{itemid, count}}
#   COACHNAME_CHANGE {name} -> {result} (the name check first posts /coach/reservename {name})
#   TEAM_CHANGE (nation/league/club steppers, /static/info/team/summary/<id>) {teamid} -> {result}
# PACKAGE (Shop > Paket) bundles drop their items into the inventory on purchase; MILEAGE
# (MP Shop) sells copies of some items. All free while SHOP_FREE.
# --------------------------------------------------------------------------- #
EP_ITEM = 1  # externalAssets/shop/1.png = EP coin: EP in Bag entries and package contents
EP_ITEM_DEF = {"itemid": EP_ITEM, "productid": EP_ITEM, "name": "EP", "category": "EP", "type": "EP",
               "unit": "EP", "itemicon": EP_ITEM}
ES_ITEM = 2  # upgrade points (money.es) in package contents, credited like EP; icon 8700001 = "P"
ES_ITEM_DEF = {"itemid": ES_ITEM, "productid": ES_ITEM, "name": "Upgrade point", "category": "USE", "type": "ES",
               "unit": "COUNT", "itemicon": 8700001}
ITEMS = {  # itemid: (name, icon, type, effect, description)
    501: ("Lucky EP", 501, "EP_LOTTERY", {"ep": (10_000, 1_000_000)}, "Buka kartu: dapat 10.000 - 1.000.000 EP."),
    50029: ("Lucky EP max 20.000.000", "ID50029", "EP_LOTTERY", {"ep": (100_000, 20_000_000)},
            "Buka kartu: dapat 100.000 - 20.000.000 EP."),
    50028: ("Lucky EP max 50.000.000", "ID50028", "EP_LOTTERY", {"ep": (200_000, 50_000_000)},
            "Buka kartu: dapat 200.000 - 50.000.000 EP."),
    908: ("Jutawan EP Lottery", 908, "EP_LOTTERY", {"ep": (1_000_000, 10_000_000)}, "Undian EP: 1 - 10 juta EP."),
    50019: ("EP +200.000", "ID50019", "EP_LOTTERY", {"ep": (200_000, 200_000)}, "Langsung dapat 200.000 EP."),
    50020: ("EP +500.000", "ID50020", "EP_LOTTERY", {"ep": (500_000, 500_000)}, "Langsung dapat 500.000 EP."),
    50021: ("EP +1.000.000", "ID50021", "EP_LOTTERY", {"ep": (1_000_000, 1_000_000)}, "Langsung dapat 1.000.000 EP."),
    50840: ("Kantong EP", "ID50840", "EP_LOTTERY", {"ep": (500_000, 5_000_000)}, "Isi 500.000 - 5.000.000 EP."),
    50841: ("Peti EP", "ID50841", "EP_LOTTERY", {"ep": (5_000_000, 30_000_000)}, "Isi 5 - 30 juta EP."),
    119082304: ("Mesin Jackpot EP", 119082304, "EP_LOTTERY", {"ep": (100_000, 100_000_000)},
                "Jackpot: 100.000 - 100.000.000 EP."),
    # random boxes: one prize from the table, (weight, EP amount | pack itemid)
    50121: ("Kotak Platinum", "ID50121GP", "ITEM_LOTTERY",
            {"prizes": [(10, 8334), (5, 8194), (25, 82214007), (30, 45011), (30, ("ep", 10_000_000))]},
            "Hadiah acak: World Best 5, World Legend Top 30, Top 300, This Season Top 10 atau 10 juta EP."),
    50122: ("Kotak Emas", "ID50122GP", "ITEM_LOTTERY",
            {"prizes": [(30, 82214180), (40, 45012), (30, ("ep", 3_000_000))]},
            "Hadiah acak: World Best 50 2017, This Season Top 20 atau 3 juta EP."),
    50123: ("Kotak Perak", "ID50123GP", "ITEM_LOTTERY",
            {"prizes": [(25, 8223600), (25, 8223601), (25, 8223602), (25, ("ep", 1_000_000))]},
            "Hadiah acak: pak FW/MF/DF 65+ atau 1 juta EP."),
    50124: ("Kotak Perunggu", "ID50124GP", "ITEM_LOTTERY",
            {"prizes": [(25, 28006), (25, 28007), (25, 28008), (25, ("ep", 300_000))]},
            "Hadiah acak: pak FW/MF/DF 60+ atau 300.000 EP."),
    50125: ("Kotak Metal", "ID50125GP", "ITEM_LOTTERY",
            {"prizes": [(50, 82214356), (50, ("ep", 100_000))]}, "Hadiah acak: pak 07 Top 200 atau 100.000 EP."),
    200130000: ("Kotak Hadiah Perak", 200130000, "ITEM_LOTTERY",
                {"prizes": [(40, ("ep", 200_000)), (30, 28009), (30, 82214068)]},
                "Hadiah acak: 200.000 EP, pak GK 60+ atau World Best 300 2017."),
    200130001: ("Kotak Hadiah Emas", 200130001, "ITEM_LOTTERY",
                {"prizes": [(40, ("ep", 1_000_000)), (30, 45007), (30, 45013)]},
                "Hadiah acak: 1 juta EP, 06-11 Player Top 50 atau This Season Top 30."),
    3002: ("Ganti nama manager", 3002, "COACHNAME_CHANGE", {}, "Ganti nama manager Anda."),
    3001: ("Ganti tim", 3001, "TEAM_CHANGE", {}, "Pindah ke klub lain (logo, seragam, liga); pemain tetap."),
    # Player Growth's Reset button (any item of this type; icon drawn for this server)
    3003: ("Reset poin taktik", 9900200, "PLAYER_GROWTH_RESET", {},
           "Perkembangan pemain: kembalikan semua poin taktik yang dipakai satu pemain."),
    # Upgrade 2.5 boost items (EnhanceControlPanel lists type ENHANCEMENT_BOOST; names from the
    # client's ENHANCEMENT_BOOST<id>; the cache has no icons of their own, 1000002 = green arrows)
    1000052: ("Enhancement Boost Gold", 1000002, "ENHANCEMENT_BOOST", {"boost": 0.15}, "Upgrade: peluang sukses +15%."),
    1000053: ("Enhancement Boost Silver", 1000002, "ENHANCEMENT_BOOST", {"boost": 0.10}, "Upgrade: peluang sukses +10%."),
    1000054: ("Enhancement Boost Bronze", 1000002, "ENHANCEMENT_BOOST", {"boost": 0.05}, "Upgrade: peluang sukses +5%."),
    # League Simulation coupon (user: "item untuk League Simulation, jadi tanpa main league langsung
    # hasil"): Liga's Simulasi button (LeagueCupFrontScreenCalendarTabPage) counts the Item saya
    # items of type LEAGUE_SIMULATION and stays off at 0 (a click then opens the Shop on its
    # SIMULATION_ITEM_ID "2001"); each simulated match uses one up (ep_l2_match)
    2001: ("Simulasi Liga", 2001, "LEAGUE_SIMULATION", {}, "Liga: hasil satu pertandingan langsung, tanpa main."),
    # EXP capsules (issue #27, "Level capsule"; TeamManagementExpCapsuleScreen): "Gunakan" lists the
    # players it fits (below the max level, at most level lv, of the season) and posts
    # /item/<_id>/use {targetid}; that player gets the EXP. LV.1 -> LV.23 takes 25.300 EXP.
    6001: ("EXP Capsule 1.000", "ID50071", "PLAYER_EXP_CAPSULE", {"exp": 1000}, "EXP pemain +1.000."),
    6002: ("EXP Capsule 5.000", "ID50071", "PLAYER_EXP_CAPSULE", {"exp": 5000}, "EXP pemain +5.000."),
    6003: ("EXP Capsule Max", "ID50071", "PLAYER_EXP_CAPSULE", {"exp": 25_300}, "Pemain langsung ke level max (LV.23)."),
    6004: ("EXP Capsule LV.10", "ID50071", "PLAYER_EXP_CAPSULE", {"exp": 3000, "lv": 10},
           "EXP pemain +3.000, untuk pemain LV.10 ke bawah."),
    6005: ("EXP Capsule 2017", "ID50071", "PLAYER_EXP_CAPSULE", {"exp": 10_000, "season": 88},
           "EXP pemain +10.000, untuk pemain musim 2017."),
}
SIM_COUPON = 2001
PACKAGES = {  # itemid: (name, icon, [(itemid, count)], description); bought = contents to Item saya
    801: ("Paket Starter", 801, [(28006, 1), (28007, 1), (28008, 1), (28009, 1), (50020, 2)],
          "Pak FW/MF/DF/GK 60+ dan 2x EP +500.000."),
    50880: ("Kotak Terima Kasih", "ID50880", [(50021, 1), (50122, 1), (501, 3)],
            "EP +1.000.000, Kotak Emas dan 3x Lucky EP."),
    50881: ("Kotak World Best", "ID50881", [(8334, 1), (82214180, 3), (82214068, 5)],
            "World Best 5, 3x World Best 50 2017, 5x World Best 300 2017."),
    50882: ("Kotak Legenda 06-11", "ID50882", [(45004, 1), (45006, 2), (45007, 3)],
            "06-11 Player Top 10, 2x Top 30, 3x Top 50."),
    118012902: ("Kotak Upgrade", 118012902, [(15031, 1), (7618, 1), (82232124, 1)],
                "'14 Top 30 +5, Europe '10 Top 30 +3, EC Top 30 +3."),
    80000001: ("VIP Premium", "vipterbaru", [(50028, 1), (50121, 1), (50021, 5)],
               "Lucky EP max 50 juta, Kotak Platinum, 5x EP +1.000.000."),
    # upgrade points (user: "point upgrade beli dimana?"): Upgrade's "Gunakan poin" / restore
    81000100: ("Upgrade Point 100", 8700001, [(ES_ITEM, 100)], "100 upgrade point untuk Upgrade."),
    81000500: ("Upgrade Point 500", 8700001, [(ES_ITEM, 500)], "500 upgrade point untuk Upgrade."),
    81001000: ("Upgrade Point 1.000", 8700001, [(ES_ITEM, 1000)], "1.000 upgrade point untuk Upgrade."),
}
PACKAGES.update({  # Serial product steps (CHAINED below), bought in order
    70000001: ("Paket Berantai 1/3", 200130000, [(501, 3), (28007, 1)], "Tahap 1: 3x Lucky EP dan pak MF 60+."),
    70000002: ("Paket Berantai 2/3", 200130001, [(50122, 1), (50021, 1)], "Tahap 2: Kotak Emas dan EP +1.000.000."),
    70000003: ("Paket Berantai 3/3", "ID50121GP", [(50121, 1), (8334, 1)], "Tahap 3: Kotak Platinum dan World Best 5."),
})
MP_SHOP = (501, 50123, 50122, 45013, 45006, 3002)  # MP Shop (shopcategory MILEAGE) copies
MP_SHOPID = 900_000_000  # MP Shop entries: shopid = MP_SHOPID + itemid (productid stays itemid)
# Serial product (ShopChainedPackageTabPage, ShopController.currentChainedItemCheck): one entry
# {shopcategory CHAINED, step, chained: [later steps]}; the client shows step 0 = the entry
# itself, step k = chained[k-1]; remainedperaccount 0 marks a bought step ("complete").
CHAINED = {70000001: (70000001, 70000002, 70000003)}  # first step's itemid: all steps
# Special Bonus (ShopSpecialTabPage): GET /specifictarget {issell, targets:[{stid, endtime}]} and
# GET /shop/list/STSHOP = [entries with those stids] (ShopController keeps the targeted ones).
SPECIAL = (8335, 8194, 50841, 50881)
STSHOPID = 910_000_000
# Paket Bulanan (ShopMonthlyPackageTabPage: shopcategory MONTHLYPACK; ShopMonthlyPackageProxy):
# items now, then dailyitems every day for daily_period days; GET /monthlypack = my packages
# [{productid, product, expireat, rewardedat}], POST /monthlypack (the proxy, once a day while
# active) drops the day's items into the Bag as type "monthlypack".
MONTHLY = {  # itemid: (name, icon, days, items now, daily items, description)
    81000001: ("Paket Bulanan EP", "ID50840", 30, [(50021, 1)], [(EP_ITEM, 200_000)],
               "Langsung EP +1.000.000, lalu 200.000 EP tiap hari selama 30 hari."),
    81000002: ("Paket Bulanan Pak", 200130001, 30, [(8223601, 1)], [(28007, 1), (EP_ITEM, 50_000)],
               "Langsung pak MF 65+, lalu pak MF 60+ dan 50.000 EP tiap hari selama 30 hari."),
    81000003: ("Paket Bulanan Premium", "ID50841", 30, [(50121, 1)], [(45013, 1), (EP_ITEM, 500_000)],
               "Langsung Kotak Platinum, lalu This Season Top 30 dan 500.000 EP tiap hari selama 30 hari."),
}


def use_item_def(itemid, prizes=True):
    name, icon, typ, eff, desc = ITEMS[itemid]
    return {"itemid": itemid, "productid": itemid, "name": name, "category": "USE", "type": typ, "unit": "COUNT",
            "itemicon": icon, "bundleicon": icon, "currency": "ep", "stackable": True, "description": desc,
            # TeamManagementEPCardScreen.onQuery sizes the coin counter from count.max
            **({"count": {"min": eff["ep"][0], "max": eff["ep"][1]}} if "ep" in eff else {}),
            # an EXP capsule's EXP and limits (GeneralInventoryTileItemRenderer.useUseItem)
            **{"capsule" + k: eff[k] for k in ("exp", "lv", "season") if eff.get(k)},
            # a random box's prizes: TeamManagementRandomCardScreen.SetItemBackground hands
            # staticdata.availables to its panel as item tiles (GeneralInventoryTileItemRenderer);
            # none crashed every box at "Gunakan" (issue #12). A box among the prizes shows no prizes
            # of its own (no loop).
            **({"availables": [prize_item(p) for _, p in eff["prizes"]]} if prizes and "prizes" in eff else {}),
            "bunches": [{"bunch": n, "price": product_price(itemid, n)} for n in PACK_BUNCHES]}


def prize_item(p):
    """A prize ("ep", amount) | itemid as an item entry {itemid, count, staticdata}."""
    if isinstance(p, tuple):
        return {"itemid": EP_ITEM, "count": p[1], "staticdata": EP_ITEM_DEF}
    return {"itemid": p, "count": 1, "staticdata": use_item_def(p, False) if p in ITEMS else item_def(p)}


def package_def(itemid):
    if itemid in MONTHLY:
        name, icon, days, now, daily, desc = MONTHLY[itemid]
        contents, extra = now, {"daily_period": days, "monthly_platform": "all",
                                "dailyitems": [{"itemid": i, "count": n, "staticdata": item_def(i)} for i, n in daily]}
    else:
        name, icon, contents, desc = PACKAGES[itemid]
        extra = {}
    return {"itemid": itemid, "productid": itemid, "name": name, "category": "PACKAGE", "type": "ITEM",
            "unit": "COUNT", "itemicon": icon, "bundleicon": icon, "currency": "ep", "description": desc,
            "bunches": [{"bunch": 1, "price": product_price(itemid, 1)}], **extra,
            "items": [{"itemid": i, "count": n, "staticdata": item_def(i)} for i, n in contents]}


SHOP_NEW = (45011, 45004, 50121, 50881, 501)  # Rekomendasi: "new" order
SHOP_BEST = (82214180, 8223600, 28001, 28003, 8223601)  # Rekomendasi: Terlaris rank


def shop_entry(itemid, category="PLAYERPACK", shopid=None):
    d = item_def(itemid)
    rank = {k: t.index(itemid) + 1 for k, t in (("new", SHOP_NEW), ("best", SHOP_BEST)) if itemid in t and not shopid}
    icon = d.get("bundleicon", itemid)
    items = d["items"] if category == "PACKAGE" else [{"itemid": itemid, "count": 1, "staticdata": d}]
    return {"_id": str(shopid or itemid), "shopid": shopid or itemid, "shopcategory": category, "forceenable": True,
            "visible": True, **rank, "salestart": "2014-01-01T00:00:00.000Z", "saleend": "2035-01-01T00:00:00.000Z",
            "product": {"itemid": itemid, "staticdata": {**d, "bundleicon": icon, "items": items}}}


CHAIN_STEPS = {i: (first, k) for first, steps in CHAINED.items() for k, i in enumerate(steps)}


def chained_entry(st, first):
    """A Serial product at my progress: step = steps bought (capped at the last)."""
    steps, done = CHAINED[first], my_coach(st).get("chain", {}).get(str(first), 0)
    parts = [dict(shop_entry(i, "CHAINED"), remainedperaccount=0 if k < done else 1, buy=k < done)
             for k, i in enumerate(steps)]
    return {**parts[0], "step": min(done, len(steps) - 1), "chained": parts[1:]}


def shop_list(st):
    """GET /shop/list: every Shop tab's products (ShopController filters them by shopcategory);
    the ones hidden on the admin page and packs without a card (a season not installed) are left out."""
    on = lambda i: i not in SHOP_HIDDEN
    return ([shop_entry(i) for i in PACKS if on(i) and i not in REWARD_PACKS and pack_pool(i)]
            + [shop_entry(i, "USE") for i in ITEMS if on(i)]
            + [shop_entry(i, "PACKAGE") for i in PACKAGES if i not in CHAIN_STEPS and on(i)]
            + [shop_entry(i, "MILEAGE", MP_SHOPID + i) for i in MP_SHOP if on(i)]
            + [shop_entry(i, "MONTHLYPACK") for i in MONTHLY if on(i)]
            + [chained_entry(st, first) for first in CHAINED if on(first)])


def product_category(itemid):
    return "PLAYERPACK" if itemid in PACKS else "USE" if itemid in ITEMS else "PACKAGE"


# --------------------------------------------------------------------------- #
# Shop catalog from the admin page (user: "di admin page buatkan untuk kontrol shop semua tab, bisa
# buat pack baru, paket baru dll"; issue #10). gamedata/shop.json holds only what was changed
# there, on top of the built-in tables above:
#   free     every product costs 0 EP (default SHOP_FREE)
#   prices   {itemid: EP for one}: a fixed price, also while the Shop is free (0 = free)
#   hidden   [itemid]: left out of every Shop tab (cards and items already owned still work)
#   packs    {itemid: {name, icon, seasons [codes], top, ovr (rating at +1), grade (max +N), pos
#             (FW/MF/DF/GK), league, all (any card, else ID-licensed only), draft (pick N of
#             one season's cards, opened at +grade)}}
#   items    {itemid: {name, icon, type, description, ep [min, max] | prizes [[weight, itemid |
#             {"ep": n}]] | boost (0..1) | exp, lv, season (EXP capsule)}}: the types use_item knows
#   packages {itemid: {name, icon, contents [[itemid, count]], description}}
#   monthly  {itemid: {name, icon, days, now [[itemid, count]], daily [[itemid, count]], description}}
#   lists    {new, best (Rekomendasi), mp (MP Shop), special (Bonus spesial): [itemid],
#             chained: [[package itemid, ...]] (Paket berantai, bought in order)}
# A built-in itemid in packs/items/packages/monthly is replaced by that entry. apply_shop()
# rebuilds the tables in place (every lookup above keeps working) and drops the pack pools.
# --------------------------------------------------------------------------- #
SHOP_FILE = os.path.join(HERE, "gamedata", "shop.json")
SHOP_ITEM_TYPES = ("EP_LOTTERY", "ITEM_LOTTERY", "COACHNAME_CHANGE", "TEAM_CHANGE", "ENHANCEMENT_BOOST", "LEAGUE_SIMULATION",
                   "PLAYER_EXP_CAPSULE", "PLAYER_GROWTH_RESET")
SHOP_ID_BASE = {"packs": 93_000_000, "items": 94_000_000, "packages": 95_000_000, "monthly": 96_000_000}
SHOP_HIDDEN = frozenset()
_SHOP_BASE = None
_SHOP = {}


def shop_config():
    """The admin page's Shop changes (gamedata/shop.json), {} when there are none."""
    try:
        with open(SHOP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _pairs(rows):
    return [(int(i), int(n)) for i, n in rows]


def pack_from(v):
    f = {k: v[k] for k in ("top", "ovr", "grade", "pos", "league", "draft") if v.get(k) not in (None, "", 0)}
    if v.get("seasons"):
        f["seasons"] = tuple(int(s) for s in v["seasons"])
    if v.get("all"):
        f["all"] = True
    if v.get("icon"):
        f["icon"] = v["icon"]
    return (str(v["name"]), f)


def pack_to(entry):
    name, f = entry
    seasons = f.get("seasons") or ((f["season"],) if "season" in f else ())
    return {"name": name, "icon": f.get("icon", ""), "seasons": list(seasons), "all": bool(f.get("all")),
            **{k: f.get(k) for k in ("top", "ovr", "grade", "pos", "league", "draft")}}


def item_from(v):
    t = v["type"]
    eff = ({"ep": tuple(int(x) for x in v["ep"])} if t == "EP_LOTTERY"
           else {"prizes": [(int(w), ("ep", int(p["ep"])) if isinstance(p, dict) else int(p)) for w, p in v["prizes"]]}
           if t == "ITEM_LOTTERY" else {"boost": float(v["boost"])} if t == "ENHANCEMENT_BOOST"
           else {k: int(v[k]) for k in ("exp", "lv", "season") if v.get(k)} if t == "PLAYER_EXP_CAPSULE" else {})
    return (str(v["name"]), v.get("icon") or "", t, eff, str(v.get("description") or ""))


def item_to(entry):
    name, icon, t, eff, desc = entry
    return {"name": name, "icon": icon, "type": t, "description": desc,
            **({"ep": list(eff["ep"])} if "ep" in eff else {}), **({"boost": eff["boost"]} if "boost" in eff else {}),
            **{k: eff[k] for k in ("exp", "lv", "season") if k in eff},
            **({"prizes": [[w, {"ep": p[1]} if isinstance(p, tuple) else p] for w, p in eff["prizes"]]}
               if "prizes" in eff else {})}


def package_from(v):
    return (str(v["name"]), v.get("icon") or "", _pairs(v["contents"]), str(v.get("description") or ""))


def package_to(entry):
    name, icon, contents, desc = entry
    return {"name": name, "icon": icon, "contents": [list(c) for c in contents], "description": desc}


def monthly_from(v):
    return (str(v["name"]), v.get("icon") or "", int(v["days"]), _pairs(v["now"]), _pairs(v["daily"]),
            str(v.get("description") or ""))


def monthly_to(entry):
    name, icon, days, now, daily, desc = entry
    return {"name": name, "icon": icon, "days": days, "now": [list(c) for c in now], "daily": [list(c) for c in daily],
            "description": desc}


SHOP_KINDS = {"packs": (PACKS, pack_from, pack_to), "items": (ITEMS, item_from, item_to),
              "packages": (PACKAGES, package_from, package_to), "monthly": (MONTHLY, monthly_from, monthly_to)}


def apply_shop(cfg=None):
    """Rebuild the Shop tables from the built-in ones plus gamedata/shop.json (or cfg)."""
    global _SHOP_BASE, _SHOP, SHOP_FREE, SHOP_HIDDEN, SHOP_NEW, SHOP_BEST, MP_SHOP, SPECIAL, CHAINED, CHAIN_STEPS
    if _SHOP_BASE is None:
        _SHOP_BASE = {"free": SHOP_FREE, "new": SHOP_NEW, "best": SHOP_BEST, "mp": MP_SHOP, "special": SPECIAL,
                      "chained": dict(CHAINED), **{k: dict(t) for k, (t, _, _) in SHOP_KINDS.items()}}
    _SHOP = shop_config() if cfg is None else cfg
    before = dict(PACKS)
    for kind, (table, conv, _) in SHOP_KINDS.items():
        table.clear()
        table.update(_SHOP_BASE[kind])
        table.update({int(k): conv(v) for k, v in (_SHOP.get(kind) or {}).items()})
    lists = _SHOP.get("lists") or {}
    SHOP_FREE = bool(_SHOP.get("free", _SHOP_BASE["free"]))
    SHOP_HIDDEN = frozenset(int(i) for i in _SHOP.get("hidden") or ())
    SHOP_NEW, SHOP_BEST, MP_SHOP, SPECIAL = (tuple(int(i) for i in lists.get(k, _SHOP_BASE[k]))
                                             for k in ("new", "best", "mp", "special"))
    CHAINED = ({int(c[0]): tuple(int(i) for i in c) for c in lists["chained"] if c} if "chained" in lists
               else dict(_SHOP_BASE["chained"]))
    CHAIN_STEPS = {i: (first, k) for first, steps in CHAINED.items() for k, i in enumerate(steps)}
    changed = [k for k in set(before) | set(PACKS) if before.get(k) != PACKS.get(k)]
    for k in changed:  # rebuild only the pools of changed packs (all of them take ~7 s)
        _PACK_POOL.pop(k, None)
    if changed:
        pack_ev.cache_clear()  # a changed pack kept its old price (the others' are a quick sum again)


def product_price(itemid, n):
    """EP for a bunch of n: the admin page's fixed price (10 packs 5% off), else 0 while the Shop
    is free, else what it is worth (shop_worth) rounded up: a paid Shop sells nothing for less EP
    than it gives or fetches (rounded to the nearest, or 5% off for 10, a pack made EP: its cards
    sell for 99.75% of their price at best)."""
    fixed = (_SHOP.get("prices") or {}).get(str(itemid))
    if fixed is not None:
        return sig3(int(fixed) * n * (0.95 if n > 1 and itemid in PACKS else 1))
    return 0 if SHOP_FREE else nice_ceil(shop_worth(itemid) * n)


# What holds no EP costs in a paid Shop, at player prices x1 (issue #30: "all Shop items should have
# reasonable prices that scale according to the selected Economy difficulty"), so times the Ekonomi
# tab's player price setting; the admin page's fixed price wins. A boost costs the upgrade points
# "Gunakan poin" takes at +9 for the same chance (Silver +10% = 100 points).
ES_EP = 1000  # an upgrade point
EXP_EP = 10  # a point of a player's EXP (LV.1 -> LV.23 takes 25,300)
CHANGE_EP = {"COACHNAME_CHANGE": 100_000, "TEAM_CHANGE": 300_000, "PLAYER_GROWTH_RESET": 200_000}


def shop_worth(itemid, depth=0):
    """EP worth of one itemid in a paid Shop: a pack its cards' (pack_worth), EP its amount, an EP
    card its expected EP, a box its expected prize, a package or monthly pack all it gives, a
    League Simulation coupon the most its round pays; upgrade points, boosts, EXP capsules and a
    name or team change their set price (ES_EP, EXP_EP, CHANGE_EP)."""
    if depth > 4:  # an admin-made box or package holding itself
        return 0
    worth = lambda i: shop_worth(i, depth + 1)
    if itemid == EP_ITEM:
        return 1
    if itemid == ES_ITEM:
        return ES_EP * ECONOMY["players"]
    if itemid in PACKS:
        return pack_worth(itemid)
    if itemid in ITEMS:
        typ, eff = ITEMS[itemid][2:4]
        if "ep" in eff:  # use_item: triangular(lo, hi, mode lo)
            return (2 * eff["ep"][0] + eff["ep"][1]) / 3
        if "boost" in eff:
            return eff["boost"] / ES_BOOST * enhance_es({"grade": 9}) * ES_EP * ECONOMY["players"]
        if "exp" in eff:
            return eff["exp"] * EXP_EP * ECONOMY["players"]
        if typ in CHANGE_EP:
            return CHANGE_EP[typ] * ECONOMY["players"]
        if typ == "LEAGUE_SIMULATION":  # a won match, its reward card, its share of the season's prizes
            cfg = match_reward_cfg()
            cards = [(w, p["ep"] if isinstance(p, dict) else worth(p)) for w, p in cfg["cards"]
                     if isinstance(p, dict) or p in ITEMS or p in PACKS]  # as match_cards_reward draws
            total = sum(w for w, _ in cards)
            card = sum(w * v for w, v in cards) / total if total else 0
            return cfg["result"]["win"]["ep"] + card + season_top_ep() / (2 * (LEAGUE_SIZE - 1))
        prizes = eff.get("prizes") or ()
        total = sum(w for w, _ in prizes)
        return sum(w * (p[1] if isinstance(p, tuple) else worth(p)) for w, p in prizes) / total if total else 0
    if itemid in MONTHLY:
        _, _, days, now, daily, _ = MONTHLY[itemid]
        return sum(n * worth(i) for i, n in now) + days * sum(n * worth(i) for i, n in daily)
    if itemid in PACKAGES:
        return sum(n * worth(i) for i, n in PACKAGES[itemid][2])
    return 0


apply_shop()


def ep_specifictarget(query, body):
    """GET /specifictarget: Special Bonus is open for me (ShopController.getSpecialShopInfo)."""
    return _json({"issell": True, "targets": [{"stid": STSHOPID + i, "endtime": "2035-01-01T00:00:00.000Z"}
                                              for i in SPECIAL if i not in SHOP_HIDDEN]})


def ep_stshop(query, body):
    """GET /shop/list/STSHOP: the Special Bonus products."""
    return _json([dict(shop_entry(i, product_category(i), STSHOPID + i), stid=STSHOPID + i)
                  for i in SPECIAL if i not in SHOP_HIDDEN])


def monthly_list(st):
    """GET /monthlypack body: my active monthly packages."""
    return [{"_id": f"{i:024x}", "productid": i, "product": {"itemid": i, "staticdata": item_def(i)},
             "purchasedat": w3c(m["since"]), "expireat": w3c(m["expire"]), "rewardedat": w3c(m["rewarded"])}
            for i, m in ((int(k), v) for k, v in my_coach(st).get("monthly", {}).items()) if m["expire"] > time.time()]


def ep_monthlypack(query, body):
    """GET /monthlypack: my packages; POST: today's daily items of each (once a day) to the Bag."""
    st = _state()
    if body:  # POST (ShopMonthlyPackageProxy.requestReward)
        today = today_wib()
        for k, m in my_coach(st).get("monthly", {}).items():
            if m["expire"] > time.time() and today_wib(m["rewarded"]) != today and int(k) in MONTHLY:
                name, _, _, _, daily, _ = MONTHLY[int(k)]
                e = bag_add(st, f"{name} - hadiah harian", [{"itemid": i, "count": n} for i, n in daily],
                            msg=f"Hadiah harian {name}.")
                e["type"] = "monthlypack"
                m["rewarded"] = time.time()
        _save(st)
        return _json({"result": True, "resources": [{"uri": "/monthlypack", "body": monthly_list(st)},
                                                    {"uri": "/bag", "body": bag_list(st)}]})
    return _json(monthly_list(st))


def inventory_add(coach, itemid, count):
    inv = coach.setdefault("items", [])
    it = next((i for i in inv if i["itemid"] == itemid), None)
    if it is None:
        it = {"_id": new_iid(), "itemid": itemid, "remains": 0}
        inv.append(it)
    it["remains"] += count
    return it


def my_coach(st):
    return next(c for c in st["coaches"] if c["slot"] == _default_slot(st))


def coach_items(st, slot, where="items"):
    """/coach/me items (where="depot": the Gudang list): one stack per pack ({_id, itemid,
    remains}); expireable marks an owned item (GeneralInventoryTileItemRenderer: Gunakan button
    instead of Beli). Item saya also holds the locker room ticket for good."""
    coach = next(c for c in st["coaches"] if c["slot"] == slot)
    return [{"_id": i["_id"], "id": i["_id"], "itemid": i["itemid"], "remains": i["remains"], "count": i["remains"],
             "expireable": True, "expired": False, "staticdata": item_def(i["itemid"])}
            for i in coach.get(where, []) if i["itemid"] in PACKS or i["itemid"] in ITEMS] + [LR_TICKET_ITEM] * (where == "items")


def ep_shop_purchase(query, body):
    """POST /shop/purchase {shopid, productid, bunch: price option index}: pay EP; packs and
    items go to Item saya, a package's contents too."""
    req = json.loads(body or b"{}")
    shopid = int(req.get("shopid") or 0)
    gp = shopid >= GP_SHOPID and gp_shop_prices().get(shopid - GP_SHOPID)  # a Quest window reward box: GP
    itemid = int(req.get("productid") or 0) or (shopid - GP_SHOPID if gp else shopid % MP_SHOPID % STSHOPID)
    if itemid in SHOP_HIDDEN and not gp or not any(itemid in t for t in (PACKS, ITEMS, PACKAGES, MONTHLY)):
        return _json({"result": False, "reason": "NOT_EXIST_PRODUCT"})
    bunches = [{"bunch": 1, "price": gp}] if gp else item_def(itemid)["bunches"]
    b = bunches[min(max(int(req.get("bunch") or 0), 0), len(bunches) - 1)]
    st = _state(); slot = _default_slot(st); coach = my_coach(st)
    if gp:
        m = missions_state(st)
        if m["gp"] < gp:
            return _json({"result": False, "reason": "NOT_ENOUGH_GP"})
        m["gp"] -= gp
    elif coach_me(st, slot)["money"]["ep"] < b["price"]:
        return _json({"result": False, "reason": "NOT_ENOUGH_EP"})
    else:
        add_ep(st, -b["price"])
    contents = PACKAGES[itemid][2] if itemid in PACKAGES else MONTHLY[itemid][3] if itemid in MONTHLY else [(itemid, 1)]
    for i, n in contents:
        if i == EP_ITEM:
            add_ep(st, n * b["bunch"])
        elif i == ES_ITEM:
            add_es(st, n * b["bunch"])
        else:
            inventory_add(coach, i, n * b["bunch"])
    if itemid in MONTHLY:  # a month of daily items (renewing extends it)
        m = coach.setdefault("monthly", {}).setdefault(str(itemid), {"since": time.time(), "expire": time.time(), "rewarded": 0})
        m["expire"] = max(m["expire"], time.time()) + MONTHLY[itemid][2] * 86400
    if itemid in CHAIN_STEPS:
        first, k = CHAIN_STEPS[itemid]
        chain = coach.setdefault("chain", {})
        chain[str(first)] = max(chain.get(str(first), 0), k + 1)
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)
                  + ([{"uri": "/monthlypack", "body": monthly_list(st)}] if itemid in MONTHLY else [])})


def use_item(st, slot, coach, it, req):
    """/item/<_id>/use for a USE item (see ITEMS): its effect, one used up."""
    _, _, typ, eff, _ = ITEMS[it["itemid"]]
    if typ in ("ENHANCEMENT_BOOST", "LEAGUE_SIMULATION"):  # spent by the Upgrade tab / Liga's Simulasi, not by "Gunakan"
        return {"result": False, "reason": "ITEM_USE_ERROR"}
    out = {"result": True}
    if typ == "EP_LOTTERY":  # lower amounts more likely
        lo, hi = eff["ep"]
        out["ep"] = lo if lo == hi else max(lo, round(random.triangular(lo, hi, lo), -4))
        add_ep(st, out["ep"])
    elif typ == "ITEM_LOTTERY":
        prize = random.choices([p for _, p in eff["prizes"]], [w for w, _ in eff["prizes"]])[0]
        if isinstance(prize, tuple):
            out["ep"] = prize[1]
            add_ep(st, prize[1])
        else:
            inventory_add(coach, prize, 1)
            out["item"] = {"itemid": prize, "count": 1, "staticdata": item_def(prize)}
    elif typ == "COACHNAME_CHANGE":
        name = str(req.get("name") or "").strip()
        if not 2 <= len(name) <= 12:
            return {"result": False, "reason": "COACH_CREATION_ERROR_COACH_TOO_SHORT"}
        coach["name"] = name
    elif typ == "TEAM_CHANGE":
        teamid = int(req.get("teamid") or 0)
        if not roster(teamid)["team"]["players"]:
            return {"result": False, "reason": "ITEM_USE_ERROR"}
        coach["teamid"] = teamid
    elif typ == "PLAYER_EXP_CAPSULE":  # the players ExpCapsuleSelectingPage lists: below the max level, lv, season
        p = next((p for p in st["teams"][str(slot)]["players"] if iid(p) == str(req.get("targetid"))), None)
        level = player_levelinfo(p)["level"] if p else PLAYER_MAX_LEVEL
        if (level >= PLAYER_MAX_LEVEL or level > (eff.get("lv") or PLAYER_MAX_LEVEL)
                or eff.get("season") and p["spid"] // 1000000 != eff["season"]):
            return {"result": False, "reason": "ITEM_USE_ERROR"}
        gain_exp(p, eff["exp"], player_need, PLAYER_MAX_LEVEL)
    elif typ == "PLAYER_GROWTH_RESET":  # Player Growth's Reset: the card's points back to me
        p = next((p for p in st["teams"][str(slot)]["players"] if iid(p) == str(req.get("targetid"))), None)
        if p is None or not (p.get("sp") or {}).get("used"):
            return {"result": True}  # nothing to reset: the item stays (the page shuts the game on false)
        csp = coach_sp(coach)
        csp["used"] = max(0, csp["used"] - p.pop("sp")["used"])
    it["remains"] -= 1
    if not it["remains"]:
        coach["items"].remove(it)
    return out


def ep_item_use(item_id, query, body):
    """POST /item/<_id>/use: open one pack -> {result, player: {spid, grade, price}}; USE items
    see use_item."""
    st = _state(); slot = _default_slot(st); coach = my_coach(st); team = st["teams"][str(slot)]
    it = next((i for i in coach.get("items", []) if i["_id"] == item_id and i["remains"] > 0), None)
    req = json.loads(body or b"{}")
    if it is not None and it["itemid"] in ITEMS:
        out = use_item(st, slot, coach, it, req)
        if out["result"]:
            _save(st)
        return _json({**out, "resources": coach_me_resources(st, slot)})
    if it is None or it["itemid"] not in PACKS:
        return _json({"result": False, "reason": "INVALID_ITEM"})
    if len(team["players"]) >= MAX_PLAYERS:
        return _json({"result": False, "reason": "Exceed plyer limit"})  # client shows "squad full"
    pool = pack_pool(it["itemid"])
    draft = PACKS[it["itemid"]][1].get("draft")
    if draft:  # one of the cards picked on the draft screen, each as likely
        try:
            picks = sorted({int(s) for s in req.get("spids") or []})
        except (TypeError, ValueError):
            picks = []
        if len(picks) != draft or not set(picks) <= {s for s, _ in pool}:
            return _json({"result": False, "reason": "ITEM_USE_ERROR"})
        spid = random.choice(picks)
    else:
        spid = random.choices([s for s, _ in pool], [w for _, w in pool])[0]
    g = pack_grade(it["itemid"])
    p = add_player(team, spid, g)
    it["remains"] -= 1
    if not it["remains"]:
        coach["items"].remove(it)
    _save(st)
    return _json({"result": True, "player": {"_id": p["_id"], "spid": spid, "grade": g, "price": card_price(spid, g)},
                  "resources": coach_me_resources(st, slot)})


def ep_draftpack_players(itemid, n, query, body):
    """GET /item/draftpack/<itemid>/players/<n>: the draft pack's n most valuable cards
    [{spid, price}] (the client sets their grade itself)."""
    itemid = int(itemid)
    if not PACKS.get(itemid, ("", {}))[1].get("draft"):
        return _json([])
    g = pack_grades(itemid)[0][0]
    return _json([{"spid": s, "price": card_price(s, g)} for s in draft_top(itemid, int(n))])


def ep_transfer_price_default(grade, query, body):
    """POST /transfer/price/default/<grade> {spids}: Market prices of cards at that grade
    ({prices: [{spid, price}]}; the draft screen's search results outside its list)."""
    spids = []
    for s in json.loads(body or b"{}").get("spids") or []:
        try:
            spids.append(int(s))
        except (TypeError, ValueError):
            pass
    return _json({"prices": [{"spid": s, "price": card_price(s, int(grade))} for s in spids if on_market(s)]})


def ep_static_item(query, body):
    """POST /static/cache/item {itemids} -> {itemid: staticdata} (ItemUtil cache)."""
    ids = []
    for i in json.loads(body or b"{}").get("itemids") or []:
        try:
            ids.append(int(i))
        except (TypeError, ValueError):
            pass
    return _json({str(i): item_def(i) for i in ids if i in (EP_ITEM, ES_ITEM) or i in PACKS or i in ITEMS or i in PACKAGES})


# --------------------------------------------------------------------------- #
# Gudang (Item saya > Gudang, ItemDepotTabPage; issue #12): coach["depot"] holds stacks like
# coach["items"]. An Item saya tile (packs, USE items: GeneralInventoryTileItemRenderer
# .isMovableItem) posts /items/itemtodepot {moveitems: [id]}; a Gudang tile posts
# /item/depottoitem/<_id> {} (ItemUtil.checkMovableItemsToInventory checks for room first).
# Whole stacks move. On {result: true} the client GETs /item/depot (an array of coach_items
# entries) and shows DepotItemMoveCompletePopup; otherwise a dialog with `reason`.
# --------------------------------------------------------------------------- #
def stack_move(coach, item_id, src, dst):
    """Move stack item_id from coach[src] to coach[dst], joining a stack of the same item there."""
    it = next((i for i in coach.get(src, []) if i["_id"] == item_id and i["remains"] > 0), None)
    if it is None:
        return False
    coach[src].remove(it)
    same = next((i for i in coach.setdefault(dst, []) if i["itemid"] == it["itemid"]), None)
    if same is None:
        coach[dst].append(it)
    else:
        same["remains"] += it["remains"]
    return True


def depot_reply(st, ok):
    if not ok:  # nothing saved: a refused move changes nothing
        return _json({"result": False, "reason": "Item tidak ditemukan."})
    _save(st)
    slot = _default_slot(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)
                  + [{"uri": "/item/depot", "body": coach_items(st, slot, "depot")}]})


def ep_item_to_depot(query, body):
    """POST /items/itemtodepot {moveitems: [_id...]}: Item saya -> Gudang (the locker room ticket stays)."""
    st = _state(); coach = my_coach(st)
    moved = [stack_move(coach, str(i), "items", "depot") for i in json.loads(body or b"{}").get("moveitems") or []
             if str(i) != LR_TICKET_ITEM["_id"]]
    return depot_reply(st, moved and all(moved))


def ep_depot_to_item(item_id, query, body):
    """POST /item/depottoitem/<_id> {}: Gudang -> Item saya."""
    st = _state()
    return depot_reply(st, stack_move(my_coach(st), item_id, "depot", "items"))


def ep_item_depot(query, body):
    """GET /item/depot: the Gudang list."""
    st = _state()
    return _json(coach_items(st, _default_slot(st), "depot"))


# --------------------------------------------------------------------------- #
# Daily login reward: the first login of a day (WIB, at PUT /coach/<slot>/default) drops that
# day's reward into the Bag (Item saya > Items, ItemBagTabPage: GET /bag; "Terima" = POST
# /bag/<id>/unpack, "Terima semua" = POST /bag/unpackall {reqUnpackItems}). 7-day cycle on a
# consecutive-login streak; the table is gamedata/daily_rewards.json (admin page edits it).
# Bag entries are event packs (ItemUtil.isEventItempackData: no product, type "event",
# items [{itemid, count}] expanded via /static/cache/item, players [{spid, grade}]).
# --------------------------------------------------------------------------- #
DAILY_FILE = os.path.join(HERE, "gamedata", "daily_rewards.json")
DAILY_DEFAULT = [{"ep": 10000}, {"ep": 20000}, {"pack": 28007}, {"ep": 30000}, {"pack": 28006},
                 {"ep": 50000}, {"ep": 100000, "pack": 82214068}]  # day 1..7
WIB = 7 * 3600


def daily_table():
    try:
        with open(DAILY_FILE, encoding="utf-8") as f:
            return json.load(f) or DAILY_DEFAULT
    except (OSError, ValueError):
        return DAILY_DEFAULT


def today_wib(ts=None):
    return time.strftime("%Y-%m-%d", time.gmtime((time.time() if ts is None else ts) + WIB))


def reward_items(reward):
    return ([{"itemid": EP_ITEM, "count": int(reward["ep"])}] if reward.get("ep") else []) + \
           ([{"itemid": int(reward["pack"]), "count": int(reward.get("count") or 1)}] if reward.get("pack") else [])


def daily_note(st, now=None):
    """Today's login reward as an event notification, once a day (user: "daily login belum ada"):
    its toast (EVENT_GET_MESSAGE) and a list entry that opens EventRewardPopup (SystemMessageUtil
    event.reward: details = the event {type daily, opts.dailytype, rewardindex (day - 1),
    messages.popup {name, description}, assets, items}), or None."""
    d, today = my_coach(st).get("daily") or {}, today_wib(now)
    if d.get("last") != today or d.get("noted") == today:
        return None
    d["noted"] = today
    table, n = daily_table(), d["streak"]
    items = [{**it, "staticdata": item_def(it["itemid"])} for it in reward_items(table[(n - 1) % len(table)])]
    return {**note("event", "reward", {
        "type": "daily", "opts": {"dailytype": "continuous"}, "rewardindex": n - 1, "assets": {}, "items": items,
        "title": "Hadiah login harian", "messages": {"popup": {"name": "Hadiah login harian", "description":
            f"Login {n} hari berturut-turut. Hadiahnya sudah masuk Bag (Item saya)."}}}, now), "stackable": True}


def ep_event_daily(query, body):
    """PUT /events/rewards/daily: InGameEventController asks for it 0-2 minutes into a session and
    when the UTC date turns. Today's login reward if not given yet, and its notification."""
    st = _state()
    daily_login(st)
    n = daily_note(st)
    _save(st)
    if n:
        messenger_push("/inbox/my/notifications/latest", n)
    return _json({"result": True})


# Play time rewards (user: "sama hadiah berapa lama player login"): InGameEventController's "dt"
# event in GET /events: condition.dt {time {beginningat, prevsession (s)}, timeline [s per step],
# endline (s), rewarded}, limit.reward (rounds a day). The client counts prevsession + its clock
# since beginningat (ms, a number: TimerUtil.convertToUTCTime adds -timezone minutes to it, as to
# the server time, so its "UTC" day is the WIB day of /info/server/time's timezone -420; a W3C string
# made an invalid date, read as midnight), starts over at 00:00 WIB (prevsession 0, rewarded 0), and
# once a step of the timeline is passed asks PUT /events/rewards/dt; done = rewarded >= steps x
# limit.reward. The time is the server's play time (note_activity: request gaps up to PLAYTIME_GAP)
# since that WIB day began.
PLAYTIME_FILE = os.path.join(HERE, "gamedata", "playtime_rewards.json")
PLAYTIME_DEFAULT = [{"minutes": 10, "ep": 10000}, {"minutes": 30, "ep": 30000}, {"minutes": 60, "pack": 28007}]
DT_EVENTID = 1002


def playtime_table():
    try:
        with open(PLAYTIME_FILE, encoding="utf-8") as f:
            return json.load(f) or PLAYTIME_DEFAULT
    except (OSError, ValueError):
        return PLAYTIME_DEFAULT


def played_today(st, now=None):
    """(my coach's "dt" record, seconds played since 00:00 WIB): its base is the play time then."""
    coach = my_coach(st)
    total = coach.get("playtime", 0) + PLAYTIME["pending"]
    day = today_wib(now)
    if (coach.get("dt") or {}).get("day") != day:
        coach["dt"] = {"day": day, "base": total, "rewarded": 0}
    return coach["dt"], int(total - coach["dt"]["base"])


def dt_event(st, now=None):
    now = time.time() if now is None else now
    d, secs = played_today(st, now)
    table = playtime_table()
    timeline = [int(r["minutes"]) * 60 for r in table]
    return {"eventid": DT_EVENTID, "active": True, "reason": [],
            "condition": {"type": "dt", "timeset": ENHANCE_EVENT["condition"]["timeset"],
                          "targetuser": ENHANCE_EVENT["condition"]["targetuser"],
                          "dt": {"time": {"beginningat": int(now * 1000), "prevsession": secs}, "timeline": timeline,
                                 "endline": timeline[-1], "rewarded": d["rewarded"]}},
            # a step's reward: [{items: [...]}] (InGameDTEventViewer.updateRewardIcon shows
            # reward[step][0].items[0]; a bare item list threw TypeError #1010 entering the lobby)
            "limit": {"reward": 1}, "reward": [[{"items": [{**it, "staticdata": item_def(it["itemid"])} for it in reward_items(r)]}]
                                                for r in table],
            "messages": {"main": {"name": "Hadiah waktu bermain", "description": "Main " + ", ".join(
                f"{r['minutes']}" for r in table) + " menit hari ini (sejak 00:00 WIB): tiap target memberi hadiah ke Bag."}}}


def events_body(st):
    return {"events": [ENHANCE_EVENT, dt_event(st)] if st.get("coaches") else [ENHANCE_EVENT]}


def ep_events(query, body):
    st = _state()
    out = events_body(st)
    _save(st)
    return _json(out)


def ep_event_dt(query, body):
    """PUT /events/rewards/dt: the next step's reward once its time is played (2 minutes' slack: the
    client counts its own clock), then the event as it is now."""
    st, now = _state(), time.time()
    d, secs = played_today(st, now)
    table = playtime_table()
    i = d["rewarded"]
    ok = i < len(table) and secs + 120 >= int(table[i]["minutes"]) * 60
    n = None
    if ok:
        d["rewarded"] = i + 1
        items = reward_items(table[i])
        bag_add(st, f"Hadiah main {table[i]['minutes']} menit", items, msg=f"Kamu sudah main {table[i]['minutes']} menit hari ini.")
        n = {**note("event", "reward", {
            "type": "dt", "opts": {}, "rewardindex": i, "assets": {}, "title": "Hadiah waktu bermain",
            "items": [{**it, "staticdata": item_def(it["itemid"])} for it in items],
            "messages": {"popup": {"name": "Hadiah waktu bermain", "description":
                f"Main {table[i]['minutes']} menit hari ini. Hadiahnya sudah masuk Bag (Item saya)."}}}, now), "stackable": True}
    out = events_body(st)
    _save(st)
    if n:
        messenger_push("/inbox/my/notifications/latest", n)
    return _json({"result": ok, "resources": [{"uri": "/events", "body": out}]})


def bag_add(st, title, items=(), players=(), msg=""):
    entry = {"_id": new_iid(), "title": title, "msg": msg, "items": list(items), "players": list(players),
             "createdat": int(time.time())}
    my_coach(st).setdefault("bag", []).append(entry)
    return entry


def daily_login(st, now=None):
    """Grant today's reward once per WIB day; returns the new bag entry or None."""
    now = time.time() if now is None else now
    d = my_coach(st).setdefault("daily", {"last": "", "streak": 0, "total": 0})
    if d["last"] == today_wib(now):
        return None
    d["streak"] = d["streak"] + 1 if d["last"] == today_wib(now - 86400) else 1
    d["total"] += 1
    d["last"] = today_wib(now)
    table, n = daily_table(), d["streak"]
    return bag_add(st, f"Hadiah login harian - hari ke-{n}", reward_items(table[(n - 1) % len(table)]),
                   msg=f"Login {n} hari berturut-turut. Login lagi besok untuk hadiah hari ke-{n + 1}.")


def bag_list(st):
    out = []
    for e in my_coach(st).get("bag", []):
        icon = next((PACKS.get(i["itemid"], ("", {}))[1].get("icon", i["itemid"])
                     for i in e["items"] if i["itemid"] != EP_ITEM), EP_ITEM)
        # ShopItemDescription.MakeEventItemDesc tooltip: msg = text, date (W3C) = "Tanggal diterima"
        out.append({"_id": e["_id"], "id": e["_id"], "type": e.get("type", "event"), "title": e["title"],
                    "msg": e.get("msg") or "Hadiah dari server.", "date": w3c(e["createdat"]),
                    "items": e["items"], "players": e["players"], "bundleicon": icon})
    return out


def bag_apply(st, entry):
    """Credit a bag entry: EP, packs and items to Item saya, players to the club."""
    coach = my_coach(st); team = st["teams"][str(_default_slot(st))]
    for it in entry["items"]:
        if it["itemid"] == EP_ITEM:
            add_ep(st, it["count"])
        elif it["itemid"] in PACKS or it["itemid"] in ITEMS:
            inventory_add(coach, it["itemid"], it["count"])
    for p in entry["players"]:
        add_player(team, p["spid"], p.get("grade", 1))
    coach["bag"].remove(entry)


# Manager level rewards (issue #35: "When level up got nothing", FO3's table at
# fifaaddict.com/fo3level_rewards.php): the EP and items of every level 2..100 go to the Bag
# ("Hadiah naik level") as the manager reaches it. The post-match screen shows them in its level-up
# popup (PostMatchRewardDefaultScene.showCoachLevelUpPopup: GET /coach/me/level/reward, a list of
# item tiles; CoachLevelUpPopup) and the coming levels' behind its info icon (setLevelUpRewards:
# GET /static/db/REWARDS/coachlevelup {"<level>": {coachlevel, items}}, CoachLevelUpRewardsPopup).
# Items this server has no use for are left out (staff packs, Red Ball, locker room and reward boost
# coupons, EXP transfer); the EXP capsules are the nearest of ours (LV5 1,000, LV7 5,000, LV10 the
# LV.10 one, LV17+ Max). EP follows the Ekonomi tab's player prices, like the missions'.
LEVEL_EP = (50_000,) * 6 + (60_000, 70_000, 80_000, 90_000, 110_000, 120_000, 130_000, 140_000, 150_000, 160_000,
            170_000, 180_000, 200_000, 210_000, 220_000, 230_000, 240_000, 260_000, 270_000, 280_000, 300_000,
            310_000, 330_000, 340_000, 360_000, 380_000, 390_000, 410_000, 430_000, 450_000, 470_000, 500_000,
            520_000, 540_000, 560_000, 590_000, 610_000, 640_000, 670_000, 690_000, 720_000, 800_000, 900_000,
            1_000_000, 1_100_000, 1_200_000, 1_300_000, 1_400_000, 990_000, 1_030_000, 1_070_000, 1_120_000,
            *range(1_900_000, 4_000_000, 100_000), *range(4_900_000, 19_000_000, 1_000_000),
            *range(20_900_000, 29_000_000, 2_000_000))  # levels 2..100
_TOP300, _TOP100, _BEST50, _BEST30, _BEST20, _BEST10, _BEST7 = 82214007, 98000001, 98000002, 8223562, 98000003, 98000004, 98000005
_DF70, _MF70, _FW70, _B30P5, _B20P5, _B7P5 = 98000006, 98000007, 98000008, 98000009, 98000010, 98000011
_SIM, _TEAM, _ALL = 2001, 3001, 92000000
LEVEL_ITEMS = {  # level: ((itemid, count), ...); the levels left out give EP only
    2: ((_TOP300, 2), (_ALL, 5), (_SIM, 3)), 3: ((_TOP100, 2), (_ALL, 5)), 4: ((_TOP300, 2), (_SIM, 3)),
    5: ((_TOP300, 2), (_TEAM, 1)), 6: ((_TOP300, 2), (_SIM, 3)), 7: ((_TOP300, 2), (6001, 2)), 8: ((_TOP300, 2),),
    9: ((_TOP300, 2),), 10: ((_TOP100, 1), (_SIM, 20), (_TEAM, 1)), 11: ((_TOP100, 1), (6002, 2)),
    12: ((_TOP100, 1), (28004, 1), (6004, 2)), 13: ((_TOP100, 1), (28002, 1)), 14: ((_TOP100, 1), (28003, 1)),
    15: ((_TOP100, 1), (28001, 1)), 16: ((_TOP100, 1), (8223603, 1)), 17: ((_TOP100, 1), (8223603, 1)),
    18: ((_TOP100, 2), (8223603, 2)), 41: ((_BEST30, 5), (_DF70, 3)), 43: ((_BEST30, 6), (_DF70, 3)),
    44: ((_BEST30, 6), (_MF70, 3)), 45: ((_BEST10, 1), (_MF70, 3)), 46: ((_BEST10, 1), (_MF70, 3)),
    47: ((_BEST10, 1), (_FW70, 3)), 48: ((_BEST10, 1), (_FW70, 3)), 49: ((_BEST10, 1), (_FW70, 3)),
    51: ((_BEST10, 1),), 52: ((_BEST10, 2),), 53: ((_BEST10, 2),), 54: ((_BEST10, 2),), 55: ((_BEST10, 2),),
    60: ((_BEST10, 2), (98000012, 3), (_BEST50, 5)), 61: ((_BEST10, 2), (98000013, 3), (_BEST50, 6)),
    62: ((_BEST10, 2), (_BEST50, 8)), 63: ((_BEST7, 2), (45004, 2)), 64: ((_BEST7, 2), (45004, 2)),
    65: ((_BEST7, 2), (45004, 4)), 66: ((_BEST7, 2), (45004, 4), (_SIM, 20)), 67: ((_BEST7, 2), (45004, 5)),
    68: ((_BEST7, 2), (98000014, 1), (45004, 5)), 69: ((_BEST7, 2), (_BEST50, 4)), 70: ((_BEST7, 2), (_BEST50, 4)),
    71: ((_BEST7, 2), (_BEST50, 4)), 72: ((_BEST7, 2), (_BEST50, 5)), 73: ((_BEST7, 2), (_BEST30, 5)),
    74: ((_BEST7, 2), (_BEST30, 5)), 75: ((_BEST7, 2), (_BEST30, 5)), 76: ((_BEST7, 2), (_BEST30, 6)),
    77: ((_BEST7, 2), (_BEST30, 6), (_SIM, 20)), 78: ((_BEST7, 2), (_BEST30, 7)), 79: ((_B30P5, 2), (_BEST50, 1)),
    80: ((_B30P5, 2), (_BEST50, 2)), **{lv: ((_B20P5, 2),) for lv in range(81, 100)},
    90: ((_B20P5, 2), (6003, 1)), 93: ((_B20P5, 2), (6003, 1)), 100: ((_B7P5, 1), (6003, 1)),
}


def level_reward_items(level):
    """[{itemid, count}] of reaching manager level `level`: its EP (x the player price setting),
    then its items (one the admin page removed is skipped)."""
    ep = int(LEVEL_EP[level - 2] * ECONOMY["players"]) if 2 <= level <= len(LEVEL_EP) + 1 else 0
    return ([{"itemid": EP_ITEM, "count": ep}] if ep else []) + \
           [{"itemid": i, "count": n} for i, n in LEVEL_ITEMS.get(level, ()) if i in PACKS or i in ITEMS]


def coach_gain_exp(st, n):
    """gain_exp for my manager: each level he reaches puts its reward in the Bag and in the
    level-up popup's list (coach["levelreward"])."""
    coach = my_coach(st)
    prev, cur = gain_exp(coach, n, coach_need, COACH_MAX_LEVEL)
    for level in range(prev["level"] + 1, cur["level"] + 1):
        items = level_reward_items(level)
        if items:
            bag_add(st, f"Hadiah naik level Lv.{level}", items, msg=f"Level manajer naik ke {level}.")
            coach.setdefault("levelreward", []).extend(items)
    return prev, cur


def ep_level_reward(query, body):
    """GET /coach/me/level/reward: what the last level-ups gave (summed by item), once."""
    st = _state()
    got = my_coach(st).pop("levelreward", [])
    if got:
        _save(st)
    total = {}
    for it in got:
        total[it["itemid"]] = total.get(it["itemid"], 0) + it["count"]
    return _json([{"itemid": i, "count": n, "staticdata": item_def(i)} for i, n in total.items()])


def ep_level_reward_table(query, body):
    """GET /static/db/REWARDS/coachlevelup: every level's reward, for the coming levels' list."""
    return _json({str(lv): {"coachlevel": lv, "items": [{**it, "staticdata": item_def(it["itemid"])}
                                                        for it in level_reward_items(lv)]}
                  for lv in range(2, COACH_MAX_LEVEL + 1)})


# --------------------------------------------------------------------------- #
# Staff (user: "Aktifkan kembali staff", fifaaddict.com/fo3staff.php): Tim > Manajemen Staff and its
# tabs Staff yang dimiliki (the role frames), Latihan (upgrade), Rekrut (packs) and Pembinaan
# (study). CoachProxy reads it all from /coach/me team (staff_team) once the account has
# features.teammanagement.staffmanagement, and /static/db/STAFF (staff_static) once at boot. A
# staff in a role frame with contract left (life, in matches) boosts the starters of its position
# group, a MEDICAL, PREMIUM or FRONT one every starter: the client adds it to its squad lists
# (TeamColorController.applyStaffBoostStat; xi_ratings does the same for summary.ovr) and native
# recomputes it in a match from side.team.staffroles + staffs (0x93e230, which checks neither
# contract nor status: match_side sends the working ones only). Names and the pools of boost
# 1 / 2 / 3 are fifaaddict's (its ids 10xx..40xx and 9117..9122 are the client's 11xx..41xx and
# 9500..9505, the set the client cache has pictures of). Garena's grades, EXP, fees and contracts
# are lost: the numbers below are the server's own. "Boost mental status" (condition) is left out:
# the client leaves it to the server, and native's scale for a player's condition is unknown.
STAFF_FIT = ("MEDICAL", "PREMIUM", "FRONT")  # boost every starter (the others: their own group)
_SB3 = {"FW": "ballcontrol reactions finishing sprintspeed strength positioning acceleration",
        "MF": "dribbling shortpassing strength sprintspeed reactions acceleration positioning",
        "DF": "strength positioning reactions sprintspeed acceleration", "GK": "gkdiving gkpositioning gkreflexes",
        "FRONT": "jumping finishing strength agility acceleration"}
_SHEAD = (  # 9021..9033 (fifaaddict's made-up-name twins of 9001..9013): boost 1, boost 2 pool
    ("strength", "sprintspeed penalties finishing shotpower freekickaccuracy"),
    ("sprintspeed", "longpassing shortpassing crossing agility acceleration"),
    ("sprintspeed", "dribbling ballcontrol agility curve acceleration"),
    ("longpassing", "headingaccuracy jumping positioning longshots vision"),
    ("reactions", "positioning aggression jumping interceptions curve"),
    ("strength", "slidingtackle interceptions vision positioning longpassing"),
    ("finishing", "positioning volleys shotpower freekickaccuracy agility"),
    ("aggression", "standingtackle longpassing jumping headingaccuracy ballcontrol"),
    ("standingtackle", "slidingtackle positioning vision jumping headingaccuracy"),
    ("headingaccuracy", "positioning volleys jumping agility reactions"),
    ("ballcontrol", "shortpassing longpassing vision dribbling freekickaccuracy"),
    ("longshots", "crossing marking vision interceptions longpassing"),
    ("shotpower", "standingtackle aggression interceptions crossing longpassing"))
_SFRONT = (  # 9500..9505 (fifaaddict 9117..9122): boost 1 pool, boost 2 pool
    ("shotpower finishing jumping volleys aggression strength positioning reactions",
     "longshots volleys vision shotpower aggression finishing dribbling ballcontrol positioning reactions stamina balance curve"),
    ("marking interceptions reactions sprintspeed acceleration slidingtackle standingtackle dribbling",
     "longpassing shortpassing aggression marking crossing interceptions dribbling ballcontrol reactions stamina balance slidingtackle standingtackle"),
    ("longshots agility ballcontrol reactions stamina sprintspeed acceleration vision",
     "longpassing longshots vision shortpassing aggression finishing dribbling ballcontrol positioning reactions stamina balance agility"),
    ("ballcontrol balance agility interceptions reactions marking slidingtackle standingtackle",
     "jumping aggression strength positioning headingaccuracy longpassing interceptions vision shortpassing dribbling ballcontrol agility"),
    ("finishing dribbling ballcontrol positioning reactions sprintspeed strength acceleration",
     "longshots jumping volleys vision headingaccuracy shortpassing aggression shotpower dribbling ballcontrol positioning stamina strength"),
    ("longpassing vision shortpassing positioning balance dribbling ballcontrol agility",
     "longpassing longshots vision shortpassing aggression reactions dribbling ballcontrol positioning stamina balance agility"))


def _staff_kind(pos, first, b1s, b2s, b3, expect=1):
    """{stfid: (position, (boost 1, 2, 3 pools), gradeexpect id)} of the staff first, first + 1, ...
    (b1s: each one's boost 1 pool; b2s: (pool, how many staff in a row have it), ...)."""
    b2 = [p.split() for p, n in b2s for _ in range(n)]
    return {first + i: (pos, (tuple(b.split()), tuple(b2[i]), tuple(b3.split())), expect) for i, b in enumerate(b1s)}


# stfid: (position, (boost 1, 2, 3 pools), gradeexpect id); a helper (Staff pembantu, only ever an
# upgrade material) has no pools
STAFF_CATALOGUE = {
    **_staff_kind("FW", 1101, ("dribbling", "dribbling", "freekickaccuracy", "shotpower", "crossing") * 2,
                  (("longpassing longshots jumping volleys vision", 5), ("shortpassing agility headingaccuracy aggression", 5)), _SB3["FW"]),
    **_staff_kind("MF", 2101, ("longpassing", "standingtackle", "ballcontrol", "longshots", "aggression", "freekickaccuracy") * 2,
                  (("headingaccuracy finishing marking vision", 6), ("shotpower crossing interceptions", 6)), _SB3["MF"]),
    **_staff_kind("DF", 3101, ("marking", "standingtackle", "slidingtackle", "interceptions", "jumping", "shortpassing",
                               "headingaccuracy") * 2,
                  (("aggression longpassing crossing", 7), ("dribbling ballcontrol vision", 7)), _SB3["DF"]),
    **_staff_kind("GK", 4101, ("gkhandling", "gkkicking") * 2, (("jumping longpassing", 2), ("positioning reactions", 2)), _SB3["GK"]),
    **_staff_kind("MEDICAL", 5001, ("stamina", "balance"), (("stamina balance", 2),), "stamina balance"),
    **_staff_kind("MEDICAL", 9507, ("sprintspeed", "jumping", "dribbling", "agility", "reactions"), (("stamina balance", 5),),
                  "stamina balance ovr", expect=2),
    **_staff_kind("PREMIUM", 9021, [b for b, _ in _SHEAD], [(p, 1) for _, p in _SHEAD], "ovr", expect=2),
    **_staff_kind("FRONT", 9500, [b for b, _ in _SFRONT], [(p, 1) for _, p in _SFRONT], _SB3["FRONT"]),
    **{4901 + i: (pos, None, 1) for i, pos in enumerate(("FW", "MF", "DF", "GK", "MEDICAL"))},
}
STAFF_SLOTS = ("FW", "MF", "DF", "PREMIUM", "GK", "MEDICAL", "FRONT")  # role frames role00..06 (role03: the centre), all open
STAFF_MAX_GRADE, STAFF_LIFE, STAFF_MAX = 10, 50, 30  # half stars; contract matches (config.max_staff_life); staff a manager keeps
STAFF_GRADEUP = tuple(100 * g for g in range(1, STAFF_MAX_GRADE))  # EXP from grade g to g + 1 (exptable gradeupto)
# what reaching grade g adds to boosts 1 / 2 / 3 (gradeexpect): boost 1 +1 a grade (1 at 0.5 star,
# 10 at 5 stars, as the one figure found: a fitness coach's stamina +9 at 4.5 and +10 at 5 stars),
# boost 2 from 1.5 stars (+1 every other grade, 4), boost 3 from 3 stars (3); an OVR boost 3 (head
# coaches, the special fitness coaches; OVR is every stat) +1 at 3 and at 5 stars only
STAFF_EXPECT = {1: {g: (1, g % 2, int(g >= 6 and g % 2 == 0)) for g in range(2, STAFF_MAX_GRADE + 1)},
                2: {g: (1, g % 2, int(g in (6, 10))) for g in range(2, STAFF_MAX_GRADE + 1)}}
STAFF_MATERIAL_EXP = (30, 50, 80, 120)  # x the material's grade: other position, same, a helper of another, of the same
STAFF_ENHANCE_EP = 2_000  # x the material's grade (enhancefee)
STAFF_JACKPOT = (0.1, 0.5)  # chance of Latihan's bonus EXP, and its size (x the materials' EXP)
STAFF_CONTRACT_EP = 100  # x grade, per match a recontract adds
_ALL5, _ALL7 = ("FW", "MF", "DF", "GK", "MEDICAL"), ("FW", "MF", "DF", "GK", "MEDICAL", "FRONT", "PREMIUM")
# Rekrut: recruitid: (STAFFPACK_NAME_n, STAFFPACK_DESC_n, EP, positions, grades, helper chance,
# staff whose picture the tile shows without one of its own, icon: the tile's picture
# externalAssets/shop/<icon>.png, issue #41's pack art: drop it in OWN_ASSETS). The client's names and
# descriptions do not pair by number (DESC_2 is the FW pack's text, DESC_5 the ALL one's); 8 and 10
# are test data.
STAFF_PACKS = {
    2: (2, 5, 50_000, _ALL5, (2, 5), 0.2, 2103, "broall"), 3: (3, 2, 50_000, ("FW",), (2, 5), 0.2, 1105, "brofw"),
    4: (4, 3, 50_000, ("MF",), (2, 5), 0.2, 2103, "bromf"), 5: (5, 4, 50_000, ("DF",), (2, 5), 0.2, 3108, "brodf"),
    6: (6, 6, 50_000, ("GK",), (2, 5), 0.2, 4103, "brogk"), 7: (7, 7, 150_000, _ALL5, (3, 6), 0.2, 3108, "silall"),
    13: (13, 13, 300_000, ("MEDICAL",), (2, 6), 0, 9507, "medical"),
    9: (9, 9, 600_000, _ALL7, (2, 10), 0.2, 9027, "goldall"),
    11: (11, 11, 1_500_000, ("FRONT",), (6, 10), 0, 9501, "position"),
    12: (12, 12, 3_000_000, ("MEDICAL", "FRONT", "PREMIUM"), (6, 10), 0, 9027, "goldall")}
# the free row: ALL staff pack (0.5~2.0 star) once a day for logging in, once for 3 matches played
STAFF_FREE_ID, STAFF_FREE_MATCHES = 1, 3
STAFF_FREE_PACK = (1, 1, 0, _ALL5, (1, 4), 0.2, 1105, "staffpack_2103")
STAFF_PACK_PICTURES = {p[7]: p[6] for p in (STAFF_FREE_PACK, *STAFF_PACKS.values())}  # icon: stfid (asset_path)
STAFF_OPEN_MAX = 10  # staff one Rekrut buys at most (the count list; issue #41: 10 at once)
# Pembinaan (STAFF_STUDY_LOCAINFO01..06): lid: (boost it changes, None = Europe's random +1..3 on one,
# positions, lowest grade, EP, matches). Each repeat at a place doubles its fee, up to the max.
STAFF_STUDY = {"1": (0, ("FW", "DF"), 1, 20_000, 5), "2": (0, ("MF",), 1, 20_000, 5),
               "3": (1, ("MF", "DF"), 3, 30_000, 5), "4": (1, ("FW",), 3, 30_000, 5),
               "5": (2, ("FW", "MF", "DF", "GK", "MEDICAL", "FRONT"), 6, 50_000, 5), "6": (None, _ALL7, 1, 50_000, 10)}
STAFF_STUDY_FEE_X, STAFF_STUDY_FEE_MAX = 2, 1_000_000


def staff_ep(n):
    """EP of a staff price, on the Ekonomi tab's player price setting."""
    return nice_price(n * ECONOMY["players"])


def staff_helper(s):
    return STAFF_CATALOGUE[s["stfid"]][1] is None


def staff_grow(s, g, rng=random):
    """s reaches grade g: its boosts grow by gradeexpect, and a new one takes a stat of its pool."""
    s["grade"] = g
    pos, pools, expect = STAFF_CATALOGUE[s["stfid"]]
    for k, n in enumerate(STAFF_EXPECT[expect][g] if pools else ()):
        if n and k < len(s["effect"]):
            s["effect"][k]["amount"] += n
        elif n:
            s["effect"].append({"effect": rng.choice(pools[k]), "amount": n})


def staff_make(sid, grade, rng=random):
    """A new staff sid of grade `grade`, its boosts grown from 0.5 star."""
    s = {"_id": new_iid(), "stfid": sid, "grade": 1, "exp": 0, "life": STAFF_LIFE, "status": None,
         "effect": [{"effect": rng.choice(STAFF_CATALOGUE[sid][1][0]), "amount": 1}] if STAFF_CATALOGUE[sid][1] else []}
    for g in range(2, grade + 1):
        staff_grow(s, g, rng)
    return s


def staff_new(pack, rng=random):
    """A staff out of a Rekrut pack: one of its positions, a staff of it (or that position's helper),
    a grade of its range (each half star up 0.6 times as likely)."""
    positions, (lo, hi), helper = pack[3], pack[4], pack[5]
    pos = rng.choice(positions)
    ids = [i for i, c in STAFF_CATALOGUE.items() if c[0] == pos and c[1]]
    helpers = [i for i, c in STAFF_CATALOGUE.items() if c[0] == pos and not c[1]]
    sid = helpers[0] if helpers and rng.random() < helper else rng.choice(ids)
    return staff_make(sid, rng.choices(range(lo, hi + 1), [0.6 ** (g - lo) for g in range(lo, hi + 1)])[0], rng)


def staff_gain(s, n, rng=random):
    """n EXP into s (Latihan): each grade it reaches grows its boosts; at 5 stars EXP stays 0."""
    s["exp"] += n
    while s["grade"] < STAFF_MAX_GRADE and s["exp"] >= STAFF_GRADEUP[s["grade"] - 1]:
        s["exp"] -= STAFF_GRADEUP[s["grade"] - 1]
        staff_grow(s, s["grade"] + 1, rng)
    if s["grade"] >= STAFF_MAX_GRADE:
        s["exp"] = 0


def staff_out(s):
    """A staff as coach.team.staffs holds it. Never `id`: the client names it (commonname, a
    language key) only without one; effect must be an array or the whole coach feed stops."""
    pos, pools, _ = STAFF_CATALOGUE[s["stfid"]]
    g = s["grade"]
    return {"_id": s["_id"], "stfid": s["stfid"], "commonname": f"STAFF_NAME_{s['stfid']}", "position": pos, "grade": g,
            "exp": s["exp"], "gradeupto": STAFF_GRADEUP[g - 1] if g < STAFF_MAX_GRADE else 0, "life": s["life"],
            "status": s.get("status"), "material": not pools, "effect": [dict(e) for e in s["effect"]],
            **({"study": copy.deepcopy(s["study"])} if s.get("study") else {})}


def staff_slots(raw):
    """The staff _id in each role frame (None: empty)."""
    return (list(raw.get("staffslots") or []) + [None] * len(STAFF_SLOTS))[:len(STAFF_SLOTS)]


def staff_team(raw):
    """The staff fields of my coach.team: staffroles = one entry per role frame of role set 1
    (staffroleidx; nothing in the client switches sets), staffsmc = my matches played (a study is
    done matchcount of them after its mcdate)."""
    return {"staffs": [staff_out(s) for s in raw.get("staffs") or []], "staffroleidx": 1, "maxstaff": STAFF_MAX,
            "staffroles": [{"roleidx": 1, "slotidx": i + 1, "open": True, "staffid": sid}
                           for i, sid in enumerate(staff_slots(raw))],
            "staffsmc": int(raw.get("staffsmc") or 0)}


def staff_working(team):
    """The staffs (staff_out) of a team's role frames that work: appointed, with contract left."""
    by, out = {s["_id"]: s for s in team.get("staffs") or []}, {}
    for r in team.get("staffroles") or []:
        s = by.get(r.get("staffid"))
        if s and s.get("status") == "appointed" and s.get("life", 0) > 0:
            out[s["_id"]] = s
    return list(out.values())


def staff_stat(s):
    """{stat: boost} of a staff (CoachProxy.staffStatCheck)."""
    out = {}
    for e in s["effect"]:
        out[e["effect"]] = out.get(e["effect"], 0) + e["amount"]
    return out


def staff_match(team):
    """side.team staffroles + staffs of my match side: the working staff only, each with its stat;
    both lists the same length (native walks staffroles up to len(staffs))."""
    work = staff_working(team)
    return {"staffroles": [{"roleidx": 1, "slotidx": i + 1, "open": True, "staffid": s["_id"]} for i, s in enumerate(work)],
            "staffs": [dict(s, stat=staff_stat(s)) for s in work]}


def staff_static():
    """/static/db/STAFF, cached by the client from boot. Without `study` CoachProxy skips config
    (maxstaff stays 0); stafflist must hold every stfid once a staff exists (else the coach feed
    throws); Latihan predicts from exptable / materialexp / enhancefee / gradeexpect (staff_gain and
    ep_staff_enhance agree); a study place's location must be a frame of the Pembinaan map."""
    grades = range(1, STAFF_MAX_GRADE + 1)
    return {"config": {"max_staff_open_count": STAFF_OPEN_MAX, "max_staff_life": STAFF_LIFE, "default_staff_count": STAFF_MAX,
                       "max_staff_studyfee": staff_ep(STAFF_STUDY_FEE_MAX), "multiply_study_fee": STAFF_STUDY_FEE_X},
            "slots": {"1": [{"slot": i + 1, "position": p, "enabled": True, "lv": 0} for i, p in enumerate(STAFF_SLOTS)]},
            "stafflist": {str(i): {"material": not c[1], "enablemax": False, "expectid": c[2]} for i, c in STAFF_CATALOGUE.items()},
            "exptable": {str(g): {"totalexp": sum(STAFF_GRADEUP[:g - 1]), "gradeupto": (STAFF_GRADEUP + (0,))[g - 1]} for g in grades},
            "materialexp": {str(g): [n * g for n in STAFF_MATERIAL_EXP] for g in grades},
            "enhancefee": {str(g): {"fee": staff_ep(STAFF_ENHANCE_EP * g)} for g in grades},
            "gradeexpect": {str(e): {str(g): {"effects": list(v)} for g, v in t.items()} for e, t in STAFF_EXPECT.items()},
            "study": {lid: {"location": f"LOCATION_NAME_0{lid}", "grade": g, "fee": staff_ep(fee), "matchcount": n,
                            "position": list(pos), "boosttype": "enhance" if k is None else "change",
                            "effectstring": f"STAFF_STUDY_EXPECTATION0{4 if k is None else k + 1}"}
                      for lid, (k, pos, g, fee, n) in STAFF_STUDY.items()}}


def my_staff(st):
    """(my raw team, its staff list)."""
    team = st["teams"][str(_default_slot(st))]
    return team, team.setdefault("staffs", [])


def staff_find(staffs, sid):
    return next((s for s in staffs if s["_id"] == sid), None)


def staff_reply(st, **extra):
    """A staff change: the screens redraw from the /coach/me resource only."""
    return _json({"result": True, **extra, "resources": coach_me_resources(st, _default_slot(st))})


def staff_fail(reason):
    """reason is a language key the screens show (never DEFCON_ENTIRE_USER: a connection error)."""
    return _json({"result": False, "reason": reason})


def staff_free_state(team, now=None):
    """Today's free Rekrut pack {day, matches, claimed}, new at WIB midnight ("Reset setiap hari 0")."""
    if (team.get("staffrecruit") or {}).get("day") != today_wib(now):
        team["staffrecruit"] = {"day": today_wib(now), "matches": 0, "claimed": []}
    return team["staffrecruit"]


def ep_staff_recruit_list(query, body):
    """GET /staff/recruit: a top-level array; "free" rows show one line per eventlist key (an object:
    STAFF_FREEPACK_<KEY>_*), whose enable is the button."""
    st = _state()
    team = st["teams"].get(str(_default_slot(st)))
    if team is None:
        return _json([])
    f = staff_free_state(team)
    row = lambda rid, p, kind: {"recruitid": rid, "recruittype": kind, "icon": p[7],
                                "staffpack_name": f"STAFFPACK_NAME_{p[0]}", "staffpack_desc": f"STAFFPACK_DESC_{p[1]}"}
    n = min(f["matches"], STAFF_FREE_MATCHES)
    return _json([dict(row(rid, p, "normal"), ep=staff_ep(p[2]), item=0) for rid, p in STAFF_PACKS.items()] + [
        dict(row(STAFF_FREE_ID, STAFF_FREE_PACK, "free"), eventlist={
            "login": {"count": 1, "targetcount": 1, "enable": "login" not in f["claimed"]},
            "pcmatch": {"count": n, "targetcount": STAFF_FREE_MATCHES,
                        "enable": n >= STAFF_FREE_MATCHES and "pcmatch" not in f["claimed"]}})])


def ep_staff_recruit(rid, query, body):
    """POST /staff/recruit/<recruitid> {feetype: "ep"} or {eventtype: login | pcmatch}: one staff
    (the popup asks once per card), appended last: the popup shows staffs[length - 1] when the
    /coach/me resource comes, and stalls with no way out if a "result: true" brings none."""
    req = json.loads(body or b"{}")
    st = _state(); team, staffs = my_staff(st)
    if len(staffs) >= STAFF_MAX:
        return staff_fail("ITEM_INVENTORY_ERROR_STAFF_FULL_INFO")
    if int(rid) == STAFF_FREE_ID:
        f, k = staff_free_state(team), req.get("eventtype")
        if k not in ("login", "pcmatch") or k in f["claimed"] or (k == "pcmatch" and f["matches"] < STAFF_FREE_MATCHES):
            return staff_fail("STAFF_GET_FREEPACKCOMPLETE")
        f["claimed"].append(k)
        pack = STAFF_FREE_PACK
    else:
        pack = STAFF_PACKS.get(int(rid))
        if pack is None:
            return staff_fail("ITEM_USE_ERROR")
        if my_coach(st).get("ep", START_EP) < staff_ep(pack[2]):
            return staff_fail("STAFF_NOT_ENOUGH_GAMEMONEY")
        add_ep(st, -staff_ep(pack[2]))
    staffs.append(staff_new(pack))
    _save(st)
    return staff_reply(st)


def ep_staff_appoint(sid, query, body):
    """PUT /staff/appoint/<_id> {slot}: into role frame slot (1-based) of its position, taking the
    place of the one there; DELETE: out of its frame. The frames redraw from the resource only."""
    req = json.loads(body or b"{}")
    st = _state(); team, staffs = my_staff(st)
    s, slots = staff_find(staffs, sid), staff_slots(team)
    if s is None:
        return staff_fail("ITEM_USE_ERROR")
    if req.get("_method") == "DELETE":
        team["staffslots"], s["status"] = [None if x == sid else x for x in slots], None
        _save(st)
        return staff_reply(st, staffid=sid)
    i = int(req.get("slot") or 0) - 1
    if not 0 <= i < len(STAFF_SLOTS) or staff_helper(s) or s.get("status") == "studying" \
            or STAFF_CATALOGUE[s["stfid"]][0] != STAFF_SLOTS[i]:
        return staff_fail("ITEM_USE_ERROR")
    prev = staff_find(staffs, slots[i]) if slots[i] != sid else None
    slots = [None if x == sid else x for x in slots]
    if prev:
        prev["status"] = None
    slots[i], s["status"], team["staffslots"] = sid, "appointed", slots
    _save(st)
    who = lambda x: {"_id": x["_id"], "id": x["_id"], "status": x["status"]}
    return staff_reply(st, appointed={"appointedstaff": who(s), "prevstaff": prev and who(prev)})


def ep_staff_fire(sid, query, body):
    """DELETE /staff/<_id>: Hapus (offered for a staff with nothing to do only)."""
    req = json.loads(body or b"{}")
    st = _state(); _, staffs = my_staff(st)
    s = staff_find(staffs, sid)
    if req.get("_method") != "DELETE" or s is None or s.get("status"):
        return staff_fail("ITEM_USE_ERROR")
    staffs.remove(s)
    _save(st)
    return staff_reply(st)


def staff_contract_fee(s):
    return staff_ep(STAFF_CONTRACT_EP * s["grade"] * (STAFF_LIFE - s["life"]))


def ep_staff_recontract_preview(query, body):
    """PUT /staff/recontract/preview {staffs: [_id]}: each one's fee back to a full contract (the
    popup reads boost without a guard; no VIP or PC discount here). Also after Rank and quick
    matches, for staff with 5 matches or fewer left."""
    ids = set(json.loads(body or b"{}").get("staffs") or [])
    rows = [{"id": s["_id"], "base": staff_contract_fee(s), "availablematchcount": STAFF_LIFE - s["life"],
             "contractfee": staff_contract_fee(s)} for s in my_staff(_state())[1] if s["_id"] in ids]
    return _json({"result": True, "total": sum(r["contractfee"] for r in rows), "staffs": rows, "boostfactor": 0,
                  "boost": {"igr": 0, "vip": 0, "discountlimit": 0}})


def ep_staff_recontract(query, body):
    """PUT /staff/recontract {staffs: [_id]}: back to STAFF_LIFE matches, for their fees."""
    ids = set(json.loads(body or b"{}").get("staffs") or [])
    st = _state(); _, staffs = my_staff(st)
    picked = [s for s in staffs if s["_id"] in ids and s["life"] < STAFF_LIFE]
    fee = sum(staff_contract_fee(s) for s in picked)
    if not picked:
        return staff_fail("PLAYER_RECONTRACT_ERROR_POPUP_TITLE")
    if my_coach(st).get("ep", START_EP) < fee:
        return staff_fail("STAFF_NOT_ENOUGH_GAMEMONEY")
    add_ep(st, -fee)
    for s in picked:
        s["life"] = STAFF_LIFE
    _save(st)
    return staff_reply(st)


def staff_material_exp(target, m):
    """EXP material m gives target (the Latihan popup's sum: materialexp[grade][same position + 2 x helper])."""
    same = STAFF_CATALOGUE[m["stfid"]][0] == STAFF_CATALOGUE[target["stfid"]][0]
    return STAFF_MATERIAL_EXP[int(same) + 2 * int(staff_helper(m))] * m["grade"]


def ep_staff_enhance(tid, query, body):
    """PUT /staff/enhance/<target _id> {materials: [_id] x 1..10}: the materials (free staff, gone
    after) go into the target's EXP for enhancefee EP each. jackpot: bonus EXP on top of what the
    popup predicted (it plays its bonus scene, then shows the staff as the resource has it)."""
    req = json.loads(body or b"{}")
    st = _state(); _, staffs = my_staff(st)
    t = staff_find(staffs, tid)
    mats = [staff_find(staffs, m) for m in dict.fromkeys(req.get("materials") or [])]
    if t is None or staff_helper(t) or t.get("status") == "studying" or t["grade"] >= STAFF_MAX_GRADE \
            or not 1 <= len(mats) <= 10 or any(m is None or m is t or m.get("status") for m in mats):
        return staff_fail("STAFF_TRAINING_ERROR_POPUP_INFO")
    fee = sum(staff_ep(STAFF_ENHANCE_EP * m["grade"]) for m in mats)
    if my_coach(st).get("ep", START_EP) < fee:
        return staff_fail("STAFF_NOT_ENOUGH_GAMEMONEY")
    gain = sum(staff_material_exp(t, m) for m in mats)
    jackpot = random.random() < STAFF_JACKPOT[0]
    staff_gain(t, gain + (int(gain * STAFF_JACKPOT[1]) if jackpot else 0))
    for m in mats:
        staffs.remove(m)
    add_ep(st, -fee)
    _save(st)
    return staff_reply(st, jackpot=jackpot)


def staff_study_pool(s, k):
    """What a study may turn boost k of s into: that boost's stats over the staff of its position
    (OVR only for one whose own pool has it), less the one it has."""
    pos, pools, _ = STAFF_CATALOGUE[s["stfid"]]
    out = {x for c in STAFF_CATALOGUE.values() if c[0] == pos and c[1] for x in c[1][k]}
    out -= {s["effect"][k]["effect"]} | ({"ovr"} - set(pools[k]))
    return sorted(out)


def staff_study_fee(s, lid):
    """The client's sum: min(max_staff_studyfee, fee x multiply_study_fee ^ studies of s at lid)."""
    n = len(((s.get("study") or {}).get("history") or {}).get(lid, []))
    return min(staff_ep(STAFF_STUDY_FEE_MAX), staff_ep(STAFF_STUDY[lid][3]) * STAFF_STUDY_FEE_X ** n)


def ep_staff_study(lid, query, body):
    """PUT /staff/study/<lid> {staffs: [_id]}: a free staff goes to place lid (one at a time) for its
    matchcount matches; the result is rolled now (the tab reads study.boost before the return)."""
    req = json.loads(body or b"{}")
    st = _state(); team, staffs = my_staff(st)
    s, place = staff_find(staffs, (req.get("staffs") or [None])[0]), STAFF_STUDY.get(lid)
    if s is None or place is None or s.get("status") or staff_helper(s) or s["grade"] < place[2] \
            or STAFF_CATALOGUE[s["stfid"]][0] not in place[1] \
            or any(x.get("status") == "studying" and x["study"]["lid"] == lid for x in staffs):
        return staff_fail("STAFF_STUDY_DISABLE")
    k = place[0]
    if k is None:
        k = random.randrange(len(s["effect"]))
        boost = {"boosttype": "enhance", "effectidx": k, "inc": random.choices((1, 2, 3), (6, 3, 1))[0]}
    else:
        pool = staff_study_pool(s, k) if k < len(s["effect"]) else []
        if not pool:
            return staff_fail("STAFF_STUDY_DISABLE")
        boost = {"boosttype": "change", "effectidx": k, "effect": random.choice(pool)}
    fee = staff_study_fee(s, lid)
    if my_coach(st).get("ep", START_EP) < fee:
        return staff_fail("STAFF_EP_NOTENOUGH")
    add_ep(st, -fee)
    study = s.setdefault("study", {})
    # prevboost: the boost last applied (green while away); never missing for a staff away
    study.update(lid=lid, mcdate=int(team.get("staffsmc") or 0), prevboost=study.get("boost") or {"effectidx": -1}, boost=boost)
    study.setdefault("history", {}).setdefault(lid, []).append(w3c(time.time()))
    s["status"] = "studying"
    _save(st)
    return staff_reply(st)


def ep_staff_study_complete(lid, query, body):
    """PUT /staff/study/<lid>/complete: back from place lid once its matches are played, the boost
    applied (Europe's + replaces the last one Europe gave: "boost sebelumnya akan di reset"). The
    return popup reads staffs as {_id: staff}."""
    st = _state(); team, staffs = my_staff(st)
    s = next((x for x in staffs if x.get("status") == "studying" and x["study"]["lid"] == lid), None)
    if s is None or int(team.get("staffsmc") or 0) - s["study"]["mcdate"] < STAFF_STUDY[lid][4]:
        return staff_fail("STAFF_STUDY_DISABLE")
    study = s["study"]; b = study["boost"]
    if b["boosttype"] == "change":
        s["effect"][b["effectidx"]]["effect"] = b["effect"]
    else:
        old = study.pop("bonus", None)
        if old:
            s["effect"][old["effectidx"]]["amount"] -= old["inc"]
        s["effect"][b["effectidx"]]["amount"] += b["inc"]
        study["bonus"] = {"effectidx": b["effectidx"], "inc": b["inc"]}
    s["status"] = None
    _save(st)
    return staff_reply(st, staffs={s["_id"]: staff_out(s)})


def staff_after_match(st):
    """A match of mine played to the end (no simulations, as the free pack's rule says): the study
    counter moves on, each staff in a role frame plays a match of its contract, the free pack counts it."""
    team = st["teams"].get(str(_default_slot(st)))
    if team is None:
        return
    team["staffsmc"] = int(team.get("staffsmc") or 0) + 1
    for s in team.get("staffs") or []:
        if s.get("status") == "appointed":
            s["life"] = max(0, s["life"] - 1)
    staff_free_state(team)["matches"] += 1


def bag_reply(st):
    return _json({"result": True, "overdueitems": [],
                  "resources": coach_me_resources(st, _default_slot(st)) + [{"uri": "/bag", "body": bag_list(st)}]})


def ep_bag_unpack(bid, query, body):
    """POST /bag/<id>/unpack: take one bag entry."""
    st = _state()
    entry = next((e for e in my_coach(st).get("bag", []) if e["_id"] == bid), None)
    if entry is None:
        return _json({"result": False, "reason": "ITEM_USE_ERROR"})
    if len(st["teams"][str(_default_slot(st))]["players"]) + len(entry["players"]) > MAX_PLAYERS:
        return _json({"result": False, "reason": "TRANSFERMARKET_FULLSLOT_DESC"})
    bag_apply(st, entry)
    _save(st)
    return bag_reply(st)


def ep_bag_unpackall(query, body):
    """POST /bag/unpackall {reqUnpackItems: [id...]}: take them all (club slots permitting)."""
    ids = set(map(str, json.loads(body or b"{}").get("reqUnpackItems") or []))
    st = _state(); team = st["teams"][str(_default_slot(st))]
    for e in [e for e in my_coach(st).get("bag", []) if e["_id"] in ids]:
        if len(team["players"]) + len(e["players"]) <= MAX_PLAYERS:
            bag_apply(st, e)
    _save(st)
    return bag_reply(st)


# --------------------------------------------------------------------------- #
# Missions (issue #33: "the Mission section is completely empty"). The client has two mission
# systems of its own and reports nothing to them, so the server counts my play (coach
# ["missions"]: matches, wins, goals, Rank matches and wins, upgrades; per WIB day, week and in
# all) and feeds both from GET /achievement/me {summary, achievement, quest} (AchievementProxy
# reads it by path; a cached resource, so the replies after a match or an upgrade carry it):
#  * Manager > Daftar pencapaian (CoachAchievementListTabPage): a row per badge group of up to 5
#    achievements of up to 5 grades. GET /static/info/ACHIEVEMENT {list: defs, mixed: {group:
#    [ids]}}; a def {id (an int), name, shortdesc / longdesc ({0} = the target), difficulty
#    (stars), rewards [{todo, reward [{itemid, count}]}]}; the group's own def (id = the group)
#    names its row. GET /static/db/BADGES [{itemid, category, itemicon, itemback, asset: group,
#    image_effect}] lists a row ("misc" has no badge: the client adds that row itself). GET
#    /static/db/ITEMNAMES {itemid: {<language>: name}}: a reward with no name throws. Progress entries:
#    {id, aon: true (else 0 shows), complete, progress {grade from 1, detail {current, total}},
#    rewards {available, grade}}; a row stops drawing at an id without one. Once a badge is earned,
#    its group's {id, complete, completedat} too (the panel lights and dates a badge that has one). POST /achievement/<id>/reward/<grade> pays a grade; a group all paid
#    earns its badge (coach badges; POST /badge/<itemid>/use wears it, 0 none). The misc row holds
#    the daily missions, reset at 00:00 WIB.
#  * The Quest window (AchievementQuestWindow, "Quest Mingguan"; features.freeroambuttons.quest
#    adds its menu button): GET /static/info/request {expire [current's, next's], current [{name, todo,
#    reward {count}}], next [...]} = this week's quests (until Monday 00:00 WIB) and next week's,
#    the client switching itself at the reset; progress: /achievement/me quest [{id = index,
#    progress {detail {current}}, completedat}]: current == todo with a newer completedat lights
#    its button and posts "quest completed". A done quest pays GP (coach gpdata {point, until}; it
#    lapses at the reset, CoachProxy.clearGP) for the window's reward boxes (GET /shop/list/GPSHOP,
#    up to 5; /shop/purchase charges GP for them).
# The admin page's Misi tab (gamedata/missions.json): on/off, the texts' language, a multiplier on
# the targets and one on the rewards; EP rewards also follow the Ekonomi tab's player prices. Its
# weekly quest editor (user: "tambah editor untuk quest mingguan di admin panel"): the quests a week
# draws from (counter, target, GP, an own name), how many a week, the reward boxes (item, GP).
# --------------------------------------------------------------------------- #
MISSIONS_FILE = os.path.join(HERE, "gamedata", "missions.json")
MISSION_LANGS = {"id": "Bahasa Indonesia", "en": "English", "vi": "Tiếng Việt"}
# (id, group, counter or "level" / "grade" / "value" (from the coach and the club now), targets
# of its grades, stars). Daily ones: one grade, counted today
ACHIEVEMENTS = ((101, "lv", "level", (10, 20, 40, 70, 100), 2), (102, "lv", "match", (10, 50, 150, 400, 1000), 2),
                (103, "lv", "win", (5, 25, 75, 200, 500), 3), (201, "upg", "upgrade", (1, 5, 20, 50, 100), 2),
                (202, "upg", "grade", (3, 4, 5, 6, 7), 4), (301, "tv", "value", (10 ** 7, 10 ** 8, 10 ** 9, 10 ** 10, 10 ** 11), 3),
                (401, "gol", "goal", (10, 50, 150, 400, 1000), 2), (402, "gol", "rankwin", (1, 10, 30, 75, 150), 3))
DAILY = ((901, "match", 3, 30_000), (902, "win", 2, 40_000), (903, "goal", 5, 30_000), (904, "rank", 1, 20_000))
GRADE_REWARDS = ((50_000, None), (100_000, 501), (250_000, 28007), (500_000, 8223601), (1_000_000, 50122))  # EP, item
BADGE_GROUPS = {"lv": (900046, 1, 2), "upg": (900021, 1, 4), "tv": (900038, 1, 3), "gol": (900039, 1, 3)}  # badge, effect, stars
QUESTS = (("match", 10, 30), ("win", 6, 30), ("goal", 15, 25), ("upgrade", 3, 20), ("rank", 5, 25), ("rankwin", 3, 30),
          ("match", 20, 50), ("goal", 30, 45))  # (counter, target, GP): QUEST_COUNT of them a week
QUEST_COUNT = 5
GP_SHOP = ((50021, 40), (501, 20), (8223601, 50), (1000053, 30), (50122, 60))  # (itemid, GP): the reward boxes
GP_SHOPID = 920_000_000
QUEST_STATS = ("match", "win", "goal", "rank", "rankwin", "upgrade")  # the counters mission_event keeps a week
QUEST_MAX, GP_SHOP_MAX = 6, 5  # the window's quest list shows 6; it has 5 reward boxes (REWARD_BOX_COUNT)
MISSIONS_DEFAULT = {"enabled": True, "lang": "id", "targets": 1.0, "rewards": 1.0,
                    "quests": [{"stat": s, "todo": t, "gp": g, "name": ""} for s, t, g in QUESTS],
                    "questcount": QUEST_COUNT, "gpshop": [{"itemid": i, "gp": g} for i, g in GP_SHOP]}
MISSION_TEXT = {  # counter: {lang: (name, the goal ({0} = target), the goal said in full)}
    "level": {"id": ("Level manajer", "Capai level {0}", "Naikkan level manajer sampai level {0}."),
              "en": ("Manager level", "Reach level {0}", "Raise your manager to level {0}."),
              "vi": ("Cấp HLV", "Đạt cấp {0}", "Nâng HLV lên cấp {0}.")},
    "match": {"id": ("Pertandingan", "Main {0} pertandingan", "Mainkan {0} pertandingan di Liga, Cup atau Rank."),
              "en": ("Matches", "Play {0} matches", "Play {0} matches in League, Cup or Rank."),
              "vi": ("Trận đấu", "Chơi {0} trận", "Chơi {0} trận ở Giải đấu, Cúp hoặc Rank.")},
    "win": {"id": ("Kemenangan", "Menangi {0} pertandingan", "Menangkan {0} pertandingan."),
            "en": ("Wins", "Win {0} matches", "Win {0} matches."), "vi": ("Chiến thắng", "Thắng {0} trận", "Thắng {0} trận.")},
    "goal": {"id": ("Gol", "Cetak {0} gol", "Cetak {0} gol di pertandingan yang kamu mainkan."),
             "en": ("Goals", "Score {0} goals", "Score {0} goals in the matches you play."),
             "vi": ("Bàn thắng", "Ghi {0} bàn", "Ghi {0} bàn trong các trận bạn chơi.")},
    "rank": {"id": ("Rank", "Main {0} pertandingan Rank", "Mainkan {0} pertandingan Rank."),
             "en": ("Rank", "Play {0} Rank matches", "Play {0} Rank matches."), "vi": ("Rank", "Chơi {0} trận Rank", "Chơi {0} trận Rank.")},
    "rankwin": {"id": ("Kemenangan Rank", "Menang {0} kali di Rank", "Menangkan {0} pertandingan Rank."),
                "en": ("Rank wins", "Win {0} Rank matches", "Win {0} Rank matches."),
                "vi": ("Thắng Rank", "Thắng {0} trận Rank", "Thắng {0} trận Rank.")},
    "upgrade": {"id": ("Upgrade berhasil", "Upgrade pemain {0} kali", "Berhasil meng-upgrade pemain {0} kali."),
                "en": ("Successful upgrades", "Upgrade players {0} times", "Upgrade players successfully {0} times."),
                "vi": ("Nâng cấp thành công", "Nâng cấp cầu thủ {0} lần", "Nâng cấp cầu thủ thành công {0} lần.")},
    "grade": {"id": ("Pemain berkelas", "Punya pemain +{0}", "Miliki pemain dengan grade +{0} atau lebih."),
              "en": ("Top grade", "Own a +{0} player", "Own a player of grade +{0} or higher."),
              "vi": ("Thẻ cấp cao", "Sở hữu cầu thủ +{0}", "Sở hữu cầu thủ cấp +{0} trở lên.")},
    "value": {"id": ("Nilai tim", "Nilai tim {0} EP", "Naikkan nilai Tim inti sampai {0} EP."),
              "en": ("Team value", "Team value {0} EP", "Raise your first team's value to {0} EP."),
              "vi": ("Giá trị đội", "Giá trị đội {0} EP", "Nâng giá trị đội hình chính lên {0} EP.")},
}
MISSION_WORDS = {  # lang: (group names, "Daily", "today", the reset note)
    "id": ({"lv": "Manajer", "upg": "Upgrade", "tv": "Nilai tim", "gol": "Gol", "misc": "Misi harian"},
           "Harian", "hari ini", "Direset tiap hari pukul 00:00 WIB."),
    "en": ({"lv": "Manager", "upg": "Upgrades", "tv": "Team value", "gol": "Goals", "misc": "Daily missions"},
           "Daily", "today", "Resets every day at 00:00 WIB."),
    "vi": ({"lv": "HLV", "upg": "Nâng cấp", "tv": "Giá trị đội", "gol": "Bàn thắng", "misc": "Nhiệm vụ hằng ngày"},
           "Hằng ngày", "hôm nay", "Làm mới mỗi ngày lúc 00:00 WIB."),
}


def missions_cfg():
    try:
        with open(MISSIONS_FILE, encoding="utf-8") as f:
            cfg = {**MISSIONS_DEFAULT, **json.load(f)}
    except (OSError, ValueError):
        return dict(MISSIONS_DEFAULT)
    return cfg if cfg["lang"] in MISSION_LANGS else {**cfg, "lang": MISSIONS_DEFAULT["lang"]}


def week_end(now=None):
    """This mission week's end, Monday 00:00 WIB (epoch s)."""
    t = time.gmtime((time.time() if now is None else now) + WIB)
    return calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0)) + (7 - t.tm_wday) * 86400 - WIB


def mission_target(n, cfg):
    return max(1, round(n * cfg["targets"]))


def mission_ep(n, cfg):
    return nice_price(n * cfg["rewards"] * ECONOMY["players"])


def mission_defs(cfg):
    """[(def for /static/info/ACHIEVEMENT, group, counter, daily)] in the admin page's language."""
    lang = cfg["lang"]
    groups, daily_word, today, reset = MISSION_WORDS[lang]
    out = []
    for aid, group, stat, todos, stars in ACHIEVEMENTS:
        name, short, long = MISSION_TEXT[stat][lang]
        todos = [mission_target(t, cfg) if stat not in ("level", "grade") else t for t in todos]
        rewards = [{"todo": t, "reward": [{"itemid": EP_ITEM, "count": mission_ep(ep, cfg)}]
                    + ([{"itemid": item, "count": 1}] if item else [])} for t, (ep, item) in zip(todos, GRADE_REWARDS)]
        out.append(({"id": aid, "name": name, "shortdesc": short, "longdesc": long, "difficulty": stars, "rewards": rewards},
                    group, stat, False))
    for aid, stat, todo, ep in DAILY:
        name, short, long = MISSION_TEXT[stat][lang]
        out.append(({"id": aid, "name": f"{daily_word}: {name}", "shortdesc": f"{short} {today}",
                     "longdesc": f"{short} {today}. {reset}", "difficulty": 1,
                     "rewards": [{"todo": mission_target(todo, cfg), "reward": [{"itemid": EP_ITEM, "count": mission_ep(ep, cfg)}]}]},
                    "misc", stat, True))
    return out


def week_quests(cfg, week_end_ts):
    """The week's quests: [(name, counter, target, GP)], the same draw for the same week and list.
    A quest's own name may say its target as {0}."""
    out, pool = [], cfg["quests"]
    for q in random.Random(f"quests/{week_end_ts}").sample(pool, min(cfg["questcount"], len(pool))):
        todo = mission_target(q["todo"], cfg)
        name = (q.get("name") or MISSION_TEXT[q["stat"]][cfg["lang"]][1]).replace("{0}", f"{todo:,}")
        out.append((name, q["stat"], todo, max(1, round(q["gp"] * cfg["rewards"]))))
    return out


def quest_key(stat, todo):
    """A done quest in coach missions.quests: its counter and target (an edited list keeps it)."""
    return f"{stat}:{todo}"


def mission_gp(coach):
    m = coach.get("missions", {})
    return m.get("gp", 0) if m.get("week") == week_end() else 0


def missions_state(st, now=None):
    """My mission counters, turned over to today (WIB) and this week."""
    now = time.time() if now is None else now
    m = my_coach(st).setdefault("missions", {})
    day, week = today_wib(now), week_end(now)
    if m.get("day") != day:
        m.update(day=day, daily={}, dailyclaimed=[])
    if m.get("week") != week:
        m.update(week=week, weekly={}, quests={}, gp=0)
    if any(k.isdigit() for k in m["quests"]):  # by its place in the week's list, before the quest editor
        qs = week_quests(missions_cfg(), week)
        m["quests"] = {quest_key(*qs[int(k)][1:3]) if k.isdigit() and int(k) < len(qs) else k: v
                       for k, v in m["quests"].items()}
    for k, v in (("total", {}), ("claimed", {}), ("groups", {}), ("badges", []), ("currentbadge", 0)):
        m.setdefault(k, v)
    return m


def mission_event(st, now=None, **counts):
    """Count my play (match, win, goal, rank, rankwin, upgrade); a quest just done pays its GP."""
    cfg = missions_cfg()
    if not cfg["enabled"]:
        return
    now = time.time() if now is None else now
    m = missions_state(st, now)
    for k, n in counts.items():
        for scope in ("daily", "weekly", "total"):
            m[scope][k] = m[scope].get(k, 0) + int(n)
    for _, stat, todo, gp in week_quests(cfg, m["week"]):
        if quest_key(stat, todo) not in m["quests"] and m["weekly"].get(stat, 0) >= todo:
            m["quests"][quest_key(stat, todo)] = w3c(now)
            m["gp"] += gp


def mission_value(st, m, stat, daily):
    if stat == "level":
        return coach_levelinfo(my_coach(st))["level"]
    team = st["teams"].get(str(_default_slot(st))) or {"players": [], "squad": {"positions": [], "kickers": []}}
    if stat == "grade":
        return max((grade_of(p) for p in team["players"]), default=0)
    if stat == "value":
        return first_team_value(owned_team(team))
    return m["daily" if daily else "total"].get(stat, 0)


def achievement_me(st, now=None):
    """GET /achievement/me: my achievements, daily missions and this week's quests."""
    cfg = missions_cfg()
    if not cfg["enabled"]:
        return {"summary": {"done": 0, "total": 0, "rewardable": 0}, "achievement": [], "quest": []}
    m = missions_state(st, now)
    rows, groups = [], {}
    for d, group, stat, daily in mission_defs(cfg):
        todos = [r["todo"] for r in d["rewards"]]
        paid = int(str(d["id"]) in m["dailyclaimed"]) if daily else m["claimed"].get(str(d["id"]), 0)
        grade, cur = min(paid + 1, len(todos)), mission_value(st, m, stat, daily)
        rows.append({"id": d["id"], "aon": True, "complete": paid >= len(todos),
                     "progress": {"grade": grade, "detail": {"current": min(cur, todos[grade - 1]), "total": todos[grade - 1]}},
                     "rewards": {"available": paid < len(todos) and cur >= todos[paid], "grade": paid}})
        groups.setdefault(group, []).append(paid >= len(todos))
    done = sum(r["complete"] for r in rows)
    # a group's own entry only once its badge is earned: the panel lights the badge and shows the
    # date whenever there is one (none was 1.1.1970 on an unlit row)
    out = rows + [{"id": g, "complete": True, "completedat": m["groups"][g]} for g in groups if g in m["groups"]]
    quests = [{"id": i, "progress": {"detail": {"current": min(m["weekly"].get(stat, 0), todo)}},
               **({"completedat": m["quests"][quest_key(stat, todo)]} if quest_key(stat, todo) in m["quests"] else {})}
              for i, (_, stat, todo, _) in enumerate(week_quests(cfg, m["week"]))]
    return {"summary": {"done": done, "total": len(rows), "rewardable": sum(r["rewards"]["available"] for r in rows)},
            "achievement": out, "quest": quests}


def achievement_resources(st):
    return [{"uri": "/achievement/me", "body": achievement_me(st)}] if missions_cfg()["enabled"] else []


def ep_achievement_me(query, body):
    st = _state()
    out = achievement_me(st)
    _save(st)  # the day / week turned over
    return _json(out)


def ep_achievement_other(cid, query, body):
    """GET /achievement/<another manager>: his badge popup; the virtual managers have none."""
    return _json({"summary": {"done": 0, "total": 0, "rewardable": 0}, "achievement": []})


def ep_static_achievement(query, body):
    cfg = missions_cfg()
    if not cfg["enabled"]:
        return _json({"list": [], "mixed": {}})
    defs, groups = mission_defs(cfg), MISSION_WORDS[cfg["lang"]][0]
    mixed = {}
    for d, group, _, _ in defs:
        mixed.setdefault(group, []).append(d["id"])
    stars = {g: v[2] for g, v in BADGE_GROUPS.items()}
    return _json({"list": [d for d, _, _, _ in defs] + [{"id": g, "name": groups[g], "difficulty": stars.get(g, 1)} for g in mixed],
                  "mixed": mixed})


def ep_badges(query, body):
    return _json([{"itemid": b, "category": "BADGE", "itemicon": b, "itemback": "defaultblack", "asset": g, "image_effect": fx}
                  for g, (b, fx, _) in BADGE_GROUPS.items()])


def ep_itemnames(query, body):
    """GET /static/db/ITEMNAMES {itemid: {<language>: name}}: the reward panels' (and Upgrade's) item
    names. The client reads name[Localization.LOCALE] (= ep_locale's language), so every language
    code gets one (issue #36: the Vietnamese pack's "vie" showed "undefined" and a UI error)."""
    ids = [EP_ITEM, ES_ITEM, *PACKS, *ITEMS, *PACKAGES, *MONTHLY]
    return _json({str(i): dict.fromkeys(("kor", "eng", "chi", "vie", "tha", "ind", "jpn"), item_def(i)["name"]) for i in ids})


def ep_quest_bank(query, body):
    """GET /static/info/request: this week's quests and next week's. expire is [this week's end,
    next week's] (AchievementProxy.checkCurrentQuestAndFeed reads expire[i] for current / next)."""
    cfg, end = missions_cfg(), week_end()
    expire = [w3c(end), w3c(end + 7 * 86400)]
    if not cfg["enabled"]:
        return _json({"expire": expire, "current": [], "next": []})
    bank = lambda e: [{"name": name, "todo": todo, "reward": {"count": gp}, "platform": 1}
                      for name, _, todo, gp in week_quests(cfg, e)]
    return _json({"expire": expire, "current": bank(end), "next": bank(end + 7 * 86400)})


def gp_shop_prices():
    """{itemid: GP} of the Quest window's reward boxes (the admin page's list)."""
    return {b["itemid"]: b["gp"] for b in missions_cfg()["gpshop"]}


def gp_shop_list():
    """The Quest window's reward boxes: Shop entries priced in GP."""
    out = []
    for itemid, gp in gp_shop_prices().items():
        e = shop_entry(itemid, "GPSHOP", GP_SHOPID + itemid)
        e["product"]["staticdata"]["bunches"] = [{"bunch": 1, "price": gp}]
        out.append(e)
    return out


def ep_achievement_reward(aid, grade, query, body):
    """POST /achievement/<id>/reward/<grade>: pay a grade that is done."""
    st = _state(); cfg = missions_cfg(); slot = _default_slot(st)
    me = achievement_me(st)
    row = next((r for r in me["achievement"] if str(r["id"]) == aid), None)
    found = next(((d, group, daily) for d, group, _, daily in mission_defs(cfg) if str(d["id"]) == aid), None)
    if not cfg["enabled"] or row is None or found is None or int(grade) != row["rewards"]["grade"] + 1 \
            or not row["rewards"]["available"]:
        return _json({"result": False, "reason": "ACHIEVEMENT_REWARDS_ERROR"})
    d, group, daily = found
    m, coach = missions_state(st), my_coach(st)
    reward = d["rewards"][row["rewards"]["grade"]]["reward"]
    for r in reward:
        if r["itemid"] == EP_ITEM:
            add_ep(st, r["count"])
        else:
            inventory_add(coach, r["itemid"], r["count"])
    if daily:
        m["dailyclaimed"].append(aid)
    else:
        m["claimed"][aid] = row["rewards"]["grade"] + 1
    after = achievement_me(st)
    ids = {d["id"] for d, g, _, _ in mission_defs(cfg) if g == group}
    if all(r["complete"] for r in after["achievement"] if r["id"] in ids) and group in BADGE_GROUPS \
            and BADGE_GROUPS[group][0] not in m["badges"]:
        m["groups"][group] = w3c(time.time())  # the badge screen shows after the popup
        m["badges"].append(BADGE_GROUPS[group][0])
        after = achievement_me(st)
    _save(st)
    return _json({"result": True, "rewards": [{**r, "staticdata": item_def(r["itemid"])} for r in reward],
                  "resources": [{"uri": "/achievement/me", "body": after}] + coach_me_resources(st, slot)})


def ep_badge_use(itemid, query, body):
    """POST /badge/<itemid>/use: wear an earned badge (0 = none)."""
    st = _state()
    m = missions_state(st)
    if int(itemid) and int(itemid) not in m["badges"]:
        return _json({"result": False, "reason": "INVALID_PARAM"})
    m["currentbadge"] = int(itemid)
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, _default_slot(st))})


# --------------------------------------------------------------------------- #
# Virtual managers (issue #30, stage 1): a fixed crowd of generated managers for Multiple > Rank
# (LadderMatchController in data/ui/libs/Module.big), which had no other players. Each one has a
# name, a level, a club the club picker lists, a card grade by tier (+1 beginners, +2/+3
# intermediate, +4..+6 advanced) and a ladder rating per mode; one seed, so the same crowd every
# run. Matches against them are stage 2. Shapes:
#   /ladder/leaderboard  [ladder index: [{name, elo, rank}]] (the front window shows the top 3)
#   /ladder/friends/info [{_id, name, teamid, ovr {FW, MF, DF}, elo [i], wins [i], losses [i],
#                         forfeits [i]}], i = ladder index (rebuildLadderRecordFriendsInfo)
#   /ladder/my/info      {start, next, ladder_<mode> records, ladder_total}: this season (ladder_my_info)
#   /ladder/season/info  {sid, prevsid}; /coach/me statistics.ladder {seasons}
# The divisions (/ladder/division/info: {name, rating}, highest first; 1050 = Amateur B, +50 a step
# to Legendary A at 1700) is a responses/ file; the rewards (/ladder/reward/info: divs [{name
# DIVISION_<div>, items [main, sub]}], wins [{from, items}]) are ladder_rewards() (see there).
# --------------------------------------------------------------------------- #
VM_COUNT, VM_SEED, VM_FRIENDS = 100, 30, 20
LADDER_COUNT = 6  # covers every ladder index (Enums.LADDER_MATCH_COUNT and 3vs3 roleplay)
VM_TIERS = (  # (share, card grade range, level range, ladder rating range)
    (0.50, (1, 1), (3, 30), (1000, 1250)),  # beginners: Amateur to Semi-Pro
    (0.35, (2, 3), (25, 60), (1200, 1500)),  # intermediate: Semi-Pro to World class
    (0.15, (4, 6), (50, 100), (1450, 1780)),  # advanced: World class and Legendary
)
VM_WORDS = ("Garuda", "Macan", "Rajawali", "Elang", "Badai", "Petir", "Naga", "Banteng", "Kopi", "Sambal",
            "Bintang", "Komodo", "Tiger", "Wolf", "Shadow", "Ninja", "Samurai", "Falcon", "Viper", "Storm",
            "Phoenix", "Rocket", "Panda", "Cobra", "Lotus", "Mango", "Pho", "Siam", "Mekong", "Saigon",
            "Hanoi", "Bangkok", "Jakarta", "Bandung", "Medan", "Kancil", "Kucing", "Gajah", "Singa", "Hiu")
VM_TAGS = ("FC", "10", "99", "07", "Gol", "Striker", "Juara", "Master", "Pro", "King", "77", "GG", "_x",
           "88", "23", "Captain", "Keeper", "Boss", "Legend", "Jr")


# The crowd lives on (issue #30, stage 3; user: "manajer virtual bisa bertambah, skuad bisa
# berubah, bisa ada upgrade atau downgrade grade, random level pemain"). Its calendar starts at
# VM_EPOCH (day 0 = the first crowd, vm_base); from day 1 newcomers join (vm_newcomers) and every
# manager's squad changes day by day (vm_team). All of it is seeded by manager and day: nothing
# is stored, every server run sees the same crowd on the same day.
VM_EPOCH = calendar.timegm((2026, 11, 1, 0, 0, 0))
# The crowd's generation (user: "Di admin panel tambah untuk generate ulang virtual manager"): the
# admin page's Generate ulang counts it up (VM_GEN_FILE {"gen": n}); every crowd seed takes it in
# (vmseed), so names, tiers, clubs, squads, styles, ratings, regions and the AI clubs are new while
# the ids stay (what the save refers to by id keeps working). 0 = the crowd as it always was; it
# counts from the server's next start.
VM_GEN_FILE = os.path.join(HERE, "gamedata", "vm_gen.json")


def vm_generation():
    try:
        with open(VM_GEN_FILE, encoding="utf-8") as f:
            return max(0, int(json.load(f).get("gen") or 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


VM_GEN = vm_generation()  # the crowd in use: the saved one, changed only by the admin page


def vmseed(s):
    return s if not VM_GEN else f"{s}#g{VM_GEN}"


def vm_set_generation(gen):
    """Save the crowd's generation and use it at once: every crowd cache is dropped."""
    global VM_GEN
    os.makedirs(os.path.dirname(VM_GEN_FILE), exist_ok=True)
    with open(VM_GEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"gen": gen}, f)
    VM_GEN = gen
    for f in (vm_crowd, vm_skill, vm_thirds, vm_base, vm_extra, vm_newcomers, vm_theme, vm_squad, vm_ratings,
              vm_value, club_vm_points, ai_clubs):
        f.cache_clear()
    VM_SEASON.clear()
VM_MAX = 2000  # the crowd stops growing here (user: "max player ai online 1000"; issue #36: doubled)
# The rest of the population beside the first crowd (VM_EXTRA): there from day 0 with this
# season's record so far, like the first crowd, so the rankings and the Rank searches hold some
# 2,000 managers (two groups) while the first crowd (the friends) and the newcomers keep their ids and names.
# Ids from vm2000; names from VM_WORDS2 x VM_TAGS2 (950, none of the first pool's, none holding a
# word the chat overlay masks: "Captain" has "tai").
VM_EXTRA, VM_EXTRA_ID = 850, 2000
VM_WORDS2 = ("Bromo", "Rinjani", "Toba", "Lombok", "Sumba", "Flores", "Ambon", "Borneo", "Celebes", "Danang",
             "Halong", "Dalat", "Manila", "Penang", "Malaka", "Johor", "Sabah", "Brunei", "Yangon", "Lion",
             "Eagle", "Hawk", "Bear", "Shark", "Dragon", "Thunder", "Blaze", "Comet", "Meteor", "Nova",
             "Orion", "Zeus", "Apollo", "Kaiser", "Sultan", "Pendekar", "Satria", "Jagoan", "Surya", "Arjuna",
             "Badak", "Buaya", "Merpati", "Pinisi", "Keris", "Topan", "Gelora", "Senayan", "Mustang", "Jaguar")
VM_TAGS2 = tuple(t for t in VM_TAGS if t != "Captain")
# A second group of the population (issue #36: "Could you consider doubling the number of AI virtual
# players"): VM_EXTRA2 more from vm4000, named from VM_WORDS3 x VM_TAGS2 without the pairs the chat
# overlay masks (FORBIDDENWORDS_CHAT_ENG/IND: VM_NAMES3_MASKED); the first group keeps its ids and names.
VM_EXTRA2, VM_EXTRA2_ID = 1000, 4000
VM_WORDS3 = ("Merapi", "Semeru", "Kerinci", "Batur", "Bunaken", "Lawu", "Cebu", "Davao", "Luzon", "Hue", "Nhatrang",
             "Vungtau", "Cantho", "Haiphong", "Krabi", "Chiangmai", "Ubud", "Tidore", "Ternate", "Banda", "Natuna",
             "Krakatau", "Jambi", "Riau", "Palu", "Kendari", "Sorong", "Merauke", "Kupang", "Sumbawa", "Wakatobi",
             "Cakra", "Sakti", "Gemilang", "Kilat", "Rimau", "Harimau", "Kijang", "Rusa", "Cendrawasih", "Kakatua",
             "Nuri", "Camar", "Marlin", "Barracuda", "Orca", "Puma", "Lynx", "Raven", "Condor", "Atlas", "Vortex",
             "Turbo", "Sniper", "Maestro", "Rebel", "Spartan", "Viking", "Gladiator", "Ronin", "Shogun", "Mamba",
             "Raptor", "Hydra", "Kraken", "Zenith", "Nebula", "Galaxy", "Cosmos", "Pulsar")
VM_NAMES3_MASKED = {"MerapiStriker", "SemeruKing", "LawuKing", "CebuKing", "VungtauKing", "CanthoJuara", "CanthoJr",
                    "KrakatauKing", "RiauKing", "PaluKing", "KendariLegend", "MeraukeBoss", "RimauKing", "HarimauKing",
                    "NuriLegend", "AtlasStriker"}
VM_JOIN = (0, 1, 1, 2, 3)  # managers who join on one day (one is drawn)
VM_JOIN_TIER = (0.7, 0.25, 0.05)  # a newcomer's tier odds: mostly beginners
VM_LIFE = (0.12, 0.08, 0.03)  # a day's odds: a transfer, an upgrade try, a starter's level up


def vm_day(t=None):
    """The crowd's day number now (or at t): whole UTC days since VM_EPOCH."""
    return (int(time.time() if t is None else t) - VM_EPOCH) // 86400


def virtual_managers():
    """The crowd now: [{_id, name, level, teamid, grade, elo [i], wins [i], losses [i],
    forfeits [i]}], the first crowd strongest tier last, the rest of the population, then the
    newcomers (vm_base, vm_extra and vm_newcomers, then this season's games so far: vm_crowd).
    One clock read: a call across midnight on the 1st must not pair next month with this month's
    last day."""
    now = time.time()
    t = time.gmtime(now)
    return vm_crowd(ladder_season(now)[0], t.tm_mday, t.tm_hour)


VM_GAMES = (0, 0, 1, 1, 2, 3)  # Rank games a manager plays on one ladder in a day (one is drawn)
# the hours (UTC) a manager plays or joins, one drawn: 01..19 = 08:00..02:00 WIB, the evening
# (16:00..23:00 WIB) twice as busy; nobody plays 03:00..07:00 WIB
VM_HOURS = tuple(range(1, 20)) + tuple(range(9, 17))


@functools.lru_cache(maxsize=2)
def vm_crowd(sid, day, hour):
    """The crowd at hour `hour` (UTC) of day `day` of season sid: every manager plays a few Rank
    games a day on each ladder, at seeded hours, against players near his rating, and wins by the
    Elo odds tilted by his own skill (+-60), so the ratings, the records and the rankings move
    through the day. A newcomer is there from the hour he joined and plays from then on; one who
    joined in an earlier season starts this one at his first rating with no games. Seeded by
    manager, season and day: nothing is stored, the same hour always gives the same crowd. The
    days before today come from vm_crowd_start; an hour plays only today's games."""
    first = vm_season_first(sid)
    today = first + day - 1
    joined = {m["_id"]: (jd, jh) for jd, jh, m in vm_newcomers(today)}
    out = copy.deepcopy(vm_crowd_start(sid, day) + [m for jd, jh, m in vm_newcomers(today) if jd == today and jh <= hour])
    for vm in out:
        vm_play(vm, sid, day, first, joined.get(vm["_id"]), hour)
    return out


VM_SEASON = {}  # "crowd": (sid, day, the crowd at the start of that season day), see vm_crowd_start


def vm_season_first(sid):
    """The crowd day of season sid's day 1."""
    return vm_day(calendar.timegm((sid // 100, sid % 100, 1, 0, 0, 0)))


def vm_crowd_start(sid, day):
    """The crowd at the start of season day `day`, its days 1..day-1 played: kept for the season and
    moved on a day at a time (VM_SEASON), as replaying a month of 1,000 managers every hour took
    0.4 s (now: once per server run; a new day ~40 ms, a new hour ~20 ms, all under STATE_LOCK).
    Season day 1 starts with the first crowd and the rest with their
    records, and the newcomers of earlier seasons at their first rating; a day's newcomers come in
    on their day. Never changed in place: a copy is moved on."""
    first = vm_season_first(sid)
    s, d0, crowd = VM_SEASON.get("crowd") or (None, 0, None)
    if s != sid or d0 > day:
        d0, crowd = 1, copy.deepcopy(vm_base() + vm_extra() + [m for _, _, m in vm_newcomers(first - 1)])
    elif d0 < day:
        crowd = copy.deepcopy(crowd)
    news = vm_newcomers(first + day - 2) if day > 1 else []
    joined = {m["_id"]: (jd, jh) for jd, jh, m in news}
    for d in range(d0, day):
        crowd += copy.deepcopy([m for jd, _, m in news if jd == first + d - 1])
        for vm in crowd:
            vm_play(vm, sid, d, first, joined.get(vm["_id"]))
    VM_SEASON["crowd"] = (sid, day, crowd)
    return crowd


@functools.lru_cache(maxsize=None)
def vm_skill(vid):
    return random.Random(vmseed(f"{vid}/skill")).randint(-60, 60)


def vm_play(vm, sid, d, first, joined=None, hour=None):
    """His Rank games of season day d, seeded by manager, season and day (the draw order is the
    record): a game before his join hour on his join day (joined = (crowd day, hour UTC)) never
    happened. With `hour` (today's) the later ones are drawn but not played yet, and today's 1on1 /
    VS Manager hours are kept: his presence (vm_status)."""
    rnd, skill = random.Random(vmseed(f"{vm['_id']}/{sid}/{d}")), vm_skill(vm["_id"])
    jd, jh = joined or (None, 0)
    for i in range(LADDER_COUNT):
        for _ in range(rnd.choice(VM_GAMES)):
            at, opp = rnd.choice(VM_HOURS), vm["elo"][i] + rnd.randint(-80, 80)
            won = rnd.random() < 1 / (1 + 10 ** ((opp - vm["elo"][i] - skill) / 400))
            if first + d - 1 == jd and at < jh:  # before he joined
                continue
            if hour is not None and i in (0, 3):
                vm.setdefault("hours", []).append(at)
            if hour is None or at <= hour:
                expect = 1 / (1 + 10 ** ((opp - vm["elo"][i]) / 400))
                vm["elo"][i] = max(LADDER_START, round(vm["elo"][i] + LADDER_K * (won - expect)))
                vm["wins" if won else "losses"][i] += 1


@functools.lru_cache(maxsize=None)
def vm_thirds():
    """Clubs by strength for a manager's club: a beginner gets one from the bottom third of a
    league, intermediate the middle, advanced the top (club_index lists each league's clubs
    strongest first). all_clubs, so that a club without a kit only moves its own managers (vm_make)."""
    thirds = ([], [], [])
    for _, leagues in all_clubs():
        for lid, teams in leagues:
            if lid != gen_teams.NATIONAL_LEAGUE:
                for k, t in enumerate(teams):
                    thirds[2 - min(2, 3 * k // len(teams))].append(t)
    return thirds


def vm_make(rnd, n, name, tier, played=True):
    """Manager n (id vm<n>) of tier `tier` (VM_TIERS), drawn from rnd. played: this season's
    record so far (the first crowd's), else none (a newcomer)."""
    _, grades, levels, ratings = VM_TIERS[tier]
    pool = vm_thirds()[tier] or vm_thirds()[0]
    # a club without a kit: the next one of its third that has one (no extra draw: the rest stays)
    kit = lambda t: next((c for c in pool[pool.index(t):] + pool if has_kit(c)), t)
    base = rnd.randint(*ratings)
    elo = [max(1000, base + rnd.randint(-40, 40)) for _ in range(LADDER_COUNT)]
    games = [rnd.randint(10, 60) * (tier + 1) if played else 0 for _ in range(LADDER_COUNT)]
    wins = [round(g * min(0.8, 0.35 + (e - 1000) / 2000)) for g, e in zip(games, elo)]
    forfeits = [rnd.randint(0, g // 25) for g in games]
    return {"_id": f"vm{n:04d}", "name": name, "level": rnd.randint(*levels),
            "teamid": kit(rnd.choice(pool)), "grade": rnd.randint(*grades),
            "elo": elo, "wins": wins, "forfeits": forfeits,
            "losses": [g - w - f for g, w, f in zip(games, wins, forfeits)]}


@functools.lru_cache(maxsize=None)
def vm_base():
    """The first crowd (VM_COUNT managers, VM_TIERS shares) at the start of a season."""
    rnd = random.Random(vmseed(VM_SEED))
    names = rnd.sample([w + t for w in VM_WORDS for t in VM_TAGS], VM_COUNT)
    out = []
    for tier, (share, *_) in enumerate(VM_TIERS):
        for _ in range(round(share * VM_COUNT) if tier < 2 else VM_COUNT - len(out)):
            out.append(vm_make(rnd, len(out), names[len(out)], tier))
    return out


@functools.lru_cache(maxsize=None)
def vm_extra():
    """The rest of the population (VM_EXTRA managers from VM_EXTRA_ID, then VM_EXTRA2 from
    VM_EXTRA2_ID; VM_TIERS shares each), there from day 0 with this season's record so far, like
    the first crowd."""
    return (vm_group("extra", VM_EXTRA, VM_EXTRA_ID, [w + t for w in VM_WORDS2 for t in VM_TAGS2])
            + vm_group("extra2", VM_EXTRA2, VM_EXTRA2_ID,
                       [w + t for w in VM_WORDS3 for t in VM_TAGS2 if w + t not in VM_NAMES3_MASKED]))


def vm_group(seed, n, first_id, pool):
    """n managers from first_id, named from pool, VM_TIERS shares (seeded by seed)."""
    rnd = random.Random(vmseed(f"{VM_SEED}/{seed}"))
    names = rnd.sample(pool, n)
    out = []
    for tier, (share, *_) in enumerate(VM_TIERS):
        for _ in range(round(share * n) if tier < 2 else n - len(out)):
            out.append(vm_make(rnd, first_id + len(out), names[len(out)], tier))
    return out


@functools.lru_cache(maxsize=4)
def vm_newcomers(day):
    """[(join day, join hour UTC, manager)] of the managers who joined on crowd days 1..day:
    VM_JOIN a day, tiers by VM_JOIN_TIER, a name nobody has, ids after the first crowd's, until
    the crowd (the first one, the rest of the population and them) holds VM_MAX."""
    taken = {m["name"] for m in vm_base()}
    free = sorted(w + t for w in VM_WORDS for t in VM_TAGS if w + t not in taken)
    out = []
    for d in range(1, day + 1):
        rnd = random.Random(vmseed(f"vmjoin/{d}"))
        for _ in range(rnd.choice(VM_JOIN)):
            if VM_COUNT + VM_EXTRA + VM_EXTRA2 + len(out) >= VM_MAX:
                return out
            tier = rnd.choices(range(len(VM_TIERS)), VM_JOIN_TIER)[0]
            name = free.pop(rnd.randrange(len(free)))
            out.append((d, rnd.choice(VM_HOURS), vm_make(rnd, VM_COUNT + len(out), name, tier, played=False)))
    return out


VmCard = collections.namedtuple("VmCard", "ovr spid pid team nation height born rep pace power tech passing "
                                           "defence shooting gk stamina")


@functools.lru_cache(maxsize=None)
def vm_card_pool():
    """{role (POS_ROLE of preferredposition1): [VmCard]} of every Market card, any season (in the
    classic world FO3's own seasons only): what a virtual manager's squad is drawn from (rating,
    ids, club, nation, height, birth year, reputation and the stat groups the squad themes rank by)."""
    pool, added = {}, ADDED_SEASONS if gen_teams.WORLD == "classic" else ()
    born = "CAST(substr(p.birthdate, instr(p.birthdate, ' ') - 4, 4) AS INTEGER)"
    with closing(_db()) as c:
        for r in c.execute(f"""SELECT p.spid, p.playerid, p.preferredposition1, {POSOVR}, p.teamid, p.nationality,
                p.height, {born}, p.internationalrep, (p.sprintspeed + p.acceleration) / 2,
                (p.strength + p.jumping + p.headingaccuracy) / 3, (p.dribbling + p.ballcontrol + p.agility) / 3 + 3 * p.skillmoves,
                (p.shortpassing + p.vision + p.longpassing) / 3, (p.standingtackle + p.marking + p.interceptions) / 3,
                (p.finishing + p.shotpower + p.longshots) / 3, (p.gkreflexes + p.gkdiving) / 2, p.stamina FROM players p"""):
            spid, pid, pos, *rest = r
            if 0 <= pos < POS_SUB and on_market(spid) and spid // 1000000 not in added:
                pool.setdefault(POS_ROLE[pos], []).append(VmCard(rest[0], spid, pid, *rest[1:]))
    return pool


# Squad themes (user: "Virtual manager tambah variasi dalam pemilihan skuad: ada yang ikuti meta,
# klub tertentu, negara tertentu, pemain tertentu, team color tertentu, stats tertentu"). Each
# manager builds around one theme (VM_THEMES by tier, seeded by his id, kept for good):
#   free    any card near his rating (the squads as they were)
#   club    a top club (user: "Klub dan negara top saja"; his own when it is one): its players now
#           or formerly (the club team color)
#   nation  a top nation (the nation team color)
#   color   one of the game's special team colors: a card season or a named set (tc_special_sets)
#   star    a card of one famous player (reputation 4+), the rest his top club's or nation's mates
#   meta    the stats that win games in FO3 by role (VM_META: pace up front, size and strength at the back)
#   stat    one stat group (VM_STAT_THEMES): pace, power, technique, passing, defence, shooting, youth, old school
# A theme takes cards near his rating only (+-VM_THEME_WIDTH: no stronger squads), a slot it cannot
# fill takes any card, and a club, nation, color or star theme must fill VM_THEME_MIN of his XI.
VM_THEMES = (("free", 30, 15, 5), ("club", 25, 20, 15), ("nation", 15, 15, 15), ("color", 5, 15, 20),
             ("star", 5, 10, 10), ("meta", 5, 10, 20), ("stat", 15, 15, 15))  # (theme, beginner, intermediate, advanced)
VM_THEME_WIDTH, VM_THEME_MIN = 8, 9
VM_TOP_CLUBS = (243, 241, 240, 11, 10, 5, 9, 1, 18, 21, 22, 45, 47, 44, 52, 48, 73)  # Real Madrid .. PSG
VM_TOP_NATIONS = (54, 52, 45, 21, 18, 14, 27, 34, 38, 7, 60, 10)  # Brazil .. Croatia
VM_STAT_THEMES = ("pace", "power", "tech", "passing", "defence", "shooting", "youth", "veteran")
# The meta by role, from FO3's own press (Inven, Gamemeca 2013-2018): "physical online" before the
# Impact Engine (Nov 2015: strength, height, heading), pace for everyone after it; a tall, strong,
# finishing striker; wingers with pace and a shot (cutting in); a CAM who passes and shoots; a CM
# with stamina and passing; a tall, heavy CDM; CBs with pace, strength, height and marking; an
# aggressive full back with pace; a tall keeper.
_FB = {"pace": 1.5, "defence": 1, "stamina": 0.5}
_CB = {"pace": 1, "power": 1, "height": 1, "defence": 1}
_ST = {"pace": 1, "shooting": 1, "power": 1, "height": 0.5}
VM_META = {"GK": {"gk": 2, "height": 1.5}, "SW": _CB, "CB": _CB, "FB": _FB, "WB": _FB,
           "DM": {"power": 1, "height": 1, "defence": 1, "stamina": 0.5}, "CM": {"stamina": 1, "passing": 1, "pace": 0.5},
           "WM": {"pace": 1, "passing": 0.5, "stamina": 0.5, "shooting": 0.5}, "AM": {"passing": 1, "shooting": 1, "power": 0.5},
           "W": {"pace": 1.5, "shooting": 1, "tech": 0.5}, "CF": _ST, "ST": _ST}
# the cards rankers fielded (same sources): Drogba, Ibrahimovic, C. Ronaldo, Pogba, Yaya Toure, Rio
# Ferdinand, Hummels, Rojo, Neuer, Henry, Ronaldinho, Vieira, Gullit, Rijkaard, van Basten, Crespo,
# van Nistelrooy, Maldini, van der Sar, Effenberg, Kompany, Ronaldo (R9), Courtois, Messi and
# Neymar after the new engine: a meta squad takes them when they are near his rating
VM_META_ICONS = frozenset((31432, 41236, 20801, 195864, 20289, 7289, 178603, 201862, 167495, 1625, 28130, 1419, 214100,
                           214098, 192181, 7512, 10264, 1109, 51539, 215558, 139720, 37576, 192119, 158023, 190871))


def vm_score(c, role, kind):
    """How well card c (VmCard) fits a meta or stat theme in role (higher is better)."""
    feat = lambda k: (c.height - 170) * 2 if k == "height" else getattr(c, k)
    if kind == "meta":
        return sum(w * feat(k) for k, w in VM_META[role].items()) + 40 * (c.pid in VM_META_ICONS)
    if kind in ("youth", "veteran"):
        return c.born if kind == "youth" else -c.born
    return feat("gk") + feat("height") / 2 if role == "GK" else feat(kind)  # a keeper is picked as a keeper


@functools.lru_cache(maxsize=None)
def vm_theme_keys():
    """{theme: candidate keys}: the top clubs and nations, the special team colors with 40 or more
    Market cards (not the admin page's colors and seasons: they would change every crowd squad)."""
    sets = tc_special_sets()
    own = {cid for cid, *_ in tc_custom()} | {TC_SEASON + code for code in CUSTOM_SEASON_CODES}
    return {"club": list(VM_TOP_CLUBS), "nation": list(VM_TOP_NATIONS),
            "color": sorted(cid for cid, (spids, _) in sets.items() if len(spids & market_spids()) >= 40 and cid not in own)}


@functools.lru_cache(maxsize=512)
def vm_theme_pool(kind, key):
    """{role: [VmCard]} of the cards a club / nation / color theme takes."""
    hist = tc_static()[1]
    fits = {"club": lambda c: c.team == key or key in hist.get(c.pid, ()), "nation": lambda c: c.nation == key,
            "color": lambda c: c.spid in tc_special_sets()[key][0]}[kind]
    return {role: [c for c in cs if fits(c)] for role, cs in vm_card_pool().items()}


def vm_theme_cover(kind, key, roles, centre):
    """XI slots (roles) a club / nation / color theme fills near the rating centre."""
    pool = vm_theme_pool(kind, key)
    return sum(min(n, sum(abs(c.ovr - centre) <= VM_THEME_WIDTH for c in pool.get(role, ())))
               for role, n in collections.Counter(roles).items())


@functools.lru_cache(maxsize=4096)
def vm_theme(vid, centre, roles, teamid):
    """(theme, key) of a manager's squad (VM_THEMES), seeded by his id: drawn by his tier (by his
    rating centre) until one fits his XI roles near his rating; else free."""
    rnd = random.Random(vmseed(f"{vid}/theme"))
    tier = 0 if centre < 69 else 1 if centre < 76 else 2
    keys = vm_theme_keys()
    fills = lambda kind, key: vm_theme_cover(kind, key, roles, centre) >= VM_THEME_MIN
    for _ in range(6):
        kind = rnd.choices([t for t, *_ in VM_THEMES], [w[tier] for _, *w in VM_THEMES])[0]
        if kind in ("free", "meta"):
            return kind, None
        if kind == "stat":
            return kind, rnd.choice(VM_STAT_THEMES)
        if kind == "star":
            pool = vm_card_pool()
            stars = sorted({c.pid for role in set(roles) for c in pool.get(role, ())
                            if c.rep >= 4 and centre - 4 <= c.ovr <= centre + 10})
            for pid in rnd.sample(stars, min(8, len(stars))):
                c = next(c for cs in pool.values() for c in cs if c.pid == pid)
                for mates in rnd.sample([("club", c.team), ("nation", c.nation)], 2):
                    if mates[1] in keys[mates[0]] and fills(*mates):
                        return kind, (pid, *mates)
            continue
        first = [teamid] if kind == "club" and teamid in VM_TOP_CLUBS else []
        for key in first + rnd.sample(keys[kind], min(12, len(keys[kind]))):
            if fills(kind, key):
                return kind, key
    return "free", None


def vm_theme_cards(theme, role, centre, used):
    """The cards his theme takes for a slot in role (none: any card near his rating)."""
    kind, key = theme
    if kind == "free":
        return []
    pool = (vm_theme_pool(*key[1:]) if kind == "star" else vm_theme_pool(kind, key)
            if kind in ("club", "nation", "color") else vm_card_pool())
    for width in (4, VM_THEME_WIDTH):
        cand = [c for c in pool.get(role, ()) if abs(c.ovr - centre) <= width and c.pid not in used]
        if cand and kind in ("meta", "stat"):  # the best fifth of the cards near his rating
            cand.sort(key=lambda c: -vm_score(c, role, key if kind == "stat" else kind))
            return cand[:max(3, len(cand) // 5)]
        if cand:
            return cand
    return []


# A virtual manager's playing style (issue #30). The match reads side.<x>.tactics[squad.tactic or 0]
# (build-up, chance creation, defence) and side.team.squad (formation id, each slot's pos and
# starting [attack participation, defence participation, x, y]) for the CPU side exactly as for
# mine (fifazf 0x9480d0 -> 0x9c2310 at kick-off). teammentality is not applied to a CPU side.
# Sliders 1..100 (<34 low, >=67 high); radios positioning 1 organised / 2 free form, defenderline
# 1 cover / 2 offside trap; participation 0 medium, 1 low, 2 high. The first four are the engine's
# own presets (fifazf 0x17742f8: Possession, Counter Attack, High Pressure, Long Ball).
VM_STYLES = {  # (bus speed, pass, positioning, cc pass, cross, shoot, positioning, def pressure,
               # aggression, width, defenderline), mentality, formation, (att, def) for DF / MF / FW
    "possession": ((20, 20, 2, 25, 25, 25, 2, 50, 25, 50, 1), 3, "4-3-3(1)", ((0, 0), (0, 0), (2, 1))),
    "counter": ((95, 50, 1, 50, 50, 50, 1, 10, 50, 60, 1), 2, "4-4-1-1(1)", ((1, 2), (0, 2), (2, 1))),
    "press": ((50, 50, 1, 75, 50, 75, 1, 85, 75, 25, 2), 4, "4-2-3-1(1)", ((0, 2), (2, 2), (2, 2))),
    "longball": ((90, 90, 1, 75, 75, 75, 1, 50, 75, 50, 1), 3, "4-4-2(1)", ((1, 2), (0, 0), (2, 1))),
    "parkbus": ((30, 70, 1, 30, 40, 30, 1, 15, 40, 25, 1), 1, "5-4-1(2)", ((1, 2), (1, 2), (2, 1))),
    "allout": ((80, 45, 2, 80, 70, 85, 2, 75, 70, 70, 2), 5, "4-2-4", ((2, 0), (2, 0), (2, 1))),
}


def vm_style(vm):
    return random.Random(vmseed(f"{vm['_id']}/style")).choice(sorted(VM_STYLES))


def style_tactic(name, i=0):
    v, mentality = VM_STYLES[name][:2]
    return {"id": i, "name": "", "teammentality": mentality,
            "bus": {"buildupspeed": v[0], "passing": v[1], "positioning": v[2]},
            "cc": {"passing": v[3], "crossing": v[4], "shooting": v[5], "positioning": v[6]},
            "def": {"mentality": v[7], "aggression": v[8], "teamwidth": v[9], "defenderline": v[10]}}


def style_slots(name):
    """His style's formation as squad slots [{pos, starting}] (11), or None if the client's
    formation table lacks it."""
    _, _, formation, part = VM_STYLES[name]
    form = next((f for f in formation_table()["common"] if f["formationname"] == formation), None)
    line = lambda p: (0, 0) if p == 0 else part[0 if p <= 8 else 1 if p <= 19 else 2]  # GK / DF 1-8 / MF 9-19 / FW
    return form and [{"pos": p, "starting": [*line(p), o["x"], o["y"]]} for p, o in zip(form["positions"], form["offsets"])]


def vm_team(vid, day=None):
    """A virtual manager's squad on crowd day `day` (today by default): vm_squad."""
    return vm_squad(vid, vm_day() if day is None else day)


def vm_origin(vid, day):
    """(join day, record) of manager vid as he joined the crowd (vm_base: day 0), or None."""
    return next(((jd, m) for jd, _, m in vm_newcomers(day) if m["_id"] == vid),
                next(((0, m) for m in vm_base() + vm_extra() if m["_id"] == vid), None))


@functools.lru_cache(maxsize=512)
def vm_squad(vid, day):
    """His squad on crowd day `day` (user: "skuad lawan kurang random session"): his style's
    formation (VM_STYLES; else his club's) and his club's kit, every position a random card of any
    season in that role, rated by his first ladder rating (about 64 for a beginner to 82 at
    Legendary, +-4), plus 7 on the bench. Each card has its own grade (his grade +-1) and level
    (1..2 for a beginner up to 1..8, by his rating). Then every day since he joined lives
    (VM_LIFE, seeded by his id and the day): a transfer swaps one of his 18 for another card of
    that role, an upgrade try moves one card by the Upgrade odds (ENHANCE_RATE; a failed try from
    +ENHANCE_DROP_FROM drops a grade; at most his grade + 2), and each starter may gain a level
    (at most 3 over his range)."""
    joined, vm = vm_origin(vid, day)
    rnd, rc = random.Random(vmseed(vid)), random.Random(vmseed(f"{vid}/cards"))
    club = roster_team(vm["teamid"])
    slots = style_slots(vm_style(vm))
    formation = next((f["formationid"] for f in formation_table()["common"]
                      if slots and f["formationname"] == VM_STYLES[vm_style(vm)][2]), club["squad"].get("formation"))
    centre = 64 + (max(vm["elo"]) - LADDER_START) * 18 // 780
    top = 2 + (max(vm["elo"]) - LADDER_START) * 6 // 780  # his cards' levels: 1..top
    gmax, lmax = min(10, vm["grade"] + 2), min(PLAYER_MAX_LEVEL, top + 3)
    pool = vm_card_pool()
    xi = slots or club["squad"]["positions"]
    roles = tuple(POS_ROLE[s["pos"]] for s in xi)
    theme = vm_theme(vid, centre, roles, vm["teamid"])  # his squad theme (VM_THEMES)
    star = theme[1][0] if theme[0] == "star" else None

    def card(slot, r, used):
        """(slot, rating, spid, playerid, grade, level): a new card for slot near his rating, of
        his theme when it has one."""
        role = POS_ROLE[slot["pos"]] if slot["pos"] < POS_SUB else r.choice(("CB", "CM", "ST", "W", "FB"))
        cand = vm_theme_cards(theme, role, centre, used)
        for width in () if cand else (4, 8, 16, 99):
            cand = [c for c in pool.get(role, ()) if abs(c.ovr - centre) <= width and c.pid not in used]
            if cand:
                break
        c = r.choice(cand)
        grade = max(1, min(gmax, vm["grade"] + rc.choice((-1, 0, 0, 1))))
        return [slot, c.ovr, c.spid, c.pid, grade, rc.randint(1, top)]

    chosen = []
    for slot in xi + [{"pos": POS_SUB}] * 7:
        chosen.append(card(slot, rnd, {c[3] for c in chosen}))
    if star and star not in {c[3] for c in chosen[:len(roles)]}:  # his favourite player in his XI: his best card near his rating
        cards = [(c, k) for k, role in enumerate(roles) for c in pool.get(role, ())
                 if c.pid == star and centre - 4 <= c.ovr <= centre + 10]
        if cards:
            c, k = max(cards, key=lambda x: (x[0].ovr, -x[1]))
            out, chosen[k][1:4] = chosen[k][1:4], [c.ovr, c.spid, c.pid]
            for b in chosen[len(roles):]:  # drawn for the bench before: the starter he replaces sits there
                if b[3] == star:
                    b[1:4] = out
    for d in range(joined + 1, day + 1):
        r = random.Random(vmseed(f"{vid}/life/{d}"))
        if r.random() < VM_LIFE[0]:  # a transfer: one out, one in (the Market); never his star
            k = r.randrange(len(chosen))
            if chosen[k][3] != star:
                chosen[k] = card(chosen[k][0], r, {c[3] for c in chosen})
        if r.random() < VM_LIFE[1]:  # an upgrade try
            c = chosen[r.randrange(len(chosen))]
            if c[4] < gmax:
                c[4] += 1 if r.random() < ENHANCE_RATE[c[4]] else -(c[4] >= ENHANCE_DROP_FROM)
        for c in chosen:
            if c[0]["pos"] < POS_SUB and r.random() < VM_LIFE[2]:
                c[5] = min(lmax, c[5] + 1)
    with closing(_db()) as c:
        players = [{"spid": spid, "expandme": "player", "price": ovr, "jersey": n + 1, "grade": g,
                    "levelinfo": {"level": lv, "exp": 0}, "jerseyname": gen_teams.jersey_name(c, pid)}
                   for n, (_, ovr, spid, pid, g, lv) in enumerate(chosen)]
    # his cards' growth (Player Growth): points by his rating, 0 for a beginner up to a card's
    # limit at Legendary, on the stats of the card's role at its slot (the bench: its own position)
    budget = max(0, min(GROWTH_PLAYER_LIMIT, (max(vm["elo"]) - LADDER_START) * GROWTH_PLAYER_LIMIT // 780))
    for p, (slot, _, spid, *_) in zip(players, chosen):
        sp = vm_growth(vid, spid, POS_ROLE[slot["pos"]] if slot["pos"] < POS_SUB else card_role(spid), budget)
        if sp:
            p["sp"] = sp
    xi = [(slot, ovr, spid) for slot, ovr, spid, *_ in chosen if slot["pos"] < POS_SUB]
    best = sorted(xi, key=lambda x: -x[1])  # the kickers: his best outfield players (never the GK)
    squad = {"positions": [{**slot, "spid": spid} for slot, _, spid in xi], "captain": best[0][2],
             "kickers": [spid for slot, _, spid in best if slot["pos"]][:KICKERS], "formation": formation}
    return {"teamid": vm["teamid"], "squad": squad, "players": players, "formations": club.get("formations", [])}


VM_STAFF_ORDER = ("FW", "MF", "DF", "GK", "MEDICAL", "PREMIUM", "FRONT")  # the role frames a manager fills as he rises


def vm_staff(vm):
    """A virtual manager's staff (user: "harus mempunyai staff sesuai dengan level nya"): one role
    frame more every 12 levels (VM_STAFF_ORDER: an FW coach at level 1, all 7 from level 72), each a
    staff of that position of about grade 1 + level / 12 (+-1 half star; 5 stars near level 100),
    seeded by his id, always under contract. The staff fields of a raw team (staff_team)."""
    rng, level = random.Random(vmseed(f"{vm['_id']}/staff")), vm["level"]
    staffs, slots = [], [None] * len(STAFF_SLOTS)
    for k, pos in enumerate(VM_STAFF_ORDER[:min(len(VM_STAFF_ORDER), 1 + level // 12)]):
        sid = rng.choice([i for i, c in STAFF_CATALOGUE.items() if c[0] == pos and c[1]])
        s = staff_make(sid, max(1, min(STAFF_MAX_GRADE, 1 + level // 12 + rng.choice((-1, 0, 0, 1)))), rng)
        s.update(_id=f"{vm['_id']}st{k}", status="appointed")
        staffs.append(s)
        slots[STAFF_SLOTS.index(pos)] = s["_id"]
    return {"staffs": staffs, "staffslots": slots}


def vm_colors_staff(team, vm):
    """A virtual manager's owned_team with what boosts his starters in a match (user: "Virtual
    manager harus dapat bonus team color saat bertanding"): the team colors his XI meets, as mine,
    and his staff (an AI club stays without: a whole club would have its +4 for free)."""
    team["teamcolors"] = active_teamcolors(team)
    team.update(staff_match(staff_team(vm_staff(vm))))
    return team


def vm_side(vm):
    """match.side for a virtual manager: his squad (each card at its grade and level) with its
    team colors and his staff, his style's tactic, his name on it (not "COM")."""
    side = match_side(copy.deepcopy(vm_team(vm["_id"])), vm["teamid"], tactics=[style_tactic(vm_style(vm))])
    vm_colors_staff(side["team"], vm)
    side["players"] = [{"id": vm["_id"], "name": vm["name"], "levelinfo": {"level": vm["level"], "exp": 0}}]
    return side


def vm_ovr(vid, day=None):
    """A virtual manager's FW / MF / DF ratings: team_summary of his squad (grades and levels in)
    with his team colors and staff, as my own summary counts mine."""
    return vm_ratings(vid, vm_day() if day is None else day)


@functools.lru_cache(maxsize=512)
def vm_ratings(vid, day):
    team = vm_squad(vid, day)
    owned = vm_colors_staff(owned_team(copy.deepcopy(team)), vm_origin(vid, day)[1])
    ovr = team_summary(owned, team["teamid"]).get("ovr") or {}
    return {k: ovr.get(k, 0) for k in ("FW", "MF", "DF")}


def ladder_board(st, i):
    """Ladder i's ranking, the virtual managers and me: [(elo, name, _id)], best first."""
    me = my_coach(st)
    mine = [(my_ladder(st)["elo"][i], me["name"], me["_id"])] if my_ladder(st)["games"][i] else []
    return sorted([(m["elo"][i], m["name"], m["_id"]) for m in virtual_managers()] + mine, key=lambda e: -e[0])


def ep_ladder_leaderboard(query, body):
    st = _state()
    return _json([[{"_id": i_, "name": n, "elo": e, "rank": r + 1} for r, (e, n, i_) in enumerate(ladder_board(st, i)[:10])]
                  for i in range(LADDER_COUNT)])


def vm_friends():
    """Every fifth manager of the first crowd is a friend (a spread over the tiers): the Rank
    friends ranking and the Messenger friends list are the same 20."""
    return virtual_managers()[:VM_COUNT:VM_COUNT // VM_FRIENDS]


def ep_ladder_friends(query, body):
    return _json([{**{k: m[k] for k in ("_id", "name", "teamid", "elo", "wins", "losses", "forfeits")},
                   "ovr": vm_ovr(m["_id"])} for m in vm_friends()])


# Manager > Peringkat manajer (CoachRankingTabPage, issue #33: the list spun forever on {}).
# GET /coach/me/teamvalue/history {values}: my team value on each of the last TV_DAYS days, oldest
# first (the graph is 30 points of 12 px and dates them by the client's clock; no values = its
# spinner never stops). GET /rank/teamvalue/friend | club: a top-level array of {_id, rank, name,
# level, ovr {FW, MF, DF}, teamvalue {val}, fluc = yesterday's rank - today's (none = "NEW")}.
# Friends = the Messenger's 20 virtual managers and me; there are no clubs (club: {}), so the club
# tab says "not in any club". A team's value is the client's own (CoachProxy.expandTeam): the
# prices of its Tim inti cards. POST /coach/me/hiderank {hiderank} hides my name on the club tab.
TV_DAYS = 30


def first_team_value(team):
    return sum(p["price"] for p in team["players"] if p.get("state") == "first")


@functools.lru_cache(maxsize=256)
def vm_value(vid, day):
    """A virtual manager's team value on crowd day `day` (set_economy drops these)."""
    return first_team_value(owned_team(copy.deepcopy(vm_squad(vid, day))))


def tv_history(st):
    """My team value on each of the last TV_DAYS crowd days, oldest first: coach["tvhist"] {day:
    EP}, today's taken now; a day without one repeats the one before (the first known, before)."""
    coach, today = my_coach(st), vm_day()
    hist = {int(d): v for d, v in coach.get("tvhist", {}).items() if int(d) > today - TV_DAYS}
    hist[today] = first_team_value(coach_me(st, coach["slot"])["team"])
    coach["tvhist"] = {str(d): v for d, v in hist.items()}
    last, out = hist[min(hist)], []
    for d in range(today - TV_DAYS + 1, today + 1):
        last = hist.get(d, last)
        out.append(last)
    return out


def ep_tv_history(query, body):
    st = _state()
    values = tv_history(st)
    _save(st)
    return _json({"values": values})


def ep_rank_friends(query, body, vms=None):
    st = _state()
    coach, day = my_coach(st), vm_day()
    me, hist = coach_me(st, coach["slot"]), tv_history(st)
    _save(st)
    rows = [(m["_id"], m["name"], m["level"], vm_ovr(m["_id"]), vm_value(m["_id"], day),
             vm_value(m["_id"], day - 1 if vm_origin(m["_id"], day - 1) else day))
            for m in (vm_friends() if vms is None else vms)]
    rows.append((coach["_id"], coach["name"], me["levelinfo"]["level"],
                 {k: (me["summary"].get("ovr") or {}).get(k, 0) for k in ("FW", "MF", "DF")}, hist[-1], hist[-2]))
    before = {r[0]: k for k, r in enumerate(sorted(rows, key=lambda r: (-r[5], -r[2])), 1)}
    return _json([{"_id": i_, "name": n, "level": lv, "ovr": o, "teamvalue": {"val": v}, "rank": k, "fluc": before[i_] - k}
                  for k, (i_, n, lv, o, v, _) in enumerate(sorted(rows, key=lambda r: (-r[4], -r[2])), 1)])


def ep_hiderank(query, body):
    st = _state()
    my_coach(st)["hiderank"] = bool(json.loads(body or b"{}").get("hiderank"))
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, _default_slot(st))})


def ep_my_summary(cid, query, body):
    """GET /coach/<my id>/summary: my own profile (my row's Info button on a ranking), as
    ep_vm_summary gives a virtual manager's."""
    st = _state()
    coach = my_coach(st)
    if coach["_id"] != cid:
        return _json({"result": False, "reason": "invalid coach id"})
    me = coach_me(st, coach["slot"])
    return _json({"coach": {"_id": cid, "id": cid, **{k: me[k] for k in ("name", "levelinfo", "currentbadge", "playtime", "profile")},
                            "statistics": {"ladder": me["statistics"]["ladder"]}}, "friends": VM_FRIENDS})


def ep_coach_profile(query, body):
    """PUT /coach/me/profile {description} | {picture: a card's _id} (Info Manager's Summary: its
    intro box, its manager photo; the photo redraws on result true)."""
    req = json.loads(body or b"{}")
    st = _state()
    prof = my_coach(st).setdefault("profile", {})
    if isinstance(req.get("description"), str):
        prof["description"] = req["description"][:200]
    if req.get("picture") is not None:
        prof["picture"] = str(req["picture"])
    _save(st)
    return _json({"result": True})


def vm_status(vm, hour=None):
    """His Messenger presence now (issue #30, simulated online presence): "busy" in an hour he has
    a Rank game today (vm_crowd), "online" the hour before or after one, else "offline". Game hours
    are VM_HOURS (08:00..02:00 WIB)."""
    h = time.gmtime().tm_hour if hour is None else hour
    hours = set(vm.get("hours", ()))
    return "busy" if h in hours else "online" if {(h - 1) % 24, (h + 1) % 24} & hours else "offline"


def ep_contact_list(query, body):
    """GET /contact/list (MessengerProxy): the friends list, the only place the client shows who
    is online. The 20 friends with their presence; newfriends must be an Array once friends is not
    empty (MessengerProxy.checkFriends throws and the messenger never gets ready). Left out on
    purpose: group (null = "Teman"; an unknown name asserts), club, channelid (0 disables "go to
    his channel")."""
    return _json({"friends": [{"_id": m["_id"], "coachname": m["name"], "status": vm_status(m), "level": m["level"],
                               "currdivision": f"DIVISION_{ladder_division(m['elo'][0])}"} for m in vm_friends()],
                  "newfriends": [], "requesting": [], "requested": [], "groups": []})


def presence_loop():
    """The server's thread: a /presence/<id> push for every friend whose status changed (the
    client repaints the friends list from these pushes, not from /contact/list again). The crowd
    is read under STATE_LOCK like a request's (its caches are built on first use)."""
    last = {}
    while True:
        try:
            with STATE_LOCK:
                st = _state()
                v = club_view(st)
                now = {m["_id"]: vm_status(m) for m in vm_friends() + [m["vm"] for m in (v["members"] if v else []) if m["vm"]]}
                club_idle(st)
                channel_idle()
            for vid, s in now.items():
                if last.get(vid) not in (None, s):
                    messenger_push(f"/presence/{vid}", {"status": s})
            last = now
        except Exception as e:  # keep the presence going
            print(f"presence loop: {e!r}", flush=True)
        time.sleep(60)


def ep_vm_summary(vid, query, body):
    """GET /coach/<vm id>/summary[?view=true]: his profile (the Info button of a friend or of the
    Rank friends ranking -> CoachUserPopupScreen; with {} it stayed invisible and kept the keys).
    The popup reads the last season and its 1on1 index; coach.roster is left out (an old one
    closes it)."""
    vm = vm_by_id(vid)
    if not vm:
        return _json({"result": False, "reason": "invalid coach id"})  # "Manager tidak lagi tersedia"
    now = time.time()
    day = time.strftime("%Y%m%d", time.gmtime(now))
    season = {"sid": str(ladder_season(now)[0]), "wins": vm["wins"], "losses": vm["losses"], "forfeits": vm["forfeits"],
              "elos": [[{"elo": e, "dateymd": day}] for e in vm["elo"]]}
    return _json({"coach": {"_id": vm["_id"], "id": vm["_id"], "name": vm["name"], "levelinfo": {"level": vm["level"], "exp": 0},
                            "currentbadge": 0, "playtime": 0, "profile": {"description": "", "views": 0},
                            "statistics": {"ladder": {"seasons": [season]}}}, "friends": VM_FRIENDS})


def ep_vm_ladder_info(vid, query, body):
    """GET /ladder/<vm id>/info (his profile): his 1on1 rating and rank."""
    vm = vm_by_id(vid)
    if not vm:
        return _json({})
    rank = next(r + 1 for r, e in enumerate(ladder_board(_state(), 0)) if e[2] == vid)
    return _json({"sid": str(ladder_season()[0]), "ladder_pvp_1v1": {"rank": rank, "elo": vm["elo"][0]}})


def ep_vm_coach(vid, query, body):
    """GET /coach/<vm id>?path=team (his profile): the team object itself; GET /coach/<vm id>
    (its squad popup, PlayerListUtil.generateCaptainFromCoachInfo): a coach like /coach/me, with
    the team and teamsquadlist.preset."""
    vm = vm_by_id(vid)
    if not vm:
        return _json({})
    team = vm_side(vm)["team"]
    if "path=team" in query:
        return _json(team)
    return _json({"id": vm["_id"], "_id": vm["_id"], "name": vm["name"], "teamid": vm["teamid"],
                  "levelinfo": {"level": vm["level"], "exp": 0}, "team": team, "kit": team.get("kit"),
                  "summary": team.get("summary"), "formations": team.get("formations", []),
                  "teamsquadlist": {"preset": preset_squads(team)}})


def ladder_season(t=None):
    """(sid, start, next) of the ladder season: the calendar month (sid yyyymm)."""
    y, mo = time.gmtime(t or time.time())[:2]
    nxt = (y + mo // 12, mo % 12 + 1)
    return y * 100 + mo, w3c(calendar.timegm((y, mo, 1, 0, 0, 0))), w3c(calendar.timegm((*nxt, 1, 0, 0, 0)))


def ep_ladder_season_info(query, body):
    sid, start, nxt = ladder_season()
    prev = sid - 1 if sid % 100 > 1 else sid - 89  # 202601 -> 202512
    return _json({"sid": sid, "prevsid": prev, "start": start, "end": nxt})


# --------------------------------------------------------------------------- #
# Matches against the virtual managers (issue #30, stage 2). The Rank buttons open a room and
# search at once (RoomController: POST /room {slotsize, type pvp_ladder_mgr | ...} -> resources
# [/room/<id>], RoomProxy base uri /room; quick match -> POST /matchmaking). The room's stage
# drives the screen (room / searching / matched ...) and matchmaking.resources carries the match
# (processRoomMatchmakingResource -> MatchProxy.registerByResource). The match is the real PvP
# type (Ladder_MGR_PvP_1v1 / Ladder_PvP_1v1): squad screen, multiplay game setting screen,
# then a match the client plays against its CPU, as the native session holds my user only
# (ladder_match). The opponent is the virtual manager nearest my rating, his club with every
# card at his grade; the result comes in by PUT /match/<id>/end. Enums: LADDER_IDX_1VS1 0,
# 2VS2 1, 3VS3 2, MANAGER 3; feature names vs1 / mgr1 (getLadderNameByIdx). My record is per
# season (the month) and goes out as /ladder/my/info ladder_<pvp_1v1 | mgr_pvp_1v1> and
# /coach/me statistics.ladder {seasons [{sid, elos [i] [...], wins, losses, forfeits, rank,
# division [i] {name, rank}, highrecord [i] {elo, rank}}], highrecords {elo [i], division [i]}}.
# --------------------------------------------------------------------------- #
LADDER_START, LADDER_K = 1000, 32
LADDER_DEPLOY = 5  # Enums.LADDER_MAX_DEPLOY_MATCH: placement matches
LADDER_MY_KEYS = {0: "ladder_pvp_1v1", 1: "ladder_pvp_2v2", 2: "ladder_pvp_3v3", 3: "ladder_mgr_pvp_1v1"}
LADDER_TYPES = ("Ladder_PvP_1v1", "Ladder_PvP_2v2", "Ladder_PvP_3v3", "Ladder_MGR_PvP_1v1")  # LadderMatchController.matchTypes


def ladder_index(room_type):
    return 3 if "mgr" in str(room_type) else 0


# Multiple > Persahabatan (issue #36: "Friendly Matches ... not working"; the screen was empty
# without features.friendlymatch). Its rooms (RoomController.createARoom: POST /room {type}) play
# against AI on the Rank path (the one-user session, see ladder_match): Quick match and Random
# match (pvp_random) search and find a virtual manager; a custom room (pvp_referee / pvp_custom)
# plays one invited from the friends list (POST /room/<id>/invite/<id>: he joins and gets ready);
# vs COM (pve*, PUT /room/<id>/type) plays the club picked in the room's AI picker (PUT
# /room/<id>/AI {index {nation, league, team, difficulty}}: indices into the client's SORTNATION,
# SORTLEAGUE/<nation>, SORTTEAM/<league> lists without 2111, difficulty 0..4 = Amateur..Legendary)
# once I press Mulai (PUT /room/<id>/side/left/ready). Results count in statistics.friendly
# {quick, custom, vscom} (friendly_record), not in the Rank ratings.
def room_kind(room_type):
    t = str(room_type)
    return ("ladder" if "ladder" in t else "koth" if t == "esports2" else "exhibition" if t == "pvp_exhibition"
            else "vscom" if t.startswith("pve") else "friendly")


def room_ai_team(room):
    """The club the room's AI picker shows (RoomProxy's lists, 2111 dropped)."""
    idx = (room.get("AI") or {}).get("index") or {}
    pick = lambda xs, k: [x for x in xs if int(x) != 2111][max(0, min(int(idx.get(k) or 0), len([x for x in xs if int(x) != 2111]) - 1))]
    nation = pick([n for n, _ in club_index()], "nation")
    league = pick([lid for lid, _ in dict(club_index())[nation]], "league")
    return int(pick(json.loads(ep_sortteam("", b"")[1]).get(str(league)) or [my_teamid(_state())], "team"))


def room_vm(vm):
    """A virtual manager as a room user (the custom room's slot reads summary.id and summary.ovr)."""
    return {"id": vm["_id"], "_id": vm["_id"], "name": vm["name"], "teamid": vm["teamid"],
            "levelinfo": {"level": vm["level"], "exp": 0}, "captain": True, "summary": {"id": vm["teamid"], "ovr": vm_ovr(vm["_id"])}}


@functools.lru_cache(maxsize=None)
def ladder_divisions():
    with open(os.path.join(RESPONSES, "ladder_division_info.json"), encoding="utf-8") as f:
        return json.load(f)  # highest first


def ladder_division(elo):
    return next((d["name"] for d in ladder_divisions() if elo >= d["rating"]), ladder_divisions()[-1]["name"])


# Rank rewards (user: "hadiah ranking bisa dirubah di admin panel"): /ladder/reward/info {divs [{name
# DIVISION_<div>, items [main, sub]}], wins [{from, items}]}, the Rank screen's centre panel (its
# best division's main and sub item, the next weekly wins step). The admin page's Match tab writes
# gamedata/ladder_rewards.json over responses/ladder_rewards.json. They used to be shown only; now
# - the weekly wins steps reached this ISO week are paid (into the Bag) by the client's own flow:
#   ladder_total.midrewardable -> POST /ladder/reward -> {result, rewards {wins [[item tiles]]}}
#   (LadderWeeklyRewardPopup shows wins[0]);
# - a finished season pays at the new season's start (into the Bag): the main item of the division
#   of the ladder rated best at its end, the sub item of every other ladder played. Its season
#   record stays rewarded (LadderSeasonRewardPopupScreen wants records this server does not keep).
LADDER_REWARDS_FILE = os.path.join(HERE, "gamedata", "ladder_rewards.json")


def ladder_rewards():
    for f in (LADDER_REWARDS_FILE, os.path.join(RESPONSES, "ladder_rewards.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            continue
    return {"divs": [], "wins": []}


def ladder_week(st):
    """This ISO week's {week, wins, paid [from]} of my Rank matches."""
    week, wk = time.strftime("%G%V", time.gmtime()), my_coach(st).setdefault("ladderweek", {})
    if wk.get("week") != week:
        wk.clear()
        wk.update(week=week, wins=0)
    wk.setdefault("paid", [])
    return wk


def ladder_week_due(st):
    """The weekly wins steps reached and not paid yet: [{from, items}]."""
    wk = ladder_week(st)
    return [w for w in ladder_rewards()["wins"] if w["from"] <= wk["wins"] and w["from"] not in wk["paid"]]


def ep_ladder_reward(query, body):
    """POST /ladder/reward (LadderMatchFrontScreen.checkMidReward): the weekly wins steps reached."""
    st = _state()
    due = ladder_week_due(st)
    if not due:
        return _json({"result": False})
    items = [it for w in due for it in w["items"]]
    for w in due:
        bag_add(st, f"Hadiah Rank mingguan: {w['from']} kemenangan", w["items"],
                msg=f"{w['from']} kemenangan Rank minggu ini.")
        ladder_week(st)["paid"].append(w["from"])
    _save(st)
    return _json({"result": True, "rewards": {"wins": [[{**it, "staticdata": item_def(it["itemid"])} for it in items]]}})


def ladder_season_pay(st, prev):
    """The finished season prev's rewards into the Bag (see ladder_rewards)."""
    played = [i for i in range(LADDER_COUNT) if (prev.get("games") or [0] * LADDER_COUNT)[i]]
    if not played:
        return None
    divs = {d["name"]: d["items"] for d in ladder_rewards()["divs"]}
    best = max(played, key=lambda i: prev["elo"][i])
    items = [it for i in played for it in divs.get(f"DIVISION_{ladder_division(prev['elo'][i])}", [])[0 if i == best else 1:][:1]]
    if not items:
        return None
    return bag_add(st, f"Hadiah musim Rank {prev.get('sid', '')}", items,
                   msg=f"Divisi terbaik: {ladder_division(prev['elo'][best]).replace('_', ' ').title()}.")


def my_ladder(st):
    """This season's record: {sid, elo [i], high [i], wins, losses, draws, games,
    elos [i] [[elo, yyyymmdd]] (the rating at the end of each day played)}."""
    sid = ladder_season()[0]
    coach = my_coach(st)
    lad = coach.setdefault("ladder", {})
    # ratings recorded before they carried a date go on their season's first day
    lad["elos"] = [[e if isinstance(e, list) else [e, f"{lad['sid']}01"] for e in h] for h in lad.get("elos", [])]
    if lad.get("sid") != sid:  # a new season starts from scratch
        if any(lad.get("games") or ()):  # the finished one stays: the record screen's last season, best ratings
            coach["ladderprev"] = copy.deepcopy(lad)
            ladder_season_pay(st, lad)
            coach["ladderbest"] = [max(b, h) for b, h in zip(coach.get("ladderbest") or lad["high"], lad["high"])]
        lad.clear()
        lad.update(sid=sid, elo=[LADDER_START] * LADDER_COUNT, high=[LADDER_START] * LADDER_COUNT,
                   **{k: [0] * LADDER_COUNT for k in ("wins", "losses", "draws", "games")},
                   elos=[[] for _ in range(LADDER_COUNT)])
    return lad


def ladder_season_record(lad, rank):
    """One statistics.ladder season. elos [i] = the rating history as [{elo, dateymd "YYYYMMDD"}]
    (the friends ranking shows the last one's elo, none yet: "-"; the record screen's daily graph,
    LadderMatchRecordSeasonTabPage.drawDailyMatchRecord, parses dateymd). sid as a string:
    LadderMatchController.setSeasonalRecord(dest, sid:String, seasons) finds a season by
    season.sid === sid with /ladder/season/info's sid / prevsid coerced to a String, so a number
    never matched (my row in "Poin peringkat" stayed "- Poin", the record screen empty)."""
    return {"sid": str(lad["sid"]), "elos": [[{"elo": e, "dateymd": d} for e, d in h] for h in lad["elos"]],
            "wins": lad["wins"], "losses": lad["losses"], "forfeits": [0] * LADDER_COUNT, "rank": rank,
            # the record screen's "last 3 matches" (oldest first; {date, goals, goalsconceded,
            # shootout, shootoutconceded, streak +n win / -n loss, elodiff, elo, forfeits}) and
            # its "(Penalti x Menang y Kalah)"
            "recentmatches": lad.get("recent") or [[] for _ in range(LADDER_COUNT)],
            "swins": lad.get("swins") or [0] * LADDER_COUNT, "slosses": lad.get("slosses") or [0] * LADDER_COUNT,
            "division": [{"name": f"DIVISION_{ladder_division(e)}", "rank": r} for e, r in zip(lad["elo"], rank)],
            "highrecord": [{"elo": h, "rank": r} for h, r in zip(lad["high"], rank)]}


def ladder_statistics(st):
    lad, coach = my_ladder(st), my_coach(st)
    rank = [next(r + 1 for r, e in enumerate(ladder_board(st, i)) if e[2] == coach["_id"])
            if lad["games"][i] else 0 for i in range(LADDER_COUNT)]
    # this season last: Info Manager's Summary and the profile popup take the last one as the
    # current season (the Rank screens find a season by its sid)
    seasons = [ladder_season_record(lad, rank)]
    prev = coach.get("ladderprev")
    if prev:  # last season, ranked by its final ratings against the virtual managers. rewarded: no
        # season-end reward yet; without it LadderMatchFrontScreen.checkSeasonReward POSTs /ladder/reward
        # on every visit and opens the season reward popup on the default {result: true} (TypeError)
        seasons.insert(0, {**ladder_season_record(prev, [1 + sum(m["elo"][i] > e for m in virtual_managers()) if g else 0
                                                         for i, (e, g) in enumerate(zip(prev["elo"], prev["games"]))]),
                           "rewarded": True})
    # the best season (closure 46 of rebuildLadderRecordInfo): the best rating of any season, its division
    best = [max(b, h) for b, h in zip(coach.get("ladderbest") or lad["high"], lad["high"])]
    return {"seasons": seasons, "highrecords": {"elo": best, "division": [
        {"name": f"DIVISION_{ladder_division(b)}", "rank": r} for b, r in zip(best, rank)]}}, rank


def ep_ladder_my_info(query, body):
    return _json(ladder_my_info(_state()))


def ladder_my_info(st):
    lad = my_ladder(st)
    _, start, nxt = ladder_season()
    stats, rank = ladder_statistics(st)
    # sid: Info Manager's Summary shows this season's Rank rates only for a season of this sid
    out = {"sid": str(ladder_season()[0]), "start": start, "next": nxt}
    for i, key in LADDER_MY_KEYS.items():
        if lad["games"][i]:
            # firstround: the placement matches (LadderMatchFrontRecordWindow.setRankedMatchRecord
            # shows "Placement match x/5" while it is missing or ongoing; wins = a flag per match).
            # After the 5th, LadderMatchController.updateLadderResult pops the placement result
            # from beforeelo to afterelo (missing: NaN, shown as Legendary A)
            done = (lad.get("results") or [[]] * LADDER_COUNT)[i]
            out[key] = {"elo": lad["elo"][i], "highelo": lad["high"][i], "rank": rank[i],
                        "division": {"name": ladder_division(lad["elo"][i]), "rank": rank[i]},
                        "wins": lad["wins"][i], "losses": lad["losses"][i], "forfeits": 0,
                        "firstround": {"ongoing": lad["games"][i] < LADDER_DEPLOY, "wins": [r == 1 for r in done],
                                       "beforeelo": LADDER_START,
                                       "afterelo": (lad.get("placed") or [None] * LADDER_COUNT)[i] or lad["elo"][i]}}
    played = [i for i in LADDER_MY_KEYS if lad["games"][i]]
    if played:
        # the Rank screen's centre panel ("Rekor rank terbaik musim ini", LadderMatchFrontRecordWindow
        # .initializeBestRecord): the season's best rating and its mode (a matchTypes name), games
        # played (against the division's mingames) and this week's wins (the weekly reward gauge).
        # Without it the panel says "Tdk ada rekor".
        best = max(played, key=lambda i: lad["high"][i])
        week = my_coach(st).get("ladderweek") or {}
        wins = week.get("wins", 0) if week.get("week") == time.strftime("%G%V", time.gmtime()) else 0
        out["ladder_total"] = {"midrewardable": bool(ladder_week_due(st)),  # checkMidReward: POST /ladder/reward
                               "highelo": lad["high"][best], "hightype": LADDER_TYPES[best],
                               "playedgames": sum(lad["games"]), "totalwins": wins}
    return out


def room_player(me):
    return {"id": me["id"], "_id": me["id"], "name": me["name"], "levelinfo": me["levelinfo"], "teamid": me["teamid"],
            "summary": me["summary"], "captain": True}


def ladder_room_reply(room):
    return _json({"result": True, "resources": [{"uri": f"/room/{room['roomid']}", "body": room}]})


def room_change(change):
    """A handler for PUT /room/<id>/...: change(st, room, request) on my room, the room back."""
    def handler(*args):
        *groups, query, body = args
        st = _state(); room = st.get("room")
        if room is None:
            return _json({"result": False, "reason": "invalid room"})
        out = change(st, room, json.loads(body or b"{}"), *groups)
        _save(st)
        return out or ladder_room_reply(room)
    return handler


def _room_type(st, room, req):
    """PUT /room/<id>/type {type}: vs Teman / vs COM / vs Random in the custom room."""
    room["type"] = str(req.get("type") or room["type"])
    room["side"]["left"]["ready"] = room["side"]["right"]["ready"] = False
    if room_kind(room["type"]) == "vscom":
        room["side"]["right"]["players"] = []


def _room_ai(st, room, req):
    """PUT /room/<id>/AI {index}: the AI picker's nation / league / club / difficulty."""
    room["AI"]["index"].update({k: int(v) for k, v in (req.get("index") or {}).items()
                                if k in ("nation", "league", "team", "difficulty") and str(v).lstrip("-").isdigit()})


def _room_ready(st, room, req, side):
    """PUT /room/<id>/side/<side>/ready {ready}: Ready against the friend I invited, or Mulai vs
    COM: the match comes a second later (ladder_found)."""
    if side not in room["side"]:
        return _json({"result": False, "reason": "invalid side"})
    room["side"][side]["ready"] = ready = bool(req.get("ready", True))
    kind = room_kind(room["type"])
    if ready and side == "left" and room["stage"] == "room" and (
            kind == "vscom" or kind in ("friendly", "exhibition") and room["side"]["right"]["players"]):
        room["search"] = os.urandom(4).hex()
        threading.Timer(1, ladder_found, (room["search"],)).start()


def _room_leave(st, room, req):
    """PUT /room/<id>/leave: out of the room (RoomProxy finds me gone: ON_LEAVE)."""
    st.pop("room", None)
    gone = dict(room, side={**room["side"], "left": {"players": [], "ready": False}})
    return _json({"result": True, "resources": [{"uri": f"/room/{room['roomid']}", "body": gone}]})


def _room_invite(st, room, req, vid):
    """POST /room/<id>/invite/<coachid>: the friend joins the right side a few seconds later, ready."""
    if not vm_by_id(vid) or room_kind(room["type"]) not in ("friendly", "exhibition"):
        return _json({"result": False, "reason": "invalid coach"})
    threading.Timer(random.uniform(2, 4), room_join, (room["roomid"], vid)).start()
    return _json({"result": True})


def room_join(roomid, vid):
    with STATE_LOCK:
        st = _state(); room = st.get("room"); vm = vm_by_id(vid)
        if room and room["roomid"] == roomid and vm and room["stage"] == "room" and room_kind(room["type"]) in ("friendly", "exhibition"):
            room["side"]["right"].update(players=[room_vm(vm)], ready=True)
            if room_kind(room["type"]) == "exhibition":  # his own pick of the recommended clubs
                mine = room["side"]["left"].get("authTeamId")
                room["side"]["right"]["authTeamId"] = random.Random(vmseed(vid)).choice([t for t in EXHIBITION_TEAMS if t != mine])
            _save(st)
            messenger_push(f"/room/{roomid}", room)


def ep_room(query, body):
    """POST /room {slotsize, type, properties}: my room, in stage "room"."""
    req = json.loads(body or b"{}")
    st = _state(); me = coach_me(st, _default_slot(st))
    i = ladder_index(req.get("type"))
    room = {"roomid": int(time.time()) % 1000000, "type": req.get("type", "pvp_ladder_mgr"), "stage": "room",
            "slotsize": int(req.get("slotsize") or 1), "option": {}, "publicset": True, "state": None,
            **({"ladder": {"elo": my_ladder(st)["elo"][i]}} if room_kind(req.get("type", "ladder")) == "ladder" else {}),
            "AI": {"index": {"nation": 0, "league": 0, "team": 0, "difficulty": 2}}, "matchmaking": None,
            "side": {"left": {"players": [room_player(me)], "ready": False}, "center": {"players": []},
                     "right": {"players": [], "ready": False}, "referee": {"players": []}}}
    st["room"] = room
    _save(st)
    return ladder_room_reply(room)


def ladder_opponent(st, i, rng=random):
    """One of the five virtual managers nearest my rating on ladder i."""
    elo = my_ladder(st)["elo"][i]
    return rng.choice(sorted(virtual_managers(), key=lambda m: abs(m["elo"][i] - elo))[:5])


MESSENGER_PUSH = ("127.0.0.1", 28003)  # messenger.py PUSH_PORT
LADDER_SEARCH = (3, 7)  # seconds "searching" before the opponent is found


def messenger_push(uri, body):
    """Push a resource to the game over the messenger connection (best effort). One UDP datagram,
    framed by messenger.py with a u16 length: at most 64 KB (see push_match)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(json.dumps({"cmd": "push", "uri": uri, "body": body}, separators=(",", ":")).encode(), MESSENGER_PUSH)
    except OSError as e:
        print(f"messenger push {uri} failed: {e}", flush=True)


SHOP_CHANGED = 0  # ms of the last shop_changed(): GET /shop/list/clear


def shop_changed():
    """The Shop's products or prices changed on the admin page (issue #30: a preset chosen while
    the game ran left its Shop at the old prices). The game keeps /shop/list, /shop/list/STSHOP
    and /shop/products/* in StaticData for the whole session; StaticData's observer on the
    /shop/list/clear push drops them, so the Shop asks again the next time it opens (a tab
    already drawn keeps its prices until the game restarts). The same push twice does nothing
    (seen live: only the first {} cleared), so each change carries a new time, and GET
    /shop/list/clear (the game's read of an empty push) answers it."""
    global SHOP_CHANGED
    SHOP_CHANGED = max(int(time.time() * 1000), SHOP_CHANGED + 1)
    messenger_push("/shop/list/clear", {"changed": SHOP_CHANGED})


def push_match(match):
    """The match as pushed: every card of a big club (~165+) took it over the 64 KB a push can
    carry, so the Rank search never ended. Each side keeps the cards of its 18; the HTTP replies
    that follow (squad PUT, ready) carry the whole club."""
    m = copy.deepcopy(match)
    for side in m["side"].values():
        team = side.get("team") or {}
        squad = {str(p.get("id")) for p in (team.get("squad") or {}).get("positions", [])}
        kept = [p for p in team.get("players", []) if str(p.get("id")) in squad]
        if len(kept) >= len(squad):  # ids that do not match: keep them all
            team["players"] = kept
    return m


def ep_matchmaking(query, body):
    """POST /room/<id>/matchmaking: stage searching; a few seconds later the virtual opponent
    is found (ladder_found pushes the room, stage matched, the match in matchmaking.resources:
    the answer to this request is read before its callback starts the searching screen, so it
    cannot carry the match). DELETE (POST {_method: DELETE}): back to the room."""
    req = json.loads(body or b"{}")
    st = _state(); room = st.get("room")
    if room is None:
        return _json({"result": False})
    room["side"]["right"]["players"] = []
    if req.get("_method") == "DELETE":
        room.update(stage="room", matchmaking=None, search=None)
    else:
        room.update(stage="searching", matchmaking=None, search=os.urandom(4).hex())
        threading.Timer(random.uniform(*LADDER_SEARCH), ladder_found, (room["search"],)).start()
    _save(st)
    return ladder_room_reply(room)


def ep_room_back(query, body):
    """PUT /room/<id>/back (the Rank screen after a match): the room, /coach/me so the EP and EXP
    the match gave show at once, and /ladder/my/info (LadderMatchController's cached resource:
    the record panel is rebuilt from it; it was not fetched again after a match)."""
    st = _state(); room = st.get("room")
    res = coach_me_resources(st, _default_slot(st)) + [{"uri": "/ladder/my/info", "body": ladder_my_info(st)}]
    if room:
        room.update(stage="room", matchmaking=None, search=None)
        room["side"]["left"]["ready"] = False
        if room_kind(room["type"]) in ("ladder", "koth") or room["type"] == "pvp_random":
            room["side"]["right"]["players"] = []
        _save(st)
        res.append({"uri": f"/room/{room['roomid']}", "body": room})
    vm_say_result()
    return _json({"result": True, "resources": res})


def ladder_found(search):
    """The opponent is found: the room goes matched (the versus intro), and 2 s later the match
    goes to its squad stage (MatchController ON_SQUADING), as a PvP server does once both are in."""
    with STATE_LOCK:
        st = _state(); room = st.get("room")
        if room and room.get("search") == search and (  # not cancelled; a room's Ready not taken back
                room["stage"] == "searching" or room["stage"] == "room" and room["side"]["left"].get("ready")):
            ladder_match(st, room)
            _save(st)
            mid = st["match"]["id"]
            messenger_push(f"/room/{room['roomid']}", dict(room, matchmaking={"resources": [
                {"uri": f"/match/{mid}", "body": push_match(st["match"])}]}))
            threading.Timer(2, ladder_squad, (mid,)).start()


def ladder_squad(mid):
    with STATE_LOCK:
        st = _state(); match = st.get("match")
        # the one-user session connects at once and the client asks for squad itself
        if match and match["id"] == mid and match["stage"] in ("created", "squad"):
            match["stage"] = "squad"
            vm = vm_by_id((st.get("ladder_pending") or {}).get("opponent"))  # his name and ready back (see ladder_match)
            for p in match["side"]["right"]["players"]:
                p["name"] = vm["name"] if vm else "COM"
            match["side"]["right"]["ready"] = True
            _save(st)
            messenger_push(f"/match/{mid}", push_match(match))


def ladder_match(st, room):
    """The match into the room (stage matched): Rank against the virtual manager nearest my
    rating; a friendly against the one invited or a near one; vs COM against the room's AI club
    (room_kind)."""
    i, kind = ladder_index(room["type"]), room_kind(room["type"])
    me = coach_me(st, _default_slot(st)); teamid = me["teamid"]
    user = {"id": me["id"], "name": me["name"], "levelinfo": me["levelinfo"]}
    mine = match_side(st["teams"][str(_default_slot(st))], teamid, user, _tactics(st)[1])
    if kind == "vscom":
        vm, ai = None, room_ai_team(room)
        match = new_match("League_2_league", mine, match_side(roster_team(ai), ai))
        away = ai
    elif kind == "exhibition":  # each side the club picked in the room, at its own squad
        guest = room["side"]["right"]["players"]
        vm = (vm_by_id(guest[0]["id"]) if guest else None) or ladder_opponent(st, i)
        lt = room["side"]["left"].get("authTeamId") or EXHIBITION_TEAMS[0]
        away = room["side"]["right"].get("authTeamId") or EXHIBITION_TEAMS[1]
        mine = match_side(roster_team(lt), lt, user, [default_tactic(0)])
        theirs = match_side(roster_team(away), away, tactics=[style_tactic(vm_style(vm))])
        theirs["players"] = [{"id": vm["_id"], "name": vm["name"], "levelinfo": {"level": vm["level"], "exp": 0}}]
        mine["team"]["teamcolors"] = []
        match = new_match("League_2_league", mine, theirs)
    else:
        guest = room["side"]["right"]["players"] if kind == "friendly" else []
        vm = (vm_by_id(guest[0]["id"]) if guest else None) or (koth_opponent(st) if kind == "koth" else ladder_opponent(st, i))
        i = KOTH_LADDER if kind == "koth" else i
        match = new_match("League_2_league_MGR" if i == 3 else "League_2_league", mine, vm_side(vm))
        away = vm["teamid"]
    apply_prematch(st, match)  # the game setting screen's stadium / time / weather / length steppers
    if kind in ("ladder", "koth"):
        # a level score goes to extra time, then penalties (Enums.DRAWMODE_EXTRATIME; native end rule
        # 9: the half-end check plays two extra halves and a shootout if still level). The shootout
        # comes back as matchStats home/away.shootout (ladder_record / koth_record count it)
        match["gamesettings"]["drawMode"] = 1
    # Liga levels 10..24 by his rating; vs COM the picker's 0..4 (Amateur..Legendary, the game's own)
    match["gamesettings"]["difficulty"] = (max(0, min(4, int(room["AI"]["index"].get("difficulty") or 0))) if kind == "vscom"
                                           else 10 + min(14, (vm["elo"][i] - LADDER_START) // 55))
    match["leagueparam"] = {"matchtype": "ladder" if kind == "ladder" else "friendly", "round": 0, "home": teamid,
                            "away": away, "leagueid": 0}
    if vm:
        mine_elo = my_koth(st)["elo"] if kind == "koth" else my_ladder(st)["elo"][i]
        match["elo"] = {"left": {"rating": mine_elo}, "right": {"rating": vm["elo"][i]}}
    # The PvP type: MatchController.connectToTheSession opens the native session
    # (LobbyManager::ConnectSession), whose user reader (fifazf 0x93d200) takes "ip", "id",
    # "name" as strings (jsoncpp asCString crashes on a missing one), "privateip" as a list and,
    # for ladder types, summary.ladderelo as the 5 ratings (the manager one is ladderelo[len - 2]).
    match["type"] = {"ladder": "Ladder_MGR_PvP_1v1" if i == 3 else "Ladder_PvP_1v1", "friendly": "Friendly_PvP_1v1",
                     "vscom": "Friendly_PvE_1v1", "koth": "Regional_League", "exhibition": "Exhibition_PvP"}[kind]
    match["id"] = str(int(time.time()) % 1000000000)  # relaySessionInfo.matchid = parseInt(resourceId)
    match["relayServer"] = {"address": "127.0.0.1:28010", "forceRelay": True}  # read, never contacted
    for p in match["side"]["left"]["players"]:
        p.update(ip="127.0.0.1", privateip=[], summary={"ladderelo": my_ladder(st)["elo"][:5]})
    if kind == "koth":  # the native user reader's KOTH rating (matchstats type Regional_League)
        for side, elo in (("left", my_koth(st)["elo"]), ("right", vm["elo"][KOTH_LADDER])):
            for p in match["side"][side]["players"]:
                p["matchstats"] = [{"type": "Regional_League", "elorating": elo}]
            # PlayerListUtil takes a Regional_League card's stamina from es2stamina (no default:
            # my squad screen showed empty stamina bars)
            for card in match["side"][side]["team"]["players"]:
                card["es2stamina"] = card.get("stamina", 1)
    # The opponent stays a side user (generateCaptainFromMatchInfo, the game setting screen) but
    # nameless while the match is "created": connectToTheSession leaves users whose name is
    # undefined out of the native session, so it has one user and nobody to wait for (no relay
    # traffic at all) and the client's CPU plays his side. Unready too, as
    # processMatchSideReadyResource reads the captain's name; ladder_squad gives both back.
    for p in match["side"]["right"]["players"]:
        p.pop("name", None)
    match["side"]["right"]["ready"] = False
    st["match"] = match
    st["ladder_pending"] = {"id": match["id"], "index": i, "opponent": vm["_id"] if vm else None, "kind": kind,
                            "teamid": away, "record": {"vscom": "vscom", "koth": "koth", "exhibition": "exhibition"}.get(
                                kind, "quick" if room["type"] == "pvp_random" else "custom")}
    VM_CHAT_AFTER.clear()  # a result whose Rank screen never came back is not said on a later one
    room["side"]["right"]["players"] = [room_vm(vm)] if vm else []
    room.update(stage="matched", search=None, matchmaking={"resources": [{"uri": f"/match/{match['id']}", "body": match}]})


def ladder_result(st, mid, req):
    """A finished ladder match: my rating moves (Elo, K 32) and my record counts it."""
    p = st.get("ladder_pending") or {}
    if p.get("id") != mid:
        return
    # an opponent this server version does not know (the crowd changed while a match was pending)
    # plays at the start rating
    vm = vm_by_id(p["opponent"]) or {"elo": [LADDER_START] * LADDER_COUNT, "teamid": p.get("teamid")}
    if p.get("kind") == "exhibition":  # he played the club he picked
        vm = {**vm, "teamid": p["teamid"]}
        exhibition_played(st, 1)
    h, a = req.get("home") or {}, req.get("away") or {}
    mine, theirs = int(h.get("score") or 0), int(a.get("score") or 0)
    sh, sa = int(h.get("shootout") or 0), int(a.get("shootout") or 0)
    if str(h.get("id")) == str(vm["teamid"]) and str(a.get("id")) != str(h.get("id")):
        mine, theirs, sh, sa = theirs, mine, sa, sh
    if p.get("kind", "ladder") == "ladder":
        ladder_record(st, p["index"], vm, mine, theirs, sh, sa)
    elif p["kind"] == "koth":
        old, new, streak = koth_record(st, vm, mine, theirs, sh, sa)
        if (st.get("match") or {}).get("id") == mid and "reward" in st["match"]:  # the reward screen's "Poin Divisi"
            st["match"]["reward"].update(preveloinfo={"elo": old}, eloinfo={"elo": new, "streak": streak, "goaldiff": mine - theirs})
    else:
        friendly_record(st, p, vm, mine, theirs, sh, sa)
    st.pop("ladder_pending")
    VM_CHAT.pop(mid, None)
    if "_id" in vm:  # his word on it once I am back on the Rank screen (vm_say_result)
        VM_CHAT_AFTER["last"] = {"vm": vm["_id"], "outcome": "win" if (theirs, sa) > (mine, sh) else "loss" if (mine, sh) > (theirs, sa) else "draw"}
    if st.get("room"):
        st["room"].update(stage="room", matchmaking=None)
        if "ladder" in st["room"]:
            st["room"]["ladder"] = {"elo": my_ladder(st)["elo"][p["index"]]}


def vm_by_id(vid):
    return next((m for m in virtual_managers() if m["_id"] == vid), None)


def multiplay_add(st, mtype, vm, mine, theirs, sh, sa):
    """Manager > Info Manager > "Hasil tanding multiplayer" (CoachMultiResultTabPage): his crest,
    club, score and the type (Ranking / Rank Manager / Persahabatan); a coachid opens his profile."""
    me = my_coach(st)
    me["multiplay"] = (me.get("multiplay", []) + [{
        "type": mtype, "date": w3c(time.time()), "forfeits": 0,
        "team": {"teamid": me["teamid"], "score": mine, "shootout": sh, "coaches": [{"coachid": me["_id"], "name": me["name"]}]},
        "opponent": {"teamid": vm.get("teamid"), "score": theirs, "shootout": sa, "clubname": "",
                     "coaches": [{"coachid": vm["_id"], "name": vm["name"]}] if vm.get("_id") else []}}])[-MULTIPLAY_MAX:]


def friendly_record(st, p, vm, mine, theirs, sh, sa):
    """A friendly's result: statistics.friendly[quick | custom | vscom] (1 vs 1 row) and the
    multiplay list."""
    r = coach_record(my_coach(st), "friendly")
    row = r.setdefault(p["record"], {k: [0] * 5 for k in ("wins", "draws", "losses")})
    res = "wins" if (mine, sh) > (theirs, sa) else "losses" if (mine, sh) < (theirs, sa) else "draws"
    row[res][0] += 1
    if p["record"] == "quick":  # Quick match's best win streak
        r["streak"] = r.get("streak", 0) + 1 if res == "wins" else 0
        r["best"] = max(r.get("best", 0), r["streak"])
    multiplay_add(st, {"vscom": "Friendly_PvE_1v1", "exhibition": "Exhibition_PvP"}.get(p["kind"], "Friendly_PvP_1v1"),
                  vm, mine, theirs, sh, sa)


LADDER_RECENT, MULTIPLAY_MAX = 3, 20  # record screen's last matches (RANKEDMATCH_RECENT_MATCH_MAX); Info Manager's list


def ladder_record(st, i, vm, mine, theirs, sh=0, sa=0):
    """My ladder-i rating moves after a match against vm (Elo, K 32); the record counts it. A
    shootout (sh:sa) decides a level score."""
    lad = my_ladder(st)
    score = 1 if (mine, sh) > (theirs, sa) else 0 if (mine, sh) < (theirs, sa) else 0.5
    old = lad["elo"][i]
    expect = 1 / (1 + 10 ** ((vm["elo"][i] - lad["elo"][i]) / 400))
    lad["elo"][i] = max(LADDER_START, round(lad["elo"][i] + LADDER_K * (score - expect)))
    lad["high"][i] = max(lad["high"][i], lad["elo"][i])
    lad["games"][i] += 1
    lad[{1: "wins", 0: "losses", 0.5: "draws"}[score]][i] += 1
    day, hist = time.strftime("%Y%m%d", time.gmtime()), lad["elos"][i]  # one point a day for the graph
    if hist and hist[-1][1] == day:
        hist[-1][0] = lad["elo"][i]
    else:
        hist.append([lad["elo"][i], day])
    results = lad.setdefault("results", [[] for _ in range(LADDER_COUNT)])
    results[i] = (results[i] + [score])[:LADDER_DEPLOY]  # the placement matches' results
    if lad["games"][i] == LADDER_DEPLOY:  # placement over: the result popup's "to" rating
        lad.setdefault("placed", [None] * LADDER_COUNT)[i] = lad["elo"][i]
    now = w3c(time.time())
    if score != 0.5:  # the record screen's last matches (its streak has no draw; Info Manager shows it)
        recent = lad.setdefault("recent", [[] for _ in range(LADDER_COUNT)])
        prev = recent[i][-1]["streak"] if recent[i] else 0
        streak = (prev + 1 if prev > 0 else 1) if score == 1 else (prev - 1 if prev < 0 else -1)
        recent[i] = (recent[i] + [{"date": now, "goals": mine, "goalsconceded": theirs, "shootout": sh,
                                   "shootoutconceded": sa, "streak": streak, "elodiff": lad["elo"][i] - old,
                                   "elo": lad["elo"][i], "forfeits": 0}])[-LADDER_RECENT:]
        if sh or sa:  # "(Penalti x Menang y Kalah)"
            lad.setdefault("swins" if score == 1 else "slosses", [0] * LADDER_COUNT)[i] += 1
    multiplay_add(st, LADDER_TYPES[i] if i < len(LADDER_TYPES) else LADDER_TYPES[0], vm, mine, theirs, sh, sa)
    me = my_coach(st)
    # Info Manager's Rank record: matches, and per ladder the best rank and the best win streak
    r = coach_record(me, "ladder")
    r["count"] = r.get("count", 0) + 1
    run, best, top = (r.setdefault(k, [0] * LADDER_COUNT) for k in ("streak", "winningstreak", "rank"))
    run[i] = run[i] + 1 if score == 1 else 0
    best[i] = max(best[i], run[i])
    pos = next(k for k, e in enumerate(ladder_board(st, i), 1) if e[2] == me["_id"])
    top[i] = min(top[i] or pos, pos)
    # this ISO week's wins (ladder_my_info ladder_total), on the coach: a season can start mid-week
    ladder_week(st)["wins"] += score == 1


# --------------------------------------------------------------------------- #
# Virtual managers' chat (issue #30, part 3): the Rank opponent chats during the match, in his
# personality and in the language set on the admin page (gamedata/vm_chat.json). The match chat
# overlay (OverlayChatScreen; MatchController's observer on /match/<id>/messages/latest) shows a
# messenger push of [{type "chat", target, coachname, body, myside}] as "coachname: body", gray
# when myside is false; it listens in session matches only (Rank, not Liga/Cup). One line per
# push: every item of the array is shown again. Triggers come from the game's PUT
# /match/<id>/gameplay/event/<n> {type STARTHALF | SHOT | GOAL_TYPE_* | FOUL_*, teamid 0 = home
# = me, 1 = him}: there is no save event, so a SHOT with no GOAL of that team within
# VM_CHAT_SHOT s is his miss or my shot saved. The result line waits for the Rank screen (the
# result screen hides the overlay and the banners) and goes out as an /alert/<key>/latest banner.
# The client shows a typed line only when it comes back: POST /match/<id>/messages
# {message, target ""|"team"} is pushed back with myside true. The overlay masks every substring
# found in the client's localization/FORBIDDENWORDS_CHAT_<locale>.json (ENG or IND here: eng.big,
# ind.big), so the lines in responses/vm_chat.json avoid them ("masuk" has "asu", "cuma" "cum",
# "santai" "tai", "pass" "ass"; test_league.py checks); no emoji (the font has none).
# --------------------------------------------------------------------------- #
VM_CHAT_FILE = os.path.join(HERE, "gamedata", "vm_chat.json")
VM_CHAT_DEFAULT = {"enabled": True, "lang": "id", "newsregions": []}  # newsregions: the global chat's news besides the global
VM_CHAT_LANGS = {"id": "Bahasa Indonesia", "en": "English", "vi": "Tiếng Việt"}  # responses/vm_chat.json
VM_PERSONALITIES = ("friendly", "competitive", "funny", "confident", "playful")
VM_CHAT_ODDS = {"hello": 0.9, "goal": 1, "conceded": 0.85, "miss": 0.5, "save": 0.45, "reply": 0.6}
VM_CHAT_GAP, VM_CHAT_SHOT = 6, 4  # s between his lines (goals excepted); s for a shot to become a goal
VM_CHAT = {}  # match id -> {"last": monotonic time of his last line, "used": lines said, "shots": {teamid: sqid}}
VM_CHAT_AFTER = {}  # "last": {vm, outcome} of the last Rank result, said back on the Rank screen


def vm_chat_cfg():
    try:
        with open(VM_CHAT_FILE, encoding="utf-8") as f:
            cfg = {**VM_CHAT_DEFAULT, **json.load(f)}
        cfg = cfg if cfg["lang"] in VM_CHAT_LANGS else {**cfg, "lang": VM_CHAT_DEFAULT["lang"]}
        return {**cfg, "newsregions": [r for r in NEWS_REGIONS if r in (cfg.get("newsregions") or [])]}
    except (OSError, ValueError, TypeError):  # missing, broken, or not an object
        return dict(VM_CHAT_DEFAULT)


def vm_personality(vm):
    return random.Random(vmseed(vm["_id"])).choice(VM_PERSONALITIES)


def vm_chat_line(vm, trigger, used=()):
    """His line for trigger in the admin page's language and his personality; not one he said
    this match while he has another."""
    with open(os.path.join(RESPONSES, "vm_chat.json"), encoding="utf-8") as f:
        lines = json.load(f)[vm_chat_cfg()["lang"]][trigger][vm_personality(vm)]
    return random.choice([s for s in lines if s not in used] or lines)


def vm_say(mid, vm, trigger, delay):
    """His line for trigger into the match chat after delay s: by the trigger's odds, and not
    within VM_CHAT_GAP s of his last one unless it is about a goal."""
    if not vm_chat_cfg()["enabled"]:
        return
    s = VM_CHAT.setdefault(mid, {"last": 0, "used": set(), "shots": {}})
    now = time.monotonic()
    if random.random() >= VM_CHAT_ODDS[trigger] or (trigger not in ("goal", "conceded") and now - s["last"] < VM_CHAT_GAP):
        return
    s["last"] = now
    line = vm_chat_line(vm, trigger, s["used"])
    s["used"].add(line)
    threading.Timer(delay, messenger_push, (f"/match/{mid}/messages/latest", [
        {"type": "chat", "target": "", "coachname": vm["name"], "body": line, "myside": False}])).start()


def ladder_opponent_of(st, mid):
    """The virtual manager of Rank match mid, if that is the pending one."""
    p = st.get("ladder_pending") or {}
    return vm_by_id(p.get("opponent")) if p.get("id") == mid else None


def ep_gameplay_event(mid, n, query, body):
    """PUT /match/<id>/gameplay/event/<n> (the game's own request, during every local match;
    only "result" of the reply is read): his chat triggers in a Rank match."""
    ev = json.loads(body or b"{}")
    vm = ladder_opponent_of(_state(), mid)
    if vm:
        kind, team = str(ev.get("type", "")), ev.get("teamid")
        shots = VM_CHAT.setdefault(mid, {"last": 0, "used": set(), "shots": {}})["shots"]
        if kind == "STARTHALF" and ev.get("halftype") == "FIRST_HALF":
            vm_say(mid, vm, "hello", 3)
        elif kind.startswith("GOAL_TYPE"):
            # an own goal's teamid is the side that put it in its own net (fifazf 0xb43fb0 flips it)
            side = 1 - team if kind == "GOAL_TYPE_OWN_GOAL" and team in (0, 1) else team
            shots.pop(side, None)
            vm_say(mid, vm, "goal" if side == 1 else "conceded", 4)
        elif kind == "SHOT":
            shots[team] = n
            threading.Timer(VM_CHAT_SHOT, vm_shot_missed, (mid, vm, team, n)).start()
    return _json({"result": True})


def vm_shot_missed(mid, vm, team, n):
    with STATE_LOCK:  # a timer: no goal came of that shot, and the match is still on
        if ladder_opponent_of(_state(), mid) and VM_CHAT.get(mid, {}).get("shots", {}).get(team) == n:
            vm_say(mid, vm, "miss" if team == 1 else "save", 0.5)


def ep_match_messages(mid, query, body):
    """POST /match/<id>/messages {message, target} from the match chat overlay and the squad
    screen: the line comes back to me (myside true; the client shows nothing of its own), and in
    a Rank match he may answer. A post with "type" comes from the game itself (HTML), not typed."""
    req = json.loads(body or b"{}")
    text, target = str(req.get("message") or "")[:200], req.get("target") or ""
    st = _state()
    if text and "type" not in req:
        messenger_push(f"/match/{mid}/messages/latest", [
            {"type": "chat", "target": target, "coachname": my_coach(st)["name"], "body": text, "myside": True}])
        vm = ladder_opponent_of(st, mid)
        if vm and target != "team":
            vm_say(mid, vm, "reply", 2.5)
    return _json({"result": True})


def vm_say_result():
    """After a Rank match, back on the Rank screen: his word on the result as a banner
    (/alert/<key>/latest shows the last item as bold HTML for 10 s)."""
    after = VM_CHAT_AFTER.pop("last", None)
    vm = vm_by_id(after["vm"]) if after else None
    if vm and vm_chat_cfg()["enabled"]:
        line = vm_chat_line(vm, after["outcome"])
        text = f"<FONT COLOR='#ffcc00'>{html.escape(vm['name'])}</FONT>: {html.escape(line, quote=False)}"
        threading.Timer(1.5, messenger_push, ("/alert/vm/latest", [text])).start()


# Path-pattern routes (ids in the path); checked after the exact ENDPOINTS.
# --------------------------------------------------------------------------- #
# Clubs (issue #36: "Team creation not working ... add AI members and AI-controlled teams").
# Komunitas > Klub (ClubScreen and its tab pages; ClubProxy, ClubController) reads /club/me
# (ClubProxy.clubInfo; _id must be a string or clubid stays null) and the coach's club {clubid}
# (/coach/me; null = none, the list's create button). Founding: GET /club/fee/creation {fee}
# (the confirm dialog's EP), POST /club/create {name, description}. "Klub telah dibuat"'s
# Konfirmasi is enabled by ClubEvent.ON_CLUB_INFO_CHANGED, which only a /club/me fed by a write
# reply's resources sends (a GET fires ON_COMPLETE only), so every write reply here carries
# /club/me and /coach/me. A failure is {result false, reason} with ClubCreationScreen's reasons
# ("name is already used", "not enough money", "invalid name", ...). ClubScreen.refreshTabButton
# reads clubInfo.member.grade unguarded (master 10, submaster 8, manager 6, member 1; the
# Manajemen tab from 6); the info page reads rewards[i] (the week's club rewards: MATCH_PLUS_*
# boosts, value x100 = %) and member.rewards[day] (seven, one per weekday counted from resetat =
# the club day's start in minutes after 00:00 UTC).
# AI clubs (CLUB_AI): virtual managers, seeded and not stored. CLUB_IN of the crowd belong to one
# (drawn once per manager), the master is its highest level member; descriptions, news, board
# posts and chat come from responses/vm_club_chat.json in the chat language (admin Chat AI tab).
# I join one at once (POST /club/<id>/join) or found my own (st["club"]): managers without a club
# ask to join it over time (Manajemen > permohonan; they join at once while it takes members
# without a check). Members are online by their Rank hours (vm_status), earn activity points by
# their seeded days, and talk in the Klub chat tab: POST /club/me/message {message} comes back as
# one push to /club/me/message {type "chat", coachid, coachname, message} (MessagingGroupProxy's
# observer; a clubid other than mine would be filtered), online members answer it, greet newcomers
# and cheer my results. The week's club points give their boosts to my match rewards, and my own
# points of a day (CLUB_DAY_GOAL) pay CLUB_DAY_REWARD EP to the Bag.
# Left out: the club facility and club ranking (their features are off in account_base.json),
# club matches (PvP rooms) and emblems (the default crest shows).
# --------------------------------------------------------------------------- #
CLUB_FEE, CLUB_EXPAND_FEE = 10000, 5000  # EP: founding a club; one expansion
CLUB_LIMIT, CLUB_EXPAND, CLUB_LIMIT_MAX = 30, 10, 60  # members: a new club, one expansion, at most
CLUB_AI, CLUB_IN = 60, 0.6  # AI clubs; the share of virtual managers in one
CLUB_SUFFIX = ("FC", "United", "Squad", "Legends", "Warriors", "Elite", "Academy", "Stars", "Family", "Army",
               "Crew", "Club", "Brothers", "Rangers", "Titans")
CLUB_COLORS = (1203168, 13382451, 3381555, 16750848, 10027161, 39372, 16737843, 6697932, 13421619, 3355647)
CLUB_LEVELS = (0, 1500, 5000, 11000, 20000, 32000, 48000, 68000, 92000, 120000)  # total points of Lv.1..10
CLUB_REWARDS = ((300, "MATCH_PLUS_COACHEXP", 0.05), (800, "MATCH_PLUS_GAMEMONEY", 0.05),
                (1500, "MATCH_PLUS_PLAYEREXP_ALL", 0.05), (2500, "MATCH_PLUS_GAMEMONEY", 0.1))  # week's points -> boost
CLUB_DAY_GOAL, CLUB_DAY_REWARD = 30, 1000  # my points of a day -> EP to the Bag
CLUB_DAY_LIMIT, CLUB_WEEK_LIMIT, CLUB_MEMBER_DAY = 3000, 15000, 100  # point caps: club a day / a week, me a day
CLUB_ASK = 3 * 3600  # s: about one manager without a club asks to join my open club this often
CLUB_RESET = 17 * 60  # resetat: the club day starts 00:00 WIB = 17:00 UTC (minutes)
CLUB_STATIC = {"club_pre_members_max": 20, "club_dissolution_time": 60, "club_instant_dissolution_lv": 1}  # minutes
CLUB_REPLY = (0.8, 0.3)  # odds that an online member answers my chat line; that a second one does too
REQUEST = {"method": "GET"}  # the request being handled (H._respond, under STATE_LOCK)


def club_day(ts=None):
    """The club day number (WIB days since 1970)."""
    return int((time.time() if ts is None else ts) + WIB) // 86400


def club_activity():
    """CLUBACTIVITYPOINTS {kind: {points}} (the info page's tooltip lists them)."""
    with open(os.path.join(RESPONSES, "static_db_CLUBACTIVITYPOINTS.json"), encoding="utf-8") as f:
        return {k: v["points"] for k, v in json.load(f).items()}


@functools.lru_cache(maxsize=1)
def club_chat_file(mtime):
    with open(os.path.join(RESPONSES, "vm_club_chat.json"), encoding="utf-8") as f:
        return json.load(f)


def club_line(vm, trigger, seed=None):
    """His line for trigger in the chat language and his personality (seed: the same line for
    the same seed)."""
    lines = club_chat_file(os.path.getmtime(os.path.join(RESPONSES, "vm_club_chat.json")))[
        vm_chat_cfg()["lang"]][trigger][vm_personality(vm)]
    return (random.Random(seed) if seed is not None else random).choice(lines)


def club_vm_day(vid, day, keen):
    r = random.Random(vmseed(f"clubpts:{vid}:{day}"))
    return (10 if r.random() < 0.8 else 0) + 2 * round(keen * r.choice((0, 1, 2, 3, 4, 6)))


@functools.lru_cache(maxsize=8192)
def club_vm_points(vid, d0, d1):
    """Points manager vid earned on club days d0..d1: day by day for the last week, his average before
    (a club's history goes back before the crowd's VM_EPOCH)."""
    keen = random.Random(vmseed("clubkeen" + vid)).uniform(0.5, 1.5)
    exact = max(d0, d1 - 7)
    return max(0, exact - d0) * round(8 + 6 * keen) + sum(club_vm_day(vid, d, keen) for d in range(exact, d1 + 1))


def club_level(total):
    """(level, its first points, the next level's) for a club's total points."""
    lv = sum(total >= p for p in CLUB_LEVELS)
    return lv, CLUB_LEVELS[lv - 1], CLUB_LEVELS[lv] if lv < len(CLUB_LEVELS) else CLUB_LEVELS[-1]


def ai_clubs_now():
    return ai_clubs(club_day(), vm_chat_cfg()["lang"])


@functools.lru_cache(maxsize=2)
def ai_clubs(day, lang):
    """The AI clubs on club day `day` (texts in lang): {id: view} (club_view's shape). Who is in
    which club is drawn once per manager (seeded by his id), the first clubs the biggest; grades
    by level."""
    names = [f"{w} {s}" for w in VM_WORDS2 for s in CLUB_SUFFIX]
    random.Random(vmseed("clubnames")).shuffle(names)
    weights = [1 / (k + 10) for k in range(CLUB_AI)]
    clubs = {}
    for k in range(CLUB_AI):
        r = random.Random(vmseed(f"club{k}"))
        created = VM_EPOCH - r.randint(20, 700) * 86400
        clubs[f"club{k + 1:04d}"] = {
            "_id": f"club{k + 1:04d}", "name": names[k], "created": created, "color": r.choice(CLUB_COLORS),
            "allowjoin": r.random() < 0.85, "skipjudgement": True, "limit": CLUB_LIMIT + CLUB_EXPAND * (k < 8),
            "seed": k, "members": []}
    for m in sorted(virtual_managers(), key=lambda m: (-m["level"], m["_id"])):
        r = random.Random(vmseed("clubof" + m["_id"]))
        if r.random() < CLUB_IN:
            c = clubs[f"club{r.choices(range(CLUB_AI), weights)[0] + 1:04d}"]
            if len(c["members"]) < c["limit"] - 2:  # two seats left: I can always join
                c["members"].append({"id": m["_id"], "name": m["name"], "level": m["level"], "vm": m,  # joined in its life
                                     "joined": c["created"] + r.randint(0, max(0, (VM_EPOCH - c["created"]) // 86400)) * 86400})
    for c in clubs.values():
        for i, m in enumerate(c["members"]):
            m["grade"] = 10 if i == 0 else 8 if i == 1 else 6 if i < 4 else 1
        master = c["members"][0]["vm"] if c["members"] else {"_id": "vm0000"}
        c.update(description=club_line(master, "description", c["_id"]), descat=c["created"],
                 news=club_line(master, "news", f"{c['_id']}:{day // 7}"),
                 newsby=c["members"][0]["name"] if c["members"] else "", newsat=day // 7 * 7 * 86400 - WIB + 9 * 3600)
    return {k: c for k, c in clubs.items() if c["members"]}


def club_free(st):
    """Virtual managers in no club (AI or mine), weakest first."""
    taken = {m["id"] for c in ai_clubs_now().values() for m in c["members"]}
    c = st.get("club") or {}
    taken |= {m["id"] for m in c.get("members", [])} | {r["id"] for r in c.get("requests", [])}
    return [m for m in virtual_managers() if m["_id"] not in taken]


def club_me(st):
    """My own club activity record (in st["club"]["me"]): {days {day: {act: points}}, total}."""
    return st["club"].setdefault("me", {"days": {}, "total": 0})


def club_earn(st, act, kind=None):
    """My club activity: act (CLUB_POINT_LIST_<act>) worth CLUBACTIVITYPOINTS[kind or act]; the
    day's goal pays its EP once."""
    if not st.get("club"):
        return
    me, today = club_me(st), str(club_day())
    day = me["days"].setdefault(today, {})
    n = min(club_activity().get(kind or act.title(), 0), CLUB_MEMBER_DAY - sum(day.values()))
    if n <= 0:
        return
    day[act] = day.get(act, 0) + n
    me["total"] += n
    me["days"] = {d: v for d, v in me["days"].items() if int(d) > int(today) - 8}
    coach = my_coach(st)  # on the manager: leaving and joining again starts a new club record
    if sum(day.values()) >= CLUB_DAY_GOAL and coach.get("clubpaid") != int(today):
        coach["clubpaid"] = int(today)
        bag_add(st, "Hadiah klub harian", [{"itemid": EP_ITEM, "count": CLUB_DAY_REWARD}])


def club_view(st):
    """My club as club_body takes it ({_id, name, created, members [{id, name, level, grade,
    joined, vm}], ...}; me first in my own club), or None."""
    c = st.get("club")
    if not c:
        return None
    coach = my_coach(st)
    me = {"id": coach["_id"], "name": coach["name"], "level": coach_levelinfo(coach)["level"], "vm": None}
    vms = {m["_id"]: m for m in virtual_managers()}
    if c.get("ai"):
        v = ai_clubs_now().get(c["ai"])
        if v is None:  # gone (its members all left): so am I
            st["club"] = None
            return None
        return {**v, "members": [{**m, "vm": vms.get(m["id"], m["vm"])} for m in v["members"]]
                + [{**me, "grade": 1, "joined": c["joined"]}]}
    members = [{**me, "grade": 10, "joined": c["created"]}] + [
        {"id": m["id"], "name": vms[m["id"]]["name"], "level": vms[m["id"]]["level"], "grade": m["grade"],
         "joined": m["joined"], "vm": vms[m["id"]]} for m in c["members"] if m["id"] in vms]
    return {**c, "members": members}


def club_points(st, v, member):
    """(total, weekly, today) points of club view v, or of one member of it."""
    today, week0 = club_day(), club_day(week_end() - 7 * 86400)
    out = [0, 0, 0]
    for m in [member] if member else v["members"]:
        if m["vm"] is None:
            me = club_me(st)
            days = {int(d): sum(x.values()) for d, x in me["days"].items()}
            pts = (me["total"], sum(n for d, n in days.items() if d >= week0), days.get(today, 0))
        else:
            d0 = club_day(m["joined"])
            pts = (club_vm_points(m["id"], d0, today), club_vm_points(m["id"], max(d0, week0), today) if today >= d0 else 0,
                   club_vm_day(m["id"], today, random.Random(vmseed("clubkeen" + m["id"])).uniform(0.5, 1.5)) if today >= d0 else 0)
        out = [a + b for a, b in zip(out, pts)]
    return out


def club_member_row(st, m):
    """A row of GET /club/me/members (ClubMemberListTabPage)."""
    total, weekly, _ = club_points(st, None, m)
    if m["vm"] is None:
        coach = my_coach(st)
        div, status = ladder_division(my_ladder(st)["elo"][0]), "online"
    else:
        div, status = ladder_division(m["vm"]["elo"][0]), vm_status(m["vm"])
    seen = time.time() if status != "offline" else time.time() - random.Random(vmseed(f"seen:{m['id']}:{club_day()}")).randint(3600, 20 * 3600)
    return {"_id": m["id"], "coachid": m["id"], "name": m["name"], "level": m["level"],
            "division": f"DIVISION_{div}", "currdivision": f"DIVISION_{div}", "actpoints": {"weekly": weekly, "total": total},
            "joinedat": w3c(m["joined"]), "connectedat": w3c(seen), "donates": None, "grade": m["grade"],
            "online": status != "offline"}


def club_body(st, v, mine=False):
    """The club resource (/club/me, /club/<id>, the list's rows)."""
    total, weekly, today = club_points(st, v, None)
    level, lo, hi = club_level(total)
    master = v["members"][0]
    end = week_end()
    month = int(time.strftime("%Y%m", time.gmtime(time.time() + WIB)))
    body = {
        "_id": v["_id"], "name": v["name"], "createdat": w3c(v["created"]), "state": "inactive" if v.get("deleteat") else "active",
        "description": {"message": v["description"], "changedat": w3c(v["descat"])},
        "news": {"message": v["news"], "coachname": v["newsby"], "changedat": w3c(v["newsat"])},
        "allowjoin": v["allowjoin"], "skipjudgement": v["skipjudgement"], "color": v["color"],
        "masterid": master["id"], "mastername": master["name"], "masterlevel": master["level"],
        "membercount": len(v["members"]), "limits": {"members": v["limit"], "submasters": 2},
        "levelinfo": {"level": level, "from": lo, "to": hi, "actpoints": {
            "total": total, "weekly": weekly, "today": today, "weeklycached": weekly,
            "limits": {"weekly": CLUB_WEEK_LIMIT, "today": CLUB_DAY_LIMIT}, "resetat": CLUB_RESET,
            "weeklybegindate": w3c(end - 7 * 86400), "weeklyenddate": w3c(end - 1)}},
        "rewards": [{"points": p, "boost": {"type": t, "value": x}} for p, t, x in CLUB_REWARDS],
        "statistics": {"friendly": {"wins": 0, "total": 0, "forfeitwins": 0, "recent": {"month": month, "wins": 0, "forfeitwins": 0}},
                       "ranked": {"recent": {"wins": 0, "losses": 0, "total": 0, "wprating": 0, "sid": 0, "highrecord": {"wp": 0}},
                                  "highrecord": {"wp": 0, "rank": 0}, "last": {"rank": 0, "wprating": 0}}},
        "member": {"grade": -2}}  # the list's status: -2 none (may ask), -1 "Ditolak", 0 "Menunggu", 1+ "Bergabung"
    if v.get("deleteat"):
        body["willbedeletedat"] = w3c(v["deleteat"])
    if mine:
        me = next(m for m in v["members"] if m["vm"] is None)
        mt, mw, md = club_points(st, v, me)
        acts = club_me(st)["days"].get(str(club_day()), {})
        body["member"] = {
            "grade": me["grade"], "limits": {"today": CLUB_MEMBER_DAY},
            "actpoints": {"today": md, "weekly": mw, "total": mt, "activities": [{"actName": k, "point": n} for k, n in acts.items()]},
            "rewards": [{"goalpoints": CLUB_DAY_GOAL, "reward": {"name": "EP", "count": CLUB_DAY_REWARD}}] * 7,
            "reward": {"todaypoint": md, "prev": {"step": 0, "goalpoints": 0},
                       "next": {"step": 1, "goalpoints": CLUB_DAY_GOAL, "reward": {"clubep": 0, "expandme": 0}}},
            "statistics": {"ranked": {"recent": {"wins": 0, "losses": 0, "sid": 0}}}}
    return body


def club_boosts(st):
    """{boost type: value} my club's points of this week give my matches."""
    v = club_view(st)
    if not v:
        return {}
    weekly = club_points(st, v, None)[1]
    out = {}
    for p, t, x in CLUB_REWARDS:
        if weekly >= p:
            out[t] = out.get(t, 0) + x
    return out


def club_tick(st, now=None):
    """What happened in my club since the last look: the day's login points, managers who asked
    to join (joined at once while it takes members without a check), a dissolution that came due."""
    c = st.get("club")
    if not c:
        return
    now = time.time() if now is None else now
    if c.get("deleteat") and now >= c["deleteat"]:
        st["club"] = None
        return
    if "LOGIN" not in club_me(st)["days"].get(str(club_day()), {}):
        club_earn(st, "LOGIN", "Login")
    if c.get("ai") or not c.get("allowjoin"):
        c["next"] = max(c.get("next", now), now)  # closed: nobody queues up meanwhile
        return
    c["next"] = max(c.get("next", now), now - 4 * CLUB_ASK)  # a long absence brings a few, not a flood
    free = club_free(st)
    while free and c["next"] <= now and len(c["requests"]) < CLUB_STATIC["club_pre_members_max"] and len(c["members"]) + 1 < c["limit"]:
        at = c["next"]
        rnd = random.Random(f"{c['_id']}:{int(at)}")  # the same arrivals however often it is looked at
        c["next"] = at + CLUB_ASK * rnd.uniform(0.3, 1.7)
        vm = free.pop(rnd.randrange(len(free)))
        if c["skipjudgement"]:
            club_add_member(st, vm, at)
        else:
            c["requests"].append({"id": vm["_id"], "at": at})


def club_add_member(st, vm, at=None):
    c = st["club"]
    c["members"].append({"id": vm["_id"], "grade": 1, "joined": time.time() if at is None else at})
    c["requests"] = [r for r in c["requests"] if r["id"] != vm["_id"]]
    if at is None or time.time() - at < 60:  # one joining now says hello, a member welcomes him
        club_say(vm, "joined", 2)
        club_chat_reply(st, "welcome", 5, besides=vm["_id"])


def chat_push(uri, who, text, delay=0, **extra):
    """A line {type, coachid, coachname, message, time} into a chat tab, now or after delay s. time
    (W3C, when it is said) is the line's clock (TextArea.getHeaderByTarget); without it every line
    showed 7:00 (the epoch in WIB)."""
    go = lambda: messenger_push(uri, {"type": "chat", "coachid": who["_id"], "coachname": who["name"], "message": text,
                                      **extra, "time": w3c(time.time())})
    threading.Timer(delay, go).start() if delay else go()


def club_say(vm, trigger, delay):
    """Member vm's line for trigger into the Klub chat tab after delay s."""
    if vm_chat_cfg()["enabled"]:
        chat_push("/club/me/message", vm, club_line(vm, trigger), delay or 0.01)


def club_online(st, besides=None):
    v = club_view(st)
    return [m["vm"] for m in v["members"] if m["vm"] and m["id"] != besides and vm_status(m["vm"]) != "offline"] if v else []


def club_chat_reply(st, trigger, delay, besides=None, odds=(1, 0)):
    """One online member (maybe a second) says trigger's line."""
    online = club_online(st, besides)
    random.shuffle(online)
    for i, vm in enumerate(online[:2]):
        if random.random() < odds[i]:
            club_say(vm, trigger, delay + 3 * i + random.uniform(0, 3))


CLUB_HELLO = ("halo", "hai", "hi", "hello", "hey", "pagi", "siang", "sore", "malam", "chao", "chào", "xin chào", "assalam")
CLUB_THANKS = ("makasih", "terima kasih", "thanks", "thank", "thx", "cảm ơn", "cam on", "tq")


def ep_club_message(query, body):
    """POST /club/me/message {message, hint}: my line comes back to me (the client shows nothing
    of its own) and online members may answer."""
    req = json.loads(body or b"{}")
    text = str(req.get("message") or "")[:200]
    st = _state()
    if text and st.get("club"):
        coach = my_coach(st)
        chat_push("/club/me/message", coach, text, hint=req.get("hint"))
        low = text.lower()
        trigger = ("thanks" if any(w in low for w in CLUB_THANKS) else
                   "hello" if any(re.search(rf"\b{re.escape(w)}\b", low) for w in CLUB_HELLO) else "reply")
        club_chat_reply(st, trigger, 2, odds=CLUB_REPLY)
    return _json({"result": True})


def club_after_match(st, won, act):
    """A match of mine just ended: its activity points, and my club cheers the result."""
    if not st.get("club"):
        return
    club_earn(st, act, {"LEAGUE": "Match_League_league", "CUP": "Match_Cup_PvE"}.get(act, "Match_general"))
    club_chat_reply(st, ("loss", "draw", "win")[won + 1], 8, odds=(0.7, 0.2))


# Global chat (user: "global chat belum ada"): the Chatting window's Channel tab. ChattingController
# joins a channel (POST /chat/channel {channelid} or PUT /chat/channel/<id> -> {result, channelid,
# created, reason}; GET /chat/coach/<name>/channel/detail {result, channelid, coachid} for "go to
# his channel"); a line is POST /chat/my/channel/message {message, hint}, and every line of the
# channel, mine too (the client shows nothing of its own), comes as the pushed resource
# /chat/my/channel/messages/latest {type: "chat", coachid, coachname, message} (MessagingGroupProxy).
# The others in it are the online virtual managers: one or two may answer (the club chat's lines in
# the chat language, without the ones about a club) and now and then one talks by himself.
CHANNEL_MAX = 99
CHANNEL_REPLY = (0.7, 0.25)  # odds the first / second online manager answers
CHANNEL_IDLE = 0.1  # a minute


def channel_line(vm, trigger):
    lines = club_chat_file(os.path.getmtime(os.path.join(RESPONSES, "vm_club_chat.json")))[
        vm_chat_cfg()["lang"]][trigger][vm_personality(vm)]
    return random.choice([s for s in lines if not re.search(r"klub|club|clb", s, re.I)] or lines)


def channel_say(vm, trigger, delay):
    channel_say_text(vm, channel_line(vm, trigger), delay)


def channel_online():
    return [vm for vm in virtual_managers() if vm_status(vm) != "offline"]


def ep_channel_message(query, body):
    """POST /chat/my/channel/message {message, hint}: my line back to me, and maybe answers."""
    req = json.loads(body or b"{}")
    text = str(req.get("message") or "")[:200]
    if text:
        coach = my_coach(_state())
        chat_push("/chat/my/channel/messages/latest", coach, text, hint=req.get("hint"))
        low = text.lower()
        trigger = ("thanks" if any(w in low for w in CLUB_THANKS) else
                   "hello" if any(re.search(rf"\b{re.escape(w)}\b", low) for w in CLUB_HELLO) else "reply")
        online = channel_online()
        random.shuffle(online)
        for i, vm in enumerate(online[:2]):
            if random.random() < CHANNEL_REPLY[i]:
                channel_say(vm, trigger, 2 + 3 * i + random.uniform(0, 3))
    return _json({"result": True})


def ep_channel_join(cid, query, body):
    """POST /chat/channel {channelid} | PUT /chat/channel/<id>: any channel 1..CHANNEL_MAX."""
    cid = int(cid or json.loads(body or b"{}").get("channelid") or 1)
    if not 1 <= cid <= CHANNEL_MAX:
        return _json({"result": False, "reason": "channel does not exist", "channelid": cid})
    return _json({"result": True, "channelid": cid, "created": False, "reason": ""})


# The day's football in the channel (user: "virtual manager diskusi tentang trending sepakbola hari
# itu dari medsos untuk umum atau klub atau pemain atau negara yang mereka suka, ... negara regional
# bisa di setting di admin panel"; "Indonesia, Malaysia, Vietnam, Thailand, Korea pilih manual, global
# selalu aktif"). Topics: the global ones always, plus each country ticked on the admin page's Chat AI
# tab: Google Trends' searches rising there today whose news are about football, then Google News'
# football headlines in its language (X, Instagram and TikTok need a login; Reddit refuses a server),
# fetched once a day ("update sehari sekali tidak usah manual"); the channel talks always, not tied
# to the Rank chat's switch. A manager posts one: his club, his best card's player or his country (one
# of the ticked, his for good) when the news has them (vm_news_chat.json's lines for each), else a
# topic of the day; one or two others answer.
NEWS_GLOBAL = ("Global", "en-GB", "GB", "GB:en", "football", ())
NEWS_REGIONS = {  # region: (name, Google News hl, gl, ceid, query, words for its national team)
    "ID": ("Indonesia", "id", "ID", "ID:id", "sepak bola", ("timnas", "indonesia")),
    "MY": ("Malaysia", "ms", "MY", "MY:ms", "bola sepak", ("harimau malaya", "malaysia")),
    "VN": ("Việt Nam", "vi", "VN", "VN:vi", "bóng đá", ("tuyển việt nam", "việt nam")),
    "TH": ("ประเทศไทย", "th", "TH", "TH:th", "ฟุตบอล", ("ช้างศึก", "ทีมชาติไทย")),
    "KR": ("대한민국", "ko", "KR", "KR:ko", "축구", ("대표팀", "한국")),
}
NEWS_COUNTRY = {"ID": "Timnas Indonesia", "MY": "Harimau Malaya", "VN": "ĐT Việt Nam", "TH": "ช้างศึก", "KR": "대표팀"}  # as a line names it
FOOTBALL_WORDS = ("sepak bola", "bola", "liga", "timnas", "gol ", "pelatih", "striker", "football", "soccer", "premier league",
                  "laliga", "la liga", "serie a", "bundesliga", "ligue 1", "champions league", "fifa", "uefa", "bóng đá",
                  "ฟุตบอล", "축구", "transfer", "manchester", "madrid", "barcelona", "arsenal", "liverpool", "chelsea",
                  "juventus", "bayern", "psg", "inter ", "milan")
# never small talk: crime, deaths, violence, abuse, racism, drugs, gambling, disasters (a cheerful
# "Udah baca belum?" under an assault headline came up in the first try)
NEWS_SKIP = ("assault", "sexual", "rape", "murder", "kill", "dies", "died", "death", "dead", "abuse", "racis", "arrest",
             "court", "jail", "prison", "police", "crash", "tragedy", "funeral", "suicide", "cancer", "war ", "bomb",
             "shooting", "stabb", "drug", "gambl", "betting", "violence", "victim", "meninggal", "tewas", "wafat",
             "pelecehan", "perkosa", "pembunuhan", "kekerasan", "ditangkap", "polisi", "penjara", "narkoba", "rasis",
             "kecelakaan", "berduka", "judi", "korban", "qua doi", "tu vong", "cuong hiep", "bat giu", "ma tuy",
             "tai nan", "เสียชีวิต", "ข่มขืน", "ยาเสพติด", "사망", "성폭", "마약", "폭행")
NEWS = {"key": None, "at": 0, "items": []}  # the topics of the day: [{title, source, trend, region}]
CHANNEL_NEWS = 0.7  # share of the channel's idle lines that are about the news


def _plain(s):
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(ch)).lower()


def is_football(text):
    t = f" {_plain(text)} "
    return any(_plain(w) in t for w in FOOTBALL_WORDS)


def news_fetch(region, get=None):
    """The football topics now of a region ("" = the global ones), trends first: [{title, source,
    trend}] (at most 40)."""
    import urllib.parse, urllib.request, xml.etree.ElementTree as ET
    get = get or (lambda u: urllib.request.urlopen(urllib.request.Request(u, headers=FUTGG_UA), timeout=20).read())
    _, hl, gl, ceid, q, _ = NEWS_REGIONS[region] if region else NEWS_GLOBAL
    ns = {"ht": "https://trends.google.com/trending/rss"}
    items = []
    try:  # optional: a region without trends still has the news
        for it in ET.fromstring(get(f"https://trends.google.com/trending/rss?geo={gl}")).iter("item"):
            news = [(n.findtext("ht:news_item_title", "", ns), n.findtext("ht:news_item_source", "", ns))
                    for n in it.findall("ht:news_item", ns)]
            hit = next(((t, s) for t, s in news if is_football(t)), None)
            if hit or is_football(it.findtext("title", "")):
                t, s = hit or (news[0] if news else (it.findtext("title", ""), ""))
                items.append({"title": html.unescape(t).strip(), "source": s, "trend": it.findtext("title", "")})
    except Exception as e:
        print(f"news trends {region}: {e!r}", flush=True)
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": hl, "gl": gl, "ceid": ceid})
    for it in ET.fromstring(get(url)).iter("item"):
        t, s = html.unescape(it.findtext("title", "")), it.findtext("source", "")
        items.append({"title": (t[:-len(s) - 3] if s and t.endswith(" - " + s) else t).strip(), "source": s, "trend": ""})
    seen, out = set(), []
    for i in items:
        if i["title"] and i["title"].lower() not in seen and not any(w in f" {_plain(i['title'])} " for w in NEWS_SKIP):
            seen.add(i["title"].lower())
            out.append(i)
    return out[:40]


def news_refresh(now=None):
    """The global topics and the ticked countries', once a WIB day (again when the ticks change, or
    10 minutes after a fetch that got none); NEWS as it is now."""
    now = time.time() if now is None else now
    key = (tuple(vm_chat_cfg()["newsregions"]), today_wib(now))
    if NEWS["key"] != key or not NEWS["items"] and now - NEWS["at"] > 600:
        NEWS.update(key=key, at=now, items=[])
        items = []
        for region in ("", *key[0]):
            try:
                items += [{**i, "region": region} for i in news_fetch(region)]
            except Exception as e:  # one source down: the others still talk
                print(f"news {region or 'global'}: {e!r}", flush=True)
        NEWS["items"] = items
    return NEWS


def news_loop():
    """The server's thread: the channel's topics of the day (offline: none, the old idle lines)."""
    while True:
        try:
            news_refresh()
        except Exception as e:
            print(f"news: {e!r}", flush=True)
        time.sleep(600)


@functools.lru_cache(maxsize=1)
def team_names():
    """{teamid: name} from the client's eng.db (data_0.big), {} without it."""
    try:
        from extract_fifadb import decrypt
        raw = client_big(os.path.join(GAME, "data", "data_0.big"), "data/db2/eng.db.db2")
        db = decrypt(bytes(raw))
        db[:16] = b"SQLite format 3\x00"
        with closing(sqlite3.connect(":memory:")) as c:
            c.deserialize(bytes(db))
            return dict(c.execute("SELECT teamid, teamname FROM team"))
    except Exception:
        return {}


NAME_SKIP = {"real", "club", "city", "united", "sporting", "athletic", "atletico", "calcio", "olympique", "borussia"}


def vm_likes(vm, regions):
    """[(kind, name, words)]: his club, his best card's player, his country (his for good of the
    ticked ones; none ticked, none)."""
    out = []
    club = team_names().get(vm["teamid"])
    if club:
        words = {_plain(club)} | {w for w in _plain(club).split() if len(w) >= 5 and w not in NAME_SKIP}
        out.append(("club", club, words))
    try:
        best = max(vm_team(vm["_id"])["players"], key=lambda p: card_ovr(p["spid"]))
        star = card_name(best["spid"])
        if not star.isdigit() and len(star.split()[-1]) >= 4:
            out.append(("player", star, {_plain(star.split()[-1])}))
    except (ValueError, KeyError):
        pass
    if regions:
        region = random.Random(vmseed("newsregion" + vm["_id"])).choice(sorted(regions))
        out.append(("country", NEWS_COUNTRY[region], set(NEWS_REGIONS[region][5])))
    return out


@functools.lru_cache(maxsize=1)
def news_chat_lines(mtime):
    with open(os.path.join(RESPONSES, "vm_news_chat.json"), encoding="utf-8") as f:
        return json.load(f)


def news_line(vm, kind, **words):
    """His line of kind (general, club, player, country, react) in the chat language, his mood."""
    lines = news_chat_lines(os.path.getmtime(os.path.join(RESPONSES, "vm_news_chat.json")))[vm_chat_cfg()["lang"]]
    return random.choice(lines[kind]).format(**words) + random.choice(lines["mood"][vm_personality(vm)])


def channel_news(online, rng=random):
    """A topic of the day by one online manager and answers from one or two others: [(vm, line, delay)]."""
    items = NEWS["items"]
    if not items or not online:
        return []
    who = rng.sample(online, min(3, len(online)))
    likes = vm_likes(who[0], sorted({i["region"] for i in items} - {""}))
    mine = {}  # kind: [(name, item)] of the news about what he likes
    for k, name, words in likes:
        mine[k] = [(name, i) for i in items if any(w in f" {_plain(i['title'])} " for w in words)]
    kinds = [k for k in mine if mine[k]] + ["general"]  # one kind at a time: a country's own team is in most of its news
    kind = rng.choice(kinds)
    if kind == "general":  # the day's top ones of one source (the global, or a ticked country)
        src = rng.choice(sorted({i["region"] for i in items}))
        name, item = "", rng.choice([i for i in items if i["region"] == src][:15])
    else:
        name, item = rng.choice(mine[kind])
    title = item["title"] if len(item["title"]) <= 110 else item["title"][:107].rstrip() + "..."
    out = [(who[0], news_line(who[0], kind, title=title, name=name), 0)]
    for i, vm in enumerate(who[1:]):
        if rng.random() < (0.85, 0.45)[i]:
            club = team_names().get(vm["teamid"]) or "timku"
            for _ in range(5):  # not the answer before his
                line = news_line(vm, "react", title=title, name=name, club=club)
                if not any(line.rstrip(" .:)^PDBx") [:20] == o[1][:20] for o in out[1:]):
                    break
            out.append((vm, line, 4 + 5 * i + rng.uniform(0, 4)))
    return out


def channel_say_text(vm, text, delay):
    """Into the Channel tab after delay s: always (the AI chat switch is the Rank opponents')."""
    chat_push("/chat/my/channel/messages/latest", vm, text, delay or 0.01)


def channel_idle():
    """Once a minute (presence_loop): now and then an online manager talks in the channel, mostly
    about the day's football when there are topics."""
    online = channel_online()
    if not online or random.random() >= CHANNEL_IDLE:
        return
    talk = channel_news(online) if random.random() < CHANNEL_NEWS else []
    for vm, text, delay in talk:
        channel_say_text(vm, text, delay)
    if not talk:
        channel_say(random.choice(online), "idle", 0)


def club_idle(st):
    """Once a minute (presence_loop): now and then an online member chats by himself."""
    if st.get("club") and random.random() < 0.15:
        club_chat_reply(st, "idle", 0)


def club_resources(st):
    """/club/me and /coach/me for a write reply (fires ON_CLUB_INFO_CHANGED)."""
    v = club_view(st)
    return [{"uri": "/club/me", "body": club_body(st, v, True) if v else {}}] + coach_me_resources(st, _default_slot(st))


def club_reply(st, ok=True, reason=None, **extra):
    _save(st)
    return _json({"result": ok, **({"reason": reason} if reason else {}), **extra,
                  **({"resources": club_resources(st)} if ok else {})})


def club_name_ok(name):
    n = len(name.encode("utf-8"))
    return 4 <= n <= 20 and name.strip() == name and not set(name) & set("<>&\"'\\/")


def ep_club_create(query, body):
    """POST /club/create {name, description}."""
    req = json.loads(body or b"{}")
    name, desc = str(req.get("name") or ""), str(req.get("description") or "")[:200]
    st = _state()
    coach = my_coach(st)
    if st.get("club"):
        return _json({"result": False, "reason": "already have a club"})
    if not club_name_ok(name):
        return _json({"result": False, "reason": "invalid name"})
    if name.lower() in {c["name"].lower() for c in ai_clubs_now().values()}:
        return _json({"result": False, "reason": "name is already used"})
    if coach.get("ep", START_EP) < CLUB_FEE:
        return _json({"result": False, "reason": "not enough money"})
    add_ep(st, -CLUB_FEE)
    now = time.time()
    st["club"] = {"_id": os.urandom(12).hex(), "name": name, "created": now, "description": desc, "descat": now,
                  "news": "", "newsby": coach["name"], "newsat": now, "allowjoin": True, "skipjudgement": False,
                  "color": CLUB_COLORS[0], "limit": CLUB_LIMIT, "members": [], "requests": [], "posts": [],
                  "next": now + CLUB_ASK / 2}
    club_earn(st, "CLUB_JOIN", "Club_Join")
    return club_reply(st, clubid=st["club"]["_id"], name=name)


def ep_club_me(query, body):
    """GET /club/me (or DELETE /club/me: close my club, after CLUB_STATIC's time)."""
    st = _state()
    if REQUEST["method"] == "DELETE":
        c = st.get("club")
        if not c or c.get("ai"):
            return _json({"result": False, "reason": "invalid request"})
        c["deleteat"] = time.time() + 60 * CLUB_STATIC["club_dissolution_time"]
        return club_reply(st)
    club_tick(st)
    _save(st)
    v = club_view(st)
    return _json(club_body(st, v, True) if v else {})


def ep_club_find(query, body):
    """GET /club/find?keyword&sort&order&allowjoin&skipjudgement&membernumfrom&membernumto:
    {clubs, count}; member.grade = me there (0 asked, -1 may ask, else my grade)."""
    q = urllib.parse.parse_qs(query)
    arg = lambda k, d="": (q.get(k) or [d])[0]
    st = _state()
    mine = club_view(st)
    views = list(ai_clubs_now().values())
    if mine:
        views = [mine if v["_id"] == mine["_id"] else v for v in views] + ([] if st["club"].get("ai") else [mine])
    rows = []
    for v in views:
        rows.append(club_body(st, v))
        if mine and v["_id"] == mine["_id"]:
            rows[-1]["member"] = {"grade": club_my_grade(st)}
    kw = arg("keyword").lower()
    lo, hi = int(arg("membernumfrom", "0") or 0), int(arg("membernumto", "0") or 0)
    rows = [r for r in rows if kw in r["name"].lower() and (arg("allowjoin") != "true" or r["allowjoin"])
            and (arg("skipjudgement") != "true" or r["skipjudgement"]) and r["membercount"] >= lo and (not hi or r["membercount"] <= hi)]
    key = {"name": lambda r: r["name"].lower(), "level": lambda r: r["levelinfo"]["level"],
           "weeklypoints": lambda r: r["levelinfo"]["actpoints"]["weeklycached"], "member": lambda r: r["membercount"]}
    rows.sort(key=key.get(arg("sort"), key["level"]), reverse=arg("order", "-1") == "-1")
    return _json({"clubs": rows, "count": len(rows)})


def ep_club_get(cid, query, body):
    """GET /club/<id>: a club's page (the list's detail popup)."""
    st = _state()
    v = ai_clubs_now().get(cid)
    mine = club_view(st)
    if mine and mine["_id"] == cid:
        return _json(club_body(st, mine, True))
    return _json(club_body(st, v) if v else {"result": False, "reason": "deleted club"})


def ep_club_join(cid, query, body):
    """POST /club/<id>/join: an AI club takes me at once (DELETE: an ask taken back)."""
    st = _state()
    if REQUEST["method"] == "DELETE":
        return _json({"result": True})
    v = ai_clubs_now().get(cid)
    if st.get("club"):
        return _json({"result": False, "reason": "already have a club"})
    if not v or not v["allowjoin"] or len(v["members"]) >= v["limit"]:
        return _json({"result": False, "reason": "invalid request"})
    st["club"] = {"ai": cid, "joined": time.time(), "posts": []}
    club_earn(st, "CLUB_JOIN", "Club_Join")
    club_chat_reply(st, "welcome", 4, odds=(1, 0.5))
    return club_reply(st)


def ep_club_leave(query, body):
    """PUT /club/me/member/leave: out of the AI club I joined."""
    st = _state()
    if not (st.get("club") or {}).get("ai"):
        return _json({"result": False, "reason": "invalid request"})
    st["club"] = None
    return club_reply(st)


def ep_club_members(query, body):
    st = _state()
    club_tick(st)
    _save(st)
    v = club_view(st)
    return _json([club_member_row(st, m) for m in v["members"]] if v else [])


def club_my_grade(st):
    v = club_view(st)
    return next((m["grade"] for m in v["members"] if m["vm"] is None), 0) if v else 0


def ep_club_member(mid, query, body):
    """POST /club/me/member/<memberid> {grade}: a member's grade in my club."""
    req = json.loads(body or b"{}")
    st = _state()
    c = st.get("club") or {}
    m = next((m for m in c.get("members", []) if m["id"] == mid), None)
    if c.get("ai") or not m or req.get("grade") not in (8, 6, 1):
        return _json({"result": False, "reason": "invalid request"})
    m["grade"] = req["grade"]
    return club_reply(st)


def ep_club_exclude(mid, query, body):
    """PUT /club/me/member/exclude/<memberid>: out of my club."""
    st = _state()
    c = st.get("club") or {}
    if c.get("ai") or not any(m["id"] == mid for m in c.get("members", [])):
        return _json({"result": False, "reason": "invalid request"})
    c["members"] = [m for m in c["members"] if m["id"] != mid]
    return club_reply(st)


def ep_club_requests(query, body):
    """GET /club/me/join/requests: managers asking to join my club."""
    st = _state()
    club_tick(st)
    _save(st)
    vms = {m["_id"]: m for m in virtual_managers()}
    return _json([{"id": r["id"], "coachid": r["id"], "name": vms[r["id"]]["name"], "level": vms[r["id"]]["level"],
                   "division": f"DIVISION_{ladder_division(vms[r['id']]['elo'][0])}", "ovr": vm_ovr(r["id"]),
                   "requestedat": w3c(r["at"]), "teamid": vms[r["id"]]["teamid"]}
                  for r in (st.get("club") or {}).get("requests", []) if r["id"] in vms])


def ep_club_request(rid, query, body):
    """PUT /club/me/join/<id> takes the manager in, DELETE turns him down."""
    st = _state()
    c = st.get("club") or {}
    if c.get("ai") or not any(r["id"] == rid for r in c.get("requests", [])):
        return _json({"result": False, "reason": "invalid request"})
    vm = vm_by_id(rid)
    if REQUEST["method"] == "DELETE" or not vm:
        c["requests"] = [r for r in c["requests"] if r["id"] != rid]
    elif len(c["members"]) + 1 >= c["limit"]:
        return _json({"result": False, "reason": "club is full"})
    else:
        club_add_member(st, vm)
    return club_reply(st)


def ep_club_settings(what, value, query, body):
    """POST /club/me/settings/description {description} | news {message} | allowjoin/<bool>,
    PUT .../color {color}."""
    req = json.loads(body or b"{}")
    st = _state()
    c = st.get("club") or {}
    if not c or c.get("ai"):
        return _json({"result": False, "reason": "invalid request"})
    now = time.time()
    if what == "description":
        c.update(description=str(req.get("description") or "")[:200], descat=now)
    elif what == "news":
        c.update(news=str(req.get("message") or "")[:200], newsby=my_coach(st)["name"], newsat=now)
    elif what == "allowjoin":
        c["allowjoin"] = value == "true"
    elif what == "skipjudgement":
        c["skipjudgement"] = value == "true"
    elif what == "color" and isinstance(req.get("color"), int):
        c["color"] = req["color"]
    return club_reply(st)


def ep_club_expand(n, query, body):
    """POST /club/me/expand/<n>: CLUB_EXPAND more members for CLUB_EXPAND_FEE EP."""
    st = _state()
    c = st.get("club") or {}
    if c.get("ai") or not c or c["limit"] >= CLUB_LIMIT_MAX:
        return _json({"result": False, "reason": "invalid request"})
    if my_coach(st).get("ep", START_EP) < CLUB_EXPAND_FEE:
        return _json({"result": False, "reason": "not enough money"})
    add_ep(st, -CLUB_EXPAND_FEE)
    c["limit"] += CLUB_EXPAND
    return club_reply(st)


def ep_club_undelete(query, body):
    st = _state()
    (st.get("club") or {}).pop("deleteat", None)
    return club_reply(st)


def club_board(st):
    """The board: my posts and the members' (seeded, a few a day), newest first."""
    v = club_view(st)
    if not v:
        return []
    posts = [{**p} for p in st["club"].get("posts", [])]
    today = club_day()
    for m in v["members"]:
        if m["vm"] is None:
            continue
        for d in range(max(club_day(m["joined"]), today - 6), today + 1):
            r = random.Random(vmseed(f"board:{m['id']}:{d}"))
            if r.random() < 0.12:
                at = d * 86400 - WIB + r.randint(9, 23) * 3600
                if at < time.time():
                    posts.append({"_id": f"{m['id']}x{d}", "coachid": m["id"], "name": m["name"],
                                  "message": club_line(m["vm"], "board", f"board:{m['id']}:{d}"), "at": at})
    posts.sort(key=lambda p: -p["at"])
    return [{"_id": p["_id"], "name": p["name"], "message": p["message"], "createdat": w3c(p["at"])} for p in posts[:50]]


def ep_club_posts(query, body):
    return _json(club_board(_state()))


def ep_club_post(pid, query, body):
    """POST /club/me/post/ {msg}: my post on the board; DELETE /club/me/post/<id>: one of mine gone."""
    req = json.loads(body or b"{}")
    st = _state()
    c = st.get("club")
    if not c:
        return _json({"result": False, "reason": "invalid request"})
    if REQUEST["method"] == "DELETE":
        c["posts"] = [p for p in c.get("posts", []) if p["_id"] != pid]
    else:
        text = str(req.get("msg") or req.get("message") or "")[:200]
        if not text:
            return _json({"result": False, "reason": "invalid request"})
        coach = my_coach(st)
        c.setdefault("posts", []).append({"_id": os.urandom(12).hex(), "coachid": coach["_id"], "name": coach["name"],
                                          "message": text, "at": time.time()})
        c["posts"] = c["posts"][-30:]
        club_earn(st, "CLUB_BOARD", "Club_Board")
    return club_reply(st)


def ep_contact_club(query, body):
    """GET /contact/club {members}: the chat window's Klub member list; their presence follows as
    /presence/<id> pushes (now, and from presence_loop on change)."""
    st = _state()
    v = club_view(st)
    members = [m for m in (v["members"] if v else []) if m["vm"]]
    threading.Timer(1, lambda: [messenger_push(f"/presence/{m['id']}", {"status": vm_status(m["vm"])}) for m in members]).start()
    return _json({"members": [{"_id": m["id"], "coachname": m["name"]} for m in members]})


def ep_club_record(what, query, body):
    """GET /club/record/members | opponent | recent | leaderboard (no club matches here)."""
    st = _state()
    v = club_view(st)
    month = int(time.strftime("%Y%m", time.gmtime(time.time() + WIB)))
    if what == "members":
        mine = coach_me(st, _default_slot(st))["summary"].get("ovr") or {}
        rows = [{"coachid": m["id"], "name": m["name"], "level": m["level"], "grade": m["grade"], "ranking": i + 1,
                 "ovr": vm_ovr(m["id"]) if m["vm"] else {k: mine.get(k, 0) for k in ("FW", "MF", "DF")},
                 "statistics": {"friendly": {"recent": {"wins": 0, "forfeitwins": 0}}}} for i, m in enumerate(v["members"] if v else [])]
        return _json({"month": month, "members": rows})
    if what == "leaderboard":
        clubs = sorted(ai_clubs_now().values(), key=lambda c: -club_points(st, c, None)[1])
        return _json({"month": month, "leaderboardlist": [
            {"clubid": c["_id"], "clubname": c["name"], "level": club_level(club_points(st, c, None)[0])[0], "rank": i + 1,
             "wins": 0, "forfeitwins": 0, "totalwins": 0, "totalforfeitwins": 0, "total": 0, "wp": 0} for i, c in enumerate(clubs)]})
    return _json({"month": month, "opponentrecords": [], "recentmatches": []})


def club_id(st):
    """My club's id for /coach/me club.clubid (None: in no club)."""
    c = st.get("club") or {}
    return c.get("ai") or c.get("_id")


def ep_rank_club(query, body):
    """GET /rank/teamvalue/club: Peringkat manajer's club tab, my club's members and me."""
    v = club_view(_state())
    return ep_rank_friends(query, body, [m["vm"] for m in v["members"] if m["vm"]]) if v else _json([])


# --------------------------------------------------------------------------- #
# King of the Hill (issue #36: "King of the Hill Mode Cannot Be Started"): the Esports
# "Grassroot" Regional League (RegionalLeagueController / Proxy; room type esports2, match type
# Regional_League). RegionalLeagueProxy starts only with features.esports.regionalleague (without
# it the main screen said "Musim sedang tidak aktif"); then GET /esports2/region/seasons/presently
# {seasons, regiongrouptrees, prev}: the season with startdt <= now < enddt (local dates
# "YYYYMMDD") is current, one a month here, open all day ("0000".."2400"). Its regiongroup.id
# names the region tree {major: {id, name, rewardgroupid, children {id: {id, name, parent}}}}.
# I pick a region (POST /esports2/region/register {regiongroupid = the region}), its board is GET
# /esports2/region/leaderboard/<region> (an array of {cid, name, elo, wins, losses}: the virtual
# managers there on their KOTH rating, ladder index KOTH_LADDER, which the Rank screens never
# show). /coach/me statistics.esports2 {seasonid, regionid (only once registered: isInSeason
# reads hasOwnProperty), introid (= the season once its intro was left: PUT
# /esports2/region/intro), elo, wins, losses, ...; rewarded true and midrewarded [] keep the
# reward screen away}. CoachProxy grades that elo with /static/db/ESPORTS2_BALANCING_DEFAULT
# synchronously, so GET /static/initialize preloads it (an async null was a TypeError inside
# onChangeCoachMeResource: the black FREEROAM). Mulai: POST /room {type esports2}, matchmaking,
# then the Rank path (ladder_match, the one-user session) against a manager of my region near my
# rating; the result moves my KOTH rating (Elo) and fills the reward screen's "Poin Divisi" box
# (match.reward preveloinfo / eloinfo). Weekly and season rewards: see koth_rewards.
# --------------------------------------------------------------------------- #
KOTH_GROUP, KOTH_LADDER = "1", 5
KOTH_REGIONS = (("1", "Sumatera", (("101", "Medan"), ("102", "Padang"), ("103", "Palembang"), ("104", "Pekanbaru"))),
                ("2", "Jawa", (("201", "Jakarta"), ("202", "Bandung"), ("203", "Semarang"), ("204", "Yogyakarta"),
                               ("205", "Surabaya"), ("206", "Malang"))),
                ("3", "Kalimantan", (("301", "Pontianak"), ("302", "Balikpapan"), ("303", "Banjarmasin"))),
                ("4", "Sulawesi", (("401", "Makassar"), ("402", "Manado"))),
                ("5", "Bali Nusra", (("501", "Denpasar"), ("502", "Mataram"))),
                ("6", "Maluku Papua", (("601", "Ambon"), ("602", "Jayapura"))))
# Region sets (user: "admin panel bisa ubah nama region ke wilayah indo, SG/Malay, Thai dan vietnam";
# issue #36 asked for Vietnamese names): the admin page picks one (KOTH_FILE {"regions": key}). Each
# set has its own ids, so after a switch my region is none (the main screen asks for one again) and
# the virtual managers spread over the new set's cities. Names are keys of the region pickers: unique.
KOTH_SETS = {
    "id": KOTH_REGIONS,
    "sgmy": (("11", "Singapore", (("1101", "Jurong"), ("1102", "Woodlands"), ("1103", "Tampines"), ("1104", "Ang Mo Kio"))),
             ("12", "Utara", (("1201", "George Town"), ("1202", "Alor Setar"), ("1203", "Ipoh"))),
             ("13", "Lembah Klang", (("1301", "Kuala Lumpur"), ("1302", "Shah Alam"), ("1303", "Petaling Jaya"))),
             ("14", "Selatan", (("1401", "Seremban"), ("1402", "Melaka"), ("1403", "Johor Bahru"))),
             ("15", "Pantai Timur", (("1501", "Kota Bharu"), ("1502", "Kuala Terengganu"), ("1503", "Kuantan"))),
             ("16", "Sabah Sarawak", (("1601", "Kota Kinabalu"), ("1602", "Kuching"), ("1603", "Miri")))),
    "th": (("21", "Greater Bangkok", (("2101", "Bangkok"), ("2102", "Nonthaburi"), ("2103", "Samut Prakan"))),
           ("22", "Central", (("2201", "Ayutthaya"), ("2202", "Nakhon Pathom"))),
           ("23", "North", (("2301", "Chiang Mai"), ("2302", "Chiang Rai"), ("2303", "Phitsanulok"))),
           ("24", "Northeast", (("2401", "Khon Kaen"), ("2402", "Nakhon Ratchasima"), ("2403", "Udon Thani"),
                                ("2404", "Ubon Ratchathani"))),
           ("25", "East", (("2501", "Chonburi"), ("2502", "Pattaya"), ("2503", "Rayong"))),
           ("26", "South", (("2601", "Phuket"), ("2602", "Hat Yai"), ("2603", "Surat Thani")))),
    "vn": (("31", "Miền Bắc", (("3101", "Hà Nội"), ("3102", "Hải Phòng"), ("3103", "Quảng Ninh"), ("3104", "Bắc Ninh"))),
           ("32", "Miền Trung", (("3201", "Đà Nẵng"), ("3202", "Huế"), ("3203", "Vinh"), ("3204", "Nha Trang"))),
           ("33", "Tây Nguyên", (("3301", "Buôn Ma Thuột"), ("3302", "Pleiku"), ("3303", "Đà Lạt"))),
           ("34", "Đông Nam Bộ", (("3401", "TP. Hồ Chí Minh"), ("3402", "Biên Hòa"), ("3403", "Thủ Dầu Một"),
                                  ("3404", "Vũng Tàu"))),
           ("35", "Tây Nam Bộ", (("3501", "Cần Thơ"), ("3502", "Long Xuyên"), ("3503", "Rạch Giá")))),
}
KOTH_FILE = os.path.join(HERE, "gamedata", "koth.json")
# King of the Hill rewards (user: "buat editor hadiah king off hill"): the admin page writes
# KOTH_REWARDS_FILE over responses/koth_rewards.json {week {type winning|match, win, match, items,
# grade, divs [{name, items}]}, season {grade, divs [{name, items}], ranking [{from, to, items}]}}
# (divs worst first, as the client indexes them: divisions - 1 - the elo's division). The season
# shows them (reward.week win / match / grade, reward.season grade / ranking; reward.config = the
# head tab's gauges: this week's wins or matches, this week's wins, the season's wins). A season
# has 4 weeks (schedule.midreward = the days after its start a week ends: days 1-7 .. 22-28):
# once one ended, statistics.midrewarded[that week] false sends the client to the reward screen
# (RegionalLeagueController.routing "reward"), which POSTs /esports2/region/reward. The previous
# season does it with statistics.seasonid = it and rewarded false. The reply {reward {config,
# week {type, target, grade, win, match}, season {target, grade, ranking}}} lists what was earned
# ({rewarded, items}; not earned = shown greyed with its goal); earned items go to the Bag.
KOTH_REWARDS_FILE = os.path.join(HERE, "gamedata", "koth_rewards.json")
KOTH_MIDREWARD = [7, 14, 21, 28]
KOTH_SCALES = ("normal", "medium", "large", "exlarge")


def koth_rewards():
    for f in (KOTH_REWARDS_FILE, os.path.join(RESPONSES, "koth_rewards.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            continue
    return {}


def koth_goals(cfg):
    return {"weekly_winning_reward_condition": cfg["week"]["win"], "weekly_match_reward_condition": cfg["week"]["match"],
            "weekly_grade_reward_condition": cfg["week"]["grade"], "season_grade_reward_condition": cfg["season"]["grade"]}


def koth_week(t=None):
    """This King of the Hill week: 0 for days 1-7 of the WIB month .. 4 for days 29-31 (no reward)."""
    return (time.gmtime((time.time() if t is None else t) + WIB).tm_mday - 1) // 7


def koth_div_items(divs, elo):
    name = "DIVISION_" + ladder_division(elo)
    return next((d["items"] for d in divs if d["name"] == name), [])


# The admin page's own set (user: "buatkan untuk custom area king of hill di admin page"; issue
# #36 asked for Sa Pa in Miền Bắc): KOTH_CUSTOM_FILE [{id, name, cities [{id, name}]}], region ids
# from 41, a city's id its region's + 01..99 (the admin page keeps the ids of names it already had).
KOTH_CUSTOM_FILE = os.path.join(HERE, "gamedata", "koth_custom.json")
_KOTH_CUSTOM = {}  # (file, mtime, size) -> its tree: an edit counts at once


def koth_custom():
    """The custom set as a KOTH_SETS tree, () while there is none."""
    try:
        st = os.stat(KOTH_CUSTOM_FILE)
    except OSError:
        return ()
    key = (KOTH_CUSTOM_FILE, st.st_mtime_ns, st.st_size)
    if key not in _KOTH_CUSTOM:
        try:
            with open(KOTH_CUSTOM_FILE, encoding="utf-8") as f:
                tree = tuple((str(r["id"]), str(r["name"]), tuple((str(c["id"]), str(c["name"])) for c in r["cities"]))
                             for r in json.load(f))
        except (OSError, ValueError, KeyError, TypeError):
            tree = ()
        _KOTH_CUSTOM.clear()
        _KOTH_CUSTOM[key] = tree
    return _KOTH_CUSTOM[key]


def koth_set():
    """The region set in use (a KOTH_SETS key, or "custom" while it has a city)."""
    try:
        with open(KOTH_FILE, encoding="utf-8") as f:
            key = json.load(f).get("regions")
    except (OSError, ValueError, AttributeError):
        key = None
    return key if key in KOTH_SETS or key == "custom" and any(subs for _, _, subs in koth_custom()) else "id"


def koth_tree(key=None):
    """((major id, name, ((city id, name), ...)), ...) of a region set (the one in use)."""
    key = key or koth_set()
    return koth_custom() if key == "custom" else KOTH_SETS[key]


@functools.lru_cache(maxsize=16)
def _koth_leaves(tree):
    leaves = {sid: (name, mid, mname) for mid, mname, subs in tree for sid, name in subs}
    return leaves, tuple(sorted(leaves))


def koth_leaves(key):
    """{city id: (name, major id, major name)} of a region set."""
    return _koth_leaves(koth_tree(key))[0]


def vm_region(vid, key=None):
    return random.Random(vmseed("region" + vid)).choice(_koth_leaves(koth_tree(key))[1])


def koth_season(sid):
    y, m = divmod(sid, 100)
    ny, nm = (y + m // 12, m % 12 + 1)
    divs, cfg = len(ladder_divisions()), koth_rewards()
    ranks = [{"rank_from": r["from"], "rank_to": r["to"], "items": r["items"]} for r in cfg["season"]["ranking"]]
    return {"_id": str(sid), "title": f"King of the Hill {m:02d}/{y}",
            "schedule": {"startdt": f"{y}{m:02d}01", "enddt": f"{ny}{nm:02d}01", "week": [True] * 7, "midreward": KOTH_MIDREWARD,
                         "weekdays": {"starttime": "0000", "endtime": "2400"}, "weekend": {"starttime": "0000", "endtime": "2400"}},
            "regiongroup": {"id": KOTH_GROUP}, "scale": {"medium": 30, "large": 60, "exlarge": 100},
            "condition": {"autoreg": False, "regonlyigr": False, "playonlyigr": False},
            "reward": {"popup": False, "config": koth_goals(cfg),
                       "week": {"type": cfg["week"]["type"], "win": cfg["week"]["items"], "match": cfg["week"]["items"],
                                "grade": [d["items"] for d in cfg["week"]["divs"]][:divs]},
                       "season": {"grade": {"1": [d["items"] for d in cfg["season"]["divs"]][:divs]},
                                  "ranking": {"1": {k: ranks for k in KOTH_SCALES}}}}}


def koth_sid():
    """This King of the Hill season: the WIB month (the client picks the season by its own date)."""
    return ladder_season(time.time() + WIB)[0]


def ep_koth_presently(query, body):
    sid = koth_sid()
    nxt = sid + 1 if sid % 100 < 12 else sid + 89
    # keyed by name: the region pickers list the keys (RegionalLeagueProxy.mainRegionList / subRegionList)
    tree = {mname: {"id": mid, "name": mname, "rewardgroupid": 1,
                    "children": {n: {"id": s, "name": n, "parent": mname} for s, n in subs}} for mid, mname, subs in koth_tree()}
    return _json({"seasons": [koth_season(sid), koth_season(nxt)], "regiongrouptrees": {KOTH_GROUP: tree}, "prev": []})


def my_koth(st, coach=None):
    """My KOTH record of this season {sid, regionid, introid, elo, wins, draws, losses, streak,
    weeks [{wins, games, elo}] (koth_week), paid [week], prev}: a new month starts at the start
    rating in the same region; the one I played stays as prev {sid, regionid, elo, wins, rank,
    rewarded} until its rewards were shown. Before the first manager is made (a fresh install's
    boot asks /esports2/region/register/<season> first: issue #42, the client quit) a blank one
    that is not kept."""
    sid = koth_sid()
    if coach is None:
        coach = next((c for c in st["coaches"] if c["slot"] == _default_slot(st)), None)
    r = (coach if coach is not None else {}).setdefault("es2", {})
    if r.get("sid") != sid:
        if r.get("sid") and r.get("regionid") and r.get("wins", 0) + r.get("draws", 0) + r.get("losses", 0):
            key = koth_set()
            above = sum(m["elo"][KOTH_LADDER] > r["elo"] for m in virtual_managers() if vm_region(m["_id"], key) == r["regionid"])
            r["prev"] = {"sid": r["sid"], "regionid": r["regionid"], "elo": r["elo"], "wins": r["wins"],
                         "rank": above + 1, "rewarded": False}
        r.update(sid=sid, elo=LADDER_START, wins=0, draws=0, losses=0, streak=0, introid="",
                 weeks=[{"wins": 0, "games": 0, "elo": LADDER_START} for _ in range(5)], paid=[])
    for k in ("week", "weekwins", "weekgames"):  # the ISO-week counters of before
        r.pop(k, None)
    r.setdefault("weeks", [{"wins": 0, "games": 0, "elo": r["elo"]} for _ in range(5)])
    r.setdefault("paid", [])
    return r


def koth_week_due(r, t=None):
    """The ended week of this season whose rewards were not shown yet (it had matches), or None."""
    i = min(koth_week(t), len(KOTH_MIDREWARD)) - 1
    return i if i >= 0 and r["weeks"][i]["games"] and i not in r["paid"] else None


def ep_koth_reward(query, body):
    """POST /esports2/region/reward: the previous season's rewards, else the ended week's (see
    koth_rewards); earned items go to the Bag."""
    st = _state()
    r, cfg = my_koth(st), koth_rewards()
    none = {"rewarded": False, "items": []}
    out = {"config": koth_goals(cfg), "week": {"type": cfg["week"]["type"], "target": False, "grade": none, "win": none, "match": none},
           "season": {"target": False, "grade": none, "ranking": none}}
    got = []
    prev, week = r.get("prev") or {}, koth_week_due(r)
    if prev and not prev["rewarded"]:
        grade = koth_div_items(cfg["season"]["divs"], prev["elo"])
        rank = next((x["items"] for x in cfg["season"]["ranking"] if x["from"] <= prev["rank"] <= x["to"]), [])
        g_ok, r_ok = bool(grade) and prev["wins"] >= cfg["season"]["grade"], bool(rank)
        out["season"] = {"target": True, "grade": {"rewarded": g_ok, "items": grade}, "ranking": {"rewarded": r_ok, "items": rank}}
        got = grade * g_ok + rank * r_ok
        prev["rewarded"] = True
        title, msg = f"Hadiah musim King of the Hill {prev['sid']}", f"Peringkat {prev['rank']} di region, divisi {ladder_division(prev['elo']).replace('_', ' ').title()}."
    elif week is not None:
        w = r["weeks"][week]
        grade = koth_div_items(cfg["week"]["divs"], w["elo"])
        goal = w["wins"] >= cfg["week"]["win"] if cfg["week"]["type"] == "winning" else w["games"] >= cfg["week"]["match"]
        g_ok = bool(grade) and w["wins"] >= cfg["week"]["grade"]
        won = {"rewarded": goal and bool(cfg["week"]["items"]), "items": cfg["week"]["items"]}
        out["week"] = {"type": cfg["week"]["type"], "target": True, "grade": {"rewarded": g_ok, "items": grade}, "win": won, "match": won}
        got = cfg["week"]["items"] * won["rewarded"] + grade * g_ok
        r["paid"] = sorted({*r["paid"], *range(week + 1)})
        title, msg = f"Hadiah mingguan King of the Hill (minggu {week + 1})", f"{w['wins']} menang dari {w['games']} laga."
    else:
        return _json({"result": False})
    if got:
        bag_add(st, title, got, msg=msg)
    _save(st)
    return _json({"result": True, "reward": out, "resources": coach_me_resources(st, _default_slot(st))})


def koth_board(st, region):
    """[(elo, wins, losses, id, name)] of a region (a major one: all of its own), best first: its
    virtual managers, and me."""
    key = koth_set()
    within = lambda leaf: leaf == region or (koth_leaves(key).get(leaf) or (0, 0))[1] == region
    rows = [(m["elo"][KOTH_LADDER], m["wins"][KOTH_LADDER], m["losses"][KOTH_LADDER], m["_id"], m["name"])
            for m in virtual_managers() if within(vm_region(m["_id"], key))]
    r, coach = my_koth(st), my_coach(st)
    if within(r.get("regionid")):
        rows.append((r["elo"], r["wins"], r["losses"], coach["_id"], coach["name"]))
    return sorted(rows, key=lambda x: (-x[0], -x[1], x[3]))


def koth_statistics(st, coach):
    r = my_koth(st, coach)
    # seasonid only once a region is picked: the main screen shows its "pick a region" panel while
    # the season is not mine (isCurrentSeasonRegistered), and Mulai Laga needs regionid too
    leaves = koth_leaves(koth_set())
    prev, week = r.get("prev") or {}, r["weeks"][koth_week()] if koth_week() < len(r["weeks"]) else {}
    if prev and not prev["rewarded"] and prev["regionid"] in leaves:  # the reward screen of that season first
        name, _, parent = leaves[prev["regionid"]]
        return {"seasonid": str(prev["sid"]), "introid": str(prev["sid"]), "elo": prev["elo"], "wins": prev["wins"],
                "losses": 0, "forfeits": 0, "midwins": 0, "midmatches": 0, "rank": prev["rank"], "rewarded": False,
                "midrewarded": [], "regionid": prev["regionid"], "regionname": {"name": name, "parent": parent}}
    out = {"seasonid": str(r["sid"]) if r.get("regionid") in leaves else "", "introid": r.get("introid", ""),
           "elo": r["elo"], "wins": r["wins"],
           "losses": r["losses"], "forfeits": 0, "midwins": week.get("wins", 0), "midmatches": week.get("games", 0),
           "rank": 0, "rewarded": True,
           "midrewarded": [not (w["games"] and i not in r["paid"]) for i, w in enumerate(r["weeks"][:len(KOTH_MIDREWARD)])]}
    if r.get("regionid") in leaves:
        name, _, parent = leaves[r["regionid"]]
        out.update(regionid=r["regionid"], regionname={"name": name, "parent": parent})
        if coach is my_coach(st):
            out["rank"] = next(k for k, x in enumerate(koth_board(st, r["regionid"]), 1) if x[3] == coach["_id"])
    return out


def ep_koth_register_counts(sid, query, body):
    """GET /esports2/region/register/<season>: players per region (the region picker shows them)."""
    key = koth_set()
    count = collections.Counter(vm_region(m["_id"], key) for m in virtual_managers())
    r = my_koth(_state())
    if r.get("regionid"):
        count[r["regionid"]] += 1
    return _json({"result": True, "registeredplayer": {k: count[k] for k in koth_leaves(key)}})


def ep_koth_register(query, body):
    """POST /esports2/region/register {regiongroupid}: my region for the season (and after)."""
    region = str(json.loads(body or b"{}").get("regiongroupid") or "")
    if region not in koth_leaves(koth_set()):
        return _json({"result": False, "reason": "Esports_Grassroot_RegionSelect_Fail"})
    st = _state()
    my_koth(st)["regionid"] = region
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, _default_slot(st))})


def ep_koth_intro(query, body):
    """PUT /esports2/region/intro {seasonid}: the intro was left; the menu opens the main screen."""
    st = _state()
    my_koth(st)["introid"] = str(json.loads(body or b"{}").get("seasonid") or koth_sid())
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, _default_slot(st))})


def ep_koth_leaderboard(region, query, body):
    return _json([{"cid": i, "name": n, "elo": e, "wins": w, "losses": l} for e, w, l, i, n in koth_board(_state(), region)[:100]])


def koth_opponent(st):
    """One of the five managers of my region nearest my KOTH rating (anyone if the region has none)."""
    r = my_koth(st)
    key = koth_set()
    pool = [m for m in virtual_managers() if vm_region(m["_id"], key) == r.get("regionid")] or virtual_managers()
    return random.choice(sorted(pool, key=lambda m: abs(m["elo"][KOTH_LADDER] - r["elo"]))[:5])


def koth_record(st, vm, mine, theirs, sh, sa):
    """A KOTH result: my rating moves (Elo, K 32), the record and the multiplay list count it;
    (rating before, rating after, streak) for the reward screen."""
    r = my_koth(st)
    score = 1 if (mine, sh) > (theirs, sa) else 0 if (mine, sh) < (theirs, sa) else 0.5
    old = r["elo"]
    expect = 1 / (1 + 10 ** ((vm["elo"][KOTH_LADDER] - old) / 400))
    r["elo"] = max(LADDER_START, round(old + LADDER_K * (score - expect)))
    r[{1: "wins", 0: "losses", 0.5: "draws"}[score]] += 1
    r["streak"] = (max(r["streak"], 0) + 1 if score == 1 else min(r["streak"], 0) - 1 if score == 0 else 0)
    w = r["weeks"][min(koth_week(), len(r["weeks"]) - 1)]
    w.update(games=w["games"] + 1, wins=w["wins"] + (score == 1), elo=r["elo"])
    multiplay_add(st, "Regional_League", vm, mine, theirs, sh, sa)
    return old, r["elo"], r["streak"]


# Multiple > Exhibition (see the Live Center note by its ENDPOINTS)
EXHIBITION_PLAYS = 5
EXHIBITION_TEAMS = tuple(VM_TOP_CLUBS[:8])


def exhibition_played(st, add=0):
    """Exhibition matches I played today (WIB)."""
    r = my_coach(st).setdefault("exhibition", {})
    if r.get("day") != today_wib():
        r.update(day=today_wib(), played=0)
    r["played"] += add
    return r["played"]


def ep_exhibition_room(query, body):
    """POST /exhibition/room: my Exhibition room, my side on the first recommended club."""
    st = _state()
    if exhibition_played(st) >= EXHIBITION_PLAYS:
        return _json({"result": False, "reason": "the point is not enough"})
    ep_room(query, json.dumps({**json.loads(body or b"{}"), "type": "pvp_exhibition"}).encode())
    st = _state(); room = st["room"]
    room["side"]["left"]["authTeamId"] = EXHIBITION_TEAMS[0]
    _save(st)
    return ladder_room_reply(room)


def _room_team(st, room, req):
    """POST /exhibition/room/<id>/team {teamid, side}: the club that side plays."""
    side, teamid = req.get("side"), int(req.get("teamid") or 0)
    if side not in ("left", "right") or not roster(teamid)["team"]["players"]:
        return _json({"result": False, "reason": "invalid team"})
    room["side"][side]["authTeamId"] = teamid
    room["side"]["left"]["ready"] = False


ROUTES = [
    (re.compile(r"/item/(\w+)/use"), ep_item_use),
    (re.compile(r"/item/draftpack/(\d+)/players/(\d+)"), ep_draftpack_players),
    (re.compile(r"/transfer/price/default/(\d+)"), ep_transfer_price_default),
    (re.compile(r"/item/depottoitem/(\w+)"), ep_depot_to_item),
    (re.compile(r"/bag/(\w+)/unpack"), ep_bag_unpack),
    (re.compile(r"/coach/(\w+)/postponed"), ep_coach_postponed),
    (re.compile(r"/stadium/(\d+)"), ep_stadium),
    (re.compile(r"/lockerroom/(\w+)/itemset"), ep_lockerroom_itemset),
    (re.compile(r"/lockerroom/(\w+)/jersey"), ep_lockerroom_jersey),
    (re.compile(r"/static/info/authentic/team/(licensed|all)/(\d+)"), ep_authentic_team),
    (re.compile(r"/static/info/team/summary/(\d+)"), ep_team_summary),
    (re.compile(r"/player/(\w+)/move/(firstteam|secondteam)"), ep_player_move),
    (re.compile(r"/player/(\w+)/extra/growth"), ep_player_growth),
    (re.compile(r"/player/(\w+)/enhance/(\w+)/preview"), ep_enhance_preview),
    (re.compile(r"/player/(\w+)/enhance2\.5/(\w+)/preview/(\d)/(-?\d+)/(-?\d+)"), ep_enhance25_preview),
    (re.compile(r"/player/(\w+)/enhance2\.5/(\w+)/(\d)/(-?\d+)/(-?\d+)"), ep_enhance25),
    (re.compile(r"/player/(\w+)/enhance/(\w+)(?:/(-?\w+)/(-?\w+))?"), ep_enhance),
    (re.compile(r"/transfer/top/50(?:/position/(\w+))?"), ep_transfer_top),
    (re.compile(r"/transfer/search/(\d+)"), ep_transfer_search),
    (re.compile(r"/transfer/purchase/(\d+)/(\d+)/(\d+)/purchase"), ep_transfer_purchase),
    (re.compile(r"/transfer/price/(\d+)/(\d+)"), ep_transfer_price),
    (re.compile(r"/transfer/my/offer/completed/(\w+)(?:/(\w+))?"), ep_offer_collect),
    (re.compile(r"/transfer/my/offer/(waiting|expired)/(\w+)"), ep_offer_cancel),
    (re.compile(r"/transfer/offer/from/(\w+)"), ep_offer_reoffer),
    (re.compile(r"/transfer/(\d+)/(\d+)/detail"), ep_transfer_detail),
    # a system notification clicked away (SystemMessageCommand.deleteNotification): not kept
    (re.compile(r"/inbox/my/notifications/(\w+)"), lambda nid, q, b: _json({"result": True})),
    (re.compile(r"/match/([0-9a-f]+)/player/ready"), ep_match_player_ready),
    (re.compile(r"/match/([0-9a-f]+)/side/(\w+)/ready"), ep_match_ready),
    (re.compile(r"/match/([0-9a-f]+)/side/(\w+)/squad"), ep_match_squad),
    (re.compile(r"/match/([0-9a-f]+)/end"), ep_match_end),
    (re.compile(r"/match/(\d+)/gameplay/event/(\d+)"), ep_gameplay_event),
    (re.compile(r"/match/([0-9a-f]+)/messages"), ep_match_messages),
    # a virtual manager's profile (CoachUserPopupScreen: summary, 1on1 rank, squad, achievements)
    (re.compile(r"/coach/(vm\d{4})/summary"), ep_vm_summary),
    (re.compile(r"/coach/([0-9a-f]{24})/summary"), ep_my_summary),
    (re.compile(r"/achievement/(\d+)/reward/(\d+)"), ep_achievement_reward),
    (re.compile(r"/achievement/(vm\d{4}|[0-9a-f]{24})"), ep_achievement_other),
    (re.compile(r"/badge/(\d+)/use"), ep_badge_use),
    (re.compile(r"/ladder/(vm\d{4})/info"), ep_vm_ladder_info),
    (re.compile(r"/coach/(vm\d{4})"), ep_vm_coach),
    (re.compile(r"/achievement/(vm\d{4})"), lambda vid, q, b: _json({"summary": {"done": 0}})),
    (re.compile(r"/match/([0-9a-f]+)/gamesettings"), ep_match_gamesettings),
    # StaticData bescripts go to native as-is (BeginnersGuideManager.startLeagueTutorial ->
    # ApplyCampaignScript); "{}" for an unknown GET crashed the script parser. Ones without a
    # responses/ file get camera_controller's do-nothing script (no tutorial prompts, no crash).
    (re.compile(r"/static/bescript/(\w+)"), lambda name, q, b: from_file("/static/bescript/camera_controller")),
    # GET /game/inweburl/<notice|webevent|cookie|vip_purchase|...> {url}: StartupNoticeScreen
    # .onReceivedNoticeUrl hands url straight to native LoadWebAddress, and a missing one (the
    # old "{}") is a NULL string there -> crash on the manager select screen whenever the notice
    # popup opens. Garena's pages are gone: an empty page, or for the notice the admin page's picture.
    (re.compile(r"/game/inweburl/(\w+)"), lambda name, q, b: _json({"result": True, "url": notice_url() if name == "notice" else "about:blank"})),
    (re.compile(r"/match/([0-9a-f]{9,24})"), ep_match_get),
    (re.compile(r"/league2/sponsor/(\d+)"), ep_l2_sponsor),
    (re.compile(r"/cup/([0-9a-f]{24})/match"), ep_cup_match),
    (re.compile(r"/cup/([0-9a-f]{24})/next"), ep_cup_next),
    (re.compile(r"/cup/([0-9a-f]{24})/(?:resume|test)"), ep_cup_resume),
    (re.compile(r"/cup/([0-9a-f]{24})/leave"), ep_cup_leave),
    (re.compile(r"/cup/([0-9a-f]{24})"), lambda cupid, q, b: _json(next(
        (r["body"] for c in [(_state().get("cups") or {}).get(cupid)] if c for r in cup_resources(c)), {}))),
    # the ladder room (RoomController paths under /room/<id>) and its AI picker (RoomProxy
    # StaticData.fetch "SORT" + NATION | LEAGUE | TEAM, id): one list of ids each
    (re.compile(r"/room/\d+/matchmaking"), ep_matchmaking),
    (re.compile(r"/room/\d+/back"), ep_room_back),
    (re.compile(r"/room/\d+/type"), room_change(_room_type)), (re.compile(r"/room/\d+/AI"), room_change(_room_ai)),
    (re.compile(r"/room/\d+/side/(\w+)/ready"), room_change(_room_ready)),
    (re.compile(r"/room/\d+/leave"), room_change(_room_leave)), (re.compile(r"/room/\d+/invite/(\w+)"), room_change(_room_invite)),
    (re.compile(r"/room/\d+/[\w/]+"), room_change(lambda st, room, req: None)),  # slotsize, drawmode, ...: as it is
    (re.compile(r"/static/db/SORTLEAGUE/(\d+)"), lambda n, q, b: _json(json.loads(ep_sortleague(q, b)[1]).get(n, []))),
    (re.compile(r"/static/db/SORTTEAM/(\d+)"), lambda l, q, b: _json(json.loads(ep_sortteam(q, b)[1]).get(l, []))),
    (re.compile(r"/staff/recruit/(\d+)"), ep_staff_recruit), (re.compile(r"/staff/appoint/(\w+)"), ep_staff_appoint),
    (re.compile(r"/staff/study/(\w+)/complete"), ep_staff_study_complete), (re.compile(r"/staff/study/(\w+)"), ep_staff_study),
    (re.compile(r"/staff/enhance/(\w+)"), ep_staff_enhance), (re.compile(r"/staff/([0-9a-f]{24})"), ep_staff_fire),
    (re.compile(r"/exhibition/room/\d+/team"), room_change(_room_team)),  # Exhibition
    (re.compile(r"/exhibition/info/authentic/team/(\d+)"), lambda t, q, b: ep_authentic_team("all", t, q, b)),
    (re.compile(r"/esports2/region/register/(\w+)"), ep_koth_register_counts),  # King of the Hill
    (re.compile(r"/esports2/region/leaderboard/(\w+)"), ep_koth_leaderboard),
    # Komunitas > Klub (issue #36); /club/me, /club/find, ... are ENDPOINTS
    (re.compile(r"/club/fee/expansion/(\d+)"),
     lambda n, q, b: _json({"fee": CLUB_EXPAND_FEE, "newLimit": min(int(n) + CLUB_EXPAND, CLUB_LIMIT_MAX)})),
    (re.compile(r"/club/me/member/exclude/(\w+)"), ep_club_exclude), (re.compile(r"/club/me/member/(\w+)"), ep_club_member),
    (re.compile(r"/club/me/join/(\w+)"), ep_club_request), (re.compile(r"/club/me/expand/(\d+)"), ep_club_expand),
    (re.compile(r"/club/me/settings/(description|news|allowjoin|skipjudgement|color)(?:/(\w+))?"), ep_club_settings),
    (re.compile(r"/club/me/post/(\w*)"), ep_club_post), (re.compile(r"/club/record/(\w+)"), ep_club_record),
    (re.compile(r"/club/(\w+)/join"), ep_club_join), (re.compile(r"/club/(\w+)"), ep_club_get),
]


def route(path):
    for pat, fn in ROUTES:
        m = pat.fullmatch(path)
        if m:
            return lambda q, b, g=m.groups(): fn(*g, q, b)


ENDPOINTS = {
    "/info/server/config": ep_server_config,
    "/info/server/locale": ep_locale,
    "/info/server/time": ep_server_time,
    "/game/my/ipaddress": ep_my_ip,
    "/publisher/garena/sign": ep_garena_sign,
    "/publisher/nexon/2ndauth": ep_nexon_2ndauth,
    "/telemetry/gameconnection": ep_telemetry,
    "/shop/cash/charge/url": ep_shop_url,
    "/shop/cash/history/url": ep_shop_url,
}
ENDPOINTS["/contact/list"] = ep_contact_list
ENDPOINTS["/stadium/list"] = ep_stadium_list  # before the /stadium/<id> route
ENDPOINTS.update({"/ladder/reward": ep_ladder_reward, "/ladder/reward/info": lambda q, b: _json(ladder_rewards())})
ENDPOINTS.update({"/ladder/leaderboard": ep_ladder_leaderboard, "/ladder/friends/info": ep_ladder_friends,
                  "/ladder/my/info": ep_ladder_my_info, "/ladder/season/info": ep_ladder_season_info,
                  "/room": ep_room, "/matchmaking": ep_matchmaking})
ENDPOINTS["/coaches"] = ep_coaches
ENDPOINTS["/coach/me"] = ep_coach_me
# Multiple > Live Center (issue #36): its front screen read these unguarded, and {} gave two UI
# error popups (EXHIBITIONMATCH entrycondition.NORMAL_use, fixture curr.length). No live matches.
# Exhibition: EXHIBITION_PLAYS a day (/exhibition/info/points {point = played, maxpoint}; the button
# is on while plays are left). Its room (POST /exhibition/room, type pvp_exhibition) puts a licensed
# club on each side (side.<side>.authTeamId; the room's arrows walk recommendteams, "all teams"
# picks any: POST /exhibition/room/<id>/team {teamid, side}); the opponent is a friend invited from
# the list (a virtual manager, who joins with a club of his own and is ready), then my Ready plays
# Exhibition_PvP on the Rank path (ladder_match), each side the club's own squad, no team colors
# or staff; a squad change goes to that club (ep_match_squad).
ENDPOINTS.update({
    "/static/db/EXHIBITIONMATCH": lambda q, b: _json({"entrycondition": {"NORMAL_use": True, "IGR_use": False, "VIP_use": False},
                                                      "recommendteams": list(EXHIBITION_TEAMS),
                                                      "staticvalues": {k: EXHIBITION_PLAYS for k in ("Normal_count", "IGR_count", "VIP1_count")}}),
    "/exhibition/info/points": lambda q, b: _json({"point": exhibition_played(_state()), "maxpoint": EXHIBITION_PLAYS}),
    "/exhibition/room": ep_exhibition_room,
    "/livematch/info/fixture": lambda q, b: _json({"prev": [], "curr": [], "remainedsecs": 86400})})


# --------------------------------------------------------------------------- #
# Tim > Live Boost (user: "implementasikan fitur live boost ambil dari totw ea efc update perminggu";
# the account's mainmenu.teammanagement.liveboost; Market > Beli gets a "Terbaik minggu" tab too). The
# client has all of it (LiveCenterLiveBoostScreen, Module.big's PlayerBoostController) once
# features.livecenter.boost is on:
#  * GET /static/db/LIVEBOOST {weekid, month, weekofmonth, expiretime, liveplayers {groupid: [{pid,
#    boost, best11, position}]}}: kept while expiretime is ahead (an older one is dropped, "Received
#    Old Live Boost Data", and the button stays off), fetched again after it. Every card of a listed
#    player gets +boost (1..10) on every stat in the client: squad lists, the Market's rating, both
#    match sides (PlayerListUtil.checkBoostPlayer; not in Road to World).
#  * The screen: a stepper entry per group (LEAGUENAME(id), else LIVEBOOST_CUSTOM_<id>), its best11
#    in a 4-3-3 (1 GK, 4 DF, 3 MF, 3 FW by Enums.PLAYERPOSITION), 7 more as subs, each player as his
#    best card of any season (findPlayersInDB). Owning 11 of a group (grade > 0, not rental) turns on
#    "Terima": POST /live/boost/collecting/rewards {weekid, leagueid} -> {result, items} | {result:
#    false, reason: "rewarded" | "invalid week and league data"}; GET /live/boost/info {info.rewarded}.
#  * A PvP match's boostweekid must be that weekid: MatchController.processMatchWeekIDResource
#    fetches the data again on a mismatch before it sends ready (new_match).
# The players are EA FC's Team of the Week from fut.gg's API (EA's pages have no list; futbin, futwiz
# and fifplay answer a server with a Cloudflare challenge): the current FC's Team of the Week cards
# (rarity 3), the newest batch (a week's 23 come at once, Wednesday 18:00 UK), each +5 (user: "jika
# masuk totw rating tambah 5"); men only (FO3 has no women); each as his FO3 player id: EA's own id when
# fifadb has him with that birth date, else the 26 LIVE new player of that birth date and name
# (tools/fo4_season.py PIDS). One group, 2900 (the pack's LIVEBOOST_CUSTOM_2900, renamed in
# LANG_FIXES): 23 players of many leagues would never make 11 of one league. live_boost_loop checks
# every hour and fetches when a new week is due.
LIVEBOOST = os.path.join(HERE, "gamedata", "live_boost.json")
LIVEBOOST_GROUP = 2900
LIVEBOOST_BOOST = 5  # on every stat of every card of a Team of the Week player (the client takes 1..10)
LIVEBOOST_OWN = 11  # LiveCenterLiveBoostScreen.PLAYER_COUNT_FOR_REWARD
LIVEBOOST_REWARD_EP = 100_000  # x the player price setting
FUTGG, FUTGG_UA = "https://www.fut.gg/api/fut", {"User-Agent": "Mozilla/5.0"}  # urllib's own is refused
WEEK = 7 * 86400
LIVEBOOST_LINES = {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
LIVEBOOST_LINE = {**dict.fromkeys(("RB", "LB", "CB", "RWB", "LWB"), "DF"),
                  **dict.fromkeys(("CDM", "CM", "CAM", "RM", "LM"), "MF"),
                  **dict.fromkeys(("RW", "LW", "ST", "CF", "RF", "LF"), "FW"), "GK": "GK"}


def _futgg(path):
    import urllib.request
    with urllib.request.urlopen(urllib.request.Request(FUTGG + path, headers=FUTGG_UA), timeout=30) as r:
        return json.load(r)


def _utc(s):
    """fut.gg's "2026-11-04T18:00:44.769558Z" as a timestamp."""
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))


def totw_cards(now, get=_futgg):
    """(game, week, release time, cards) of the newest Team of the Week: FC <game> from September
    (the year before until a new FC has its first week), a week = the cards made within 3 days."""
    t = time.gmtime(now)
    game = t.tm_year % 100 + (t.tm_mon >= 9)
    for g in (game, game - 1):
        cards, page = [], 1
        while page:
            d = get(f"/players/v2/{g}/?page={page}&rarity_id=3")
            cards += d.get("data") or []
            page = d.get("next")
        if cards:
            break
    else:
        raise ValueError("fut.gg has no Team of the Week cards")
    cards.sort(key=lambda c: c["createdAt"])
    weeks = [[cards[0]]]
    for c in cards[1:]:
        if _utc(c["createdAt"]) - _utc(weeks[-1][-1]["createdAt"]) > 3 * 86400:
            weeks.append([])
        weeks[-1].append(c)
    return g, len(weeks), _utc(weeks[-1][0]["createdAt"]), weeks[-1]


def totw_pid(card, c, pids, fo4):
    """A Team of the Week card's FO3 player id, or None."""
    b = str(card.get("birthDate") or "")
    if len(b) != 8:
        return None
    y, m, d = int(b[:4]), int(b[4:6]), int(b[6:])
    row = c.execute("SELECT birthdate FROM players WHERE playerid = ? LIMIT 1", (int(card["basePlayerEaId"]),)).fetchone()
    if row and str(row[0]).split()[0] == f"{m}/{d}/{y}":
        return int(card["basePlayerEaId"])
    name = card.get("commonName") or f"{card.get('firstName') or ''} {card.get('lastName') or ''}"
    best = max(((fo4._same_name(name, k.split("|", 1)[1]), pid) for k, pid in pids.items()
                if k.startswith(f"{y}-{m}-{d}|")), default=(0, None))
    return best[1] if best[0] >= 1.5 else None


def totw_fetch(now=None, get=_futgg):
    """Fetch the newest Team of the Week into LIVEBOOST (the week kept as it is when it is not new)."""
    import importlib.util
    now = time.time() if now is None else now
    game, week, released, cards = totw_cards(now, get)
    old = live_boost()
    if old and (old["game"], old["week"]) == (game, week):
        return old
    spec = importlib.util.spec_from_file_location("fo4_season", os.path.join(HERE, "tools", "fo4_season.py"))
    fo4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fo4)
    try:
        with open(fo4.PIDS) as f:
            pids = json.load(f)
    except (OSError, ValueError):
        pids = {}
    players, missing = [], []
    with closing(_db()) as c:
        for card in sorted(cards, key=lambda x: -x["overall"]):
            name = card.get("commonName") or f"{card.get('firstName')} {card.get('lastName')}"
            pid = totw_pid(card, c, pids, fo4) if card.get("gender") == 1 else None
            if pid is None or any(p["pid"] == pid for p in players):
                missing.append(name)
                continue
            players.append({"pid": pid, "name": name, "overall": card["overall"], "position": card.get("position") or "CM",
                            "boost": LIVEBOOST_BOOST})
    left = dict(LIVEBOOST_LINES)
    for p in players:  # the best in each line of a 4-3-3 start
        line = LIVEBOOST_LINE.get(p["position"], "MF")
        p["best11"] = left[line] > 0
        left[line] -= p["best11"]
    # the screen fills a line's empty places with copies of its last player (4 x van Hecke): the
    # next best play there instead, out of position (the nearest line; nobody else in goal)
    order = ("FW", "MF", "DF")
    for p in players:
        line = LIVEBOOST_LINE.get(p["position"], "MF")
        free = [x for x in order if left[x]]
        if not p["best11"] and line != "GK" and free:
            spot = min(free, key=lambda x: abs(order.index(x) - order.index(line)))
            p.update(best11=True, position={"FW": "ST", "MF": "CM", "DF": "CB"}[spot])
            left[spot] -= 1
    lb = {"game": game, "week": week, "released": released, "fetched": now, "players": players, "missing": missing}
    os.makedirs(os.path.dirname(LIVEBOOST), exist_ok=True)
    with open(LIVEBOOST + ".tmp", "w", encoding="utf-8") as f:
        json.dump(lb, f, ensure_ascii=False, indent=1)
    os.replace(LIVEBOOST + ".tmp", LIVEBOOST)
    print(f"live boost: FC {game} Team of the Week {week}, {len(players)} players ({len(missing)} not in FO3)", flush=True)
    return lb


def live_boost():
    """The Team of the Week the server has (LIVEBOOST), or None."""
    try:
        with open(LIVEBOOST, encoding="utf-8") as f:
            lb = json.load(f)
        return lb if lb.get("players") else None
    except (OSError, ValueError):
        return None


def live_boost_weekid(lb=None):
    """FC 27's week 8 = 2708 (0 without a Team of the Week)."""
    lb = lb or live_boost()
    return lb["game"] * 100 + lb["week"] if lb else 0


def live_boost_loop():
    """The server's thread: the Team of the Week at start and whenever a new week is due (hourly)."""
    while True:
        lb = live_boost()
        if not lb or time.time() >= lb["released"] + WEEK:
            try:
                totw_fetch()
            except Exception as e:  # offline, fut.gg down: the last week stays
                print(f"live boost: {e!r}", flush=True)
        time.sleep(3600)


def ep_liveboost_static(query, body):
    """GET /static/db/LIVEBOOST. expiretime = the next week's release, or an hour on while it is late."""
    lb, now = live_boost(), time.time()
    if not lb:
        return _json({"weekid": 0, "month": 1, "weekofmonth": 1, "expiretime": w3c(now - 60), "liveplayers": {}})
    rel = time.gmtime(lb["released"])
    nxt = lb["released"] + WEEK
    return _json({"weekid": live_boost_weekid(lb), "month": rel.tm_mon, "weekofmonth": (rel.tm_mday - 1) // 7 + 1,
                  "expiretime": w3c(nxt if nxt > now + 600 else now + 3600),
                  "liveplayers": {str(LIVEBOOST_GROUP): [{k: p[k] for k in ("pid", "boost", "best11", "position")}
                                                         for p in lb["players"]]}})


def ep_liveboost_info(query, body):
    """GET /live/boost/info: this week's reward taken or not."""
    return _json({"info": {"rewarded": live_boost_weekid() in my_coach(_state()).get("liveboostrewards", [])}})


def ep_liveboost_reward(query, body):
    """POST /live/boost/collecting/rewards {weekid, leagueid}: EP to the Bag for owning 11 of this
    week's players (grade > 0, as the screen counts), once a week."""
    req = json.loads(body or b"{}")
    lb, st = live_boost(), _state()
    week, coach = live_boost_weekid(lb), my_coach(st)
    team = st["teams"].get(str(_default_slot(st))) or {"players": []}
    owned = {int(p["spid"]) % 1000000 for p in team["players"] if grade_of(p) > 0}
    if not lb or str(req.get("weekid")) != str(week) or str(req.get("leagueid")) != str(LIVEBOOST_GROUP) \
            or len(owned & {p["pid"] for p in lb["players"]}) < LIVEBOOST_OWN:
        return _json({"result": False, "reason": "invalid week and league data"})
    if week in coach.get("liveboostrewards", []):
        return _json({"result": False, "reason": "rewarded"})
    items = [{"itemid": EP_ITEM, "count": int(LIVEBOOST_REWARD_EP * ECONOMY["players"])}]
    bag_add(st, f"Hadiah LIVE BOOST - Team of the Week {lb['week']}", items,
            msg=f"Kamu memiliki {LIVEBOOST_OWN} pemain Team of the Week {lb['week']}.")
    coach.setdefault("liveboostrewards", []).append(week)
    _save(st)
    return _json({"result": True, "items": [{**it, "staticdata": item_def(it["itemid"])} for it in items]})


ENDPOINTS.update({"/chat/my/channel/message": ep_channel_message,  # global chat
                  "/chat/channel": lambda q, b: ep_channel_join(None, q, b)})
ROUTES += [(re.compile(r"/chat/channel/(\d+)"), ep_channel_join),
           (re.compile(r"/chat/coach/[^/]+/channel/detail"), lambda q, b: _json({"result": True, "channelid": 1, "coachid": ""}))]
ENDPOINTS.update({"/static/db/LIVEBOOST": ep_liveboost_static, "/live/boost/info": ep_liveboost_info,
                  "/live/boost/collecting/rewards": ep_liveboost_reward})
ENDPOINTS.update({  # King of the Hill (issue #36)
    "/static/initialize": lambda q, b: _json({"resources": [
        {"uri": "/static/db/ESPORTS2_BALANCING_DEFAULT", "body": {"divisions": ladder_divisions()}}]}),
    "/static/db/ESPORTS2_BALANCING_DEFAULT": lambda q, b: _json({"divisions": ladder_divisions()}),
    "/esports2/region/seasons/presently": ep_koth_presently, "/esports2/region/igrregion": lambda q, b: _json({"result": True}),
    "/esports2/region/register": ep_koth_register, "/esports2/region/intro": ep_koth_intro, "/esports2/region/reward": ep_koth_reward})
ENDPOINTS.update({"/club/fee/creation": lambda q, b: _json({"fee": CLUB_FEE}), "/club/create": ep_club_create,
                  "/club/me": ep_club_me, "/club/find": ep_club_find, "/club/me/members": ep_club_members,
                  "/club/me/member/leave": ep_club_leave, "/club/me/join/requests": ep_club_requests,
                  "/club/me/posts": ep_club_posts, "/club/me/message": ep_club_message,
                  "/club/me/delete/cancel": ep_club_undelete, "/contact/club": ep_contact_club})
ENDPOINTS.update({"/coach/me/teamvalue/history": ep_tv_history, "/rank/teamvalue/friend": ep_rank_friends,
                  "/rank/teamvalue/club": ep_rank_club, "/coach/me/hiderank": ep_hiderank,
                  "/coach/me/profile": ep_coach_profile})
ENDPOINTS.update({"/achievement/me": ep_achievement_me, "/static/info/ACHIEVEMENT": ep_static_achievement,
                  "/static/db/BADGES": ep_badges, "/static/db/ITEMNAMES": ep_itemnames, "/static/info/request": ep_quest_bank,
                  "/shop/list/GPSHOP": lambda query, body: _json(gp_shop_list() if missions_cfg()["enabled"] else [])})
ENDPOINTS["/coach/me/onready"] = ep_coach_onready
for _slot in (0, 1):  # two coach slots
    ENDPOINTS[f"/coach/{_slot}"] = lambda q, b, s=_slot: ep_create_coach(s, q, b)
    ENDPOINTS[f"/coach/{_slot}/new/step/3"] = lambda q, b, s=_slot: ep_new_team(s, q, b)
    ENDPOINTS[f"/coach/{_slot}/default"] = lambda q, b, s=_slot: ep_default_coach(s, q, b)
ENDPOINTS["/account"] = ep_account
ENDPOINTS["/account/option"] = ep_account_option
ENDPOINTS["/coach/me/squad"] = ep_coach_squad
ENDPOINTS["/static/info/authentic/team/recommendation"] = ep_recommendation
ENDPOINTS["/match/campaign"] = ep_match_campaign
ENDPOINTS["/game/my/tactic/all"] = ep_tactic_all
ENDPOINTS["/game/my/team/captain"] = lambda q, b: ep_team_role(None, q, b)
for _i in range(KICKERS):
    ENDPOINTS[f"/game/my/team/kickers/{_i}"] = lambda q, b, i=_i: ep_team_role(i, q, b)
for _i in range(TACTIC_SLOTS):
    ENDPOINTS[f"/game/my/tactic/{_i}"] = lambda q, b, i=_i: ep_tactic(i, q, b)
ENDPOINTS.update({
    "/league2/me": ep_l2_me, "/league2/reward": ep_l2_reward, "/league2/calendar": ep_l2_calendar,
    "/league2/match": ep_l2_match, "/league2/match/sim": lambda q, b: ep_l2_match(q, b, sim=True),
    "/league2/sleep": ep_l2_sleep, "/league2/end": ep_l2_end, "/league2/abandon": ep_l2_finish,
    "/league2/back": ep_telemetry, "/static/db/SORTTEAM": ep_sortteam,
    "/static/db/SORTNATION": ep_sortnation, "/static/db/SORTLEAGUE": ep_sortleague,
    "/league2/sponsor/achievement": ep_l2_achievement,
    # Rekor liga tabs: Rekorku (player records) and Rekor gabungan (best XI, stat leaders); the
    # league's own cup (cupinfo) is never entered, so its records stay empty
    "/league2/history/players/league": ep_l2_history_players, "/league2/leader/league": ep_l2_leaders,
    "/league2/history/players/cup": lambda q, b: _json([]),
    "/league2/leader/cup": lambda q, b: _json({k: [] for k in (*LINES, "ratings", "goals", "assists", "tackles", "passes", "saves")}),
    "/cup/list": ep_cup_list, "/cup/record": ep_cup_record, "/cup/new": ep_cup_new,
    "/transfer/offer": ep_transfer_offer, "/transfer/my/offer/list": ep_offer_list,
    "/transfer/my/boostfactor": lambda q, b: _json(NO_BOOST),
    "/players/trade/proposal": ep_trade_proposal, "/players/trade/contract": ep_trade_contract,
    "/players/trade2/proposal": lambda q, b: trade_proposal(b, trade2_props), "/players/trade2/contract": ep_trade_contract,
    "/players/trade2/info": ep_trade2_info, "/static/db/TRADE": lambda q, b: _json(TRADE2_TABLE),
    "/static/cache/TRADE_SPECIALPLAYERS": lambda q, b: _json(trade2_special()),
    "/account/trade2/usage": lambda q, b: _json({"result": True, "usedcount": 0, "maxcount": 0, "remainsec": 0}),
    "/trade3/contract": ep_trade_contract, "/trade3/proposal": ep_trade3_proposal,
    "/trade3/staticvalues": lambda q, b: _json(TRADE3_STATIC), "/trade3/info": ep_trade3_info,
    "/trade3/notification": lambda q, b: _json({"result": True}),
    "/static/db/PLAYERTRADE": lambda q, b: _json({"playergrade": {str(s): trade_grades(s) for s in range(2, 101)}}),
    "/static/db/EXPPLAYER": lambda q, b: _json(EXPPLAYER), "/static/db/EXPCOACH": lambda q, b: _json(EXPCOACH),
    "/static/db/PE2STATICVALUES": lambda q, b: _json(PE2STATICVALUES),
    "/static/db/LOCKERROOMITEM": lambda q, b: _json(lockerroom_items()),
    "/player/enhance2.5/restore": ep_enhance25_restore,
    "/events": ep_events, "/events/rewards/daily": ep_event_daily, "/events/rewards/dt": ep_event_dt,
    "/static/db/STATICVALUES/max_coachlevel": lambda q, b: _json(COACH_MAX_LEVEL),
    "/static/info/teamcolor": ep_teamcolor,
    "/shop/list": lambda q, b: _json(shop_list(_state())), "/shop/list/STSHOP": ep_stshop,
    "/shop/list/clear": lambda q, b: _json({"changed": SHOP_CHANGED}),
    "/coach/me/level/reward": ep_level_reward, "/static/db/REWARDS/coachlevelup": ep_level_reward_table,
    "/static/db/STAFF": lambda q, b: _json(staff_static()), "/staff/recruit": ep_staff_recruit_list,
    "/static/db/PLAYER_EXTRA_GROWTH": lambda q, b: _json(growth_db()),
    "/staff/recontract/preview": ep_staff_recontract_preview, "/staff/recontract": ep_staff_recontract,
    # the Shop's limited stock, polled each minute while it is open (ShopController.onRemainedChange
    # takes an array; {} raised "Kesalahan UI telah terjadi"): nothing is limited here
    "/shop/remained/limited": lambda q, b: _json([]), "/shop/remained/limited/STSHOP": lambda q, b: _json([]),
    "/specifictarget": ep_specifictarget, "/monthlypack": ep_monthlypack,
    "/shop/purchase": ep_shop_purchase, "/static/cache/item": ep_static_item,
    "/bag": lambda q, b: _json(bag_list(_state())), "/bag/unpackall": ep_bag_unpackall,
    "/items/itemtodepot": ep_item_to_depot, "/item/depot": ep_item_depot,
    "/players/trade/proposal/resend": lambda q, b: _json({"result": False, "reason": "NOT_SUPPORTED_REQUEST"}),
})


RESPONSES = os.path.join(HERE, "responses")


def from_file(path):
    """responses/<path, '/' -> '_'>.json overrides the handler; re-read every request so
    response shapes can be iterated against the live client without a restart."""
    f = os.path.join(RESPONSES, path.strip("/").replace("/", "_") + ".json")
    if path.startswith("/static/info/authentic/team/") and gen_teams.CURRENT_SEASON != gen_teams.LEGACY_SEASON:
        return None  # gen_teams.py's files hold 2017 squads: ep_recommendation / roster build them
    if os.path.isfile(f):
        with open(f, "rb") as fh:
            data = fh.read()
        edit = {"/static/roster": roster_seasons, "/static/db/STATICVALUES": static_values_out}.get(path)
        return ("application/json", edit(data) if edit else data)


def static_values_out(raw):
    """/static/db/STATICVALUES with max_playerlevel: the Player Growth page lists the cards at that
    level (int(undefined) = 0 listed none) and its cost formula uses the EXPPLAYER table's top."""
    d = json.loads(raw)
    d["max_playerlevel"] = PLAYER_MAX_LEVEL
    return json.dumps(d).encode()


@functools.lru_cache(maxsize=None)
def card_seasons():
    """The seasons of FO3's own cards in fifadb (not season 0, not the added ones)."""
    with closing(_db()) as c:
        return sorted(s for (s,) in c.execute("SELECT DISTINCT spid / 1000000 FROM players WHERE spid >= 1000000")
                      if s not in ADDED_SEASONS)


def roster_seasons(raw):
    """/static/roster with the seasons of the client's Market search (TransferUtil): the classic
    world leaves the added ones out, the modern one adds the admin page's own seasons. FO3's own
    seasons the list lacks (26: Kahn, Cafu, Rivaldo...; 32, 38, 41, 57) are added, by their
    TRANSFERMARKET_SEASON_<n> name where the language pack has one, else the number."""
    d = json.loads(raw)
    frame = {k: v for k, v in d["seasons"][0].items() if k in ("open", "close")}
    listed = {v for s in d["seasons"] for v in s["value"]}
    src = asset_path(LANG_PACK)
    names = client_json(src, LANG_ENTRY) if src else {}
    d["seasons"] += [{"season": f"$TRANSFERMARKET_SEASON_{s}" if f"TRANSFERMARKET_SEASON_{s}" in names else str(s),
                      "value": [s], **frame} for s in card_seasons() if s not in listed]
    if gen_teams.WORLD == "classic":
        d["seasons"] = [s for s in d["seasons"] if not set(s["value"]) & ADDED_SEASONS]
    else:
        d["seasons"] += [{"season": label, "value": list(codes), **frame}
                         for label, codes, _ in FA_SEASONS if codes[0] in CUSTOM_SEASON_CODES]
    return json.dumps(d).encode()


class H(http.server.BaseHTTPRequestHandler):
    def _log(self, body):
        line = (f"{time.strftime('%H:%M:%S')} {self.command} {self.path} "
                f"from {self.client_address[0]} host={self.headers.get('Host')} "
                f"len={len(body)} body={body[:2000]!r}")  # UI error stack traces run long
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _respond(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if body[:2] == b"\x1f\x8b":  # the client gzips larger bodies (telemetry, /coach/me/squad)
            body = gzip.decompress(body)
        self._log(body)
        path = self.path.split("?")[0]
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        # Unknown GETs get {}: a wrong shape in AS3 only raises a logged UI error, but []
        # where native code expects an object crashes the client (seen with
        # /static/db/PLAYER_EXTRA_GROWTH), and tables are id->row maps whose values get
        # iterated, so a stray "result": true becomes a bogus row. List endpoints get a
        # responses/ file with []. Unknown POSTs get {"result": true}.
        fallback = ep_telemetry if self.command == "POST" else ep_empty
        if path.startswith("/" + CDN_PREFIX):
            hit = cdn_file(path[len(CDN_PREFIX) + 1:])
            if hit is None:
                self.send_error(404)
                return
            ctype, data = hit
        else:
            try:
                with STATE_LOCK:
                    note_activity()
                    REQUEST["method"] = self.command
                    if b'"_method"' in body:
                        try:
                            REQUEST["method"] = str(json.loads(body).get("_method") or self.command).upper()
                        except (ValueError, AttributeError):
                            pass
                    ctype, data = from_file(path) or (ENDPOINTS.get(path) or route(path) or fallback)(query, body)
            except Exception:
                # an empty reply is a curl error and the client quits (CLIENT_CLOSE); answer
                # something parseable and keep the traceback in the log instead
                import traceback
                self._log(traceback.format_exc().encode())
                ctype, data = _json({"result": False})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _respond
    do_POST = _respond
    do_PUT = _respond  # /game/my/ipaddress is a real PUT; 501 there made the client quit
    do_DELETE = _respond

    def log_message(self, *a):
        pass  # we do our own logging


if __name__ == "__main__":
    # Windows SO_REUSEADDR lets a second copy bind the same port and split the client's
    # requests between two servers; refuse instead (a second play.bat then just fails)
    http.server.ThreadingHTTPServer.allow_reuse_address = os.name != "nt"
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H)
    if TLS:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT)
        # Lazy handshake: a failed handshake (e.g. client rejects the cert) then
        # surfaces as a logged traceback in the handler thread instead of vanishing.
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    print(f"http config stub on 0.0.0.0:{PORT}{' (https)' if TLS else ''}, log -> {LOG}", flush=True)
    if not os.path.isdir(os.path.join(ASSETS, "externalAssets")):
        print(f"WARNING: no client cache at {ASSETS}: crests, faces and shop images will show the "
              "game's defaults. Set FO3_GAME to your FO3 ID folder (or FO3_ASSETS).", flush=True)
    if ADMIN_PORT:
        import admin
        admin.start(sys.modules[__name__], ADMIN_PORT)
        print(f"admin page on http://127.0.0.1:{ADMIN_PORT}/", flush=True)
    threading.Thread(target=warm_packs, daemon=True, name="packs").start()
    threading.Thread(target=presence_loop, daemon=True, name="presence").start()
    threading.Thread(target=market_loop, daemon=True, name="market").start()
    threading.Thread(target=live_boost_loop, daemon=True, name="liveboost").start()
    threading.Thread(target=news_loop, daemon=True, name="news").start()
    srv.serve_forever()
