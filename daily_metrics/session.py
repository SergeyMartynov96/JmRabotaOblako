"""Prepare the proven SU HTTP session within one bounded authentication window."""
import ssl,time,urllib.parse,urllib.error
from .io import SU,SUReports
from .core import StructureError

class ReportSUReports(SUReports):
    def __init__(self):
        super().__init__();self.login_deadline=None
    def request(self,path,data=None):
        if path!='/site/login':return super().request(path,data)
        if self.login_deadline is None:self.login_deadline=time.monotonic()+140
        print('SU: login stage '+('form' if data is None else 'submission'),flush=True)
        encoded=urllib.parse.urlencode(data,doseq=True).encode() if data is not None else None
        for attempt in range(3):
            remaining=self.login_deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('SU authentication exceeded its bounded window')
            try:
                with self.opener.open(SU+path,encoded,timeout=min(30,remaining)) as response:
                    chunks=[]
                    while time.monotonic()<self.login_deadline:
                        chunk=response.read1(65536)
                        if not chunk:
                            print('SU: login response complete',flush=True)
                            return b''.join(chunks).decode()
                        chunks.append(chunk)
                    raise TimeoutError('SU authentication exceeded its bounded window')
            except urllib.error.HTTPError as exc:
                if exc.code not in (429,500,502,503,504) or attempt==2:raise
            except (TimeoutError,urllib.error.URLError) as exc:
                if isinstance(getattr(exc,'reason',None),ssl.SSLCertVerificationError) or attempt==2:raise
            print('SU: retrying a transient authentication transport failure within the shared deadline',flush=True)
