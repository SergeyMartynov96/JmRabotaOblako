import unittest,datetime as dt
from unittest.mock import Mock,patch
from daily_metrics.io import SUReports,SU
from daily_metrics.core import StructureError

class SharedSUSession(unittest.TestCase):
    def context(self):
        context=Mock();context.cookies.return_value=[{'name':'PHPSESSID','value':'unit-session','domain':'su.lifemart.ru','path':'/','secure':True,'expires':-1}]
        return context
    def test_verified_browser_session_recovers_cached_http_login_failure(self):
        client=SUReports();client._login_error=TimeoutError('login transport')
        context=self.context();client.adopt_browser_session(context,verified=True)
        self.assertTrue(client._authenticated);self.assertIsNone(client._login_error)
        context.cookies.assert_called_once_with(SU)
        handler=next(h for h in client.opener.handlers if hasattr(h,'cookiejar'))
        cookies=list(handler.cookiejar);self.assertEqual(len(cookies),1)
        self.assertEqual((cookies[0].domain,cookies[0].path,cookies[0].secure),('su.lifemart.ru','/',True))
        with patch.object(client,'login',side_effect=AssertionError('must reuse verified session')),patch.object(client,'request',return_value='report'):
            self.assertEqual(client.revenue(Mock(),dt.date(2026,11,25)),'report')
    def test_missing_or_unverified_session_is_not_adopted(self):
        client=SUReports();context=self.context()
        with self.assertRaises(StructureError):client.adopt_browser_session(context)
        context.cookies.assert_not_called();self.assertFalse(client._authenticated)
        context.cookies.return_value=[]
        with self.assertRaises(StructureError):client.adopt_browser_session(context,verified=True)
        self.assertFalse(client._authenticated)
