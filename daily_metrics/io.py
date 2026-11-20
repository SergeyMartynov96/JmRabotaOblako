"""Narrow network adapters. SU report generation is read-only."""
import csv
import datetime as dt
import http.cookiejar
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from .core import StructureError, MONTHS, same_cell_value

SU='https://su.lifemart.ru'
REVENUE='/receipts/receiptsGeneral/ajax_report'
WRITEOFF='/turnover/writeOffAct/report'


def get(url,timeout=45):
    with urllib.request.urlopen(url,timeout=timeout) as response:
        return response.read().decode('utf-8-sig')


def native_cell(book,gid,cell):
    """Read one unformatted public Sheets cell, preserving the date's full year."""
    params={'gid':str(gid),'range':cell,'headers':'0','tqx':'out:json','cachebust':str(time.time_ns())}
    raw=get(f'https://docs.google.com/spreadsheets/d/{book}/gviz/tq?'+urllib.parse.urlencode(params),timeout=20)
    try:
        result=json.loads(raw[raw.index('{'):raw.rindex('}')+1])
        if result.get('status')!='ok':raise ValueError()
        table=result['table'];cols=table['cols'];rows=table['rows']
        if len(cols)!=1 or len(rows)>1:raise ValueError()
        data=(rows[0].get('c') or [None])[0] if rows else None
        return cols[0]['type'],data.get('v') if isinstance(data,dict) else None
    except (ValueError,KeyError,IndexError,TypeError):
        raise StructureError('Sheets native cell unavailable ('+cell+')') from None


def csv_sheet(book,gid):
    raw=get(f'https://docs.google.com/spreadsheets/d/{book}/export?format=csv&gid={gid}&cachebust={time.time_ns()}')
    if raw.lstrip().startswith('<'):
        raise StructureError('Sheet CSV unavailable')
    return list(csv.reader(io.StringIO(raw)))


def tops_sheet(book,day):
    html=get(f'https://docs.google.com/spreadsheets/d/{book}/htmlview')
    sheets=[];recognized=0
    for match in re.finditer(r'items\.push\(\{name: ("(?:\\.|[^"\\])*"),.*?gid: "(-?\d+)"',html):
        recognized+=1
        title=json.loads(match[1]);gid=match[2]
        if MONTHS[day.month-1] in title.casefold() and str(day.year) in title:
            sheets.append((2,0,gid))
        m=re.fullmatch(r'с\s*(\d{1,2})/(\d{1,2})',title)
        if m:
            import datetime as dt
            start=dt.date(day.year,int(m[2]),int(m[1]))
            if start<=day:sheets.append((1,start.toordinal(),gid))
    if not sheets:
        if recognized:return None  # Valid workbook, but period not prepared by store.
        raise StructureError('Tops workbook structure unavailable')
    sheets.sort(reverse=True)
    if len(sheets)>1 and sheets[0][:2]==sheets[1][:2]:
        raise StructureError('Current tops tab ambiguous')
    return csv_sheet(book,sheets[0][2])


class SUReports:
    def __init__(self):
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.revenue_cache={}
        self._authenticated=False
        self._login_error=None
    def request(self,path,data=None):
        if urllib.parse.urlsplit(path).path not in ('/site/login',REVENUE,WRITEOFF, '/handbook/biSupersetSettings/index', '/turnover/waybill/report', '/turnover/waybill/contractorAutocomplete'):
            raise ValueError('Only metrics read endpoints allowed')
        if data is not None and urllib.parse.urlsplit(path).path not in ('/site/login',REVENUE):
            raise ValueError('Only login and revenue report POST allowed')
        encoded=urllib.parse.urlencode(data,doseq=True).encode() if data is not None else None
        fast=urllib.parse.urlsplit(path).path=='/site/login'
        attempts=3 if fast else 1
        for attempt in range(attempts):
            try:
                with self.opener.open(SU+path,encoded,timeout=35 if fast else (600 if path==REVENUE else 180 if urllib.parse.urlsplit(path).path=='/handbook/biSupersetSettings/index' else 120)) as response:
                    result=response.read().decode()
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in (429,500,502,503,504) or attempt+1==attempts:raise
            except (TimeoutError,urllib.error.URLError):
                if attempt+1==attempts:raise
            print('Retrying a transient SU authentication/menu read failure',flush=True)
        if path!='/site/login' and 'LoginForm[password]' in result:
            raise StructureError('SU session expired')
        return result
    def login(self):
        username=os.getenv('LIFEMART_LOGIN');password=os.getenv('LIFEMART_PASSWORD')
        if not (username and password):
            raise StructureError('LIFEMART_LOGIN and LIFEMART_PASSWORD must be configured in GitHub Actions Secrets')
        if os.getenv('GITHUB_ACTIONS')=='true':
            for item in (username,password):
                print('::add-mask::'+item.replace('%','%25').replace('\r','%0D').replace('\n','%0A'),flush=True)
        self.request('/site/login')
        html=self.request('/site/login',{'LoginForm[username]':username,'LoginForm[password]':password,'yt0':'Войти'})
        if 'LoginForm[password]' in html:
            raise StructureError('SU login failed')
        self._authenticated=True
    def ensure_authenticated(self):
        if self._authenticated:
            return
        if self._login_error is not None:
            raise self._login_error
        try:
            self.login()
            self._authenticated=True
        except Exception as exc:
            self._login_error=exc
            raise
    def revenue(self,branch,day):
        # One report with all three selected stores, matching the owner's manual workflow.
        # A failed request is not repeated for each branch in this same run.
        if day not in self.revenue_cache:
            try:
                self.ensure_authenticated()
                self.revenue_cache[day]=self.request(REVENUE,{'department[]':['115','153','357'],
                    'brandId':'','format':'1','date_s':day.strftime('%d.%m.%Y'),
                    'date_do':day.strftime('%d.%m.%Y'),'day_of_week[]':['all'],'night':'2'})
            except Exception as exc:self.revenue_cache[day]=exc
        result=self.revenue_cache[day]
        if isinstance(result,Exception):raise result
        return result
    def writeoff(self,branch,day):
        self.ensure_authenticated()
        params={'WriteOffAct[create_date_from]':day.strftime('%d.%m.%Y'),
            'WriteOffAct[create_date_to]':day.strftime('%d.%m.%Y'),
            'WriteOffAct[search_project_id]':'2','WriteOffAct[department_id][]':[str(branch.department)],'yt6':'Сформировать'}
        return self.request(WRITEOFF+'?'+urllib.parse.urlencode(params,doseq=True))


class RevenueSheet:
    """Can edit only the configured revenue tab and cells explicitly allowed by runner."""
    def __init__(self,browser,branch,day):
        self.branch=branch;self.day=day;self.allowed=set();self.anchors={}
        self.page=browser.new_page()
        self.page.context.grant_permissions(['clipboard-read','clipboard-write'],origin='https://docs.google.com')
        self.page.goto(f'https://docs.google.com/spreadsheets/d/{branch.book}/edit?gid={branch.revenue_gid}',
                       wait_until='domcontentloaded',timeout=60000)
        self.page.locator('#t-name-box').wait_for(timeout=45000)
    def refresh(self):
        return csv_sheet(self.branch.book,self.branch.revenue_gid)
    def select(self,cell):
        box=self.page.locator('#t-name-box');box.fill(cell);box.press('Enter')
        self.page.wait_for_timeout(300)
        if box.input_value().upper()!=cell.upper():raise StructureError('Cell selection mismatch')
    def formula(self,cell):
        self.select(cell)
        bar=self.page.locator('#t-formula-bar-input')
        result=bar.inner_text().strip();self.page.wait_for_timeout(150)
        if result!=bar.inner_text().strip():raise StructureError('Unstable cell selection')
        return result
    def stable_formula(self,cell):
        # The name box can update before the formula bar, especially after recalculation.
        # Require agreement across independent selections before taking a snapshot.
        first=self.formula(cell);self.page.wait_for_timeout(250)
        second=self.formula(cell)
        if first==second:return self._confirm_selection(cell,second)
        self.page.wait_for_timeout(250)
        third=self.formula(cell)
        if second!=third:raise StructureError('Unstable revenue anchor '+cell)
        return self._confirm_selection(cell,third)

    def _confirm_selection(self,cell,formula):
        _,raw=native_cell(self.branch.book,self.branch.revenue_gid,cell)
        native_blank=raw is None or raw==''
        if formula.startswith('='):return formula
        if native_blank and not formula:return formula
        if not native_blank and formula and (same_cell_value(formula,str(raw)) or same_cell_value(str(raw),formula)):return formula
        # After a paste the formula bar can retain the previous cell's scalar.
        # A name-box value alone does not prove the grid finished selecting.
        self.page.keyboard.press('Escape')
        self.select('A1' if cell.upper()!='A1' else 'A2')
        for _ in range(5):
            actual=self.formula(cell)
            if actual.startswith('='):return actual
            if (native_blank and not actual) or (not native_blank and actual and (same_cell_value(actual,str(raw)) or same_cell_value(str(raw),actual))):return actual
            self.page.wait_for_timeout(300)
        raise StructureError('Selected cell differs from native data ('+cell+')')

    def cell_has_content(self,cell):
        if self.stable_formula(cell):return True
        # The formula bar may lag a selection. An existing native scalar (also 0)
        # must not be mistaken for a blank procurement destination.
        _,raw=native_cell(self.branch.book,self.branch.revenue_gid,cell)
        return raw is not None and raw!=''

    def anchor_value(self,cell):
        kind,value=native_cell(self.branch.book,self.branch.revenue_gid,cell)
        if kind not in ('date','datetime','string') or value is None or value=='':
            raise StructureError('Invalid date/week anchor ('+cell+')')
        return kind,value

    def is_reporting_date(self,cell,day):
        kind,value=native_cell(self.branch.book,self.branch.revenue_gid,cell)
        if kind=='string':
            from .core import date_matches
            return date_matches(value,day)
        match=re.match(r'Date\((\d{4}),(\d{1,2}),(\d{1,2})(?:,|\))',str(value)) if kind in ('date','datetime') else None
        return bool(match and dt.date(int(match[1]),int(match[2])+1,int(match[3]))==day)

    def check_anchors(self):
        for cell,expected in self.anchors.items():
            for attempt in range(3):
                try:
                    if self.anchor_value(cell)==expected:break
                except StructureError:pass
                self.page.wait_for_timeout(250)
            else:raise StructureError('Revenue date/week moved or unreadable ('+cell+')')

    def put_blank(self,cell,result):
        if cell not in self.allowed:raise StructureError('Write outside approved revenue cells')
        self.check_anchors()
        if self.stable_formula(cell):return False
        # Paste once: character-by-character typing into a percent-formatted cell can
        # append Sheets' automatic percent suffix twice (e.g. 4,8%%).
        self.select(cell)
        self.page.evaluate('(text) => navigator.clipboard.writeText(text)',result)
        self.page.keyboard.press('Control+V')
        self.page.wait_for_timeout(250)
        self.page.keyboard.press('Enter')
        for _ in range(8):
            self.page.wait_for_timeout(500)
            actual=self.formula(cell)
            if same_cell_value(actual,result):return True
        raise StructureError('Revenue write not verified')


    def replace_if_unchanged(self,cell,expected,result):
        """Only the approved defect+damage correction may replace a verified old scalar."""
        if cell not in self.allowed:raise StructureError('Write outside approved revenue cells')
        self.check_anchors()
        actual=self.stable_formula(cell)
        if actual.startswith('=') or not same_cell_value(actual,expected):return False
        self.select(cell)
        self.page.evaluate('(text) => navigator.clipboard.writeText(text)',result)
        self.page.keyboard.press('Control+V');self.page.wait_for_timeout(250);self.page.keyboard.press('Enter')
        for _ in range(8):
            self.page.wait_for_timeout(500)
            if same_cell_value(self.formula(cell),result):return True
        raise StructureError('Combined damage write not verified')
