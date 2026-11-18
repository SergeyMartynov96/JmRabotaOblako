"""Credentials must never fall back to tracked instructions or be printed."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('readonly_secrets',Path(__file__).resolve().parents[1]/'scripts/cloud_readonly_test.py')
ro=importlib.util.module_from_spec(spec);spec.loader.exec_module(ro)
from daily_metrics.io import SUReports
from daily_metrics.core import StructureError

class SecretStorage(unittest.TestCase):
    def test_legacy_missing_secrets_never_reads_instructions(self):
        with patch.dict(os.environ,{},clear=True),patch.object(Path,'read_text',side_effect=AssertionError('Must not read a file')):
            with self.assertRaisesRegex(RuntimeError,'GitHub Actions Secrets'):ro.credentials()
    def test_metrics_missing_secrets_never_reads_instructions_or_logs_in(self):
        with patch.dict(os.environ,{},clear=True),patch.object(Path,'read_text',side_effect=AssertionError('Must not read a file')),patch.object(SUReports,'request',side_effect=AssertionError('Must not send blank credentials')):
            with self.assertRaisesRegex(StructureError,'GitHub Actions Secrets'):SUReports().login()
    def test_env_credentials_preserved_exactly(self):
        with patch.dict(os.environ,{'LIFEMART_LOGIN':'test-user','LIFEMART_PASSWORD':'test-value-with-spaces '},clear=True):
            self.assertEqual(ro.credentials(),('test-user','test-value-with-spaces '))
    def test_both_secrets_required(self):
        for env in ({'LIFEMART_LOGIN':'test-user'},{'LIFEMART_PASSWORD':'test-password'}):
            with patch.dict(os.environ,env,clear=True):
                with self.assertRaises(RuntimeError):ro.credentials()
                with self.assertRaises(StructureError):SUReports().login()

if __name__=='__main__':unittest.main()

class SecretGuard(unittest.TestCase):
    def test_guard_detects_credential_block_without_reporting_values(self):
        spec=importlib.util.spec_from_file_location('secret_guard',Path(__file__).resolve().parents[1]/'scripts/check_no_secrets.py')
        guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
        labels=guard.findings('Логин '+'1234567890'+' Пароль '+'example-value')
        self.assertEqual(labels,['SU credentials in text'])
        self.assertNotIn('example-value',str(labels))
    def test_guard_detects_known_values_without_echo(self):
        spec=importlib.util.spec_from_file_location('secret_guard',Path(__file__).resolve().parents[1]/'scripts/check_no_secrets.py')
        guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
        self.assertEqual(guard.findings('test-value',[('EXAMPLE','test-value')]),['Literal value of EXAMPLE'])
