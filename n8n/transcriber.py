# -*- coding: utf-8 -*-
"""
CUATRO RIOS (TA-325555) - transcripción local de notas de voz para el flujo de n8n.

Servicio HTTP mínimo que escucha solo en el Mac (127.0.0.1:8765). n8n, desde el
contenedor, le manda el audio de la nota de voz de Telegram (POST /transcribe,
cuerpo binario) y recibe {"text": "..."}. Transcribe con mlx-whisper y el modelo
large-v3-turbo ya descargado: gratis, sin cuentas y sin que el audio salga del equipo.

Arranque (antes de la demo):
    /usr/bin/python3 demo_cuatrorios_transcriber.py
"""
import json
import os
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import mlx_whisper

MODEL = "mlx-community/whisper-large-v3-turbo"
HOST, PORT = "127.0.0.1", 8765
MAX_BYTES = 20 * 1024 * 1024  # Telegram voice notes are far smaller

# ffmpeg decodes the Ogg/Opus of Telegram
os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")


def transcribe(path):
    result = mlx_whisper.transcribe(path, path_or_hf_repo=MODEL, language="es")
    return (result.get("text") or "").strip()


class Handler(BaseHTTPRequestHandler):

    def _answer(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._answer(200, {"ok": True, "model": MODEL})
        else:
            self._answer(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/transcribe":
            self._answer(404, {"error": "not found"})
            return
        size = int(self.headers.get("Content-Length") or 0)
        if not size or size > MAX_BYTES:
            self._answer(400, {"error": "empty or too large audio"})
            return
        audio = self.rfile.read(size)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio)
            path = tmp.name
        try:
            start = time.time()
            text = transcribe(path)
            print(f"{time.strftime('%H:%M:%S')} {size} bytes en {time.time() - start:.1f}s: {text[:80]}", flush=True)
            self._answer(200, {"text": text})
        except Exception as e:  # noqa: BLE001 - the caller needs an answer, not a dropped connection
            print(f"{time.strftime('%H:%M:%S')} error: {e}", flush=True)
            self._answer(500, {"error": str(e)})
        finally:
            os.unlink(path)

    def log_message(self, *args):
        pass


def main():
    print(f"Cargando {MODEL}...", flush=True)
    # warm up so that the first voice note of the demo is fast
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        warm = tmp.name
    os.system(f"/opt/homebrew/bin/ffmpeg -loglevel quiet -y -f lavfi -i anullsrc=r=16000:cl=mono -t 1 {warm}")
    transcribe(warm)
    os.unlink(warm)
    print(f"Escuchando en http://{HOST}:{PORT} (POST /transcribe, GET /health)", flush=True)
    HTTPServer((HOST, PORT), Handler).serve_forever()  # one at a time: the model is not shared


if __name__ == "__main__":
    sys.exit(main())
