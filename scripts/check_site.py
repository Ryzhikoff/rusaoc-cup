#!/usr/bin/env python3
"""Check snapshots and offline navigation before publishing."""

import argparse
import json
from pathlib import Path
import re
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
        for player in seed["players"]:
            for row in player["results"]:
                if not 0 <= row[0] < len(seed["events"]) or any(type(value) is not int or value < 0 for value in row):
                    raise ValueError(f"{name}: invalid player result")
                if row[1] + row[2] > row[3]:
                    raise ValueError(f"{name}: inconsistent map counts")
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
