#!/usr/bin/env python3
"""Read-only LifeMart check. No application writes and no financial data in CI logs."""
import argparse
import csv
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP
from html import unescape
from html.parser import HTMLParser
import http.cookiejar
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SU = "https://su.lifemart.ru"
BRANCHES = (
    ("АВ12", 115, "10oYGVjhn5AEEz62CTDbYWRtfLRPk715oh9jT6CPoH5Y", "672003479"),
    ("М42", 153, "1bjWG6XBZRmOTd-UF2v7MlXKN-cXPJNAJ9rWzqu6gQQw", "1613797398"),
    ("Г34", 357, "1Ju0nexwNF3KQdYUBjZ2OSzNBQbtZIl52KJFMv6VbB-g", "708404997"),
)


class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text, self.rows, self.links, self.prices = [], [], [], []
        self.row = None
        self.cell = None
        self.cell_class = ""
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "tr":
            self.row = []
        if tag in ("td", "th"):
            self.cell = []
            self.cell_class = attrs.get("class", "")

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.text.append(data.strip())
            if self.cell is not None:
                self.cell.append(data.strip())

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if tag in ("td", "th") and self.cell is not None:
            value = " ".join(self.cell)
            if self.row is not None:
                self.row.append(value)
            if "supply-total-price" in self.cell_class.split():
                self.prices.append(number(value))
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def number(value):
    return Decimal(re.sub(r"\s", "", value).replace(",", "."))


def normalize(value):
    value = value.casefold().replace("ё", "е")
    value = re.sub(r"\b(?:ип|ооо|индивидуальный|предприниматель)\b", " ", value)
    return re.sub(r"[^а-яa-z0-9]+", " ", value).strip()



def supplier_identity(value):
    # Report counterparty labels can add an RC channel marker which is absent
    # from the same request's supplier field. Do not approximate other names.
    return normalize(re.sub(r'\s*\(\s*РЦ\s*\)\s*$', '', value, flags=re.I))


def supplier_column(supplier, headers):
    distribution_center = bool(re.search(r"\(\s*РЦ\s*\)", supplier, re.I))
    supplier = normalize(supplier)
    # A whole-word prefix permits an abbreviated surname header, but rejects ambiguity.
    matches = []
    for index, header in enumerate(headers):
        name = normalize(header)
        if len(name) < 4 or name in {"дата", "план", "остаток", "день недели"}:
            continue
        if supplier == name or supplier.startswith(name + " ") or name.startswith(supplier + " "):
            matches.append(index)
    if matches:
        return matches[0] if len(matches) == 1 else None
    # Personal suppliers are often abbreviated to a surname in the sheet.
    # Permit a one-character typo/inflection, but never choose between rivals.
    def distance(a, b):
        previous = list(range(len(b) + 1))
        for i, x in enumerate(a, 1):
            current = [i]
            for j, y in enumerate(b, 1):
                current.append(min(current[-1] + 1, previous[j] + 1,
                                   previous[j - 1] + (x != y)))
            previous = current
        return previous[-1]
    def surname_key(value):
        return re.sub(r"(ов|ев|ин)а$", r"\1", value)
    # A distribution-centre counterparty must not become a different personal
    # supplier through a one-letter surname approximation (Мухачев / Мурачев).
    if distribution_center:
        return None
    surname = surname_key(supplier.split()[0]) if supplier else ""
    candidates = []
    for index, header in enumerate(headers):
        words = normalize(header).split()
        if not words or len(surname) < 6 or len(words[0]) < 6:
            continue
        # A close surname is insufficient when the sheet gives a full name.
        given = supplier.split()[1:]
        if any(a != b and not (len(a) == 1 and b.startswith(a))
               and not (len(b) == 1 and a.startswith(b))
               for a, b in zip(given, words[1:])):
            continue
        if distance(surname, surname_key(words[0])) <= 1:
            candidates.append(index)
    return candidates[0] if len(candidates) == 1 else None


def credentials():
    username, password = os.getenv("LIFEMART_LOGIN"), os.getenv("LIFEMART_PASSWORD")
    if not (username and password):
        # Existing credentials file explicitly selected by the repository owner.
        text = (ROOT / "AGENTS.md").read_text()
        match = re.search(r"Логин\s*[:=]?\s*(\d{10,11})\s+Пароль\s*[:=]?\s*(\S+)", text, re.I)
        if not match:
            raise RuntimeError("Credentials unavailable")
        username, password = match.groups()
        password = password.strip("`\"'")
    if os.getenv("GITHUB_ACTIONS") == "true":
        for value in (username, password):
            value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::add-mask::" + value, flush=True)
    return username, password


class ReadOnlySU:
    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, data=None):
        allowed_posts = {"/site/login", "/turnover/supplyRequest/report"}
        parsed = urllib.parse.urlsplit(path)
        if parsed.scheme or parsed.netloc:
            raise ValueError("Only relative SU paths are permitted")
        allowed_gets = (
            "/site/login", "/dashboad/api/header", "/turnover/supplyRequest/report")
        if data is not None and parsed.path not in allowed_posts:
            raise ValueError("Application writes are forbidden")
        if data is None and parsed.path not in allowed_gets and not re.fullmatch(
                r"/turnover/supplyRequest/view/\d+", parsed.path):
            raise ValueError("Unapproved read endpoint")
        encoded = urllib.parse.urlencode(data).encode() if data is not None else None
        attempts = 3 if data is None or parsed.path == '/turnover/supplyRequest/report' else 1
        for attempt in range(attempts):
            try:
                with self.opener.open(SU + path, encoded, timeout=35) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt + 1 == attempts:
                    raise
            except (TimeoutError, urllib.error.URLError):
                if attempt + 1 == attempts:
                    raise
            # No URLs, remote error text, credentials or request bodies in logs.
            print('Read-only SU request retry after transient network failure.', flush=True)
            time.sleep(attempt + 1)

    def login(self):
        username, password = credentials()
        self.request("/site/login")
        result = self.request("/site/login", {
            "LoginForm[username]": username, "LoginForm[password]": password, "yt0": "Войти"})
        if "LoginForm[password]" in result:
            raise RuntimeError("SU login failed")


def spreadsheet(ident, gid):
    url = f"https://docs.google.com/spreadsheets/d/{ident}/export?format=csv&gid={gid}"
    with urllib.request.urlopen(url, timeout=35) as response:
        if "csv" not in response.headers.get("Content-Type", ""):
            raise RuntimeError("Spreadsheet did not return CSV")
        rows = list(csv.reader(io.StringIO(response.read().decode("utf-8-sig"))))
    header = next((row for row in rows if len(row) > 1 and row[1] == "Дата"), None)
    if not header:
        raise RuntimeError("Spreadsheet headers missing")
    return header, rows


def report_requests(html, branch_title, slots):
    doc = Document()
    doc.feed(html)
    if any(re.search(r"(?:_page|page)=", link) for link in doc.links):
        raise RuntimeError("Report pagination requires handling; refusing partial result")
    results = []
    for row in doc.rows:
        if len(row) < 12 or not re.fullmatch(r"\d{7,12}", row[1]):
            continue
        if normalize(row[6]) != normalize(branch_title):
            raise RuntimeError("Report branch mismatch")
        match = re.search(r"Ожидает отправки\s*\((\d{2}:\d{2})\)", row[9])
        if match and match.group(1) in slots:
            results.append({"id": row[1], "supplier": row[8], "slot": match.group(1)})
    if len({item["id"] for item in results}) != len(results):
        raise RuntimeError("Duplicate request IDs")
    return results


def check_branch(client, branch, date, slots):
    name, department_id, ident, gid = branch
    headers, rows = spreadsheet(ident, gid)
    info = json.loads(client.request(f"/dashboad/api/header?departmentId={department_id}"))
    department = info.get("department_info", {})
    if not info.get("success") or department.get("id") != department_id:
        raise RuntimeError("SU department access failed")
    date_text = date.strftime("%d.%m.%Y")
    html = client.request("/turnover/supplyRequest/report", {
        "SupplyRequest[department_id][]": department_id,
        "SupplyRequest[date_created_from]": date_text,
        "SupplyRequest[date_created_to]": date_text,
        "SupplyRequest[actual_applications]": "1", "yt11": "Сформировать"})
    requests = report_requests(html, department["title"], slots)
    result = {"branch": name, "candidates": len(requests), "skipped": 0, "checked": []}
    for item in requests:
        column = supplier_column(item["supplier"], headers)
        if column is None:
            result["skipped"] += 1
            continue
        card = Document()
        card.feed(client.request("/turnover/supplyRequest/view/" + item["id"]))
        def field(label):
            return card.text[card.text.index(label) + 1]
        if normalize(field("Подразделение")) != normalize(department["title"]):
            raise RuntimeError("Card branch mismatch")
        if supplier_column(field("Поставщик"), headers) != column:
            raise RuntimeError("Card supplier mismatch")
        if not card.prices:
            raise RuntimeError("Card positions missing")
        arrival = field("Дата привоза заявки")
        matching_rows = [row for row in rows if len(row) > 1 and row[1] == arrival]
        if len(matching_rows) != 1:
            raise RuntimeError("Arrival row missing or ambiguous")
        row = matching_rows[0]
        total = sum(card.prices, Decimal(0))
        item.update(arrival=arrival, exact_total=str(total),
                    rounded_total=str(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
                    sheet_value=row[column] if column < len(row) else "",
                    decision="Read-only comparison; no edits. CSV does not expose plan formulas.")
        result["checked"].append(item)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=dt.datetime.now(ZoneInfo("Asia/Yekaterinburg")).date().isoformat())
    parser.add_argument("--slots", default="15:00,16:00")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    date = dt.date.fromisoformat(args.date)
    client = ReadOnlySU()
    client.login()
    results = []
    for branch in BRANCHES:
        result = check_branch(client, branch, date, set(args.slots.split(",")))
        results.append(result)
        # Public CI: statuses only; no names, amounts, request IDs, or credentials.
        print(f"{branch[0]}: spreadsheet and SU OK; "
              f"checked={len(result['checked'])}; skipped={result['skipped']}", flush=True)
    checked = sum(len(result["checked"]) for result in results)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        args.report.chmod(0o600)
    if not checked:
        raise RuntimeError("No in-scope cards executed; functional test is incomplete")
    print("PASS: autonomous read-only check completed; no application or spreadsheet writes.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not print exception payloads: remote responses may contain sensitive fields.
        print(f"FAIL: {type(exc).__name__}; check access, date, schema, and available requests.", file=sys.stderr)
        sys.exit(1)
