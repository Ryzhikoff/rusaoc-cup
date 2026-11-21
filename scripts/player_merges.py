"""Confirmed player identities, display aliases and source-preserving merges."""

import copy
import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "data" / "goodgame-identities.json"


def merge_aliases():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return {alias: config["aliases"][alias] for alias in config.get("mergePlayers", [])}


def build_aliases(names, extra_aliases=None):
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    pairs = [*config.get("aliases", {}).items(), *(extra_aliases or {}).items()]
    key = lambda name: name.strip().casefold()
    graph, labels = {}, {}
    for alias, target in pairs:
        a, b = key(alias), key(target)
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
        labels.setdefault(a, alias)
        labels.setdefault(b, target)
    names = sorted(set(names), key=lambda name: (key(name), name))
    labels.update((key(name), name) for name in names)
    labels.update(config.get("aliasDisplayNames", {}))
    result = {}
    for name in names:
        current = key(name)
        seen, pending = {current}, [current]
        while pending:
            for other in graph.get(pending.pop(), ()):
                if other not in seen:
                    seen.add(other)
                    pending.append(other)
        aliases = sorted((labels[other] for other in seen if other != current), key=key)
        if aliases:
            result[name] = aliases
    return result


def merge_players(snapshot):
    aliases = merge_aliases()
    result = copy.deepcopy(snapshot)
    players = {}
    for player in result["players"]:
        name = aliases.get(player["name"].strip().casefold(), player["name"])
        player["name"] = name
        if name not in players:
            players[name] = player
            continue
        target = players[name]
        overlap = {row[0] for row in target["results"]} & {row[0] for row in player["results"]}
        if overlap:
            raise ValueError(f"Cannot merge {name}: overlapping tournament results {sorted(overlap)}")
        target["results"] = sorted(target["results"] + player["results"], key=lambda row: row[0])
        for field in ("places", "goodgamePlaces"):
            if field in player:
                previous = target.get(field, [0, 0, 0, 0])
                if len(previous) != 4 or len(player[field]) != 4:
                    raise ValueError(f"Cannot merge {name}: invalid {field}")
                target[field] = [a + b for a, b in zip(previous, player[field])]
        if "goodgamePlacements" in player:
            target.setdefault("goodgamePlacements", {}).update(player["goodgamePlacements"])
    result["players"] = list(players.values())
    if "goodgameIdentities" in result:
        result["goodgameIdentities"] = {
            user_id: aliases.get(name.strip().casefold(), name)
            for user_id, name in result["goodgameIdentities"].items()
        }
    return result
