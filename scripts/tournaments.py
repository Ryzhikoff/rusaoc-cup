"""Tournament catalogue, historical prize amounts and local map thumbnails.

Money is stored in integer kopecks/cents. Every tournament's USD amount uses
Liquipedia's CurrencyExchange for its own end date, never an aggregate rate.
"""

import ast
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import liquipedia as lp

SERIES = "https://liquipedia.net/ageofempires/Rusaoc_Cup"
IDENTITIES = Path(__file__).resolve().parents[1] / "data" / "goodgame-identities.json"


def read_catalog(document):
    match = re.search(r'<script id="tournaments" type="application/json">(.*?)</script>', document, re.S)
    return json.loads(match[1]) if match else {}


def number(value):
    """Read numbers and simple #expr arithmetic without executing wiki content."""
    value = lp.strip_comments(str(value)).strip()
    if not value:
        return None
    match = re.fullmatch(r"\{\{#expr:([^{}]+)\}\}", value, re.I)
    expression = (match[1] if match else value).strip()
    if len(expression) > 100 or not re.fullmatch(r"[\d.\s+*/()\-]+", expression):
        return None
    try:
        tree = ast.parse(expression, mode="eval")

        def evaluate(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                return Decimal(ast.get_source_segment(expression, node))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return evaluate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            if isinstance(node, ast.BinOp):
                a, b = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Add): return a + b
                if isinstance(node.op, ast.Sub): return a - b
                if isinstance(node.op, ast.Mult): return a * b
                if isinstance(node.op, ast.Div): return a / b
            raise ValueError("Unsupported arithmetic")

        result = evaluate(tree.body)
        return result if result.is_finite() and 0 <= result <= 1000000000 else None
    except (SyntaxError, ValueError, ArithmeticError):
        return None


def minor(value):
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if value is not None else None


def identity(name):
    return lp.key(lp.canon(name)).strip()


def cup_number(name):
    match = re.fullmatch(r"Rusaoc Cup (\d+)(?:_\d+)?", name)
    if not match:
        raise ValueError(f"Unrecognized tournament: {name}")
    return int(match[1])


def iso_date(value):
    value = lp.clean_name(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        dt.date.fromisoformat(value)
        return value
    return None


def page_info(raw):
    box = next(lp.nested(lp.strip_comments(raw), "Infobox league"), None)
    return lp.parse_tpl(box)[1] if box else {}


def map_names(raw, info):
    values = [v for k, v in info.items() if re.fullmatch(r"map\d+", k)]
    note = ""
    if not any(values):
        # Some older editions state maps only in the Format section.
        section = re.search(r"===?Format===?(.*?)(?=\n==|\Z)", lp.strip_comments(raw), re.S)
        if section:
            active = False
            for line in section[1].splitlines():
                if re.search(r"Map(?: settings|s):", line, re.I):
                    active = True
                    literal = line.split(":", 1)[1].strip()
                    if literal and re.fullmatch(r"[\w -]+", literal):
                        values.append(literal)
                elif active and re.match(r"\*{1,2}[^*]", line):
                    active = False
                if active:
                    values.extend(re.findall(r"\[\[([^\]]+)\]\]", line))
            if values:
                note = "Карты перечислены в правилах; полный пул основной стадии может быть не указан."
    names = []
    for value in values:
        name = lp.clean_name(value)
        if name and name.casefold() not in {"tba", "tbd", "unknown", "?"} and name not in names:
            names.append(name)
    return names, note


def prize_slots(raw):
    clean = lp.strip_comments(raw)
    teams = lp.team_rosters(clean)
    templates = list(lp.nested(clean, r"prize pool slot(?: team|_team)?"))
    if not templates:
        for pool in lp.nested(clean, r"(?:Solo|Duo|Trio|Team)PrizePool"):
            templates.extend(lp.nested(pool, "Slot"))
    result, next_place = [], 1
    for template in templates:
        _, fields, positional = lp.parse_tpl(template)
        place = fields.get("place", str(next_place))
        if not re.fullmatch(r"\d+(?:-\d+)?", place):
            continue
        people = []
        for value in positional:
            if value.startswith("{{"):
                # lastvs can itself contain an Opponent template; that person
                # belongs to another place, not to this slot.
                people.extend(lp.names_from_opponent(lp.read_tpl(value, 0), teams))
            else:
                name = lp.canon(value)
                if name:
                    people.extend(teams.get(lp.key(name), [name]))
        people = expand_teams(people, teams)
        local, usd = number(fields.get("localprize", "")), number(fields.get("usdprize", ""))
        # A top-level opponent is one placement, even if it contains 2/3 players.
        group_size = int(fields["count"]) if fields.get("count", "").isdigit() else max(1, len(positional))
        if "place" not in fields and group_size > 1:
            place = f"{next_place}-{next_place + group_size - 1}"
        next_place = int(place.split("-")[-1]) + 1
        if (local or usd or int(place.split("-")[0]) <= 4):
            result.append({"place": place, "players": people, "rubMinor": minor(local), "usdMinor": minor(usd)})
    return result


def expand_teams(people, teams):
    expanded = []
    for person in people:
        stem = re.sub(r"(?i)'s\s+team$", "", person).strip()
        members = teams.get(lp.key(person)) or teams.get(lp.key(stem))
        if members:
            expanded.extend(members)
        elif " & " in person:
            expanded.extend(lp.canon(p) for p in person.split(" & "))
        elif person:
            expanded.append(person)
    return sorted(set(expanded), key=str.casefold)


def other_tournament(cup, series):
    match = re.search(r"^\*'''Rusaoc Cup " + str(cup) + r"'''([^\n]+)", series, re.M)
    if not match:
        return {}, [], "", ""
    text = match[1]
    date = re.search(r"played on (\d+ [A-Za-z]+ \d{4})", text)
    map_match = re.search(r"played on \[\[([^\]]+)\]\]", text)
    date = dt.datetime.strptime(date[1], "%d %B %Y").date().isoformat() if date else None
    # A lower bound plus a non-cash prize is not an exact prize pool.
    note = "Не менее 1 000 ₽ и двухчасовое занятие с Vinchester; точная денежная сумма не указана." if "1000+ rub" in text else ""
    return {"date": date or "", "format": "1v1"}, [lp.clean_name(map_match[1])] if map_match else [], "", note


def podium_from_finals(raw):
    """Resolve imported prize slots only from explicitly labelled finals."""
    teams = lp.team_rosters(lp.strip_comments(raw))

    def outcome(label):
        sections = re.findall(r"<!--\s*" + label + r"\s*-->(.*?)(?=<!--|\Z)", raw, re.I | re.S)
        if len(sections) != 1:
            return None
        matches = list(lp.nested(lp.strip_comments(sections[0]), "Match"))
        if len(matches) != 1:
            return None
        _, fields, _ = lp.parse_tpl(matches[0])
        sides, scores = [], []
        for n in (1, 2):
            opponent = fields.get(f"opponent{n}", "")
            if not opponent.startswith("{{"):
                return None
            template = lp.read_tpl(opponent, 0)
            sides.append([p for p in lp.names_from_opponent(template, teams) if p])
            score = lp.parse_tpl(template)[1].get("score", "")
            scores.append(int(score) if score.isdigit() else None)
        if not all(sides):
            return None
        winner = fields.get("winner")
        if winner in ("1", "2"):
            winner = int(winner) - 1
        else:
            if any(score is None for score in scores):
                scores = [0, 0]
                for template in lp.nested(matches[0], "Map"):
                    w = lp.parse_tpl(template)[1].get("winner")
                    if w in ("1", "2"):
                        scores[int(w) - 1] += 1
            bestof = int(fields["bestof"]) if fields.get("bestof", "").isdigit() else None
            if scores[0] == scores[1] or (bestof and max(scores) < bestof // 2 + 1):
                return None
            winner = 0 if scores[0] > scores[1] else 1
        return sides[winner], sides[1 - winner]

    result = {}
    final = outcome("Grand Final") or outcome("Final")
    if final:
        result["1"], result["2"] = final
    third = outcome("(?:3rd|Third) Place Match")
    if third:
        result["3"], result["4"] = third
    else:
        lower_final = outcome("Lower Bracket Final")
        lower_semi = outcome("Lower Bracket Semifinals?")
        if lower_final:
            result["3"] = lower_final[1]
        if lower_semi:
            result["4"] = lower_semi[1]
    return result


def prepare_events(pages, series, sheet, today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    groups = {}
    for idx, event in enumerate(sheet["events"]):
        groups.setdefault(cup_number(event["name"]), []).append(idx)
    for title, raw in pages.items():
        if re.fullmatch(r"Rusaoc Cup/\d+", title):
            info = page_info(raw)
            date = iso_date(info.get("edate") or info.get("date") or info.get("sdate") or "")
            if date and date <= today.isoformat():
                groups.setdefault(int(title.split("/")[-1]), [])
    names = {identity(p["name"]): p["name"] for p in sheet["players"]}
    for alias, target in json.loads(IDENTITIES.read_text())["aliases"].items():
        if identity(target) in names:
            names[identity(alias)] = names[identity(target)]
    events = []
    for cup, indices in sorted(groups.items()):
        raw = pages.get(f"Rusaoc Cup/{cup}", "")
        info = page_info(raw)
        maps, map_note = map_names(raw, info)
        source, prize_note = SERIES + f"/{cup}", ""
        if not info:
            info, maps, map_note, prize_note = other_tournament(cup, series)
            source = SERIES + "#Other_Tournaments"
        date = iso_date(info.get("sdate") or info.get("date") or "")
        end = iso_date(info.get("edate") or info.get("date") or "") or date
        mode = re.search(r"\b(1v1|2v2|3v3|FFA)\b", info.get("format", ""), re.I)
        mode = mode[1] if mode else sheet["events"][indices[0]]["mode"] if indices else "?"
        mode = "FFA" if mode.upper() == "FFA" else mode.lower()
        teams = int(info["team_number"]) if info.get("team_number", "").isdigit() else None
        count = int(info["player_number"]) if info.get("player_number", "").isdigit() else teams * int(mode[0]) if teams and mode in ("2v2", "3v3") else None
        parsed = lp.parse_page(f"Rusaoc Cup/{cup}", raw) if page_info(raw) else {}
        # A few infoboxes say 1v1 despite declaring teams and explicit duo rosters.
        # Reuse the existing parser's roster-based correction before splitting prizes.
        if parsed.get("mode") in ("1v1", "2v2", "3v3", "FFA"):
            mode = parsed["mode"]
            if teams and mode in ("2v2", "3v3"):
                count = teams * int(mode[0])
        teams_by_name = lp.team_rosters(lp.strip_comments(raw))
        participants = sorted({names.get(identity(p), p) for p in parsed.get("participants", [])}, key=str.casefold)
        champions = list(dict.fromkeys(names.get(identity(p), p) for p in expand_teams(parsed.get("champions", []), teams_by_name)))
        slots = prize_slots(raw)
        podium = podium_from_finals(raw)
        for slot in slots:
            if not slot["players"]:
                slot["players"] = podium.get(slot["place"], [])
            slot["players"] = expand_teams(slot["players"], teams_by_name)
            slot["players"] = list(dict.fromkeys(names.get(identity(p), p) for p in slot["players"]))
            if slot["place"] == "1" and not slot["players"]:
                slot["players"] = champions
        if not champions and slots and slots[0]["place"].split("-")[0] == "1":
            champions = slots[0]["players"]
        currency = info.get("localcurrency", "").upper()
        rub = number(info.get("prizepool", "")) if currency == "RUB" else None
        usd = number(info.get("prizepoolusd", ""))
        if currency == "USD" and usd is None:
            usd = number(info.get("prizepool", ""))
        scope = info.get("name", "").split(":", 1)[1].strip() if ":" in info.get("name", "") else ""
        event = {"cup": cup, "date": date, "endDate": end, "mode": mode, "maps": maps, "mapNote": map_note,
                 "players": count, "teams": teams, "participants": participants, "champions": champions,
                 "rubMinor": minor(rub), "usdMinor": minor(usd), "prizes": slots, "prizeNote": prize_note,
                 "scope": scope, "source": source, "sheetIndices": indices,
                 "qualifiers": []}
        for title, qualifier_raw in pages.items():
            if re.fullmatch(f"Rusaoc Cup/{cup}/Qualifier/\\d+", title):
                qi = page_info(qualifier_raw)
                qm, _ = map_names(qualifier_raw, qi)
                event["qualifiers"].append({"number": int(title.split("/")[-1]), "date": iso_date(qi.get("date", "")), "maps": qm})
        events.append(event)
    return events


def fetch_exchange_rates(events, previous):
    rates = dict(previous.get("exchangeRates", {}))
    needed = sorted({f"RUB:{e['endDate']}" for e in events if e["rubMinor"] is not None and e["endDate"]} - rates.keys())
    for offset in range(0, len(needed), 20):
        batch = needed[offset:offset + 20]
        # Small numeric expansions use the same historical CurrencyExchange
        # function as Liquipedia's PrizePool module. No current FX service.
        lines = [key + "={{#currencyexchange:1|RUB|USD|" + key.split(":")[1] + "}}" for key in batch]
        response = lp.api_query(action="expandtemplates", prop="wikitext", text="\n".join(lines))
        values = dict(line.split("=", 1) for line in response["expandtemplates"]["wikitext"].splitlines() if "=" in line)
        if set(values) != set(batch):
            raise ValueError("Incomplete historical currency response")
        for key, value in values.items():
            if not re.fullmatch(r"\d+(?:\.\d+)?", value.strip()) or not 0 < Decimal(value) < 10:
                raise ValueError(f"Invalid historical exchange rate: {key}")
            rates[key] = value.strip()
        print(f"Historical currency dates: {offset + len(batch)}/{len(needed)}", flush=True)
    return rates


def apply_exchange_rates(events, rates):
    for event in events:
        rate = rates.get(f"RUB:{event['endDate']}")
        if not rate:
            continue
        rate = Decimal(rate)
        if event["usdMinor"] is None and event["rubMinor"] is not None:
            event["usdMinor"] = int((Decimal(event["rubMinor"]) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        for prize in event["prizes"]:
            if prize["usdMinor"] is None and prize["rubMinor"] is not None:
                prize["usdMinor"] = int((Decimal(prize["rubMinor"]) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fetch_map_images(events, previous, directory):
    maps = sorted({m for e in events for m in e["maps"]} | {m for e in events for q in e["qualifiers"] for m in q["maps"]})
    images, files, assets = {}, {}, {}
    cache = directory.parent / ".rusaoc-cache" / "maps"
    cache.mkdir(parents=True, exist_ok=True)
    for offset in range(0, len(maps), 25):
        batch = maps[offset:offset + 25]
        response = lp.api_query(action="query", prop="revisions", rvprop="content", rvslots="main", redirects=1, titles="|".join(batch))
        query = response["query"]
        aliases = {p["from"]: p["to"] for k in ("normalized", "redirects") for p in query.get(k, [])}
        pages = {p["title"]: p for p in query["pages"]}
        for name in batch:
            title = name
            for _ in range(10):
                if title not in aliases: break
                title = aliases[title]
            page = pages.get(title, {})
            if "revisions" not in page:
                continue
            raw = page["revisions"][0]["slots"]["main"]["content"]
            box = next(lp.nested(lp.strip_comments(raw), "Infobox map"), None)
            info = lp.parse_tpl(box)[1] if box else {}
            filename = lp.clean_name(info.get("image", ""))
            if filename and not any(c in filename for c in ("|", "[", "]", "{", "}")):
                files[name] = ("File:" + filename.removeprefix("File:"), title)
    filenames = sorted({filename for filename, _ in files.values()})
    file_info = {}
    for offset in range(0, len(filenames), 25):
        response = lp.api_query(action="query", prop="imageinfo", iiprop="url|sha1|mime", iiurlwidth=240, titles="|".join(filenames[offset:offset + 25]))
        query = response["query"]
        normalized = {p["from"]: p["to"] for p in query.get("normalized", [])}
        pages = {p["title"]: p for p in query["pages"]}
        for filename in filenames[offset:offset + 25]:
            file_info[filename] = pages.get(normalized.get(filename, filename), {}).get("imageinfo", [{}])[0]
    for name, (filename, title) in files.items():
        info = file_info.get(filename, {})
        remote = info.get("thumburl") or info.get("url")
        if not remote or not info.get("sha1"):
            continue
        remote = remote.replace("http://liquipedia.net/", "https://liquipedia.net/", 1)
        if urlsplit(remote).hostname != "liquipedia.net" or not urlsplit(remote).path.startswith("/commons/images/"):
            raise ValueError(f"Unexpected map image URL: {remote}")
        cached_images = previous.get("mapImages", {})
        old = cached_images.get(name) or next((v for v in cached_images.values() if v.get("file") == filename), {})
        if old.get("sha1") == info["sha1"] and old.get("path", "").startswith("maps/") and (directory / old["path"]).is_file():
            images[name] = old
            continue
        cache_key = hashlib.sha256((filename + info["sha1"]).encode()).hexdigest()[:24]
        cached = next((p for p in cache.glob(cache_key + ".*") if p.suffix != ".tmp"), None)
        payload = cached.read_bytes() if cached else None
        if payload is None:
            print(f"Map thumbnail: {name}", flush=True)
            for attempt in range(3):
                try:
                    lp.throttle_api()
                    with urlopen(Request(remote, headers={"User-Agent": lp.USER_AGENT}), timeout=25) as response:
                        payload = response.read(2_000_001)
                    break
                except (HTTPError, URLError, TimeoutError) as error:
                    if attempt == 2:
                        raise RuntimeError(f"Map thumbnail unavailable: {name} ({remote})") from error
                    time.sleep(max(3 * (attempt + 1), int(getattr(error, "headers", {}).get("Retry-After", 0) or 0)))
        if len(payload) > 2_000_000:
            raise ValueError(f"Map thumbnail too large: {name}")
        extension = image_extension(payload)
        if not extension:
            raise ValueError(f"Invalid map thumbnail: {name}")
        if not cached:
            staged = cache / (cache_key + ".tmp")
            staged.write_bytes(payload)
            staged.replace(cache / (cache_key + extension))
        path = "maps/" + cache_key + extension
        assets[path] = payload
        images[name] = {"path": path, "sha1": info["sha1"], "file": filename, "source": "https://liquipedia.net/ageofempires/" + quote(title.replace(" ", "_"), safe="/"), "fileSource": info.get("descriptionurl", "")}
    return images, assets


def image_extension(payload):
    if payload.startswith(b"\x89PNG\r\n\x1a\n"): return ".png"
    if payload.startswith(b"\xff\xd8\xff"): return ".jpg"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP": return ".webp"
    if payload.startswith((b"GIF87a", b"GIF89a")): return ".gif"
    if payload.lstrip().startswith((b"<svg", b"<?xml")) and b"<!DOCTYPE" not in payload and b"<!ENTITY" not in payload:
        try:
            root = ET.fromstring(payload)
            if root.tag != "{http://www.w3.org/2000/svg}svg" or not root.get("viewBox"):
                return None
            for element in root.iter():
                if element.tag.rsplit("}", 1)[-1] in ("script", "foreignObject") or any(k.lower().startswith("on") for k in element.attrib):
                    return None
            return ".svg"
        except ET.ParseError:
            return None
    return None


def normalize_maps(events, images):
    # Use explicit redirects and casing only, never fuzzy map-name matching.
    aliases = {name: unquote(img["source"].split("/ageofempires/", 1)[1]).replace("_", " ") for name, img in images.items()}
    casing = {name.casefold(): canonical for name, canonical in aliases.items()}
    for event in events:
        for row in [event] + event["qualifiers"]:
            row["maps"] = list(dict.fromkeys(aliases.get(name, casing.get(name.casefold(), name)) for name in row["maps"]))
    return {aliases[name]: image for name, image in images.items()}


def player_earnings(events, sheet):
    """Accumulate equal team shares exactly in sixths of a kopeck/cent.

    Six is divisible by both team sizes (2 and 3). Round only the final
    displayed amount; never assign a whole team's prize to every member.
    """
    known = {p["name"] for p in sheet["players"]}
    result = {}
    for event in events:
        size = int(event["mode"][0]) if event["mode"] in ("2v2", "3v3") else 1
        seen = set()
        for prize in event["prizes"]:
            recipients = prize["players"]
            if not recipients or len(recipients) % size or all(prize[k] is None for k in ("rubMinor", "usdMinor")):
                continue
            for name in recipients:
                if name not in known:
                    continue
                if name in seen:
                    raise ValueError(f"Duplicate prize recipient: {event['cup']} / {name}")
                seen.add(name)
                award = {"cup": event["cup"], "place": prize["place"], "teamSize": size}
                total = result.setdefault(name, {"rubSixths": None, "usdSixths": None, "awards": []})
                for currency in ("rub", "usd"):
                    amount = prize[currency + "Minor"]
                    award[currency + "Sixths"] = amount * (6 // size) if amount is not None else None
                    if amount is not None:
                        total[currency + "Sixths"] = (total[currency + "Sixths"] or 0) + award[currency + "Sixths"]
                total["awards"].append(award)
    return result


def build_catalog(pages, sheet, previous, directory, series=None):
    if series is None:
        result = lp.api_query(action="query", prop="revisions", rvprop="content", rvslots="main", titles="Rusaoc Cup")
        series = result["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]
    events = prepare_events(pages, series, sheet)
    rates = fetch_exchange_rates(events, previous)
    apply_exchange_rates(events, rates)
    images, assets = fetch_map_images(events, previous, directory)
    images = normalize_maps(events, images)
    catalog = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(), "events": events, "exchangeRates": rates, "mapImages": images, "earnings": player_earnings(events, sheet)}
    validate_catalog(catalog, sheet, previous)
    return catalog, assets


def validate_catalog(catalog, sheet, previous=None):
    events = catalog.get("events", [])
    cups = [e["cup"] for e in events]
    expected = {cup_number(e["name"]) for e in sheet["events"]}
    if len(cups) != len(set(cups)) or not expected <= set(cups):
        raise ValueError("Missing or duplicate catalogue tournaments")
    old_cups = {e["cup"] for e in (previous or {}).get("events", [])}
    if not old_cups <= set(cups):
        raise ValueError("Tournament catalogue shrank")
    for field in ("maps", "rubMinor", "usdMinor"):
        known = lambda rows: sum(bool(e[field]) if field == "maps" else e[field] is not None for e in rows)
        if known(events) < known((previous or {}).get("events", [])) * 0.9:
            raise ValueError(f"Tournament catalogue lost {field} coverage")
    for e in events:
        for row in [e] + e["prizes"]:
            for field in ("rubMinor", "usdMinor"):
                if row[field] is not None and (type(row[field]) is not int or not 0 <= row[field] <= 100_000_000_000):
                    raise ValueError("Invalid prize amount")
        for idx in e["sheetIndices"]:
            if not 0 <= idx < len(sheet["events"]) or cup_number(sheet["events"][idx]["name"]) != e["cup"]:
                raise ValueError("Wrong catalogue event mapping")
        if not e["source"].startswith(SERIES):
            raise ValueError("Invalid tournament source")
    for image in catalog.get("mapImages", {}).values():
        if not re.fullmatch(r"maps/[a-f0-9]{24}\.(?:png|jpg|gif|webp|svg)", image["path"]):
            raise ValueError("Invalid map asset path")
    if "earnings" in catalog and catalog["earnings"] != player_earnings(events, sheet):
        raise ValueError("Player prize totals disagree with tournament awards")
