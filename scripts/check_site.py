#!/usr/bin/env python3
"""Check snapshots and offline navigation before publishing."""

import argparse
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, unquote
from html.parser import HTMLParser

import liquipedia

ROOT = Path(__file__).resolve().parents[1]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def check_site(directory=ROOT / "site"):
    # Cover future countries too: daily data updates must never introduce
    # a country code whose flag image is absent from the published site.
    for code in liquipedia.COUNTRY_CODES:
        flag = directory / "flags" / f"{code.lower()}.svg"
        if not flag.is_file():
            raise ValueError(f"Missing local flag: {code}")
        root = ET.fromstring(flag.read_bytes())
        if root.tag != "{http://www.w3.org/2000/svg}svg" or not root.get("viewBox"):
            raise ValueError(f"Invalid flag image: {code}")
    countries = []
    for name in ("index.html", "rusaoc-cup-liquipedia.html", "rusaoc-cup-statistics.html"):
        path = directory / name
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in ("localStorage", 'id="refresh"', "function jsonp(", "function refresh(")):
            raise ValueError(f"{name}: browser-owned updates remain enabled")
        parser = Links()
        parser.feed(text)
        for link in parser.links:
            url = urlsplit(link)
            if not url.scheme and not url.netloc and url.path:
                if not (path.parent / unquote(url.path)).is_file():
                    raise ValueError(f"{name}: missing local link {link}")
        if name == "index.html":
            continue
        seed = liquipedia.read_seed(path)
        if len(seed["players"]) < 500 or len(seed["events"]) < 190:
            raise ValueError(f"{name}: incomplete snapshot")
        event_counts = [0] * len(seed["events"])
        names = set()
        for player in seed["players"]:
            if player["name"] in names:
                raise ValueError(f"{name}: duplicate player")
            names.add(player["name"])
            indices = set()
            for row in player["results"]:
                if not 0 <= row[0] < len(seed["events"]) or any(type(value) is not int or value < 0 for value in row):
                    raise ValueError(f"{name}: invalid player result")
                if row[1] + row[2] > row[3]:
                    raise ValueError(f"{name}: inconsistent map counts")
                if row[0] in indices:
                    raise ValueError(f"{name}: duplicate tournament participation")
                indices.add(row[0])
                event_counts[row[0]] += 1
            if name == "rusaoc-cup-statistics.html":
                placements = player.get("goodgamePlacements", {})
                counts = [0, 0, 0, 0]
                for idx, place in placements.items():
                    if (int(idx) not in indices or seed["events"][int(idx)].get("source") != "goodgame"
                            or type(place) is not int or not 1 <= place <= 4):
                        raise ValueError(f"{name}: invalid GoodGame placement")
                    counts[place - 1] += 1
                if counts != player.get("goodgamePlaces", [0, 0, 0, 0]):
                    raise ValueError(f"{name}: inconsistent GoodGame places")
        if name == "rusaoc-cup-statistics.html":
            for idx, event in enumerate(seed["events"]):
                if event_counts[idx] != event["players"]:
                    raise ValueError(f"{name}: inconsistent event participants")
                if event.get("source") not in ("google-sheet", "goodgame"):
                    raise ValueError(f"{name}: missing result provenance")
                expected = "https://goodgame.ru/cups/" if event["source"] == "goodgame" else "https://docs.google.com/spreadsheets/d/"
                if not event.get("sourceUrl", "").startswith(expected):
                    raise ValueError(f"{name}: invalid result source")
        if any(link in ("index.html", "rusaoc-cup-statistics.html", "rusaoc-cup-liquipedia.html") for link in parser.links):
            raise ValueError(f"{name}: cross-version navigation remains")
        match = re.search(r'<script id="countries" type="application/json">(.*?)</script>', text, re.S)
        if not match:
            raise ValueError(f"{name}: missing country data")
        country_data = json.loads(match.group(1))
        if not country_data["players"]:
            raise ValueError(f"{name}: empty country data")
        for country in country_data["players"].values():
            if country["code"] not in liquipedia.COUNTRY_CODES or not country["source"].startswith("https://liquipedia.net/ageofempires/"):
                raise ValueError(f"{name}: invalid country metadata")
        countries.append(country_data)
        print(f"Checked {name}: {len(seed['players'])} players, {len(seed['events'])} events")
    if countries[0] != countries[1]:
        raise ValueError("Country snapshots differ between the two pages")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=ROOT / "site")
    check_site(parser.parse_args().site_dir)
