"""Encrypted review bundles: only the reviewer's local private key can open images."""
import base64,json,os,zipfile,io
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def seal_images(directory,public_key_b64,destination):
    public=serialization.load_pem_public_key(base64.b64decode(public_key_b64,validate=True))
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(Path(directory).glob('*')):
            if path.suffix in ('.png','.json'):z.write(path,path.name)
    key=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12)
    wrapped=public.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    payload={'version':1,'key':base64.b64encode(wrapped).decode(),'nonce':base64.b64encode(nonce).decode(),
        'data':base64.b64encode(AESGCM(key).encrypt(nonce,archive.getvalue(),b'lifemart-review-v1')).decode()}
    Path(destination).write_text(json.dumps(payload))


def seal_tree(root,public_key_b64,destination):
    import shutil,tempfile
    if not public_key_b64:return
    with tempfile.TemporaryDirectory() as directory:
        flat=Path(directory)
        for path in Path(root).rglob('*'):
            if path.is_file() and path.suffix in ('.png','.json'):
                name='-'.join(path.relative_to(root).parts)
                shutil.copyfile(path,flat/name)
        seal_images(flat,public_key_b64,destination)


def worker_descendants(parent,rows):
    owned={parent};children=[]
    while True:
        found=[pid for pid,ppid in rows if ppid in owned and pid not in owned]
        if not found:break
        owned.update(found);children.extend(found)
    return list(reversed(children))


def force_abort(bi):
    import signal,subprocess
    try:
        callback=getattr(bi,'seal_before_exit',None)
        if callback:callback()
    finally:
        # Stop only this disposable worker's own browser/driver descendants.
        # Otherwise a surviving driver can keep the Actions output pipe open.
        try:
            raw=subprocess.check_output(['ps','-eo','pid=,ppid='],timeout=3).decode()
            rows=[tuple(map(int,line.split())) for line in raw.splitlines() if len(line.split())==2]
            for pid in worker_descendants(os.getpid(),rows):
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass
        finally:
            print('{"report_failed":true,"browser_worker_aborted":true}',flush=True)
            os._exit(1)


def arm_failed_cleanup(bi):
    """A stalled disposable browser must not turn a 3-minute error into 17 minutes."""
    import threading
    if getattr(bi,'cleanup_watchdog',None) is not None:return
    timer=threading.Timer(20,lambda:force_abort(bi));timer.daemon=True
    bi.cleanup_watchdog=timer;timer.start()


def bounded_bi_operation(function):
    import functools,threading
    @functools.wraps(function)
    def wrapped(bi,*args,**kwargs):
        timer=threading.Timer(200,lambda:force_abort(bi));timer.daemon=True;timer.start()
        try:return function(bi,*args,**kwargs)
        finally:timer.cancel()
    return wrapped


def auth_snapshot(root,public_key_b64):
    """Keep auth screenshots private; export only via the existing encryption path."""
    def capture(page,state):
        if not public_key_b64:return
        folder=Path(root)/'auth';folder.mkdir(exist_ok=True)
        (folder/'state.json').write_text(json.dumps(state))
        page.screenshot(path=str(folder/'page.png'),timeout=5000)
    return capture
