"""端到端验证：各处理模式都应回传可播放音频（听实时性）。

依次切到 passthrough / tse，各灌 8s mixed.wav，确认每种模式都收到二进制音频帧
（+ 转写）。passthrough 应近乎全量回传；TSE 回传分离音频。
（声纹门控已迁端侧：由 app/scripts/test-ondevice-gate.cjs 验证，不走本服务。）

用法：先启动后端（python -m audio_tse.server），再运行本脚本。
"""
import asyncio
import json
import time
import wave
from pathlib import Path

import websockets

URI = "ws://127.0.0.1:8765"
FRAME = 4096              # 每帧字节 = 2048 采样 = 128 ms，与前端一致
SAMPLES = Path("samples") # 测试音频目录


def load_pcm16(name: str) -> bytes:
    # 读 16 kHz 单声道 PCM16 wav → 原始字节
    w = wave.open(str(SAMPLES / name), "rb")
    data = w.readframes(w.getnframes()); w.close()
    return data


async def drain_to_state(q, want, timeout=90):
    # 消费队列直到出现目标状态；期间忽略音频块，顺带监控引擎加载失败
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        raw = await asyncio.wait_for(q.get(), timeout=deadline - time.perf_counter())
        if isinstance(raw, bytes):
            continue
        ev = json.loads(raw)
        if ev.get("event") == "tseEngineReady" and not ev.get("ready"):
            raise RuntimeError("引擎加载失败")  # TSE 权重没就绪，继续等也没意义
        if ev.get("state") == want:
            return ev
    raise TimeoutError(f"等不到 state={want}")


async def run_processor(ws, q, processor: str, mix: bytes) -> None:
    # 切到该处理器
    await ws.send(json.dumps({"command": "setModels", "asrModel": "paraformer", "processor": processor}))
    # 等 modelsChanged（顺带把期间二进制/事件抽干）
    while True:
        raw = await q.get()
        if isinstance(raw, bytes):
            continue
        ev = json.loads(raw)
        if ev.get("event") == "modelsChanged":
            break
    if processor == "tse":
        # 等引擎预加载完成
        while True:
            raw = await asyncio.wait_for(q.get(), timeout=60)
            if isinstance(raw, bytes):
                continue
            ev = json.loads(raw)
            if ev.get("event") == "tseEngineReady":
                break

    await ws.send(json.dumps({"command": "startExtraction"}))
    await drain_to_state(q, "extracting")

    n_sent = n_recv = bytes_recv = n_text = 0
    t0 = time.perf_counter()
    for i in range(0, len(mix), FRAME):
        await ws.send(mix[i:i + FRAME])     # 发一帧
        n_sent += 1
        while not q.empty():                # 抽干回传：音频块计数 + 转写计数
            raw = q.get_nowait()
            if isinstance(raw, bytes):
                n_recv += 1; bytes_recv += len(raw)
            else:
                ev = json.loads(raw)
                if ev.get("event") == "transcript" and ev.get("text"):
                    n_text += 1
        # 按真实速率节流（128 ms/帧）
        await asyncio.sleep(max(0.0, 0.12 * n_sent - (time.perf_counter() - t0)))
    await asyncio.sleep(2.0)                # 等尾部窗口
    await ws.send(json.dumps({"command": "stopExtraction"}))  # flush 尾部
    await asyncio.sleep(0.8)
    while not q.empty():                    # 收尾：把 flush 出的尾部音频也算上
        raw = q.get_nowait()
        if isinstance(raw, bytes):
            n_recv += 1; bytes_recv += len(raw)
        else:
            ev = json.loads(raw)
            if ev.get("event") == "transcript" and ev.get("text"):
                n_text += 1

    print(f"  [{processor:13s}] 发 {n_sent} 帧 | 收 {n_recv} 个音频块 / {bytes_recv/32000:.2f}s | 转写 {n_text} 条")
    assert n_recv > 0, f"{processor} 没收到任何音频！"  # 核心断言：必须回传可播放音频
    print(f"   [OK] {processor} 有可播放音频")


async def main() -> None:
    enroll = load_pcm16("enroll_target.wav")  # 注册音频
    mix = load_pcm16("mixed.wav")             # 混合音频
    async with websockets.connect(URI, max_size=2**20) as ws:
        q: asyncio.Queue = asyncio.Queue()

        async def reader():
            # 后台搬运：所有服务端消息 → 队列
            try:
                async for raw in ws:
                    q.put_nowait(raw)
            except Exception:
                pass
        rt = asyncio.create_task(reader())

        hello = json.loads(await q.get())
        print("hello:", hello.get("selectedProcessor"))

        # 先注册一次（三种模式共用同一段注册声纹）
        await ws.send(json.dumps({"command": "startEnrollment"}))
        await drain_to_state(q, "enrolling")
        for i in range(0, len(enroll), FRAME):
            await ws.send(enroll[i:i + FRAME])
        await ws.send(json.dumps({"command": "finishEnrollment"}))
        await drain_to_state(q, "ready")
        print("注册完成，开始逐模式验证音频回传…\n")

        # passthrough 先（最轻、最该全量回传）
        await run_processor(ws, q, "passthrough", mix)
        await run_processor(ws, q, "tse", mix)
        rt.cancel()
    print("\n全部通过：各模式都有可播放音频。")


if __name__ == "__main__":
    asyncio.run(main())
