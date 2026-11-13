#!/usr/bin/env python3
"""Two-phase production pass; prepare is read-only. Never log business payloads."""
import argparse
import datetime as dt
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
import urllib.request
import urllib.parse
from zoneinfo import ZoneInfo

import cloud_readonly_test as ro
D = Decimal


class Stop(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def fingerprint(items):
    return digest(sorted((x['id'], str(D(x['quantity']).normalize()),
                          str(D(x['price']).normalize())) for x in items))


class Card(ro.Document):
    def __init__(self):
        super().__init__()
        self.items = []
        self.item = None
        self.kind = ''

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attrs = dict(attrs)
        if tag == 'tr':
            self.item = {}
        if tag == 'td':
            self.kind = attrs.get('class', '')
            if 'supply-nomenclature' in self.kind:
                self.item['id'] = attrs.get('data-id')

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None and self.item is not None:
            val = ' '.join(self.cell)
            for cls, key in [('supply-nomenclature', 'name'), ('supply-totla-count', 'quantity'),
                             ('supply-item-price', 'price'), ('supply-package-volume', 'packing')]:
                if cls in self.kind.split():
                    self.item[key] = val
        if tag == 'tr' and self.item and 'id' in self.item:
            self.items.append(self.item)
        super().handle_endtag(tag)

    def field(self, name):
        matches = [r[1] for r in self.rows if len(r) == 2 and r[0] == name]
        if len(matches) != 1:
            raise Stop('Card field missing or ambiguous')
        return matches[0]


def card(client, ident, title, supplier, arrival=None):
    doc = Card()
    doc.feed(client.request('/turnover/supplyRequest/view/' + ident))
    if ro.normalize(doc.field('Подразделение')) != ro.normalize(title):
        raise Stop('Card branch changed')
    if ro.normalize(doc.field('Поставщик')) != ro.normalize(supplier):
        raise Stop('Card supplier changed')
    if arrival and doc.field('Дата привоза заявки') != arrival:
        raise Stop('Card arrival changed')
    if not doc.items or len({i['id'] for i in doc.items}) != len(doc.items):
        raise Stop('Card item schema invalid')
    for item in doc.items:
        for key in ('quantity', 'price', 'packing'):
            item[key] = str(ro.number(item[key]))
        if D(item['quantity']) < 0 or D(item['price']) <= 0:
            raise Stop('Card quantities/prices invalid')
    if sum(D(x['quantity']) * D(x['price']) for x in doc.items) != sum(doc.prices):
        raise Stop('Card totals inconsistent')
    return doc


def total(items):
    return sum((D(x['quantity']) * D(x['price']) for x in items), D(0))


def report(client, dep, title, date, arrival=False):
    field = 'date_arrives' if arrival else 'date_created'
    params = {'SupplyRequest[department_id][]': dep,
              'SupplyRequest[date_created_from]': '', 'SupplyRequest[date_created_to]': '',
              'SupplyRequest[date_arrives_from]': '', 'SupplyRequest[date_arrives_to]': '',
              f'SupplyRequest[{field}_from]': date, f'SupplyRequest[{field}_to]': date,
              'SupplyRequest[actual_applications]': '1', 'yt11': 'Сформировать'}
    pending, visited, results, ids = [1], set(), [], set()
    while pending:
        page = min(pending)
        pending.remove(page)
        if page in visited:
            continue
        if len(visited) >= 1000:
            raise Stop('Report pagination limit exceeded')
        visited.add(page)
        path = '/turnover/supplyRequest/report'
        if page != 1:
            path += '?SupplyRequest_page=' + str(page)
        doc = ro.Document()
        # Repeat filters on every page; never follow arbitrary remote URLs.
        doc.feed(client.request(path, params))
        for row in doc.rows:
            if len(row) < 12 or not re.fullmatch(r'\d{7,12}', row[1]):
                continue
            if ro.normalize(row[6]) != ro.normalize(title):
                raise Stop('Report department mismatch')
            if row[1] in ids:
                raise Stop('Duplicate report IDs across pages; unstable report')
            ids.add(row[1])
            results.append({'id': row[1], 'supplier': row[8], 'status': row[9]})
        for link in doc.links:
            parsed = urllib.parse.urlsplit(link)
            query = urllib.parse.parse_qs(parsed.query)
            keys = [k for k in query if k == 'page' or k.endswith('_page')]
            if not keys:
                continue
            if (parsed.scheme or parsed.netloc or parsed.path not in ('', '/turnover/supplyRequest/report')
                    or keys != ['SupplyRequest_page']):
                raise Stop('Unrecognized report pagination link')
            values = query[keys[0]]
            if len(values) != 1 or not re.fullmatch(r'[1-9]\d*', values[0]):
                raise Stop('Invalid report page')
            target = int(values[0])
            if target not in visited and target not in pending:
                pending.append(target)
    return results


def top_key(name):
    name = re.sub(r'\b\d+(?:[.,]\d+)?\s*(?:кг|гр|г|мл|л)\b|\b\d{8,14}\b', '', name, flags=re.I)
    name = re.sub(r'\((?:KVI|Тмн|СПТ)\)', '', name, flags=re.I)
    name = name.replace('Жизньмарт', '').replace('ЖМ', '')
    return ro.normalize(name)


def tops(branch):
    text = (ro.ROOT / 'LifeMart-Codex-Orders.md').read_text()
    section = text.split('## Топы ' + branch, 1)[1].split('\n## ', 1)[0]
    names = [top_key(line[2:]) for line in section.splitlines() if line.startswith('- ')]
    if len(names) < 40:
        raise Stop('Top list incomplete')
    return names


def is_top(name, names):
    value = top_key(name)
    if value in names:
        return True
    # Uncertain near-matches are protected as potential tops.
    from difflib import SequenceMatcher
    return any(SequenceMatcher(None, value, x).ratio() >= .84 for x in names)


def allocate(items, target, top_names):
    """Reduce evenly, weighting proportional loss twice for a top. Preserve 1 and 2."""
    result = [dict(x) for x in items]
    originals = [D(x['quantity']) for x in items]
    floors, steps, weights = [], [], []
    for item, original in zip(items, originals):
        step = D(item['packing'])
        if step <= 0 or original % step or original != original.to_integral_value() or step != step.to_integral_value():
            raise Stop('Unsupported fractional or packing schema')
        top = is_top(item['name'], top_names)
        keep = D(2)
        minimum = min(original, (keep / step).to_integral_value(rounding=ROUND_CEILING) * step)
        if item.get('restricted'):
            minimum = max(minimum, D(item['minimum']))
        floors.append(minimum)
        steps.append(step)
        weights.append(D(2) if top else D(1))
    for _ in range(100000):
        current = total(result)
        choices = []
        for i, item in enumerate(result):
            q = D(item['quantity'])
            after = current - steps[i] * D(item['price'])
            if q - steps[i] >= floors[i] and abs(after - target) < abs(current - target):
                loss = (originals[i] - q + steps[i]) / max(originals[i], D(1)) * weights[i]
                choices.append((loss, abs(after - target), i))
        if not choices:
            return result
        i = min(choices)[2]
        result[i]['quantity'] = str(D(result[i]['quantity']) - steps[i])
    raise Stop('Allocation did not converge')


def a1(row, col):
    name = ''
    col += 1
    while col:
        col, digit = divmod(col - 1, 26)
        name = chr(65 + digit) + name
    return name + str(row + 1)


class Sheet:
    def __init__(self, browser, ident, gid):
        self.ident, self.gid = ident, gid
        self.page = browser.new_page()
        self.page.goto(f'https://docs.google.com/spreadsheets/d/{ident}/edit?gid={gid}',
                       wait_until='domcontentloaded', timeout=60000)
        self.page.locator('#t-name-box').wait_for(timeout=45000)
        self.refresh()

    def refresh(self):
        self.headers, self.rows = ro.spreadsheet(self.ident, self.gid)

    def select(self, cell):
        box = self.page.locator('#t-name-box')
        box.fill(cell)
        box.press('Enter')
        self.page.wait_for_timeout(250)
        if box.input_value().upper() != cell.upper():
            raise Stop('Sheet selection mismatch')

    def formula(self, cell):
        self.select(cell)
        text = self.page.locator('#t-formula-bar-input').inner_text().strip()
        self.page.wait_for_timeout(200)
        if text != self.page.locator('#t-formula-bar-input').inner_text().strip():
            raise Stop('Sheet selection not stable')
        return text

    def locate(self, supplier, arrival):
        col = ro.supplier_column(supplier, self.headers)
        rows = [i for i, r in enumerate(self.rows) if len(r) > 1 and r[1] == arrival]
        if col is None:
            raise Stop('Sheet supplier column missing or ambiguous')
        if not rows:
            raise Stop('Sheet arrival date missing')
        if len(rows) != 1:
            raise Stop('Sheet arrival date duplicated')
        row = rows[0]
        header_rows = [i for i, r in enumerate(self.rows) if r == self.headers]
        if len(header_rows) != 1:
            raise Stop('Sheet header ambiguous')
        if self.formula(a1(header_rows[0], col)) != self.headers[col].strip():
            raise Stop('Sheet header changed')
        if self.formula(a1(row, 1)) != arrival:
            raise Stop('Sheet date changed')
        return row, col

    def write(self, group):
        self.refresh()
        row, col = self.locate(group['supplier'], group['arrival'])
        if a1(row, col) != group['cell'] or self.formula(group['cell']) != group['formula']:
            raise Stop('Concurrent sheet change; refusing overwrite')
        self.select(group['cell'])
        self.page.keyboard.type(group['rounded'])
        self.page.keyboard.press('Enter')
        for _ in range(6):
            self.refresh()
            try:
                actual = ro.number(self.rows[row][col])
            except Exception:
                actual = None
            if actual == D(group['rounded']) and self.formula(group['cell']) == group['rounded']:
                return
            self.page.wait_for_timeout(1000)
        raise Stop('Sheet write not verified; recover by re-reading SU')


def form_items(page):
    items = page.locator('tr.nomenclature-row').evaluate_all('''rows => rows.map(row => {
      const q = row.querySelector('input.quantity');
      const value = suffix => row.querySelector('input[name$="[' + suffix + ']"]')?.value;
      return {id:value('id_supply'), name:row.querySelector('td a')?.textContent.trim(),
        quantity:q?.value, submittedQuantity:value('quantity'), price:value('price'), packing:q?.dataset.packing,
        restricted:q?.dataset.hasEditRestrictions === '1', minimum:q?.dataset.min_value,
        fraction:q?.dataset.allow_fraction};
    })''')
    if any(D(x['quantity']) != D(x['submittedQuantity']) for x in items):
        raise Stop('Visible and submitted quantities differ')
    return items


def fill_quantities(page, desired):
    items = form_items(page)
    wanted = {x['id']: x for x in desired}
    if set(wanted) != {x['id'] for x in items}:
        raise Stop('Form assortment changed')
    rows = page.locator('tr.nomenclature-row')
    for index, item in enumerate(items):
        q = wanted[item['id']]['quantity']
        if D(q) != D(item['quantity']):
            field = rows.nth(index).locator('input.quantity')
            field.fill(q + '0' if '.' in q else q + '.0')
            field.press('End')
            field.press('Backspace')
            field.press('Tab')
    if fingerprint(form_items(page)) != fingerprint(desired):
        raise Stop('Browser quantity events did not apply')
    if ro.number(page.locator('.total_price').inner_text()) != total(desired):
        raise Stop('Browser displayed total differs from proposed total')


def sync_cookies(client, context):
    jar = next(h.cookiejar for h in client.opener.handlers if hasattr(h, 'cookiejar'))
    context.add_cookies([{'name': c.name, 'value': c.value, 'domain': c.domain,
                         'path': c.path, 'secure': c.secure} for c in jar])


def open_form(context, client, entry, title):
    sync_cookies(client, context)
    page = context.new_page()
    page.goto(ro.SU + '/turnover/supplyRequest/updateSuppliers/' + entry['id'],
              wait_until='domcontentloaded', timeout=45000)
    page.locator('#update_request-form').wait_for(timeout=20000)
    rows = page.locator('table.detail-view tr').evaluate_all(
        'rows => rows.map(r => [...r.querySelectorAll("th,td")].map(c => c.textContent.trim()))')
    fields = dict(r for r in rows if len(r) == 2)
    if fields.get('Заявка') != entry['id'] + ' от ' + title:
        raise Stop('Edit form department/ID mismatch')
    if fields.get('Дата привоза в магазин') != entry['arrival']:
        raise Stop('Edit form arrival mismatch')
    supplier = page.locator('#update_request-form .supplier table tr th').first.inner_text().strip()
    if ro.normalize(supplier) != ro.normalize(entry['supplier']):
        raise Stop('Edit form supplier mismatch')
    items = form_items(page)
    if not items or any(not x.get('id') or not x.get('name') for x in items):
        raise Stop('Edit form schema unsupported')
    return page, items, ro.number(fields['Минимальная сумма заявки'])


def journal_history(directory):
    entries = {}
    for path in Path(directory).rglob('journal.json'):
        doc = json.loads(path.read_text())
        if doc.get('schema') != 1:
            raise Stop('Unknown journal schema')
        for key, val in doc['entries'].items():
            entries.setdefault(key, []).append(val)
    return entries


def pending_slot(status):
    match = re.search(r'Ожидает отправки\s*\((\d{2}:\d{2})\)', status)
    return match.group(1) if match else None


def budget_forecasts(name, headers, rows, groups):
    results = []
    if name in ('М42', 'Г34'):
        top_end = next((i for i, h in enumerate(headers) if 'готовая' in h.casefold()), None)
        if top_end is None:
            raise Stop('Daily supplier block missing')
        for arrival in {g['arrival'] for g in groups}:
            row = next(r for r in rows if len(r) > 1 and r[1] == arrival)
            forecast = D(0)
            for col in range(2, top_end):
                gs = [g for g in groups if g['arrival'] == arrival and g['cell'] == a1(rows.index(row), col)]
                value = gs[0]['expected'] if gs else (row[col] if col < len(row) else '')
                if value.strip() not in ('', '-', '–', '—'):
                    forecast += ro.number(value)
            weekday = dt.datetime.strptime(arrival, '%d.%m.%Y').weekday()
            label = 'Лимит, воскресенье' if weekday == 6 else 'Лимит, суббота' if weekday == 5 else ('Лимит, пн–сб' if name == 'Г34' else 'Лимит, будни')
            limits = [ro.number(r[2]) for r in rows if r and r[0].startswith(label)]
            if len(limits) != 1:
                raise Stop('Daily limit ambiguous')
            # Regulation §6 permits 71–72k for M42's 70k soft target.
            # G34's 60k ceiling remains strict.
            limit = min(limits[0], D(60000)) if name == 'Г34' else limits[0] + D(2000)
            if name == 'Г34' and len(row) > top_end + 1 and headers[top_end + 1].casefold() == 'план' and row[top_end + 1].strip():
                limit = min(limit, ro.number(row[top_end + 1]))
            results.append((arrival, forecast, limit))
    return results


def check_budgets(name, headers, rows, groups):
    if any(forecast > limit for _, forecast, limit in budget_forecasts(name, headers, rows, groups)):
        raise Stop('Daily forecast exceeds permitted limit')


def balance_budgets(name, headers, rows, groups):
    """Use at most 5% supplier-plan slack to satisfy the hard daily ceiling.

    Sent cards and below-plan originals stay fixed. Legal minima, packing,
    singles/twos and top priority apply to every additional reduction.
    """
    top_names = tops(name) if name in ('М42', 'Г34') else []
    for arrival, forecast, limit in budget_forecasts(name, headers, rows, groups):
        end = next(i for i, h in enumerate(headers) if 'готовая' in h.casefold())
        food_cells = {a1(ri, ci) for ri, row in enumerate(rows)
                      if len(row) > 1 and row[1] == arrival for ci in range(2, end)}
        for _ in range(100000):
            if forecast <= limit:
                break
            choices = []
            for gi, group in enumerate(groups):
                if group['arrival'] != arrival or group['plan'] is None:
                    continue
                # Dessert suppliers are outside this food/Japan budget.
                if group['cell'] not in food_cells:
                    continue
                for ei, entry in enumerate(group['cards']):
                    desired = entry.get('desired')
                    if not desired:
                        continue
                    for ii, (item, original) in enumerate(zip(desired, entry['items'])):
                        quantity, step = D(item['quantity']), D(item['packing'])
                        floor = min(D(original['quantity']),
                                    (D(2) / step).to_integral_value(rounding=ROUND_CEILING) * step)
                        if item.get('restricted'):
                            floor = max(floor, D(item['minimum']))
                        reduction = step * D(item['price'])
                        if quantity - step < floor:
                            continue
                        if D(group['expected']) - reduction < D(group['plan']) * D('.95'):
                            continue
                        minimum = max(D(entry['minimum']),
                            D(800) if re.search('кумкват|фирсова', group['supplier'], re.I) else D(0))
                        if total(desired) - reduction < minimum:
                            continue
                        loss = (D(original['quantity']) - quantity + step) / D(original['quantity'])
                        loss *= D(2) if is_top(item['name'], top_names) else D(1)
                        choices.append((loss, abs(forecast - reduction - limit), gi, ei, ii, reduction))
            if not choices:
                raise Stop('Daily budget infeasible within protected quantities and supplier tolerance')
            _, _, gi, ei, ii, reduction = min(choices)
            group = groups[gi]
            item = group['cards'][ei]['desired'][ii]
            item['quantity'] = str(D(item['quantity']) - D(item['packing']))
            group['expected'] = str(D(group['expected']) - reduction)
            group['rounded'] = str(D(group['expected']).quantize(D('1'), rounding=ro.ROUND_HALF_UP))
            forecast -= reduction
        else:
            raise Stop('Daily budget allocation did not converge')
    check_budgets(name, headers, rows, groups)


def check_deadline(now=None):
    now = now or dt.datetime.now(ZoneInfo('Asia/Yekaterinburg'))
    expected_date = os.getenv('LIFEMART_EXPECTED_DATE', '')
    if expected_date and now.date().isoformat() != expected_date:
        raise Stop('Trigger belongs to another date; reconcile before retrying')
    start = os.getenv('LIFEMART_WINDOW_START', '')
    if start and now.time().replace(tzinfo=None) < dt.datetime.strptime(start, '%H:%M').time():
        raise Stop('Scheduled window is not due yet')
    deadline = os.getenv('LIFEMART_DEADLINE', '')
    if deadline:
        cutoff = dt.datetime.strptime(deadline, '%H:%M').time()
        if now.time().replace(tzinfo=None) >= cutoff:
            raise Stop('Scheduled window is late; reconcile sent requests before retrying')


def selected_for_pass(entry, slots, include_sent=False):
    slot = pending_slot(entry['status'])
    if slot is not None:
        return slot in slots
    return include_sent and (entry['status'].strip() == 'На рассмотрении' or
                             bool(re.match(r'^(?:Отправлен|Подтвержд)', entry['status'], re.I)))


def editable_now(entry, now=None):
    now = now or dt.datetime.now(ZoneInfo('Asia/Yekaterinburg'))
    slot = pending_slot(entry['status'])
    return slot is not None and now.strftime('%H:%M') < slot


def prepare(client, browser, context, args):
    check_deadline()
    date = dt.datetime.now(ZoneInfo('Asia/Yekaterinburg')).date()
    history = journal_history(args.history)
    plan = {'schema': 1, 'date': date.isoformat(), 'created': time.time(), 'branches': []}
    journal = {'schema': 1, 'entries': {}}
    slots = set(args.slots.split(','))
    if not slots or any(not re.fullmatch(r'\d{2}:\d{2}', x) for x in slots):
        raise Stop('Invalid slots')
    for branch in ro.BRANCHES:
        name, dep, ident, gid = branch
        info = json.loads(client.request(f'/dashboad/api/header?departmentId={dep}'))
        if not info.get('success') or info.get('department_info', {}).get('id') != dep:
            raise Stop('Department access failed')
        title = info['department_info']['title']
        print(f'{name}: opening spreadsheet for read-only preflight', flush=True)
        sheet = Sheet(browser, ident, gid)
        candidates = report(client, dep, title, date.strftime('%d.%m.%Y'))
        selected = []
        skipped = 0
        for entry in candidates:
            if not selected_for_pass(entry, slots, os.getenv('LIFEMART_INCLUDE_SENT') == 'true'):
                continue
            if ro.supplier_column(entry['supplier'], sheet.headers) is None:
                skipped += 1
                continue
            doc = card(client, entry['id'], title, entry['supplier'])
            entry.update(arrival=doc.field('Дата привоза заявки'), items=doc.items)
            selected.append(entry)
        groups = {}
        selected_ids = {x['id'] for x in selected if editable_now(x)}
        for entry in selected:
            key = (ro.supplier_column(entry['supplier'], sheet.headers), entry['arrival'])
            groups.setdefault(key, []).append(entry)
        output = {'name': name, 'department': dep, 'title': title, 'ident': ident,
                  'gid': gid, 'groups': [], 'skipped': skipped}
        for (supplier_col, arrival), picked in groups.items():
            supplier = picked[0]['supplier']
            complete = report(client, dep, title, arrival, arrival=True)
            related = [x for x in complete if ro.supplier_column(x['supplier'], sheet.headers) == supplier_col]
            if not {x['id'] for x in picked} <= {x['id'] for x in related}:
                raise Stop('Arrival report incomplete')
            cards = []
            for entry in related:
                doc = card(client, entry['id'], title, entry['supplier'], arrival)
                entry.update(items=doc.items, arrival=arrival, before=fingerprint(doc.items))
                cards.append(entry)
            current = sum((total(x['items']) for x in cards), D(0))
            row, col = sheet.locate(supplier, arrival)
            cell = a1(row, col)
            formula = sheet.formula(cell)
            value = sheet.rows[row][col] if col < len(sheet.rows[row]) else ''
            group = {'supplier': supplier, 'arrival': arrival, 'cell': cell, 'formula': formula,
                     'cards': cards, 'before': str(current), 'plan': None}
            recovered = set()
            for entry in cards:
                prior = history.get(digest(entry['id']), [])
                if prior and not any(entry['before'] in (h['before'], h['after']) for h in prior):
                    raise Stop('Pending journal differs from current card')
                if any(entry['before'] == h['after'] for h in prior):
                    recovered.add(entry['id'])
            if name == 'АВ12' or not any(x['id'] in selected_ids for x in cards):
                target = current
                group['decision'] = 'transfer'
            elif formula.startswith('='):
                target = ro.number(value)
                if target <= 0:
                    raise Stop('Nonpositive supplier plan')
                group.update(plan=str(target), decision='plan')
            elif formula and ro.number(formula) == current.quantize(D('1'), rounding=ro.ROUND_HALF_UP):
                target = current
                group['decision'] = 'already-fact'
            else:
                raise Stop('No unambiguous formula plan or confirmed fact')
            editable = []
            fixed = D(0)
            for entry in cards:
                entry['after'] = entry['before']
                if entry['id'] not in selected_ids or name == 'АВ12' or entry['id'] in recovered or target >= current:
                    fixed += total(entry['items'])
                    continue
                page, items, minimum = open_form(context, client, entry, title)
                if fingerprint(items) != entry['before']:
                    raise Stop('Edit form differs from card')
                entry.update(items=items, minimum=str(minimum))
                editable.append(entry)
                page.close()
            combined = [dict(i, request=e['id']) for e in editable for i in e['items']]
            if combined:
                result = allocate(combined, max(D(0), target - fixed), tops(name))
                for entry in editable:
                    desired = [i for i in result if i['request'] == entry['id']]
                    if total(desired) < max(D(entry['minimum']),
                        D(800) if re.search('кумкват|фирсова', supplier, re.I) else D(0)):
                        raise Stop('Supplier minimum would be violated')
                    entry['desired'] = desired
                    entry['after'] = fingerprint(desired)
            expected = sum((total(e.get('desired', e['items'])) for e in cards), D(0))
            group.update(expected=str(expected), rounded=str(expected.quantize(D('1'), rounding=ro.ROUND_HALF_UP)))
            output['groups'].append(group)
        balance_budgets(name, sheet.headers, sheet.rows, output['groups'])
        for group in output['groups']:
            for entry in group['cards']:
                entry['after'] = fingerprint(entry.get('desired', entry['items']))
                if entry['after'] != entry['before']:
                    preview, fresh, _ = open_form(context, client, entry, title)
                    if fingerprint(fresh) != entry['before']:
                        raise Stop('Form changed during preflight')
                    fill_quantities(preview, entry['desired'])
                    preview.close()
                    journal['entries'][digest(entry['id'])] = {'before': entry['before'], 'after': entry['after']}
        sheet.page.close()
        plan['branches'].append(output)
        changes = sum(e['before'] != e['after'] for g in output['groups'] for e in g['cards'])
        print(f'{name}: prepared groups={len(output["groups"])}; proposed_changes={changes}; skipped={skipped}', flush=True)
    if not any(b['groups'] for b in plan['branches']):
        check_deadline()
        if os.getenv('LIFEMART_ALLOW_EMPTY') != 'true':
            raise Stop('No in-scope cards; pass incomplete')
        print('NOOP: scheduled window has no eligible pending requests.', flush=True)
    for path, value in [(args.plan, plan), (args.journal, journal)]:
        Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2))
        Path(path).chmod(0o600)
    print('PASS: complete read-only preflight; no changes applied.', flush=True)


def apply(client, browser, context, args):
    check_deadline()
    if os.getenv('LIFEMART_JOURNAL_PERSISTED') != 'true':
        raise Stop('Pending-save journal has not been persisted')
    plan = json.loads(Path(args.plan).read_text())
    if plan.get('schema') != 1 or time.time() - plan['created'] > 600:
        raise Stop('Plan expired; prepare again')
    if plan['date'] != dt.datetime.now(ZoneInfo('Asia/Yekaterinburg')).date().isoformat():
        raise Stop('Plan date expired')
    for branch in plan['branches']:
        title = branch['title']
        client.request(f'/dashboad/api/header?departmentId={branch["department"]}')
        sheet = Sheet(browser, branch['ident'], branch['gid'])
        changed = written = 0
        for group in branch['groups']:
            check_deadline()
            sheet.refresh()
            row, col = sheet.locate(group['supplier'], group['arrival'])
            if a1(row, col) != group['cell'] or sheet.formula(group['cell']) != group['formula']:
                raise Stop('Sheet changed since preflight')
            if group['plan'] is not None and ro.number(sheet.rows[row][col]) != D(group['plan']):
                raise Stop('Calculated supplier plan changed since preflight')
            check_budgets(branch['name'], sheet.headers, sheet.rows, branch['groups'])
            related = report(client, branch['department'], title, group['arrival'], arrival=True)
            ids = {x['id'] for x in related if ro.supplier_column(x['supplier'], sheet.headers) == col}
            if ids != {x['id'] for x in group['cards']}:
                raise Stop('Request group changed since preflight')
            # Read every card before the first write for this supplier.
            for entry in group['cards']:
                doc = card(client, entry['id'], title, entry['supplier'], group['arrival'])
                if fingerprint(doc.items) != entry['before']:
                    raise Stop('Card changed since preflight')
                if entry['before'] != entry['after'] and pending_slot(doc.field('Статус')) != pending_slot(entry['status']):
                    raise Stop('Request status changed')
            for entry in group['cards']:
                check_deadline()
                if entry['before'] == entry['after']:
                    continue
                if branch['name'] == 'АВ12':
                    raise Stop('AV12 is strictly transfer-only')
                if not editable_now(entry):
                    raise Stop('Request sending slot has passed; reconcile before retrying')
                page, items, minimum = open_form(context, client, entry, title)
                if fingerprint(items) != entry['before']:
                    raise Stop('Form changed since preflight')
                fill_quantities(page, entry['desired'])
                buttons = page.locator('input[type=submit][value="Сохранить"]:visible')
                if buttons.count() != 1:
                    raise Stop('Save button ambiguous')
                check_deadline()
                if not editable_now(entry):
                    raise Stop('Request sending slot has passed before save')
                buttons.click()
                page.wait_for_load_state('domcontentloaded')
                saved = card(client, entry['id'], title, entry['supplier'], group['arrival'])
                if fingerprint(saved.items) != entry['after']:
                    if fingerprint(saved.items) != entry['before']:
                        raise Stop('Unexpected partial save; do not retry blindly')
                    buttons = page.locator('input[type=submit][value="Сохранить"]:visible')
                    if buttons.count() != 1 or 'updateSuppliers/' + entry['id'] not in page.url:
                        raise Stop('Confirmation missing; no verified save')
                    check_deadline()
                    if not editable_now(entry):
                        raise Stop('Request sending slot has passed before confirmation')
                    buttons.click()
                    page.wait_for_load_state('domcontentloaded')
                    saved = card(client, entry['id'], title, entry['supplier'], group['arrival'])
                if fingerprint(saved.items) != entry['after']:
                    raise Stop('Saved card not verified')
                changed += 1
                page.close()
            actual = D(0)
            for entry in group['cards']:
                doc = card(client, entry['id'], title, entry['supplier'], group['arrival'])
                if fingerprint(doc.items) != entry['after']:
                    raise Stop('Final card verification failed')
                actual += total(doc.items)
            if actual != D(group['expected']):
                raise Stop('Final aggregate mismatch')
            sheet.write(group)
            written += 1
            print(f'{branch["name"]}: saved={changed}; verified_cells={written}', flush=True)
        sheet.page.close()
    print('PASS: production pass saved and verified.', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare', 'apply'])
    parser.add_argument('--slots', default='15:00,16:00')
    parser.add_argument('--plan', default='/tmp/lifemart-plan.json')
    parser.add_argument('--journal', default='/tmp/lifemart-journal/journal.json')
    parser.add_argument('--history', default='/tmp/lifemart-history')
    args = parser.parse_args()
    if os.getenv('GITHUB_ACTIONS') != 'true':
        raise Stop('Production runner is available only in GitHub Actions')
    from playwright.sync_api import sync_playwright
    client = ro.ReadOnlySU()
    client.login()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        if args.mode == 'prepare':
            Path(args.journal).parent.mkdir(parents=True, exist_ok=True)
            prepare(client, browser, context, args)
        else:
            apply(client, browser, context, args)
        browser.close()


if __name__ == '__main__':
    try:
        main()
    except Stop as exc:
        print('FAIL: ' + str(exc) + '; stopped before further changes.', file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Remote error messages can contain business data and credentials.
        print('FAIL: ' + type(exc).__name__ + '; stopped before further changes.', file=sys.stderr)
        # Frame locations only: exception messages/locals may contain credentials or payloads.
        for frame in traceback.extract_tb(exc.__traceback__):
            if Path(frame.filename).parent.name == 'scripts':
                print(f'TRACE: {Path(frame.filename).name}:{frame.lineno} in {frame.name}', file=sys.stderr)
        sys.exit(1)
