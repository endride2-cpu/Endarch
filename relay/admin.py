"""Operator-only reconciliation after checking the provider ledger.

Stop the relay before using this command. No device/UI gate override is provided.
"""
import argparse
import json
import os
import time
from .app import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request_id')
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--not-completed', action='store_true', help='Provider confirmed no successful completion; allow user to retry')
    choice.add_argument('--response', help='Path to actual OpenAI-compatible completion JSON recovered from provider')
    parser.add_argument('--reason', required=True, help='Non-secret operator audit note')
    args = parser.parse_args()
    store = Store(os.environ.get('RELAY_DB', './relay-data/relay.sqlite3'), os.environ['RELAY_ENCRYPTION_KEY'], recover=False)
    with store.db() as db:
        row = db.execute('SELECT status FROM request WHERE id=?', (args.request_id,)).fetchone()
        if not row or row['status'] != 'UNKNOWN':
            parser.error('Only UNKNOWN requests can be reconciled')
    if args.response:
        with open(args.response, encoding='utf-8') as f:
            recovered = json.load(f)
        choice = recovered['choices'][0]
        result = dict(status='SUCCEEDED', content=choice['message']['content'], finish_reason=choice.get('finish_reason'),
                      usage=recovered.get('usage'), cost=None, cost_status='unknown')
    else:
        result = dict(status='FAILED', retryable=False, error='Operator confirmed no completion; explicit retry permitted', cost=None, cost_status='unknown')
    store.finish(args.request_id, result)
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS reconciliation(id TEXT, at REAL, reason TEXT)')
        db.execute('INSERT INTO reconciliation VALUES (?,?,?)', (args.request_id, time.time(), args.reason))
    print('Reconciled. Restart relay; resume the existing request in the APK first.')


if __name__ == '__main__':
    main()
