import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "readonly", Path(__file__).resolve().parents[1] / "scripts/cloud_readonly_test.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReadOnlyChecks(unittest.TestCase):
    def test_write_endpoints_rejected_before_network(self):
        client = module.ReadOnlySU()
        for path in ("/turnover/supplyRequest/edit/123", "/site/logout", "https://example.com/"):
            with self.assertRaises(ValueError):
                client.request(path, {"quantity": 0})

    def test_only_known_suppliers_match(self):
        headers = ["Дата", "Мамадалиев", "Маматулин", "Кисель"]
        self.assertEqual(module.supplier_column("ИП Маматулин Данис Маратович", headers), 2)
        self.assertIsNone(module.supplier_column("Комбинат Питания №3", headers))
        self.assertIsNone(module.supplier_column("КИСЕЛЬ", ["Кисель", "Кисель"]))

    def test_surname_typo_and_inflection(self):
        match = module.supplier_column
        self.assertEqual(match("ИП Сиразетдинов Артур Феликсович", ["Дата", "Сиразединов"]), 1)
        self.assertEqual(match("ИП Сиразединова", ["Сиразетдинов"]), 0)
        self.assertIsNone(match("ИП Сиразединов", ["Сиразетдинов", "Сиразединова"]))
        self.assertEqual(match("ИП Мамадалиев Мухаммадали Арипович", ["Мамадалиев", "Маматулин"]), 0)
        self.assertIsNone(match("ИП Мамадалиев", ["Маматулин"]))
        self.assertEqual(match("ИП Сиразединов", ["ИП Сиразетдинов Артур Феликсович"]), 0)
        self.assertEqual(match("ИП Фирсова Наталья Александровна", ["ИП Фирсова Н. А."]), 0)
        self.assertIsNone(match("ИП Фирсова", ["Фирсова Наталья", "Фирсова Анна"]))

    def test_distribution_centre_near_surname_is_not_a_local_supplier(self):
        self.assertIsNone(module.supplier_column('ИП Мухачев Кирилл Сергеевич (РЦ)', ['Мурачев']))
        self.assertEqual(module.supplier_column('ИП Мурачев Михаил Анатольевич', ['Мурачев']), 0)
        self.assertEqual(module.supplier_column('ИП Мухачев Кирилл Сергеевич (РЦ)', ['Мухачев']), 0)

    def test_slot_and_department_filter(self):
        def row(ident, time, department="Нужный филиал"):
            values = ["", ident, "date", "arrival", "shipment", "", department,
                      "author", "Кисель", f"Ожидает отправки ({time})", "", "100"]
            return "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
        html = "<table>" + row("10228453", "15:00") + row("10228454", "14:00") + "</table>"
        found = module.report_requests(html, "Нужный филиал", {"15:00", "16:00"})
        self.assertEqual([item["id"] for item in found], ["10228453"])
        with self.assertRaises(RuntimeError):
            module.report_requests(row("10228453", "15:00", "Другой филиал"), "Нужный филиал", {"15:00"})

    def test_pagination_cannot_silently_truncate(self):
        with self.assertRaises(RuntimeError):
            module.report_requests('<a href="?SupplyRequest_page=2">2</a>', "branch", {"15:00"})

    def test_exact_card_prices(self):
        doc = module.Document()
        doc.feed('<table><tr><td class="supply-total-price">9 348,91</td></tr></table>')
        self.assertEqual(doc.prices, [module.Decimal("9348.91")])


if __name__ == "__main__":
    unittest.main()
