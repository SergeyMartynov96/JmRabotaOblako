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
