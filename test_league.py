"""Self-check for the Single > Liga server logic in http_config.py (no client needed).

    python test_league.py

Runs a whole season through the handlers against a temporary copy of gamedata/state.json.
"""
import calendar, copy, json, os, random, re, shutil, struct, sys, tempfile, time, zlib
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import http_config as hc


def body(resp):
    return json.loads(resp[1])


def main():
    # schedule: every club once per round, every pair twice, once at each ground
    teams = list(range(1, 11))
    rounds = [hc.league_round(teams, r) for r in range(2 * (len(teams) - 1))]
    for pairs in rounds:
        assert sorted(t for p in pairs for t in p) == teams, pairs
    meet = Counter(frozenset(p) for pairs in rounds for p in pairs)
    assert set(meet.values()) == {2} and len(meet) == 45, meet
    homes = Counter(p for pairs in rounds for p in pairs)
    assert set(homes.values()) == {1}, [p for p, n in homes.items() if n != 1]

    tmp = tempfile.mkdtemp()
    try:
        hc.STATE = os.path.join(tmp, "state.json")
        hc.SHOP_FILE = os.path.join(tmp, "shop.json"); hc.apply_shop({})  # the built-in Shop, not the live edits
        hc.ECONOMY_FILE = os.path.join(tmp, "economy.json"); hc.ECONOMY = dict(hc.ECONOMY_DEFAULT)  # not the live one
        hc.MATCH_REWARD_FILE = os.path.join(tmp, "match_rewards.json")  # default match rewards; admin test writes here
        hc.MISSIONS_FILE = os.path.join(tmp, "missions.json")  # default missions; admin test writes here
        hc.CRAFT_FILE = os.path.join(tmp, "craft.json")  # the default Kontrak pemain list; admin test writes here
        hc.VM_GEN_FILE = os.path.join(tmp, "vm_gen.json"); hc.vm_set_generation(0)  # the first crowd, not a regenerated one
        # a fresh account through the client's club creation (the live state may hold no manager)
        hc._save({"coaches": [], "defaultcoachid": "", "teams": {}})
        # issue #42: the boot asks King of the Hill's registrations before any manager exists (the
        # client quit on the error); nothing is kept for no one
        assert body(hc.route(f"/esports2/region/register/{hc.koth_sid()}")("", b""))["result"] and hc._state()["coaches"] == []
        spids = [p["spid"] for p in hc.roster(241)["team"]["players"]]  # 2017 file, or built for 26 LIVE
        assert body(hc.ENDPOINTS["/coach/0/new/step/3"]("", json.dumps({"id": 241, "spids": spids}).encode()))["result"]
        assert body(hc.ENDPOINTS["/coach/0"]("", json.dumps({"id": 241, "coachName": "Tester"}).encode()))["result"]
        assert body(hc.ENDPOINTS["/coach/0/default"]("", b""))["result"]
        shown = hc.coach_me(hc._state(), 0)["team"]  # the client saves the squad it shows (11 + 7 subs)
        hc.ENDPOINTS["/coach/me/squad"]("", json.dumps({k: shown[k] for k in ("squad", "kickers", "captain")}).encode())
        st = hc._state()
        assert len(st["teams"]["0"]["squad"]["positions"]) == 18
        me = hc.my_teamid(st)
        # a new club's cards are +0: no market value, not sellable
        assert all(p["grade"] == 0 == p["price"] for p in hc.coach_me(st, 0)["team"]["players"])
        # levels start at 1; exp tables: player LV.1-23 (24 rows), coach 1-100
        assert hc.coach_me(st, 0)["levelinfo"] == {"level": 1, "exp": 0, "to": hc.coach_need(1)}
        assert len(body(hc.ENDPOINTS["/static/db/EXPPLAYER"]("", b""))) == hc.PLAYER_MAX_LEVEL + 1
        assert body(hc.ENDPOINTS["/static/db/STATICVALUES/max_coachlevel"]("", b"")) == hc.COACH_MAX_LEVEL
        # Tim > Formasi: the club's own formation first (Barcelona has none: 4-4-2(1)), then the
        # 33 common ones, each with 11 positions + offsets (needs the client's data_0.big)
        forms = hc.coach_me(st, 0)["team"]["formations"]
        if hc.formation_table()["common"]:
            assert len(forms) == 33 and forms[0]["formationname"] == "4-4-2(1)"
            assert all(len(f["positions"]) == 11 == len(f["offsets"]) and f["positions"][0] == 0 for f in forms)
            assert hc.coach_me(st, 0)["team"]["squad"]["formation"] == forms[0]["formationid"]
        # team color: feature on, the colors table, and the XI's active colors (a fresh Barcelona
        # XI = 11 Barcelona players: the top club tier); AI match sides get none
        # Opsi > Kontrol: /account always has a full controlOption (none = key changes never
        # apply or save, {} crashes the client); a saved one comes back, an incomplete one is ignored
        acc_ctl = lambda: body(hc.ENDPOINTS["/account"]("", b""))["option"]["controlOption"]
        assert acc_ctl() == dict(hc.CONTROL_DEFAULTS, selectedIndex=0, index=0) and len(acc_ctl()) == 37
        # Controller 1 = the Gamepad tab (Joy* lists); the next login's OptionManager.initialize
        # then starts with the pad (keyboardControl = Controller == 0)
        mine = dict(hc.CONTROL_DEFAULTS, Controller=1, Type=2, JoyAttType3=[3, 4, 2, 1, 5, 7, 6, 8])
        save_opt = lambda o: hc.ENDPOINTS["/account/option"]("", json.dumps(o).encode())
        cam = {"Camera": 2, "CamDetail": 0}  # Co-op (F5 in a match)
        r = body(save_opt({"soundOption": {"BGM": 3}, "controlOption": mine, "cameraOption": cam}))
        # the reply carries the saved options: AccountCommand.saveGameOption reads resources[0].body
        # (OptionManager's backup for every zone change; none = login options back in a match, issue #21)
        opt = r["resources"][0]["body"]
        assert opt == body(hc.ENDPOINTS["/account"]("", b""))["option"] and opt["controlOption"] == dict(mine, selectedIndex=0, index=2)
        assert opt["cameraOption"] == {**hc.CAMERA_DEFAULTS, **cam}
        # Opsi > Kamera: every gauge has a value (none = empty, could not be stepped, issue #24); a
        # null saved while one was NaN gets the default
        save_opt({"cameraOption": {"Camera": 1, "CamDetail": 1, "CamHeight": None, "CamZoom": 14}})
        assert body(hc.ENDPOINTS["/account"]("", b""))["option"]["cameraOption"] == dict(
            hc.CAMERA_DEFAULTS, Camera=1, CamDetail=1, CamHeight=10, CamZoom=14)
        save_opt({"soundOption": {"BGM": 4}, "controlOption": {}})  # e.g. leaving a match
        assert acc_ctl() == dict(mine, selectedIndex=0, index=2) and body(hc.ENDPOINTS["/account"]("", b""))["option"]["soundOption"]["BGM"] == 4
        save_opt({"soundOption": {"BGM": 10}, "controlOption": hc.CONTROL_DEFAULTS})
        # issue #32: each device keeps its own preset. The Kontrol screen resets a device it switches to
        # to preset 0 (Type 0); the server gives it back its last one
        kb = lambda t, **k: dict(hc.CONTROL_DEFAULTS, Controller=0, Type=t, **k)
        pad = lambda t, **k: dict(hc.CONTROL_DEFAULTS, Controller=1, Type=t, **k)
        ctl_type = lambda o: body(save_opt({"controlOption": o}))["resources"][0]["body"]["controlOption"]["Type"]
        assert ctl_type(kb(1)) == 1  # keyboard: FIFA Online 2 Baru
        assert ctl_type(pad(0)) == 2  # to the pad: its DPAD saved above comes back
        assert ctl_type(pad(1)) == 1  # the pad: Tipe W
        assert ctl_type(kb(0)) == 1 and acc_ctl()["Type"] == 1  # back to the keyboard: FO2 Baru, at the next login too
        assert ctl_type(pad(5)) == 1  # the beginner's guide flips Controller with the keyboard's Type: the pad's own
        assert ctl_type(pad(0)) == 0  # preset 0 picked without switching: taken
        assert ctl_type(kb(0, KeyAttType1=[69, 65, 68, 83, 81, 90, 67, 87])) == 0  # switched and remapped preset 0: taken
        mapped = [3, 4, 2, 1, 5, 7, 6, 8]  # the pad's own keys, kept in every PUT (the client sends all lists)
        assert ctl_type(pad(1, JoyAttType1=mapped)) == 1 and ctl_type(kb(5, JoyAttType1=mapped)) == 5
        assert ctl_type(pad(0)) == 1  # switched to the pad, then Initialize (default keys; Type stays the switch's 0)
        # the patched Kontrol screen (tools/control_presets.py) swaps the presets itself on a switch:
        # both are in controlOption (selectedIndex keyboard, index pad), a switch sets selected and
        # its Type stands (preset 0 too); the other device's preset comes back as the screen left it
        save_opt({"controlOption": kb(1)}); save_opt({"controlOption": pad(1)})  # keyboard FO2 Baru, pad Tipe W
        c = acc_ctl()
        assert (c["Controller"], c["Type"], c["selectedIndex"], c["index"]) == (1, 1, 1, 1) and "selected" not in c
        sw = lambda o: body(save_opt({"controlOption": o}))["resources"][0]["body"]["controlOption"]
        r = sw(kb(0, selectedIndex=1, index=2, selected=True))  # to the keyboard, its Default picked; the pad left at DPAD
        assert (r["Type"], r["selectedIndex"], r["index"]) == (0, 0, 2) and hc._state()["controlType"] == {"0": 0, "1": 2}
        assert not {"selectedIndex", "index", "selected"} & set(hc._state()["option"]["controlOption"])
        assert sw(kb(3, index=7))["index"] == 2  # out of the pad's range: not taken
        # the patch itself, on the client's own screen when the client is here: 55 bytes in place
        # of the reset, branches to the pad part (71) and past both (89)
        sys.path[:0] = [os.path.join(HERE, "tools"), os.path.join(HERE, "tools", "re")]
        import zlib, importlib.util, control_presets, bigdir
        big = os.path.join(hc.GAME, control_presets.BIG)
        if os.path.isfile(big):
            gfx = bigdir.read(big, control_presets.ENTRY)
            new = control_presets.patched_gfx(gfx) or gfx  # None: patched already
            assert control_presets.patched_gfx(new) is None
            spec = importlib.util.spec_from_file_location("abcp", os.path.join(HERE, "tools", "re", "abc.py"))
            abcp = importlib.util.module_from_spec(spec); spec.loader.exec_module(abcp)
            swf = b"FWS" + new[3:8] + zlib.decompress(new[8:])
            abc = abcp.parse(swf, 0x4c80)
            code = abcp.dis(abc, abc["body"][6])
            assert any("iftrue -> 71" in x for x in code) and any("jump -> 89" in x for x in code) and len(code) > 50
            props = [x.split(": ", 1)[1] for x in code if "property" in x]
            i = props.index("setproperty selected")
            assert props[i:i + 11] == [
                "setproperty selected", "getproperty Controller", "getproperty Type", "setproperty index",
                "getproperty selectedIndex", "setproperty Type", "getproperty Type", "setproperty selectedIndex",
                "getproperty index", "setproperty Type", "getproperty _option"], code
        # issue #34, Vietnamese commentary (tools/commentary.py): a VN client's speechdata\vie_vn copied
        # over and vie,vn first in the ID settings of data/loc/locale.big, patched in place (on a copy)
        import commentary
        loc = os.path.join(hc.GAME, commentary.BIG)
        if os.path.isfile(loc):
            game, vn = os.path.join(tmp, "game"), os.path.join(tmp, "vn")
            os.makedirs(os.path.join(game, "data", "loc"))
            shutil.copyfile(loc, os.path.join(game, commentary.BIG))
            os.makedirs(os.path.join(vn, commentary.SPEECH, "vie_vn"))
            for f in ("vie_vn.xml", "VIE_VN_BANK.sbr", "VIE_VN_BANK.sbs"):
                open(os.path.join(vn, commentary.SPEECH, "vie_vn", f), "wb").close()
            commentary.BACKUP = os.path.join(tmp, "commentary_backup.big")
            ini = lambda path: {n: bigdir.read(path, n) for n, *_ in bigdir.entries(path)}
            dst = commentary.install(game, vn)
            assert sorted(os.listdir(dst)) == ["VIE_VN_BANK.sbr", "VIE_VN_BANK.sbs", "vie_vn.xml"]
            was, now = ini(loc), ini(os.path.join(game, commentary.BIG))
            assert {n for n in was if was[n] != now[n]} == set(commentary.INIS)
            for n in commentary.INIS:  # both commentary entries vie,vn: the game speaks its own eng,us while listed
                want = was[n]
                for eng, vie in commentary.PAIRS:
                    want = want.replace(eng, vie)
                assert now[n] == want and want.count(b",us") == was[n].count(b",us") - 2
            assert os.path.getsize(os.path.join(game, commentary.BIG)) == os.path.getsize(loc)
            assert commentary.install(game, vn) == dst  # again: nothing left to switch
            commentary.remove(game)
            assert ini(os.path.join(game, commentary.BIG)) == was
            try:
                commentary.install(game, os.path.join(tmp, "no_vn"))  # no vie_vn there
                assert False
            except SystemExit as e:
                assert "vie_vn" in str(e)
        st = hc._state(); st["option"]["controlOption"] = pad(5); st.pop("controlType"); hc._save(st)  # saved before the fix
        assert acc_ctl()["Type"] == 0  # shown as the pad's Default
        assert ctl_type(kb(0)) == 5 and hc._state()["controlType"] == {"0": 5}  # the 5 was the keyboard's (a guide flip)
        save_opt({"controlOption": hc.CONTROL_DEFAULTS})
        assert "teamcolor" in body(hc.ENDPOINTS["/account"]("", b""))["features"]
        tc = body(hc.ENDPOINTS["/static/info/teamcolor"]("", b""))
        assert {hc.TC_CLUB, hc.TC_NATION} <= {c["teamcolorid"] for c in tc["teamColors"]}
        assert set(tc["teamColorSpecials"]) == {str(c["teamid"]) for c in tc["teamColors"] if c["type"] == "special"}
        active = hc.coach_me(st, 0)["team"]["teamcolors"]
        assert {"teamcolorid": hc.TC_CLUB, "boostindex": 3, "teamid": 241} in active, active
        # a season (special) color's teamid must be a string: native reads it with asCString at
        # half time (a number crashed every second half)
        assert all(isinstance(c["teamid"], str) for c in tc["teamColors"] if c["type"] == "special")
        kind = {c["teamcolorid"]: c["type"] for c in tc["teamColors"]}
        assert len(kind) == len(tc["teamColors"])  # unique ids
        assert all(isinstance(c["teamid"], str) == (kind[c["teamcolorid"]] == "special") for c in active), active
        # the client's named colors (user: "pakai semua team color yang belum masuk"): a Barcelona XI
        # also meets "Barcelona" (TEAM2: Single team's tiers + MF agility +3) and "Spanyol" for its
        # Spaniards; every color the client names is sent except the left-out ones
        named = {c["teamcolorid"]: c for c in tc["teamColors"]}
        assert {"teamcolorid": 2, "boostindex": 3, "teamid": 241} in active, active
        assert named[2]["boostgroups"][3]["boosts"] == [{"type": "ovr", "amount": 4}, {"type": "agility", "amount": 3, "target": "mf"}]
        assert named[50091]["name"] == "TEAMCOLOR_ATTRIBUTES_TEAM18" and named[50091]["boostgroups"][2]["boosts"][0]["amount"] == 5
        assert {1, 16, 101, 130, 20, 39, 20002, 20030, 20043, 20071, 40001, 3064} <= set(named)
        assert named[50093]["name"] == "TEAMCOLOR_ATTRIBUTES_TEAM100"  # World Legend: a season color, the client's name
        msn = named[20002]
        assert [(g["count_min"], g["count_max"]) for g in msn["boostgroups"]] == [(3, 11)]
        assert all(c["teamcolorid"] < 70000 and c["teamcolorid"] not in (20055, 20056, 20057, 20058) for c in tc["teamColors"])
        # issue #44: 20028 / 20029 ("Red Day Boost", the Thai pack's "Red Devil") are Manchester United's
        # cards, not every '17 card and every card: a Barcelona XI has neither
        assert not {20028, 20029} & {c["teamcolorid"] for c in active}, active
        with hc.closing(hc._db()) as c:
            mu = {r[0] for r in c.execute(f"SELECT spid FROM players p WHERE {hc._CLUB(11)}")}
            every = c.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        reds = hc.tc_special_sets()[20029][0]
        assert reds and reds <= mu and len(reds) < every / 10 and hc.tc_special_sets()[20028][0] <= reds
        # form arrows (issue #44): every card has a condition the squad list draws, the same all day
        cond = [p["condition"] for p in hc.coach_me(st, 0)["team"]["players"]]
        assert set(cond) <= {f for f, _ in hc.FORMS} and len(set(cond)) > 1
        assert cond == [p["condition"] for p in hc.coach_me(st, 0)["team"]["players"]]
        assert {hc.player_form(f"c{i}", "2026-11-11") for i in range(200)} == {f for f, _ in hc.FORMS}
        assert [hc.player_form(f"c{i}", "2026-11-11") for i in range(50)] != [hc.player_form(f"c{i}", "2026-11-12") for i in range(50)]
        assert hc.match_side(st["teams"]["0"], 241, user={"id": "me"})["team"]["teamcolors"] == active
        assert hc.match_side(st["teams"]["0"], 241)["team"]["teamcolors"] == []
        # a match side lists the 18 first: the HUD's season badge takes the first card of a player
        # there, and a reserve card of a starter (26 LIVE Mbappe vs WC18 Mbappe) must not win
        t2 = json.loads(json.dumps(st["teams"]["0"]))
        with hc.closing(hc._db()) as c:
            twin = next((s, r[0]) for s in t2["squad"]["positions"] if s["pos"] < hc.POS_SUB
                        for r in c.execute("SELECT spid FROM players WHERE playerid = ? AND spid != ? AND spid >= 1000000",
                                           (s["spid"] % 1000000, s["spid"])))
        t2["players"].insert(0, {"_id": "ab" * 12, "spid": twin[1], "team2": True})
        cards = hc.match_side(t2, 241, user={"id": "me"})["team"]["players"]
        assert next(p for p in cards if p["spid"] % 1000000 == twin[1] % 1000000)["_id"] == str(twin[0].get("id") or twin[0]["spid"])
        in18 = {str(s.get("id") or s["spid"]) for s in t2["squad"]["positions"]}
        order = [p["_id"] for p in cards]
        assert len(cards) == len(t2["players"]) and max(order.index(i) for i in in18) < order.index("ab" * 12)
        # upgrade colors (type "grade"): 7 starters at +5 or higher meet +2 (index 1) and +5 (index 1)
        grades = [c for c in tc["teamColors"] if c["type"] == "grade"]
        assert [(c["grade"], c["name"]) for c in grades] == [(g, n) for _, g, n, _ in hc.TC_GRADE]
        assert not {c["teamcolorid"] for c in grades} & {c["teamcolorid"] for c in active}  # +0 club cards
        t0 = json.loads(json.dumps(st["teams"]["0"]))
        xi_ids = [str(s.get("id") or s["spid"]) for s in t0["squad"]["positions"] if s["pos"] < hc.POS_SUB]
        for p in t0["players"]:
            p["grade"] = 6 if hc.iid(p) in xi_ids[:7] else p.get("grade", 0)
        got = {c["teamcolorid"]: c["boostindex"] for c in hc.active_teamcolors(hc.owned_team(t0)) if c["teamid"] == -1}
        assert got == {10002: 1, 10005: 1}, got
        # single-grade colors need all 11 starters: the whole XI at +8 meets "+3 club" … "+8 Golden passion"
        for p in t0["players"]:
            p["grade"] = 8 if hc.iid(p) in xi_ids else p.get("grade", 0)
        got = {c["teamcolorid"]: c["boostindex"] for c in hc.active_teamcolors(hc.owned_team(t0)) if c["teamid"] == -1}
        assert got == {10002: 2, 10005: 2, 10008: 2, **{10100 + g: 0 for g in range(3, 9)}}, got
        # issue #18: summary.ovr FW/MF/DF = the average of the ratings the squad list shows (rating
        # at the slot on stats + grade boost + level - 1, plus the team color boosts)
        tm = hc.owned_team(t0); tm["teamcolors"] = hc.active_teamcolors(tm)
        shown, bare = hc.xi_ratings(tm), hc.xi_ratings(dict(tm, teamcolors=[]))
        pos = {s["id"]: s["pos"] for s in tm["squad"]["positions"] if s["pos"] < hc.POS_SUB}
        summ = hc.team_summary(tm, 241)["ovr"]
        for k, r in hc.LINES.items():
            v = [shown[i] for i, p in pos.items() if p in r]
            assert k == "GK" or summ[k] == round(sum(v) / len(v)), (k, summ, v)
        card = {p["id"]: p for p in tm["players"]}
        gk = next(i for i, p in pos.items() if p == 0)
        assert bare[gk] == hc.card_ovr(card[gk]["spid"]) + hc.GRADE_BOOST[8] + card[gk]["levelinfo"]["level"] - 1
        assert all(shown[i] > bare[i] for i in pos), (shown, bare)  # club + grade colors
        # a match side's bars are the pre-match list's ratings, without team color (the list there
        # shows none: 86 over a list of 79-83); its team colors still go to the match
        side = hc.match_side(t0, 241, {"id": "u"})
        for k, r in hc.LINES.items():
            v = [bare[i] for i, p in pos.items() if p in r]
            assert k == "GK" or side["team"]["summary"]["ovr"][k] == round(sum(v) / len(v)), (k, side["team"]["summary"], v)
        assert side["team"]["teamcolors"] == tm["teamcolors"] and summ != side["team"]["summary"]["ovr"]
        res = next(p for p in st["teams"]["0"]["players"] if not hc.in_squad(st["teams"]["0"], p))
        assert not body(hc.ENDPOINTS["/transfer/offer"]("", json.dumps({"playerobjectid": hc.iid(res), "price": 1000}).encode()))["result"]
        # club picker ("Tim lain"): nation -> league -> club lists agree; a club without a starter
        # file (Man City) gets its squad built on the fly; starter files carry name id + stars
        nations, leagues = (body(hc.ENDPOINTS[f"/static/db/{t}"]("", b"")) for t in ("SORTNATION", "SORTLEAGUE"))
        clubs = body(hc.ENDPOINTS["/static/db/SORTTEAM"]("", b""))
        assert nations and all(clubs[str(lg)] for n in nations for lg in leagues[str(n)])
        assert not any(hc.REST_OF_WORLD in ts for ts in clubs.values())  # not a club to pick
        # issue #30: Multiple > Rank's virtual managers, in LadderMatchController's shapes
        vms = hc.virtual_managers()
        assert len({m["name"] for m in vms}) == len(vms) >= hc.VM_COUNT and {m["grade"] for m in vms} <= set(range(1, 7))
        # stage 3, the crowd grows: newcomers from crowd day 1 (2026-11-02), each from his join hour,
        # with a name nobody has; one from an earlier season starts the next with no games; VM_MAX caps it
        assert len(hc.vm_crowd(202611, 1, 23)) == hc.VM_COUNT + hc.VM_EXTRA + hc.VM_EXTRA2
        assert len(hc.vm_newcomers(400)) == hc.VM_MAX - hc.VM_COUNT - hc.VM_EXTRA - hc.VM_EXTRA2
        nc = hc.vm_newcomers(60)
        assert 30 <= len(nc) <= 180 and [m["_id"] for *_, m in nc] == [f"vm{hc.VM_COUNT + k:04d}" for k in range(len(nc))]
        assert len({m["name"] for *_, m in nc} | {m["name"] for m in hc.vm_base()}) == len(nc) + hc.VM_COUNT
        jd, jh, new = nc[0]
        assert new["_id"] not in {m["_id"] for m in hc.vm_crowd(202611, jd + 1, jh - 1)}
        assert new["_id"] in {m["_id"] for m in hc.vm_crowd(202611, jd + 1, jh)}
        dec = {m["_id"]: m for m in hc.vm_crowd(202612, 1, 0)}
        assert all(sum(dec[m["_id"]]["wins"] + dec[m["_id"]]["losses"]) == 0 for d, _, m in nc if d < 30)
        # and lives: transfers, upgrade tries (+-1 by the Upgrade odds), levels; the same day, the same squad
        s0, s90 = hc.vm_squad("vm0095", 0), hc.vm_squad("vm0095", 90)
        assert {p["spid"] for p in s0["players"]} != {p["spid"] for p in s90["players"]} and hc.vm_squad("vm0095", 90) is s90
        g = hc.vm_by_id("vm0095")["grade"]
        assert all(1 <= p["grade"] <= g + 2 and 1 <= p["levelinfo"]["level"] <= hc.PLAYER_MAX_LEVEL for p in s90["players"])
        assert [p["grade"] for p in s0["players"]] != [p["grade"] for p in s90["players"]]
        hc.vm_squad.cache_clear()
        assert hc.vm_squad("vm0095", 90) == s90 and len({p["levelinfo"]["level"] for p in s0["players"]}) > 1
        # the crowd plays through the season: ratings and records move, the same hour gives the same crowd
        early, late = hc.vm_crowd(202611, 1, 0), hc.vm_crowd(202611, 20, 23)
        assert [{k: v for k, v in m.items() if k != "hours"} for m in early] == hc.vm_base() + hc.vm_extra()  # nothing played at 00
        assert late == hc.vm_crowd(202611, 20, 23) and any(a["elo"] != b["elo"] for a, b in zip(early, late))
        assert all(sum(b["wins"] + b["losses"]) > sum(a["wins"] + a["losses"]) for a, b in zip(early, late))
        # some 2,000 managers (user: "max player ai online 1000", doubled for issue #36): the first
        # crowd keeps its ids and names (the friends), the rest of the population (two groups, the
        # second from vm4000) is there from day 0 under names of its own that the chat overlay leaves
        # alone, newcomers fill up to VM_MAX
        ext = hc.vm_extra()
        assert len(ext) == hc.VM_EXTRA + hc.VM_EXTRA2 and [m["_id"] for m in ext[:2]] == [f"vm{hc.VM_EXTRA_ID:04d}", f"vm{hc.VM_EXTRA_ID + 1:04d}"]
        assert ext[hc.VM_EXTRA]["_id"] == f"vm{hc.VM_EXTRA2_ID:04d}" and ext[-1]["_id"] == f"vm{hc.VM_EXTRA2_ID + hc.VM_EXTRA2 - 1:04d}"
        names = {m["name"] for m in ext}
        assert len(names) == len(ext) and not names & ({m["name"] for m in hc.vm_base()} | {m["name"] for *_, m in nc})
        assert not names & hc.VM_NAMES3_MASKED
        assert not [n for n in names if "tai" in n.lower()] and hc.vm_by_id(ext[-1]["_id"])["name"] == ext[-1]["name"]
        assert hc.vm_friends() == hc.virtual_managers()[:hc.VM_COUNT:hc.VM_COUNT // hc.VM_FRIENDS]
        assert {m["_id"] for m in hc.vm_friends()} <= {m["_id"] for m in hc.vm_base()}

        def ref_crowd(sid, day, hour):  # every day played from day 1, as before the day-by-day crowd
            first = hc.vm_season_first(sid); today = first + day - 1
            joined = {m["_id"]: (jd, jh) for jd, jh, m in hc.vm_newcomers(today) if (jd, jh) <= (today, hour)}
            out = copy.deepcopy(hc.vm_base() + hc.vm_extra() + [m for _, _, m in hc.vm_newcomers(today) if m["_id"] in joined])
            for vm in out:
                jd, jh = joined.get(vm["_id"], (None, 0))
                skill = hc.random.Random(f"{vm['_id']}/skill").randint(-60, 60)
                for d in range(1 if jd is None else max(1, jd - first + 1), day + 1):
                    rnd = hc.random.Random(f"{vm['_id']}/{sid}/{d}")
                    for i in range(hc.LADDER_COUNT):
                        for _ in range(rnd.choice(hc.VM_GAMES)):
                            at, opp = rnd.choice(hc.VM_HOURS), vm["elo"][i] + rnd.randint(-80, 80)
                            won = rnd.random() < 1 / (1 + 10 ** ((opp - vm["elo"][i] - skill) / 400))
                            if first + d - 1 == jd and at < jh:
                                continue
                            if d == day and i in (0, 3):
                                vm.setdefault("hours", []).append(at)
                            if d < day or at <= hour:
                                expect = 1 / (1 + 10 ** ((opp - vm["elo"][i]) / 400))
                                vm["elo"][i] = max(hc.LADDER_START, round(vm["elo"][i] + hc.LADDER_K * (won - expect)))
                                vm["wins" if won else "losses"][i] += 1
            return out

        def fresh(extra, extra2):
            hc.VM_EXTRA, hc.VM_EXTRA2 = extra, extra2
            for f in (hc.vm_extra, hc.vm_newcomers, hc.vm_crowd):
                f.cache_clear()
            hc.VM_SEASON.clear()
        real_extra = hc.VM_EXTRA, hc.VM_EXTRA2
        fresh(40, 20)  # a smaller population: the same rules, a quicker check
        for sid, day, hour in ((202611, 1, 0), (202611, 3, 5), (202611, 3, 20), (202611, 9, 12), (202611, 10, 1),
                               (202611, 7, 3), (202612, 2, 23), (202701, 30, 19)):  # on, on, back, other seasons
            assert hc.vm_crowd(sid, day, hour) == ref_crowd(sid, day, hour), (sid, day, hour)
        fresh(*real_extra)
        t = time.perf_counter(); hc.vm_crowd(202611, 20, 10); cold = time.perf_counter() - t
        t = time.perf_counter(); hc.vm_crowd(202611, 20, 11); hourly = time.perf_counter() - t
        assert hourly < cold / 3, (hourly, cold)  # an hour plays today's games only
        lb = body(hc.ENDPOINTS["/ladder/leaderboard"]("", b""))
        assert len(lb) == hc.LADDER_COUNT and [e["rank"] for e in lb[3][:3]] == [1, 2, 3] and lb[3][0]["elo"] >= lb[3][1]["elo"]
        squad = hc.vm_team(vms[0]["_id"])  # stage 2: a random-season squad per virtual manager
        assert len(squad["squad"]["positions"]) == 11 and len(squad["players"]) == 18
        assert len({p["spid"] // 1000000 for p in squad["players"]}) > 3 and hc.vm_team(vms[0]["_id"]) is squad
        side = hc.vm_side(vms[0])  # each card at its own grade and level, in the match and the ratings
        assert [(p["grade"], p["levelinfo"]["level"]) for p in side["team"]["players"]] == \
            [(p["grade"], p["levelinfo"]["level"]) for p in squad["players"]]
        # his team colors and staff (user: "Virtual manager harus dapat bonus team color saat bertanding dan harus
        # mempunyai staff sesuai dengan level nya"): in his match side and his ratings (as my summary counts
        # mine), not in the pre-match bars (that list shows neither); one more role frame every 12 levels
        raised = colored = 0
        for vm in sorted(hc.virtual_managers(), key=lambda m: m["level"])[::97]:
            t, n, g = hc.vm_side(vm)["team"], min(7, 1 + vm["level"] // 12), min(10, 1 + vm["level"] // 12)
            assert sorted(s["position"] for s in t["staffs"]) == sorted(hc.VM_STAFF_ORDER[:n]), vm["level"]
            assert [r["staffid"] for r in t["staffroles"]] == [s["_id"] for s in t["staffs"]] and hc.vm_staff(vm) == hc.vm_staff(vm)
            assert all(abs(s["grade"] - g) <= 1 and s["stat"] and s["life"] > 0 for s in t["staffs"])
            assert t["teamcolors"] == hc.active_teamcolors(t)
            ovr = hc.vm_ovr(vm["_id"])
            assert ovr == {k: hc.team_summary(t, t["teamid"])["ovr"][k] for k in ("FW", "MF", "DF")}
            assert all(t["summary"]["ovr"][k] <= v for k, v in ovr.items())
            raised, colored = raised + (t["summary"]["ovr"] != ovr), colored + bool(t["teamcolors"])
        assert raised >= 5 and colored >= 5, (raised, colored)  # of 11
        # team colors count a player's former clubs as the client's clubhistory has them: his career
        # in club_history.json where it has him (Pirlo: Milan, Juventus, NYCFC...), else FO3's table
        kept_ch, hc.CLUB_HISTORY = hc.CLUB_HISTORY, os.path.join(tmp, "club_history.json")
        json.dump({"players": {"7763": [[47, 2001, 2011, 0], [999, 2018, -1, 0]]}}, open(hc.CLUB_HISTORY, "w"))
        hc.tc_static.cache_clear()
        assert hc.tc_static()[1][7763] == {47, 999} and hc.tc_static()[1].get(158023) is not None  # Messi: FO3's own
        hc.CLUB_HISTORY = kept_ch
        hc.tc_static.cache_clear()
        fr = body(hc.ENDPOINTS["/ladder/friends/info"]("", b""))
        assert len(fr) == hc.VM_FRIENDS and set(fr[0]["ovr"]) == {"FW", "MF", "DF"} and len(fr[0]["elo"]) == hc.LADDER_COUNT
        # squad themes (user: "tambah variasi dalam pemilihan skuad ... meta, klub tertentu, negara tertentu,
        # pemain tertentu, team color tertentu, stats tertentu"; "Klub dan negara top saja"): each one in
        # the crowd, a club / nation / color theme filling his XI with top clubs and nations only, a
        # star in his XI, every themed XI near his rating
        day, (cards, hist), seen = hc.vm_day(), hc.tc_static(), Counter()
        for vm in hc.virtual_managers()[::15]:
            _, rec = hc.vm_origin(vm["_id"], day)
            centre = 64 + (max(rec["elo"]) - hc.LADDER_START) * 18 // 780
            xi_slots = hc.style_slots(hc.vm_style(rec)) or hc.roster_team(rec["teamid"])["squad"]["positions"]
            kind, key = hc.vm_theme(vm["_id"], centre, tuple(hc.POS_ROLE[s["pos"]] for s in xi_slots), rec["teamid"])
            seen[kind] += 1
            xi = [p["spid"] for p in hc.vm_squad(vm["_id"], day)["squad"]["positions"]]
            # near his rating: about 1 in 100 themed XIs (a thin color or nation pool) misses by 5-7
            assert abs(sum(map(hc.card_ovr, xi)) / 11 - centre) <= 7, (vm["_id"], kind, key)
            fit = {"club": lambda s: cards[s][1] == key or key in hist.get(cards[s][0], ()),
                   "nation": lambda s: cards[s][2] == key, "color": lambda s: s in hc.tc_special_sets()[key][0],
                   "star": lambda s: cards[s][0] == key[0]}.get(kind)
            if kind in ("club", "nation", "color"):
                assert sum(map(fit, xi)) >= hc.VM_THEME_MIN - 2 and key in hc.vm_theme_keys()[kind], (vm["_id"], kind, key)
            if kind == "star":
                assert any(map(fit, xi)) and key[2] in (hc.VM_TOP_CLUBS if key[1] == "club" else hc.VM_TOP_NATIONS)
        assert set(seen) == {k for k, *_ in hc.VM_THEMES}, seen
        # playing styles: his style's tactic (radios 1|2) and formation; every style is someone's
        for vm in vms[:20]:
            side, name = hc.vm_side(vm), hc.vm_style(vm)
            gk = next(str(p.get("id") or p["spid"]) for p in side["squad"]["positions"] if p["pos"] == 0)
            assert gk not in side["squad"]["kickers"] and len(side["squad"]["kickers"]) == hc.KICKERS  # no GK corners
            assert side["tactics"] == [hc.style_tactic(name)] and side["tactics"][0]["def"]["defenderline"] in (1, 2)
            assert [p["pos"] for p in side["team"]["squad"]["positions"] if p["pos"] < hc.POS_SUB] == [s["pos"] for s in hc.style_slots(name)]
        assert {hc.vm_style(vm) for vm in vms} == set(hc.VM_STYLES)
        # my tactics: 1|2 radios; the in-match Reset (a GET) gets the tactic itself, DELETE resets
        tac = hc.ENDPOINTS["/game/my/tactic/1"]
        assert body(tac("", b""))["bus"]["positioning"] == 1 and body(tac("", b'{"_method": "DELETE"}'))["result"]
        # presence: the Messenger friends are the Rank friends, each online / busy / offline
        cl = body(hc.ENDPOINTS["/contact/list"]("", b""))
        assert [f["_id"] for f in cl["friends"]] == [f["_id"] for f in fr] and cl["newfriends"] == []
        assert {f["status"] for f in cl["friends"]} <= {"online", "busy", "offline"}
        assert [hc.vm_status({"hours": [10]}, h) for h in (9, 10, 11, 12)] == ["online", "busy", "online", "offline"]
        # the Messenger screen attaches once SystemMessageProxy is ready: onready feeds its notifications
        res = body(hc.ENDPOINTS["/coach/me/onready"]("", b'{"_method": "PUT"}'))["resources"]
        assert res[0]["uri"] == "/coach/me" and {"uri": "/inbox/my/notifications", "body": []} in res
        # his profile (the friend Info button): summary, 1on1 rank, squad; an unknown one is refused
        vid = fr[0]["_id"]
        assert body(hc.route(f"/coach/{vid}/summary")("", b""))["coach"]["name"] == fr[0]["name"]
        assert body(hc.route("/coach/vm9999/summary")("", b"")) == {"result": False, "reason": "invalid coach id"}
        assert body(hc.route(f"/ladder/{vid}/info")("", b""))["ladder_pvp_1v1"]["rank"] >= 1
        team = body(hc.route(f"/coach/{vid}")("path=team", b""))  # the squad tab: the team itself
        assert sum(p["pos"] < hc.POS_SUB for p in team["squad"]["positions"]) == 11
        coach = body(hc.route(f"/coach/{vid}")("", b""))  # its squad popup: a coach with teamsquadlist.preset
        assert coach["name"] == fr[0]["name"] and coach["team"]["squad"] == team["squad"] and coach["teamsquadlist"]["preset"]
        # issue #33, Manager > Peringkat manajer: my team value over 30 days (oldest first; {} kept its
        # spinner going) and the friends' team value ranking (an array; {} left it empty): the same
        # 20 managers and me, ranked by value, fluc = yesterday's rank - today's; no clubs here
        hist = body(hc.ENDPOINTS["/coach/me/teamvalue/history"]("", b""))["values"]
        cm = hc.coach_me(hc._state(), hc._default_slot(hc._state()))
        assert len(hist) == hc.TV_DAYS and hist[-1] == hc.first_team_value(cm["team"]) and all(isinstance(v, int) for v in hist)
        rows = body(hc.ENDPOINTS["/rank/teamvalue/friend"]("", b""))
        assert [r["rank"] for r in rows] == list(range(1, hc.VM_FRIENDS + 2)) and {r["_id"] for r in rows} == {f["_id"] for f in fr} | {cm["_id"]}
        assert [r["teamvalue"]["val"] for r in rows] == sorted((r["teamvalue"]["val"] for r in rows), reverse=True)
        assert all(set(r) == {"_id", "name", "level", "ovr", "teamvalue", "rank", "fluc"} and set(r["ovr"]) == {"FW", "MF", "DF"} for r in rows)
        assert sum(r["fluc"] for r in rows) == 0 and body(hc.ENDPOINTS["/rank/teamvalue/club"]("", b"")) == []
        assert cm["_id"] == cm["id"] and cm["hiderank"] is False
        hid = body(hc.ENDPOINTS["/coach/me/hiderank"]("", b'{"hiderank": true, "_method": "PUT"}'))
        assert hid["resources"][0]["body"]["hiderank"] is True
        hc.ENDPOINTS["/coach/me/hiderank"]("", b'{"hiderank": false, "_method": "PUT"}')
        assert body(hc.route(f"/coach/{cm['_id']}/summary")("", b""))["coach"]["name"] == cm["name"]  # my row's Info
        divs = json.load(open(os.path.join(hc.RESPONSES, "ladder_division_info.json")))
        assert [d["rating"] for d in divs] == sorted((d["rating"] for d in divs), reverse=True)  # highest first
        assert all(d["mingames"] > 0 for d in divs)  # the centre panel's "Match yg diperlukan x/<mingames>"
        rewards = hc.ladder_rewards()
        assert {f"DIVISION_{d['name']}" for d in divs} == {d["name"] for d in rewards["divs"]} and rewards["wins"]
        # initializeBestRecord reads a division's items[0] and [1] and walks wins until from > totalwins
        assert all(len(d["items"]) >= 2 for d in rewards["divs"])
        assert [w["from"] for w in rewards["wins"]] == sorted(w["from"] for w in rewards["wins"])
        # a club without a kit in the client crashed the game before the match loaded (Ansan
        # Greeners): no picker, Liga or virtual manager offers one
        if hc.kit_teams():
            assert not hc.has_kit(113595) and hc.has_kit(1476)
            assert all(hc.has_kit(t) for _, ls in hc.club_index() for _, ts in ls for t in ts)
            assert all(hc.has_kit(m["teamid"]) for m in hc.virtual_managers())
        # a Rank match: the PvP type, my user as the native session reads it, the opponent left
        # out of that session (nameless, unready) until the squad stage gives him back
        pushed = []  # ladder_found / ladder_squad push to the game over messenger.py: never to a running one
        hc.messenger_push = lambda uri, b: pushed.append((uri, b))
        st = hc._state(); hc.ladder_match(st, {"roomid": 1, "type": "pvp_ladder_mgr", "side": {"right": {}}}); hc._save(st)
        m = st["match"]; user, him = (m["side"][s]["players"][0] for s in ("left", "right"))
        assert m["type"] == "Ladder_MGR_PvP_1v1" and m["id"].isdigit() and user["ip"] and len(user["summary"]["ladderelo"]) == 5
        assert "name" not in him and not m["side"]["right"]["ready"] and m["side"]["left"]["club"] == {"clubname": ""}
        assert all(p["rankedcards"] is None for p in m["side"]["left"]["team"]["players"])  # "Pilihan terbaik"
        assert m["gamesettings"]["drawMode"] == 1  # a level score: extra time, then penalties
        hc.ladder_squad(m["id"])
        m = hc._state()["match"]
        assert m["stage"] == "squad" and m["side"]["right"]["players"][0]["name"] and m["side"]["right"]["ready"]
        assert pushed == [(f"/match/{m['id']}", hc.push_match(m))]
        # squad ready -> setting with my ready cleared (the client's sendReady skips a ready side), then playing
        for stage, nxt in (("squad", "setting"), ("setting", "playing")):
            ready = json.dumps({"stage": stage, "ready": True}).encode()
            mb = body(hc.route(f"/match/{m['id']}/side/left/ready")("", ready))["resources"][0]["body"]
            assert mb["stage"] == nxt and mb["side"]["left"]["ready"] == (nxt == "playing")
        # the Rank screen's centre panel: "no record" until a Rank game, then the best rating and its mode
        assert "ladder_total" not in body(hc.ENDPOINTS["/ladder/my/info"]("", b""))
        # the result as matchStats sends it, his side first: I win 2-0
        vm, mine = hc.vm_by_id(hc._state()["ladder_pending"]["opponent"]), m["side"]["left"]["team"]["teamid"]
        assert vm["teamid"] != mine
        stats = {"home": {"id": vm["teamid"], "score": 0}, "away": {"id": mine, "score": 2}}
        body(hc.route(f"/match/{m['id']}/end")("", json.dumps(stats).encode()))
        st = hc._state(); lad = hc.my_ladder(st)
        assert lad["wins"][3] == lad["games"][3] == 1 and lad["elo"][3] > hc.LADDER_START and "ladder_pending" not in st
        tot = body(hc.ENDPOINTS["/ladder/my/info"]("", b""))["ladder_total"]
        assert tot == {"highelo": lad["high"][3], "hightype": "Ladder_MGR_PvP_1v1", "playedgames": 1, "totalwins": 1,
                       "midrewardable": False}  # the first weekly step is at 5 wins
        # "Poin peringkat" / record screen: setSeasonalRecord finds the season by season.sid === String(sid)
        sid = body(hc.ENDPOINTS["/ladder/season/info"]("", b""))["sid"]
        season = hc.coach_me(st, 0)["statistics"]["ladder"]["seasons"][-1]  # this season last
        assert season["sid"] == str(sid) and season["elos"][3] == [{"elo": tot["highelo"], "dateymd": time.strftime("%Y%m%d", time.gmtime())}]
        st.pop("match"); hc._save(st)
        # the room flow: search, cancel (its timer firing late must not match), search again, found
        timers, real_timer = [], hc.threading.Timer

        class Timer:  # threading.Timer stand-in: the test fires the timers itself
            def __init__(self, t, fn, args=()):
                self.call = (fn, args)

            def start(self):
                timers.append(self.call)
        hc.threading.Timer = Timer
        try:
            room = body(hc.ENDPOINTS["/room"]("", json.dumps({"type": "pvp_ladder_1v1", "slotsize": 1}).encode()))["resources"][0]["body"]
            mm = hc.route(f"/room/{room['roomid']}/matchmaking")
            mm("", b"{}"); mm("", json.dumps({"_method": "DELETE"}).encode())
            fn, args = timers.pop(); fn(*args)
            assert hc._state()["room"]["stage"] == "room" and "match" not in hc._state()
            mm("", b"{}"); fn, args = timers.pop(); fn(*args)
        finally:
            hc.threading.Timer = real_timer
        st = hc._state(); uri, sent = pushed[-1]
        assert st["room"]["stage"] == "matched" and st["ladder_pending"]["index"] == 0 and uri == f"/room/{room['roomid']}"
        assert len(sent["matchmaking"]["resources"][0]["body"]["side"]["left"]["team"]["players"]) <= 18
        # a big club still fits a push (messenger.py frames carry at most 64 KB)
        big = copy.deepcopy(st["match"]); team = big["side"]["left"]["team"]
        team["players"] += [dict(team["players"][0], id=f"x{n}", _id=f"x{n}") for n in range(200)]
        assert len(json.dumps(hc.push_match(big))) < 60000 < len(json.dumps(big))
        # 5 placement wins: the placement popup goes from the start rating to the rating after them
        vm = hc.vm_by_id(st["ladder_pending"]["opponent"])
        for _ in range(hc.LADDER_DEPLOY):
            hc.ladder_record(st, 0, vm, 1, 0)
        hc._save(st)
        my = body(hc.ENDPOINTS["/ladder/my/info"]("", b""))
        fr = my["ladder_pvp_1v1"]["firstround"]
        assert not fr["ongoing"] and fr["wins"] == [True] * 5 and fr["beforeelo"] == hc.LADDER_START < fr["afterelo"]
        assert my["ladder_total"]["totalwins"] == my["ladder_total"]["playedgames"] == 6
        hr = hc.coach_me(st, 0)["statistics"]["ladder"]["highrecords"]  # Info Manager's Rank record
        assert hr["count"] == 6 and hr["winningstreak"][0] == 5 and hr["winningstreak"][3] == 1 and hr["rank"][0] >= 1
        assert my["sid"] == str(hc.ladder_season()[0])  # the Summary tab shows this season's Rank rates for it
        # match history: the record screen's last 3 (streak, rating after) and Info Manager's list
        season = hc.coach_me(st, 0)["statistics"]["ladder"]["seasons"][-1]
        rm = season["recentmatches"][0]
        assert len(rm) == 3 and [r["streak"] for r in rm] == [3, 4, 5] and rm[-1]["elo"] == hc.my_ladder(st)["elo"][0]
        assert rm[-1]["goals"] == 1 and rm[-1]["elodiff"] > 0 and season["recentmatches"][3][-1]["streak"] == 1  # the manager win
        mp = hc.coach_me(st, 0)["multiplay"]
        assert mp[-1]["opponent"]["coaches"][0]["coachid"] == vm["_id"] and mp[-1]["type"] == "Ladder_PvP_1v1"
        assert mp[0]["type"] == "Ladder_MGR_PvP_1v1" and (mp[0]["team"]["score"], mp[0]["opponent"]["score"]) == (2, 0)
        # a new season: the old one stays as the last season and in the best ratings; the week's wins stay
        lad = hc.my_ladder(st); lad["sid"] -= 1; prev = copy.deepcopy(lad)
        hc.my_ladder(st)
        stats = hc.coach_me(st, 0)["statistics"]["ladder"]
        assert [s["sid"] for s in stats["seasons"]] == [str(prev["sid"]), str(sid)] and not any(hc.my_ladder(st)["games"])
        # no season-end reward yet: an unrewarded past season makes the Rank front POST /ladder/reward and
        # open the reward popup on the default reply (TypeError) at every visit
        assert stats["seasons"][0]["rewarded"] is True and "rewarded" not in stats["seasons"][1]
        assert stats["highrecords"]["elo"][0] == prev["high"][0] > hc.LADDER_START
        assert hc.my_coach(st)["ladderweek"]["wins"] == 6
        # issue #30 part 3: his chat (pushes recorded, timers fired here; odds 1, no gap between lines)
        hc.VM_CHAT_FILE = os.path.join(tmp, "vm_chat.json")
        hc.VM_CHAT_ODDS, hc.VM_CHAT_GAP = dict.fromkeys(hc.VM_CHAT_ODDS, 1), 0
        timers.clear()  # the room flow's ladder_squad timer: match ids are per second, it could hit this match
        hc.threading.Timer = Timer
        try:
            st = hc._state(); hc.ladder_match(st, {"roomid": 1, "type": "pvp_ladder_mgr", "side": {"right": {}}}); hc._save(st)
            mid, vm = st["match"]["id"], hc.vm_by_id(st["ladder_pending"]["opponent"])
            chat = json.load(open(os.path.join(hc.RESPONSES, "vm_chat.json"), encoding="utf-8"))
            mine = lambda trig, lang="id": chat[lang][trig][hc.vm_personality(vm)]
            # the library (issue #30: about 10x the lines): 20 per trigger and personality, none twice
            # in a language, short, in the chat font (no emoji, no markup), and nothing the overlay
            # masks: it starts any line holding a word of FORBIDDENWORDS_CHAT_<locale> (checked when
            # the client is installed; "biasa" holds "asa", "santai" "tai", "pass" "ass")
            masked = set()
            for big, tag in (("eng.big", "ENG"), ("ind.big", "IND")):
                masked |= {w.lower() for w in hc.client_json(os.path.join(hc.GAME, "data", "ui", "localization", big),
                                                             f"data/ui/localization/FORBIDDENWORDS_CHAT_{tag}.json")}
            assert set(chat) == set(hc.VM_CHAT_LANGS)
            for lang, triggers in chat.items():
                lines = [s for cells in triggers.values() for v in cells.values() for s in v]
                assert set(triggers) == {"hello", "goal", "conceded", "miss", "save", "win", "loss", "draw", "reply"}
                assert all(set(cells) == set(hc.VM_PERSONALITIES) and all(len(v) == 20 for v in cells.values())
                           for cells in triggers.values())
                assert len({s.lower() for s in lines}) == len(lines) == 900
                assert all(3 <= len(s) <= 60 and not set(s) & set("<>&") and max(map(ord, s)) < 0x1F00 for s in lines)
                assert not [s for s in lines if any(w in s.lower() for w in masked)], lang
            # the club chat's library (issue #36), the same rules: 10 per trigger and personality
            club_chat = json.load(open(os.path.join(hc.RESPONSES, "vm_club_chat.json"), encoding="utf-8"))
            assert set(club_chat) == set(hc.VM_CHAT_LANGS)
            for lang, triggers in club_chat.items():
                lines = [s for cells in triggers.values() for v in cells.values() for s in v]
                assert set(triggers) == {"hello", "reply", "thanks", "welcome", "joined", "idle", "win", "loss", "draw",
                                         "description", "news", "board"}
                assert all(set(cells) == set(hc.VM_PERSONALITIES) and all(len(v) == 10 for v in cells.values())
                           for cells in triggers.values())
                assert len({s.lower() for s in lines}) == len(lines) == 600
                assert all(3 <= len(s) <= 60 and not set(s) & set("<>&") and max(map(ord, s)) < 0x1F00 for s in lines)
                assert not [s for s in lines if any(w in s.lower() for w in masked)], lang

            def after(act):  # what goes out for act, its timers fired
                n0 = len(pushed); act()
                while timers:
                    fn, args = timers.pop(0); fn(*args)
                return [(u, b[0]) for u, b in pushed[n0:]]

            def ev(n, **e):
                return after(lambda: body(hc.route(f"/match/{mid}/gameplay/event/{n}")("", json.dumps(e).encode())))

            def said(out, trig):
                return [(u, m["coachname"], m["myside"], m["body"] in mine(trig)) for u, m in out] == [
                    (f"/match/{mid}/messages/latest", vm["name"], False, True)]
            assert said(ev(0, type="STARTHALF", halftype="FIRST_HALF"), "hello") and not ev(1, type="STARTHALF", halftype="SECOND_HALF")
            hc.route(f"/match/{mid}/gameplay/event/2")("", json.dumps({"type": "SHOT", "teamid": 0}).encode())  # its timer waits...
            assert said(ev(3, type="GOAL_TYPE_NORMAL", teamid=0), "conceded")  # ...and fires after the goal: no "save"
            assert said(ev(4, type="SHOT", teamid=1), "miss") and said(ev(5, type="SHOT", teamid=0), "save")
            assert said(ev(6, type="GOAL_TYPE_NORMAL", teamid=1), "goal") and not ev(7, type="FOUL_NO_BOOKING", teamid=0)
            assert said(ev(8, type="GOAL_TYPE_OWN_GOAL", teamid=1), "conceded")  # into his own net: my goal
            assert not after(lambda: hc.route("/match/123/gameplay/event/0")("", json.dumps({"type": "GOAL_TYPE_NORMAL", "teamid": 1}).encode()))
            hc.VM_CHAT_GAP = 60  # within the gap only goals get a line
            assert not ev(9, type="SHOT", teamid=1) and said(ev(10, type="GOAL_TYPE_NORMAL", teamid=1), "goal")
            hc.VM_CHAT_GAP = 0
            post = lambda m: after(lambda: body(hc.route(f"/match/{mid}/messages")("", json.dumps(m).encode())))
            echo, answer = post({"message": "halo", "target": ""})
            assert echo[1] == {"type": "chat", "target": "", "coachname": "Tester", "body": "halo", "myside": True}
            assert answer[1]["body"] in mine("reply") and not answer[1]["myside"]
            assert len(post({"message": "tim", "target": "team"})) == 1 and not post({"message": "<P>x</P>", "type": "chat"})
            # the result: he wins 1-0; his word on it comes back as a banner on the Rank screen
            stats = {"home": {"id": st["match"]["side"]["left"]["team"]["teamid"], "score": 0}, "away": {"id": vm["teamid"], "score": 1}}
            body(hc.route(f"/match/{mid}/end")("", json.dumps(stats).encode()))
            (uri, text), = after(lambda: hc.route("/room/1/back")("", b"{}"))
            assert uri == "/alert/vm/latest" and any(text == f"<FONT COLOR='#ffcc00'>{vm['name']}</FONT>: {line}" for line in mine("win"))
            assert not after(lambda: hc.route("/room/1/back")("", b"{}"))  # said once
            hc.VM_CHAT_AFTER["last"] = {"vm": vm["_id"], "outcome": "win"}
            st = hc._state(); hc.ladder_match(st, {"roomid": 1, "type": "pvp_ladder_mgr", "side": {"right": {}}}); hc._save(st)
            assert not hc.VM_CHAT_AFTER  # a new Rank match drops a result that was never said
            with open(hc.VM_CHAT_FILE, "w", encoding="utf-8") as f:
                json.dump({"enabled": False, "lang": "vi"}, f)
            hc.VM_CHAT_AFTER["last"] = {"vm": vm["_id"], "outcome": "win"}
            assert not after(lambda: hc.route("/room/1/back")("", b"{}")) and not after(lambda: hc.vm_say(mid, vm, "goal", 0))
            assert hc.vm_chat_line(vm, "goal") in mine("goal", "vi")
            with open(hc.VM_CHAT_FILE, "w", encoding="utf-8") as f:
                f.write("[1, 2]")  # valid JSON, not an object
            assert hc.vm_chat_cfg() == hc.VM_CHAT_DEFAULT
        finally:
            hc.threading.Timer = real_timer
        os.remove(hc.VM_CHAT_FILE)
        for k in ("match", "ladder_pending", "room"):
            st.pop(k, None)
        hc._save(st)
        city = body(hc.route("/static/info/authentic/team/licensed/10")("", b""))
        assert city["teamid"] == 10 == city["summary"]["id"] and len(city["team"]["squad"]["positions"]) == 11
        assert city["team"]["captain"] in {p["id"] for p in city["team"]["players"]}
        assert hc.roster(243)["summary"]["teamgrade"] == 5.0 and hc.roster(243)["summary"]["id"] == 243
        # national teams = their players' nationalteamid (most are at clubs): Italy (1343) is in the
        # International league with Italians only (issue #24); that nation stays last in the picker
        intl = clubs[str(hc.gen_teams.NATIONAL_LEAGUE)]
        assert 1343 in intl and len(intl) >= 40 and -1 not in intl
        assert str(hc.gen_teams.NATIONAL_LEAGUE) in {str(lg) for lg in leagues[str(nations[-1])]}
        ita = hc.roster(1343)
        assert ita["summary"]["league"]["id"] == 78 and ita["summary"]["nation"]["id"] == 27
        with hc.closing(hc._db()) as c:
            ita_nat = {r[0] for r in c.execute(f"SELECT nationality FROM players WHERE spid IN ({','.join(str(p['spid']) for p in ita['team']['players'])})")}
        assert ita_nat == {27} and len(ita["team"]["players"]) >= 18
        assert not hc.roster(-1)["team"]["players"] and not hc.roster(0)["team"]["players"]  # nationalteamid -1 = none

        assert "league" not in body(hc.ep_l2_me("", b""))  # -> creation screen
        offer = body(hc.route("/league2/sponsor/12")("", b"{}"))
        assert offer["result"] and len(offer["sponsors"]) == 3
        created = body(hc.ep_l2_calendar("", json.dumps({"difficulty": 12, "sponsor": "1"}).encode()))
        league = created["resources"][0]["body"]["league"]
        n = len(league["teams"])  # every club of my league with a full squad (issue #40: "not 10 team")
        assert league["teams"][0] == me and n == len(set(league["teams"])) > hc.LEAGUE_SIZE and offer["maxround"] == 2 * (n - 1) - 1
        assert league["maxround"] == 2 * (n - 1) - 1

        # Simulasi needs a League Simulation coupon (item 2001, type LEAGUE_SIMULATION: the calendar
        # tab's Simulasi button counts them; user: "item untuk League Simulation, jadi tanpa main
        # league langsung hasil"), one per simulated match
        coupons = lambda: sum(i["remains"] for i in hc.coach_me(hc._state(), hc._default_slot(hc._state()))["items"]
                              if i["staticdata"]["type"] == "LEAGUE_SIMULATION")
        h1, a1 = next(p for p in hc.league_round(league["teams"], 1) if me in p)
        req1 = json.dumps({"matchtype": "league", "playtype": "nrm", "round": 1, "home": h1, "away": a1}).encode()
        assert coupons() == 0 and not body(hc.ep_l2_match("", req1, sim=True))["result"]  # none: refused
        st = hc._state(); hc.inventory_add(hc.my_coach(st), hc.SIM_COUPON, (league["maxround"] + 1) // 2); hc._save(st)
        for rnd in range(league["maxround"] + 1):
            home, away = next(p for p in hc.league_round(league["teams"], rnd) if me in p)
            req = json.dumps({"matchtype": "league", "playtype": "nrm", "round": rnd, "home": home, "away": away}).encode()
            if rnd % 2:  # simulate
                n0 = coupons()
                r = body(hc.ep_l2_match("", req, sim=True))
                assert r["result"] and coupons() == n0 - 1 and any(x["uri"] == "/coach/me" for x in r["resources"])
                # the simulation screen plays eventlist (LeagueCupController: ON_SIMULATED with
                # body.eventlist); its GOAL_EVENTs make the score it shows, so they match the result
                ev, g = r["eventlist"], r["score"]
                mine_g, opp_g = (g["home"], g["away"]) if home == me else (g["away"], g["home"])
                assert [sum(e["event"] == "GOAL_EVENT" and e["team"] == t for e in ev) for t in (0, 1)] == [mine_g, opp_g], (ev, g)
                assert [e["time"] for e in ev] == sorted(e["time"] for e in ev) and all(1 <= e["time"] <= 90 for e in ev)
                sim = r["resources"][0]["body"]["side"]
                xi = [{int(p["spid"]) for p in sim[s]["team"]["squad"]["positions"] if p["pos"] < hc.POS_SUB} for s in ("left", "right")]
                assert all(e["player"] in xi[e["team"]] for e in ev), ev
                # the result screen after it (PostMatchResultDefaultScene.onResultReceived) reads
                # match.result like the report a played match posts at /end; it was blank
                res = r["resources"][0]["body"]["result"]
                side = "home" if home == me else "away"
                assert res["myTeam"] == side and res["opponentTeam"] == ("away" if side == "home" else "home")
                assert (res["home"]["score"], res["away"]["score"]) == (g["home"], g["away"]) and res["home"]["id"] == home
                for s, t in ((res["myTeam"], 0), (res["opponentTeam"], 1)):
                    assert len(res[s]["players"]) == 11 and sum(p["goals"] for p in res[s]["players"]) == res[s]["score"]
                    # the card's spid: the labels show its season (issue #26)
                    assert {p["spid"] for p in res[s]["players"]} == xi[t]
                    # each at his squad position on the result screen's pitch, not all at the goal (issue #26)
                    slots = [p["pos"] for p in sim[("left", "right")[t]]["team"]["squad"]["positions"] if p["pos"] < hc.POS_SUB]
                    assert [p["role"] for p in res[s]["players"]] == slots and slots.count(0) == 1
                assert res["motm"]["team"] in ("home", "away") and res["home"]["possession"]["total"] + res["away"]["possession"]["total"] == 100
                # then the reward screen (PostMatchRewardDefaultScene) reads match.reward like after a
                # played match: player EXP, manager EXP, EP (it was empty and the flow stopped there)
                rw = r["resources"][0]["body"]["reward"]
                assert rw["coach"]["reward"]["total"] > 0 and rw["money"]["reward"]["total"] > 0 and len(rw["players"]) == 11
                assert "pending" not in hc._state()["league2"]  # the round is played: no /end can play it again
            else:  # play: create, ready, end 2-1 for the home side
                m = body(hc.ep_l2_match("", req))["resources"][0]["body"]
                assert m["type"] == "League_2_league" and m["stage"] == "created"
                assert m["leagueparam"]["matchtype"] == "league" and m["side"]["left"]["ready"] is False
                for stage, nxt in (("created", "squad"), ("squad", "setting")):
                    pr = body(hc.route(f"/match/{m['id']}/player/ready")("", json.dumps({"ready": True, "stage": stage}).encode()))
                    assert pr["resources"][0]["body"]["stage"] == nxt, pr
                # pre-match squad screen (issue #16): it needs the formation list and each player's
                # stamina; a sub swapped in comes back in the match resource and stays in my club
                left = m["side"]["left"]["team"]
                assert left["formations"] and all(p["stamina"] == 1 for p in left["players"])
                sq = json.loads(json.dumps(left["squad"]))
                a = next(p for p in sq["positions"] if 0 < p["pos"] < hc.POS_SUB)
                b = next(p for p in sq["positions"] if p["pos"] == hc.POS_SUB)
                a["id"], b["id"] = b["id"], a["id"]  # b now holds the benched starter
                put = lambda s, q: body(hc.route(f"/match/{m['id']}/side/{s}/squad")("", json.dumps(
                    {"squad": q, "kickers": left["kickers"], "captain": left["captain"], "_method": "PUT"}).encode()))
                r = put("left", sq)
                xi = lambda ps: {p["id"] for p in ps if p["pos"] < hc.POS_SUB}
                assert r["result"] and xi(r["resources"][0]["body"]["side"]["left"]["team"]["squad"]["positions"]) == xi(sq["positions"])
                assert b["id"] not in xi(hc.coach_me(hc._state(), 0)["team"]["squad"]["positions"])
                assert put("right", sq)["result"] is False  # the AI side
                assert put("left", dict(sq, positions=sq["positions"][:5]))["result"] is False  # no XI: kept
                assert xi(hc._state()["match"]["side"]["left"]["team"]["squad"]["positions"]) == xi(sq["positions"])
                rd = body(hc.route(f"/match/{m['id']}/side/left/ready")("", b'{"ready": true, "stage": "setting"}'))
                assert rd["resources"][0]["body"]["stage"] == "playing"
                stats = {"home": {"id": home, "score": 2}, "away": {"id": away, "score": 1}}
                stats.update(myTeam="home" if home == me else "away", opponentTeam="away" if home == me else "home")
                cm = hc.coach_me(hc._state(), hc._default_slot(hc._state()))
                ep0, lv0 = cm["money"]["ep"], cm["levelinfo"]
                xi = [p for p in cm["team"]["squad"]["positions"] if p["pos"] < hc.POS_SUB]
                # in-match player ids are the card's player id (spid % 1e6); the game sends role 0
                # for everyone (issue #26), the server takes it from the squad slot
                stats[stats["myTeam"]]["players"] = [{"spid": int(p["spid"]) % 1000000, "goals": int(i == 0), "rating": 7,
                                                      "passes": [3, 4], "tackles": [1, 2], "role": 0} for i, p in enumerate(xi)]
                stats[stats["myTeam"]]["reserved"] = [{"id": "1", "rating": 0}]
                stats["motm"] = {"team": stats["myTeam"], "spid": int(xi[1]["spid"]) % 1000000, "rating": 9}
                items0 = {i["itemid"]: i["remains"] for i in hc.my_coach(hc._state()).get("items", [])}
                r = body(hc.route(f"/match/{m['id']}/end")("", json.dumps(stats).encode()))
                body(hc.route(f"/match/{m['id']}/end")("", json.dumps(stats).encode()))  # a resend credits nothing
                done = r["resources"][0]["body"]["result"]
                roles = [p["role"] for p in done[stats["myTeam"]]["players"]]
                assert roles == [p["pos"] for p in xi], roles
                # the game reports player ids; the result carries the cards' spids (season labels)
                assert [p["spid"] for p in done[stats["myTeam"]]["players"]] == [int(p["spid"]) for p in xi]
                assert done["motm"]["spid"] == int(xi[1]["spid"])
                # reward card (issue #26): 3 cards, the first is mine and credited once
                rw0 = r["resources"][0]["body"]["reward"]
                cards = rw0["extra"]["availables"]
                assert len(cards) == 3 and rw0["extra"]["selected"] == rw0["selected"] == {"normal": [0]}
                assert all(c["staticdata"]["unit"] in ("EP", "COUNT") and c["count"] > 0 for c in cards)
                card_ep = cards[0]["count"] if cards[0]["itemid"] == hc.EP_ITEM else 0
                assert hc.coach_me(hc._state(), hc._default_slot(hc._state()))["money"]["ep"] == ep0 + (2000 if home == me else 500) + card_ep
                if not card_ep:
                    items = {i["itemid"]: i["remains"] for i in hc.my_coach(hc._state())["items"]}
                    assert items[cards[0]["itemid"]] == items0.get(cards[0]["itemid"], 0) + 1
                mine = next(x for x in r["leagueparam"]["leaguematches"] if me in (x["home"], x["away"]))
                assert mine["goals"] == {"home": 2, "away": 1}, mine
                got = body(hc.route(f"/match/{m['id']}")("", b""))
                assert got["result"]["home"]["score"] == 2 and got["stage"] == "closed"
                assert got["reward"]["money"]["reward"]["total"] == (2000 if home == me else 500)
                # exp: coach by result, each of my 11 players by result (+20 for a goal); kept
                rw, cexp = got["reward"], hc.MATCH_REWARD_DEFAULT["result"]["win" if home == me else "loss"]["coach"]
                assert rw["coach"]["prev"] == lv0 and rw["coach"]["reward"]["total"] == cexp
                assert hc.coach_me(hc._state(), 0)["levelinfo"] == rw["coach"]["current"]
                assert len(rw["players"]) == 11 and rw["players"][0]["reward"]["total"] == rw["players"][1]["reward"]["total"] + 20
                shown = {p["id"]: p["levelinfo"] for p in hc.coach_me(hc._state(), 0)["team"]["players"]}
                assert all(shown[x["id"]] == x["current"] for x in rw["players"])
                assert all((x["current"]["level"], x["current"]["exp"]) > (x["prev"]["level"], x["prev"]["exp"]) for x in rw["players"])
            assert r["leagueparam"]["round"] == rnd and len(r["leagueparam"]["leaguematches"]) == n // 2

        lg = body(hc.ep_l2_me("", b""))
        assert lg["laststatus"] == f"league:{league['maxround']}"
        s = lg["league"]["statistics"]
        assert sorted(v["rank"] for v in s.values()) == list(range(1, n + 1))
        assert all(v["wins"] + v["draws"] + v["losses"] == 2 * (n - 1) for v in s.values())
        assert sum(v["goals"] for v in s.values()) == sum(v["goalsconceded"] for v in s.values())

        # Rekor liga (issue #19): Rekorku = my players' records of the played rounds; Rekor gabungan
        # = best XI by line + stat leaders from them, and nothing else (the page joins every key)
        played = (league["maxround"] + 2) // 2
        recs = body(hc.ENDPOINTS["/league2/history/players/league"]("", b""))
        assert sum(r["matches"] for r in recs) == 11 * played and sum(r["goals"] for r in recs) == played
        assert all(r["ratings"] == 7 * r["matches"] and r["passes"] * 4 == r["passtries"] * 3 for r in recs)
        mine_ids = {p["id"] for p in hc.coach_me(hc._state(), 0)["team"]["players"]}
        assert {r["id"] for r in recs} <= mine_ids
        lead = body(hc.ENDPOINTS["/league2/leader/league"]("", b""))
        assert set(lead) == {"GK", "DF", "MF", "FW", "ratings", "goals", "assists", "tackles", "passes", "saves"}
        assert sum(len(lead[k]) for k in hc.LINES) == 11 and len(lead["GK"]) == 1
        assert all(e["value"] == 7 and e["teamid"] == me for k in hc.LINES for e in lead[k])
        assert lead["passes"][0]["value"] == 0.75 and not lead["assists"] and not lead["saves"]
        assert len(lead["goals"]) <= hc.STAT_LEADERS and lead["goals"][0]["value"] >= 1
        assert body(hc.ENDPOINTS["/league2/history/players/cup"]("", b"")) == []
        # the standings tab reads league2difficulty[level].groupId -> league2promote (it threw
        # without them: empty table); season end promotes / demotes by the same table
        diff = json.load(open(os.path.join(hc.RESPONSES, "static_db_LEAGUE2DIFFICULTY.json")))
        assert all(str(diff["league2difficulty"][str(d)]["groupId"]) in diff["league2promote"] for d in range(10, 25))
        assert hc.league_promote(12) == {"promote_rank": 3, "demote_rank": 0}
        assert hc.league_promote(19)["promote_rank"] == 0 and hc.league_promote(24) == {"promote_rank": 0, "demote_rank": 3}

        ach = body(hc.ep_l2_achievement("", b"{}"))["missions"][0]
        assert ach["mid"] == hc.SPONSORS["1"] and ach["progress"][0] == s[str(me)]["rank"], ach
        assert ach["achievement"] == (s[str(me)]["rank"] <= hc.MISSION_TARGET[ach["mid"]])

        end = body(hc.ep_l2_end("", b"{}"))
        assert end["promotion"]["current"] == 12 and end["promotion"]["next"] == (13 if s[str(me)]["rank"] <= 3 else 12)
        assert sum(len(end["leaderRewards"][k]) for k in ("FW", "MF", "DF", "GK")) == 11
        assert all(len(end["leaderRewards"][k]) == 1 for k in ("ratings", "goals", "assists", "tackles", "passes", "saves"))
        assert end["rewards"]["money"]["reward"]["total"] == sum(end["rewards"]["money"]["reward"]["detail"].values())
        after = body(hc.ep_l2_me("", b""))
        assert after["sponsoroffice"]["season"]["status"] == "finished" and "league" in after
        assert after["difficultyselect"] == end["promotion"]["next"]
        # issue #33, Info Manager's record tab: my league matches (played or simulated) and goals, the
        # title and the level reached (it shows noramlbest - 10), once a season
        rec = hc.coach_me(hc._state(), 0)["statistics"]["league"]
        mine = [x for x in after["league"]["matchrecords"] if me in (x["home"], x["away"])]
        assert rec["wins"] + rec["draws"] + rec["losses"] == len(mine) == 2 * (n - 1)
        assert rec["goals"] == sum(x["goals"]["home" if x["home"] == me else "away"] for x in mine)
        assert rec["normalwins"] == (s[str(me)]["rank"] == 1) and rec["noramlbest"] == max(12, end["promotion"]["next"]) + 1
        body(hc.ep_l2_end("", b"{}"))  # the closing day again: not counted twice
        assert hc.coach_me(hc._state(), 0)["statistics"]["league"]["normalwins"] == rec["normalwins"]
        body(hc.ep_l2_finish("", b"{}"))  # give up / reset
        assert "league" not in body(hc.ep_l2_me("", b""))
        # --- cup: groups of 4 then an 8-club knock-out; I win every match I play
        cup = body(hc.ep_cup_new("", b'{"metaid": 1, "difficulty": 2}'))["resources"][0]["body"]
        cid = cup["_id"]
        assert len(cup["teams"]) == 16 and cup["phases"][0]["system"] == "round-robin"
        # issue #36: the cup screens draw trophies/t<trophy>.png and skins/s<skin>.png (were "undefined")
        assert (cup["trophy"], cup["skin"]) == ("66", "1") and body(hc.route(f"/cup/{cid}")("", b""))["trophy"] == "66"
        played = 0
        for _ in range(10):
            g, leg = hc.cup_my_pair(hc._state()["cups"][cid])
            if leg is not None and "score" not in leg:
                m = body(hc.route(f"/cup/{cid}/match")("", b"{}"))["resources"][0]["body"]
                assert m["type"] == "Cup_PvE"
                # kits: native reads teamid = id >> 5, kit type = id & 31; the game setting
                # screen's kit stepper (side 0 = left) comes back in the match resource
                left = m["side"]["left"]["team"]
                assert left["kit"]["availables"]["away"]["id"] == left["teamid"] << 5 | 1
                put = lambda kit, side: body(hc.route(f"/match/{m['id']}/gamesettings")("", json.dumps({"kit": kit, "side": side}).encode()))
                assert put("away", 0)["resources"][0]["body"]["gamesettings"]["kit"][0] == "away"
                assert put("home", 0)["resources"][0]["body"]["gamesettings"]["kit"][0] == "home"
                mine_home = g["teams"][leg["homeidx"]]["coach"] is not None
                stats = {"home": {"id": m["side"]["left" if mine_home else "right"]["team"]["teamid"], "score": 3 if mine_home else 0},
                         "away": {"id": m["side"]["right" if mine_home else "left"]["team"]["teamid"], "score": 0 if mine_home else 3}}
                body(hc.route(f"/match/{m['id']}/end")("", json.dumps(stats).encode()))
                played += 1
            cup = body(hc.route(f"/cup/{cid}/next")("", b"{}"))["resources"][0]["body"]
            if cup.get("finished") or cup["coaches"][0]["state"] != "playing":
                break
        assert played == 6 and cup.get("finished") and cup["coaches"][0]["state"] == "won", (played, cup["coaches"])
        assert len(cup["phases"]) == 2 and all("score" in p["legs"][0] for r in cup["phases"][1]["groups"][0]["rounds"] for p in r["pairs"])
        assert {"metaid": 1, "cupid": cid} in hc.coach_me(hc._state(), hc._default_slot(hc._state()))["cups"]  # until reward taken
        ep = lambda: hc.coach_me(hc._state(), hc._default_slot(hc._state()))["money"]["ep"]
        ep0 = ep()
        lv = body(hc.route(f"/cup/{cid}/leave")("", b"{}"))
        assert lv["rewards"] == [{"itemid": 1, "count": 60000}], lv
        assert ep() == ep0 + 60000  # cup prize credited
        assert cid not in hc._state()["cups"]
        cr = hc.coach_me(hc._state(), 0)["statistics"]["cup"]  # Info Manager: cup matches, won (by AI level), prize
        assert (cr["wins"], cr["draws"], cr["losses"]) == (6, 0, 0) and cr["cupwins"] == [0, 0, 1, 0, 0] and cr["highrecords"]["prize"] == 60000
        rec = body(hc.ENDPOINTS["/cup/record"]("", b""))["resources"][0]["body"]
        assert (rec["totalchallenges"], rec["totalwins"], rec["totalprize"]) == (1, 1, 60000)
        # --- market: top list, search, buy (EP checked, a new card instance in my players)
        top = body(hc.route("/transfer/top/50")("", b""))
        assert len(top) == 50 and top[0]["price"] >= top[-1]["price"] and top[0]["expandme"] == "player"
        assert all(o["spid"] for o in body(hc.route("/transfer/top/50/position/GK")("", b"")))
        assert body(hc.route("/transfer/search/158023")("", b"")) == []  # season-0 engine card: not tradeable
        buy = lambda o: body(hc.route(f"/transfer/purchase/{o['spid']}/{o['grade']}/{o['price']}/purchase")("", b"{}"))
        team = lambda: hc._state()["teams"][str(hc._default_slot(hc._state()))]
        copies = lambda spid: [p for p in team()["players"] if p["spid"] == spid]
        star = next((o for o in top if o["price"] > ep()), None)
        assert star is None or buy(star)["reason"] == "NOT_ENOUGH_EP"
        with hc.closing(hc._db()) as c:
            spid = next(r[0] for r in c.execute("SELECT spid FROM players WHERE overallrating BETWEEN 70 AND 75 ORDER BY spid")
                        if hc.on_market(r[0]) and not copies(r[0]) and hc.card_price(r[0]) * 3 < ep())
        offer = body(hc.route(f"/transfer/search/{spid}")("", b""))[0]
        ep0 = ep()
        assert buy(offer)["result"] and len(copies(spid)) == 1 and ep() == ep0 - offer["price"]
        assert buy(offer)["result"] and len(copies(spid)) == 2  # a second copy: an upgrade pair
        car = hc.coach_me(hc._state(), 0)["career"]  # Info Manager: cards bought, the dearest
        assert car["buyplayers"] == 2 and (car["maxbuy"]["spid"], car["maxbuy"]["amount"]) == (spid, offer["price"]) and "maxsell" not in car
        # the virtual managers' listings beside the +1..+3 stock: other grades a little under the market
        # price, Kuantitas per hour; buying takes from the row the path names, a row sold out (or never
        # there) is NO_SUCH_OFFER ("Pemain tidak ada.")
        rows = body(hc.route(f"/transfer/search/{spid}")("", b""))
        assert [r["grade"] for r in rows[:3]] == [1, 2, 3] and all(r["count"] >= 1 for r in rows)
        s, r = next((s, r) for s in sorted(hc.market_spids()) if hc.card_price(s) < 20000
                    for r in hc.market_rows(s) if r["grade"] >= 4 and r["price"] < ep() // 4)
        assert 0.955 * hc.card_price(s, r["grade"]) <= r["price"] <= hc.card_price(s, r["grade"])
        ep2, had = ep(), {hc.iid(p) for p in team()["players"]}
        for _ in range(r["count"]):
            assert buy(r)["result"]
        assert ep() == ep2 - r["count"] * r["price"] and buy(r)["reason"] == "NO_SUCH_OFFER"  # sold out this hour
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]  # undone: the listings change by the hour,
        t["players"] = [p for p in t["players"] if hc.iid(p) in had]  # and the reserves feed the trade tests below
        hc.add_ep(st, ep2 - ep()); hc._save(st)
        assert buy({**rows[0], "price": rows[0]["price"] - 1})["reason"] == "NO_SUCH_OFFER"
        hist = body(hc.route(f"/transfer/{spid}/1/detail")("", b""))["history"]  # the price graph
        assert hist["basePrice"] == hc.card_price(spid) and len(hist["data"]) == hc.PRICE_DAYS
        assert all(0.9 * hist["basePrice"] < p <= hist["basePrice"] for _, p in hist["data"]) and hist["endat"] > hist["startat"]
        # --- upgrade (enhancement v1): +1 & +1 -> +2 at 100% (ENHANCE_RATE[1] = 1.0)
        a, b = (hc.iid(p) for p in copies(spid))
        pv = body(hc.route(f"/player/{a}/enhance/{b}/preview")("", b""))
        assert pv["grade"] == 2 and pv["enhancepay"] >= 1000 and pv["price"] == hc.card_price(spid, 2), pv
        ep1 = ep()
        up = body(hc.route(f"/player/{a}/enhance/{b}")("", b"{}"))
        assert up["success"] and up["grade"] == 2 and [hc.iid(p) for p in copies(spid)] == [a] and ep() == ep1 - pv["enhancepay"]
        assert hc.grade_of(copies(spid)[0]) == 2 and hc.player_ovr(copies(spid)[0]) == hc.card_ovr(spid) + hc.GRADE_BOOST[2]
        assert not body(hc.route(f"/player/{a}/enhance/{a}/preview")("", b""))["result"]  # needs two cards
        # bonus follows the tab's label: +6 target with +1 Kosong, +2 Normal, +4 High; +9 with +2 Low
        rate = lambda t, m: hc.enhance_rate({"grade": t}, {"grade": m})
        assert [round(rate(6, m), 4) for m in (1, 2, 4)] == [0.15, 0.1875, 0.225]
        assert round(rate(9, 2), 4) == 0.022 and rate(2, 1) == 0.81
        # +0 material (issue #5): a club card adds no grade, so the target keeps its grade at
        # 100% and a +10 may take one; the copy takes its squad slot and joins Tim inti
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
        sub = min((s for s in t["squad"]["positions"] if s["pos"] >= hc.POS_SUB), key=lambda s: hc.card_price(s["spid"]))
        base = next(p for p in t["players"] if hc.iid(p) == str(sub.get("id") or sub["spid"]))
        assert hc.grade_of(base) == 0
        c10, c4 = (hc.add_player(t, base["spid"], g) for g in (10, 4))
        c10["team2"] = c4["team2"] = True
        hc.add_ep(st, 10 ** 9); hc._save(st)
        b0, c10, c4 = hc.iid(base), hc.iid(c10), hc.iid(c4)
        pv = body(hc.route(f"/player/{c10}/enhance/{b0}/preview")("", b""))
        assert pv["result"] and pv["grade"] == 10 and pv["probability"] == 100, pv
        assert not body(hc.route(f"/player/{c10}/enhance/{c4}/preview")("", b""))["result"]  # +10 with +4
        pv = body(hc.route(f"/player/{c4}/enhance/{b0}/preview")("", b""))
        assert pv["grade"] == 4 and pv["probability"] == 100, pv
        up = body(hc.route(f"/player/{c4}/enhance/{b0}")("", b"{}"))
        assert up["success"] and up["grade"] == 4 and up["attribPlus"] == 0, up
        kept = next(p for p in team()["players"] if hc.iid(p) == c4)
        assert not kept.get("team2") and hc.in_squad(team(), kept) and b0 not in {hc.iid(p) for p in team()["players"]}
        # issue #7: upgrading two starters of one player (a bench filled by the server could
        # hold a second card) left a hole in the XI; the shown squad fills it from the bench,
        # tops the bench up to 7 from Tim inti, and keeps one card per player in the 18
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
        xi = [s for s in t["squad"]["positions"] if s["pos"] < hc.POS_SUB]
        keep = next(p for p in t["players"] if hc.iid(p) == str(xi[-1].get("id") or xi[-1]["spid"]))
        twin = hc.add_player(t, keep["spid"], 1)
        xi[-2].update(id=hc.iid(twin), spid=twin["spid"])  # both cards in the XI
        hc._save(st)
        assert body(hc.route(f"/player/{hc.iid(keep)}/enhance/{hc.iid(twin)}")("", b"{}"))["result"]
        assert sum(s["pos"] < hc.POS_SUB for s in team()["squad"]["positions"]) == 10  # the hole
        shown = hc.coach_me(hc._state(), 0)["team"]["squad"]["positions"]
        players = [int(s["spid"]) % 1000000 for s in shown]
        assert sum(s["pos"] < hc.POS_SUB for s in shown) == 11 and len(set(players)) == len(players) == 18, shown
        assert {s["pos"] for s in shown if s["pos"] < hc.POS_SUB} == {s["pos"] for s in xi}
        # every starter carries starting [attacking, defensive, x, y]: without it the client moves
        # him to the bench (issue #7 again: the filled hole and a Formasi save had none)
        assert all(len(s.get("starting") or []) == 4 for s in shown if s["pos"] < hc.POS_SUB), shown
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
        for s in t["squad"]["positions"][:2]:
            s.pop("starting", None)
        hc._save(st)
        shown = hc.coach_me(hc._state(), 0)["team"]["squad"]["positions"]
        assert all(len(s.get("starting") or []) == 4 for s in shown if s["pos"] < hc.POS_SUB), shown
        # issue #9, Tim > Klub > Informasi tim: TeamStaticInfo shows coach.career.appat (start
        # date) and coach.stadium {id}, then GET /stadium/<id> {name, yearbuilt, capacity};
        # TeamManagementTeamInfoTabPage draws nothing (player stepper, 3D player) while coach.stadium is undefined
        me = hc.coach_me(hc._state(), 0)
        assert isinstance(me["stadium"]["id"], int) and me["career"]["appat"].endswith("Z")
        assert hc.club_stadium(243) == 2  # Real Madrid: Santiago Bernabeu
        bern = body(hc.route("/stadium/2")("", b""))
        assert bern == {"id": 2, "name": "Santiago Bernabéu", "yearbuilt": 1947, "capacity": 81044}, bern
        assert body(hc.route(f"/stadium/{me['stadium']['id']}")("", b""))["name"]
        # issue #8: Tim > Peran saves the captain and each set-piece taker on its own
        # (TeamManagementPlayerRolesTabPage: PUT /game/my/team/captain, .../kickers/<KICKER_TYPES
        # index: left corner, right corner, free kick, penalty, free kick 2>); the slots never shift
        xi_ids = [s["id"] for s in shown if s["pos"] < hc.POS_SUB]
        put = lambda uri, i: body(hc.ENDPOINTS[uri]("", json.dumps({"id": i, "_method": "PUT"}).encode()))
        r = put("/game/my/team/captain", xi_ids[3])
        assert r["result"] and r["resources"][0]["body"]["team"]["captain"] == xi_ids[3]
        assert put("/game/my/team/kickers/3", xi_ids[1])["result"] and put("/game/my/team/kickers/0", xi_ids[2])["result"]
        kick = hc.coach_me(hc._state(), 0)["team"]["kickers"]
        assert len(kick) == 5 and kick[3] == xi_ids[1] and kick[0] == xi_ids[2], kick
        assert not put("/game/my/team/kickers/2", "no-such-card")["result"]
        # issue #13, Tim > Locker room: every stepper has items (TOP / PANTS / SHOES, visible), boots
        # by brand; a look is saved on the card as lr and the shirt number swaps with its wearer;
        # Item saya holds the ticket (itemid 801, expireat in ms) that makes the look count in matches
        lri = body(hc.ENDPOINTS["/static/db/LOCKERROOMITEM"]("", b""))
        assert all(i["visible"] and i["type"] in ("TOP", "PANTS", "SHOES") for i in lri.values())
        cats = {(i["type"], i["category"], i.get("option") if i["category"] == "GLOVE" else None) for i in lri.values()}
        assert cats == {("TOP", c, None) for c in ("TATTOO", "UNIFORMKIND", "UNIFORMARM", "WRIST")} | {
            ("TOP", "GLOVE", "PLAYERGLOVE"), ("TOP", "GLOVE", "GKGLOVE"), ("PANTS", "SOCKS", None),
            ("PANTS", "KNEE", None), ("SHOES", "SHOES", None)}, cats
        shoes = [i for i in lri.values() if i["type"] == "SHOES"]
        assert len(shoes) == 614 and {i["option"] for i in shoes} == set(hc.LR_SHOES)
        assert lri["100012"]["asset"] == 12 and lri["100012"]["option"] == "ADIDAS" and lri["1001"]["color"] == "1"
        tix = [i for i in hc.coach_me(hc._state(), 0)["items"] if i["itemid"] == 801]
        assert len(tix) == 1 and tix[0]["createat"] and tix[0]["expireat"] > time.time() * 1000
        assert not body(hc.ENDPOINTS["/items/itemtodepot"]("", json.dumps({"moveitems": [tix[0]["_id"]]}).encode()))["result"]
        look = {"100012": {"id": "100012"}, "10": {"id": "10", "pos": "L", "clr": 3}, "nope": {"id": "nope"}}
        r = body(hc.route(f"/lockerroom/{xi_ids[0]}/itemset")("", json.dumps({"itemset": look, "_method": "PUT"}).encode()))
        card = lambda i: next(p for p in r["resources"][0]["body"]["team"]["players"] if p["_id"] == i)
        assert r["result"] and card(xi_ids[0])["lr"] == {k: look[k] for k in ("100012", "10")}
        r = body(hc.route(f"/lockerroom/{xi_ids[0]}/itemset")("", b'{"itemset":{},"_method":"PUT"}'))  # Reset
        assert r["result"] and "lr" not in card(xi_ids[0])
        n0, n1 = card(xi_ids[0])["jersey"], card(xi_ids[1])["jersey"]
        r = body(hc.route(f"/lockerroom/{xi_ids[0]}/jersey")("", json.dumps({"desired": n1, "giverplayerid": xi_ids[1]}).encode()))
        assert r["result"] and (card(xi_ids[0])["jersey"], card(xi_ids[1])["jersey"]) == (n1, n0)
        assert not body(hc.route(f"/lockerroom/{xi_ids[0]}/jersey")("", b'{"desired": 100}'))["result"]
        # a Formasi save with an empty slot keeps the others in place; a short list (old saves
        # dropped empty slots) comes back as 5 starters
        sq = hc.coach_me(hc._state(), 0)["team"]["squad"]
        hc.ENDPOINTS["/coach/me/squad"]("", json.dumps({"squad": sq, "kickers": ["", *kick[1:]], "captain": xi_ids[3]}).encode())
        kick2 = hc.coach_me(hc._state(), 0)["team"]["kickers"]
        assert kick2[1:] == kick[1:] and len(kick2) == 5 and kick2[0] in xi_ids, kick2
        st = hc._state(); st["teams"][str(hc._default_slot(st))]["squad"]["kickers"] = kick[:2]; hc._save(st)
        kick3 = hc.coach_me(hc._state(), 0)["team"]["kickers"]
        assert kick3[:2] == kick[:2] and len(kick3) == 5 and set(kick3) <= set(xi_ids), kick3
        # Formasi opens preset team.presetidx of the COACH-level teamsquadlist (none = "Tidak cukup pemain")
        me = hc.coach_me(hc._state(), 0); me_team = me["team"]
        preset = me["teamsquadlist"]["preset"][me_team["presetidx"]]
        assert preset["positions"] == me_team["squad"]["positions"] and preset["formation"] == me_team["squad"]["formation"]
        # Squad A / B / C (issue #20: the picker was an empty box without limit.preset_squad_count)
        assert body(hc.from_file("/static/db/STATICVALUES"))["limit"]["preset_squad_count"] == hc.PRESET_SQUADS
        assert len(me["teamsquadlist"]["preset"]) == hc.PRESET_SQUADS and me_team["presetidx"] == 0
        sq_a = me_team["squad"]
        put_sq = lambda sq, i=None: hc.ENDPOINTS["/coach/me/squad"]("", json.dumps(
            {"squad": sq, "kickers": sq["kickers"], "captain": sq["captain"], **({} if i is None else {"presetidx": i})}).encode())
        # pick B (starts as a copy of A), swap two starters there and save
        sq_b = copy.deepcopy(me["teamsquadlist"]["preset"][1])
        xi = [s for s in sq_b["positions"] if s["pos"] < hc.POS_SUB]
        xi[0]["pos"], xi[1]["pos"] = xi[1]["pos"], xi[0]["pos"]
        put_sq(sq_b, 1)
        me = hc.coach_me(hc._state(), 0)
        pos_of = lambda sq: {s["id"]: s["pos"] for s in sq["positions"]}
        assert me["team"]["presetidx"] == 1 and pos_of(me["team"]["squad"]) == pos_of(sq_b)
        assert pos_of(me["teamsquadlist"]["preset"][0]) == pos_of(sq_a) != pos_of(sq_b)  # A kept
        put_sq(me["team"]["squad"])  # an edit without presetidx stays on B
        assert hc.coach_me(hc._state(), 0)["team"]["presetidx"] == 1
        put_sq(me["teamsquadlist"]["preset"][0], 0)  # back to A
        me = hc.coach_me(hc._state(), 0)
        assert me["team"]["presetidx"] == 0 and pos_of(me["team"]["squad"]) == pos_of(sq_a)
        assert pos_of(me["teamsquadlist"]["preset"][1]) == pos_of(sq_b)
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
        hc.remove_player(t, next(p for p in t["players"] if hc.iid(p) == c10)); hc._save(st)
        # --- Upgrade 2.5: the tab, upgrade points (money.es) and boost items
        assert body(hc.ep_account("", b""))["features"]["teammanagement"]["playermanagement"]["enhancement"] == {"v2_5": {}}
        pe2 = body(hc.ENDPOINTS["/static/db/PE2STATICVALUES"]("", b""))
        # the popup constructor reads all three (a missing key left it half-built)
        assert pe2["bonus"]["use"] == 1000 and "use" in pe2["simplify_step"]
        ladder = [pe2["effect_price"][str(i)] for i in range(len(pe2["effect_price"]))]
        assert ladder == sorted(ladder, reverse=True) and len(ladder) == 5  # effect6..effect2
        es = lambda: hc.coach_me(hc._state(), hc._default_slot(hc._state()))["money"]["es"]
        with hc.closing(hc._db()) as c:
            s25 = next(r[0] for r in c.execute("SELECT spid FROM players WHERE overallrating BETWEEN 60 AND 65 ORDER BY spid")
                       if hc.on_market(r[0]) and not copies(r[0]))
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
        tg, mt = hc.iid(hc.add_player(t, s25, 6)), hc.iid(hc.add_player(t, s25, 2))
        gold = hc.inventory_add(hc.my_coach(st), 1000052, 2)["_id"]
        hc._save(st)
        es0 = es()
        boost_items = [i for i in hc.coach_items(hc._state(), hc._default_slot(hc._state())) if i["staticdata"]["type"] == "ENHANCEMENT_BOOST"]
        assert [i["remains"] for i in boost_items] == [2]
        assert not body(hc.route(f"/item/{gold}/use")("", b"{}"))["result"]  # not via "Gunakan"
        pv = lambda u, it, ev=-1: body(hc.route(f"/player/{tg}/enhance2.5/{mt}/preview/{u}/{it}/{ev}")("", b""))
        base = round(hc.enhance_rate({"grade": 6}, {"grade": 2}) * 100)
        assert pv(0, -1)["probability"] == base and pv(0, -1)["grade"] == 7 and pv(0, -1)["needes"] == 70
        assert pv(1, 1000052)["probability"] == base + 10 + 15
        # the never-ending Upgrade event (+10% for targets +1..+9), then the play time rewards' "dt" one
        ev = body(hc.ENDPOINTS["/events"]("", b""))["events"]
        assert [e["condition"]["type"] for e in ev] == ["enhance", "dt"] and ev[0]["active"] and ev[0]["reason"] == []
        assert all(isinstance(r, list) for r in ev[0]["reward"])  # getEnhanceGradeRange: Array entries
        assert pv(0, -1, ev[0]["eventid"])["probability"] == base + 10 and pv(0, -1, 999)["probability"] == base
        # upgrade points in Shop > Paket: credited to money.es at once
        pkg = next(e for e in hc.shop_list(hc._state()) if e["shopid"] == 81000500)
        assert pkg["shopcategory"] == "PACKAGE" and pkg["product"]["staticdata"]["items"][0]["staticdata"]["name"] == "Upgrade point"
        es1 = es()
        assert body(hc.ep_shop_purchase("", json.dumps({"productid": 81000500, "bunch": 0}).encode()))["result"]
        assert es() == es1 + 500 and body(hc.ep_static_item("", b'{"itemids": [2]}'))["2"]["itemicon"] == 8700001
        st = hc._state(); hc.add_es(st, -500); hc._save(st)
        assert not body(hc.route(f"/player/{tg}/enhance2.5/{mt}/0/1000053/-1")("", b"{}"))["result"]  # no Silver owned
        roll = hc.random.random
        try:
            hc.random.random = lambda: 0.999  # fail: +6 drops to +5, 70 points earned
            up = body(hc.route(f"/player/{tg}/enhance2.5/{mt}/0/-1/-1")("", b"{}"))
            assert not up["success"] and up["grade"] == up["rgrade"] == 5 and up["grantes"] == 70, up
            assert up["usepoint"] == {"currency": "ES", "price": 210} and es() == es0 + 70
            assert not body(hc.ENDPOINTS["/player/enhance2.5/restore"]("", b"{}"))["result"]  # 70 < 210
            st = hc._state(); hc.add_es(st, 300); hc._save(st)
            rs = body(hc.ENDPOINTS["/player/enhance2.5/restore"]("", b"{}"))
            assert rs["result"] and not rs["success"] and rs["grade"] == 6 and es() == es0 + 70 + 300 - 210, rs
            assert not body(hc.ENDPOINTS["/player/enhance2.5/restore"]("", b"{}"))["result"]  # once
            st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
            mt = hc.iid(hc.add_player(t, s25, 2)); hc._save(st)
            hc.random.random = lambda: 0.0  # success with points and a Gold boost
            up = body(hc.route(f"/player/{tg}/enhance2.5/{mt}/1/1000052/-1")("", b"{}"))
            assert up["success"] and up["grade"] == 7 and up["grantes"] == 0 and es() == es0 + 160 - 70, up
            assert [i["remains"] for i in hc.coach_items(hc._state(), hc._default_slot(hc._state())) if i["itemid"] == 1000052] == [1]
        finally:
            hc.random.random = roll
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
        hc.remove_player(t, next(p for p in t["players"] if hc.iid(p) == tg)); hc._save(st)
        # --- selling: a reserve at <= market price waits ("waiting" = Dijual) until a virtual manager
        # buys it (sooner when cheaper); then sold, pushed to the game; EP minus fee on collect
        band = body(hc.route(f"/transfer/price/{spid}/2")("", b""))
        assert band["upper"] == hc.card_price(spid, 2) and band["lower"] < band["upper"]
        assert band["lowest"] == min(r["price"] for r in hc.market_rows(spid) if r["grade"] == 2)
        sell = lambda pid, price: body(hc.ep_transfer_offer("", json.dumps({"playerobjectid": str(pid), "price": price}).encode()))
        mine = lambda: body(hc.ENDPOINTS["/transfer/my/offer/list"]("", b""))["offers"]
        squad = team()["squad"]
        starter = next(str(s.get("id") or s["spid"]) for s in squad["positions"] if s["pos"] < hc.POS_SUB)
        assert not sell(starter, 1000)["result"] and not sell(a, band["upper"] + 1)["result"]
        assert sell(a, band["upper"])["result"] and not copies(spid)
        offers = mine()
        assert len(offers) == 1 and offers[0]["status"] == "waiting" and not set(hc.OFFER_PRIVATE) & set(offers[0])
        o = offers[0]  # Batal: the card comes back; a second Batal finds nothing
        assert body(hc.route(f"/transfer/my/offer/waiting/{o['_id']}")("", b'{"_method": "DELETE"}'))["result"]
        assert [hc.iid(p) for p in copies(spid)] == [a] and mine() == []
        assert body(hc.route(f"/transfer/my/offer/waiting/{o['_id']}")("", b"{}"))["reason"] == "OFFER_NOT_EXIST_ERROR"
        assert sell(a, band["upper"])["result"]  # Ulang at the lowest allowed price: a buyer comes sooner
        o = hc._state()["offers"][0]
        slow = o["buyat"] - time.time()
        assert hc.VM_SALE[1] * 0.6 - 1 <= slow <= hc.VM_SALE[1] * 1.4 + 1 and o["buyer"].startswith("vm")
        reoffer = lambda price: body(hc.route(f"/transfer/offer/from/{o['_id']}")("", json.dumps({"price": price}).encode()))
        assert not reoffer(band["upper"] + 1)["result"] and reoffer(band["lower"])["result"]
        o = hc._state()["offers"][0]
        assert (o["status"], o["price"]) == ("waiting", band["lower"]) and o["buyat"] - time.time() < hc.VM_SALE[0] * 1.4 + 1
        n0 = len(pushed)
        st = hc._state(); st["offers"][0]["buyat"] = time.time() - 1; hc._save(st)  # his time has come
        offers = mine()
        assert len(offers) == 1 and (offers[0]["status"], offers[0]["price"]) == ("sold", band["lower"])
        assert [u for u, _ in pushed[n0:]] == ["/transfer/my/offer/list", "/inbox/my/notifications/latest"]
        alarm = pushed[-1][1]  # toast "<card> terjual seharga <price> EP."; the entry names the buyer
        assert (alarm["category"], alarm["type"], alarm["details"]["price"]) == ("transfermarket", "sold", f"{band['lower']:,}")
        assert hc.vm_by_id(o["buyer"])["name"] in alarm["details"]["title"] and alarm["details"]["expandme"] == "player"
        assert body(hc.route(f"/transfer/my/offer/waiting/{o['_id']}")("", b"{}"))["reason"] == "OFFER_NOT_EXIST_ERROR"
        assert mine() == offers and len(pushed) == n0 + 2  # told once
        res = body(hc.ENDPOINTS["/coach/me/onready"]("", b'{"_method": "PUT"}'))["resources"]
        assert [n["type"] for n in res[-1]["body"]] == ["list"]  # the login notice while it waits to be collected
        ep1 = ep()
        got = body(hc.route(f"/transfer/my/offer/completed/{offers[0]['_id']}")("", b"{}"))
        fee = offers[0]["completefee"]["base"]  # minus PC warnet 20% + VIP 30% fee discounts
        assert got["result"] and ep() == ep1 + band["lower"] - fee + int(0.2 * fee) + int(0.3 * fee)
        me = hc.coach_me(hc._state(), hc._default_slot(hc._state()))
        assert me["igr"] is True and me["vipDetail"] == 2 and me["vipExpireAt"] > time.time() * 1000
        assert body(hc.ENDPOINTS["/transfer/my/offer/list"]("", b""))["offers"] == []
        # a sold toast also stays in the notification dropdown (stackable); offers are each manager's
        # own (another slot's do not show, nor sell to this club); at most OFFERS_MAX listings; Batal
        # refuses a full club ("Exceed plyer limit")
        assert alarm["stackable"] is True
        st = hc._state()  # two test cards (taken out again below: the reserves feed the trade tests)
        spare = [hc.iid(hc.add_player(st["teams"][str(hc._default_slot(st))], spid, 1)) for _ in range(2)]; hc._save(st)
        assert sell(spare[0], 1)["result"]
        st = hc._state(); o = st["offers"][0]
        assert o["slot"] == hc._default_slot(st) and not any("slot" in x for x in mine())
        o["slot"] = 1 - o["slot"]; hc._save(st)
        assert mine() == [] and body(hc.route(f"/transfer/my/offer/waiting/{o['_id']}")("", b"{}"))["reason"] == "OFFER_NOT_EXIST_ERROR"
        o["slot"] = 1 - o["slot"]; st["offers"] += [dict(o, _id=f"x{k}") for k in range(hc.OFFERS_MAX - 1)]; hc._save(st)
        assert sell(spare[1], 1)["reason"] == "TRANSFERMARKET_MAX_REGISTERED"
        st = hc._state(); st["offers"] = st["offers"][:1]; t = st["teams"][str(hc._default_slot(st))]
        filler = [dict(t["players"][-1], _id=f"fill{k}") for k in range(hc.MAX_PLAYERS - len(t["players"]))]
        t["players"] += filler; hc._save(st)
        assert body(hc.route(f"/transfer/my/offer/waiting/{o['_id']}")("", b"{}"))["reason"] == "Exceed plyer limit"
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
        t["players"] = [p for p in t["players"] if not str(p.get("_id", "")).startswith("fill")]; hc._save(st)
        assert body(hc.route(f"/transfer/my/offer/waiting/{o['_id']}")("", b"{}"))["result"] and mine() == []
        assert not os.path.exists(hc.STATE + ".tmp")  # saves are whole: a temp file, then a rename
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
        t["players"] = [p for p in t["players"] if hc.iid(p) not in spare]; hc._save(st)
        # --- trade (Tukar v1): 2..10 reserves -> offers rated >= avg OVR + bonus, PC + VIP extra
        post = lambda path, req: body(hc.ENDPOINTS[path]("", json.dumps(req).encode()))
        with hc.closing(hc._db()) as c:
            cheap = [r[0] for r in c.execute("SELECT spid FROM players WHERE overallrating BETWEEN 58 AND 64 ORDER BY spid")
                     if hc.on_market(r[0])][:6]
        for s in cheap:  # bought cards land in the reserves
            assert buy(body(hc.route(f"/transfer/search/{s}")("", b""))[0])["result"]
        reserves = [p for p in team()["players"] if not hc.in_squad(team(), p)][:4]
        ids = [hc.iid(p) for p in reserves]
        sub = next(str(s.get("id") or s["spid"]) for s in team()["squad"]["positions"] if s["pos"] >= hc.POS_SUB)
        assert not post("/players/trade/proposal", {"players": ids[:1]})["result"]  # needs 2+
        assert not post("/players/trade/proposal", {"players": [starter] + ids[:1]})["result"]
        assert not post("/players/trade/proposal", {"players": [sub] + ids[:1]})["result"]  # subs neither
        pr = post("/players/trade/proposal", {"players": ids})
        sv = hc.static_values()
        # pack opening glow (TeamManagementPlayerCardScreen.getEffectType): highest threshold
        # first, and Market cards land in every tier (none, 1, 2, 3)
        star = sv["starplayer_price"]
        assert star == sorted(star, reverse=True)
        tiers = {sum(r[0] >= t for t in star) for r in hc.trade3_pool()[::997]}
        assert tiers == {0, 1, 2, 3}, tiers
        lo = min(int(sum(hc.card_ovr(p["spid"]) for p in reserves) / 4 + sv["playertrade_cardovrbonus"]["4"]),
                 sv["playertrade_limit"]["CardMaxValue"])
        assert pr["result"] and len(pr["props"]) == sv["playertrade_normal"]["4"] + 2, pr
        assert [o.get("boost") for o in pr["props"]][-2:] == ["igr", "vip"]
        assert all(hc.card_ovr(o["spid"]) >= lo and o["price"] == hc.card_price(o["spid"], o["grade"])
                   for o in pr["props"] if "spid" in o), (lo, pr)
        ep_only = next(i for i, o in enumerate(pr["props"]) if "spid" not in o)
        n0, ep1 = len(team()["players"]), ep()
        assert post("/players/trade/contract", {"select": ep_only})["result"]
        assert len(team()["players"]) == n0 - 4 and ep() == ep1 + pr["props"][ep_only]["ep"]
        assert not any(hc.iid(p) in ids for p in team()["players"])
        assert not post("/players/trade/contract", {"select": 0})["result"]  # proposal used up
        pr = post("/players/trade/proposal", {"players": [hc.iid(p) for p in team()["players"] if not hc.in_squad(team(), p)][:2]})
        n0, card = len(team()["players"]), pr["props"][0]
        assert post("/trade3/contract", {"selectnum": 0, "slotnum": 0})["result"] and len(team()["players"]) == n0 - 1
        assert (team()["players"][-1]["spid"], team()["players"][-1]["grade"]) == (card["spid"], card["grade"])
        # --- Tukar v2 (trade2): TP from card values, levels, "yesterday" ladder, no limits
        assert "trade2" in body(hc.ep_account("", b""))["features"]["teammanagement"]["playermanagement"]
        tt = body(hc.ENDPOINTS["/static/db/TRADE"]("", b""))
        maxg = [tt["tp"][k]["value"][-1] for k in sorted(tt["tp"], key=int)]
        assert len(maxg) == 15 and maxg == sorted(maxg) and maxg[0] == 1 and maxg[-1] == 10
        assert tt["trade2_limit"]["max"] == hc.NO_LIMIT and set(tt["playertrade2_bonus"]) >= {"IGR", "VIP", "2", "10"}
        assert body(hc.ENDPOINTS["/account/trade2/usage"]("", b""))["maxcount"] == 0  # unlimited
        special = body(hc.ENDPOINTS["/static/cache/TRADE_SPECIALPLAYERS"]("", b""))
        assert special and all(int(s) // 1000000 in hc.LEGEND_SEASONS for s in special)
        ladder = body(hc.ENDPOINTS["/players/trade2/info"]("", b""))
        assert len(ladder) == 14 and all(e["expandme"] == "player" and e["coach"] and e["grade"] <= hc.trade2_max_grade(int(k))
                                         for k, e in ladder.items())
        give = [p for p in team()["players"] if not hc.in_squad(team(), p) and hc.grade_of(p)][:3]  # +0 = no value
        tp = hc.trade2_tp(give)
        pr = post("/players/trade2/proposal", {"players": [hc.iid(p) for p in give]})
        assert pr["result"] and len(pr["props"]) == tt["playertrade2_normal"]["3"] + 2, pr
        assert all(o["grade"] <= hc.trade2_max_grade(tp) and o["price"] == hc.card_price(o["spid"], o["grade"])
                   and 0.5 * tp <= o["price"] <= 2 * tp for o in pr["props"]), (tp, pr)
        assert not post("/players/trade2/proposal", {"players": [starter, hc.iid(give[0])]})["result"]  # reserves only
        n0 = len(team()["players"])
        assert post("/players/trade2/contract", {"select": 1})["result"] and len(team()["players"]) == n0 - 2
        assert team()["players"][-1]["spid"] == pr["props"][1]["spid"]
        pt = body(hc.ENDPOINTS["/static/db/PLAYERTRADE"]("", b""))
        assert list(pt["playergrade"]["2"]) == ["1"] and list(pt["playergrade"]["40"])[-1] == "6"
        # --- trade3 (Scout): offers worth about the total value; scouts cool down after use
        st = hc._state(); hc.my_coach(st).pop("scout_used", None); hc._save(st)
        scouts = lambda: {s["key"]: s for s in hc.coach_me(hc._state(), hc._default_slot(hc._state()))["scouters"]}
        assert set(scouts()) == {"default_B01", "igr", "vip", "event", "vvip"} and len(hc.SCOUTS) <= 5 and not any(s["lock"] for s in scouts().values())
        assert scouts()["vvip"]["scoutername"] == "STR_SCOUTER_KR_VVIP_A01_NAME" and scouts()["vvip"]["quest"]["require_value"] > 0
        legend = [{"spid": s, "grade": 1} for s in list(map(int, hc.trade2_special()))[:2]]
        assert hc.quest_met(scouts()["event"]["quest"], legend) and not hc.quest_met(scouts()["event"]["quest"], legend[:1])
        for s in cheap[:3]:
            assert buy(body(hc.route(f"/transfer/search/{s}")("", b""))[0])["result"]
        give3 = [p for p in team()["players"] if not hc.in_squad(team(), p)][-3:]
        pids = [{"id": hc.iid(p), "spid": p["spid"], "grade": hc.grade_of(p), "price": hc.card_price(p["spid"], hc.grade_of(p))}
                for p in give3]
        total = sum(x["price"] for x in pids)
        # Market cards arrive in Tim Cadangan, which Tukar Scout trades from; a Tim inti card is refused
        players = lambda: {p["_id"]: p for p in hc.coach_me(hc._state(), hc._default_slot(hc._state()))["team"]["players"]}
        assert all("state" not in players()[x["id"]] for x in pids)
        move = lambda pid, where: body(hc.route(f"/player/{pid}/move/{where}")("", b"{}"))
        assert not move(starter, "secondteam")["result"]  # the 18 stay in Tim inti
        assert move(pids[0]["id"], "firstteam")["result"] and players()[pids[0]["id"]]["state"] == "first"
        assert not post("/trade3/proposal", {"id": "vip", "slotnum": 3, "playerids": pids})["result"]
        assert move(pids[0]["id"], "secondteam")["result"] and players()[starter]["state"] == "first"
        pr = post("/trade3/proposal", {"id": "vip", "slotnum": 3, "playerids": pids})
        assert pr["result"] and len(pr["props"]) == hc.TRADE3_STATIC["playertrade3_normal"]["3"] + 2, pr
        assert all(total * 0.5 <= o["price"] <= total * 3 for o in pr["props"]), (total, pr["props"])
        # scouts have no cooldown: the same scout negotiates again at once
        assert not any(s["lock"] or "cooltime" in s for s in scouts().values())
        pr = post("/trade3/proposal", {"id": "vip", "slotnum": 3, "playerids": pids})
        assert pr["result"]
        stale = [dict(pids[0], price=pids[0]["price"] + 1)] + pids[1:]
        assert post("/trade3/proposal", {"id": "default_B01", "slotnum": 0, "playerids": stale})["reason"] == "TRADE3_NOTICE_PLAYERPRICE03"
        n0 = len(team()["players"])
        assert post("/trade3/contract", {"slotnum": 3, "selectnum": 0})["result"] and len(team()["players"]) == n0 - 2
        assert team()["players"][-1]["spid"] == pr["props"][0]["spid"]
        assert hc.quest_met({"require_grade": 1, "require_grade_count": 2}, give3) and not hc.quest_met(None, give3)
        assert body(hc.ENDPOINTS["/trade3/staticvalues"]("", b""))["playertrade3_normal"] == {
            "2": 1, "3": 1, "4": 2, "5": 2, "6": 2, "7": 3, "8": 3, "9": 3, "10": 3}
        # --- daily login reward: once per WIB day into the Bag; claim one ("Terima") / all
        hc.DAILY_FILE = os.path.join(tmp, "daily_rewards.json")  # default table; admin test writes here
        st = hc._state(); hc.my_coach(st).pop("daily", None); hc.my_coach(st)["bag"] = []
        t0, day, table = time.time(), 86400, hc.daily_table()
        e1 = hc.daily_login(st, t0)
        assert e1["items"] == hc.reward_items(table[0]) and hc.daily_login(st, t0 + 60) is None  # once a day
        e2 = hc.daily_login(st, t0 + day)
        assert hc.my_coach(st)["daily"]["streak"] == 2 and e2["items"] == hc.reward_items(table[1])
        e3 = hc.daily_login(st, t0 + 3 * day)
        assert hc.my_coach(st)["daily"] == {"last": hc.today_wib(t0 + 3 * day), "streak": 1, "total": 3}  # streak broken
        hc._save(st)
        bag = body(hc.ENDPOINTS["/bag"]("", b""))
        assert [b["title"] for b in bag] == [e1["title"], e2["title"], e3["title"]] and {b["type"] for b in bag} == {"event"}
        assert "hari ke-3" in bag[1]["msg"] and all(b["date"].endswith(".000Z") for b in bag)  # tooltip text + received date
        ep1 = ep()
        assert body(hc.route(f"/bag/{e1['_id']}/unpack")("", b"{}"))["result"] and ep() == ep1 + table[0].get("ep", 0)
        got = body(hc.ENDPOINTS["/bag/unpackall"]("", json.dumps({"reqUnpackItems": [e2["_id"], e3["_id"]]}).encode()))
        assert got["result"] and body(hc.ENDPOINTS["/bag"]("", b"")) == []
        assert not body(hc.route(f"/bag/{e1['_id']}/unpack")("", b"{}"))["result"]  # already taken
        assert list(body(hc.ENDPOINTS["/static/cache/item"]("", json.dumps({"itemids": [1]}).encode()))) == ["1"]
        st = hc._state(); hc.my_coach(st).pop("items", None); hc._save(st)  # packs from the rewards (shop test counts)
        # today's login reward as an event notification (EventRewardPopup), once a day; PUT
        # /events/rewards/daily (the client, early in a session) gives the day's reward if not yet
        st = hc._state(); kept_daily = copy.deepcopy(hc.my_coach(st)["daily"]); hc.my_coach(st).pop("daily"); hc._save(st)
        n0 = len(pushed)
        assert body(hc.ENDPOINTS["/events/rewards/daily"]("", b"{}"))["result"]
        uri, n = pushed[n0]
        assert uri == "/inbox/my/notifications/latest" and (n["category"], n["type"]) == ("event", "reward") and n["stackable"]
        d = n["details"]
        assert d["type"] == "daily" and d["rewardindex"] == 0 and d["messages"]["popup"]["name"] and d["opts"]["dailytype"] != "separated"
        assert [(i["itemid"], i["count"]) for i in d["items"]] == [(i["itemid"], i["count"]) for i in hc.reward_items(table[0])]
        assert all("staticdata" in i for i in d["items"]) and len(hc.my_coach(hc._state())["bag"]) == 1
        body(hc.ENDPOINTS["/events/rewards/daily"]("", b"{}"))
        assert len(pushed) == n0 + 1  # once a day
        # play time rewards: the "dt" event (its steps in seconds, the seconds played today); a step's
        # reward once its time is played (into the Bag, with a notification), not before
        hc.PLAYTIME_FILE = os.path.join(tmp, "playtime_rewards.json")  # the defaults; the admin test writes here
        hc.PLAYTIME["pending"] = 0
        st = hc._state(); c = hc.my_coach(st); c["daily"] = kept_daily; c["bag"] = []; c.pop("dt", None); c["playtime"] = 5000; hc._save(st)
        ev = {e["eventid"]: e for e in body(hc.ENDPOINTS["/events"]("", b""))["events"]}
        dt = ev[hc.DT_EVENTID]["condition"]["dt"]
        assert ev[hc.DT_EVENTID]["condition"]["type"] == "dt" and hc.ENHANCE_EVENT in ev.values()
        assert dt["timeline"] == [600, 1800, 3600] and dt["endline"] == 3600 and dt["rewarded"] == 0 and dt["time"]["prevsession"] == 0
        assert isinstance(dt["time"]["beginningat"], int)  # ms: TimerUtil.convertToUTCTime adds to it
        assert all(r[0]["items"][0]["itemid"] and "staticdata" in r[0]["items"][0] for r in ev[hc.DT_EVENTID]["reward"])  # reward[step][0].items[0]
        claim_dt = lambda: body(hc.ENDPOINTS["/events/rewards/dt"]("", b"{}"))
        r = claim_dt()
        assert not r["result"] and r["resources"][0]["uri"] == "/events"  # nothing played yet today
        st = hc._state(); hc.my_coach(st)["playtime"] = 5000 + 700; hc._save(st)
        n0 = len(pushed)
        r = claim_dt()
        assert r["result"] and r["resources"][0]["body"]["events"][1]["condition"]["dt"]["rewarded"] == 1
        assert pushed[n0][1]["details"]["type"] == "dt" and len(hc.my_coach(hc._state())["bag"]) == 1
        assert not claim_dt()["result"]  # the next step is at 30 minutes
        st = hc._state(); hc.my_coach(st)["bag"] = []; hc._save(st)
        # --- manager level rewards (issue #35, FO3's table): each level reached puts its EP and items in
        # the Bag, the post-match popup lists them once, the coming levels' table is served, and the
        # reward-only packs stay out of the Shop
        assert len(hc.LEVEL_EP) == 99 and hc.LEVEL_EP[0] == 50_000 and hc.LEVEL_EP[58] == 1_900_000 and hc.LEVEL_EP[-1] == 28_900_000
        assert all(i in hc.PACKS or i in hc.ITEMS for items in hc.LEVEL_ITEMS.values() for i, _ in items)
        assert hc.level_reward_items(2) == [{"itemid": hc.EP_ITEM, "count": 50_000}, {"itemid": 82214007, "count": 2},
                                            {"itemid": 92000000, "count": 5}, {"itemid": 2001, "count": 3}]
        assert hc.level_reward_items(30) == [{"itemid": hc.EP_ITEM, "count": 330_000}] and hc.level_reward_items(101) == []
        st = hc._state(); coach = hc.my_coach(st); coach["bag"] = []; coach.pop("levelreward", None)
        li, lv0 = dict(coach.get("levelinfo") or {}), hc.coach_levelinfo(coach)["level"]
        prev, cur = hc.coach_gain_exp(st, sum(hc.coach_need(lv) for lv in (lv0, lv0 + 1)) - hc.coach_levelinfo(coach)["exp"])
        assert (prev["level"], cur["level"]) == (lv0, lv0 + 2)
        assert [e["title"] for e in coach["bag"]] == [f"Hadiah naik level Lv.{lv}" for lv in (lv0 + 1, lv0 + 2)]
        hc._save(st)
        want = Counter()
        for lv in (lv0 + 1, lv0 + 2):
            for it in hc.level_reward_items(lv):
                want[it["itemid"]] += it["count"]
        pop = body(hc.ENDPOINTS["/coach/me/level/reward"]("", b""))
        assert {it["itemid"]: it["count"] for it in pop} == dict(want) and all(it["staticdata"]["itemid"] == it["itemid"] for it in pop)
        assert body(hc.ENDPOINTS["/coach/me/level/reward"]("", b"")) == []  # once
        table = body(hc.ENDPOINTS["/static/db/REWARDS/coachlevelup"]("", b""))
        assert list(table) == [str(lv) for lv in range(2, hc.COACH_MAX_LEVEL + 1)] and table["60"]["coachlevel"] == 60
        assert [it["itemid"] for it in table["60"]["items"]] == [hc.EP_ITEM, 98000004, 98000012, 98000002]
        assert not {e["shopid"] for e in body(hc.ENDPOINTS["/shop/list"]("", b""))} & set(hc.REWARD_PACKS)
        st = hc._state(); hc.my_coach(st)["bag"] = []; hc.my_coach(st)["levelinfo"] = li; hc._save(st)
        # --- staff (user: "Aktifkan kembali staff"): /static/db/STAFF as the client reads it, Rekrut (EP
        # and the daily free pack), the role frames and their boosts (summary bars, match sides),
        # contracts, Latihan (what its popup predicts from the tables) and Pembinaan
        sdb = body(hc.ENDPOINTS["/static/db/STAFF"]("", b""))
        assert sdb["study"] and sdb["config"]["max_staff_life"] == hc.STAFF_LIFE and sdb["config"]["default_staff_count"] == hc.STAFF_MAX
        assert [s["slot"] for s in sdb["slots"]["1"]] == list(range(1, 8))
        assert all(s["enabled"] and s["position"] in hc.STAFF_SLOTS for s in sdb["slots"]["1"])
        assert set(sdb["stafflist"]) == {str(i) for i in hc.STAFF_CATALOGUE}
        assert all(str(v["expectid"]) in sdb["gradeexpect"] for v in sdb["stafflist"].values())
        xt = sdb["exptable"]
        assert list(xt) == [str(g) for g in range(1, 11)] and xt["10"]["gradeupto"] == 0
        assert all(xt[str(g + 1)]["totalexp"] == xt[str(g)]["totalexp"] + xt[str(g)]["gradeupto"] for g in range(1, 10))
        assert sorted(v["location"] for v in sdb["study"].values()) == [f"LOCATION_NAME_0{i}" for i in range(1, 7)]
        assert sdb["config"]["max_staff_open_count"] == 10  # issue #41: Rekrut's count list goes to 10
        client_stats = set(("strength stamina acceleration sprintspeed jumping agility balance slidingtackle dribbling ballcontrol "
                            "marking standingtackle crossing shortpassing finishing longpassing shotpower headingaccuracy longshots "
                            "volleys curve freekickaccuracy penalties interceptions positioning vision reactions aggression gkkicking "
                            "gkdiving gkhandling gkpositioning gkreflexes ovr").split())  # PlayerListUtil.PLAYER_STATS + ovr
        assert all(x in client_stats for c in hc.STAFF_CATALOGUE.values() if c[1] for pool in c[1] for x in pool)
        rng = hc.random.Random(7)  # a pack's staff: its positions and grades, boosts by grade from its pools
        for p in list(hc.STAFF_PACKS.values()) + [hc.STAFF_FREE_PACK]:
            for _ in range(60):
                x = hc.staff_new(p, rng)
                pos, pools, _ = hc.STAFF_CATALOGUE[x["stfid"]]
                assert pos in p[3] and p[4][0] <= x["grade"] <= p[4][1] and x["life"] == hc.STAFF_LIFE
                assert x["effect"] == [] if pools is None else \
                    len(x["effect"]) == 1 + (x["grade"] >= 3) + (x["grade"] >= 6) and x["effect"][0]["amount"] == x["grade"] \
                    and all(e["effect"] in pools[k] for k, e in enumerate(x["effect"]))
        slot = str(hc._default_slot(hc._state()))
        st = hc._state(); raw = st["teams"][slot]
        for k in ("staffs", "staffslots", "staffsmc", "staffrecruit"):
            raw.pop(k, None)
        ep_kept = hc.my_coach(st).get("ep", hc.START_EP); hc.my_coach(st)["ep"] = 10 ** 9; hc._save(st)
        me = lambda: hc.coach_me(hc._state(), hc._default_slot(hc._state()))
        staffs = lambda: me()["team"]["staffs"]
        staff = lambda sid: next(s for s in hc._state()["teams"][slot]["staffs"] if s["_id"] == sid)
        call = lambda path, req: body((hc.ENDPOINTS.get(path) or hc.route(path))("", json.dumps(req).encode()))
        rows = body(hc.ENDPOINTS["/staff/recruit"]("", b""))
        assert [r["recruitid"] for r in rows if r["recruittype"] == "normal"] == list(hc.STAFF_PACKS)
        assert all(r["ep"] > 0 and r["staffpack_name"].startswith("STAFFPACK_NAME_") for r in rows if r["recruittype"] == "normal")
        assert rows[-1]["eventlist"] == {"login": {"count": 1, "targetcount": 1, "enable": True},
                                         "pcmatch": {"count": 0, "targetcount": 3, "enable": False}}
        own, hc.OWN_ASSETS = hc.OWN_ASSETS, tmp  # no pack picture: a staff's; one of its own (issue #41) beats it
        assert hc.asset_path(f"externalAssets/shop/{rows[0]['icon']}.png") == hc.asset_path("externalAssets/staffs/2103.png")
        art = os.path.join(tmp, "externalAssets", "shop", "broall.png")
        os.makedirs(os.path.dirname(art), exist_ok=True)
        open(art, "wb").write(b"png")
        assert rows[0]["icon"] == "broall" and hc.asset_path("externalAssets/shop/broall.png") == art
        hc.OWN_ASSETS = own
        assert hc.asset_path("externalAssets/shop/nosuchpack.png") is None
        ep0 = ep()
        got = call("/staff/recruit/3", {"feetype": "ep"})  # Basic FW: appended last, EP taken
        assert got["result"] and ep() == ep0 - hc.staff_ep(50_000) and [s["position"] for s in staffs()] == ["FW"]
        s = staffs()[-1]
        assert "id" not in s and s["commonname"] == f"STAFF_NAME_{s['stfid']}" and isinstance(s["effect"], list)
        mt = got["resources"][0]["body"]["team"]  # the shapes CoachProxy needs (numbers, an array of 7 frames)
        assert mt["staffroleidx"] == 1 and mt["maxstaff"] == hc.STAFF_MAX and mt["staffsmc"] == 0
        assert [r["slotidx"] for r in mt["staffroles"]] == list(range(1, 8)) and not any(r["staffid"] for r in mt["staffroles"])
        assert call("/staff/recruit/1", {"eventtype": "login"})["result"] and not call("/staff/recruit/1", {"eventtype": "login"})["result"]
        assert not call("/staff/recruit/1", {"eventtype": "pcmatch"})["result"]  # 3 matches first
        st = hc._state(); [hc.staff_after_match(st) for _ in range(3)]; hc._save(st)
        assert body(hc.ENDPOINTS["/staff/recruit"]("", b""))[-1]["eventlist"]["pcmatch"]["enable"]
        assert call("/staff/recruit/1", {"eventtype": "pcmatch"})["result"] and len(staffs()) == 3
        assert not any(e["enable"] for e in body(hc.ENDPOINTS["/staff/recruit"]("", b""))[-1]["eventlist"].values())
        st = hc._state(); hc.my_coach(st)["ep"] = 0; hc._save(st)
        assert call("/staff/recruit/9", {"feetype": "ep"}) == {"result": False, "reason": "STAFF_NOT_ENOUGH_GAMEMONEY"}
        st = hc._state(); hc.my_coach(st)["ep"] = 10 ** 9
        full = st["teams"][slot]["staffs"]; full += [dict(full[0], _id=hc.new_iid()) for _ in range(hc.STAFF_MAX - len(full))]
        hc._save(st)
        assert call("/staff/recruit/2", {"feetype": "ep"})["reason"] == "ITEM_INVENTORY_ERROR_STAFF_FULL_INFO"
        # role frames: a staff in its position's frame boosts that group's starters (MEDICAL: all), as
        # the client's squad lists do, and goes into my match side; out of contract it does neither
        st = hc._state(); raw = st["teams"][slot]
        fw = {"_id": hc.new_iid(), "stfid": 1105, "grade": 4, "exp": 0, "life": 2, "status": None, "effect": [{"effect": "ovr", "amount": 5}]}
        fit = {"_id": hc.new_iid(), "stfid": 5001, "grade": 2, "exp": 0, "life": 50, "status": None, "effect": [{"effect": "ovr", "amount": 3}]}
        raw["staffs"] = [fw, fit]; raw["staffsmc"] = 0; hc._save(st)
        r0 = hc.xi_ratings(me()["team"])
        bars0 = hc.match_side(hc._state()["teams"][slot], hc.my_teamid(hc._state()), user={"id": "x"})["team"]["summary"]["ovr"]
        assert not call(f"/staff/appoint/{fw['_id']}", {"slot": 2, "staffrole": None})["result"]  # the MF frame
        got = call(f"/staff/appoint/{fw['_id']}", {"slot": 1, "staffrole": None})
        assert got["result"] and got["appointed"]["appointedstaff"]["status"] == "appointed" and got["appointed"]["prevstaff"] is None
        assert got["resources"][0]["body"]["team"]["staffroles"][0]["staffid"] == fw["_id"]
        t1 = me()["team"]; r1 = hc.xi_ratings(t1)
        fwd = {str(p.get("id") or p["spid"]) for p in t1["squad"]["positions"] if 19 < p["pos"] < hc.POS_SUB}
        assert fwd and all(r1[i] == r0[i] + 5 * (i in fwd) for i in r0)
        assert call(f"/staff/appoint/{fit['_id']}", {"slot": 6})["result"]
        assert all(hc.xi_ratings(me()["team"])[i] == r0[i] + 5 * (i in fwd) + 3 for i in r0)
        side = hc.match_side(hc._state()["teams"][slot], hc.my_teamid(hc._state()), user={"id": "x"})["team"]
        assert [r["staffid"] for r in side["staffroles"]] == [s["_id"] for s in side["staffs"]] == [fw["_id"], fit["_id"]]
        assert side["staffs"][0]["stat"] == {"ovr": 5} and side["summary"]["ovr"] == bars0  # the pre-match list: no staff
        ai = hc.match_side(hc._state()["teams"][slot], 1)["team"]
        assert ai["staffs"] == [] == ai["staffroles"]
        st = hc._state(); hc.staff_after_match(st); hc.staff_after_match(st); hc._save(st)
        assert me()["team"]["staffsmc"] == 2 and staff(fw["_id"])["life"] == 0 and staff(fit["_id"])["life"] == 48
        side = hc.match_side(hc._state()["teams"][slot], hc.my_teamid(hc._state()), user={"id": "x"})["team"]
        assert [s["_id"] for s in side["staffs"]] == [fit["_id"]]
        assert all(hc.xi_ratings(me()["team"])[i] == r0[i] + 3 for i in r0)
        pv = call("/staff/recontract/preview", {"staffs": [fw["_id"], fit["_id"]], "_method": "PUT"})
        assert pv["boost"] == {"igr": 0, "vip": 0, "discountlimit": 0} and pv["total"] == sum(r["contractfee"] for r in pv["staffs"])
        assert {r["id"]: r["contractfee"] for r in pv["staffs"]} == {fw["_id"]: hc.staff_ep(100 * 4 * 50), fit["_id"]: 400}
        e1 = ep()
        assert call("/staff/recontract", {"staffs": [fw["_id"]], "_method": "PUT"})["result"]
        assert ep() == e1 - hc.staff_ep(100 * 4 * 50) and staff(fw["_id"])["life"] == hc.STAFF_LIFE
        got = call(f"/staff/appoint/{fw['_id']}", {"_method": "DELETE"})
        assert got["result"] and got["staffid"] == fw["_id"] and staff(fw["_id"])["status"] is None
        assert fw["_id"] not in {r["staffid"] for r in got["resources"][0]["body"]["team"]["staffroles"]}
        assert not call(f"/staff/{fit['_id']}", {"_method": "DELETE"})["result"]  # at work: no Hapus
        assert call(f"/staff/{fw['_id']}", {"_method": "DELETE"})["result"] and [s["_id"] for s in staffs()] == [fit["_id"]]
        # Latihan: the staff comes out as the popup predicts from the static tables (no jackpot)
        st = hc._state(); raw = st["teams"][slot]
        tgt = {"_id": hc.new_iid(), "stfid": 2103, "grade": 2, "exp": 50, "life": 50, "status": None,
               "effect": [{"effect": "ballcontrol", "amount": 2}]}
        mats = [{"_id": hc.new_iid(), "stfid": i, "grade": g, "exp": 0, "life": 50, "status": None, "effect": []}
                for i, g in ((4902, 3), (2101, 2), (1101, 1))]  # an MF helper, an MF, an FW
        raw["staffs"] += [tgt] + mats; hc._save(st)

        def predict(t, ms):  # TeamManagementStaffUpgradeSelectScene's sums
            k = lambda m: int(hc.STAFF_CATALOGUE[m["stfid"]][0] == hc.STAFF_CATALOGUE[t["stfid"]][0]) \
                + 2 * int(sdb["stafflist"][str(m["stfid"])]["material"])
            cum = t["exp"] + sum(sdb["materialexp"][str(m["grade"])][k(m)] for m in ms) + xt[str(t["grade"])]["totalexp"]
            g = max(g for g in range(1, 11) if cum >= xt[str(g)]["totalexp"])
            amounts = [e["amount"] for e in t["effect"]] + [0, 0]
            for gg in range(t["grade"] + 1, g + 1):
                for i, n in enumerate(sdb["gradeexpect"][str(sdb["stafflist"][str(t["stfid"])]["expectid"])][str(gg)]["effects"]):
                    amounts[i] += n
            return g, 0 if g == 10 else cum - xt[str(g)]["totalexp"], [a for a in amounts if a > 0]
        want, e1, real = predict(tgt, mats), ep(), hc.random.random
        hc.random.random = lambda: 0.99  # no jackpot
        try:
            got = call(f"/staff/enhance/{tgt['_id']}", {"materials": [m["_id"] for m in mats], "_method": "PUT"})
        finally:
            hc.random.random = real
        assert got["result"] and got["jackpot"] is False and ep() == e1 - sum(sdb["enhancefee"][str(m["grade"])]["fee"] for m in mats)
        t = staff(tgt["_id"])
        assert want == (4, 40, [4, 1]) and (t["grade"], t["exp"], [e["amount"] for e in t["effect"]]) == want
        assert t["effect"][1]["effect"] in hc.STAFF_CATALOGUE[2103][1][1] and not {m["_id"] for m in mats} & {s["_id"] for s in staffs()}
        st = hc._state(); helper = {"_id": hc.new_iid(), "stfid": 4902, "grade": 1, "exp": 0, "life": 50, "status": None, "effect": []}
        st["teams"][slot]["staffs"].append(helper); hc._save(st)
        hc.random.random = lambda: 0.0  # the jackpot: half as much EXP again
        try:
            assert call(f"/staff/enhance/{tgt['_id']}", {"materials": [helper["_id"]], "_method": "PUT"})["jackpot"] is True
        finally:
            hc.random.random = real
        assert (staff(tgt["_id"])["grade"], staff(tgt["_id"])["exp"]) == (4, 40 + 180)
        # Pembinaan: Asia (3) turns boost 2 of an MF / DF staff into another of the position's, in 5 matches
        assert not call("/staff/study/1", {"staffs": [tgt["_id"]], "_method": "PUT"})["result"]  # Oceania: FW / DF only
        e1 = ep()
        assert call("/staff/study/3", {"staffs": [tgt["_id"]], "_method": "PUT"})["result"] and ep() == e1 - sdb["study"]["3"]["fee"]
        t = staff(tgt["_id"]); b = t["study"]["boost"]
        assert t["status"] == "studying" and t["study"]["lid"] == "3" and t["study"]["prevboost"] == {"effectidx": -1}
        assert b["boosttype"] == "change" and b["effectidx"] == 1 and b["effect"] != t["effect"][1]["effect"]
        assert not call("/staff/study/3/complete", {"_method": "PUT"})["result"]
        st = hc._state(); [hc.staff_after_match(st) for _ in range(5)]; hc._save(st)
        got = call("/staff/study/3/complete", {"_method": "PUT"})
        assert got["result"] and got["staffs"][tgt["_id"]]["effect"][1]["effect"] == b["effect"] and staff(tgt["_id"])["status"] is None
        assert hc.staff_study_fee(staff(tgt["_id"]), "3") == 2 * sdb["study"]["3"]["fee"]  # the client's sum
        for _ in range(2):  # Europe: +1..3 on one boost, replacing the + the last one gave
            before, bonus = [e["amount"] for e in staff(tgt["_id"])["effect"]], staff(tgt["_id"])["study"].get("bonus")
            assert call("/staff/study/6", {"staffs": [tgt["_id"]], "_method": "PUT"})["result"]
            b = staff(tgt["_id"])["study"]["boost"]
            st = hc._state(); [hc.staff_after_match(st) for _ in range(10)]; hc._save(st)
            assert call("/staff/study/6/complete", {"_method": "PUT"})["result"]
            if bonus:
                before[bonus["effectidx"]] -= bonus["inc"]
            before[b["effectidx"]] += b["inc"]
            assert b["boosttype"] == "enhance" and [e["amount"] for e in staff(tgt["_id"])["effect"]] == before
        st = hc._state(); raw = st["teams"][slot]
        for k in ("staffs", "staffslots", "staffsmc", "staffrecruit"):
            raw.pop(k, None)
        hc.my_coach(st)["ep"] = ep_kept; hc._save(st)
        # --- Player Growth (user: "...fo3player_growth.php... apakah fitur ini bisa diaktifkan kembali"):
        # the feature, its table (an object; every stat reaches tiers without gaps), max_playerlevel, the
        # coach's tactic points, a max-level card's steps by the tiers within its limit, a reset item, the
        # points of a live match (none for a simulation), growth in the team rating and for the crowd
        feats = body(hc.ENDPOINTS["/account"]("", b""))
        feats = feats.get("features") or json.load(open(os.path.join(hc.RESPONSES, "account_base.json"), encoding="utf-8-sig"))["features"]
        assert "expansion" in feats["teammanagement"]["playermanagement"]
        gdb = body(hc.ENDPOINTS["/static/db/PLAYER_EXTRA_GROWTH"]("", b""))
        assert sorted(gdb["statgroups"]) == sorted(hc.GROWTH_STATS) and len(hc.GROWTH_STATS) == 33
        tiers = gdb["growth"]["1"]
        assert tiers[0]["min"] == 0 and all(a["max"] + 1 == b["min"] for a, b in zip(tiers, tiers[1:])) and tiers[-1]["max"] >= 9999
        assert all(isinstance(t[k], int) for t in tiers for k in ("min", "max", "require"))
        assert gdb["config"]["point"]["player_limit"] == 60 and all(r["name"].startswith("PGE_") for r in gdb["records"].values())
        assert body(hc.from_file("/static/db/STATICVALUES"))["max_playerlevel"] == hc.PLAYER_MAX_LEVEL
        st = hc._state(); team_ = st["teams"][slot]; coach_ = hc.my_coach(st)
        card = team_["players"][0]; card.pop("sp", None); kept_lv = card.get("levelinfo")
        card["levelinfo"] = {"level": hc.PLAYER_MAX_LEVEL, "exp": 0}
        coach_["growth"] = {"total": 50, "used": 0, "exp": 0}; hc._save(st)
        sp_me = lambda: hc.coach_me(hc._state(), hc._default_slot(hc._state()))["sp"]
        assert {k: sp_me()[k] for k in ("total", "used", "exp")} == {"total": 50, "used": 0, "exp": 0}
        k = min(hc.GROWTH_STATS, key=hc.growth_base(card["spid"]).get)  # his weakest stat: 4 points a step
        base = hc.growth_base(card["spid"])[k]
        want = sum(hc.growth_cost(base + hc.GROWTH_BASE + i) for i in range(3))
        got = call(f"/player/{hc.iid(card)}/extra/growth", {"stats": {k: 3}, "_method": "PUT"})
        mine = lambda: next(p for p in hc._state()["teams"][slot]["players"] if hc.iid(p) == hc.iid(card))
        assert got["usedsp"] == {"point": want, "detail": {k: 3}} and mine()["sp"] == {"used": want, "stats": {k: 3}}
        assert sp_me()["used"] == want and got["resources"][0]["uri"] == "/coach/me"
        got = call(f"/player/{hc.iid(card)}/extra/growth", {"stats": {k: 99}})  # only what the card's 60 / my 50 allow
        assert mine()["sp"]["used"] <= 50 and sp_me()["used"] == mine()["sp"]["used"] and got["usedsp"]["detail"]
        xi_id = next(str(s.get("id") or s["spid"]) for s in team_["squad"]["positions"] if s["pos"] >= 20)  # a forward
        st = hc._state(); fw = next(p for p in st["teams"][slot]["players"] if hc.iid(p) == xi_id)
        before = hc.xi_ratings(hc.owned_team(st["teams"][slot]))[xi_id]
        fw["sp"] = {"used": 30, "stats": {"finishing": 6, "positioning": 6}}
        assert hc.xi_ratings(hc.owned_team(st["teams"][slot]))[xi_id] > before  # growth in the team rating
        fw.pop("sp"); hc._save(st)
        low = team_["players"][1]  # a card below the max level takes no step
        assert call(f"/player/{hc.iid(low)}/extra/growth", {"stats": {"finishing": 1}})["usedsp"]["point"] == 0
        st = hc._state(); hc.inventory_add(hc.my_coach(st), 3003, 1); hc._save(st)
        reset = next(i for i in hc.my_coach(hc._state())["items"] if i["itemid"] == 3003)
        assert hc.coach_items(hc._state(), int(slot))  # the page looks for staticdata.type PLAYER_GROWTH_RESET
        assert body(hc.route(f"/item/{reset['_id']}/use")("", json.dumps({"id": 3003, "targetid": hc.iid(low)}).encode()))["result"]
        assert any(i["itemid"] == 3003 for i in hc.my_coach(hc._state())["items"])  # nothing to reset: kept
        assert body(hc.route(f"/item/{reset['_id']}/use")("", json.dumps({"id": 3003, "targetid": hc.iid(card)}).encode()))["result"]
        assert "sp" not in mine() and sp_me()["used"] == 0 and not any(i["itemid"] == 3003 for i in hc.my_coach(hc._state())["items"])
        st = hc._state()
        stats = {"myTeam": "home", "opponentTeam": "away", "home": {"score": 2, "assists": 1, "saves": 1, "passes": [30, 40]},
                 "away": {"score": 1}}
        r = hc.growth_after_match(st, stats)
        assert r["prev"] == {"total": 50, "exp": 0} and r["descriptions"]["win"] == {"count": 1, "exp": 300}
        assert r["descriptions"]["passes"] == {"count": 30, "exp": 60} and r["current"]["exp"] > 0
        assert hc.growth_after_match(st, stats, played=False)["descriptions"] == {}
        sim = hc.match_reward(copy.deepcopy(st), stats, live=False)["sp"]  # (a copy: it hands out a reward card)
        assert sim["current"] == sim["prev"] and sim["descriptions"] == {}
        hc.my_coach(st).pop("growth"); card["levelinfo"] = kept_lv
        st["teams"][slot]["players"][0]["levelinfo"] = kept_lv or {"level": 1, "exp": 0}; hc._save(st)
        vms = sorted(hc.virtual_managers(), key=lambda m: max(m["elo"]))  # the crowd grows its cards too
        used = lambda m: [p["sp"]["used"] for p in hc.vm_team(m["_id"])["players"] if p.get("sp")]
        # by rating (the weakest is no longer at LADDER_START once the crowd's ratings have moved for days)
        assert sum(used(vms[0])) < sum(used(vms[-1])) and all(0 < u <= 60 for u in used(vms[-1]))
        # --- shop: player packs for EP (1 or 10); Item saya opens one card per use
        shop = body(hc.ENDPOINTS["/shop/list"]("", b""))
        cats = Counter(e["shopcategory"] for e in shop)
        # packs without a card (the FO4 seasons before tools/fo4_season.py install) stay out
        assert not hc.pack_pool(92000300) or 92000300 in {e["shopid"] for e in shop}
        assert cats == {"PLAYERPACK": sum(1 for i in hc.PACKS if i not in hc.REWARD_PACKS and hc.pack_pool(i)), "USE": len(hc.ITEMS),
                        "PACKAGE": len(hc.PACKAGES) - len(hc.CHAIN_STEPS),
                        "MILEAGE": len(hc.MP_SHOP), "MONTHLYPACK": len(hc.MONTHLY), "CHAINED": len(hc.CHAINED)}
        assert all(b["price"] == 0 for e in shop for b in e["product"]["staticdata"]["bunches"])  # free Shop
        pid = 8223509  # MF NEW 70+ 2017
        bunches = next(e for e in shop if e["shopid"] == pid)["product"]["staticdata"]["bunches"]
        ep1 = ep()
        assert post("/shop/purchase", {"shopid": pid, "productid": pid, "bunch": 0})["result"] and ep() == ep1 - bunches[0]["price"]
        assert post("/shop/purchase", {"shopid": pid, "productid": pid, "bunch": 1})["result"] and ep() == ep1 - bunches[0]["price"] - bunches[1]["price"]
        items = lambda: [i for i in hc.coach_me(hc._state(), hc._default_slot(hc._state()))["items"] if i["itemid"] != hc.LR_TICKET]
        assert [(i["itemid"], i["remains"], i["staticdata"]["category"]) for i in items()] == [(pid, 11, "PLAYERPACK")]
        n0 = len(team()["players"])
        got = body(hc.route(f"/item/{items()[0]['_id']}/use")("", b"{}"))
        assert got["result"] and len(team()["players"]) == n0 + 1 and team()["players"][-1]["spid"] == got["player"]["spid"]
        with hc.closing(hc._db()) as c:
            pos = c.execute("SELECT preferredposition1 FROM players WHERE spid = ?", (got["player"]["spid"],)).fetchone()[0]
        # "70+" = the rating shown at +1 (base + 5, fifaaddict's enchant table)
        assert hc.card_ovr(got["player"]["spid"]) + hc.GRADE_BOOST[1] >= 70 and 9 <= pos <= 19 and got["player"]["spid"] // 1000000 == 88
        assert hc.GRADE_BOOST[:6] == (0, 5, 6, 7, 8, 10) and hc.player_ovr({"spid": 91190871, "grade": 5}) == 93  # WB Neymar +5
        assert items()[0]["remains"] == 10
        # client rating = Enums.calcPosOvr at preferredposition1 (fifaaddict shows the same):
        # Messi 2017 RW 80, Belotti 2017 ST 77, Andre Gomes 2017 CM 65 (fifadb column: 85/82/70)
        assert [hc.card_ovr(s) for s in (88158023, 88208596, 88211575)] == [80, 77, 65]
        assert list(body(hc.ENDPOINTS["/static/cache/item"]("", json.dumps({"itemids": [pid, "x", 9]}).encode()))) == [str(pid)]
        # USE items: buy (free), then "Gunakan" -> /item/<_id>/use per type; a package fills Item saya
        def use(itemid, req=None):
            it = next(i for i in hc.coach_me(hc._state(), hc._default_slot(hc._state()))["items"] if i["itemid"] == itemid)
            return body(hc.route(f"/item/{it['_id']}/use")("", json.dumps(req or {"id": it["_id"]}).encode()))
        assert post("/shop/purchase", {"shopid": 50021, "productid": 50021, "bunch": 0})["result"]
        ep1 = ep(); assert use(50021)["ep"] == 1_000_000 and ep() == ep1 + 1_000_000  # EP +1.000.000
        assert post("/shop/purchase", {"shopid": 501, "productid": 501, "bunch": 1})["result"]  # 10 Lucky EP
        assert all(10_000 <= use(501)["ep"] <= 1_000_000 for _ in range(10))
        assert not any(i["itemid"] == 501 for i in items())  # used up
        # issue #12: the random box screen (TeamManagementRandomCardScreen.SetItemBackground) shows
        # staticdata.availables, the prizes, as item tiles; without it every box crashed at "Gunakan"
        av = hc.item_def(50121)["availables"]
        assert [a["itemid"] for a in av] == [8334, 8194, 82214007, 45011, hc.EP_ITEM]
        assert av[-1]["count"] == 10_000_000 and av[-1]["staticdata"]["type"] == "EP" and av[0]["staticdata"]["name"]
        assert post("/shop/purchase", {"shopid": 50122, "productid": 50122, "bunch": 1})["result"]  # 10 Kotak Emas
        for _ in range(10):
            got = use(50122)
            assert got["result"] and (got.get("ep") == 3_000_000 or got["item"]["itemid"] in (82214180, 45012))
        assert post("/shop/purchase", {"shopid": hc.MP_SHOPID + 3002, "productid": 3002, "bunch": 0})["result"]  # MP Shop
        assert use(3002, {"name": "Juragan"})["result"] and hc.my_coach(hc._state())["name"] == "Juragan"
        assert post("/shop/purchase", {"shopid": 3001, "productid": 3001, "bunch": 0})["result"]
        club = hc.my_teamid(hc._state())
        assert use(3001, {"teamid": 10})["result"] and hc.my_teamid(hc._state()) == 10  # Ganti tim
        # Ganti tim's club info and 3D player (TeamChangeScreenSelectPage.onReceivedTeamInfo, issue #23)
        rm = body(hc.route("/static/info/team/summary/243")("", b""))
        assert rm["id"] == 243 and rm["summary"]["name"] == "Real Madrid" and rm["summary"]["league"]["id"] == 53
        assert rm["summary"]["rival"]["name"] and rm["stadium"]["name"] == "Santiago Bernabéu" and rm["stadium"]["capacity"] == 81044
        assert rm["kit"] == hc.team_kit(243)
        hc.my_coach(st := hc._state())["teamid"] = club; hc._save(st)
        n0 = {i["itemid"]: i["remains"] for i in items()}
        assert post("/shop/purchase", {"shopid": 50881, "productid": 50881, "bunch": 0})["result"]  # Kotak World Best
        n1 = {i["itemid"]: i["remains"] for i in items()}
        assert all(n1[i] - n0.get(i, 0) == n for i, n in hc.PACKAGES[50881][2]) and 50881 not in n1
        # Gudang (issue #12): whole stacks Item saya -> Gudang and back; a moved stack joins one of the same item
        depot = lambda: body(hc.ENDPOINTS["/item/depot"]("", b""))
        assert depot() == []
        wb = next(i for i in items() if i["itemid"] == 8334)
        got = post("/items/itemtodepot", {"moveitems": [wb["id"]]})
        assert got["result"] and [r["uri"] for r in got["resources"]] == ["/coach/me", "/item/depot"]
        assert [(i["_id"], i["remains"], i["staticdata"]["category"]) for i in depot()] == [(wb["_id"], wb["remains"], "PLAYERPACK")]
        assert 8334 not in {i["itemid"] for i in items()}
        assert not body(hc.route(f"/item/{wb['_id']}/use")("", b"{}"))["result"]  # not opened from the Gudang
        assert not post("/items/itemtodepot", {"moveitems": [wb["id"]]})["result"]  # already there
        some = items()[0]
        assert not post("/items/itemtodepot", {"moveitems": [some["id"], "nope"]})["result"] and items()[0] == some  # all or nothing
        assert not post("/items/itemtodepot", {})["result"]
        st = hc._state(); hc.inventory_add(hc.my_coach(st), 8334, 1); hc._save(st)
        again = next(i for i in items() if i["itemid"] == 8334)
        assert post("/items/itemtodepot", {"moveitems": [again["id"]]})["result"]
        assert [(i["_id"], i["remains"]) for i in depot()] == [(wb["_id"], wb["remains"] + 1)]
        assert body(hc.route(f"/item/depottoitem/{wb['_id']}")("", b"{}"))["result"]
        assert depot() == [] and {i["itemid"]: i["remains"] for i in items()} == {**n1, 8334: n1[8334] + 1}
        assert not body(hc.route(f"/item/depottoitem/{wb['_id']}")("", b"{}"))["result"]  # unknown id refused
        # Paket Bulanan: items now + one daily Bag entry per day; Serial product steps; Special Bonus
        ep1 = ep(); assert post("/shop/purchase", {"shopid": 81000001, "productid": 81000001, "bunch": 0})["result"]
        assert [m["productid"] for m in body(hc.ENDPOINTS["/monthlypack"]("", b""))] == [81000001]
        hc.my_coach(st := hc._state())["bag"] = []; hc._save(st)
        assert body(hc.ENDPOINTS["/monthlypack"]("", b"{}"))["result"]
        bag = body(hc.ENDPOINTS["/bag"]("", b""))
        assert [(b["type"], b["items"]) for b in bag] == [("monthlypack", [{"itemid": hc.EP_ITEM, "count": 200_000}])]
        hc.ENDPOINTS["/monthlypack"]("", b"{}"); assert len(body(hc.ENDPOINTS["/bag"]("", b""))) == 1  # once a day
        hc.my_coach(st := hc._state())["bag"] = []; hc._save(st)
        chain = lambda: next(e for e in body(hc.ENDPOINTS["/shop/list"]("", b"")) if e["shopcategory"] == "CHAINED")
        assert chain()["step"] == 0 and [c["shopid"] for c in chain()["chained"]] == [70000002, 70000003]
        assert post("/shop/purchase", {"shopid": 70000001, "productid": 70000001, "bunch": 0})["result"]
        assert chain()["step"] == 1 and chain()["remainedperaccount"] == 0
        targets = body(hc.ENDPOINTS["/specifictarget"]("", b""))
        special = body(hc.ENDPOINTS["/shop/list/STSHOP"]("", b""))
        assert targets["issell"] and {t["stid"] for t in targets["targets"]} == {e["stid"] for e in special}
        assert post("/shop/purchase", {"shopid": special[0]["shopid"], "productid": special[0]["product"]["itemid"], "bunch": 0})["result"]
        # "+N" packs give grade-N cards; 06-11 packs draw only from seasons 2006..2011
        assert len(hc.pack_pool(45007)) == 50 and {s // 1000000 for s, _ in hc.pack_pool(45007)} <= set(range(6, 12))
        wb = hc.pack_pool(82231307)  # one odd cheap card must not win most draws (it once won 80%)
        assert max(w for _, w in wb) / sum(w for _, w in wb) < 0.2
        gp = 15031  # '14 Top 30 +5
        assert hc.pack_price(gp) > 5 * sum(hc.card_price(s) * w for s, w in hc.pack_pool(gp)) / sum(w for _, w in hc.pack_pool(gp))
        assert hc.pack_grades(gp) == [(1, 5), (2, 4), (3, 3), (4, 2), (5, 1)] and hc.pack_grades(28007) == [(1, 1)]
        # the Market's name search (TransferUtil: players.spid / 1000000 IN (availableSeasons))
        # only sees seasons listed in /static/roster: every season with Market cards is there
        # (user: "tidak ada r. gullid di world legend" -- WL and 15 more were missing)
        # issue #14: the Market filter's season combo hands value to TransferUtil.seasonQuery(Array)
        # ("cannot convert 39 to Array"); labels starting with $ are the client's season names
        seasons = body(hc.from_file("/static/roster"))["seasons"]
        assert all(isinstance(e["value"], list) for e in seasons) and seasons[23]["season"] == "$TRANSFERMARKET_SEASON_39"
        roster = {c for e in seasons for c in e["value"]}
        market = hc.market_spids()
        assert {s // 1000000 for s in market} <= roster, sorted({s // 1000000 for s in market} - roster)
        # FO3's untradeable event cards are on the Market too (user: "session special tidak ada dimarket
        # pack dan trading sepeti cafu"): Cafu's season 26 legend, his EL and CEL cards; 26 in the search
        assert all(hc.on_market(s) for s in (26005003, 27005003, 31005003)) and 26 in roster
        assert [e["season"] for e in seasons if e["value"] == [32]] == ["$TRANSFERMARKET_SEASON_32"]
        # season 0 = the engine cards the AI clubs of Liga / Cup play with ('17 badge, Messi 125): not a
        # Market season (user: "pemain ini harusnya tidak muncul di market karena khusus untuk main
        # cup/liga"); "default" is the client's own DEFAULT_SEASON_ID 88 ('17 cards), listed once
        assert 0 not in roster and [e["value"] for e in seasons if e["season"] == "default"] == [[88]]
        assert sorted(c for e in seasons for c in e["value"]) == sorted(roster)  # no season twice
        # WC18 (tools/fo4_season.py wc18 + build): the 2018 World Cup's best 300 squad players; at +1 a
        # card shows its World Cup rating - 5 (user: "grade 1 = nilai akhir turnamen dikurangi 5")
        with hc.closing(hc._db()) as c:
            wc18 = dict(c.execute("SELECT spid, overallrating FROM players WHERE spid / 1000000 = 78"))
        fetched_json = os.path.join(HERE, "gamedata", "wc18_cards.json")  # absent where the DB came from a patched client
        if wc18 and os.path.exists(fetched_json):
            fetched = json.load(open(fetched_json, encoding="utf-8"))
            assert len(wc18) == len(fetched) == 300
            kept = [(78000000 + int(e), w["rating"]) for e, w in fetched.items() if 78000000 + int(e) in wc18]
            assert len(kept) >= 290  # players FO3 has keep their (EA's) id; the others get new ones
            assert all(wc18[s] == r - 5 and hc.card_ovr(s) == r - 10 for s, r in kept), \
                [(s, r, wc18[s], hc.card_ovr(s)) for s, r in kept if (wc18[s], hc.card_ovr(s)) != (r - 5, r - 10)][:5]
            assert 78 in roster and hc.tc_season_of(78) == hc.TC_SEASON + 78
            assert len(hc.pack_pool(92000078)) == 200 and {s // 1000000 for s, _ in hc.pack_pool(92000078)} == {78}
            # its team colors (user: "Tambah tem color wc18"): the season's, named as FO3's 2014 one, and
            # one per nation as the "14 WC <nation>" ones, counting that nation's WC18 cards
            colors18 = {c["teamcolorid"]: c for c in hc.tc_colors()}
            assert colors18[hc.TC_SEASON + 78]["name"] == "2018 WC All-Star"
            assert colors18[hc.TC_SEASON + 78]["boostgroups"][2]["boosts"] == [{"type": "ovr", "amount": 3}, {"type": "reactions", "amount": 1}]
            fra = hc.TC_WC18 + 18
            assert colors18[fra]["name"] == "18 WC Perancis" and colors18[fra]["description"] == "Pemain WC18 dari negara ini"
            assert hc.tc_special_sets()[fra][1] == sum(w["nation"] == 18 for w in fetched.values()) >= 5
            # Karakteristik: FO3's own bits stay, FIFA 18's are added (user: "traits tiap pemain jangan lupa")
            with hc.closing(hc._db()) as c:
                t1 = dict(c.execute("SELECT spid, trait1 FROM players WHERE spid IN (88158023, 78158023)"))
            assert t1[78158023] & t1[88158023] == t1[88158023] and t1[78158023]  # Messi: finesse shot, playmaker ...
        # every card the client itself can trade (its Market search: permid.transferable = 1 and a
        # name) is on the Market, in the trades and in a pack (user: "jangan sampai ada pemain yang
        # tidak ada di pack, trade, dan market"); fifaaddict alone left out 6,599, 4,313 of them '17
        with hc.closing(hc._db()) as c:
            tradeable ={s for (s,) in c.execute("""SELECT p.spid FROM players p JOIN permid m ON m.spid = p.spid
                AND m.transferable = 1 WHERE p.spid >= 1000000 AND p.playerid IN (SELECT playerid FROM namesind)""")}
        assert tradeable <= market and set(hc.fa_prices()) <= market
        assert {s for rows in hc.trade_pool().values() for s, _ in rows} == market
        assert {s for s, _ in hc.pack_pool(hc.ALL_PLAYERS_PACK)} == market
        # prices follow the rating only (user: "fix harga yang tidak sesua statsnya")
        assert all(hc.stat_price(o) < hc.stat_price(o + 1) for o in range(40, 95))
        # All Season Top 300 +8: every Market card rated 81+ of any season at +1..+8; season packs = their top 200
        assert [g for g, _ in hc.pack_grades(7675)] == list(range(1, 9))
        best = max(hc.card_ovr(s) for s in hc.market_spids())
        rated = [hc.card_ovr(s) for s, _ in hc.pack_pool(7675)]
        assert len(rated) > 300 and min(rated) == 81 and max(rated) == best
        assert len(hc.pack_pool(92000088)) == 200 and {s // 1000000 for s, _ in hc.pack_pool(92000088)} == {88}
        st = hc._state(); hc.my_coach(st)["ep"] = 10 ** 10; hc._save(st)
        assert post("/shop/purchase", {"shopid": gp, "productid": gp, "bunch": 1})["result"]  # 10 packs
        opened = [body(hc.route(f"/item/{next(i for i in items() if i['itemid'] == gp)['_id']}/use")("", b"{}"))["player"]
                  for _ in range(10)]
        assert all(1 <= p["grade"] <= 5 and p["spid"] // 1000000 == 14 and p["price"] == hc.card_price(p["spid"], p["grade"])
                   for p in opened) and len({p["grade"] for p in opened}) > 1  # a random grade +1..+5
        assert team()["players"][-1]["grade"] == opened[-1]["grade"]
        # issue #27: draft packs ("Pilih pak pemain", TeamManagementDraftPackScreen) and EXP capsules
        dp = 200005  # 2017 Draft Pick 5
        assert hc.item_def(dp)["category"] == "DRAFTPACK" and hc.item_def(dp)["draft"] == {"season": 88, "grade": 1, "availables": 5}
        for d in (8223735, 200005, 97093):  # their own icons ship with the server (assets/)
            icon = hc.item_def(d)["itemicon"]
            assert icon == hc.item_def(d)["bundleicon"] and hc.asset_path(f"externalAssets/shop/{icon}.png").startswith(hc.OWN_ASSETS)
        small = os.path.join(hc.ASSETS, "externalAssets", "crests", "small", "s11.png")  # issue #29: Man United
        if os.path.isfile(small) and not os.path.isfile(small.replace("small", "large")):  # has no large crest
            assert hc.asset_path("externalAssets/crests/large/s11.png") == small
        top5 = body(hc.route(f"/item/draftpack/{dp}/players/5")("", b""))  # the default picks: the 5 most valuable
        assert len(top5) == 5 and all(p["spid"] // 1000000 == 88 and p["price"] == hc.card_price(p["spid"]) for p in top5)
        assert [p["price"] for p in top5] == sorted((p["price"] for p in top5), reverse=True)
        assert len(body(hc.route(f"/item/draftpack/{dp}/players/100")("", b""))) == 100  # the search list
        assert body(hc.route("/item/draftpack/28007/players/5")("", b"")) == []  # not a draft pack
        priced = body(hc.route("/transfer/price/default/3")("", json.dumps({"spids": [88158023, 1]}).encode()))
        assert priced == {"prices": [{"spid": 88158023, "price": hc.card_price(88158023, 3)}]}
        assert post("/shop/purchase", {"shopid": dp, "productid": dp, "bunch": 0})["result"]
        picks = [p["spid"] for p in top5]
        assert not use(dp, {"spids": picks[:4]})["result"]  # all 5 picks
        assert not use(dp, {"spids": picks[:4] + [91190871]})["result"]  # a World Best card is no 2017 pick
        got = use(dp, {"spids": picks})
        assert got["result"] and got["player"]["spid"] in picks and got["player"]["grade"] == 1
        assert not any(i["itemid"] == dp for i in items())  # used up
        # EXP capsule (PLAYER_EXP_CAPSULE): /item/<_id>/use {targetid} gives that player its EXP
        assert {k: hc.item_def(6004).get(k) for k in ("type", "capsuleexp", "capsulelv", "capsuleseason")} == {
            "type": "PLAYER_EXP_CAPSULE", "capsuleexp": 3000, "capsulelv": 10, "capsuleseason": None}
        for i in (6004, 6005, 6003, 6001):
            assert post("/shop/purchase", {"shopid": i, "productid": i, "bunch": 0})["result"]
        lv = lambda pid: hc.player_levelinfo(next(x for x in team()["players"] if hc.iid(x) == pid))
        assert lv(got["player"]["_id"]) == {"level": 1, "exp": 0, "to": 100}
        assert not use(6004, {"targetid": "nope"})["result"]
        assert use(6004, {"targetid": got["player"]["_id"]})["result"] and lv(got["player"]["_id"]) == {"level": 8, "exp": 200, "to": 800}
        assert not use(6005, {"targetid": opened[0]["_id"]})["result"]  # a '14 card, the capsule is for 2017 ones
        assert use(6005, {"targetid": got["player"]["_id"]})["result"] and lv(got["player"]["_id"]) == {"level": 16, "exp": 1000, "to": 1600}
        assert post("/shop/purchase", {"shopid": 6004, "productid": 6004, "bunch": 0})["result"]
        assert not use(6004, {"targetid": got["player"]["_id"]})["result"]  # above LV.10
        assert use(6003, {"targetid": opened[0]["_id"]})["result"] and lv(opened[0]["_id"])["level"] == hc.PLAYER_MAX_LEVEL
        assert not use(6001, {"targetid": opened[0]["_id"]})["result"]  # already at the max level
        # issue #36: clubs (Komunitas > Klub). Founding: the reply feeds /club/me with a string _id
        # and member.grade (Konfirmasi waits for that) and /coach/me with club.clubid; AI clubs to
        # browse and join; managers asking to join mine; members' chat, the board, the records.
        timers, real_timer, real_status, real_reply = [], hc.threading.Timer, hc.vm_status, hc.CLUB_REPLY
        hc.threading.Timer, hc.vm_status, hc.CLUB_REPLY = Timer, lambda vm, hour=None: "online", (1, 0)
        q = lambda path, method="GET", **req: (hc.REQUEST.update(method=method),
                                               body((hc.ENDPOINTS.get(path) or hc.route(path))("", json.dumps(req).encode())))[1]
        mine = lambda r: next(x["body"] for x in r["resources"] if x["uri"] == "/club/me")
        snapshot = copy.deepcopy(hc._state())  # put back after the club, friendly and King of the Hill blocks
        st = hc._state(); ep_kept, bag_kept = hc.my_coach(st).get("ep", hc.START_EP), list(hc.my_coach(st).get("bag", []))
        try:
            st = hc._state(); st.pop("club", None); hc.my_coach(st)["ep"] = hc.CLUB_FEE + 500; hc._save(st)
            assert q("/club/me") == {} and hc.coach_me(st, hc._default_slot(st))["club"] == {"clubid": None}
            assert q("/club/fee/creation")["fee"] == hc.CLUB_FEE
            assert q("/club/create", "POST", name="ab", description="") == {"result": False, "reason": "invalid name"}
            ai_names = [c["name"] for c in hc.ai_clubs_now().values()]
            assert len(set(ai_names)) == len(ai_names) > 40
            assert q("/club/create", "POST", name=ai_names[0], description="")["reason"] == "name is already used"
            r = q("/club/create", "POST", name="Klub Uji", description="Ayo main")
            club = mine(r)
            coach_res = next(x["body"] for x in r["resources"] if x["uri"] == "/coach/me")
            assert r["result"] and isinstance(club["_id"], str) and club["member"]["grade"] == 10 and club["rewards"]
            assert coach_res["club"]["clubid"] == club["_id"] == r["clubid"] and coach_res["money"]["ep"] == 500
            assert len(club["member"]["rewards"]) == 7 and club["levelinfo"]["level"] == 1 and club["membercount"] == 1
            assert q("/club/create", "POST", name="Klub Lain", description="")["reason"] == "already have a club"
            assert hc._state()["coaches"][0]["bag"][-1]["title"] == "Hadiah klub harian"  # founding passes the day's goal
            # AI clubs: the list (mine in it, with my grade), a club's page
            found = body(hc.ENDPOINTS["/club/find"]("keyword=&sort=level&order=-1&allowjoin=false&skipjudgement=false&membernumfrom=0&membernumto=0", b""))
            assert found["count"] == len(found["clubs"]) == len(ai_names) + 1
            assert all(isinstance(c["_id"], str) and c["membercount"] >= 1 and c["mastername"] for c in found["clubs"])
            assert next(c for c in found["clubs"] if c["_id"] == club["_id"])["member"]["grade"] == 10
            ai = next(c for c in found["clubs"] if c["_id"] != club["_id"] and c["allowjoin"])
            assert all(c["membercount"] < c["limits"]["members"] for c in found["clubs"])  # an AI club always has a seat
            assert q(f"/club/{ai['_id']}")["name"] == ai["name"] and q("/club/nope") == {"result": False, "reason": "deleted club"}
            # managers ask to join (Manajemen > permohonan): one taken in, one turned down
            st = hc._state(); st["club"]["next"] = time.time() - 4 * hc.CLUB_ASK; hc._save(st)
            asks = q("/club/me/join/requests")
            assert len(asks) >= 2 and all(a["name"] and set(a["ovr"]) == {"FW", "MF", "DF"} and a["division"].startswith("DIVISION_") for a in asks)
            n0 = len(pushed)
            assert mine(q(f"/club/me/join/{asks[0]['id']}", "PUT", memberid=asks[0]["id"]))["membercount"] == 2
            assert q(f"/club/me/join/{asks[1]['id']}", "DELETE")["result"]
            assert [a["id"] for a in q("/club/me/join/requests")] == [a["id"] for a in asks[2:]]
            members = q("/club/me/members")
            assert [m["grade"] for m in members] == [10, 1] and members[1]["coachid"] == asks[0]["id"] and members[1]["online"]
            while timers:
                fn, args = timers.pop(0); fn(*args)
            lines = json.load(open(os.path.join(hc.RESPONSES, "vm_club_chat.json"), encoding="utf-8"))["id"]
            said = [b for u, b in pushed[n0:] if u == "/club/me/message"]
            assert said and said[0]["coachname"] == members[1]["name"] and said[0]["message"] in [s for v in lines["joined"].values() for s in v]
            # chat: my line comes back to me, a member answers a greeting
            n0 = len(pushed)
            assert q("/club/me/message", "POST", message="Halo semua", hint=None)["result"]
            notime = lambda b: {k: v for k, v in b.items() if k != "time"}  # each line's clock (TextArea): when said
            assert pushed[n0][1]["time"].endswith(".000Z") and (pushed[n0][0], notime(pushed[n0][1])) == ("/club/me/message", {
                "type": "chat", "coachid": hc.my_coach(hc._state())["_id"], "coachname": hc.my_coach(hc._state())["name"],
                "message": "Halo semua", "hint": None})
            while timers:
                fn, args = timers.pop(0); fn(*args)
            assert pushed[n0 + 1][1]["message"] in [s for v in lines["hello"].values() for s in v]
            # global chat: my line comes back to me, an online manager answers (no line about a club);
            # any channel 1-99
            kept_reply, hc.CHANNEL_REPLY = hc.CHANNEL_REPLY, (1, 0)
            n0 = len(pushed)
            assert body(hc.ENDPOINTS["/chat/my/channel/message"]("", json.dumps({"message": "Halo semua", "hint": None}).encode()))["result"]
            me_ = hc.my_coach(hc._state())
            assert (pushed[n0][0], notime(pushed[n0][1])) == ("/chat/my/channel/messages/latest", {
                "type": "chat", "coachid": me_["_id"], "coachname": me_["name"], "message": "Halo semua", "hint": None})
            while timers:
                fn, args = timers.pop(0); fn(*args)
            uri, said = pushed[n0 + 1]
            assert uri == "/chat/my/channel/messages/latest" and said["message"] in [s for v in lines["hello"].values() for s in v]
            assert "klub" not in said["message"].lower() and said["coachid"] in {vm["_id"] for vm in hc.channel_online()} and said["time"]
            hc.CHANNEL_REPLY = kept_reply
            assert body(hc.route("/chat/channel/7")("", b"{}")) == {"result": True, "channelid": 7, "created": False, "reason": ""}
            assert not body(hc.route("/chat/channel/100")("", b"{}"))["result"]
            assert body(hc.ENDPOINTS["/chat/channel"]("", json.dumps({"channelid": 3}).encode()))["channelid"] == 3
            assert body(hc.route("/chat/coach/x/channel/detail")("", b""))["result"]
            assert "setcontroller" in body(hc.ep_account("", b""))["guides"]  # else the lobby has no chat window
            # the day's football in the channel: the region's trends about football first, then the news
            # headlines (" - Source" cut, no repeats); a manager posts one, others answer
            trends = b"""<rss xmlns:ht="https://trends.google.com/trending/rss"><channel>
              <item><title>bitcoin</title><ht:news_item><ht:news_item_title>Harga Bitcoin naik</ht:news_item_title></ht:news_item></item>
              <item><title>persib</title><ht:news_item><ht:news_item_title>Persib menang di Liga 1 &amp; puncak</ht:news_item_title>
                <ht:news_item_source>Bola.net</ht:news_item_source></ht:news_item></item></channel></rss>"""
            gnews = b"""<rss><channel><item><title>Timnas Indonesia lolos - Kompas.com</title><source>Kompas.com</source></item>
              <item><title>Persib menang di Liga 1 &amp; puncak - Detik</title><source>Detik</source></item>
              <item><title>Pemain ditangkap polisi - X</title><source>X</source></item></channel></rss>"""  # no small talk about crime
            got = hc.news_fetch("ID", get=lambda u: trends if "trends.google" in u else gnews)
            assert got == [{"title": "Persib menang di Liga 1 & puncak", "source": "Bola.net", "trend": "persib"},
                           {"title": "Timnas Indonesia lolos", "source": "Kompas.com", "trend": ""}]
            kept_news = dict(hc.NEWS)
            hc.NEWS.update(key=((), "x"), at=time.time(), items=[{**i, "region": "ID"} for i in got]
                           + [{"title": "Haaland scores again", "source": "BBC", "trend": "", "region": ""}])
            online = hc.channel_online()[:3]
            talk = hc.channel_news(online, random.Random(1))
            assert talk and talk[0][0] is online[0] and any(i["title"] in talk[0][1] for i in hc.NEWS["items"]) and talk[0][2] == 0
            assert all(vm in online[1:] and d > 0 for vm, _, d in talk[1:])
            kinds = {hc.channel_news(online, random.Random(s))[0][1].split('"')[0] for s in range(40)}
            assert len(kinds) > 2  # the country's lines, the general ones, ...
            assert ("country", "Timnas Indonesia", {"timnas", "indonesia"}) in hc.vm_likes(online[0], ["ID"])
            assert all(k != "country" for k, _, _ in hc.vm_likes(online[0], []))  # no country ticked: the global only
            hc.NEWS.clear(); hc.NEWS.update(kept_news)
            assert hc.channel_news(online) == [] or hc.NEWS["items"]  # no topics: nothing to say
            # the board, the news, the records, the friends ranking's club tab, the chat's member list
            assert q("/club/me/post/", "POST", msg="Latihan malam ini")["result"]
            posts = q("/club/me/posts")
            assert posts[0]["message"] == "Latihan malam ini"
            q(f"/club/me/post/{posts[0]['_id']}", "DELETE")
            assert all(p["message"] != "Latihan malam ini" for p in q("/club/me/posts"))
            assert mine(q("/club/me/settings/news", "POST", message="Kumpul jam 8"))["news"]["message"] == "Kumpul jam 8"
            assert mine(q("/club/me/settings/allowjoin/false", "POST"))["allowjoin"] is False
            assert len(q("/club/record/members")["members"]) == 2 and q("/club/record/leaderboard")["leaderboardlist"]
            assert [m["_id"] for m in q("/contact/club")["members"]] == [asks[0]["id"]] and len(q("/rank/teamvalue/club")) == 2
            # this week's club points give my matches their boosts
            st = hc._state(); assert hc.club_boosts(st) == {}
            st["club"]["me"]["days"][str(hc.club_day())]["LEAGUE"] = 300; st["club"]["me"]["total"] += 300
            assert hc.club_boosts(st) == {"MATCH_PLUS_COACHEXP": 0.05}
            # closing (DELETE /club/me): inactive until its time, cancel undoes it; then gone
            assert mine(q("/club/me", "DELETE"))["state"] == "inactive"
            assert mine(q("/club/me/delete/cancel", "POST"))["state"] == "active"
            q("/club/me", "DELETE"); st = hc._state(); st["club"]["deleteat"] = time.time() - 1; hc._save(st)
            assert q("/club/me") == {} and hc._state()["club"] is None
            # an AI club takes me at once; then I leave it
            r = q(f"/club/{ai['_id']}/join", "POST")
            assert r["result"] and mine(r)["_id"] == ai["_id"] and mine(r)["member"]["grade"] == 1 and mine(r)["membercount"] == ai["membercount"] + 1
            assert q("/club/me/members")[-1]["grade"] == 1 and q(f"/club/{ai['_id']}/join", "POST")["reason"] == "already have a club"
            assert q("/club/me/member/leave", "PUT")["result"] and hc._state()["club"] is None
            # joining and leaving again pays the day's club reward no second time
            paid = lambda: sum(b["title"] == "Hadiah klub harian" for b in hc.my_coach(hc._state())["bag"])
            n = paid(); q(f"/club/{ai['_id']}/join", "POST"); q("/club/me/member/leave", "PUT")
            assert paid() == n == 1
        finally:
            hc.threading.Timer, hc.vm_status, hc.CLUB_REPLY = real_timer, real_status, real_reply
            hc.REQUEST["method"] = "GET"
            timers.clear()
            st = hc._state(); st.pop("club", None); hc.my_coach(st).update(ep=ep_kept, bag=bag_kept); hc._save(st)
        # issue #36: Multiple > Persahabatan against AI. The feature shows its buttons; Quick match
        # finds a virtual manager (Friendly_PvP_1v1 on the Rank path), vs COM plays the AI picker's
        # club, an invited friend joins and plays; results are friendlies, not Rank games
        assert set(body(hc.ep_account("", b""))["features"]["friendlymatch"]) == {"quickmatch", "randommatch", "custommatch"}
        timers, real_timer = [], hc.threading.Timer
        hc.threading.Timer = Timer

        def fire():
            while timers:
                fn, args = timers.pop(0); fn(*args)

        def end(gf, ga):
            m = hc._state()["match"]
            req = {"home": {"id": m["side"]["left"]["team"]["teamid"], "score": gf},
                   "away": {"id": m["side"]["right"]["team"]["teamid"], "score": ga}, "myTeam": "home", "opponentTeam": "away"}
            assert body(hc.route(f"/match/{m['id']}/end")("", json.dumps(req).encode()))["result"]
        new_room = lambda t: body(hc.ENDPOINTS["/room"]("", json.dumps({"type": t, "slotsize": 1}).encode()))["resources"][0]["body"]
        rq = lambda rid, path, **req: body(hc.route(f"/room/{rid}/{path}")("", json.dumps(req).encode()))
        try:
            st = hc._state(); elo0, games0 = hc.my_ladder(st)["elo"][0], sum(hc.my_ladder(st)["games"])
            room = new_room("pvp_random")
            assert "ladder" not in room and room["AI"]["index"]["difficulty"] == 2
            rq(room["roomid"], "matchmaking"); fire()
            st = hc._state()
            assert st["match"]["type"] == "Friendly_PvP_1v1" and st["ladder_pending"]["record"] == "quick"
            assert st["room"]["side"]["right"]["players"][0]["summary"]["ovr"]  # the room slot reads it
            end(2, 1)
            st = hc._state()
            assert (hc.my_ladder(st)["elo"][0], sum(hc.my_ladder(st)["games"])) == (elo0, games0) and "ladder_pending" not in st
            fs = hc.coach_me(st, hc._default_slot(st))["statistics"]["friendly"]
            assert fs["quick"]["wins"][0] == 1 and fs["quick"]["winningstreak"]["max"] == [1] and fs["vscom"]["wins"] == [0] * 5
            assert hc.my_coach(st)["multiplay"][-1]["type"] == "Friendly_PvP_1v1"
            # a custom room vs COM: the AI picker's club and difficulty, Mulai starts it (a Ready taken
            # back within its second does not)
            room = new_room("pvp_referee"); rid = room["roomid"]
            assert rq(rid, "type", type="pve_referee")["resources"][0]["body"]["type"] == "pve_referee"
            assert rq(rid, "AI", index={"nation": 1, "league": 0, "team": 2, "difficulty": 4})["result"]
            rq(rid, "side/left/ready", ready=True); rq(rid, "side/left/ready", ready=False); fire()
            assert hc._state()["room"]["stage"] == "room" and "ladder_pending" not in hc._state()
            rq(rid, "side/left/ready", ready=True); fire()
            st = hc._state(); m = st["match"]
            assert m["type"] == "Friendly_PvE_1v1" and m["gamesettings"]["difficulty"] == 4 and st["ladder_pending"]["record"] == "vscom"
            assert m["side"]["right"]["team"]["teamid"] == st["ladder_pending"]["teamid"] == hc.room_ai_team(st["room"]) > 0
            end(0, 0)
            assert hc.coach_record(hc.my_coach(hc._state()), "friendly")["vscom"]["draws"][0] == 1
            # a friend invited from the list joins ready; my Ready plays him
            friend = hc.vm_friends()[0]
            rq(rid, "type", type="pvp_referee")
            assert rq(rid, f"invite/{friend['_id']}")["result"]
            fire()
            assert hc._state()["room"]["side"]["right"]["players"][0]["id"] == friend["_id"]
            rq(rid, "side/left/ready", ready=True); fire()
            st = hc._state()
            assert st["match"]["type"] == "Friendly_PvP_1v1" and st["ladder_pending"]["opponent"] == friend["_id"]
            end(1, 3)
            assert hc.coach_record(hc.my_coach(hc._state()), "friendly")["custom"]["losses"][0] == 1
            assert rq(rid, "slotsize", slotsize=2)["result"]  # the room's other controls: as it is
            left = rq(rid, "leave", targetid="", byWhom="")
            assert left["resources"][0]["body"]["side"]["left"]["players"] == [] and "room" not in hc._state()
            # King of the Hill (the Esports Regional League): the feature, the division table preloaded,
            # this month's season with its region tree, a region picked, its board, a match there
            assert "regionalleague" in body(hc.ep_account("", b""))["features"]["esports"]
            init = body(hc.ENDPOINTS["/static/initialize"]("", b""))["resources"][0]
            assert init["uri"] == "/static/db/ESPORTS2_BALANCING_DEFAULT" and init["body"]["divisions"] == hc.ladder_divisions()
            pres = body(hc.ENDPOINTS["/esports2/region/seasons/presently"]("", b""))
            cur = pres["seasons"][0]
            assert cur["schedule"]["startdt"] <= time.strftime("%Y%m%d") < cur["schedule"]["enddt"] == pres["seasons"][1]["schedule"]["startdt"]
            assert cur["_id"] == str(hc.koth_sid()) and cur["schedule"]["midreward"] == hc.KOTH_MIDREWARD and len(cur["schedule"]["week"]) == 7
            assert len(cur["reward"]["week"]["grade"]) == len(cur["reward"]["season"]["grade"]["1"]) == len(hc.ladder_divisions())
            tree = pres["regiongrouptrees"][cur["regiongroup"]["id"]]
            assert set(tree) == {m for _, m, _ in hc.KOTH_REGIONS}  # keyed by name: the pickers list the keys
            leaf = next(iter(next(iter(tree.values()))["children"].values()))
            assert leaf["parent"] in tree and leaf["name"] in tree[leaf["parent"]]["children"]
            koth = lambda: hc.coach_me(hc._state(), hc._default_slot(hc._state()))["statistics"]["esports2"]
            assert "regionid" not in koth() and koth()["seasonid"] == "" and koth()["rewarded"] is True
            assert koth()["midrewarded"] == [True] * len(hc.KOTH_MIDREWARD) and koth()["introid"] == ""
            assert body(hc.route(f"/esports2/region/register/{cur['_id']}")("", b""))["registeredplayer"][leaf["id"]] > 0
            assert not body(hc.ENDPOINTS["/esports2/region/register"]("", json.dumps({"regiongroupid": "nope"}).encode()))["result"]
            assert body(hc.ENDPOINTS["/esports2/region/register"]("", json.dumps({"regiongroupid": leaf["id"]}).encode()))["result"]
            body(hc.ENDPOINTS["/esports2/region/intro"]("", json.dumps({"_method": "PUT", "seasonid": cur["_id"]}).encode()))
            assert koth()["regionid"] == leaf["id"] and koth()["regionname"]["parent"] and koth()["introid"] == cur["_id"] and koth()["rank"] >= 1
            assert koth()["seasonid"] == cur["_id"]  # registered for this season
            board = body(hc.route(f"/esports2/region/leaderboard/{leaf['id']}")("", b""))
            assert any(x["cid"] == hc.my_coach(hc._state())["_id"] for x in board) and len(board) > 10
            assert all(hc.vm_region(x["cid"]) == leaf["id"] for x in board if x["cid"].startswith("vm"))
            major = tree[leaf["parent"]]["id"]  # a major region's board holds all of its own
            assert len(body(hc.route(f"/esports2/region/leaderboard/{major}")("", b""))) >= len(board)
            room = new_room("esports2"); rq(room["roomid"], "matchmaking"); fire()
            st = hc._state(); m = st["match"]
            assert m["type"] == "Regional_League" and hc.vm_region(st["ladder_pending"]["opponent"]) == leaf["id"]
            assert m["side"]["left"]["players"][0]["matchstats"] == [{"type": "Regional_League", "elorating": koth()["elo"]}]
            assert all(c["es2stamina"] == 1 for s_ in m["side"].values() for c in s_["team"]["players"])
            assert m["gamesettings"]["drawMode"] == 1  # extra time and penalties, as in Rank
            elo0, lad0 = koth()["elo"], copy.deepcopy(hc.my_ladder(st))
            end(3, 0)
            st = hc._state(); rw = st["match"]["reward"]
            assert koth()["elo"] > elo0 == rw["preveloinfo"]["elo"] and rw["eloinfo"]["elo"] == koth()["elo"] and koth()["wins"] == 1
            assert hc.my_ladder(st) == lad0 and hc.my_coach(st)["multiplay"][-1]["type"] == "Regional_League"
            # the admin page's region set (Vietnam here): its tree, its cities only, my Indonesian
            # region is none now, the crowd spreads over its cities
            kept_koth, hc.KOTH_FILE = hc.KOTH_FILE, os.path.join(tmp, "koth.json")
            try:
                assert hc.koth_set() == "id" and all(hc.koth_leaves(k) and len(hc.koth_leaves(k)) >= 18 for k in hc.KOTH_SETS)
                json.dump({"regions": "vn"}, open(hc.KOTH_FILE, "w"))
                vn = body(hc.ENDPOINTS["/esports2/region/seasons/presently"]("", b""))["regiongrouptrees"][cur["regiongroup"]["id"]]
                assert set(vn) == {m for _, m, _ in hc.KOTH_SETS["vn"]} and "Miền Bắc" in vn and "Hà Nội" in vn["Miền Bắc"]["children"]
                assert "regionid" not in koth() and koth()["seasonid"] == ""
                assert not body(hc.ENDPOINTS["/esports2/region/register"]("", json.dumps({"regiongroupid": leaf["id"]}).encode()))["result"]
                assert body(hc.ENDPOINTS["/esports2/region/register"]("", json.dumps({"regiongroupid": "3101"}).encode()))["result"]
                assert koth()["regionname"] == {"name": "Hà Nội", "parent": "Miền Bắc"}
                counts = body(hc.route(f"/esports2/region/register/{cur['_id']}")("", b""))["registeredplayer"]
                assert set(counts) == set(hc.koth_leaves("vn")) and all(counts.values())
                assert {hc.vm_region(m["_id"]) for m in hc.virtual_managers()} == set(hc.koth_leaves("vn"))
            finally:
                hc.KOTH_FILE = kept_koth
            assert body(hc.ENDPOINTS["/esports2/region/register"]("", json.dumps({"regiongroupid": leaf["id"]}).encode()))["result"]
            # Exhibition (Live Center): plays a day; a room on licensed clubs (my side's club changed by
            # the room's team picker); an invited manager joins with a club of his own; my Ready plays
            # Exhibition_PvP, each side its club's own squad; a squad change goes to that club; my
            # cards earn no EXP from it; a play is used
            assert body(hc.ENDPOINTS["/exhibition/info/points"]("", b"")) == {"point": 0, "maxpoint": hc.EXHIBITION_PLAYS}
            ex = body(hc.ENDPOINTS["/static/db/EXHIBITIONMATCH"]("", b""))
            assert ex["entrycondition"]["NORMAL_use"] and ex["recommendteams"] == list(hc.EXHIBITION_TEAMS)
            r = body(hc.ENDPOINTS["/exhibition/room"]("", json.dumps({"type": "pvp_exhibition", "slotsize": 5}).encode()))
            room = r["resources"][0]["body"]; rid = room["roomid"]
            assert r["result"] and room["type"] == "pvp_exhibition" and room["side"]["left"]["authTeamId"] == hc.EXHIBITION_TEAMS[0]
            club = hc.EXHIBITION_TEAMS[2]
            t = body(hc.route(f"/exhibition/room/{rid}/team")("", json.dumps({"teamid": club, "side": "left"}).encode()))
            assert t["result"] and t["resources"][0]["body"]["side"]["left"]["authTeamId"] == club
            assert not body(hc.route(f"/exhibition/room/{rid}/team")("", json.dumps({"teamid": 0, "side": "left"}).encode()))["result"]
            info = body(hc.route(f"/exhibition/info/authentic/team/{club}")("", b""))
            assert info["teamid"] == club and set(info["summary"]["ovr"]) >= {"FW", "MF", "DF"}
            friend = hc.vm_friends()[1]
            assert rq(rid, f"invite/{friend['_id']}")["result"]
            fire()
            right = hc._state()["room"]["side"]["right"]
            assert right["players"][0]["id"] == friend["_id"] and right["ready"] and right["authTeamId"] in hc.EXHIBITION_TEAMS
            assert right["authTeamId"] != club
            rq(rid, "side/left/ready", ready=True); fire()
            m = hc._state()["match"]
            assert m["type"] == "Exhibition_PvP" and m["side"]["left"]["team"]["teamid"] == club
            assert m["side"]["right"]["team"]["teamid"] == right["authTeamId"] and m["side"]["left"]["team"]["teamcolors"] == []
            sq = m["side"]["left"]["team"]["squad"]
            xi = [p for p in sq["positions"] if p["pos"] < hc.POS_SUB]
            a_, b_ = xi[1], xi[2]
            swapped = [dict(p, pos=b_["pos"]) if p["id"] == a_["id"] else dict(p, pos=a_["pos"]) if p["id"] == b_["id"] else p
                       for p in sq["positions"]]
            r = body(hc.route(f"/match/{m['id']}/side/left/squad")("", json.dumps({"squad": {"positions": swapped}}).encode()))
            got = {p["id"]: p["pos"] for p in hc._state()["match"]["side"]["left"]["team"]["squad"]["positions"]}
            assert r["result"] and (got[a_["id"]], got[b_["id"]]) == (b_["pos"], a_["pos"])
            end(2, 0)
            st = hc._state()
            assert st["match"]["reward"]["players"] == [] and hc.my_coach(st)["multiplay"][-1]["type"] == "Exhibition_PvP"
            assert body(hc.ENDPOINTS["/exhibition/info/points"]("", b""))["point"] == 1
            st = hc._state(); hc.my_coach(st)["exhibition"]["played"] = hc.EXHIBITION_PLAYS; hc._save(st)
            assert body(hc.ENDPOINTS["/exhibition/room"]("", b"{}")) == {"result": False, "reason": "the point is not enough"}
        finally:
            hc.threading.Timer = real_timer
            timers.clear()
            hc._save(snapshot)  # their matches levelled the manager up (level rewards in the Bag) and so on
        # --- admin page API (admin.py)
        import admin, urllib.error, urllib.request
        admin.hc = hc
        A = lambda name, **p: admin.API[name](p)
        def refused(name, **p):
            try:
                A(name, **p)
            except admin.AdminError:
                return True
        assert A("ep", set=5000)["ep"] == 5000 and A("ep", add=-9000)["ep"] == 0 and A("ep", add=1500)["ep"] == ep() == 1500
        # the Rank opponents' chat: on/off and language (gamedata/vm_chat.json)
        assert A("vm_chat")["enabled"] and A("vm_chat")["lang"] == "id" and refused("vm_chat_set", enabled=True, lang="xx")
        # Rank rewards: the admin page edits them (/ladder/reward/info shows them); the weekly wins steps
        # are paid by POST /ladder/reward (once each a week), a finished season at the next one's start
        hc.LADDER_REWARDS_FILE = os.path.join(tmp, "ladder_rewards.json")
        lr = A("ladder_rewards")
        assert lr["divs"] and lr["wins"] and body(hc.ENDPOINTS["/ladder/reward/info"]("", b""))["wins"] == lr["wins"]
        assert refused("ladder_rewards_set", divs=lr["divs"], wins=[{"from": 10, "items": [{"itemid": 1, "count": 5}]},
                                                                     {"from": 5, "items": [{"itemid": 1, "count": 5}]}])  # not rising
        assert refused("ladder_rewards_set", divs=lr["divs"][1:], wins=lr["wins"])  # a division missing
        assert refused("ladder_rewards_set", divs=lr["divs"], wins=[{"from": 3, "items": [{"itemid": 28007, "count": 500}]}])
        divs = [{**d, "items": [{"itemid": 1, "count": 1000 * (i + 1)}, {"itemid": 28007, "count": 1}]} for i, d in enumerate(lr["divs"])]
        new = A("ladder_rewards_set", divs=divs, wins=[{"from": 2, "items": [{"itemid": 1, "count": 700}]},
                                                      {"from": 4, "items": [{"itemid": 28007, "count": 2}]}])
        assert hc.ladder_rewards() == {k: new[k] for k in ("divs", "wins")} and new["wins"][1]["items"] == [{"itemid": 28007, "count": 2}]
        st = hc._state(); c = hc.my_coach(st)
        kept_lad = copy.deepcopy({k: c.get(k) for k in ("ladder", "ladderweek", "ladderprev", "ladderbest", "bag")})
        c["bag"] = []; hc.ladder_week(st)["wins"] = 3; hc._save(st)
        claim_lr = lambda: body(hc.ENDPOINTS["/ladder/reward"]("", b"{}"))
        r = claim_lr()
        assert r["result"] and [(i["itemid"], i["count"]) for i in r["rewards"]["wins"][0]] == [(1, 700)] and not claim_lr()["result"]
        st = hc._state(); hc.ladder_week(st)["wins"] = 4; hc._save(st)
        assert claim_lr()["rewards"]["wins"][0][0]["itemid"] == 28007 and len(hc.my_coach(hc._state())["bag"]) == 2
        st = hc._state(); lad = hc.my_ladder(st)  # a season over: main for the best ladder, sub for the other played
        lad.update(sid=200001, elo=[1700, 1060] + [1000] * (hc.LADDER_COUNT - 2), games=[3, 2] + [0] * (hc.LADDER_COUNT - 2))
        hc.my_ladder(st)
        paid, names = hc.my_coach(st)["bag"][-1], [d["name"] for d in divs]
        assert paid["items"] == [divs[names.index("DIVISION_" + hc.ladder_division(1700))]["items"][0], {"itemid": 28007, "count": 1}]
        assert "200001" in paid["title"]
        st = hc._state(); c = hc.my_coach(st)
        for k, v in kept_lad.items():
            c.pop(k, None) if v is None else c.__setitem__(k, v)
        hc._save(st)
        A("ladder_rewards_set", reset=True)
        assert not os.path.isfile(hc.LADDER_REWARDS_FILE) and hc.ladder_rewards()["wins"] == lr["wins"]
        # King of the Hill rewards: the admin page edits them (the season shows them); an ended week with
        # matches, then a played season, send the client to the reward screen (POST /esports2/region/reward)
        hc.KOTH_REWARDS_FILE = os.path.join(tmp, "koth_rewards.json")
        kr = A("koth_rewards")
        cur = body(hc.ENDPOINTS["/esports2/region/seasons/presently"]("", b""))["seasons"][0]
        assert cur["schedule"]["midreward"] == hc.KOTH_MIDREWARD and cur["reward"]["week"]["win"] == kr["week"]["items"]
        assert cur["reward"]["season"]["ranking"]["1"]["large"][0] == {"rank_from": 1, "rank_to": 1, "items": kr["season"]["ranking"][0]["items"]}
        assert cur["reward"]["week"]["grade"] == [d["items"] for d in kr["week"]["divs"]] and len(kr["week"]["divs"]) == len(hc.ladder_divisions())
        assert cur["reward"]["config"]["weekly_grade_reward_condition"] == kr["week"]["grade"]
        week, season = copy.deepcopy(kr["week"]), copy.deepcopy(kr["season"])
        assert refused("koth_rewards_set", week={**week, "type": "draws"}, season=season)
        assert refused("koth_rewards_set", week={**week, "divs": week["divs"][1:]}, season=season)  # a division missing
        assert refused("koth_rewards_set", week=week, season={**season, "ranking": [  # ranks overlap
            {"from": 1, "to": 5, "items": [{"itemid": 1, "count": 9}]}, {"from": 5, "to": 9, "items": [{"itemid": 1, "count": 9}]}]})
        week.update(type="match", match=2, grade=1, items=[{"itemid": 28007, "count": 1}])
        season.update(grade=1, ranking=[{"from": 1, "to": 3, "items": [{"itemid": 1, "count": 777}]}])
        assert A("koth_rewards_set", week=week, season=season)["season"]["ranking"][0]["to"] == 3
        assert hc.koth_rewards() == {"week": week, "season": season}
        es2 = lambda: hc.coach_me(hc._state(), hc._default_slot(hc._state()))["statistics"]["esports2"]
        claim = lambda: body(hc.ENDPOINTS["/esports2/region/reward"]("", b"{}"))
        st = hc._state(); c = hc.my_coach(st)
        kept_koth, kept_week = copy.deepcopy({k: c.get(k) for k in ("es2", "bag")}), hc.koth_week
        c["bag"] = []
        r = hc.my_koth(st)
        r.update(regionid=next(iter(hc.koth_leaves(hc.koth_set()))), paid=[])
        r["weeks"][0].update(games=2, wins=1, elo=1000)
        hc._save(st)
        hc.koth_week = lambda t=None: 1  # in week 2: week 1 is over
        try:
            assert es2()["midrewarded"][0] is False
            got = claim()
            w = got["reward"]["week"]
            assert got["result"] and w["target"] and w["match"]["rewarded"] and w["grade"]["rewarded"] and not got["reward"]["season"]["target"]
            assert w["match"]["items"] == [{"itemid": 28007, "count": 1}] and w["grade"]["items"] == hc.koth_div_items(week["divs"], 1000)
            bag = hc.my_coach(hc._state())["bag"]
            assert len(bag) == 1 and bag[0]["items"] == [{"itemid": 28007, "count": 1}] + w["grade"]["items"]
            assert es2()["midrewarded"][0] is True and not claim()["result"]
            # a season over: its rewards come first (rank 2 of my region, enough wins), then the new one
            st = hc._state(); r = hc.my_koth(st)
            r.update(sid=200001, elo=1700, wins=3)
            hc.my_koth(st)
            assert r["prev"]["sid"] == 200001 and r["prev"]["rank"] >= 1 and r["wins"] == 0 and r["sid"] == hc.koth_sid()
            r["prev"]["rank"] = 2; hc._save(st)
            assert es2()["seasonid"] == "200001" and es2()["rewarded"] is False
            got = claim()["reward"]["season"]
            assert got["target"] and got["ranking"] == {"rewarded": True, "items": [{"itemid": 1, "count": 777}]}
            assert got["grade"] == {"rewarded": True, "items": hc.koth_div_items(season["divs"], 1700)}
            assert es2()["seasonid"] == str(hc.koth_sid()) and es2()["rewarded"] is True and not claim()["result"]
            assert len(hc.my_coach(hc._state())["bag"]) == 2
        finally:
            hc.koth_week = kept_week
            st = hc._state(); c = hc.my_coach(st)
            for k, v in kept_koth.items():
                c.pop(k, None) if v is None else c.__setitem__(k, v)
            hc._save(st)
        A("koth_rewards_set", reset=True)
        assert not os.path.isfile(hc.KOTH_REWARDS_FILE) and hc.koth_rewards()["week"] == kr["week"]
        # ... and the global chat's football topics: the global ones always, plus the countries ticked by
        # hand; they come by themselves once a WIB day (again when the ticks change)
        kept_timer, refreshes = hc.threading.Timer, []  # a changed tick fetches at once, in the background
        hc.threading.Timer = lambda t, fn, args=(): type("T", (), {"start": lambda self: refreshes.append(fn)})()
        assert refused("vm_chat_set", enabled=True, lang="id", newsregions=["XX"]) and refused("vm_chat_set", enabled=True, lang="id", newsregions="ID")
        v = A("vm_chat_set", enabled=True, lang="id", newsregions=["VN", "ID"])
        assert refreshes == [hc.news_refresh] and A("vm_chat_set", enabled=True, lang="id", newsregions=["ID", "VN"]) and len(refreshes) == 1
        assert v == hc.vm_chat_cfg() and v["newsregions"] == ["ID", "VN"] and "news" not in v
        assert list(A("vm_chat")["regions"]) == ["ID", "MY", "VN", "TH", "KR"] and isinstance(A("vm_chat")["topics"], list)
        kept_fetch, kept_news = hc.news_fetch, dict(hc.NEWS)
        fetched = []
        hc.news_fetch = lambda region, get=None: fetched.append(region) or [{"title": f"topic {region or 'global'}", "source": "", "trend": ""}]
        hc.NEWS.update(key=None, at=0, items=[])
        t0 = calendar.timegm((2026, 11, 9, 3, 0, 0))  # 10:00 WIB
        hc.news_refresh(t0); hc.news_refresh(t0 + 3600 * 12)  # the same WIB day: once
        assert fetched == ["", "ID", "VN"] and A("vm_chat")["topics"] == [["Global", ["topic global"]], ["Indonesia", ["topic ID"]], ["Việt Nam", ["topic VN"]]]
        hc.news_refresh(t0 + 3600 * 15)  # 01:00 WIB the next day
        A("vm_chat_set", enabled=True, lang="id", newsregions=[]); hc.news_refresh(t0 + 3600 * 16)
        assert fetched == ["", "ID", "VN"] * 2 + [""]  # nothing ticked: the global only
        hc.news_fetch = kept_fetch; hc.NEWS.clear(); hc.NEWS.update(kept_news); hc.threading.Timer = kept_timer
        assert A("vm_chat_set", enabled=False, lang="vi") == hc.vm_chat_cfg() == {"enabled": False, "lang": "vi", "newsregions": []}
        # Generate ulang (the virtual managers' generation): only the admin page changes it, at once;
        # a new crowd with the same ids, generation 0 is the crowd as it always was
        kept_gen = (hc.VM_GEN_FILE, hc.VM_GEN)
        hc.VM_GEN_FILE = os.path.join(tmp, "vm_gen.json")
        crowd = lambda: {m["_id"]: (m["name"], m["teamid"]) for m in hc.virtual_managers()}
        try:
            hc.vm_set_generation(0)
            first = crowd()
            assert A("vm_gen") == {"gen": 0, "count": len(first)} and hc.vm_generation() == 0
            assert A("vm_regenerate")["gen"] == 1 == hc.VM_GEN == hc.vm_generation()  # saved and in use
            second = crowd()
            assert set(second) - set(first) <= {k for k in second if k.startswith("vm0")}  # ids: the same ranges
            assert sum(first[k][0] != second[k][0] for k in set(first) & set(second)) > 0.9 * len(set(first) & set(second))
            assert A("vm_regenerate")["gen"] == 2 and crowd() != second
            assert A("vm_regenerate", reset=True)["gen"] == 0 and crowd() == first  # back to the first crowd exactly
        finally:
            hc.vm_set_generation(kept_gen[1])
            hc.VM_GEN_FILE = kept_gen[0]
        # the game world (issue #38): classic FO3 = 2017 squads, the added seasons only to collect
        import gen_teams
        kept_world, gen_teams.WORLD_FILE = gen_teams.WORLD_FILE, os.path.join(tmp, "world.json")
        kept_season, kept_running = gen_teams.CURRENT_SEASON, gen_teams.WORLD
        try:
            assert A("world") == {"world": "modern", "running": gen_teams.WORLD} and refused("world_set", world="retro")
            assert A("world_set", world="classic")["world"] == gen_teams.world() == "classic"
            assert gen_teams._current_season() == gen_teams.LEGACY_SEASON
            gen_teams.WORLD = gen_teams.world()  # as after a restart
            classic = {c.spid // 1000000 for cs in hc.vm_card_pool.__wrapped__().values() for c in cs}
            assert classic and not classic & hc.ADDED_SEASONS and 88 in classic
            # no added season in packs, the Market or trades (all of them go through market_spids),
            # nor in the client's Market search seasons
            sold = {s // 1000000 for s in hc.market_spids.__wrapped__()}
            assert not sold & hc.ADDED_SEASONS and 88 in sold
            raw = open(os.path.join(hc.RESPONSES, "static_roster.json"), "rb").read()
            assert not {v for s in json.loads(hc.roster_seasons(raw))["seasons"] for v in s["value"]} & hc.ADDED_SEASONS
            gen_teams.CURRENT_SEASON = gen_teams._current_season()
            with hc.closing(hc._db()) as c:
                madrid = gen_teams.team(c, 243)
            assert {int(p["spid"]) // 1000000 for p in madrid["team"]["players"]} == {gen_teams.LEGACY_SEASON}
            # issue #43: a club FO3 ID never had (AC Milan) plays FO3's cards of every year in classic, none
            # added, and is listed when they make a squad
            with hc.closing(hc._db()) as c:
                milan = gen_teams.team(c, 47)
            hc._CLUBS.clear(); hc._CLUBS_KIT.clear()
            assert bool(milan) == (47 in {t for _, ls in hc.all_clubs() for _, ts in ls for t in ts})
            assert not milan or not {int(p["spid"]) // 1000000 for p in milan["team"]["players"]} & hc.ADDED_SEASONS
            A("world_set", world="modern")
            gen_teams.WORLD = gen_teams.world()
            modern = {c.spid // 1000000 for cs in hc.vm_card_pool.__wrapped__().values() for c in cs}
            assert modern > classic and (300 in modern) == (300 in hc.ADDED_SEASONS and gen_teams._current_season() == 300)
            assert {s // 1000000 for s in hc.market_spids.__wrapped__()} >= sold
            got, base = json.loads(hc.roster_seasons(raw))["seasons"], json.loads(raw)["seasons"]
            assert got[:len(base)] == base and {v for e in got[len(base):] for v in e["value"]} <= set(hc.card_seasons())  # no own season here
        finally:
            gen_teams.WORLD_FILE, gen_teams.CURRENT_SEASON, gen_teams.WORLD = kept_world, kept_season, kept_running
            hc._CLUBS.clear(); hc._CLUBS_KIT.clear()  # the club lists of the running world again
        # King of the Hill's region set (issue #36): Indonesia, Singapore / Malaysia, Thailand, Vietnam
        kept_koth = (hc.KOTH_FILE, hc.KOTH_CUSTOM_FILE)
        hc.KOTH_FILE, hc.KOTH_CUSTOM_FILE = os.path.join(tmp, "koth_admin.json"), os.path.join(tmp, "koth_custom.json")
        try:
            assert A("koth")["set"] == "id" and set(A("koth")["sets"]) == {"id", "sgmy", "th", "vn", "custom"} and refused("koth_set", set="xx")
            assert A("koth_set", set="th")["set"] == hc.koth_set() == "th"
            # the custom area (issue #36 asked for Sa Pa in Miền Bắc): the editor's lines, ids kept by name
            assert refused("koth_set", set="custom") and A("koth")["texts"]["custom"] == ""  # nothing yet
            vn = A("koth")["texts"]["vn"]
            assert vn.startswith("Miền Bắc: Hà Nội, Hải Phòng, Quảng Ninh, Bắc Ninh\n")
            k = A("koth_custom_set", text=vn.replace("Bắc Ninh\n", "Bắc Ninh, Sa Pa\n", 1))
            assert k["sets"]["custom"][0] == ["Miền Bắc", ["Hà Nội", "Hải Phòng", "Quảng Ninh", "Bắc Ninh", "Sa Pa"]]
            assert A("koth_set", set="custom")["set"] == hc.koth_set() == "custom"
            leaves = hc.koth_leaves("custom")
            sapa = next(i for i, x in leaves.items() if x[0] == "Sa Pa")
            assert (sapa, leaves[sapa][2]) == ("4105", "Miền Bắc") and len(leaves) == 19
            tree = body(hc.ENDPOINTS["/esports2/region/seasons/presently"]("", b""))["regiongrouptrees"][hc.KOTH_GROUP]
            assert "Sa Pa" in tree["Miền Bắc"]["children"] and tree["Miền Bắc"]["id"] == "41"
            assert {hc.vm_region(f"vm{n:04d}", "custom") for n in range(1, 100)} <= set(leaves)
            # a new region first, a renamed city: every other id stays
            k = A("koth_custom_set", text="Tây Bắc：Điện Biên，Lai Châu\n" + vn.replace("Bắc Ninh\n", "Bắc Ninh, Sa Pa\n", 1).replace("Huế", "Cố đô Huế"))
            again = hc.koth_leaves("custom")
            assert {i: x[0] for i, x in again.items() if x[0] in ("Sa Pa", "Hà Nội", "Đà Nẵng")} == {
                i: x[0] for i, x in leaves.items() if x[0] in ("Sa Pa", "Hà Nội", "Đà Nẵng")}
            assert next(x[1] for x in again.values() if x[0] == "Điện Biên") not in {x[1] for x in leaves.values()}
            hue = next(i for i, x in leaves.items() if x[0] == "Huế")  # a renamed city takes the free id: its slot
            assert next(i for i, x in again.items() if x[0] == "Cố đô Huế") == hue
            for bad in ("Miền Bắc Hà Nội", "A: x\nB: x", "A:", "A" * 25 + ": x", "\n".join(f"R{i}: c{i}" for i in range(21))):
                assert refused("koth_custom_set", text=bad), bad
        finally:
            hc.KOTH_FILE, hc.KOTH_CUSTOM_FILE = kept_koth
        # issue #33, Info Manager: the record tab draws once /coach/me has createdat, playtime, career and
        # statistics with every object it reads (one missing stopped it at "-")
        me = hc.coach_me(hc._state(), 0)
        assert len(me["createdat"]) > 19 and {"clubs", "maxplayers", "buyplayers", "sellplayers"} <= set(me["career"])
        s = me["statistics"]
        assert {"league", "friendly", "simulation", "club", "cup", "ladder", "winstreak"} <= set(s) and s["league"]["leaguewins"] == [0]
        assert set(s["friendly"]) == {"custom", "quick", "vscom"} and len(s["friendly"]["quick"]["wins"]) == 5
        assert all(len(s["ladder"]["highrecords"][k]) == hc.LADDER_COUNT for k in ("rank", "winningstreak", "elo"))
        assert body(hc.ENDPOINTS["/coach/me/profile"]("", b'{"description": "Halo", "_method": "PUT"}'))["result"]
        assert hc.coach_me(hc._state(), 0)["profile"]["description"] == "Halo"
        # play time: the gaps between requests, a long silence is time away; added on the next save
        hc.PLAYTIME.update(last=0.0, pending=0.0)
        for t in (10_000, 10_600, 20_000):  # away, 600 s of play, away
            hc.note_activity(t)
        st = hc._state(); t0 = hc.my_coach(st).get("playtime", 0); hc._save(st)
        assert hc.coach_me(hc._state(), 0)["playtime"] == t0 + 600
        # missions: the server counts my play; Manager > Daftar pencapaian (badge rows of achievements
        # and the daily row) and the weekly Quest window (GP for its reward boxes)
        st = hc._state(); hc.my_coach(st).pop("missions", None); hc._save(st)
        ep_before = ep()  # the rewards paid below are taken back after
        sa = body(hc.ENDPOINTS["/static/info/ACHIEVEMENT"]("", b""))
        ids = {d["id"] for d in sa["list"]}
        assert all(i in ids and isinstance(i, int) for g in sa["mixed"].values() for i in g) and set(sa["mixed"]) <= ids
        assert all(len(g) <= 5 for g in sa["mixed"].values()) and all(len(d.get("rewards", [])) <= 5 for d in sa["list"])
        names = body(hc.ENDPOINTS["/static/db/ITEMNAMES"]("", b""))  # a reward without a name throws
        assert all(str(r["itemid"]) in names for d in sa["list"] for g in d.get("rewards", []) for r in g["reward"])
        assert all(hc.LANGUAGE in n and "vie" in n for n in names.values())  # read by the client's LOCALE (issue #36)
        assert {b["asset"] for b in body(hc.ENDPOINTS["/static/db/BADGES"]("", b""))} <= set(sa["mixed"]) and "misc" in sa["mixed"]
        ach = lambda: body(hc.ENDPOINTS["/achievement/me"]("", b""))
        rows = lambda: {r["id"]: r for r in ach()["achievement"]}
        mine = ach()
        subs = [r for r in mine["achievement"] if not isinstance(r["id"], str)]
        assert len(subs) == mine["summary"]["total"] == len(ids) - len(sa["mixed"]) and all(r["aon"] for r in subs)
        assert {r["id"] for r in mine["achievement"]} == ids - set(sa["mixed"])  # every id of a row (or the row stops);
        # a group's own entry only with its badge (the panel lights it and dates it whenever there is one)
        st = hc._state(); hc.mission_event(st, match=3, win=2, goal=5, rank=1, rankwin=1); hc._save(st)
        assert all(rows()[i]["rewards"]["available"] for i in (901, 902, 903, 904))  # today's missions done
        e0 = ep()
        got = body(hc.route("/achievement/902/reward/1")("", b"{}"))
        assert got["result"] and got["rewards"][0]["itemid"] == hc.EP_ITEM and ep() == e0 + got["rewards"][0]["count"]
        assert got["resources"][0]["uri"] == "/achievement/me" and rows()[902]["complete"]
        assert not body(hc.route("/achievement/902/reward/1")("", b"{}"))["result"]  # once a day
        st = hc._state(); hc.my_coach(st)["missions"]["day"] = "2000-01-01"; hc._save(st)  # the next day
        assert not rows()[902]["complete"] and rows()[902]["progress"]["detail"]["current"] == 0
        assert rows()[102]["progress"]["detail"]["current"] == 3  # the totals stay
        # a badge row all paid earns its badge, which can be worn
        st = hc._state(); hc.mission_event(st, goal=1000, rankwin=150); hc._save(st)
        for aid in (401, 402):
            for g in range(1, 6):
                assert body(hc.route(f"/achievement/{aid}/reward/{g}")("", b"{}"))["result"], (aid, g)
        assert rows()["gol"]["complete"] and rows()["gol"]["completedat"] and 900039 in hc.coach_me(hc._state(), 0)["badges"]
        assert body(hc.route("/badge/900039/use")("", b"{}"))["resources"][0]["body"]["currentbadge"] == 900039
        assert not body(hc.route("/badge/900046/use")("", b"{}"))["result"]  # not earned
        # this week's quests (until Monday 00:00 WIB) and next week's; a done one paid its GP
        bank, end = body(hc.ENDPOINTS["/static/info/request"]("", b"")), hc.week_end()
        assert bank["expire"] == [hc.w3c(end), hc.w3c(end + 7 * 86400)] and len(bank["current"]) == hc.QUEST_COUNT  # expire[i]
        assert [q["name"] for q in bank["next"]] == [q[0] for q in hc.week_quests(hc.missions_cfg(), end + 7 * 86400)]
        quests = ach()["quest"]
        assert all(("completedat" in q) == (q["progress"]["detail"]["current"] == b["todo"]) for q, b in zip(quests, bank["current"]))
        assert hc.coach_me(hc._state(), 0)["gpdata"]["point"] == sum(b["reward"]["count"] for q, b in zip(quests, bank["current"]) if "completedat" in q)
        shop = body(hc.ENDPOINTS["/shop/list/GPSHOP"]("", b""))  # its reward boxes, priced in GP
        assert [(e["product"]["itemid"], e["product"]["staticdata"]["bunches"][0]["price"]) for e in shop] == list(hc.GP_SHOP)
        # issue #44: a reward box stretches its picture to 170 x 140 on screen, so its icon is the Shop's
        # 170 x 120 one with transparent rows above and below; the image rows stay byte for byte
        assert all(str(e["product"]["staticdata"]["bundleicon"]).startswith(hc.QUEST_ICON) for e in shop)

        def rgba_png(w, h, raw, ctype=6):
            chunk = lambda k, c: struct.pack(">I", len(c)) + k + c + struct.pack(">I", zlib.crc32(k + c))
            return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        rows = bytes([2]) + bytes(range(1, 17)) + bytes([0]) + bytes(range(17, 33))  # 4 x 2 RGBA, row 1 "Up" filtered
        out = hc.pad_png(rgba_png(4, 2, rows), 1.0)
        k = out.index(b"IDAT"); n = struct.unpack(">I", out[k - 4:k])[0]
        assert struct.unpack(">II", out[16:24]) == (4, 4) and zlib.decompress(out[k + 4:k + 4 + n]) == bytes(17) + rows + bytes(17)
        assert hc.pad_png(rgba_png(4, 2, rows[:13] + rows[17:30], ctype=2), 1.0) == rgba_png(4, 2, rows[:13] + rows[17:30], ctype=2)  # RGB: as it is
        icon, real = os.path.join(tmp, "icon.png"), hc.asset_path
        with open(icon, "wb") as f:
            f.write(rgba_png(170, 120, bytes(681) * 120))
        hc.asset_path = lambda rel: icon if rel == "externalAssets/shop/501.png" else real(rel)
        try:
            served = hc.cdn_file(f"externalAssets/shop/{hc.QUEST_ICON}501.png")
        finally:
            hc.asset_path = real
        assert served[0] == "image/png" and struct.unpack(">II", served[1][16:24]) == (170, 140)
        st = hc._state(); hc.missions_state(st)["gp"] = 25; hc._save(st)
        buy_gp = lambda e: body(hc.ENDPOINTS["/shop/purchase"]("", json.dumps({"shopid": e["shopid"], "productid": e["product"]["itemid"], "bunch": 0}).encode()))
        assert buy_gp(shop[1])["resources"][0]["body"]["gpdata"]["point"] == 5 and buy_gp(shop[1])["reason"] == "NOT_ENOUGH_GP"
        # the admin page's Misi tab: on/off, language, targets and rewards
        assert A("missions")["enabled"] and refused("missions_set", enabled=True, lang="xx", targets=1, rewards=1)
        # the Daily reward tab's play time rewards: minutes rising, each step EP or a pack
        assert A("playtime")["table"] == hc.PLAYTIME_DEFAULT
        assert refused("playtime_set", table=[{"minutes": 30, "ep": 1}, {"minutes": 10, "ep": 1}])  # not rising
        assert refused("playtime_set", table=[{"minutes": 30}]) and refused("playtime_set", table=[{"minutes": 5, "pack": 1}])
        A("playtime_set", table=[{"minutes": 5, "ep": 500}, {"minutes": 20, "pack": 28007, "count": 2}])
        assert hc.playtime_table() == [{"minutes": 5, "ep": 500}, {"minutes": 20, "pack": 28007, "count": 2}]
        os.remove(hc.PLAYTIME_FILE)
        assert refused("missions_set", enabled=True, lang="en", targets=0, rewards=1)
        cfg = A("missions_set", enabled=True, lang="en", targets=2, rewards=0.5)
        assert cfg == hc.missions_cfg() and {k: cfg[k] for k in ("enabled", "lang", "targets", "rewards")} == \
            {"enabled": True, "lang": "en", "targets": 2.0, "rewards": 0.5}
        d902 = next(d for d in body(hc.ENDPOINTS["/static/info/ACHIEVEMENT"]("", b""))["list"] if d["id"] == 902)
        assert d902["name"] == "Daily: Wins" and d902["rewards"][0]["todo"] == 4
        assert "quest" in body(hc.ep_account("", b""))["features"]["freeroambuttons"]
        A("missions_set", enabled=False, lang="id", targets=1, rewards=1)
        assert ach()["achievement"] == [] and "quest" not in body(hc.ep_account("", b""))["features"]["freeroambuttons"]
        A("missions_set", enabled=True, lang="id", targets=1, rewards=1)
        # its weekly quest editor (the list a week draws from, how many, the reward boxes): a quest done
        # stays done and paid once when the list changes; reset = the built-in ones
        m = A("missions")
        assert [(q["stat"], q["todo"], q["gp"]) for q in m["quests"]] == list(hc.QUESTS) and m["questcount"] == hc.QUEST_COUNT
        assert [q["name"] for q in m["thisweek"]] == [q[0] for q in hc.week_quests(hc.missions_cfg(), hc.week_end())]
        assert set(m["stats"]) == set(hc.QUEST_STATS) and [501, "Lucky EP"] in m["items"]
        st = hc._state(); done = dict(hc.missions_state(st)["quests"])
        assert done and all(":" in k for k in done)
        own = [{"stat": "goal", "todo": 2, "gp": 7, "name": "Gol {0}x"}, {"stat": "win", "todo": 999, "gp": 9, "name": ""}]
        assert refused("quests_set", quests=own, questcount=3, gpshop=m["gpshop"])  # more a week than the list
        assert refused("quests_set", quests=[{**own[0], "stat": "level"}], questcount=1, gpshop=m["gpshop"])
        assert refused("quests_set", quests=own, questcount=2, gpshop=[{"itemid": 1, "gp": 5}])  # not an item
        assert refused("quests_set", quests=own, questcount=2, gpshop=m["gpshop"] * 2)  # 10 boxes
        A("quests_set", quests=own, questcount=2, gpshop=[{"itemid": 501, "gp": 15}])
        assert hc.missions_cfg()["lang"] == "id"  # the other settings kept
        bank = body(hc.ENDPOINTS["/static/info/request"]("", b""))
        assert sorted(q["name"] for q in bank["current"]) == ["Gol 2x", "Menangi 999 pertandingan"]
        assert [(e["product"]["itemid"], e["product"]["staticdata"]["bunches"][0]["price"])
                for e in body(hc.ENDPOINTS["/shop/list/GPSHOP"]("", b""))] == [(501, 15)]
        gp0 = hc.missions_state(hc._state())["gp"]
        st = hc._state(); hc.mission_event(st, match=1); hc._save(st)  # this week's goals already make 2
        assert hc.missions_state(hc._state())["gp"] == gp0 + 7
        assert sorted("completedat" in q for q in ach()["quest"]) == [False, True]  # the goal one only
        A("quests_set", reset=True)
        assert hc.missions_cfg() == {**hc.MISSIONS_DEFAULT, "lang": "id"}
        st = hc._state(); hc.mission_event(st, match=1); hc._save(st)
        assert hc.missions_state(hc._state())["gp"] == gp0 + 7  # the built-in ones done before: not paid again
        # a done quest saved by its place in the list (before the editor) is moved to its counter and target
        st = hc._state(); m_ = hc.missions_state(st); m_["quests"] = {"0": "x"}
        first = hc.week_quests(hc.missions_cfg(), m_["week"])[0]
        assert hc.missions_state(st)["quests"] == {hc.quest_key(first[1], first[2]): "x"}
        A("ep", set=ep_before)
        # issue #31, pre-match settings: Rank's game setting screen has the steppers (its stadium list is
        # GET /stadium/list, a change is PUT /match/<id>/gamesettings); Liga and Cup only show the values,
        # which come from the last choice (also set here, on the admin page)
        stads = body(hc.ENDPOINTS["/stadium/list"]("", b""))
        assert len(stads) == len(hc.STADIUMS_SAFE) and {s["id"] for s in stads} == set(hc.STADIUMS_SAFE)
        assert all(s["name"] and "(" not in s["name"] and s["todWeatherAvailables"] == {"1": [0, 1, 2, 3], "3": [0, 1, 2, 3]}
                   for s in stads)
        assert A("prematch")["stadium"] == hc.STADIUM_DEFAULT and A("prematch")["tod"] == 1 and len(A("prematch")["stadiums"]) == 70
        assert {s["id"]: s["name"] for s in stads}[2] == hc.client_stadium_names().get("2", "Santiago Bernabeu")  # the client's names
        assert [b["id"] for b in A("prematch")["balls"]] == list(hc.BALLS) and A("prematch")["ball"] == 0
        assert refused("prematch_set", stadium=6, tod=1, weather=0, halfLength=30, ball=0)  # Camp Nou: no DB row in the client
        assert refused("prematch_set", stadium=1, tod=4, weather=0, halfLength=30, ball=0)  # dusk: no light set
        assert refused("prematch_set", stadium=1, tod=1, weather=0, halfLength=30, ball=88)  # a ball not offered
        assert A("prematch_set", stadium="1", tod=3, weather=1, halfLength=60, ball=6) == dict(stadium=1, tod=3, weather=1, halfLength=60, ball=6)
        mk = lambda: hc.new_match("Cup_PvE", hc.match_side(hc.roster_team(241), 241), hc.match_side(hc.roster_team(243), 243))
        m = mk(); hc.apply_prematch(hc._state(), m); gs = m["gamesettings"]
        assert (gs["stadium"], gs["tod"], gs["weather"], gs["halfLength"], gs["ball"]) == (1, 3, 1, 60, {"id": 6, "availables": list(hc.BALLS)})
        assert gs["stadiumchange"] and gs["timechange"] and gs["weatherchange"] and gs["matchtimechange"]
        A("prematch_set", stadium="home", tod="random", weather="random", halfLength=30, ball=0)
        m = mk(); m["gamesettings"]["home"] = False; hc.apply_prematch(hc._state(), m, hc.random.Random(1)); gs = m["gamesettings"]
        assert gs["stadium"] == (hc.club_stadium(243) if hc.club_stadium(243) in hc.STADIUMS_SAFE else hc.STADIUM_DEFAULT)
        assert gs["tod"] in hc.TODS and gs["weather"] in hc.WEATHERS
        for k in range(30):  # a random weather is clear, rain or overcast: snow only when picked
            m2 = mk(); hc.apply_prematch(hc._state(), m2, hc.random.Random(k)); assert m2["gamesettings"]["weather"] != 2
        st = hc._state(); st["match"] = m; hc._save(st)  # Rank's steppers: several keys may come in one body
        put = lambda **b: body(hc.route(f"/match/{m['id']}/gamesettings")("", json.dumps({**b, "_method": "PUT"}).encode()))
        r = put(stadium=137, tod=3, weather=2, halfLength=50, side=0, kit="away")
        assert r["resources"][0]["uri"] == f"/match/{m['id']}" and r["resources"][0]["body"]["gamesettings"]["stadium"] == 137
        gs = hc._state()["match"]["gamesettings"]
        assert (gs["stadium"], gs["tod"], gs["weather"], gs["halfLength"], gs["kit"][0]) == (137, 3, 2, 50, "away")
        put(stadium=6, tod=5, weather=7, halfLength=45, ball={"id": 88})  # not offered: ignored (no crash-prone value reaches native)
        gs = hc._state()["match"]["gamesettings"]
        assert (gs["stadium"], gs["tod"], gs["weather"], gs["halfLength"], gs["ball"]["id"]) == (137, 3, 2, 50, 0)
        assert hc._state()["matchprefs"]["ball"] == 0
        put(ball={"id": 41})  # the ball stepper
        assert hc._state()["match"]["gamesettings"]["ball"]["id"] == 41
        assert A("prematch")["stadium"] == 137 and hc.match_prefs(hc._state()) == dict(stadium=137, tod=3, weather=2, halfLength=50, ball=41)
        st = hc._state(); st.pop("match"); st.pop("matchprefs"); hc._save(st)
        hl, hc.HALF_LENGTH = hc.HALF_LENGTH, 20  # a FO3_HALF_LENGTH off the stepper's list: kept, the stepper off
        m = mk(); hc.apply_prematch(hc._state(), m)
        assert m["gamesettings"]["halfLength"] == 20 and not m["gamesettings"]["matchtimechange"]
        # issue #33: the officials are rows of the engine's referee table (assistants 0 = an all-zero
        # record, missing heads and bodies: the deformed officials at the line-up)
        assert m["gamesettings"]["refereeIdSelector"] == 112 and 0 not in m["gamesettings"]["subRefereeIds"]
        hc.HALF_LENGTH = hl
        # issue #30's economy request: harder, configurable prices (admin Ekonomi tab). Easy = as it was
        assert A("economy") == dict(hc.ECONOMY_DEFAULT, shopfree=hc.SHOP_FREE) and hc.SHOP_FREE
        m88, p1, pk = 88158023, hc.card_price(88158023), next(iter(hc.PACKS))
        pp1 = hc.pack_price(pk)
        assert refused("economy_set", players=0, packs=1, start_ep=0) and refused("economy_set", players=1, packs="x", start_ep=0)
        n_push = len(pushed)
        r = A("economy_set", players=2, packs=3, start_ep=5000, dynamic=False, shopfree=False)
        assert r == dict(players=2.0, packs=3.0, start_ep=5000, dynamic=False, strict=False, shopfree=False) and not hc.SHOP_FREE
        # a running game keeps its Shop list for the whole session: the push makes it ask again, and
        # the game reads /shop/list/clear back and acts only on a new value (issue #30: a preset
        # chosen while the game ran left every price at 0)
        clears = lambda: [b["changed"] for u, b in pushed[n_push:] if u == "/shop/list/clear"]
        assert len(clears()) == 1 and body(hc.ENDPOINTS["/shop/list/clear"]("", b"")) == {"changed": clears()[0]}
        assert body(hc.ENDPOINTS["/shop/remained/limited"]("", b"")) == []  # {} raised a UI error in the Shop
        assert hc.card_price(m88) == hc.sig3(p1 * 2) and abs(hc.pack_price(pk) - pp1 * 6) <= pp1 * 6 * 0.1  # caches dropped
        # the paid Shop prices every product by what it gives (EP at face, packs at their cards'
        # worth), rounded up and none off for 10: rounding down or 5% off made EP
        assert hc.product_price(50021, 1) == 1_000_000 and hc.product_price(50021, 10) == 10_000_000
        assert hc.product_price(119082304, 1) == hc.nice_ceil((2 * 100_000 + 100_000_000) / 3)  # its expected EP
        box = hc.ITEMS[50121][3]["prizes"]
        assert hc.shop_worth(50121) == sum(w * (p[1] if isinstance(p, tuple) else hc.pack_worth(p)) for w, p in box) / sum(w for w, _ in box)
        assert hc.product_price(801, 1) == hc.nice_ceil(sum(n * hc.shop_worth(i) for i, n in hc.PACKAGES[801][2])) > 0
        assert hc.product_price(81000001, 1) == 7_000_000  # a month of EP
        # what holds no EP has a set price times the player price setting (x2 here): issue #30, "all
        # Shop items should have reasonable prices that scale according to the selected Economy difficulty"
        assert [hc.product_price(i, 1) for i in (1000054, 1000053, 1000052)] == [100_000, 200_000, 300_000]  # boosts
        assert hc.product_price(81000100, 1) == 200_000 and hc.product_price(6003, 1) == 506_000  # 100 points; EXP Max
        assert (hc.product_price(3002, 1), hc.product_price(3001, 1)) == (200_000, 600_000)  # name, team change
        free = [(e["shopcategory"], e["product"]["itemid"]) for e in hc.shop_list(hc._state())
                if any(b["price"] == 0 for b in e["product"]["staticdata"]["bunches"])]
        assert not free, free
        assert hc.product_price(pk, 10) == hc.nice_ceil(hc.pack_ev(pk) * 3 * 10) >= hc.pack_ev(pk) * 3 * 10
        assert all(hc.product_price(i, 1) >= hc.pack_ev(i) * 3 for i in list(hc.PACKS)[:40])  # never under the cards' worth
        # a League Simulation coupon costs the most its round pays: a won match, its reward card and
        # a share of the season's top prizes (free coupons made EP without playing)
        cfg = hc.match_reward_cfg()
        card = sum(w * (p["ep"] if isinstance(p, dict) else hc.shop_worth(p)) for w, p in cfg["cards"]) / sum(w for w, _ in cfg["cards"])
        assert hc.season_top_ep() >= hc.LEAGUE_SIZE * hc.PLACE_EP + sum(hc.LEADER_EP.values())
        assert hc.product_price(hc.SIM_COUPON, 1) == hc.nice_ceil(cfg["result"]["win"]["ep"] + card + hc.season_top_ep() / (2 * (hc.LEAGUE_SIZE - 1)))
        assert json.load(open(hc.ECONOMY_FILE))["players"] == 2.0 and hc.load_economy() == hc.ECONOMY
        live = hc.STATE; hc.STATE = os.path.join(tmp, "eco_state.json")  # a new manager starts with start_ep
        hc._save({"coaches": [], "defaultcoachid": "", "teams": {}})
        hc.ENDPOINTS["/coach/1"]("", json.dumps({"id": 241, "coachName": "Eco"}).encode())
        assert hc._state()["coaches"][0]["ep"] == 5000
        # issue #40: each manager keeps his own Liga (and Cup, club, ...): switching managers swaps
        # them; another club's league saved before the fix shows the creation screen
        hc._save({"coaches": [], "defaultcoachid": "", "teams": {}})
        for slot, club in ((0, 241), (1, 1)):  # Barcelona (LaLiga), Arsenal (Premier League)
            sp = [p["spid"] for p in hc.roster(club)["team"]["players"]]
            assert body(hc.ENDPOINTS[f"/coach/{slot}/new/step/3"]("", json.dumps({"id": club, "spids": sp}).encode()))["result"]
            assert body(hc.ENDPOINTS[f"/coach/{slot}"]("", json.dumps({"id": club, "coachName": f"M{slot}"}).encode()))["result"]
        use_coach = lambda s: body(hc.ENDPOINTS[f"/coach/{s}/default"]("", b""))
        l2_now = lambda: body(hc.ENDPOINTS["/league2/me"]("", b""))
        start_l2 = lambda: body(hc.ENDPOINTS["/league2/calendar"]("", json.dumps({"difficulty": 10}).encode()))
        use_coach(0); start_l2()
        assert (l2_now()["leagueid"], l2_now()["league"]["teams"][0]) == (53, 241)
        use_coach(1)
        assert "league" not in l2_now() and l2_now()["sponsoroffice"]["season"]["status"] == "none"  # his own creation screen
        start_l2()
        assert (l2_now()["leagueid"], l2_now()["league"]["teams"][0]) == (13, 1)
        use_coach(0)
        assert (l2_now()["leagueid"], l2_now()["league"]["teams"][0]) == (53, 241)  # the first one's season is back
        st = hc._state(); st["league2"] = next(c for c in st["coaches"] if c["slot"] == 1)["clubdata"]["league2"]; hc._save(st)
        assert "league" not in l2_now() and l2_now()["teamid"] == 241
        hc.STATE = live
        # the dynamic Market: a player's price moves day by day (the same for every grade), the band
        # goes to 105% (a slower buyer; the fee takes that back), the managers list bargains, the
        # graph draws the real days
        A("economy_set", players=1, packs=1, start_ep=1000000, dynamic=True, shopfree=True)
        assert len(clears()) == 2 and clears()[1] > clears()[0]  # each change a new value
        days = [hc.market_value(m88, 1, hc.VM_EPOCH + d * 86400 + 3600) for d in range(60)]
        assert all(0.86 * p1 <= v <= 1.14 * p1 for v in days) and max(days) / min(days) > 1.1  # room to trade
        lower, upper, v = hc.sell_band(m88, 1)
        assert v == hc.market_value(m88, 1) and upper == hc.nice_floor(v * hc.MARKET_ASK_MAX) and lower == v * 8 // 10
        # the stock never runs out, so its card must not resell at a profit at the top of the band
        net = lambda s, g: hc.sale_net({"price": hc.sell_band(s, g)[1], "completefee": {"base": int(hc.sell_band(s, g)[1] * hc.SALE_FEE)}})
        assert all(net(s, g) <= hc.market_value(s, g) for s in sorted(hc.market_spids())[:300] for g in (1, 2, 3))
        assert hc.market_rows(m88)[0]["price"] == v
        lows = [r["price"] / hc.market_value(s, r["grade"]) for s in sorted(hc.market_spids())[:400] for r in hc.market_rows(s)[3:]]
        assert lows and min(lows) < 0.95 and all(0.85 <= x <= 1.03 for x in lows)  # bargains now and then
        o = {"_id": "t", "spid": m88, "grade": 1, "price": upper}
        now = time.time(); hc.vm_buyer(o, now)
        assert 300 * 48 * 0.6 * 0.9 <= o["buyat"] - now <= 300 * 48 * 1.4 * 1.1  # ~4 h at 105%
        hist = body(hc.route(f"/transfer/{m88}/1/detail")("", b""))["history"]
        assert hist["basePrice"] == v and hist["data"][-1][1] == hc.market_value(m88, 1, hc.VM_EPOCH + hc.vm_day() * 86400 + 43200)
        # --- scarce EP (a Shop that costs EP with the dynamic Market: the Realistis preset): nothing
        # turns EP into more EP. An Upgrade's fee takes what the try adds at its odds (boosts too), so
        # buying both cards and selling the result at the band's top never pays (a +3 tried with a +1
        # doubled its money); Easy keeps the 2% fee
        # (with the strict rules, Sulit; Realistis keeps the game's own: a 2% fee, no offer cap. User:
        # "harga upgrade pemain terlalu mahal", "ikuti aturan game aslinya saja untuk Realistis")
        pa, pb = {"spid": 88158023, "grade": 1}, {"spid": 88158023, "grade": 1}
        A("economy_set", players=1, packs=1, start_ep=1000000, dynamic=True, shopfree=False)
        base = max(1000, hc.sig3(hc.card_price(88158023, 1) // 50))
        assert hc.enhance_cost(pa, pb, 1.0) == base and hc.trade_cap([pa]) is None and not hc.ECONOMY["strict"]
        assert A("economy_set", players=1, packs=1, start_ep=1000000, dynamic=True, shopfree=False, strict=True)["strict"]
        assert hc.enhance_cost(pa, pb, 1.0) > base and hc.trade_cap([pa])
        for s in sorted(hc.market_spids())[::2000][:25]:
            for tg in range(1, 10):
                for mg in range(tg + 1):
                    pa, pb = {"spid": s, "grade": tg}, {"spid": s, "grade": mg}
                    for b in (0, 0.10, 0.35):  # none, the event, the event + Gold + points
                        r = min(1.0, hc.enhance_rate(pa, pb) + (b if mg else 0))
                        bad = tg - 1 if tg >= hc.ENHANCE_DROP_FROM else tg
                        out = r * net(s, hc.enhance_grade(pa, pb)) + (1 - r) * net(s, bad)
                        assert out <= hc.market_value(s, tg) + hc.market_value(s, mg) + hc.enhance_cost(pa, pb, r), (s, tg, mg, b)
        hc.SHOP_FREE = True
        assert hc.enhance_cost(pa, pb, 0.5) == max(1000, hc.sig3(hc.card_price(s, 9) // 50))
        hc.SHOP_FREE = False
        # the preview shows that fee; a failure that drops a grade offers the restore for EP, the
        # Market value it gives back (upgrade points are free in the Shop)
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
        s = next(x for x in sorted(hc.market_spids())[::997] if not copies(x))
        t4, m1, tg, mt = (hc.add_player(t, s, g) for g in (4, 1, 6, 2))
        ep0, es0 = ep(), es()
        hc.add_ep(st, 10 ** 11); hc._save(st)
        v1 = body(hc.route(f"/player/{hc.iid(t4)}/enhance/{hc.iid(m1)}/preview")("", b""))  # +4 with a +1: 50%
        assert v1["enhancepay"] == hc.enhance_cost(t4, m1, 0.5) > max(1000, hc.sig3(hc.card_price(s, 4) // 50)), v1
        pv = body(hc.route(f"/player/{hc.iid(tg)}/enhance2.5/{hc.iid(mt)}/preview/0/-1/-1")("", b""))
        assert pv["enhancepay"] == hc.enhance_cost(tg, mt, hc.enhance25_rate(tg, mt, False, None, "-1"))
        roll = hc.random.random
        try:
            hc.random.random = lambda: 0.999
            e1 = ep(); up = body(hc.route(f"/player/{hc.iid(tg)}/enhance2.5/{hc.iid(mt)}/0/-1/-1")("", b"{}"))
            gap = hc.nice_ceil(hc.market_value(s, 6) - hc.market_value(s, 5))
            assert up["grade"] == 5 and up["usepoint"] == {"currency": "EP", "price": gap} and e1 - ep() == pv["enhancepay"], up
            e2, es1 = ep(), es()
            rs = body(hc.ENDPOINTS["/player/enhance2.5/restore"]("", b"{}"))
            assert rs["result"] and rs["grade"] == 6 and e2 - ep() == gap and es() == es1 and rs["usepoint"]["currency"] == "EP", rs
        finally:
            hc.random.random = roll
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
        for p in [p for p in t["players"] if p["spid"] == s]:
            hc.remove_player(t, p)
        hc.add_ep(st, ep0 - hc.coach_me(st, hc._default_slot(st))["money"]["ep"]); hc.add_es(st, es0 - hc.my_coach(st).get("es", 0)); hc._save(st)
        # Tukar v2 and Scout: no offer is worth more than the cards given, by value or Market value
        # (bonuses, scout ranges and asking again gave up to about twice back)
        legend, rows = [int(x) for x in hc.trade2_special()][:20], [r for r in hc.trade3_pool() if r[3] == 1][::500]
        for seed in range(60):
            rng = hc.random.Random(seed)
            give = [{"spid": rng.choice(legend) if rng.random() < 0.5 else rng.choice(rows)[1], "grade": rng.randint(1, 5)}
                    for _ in range(rng.randint(2, 10))]
            cap = hc.trade_cap(give)
            for o in hc.trade2_props(give, hc.random.Random(seed)) + hc.trade3_props(give, rng.choice(hc.SCOUTS), hc.random.Random(seed)):
                assert o["price"] + o.get("ep", 0) <= cap[0] and hc.market_value(o["spid"], o["grade"]) + o.get("ep", 0) <= cap[1], (give, o)
        # packs at their cards' worth at the dearest swing: a pack waits in Item saya until opened, so
        # no day makes one worth more than it cost (91 of 92 packs had such days; a one-player pack 11%)
        sample = sorted(hc.market_spids())[::3000]
        assert all(hc.market_swing(x, d) <= hc.MARKET_SWING_MAX for x in sample for d in range(400))
        assert hc.product_price(pk, 1) == hc.nice_ceil(hc.pack_ev(pk) * hc.MARKET_SWING_MAX)
        small = min((i for i in hc.PACKS if hc.pack_pool(i) and not hc.PACKS[i][1].get("draft")), key=lambda i: len(hc.pack_pool(i)))
        pool, grades = hc.pack_pool(small), hc.pack_grades(small)
        dear = max(sum(hc.market_value(x, g, hc.VM_EPOCH + d * 86400) * w * gw for x, w in pool for g, gw in grades)
                   for d in range(120)) / sum(w for _, w in pool) / sum(gw for _, gw in grades)
        assert dear > hc.pack_ev(small) * 1.0025 and dear * 0.9975 <= hc.product_price(small, 1), (small, dear)
        A("economy_set", players=1, packs=1, start_ep=1000000, dynamic=False, shopfree=True)
        assert hc.card_price(m88) == p1 and hc.pack_price(pk) == pp1 and hc.market_value(m88, 1) == p1 and hc.SHOP_FREE
        # Live Center > LIVE BOOST: the newest Team of the Week (a fake fut.gg here: 11 of my club's
        # players in week 2, plus a woman and a wrong birth date, who are left out), its players by FO3
        # id, the features flag, /static/db/LIVEBOOST, a match's boostweekid, the reward once a week
        hc.LIVEBOOST = os.path.join(tmp, "live_boost.json")
        assert hc.live_boost() is None and hc.live_boost_weekid() == 0
        assert "livecenter" not in body(hc.ENDPOINTS["/account"]("", b""))["features"]
        assert body(hc.ENDPOINTS["/static/db/LIVEBOOST"]("", b""))["liveplayers"] == {}
        snap = hc._state(); mine = snap["teams"]["0"]["players"]
        with hc.closing(hc._db()) as c:
            born = {pid: c.execute("SELECT birthdate FROM players WHERE playerid = ?", (pid,)).fetchone()[0].split()[0]
                    for pid in {int(p["spid"]) % 1000000 for p in mine}}
        xi = sorted(born)[:11]

        def card(pid, ovr, day, gender=1, dob=None, pos="CM"):
            m_, d_, y_ = (int(x) for x in (dob or born[pid]).split("/"))
            return {"basePlayerEaId": pid, "overall": ovr, "gender": gender, "birthDate": y_ * 10000 + m_ * 100 + d_,
                    "position": pos, "commonName": f"P{pid}", "createdAt": f"2026-{day}T18:00:44.769558Z"}
        week1 = [card(pid, 80, "10-28") for pid in xi[:3]]
        week2 = [card(pid, 90 - i, "11-04", pos="GK" if i < 2 else "ST") for i, pid in enumerate(xi)] + \
            [card(xi[0], 99, "11-04", gender=2), card(xi[1], 99, "11-05", dob="1/1/1900")]
        calls = []

        def futgg(path):
            calls.append(path)
            assert path == f"/players/v2/27/?page={len(calls)}&rarity_id=3"
            return {"data": (week1 + week2)[:9] if len(calls) == 1 else (week1 + week2)[9:], "next": 2 if len(calls) == 1 else None}
        now = calendar.timegm((2026, 11, 9, 0, 0, 0))
        lb = hc.totw_fetch(now, futgg)
        assert (lb["game"], lb["week"], lb["released"]) == (27, 2, calendar.timegm((2026, 11, 4, 18, 0, 44)))
        assert [p["pid"] for p in lb["players"]] == xi and len(lb["missing"]) == 2
        assert all(p["boost"] == 5 for p in lb["players"])  # user: "jika masuk totw rating tambah 5"
        # a 4-3-3: 1 GK (the other on the bench), 3 FW, then the next strikers in midfield and defence
        assert [p["best11"] for p in lb["players"]] == [True, False] + [True] * 9
        assert [p["position"] for p in lb["players"]] == ["GK"] * 2 + ["ST"] * 3 + ["CM"] * 3 + ["CB"] * 3
        assert hc.live_boost() == lb and hc.live_boost_weekid() == 2702
        calls.clear()
        assert hc.totw_fetch(now, futgg) == lb  # the same week: kept as it is
        assert body(hc.ENDPOINTS["/account"]("", b""))["features"]["livecenter"]["boost"] == {}
        s = body(hc.ENDPOINTS["/static/db/LIVEBOOST"]("", b""))
        assert (s["weekid"], s["month"], s["weekofmonth"]) == (2702, 11, 1)
        assert hc._utc(s["expiretime"]) > time.time() and list(s["liveplayers"]) == [str(hc.LIVEBOOST_GROUP)]
        assert s["liveplayers"]["2900"][0] == {"pid": xi[0], "boost": 5, "best11": True, "position": "GK"}
        assert hc.new_match("x", {"team": {"teamid": 1}}, {"team": {"teamid": 2}})["boostweekid"] == 2702
        claim = lambda week=2702, league=2900: body(hc.ENDPOINTS["/live/boost/collecting/rewards"](
            "", json.dumps({"weekid": week, "leagueid": league}).encode()))
        bad = {"result": False, "reason": "invalid week and league data"}
        assert claim() == bad  # a new club's cards are +0: not counted
        st = hc._state()
        for p in st["teams"]["0"]["players"]:
            p["grade"] = 1 if int(p["spid"]) % 1000000 in xi else p.get("grade", 0)
        hc._save(st)
        assert claim(2701) == bad and claim(league=13) == bad
        assert not body(hc.ENDPOINTS["/live/boost/info"]("", b""))["info"]["rewarded"]
        bag = len(hc.my_coach(hc._state()).get("bag", []))
        r = claim()
        assert r["result"] and r["items"][0]["itemid"] == hc.EP_ITEM and r["items"][0]["count"] == int(hc.LIVEBOOST_REWARD_EP * hc.ECONOMY["players"])
        assert len(hc.my_coach(hc._state())["bag"]) == bag + 1 and body(hc.ENDPOINTS["/live/boost/info"]("", b""))["info"]["rewarded"]
        assert claim() == {"result": False, "reason": "rewarded"}
        hc._save(snap)  # grades, Bag and reward as before
        # the client's string pack is served with the setting screens' labels fixed: day "Siang" (was
        # "Waktu"), weather 3 "Mendung" (was "Malam"); built once, the rest untouched
        src = hc.asset_path(hc.LANG_PACK)
        if src:
            hc.LANG_FIXED = os.path.join(tmp, "language_ind.big")
            out = hc.language_pack(src)
            fixed, orig = (json.loads(hc.client_big(f, hc.LANG_ENTRY).decode("utf-8-sig")) for f in (out, src))
            assert out == hc.LANG_FIXED and (fixed["COMMON_TIME_DAY"], fixed["COMMON_WEATHER_DUSK"]) == ("Siang", "Mendung")
            assert fixed["LIVEBOOST_CUSTOM_2900"] == "Team of the Week"  # Live Boost's one group
            # Skuad Pinjaman: its tooltip as the server runs the event, and the labels the pack lacks
            assert fixed["RENTAL_SQUAD_MAIN_SUB_TITLE_11"].startswith("Pemain sewaan pulang") and fixed["RENTAL_SQUAD_FREEROM_TITLE"] == "Skuad Pinjaman"
            keep = ("COMMON_TIME_DAY", "COMMON_WEATHER_DUSK", "LIVEBOOST_CUSTOM_2900", "RENTAL_SQUAD_MAIN_SUB_TITLE_11")
            added = {k: v for k, v in hc.LANG_ADD.items() if k not in orig}  # LANG_ADD's keys the pack lacks (Kontrak pemain's notices)
            assert {"CRAFTSYSTEM_NOTICE01", "CRAFTSYSTEM_NOTICE02"} <= set(added) and all(fixed[k] == v for k, v in added.items())
            assert {k: v for k, v in fixed.items() if k not in keep and k not in added} == {k: v for k, v in orig.items() if k not in keep}
            assert hc.language_pack(src) == out and hc.cdn_file(hc.LANG_PACK)[1] == open(out, "rb").read()
            built = os.stat(out).st_mtime_ns
            assert hc.language_pack(src) == out and os.stat(out).st_mtime_ns == built  # built once
            fixes, hc.LANG_FIXES = hc.LANG_FIXES, {**hc.LANG_FIXES, b'"x": "y"': b'"x": "z"'}
            assert hc.language_pack(src) == out and os.stat(out).st_mtime_ns != built  # new fixes: built again
            hc.LANG_FIXES = fixes
            # another language's pack (issue #41: Thai lacked Rekrut packs 11-13): its own keys stay,
            # LANG_ADD's and then the Indonesian pack's fill the gaps
            tha = json.loads(hc.lang_missing('﻿{"STAFFPACK_NAME_11": "x"}'.encode()).decode("utf-8-sig"))
            assert tha["STAFFPACK_NAME_11"] == "x" and tha["STAFFPACK_NAME_12"] == hc.LANG_ADD["STAFFPACK_NAME_12"]
            assert tha["COMMON_TIME_DAY"] == orig["COMMON_TIME_DAY"] and len(tha) == len(orig) + len(added)
            ind = json.loads(hc.lang_missing(hc.client_big(src, hc.LANG_ENTRY)).decode("utf-8-sig"))
            assert ind == {**orig, **added}  # the Indonesian pack itself gains only LANG_ADD's missing keys
            junk = os.path.join(tmp, "junk.big")
            with open(junk, "wb") as f:
                f.write(b"<html>404</html>")  # a damaged pack: served as it is, never no reply
            assert hc.client_big(junk, hc.LANG_ENTRY) is None and hc.language_pack(junk) == junk and hc.client_json(junk, "x") == {}
        assert A("summary")["coach"]["ep"] == 1500 and A("summary")["players"] == len(team()["players"])
        assert any(r["spid"] == 88158023 and r["ovr"] == 80 for r in A("search", q="Messi")["rows"])
        roster = lambda: {p["id"]: p for p in A("players")["players"]}
        if len(team()["players"]) >= hc.MAX_PLAYERS:  # make room for one
            A("player_remove", id=next(i for i, p in roster().items() if p["role"] == "RES"))
        new = A("player_add", spid=88158023, grade=3)["id"]
        assert (roster()[new]["role"], roster()[new]["ovr"]) == ("RES", 80 + hc.GRADE_BOOST[3])
        A("player_grade", id=new, grade=5); A("player_team", id=new, team="second")
        assert (roster()[new]["grade"], roster()[new]["team2"]) == (5, True)
        A("player_team", id=new, team="first"); assert not roster()[new]["team2"]
        A("player_remove", id=new); assert new not in roster()
        main_id = next(i for i, p in roster().items() if p["role"] == "MAIN")
        assert refused("player_remove", id=main_id) and refused("player_team", id=main_id, team="second")
        assert refused("player_add", spid=1) and refused("player_grade", id="nope", grade=2) and refused("ep", set="x")
        A("give_pack", itemid=pid, count=2); assert items()[0]["remains"] == 12 and refused("give_pack", itemid=2)
        gift = A("bag_send", title="tes", ep=777, pack=pid, count=1, spid=88158023, grade=2)["id"]
        g = next(b for b in body(hc.ENDPOINTS["/bag"]("", b"")) if b["_id"] == gift)
        assert g["items"] == [{"itemid": hc.EP_ITEM, "count": 777}, {"itemid": pid, "count": 1}] and g["players"] == [{"spid": 88158023, "grade": 2}]
        ep1, n0 = ep(), len(team()["players"])
        assert body(hc.route(f"/bag/{gift}/unpack")("", b"{}"))["result"]
        assert ep() == ep1 + 777 and items()[0]["remains"] == 13 and len(team()["players"]) == n0 + 1
        assert refused("bag_send", title="kosong") and refused("bag_send", pack=2)
        # staff on the account (issue #41): added at a grade, refused past STAFF_MAX, removed out of its frame
        n = len(A("staff")["staffs"])
        A("staff_add", stfid=9027, grade=8, count=2)
        got = A("staff")["staffs"][-2:]
        assert len(A("staff")["staffs"]) == n + 2 and [(s["stfid"], s["grade"], s["position"]) for s in got] == [(9027, 8, "PREMIUM")] * 2
        assert refused("staff_add", stfid=1, grade=2) and refused("staff_add", stfid=9027, count=hc.STAFF_MAX - n - 1)
        assert any(c["stfid"] == 4901 and c["helper"] for c in A("staff")["catalogue"])
        assert body(hc.route(f"/staff/appoint/{got[0]['id']}")("", b'{"slot": 4}'))["result"]
        A("staff_remove", id=got[0]["id"]); A("staff_remove", id=got[1]["id"])
        assert len(A("staff")["staffs"]) == n and got[0]["id"] not in hc.staff_slots(team()) and refused("staff_remove", id="nope")
        tbl = [{"ep": 1234}, {"pack": pid, "count": 2}]
        assert A("daily_set", table=tbl)["table"] == tbl == hc.daily_table()
        assert refused("daily_set", table=[{"ep": 0}]) and refused("daily_set", table=[{"pack": 2}]) and refused("daily_set", table=[])
        A("daily_reset")
        assert A("daily_grant")["title"].endswith("hari ke-1") and A("daily_grant")["title"].endswith("hari ke-2")
        assert [b["items"] for b in body(hc.ENDPOINTS["/bag"]("", b""))] == [hc.reward_items(r) for r in tbl]
        # --- match rewards (user: "tambahkan di admin page untuk setting reward hadiah match")
        assert A("match_rewards")["cards"] == hc.MATCH_REWARD_DEFAULT["cards"]
        res = {k: {"ep": 7000, "coach": 50, "player": 40} for k in ("win", "draw", "loss")}
        cfg = A("match_rewards_set", result=res, cards=[[1, {"ep": 777}], [2, pid]])
        assert cfg == {"result": res, "cards": [[1, {"ep": 777}], [2, pid]]} and hc.match_reward_cfg() == cfg
        ep1, n1, st = ep(), items()[0]["remains"], hc._state()
        rw = hc.match_reward(st, {"myTeam": "home", "opponentTeam": "away", "home": {"score": 1}, "away": {"score": 0}})
        hc._save(st)
        assert rw["money"]["reward"]["total"] == 7000 and rw["coach"]["reward"]["total"] == 50
        assert {c["itemid"] for c in rw["extra"]["availables"]} <= {hc.EP_ITEM, pid}
        mine = rw["extra"]["availables"][0]  # the opened card is credited
        assert (ep(), items()[0]["remains"]) == ((ep1 + 777, n1) if mine["itemid"] == hc.EP_ITEM else (ep1, n1 + 1))
        # issue #44: a sub who came on (matchStats substitutes, role 28) gets exp too; the unused bench
        # (reserved) does not
        st = hc._state(); pos = st["teams"][str(hc._default_slot(st))]["squad"]["positions"]
        xi, subs = [s for s in pos if s["pos"] < hc.POS_SUB][:1], [s for s in pos if s["pos"] == hc.POS_SUB][:2]
        card = lambda s: str(s.get("id") or s["spid"])
        stats = {"myTeam": "home", "opponentTeam": "away", "away": {"score": 0},
                 "home": {"score": 0, "players": [{"spid": int(xi[0]["spid"]) % 1000000}],
                          "substitutes": [{"spid": int(subs[0]["spid"]) % 1000000, "role": 28}],
                          "reserved": [{"id": card(subs[1]), "rating": 0}]}}
        got = [p["id"] for p in hc.match_reward(copy.deepcopy(st), stats)["players"]]
        assert got == [card(xi[0]), card(subs[0])], got
        # a shootout decides a level score: won on penalties = the win reward
        pen = lambda a, b: {"myTeam": "away", "opponentTeam": "home", "home": {"score": 1, "shootout": b}, "away": {"score": 1, "shootout": a}}
        assert [hc.match_outcome(pen(4, 3)), hc.match_outcome(pen(2, 4)), hc.match_outcome(pen(0, 0))] == [1, -1, 0]
        cfg2 = A("match_rewards_set", result={**res, "win": {"ep": 9000, "coach": 50, "player": 40}}, cards=cfg["cards"])
        assert hc.match_reward(copy.deepcopy(hc._state()), pen(5, 4))["money"]["reward"]["total"] == 9000
        assert A("match_rewards_set", result=res, cards=cfg2["cards"]) == cfg
        assert refused("match_rewards_set", result=res, cards=[[1, 2]]) and refused("match_rewards_set", result=res, cards=[])
        assert refused("match_rewards_set", result={}, cards=[[1, {"ep": 1}]])
        os.remove(hc.MATCH_REWARD_FILE)
        # --- Shop control (issue #10; user: "kontrol shop semua tab, bisa buat pack baru, paket baru
        # dll"): new pack / item / package / monthly with the client's icons, prices, hidden, tab lists
        sh = A("shop")
        assert sh["free"] and "8194" in sh["icons"] and {p["kind"] for p in sh["products"]} == set(hc.SHOP_KINDS)
        save = lambda kind, d, **k: A("shop_save", kind=kind, **k, **{"def": d})["itemid"]
        newpack = save("packs", {"name": "Tes WL", "icon": "8194", "seasons": [93], "top": 5, "grade": 3, "all": True})
        assert newpack == hc.SHOP_ID_BASE["packs"] + 1 and len(hc.pack_pool(newpack)) == 5
        assert any(e["product"]["itemid"] == newpack for e in hc.shop_list(hc._state()))
        assert refused("shop_save", kind="packs", **{"def": {"name": "kosong", "seasons": [93], "ovr": 130}})  # no card
        draftpack = save("packs", {"name": "Draft Tes", "icon": "200005", "seasons": [93], "draft": 3, "grade": 2, "all": True})
        assert hc.item_def(draftpack)["draft"] == {"season": 93, "grade": 2, "availables": 3}
        assert refused("shop_save", kind="packs", **{"def": {"name": "x", "seasons": [93, 91], "draft": 3}})  # one season
        capsule = save("items", {"name": "Kapsul Tes", "icon": "ID50071", "type": "PLAYER_EXP_CAPSULE", "exp": 500, "lv": 5,
                                 "season": 88})
        assert [hc.item_def(capsule)[k] for k in ("capsuleexp", "capsulelv", "capsuleseason")] == [500, 5, 88]
        assert refused("shop_save", kind="items", **{"def": {"name": "x", "icon": "nope", "type": "EP_LOTTERY", "ep": [1, 2]}})
        box = save("items", {"name": "Kotak Tes", "icon": "ID50121GP", "type": "ITEM_LOTTERY",
                             "prizes": [[1, newpack], [1, {"ep": 5000}]]})
        assert [a["itemid"] for a in hc.item_def(box)["availables"]] == [newpack, hc.EP_ITEM]
        pkg = save("packages", {"name": "Paket Tes", "icon": "801", "contents": [[box, 2], [hc.EP_ITEM, 1000]]})
        mon = save("monthly", {"name": "Bulanan Tes", "icon": "801", "days": 7, "now": [], "daily": [[newpack, 1]]})
        assert {e["shopcategory"] for e in hc.shop_list(hc._state()) if e["product"]["itemid"] in (box, pkg, mon)} == {
            "USE", "PACKAGE", "MONTHLYPACK"}
        ep1 = ep()
        assert post("/shop/purchase", {"shopid": pkg, "productid": pkg, "bunch": 0})["result"] and ep() == ep1 + 1000
        assert use(box)["result"] and use(box)["result"]
        A("shop_price", itemid=newpack, price=5000); assert hc.item_def(newpack)["bunches"][0]["price"] == 5000
        A("shop_price", itemid=newpack, price=None); assert hc.item_def(newpack)["bunches"][0]["price"] == 0
        A("shop_hide", itemid=8336, hidden=True)
        assert all(e["product"]["itemid"] != 8336 for e in hc.shop_list(hc._state()))
        assert not post("/shop/purchase", {"shopid": 8336, "productid": 8336, "bunch": 0})["result"]
        A("shop_hide", itemid=8336, hidden=False)
        save("items", {**hc.item_to(hc.ITEMS[501]), "name": "Lucky EP baru"}, itemid=501)  # a built-in, changed
        assert hc.item_def(501)["name"] == "Lucky EP baru"
        A("shop_delete", kind="items", itemid=501); assert hc.item_def(501)["name"] == "Lucky EP"  # back to the original
        assert refused("shop_delete", kind="packs", itemid=newpack)  # still a prize of the box
        A("shop_lists", lists={"new": [newpack, 501], "mp": [box], "chained": [[pkg, 801]]})
        assert hc.SHOP_NEW == (newpack, 501) and hc.MP_SHOP == (box,) and hc.CHAINED == {pkg: (pkg, 801)}
        assert refused("shop_lists", lists={"mp": [pkg]}) and refused("shop_lists", lists={"chained": [[newpack]]})
        A("shop_free", free=False); assert hc.item_def(8336)["bunches"][0]["price"] > 0; A("shop_free", free=True)
        hc.apply_shop(); assert newpack in hc.PACKS and hc.SHOP_NEW == (newpack, 501)  # all of it read back from the file
        # issue #15 + user: "tambahkan juga thai ,vietnam, korea": one string table per language
        err = admin.AdminError("e_day", ("day_n", 2), ("e_range", ("pack_count",), 1, 99))
        assert (err.text("en"), err.text(None), err.text("zh")) == (
            "Day 2: Pack count must be 1..99", "Hari 2: Jumlah pak harus 1..99", "第 2 天：卡包数量 必须在 1..99 之间")
        admin.check_i18n()
        assert set(admin.I18N) == {"id", "en", "zh", "th", "vi", "ko"}
        assert all("t_" + x in admin.I18N["id"] for x in hc.SHOP_ITEM_TYPES)  # a label for every item type
        hc.apply_shop({}); os.remove(hc.SHOP_FILE)  # the built-in Shop again
        # every Tim Cadangan card back to Tim inti at once (they went there by themselves while it held 36)
        st = hc._state(); next(p for p in team()["players"] if not hc.in_squad(team(), p))
        t = st["teams"][str(hc._default_slot(st))]; t["players"][-1]["team2"] = True; hc._save(st)
        assert A("team2_clear")["moved"] >= 1 and not any(p.get("team2") for p in team()["players"])
        st = hc._state(); hc.my_coach(st)["scout_used"] = {"vip": time.time()}; hc._save(st)
        assert [s["lock"] for s in A("summary")["scouts"]] == [False] * len(hc.SCOUTS)  # no cooldowns
        A("scouts_reset"); assert not any(s["lock"] for s in A("summary")["scouts"])
        assert len(A("log", n=5)["lines"]) <= 5
        # issue #36: the login notice's picture (admin Pengumuman tab -> gamedata/notice.png, served as
        # externalAssets/notice/notice.png); the user's own one is put back afterwards
        import base64
        def png(w, h):
            chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
            return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + b"\x40" * w for _ in range(h)))) + chunk(b"IEND", b""))
        kept = open(hc.NOTICE_IMAGE, "rb").read() if os.path.isfile(hc.NOTICE_IMAGE) else None
        try:
            assert refused("notice_set", png=base64.b64encode(png(640, 480)).decode())  # not the popup's 800x720 box
            assert refused("notice_set", png="not base64!")
            assert A("notice_set", png=base64.b64encode(png(800, 720)).decode())["has"]
            assert hc.cdn_file("externalAssets/notice/notice.png")[1] == png(800, 720)  # the Korean client's way
            if hc.ADMIN_PORT:  # this client's way: its web renderer opens the admin page's /notice
                assert hc.notice_url().startswith(f"http://127.0.0.1:{hc.ADMIN_PORT}/notice?t=")
            assert not A("notice_delete")["has"] and hc.cdn_file("externalAssets/notice/notice.png") is None
            assert hc.notice_url() == "about:blank"
        finally:
            if kept is not None:
                open(hc.NOTICE_IMAGE, "wb").write(kept)
        # issue #36: the Aset tab runs tools/fa_faces.py in the background, one kind at a time (a
        # stand-in here, which waits for the Stop button's file as the tool does)
        stand_in = ("import os, sys, time\nprint('stand-in', *sys.argv[1:], flush=True)\n"
                    f"while not os.path.isfile({admin.ASSET_STOP!r}): time.sleep(0.05)\nprint('stopped')")
        cmd, log = admin.ASSET_CMD, admin.ASSET_LOG
        admin.ASSET_CMD, admin.ASSET_LOG = [sys.executable, "-c", stand_in], os.path.join(tmp, "assets_fetch.log")
        try:
            assert refused("assets_fetch", kind="music")
            assert A("assets_fetch", kind="crests")["running"] == "crests"
            assert refused("assets_fetch", kind="faces")  # one at a time
            a = A("assets_stop")
            assert (a["running"], a["last"], a["lines"]) == (None, "crests", ["stand-in --crests", "stopped"])
            assert set(a["counts"]) == set(admin.ASSET_KINDS) and not os.path.exists(admin.ASSET_STOP)
            assert A("assets_fetch", kind="all")["running"] == "all"  # every picture kind in one run
            assert A("assets_stop")["lines"] == ["stand-in --all", "stopped"]
            # PR #39 follow-up: the club careers row runs tools/club_history.py fetch; its counts come
            # from gamedata/club_history.json (players with a career, players not found)
            careers, admin.CAREER_CMD, admin.CAREERS = (admin.CAREER_CMD, admin.CAREERS), admin.ASSET_CMD + ["fetch"], os.path.join(tmp, "club_history.json")
            json.dump({"players": {"1": [[73, 2017, -1, 0]], "2": []}, "teams": {}, "missing": 5}, open(admin.CAREERS, "w"))
            assert A("assets_fetch", kind="careers")["running"] == "careers"
            a = A("assets_stop")
            assert a["lines"] == ["stand-in fetch", "stopped"] and a["counts"]["careers"] == {"added": 2, "failed": 5}, a
            admin.CAREER_CMD, admin.CAREERS = careers
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(admin.__file__)), "tools"))
            import club_history
            club_history.cmd_selftest()
        finally:
            admin.ASSET_CMD, admin.ASSET_LOG = cmd, log
        # issue #36 (a user's 1,049 crests all "failed" and a rerun fetched none): only a 404 is final; a
        # refusal is not recorded, waits longer each time and stops the run after TRIES in a row
        import importlib.util, io, types, urllib.error
        spec = importlib.util.spec_from_file_location("fa_faces", os.path.join(os.path.dirname(os.path.abspath(admin.__file__)), "tools", "fa_faces.py"))
        fa = importlib.util.module_from_spec(spec); spec.loader.exec_module(fa)
        fa.EXT, fa.MANIFESTS = os.path.join(tmp, "ext36"), {**fa.MANIFESTS, "crests": os.path.join(tmp, "fa_crests.json")}
        json.dump({"added": [], "failed": {"crests/large/s1.png": "403", "crests/large/s2.png": "404"}}, open(fa.MANIFESTS["crests"], "w"))
        jobs = [(f"crests/large/s{i}.png", f"https://example.invalid/{i}") for i in range(1, 12)]
        fa.wanted_crests = lambda: jobs
        waits = []
        fa.time = types.SimpleNamespace(sleep=waits.append)
        def answer(req, timeout):
            i = int(req.full_url.rsplit("/", 1)[1])
            if i == 3:
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
            if i == 4:
                return io.BytesIO(b"\x89PNG crest")
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        real_urlopen, fa.urllib.request.urlopen = fa.urllib.request.urlopen, answer
        try:
            fa.ARGS[:] = ["--crests", "--delay", "0"]
            fa.main()
        finally:
            fa.urllib.request.urlopen = real_urlopen
        man = json.load(open(fa.MANIFESTS["crests"]))
        assert man == {"added": ["crests/large/s4.png"], "fo4": True, "failed": {"crests/large/s2.png": "404", "crests/large/s3.png": "404"}}
        # (issue #36, 2026-11-09: "automatically retry failed downloads") it goes on, waiting 10 s more,
        # doubling, up to 5 min; only TRIES refusals in a row end it
        assert waits == [0, 10, 0, 0, 10, 20, 40, 80, 160, 300] and open(os.path.join(fa.EXT, "crests", "large", "s4.png"), "rb").read() == b"\x89PNG crest"
        # issue #43 ("where to get crest and uniform?"): the club picker's kit pictures, home/away/third of a club
        real_index, real_asked = fa.hc.club_index, fa._asked
        fa.hc.club_index, fa._asked = (lambda: [("Italy", [(31, [47, 48])])]), (lambda pattern: [243, 47])
        try:
            u = fa.wanted_uniforms()
        finally:
            fa.hc.club_index, fa._asked = real_index, real_asked
        assert [r for r, _ in u[:4]] == ["uniforms/j0_243.png", "uniforms/j1_243.png", "uniforms/j2_243.png", "uniforms/j0_47.png"]
        assert len(u) == 9 and u[3][1] == fa.FA + "uniforms/j0_47.png" and "uniforms" in admin.ASSET_KINDS
        tries, fa.TRIES, waits[:] = fa.TRIES, 2, []
        fa.urllib.request.urlopen = answer
        try:
            assert fa.run("crests", fa.argparse.Namespace(delay=0, limit=0, remove=False)) is False and waits == [0, 10]
        finally:
            fa.urllib.request.urlopen, fa.TRIES = real_urlopen, tries
        # a player's picture fifaaddict lacks (or refuses) comes from FC Online (FO4) (user: "jika download
        # dari fo3 tidak ada pindah ke fo4"); the old run's 404s of player pictures are asked again
        fa.MANIFESTS["faces"] = os.path.join(tmp, "fa_faces.json")
        json.dump({"added": [], "failed": {"players/p7.png": "404", "players/p91000007.png": "404"}}, open(fa.MANIFESTS["faces"], "w"))
        fa.wanted_faces = lambda: [(f"players/p{i}.png", [f"{fa.FA}players/p{i}.png", fa.FO4_FACE.format(i)]) for i in (5, 6, 7, 8)] \
            + [("players/p91000007.png", [f"{fa.FA}players/p91000007.png"])]
        def answer2(req, timeout):
            u = req.full_url
            i, fo4 = int(re.search(r"/p(\d+)\.png", u).group(1)), "nexoncdn" in u
            if (i, fo4) in ((5, False), (7, True)):
                return io.BytesIO(b"\x89PNG " + (b"fo4" if fo4 else b"fa"))
            code = {(6, False): 404, (6, True): 403, (7, False): 404, (8, False): 503, (8, True): 403}[i, fo4]  # FO4's "not there" is 403
            raise urllib.error.HTTPError(u, code, "no", {}, None)
        waits[:] = []
        fa.urllib.request.urlopen = answer2
        try:
            fa.ARGS[:] = ["--delay", "0"]
            fa.main()
        finally:
            fa.urllib.request.urlopen = real_urlopen
        man = json.load(open(fa.MANIFESTS["faces"]))
        assert man == {"added": ["players/p5.png", "players/p7.png"], "fo4": True,
                       "failed": {"players/p91000007.png": "404", "players/p6.png": "404"}}, man  # p8: fifaaddict refused, tried next time
        assert open(os.path.join(fa.EXT, "players", "p7.png"), "rb").read() == b"\x89PNG fo4" and waits == [0, 0, 0, 0]
        # faces: each card's season picture (fifaaddict has p<spid>.png) and its player's (FO4's after it),
        # none of the added seasons; every card of the DB, not only the club's and the Market's
        spec.loader.exec_module(fa)
        w = dict(fa.wanted_faces())
        assert w["players/p91031432.png"] == [fa.FA + "players/p91031432.png"]
        assert w["players/p31432.png"] == [fa.FA + "players/p31432.png", fa.FO4_FACE.format(31432)]
        assert not [r for r in w if re.fullmatch(r"players/p(78|300|325)\d{6}\.png", r) and int(r[-10:-4]) < 700000]  # new players': FO4's
        with hc.closing(hc._db()) as c:
            pids = {r[0] for r in c.execute("SELECT DISTINCT playerid FROM players WHERE playerid < 700000")}
        assert all(f"players/p{p}.png" in w for p in pids)
        # the server's new players (user: "pemain baru kenapa tidak di search by nama"): their FO4 cards,
        # found by the birth date and name that gave them their id, have pictures on fifaaddict by card id
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(admin.__file__)), "tools"))
        import fo4_season, hashlib
        kept_fo4 = fo4_season.PIDS, fo4_season.CARDS
        fo4_season.PIDS, fo4_season.CARDS = os.path.join(tmp, "fo4_pids.json"), os.path.join(tmp, "fo4_cards.json")
        try:
            json.dump({"2007-7-13|lamine yamal": 700000}, open(fo4_season.PIDS, "w"))
            json.dump({"aaa": {"season": 325, "db": {"name": "Lamine Yamal", "birthdate": "13.07.2007"}},
                       "bbb": {"season": 300, "db": {"name": "Lamine Yamal", "birthdate": "13.07.2007"}},
                       "ccc": {"season": 300, "db": {"name": "Somebody Else", "birthdate": "1.01.2000"}}}, open(fo4_season.CARDS, "w"))
            assert fo4_season.new_player_cards() == {325700000: "aaa", 300700000: "bbb"}
        finally:
            fo4_season.PIDS, fo4_season.CARDS = kept_fo4
        fa.fo4_new_players = lambda: {325700000: "aaa", 300700000: "bbb"}
        w = dict(fa.wanted_faces())
        assert w["players/p700000.png"] == [fa.FA4.format("bbb")] == w["players/p300700000.png"]  # 26 LIVE's for him
        assert w.get("players/p325700000.png", [fa.FA4.format("aaa")]) == [fa.FA4.format("aaa")] and "players/p700001.png" not in w
        # fifaaddict answers an unknown card id with its silhouette: not there
        spec.loader.exec_module(fa)
        fa.EXT, fa.MANIFESTS = os.path.join(tmp, "ext36"), {**fa.MANIFESTS, "faces": os.path.join(tmp, "fa_faces2.json")}
        fa.FA4_NONE = hashlib.sha256(b"\x89PNG silhouette").hexdigest()
        fa.wanted_faces = lambda: [("players/p700000.png", [fa.FA4.format("bbb")]), ("players/p700001.png", [fa.FA4.format("zzz")])]
        fa.time = types.SimpleNamespace(sleep=lambda s: None)
        fa.urllib.request.urlopen = lambda req, timeout: io.BytesIO(b"\x89PNG " + (b"yamal" if "bbb" in req.full_url else b"silhouette"))
        try:
            fa.ARGS[:] = ["--delay", "0"]
            fa.main()
        finally:
            fa.urllib.request.urlopen = real_urlopen
        assert json.load(open(fa.MANIFESTS["faces"]))["failed"] == {"players/p700001.png": "404"}
        assert open(os.path.join(fa.EXT, "players", "p700000.png"), "rb").read() == b"\x89PNG yamal"
        # issue #38: the Database tab edits cards in a copy of fifadb (the real one stays as it is)
        # and club crests in a stand-in cache; tools/fo4_season.py build puts the edits back
        import gen_teams, sqlite3, importlib.util
        kept = (gen_teams.DB, admin.CARD_EDITS, admin.CREST_BACKUP, admin.INSTALL_CMD, hc.ASSETS, admin.CUSTOM_SEASONS, admin.SEASON_ICONS,
                hc.CUSTOM_COLORS, hc.FACES, admin.ASSET_LOG)
        hc.FACES = os.path.join(tmp, "faces")
        admin.ASSET_LOG = os.path.join(tmp, "install38.log")  # not the owner's gamedata/assets_fetch.log
        hc.CUSTOM_COLORS = os.path.join(tmp, "teamcolors.json")
        gen_teams.DB = shutil.copyfile(gen_teams.DB, os.path.join(tmp, "fifadb38.sqlite"))
        admin.CARD_EDITS, admin.CREST_BACKUP = os.path.join(tmp, "card_edits.json"), os.path.join(tmp, "crest_backup")
        admin.CUSTOM_SEASONS, admin.SEASON_ICONS = os.path.join(tmp, "seasons.json"), os.path.join(tmp, "season_icons")
        # the stand-in lives a second, so a second Terapkan while it runs is refused (it was flaky)
        admin.INSTALL_CMD = [sys.executable, "-c", "import sys, time; print('stand-in', *sys.argv[1:], flush=True); time.sleep(1)"]
        try:
            row = lambda spid: tuple(sqlite3.connect(gen_teams.DB).execute("SELECT * FROM players WHERE spid = ?", (spid,)).fetchone())
            messi, before = 91158023, row(91158023)
            found = {r["spid"] for r in A("search", q="158023")["rows"]}  # a player id: every card of his
            assert {158023, messi} <= found and {s % 1000000 for s in found} == {158023}
            assert {r["spid"] for r in A("search", q="91158023")["rows"]} == {messi}
            meta = A("card_meta")
            assert len(meta["traits"]) == 30 and len(meta["clubs"]) > 500 and [1, "Arsenal", 13] in meta["clubs"]
            c = A("card", spid=messi)
            assert (c["name"], c["season"], c["playerid"], c["orig"]) == ("Lionel Messi", 91, 158023, {}) and len(c["fields"]) == len(admin.CARD_FIELDS)
            assert refused("card_set", spid=messi, fields={"finishing": 151}) and refused("card_set", spid=messi, fields={"overallrating": 99})
            assert refused("card_set", spid=messi, fields={"teamid": 424242}) and refused("card_set", spid=1, fields={})
            ovr = hc.card_ovr(messi)
            c = A("card_set", spid=messi, fields={**c["fields"], "finishing": 99, "teamid": 1, "weakfootabilitytypecode": 5, "trait1": 3})
            assert c["club"] == "Arsenal" and c["leagueid"] == 13 and c["fields"]["finishing"] == 99 and c["ovr"] > ovr == c["ovr"] - 1
            assert hc.card_ovr(messi) == c["ovr"]  # the server's cached rating follows
            assert set(c["orig"]) == {"finishing", "teamid", "leagueid", "weakfootabilitytypecode", "trait1"}
            assert A("card_edits")["rows"] == [{"spid": messi, "name": "Lionel Messi", "season": 91, "changed": sorted(c["orig"])}]
            c = A("card_set", spid=messi, fields={"weakfootabilitytypecode": c["orig"]["weakfootabilitytypecode"]})  # set back by hand
            assert "weakfootabilitytypecode" not in c["orig"]
            assert A("card_revert", spid=messi)["orig"] == {} and row(messi) == before and A("card_edits")["rows"] == []
            assert refused("card_revert", spid=messi) and hc.card_ovr(messi) == ovr
            # build: a rebuilt season card gets its edit back (the rebuilt values become its originals);
            # other seasons' edited cards are not touched
            spec = importlib.util.spec_from_file_location("fo4_season", os.path.join(os.path.dirname(os.path.abspath(admin.__file__)), "tools", "fo4_season.py"))
            fo4 = importlib.util.module_from_spec(spec); spec.loader.exec_module(fo4)
            fo4.CARD_EDITS = admin.CARD_EDITS
            db = sqlite3.connect(gen_teams.DB); db.row_factory = sqlite3.Row
            live = db.execute("SELECT spid, finishing FROM players WHERE spid / 1000000 = 300 LIMIT 1").fetchone()
            json.dump({str(live["spid"]): {"orig": {"finishing": 1}, "new": {"finishing": 77}},
                       str(messi): {"orig": {"finishing": 90}, "new": {"finishing": 50}}}, open(admin.CARD_EDITS, "w"))
            assert fo4.apply_card_edits(db, (300,)) == 1
            assert db.execute("SELECT finishing FROM players WHERE spid = ?", (live["spid"],)).fetchone()[0] == 77
            assert row(messi) == before  # not rebuilt: left alone (not committed either)
            assert json.load(open(admin.CARD_EDITS))[str(live["spid"])]["orig"] == {"finishing": live["finishing"]}
            db.rollback(); db.close(); os.remove(admin.CARD_EDITS)
            # a card's 3D look (engine DB row): another of the player's heads, hair; install makes the row
            look = A("card", spid=messi)["look"]
            assert look["row"] == messi and not look["shared"] and [40158023, 158023] in look["faces"]
            assert A("card", spid=300158023)["look"]["shared"]  # 26 LIVE: no row of its own, his player's
            yamal = A("card", spid=300700000)["look"]  # a new player: the generic head install copies for him
            assert yamal["shared"] and yamal["fields"]["headclasscode"] == 1 and yamal["faces"] == [[yamal["row"], yamal["fields"]["headassetid"]]]
            assert refused("card_set", spid=messi, look={"face": 424242}) and refused("card_set", spid=messi, look={"haircolorcode": 99})
            c = A("card_set", spid=messi, look={"face": 40158023, "haircolorcode": 1, "hairtypecode": look["fields"]["hairtypecode"]})
            assert c["look_edit"] == {"face": 40158023, "haircolorcode": 1} and A("card_edits")["rows"][0]["changed"] == ["look"]
            # own seasons: a free code, a name and a badge; cards are copies (+ boost) with their perms
            icon = base64.b64encode(png(64, 64)).decode()
            def season(**p):  # refused() takes "name" itself
                try:
                    return admin.API["season_set"](p)
                except admin.AdminError:
                    return None
            assert season(code=91, name="Taken", icon=icon) is None and season(code=50, name="No badge") is None
            assert season(code=50, name="Admin 25", icon=icon)["seasons"] == [{"code": 50, "name": "Admin 25", "cards": 0, "icon": True}]
            c = A("season_card_add", code=50, spid=messi, boost=5)
            assert (c["spid"], c["season"], c["custom"], c["ovr"]) == (50158023, 50, True, A("card", spid=messi)["ovr"] + 5)
            assert c["fields"]["finishing"] == A("card", spid=messi)["fields"]["finishing"] + 5
            assert c["look"]["row"] == messi and refused("season_card_add", code=50, spid=messi) and refused("season_card_add", code=52, spid=messi)
            assert sqlite3.connect(gen_teams.DB).execute("SELECT transferable FROM permid WHERE spid = 50158023").fetchone() == (1,)
            # install's engine rows: the season card wears its source card's row, the edited card its look
            fo4.CUSTOM_SEASONS = admin.CUSTOM_SEASONS
            t, fld, shipped, _ = admin.engine_rows()
            by = dict(shipped)
            assert fo4.card_looks(t, fld, by) == 2
            get = lambda k, f, rows=by: t.get(rows[k], fld[f][0]) + fld[f][1]
            assert [get(50158023, f) for f in ("playerid", "spid", "headassetid")] == [50158023, 50158023, get(messi, "headassetid", shipped)]
            assert get(messi, "haircolorcode") == 1
            assert get(messi, "headassetid") == get(40158023, "headassetid")
            # a mod file: every own season, copied card and edit, made again on an empty server
            mod = A("mod_export")
            assert set(mod["seasons"]) == {"50"} and mod["seasons"]["50"]["cards"] == {"50158023": {"from": messi, "boost": 5}}
            assert A("season_delete", code=50)["seasons"] == [] and not os.path.exists(admin.season_icon(50))
            assert not sqlite3.connect(gen_teams.DB).execute("SELECT 1 FROM players WHERE spid / 1000000 = 50").fetchone()
            A("card_revert", spid=messi)
            assert A("mod_import", mod=mod) == {"done": 2, "failed": []}
            assert A("card", spid=50158023)["ovr"] == c["ovr"] and A("card", spid=messi)["look_edit"]["face"] == 40158023
            assert refused("mod_import", mod={"something": "else"})
            A("season_card_remove", spid=50158023); A("season_delete", code=50); A("card_revert", spid=messi)
            assert row(messi) == before and A("card_edits")["rows"] == []
            # own team colors (user: "bisa tambahkan team color baru"): members by filters, one boost;
            # the server lists them at once (next login), the crowd's themes leave them out
            def color(**p):  # refused() takes "name" itself
                try:
                    return admin.API["teamcolor_set"](p)
                except admin.AdminError:
                    return None
            assert color(name="Bad", nations=[999], amount=3) is None and color(name="Bad", players=[699999], amount=3) is None
            assert color(name="Bad", stat="speed", amount=3) is None and color(name="Bad", clubs=[424242], amount=3) is None
            r = color(name="Trio MSN", players=[158023, 176580, 190871], stat="ovr", amount=3)
            msn = next(c for c in r["colors"] if c["id"] == r["id"] == hc.TC_CUSTOM)
            assert msn["nplayers"] == 3 and msn["cards"] > 3 and messi in hc.tc_special_sets()[hc.TC_CUSTOM][0]
            tc = next(c for c in hc.tc_colors() if c["teamcolorid"] == hc.TC_CUSTOM)
            assert (tc["type"], tc["teamid"], tc["name"], tc["description"]) == ("special", str(hc.TC_CUSTOM), "Trio MSN", "Trio MSN")
            assert tc["boostgroups"] == [{"count_min": 3, "count_max": 11, "boosts": [{"type": "ovr", "amount": 3}]}]
            r = color(name="Samba", nations=[54], target="fw", stat="sprintspeed", amount=4, min_ovr=80)
            samba = next(c for c in r["colors"] if c["id"] == hc.TC_CUSTOM + 1)
            assert samba["nplayers"] >= 5 and all(hc.card_ovr(s) >= 80 for s in hc.tc_special_sets()[samba["id"]][0])
            hc.vm_theme_keys.cache_clear()
            assert not {hc.TC_CUSTOM, hc.TC_CUSTOM + 1} & set(hc.vm_theme_keys()["color"])
            assert color(id=hc.TC_CUSTOM, name="MSN", players=[158023, 176580, 190871], amount=2)["colors"][0]["amount"] == 2
            mod = A("mod_export")
            assert [c["name"] for c in mod["teamcolors"]] == ["MSN", "Samba"] and "id" not in mod["teamcolors"][0]
            assert A("teamcolor_delete", id=hc.TC_CUSTOM)["colors"][0]["name"] == "Samba"
            assert A("mod_import", mod={"fo3id_mod": 1, "teamcolors": mod["teamcolors"]}) == {"done": 2, "failed": []}
            assert [(c["id"], c["name"]) for c in A("teamcolors")["colors"]] == [(hc.TC_CUSTOM + 1, "Samba"), (hc.TC_CUSTOM + 2, "MSN")]
            # Terapkan ke game: fo4_season.py install in the background, its output on the page
            a = A("db_install")
            assert a["running"] == "install" and refused("db_install"), a
            admin.ASSET_JOB["proc"].wait(30)
            a = A("assets")
            assert (a["running"], a["last"], a["exit"], a["lines"]) == (None, "install", 0, [f"stand-in --game {hc.GAME}"])
            # club crests: both sizes into the cache, the cache's own pair kept for Kembalikan
            hc.ASSETS = os.path.join(tmp, "cache38")
            crest = lambda size, root=None: admin.crest_file(size, 241, root)
            os.makedirs(os.path.dirname(crest("large")))
            open(crest("large"), "wb").write(b"the cache's own crest")
            b64 = lambda *size: base64.b64encode(png(*size)).decode()
            assert refused("crest_set", teamid=241, large=b64(128, 128), small=b64(128, 128))  # not 256 x 256
            assert refused("crest_set", teamid=424242, large=b64(256, 256), small=b64(128, 128))  # no such club
            c = A("crest_set", teamid=241, large=b64(256, 256), small=b64(128, 128))
            assert c["custom"] and c["has"] == {"large": True, "small": True}
            assert (open(crest("large"), "rb").read(), open(crest("small"), "rb").read()) == (png(256, 256), png(128, 128))
            assert os.path.getsize(crest("small", admin.CREST_BACKUP)) == 0  # there was none: Kembalikan removes it
            c = A("crest_revert", teamid=241)
            assert not c["custom"] and c["has"] == {"large": True, "small": False}
            assert open(crest("large"), "rb").read() == b"the cache's own crest"
            # issue #36: own player pictures in hc.FACES, served before the cache's (p<spid>: that card,
            # p<player id>: every card of his); restoring deletes them
            drogba, cached = 91031432, os.path.join(hc.ASSETS, "externalAssets", "players", "p31432.png")
            os.makedirs(os.path.dirname(cached))
            open(cached, "wb").write(b"the cache's own face")
            f = A("face", id=31432)
            assert (f["has"], f["custom"]) == (True, False) and not A("face", id=drogba)["has"]
            assert refused("face_set", id=31432, png=b64(256, 256)) and refused("face_set", id=699999, png=b64(128, 128))  # 128 x 128, a known id
            assert A("face_set", id=drogba, png=b64(128, 128))["custom"] and A("face_set", id=31432, png=b64(128, 128))["custom"]
            assert hc.asset_path("externalAssets/players/p91031432.png") == os.path.join(hc.FACES, "p91031432.png")
            assert hc.cdn_file("externalAssets/players/p31432.png")[1] == png(128, 128)
            assert not A("face_revert", id=31432)["custom"] and open(hc.asset_path("externalAssets/players/p31432.png"), "rb").read() == b"the cache's own face"
            assert not A("face_revert", id=drogba)["has"] and os.listdir(hc.FACES) == []
            # a card's picture = another id's (issue #36: per season): the cache's, else fifaaddict.com's
            open(os.path.join(os.path.dirname(cached), "p8031432.png"), "wb").write(png(128, 128))
            assert A("face_from", id=drogba, **{"from": 8031432})["custom"]
            assert open(os.path.join(hc.FACES, "p91031432.png"), "rb").read() == png(128, 128)
            asked = []
            def fa_answer(req, timeout):
                asked.append(req.full_url)
                if "p10031432" in req.full_url or req.full_url in (admin.FO4_FACE.format(5), admin.FA4_FACE.format("bbb")):
                    return io.BytesIO(png(128, 128))
                raise urllib.error.HTTPError(req.full_url, 403 if "nexoncdn" in req.full_url else 404, "Not Found", {}, None)
            real_urlopen, admin.urllib.request.urlopen = admin.urllib.request.urlopen, fa_answer
            try:
                assert A("face_from", id=31432, **{"from": 10031432})["custom"]
                assert refused("face_from", id=31432, **{"from": 11031432})  # fifaaddict has none
                assert A("face_from", id=31432, **{"from": 5})["custom"]  # a player's: FO4's when fifaaddict has none
                assert refused("face_from", id=31432, **{"from": 6})  # neither
                real_fo4, admin.fo4_season = admin.fo4_season, lambda: types.SimpleNamespace(new_player_cards=lambda: {300700000: "bbb"})
                try:  # a new player's: his FO4 card's on fifaaddict
                    assert A("face_from", id=31432, **{"from": 700000})["custom"]
                finally:
                    admin.fo4_season = real_fo4
            finally:
                admin.urllib.request.urlopen = real_urlopen
            assert asked == [admin.FA_FACE.format(10031432), admin.FA_FACE.format(11031432), admin.FA_FACE.format(5), admin.FO4_FACE.format(5),
                             admin.FA_FACE.format(6), admin.FO4_FACE.format(6), admin.FA4_FACE.format("bbb")]
            A("face_revert", id=31432); A("face_revert", id=drogba)
            assert os.listdir(hc.FACES) == []
            # the Face field's name search: engine rows of the players found, a scan or a generic head
            h = A("heads", q="Drogba")["rows"]
            assert h and all(k % 1000000 == 31432 and n == "Didier Drogba" and isinstance(scan, bool) for k, n, _, scan in h)
            assert A("heads", q="D")["rows"] == []
            # a 3D face from a photo (tools/photo_head.py, user: "buat 3d model berdasarkan foto close up
            # pemain ... untuk pemain2 yang masih generic face"): the photo's points painted on a scanned
            # donor's face texture, kept for the player id; install's engine rows: his id row wears it
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(admin.__file__)), "tools"))
            import photo_head, bigdir
            import numpy as np
            from PIL import Image
            photo_head.cmd_selftest()
            mods, d = (admin.photo_head_mod(), photo_head), os.path.join(tmp, "photo_heads")  # the page's, install's
            ph_kept = [(m.DIR, m.HEADS) for m in mods]
            for m in mods:
                m.DIR, m.HEADS = d, os.path.join(d, "heads.json")
            try:
                gen = next(r[0] for r in sqlite3.connect(gen_teams.DB).execute("SELECT DISTINCT playerid FROM players ORDER BY playerid")
                           if r[0] in shipped and t.get(shipped[r[0]], fld["headclasscode"][0]) == 1)
                ph = mods[0]
                face = np.full((200, 160, 3), (205, 160, 140), np.uint8)
                face[80:90, 70:90] = 30  # a dark mark between the eyes
                pts = [[ph.UV[k][0] * 160 / 512, ph.UV[k][1] * 200 / 512] for k in ph.POINTS]
                photo = lambda img: (b := io.BytesIO(), Image.fromarray(img).save(b, "PNG"), base64.b64encode(b.getvalue()).decode())[2]
                assert A("photo_head", pid=gen)["id"] is None and A("photo_head", pid=gen)["points"] == list(ph.POINTS)
                assert refused("photo_head_make", pid=gen, png=photo(np.zeros((10, 1100, 3), np.uint8)), points=pts, row=messi)  # too big
                assert refused("photo_head_make", pid=gen, png=photo(face), points=pts[:-1], row=messi)  # a point short
                assert refused("photo_head_make", pid=gen, png=photo(face), points=pts, row=gen)  # not a scanned head
                r = A("photo_head_make", pid=gen, png=photo(face), points=pts, row=messi)
                assert (r["id"], r["row"]) == (ph.ID_BASE, messi) and A("photo_head_make", pid=gen, png=photo(face), points=pts, row=messi)["id"] == ph.ID_BASE
                tex = np.asarray(Image.open(os.path.join(d, f"{ph.ID_BASE}.png")))
                assert tex.shape == (512, 512, 3) and json.load(open(ph.HEADS))[str(ph.ID_BASE)]["donor"] == get(messi, "headassetid", shipped)
                by = dict(shipped)
                assert fo4.photo_heads(t, fld, by) >= 1
                assert (get(gen, "headassetid", by), get(gen, "headclasscode", by)) == (ph.ID_BASE, 0) and by[messi] == shipped[messi]
                # data_7.big: its own entries plus the head's files (the donor's mesh and hair, the new face)
                orig7 = next(p for p in (os.path.join(fo4.BACKUP, "data_7.big"), os.path.join(hc.GAME, "data", "data_7.big")) if os.path.isfile(p))
                big, n = photo_head.archive(hc.GAME, orig7)
                open(os.path.join(tmp, "data_7.big"), "wb").write(big)
                names = [e[0] for e in bigdir.entries(os.path.join(tmp, "data_7.big"))]
                assert n == 1 and names[:len(bigdir.entries(orig7))] == [e[0] for e in bigdir.entries(orig7)]
                assert sorted(names[-5:]) == sorted(p.format(ph.ID_BASE) for p in ph.FILES)
                put = ph.texture_image(bigdir.read(os.path.join(tmp, "data_7.big"), ph.FACE_FILE.format(ph.ID_BASE)))
                assert np.abs(put.astype(int) - tex).mean() < 6
                assert A("photo_head_delete", pid=gen)["id"] is None and os.listdir(d) == ["heads.json"]
                # from the player's picture (user: "Ya, pakai YuNet"): YuNet's points, a donor by skin
                # and hair; the club's generic players in one go
                look = ph.look(face, pts)
                assert len(look) == 5 and look[0] > 150 and abs(look[3]) < 3  # light skin, no beard
                donors = {1: [messi, look[0] + 20, *look[1:]], 2: [40158023, *look[:3], 60, look[4]], 3: [158023, *look]}
                assert ph.suggest(look, donors) == 3 and ph.suggest(look, {k: donors[k] for k in (1, 2)}) == 1  # no beard before a closer skin
                # skin against a donor's own picture (a lit picture reads lighter than a texture), and a
                # donor from the same side of ASIAN as the player (its eyes stay)
                pics = {1: [messi, look[0] - 40, *look[1:], look[:3], 14], 3: [158023, *look, [look[0] + 60, *look[1:3]], 167]}
                assert ph.suggest(look, pics) == 1 and ph.suggest(look, pics, 167) == 3 and ph.suggest(look, pics, 34) == 1
                for m in mods:
                    m.DONORS = os.path.join(d, "donors.json")
                dl = ph.donor_looks(hc.GAME, {get(messi, "headassetid", shipped): messi})
                assert list(dl) == [get(messi, "headassetid", shipped)] and dl[get(messi, "headassetid", shipped)][0] == messi
                assert ph.donor_looks(hc.GAME, {}) == dl  # kept in DONORS
                real_icon = os.path.join(kept[4], "externalAssets", "players", "p198176.png")  # de Vrij's FO3 picture
                if os.path.isfile(ph.MODEL) and os.path.isfile(real_icon):  # (the test fetches nothing)
                    got = ph.landmarks(ph.picture(real_icon))
                    assert abs(got[0][0] - 52) < 4 and abs(got[1][0] - 76.5) < 4 and abs(got[0][1] - 67) < 4, got
                    assert ph.landmarks(np.full((128, 128, 3), 150, np.uint8)) is None
                    real_donors, admin.photo_donors = admin.photo_donors, lambda: dl
                    try:
                        os.makedirs(os.path.dirname(cached), exist_ok=True)
                        shutil.copyfile(real_icon, os.path.join(os.path.dirname(cached), f"p{gen}.png"))
                        r = A("photo_head_icon", pid=gen)
                        assert len(r["points"]) == 11 and r["row"] == messi and r["donor"] == "Lionel Messi" and base64.b64decode(r["png"])[:4] == b"\x89PNG"
                        assert refused("photo_head_icon", pid=699999)
                        team = next(iter(hc._state()["teams"].values()))["players"]
                        generic = [c["spid"] for c in team if (admin.card_look(c["spid"], c["spid"] % 1000000) or {}).get("fields", {}).get("headclasscode") == 1]
                        if generic:
                            shutil.copyfile(real_icon, os.path.join(os.path.dirname(cached), f"p{generic[0] % 1000000}.png"))
                            r = A("photo_head_club")
                            assert [m[0] for m in r["made"]] == [generic[0] % 1000000] and r["made"][0][2] == "Lionel Messi"
                            assert {s[2] for s in r["skipped"]} <= {"phd_skip_icon", "phd_skip_face"}
                            assert A("photo_head_club")["made"] == []  # once
                            again = A("photo_head_club", redo=True)["made"]  # made again on request
                            assert [m[0] for m in again] == [generic[0] % 1000000] and len(ph.load()) == 1
                            A("photo_head_delete", pid=generic[0] % 1000000)
                    finally:
                        admin.photo_donors = real_donors
                else:
                    print("photo heads: no YuNet model or picture here, its checks skipped")
            finally:
                for m, (a, b) in zip(mods, ph_kept):
                    m.DIR, m.HEADS = a, b
                    m.DONORS = os.path.join(a, "donors.json")
        finally:
            (gen_teams.DB, admin.CARD_EDITS, admin.CREST_BACKUP, admin.INSTALL_CMD, hc.ASSETS, admin.CUSTOM_SEASONS, admin.SEASON_ICONS,
             hc.CUSTOM_COLORS, hc.FACES, admin.ASSET_LOG) = kept
            hc.tc_special_sets.cache_clear(); hc.tc_colors.cache_clear(); hc.vm_theme_keys.cache_clear()
            hc._OVR.clear(); hc._STATS.clear()
        # the game setting screen's "Default ball" (ball 0, "NOT FOUND") shows ball 6's picture, and a
        # small flag (none cached) is the nation's big one
        assert hc.asset_path("externalAssets/balls/ball_0.png") == hc.asset_path("externalAssets/balls/ball_6.png") is not None
        flag = sorted(f for f in os.listdir(os.path.join(hc.ASSETS, "externalAssets", "countries")) if f[:-4].isdigit())[0]
        assert hc.asset_path(f"externalAssets/countries/smallFlags/{flag}") == hc.asset_path(f"externalAssets/countries/{flag}")
        # HTTP: reads are GETs; a change needs POST + X-FO3-Admin (a cross-site form can't add it)
        srv = admin.start(hc, 0)
        url, web = f"http://127.0.0.1:{srv.server_address[1]}", urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            page = web.open(url + "/").read()
            assert b"FO3ID admin" in page and b"/*I18N*/" not in page and b'"lang_name": "\\u7b80\\u4f53\\u4e2d\\u6587"' in page
            assert web.open(url + "/icon/8194.png").read()[:4] == b"\x89PNG"  # the client's shop icon
            assert web.open(url + "/face/20801.png").read()[:4] == b"\x89PNG"  # the card dialog's picture: the one the game gets
            assert b'<img src="/notice.png?t=' in web.open(url + "/notice").read()  # the login notice page
            err = urllib.request.Request(url + "/api/player_grade", b'{"id": "nope", "grade": 2}',
                                         {"X-FO3-Admin": "1", "X-FO3-Lang": "en"}, method="POST")
            assert json.load(web.open(err)) == {"error": "player not found"}
            assert json.load(web.open(url + "/api/summary"))["coach"]["ep"] == ep()
            # every API the page reads without a body (a GET) has to be read-only, or the tab fails to load
            page = open(os.path.join(os.path.dirname(os.path.abspath(admin.__file__)), "admin", "index.html"), encoding="utf-8").read()
            assert set(re.findall(r'api\("(\w+)"\)', page)) <= admin.READ_ONLY
            assert json.load(web.open(url + "/api/vm_chat"))["lang"] in hc.VM_CHAT_LANGS
            for req in (urllib.request.Request(url + "/api/ep?add=5"),
                        urllib.request.Request(url + "/api/ep", b'{"add": 5}', method="POST")):
                try:
                    web.open(req); raise AssertionError("change without POST + header")
                except urllib.error.HTTPError as e:
                    assert e.code == 403
            ep1, hdr = ep(), {"X-FO3-Admin": "1", "Content-Type": "application/json"}
            assert json.load(web.open(urllib.request.Request(url + "/api/ep", b'{"add": 5}', hdr, method="POST")))["ep"] == ep1 + 5 == ep()
            assert json.load(web.open(urllib.request.Request(url + "/api/player_grade", b'{"id": "nope", "grade": 2}', hdr, method="POST"))) == {
                "error": "pemain tidak ditemukan"}
        finally:
            srv.shutdown(); srv.server_close()
        # --- Tim inti can hold the whole club; a new card joins Tim Cadangan ("-" in Skuad, user:
        # "neymar baru di pack harusnya jangan masuk res"), a login keeps the cards where they are,
        # and a Tim Cadangan card can always move to Tim inti
        assert hc.FIRST_TEAM_MAX == hc.MAX_PLAYERS
        st = hc._state(); t = st["teams"][str(hc._default_slot(st))]
        for p in t["players"]:
            p.pop("team2", None)
        while len(t["players"]) < 45:
            t["players"].append(dict(t["players"][-1], _id=hc.new_iid()))
        res = next(p for p in t["players"] if not hc.in_squad(t, p))
        res["team2"] = True
        hc._save(st)
        assert body(hc.ENDPOINTS[f"/coach/{hc._default_slot(st)}/default"]("", b""))["result"]
        t = hc._state()["teams"][str(hc._default_slot(st))]
        assert hc.first_team_count(t) == 44 and sum(bool(p.get("team2")) for p in t["players"]) == 1
        # the Skuad tab's move to Tim inti reads limits.firstTeam (missing = it never asks)
        assert hc.coach_me(hc._state(), hc._default_slot(hc._state()))["limits"]["firstTeam"] == hc.FIRST_TEAM_MAX
        new = hc.add_player(t, 88158023)
        assert new["team2"] and not next(p for p in hc.owned_team(t)["players"] if p["id"] == new["_id"]).get("state")
        hc._save(hc._state() | {"teams": {**hc._state()["teams"], str(hc._default_slot(st)): t}})
        res = next(p for p in t["players"] if p.get("team2"))
        assert body(hc.route(f"/player/{hc.iid(res)}/move/firstteam")("", b"{}"))["result"]
        # issue #44: Lepas (DELETE /player/<_id>) takes a card away for good, the reply's /coach/me without
        # it (the player stayed in the list); a starter stays, and a GET is no release
        t = hc._state()["teams"][str(hc._default_slot(st))]
        gone = next(p for p in t["players"] if not hc.in_squad(t, p))
        starter = next(p for p in t["players"] if hc.is_starter(t, p))
        release = lambda p, m="DELETE": body(hc.route(f"/player/{hc.iid(p)}")("", json.dumps({"_method": m}).encode()))
        assert release(gone, "GET") == {} and release(starter)["reason"] == "COMMON_PLAYER_INFO_REMOVE_NOTALLOWED"
        r = release(gone)
        assert r["result"] and hc.iid(gone) not in {p["_id"] for p in r["resources"][0]["body"]["team"]["players"]}
        assert len(hc._state()["teams"][str(hc._default_slot(st))]["players"]) == len(t["players"]) - 1
        assert release(gone) == {"result": False, "reason": "not exists player"}
        # issue #44: Kontrak pemain (craft) lists the best Legend players; a "Kotak player piece" gives 1-3
        # pieces of one of them; 10 pieces + a contract + the fee sign his card
        recipes = body(hc.ENDPOINTS["/mixin/recipes"]("", b""))
        assert len(recipes) == hc.CRAFT_DEFAULT["count"] and all(r["category"] == "player" for r in recipes.values())
        rid, r = next(iter(recipes.items()))
        spid, piece = r["result"]["normal"]["id"], r["materials"][0]["itemid"]
        assert spid // 1000000 in hc.TC_LEGENDS and hc.on_market(spid) and piece == hc.CRAFT_PIECE + int(rid)
        assert r["materials"][1] == {"itemid": hc.CRAFT_CONTRACT, "count": 1} and 0 < r["money"]["ep"] < r["result"]["normal"]["price"]
        st = hc._state(); coach = hc.my_coach(st)
        coach["items"] = [i for i in coach.get("items", []) if not hc.is_piece(i["itemid"]) and i["itemid"] != hc.CRAFT_CONTRACT]
        box = hc.inventory_add(coach, hc.CRAFT_BOX, 1); hc._save(st)
        got = body(hc.route(f"/item/{box['_id']}/use")("", json.dumps({"id": hc.CRAFT_BOX}).encode()))
        assert got["result"] and hc.is_piece(got["item"]["itemid"]) and 1 <= got["item"]["count"] <= 3
        assert got["item"]["staticdata"]["type"] == "ITEM_PLAYER_CRAFT" and got["item"]["staticdata"]["spid"] // 1000000 in hc.TC_LEGENDS
        mine = {i["itemid"]: i for i in hc.coach_me(hc._state(), hc._default_slot(st))["items"]}
        assert mine[got["item"]["itemid"]]["remains"] == got["item"]["count"] and hc.CRAFT_BOX not in mine
        cache = body(hc.ENDPOINTS["/static/cache/item"]("", json.dumps({"itemids": [piece]}).encode()))
        assert cache[str(piece)]["spid"] == spid and cache[str(piece)]["enhance"] == hc.CRAFT_DEFAULT["grade"]
        mix = lambda rid: body(hc.route(f"/mixin/{rid}")("", json.dumps({"materials": []}).encode()))
        assert mix(rid)["reason"] == "CRAFTSYSTEM_ERROR_02" and mix("1")["reason"] == "CRAFTSYSTEM_ERROR_03"
        st = hc._state(); coach = hc.my_coach(st)
        hc.inventory_add(coach, piece, hc.CRAFT_DEFAULT["pieces"] + 2); contract = hc.inventory_add(coach, hc.CRAFT_CONTRACT, 1)
        coach["ep"] = r["money"]["ep"] - 1; hc._save(st)
        assert mix(rid)["reason"] == "CRAFTSYSTEM_ERROR_05"
        assert body(hc.route(f"/item/{contract['_id']}/use")("", b"{}"))["reason"] == "ITEM_USE_ERROR"  # only Kontrak pemain spends it
        st = hc._state(); hc.my_coach(st)["ep"] = r["money"]["ep"] + 5; hc._save(st)
        players = len(hc._state()["teams"][str(hc._default_slot(st))]["players"])
        done = mix(rid)
        assert done["result"] and done["results"] == [{"spid": spid, "grade": hc.CRAFT_DEFAULT["grade"]}], done
        st = hc._state(); coach = hc.my_coach(st); t = st["teams"][str(hc._default_slot(st))]
        left = {i["itemid"]: i["remains"] for i in coach["items"]}
        assert coach["ep"] == 5 and hc.CRAFT_CONTRACT not in left and left[piece] >= 2
        assert len(t["players"]) == players + 1 and t["players"][-1]["spid"] == spid and t["players"][-1]["grade"] == hc.CRAFT_DEFAULT["grade"]
        assert any(p["spid"] == spid for p in done["resources"][0]["body"]["team"]["players"])
        # issue #44 ("Can we change the player list in craft system?"): the admin page's Shop tab sets the
        # list (one card a player, in its order), the pieces, the fee and the grade; reset = the default
        cr = A("craft")
        assert not cr["custom"] and len(cr["players"]) == hc.CRAFT_DEFAULT["count"] and cr["fee"] == 5 and cr["pieces"] == 10
        messi = next(r["spid"] for r in A("search", q="Messi")["rows"] if r["market"])
        assert refused("craft_set", players=[158023], pieces=4, fee=2, grade=3, count=30)  # season 0: an engine card
        with hc.closing(hc._db()) as c:  # spid twice, then another card of his: one recipe a player
            other = c.execute("SELECT spid FROM players WHERE playerid = ? AND spid != ? AND spid >= 1000000",
                              (spid % 1000000, spid)).fetchone()
        own = [messi, spid, spid] + ([other[0]] if other else [])
        cr = A("craft_set", players=own, pieces=4, fee=2.5, grade=3, count=30)
        assert cr["custom"] and [r["spid"] for r in cr["players"]] == [messi, spid] and cr["fee"] == 2.5
        rec = body(hc.ENDPOINTS["/mixin/recipes"]("", b""))
        assert list(rec) == [str(messi % 1000000), str(spid % 1000000)]
        assert rec[str(messi % 1000000)]["materials"][0]["count"] == 4 and rec[str(messi % 1000000)]["result"]["normal"]["grade"] == 3
        assert rec[str(messi % 1000000)]["money"]["ep"] == hc.sig3(hc.card_price(messi, 3) * 0.025)
        assert hc.piece_def(hc.CRAFT_PIECE + messi % 1000000)["enhance"] == 3
        assert refused("craft_set", players=[1234567], pieces=4, fee=2, grade=3, count=30)  # no such card
        assert refused("craft_set", players=[], pieces=0, fee=2, grade=3, count=30) and refused("craft_set", players=[], pieces=4, fee=101, grade=3, count=30)
        assert len(A("craft_set", players=[], pieces=10, fee=5, grade=1, count=12)["players"]) == 12  # the default list, 12 long
        assert A("craft_set", reset=True)["players"] == A("craft")["players"] and not os.path.isfile(hc.CRAFT_FILE)
        # --- Skuad Pinjaman (LoanSquadScreen): the month's loan squad
        R = lambda path, req=None: body((hc.ENDPOINTS.get(path) or hc.route(path))("", json.dumps(req).encode() if req is not None else b""))
        sent, real_timer, real_push = [], hc.threading.Timer, hc.messenger_push
        hc.messenger_push = lambda uri, b: sent.append((uri, b))
        hc.threading.Timer = lambda s, fn, args=(): type("T", (), {"start": lambda self: fn(*args)})()
        try:
            sid, first, last = hc.rental_season()
            assert hc.rental_season(calendar.timegm((2026, 12, 31, 17, 0, 0))) == (202701, 20270101, 20270131)  # 00:00 WIB
            limit = hc.rental_limit()
            assert limit >= 11 * hc.rental_price(*hc.RENTAL_BUDGET)
            static = R("/static/db/RENTALPLAYER")
            assert static["config"] == {"scout": {"point": hc.RENTAL_COST}, "search": {"grade": hc.RENTAL_GRADE_MAX}}
            assert len(static["formations"]) == len(hc.formation_table()["common"]) > 0
            f0 = next(iter(static["formations"].values()))
            assert f0["formationname"] and f0["position0"] == 0 and all(f"offset{i}{a}" in f0 and f"position{i}" in f0 for i in range(11) for a in "xy")
            info = R("/rentalplayer/squad")
            assert info == {"target": True, "startdate": first, "enddate": last, "scoutpoint": 0, "squadvalue": limit, "completed": False}
            fid = int(next(iter(static["formations"])))
            prop = R(f"/rentalplayer/squad/proposal/{fid}")["players"]
            assert len(prop) == 11 == len({r["spid"] % 1000000 for r in prop}) and prop == R(f"/rentalplayer/squad/proposal/{fid}")["players"]
            assert sum(r["price"] for r in prop) <= limit and all(r["expandme"] == "player" and "contracttype" not in r for r in prop)
            shape = hc.rental_formations()[fid]["positions"]
            pos_of = dict(c for by in hc.rental_pool().values() for cards in by.values() for c in cards)
            assert all(hc.RENTAL_LINE[pos_of[r["spid"]]] == hc.RENTAL_LINE[x] for r, x in zip(prop, shape))  # a card of the slot's line
            assert R("/rentalplayer/squad/proposal/99999") == {"players": []}
            for line in hc.LINES:
                for g in (1, hc.RENTAL_GRADE_MAX):
                    rows = R(f"/rentalplayer/search/{line}/{g}")["players"]
                    assert rows and all(r["grade"] == g and r["price"] <= limit * hc.RENTAL_SHARE for r in rows)
                    assert all(hc.RENTAL_LINE[pos_of[r["spid"]]] == line and hc.on_market(r["spid"]) for r in rows)
            assert R(f"/rentalplayer/search/FW/{hc.RENTAL_GRADE_MAX + 1}") == R("/rentalplayer/search/FW/0") == {"players": []}
            # confirm: 11 different players, Market cards, +1..+max, within the limit, once a season
            pick = [{"spid": r["spid"], "grade": r["grade"]} for r in prop]
            before = len(hc._state()["teams"][str(hc._default_slot(hc._state()))]["players"])
            tops = {}  # the five dearest candidates at the top grade (one card a player): over the limit by themselves
            for r in sorted((r for line in hc.LINES for r in R(f"/rentalplayer/search/{line}/{hc.RENTAL_GRADE_MAX}")["players"]), key=lambda r: -r["price"]):
                tops.setdefault(r["spid"] % 1000000, {"spid": r["spid"], "grade": r["grade"]})
            dear = list(tops.values())[:5] + [x for x in pick if x["spid"] % 1000000 not in list(tops)[:5]][:6]
            bad = lambda **kw: R("/rentalplayer/squad", {"formationid": fid, "players": pick, **kw})
            assert not bad(players=pick[:10])["result"] and not bad(players=pick[:10] + pick[:1])["result"]
            assert not bad(formationid=99999)["result"] and not bad(players=[{**pick[0], "grade": hc.RENTAL_GRADE_MAX + 1}] + pick[1:])["result"]
            assert not bad(players=[{"spid": 1234567, "grade": 1}] + pick[1:])["result"] and not bad(players="x")["result"]
            assert len(dear) == 11 and sum(hc.card_price(x["spid"], x["grade"]) for x in dear) > limit
            assert bad(players=dear) == {"result": False, "reason": "RENTAL_SQUAD_POPUP_TITLE_02"}
            assert len(hc._state()["teams"][str(hc._default_slot(hc._state()))]["players"]) == before and not sent
            done = R("/rentalplayer/squad", {"formationid": fid, "players": pick})
            assert done["result"] and done["formationId"] == fid
            got = [r["body"] for r in done["resources"]]
            # the game's club gets the cards by a push to the players' partial path, {_id: card}
            assert sent == [("/coach/me?path=team.players.$", {c["_id"]: c for c in got})] and len(sent[0][1]) == 11
            assert [(c["spid"], c["grade"]) for c in got] == [(x["spid"], x["grade"]) for x in pick]  # slot order
            assert all(c["contracttype"] == "rental" and c["state"] == "first" and c["id"] == c["_id"] and c["price"] > 0 for c in got)
            st = hc._state(); slot = hc._default_slot(st); team = st["teams"][str(slot)]
            assert len(team["players"]) == before + 11 == len(hc.coach_me(st, slot)["team"]["players"])
            assert bad() == {"result": False, "reason": "RENTAL_SQUAD_POPUP_TEXT_05"}
            info = R("/rentalplayer/squad")
            assert info["completed"] and info["formationid"] == fid and [c["playerid"] for c in info["players"]] == [c["id"] for c in got]
            assert all(c["contracttype"] == "rental" and c["price"] == hc.card_price(c["spid"], c["grade"]) for c in info["players"])
            # a rented card is not for sale, release, upgrade or trade
            rid = got[0]["id"]; card = next(x for x in team["players"] if hc.iid(x) == rid)
            assert not body(hc.ENDPOINTS["/transfer/offer"]("", json.dumps({"playerobjectid": rid, "price": 1}).encode()))["result"]
            assert not body(hc.route(f"/player/{rid}")("", b'{"_method": "DELETE"}'))["result"]
            twin = hc.add_player(team, card["spid"], 1)
            assert hc.enhance_pair(team, rid, hc.iid(twin)) == (None, None) == hc.enhance_pair(team, hc.iid(twin), rid)
            hc.remove_player(team, twin)
            assert hc.card_bound(card) and not hc.card_bound(twin)
            own = [{"spid": i} for i in range(20)]  # while cards are rented, 18 of the club's own stay
            assert not hc.rental_keeps({"players": own[:18] + hc.rental_cards(team)}) and hc.rental_keeps({"players": own[:19] + hc.rental_cards(team)})
            assert hc.rental_keeps({"players": own[:18]}) and not hc.rental_keeps({"players": own + hc.rental_cards(team)}, 3)
            # scout points: a live match of mine while the squad is rented; a transfer takes RENTAL_COST
            put = lambda i: R(f"/rentalplayer/{i}", {"_method": "PUT"})
            for outcome in (1, 0, -1):
                hc.rental_match(st, outcome)
            assert hc.my_coach(st)["rental"]["points"] == sum(hc.RENTAL_POINTS); hc._save(st)
            assert R("/rentalplayer/squad")["scoutpoint"] == sum(hc.RENTAL_POINTS) < hc.RENTAL_COST
            assert put(rid) == {"result": False, "reason": "INVALID_PARAM"}
            st = hc._state(); hc.my_coach(st)["rental"]["points"] = hc.RENTAL_COST + 7; hc._save(st)
            moved = put(rid)
            assert moved["result"] and moved["scoutpoint"] == 7 and moved["price"] == got[0]["price"] and moved["resources"][0]["uri"] == "/coach/me"
            assert not put(rid)["result"] and not put("nosuchcard")["result"]
            assert R(f"/rentalplayer/{rid}") == {}  # a GET of the path: as before
            st = hc._state(); team = st["teams"][str(slot)]; card = next(x for x in team["players"] if hc.iid(x) == rid)
            assert card["contracttype"] == "normal" and card["loan"] and "rental" not in card and hc.card_bound(card)
            assert R("/rentalplayer/squad")["players"][0]["contracttype"] == "normal"
            assert not body(hc.ENDPOINTS["/transfer/offer"]("", json.dumps({"playerobjectid": rid, "price": 1}).encode()))["result"]
            # the month ends: the rented cards leave, the transferred one stays, the season starts over
            assert hc.coach_cards_push(st, slot, gone=["a", "b"]) == ("/coach/me?path=team.players.$", {"a": None, "b": None})
            assert not hc.rental_expire(st) and hc.rental_expire(st, sid + 1)
            assert not hc.rental_cards(team) and len(team["players"]) == before + 1 and "rental" not in hc.my_coach(st)
            left = {hc.iid(x) for x in team["players"]}
            assert rid in left and all(str(s.get("id") or s["spid"]) in left for s in team["squad"]["positions"])
            hc._save(st)
            assert R("/rentalplayer/squad") == {"target": True, "startdate": first, "enddate": last, "scoutpoint": 0, "squadvalue": limit, "completed": False}
            hc.rental_match(st, 1); assert "rental" not in hc.my_coach(st)  # no squad, no points
            st = hc._state(); hc.remove_player(st["teams"][str(slot)], next(x for x in st["teams"][str(slot)]["players"] if hc.iid(x) == rid)); hc._save(st)
        finally:
            hc.threading.Timer, hc.messenger_push = real_timer, real_push
        # --- delete a manager: PUT /coach/<slot>/postponed -> "postpone" until hideat (restorable with
        # DELETE /coach/<_id>/postponed), then the slot is free and the club data goes with it
        slot = hc._default_slot(hc._state()); cid = hc.coach_id(slot)
        coaches = lambda: body(hc.ENDPOINTS["/coaches"]("", b""))
        mine_c = lambda: next((x for x in coaches()["coachlist"] if x["_id"] == cid), None)
        postpone = lambda key, method: body(hc.route(f"/coach/{key}/postponed")("", json.dumps({"_method": method}).encode()))
        assert mine_c()["recoveryperiod"] == hc.COACH_RECOVERY and "hideat" not in mine_c()
        # manager select screen: native LoadFrontPlayers needs teamid + frontplayer (captain, club kit)
        fp = mine_c()["frontplayer"]
        assert isinstance(fp["id"], str) and fp["kit"]["home"]["id"] == mine_c()["teamid"] << 5
        assert fp["spid"] and "preferredposition1" in fp["staticdata"]
        mine_team = hc.coach_me(hc._state(), slot)["team"]["players"]
        assert fp["spid"] == max(mine_team, key=lambda x: x.get("price", 0))["spid"]  # most valuable
        # Beginner's Guide league tutorial script: a JS source string, never "{}" (crashed native)
        assert body(hc.route("/static/bescript/campaign_tutorial01")("", b"")).startswith("function main()")
        # the startup notice popup passes url to native LoadWebAddress: never missing
        assert body(hc.route("/game/inweburl/notice")("", b""))["url"]
        assert postpone(slot, "PUT")["result"]
        assert mine_c()["state"] == "postpone" and mine_c()["hideat"].endswith(".000Z") and coaches()["defaultcoachid"] == ""
        assert postpone(cid, "DELETE")["result"] and mine_c()["state"] == "active" and coaches()["defaultcoachid"] == cid
        assert postpone(slot, "PUT")["result"]
        st = hc._state(); hc.my_coach(st)["deleteat"] = time.time() - 1; st["offers"] = []; hc._save(st)  # restore time over
        assert mine_c() is None and coaches()["defaultcoachid"] == ""
        st = hc._state(); assert str(slot) not in st["teams"] and "offers" not in st and "cups" not in st
        # a purged manager takes only his own Market offers (one from before slots: the default's)
        two = {"defaultcoachid": "b", "teams": {"0": {}, "1": {}}, "offers": [{"slot": 0}, {"slot": 1}, {"_id": "old"}],
               "coaches": [{"_id": "a", "slot": 0, "state": "postpone", "deleteat": 0}, {"_id": "b", "slot": 1}]}
        assert [c["_id"] for c in hc.purge_deleted_coaches(two)] == ["a"] and two["offers"] == [{"slot": 1}, {"_id": "old"}]
        assert postpone(cid, "DELETE") == {"result": False, "reason": "already removed"}
    finally:
        shutil.rmtree(tmp)
    print("league + cup + market + upgrade + trade + trade3 + daily + shop + admin + loan squad + coach delete OK")


if __name__ == "__main__":
    main()
