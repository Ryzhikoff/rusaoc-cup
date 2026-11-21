import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import goodgame
import liquipedia
import player_merges
import tournaments
import update


class PlayerMergeTests(unittest.TestCase):
    def test_display_aliases_preserve_case_and_support_both_page_names(self):
        result = player_merges.build_aliases(
            {"Landau", "Landau_", "UMARR", "Koliasik", "Daniil", "DzeDanik", "Unrelated"},
            liquipedia.ALIASES)
        self.assertEqual(result["Landau"], ["Landau_", "p0pengaben"])
        self.assertEqual(result["UMARR"], ["Rexan_"])
        self.assertEqual(result["Koliasik"], ["Колясик"])
        self.assertIn("DzeDanik", result["Daniil"])
        self.assertIn("Daniil", result["DzeDanik"])
        self.assertNotIn("Unrelated", result)
        for name, aliases in result.items():
            self.assertNotIn(name.casefold(), [a.casefold() for a in aliases])

    def test_display_alias_groups_handle_chains_cycles_and_duplicate_casing(self):
        config = {"aliases": {"old": "Main", "older": "Old", "main": "Main"},
                  "aliasDisplayNames": {"older": "Older"}}
        with patch.object(player_merges, "CONFIG") as path:
            path.read_text.return_value = json.dumps(config)
            result = player_merges.build_aliases({"Main", "Old", "Unrelated"}, {"OLD": "Main"})
        self.assertEqual(result, {"Main": ["Old", "Older"], "Old": ["Main", "Older"]})

    def test_confirmed_merge_preserves_results_places_and_account_ids(self):
        source = {"updated": "2026-11-21T00:00:00Z", "events": [{"players": 1}] * 2,
                  "players": [
                      {"name": "p0pengaben", "places": [1, 1, 0, 0], "results": [[0, 15, 18, 33]]},
                      {"name": "Landau", "places": [0, 0, 0, 0], "results": [[1, 4, 4, 8]],
                       "goodgamePlaces": [0, 0, 1, 0], "goodgamePlacements": {"1": 3}},
                      {"name": "AnotherPlayer", "places": [0, 0, 0, 0], "results": []}],
                  "goodgameIdentities": {"42": "p0pengaben", "43": "Landau", "44": "AnotherPlayer"}}
        original = copy.deepcopy(source)
        result = player_merges.merge_players(source)
        self.assertEqual(source, original)
        self.assertEqual([p["name"] for p in result["players"]], ["Landau", "AnotherPlayer"])
        merged = result["players"][0]
        self.assertEqual(merged["results"], [[0, 15, 18, 33], [1, 4, 4, 8]])
        self.assertEqual(merged["places"], [1, 1, 0, 0])
        self.assertEqual(merged["goodgamePlaces"], [0, 0, 1, 0])
        self.assertEqual(merged["goodgamePlacements"], {"1": 3})
        self.assertEqual(result["goodgameIdentities"], {"42": "Landau", "43": "Landau", "44": "AnotherPlayer"})
        self.assertEqual(result["events"], source["events"])
        self.assertEqual(result["updated"], source["updated"])
        self.assertEqual(player_merges.merge_players(result), result)

    def test_overlapping_tournaments_fail_without_modifying_the_source(self):
        for second in ([0, 1, 0, 1], [0, 0, 1, 1]):
            source = {"players": [{"name": "p0pengaben", "results": [[0, 1, 0, 1]]},
                                  {"name": "Landau", "results": [second]}]}
            original = copy.deepcopy(source)
            with self.subTest(second=second), self.assertRaisesRegex(ValueError, "overlapping tournament"):
                player_merges.merge_players(source)
            self.assertEqual(source, original)

    def test_existing_aliases_are_not_additional_unapproved_merges(self):
        source = {"players": [{"name": "H1ew", "results": [[0, 1, 0, 1, 0]]},
                              {"name": "Lamo", "results": [[1, 1, 0, 1, 0]]},
                              {"name": "p0pengaben", "results": [[2, 1, 0, 1, 1]]}]}
        result = player_merges.merge_players(source)
        self.assertEqual([p["name"] for p in result["players"]], ["H1ew", "Lamo", "Landau"])
        self.assertEqual(result["players"][:2], source["players"][:2])

    def test_future_liquipedia_snapshot_uses_the_confirmed_name(self):
        names = ("p0pengaben", "Landau")
        pages = [{"cup": i + 1, "mode": "1v1", "declared": 1, "date": "2026-01-01",
                  "participants": [name], "champions": [name], "stats": {name: [1, 0, 1]},
                  "quality": {"matches": 1, "mapMatches": 1, "scoreMatches": 0,
                              "unresolved": 0, "unknownScores": 0}}
                 for i, name in enumerate(names)]
        with patch.object(liquipedia, "PAGES", {f"Rusaoc Cup/{i + 1}": "" for i in range(2)}):
            seed = liquipedia.make_seed(pages)
        self.assertEqual(seed["players"], [{"name": "Landau", "results": [[0, 1, 0, 1, 1], [1, 1, 0, 1, 1]]}])

    def test_goodgame_and_catalogue_link_both_nicks_to_landau(self):
        source = {"players": [{"name": "Landau", "results": []}],
                  "events": [{"name": "Rusaoc Cup 1", "mode": "1v1"}]}
        resolver = goodgame.Identities(source, json.loads(goodgame.CONFIG.read_text()))
        self.assertEqual(resolver.name("P0PENGABEN"), "Landau")
        events = []
        for name in ("p0pengaben", "Landau"):
            raw = "{{Infobox league|date=2026-01-01|format=1v1|localcurrency=RUB|prizepool=1000}}{{SoloPrizePool|{{Slot|localprize=1000|{{Opponent|" + name + "}}}}}}"
            event = tournaments.prepare_events({"Rusaoc Cup/1": raw}, "", source)[0]
            self.assertEqual(event["prizes"][0]["players"], ["Landau"])
            events.append(event)
        earnings = tournaments.player_earnings(events, source)
        self.assertEqual(set(earnings), {"Landau"})
        self.assertEqual(earnings["Landau"]["rubSixths"], 200000 * 6)

    def test_daily_update_merges_the_fresh_sheet_before_goodgame_matching(self):
        seed = {"events": [{"name": "Rusaoc Cup 1", "mode": "1v1", "players": 1}],
                "players": [{"name": "p0pengaben", "places": [1, 0, 0, 0], "results": [[0, 1, 0, 1]]}]}
        document = '<script id="seed" type="application/json">' + json.dumps(seed) + '</script>'
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for filename in update.FILES:
                (directory / filename).write_text(document)
            with patch.object(update.google_sheet, "fetch_sheet", return_value=seed), patch.object(liquipedia, "fetch_pages", return_value={}), patch.object(liquipedia, "build", return_value=[]), patch.object(liquipedia, "make_seed", return_value=seed), patch.object(goodgame, "supplement_sheet", side_effect=RuntimeError("stop before network imports")) as supplement:
                with self.assertRaisesRegex(RuntimeError, "stop before network"):
                    update.update_site(directory)
                self.assertEqual(supplement.call_args.args[0]["players"][0]["name"], "Landau")
            for filename in update.FILES:
                self.assertEqual((directory / filename).read_text(), document)

    def test_rexan_third_place_is_credited_to_umarr_as_half_the_team_prize(self):
        source = {"players": [{"name": "UMARR"}, {"name": "AntagonisT"}],
                  "events": [{"name": "Rusaoc Cup 153", "mode": "2v2"}]}
        raw = "{{Infobox league|date=2025-03-08|format=2v2|team_number=25|localcurrency=RUB|prizepool=14845}}{{TeamCard|team=Third|p1=AntagonisT|p2=Rexan_}}{{TeamPrizePool|{{Slot|place=3|localprize=2969|usdprize=32.99|{{Opponent|Third}}}}}}"
        event = tournaments.prepare_events({"Rusaoc Cup/153": raw}, "", source)[0]
        self.assertEqual(event["prizes"][0]["players"], ["AntagonisT", "UMARR"])
        earnings = tournaments.player_earnings([event], source)
        self.assertEqual(set(earnings), {"UMARR", "AntagonisT"})
        self.assertEqual(earnings["UMARR"]["rubSixths"], 296900 * 3)
        self.assertEqual(earnings["UMARR"]["usdSixths"], 3299 * 3)
        self.assertEqual(earnings["UMARR"]["awards"][0]["place"], "3")
        self.assertEqual(earnings["AntagonisT"], earnings["UMARR"])
        resolver = goodgame.Identities(source, json.loads(goodgame.CONFIG.read_text()))
        self.assertEqual(resolver.name("Rexan_"), "UMARR")

    def test_rexan_merge_keeps_already_counted_umarr_medals(self):
        source = {"players": [{"name": "UMARR", "places": [1, 1, 2, 0], "results": [[1, 1, 0, 1]]}]}
        self.assertEqual(player_merges.merge_players(source), source)
        source["players"].append({"name": "Rexan_", "results": [[0, 2, 1, 3]]})
        result = player_merges.merge_players(source)
        self.assertEqual(result["players"], [{"name": "UMARR", "places": [1, 1, 2, 0],
                                              "results": [[0, 2, 1, 3], [1, 1, 0, 1]]}])
        self.assertEqual(player_merges.merge_players(result), result)


if __name__ == "__main__":
    unittest.main()
