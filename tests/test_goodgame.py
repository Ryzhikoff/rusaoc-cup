from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import goodgame


def sheet():
    return {"updated": "2026-11-11T00:00:00Z", "events": [
        {"name": "Rusaoc Cup 187", "mode": "1v1", "players": 1},
        {"name": "Rusaoc Cup 188", "mode": "1v1", "players": 0}],
        "players": [{"name": "Alice", "places": [5, 2, 1, 0], "results": [[0, 1, 0, 1]]}]}


def bracket():
    def match(number, a, b, scores, technical=-1):
        return {"_id": str(number), "id": number, "players": [a, b], "scores": scores,
                "bo": max(scores), "tLose": technical}

    return {"id": "event-id", "title": "RUSAOC CUP 188 | Test 1v1", "url": "rusaoc-cup-188-1",
            "start": "2026-09-08T15:10:00Z", "status": "CLOSED", "participationType": "SINGLE",
            "players": [{"status": "CONFIRM", "lineup": [{"id": i, "username": name}]} for i, name
                        in enumerate(("Alice", "Bob", "Carol", "Dave"), 1)] + [
                            {"status": "REGISTER", "lineup": [{"id": 5, "username": "RegisteredOnly"}]}],
            "format": {"grids": [{"grid": {"config": {"type": "DOUBLE_ELIMINATION"},
                "upGrid": {"_stages": [
                    {"matches": [match(0, "1", "empty-bye", [2, 0])]},
                    {"matches": [match(1, "1", "2", [2, 1]), match(2, "3", "4", [2, 0])]},
                    {"matches": [match(3, "1", "3", [1, 0])]},
                    {"matches": [match(4, "1", "2", [2, 0])]}]},
                "downGrid": {"_stages": [
                    {"matches": [match(5, "2", "4", [2, 0], 1)]},
                    {"matches": [match(6, "3", "2", [0, 1])]}]}}}]}}


def parse(view=None, mode="1v1", wiki="", config=None):
    return goodgame.parse_tournament(188, mode, view or bracket(), wiki,
                                    goodgame.Identities(sheet(), config or {}))


class BracketTests(unittest.TestCase):
    def test_real_187_reproduces_all_26_spreadsheet_rows(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "goodgame-187.json").read_text())
        original = {"players": [{"name": n} for n in fixture["expected"]]}
        resolver = goodgame.Identities(original, json.loads(goodgame.CONFIG.read_text()))
        actual = goodgame.parse_tournament(187, "1v1", fixture["view"], "", resolver)
        self.assertEqual(len(actual["counts"]), 26)
        self.assertEqual(actual["counts"], fixture["expected"])

    def test_series_are_one_match_and_walkovers_are_excluded(self):
        result = parse()
        self.assertEqual(result["counts"], {"Alice": [3, 0, 3], "Bob": [1, 2, 3],
                                            "Carol": [1, 2, 3], "Dave": [0, 1, 1]})
        self.assertEqual(result["places"], {"Alice": 1, "Bob": 2, "Carol": 3, "Dave": 4})
        self.assertEqual(result["event"]["matches"], 5)
        self.assertEqual(result["event"]["technicalMatches"], 1)
        self.assertEqual(result["event"]["byeMatches"], 1)
        self.assertNotIn("RegisteredOnly", result["counts"])

    def test_captain_can_be_any_member_of_the_team(self):
        wiki = "".join("{{TrioOpponent|" + "|".join([n + "Mate1", n, n + "Mate2"]) + "}}"
                       for n in ("Alice", "Bob", "Carol", "Dave"))
        result = parse(mode="3v3", wiki=wiki)
        self.assertEqual(len(result["counts"]), 12)
        for name in ("Alice", "AliceMate1", "AliceMate2"):
            self.assertEqual(result["counts"][name], [3, 0, 3])
            self.assertEqual(result["places"][name], 1)

    def test_missing_or_overlapping_rosters_are_not_guessed(self):
        with self.assertRaisesRegex(goodgame.IncompleteTournament, "состав команды"):
            parse(mode="3v3")
        wiki = "{{TrioOpponent|Alice|Bob|Carol}}{{TrioOpponent|Dave|Eve|Frank}}"
        with self.assertRaisesRegex(goodgame.IncompleteTournament, "Неоднозначный состав"):
            parse(mode="3v3", wiki=wiki)

    def test_truncated_drawn_or_unfinished_brackets_are_not_published(self):
        unfinished = bracket()
        unfinished["status"] = "START"
        truncated = bracket()
        truncated["format"]["grids"][0]["grid"]["upGrid"]["_stages"][1]["matches"].pop()
        drawn = bracket()
        drawn["format"]["grids"][0]["grid"]["upGrid"]["_stages"][-1]["matches"][0]["scores"] = [0, 0]
        for view in (unfinished, truncated, drawn):
            with self.subTest(view=view["status"]), self.assertRaises(goodgame.IncompleteTournament):
                parse(view)

    def test_stable_user_id_and_explicit_aliases(self):
        resolver = goodgame.Identities(sheet(), {"users": {"42": "Alice"}, "aliases": {"alice_old": "Alice"}})
        self.assertEqual(resolver.user({"id": 42, "username": "ChangedName"}), "Alice")
        self.assertEqual(resolver.name("Alice_old"), "Alice")
        self.assertEqual(resolver.name("Alicee"), "Alicee")
        two_players = sheet()
        two_players["players"].append({"name": "Bob"})
        resolver = goodgame.Identities(two_players, {})
        with self.assertRaisesRegex(goodgame.IncompleteTournament, "сопоставить"):
            resolver.user({"id": 43, "username": "Alice", "profile": {
                "games": [{"game": goodgame.GAME_ID, "account": "Bob"}]}})


class MergeTests(unittest.TestCase):
    def test_empty_event_is_filled_without_changing_sheet_prize_totals(self):
        original = sheet()
        result = goodgame.merge_supplements(original, [parse()])
        self.assertEqual(original, sheet())
        self.assertEqual(len(result["events"]), 2)
        alice = next(p for p in result["players"] if p["name"] == "Alice")
        self.assertEqual(alice["results"], [[0, 1, 0, 1], [1, 3, 0, 3]])
        self.assertEqual(alice["places"], [5, 2, 1, 0])
        self.assertEqual(alice["goodgamePlaces"], [1, 0, 0, 0])
        self.assertEqual(result["events"][1]["source"], "goodgame")

    def test_repeated_daily_refresh_does_not_accumulate(self):
        first = goodgame.merge_supplements(sheet(), [parse()])
        second = goodgame.merge_supplements(sheet(), [parse()])
        self.assertEqual(first["players"], second["players"])
        with self.assertRaisesRegex(ValueError, "original Google Sheet"):
            goodgame.merge_supplements(first, [parse()])

    def test_even_partially_filled_google_event_replaces_entire_supplement(self):
        fresh = sheet()
        fresh["events"][1]["players"] = 1
        fresh["players"][0]["results"].append([1, 9, 1, 10])
        fresh["players"][0]["places"][0] = 6
        result = goodgame.merge_supplements(fresh, [parse()])
        self.assertEqual(result["players"], fresh["players"])
        self.assertEqual(result["events"][1]["source"], "google-sheet")
        self.assertNotIn("goodgamePlaces", result["players"][0])

    def test_repeated_sheet_columns_cannot_import_an_already_filled_event(self):
        fresh = sheet()
        fresh["events"][1]["players"] = 1
        fresh["players"][0]["results"].append([1, 9, 1, 10])
        fresh["events"].append({"name": "Rusaoc Cup 188", "mode": "1v1", "players": 0})
        result = goodgame.merge_supplements(fresh, [parse()])
        self.assertEqual(result["players"], fresh["players"])

    def test_future_completed_tournament_adds_column(self):
        item = parse()
        item.update(number=193)
        item["event"]["name"] = "Rusaoc Cup 193"
        result = goodgame.merge_supplements(sheet(), [item])
        self.assertEqual(len(result["events"]), 3)
        self.assertEqual(result["players"][0]["results"][-1], [2, 3, 0, 3])

    def test_duplicate_supplements_and_mode_conflicts_fail(self):
        with self.assertRaisesRegex(ValueError, "Duplicate supplement"):
            goodgame.merge_supplements(sheet(), [parse(), parse()])
        item = parse()
        item["mode"] = "3v3"
        with self.assertRaisesRegex(ValueError, "format differs"):
            goodgame.merge_supplements(sheet(), [item])

    def test_previously_published_supplement_cannot_silently_disappear(self):
        previous = goodgame.merge_supplements(sheet(), [parse()])
        with patch.object(goodgame, "fetch_completed", return_value={}):
            with self.assertRaisesRegex(ValueError, "disappeared"):
                goodgame.supplement_sheet(sheet(), {}, previous)


class DiscoveryTests(unittest.TestCase):
    def test_historical_duplicates_do_not_block_new_editions(self):
        def row(number, identity):
            return {"id": identity, "title": f"RUSAOC CUP {number} | Test", "status": "CLOSED",
                    "url": f"rusaoc-cup-{number}-{identity}", "creator": {"id": goodgame.ORGANISER_ID}}
        rows = [row(188, "a"), row(50, "b"), row(50, "c")]
        with patch.object(goodgame, "graphql", return_value=rows):
            self.assertEqual(list(goodgame.fetch_completed(186)), [188])
        with patch.object(goodgame, "graphql", return_value=[row(188, "a"), row(188, "b")]):
            with self.assertRaisesRegex(ValueError, "duplicate Rusaoc Cup"):
                goodgame.fetch_completed(186)

    def test_empty_api_archive_is_an_error_but_no_new_tournaments_is_valid(self):
        with patch.object(goodgame, "graphql", return_value=[]):
            with self.assertRaisesRegex(ValueError, "empty tournament archive"):
                goodgame.fetch_completed(193)
        rows = [{"id": "old", "title": "RUSAOC CUP 192 | Test", "status": "CLOSED",
                 "url": "rusaoc-cup-192-1", "creator": {"id": goodgame.ORGANISER_ID}}]
        with patch.object(goodgame, "graphql", return_value=rows):
            self.assertEqual(goodgame.fetch_completed(193), {})


if __name__ == "__main__":
    unittest.main()
import json
