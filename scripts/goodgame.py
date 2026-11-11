"""Supplement empty spreadsheet events with completed GoodGame brackets.

Results are match wins/losses, not map scores. The Google Sheet is always the
base of a new snapshot; supplements are never added to yesterday's totals.
"""

import copy
import datetime
import gzip
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import google_sheet
import liquipedia

API = "https://cups.goodgame.ru/graphql"
GAME_ID = "5e4281c06bd25e29badc5da1"
ORGANISER_ID = 611086
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{google_sheet.SHEET_ID}/edit"
CONFIG = Path(__file__).resolve().parents[1] / "data" / "goodgame-identities.json"
LIST_QUERY = """query cups($pagination: PaginationInput, $filter: CupsFilterInput) {
  cups(pagination: $pagination, filter: $filter) {
    id title url start status creator { id }
  }
}"""
VIEW_QUERY = """query getCupsByUrlView($url: String!) {
  getCupsByUrlView(url: $url) { cup {
    id title url start status participationType participationSize format
    players { id status gridId lineup {
      id username profile { games { account game } }
    } }
  } }
}"""


class IncompleteTournament(ValueError):
    """An event needs a roster or a supported, fully recorded bracket."""


def graphql(query, variables, field):
    request = Request(API, data=json.dumps([{"query": query, "variables": variables}]).encode(),
                      headers={"Content-Type": "application/json", "Accept-Encoding": "gzip",
                               "User-Agent": liquipedia.USER_AGENT})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=60) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
            result = json.loads(payload)
            if not isinstance(result, list) or len(result) != 1:
                raise ValueError("GoodGame: unexpected API response")
            result = result[0]
            if result.get("errors") or field not in result.get("data", {}):
                raise ValueError(f"GoodGame: missing {field}: {result.get('errors')}")
            return result["data"][field]
        except (HTTPError, URLError, TimeoutError) as error:
            if isinstance(error, HTTPError) and error.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"GoodGame API: {error}") from error
            if attempt == 3:
                raise RuntimeError(f"GoodGame unavailable: {error}") from error
            retry = error.headers.get("Retry-After", "") if isinstance(error, HTTPError) else ""
            delay = max(5 * (attempt + 1), int(retry) if retry.isdigit() else 0)
            print(f"GoodGame unavailable; retrying in {delay}s", flush=True)
            time.sleep(delay)


def cup_number(name):
    match = re.fullmatch(r"Rusaoc Cup (\d+)", name, re.I)
    return int(match[1]) if match else None


def fetch_completed(minimum=1):
    """Read all pages for this game; ignore unrelated organisers and qualifiers."""
    found, seen = {}, set()
    for page in range(1, 51):
        rows = graphql(LIST_QUERY, {"pagination": {"page": page}, "filter": {
            "status": {"eq": "CLOSED"}, "game": {"eq": GAME_ID}}}, "cups")
        if not isinstance(rows, list):
            raise ValueError("GoodGame: missing tournament list")
        for row in rows:
            if row["id"] in seen:
                raise ValueError("GoodGame: repeated archive page")
            seen.add(row["id"])
            match = re.match(r"^RUSAOC CUP\s+(\d+)(?:\s*\||\s*$)", row["title"], re.I)
            if not match or row.get("creator", {}).get("id") != ORGANISER_ID:
                continue
            if row["status"] != "CLOSED":
                raise ValueError("GoodGame: unfinished event in completed archive")
            number = int(match[1])
            if number < minimum:
                continue
            if number in found:
                raise ValueError(f"GoodGame: duplicate Rusaoc Cup {number}")
            if not re.fullmatch(r"[a-z0-9-]+", row["url"]):
                raise ValueError("GoodGame: invalid tournament URL")
            found[number] = row
        if len(rows) < 100:
            if not seen:
                raise ValueError("GoodGame returned an empty tournament archive")
            return found
        time.sleep(1)
    raise ValueError("GoodGame archive pagination limit exceeded")


def name_key(name):
    # No fuzzy matching: punctuation and unrelated nicknames remain distinct.
    return name.strip().casefold()


class Identities:
    def __init__(self, sheet, config):
        self.config = config
        self.resolved_users = {}
        self.names = {}
        for player in sheet["players"]:
            self.names.setdefault(name_key(player["name"]), []).append(player["name"])

    def name(self, value):
        target = self.config.get("aliases", {}).get(name_key(value), value.strip())
        matches = self.names.get(name_key(target), [])
        if len(matches) > 1:
            raise IncompleteTournament(f"Неоднозначный ник: {value}")
        return matches[0] if matches else target

    def user(self, user):
        name = self._user(user)
        self.resolved_users[str(user["id"])] = name
        return name

    def _user(self, user):
        known = self.config.get("users", {}).get(str(user["id"]))
        if known:
            return self.name(known)
        candidates = [user["username"]] + [g["account"] for g in
                      (user.get("profile") or {}).get("games", [])
                      if g.get("game") == GAME_ID and g.get("account")]
        matches = {self.name(n) for n in candidates if
                   name_key(n) in self.names or name_key(n) in self.config.get("aliases", {})}
        if len(matches) > 1:
            raise IncompleteTournament(f"Нужно сопоставить ник {user['username']}")
        return next(iter(matches)) if matches else self.name(user["username"])


def rosters(number, mode, view, wiki_raw, identities):
    size = int(mode[0])
    candidates = set()
    if size > 1:
        template = "DuoOpponent" if size == 2 else "TrioOpponent"
        teams = liquipedia.team_rosters(wiki_raw)
        groups = list(teams.values()) + [liquipedia.names_from_opponent(t, teams)
                  for t in liquipedia.nested(wiki_raw, template)]
        for people in groups:
            people = tuple(sorted({identities.name(n) for n in people if n}))
            if len(people) == size:
                candidates.add(people)
    overrides = identities.config.get("rosters", {}).get(str(number), {})
    result, used = {}, set()
    for entry in view["players"]:
        if entry["status"] != "CONFIRM":
            continue
        lineup = entry.get("lineup", [])
        # These Rusaoc brackets register individuals, including team captains.
        if len(lineup) != 1 or view["participationType"] != "SINGLE":
            raise IncompleteTournament("Нужна проверка формата регистрации команд")
        user = lineup[0]
        user_id = str(user["id"])
        name = identities.user(user)
        if size == 1:
            people = [name]
        elif user_id in overrides:
            people = [identities.name(n) for n in overrides[user_id]["players"]]
        else:
            choices = [p for p in candidates if name in p]
            if len(choices) != 1:
                raise IncompleteTournament(f"Нужен состав команды {name}")
            people = list(choices[0])
        if len(set(people)) != size or name not in people or used.intersection(people) or user_id in result:
            raise IncompleteTournament(f"Неоднозначный состав команды {name}")
        result[user_id] = people
        used.update(people)
    if len(result) < 2:
        raise IncompleteTournament("В сетке недостаточно участников")
    return result


def parse_tournament(number, mode, view, wiki_raw, identities):
    if view.get("status") != "CLOSED":
        raise IncompleteTournament("Турнир ещё не завершён")
    if mode not in ("1v1", "2v2", "3v3"):
        raise IncompleteTournament("Этот формат пока требует проверки результатов")
    grids = (view.get("format") or {}).get("grids", [])
    if len(grids) != 1 or grids[0]["grid"]["config"]["type"] != "DOUBLE_ELIMINATION":
        raise IncompleteTournament("Нужна проверка структуры сетки")
    grid = grids[0]["grid"]
    sides = rosters(number, mode, view, wiki_raw, identities)
    counts = {name: [0, 0, 0] for names in sides.values() for name in names}
    real, seen, technical, byes = [], set(), 0, 0
    for branch in ("upGrid", "downGrid"):
        for stage in grid[branch]["_stages"]:
            for match in stage["matches"]:
                if match["_id"] in seen:
                    raise IncompleteTournament("Повтор матча в сетке")
                seen.add(match["_id"])
                players = [str(p) for p in match["players"]]
                if len(players) != 2 or any(p not in sides and not p.startswith("empty") for p in players):
                    raise IncompleteTournament("Неизвестный участник в сетке")
                if any(p.startswith("empty") for p in players):
                    byes += 1
                    continue
                scores = match["scores"]
                if (players[0] == players[1] or type(match["bo"]) is not int or match["bo"] < 1
                        or len(scores) != 2 or any(type(v) is not int or v < 0 for v in scores)
                        or scores[0] == scores[1] or max(scores) != match["bo"]
                        or type(match.get("tLose")) is not int or match["tLose"] not in (-1, 0, 1)):
                    raise IncompleteTournament("Не все матчи имеют окончательный результат")
                real.append(match)
                if match["tLose"] != -1:
                    technical += 1
                    continue
                for i, side in enumerate(players):
                    for name in sides[side]:
                        counts[name][0] += int(scores[i] > scores[1 - i])
                        counts[name][1] += int(scores[i] < scores[1 - i])
                        counts[name][2] += 1
    # A standard completed double-elimination bracket has 2*N-2 matches.
    # Fail closed for truncated archives and unfamiliar bracket/reset layouts.
    if len(real) != 2 * len(sides) - 2:
        raise IncompleteTournament("Сетка неполная или содержит нестандартный финал")

    def winner_loser(match):
        if match not in real:
            raise IncompleteTournament("Финал не записан полностью")
        win = int(match["scores"][1] > match["scores"][0])
        return str(match["players"][win]), str(match["players"][1 - win])

    finals = grid["upGrid"]["_stages"][-1]["matches"]
    lower = grid["downGrid"]["_stages"]
    if len(finals) != 1 or len(lower[-1]["matches"]) != 1:
        raise IncompleteTournament("Неоднозначные призовые места")
    first, second = winner_loser(finals[0])
    losses = {side: 0 for side in sides}
    for match in real:
        _, loser = winner_loser(match)
        losses[loser] += 1
    if (losses[first] not in (0, 1) or losses[second] not in (1, 2)
            or any(n != 2 for side, n in losses.items() if side not in (first, second))):
        raise IncompleteTournament("Результаты не согласуются с выбыванием из сетки")
    lower_winner, third = winner_loser(lower[-1]["matches"][0])
    if lower_winner not in (first, second):
        raise IncompleteTournament("Финал не согласуется с нижней сеткой")
    placements = {first: 1, second: 2, third: 3}
    if len(lower) > 1 and len(lower[-2]["matches"]) == 1:
        _, fourth = winner_loser(lower[-2]["matches"][0])
        placements[fourth] = 4
    if len(placements) != (4 if len(lower) > 1 and len(lower[-2]["matches"]) == 1 else 3):
        raise IncompleteTournament("Повтор участника в призовых местах")
    places = {name: place for side, place in placements.items() for name in sides[side]}
    source = "https://goodgame.ru/cups/" + view["url"]
    roster_sources = []
    if mode != "1v1":
        roster_sources.append(f"https://liquipedia.net/ageofempires/Rusaoc_Cup/{number}")
        for entry in identities.config.get("rosters", {}).get(str(number), {}).values():
            roster_sources.extend(entry["sources"])
    return {"number": number, "mode": mode, "counts": counts, "places": places,
            "event": {"name": f"Rusaoc Cup {number}", "mode": mode, "players": len(counts),
                      "source": "goodgame", "sourceUrl": source, "goodgameId": view["id"],
                      "date": view["start"][:10], "matches": len(real) - technical,
                      "technicalMatches": technical, "byeMatches": byes,
                      "rosterSources": sorted(set(roster_sources))}}


def merge_supplements(sheet, supplements, pending=()):
    """Pure merge, from the freshly fetched Sheet only. Filled columns win."""
    if any(e.get("source") == "goodgame" for e in sheet["events"]):
        raise ValueError("Merge must start with the original Google Sheet")
    result = copy.deepcopy(sheet)
    for event in result["events"]:
        event.update(source="google-sheet", sourceUrl=SHEET_URL)
    by_number = {cup_number(e["name"]): i for i, e in enumerate(result["events"]) if cup_number(e["name"])}
    filled_numbers = {cup_number(e["name"]) for e in result["events"] if e["players"]}
    players = {p["name"]: p for p in result["players"]}
    seen = set()
    for supplement in sorted(supplements, key=lambda s: s["number"]):
        number = supplement["number"]
        if number in seen:
            raise ValueError(f"Duplicate supplement {number}")
        seen.add(number)
        idx = by_number.get(number)
        if number in filled_numbers:
            continue
        if sum(cup_number(e["name"]) == number for e in result["events"]) > 1:
            raise ValueError(f"Ambiguous spreadsheet columns for {number}")
        if idx is None:
            idx = len(result["events"])
            result["events"].append({})
        elif result["events"][idx]["mode"] != supplement["mode"]:
            raise ValueError(f"GoodGame format differs from Google Sheet for {number}")
        by_number[number] = idx
        result["events"][idx] = copy.deepcopy(supplement["event"])
        for name, counts in supplement["counts"].items():
            if name not in players:
                players[name] = {"name": name, "places": [0, 0, 0, 0], "results": []}
                result["players"].append(players[name])
            player = players[name]
            player["results"].append([idx, *counts])
            place = supplement["places"].get(name)
            if place:
                player.setdefault("goodgamePlaces", [0, 0, 0, 0])[place - 1] += 1
                player.setdefault("goodgamePlacements", {})[str(idx)] = place
    for player in result["players"]:
        player["results"].sort(key=lambda r: r[0])
    result["pendingGoodgame"] = list(pending)
    result["updated"] = sheet["updated"]
    result["goodgameUpdated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return result


def supplement_sheet(sheet, wiki_pages, previous):
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    # Retain public account IDs across refreshes, including new players and
    # events that have since been filled in the Google Sheet.
    config["users"] = {**previous.get("goodgameIdentities", {}), **config["users"]}
    identities = Identities(sheet, config)
    events = {cup_number(e["name"]): e for e in sheet["events"] if cup_number(e["name"])}
    filled_numbers = {cup_number(e["name"]) for e in sheet["events"] if e["players"]}
    minimum = min([n for n, e in events.items() if not e["players"]] + [max(events) + 1])
    # Old editions can have several brackets (e.g. qualifiers) under one number.
    # They must not block updates of already complete spreadsheet history.
    archive = fetch_completed(minimum)
    supplements, pending = [], []
    for number, summary in sorted(archive.items()):
        existing = events.get(number)
        if number in filled_numbers:
            continue
        # Historical events before the Sheet's range aren't silently introduced.
        if not existing and number < max(events):
            continue
        if sum(cup_number(e["name"]) == number for e in sheet["events"]) > 1:
            pending.append({"name": f"Rusaoc Cup {number}", "reason": "Нужно сопоставить несколько колонок турнира",
                            "sourceUrl": "https://goodgame.ru/cups/" + summary["url"]})
            continue
        raw = wiki_pages.get(f"Rusaoc Cup/{number}", "")
        mode = existing["mode"] if existing else None
        if mode is None:
            match = re.search(r"\b(1v1|2v2|3v3|FFA)\b", summary["title"], re.I)
            mode = match[1].lower() if match else (
                liquipedia.parse_page(f"Rusaoc Cup/{number}", raw)["mode"] if raw else "?")
        view = graphql(VIEW_QUERY, {"url": summary["url"]}, "getCupsByUrlView")["cup"]
        if view["id"] != summary["id"] or view["url"] != summary["url"]:
            raise ValueError("GoodGame tournament identity changed")
        if not re.match(rf"^RUSAOC CUP\s+{number}(?:\s*\||\s*$)", view["title"], re.I):
            raise ValueError("GoodGame tournament number changed")
        try:
            item = parse_tournament(number, mode, view, raw, identities)
        except IncompleteTournament as error:
            pending.append({"name": f"Rusaoc Cup {number}", "reason": str(error),
                            "sourceUrl": "https://goodgame.ru/cups/" + summary["url"]})
            print(f"Pending Rusaoc Cup {number}: {error}", flush=True)
        else:
            supplements.append(item)
            print(f"GoodGame {number}: {len(item['counts'])} players, {item['event']['matches']} played matches", flush=True)
        time.sleep(1)
    merged = merge_supplements(sheet, supplements, pending)
    merged["goodgameIdentities"] = {**previous.get("goodgameIdentities", {}), **identities.resolved_users}
    new_events = {e["name"]: e for e in merged["events"]}
    for old in previous["events"]:
        if old.get("source") == "goodgame" and not new_events.get(old["name"], {}).get("players"):
            raise ValueError(f"Previously published GoodGame results disappeared: {old['name']}")
    return merged
