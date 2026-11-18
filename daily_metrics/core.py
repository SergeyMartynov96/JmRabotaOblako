"""Pure parsers and target discovery, independent of purchase automation."""
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html.parser import HTMLParser
import re

DASH = '—'
MONTHS = ('январь','февраль','март','апрель','май','июнь','июль','август','сентябрь','октябрь','ноябрь','декабрь')

class StructureError(RuntimeError):
    pass


def text(value):
    return re.sub(r'\s+', ' ', str(value)).strip().casefold().replace('ё', 'е')


def number(value):
    try:
        result = Decimal(re.sub(r'[\s%₽]', '', str(value)).replace(',', '.'))
    except InvalidOperation:
        raise StructureError('Expected numeric report value') from None
    if not result.is_finite():
        raise StructureError('Non-finite report value')
    return result


def money(value):
    return str(number(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def value(rows, r, c):
    return rows[r][c].strip() if 0 <= r < len(rows) and 0 <= c < len(rows[r]) else ''


def date_matches(raw, day, allow_short=False):
    raw = str(raw).strip()
    if raw in (day.strftime('%d.%m.%Y'), day.isoformat()):
        return True
    return allow_short and raw in (day.strftime('%d.%m'), f'{day.day}.{day.month:02}')


def a1(r, c):
    col = ''
    c += 1
    while c:
        c, digit = divmod(c - 1, 26)
        col = chr(65 + digit) + col
    return col + str(r + 1)


def procurement(rows, day):
    dates = [r for r in range(len(rows)) if date_matches(value(rows,r,1),day)]
    if not dates:
        return DASH
    if len(dates) != 1:
        raise StructureError('Procurement date duplicated')
    # Explicit combined total preferred. Otherwise sum ONLY food and Japan totals.
    headers = [(r,c,text(v)) for r,row in enumerate(rows[:6]) for c,v in enumerate(row)]
    combined = [(r,c) for r,c,t in headers if ('итого' in t or 'сумма' in t) and 'готов' in t and 'япон' in t]
    if not combined:
        # М42 combined food/Japan sheet labels its combined column "Итого готовая еда".
        food = [(r,c) for r,c,t in headers if 'итого' in t and 'готов' in t]
        japan = [(r,c) for r,c,t in headers if 'итого' in t and 'япон' in t]
        if len(food) == 1 and not japan:
            combined = food
        elif len(food) == 1 and len(japan) == 1:
            vals = [value(rows,dates[0],c) for _,c in (food[0],japan[0])]
            return DASH if any(not v or v == DASH for v in vals) else money(sum(number(v) for v in vals))
    if len(combined) != 1:
        raise StructureError('Food/Japan total missing or ambiguous')
    result = value(rows, dates[0], combined[0][1])
    return DASH if not result or result == DASH else money(result)


def tops(rows, day):
    """Each of the first three product blocks ends in the supplied availability total."""
    groups = []
    start = None
    for r,row in enumerate(rows):
        label = value(rows,r,0)
        # Product names (not category labels, dates, or total labels).
        product = bool(label and label.casefold() not in ('итого:', 'итого', 'дата', 'время', 'готовая еда', 'десерты', 'япония')
                       and any(value(rows,r,c) in ('0','1') for c in range(1,len(row))))
        # Empty future blocks still have product names; identify by name, not 0/1 alone.
        if label and len(label) > 15 and label.casefold() not in ('готовая еда',):
            product = True
        if product:
            if start is None:
                start = r
        elif start is not None:
            # First row directly below products is the total; ignore later bakery blocks.
            groups.append((start,r))
            start = None
            if len(groups) == 3:
                break
    if len(groups) != 3:
        raise StructureError('Three tops blocks not found')
    results = []
    for start,end in groups:
        headers = []
        for r in range(max(0,start-5),start):
            for c in range(1,len(rows[r])):
                if date_matches(value(rows,r,c), day, allow_short=True):
                    headers.append((r,c))
        if not headers:
            # АВ12 has one shared date/time header for all three vertically stacked blocks.
            headers=[(r,c) for r in range(groups[0][0]) for c in range(1,len(rows[r]))
                     if date_matches(value(rows,r,c),day,allow_short=True)]
        if not headers:
            results.append(DASH)
            continue
        if len(headers) != 1:
            raise StructureError('Tops date missing unique two-column header')
        col = headers[0][1]+1
        entries = [value(rows,r,col) for r in range(start,end)]
        if not any(entries):
            results.append(DASH)
            continue
        if any(v not in ('','0','1') for v in entries):
            raise StructureError('Unexpected tops product value')
        total = value(rows,end,col)
        if not total:
            results.append(DASH)
            continue
        available = number(total)
        if not 0 <= available <= 100:
            raise StructureError('Tops percentage outside 0..100')
        results.append(str(Decimal(100)-available))
    return results


def target_candidates(rows,day,newest_first=False):
    candidates=[]
    for r in range(len(rows)):
        if not date_matches(value(rows,r,0),day,allow_short=True):
            continue
        for h in range(max(0,r-7),r):
            if text(value(rows,h,2))=='выручка' and text(value(rows,h,3))=='списания':
                if not all(k in text(value(rows,h,c)) for c,k in [(6,'готов'),(7,'десерт'),(8,'япони')]):
                    continue
                candidates.append((r,h))
                break
    return candidates if newest_first else list(reversed(candidates))


def lower_targets(rows,header,day):
    # Search only inside the matching weekly block, stopping before the next upper block.
    end = min(len(rows),header+55)
    for r in range(header+8,end):
        if text(value(rows,r,2))=='выручка':
            end=r-1
            break
    monday=day-dt.timedelta(days=day.weekday())
    sunday=monday+dt.timedelta(days=6)
    revenue=[]; writeoffs=[]
    for r in range(header+8,end):
        row=rows[r]
        if text(value(rows,r,1))=='план':
            for c in range(2,min(9,len(row))):
                if re.sub(r'\s','',row[c])==f'{monday:%d.%m}-{sunday:%d.%m}':
                    target=r+1+day.weekday()
                    if text(value(rows,target,0)) not in ('пн','вт','ср','чт','пт','сб','вс'):
                        raise StructureError('Weekly revenue weekday row missing')
                    revenue.append((target,c))
        for c,v in enumerate(row):
            if text(v)=='% списания':
                for rr in range(r+1,min(r+9,end)):
                    if date_matches(value(rows,rr,c-1),day,allow_short=True):
                        writeoffs.append((rr,c))
    if len(revenue)!=1 or len(writeoffs)!=1:
        raise StructureError('Weekly revenue/writeoff target missing or ambiguous')
    return revenue[0],writeoffs[0]


class Report(HTMLParser):
    """Keep tables and department headings separate to avoid mixing branch totals."""
    def __init__(self):
        super().__init__(); self.tables=[]; self.heading=''; self.heading_parts=None
        self.depth=0; self.rows=[]; self.row=None; self.cell=None; self.texts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('h1','h2','h3','h4','h5'):
            self.heading_parts=[]
        if tag=='table':
            self.depth+=1
            if self.depth==1:self.rows=[]
        if tag=='tr' and self.depth==1:self.row=[]
        if tag in ('td','th') and self.depth==1:self.cell=[]
    def handle_data(self,data):
        if data.strip():
            self.texts.append(data.strip())
            if self.heading_parts is not None:self.heading_parts.append(data.strip())
            if self.cell is not None:self.cell.append(data.strip())
    def handle_endtag(self,tag):
        if tag in ('h1','h2','h3','h4','h5') and self.heading_parts is not None:
            self.heading=' '.join(self.heading_parts);self.heading_parts=None
        if tag in ('td','th') and self.cell is not None:
            if self.row is not None:self.row.append(' '.join(self.cell))
            self.cell=None
        if tag=='tr' and self.depth==1 and self.row is not None:
            self.rows.append(self.row);self.row=None
        if tag=='table':
            if self.depth==1:self.tables.append((self.heading,self.rows))
            self.depth=max(0,self.depth-1)


def revenue_report(html,title,day=None):
    doc=Report();doc.feed(html)
    results=[]
    for heading,rows in doc.tables:
        if text(heading)!=text(title):continue
        if day is not None and not any(day.strftime('%d.%m.%Y') in ' '.join(row) for row in rows[:2]):
            raise StructureError('Revenue report date mismatch')
        if not any(row and text(row[-1])=='итоги:' for row in rows[:2]):
            raise StructureError('Revenue totals column missing')
        for row in rows:
            if row and text(row[0])=='итоговая выручка':results.append(money(row[-1]))
    if len(results)>1:raise StructureError('Duplicate revenue total')
    return results[0] if results else DASH


def writeoff_report(html,title,day):
    doc=Report();doc.feed(html)
    found=[]
    for heading,rows in doc.tables:
        headers=[(i,row) for i,row in enumerate(rows) if any(text(v)=='себестоимость' for v in row)]
        for hi,header in headers:
            cost=next(i for i,v in enumerate(header) if text(v)=='себестоимость')
            dep=next((i for i,v in enumerate(header) if text(v)=='подразделение'),None)
            if dep is None:raise StructureError('Writeoff department column missing')
            totals=[]
            for row in rows[hi+1:]:
                if len(row)>dep and row[dep].strip():
                    if text(row[dep])!=text(title):raise StructureError('Foreign writeoff department')
                    if not date_matches(row[0],day):raise StructureError('Foreign writeoff date')
                    totals.append(number(row[cost]))
            if totals:found.append(money(sum(totals)))
    if len(found)>1:raise StructureError('Duplicate writeoff tables')
    return found[0] if found else DASH
