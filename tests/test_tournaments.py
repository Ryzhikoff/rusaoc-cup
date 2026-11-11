import copy
from decimal import Decimal
import datetime as dt
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tournaments as t
import liquipedia
import update
import google_sheet
import goodgame


def sheet():
    return {"players": [{"name": "Alice", "results": [[0, 1, 0, 1], [1, 2, 0, 2]]}],
            "events": [{"name": "Rusaoc Cup 100_1", "mode": "1v1", "players": 1},
                       {"name": "Rusaoc Cup 100", "mode": "1v1", "players": 1}]}


def pages():
    return {"Rusaoc Cup/100": """{{Infobox league|date=2023-01-07|format=1v1|map1=Arabia|map2=Arena|player_number=8|prizepool=90000|localcurrency=RUB}}
{{SoloPrizePool|localcurrency=RUB|{{Slot|localprize=41400|{{Opponent|Alice}}}}|{{Slot|localprize=18000|{{Opponent|Bob}}}}}}
{{ParticipantTable|p1=Alice|p2=Bob}}""",
            "Rusaoc Cup/100/Qualifier/1": "{{Infobox league|date=2022-12-30|map1=Nomad}}",
            "Rusaoc Cup/193": "{{Infobox league|date=2099-01-01|format=1v1|prizepool=10000|localcurrency=RUB}}"}


class CatalogueTests(unittest.TestCase):
    def test_groups_qualifiers_once_without_future_prizes(self):
        events = t.prepare_events(pages(), "", sheet(), today=dt.date(2026, 11, 11))
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["sheetIndices"], [0, 1])
        self.assertEqual(e["rubMinor"], 9_000_000)
        self.assertEqual(e["maps"], ["Arabia", "Arena"])
        self.assertEqual(e["qualifiers"][0]["maps"], ["Nomad"])
        self.assertEqual(e["players"], 8)
        self.assertEqual(e["prizes"][0]["players"], ["Alice"])

    def test_each_tournament_uses_its_own_historical_rate(self):
        events = [{"endDate": "2017-09-16", "rubMinor": 450000, "usdMinor": None, "prizes": []},
                  {"endDate": "2026-10-24", "rubMinor": 1000000, "usdMinor": None, "prizes": [{"rubMinor": 400000, "usdMinor": None}]}]
        rates = {"RUB:2017-09-16": "0.017464140138827", "RUB:2026-10-24": "0.012448374877731"}
        t.apply_exchange_rates(events, rates)
        self.assertEqual([e["usdMinor"] for e in events], [7859, 12448])
        self.assertEqual(sum(e["usdMinor"] for e in events), 20307)
        self.assertEqual(events[1]["prizes"][0]["usdMinor"], 4979)
        self.assertEqual(sum(e["rubMinor"] for e in events), 1450000)

    def test_explicit_dollars_and_missing_date_are_preserved(self):
        events = [{"endDate": "2020-01-01", "rubMinor": 100000, "usdMinor": 1200, "prizes": []},
                  {"endDate": None, "rubMinor": 100000, "usdMinor": None, "prizes": []}]
        t.apply_exchange_rates(events, {"RUB:2020-01-01": "0.02"})
        self.assertEqual([e["usdMinor"] for e in events], [1200, None])

    def test_cached_currency_needs_no_requests_and_bad_response_fails(self):
        events = [{"endDate": "2026-10-24", "rubMinor": 1000000}]
        with patch.object(liquipedia, "api_query") as query:
            self.assertEqual(t.fetch_exchange_rates(events, {"exchangeRates": {"RUB:2026-10-24": "0.0124"}}), {"RUB:2026-10-24": "0.0124"})
            query.assert_not_called()
        with patch.object(liquipedia, "api_query", return_value={"expandtemplates": {"wikitext": "RUB:2026-10-24=error"}}):
            with self.assertRaisesRegex(ValueError, "Invalid historical"):
                t.fetch_exchange_rates(events, {})

    def test_team_count_and_fund_are_not_multiplied_per_player(self):
        raw = "{{Infobox league|format=3v3|team_number=9|prizepool=14190|localcurrency=RUB|date=2026-08-23|map1=Fortified Clearing}}"
        source = {"events": [{"name": "Rusaoc Cup 186", "mode": "3v3"}], "players": []}
        e = t.prepare_events({"Rusaoc Cup/186": raw}, "", source)[0]
        self.assertEqual((e["players"], e["teams"], e["rubMinor"]), (27, 9, 1419000))

    def test_unknown_maps_and_inexact_prizes_stay_unknown(self):
        raw = "*'''Rusaoc Cup 43''' is played on [[Arabia]] exclusively. The tournament was played on 13 September 2020. The prizepool was 1000+ rub and 2 hour private lesson."
        source = {"events": [{"name": "Rusaoc Cup 43", "mode": "1v1"}], "players": []}
        e = t.prepare_events({}, raw, source)[0]
        self.assertEqual(e["maps"], ["Arabia"])
        self.assertEqual(e["date"], "2020-09-13")
        self.assertIsNone(e["rubMinor"])
        self.assertIn("Не менее", e["prizeNote"])
        self.assertEqual(t.map_names("{{Infobox league|map1=TBA}}", {"map1": "TBA"})[0], [])

    def test_small_arithmetic_is_safe_and_rounds_cents(self):
        self.assertEqual(t.number("{{#expr: 0.35*55530 }}"), Decimal("19435.50"))
        self.assertEqual(t.minor(t.number("{{#expr: 10/3 }}")), 333)
        for value in ("1000+", "__import__('os').system('pwd')", "2**100000", "1/0", "-2"):
            self.assertIsNone(t.number(value))
        self.assertEqual(t.minor(t.number("0")), 0)

    def test_nested_last_opponent_does_not_receive_the_same_prize(self):
        raw = "{{SoloPrizePool|{{Slot|localprize=3600|{{Opponent|Alice|lastvs={{Opponent|Bob}}}}}}|{{Slot|localprize=2700|{{Opponent|Bob|lastvs={{Opponent|Alice}}}}}}}}"
        slots = t.prize_slots(raw)
        self.assertEqual([(p["place"], p["players"]) for p in slots], [("1", ["Alice"]), ("2", ["Bob"])])

    def test_imported_places_require_a_decided_and_explicit_final(self):
        raw = "<!-- Grand Final -->{{Match|bestof=3|opponent1={{SoloOpponent|Alice}}|opponent2={{SoloOpponent|Bob}}|map1={{Map|winner=1}}|map2={{Map|winner=1}}}}<!-- Lower Bracket Final -->{{Match|winner=1|opponent1={{SoloOpponent|Bob}}|opponent2={{SoloOpponent|Carol}}}}"
        self.assertEqual(t.podium_from_finals(raw), {"1": ["Alice"], "2": ["Bob"], "3": ["Carol"]})
        unfinished = raw.replace('|map2={{Map|winner=1}}', '')
        self.assertNotIn("1", t.podium_from_finals(unfinished))

    def test_missing_or_duplicate_tournament_rejected(self):
        events = t.prepare_events(pages(), "", sheet())
        t.validate_catalog({"events": events}, sheet())
        with self.assertRaisesRegex(ValueError, "Missing or duplicate"):
            t.validate_catalog({"events": events * 2}, sheet())
        with self.assertRaisesRegex(ValueError, "Missing or duplicate"):
            t.validate_catalog({"events": []}, sheet())

    def test_team_prizes_are_equal_shares_and_round_only_after_aggregation(self):
        source = {"players": [{"name": n} for n in ("Alice", "Bob", "Carol")]}
        events = [{"cup": cup, "mode": "3v3", "prizes": [{"place": "1", "players": ["Alice", "Bob", "Carol"], "rubMinor": 100, "usdMinor": 10}]} for cup in (1, 2, 3)]
        earnings = t.player_earnings(events, source)
        # Three thirds sum to exactly one rouble / ten cents per player.
        for player in earnings.values():
            self.assertEqual(player["rubSixths"], 600)
            self.assertEqual(player["usdSixths"], 60)
            self.assertEqual(len(player["awards"]), 3)
        self.assertEqual(sum(p["rubSixths"] for p in earnings.values()), 300 * 6)

    def test_missing_recipients_and_incomplete_rosters_are_not_guessed(self):
        source = {"players": [{"name": "Alice"}, {"name": "Bob"}]}
        events = [{"cup": 1, "mode": "2v2", "prizes": [{"place": "1", "players": ["Alice"], "rubMinor": 100, "usdMinor": 10}]}]
        self.assertEqual(t.player_earnings(events, source), {})
        events[0]["mode"] = "1v1"
        events[0]["prizes"][0]["usdMinor"] = None
        self.assertIsNone(t.player_earnings(events, source)["Alice"]["usdSixths"])
        self.assertNotIn("Bob", t.player_earnings(events, source))
        events[0]["prizes"].append(copy.deepcopy(events[0]["prizes"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate prize"):
            t.player_earnings(events, source)

    def test_existing_identity_aliases_link_prizes_to_current_players(self):
        source = {"players": [{"name": "exGC_SMY"}], "events": [{"name": "Rusaoc Cup 1", "mode": "1v1"}]}
        raw = "{{Infobox league|date=2026-01-01|format=1v1|localcurrency=RUB|prizepool=1000}}{{SoloPrizePool|{{Slot|localprize=1000|{{Opponent|SMY}}}}}}"
        e = t.prepare_events({"Rusaoc Cup/1": raw}, "", source)[0]
        self.assertEqual(e["prizes"][0]["players"], ["exGC_SMY"])

    def test_duo_roster_corrects_mislabelled_solo_infobox(self):
        raw = "{{Infobox league|date=2026-01-01|format=1v1|team_number=8|localcurrency=RUB|prizepool=1000}}{{TeamCard|team=Example|p1=Alice|p2=Bob}}{{TeamPrizePool|{{Slot|localprize=1000|{{Opponent|Example}}}}}}"
        source = {"players": [{"name": "Alice"}, {"name": "Bob"}], "events": [{"name": "Rusaoc Cup 1", "mode": "2v2"}]}
        e = t.prepare_events({"Rusaoc Cup/1": raw}, "", source)[0]
        self.assertEqual((e["mode"], e["players"]), ("2v2", 16))
        self.assertEqual(e["prizes"][0]["place"], "1")
        self.assertEqual(t.player_earnings([e], source)["Alice"]["rubSixths"], 50000 * 6)


class CataloguePublicationTests(unittest.TestCase):
    def test_catalogue_fetch_failure_keeps_existing_html_and_images(self):
        import json
        seed = sheet()
        document = '<script id="seed" type="application/json">' + json.dumps(seed) + '</script><script id="countries" type="application/json">{}</script><script id="tournaments" type="application/json">{}</script>'
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in update.FILES:
                (directory / name).write_text(document)
            (directory / "maps").mkdir()
            asset = directory / "maps/old.png"
            asset.write_bytes(b"unchanged")
            with patch.object(google_sheet, "fetch_sheet", return_value=seed), patch.object(goodgame, "supplement_sheet", return_value=seed), patch.object(liquipedia, "fetch_pages", return_value={}), patch.object(liquipedia, "build", return_value=[]), patch.object(liquipedia, "make_seed", return_value=seed), patch.object(liquipedia, "fetch_player_profiles", return_value=[]), patch.object(liquipedia, "make_countries", return_value={"players": {"alice": {"code": "RU"}}}), patch.object(t, "build_catalog", side_effect=RuntimeError("thumbnail unavailable")):
                with self.assertRaisesRegex(RuntimeError, "thumbnail unavailable"):
                    update.update_site(directory)
            for name in update.FILES:
                self.assertEqual((directory / name).read_text(), document)
            self.assertEqual(asset.read_bytes(), b"unchanged")


if __name__ == "__main__":
    unittest.main()
