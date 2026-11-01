"""JSON boundary for Chaquopy. Serialized by the Kotlin Bridge mutex."""
import json
from pathlib import Path
from .engine import Engine


def call(root, action, novel_id='', data='{}', transport=None):
    body = json.loads(data)
    if action == 'list':
        return json.dumps([json.loads(p.read_text()) for p in Path(root).glob('*/job.json')], ensure_ascii=False)
    if action == 'create':
        engine = Engine.create(root, body['title'], body['source'], body['model'], body.get('source_kind', 'EN_TRANSLATED'), body.get('raw'))
        return json.dumps(engine.state, ensure_ascii=False)
    def send(request_id, payload):
        return json.loads(str(transport.request(request_id, json.dumps(payload))))
    engine = Engine(root, novel_id, send if transport is not None else None,
                    (lambda: bool(transport.shouldPause())) if transport is not None else None)
    if action == 'detail':
        result = engine.detail(include_content=False)
    elif action == 'history':
        result = engine.history(body['name'])
    elif action == 'chapter':
        result = engine.chapter(int(body['chapter']))
    elif action == 'step':
        result = engine.step()
    elif action == 'debug':
        result = engine.debug_report()
    elif action == 'export':
        result = {'path': engine.export(body['kind'])}
    else:
        result = engine.command(action, body)
    return json.dumps(result, ensure_ascii=False)
