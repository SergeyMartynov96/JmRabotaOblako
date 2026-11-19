#!/usr/bin/env python3
"""Scan tracked files; report locations and rule names, never matching values."""
import os
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
RULES={
    'SU credentials in text':re.compile(r'Логин\s*[:=]?\s*\d{10,11}\s+Пароль\s*[:=]?\s*\S+',re.I),
    'GitHub token':re.compile(r'(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})'),
    'Telegram bot token':re.compile(r'(?<![A-Za-z0-9_])[0-9]{6,12}:[A-Za-z0-9_-]{30,50}(?![A-Za-z0-9_-])'),
    'Private key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}


def findings(data,known_values=()):
    result=[label for label,pattern in RULES.items() if pattern.search(data)]
    for name,value in known_values:
        if value and len(value)>=6 and value in data:
            result.append('Literal value of '+name)
    return result


def main():
    files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    known=[(name,os.getenv(name,'')) for name in ('LIFEMART_LOGIN','LIFEMART_PASSWORD','CRONITOR_TELEMETRY_KEY','TELEGRAM_BOT_TOKEN')]
    failed=False
    for filename in filter(None,files):
        try:data=(ROOT/filename).read_text()
        except UnicodeDecodeError:continue
        labels=findings(data,known)
        if labels:
            failed=True
            print(filename+': '+', '.join(labels))
    if failed:raise SystemExit(1)
    print('PASS: no credential values or recognized secrets in tracked files.')

if __name__=='__main__':main()
