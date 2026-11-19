"""Pair only the private chat holding the one-time code. Never log a raw chat ID."""
import argparse,json,os,re
from pathlib import Path
from .transport import BotClient,BotError,wait_for_private_start,encrypt_recipient,decrypt_recipient,send_connection_test

BINDING=Path(__file__).with_name('recipient.enc.json')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--operation',choices=('pair','test'),default='pair')
    parser.add_argument('--pairing-hash',default='')
    args=parser.parse_args()
    if os.getenv('GITHUB_ACTIONS')!='true':raise BotError('Use the manual Telegram connection workflow')
    if args.operation=='pair' and not re.fullmatch(r'[0-9a-f]{64}',args.pairing_hash):raise BotError('A SHA-256 pairing hash is required')
    token=os.getenv('TELEGRAM_BOT_TOKEN','');client=BotClient(token);me=client.identity()
    if args.operation=='pair':
        ident=wait_for_private_start(client,args.pairing_hash)
        if BINDING.exists() and decrypt_recipient(token,me['id'],json.loads(BINDING.read_text()))!=ident:
            raise BotError('A different recipient is already bound; explicit re-pairing is required')
        envelope=encrypt_recipient(token,me['id'],ident)
    else:
        if not BINDING.exists():raise BotError('No private recipient has been paired yet')
        envelope=json.loads(BINDING.read_text());ident=decrypt_recipient(token,me['id'],envelope)
    send_connection_test(client,ident)
    result={'token_valid':True,'bot_username':me['username'],'private_chat_verified':True,'test_message_accepted':True}
    # Only authenticated ciphertext leaves the worker. Its key remains in Actions Secrets.
    if args.operation=='pair':result['encrypted_recipient_binding']=envelope
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('Telegram private connection verified. Test message accepted. No token or private chat ID displayed.\n')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'connection_verified':False,'error':str(exc) if isinstance(exc,BotError) else type(exc).__name__}),flush=True)
        raise SystemExit(1) from None
