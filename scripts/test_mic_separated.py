"""端到端验证：模拟前端「麦克风」路径，确认分离音频能用。

流程：enroll_target.wav 注册 → 以 ~真实速率灌 mixed.wav（模拟麦克风流）→
接收后端回传的二进制分离音频 → 存盘，并与 target_clean.wav 算 SI-SDR。

用法：先启动后端（python -m audio_tse.server），再运行本脚本。
"""
import asyncio
import json
import time
import wave
from pathlib import Path

import websockets

URI = "ws://127.0.0.1:8765"
FRAME = 4096  # bytes == 2048 samples == 128 ms @16k，与前端一致
SAMPLES = Path("samples")                       # 测试音频目录（项目根下运行）
OUT = Path("samples/_test_mic_separated.wav")   # 分离音频的落盘位置


def load_pcm16(name: str) -> bytes:
    # 读一个 16 kHz 单声道 PCM16 wav，返回原始字节（校验格式防拿错文件）
    w = wave.open(str(SAMPLES / name), "rb")
    assert w.getframerate() == 16000 and w.getnchannels() == 1, name
    data = w.readframes(w.getnframes())
    w.close()
    return data


def si_sdr(ref: bytes, est: bytes) -> float:
    # SI-SDR（scale-invariant SDR）：分离结果相对干净参考的分贝值，越高越好
    import numpy as np
    r = np.frombuffer(ref, dtype="<i2").astype(np.float64) / 32768.0  # 参考信号
    e = np.frombuffer(est, dtype="<i2").astype(np.float64) / 32768.0  # 估计信号
    n = min(len(r), len(e))          # 长度对齐（尾部 flush 产生的少量差异忽略）
    r, e = r[:n], e[:n]
    # 对齐能量后再算（scale-invariant）
    alpha = float(np.dot(e, r) / (np.dot(r, r) + 1e-9))  # 最优缩放系数
    e_proj = alpha * r              # 估计信号在参考上的投影（信号分量）
    noise = e - e_proj              # 残差（噪声分量）
    return float(10 * np.log10((np.dot(e_proj, e_proj) + 1e-9) / (np.dot(noise, noise) + 1e-9)))


async def main() -> None:
    enroll = load_pcm16("enroll_target.wav")        # 注册音频（目标人独唱）
    mix = load_pcm16("mixed.wav")                   # 混合音频（模拟麦克风流）
    target_clean = load_pcm16("target_clean.wav")   # 干净参考（算 SI-SDR 用）
    print(f"enroll {len(enroll)/32000:.1f}s | mix {len(mix)/32000:.1f}s | target {len(target_clean)/32000:.1f}s")

    async with websockets.connect(URI, max_size=2**20) as ws:
        q: asyncio.Queue = asyncio.Queue()  # 收件队列：reader 任务把所有消息塞进来

        async def reader() -> None:
            # 持续把服务端消息搬运进队列（发送循环里再慢慢消费）
            try:
                async for raw in ws:
                    await q.put(raw)
            except Exception:
                pass  # 连接关闭即结束

        reader_task = asyncio.create_task(reader())
        sep_chunks: list[bytes] = []      # 收到的分离音频块
        transcripts: list[str] = []       # 收到的转写文本
        tse_ms_vals: list[float] = []     # 从 metrics 事件里抓的单窗分离耗时

        async def wait_for_state(want: str, timeout: float = 60) -> None:
            # 消费队列直到出现目标状态（顺带收集 metrics 里的 tseMs）
            deadline = time.perf_counter() + timeout
            while time.perf_counter() < deadline:
                raw = await asyncio.wait_for(q.get(), timeout=deadline - time.perf_counter())
                if isinstance(raw, bytes):
                    continue  # 音频块先跳过（这里在等状态）
                ev = json.loads(raw)
                if ev.get("event") == "metrics" and ev.get("tseMs"):
                    tse_ms_vals.append(float(ev["tseMs"]))
                if ev.get("state") == want:
                    return
            raise TimeoutError(f"等不到 state={want}")

        # 1) hello
        hello = json.loads(await q.get())
        print(f"hello: processor={hello.get('selectedProcessor')} | tseReady={hello.get('tseReady')}")
        if hello.get("selectedProcessor") == "tse":
            # 等分离引擎后台预加载 + 预热完成（与真实前端一致：收到 tseEngineReady 才继续）
            t0 = time.perf_counter()
            while True:
                ev = json.loads(await asyncio.wait_for(q.get(), timeout=40))
                if ev.get("event") == "tseEngineReady":
                    print(f"分离引擎就绪（含预热），等待 {time.perf_counter()-t0:.1f}s")
                    break

        # 2) 注册
        await ws.send(json.dumps({"command": "startEnrollment"}))
        await wait_for_state("enrolling")
        for i in range(0, len(enroll), FRAME):    # 注册音频全量快速灌入（注册期不需要按实时速率）
            await ws.send(enroll[i:i + FRAME])
        await ws.send(json.dumps({"command": "finishEnrollment"}))
        await wait_for_state("ready")
        print("注册完成")

        # 3) 开始提取（首次会触发引擎加载/预热；这里测的是「能不能用」，等得起）
        await ws.send(json.dumps({"command": "startExtraction"}))
        await wait_for_state("extracting")
        print("开始提取，按真实速率灌 mixed.wav…")

        n_sent = 0
        t0 = time.perf_counter()
        for i in range(0, len(mix), FRAME):
            await ws.send(mix[i:i + FRAME])
            n_sent += 1
            # 边发边收（reader 把消息塞进 q，这里快速抽干）
            while not q.empty():
                raw = q.get_nowait()
                if isinstance(raw, bytes):
                    sep_chunks.append(raw)        # 二进制 = 分离音频
                else:
                    ev = json.loads(raw)
                    if ev.get("event") == "transcript" and ev.get("text"):
                        transcripts.append(ev["text"])
                    elif ev.get("event") == "metrics" and ev.get("tseMs"):
                        tse_ms_vals.append(float(ev["tseMs"]))
            # 按真实速率节流：每帧 128 ms，落后了才 sleep（模拟麦克风节奏）
            await asyncio.sleep(max(0.0, 0.12 * n_sent - (time.perf_counter() - t0)))

        # 4) 排干尾部
        await asyncio.sleep(2.5)                  # 等最后几个窗口分离完并回传
        await ws.send(json.dumps({"command": "stopExtraction"}))  # 触发 flush 尾部
        await asyncio.sleep(1.0)
        reader_task.cancel()

    sep = b"".join(sep_chunks)
    print(f"\n=== 结果 ===")
    print(f"发出 {n_sent} 帧（{n_sent*0.128:.1f}s），收回 {len(sep_chunks)} 个分离音频块 / {len(sep)/32000:.2f}s")
    print(f"转写片段 {len(transcripts)} 条：{''.join(transcripts)[:120]}")
    if tse_ms_vals:
        print(f"单窗 TSE 耗时 {min(tse_ms_vals):.0f}~{max(tse_ms_vals):.0f} ms（中位 {sorted(tse_ms_vals)[len(tse_ms_vals)//2]:.0f}）")

    if len(sep) > 3200:  # 至少 0.1s 才存盘/比较
        w = wave.open(str(OUT), "wb")             # 分离音频落盘（可人工试听）
        w.setnchannels(1); w.setframerate(16000); w.setsampwidth(2)
        w.writeframes(sep); w.close()
        print(f"分离音频已存 {OUT}")
        sdr = si_sdr(target_clean, sep)           # 与干净参考对比
        print(f"SI-DR(相对 target_clean) = {sdr:.2f} dB（>0 说明确实分离出了目标人声；越高越好）")
    else:
        print(f"⚠️ 收到的分离音频不足（{len(sep)} 字节），怀疑麦克风分离路径没产出音频")


if __name__ == "__main__":
    asyncio.run(main())
