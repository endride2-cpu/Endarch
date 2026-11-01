#!/usr/bin/env python3
"""
upload_to_channel.py — Upload file ke channel Telegram via Bot API.

Contoh:
  python3 upload_to_channel.py --discover
      # tampilkan chat_id semua channel/group yang bot-nya udah masuk
  python3 upload_to_channel.py results/buku_id.epub -1001234567890
      # kirim 1 file sebagai dokumen
  python3 upload_to_channel.py extracted/buku/images/*.jpg -1001234567890 --delete-after
      # kirim banyak file, hapus lokal kalau sukses

Token baru dibaca HANYA dari env TELEGRAM_BOT_TOKEN.
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path
from publication import ensure_uploadable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "https://api.telegram.org"
def get_creds() -> tuple[str, str]:
    """New token only; no legacy files or automatic local-server discovery."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Upload optional: configure a NEW TELEGRAM_BOT_TOKEN first")
    base = os.environ.get("TELEGRAM_API_BASE", API).rstrip("/")
    if not re.match(r"^(https://|http://(localhost|127\.0\.0\.1)(:\d+)?$)", base):
        raise ValueError("TELEGRAM_API_BASE harus https:// (atau http://localhost untuk server lokal)")
    return base, token


def redact(message: str) -> str:
    """Sensor token bot pada semua pesan keluar."""
    return re.sub(r"\bbot\d+:[A-Za-z0-9_-]+", "bot***", str(message))


def api(creds: tuple[str, str], method: str, **params) -> dict:
    import requests

    base, token = creds
    try:
        r = requests.post(f"{base}/bot{token}/{method}", data=params, timeout=120)
    except requests.RequestException:
        raise RuntimeError(f"{method} request failed (bot***)") from None
    d = r.json()
    if not d.get("ok"):
        raise RuntimeError(f"{method} failed (bot***)")
    return d["result"]


def discover(creds: tuple[str, str]):
    import requests

    base, token = creds
    try:
        r = requests.get(f"{base}/bot{token}/getUpdates", timeout=30)
    except requests.RequestException:
        raise RuntimeError("getUpdates request failed (bot***)") from None
    d = r.json()
    if not d.get("ok"):
        raise RuntimeError("getUpdates failed (bot***)")
    seen = {}
    for upd in d.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
        chat = msg.get("chat", {})
        if not chat:
            continue
        cid = chat.get("id")
        title = chat.get("title") or chat.get("username") or "(dm)"
        ctype = chat.get("type", "?")
        seen[cid] = (title, ctype)
    if not seen:
        print("Belum ada chat yang terdeteksi. Pastikan bot sudah ditambahkan ke channel.")
        return
    print("Chat yang terdeteksi untuk bot ini:")
    for cid, (title, ctype) in sorted(seen.items(), key=lambda kv: kv[1][0].lower()):
        print(f"  {cid}  [{ctype}]  {title}")


def send_file(creds: tuple[str, str], chat_id, path: Path, caption: str = "", delete_after: bool = False) -> bool:
    import requests

    if not path.exists():
        print(f"  ⚠ {path.name} tidak ditemukan, dilewati")
        return False
    ensure_uploadable(path)
    base, token = creds
    limit = 50 * 1024 * 1024 if base == API else 2 * 1024 * 1024 * 1024
    if path.stat().st_size > limit:
        print(f"  ⚠ {path.name} > {limit // (1024*1024)}MB (limit), dilewati")
        return False
    with open(path, "rb") as f:
        files = {"document": (path.name, f)}
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        try:
            r = requests.post(f"{base}/bot{token}/sendDocument", data=data, files=files, timeout=1800)
        except requests.RequestException:
            raise RuntimeError("sendDocument request failed (bot***)") from None
    d = r.json()
    if d.get("ok"):
        print(f"  ✅ {path.name} → {d['result']['message_id']}")
        if delete_after:
            path.unlink(missing_ok=True)
            print(f"  🗑 dihapus dari server: {path.name}")
        return True
    print(f"  ❌ {path.name}: sendDocument failed (bot***)")
    return False


def main():
    ap = argparse.ArgumentParser(description="Upload file ke channel Telegram")
    ap.add_argument("chat_id", nargs="?", default=None, help="ID channel numerik (lihat --discover)")
    ap.add_argument("paths", nargs="*", help="File yang mau diupload (atau folder — semua isinya)")
    ap.add_argument("--discover", action="store_true", help="Tampilkan chat_id yang bisa dipakai")
    ap.add_argument("--caption", default="", help="Caption (dipakai di file pertama)")
    ap.add_argument("--delete-after", action="store_true", help="Hapus file lokal setelah upload sukses")
    ap.add_argument("--delay", type=float, default=0.4, help="Jeda antar file (detik, default 0.4)")
    args = ap.parse_args()

    creds = get_creds()

    if args.discover:
        discover(creds)
        return

    if not args.chat_id or not args.paths:
        ap.error("butuh chat_id dan minimal 1 file (atau --discover)")

    chat_id = args.chat_id
    if not str(chat_id).lstrip("-").isdigit():
        ap.error(f"chat_id harus numerik ({chat_id}) — pakai --discover untuk cari ID channel")

    paths = []
    for p in args.paths:
        pp = Path(p).expanduser()
        if pp.is_dir():
            paths.extend(sorted(f for f in pp.iterdir() if f.is_file()))
        else:
            paths.append(pp)

    ok = 0
    for i, p in enumerate(paths):
        cap = args.caption if i == 0 else ""
        if send_file(creds, chat_id, p, cap, args.delete_after):
            ok += 1
        time.sleep(args.delay)

    print(f"\n📊 {ok}/{len(paths)} file terupload")
    sys.exit(0 if ok == len(paths) else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Gagal: {redact(e)}")
        sys.exit(1)
