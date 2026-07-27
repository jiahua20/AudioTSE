"""Confirm the backend streams separated audio as binary frames alongside the
transcript, so the UI can play it. Run with the backend on 8765."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import soundfile as sf
import websockets

ROOT = Path(__file__).resolve().parents[1]
FRAME = 4096  # 2048 samples, matches UI


def pcm_bytes(name: str) -> bytes:
    data, _sr = sf.read(ROOT / "samples" / name, dtype="int16")
    if data.ndim > 1:
        data = data[:, 0]
    return data.astype("<i2").tobytes()


def chunks(buf: bytes):
    for i in range(0, len(buf), FRAME):
        yield buf[i:i + FRAME]


async def drain_until(ws, pred, timeout=30.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), 2.0)
        except asyncio.TimeoutError:
            continue  # server silent this window (e.g. during model construction) — keep waiting
        if isinstance(raw, (bytes, bytearray)):
            continue
        ev = json.loads(raw)
        if ev.get("event") == "error":
            raise RuntimeError(f"backend error: {ev.get('message')}")
        if pred(ev):
            return ev
    raise TimeoutError("drain timed out")


async def main() -> None:
    enr = pcm_bytes("enroll_target.wav")
    mix = pcm_bytes("mixed.wav")
    ws = await websockets.connect("ws://127.0.0.1:8765", max_size=2 ** 22)
    try:
        await ws.recv()  # hello
        await ws.send(json.dumps({"command": "setModels", "asrModel": "paraformer", "processor": "tse"}))
        await drain_until(ws, lambda m: m.get("event") == "modelsChanged")
        await ws.send(json.dumps({"command": "startEnrollment"}))
        await drain_until(ws, lambda m: m.get("state") == "enrolling")
        for c in chunks(enr):
            await ws.send(c)
            await asyncio.sleep(0.004)
        await ws.send(json.dumps({"command": "finishEnrollment"}))
        await drain_until(ws, lambda m: m.get("state") == "ready")
        await ws.send(json.dumps({"command": "startExtraction"}))
        await drain_until(ws, lambda m: m.get("state") == "extracting", timeout=90.0)

        bin_frames = 0
        bin_bytes = 0
        texts = 0

        async def send_mix():
            for c in chunks(mix):
                await ws.send(c)
                await asyncio.sleep(0.128)
            await ws.send(json.dumps({"command": "stopExtraction"}))

        task = asyncio.create_task(send_mix())
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), 30.0)
            except asyncio.TimeoutError:
                break
            if isinstance(raw, (bytes, bytearray)):
                bin_frames += 1
                bin_bytes += len(raw)
                continue
            ev = json.loads(raw)
            if ev.get("event") == "transcript" and ev.get("text"):
                texts += 1
            if ev.get("state") == "ready" and task.done():
                await asyncio.sleep(0.3)
                break
        await task
    finally:
        await ws.close()

    secs = bin_bytes / 2 / 16000
    print(f"binary audio frames: {bin_frames}  ({secs:.2f}s of separated audio)")
    print(f"non-empty transcript texts: {texts}")
    if bin_frames > 0 and secs > 5.0:
        print("PLAYBACK_OK: backend streams separated audio alongside transcript")
    else:
        print("PLAYBACK_FAIL")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
