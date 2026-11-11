import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import google_sheet
import liquipedia
import update


def sheet_fixture():
    cols = [{"label": "Игрок"}]
    for number in range(1, 191):
        cols.extend([{"label": f"Rusaoc Cup {number} (1v1; results)"}, {"label": "Поражения"}, {"label": "Карты"}])
    cols.extend({"label": label} for label in ("Призовые места (1;2;3;4)", "Общие итоги Всего побед", "Всего поражений", "Посетил турниров"))
    rows = []
    for number in range(500):
        cells = [None] * len(cols)
        cells[0] = {"v": f"Player{number}"}
        cells[1:4] = [{"v": 0}, {"v": 0}, {"v": 1}]
        cells[-4:] = [{"v": "0;0;0;0"}, {"v": 0}, {"v": 0}, {"v": 1}]
        rows.append({"c": cells})
    return {"status": "ok", "table": {"cols": cols, "rows": rows}}


class SpreadsheetTests(unittest.TestCase):
    def test_maps_without_outcome_remain_games(self):
        result = google_sheet.parse_sheet(sheet_fixture())
        self.assertEqual(result["players"][0]["results"], [[0, 0, 0, 1]])
        self.assertEqual(result["events"][0]["players"], 500)
        self.assertEqual(result["events"][1]["players"], 0)

    def test_wrong_totals_and_truncated_table_fail(self):
        response = sheet_fixture()
        response["table"]["rows"][0]["c"][-3] = {"v": 1}
        with self.assertRaisesRegex(ValueError, "totals disagree"):
            google_sheet.parse_sheet(response)
        response = sheet_fixture()
        response["table"]["rows"] = response["table"]["rows"][:50]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            google_sheet.parse_sheet(response)

    def test_negative_or_fractional_maps_fail(self):
        for value in (-1, 0.5):
            with self.subTest(value=value):
                response = sheet_fixture()
                response["table"]["rows"][0]["c"][1] = {"v": value}
                with self.assertRaises(ValueError):
                    google_sheet.parse_sheet(response)

    def test_jsonp_is_parsed_without_executing_code(self):
        payload = {"status": "ok"}
        self.assertEqual(google_sheet.decode_response('/*O_o*/\ngoogle.visualization.Query.setResponse(' + json.dumps(payload) + ');'), payload)
        with self.assertRaises(ValueError):
            google_sheet.decode_response('alert("unexpected");')


class CountryTests(unittest.TestCase):
    def test_only_explicit_player_countries_are_used(self):
        result = liquipedia.tournament_countries({"Rusaoc Cup/1": "{{Infobox league|country=Russia}}{{TeamCard|team=Example|flag=us|p1=Unknown}}{{ParticipantTable|p1=Alice|p1flag=Ukr}}{{SoloOpponent|Bob|flag=by}}"})
        self.assertEqual(result["alice"]["code"], "UA")
        self.assertEqual(result["bob"]["code"], "BY")
        self.assertNotIn("unknown", result)
        self.assertIsNone(liquipedia.country_code("CIS"))

    def test_latest_country_and_conflicting_evidence(self):
        result = liquipedia.tournament_countries({"Rusaoc Cup/1": "{{SoloOpponent|Alice|flag=ua}}", "Rusaoc Cup/2": "{{SoloOpponent|Alice|flag=us}}{{SoloOpponent|Bob|flag=ru}}{{Opponent|Bob|flag=by}}"})
        self.assertEqual(result["alice"]["code"], "US")
        self.assertNotIn("bob", result)


class PublicationTests(unittest.TestCase):
    def test_late_network_failure_preserves_both_pages(self):
        seed = {"updated": "2026-11-11T00:00:00Z", "events": [{"cup": 1}], "players": [{"name": "Alice", "results": [[0, 1, 0, 1, 1]]}]}
        document = '<script id="seed" type="application/json">' + json.dumps(seed) + '</script><script id="countries" type="application/json">{}</script>'
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in update.FILES:
                (directory / name).write_text(document)
            with patch.object(google_sheet, "fetch_sheet", return_value=seed), patch.object(liquipedia, "fetch_pages", return_value={}), patch.object(liquipedia, "build", return_value=[]), patch.object(liquipedia, "make_seed", return_value=seed), patch.object(liquipedia, "fetch_player_profiles", side_effect=RuntimeError("API unavailable")):
                with self.assertRaisesRegex(RuntimeError, "API unavailable"):
                    update.update_site(directory)
            for name in update.FILES:
                self.assertEqual((directory / name).read_text(), document)

    def test_both_embedded_json_blocks_are_safe(self):
        text = '<script id="seed" type="application/json">{}</script>'
        value = {"name": "</script><script>alert(1)</script>"}
        output = liquipedia.replace_json(text, "seed", value)
        self.assertEqual(output.count("</script>"), 1)
        self.assertIn(r"<\/script>", output)


if __name__ == "__main__":
    unittest.main()
