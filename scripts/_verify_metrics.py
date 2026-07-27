"""Verify the realtime-metrics path end to end: stream a wav through passthrough
(only needs sherpa, not torch/wesep) and assert the backend emits `metrics`
events carrying a non-null RTF. Run with the backend already listening on 8765.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
from pathlib import Path

import soundfile as sf
import websockets

ROOT = Path(__file__).resolve().parents[1]
FRAME = 4096  # bytes == 2048 int16 samples, matches the UI's FILE_FRAME
MIX_REPEATS = 2  # loop the mix this many times so several windows get processed


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
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), 1.0)
        except asyncio.TimeoutError:
            continue
        if isinstance(raw, (bytes, bytearray)):
            continue
        ev = json.loads(raw)
        if ev.get("event") == "error":
            raise RuntimeError(f"backend error: {ev.get('message')}")
        if predicate(ev):
            return ev
    return None


async def main(processor: str = "passthrough") -> None:
    enr = pcm_bytes("enroll_target.wav")
    mix = pcm_bytes("mixed.wav")
    print(f"processor={processor}  enroll {len(enr)//2/16000:.1f}s, mix {len(mix)//2/16000:.1f}s")

    for _ in range(40):
        try:
            ws = await websockets.connect("ws://127.0.0.1:8765", max_size=2**20)
            break
        except OSError:
            await asyncio.sleep(0.5)
    else:
        raise SystemExit("backend not reachable on ws://127.0.0.1:8765")

    try:
        await ws.recv()  # hello
        await ws.send(json.dumps({"command": "setModels",
                                  "asrModel": "paraformer", "processor": processor}))
        await drain(ws, lambda m: m.get("event") == "modelsChanged")

        await ws.send(json.dumps({"command": "startEnrollment"}))
        await drain(ws, lambda m: m.get("state") == "enrolling")
        for c in chunks(enr):
            await ws.send(c)
            await asyncio.sleep(0.004)
        await ws.send(json.dumps({"command": "finishEnrollment"}))
        st = await drain(ws, lambda m: m.get("state") in ("ready", "idle"))
        if st and st.get("state") != "ready":
            raise SystemExit("enrollment did not reach ready (need >=3s target audio)")

        await ws.send(json.dumps({"command": "startExtraction"}))
        await drain(ws, lambda m: m.get("state") == "extracting", timeout=60.0)

        metrics_seen: list[dict] = []

        async def sender():
            for _ in range(MIX_REPEATS):
                for c in chunks(mix):
                    await ws.send(c)
                    await asyncio.sleep(0.128)  # real-time pace, like the UI
            await ws.send(json.dumps({"command": "stopExtraction"}))

        sender_task = asyncio.create_task(sender())
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), 30.0)
            except asyncio.TimeoutError:
                break
            if isinstance(raw, (bytes, bytearray)):
                continue
            ev = json.loads(raw)
            if ev.get("event") == "metrics":
                metrics_seen.append(ev)
                print(f"  metrics rtf={ev.get('rtf')} asrMs={ev.get('asrMs')} "
                      f"backlog={ev.get('backlogSec')} e2e={ev.get('e2eFirstMs')} "
                      f"audio={ev.get('audioSec')}s wall={ev.get('wallSec')}s")
            elif ev.get("event") == "error":
                raise RuntimeError(f"backend error: {ev.get('message')}")
            if ev.get("state") == "ready" and sender_task.done():
                await asyncio.sleep(0.3)
                break
        await sender_task

        assert metrics_seen, "no metrics event received"
        rtf_vals = [m["rtf"] for m in metrics_seen if m.get("rtf") is not None]
        assert rtf_vals, "metrics never carried a non-null rtf"
        steady = statistics.median(rtf_vals[1:]) if len(rtf_vals) > 1 else rtf_vals[0]
        print(f"\nMETRICS_OK: {len(metrics_seen)} events; "
              f"steady rtf={steady:.3f} (range {min(rtf_vals):.3f}..{max(rtf_vals):.3f}); "
              f"first e2e={metrics_seen[0].get('e2eFirstMs')} ms")
    finally:
        await ws.close()


if __name__ == "__main__":
    proc = sys.argv[1] if len(sys.argv) > 1 else "passthrough"
    asyncio.run(main(proc))
