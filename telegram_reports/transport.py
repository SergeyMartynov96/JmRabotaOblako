"""Narrow Telegram API and authenticated encryption of the private recipient."""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

EXPECTED_USERNAME='JmReport1_bot'

class BotError(RuntimeError):
    def __init__(self,message,*,delivery_state='unknown',details=None):
        super().__init__(message);self.delivery_state=delivery_state;self.details=details


def album_rejection(payload):
    if payload.get('ok') is False and type(payload.get('error_code')) is int:
        code=payload['error_code']
        return BotError('Telegram album rejected (code '+str(code)+')',delivery_state='rejected',details=payload)
    return None

class BotClient:
    METHODS={'getMe','getWebhookInfo','getUpdates','sendMessage','sendMediaGroup'}
    def __init__(self,token):
        if not token:raise BotError('TELEGRAM_BOT_TOKEN must be configured in Actions Secrets')
        self._token=token
    def api(self,method,params=None,timeout=35):
        if method not in self.METHODS:raise BotError('Unapproved Telegram method')
        request=urllib.request.Request('https://api.telegram.org/bot'+self._token+'/'+method,
            data=json.dumps(params or {},ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=timeout) as response:payload=json.load(response)
        except urllib.error.HTTPError as exc:
            raise BotError('Telegram '+method+' HTTP '+str(exc.code)) from None
        except Exception as exc:
            raise BotError('Telegram '+method+' transport failure ('+type(exc).__name__+')') from None
        if not payload.get('ok'):raise BotError('Telegram '+method+' rejected ('+str(payload.get('error_code','unknown'))+')')
        return payload['result']
    def identity(self):
        me=self.api('getMe')
        if not me.get('is_bot') or me.get('username','').casefold()!=EXPECTED_USERNAME.casefold():
            raise BotError('The configured token belongs to a different bot')
        return me


def matching_private_chat(updates,challenge_hash,username=EXPECTED_USERNAME):
    chats=set()
    for entry in updates:
        message=entry.get('message',{});chat=message.get('chat',{});sender=message.get('from',{})
        ident=chat.get('id')
        if chat.get('type')!='private' or type(ident) is not int or ident<=0:continue
        if sender.get('id')!=ident or sender.get('is_bot'):continue
        parts=message.get('text','').strip().split()
        if len(parts)!=2:continue
        command,_,mention=parts[0].partition('@')
        if command!='/start' or (mention and mention.casefold()!=username.casefold()):continue
        digest=hashlib.sha256(parts[1].encode()).hexdigest()
        if hmac.compare_digest(digest,challenge_hash):chats.add(ident)
    if len(chats)>1:raise BotError('Pairing code appeared in multiple private chats')
    return next(iter(chats)) if chats else None


def wait_for_private_start(client,challenge_hash,seconds=180):
    if client.api('getWebhookInfo').get('url'):
        raise BotError('An existing webhook prevents private-chat pairing')
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        remaining=max(1,int(deadline-time.monotonic()))
        # No offset: reading the queue does not acknowledge or discard messages.
        updates=client.api('getUpdates',{'timeout':min(20,remaining),'limit':100,'allowed_updates':['message']},timeout=min(35,remaining+5))
        ident=matching_private_chat(updates,challenge_hash)
        if ident is not None:return ident
        if len(updates)>=100:raise BotError('Pending update queue needs review before pairing')
    raise BotError('No matching private Start received within the pairing window')


def _key(token):
    return HKDF(algorithm=hashes.SHA256(),length=32,salt=None,
        info=b'lifemart-telegram-private-recipient-v1').derive(token.encode())


def encrypt_recipient(token,bot_id,chat_id):
    if type(chat_id) is not int or chat_id<=0:raise BotError('Recipient must be a private user chat')
    nonce=os.urandom(12);aad=('lifemart-recipient-v1:'+str(bot_id)).encode()
    data=json.dumps({'chat_id':chat_id,'chat_type':'private'},separators=(',',':')).encode()
    ciphertext=AESGCM(_key(token)).encrypt(nonce,data,aad)
    return {'version':1,'bot_id':bot_id,'nonce':base64.b64encode(nonce).decode(),
            'ciphertext':base64.b64encode(ciphertext).decode()}


def decrypt_recipient(token,bot_id,envelope):
    if envelope.get('version')!=1 or envelope.get('bot_id')!=bot_id:raise BotError('Recipient binding does not match this bot')
    try:
        nonce=base64.b64decode(envelope['nonce'],validate=True)
        data=AESGCM(_key(token)).decrypt(nonce,base64.b64decode(envelope['ciphertext'],validate=True),
            ('lifemart-recipient-v1:'+str(bot_id)).encode())
        payload=json.loads(data)
    except (InvalidTag,ValueError,KeyError,TypeError):
        raise BotError('Recipient binding cannot be authenticated with this token') from None
    ident=payload.get('chat_id')
    if payload.get('chat_type')!='private' or type(ident) is not int or ident<=0:raise BotError('Recipient binding is not a private chat')
    return ident


def send_connection_test(client,chat_id):
    message=client.api('sendMessage',{'chat_id':chat_id,'text':
        'Связь с JmReport подтверждена ✅\nЭто проверка личного чата для отчётов АВ12, М42 и Г34. Настройка автоотправки продолжается.'})
    chat=message.get('chat',{})
    if chat.get('id')!=chat_id or chat.get('type')!='private' or not message.get('message_id'):
        raise BotError('Telegram did not confirm the intended private recipient')


def send_album(client,chat_id,paths,caption):
    """Send one complete album, without retrying ambiguous delivery failures."""
    from pathlib import Path
    import uuid
    if not 2<=len(paths)<=10:raise BotError('Album must contain 2 to 10 images')
    boundary='lifemart-'+uuid.uuid4().hex
    media=[];parts=[]
    def field(name,value):
        parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"\r\n\r\n'+value+'\r\n').encode())
    for i,path in enumerate(paths):
        data=Path(path).read_bytes()
        if not data.startswith(b'\x89PNG') or len(data)>10_000_000:raise BotError('Invalid or oversized report image')
        name='photo'+str(i)
        item={'type':'photo','media':'attach://'+name}
        if i==0:item['caption']=caption
        media.append(item)
        parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"; filename="report.png"\r\nContent-Type: image/png\r\n\r\n').encode()+data+b'\r\n')
    field('chat_id',str(chat_id));field('media',json.dumps(media,ensure_ascii=False))
    parts.append(('--'+boundary+'--\r\n').encode())
    request=urllib.request.Request('https://api.telegram.org/bot'+client._token+'/sendMediaGroup',data=b''.join(parts),
        headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    try:
        with urllib.request.urlopen(request,timeout=120) as response:payload=json.load(response)
    except urllib.error.HTTPError as exc:
        try:rejected=album_rejection(json.loads(exc.read(65536)))
        except Exception:rejected=None
        if rejected is not None:raise rejected from None
        raise BotError('Telegram album delivery unconfirmed (HTTP '+str(exc.code)+'); do not automatically retry') from None
    except Exception as exc:
        raise BotError('Telegram album delivery unconfirmed ('+type(exc).__name__+'); do not automatically retry') from None
    rejected=album_rejection(payload)
    if rejected is not None:raise rejected
    result=payload.get('result',[])
    if not payload.get('ok') or len(result)!=len(paths):raise BotError('Telegram album delivery incomplete; do not automatically retry')
    for message in result:
        if message.get('chat',{}).get('id')!=chat_id or message.get('chat',{}).get('type')!='private':
            raise BotError('Telegram album recipient mismatch',delivery_state='unsafe')
    return len(result)
