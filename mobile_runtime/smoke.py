"""Offline device smoke test. Synthetic content only; never contacts the relay."""
import io
import json
from pathlib import Path
import tempfile
import zipfile
from PIL import Image
from .engine import Engine


def run(root):
    paragraph = 'Bulan naik di atas halaman dan semua orang memperhatikan dengan tenang.'
    text = '# 1. Bulan\n\n' + '\n\n'.join([paragraph] * 15)
    def model(request_id, body):
        prompt = body['prompt']
        if prompt.startswith('# GLOSSARY'):
            content = '# KARAKTER\n| Hangeul | Nama_Resmi |\n|---|---|\n| — | Bulan |\n'
        elif prompt.startswith('Buat panduan'):
            content = 'Bahasa Indonesia alami.'
        elif prompt.startswith('# REFINEMENT'):
            content = f'===TEKS===\n{text}\n===GATE===\nLULUS\n===CATATAN===\nCatatan uji.'
        elif prompt.startswith('Anda auditor'):
            content = json.dumps({'gate': 'LULUS', 'masalah': []})
        else:
            content = text
        return dict(status='SUCCEEDED', content=content, finish_reason='stop', cost_status='unknown')
    with tempfile.TemporaryDirectory(dir=root) as directory:
        folder = Path(directory)
        source = folder / 'source.epub'
        with zipfile.ZipFile(source, 'w') as z:
            z.writestr('mimetype', 'application/epub+zip')
            z.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
            z.writestr('book.opf', '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c"/></spine></package>')
            z.writestr('c.xhtml', '<html><body><h1>Bulan</h1>' + ('<p>' + paragraph + '</p>') * 15 + '</body></html>')
        engine = Engine.create(folder / 'novels', 'Uji perangkat', source, 'offline')
        engine.transport = model
        for _ in range(40):
            state = engine.step()
            if state['status'] == 'WAITING_INPUT' and state['stage'] == 'trans':
                engine.command('approve_refine')
            elif state['status'] == 'COMPLETED':
                break
            elif state['status'] in ('FAILED', 'BLOCKED_QA', 'WAITING_INPUT'):
                if state['stage'] == 'validate':
                    from validate_epub import validate
                    epub = engine.home / (engine.state['id'] + '_terjemahan.epub')
                    if epub.exists():
                        raise AssertionError(str(validate(epub)))
                raise AssertionError(state['error'])
        assert engine.state['status'] == 'COMPLETED'
        assert Path(engine.export('epub')).exists()
        assert Path(engine.export('notes')).exists()
        # Exercise the native Pillow JPEG library as well as EPUB/font resource access.
        image = Image.new('RGB', (8, 8), 'white')
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG')
        assert Image.open(io.BytesIO(buffer.getvalue())).size == (8, 8)
        return 'OK: eight stages, QA, EPUB, notes, fonts, Pillow'
