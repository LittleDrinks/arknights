"""Small, deliberately simple baseline; all choices are made through the player interface.

It reads the opening briefing, chooses a strategy, buys up to four operators per
round, deploys from hand, assigns ordinary equipment, and readies. No win claim.
"""


def play(game):
    state = game.observe()
    attempted, bought, round_ = set(), 0, None
    while not state["terminal"]:
        if state["round"] != round_:
            round_, attempted, bought = state["round"], set(), 0
        own, public = state["own"], state["public"]
        operations = {a["t"]: a for a in state["actions"]}
        action = None
        if state["phase"] == "INFO_CHECK" and state["can_act"]:
            # Public opening context is available before choosing a strategy.
            boss = public.get("bossId")
            if boss:
                game.lookup({"kind": "bosses", "id": boss})
            action = {"type": "confirm", "setupRevision": public["setupRevision"]}
        elif state["phase"] == "BAND_DRAFT" and state["can_act"]:
            choices = operations.get("g.band", {}).get("targets", {}).get("bandId", [])
            if choices:
                game.lookup({"kind": "bands", "id": choices[0]})
                action = {"type": "band", "bandId": choices[0]}
        elif "g.choice" in operations and state["can_act"]:
            targets = operations["g.choice"]["targets"]
            choices = targets.get("idx", [])
            if choices:
                action = {"type": "choice", "idx": choices[0]}
                if targets.get("choiceId"):
                    action["choiceId"] = targets["choiceId"][0]
        elif state["phase"] == "PREP" and state["can_act"]:
            # Resolve displayed rewards before ordinary purchases.
            offer = own["shop"].get("rewardOffer")
            if offer and None in own["hand"]:
                for idx, slot in enumerate(offer["slots"]):
                    key = ("reward", idx, slot.get("id"))
                    if not slot.get("sold") and slot.get("price", 0) <= own["funds"] and key not in attempted:
                        action = {"type": "reward", "idx": idx}
                        attempted.add(key)
                        break
            # Give regular equipment to deployed operators; every attempt is logged.
            if action is None:
                for item in [p for p in own["hand"] + own["temp"] if p and p["kind"] == "item"]:
                    for carrier in [p for p in own["board"] if p["kind"] == "chess" and len(p["items"]) < 2]:
                        key = ("equip", item["uid"], carrier["uid"])
                        if key not in attempted:
                            attempted.add(key)
                            action = {"type": "equip", "itemUid": item["uid"], "targetUid": carrier["uid"]}
                            break
                    if action:
                        break
            if action is None and own["deployCount"] < own["deployCap"]:
                board = operations.get("g.move", {}).get("targets", {}).get("board")
                occupied = {(p["row"], p["col"]) for p in own["board"]}
                if board:
                    for piece in [p for p in own["hand"] + own["temp"] if p and p["kind"] == "chess"]:
                        for row in range(board["rows"][0], board["rows"][1] + 1):
                            for col in range(board["cols"][0], board["cols"][1] + 1):
                                key = ("move", piece["uid"], row, col)
                                if (row, col) not in occupied and key not in attempted:
                                    attempted.add(key)
                                    action = {"type": "move", "uid": piece["uid"],
                                              "to": {"area": "board", "row": row, "col": col}, "dir": "RIGHT"}
                                    break
                            if action:
                                break
                        if action:
                            break
            if action is None and own["board"] and None in own["hand"] and ("item-purchase",) not in attempted:
                items = [(s["price"], idx) for idx, s in enumerate(own["shop"]["slots"])
                         if s and s["kind"] == "item" and not s["sold"] and s["price"] <= own["funds"]]
                attempted.add(("item-purchase",))
                if items:
                    _, slot = min(items)
                    game.lookup({"kind": "items", "id": own["shop"]["slots"][slot]["id"]})
                    action = {"type": "buy", "slot": slot}
            if action is None and bought < 4 and None in own["hand"]:
                choices = [(s["price"], idx) for idx, s in enumerate(own["shop"]["slots"])
                           if s and s["kind"] == "chess" and not s["sold"] and s["price"] <= own["funds"]
                           and ("buy", idx, s["id"]) not in attempted]
                if choices:
                    _, slot = min(choices)
                    item = own["shop"]["slots"][slot]
                    game.lookup({"kind": "chess", "id": item["id"]})
                    attempted.add(("buy", slot, item["id"]))
                    action = {"type": "buy", "slot": slot}
                    bought += 1
            if action is None and own.get("canReady"):
                action = {"type": "ready", "ready": True}
            if action is None:
                # Untimed preparation cannot resolve overflow merely by waiting.
                for piece in [p for p in own["temp"] if p]:
                    if piece["kind"] in ("chess", "item"):
                        kind = "sell" if piece["kind"] == "chess" else "destroy"
                        key = (kind, piece["uid"])
                        if key not in attempted:
                            attempted.add(key)
                            action = {"type": kind, "uid": piece["uid"]}
                            break
                    elif piece["kind"] == "token" and None in own["hand"]:
                        key = ("stow", piece["uid"])
                        if key not in attempted:
                            attempted.add(key)
                            action = {"type": "move", "uid": piece["uid"],
                                      "to": {"area": "hand", "idx": own["hand"].index(None)}}
                            break
                if action is None:
                    # Clear a hand slot for a remaining temporary summon.
                    for piece in [p for p in own["hand"] if p]:
                        if piece["kind"] in ("chess", "item"):
                            kind = "sell" if piece["kind"] == "chess" else "destroy"
                            key = ("clear", piece["uid"])
                            if key not in attempted:
                                attempted.add(key)
                                action = {"type": kind, "uid": piece["uid"]}
                                break
                if action is None:
                    raise RuntimeError("Baseline cannot resolve this preparation; inspect the saved prefix")
        if action is None:
            state = game.wait()
        else:
            state = game.act(action)["state"]
