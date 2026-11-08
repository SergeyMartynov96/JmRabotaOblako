import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cloud_production as m
D = m.D


def item(ident, name, quantity, price='100', packing='1'):
    return dict(id=str(ident), name=name, quantity=str(quantity), price=price, packing=packing)


class ProductionChecks(unittest.TestCase):
    def test_real_card_schema_and_total(self):
        doc = m.Card()
        doc.feed('<table><tr><th>Подразделение</th><td>Branch</td></tr><tr>'
                 '<td class="supply-nomenclature" data-id="1">Паста</td>'
                 '<td class="supply-totla-count">4.000</td>'
                 '<td class="supply-package-volume">1</td>'
                 '<td class="supply-item-price">93</td>'
                 '<td class="supply-total-price">372</td></tr></table>')
        self.assertEqual(doc.field('Подразделение'), 'Branch')
        self.assertEqual(m.total(doc.items), D(372))
        self.assertEqual(len(doc.items), 1)

    def test_singletons_and_twos_survive(self):
        original = [item(1, 'Обычное', 1), item(2, 'Другое', 2), item(3, 'Третье', 10)]
        result = m.allocate(original, D(0), [])
        self.assertEqual([D(x['quantity']) for x in result], [D(1), D(2), D(4)])
        self.assertEqual(original[2]['quantity'], '10')

    def test_tops_reduced_less_and_packing_obeyed(self):
        original = [item(1, 'Паста Карбонара', 10), item(2, 'Обычное', 10), item(3, 'Другое', 12, packing='3')]
        result = m.allocate(original, D(1800), ['паста карбонара'])
        self.assertGreater(D(result[0]['quantity']), D(result[1]['quantity']))
        self.assertEqual(D(result[2]['quantity']) % 3, 0)
        self.assertGreaterEqual(D(result[0]['quantity']), 7)

    def test_below_plan_never_increased(self):
        original = [item(1, 'Обычное', 4)]
        self.assertEqual(m.allocate(original, D(1000), []), original)

    def test_fractional_schema_rejected(self):
        with self.assertRaises(RuntimeError):
            m.allocate([item(1, 'A', '1.5')], D(100), [])

    def test_fingerprint_stable_but_detects_quantity_or_price_change(self):
        first = [item(1, 'A', '4.000'), item(2, 'B', 2)]
        second = [item(2, 'Renamed B', '2.0'), item(1, 'A', 4)]
        self.assertEqual(m.fingerprint(first), m.fingerprint(second))
        second[0]['quantity'] = '1'
        self.assertNotEqual(m.fingerprint(first), m.fingerprint(second))

    def test_top_lists_loaded_from_regulation(self):
        self.assertEqual(len(m.tops('М42')), 55)
        self.assertEqual(len(m.tops('Г34')), 50)
        self.assertTrue(m.is_top('Паста Карбонара (СПТ) 250 г', m.tops('М42')))

    def test_a1_beyond_z(self):
        self.assertEqual(m.a1(9, 26), 'AA10')


if __name__ == '__main__':
    unittest.main()
