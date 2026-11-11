#!/usr/bin/env python3
"""Refresh both published pages. Any fetch/validation error leaves the site untouched."""

import argparse
from pathlib import Path
import tempfile

import google_sheet
import goodgame
import liquipedia
import tournaments

ROOT = Path(__file__).resolve().parents[1]
FILES = ("rusaoc-cup-liquipedia.html", "rusaoc-cup-statistics.html")


def validate_seed(seed, previous, label):
    if not seed.get("players") or not seed.get("events"):
        raise ValueError(f"{label}: empty snapshot")
    if len(seed["events"]) < len(previous["events"]):
        raise ValueError(f"{label}: tournament archive shrank")
    if len(seed["players"]) < len(previous["players"]) * 0.9:
        raise ValueError(f"{label}: more than 10% of players disappeared")
    for player in seed["players"]:
        for result in player["results"]:
            if not 0 <= result[0] < len(seed["events"]):
                raise ValueError(f"{label}: invalid event index")
            if result[1] + result[2] > result[3]:
                raise ValueError(f"{label}: invalid map totals")


def update_site(directory=ROOT / "site"):
    originals = {name: (directory / name).read_text(encoding="utf-8") for name in FILES}
    previous = {name: liquipedia.read_seed(directory / name) for name in FILES}
    # Fetch and validate everything before preparing any on-disk replacements.
    sheet = google_sheet.fetch_sheet()
    liquipedia.PAGES = liquipedia.fetch_pages()
    wiki = liquipedia.make_seed(liquipedia.build())
    sheet = goodgame.supplement_sheet(sheet, liquipedia.PAGES, previous[FILES[1]])
    snapshots = dict(zip(FILES, (wiki, sheet)))
    for name, snapshot in snapshots.items():
        validate_seed(snapshot, previous[name], name)
    names = {player["name"] for snapshot in snapshots.values() for player in snapshot["players"]}
    countries = liquipedia.make_countries(names, liquipedia.fetch_player_profiles(names))
    if not countries["players"]:
        raise ValueError("No country information returned")
    previous_catalog = tournaments.read_catalog(originals[FILES[1]])
    catalog, assets = tournaments.build_catalog(liquipedia.PAGES, sheet, previous_catalog, directory)
    prepared = {}
    for name, snapshot in snapshots.items():
        document = liquipedia.replace_json(originals[name], "seed", snapshot)
        prepared[name] = liquipedia.replace_json(document, "countries", countries)
    prepared[FILES[1]] = liquipedia.replace_json(prepared[FILES[1]], "tournaments", catalog)
    with tempfile.TemporaryDirectory(prefix=".rusaoc-update-", dir=directory.parent) as temporary:
        for name, document in prepared.items():
            (Path(temporary) / name).write_text(document, encoding="utf-8")
        for name, payload in assets.items():
            target = Path(temporary) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        # New immutable assets first; existing pages keep using their old files
        # until all validated HTML documents are ready to replace them.
        for name in assets:
            (directory / name).parent.mkdir(parents=True, exist_ok=True)
            (Path(temporary) / name).replace(directory / name)
        for name in prepared:
            (Path(temporary) / name).replace(directory / name)
    for name, snapshot in snapshots.items():
        flagged = sum(liquipedia.key(player["name"]) in countries["players"] for player in snapshot["players"])
        print(f"Updated {name}: {len(snapshot['events'])} events, {len(snapshot['players'])} players, {flagged} flags", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=ROOT / "site")
    args = parser.parse_args()
    update_site(args.site_dir)
