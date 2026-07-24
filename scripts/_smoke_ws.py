"""Smoke-test the WS protocol the way the new file-source UI will: stream a wav
as PCM16 frames for enrollment, then stream a mix for extraction. Confirms the
enrollment -> TSE -> ASR path runs without protocol errors on file-fed audio.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets

ROOT = Path(__file__).resolve().parents[1]
FRAME = 4096  # bytes == 2048 samples (int16), matches the UI's FILE_FRAME


def pcm_bytes(name: str) -> bytes:
    data, _sr = sf.read(ROOT / "samples" / name, dtype="int16")
    if data.ndim > 1:
        data = data[:, 0]
    return data.astype("<i2").tobytes()


def chunks(buf: bytes):
    for i in range(0, len(buf), FRAME):
        yield buf[i:i + FRAME]


async def drain(ws, predicate, timeout=8.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    last = None
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), 1.0)
        except asyncio.TimeoutError:
            continue
        if isinstance(raw, (bytes, bytearray)):
            continue
        ev = json.loads(raw)
        last = ev
        if ev.get("event") == "error":
            raise RuntimeError(f"backend error: {ev.get('message')}")
        if predicate(ev):
            return ev
    return last


async def main() -> None:
    enr = pcm_bytes("enroll_target.wav")
    mix = pcm_bytes("mixed.wav")
    print(f"enroll {len(enr)//2} samples ({len(enr)//2/16000:.1f}s), "
          f"mix {len(mix)//2} samples ({len(mix)//2/16000:.1f}s)")

    last_exc = None
    for _ in range(30):
        try:
            ws = await websockets.connect("ws://127.0.0.1:8765", max_size=2**20)
            break
        except OSError as e:
            last_exc = e
            await asyncio.sleep(0.5)
    else:
        raise SystemExit(f"backend not reachable: {last_exc}")

    try:
        hello = await asyncio.wait_for(ws.recv(), 5)
        hello = json.loads(hello)
        print(f"hello: processor={hello.get('selectedProcessor')} "
              f"asrReady={hello.get('asrReady')} tseReady={hello.get('tseReady')}")

        await ws.send(json.dumps({"command": "setModels", "asrModel": "paraformer", "processor": "tse"}))
        await drain(ws, lambda m: m.get("event") in ("modelsChanged",))

        await ws.send(json.dumps({"command": "startEnrollment"}))
        await drain(ws, lambda m: m.get("state") == "enrolling")
        for c in chunks(enr):
            await ws.send(c)
            await asyncio.sleep(0.004)
        await ws.send(json.dumps({"command": "finishEnrollment"}))
        st = await drain(ws, lambda m: m.get("state") in ("ready", "idle"))
        print(f"after enrollment: state={st.get('state')} "
              f"seconds={st.get('enrollmentSeconds')}")

        await ws.send(json.dumps({"command": "startExtraction"}))
        await drain(ws, lambda m: m.get("state") == "extracting", timeout=60.0)

        async def sender():
            for c in chunks(mix):
                await ws.send(c)
                await asyncio.sleep(0.004)
            await ws.send(json.dumps({"command": "stopExtraction"}))

        sender_task = asyncio.create_task(sender())
        finals, partials = 0, 0
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), 60.0)
            except asyncio.TimeoutError:
                break
            if isinstance(raw, (bytes, bytearray)):
                continue
            ev = json.loads(raw)
            etype = ev.get("event")
            if etype == "transcript":
                if ev.get("final"):
                    finals += 1
                else:
                    partials += 1
                print(f"  transcript final={ev.get('final')} "
                      f"text={ev.get('text')!r}")
            elif etype == "state":
                print(f"  state -> {ev.get('state')}")
            elif etype == "error":
                raise RuntimeError(f"backend error during extraction: {ev.get('message')}")
            if ev.get("state") == "ready" and sender_task.done():
                await asyncio.sleep(0.5)
                break
        await sender_task
        print(f"extraction done: final transcripts={finals}, partials={partials}")
        print("SMOKE_OK")
    finally:
        await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
