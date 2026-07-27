# TSE 深度图解：BSRNN 单窗分离 + 重叠相加

本文是 [ARCHITECTURE.md §7](ARCHITECTURE.md#7-核心原理) 的“第二层”：把纯音频 TSE 路径里两个最吃原理的环节，用**逐步图解 + 公式 + 真实数值**拆开讲。

- **Part A**：BSRNN 一个 2 秒窗口内部，混合波形如何被“按目标说话人”分离。
- **Part B**：这些独立窗口如何用重叠相加（OLA）无缝拼成连续输出。

所有数值取自 `models/wesep-bsrnn-ecapa-vox1/config.yaml` 与 `wesep/models/bsrnn.py`，代码位置指向 `backend/audio_tse/tse.py`。

---

## Part A — BSRNN 单窗前向（频域目标说话人分离）

入口：`BufferedWeSepTse._extract_float` → `_forward_cached`（`tse.py:110`）。
输入：一个 2 秒窗口的混合波形 `x ∈ ℝ^{1×32000}`（16 kHz × 2 s）。目标：估出目标说话人的波形 `ŝ`。

### A.1 为什么在频域做

时域采样点之间相互耦合，很难直接“筛人”。语音在**时频谱（STFT）**里更稀疏：不同说话人的能量在“频率×时间”格子上分布不同，网络更容易学到“留谁、压谁”。所以 BSRNN 先 STFT、在复数频谱上估一个掩蔽、再 iSTFT 回时域。

### A.2 STFT 与子带划分

```text
x : [1, 32000]                         混合波形（一个 2 s 窗）
  │ STFT  win=512(32ms), stride=128(8ms), Hann窗   ← config.yaml: win/stride
  ▼
X : [1, 257, T]  (复数)                T ≈ 250 帧（2 s ÷ 8 ms/帧；STFT 默认 center=True 反射填充）
  │ 拆成实/虚两路
  ▼
X_RI : [1, 2, 257, T] (实数)           stack([Re, Im], dim=1)
```

- `enc_dim = win/2 + 1 = 257` 个频率 bin（`bsrnn.py:182`）。
- 一帧 = 8 ms，2 秒窗 → 250 帧。

**子带（subband）划分**（`bsrnn.py:197`）：把 257 个 bin 切成 **32 个子带**，低频切得细、高频切得粗（符合人耳频率分辨率）：

```text
band_width = [3]*15  +  [6]*10  +  [16]*5  +  [64]*1  +  [8]
              └ 0~1.4kHz ┘ └ ~1.4~3.3kHz ┘ └3.3~5.8k┘ └5.8~7.8k┘ └Nyquist┘
              15 个×100Hz    10 个×200Hz     5 个×500Hz  1 个×2kHz   1 个剩余
              合计 45 + 60 + 80 + 64 + 8 = 257 bin   ✓   nband = 32
```

每个子带 `i` 取出两份：`subband_mix_spec[i]`（复数混合谱，后面套掩蔽用）和 `subband_spec[i]`（实数 Re/Im 特征，进分离器用）。

### A.3 说话人条件：注册嵌入（只算一次）

参考语音在 `startExtraction` 时算一次（`tse.py:97`），之后每个窗口复用，**不重复跑 ECAPA**：

```text
enroll PCM ──fbank(80 mel, CMN)──▶ ECAPA-TDNN(c512) ──▶ e ∈ ℝ^{192}
                                                        │ spk_transform = Identity (use_spk_transform=false)
                                                        ▼
                                        spk_embedding ∈ ℝ^{1×192×1×1}   （缓存，供每窗复用）
```

这个 192 维向量就是“要提取谁”的条件，广播进分离器。

### A.4 分离器（separator）

每个子带的实数特征先过各自的 BatchNorm（`model.BN[i]`），堆叠后连同说话人嵌入送进分离器：

```text
subband_feature : [1, 32, 128, T]     每子带 BN 到 feature_dim=128，共 32 子带
                       │
                       ▼  model.separator(subband_feature, spk_embedding, nch=1)
sep_output      : [1, 32, 128, T]     num_repeat=6 层堆叠的时频 Transformer
                                          （spk_fuse_type=multiply：嵌入以乘性融入特征）
```

`num_repeat=6` 是分离器主干的重复深度（`config.yaml`）。输出 `sep_output` 是每个子带的“潜在表示”，下一步用它预测掩蔽。

### A.5 复数比例掩蔽（CRM）—— 核心公式

每个子带一个掩蔽头 `model.mask[i]`，把 `sep_output[:,i]` 变成复数掩蔽 `M`（`tse.py:137`）：

```text
this_output = mask_func(sep_output[:,i]).view(B, 2, 2, bw, T)
this_mask   = this_output[:,0] * sigmoid(this_output[:,1])     # 门控：a·σ(b)，有界
M = this_mask[:,0] + j·this_mask[:,1]                          # 复数掩蔽  ∈ ℂ^{bw×T}
```

把 `M` 当作“目标/混合”的复数比值，**复数相乘**套到混合谱上（`tse.py:141`）：

```text
设混合谱 X = X_r + j·X_i ，  掩蔽 M = M_r + j·M_i

est = X · M
    = (X_r + j X_i)(M_r + j M_i)
    = (X_r·M_r − X_i·M_i)  +  j·(X_r·M_i + X_i·M_r)

代码里：  est_real = X_r·M_r − X_i·M_i
          est_imag = X_r·M_i + X_i·M_r      ←  完全等价于复数乘法
```

**为什么是复数掩蔽而不是实数幅度掩蔽？** 实数掩蔽只能压幅度（`|X|·m`），相位仍用混合的；而目标人和干扰人的相位不同，复数掩蔽同时修正幅度与相位，分离更干净。`M ≈ ŝ/X`，于是 `X·M ≈ ŝ`。这正是 CRM（Complex Ratio Masking）。

### A.6 iSTFT 回到波形

把 32 个子带的复数谱拼回全带 `est_spec ∈ ℂ^{257×T}`，逆 STFT：

```text
sep_subband_spec ──cat─▶ est_spec : [1, 257, T] (复数)
                                  │ iSTFT  win=512, stride=128, length=32000
                                  ▼
                            ŝ : [1, 32000]      一个窗口的目标人波形
```

> `set_output_norm(False)`（`tse.py:77`）：关闭逐窗幅度归一化。否则每窗各自归一会使窗间增益跳变，破坏 Part B 的拼接一致性；改成最后统一 clip 兜底（`_to_pcm16`，`tse.py:170`）。

### A.7 单窗全流程（张量形状速查）

| 步 | 张量 | 形状 | 代码 |
|---|---|---|---|
| 输入混合 | `x` | `[1, 32000]` | `tse.py:162` |
| STFT | `X` | `[1, 257, 250]` 复 | `tse.py:119` |
| Re/Im | `X_RI` | `[1, 2, 257, 250]` | `tse.py:121` |
| 子带特征 | `subband_feature` | `[1, 32, 128, 250]` | `tse.py:129` |
| 分离器 | `sep_output` | `[1, 32, 128, 250]` | `tse.py:134` |
| 复数掩蔽 | `M` | 各子带 `ℂ^{bw×250}` | `tse.py:140` |
| 估谱 | `est_spec` | `[1, 257, 250]` 复 | `tse.py:146` |
| iSTFT | `ŝ` | `[1, 32000]` | `tse.py:147` |

---

## Part B — 重叠相加（OLA）流式拼接

入口：`accept_pcm16`（`tse.py:188`）。问题：Part A 每次只处理一个**固定 2 秒窗**，相邻两窗是**独立估计掩蔽**的（不同的 `M`），直接首尾相接会在每 1.9 秒的接缝处产生断裂/咔哒声。OLA 用一个合成窗 + 少量重叠把它们平滑缝起来。

### B.1 参数（实数）

```text
SR              = 16000
WINDOW_SECONDS  = 2      →  W = 32000 采样       (tse.py:13)
CROSSFADE_SEC   = 0.1    →  cf = 1600 采样        (tse.py:19)
HOP             = W − cf = 30400 采样 ≈ 1.9 s     (tse.py:84)
```

每“吃进” 1.9 秒新音频，就分离一次 2 秒窗、吐出 1.9 秒结果。

### B.2 合成窗与 COLA 性质（为何能无损拼接）

合成窗（`tse.py:87`）：

```text
ramp = linspace(0, 1, 1600)        # 0 → 1，长 1600
win[   0 : 1600] = ramp            # 窗头：0→1 渐入
win[1600 :30400] = 1               # 中段：恒 1
win[30400:32000] = 1 − ramp        # 窗尾：1→0 渐出
```

设相邻窗 n 与 n+1，它们在样本区间 `[nH+W−cf, nH+W) = [nH+30400, nH+32000)` 重叠 1600 点：

```text
窗 n   在该区 = 窗尾 = 1 − ramp
窗 n+1 在该区 = 窗头 = ramp
相加           (1 − ramp) + ramp = 1     ←  COLA：重叠区恒等于 1
```

中段 `[nH+1600, nH+30400)` 只被窗 n 覆盖，窗值 = 1。**所以整条拼接窗之和处处为 1**，若每窗分离结果相同则完美重构；实际每窗掩蔽略不同，这 0.1 秒重叠区就成了两份估计的**平滑交叉淡化**：

```text
         窗 n 输出(分离A)                 窗 n+1 输出(分离B)
 ┌──────────────────────────┐   ┌──────────────────────────┐
 │ ramp↗  ████████ 1 ████████↘│↗ramp  ████████ 1 ████████ │
 │0    1600              30400 32000│                 ↑
 │         ▲重叠区▲◀── 1600 ──▶     │
 │         (1−r)·A + r·B 混合        │
 └──────────────────────────┘   └──────────────────────────┘
   样本:  nH ……………… nH+30400 ……… nH+32000 ……… nH+2H
                         ↑发出去(H=30400)      ↑留在_out_overlap里和下一窗混
```

**为什么 hop = W − cf 而不是 50% 重叠？** 50% 重叠每个采样处理两遍、RTF 翻倍；这里重叠只占 1600/32000 = 5%，`RTF ≈ W/H ≈ 1.05`，几乎无额外算力却拿到无缝。

### B.3 缓冲区机制（两个缓冲 + 一进一出）

`accept_pcm16`（`tse.py:188`）维护两个缓冲：

- `_in_buf`：**输入**累积。每来一帧 2048 样本（128 ms）就 append。
- `_out_overlap`：**输出**重叠区。每分离一窗就把“窗×合成窗”加进来，再从头部切走一个 hop 作为最终输出，尾部留给下一窗混合。

```text
循环（只要 _in_buf ≥ W=32000）：
  window   = _in_buf[0 : 32000]                # 取一个整窗
  seg      = BSRNN(window) * synth_win         # Part A 分离 + 乘合成窗
  _ola_add(seg)                                # 加进 _out_overlap（不够长则补零）
  emit     = _ola_take(H=30400)                # 切走头部 30400 → 转 PCM16 输出
  _in_buf  = _in_buf[30400:]                   # 输入前进一个 hop（保留末尾 1600 给下窗重叠）
```

`_ola_add`/`_ola_take`（`tse.py:175`/`183`）就是“累加 + 滑窗出队”。稳态下 `_out_overlap` 始终保留约 1600 样本（上一窗尾巴），与下一窗头部相加完成交叉淡化。

### B.4 一个具体走查（前两窗）

```text
t=0.0s   _in_buf 累积……
t=2.0s   _in_buf 达 32000 → 分离窗1 → _out_overlap=[窗1全 32000]
         emit[0:30400] (1.9s)  ; 留 _out_overlap=[窗1尾 1600]  ; _in_buf 留末 1600
t=3.9s   又进 1.9s → _in_buf 再次 32000（=窗1末1600 + 新30400）→ 分离窗2
         _ola_add(窗2)  → _out_overlap[0:1600] = 窗1尾(1−r) + 窗2头(r)  ←交叉淡化！
                          _out_overlap[1600:32000] = 窗2 中段+尾
         emit[0:30400] (1.9s)  ; 留 _out_overlap=[窗2尾 1600]
……      每 1.9s 一个 hop，输出连续不断
```

注意首字延迟：必须先攒满 2 秒才能出第一段，所以**算法延迟下限 ≈ 2 s**（加上推理耗时即 UI 所说“约 3 秒缓冲”）。

### B.5 收尾 flush（不丢字、不掺静音）

停止时 `_in_buf` 往往不足一窗。`flush`（`tse.py:202`）把残余**零填到整窗**再分离，但**只取真实音频占据的那一段输出**（不取零填产生的尾部），避免末尾的字被丢掉或被静音伪影污染。之后清空两个缓冲。

---

## 一句话串联

> 注册语音 → ECAPA 算出 192 维条件（**算一次**）→ 混合音频按 2 秒整窗送进 BSRNN：STFT→32 子带→分离器(融入说话人)→复数比例掩蔽→iSTFT 得到该窗目标波形（**Part A**）→ 乘合成窗、相邻窗重叠 0.1 秒相加，把独立估计平滑缝成连续流（**Part B**）→ 再喂给流式 ASR 出字幕。
```
