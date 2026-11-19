"""Prepare the already-proven SU HTTP session before opening the BI browser."""
import urllib.parse
from daily_metrics.io import SU,SUReports

class ReportSUReports(SUReports):
    def request(self,path,data=None):
        if path!='/site/login':return super().request(path,data)
        encoded=urllib.parse.urlencode(data,doseq=True).encode() if data is not None else None
        # One attempt per authentication step, no sequence of hidden retries.
        with self.opener.open(SU+path,encoded,timeout=45) as response:
            return response.read().decode()
