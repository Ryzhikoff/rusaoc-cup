"""Read the public organisers' spreadsheet using only the Python standard library."""

import datetime
import gzip
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SHEET_ID = "1zDXLyhJwHMQhtCvrNnvTV8H3tVx2WRyXRlqzmrIZRwo"


def decode_response(text):
    match = re.fullmatch(
        r"\s*(?:/\*.*?\*/\s*)?google\.visualization\.Query\.setResponse\((.*)\);?\s*",
        text, re.S,
    )
    if not match:
        raise ValueError("Unexpected Google Sheets response")
    return json.loads(match.group(1))


def fetch_sheet():
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?" + urlencode({"tqx": "out:json", "gid": "0"})
    request = Request(url, headers={"User-Agent": "RusaocCupStatistics/1.1", "Accept-Encoding": "gzip"})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=75) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
            return parse_sheet(decode_response(payload.decode("utf-8")))
        except (HTTPError, URLError, TimeoutError) as error:
            if attempt == 3:
                raise RuntimeError(f"Google Sheets unavailable: {error}") from error
            delay = 5 * (attempt + 1)
            if isinstance(error, HTTPError):
                retry = error.headers.get("Retry-After", "")
                if retry.isdigit():
                    delay = max(delay, int(retry))
            print(f"Google Sheets unavailable; retrying in {delay}s", flush=True)
            time.sleep(delay)


def parse_sheet(response):
    table = response.get("table", {})
    if response.get("status") != "ok" or not table.get("cols") or not table.get("rows"):
        raise ValueError("Google Sheets returned no table")
    columns, rows = table["cols"], table["rows"]
    labels = [column.get("label", "") for column in columns]
    events = []
    index = 1
    while index < len(columns) - 2:
        match = re.search(r"Rusaoc Cup\s*(\d+(?:_\d+)?).*?\(\s*\*?\s*(1v1|2v2|3v3|FFA)\s*;", labels[index], re.I)
        if match:
            mode = match.group(2)
            events.append({"col": index, "name": "Rusaoc Cup " + match.group(1), "mode": "FFA" if mode.upper() == "FFA" else mode.lower(), "players": 0})
            index += 3
        else:
            index += 1

    def column_for(text, exact=False):
        return next((i for i, label in enumerate(labels) if label == text or not exact and text in label), -1)

    place_col = column_for("Призовые места (1;2;3;4)")
    total_cols = [column_for("Общие итоги Всего побед"), column_for("Всего поражений", True), column_for("Посетил турниров", True)]
    if len(events) < 190 or len(rows) < 500 or min([place_col, *total_cols]) < 0:
        raise ValueError("Google Sheets structure changed or response is incomplete")

    def cell(row, index):
        cells = row.get("c", [])
        return (cells[index] or {}).get("v") if index < len(cells) else None

    def integer(value):
        if value is None or value == "":
            return 0
        if isinstance(value, bool):
            raise ValueError("Boolean used as a map count")
        number = float(value)
        if not number.is_integer() or number < 0:
            raise ValueError("Invalid map count")
        return int(number)

    players = []
    names = set()
    for row in rows:
        value = cell(row, 0)
        name = str(value).strip() if value is not None else ""
        if not name:
            continue
        if name in names:
            raise ValueError(f"Duplicate spreadsheet player: {name}")
        names.add(name)
        results = []
        for index, event in enumerate(events):
            raw = [cell(row, event["col"] + i) for i in range(3)]
            if all(value is None or value == "" for value in raw):
                continue
            wins, losses, games = map(integer, raw)
            if wins + losses > games:
                raise ValueError(f"Map totals disagree for {name} in {event['name']}")
            results.append([index, wins, losses, games])
            event["players"] += 1
        places = [int(x) for x in re.findall(r"\d+", str(cell(row, place_col) or ""))] or [0, 0, 0, 0]
        if len(places) != 4:
            raise ValueError(f"Invalid prize summary for {name}")
        actual = [sum(r[1] for r in results), sum(r[2] for r in results), len(results)]
        expected = [integer(cell(row, column)) for column in total_cols]
        if actual != expected:
            raise ValueError(f"Spreadsheet totals disagree for {name}: {actual} != {expected}")
        players.append({"name": name, "places": places, "results": results})
    if len(players) < 500:
        raise ValueError("Too few spreadsheet players; keeping the published snapshot")
    return {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "events": [{k: v for k, v in event.items() if k != "col"} for event in events],
        "players": players,
    }
