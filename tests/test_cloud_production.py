import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cloud_production as m
D = m.D


def item(ident, name, quantity, price='100', packing='1'):
    return dict(id=str(ident), name=name, quantity=str(quantity), price=price, packing=packing)


class ProductionChecks(unittest.TestCase):
    def transfer_plan(self):
        name, dep, ident, gid = m.active_branches()[0]
        items = [item(1, 'A', 4)]
        entry = dict(items=items, before=m.fingerprint(items), after=m.fingerprint(items))
        return dict(branches=[dict(name=name, department=dep, ident=ident, gid=gid,
                                  groups=[dict(cards=[entry])])])

    def test_only_av12_is_active_for_automatic_and_manual_runs(self):
        self.assertEqual([b[0] for b in m.active_branches()], ['АВ12'])
        m.validate_transfer_plan(self.transfer_plan())

    def test_transfer_plan_rejects_other_branch_before_any_network(self):
        from unittest.mock import Mock
        import tempfile, json
        plan = self.transfer_plan()
        name, dep, ident, gid = m.ro.BRANCHES[1]
        plan['branches'].append(dict(name=name, department=dep, ident=ident, gid=gid, groups=[]))
        client = Mock()
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json') as f:
            json.dump(plan, f); f.flush()
            with self.assertRaises(m.Stop):
                m.apply(client, Mock(), Mock(), type('Args', (), {'plan': f.name})())
        client.request.assert_not_called()

    def test_transfer_rejects_both_declared_and_disguised_request_edits(self):
        plan = self.transfer_plan()
        entry = plan['branches'][0]['groups'][0]['cards'][0]
        entry['after'] = 'different'
        with self.assertRaises(m.Stop):
            m.validate_transfer_plan(plan)
        entry['after'] = entry['before']
        entry['desired'] = [item(1, 'A', 3)]
        with self.assertRaises(m.Stop):
            m.validate_transfer_plan(plan)

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
        self.assertEqual([D(x['quantity']) for x in result], [D(1), D(2), D(2)])
        self.assertEqual(original[2]['quantity'], '10')

    def test_tops_reduced_less_and_packing_obeyed(self):
        original = [item(1, 'Паста Карбонара', 10), item(2, 'Обычное', 10), item(3, 'Другое', 12, packing='3')]
        result = m.allocate(original, D(1800), ['паста карбонара'])
        self.assertGreater(D(result[0]['quantity']), D(result[1]['quantity']))
        self.assertEqual(D(result[2]['quantity']) % 3, 0)
        self.assertGreaterEqual(D(result[0]['quantity']), 7)

    def test_top_can_reduce_below_arbitrary_percentage_floor(self):
        result = m.allocate([item(1, 'Top', 20), item(2, 'Other', 20)], D(1000), ['top'])
        self.assertLess(D(result[0]['quantity']), D(13))
        self.assertGreater(D(result[0]['quantity']), D(result[1]['quantity']))
        self.assertLessEqual(abs(m.total(result) - D(1000)), D(100))

    def test_day_budget_uses_supplier_slack_without_touching_sent(self):
        headers = ['Day', 'Дата', 'A', 'B', 'Итого готовая еда']
        rows = [['Лимит, пн–сб', '', '60000'], ['ср', '11.11.2026', '30000', '30000']]
        original = [item(1, 'Other', 40, '1000')]
        desired = [item(1, 'Other', 31, '1000')]
        fixed = dict(items=[item(2, 'Sent', 30, '1000')], before='same', after='same')
        group = dict(arrival='11.11.2026', cell='C2', expected='31000', plan='30000',
                     supplier='A', cards=[dict(items=original, desired=desired, minimum='0')])
        sent = dict(arrival='11.11.2026', cell='D2', expected='30000', plan=None,
                    supplier='B', cards=[fixed])
        m.balance_budgets('Г34', headers, rows, [group, sent])
        self.assertEqual(group['expected'], '30000')
        self.assertEqual(fixed['items'][0]['quantity'], '30')
        self.assertNotIn('desired', fixed)
        self.assertEqual(original[0]['quantity'], '40')

    def test_infeasible_budget_stops_with_minima_and_tolerance_intact(self):
        headers = ['Day', 'Дата', 'A', 'Итого готовая еда']
        rows = [['Лимит, пн–сб', '', '60000'], ['ср', '11.11.2026', '64000']]
        entry = dict(items=[item(1, 'Other', 80, '1000')],
                     desired=[item(1, 'Other', 64, '1000')], minimum='0')
        group = dict(arrival='11.11.2026', cell='C2', expected='64000', plan='64000',
                     supplier='A', cards=[entry])
        with self.assertRaises(m.Stop):
            m.balance_budgets('Г34', headers, rows, [group])
        self.assertGreaterEqual(D(group['expected']), D(64000) * D('.95'))

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

    def test_pending_status_ignores_display_timezone_but_detects_sent(self):
        self.assertEqual(m.pending_slot('Ожидает отправки (16:00) +5 GMT'), '16:00')
        self.assertEqual(m.pending_slot('Ожидает отправки (16:00) История'), '16:00')
        self.assertIsNone(m.pending_slot('Отправлено'))

    def test_recovery_selects_sent_but_never_edits_expired_or_sent(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 9, 9, 5)
        early = dict(status='Ожидает отправки (09:00) +5 GMT')
        future = dict(status='Ожидает отправки (10:00) +5 GMT')
        sent = dict(status='Отправлено')
        self.assertFalse(m.editable_now(early, now))
        self.assertFalse(m.editable_now(sent, now))
        self.assertTrue(m.editable_now(future, now))
        self.assertTrue(m.selected_for_pass(sent, {'10:00'}, True))
        review = dict(status='На рассмотрении')
        self.assertTrue(m.selected_for_pass(review, {'10:00'}, True))
        self.assertFalse(m.editable_now(review, now))
        self.assertTrue(m.selected_for_pass(dict(status='Отгружена'), {'10:00'}, True))
        self.assertFalse(m.selected_for_pass(dict(status='Агрегирована'), {'10:00'}, True))
        self.assertFalse(m.selected_for_pass(sent, {'10:00'}))
        self.assertFalse(m.selected_for_pass(dict(status='Не отправлено'), {'10:00'}, True))
        self.assertFalse(m.selected_for_pass(dict(status='Отменено'), {'10:00'}, True))
        self.assertFalse(m.selected_for_pass(dict(status='Ожидает отправки (11:00)'), {'10:00'}, True))

    def test_daily_budget_is_independent_of_broken_total_cell(self):
        headers = ['День', 'Дата', 'Поставщик', 'СУММА ГОТОВАЯ+ЯПОНИЯ', 'план']
        rows = [['Лимит, пн–сб, ₽', '', '60000'], ['вт', '10.11.2026', '60001', '1', '']]
        groups = [dict(arrival='10.11.2026', cell='C2', expected='60001')]
        with self.assertRaises(m.Stop):
            m.check_budgets('Г34', headers, rows, groups)
        groups[0]['expected'] = '60000'
        m.check_budgets('Г34', headers, rows, groups)
        rows[1][4] = '59000'
        with self.assertRaises(m.Stop):
            m.check_budgets('Г34', headers, rows, groups)
        rows = [['Лимит, будни, ₽', '', '70000'], ['вт', '10.11.2026', '71065', '1', '']]
        groups[0]['expected'] = '71065'
        m.check_budgets('М42', headers, rows, groups)
        groups[0]['expected'] = '72001'
        with self.assertRaises(m.Stop):
            m.check_budgets('М42', headers, rows, groups)

    def report_row(self, ident, branch='Branch'):
        values = ['', ident, '13.11.2026', '15.11.2026', '', '', branch,
                  '', 'Supplier', 'На рассмотрении', '', '100']
        return '<table><tr>' + ''.join('<td>' + x + '</td>' for x in values) + '</tr></table>'

    def test_production_report_reads_all_pages_with_same_filters(self):
        from unittest.mock import Mock
        client = Mock()
        client.request.side_effect = [
            self.report_row('10200001') + '<a href="?SupplyRequest_page=2">2</a>',
            self.report_row('10200002') + '<a href="?SupplyRequest_page=1">1</a>'
        ]
        rows = m.report(client, 115, 'Branch', '13.11.2026')
        self.assertEqual([r['id'] for r in rows], ['10200001', '10200002'])
        calls = client.request.call_args_list
        self.assertEqual(calls[1].args[0], '/turnover/supplyRequest/report?SupplyRequest_page=2')
        self.assertEqual(calls[0].args[1], calls[1].args[1])
        self.assertEqual(calls[1].args[1]['SupplyRequest[department_id][]'], 115)

    def test_production_report_rejects_repeated_page_contents(self):
        from unittest.mock import Mock
        client = Mock()
        client.request.return_value = self.report_row('10200001') + '<a href="?SupplyRequest_page=2">2</a>'
        with self.assertRaises(m.Stop):
            m.report(client, 115, 'Branch', '13.11.2026')

    def test_production_report_rejects_foreign_branch_on_later_page(self):
        from unittest.mock import Mock
        client = Mock()
        client.request.side_effect = [
            self.report_row('10200001') + '<a href="?SupplyRequest_page=2">2</a>',
            self.report_row('10200002', 'Other branch')]
        with self.assertRaises(m.Stop):
            m.report(client, 115, 'Branch', '13.11.2026')

    def test_production_report_never_follows_foreign_pager(self):
        from unittest.mock import Mock
        client = Mock()
        client.request.return_value = '<a href="https://example.com/?SupplyRequest_page=2">2</a>'
        with self.assertRaises(m.Stop):
            m.report(client, 115, 'Branch', '13.11.2026')
        self.assertEqual(client.request.call_count, 1)

    def test_a1_beyond_z(self):
        self.assertEqual(m.a1(9, 26), 'AA10')


if __name__ == '__main__':
    unittest.main()
