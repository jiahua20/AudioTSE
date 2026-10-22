# -*- coding: utf-8 -*-
"""把论文 md 里的围栏代码块渲染成「截图式」PNG：

  - 仿代码编辑器窗口：行号栏、Consolas 等宽代码、注释绿色着色、超长行按宽度折行
    （折行续行带 » 标记并缩进，不会看起来像被截断）
  - 重点行组用红色圆角框框住，框角挂红色编号圆标
  - 窗口下方是注释面板：编号 + 对应公式编号与作用说明
  - 窗口宽度按实际最宽行动态确定；总高超过一页可排高度时自动在逻辑行
    边界拆成多张图（红框组不跨图拆开，注释面板只列本图的组）

坐标约定：布局常量按 1x 书写，绘制前统一乘 SCALE；文字测量天然是 SCALE
倍像素，比较时同样先乘 SCALE，三处口径一致。

同时产出一份把代码围栏替换成图片引用的临时 md（_build_paper.md）供 pandoc 转换。
"""
import os
import re

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
MD = os.path.join(HERE, 'AudioTSE技术论文-声纹门控与TSE.md')
TMP_MD = os.path.join(HERE, '_build_paper.md')
PNG_DIR = os.path.join(HERE, 'png')
SCALE = 2
MAX_W_IN = 6.1
MAX_H_IN = 8.5          # docx 里单图最大高度（Letter 可排高度约 9 in，留余量）

CONSOLA = r'C:\Windows\Fonts\consola.ttf'
YAHEI = r'C:\Windows\Fonts\msyh.ttc'
YAHEI_BD = r'C:\Windows\Fonts\msyhbd.ttc'

INK, GRAY, LGRAY = '#D4D4D4', '#CCCCCC', '#6E7680'    # 代码 / 注释面板文字 / 行号（VS Code 深色）
RED = '#F14C4C'
COMMENT = '#6A9955'
WIN_BG, WIN_BORDER = '#1E1E1E', '#3C3C3C'
LEGEND_BG, LEGEND_BORDER = '#252526', '#7A6430'
LEGEND_TITLE = '#E5B567'

CODE_FONT, CJK_FONT = 13.5, 12.0
LINE_H = 21
CONTENT_MAX = 600          # 代码区最大宽度（1x px，约 80 个 ASCII 字符）

TITLES = [
    'sdk/web/src/full/vad.ts',
    'sdk/web/src/core/embedder.ts + voice-filter.ts',
    'sdk/web/src/core/stream-gate.ts',
    'app/backend/audio_tse/tse.py（注册嵌入缓存）',
    'app/backend/audio_tse/tse.py（_forward_cached 单窗前向）',
    'app/backend/audio_tse/tse.py（合成窗与 OLA）',
    'app/backend/audio_tse/tse.py（主循环）',
    'app/backend/audio_tse/server.py（RTF 口径）',
]

# 重点行注释规格：([匹配子串...], 注释)，按代码块顺序
SPECS = [
    [
        (['threshold: 0.5', 'minSilenceDuration: 0.3', 'minSpeechDuration: 0.1', 'maxSpeechDuration: 15.0'],
         '式(2)+段状态机：0.3 秒静音即「一句话说完」，直接决定门控的判定粒度与延迟'),
        (['const CHUNK = 2560'],
         '160 毫秒分块喂入：有状态模型的工程约束——大块单次喂入会丢段边界（2.2 节）'),
    ],
    [
        (['this.extractor.compute'],
         '式(3)~(14) 全部在这一次原生调用内完成（特征前端 + 网络前向）——公式细节在依赖库与预训练权重内部'),
        (['dot += left[i] * right[i]'],
         '式(15) 分子：内积 (a, b)，对应元素相乘求和'),
        (['Math.sqrt(ln) * Math.sqrt(rn)'],
         '式(15) 分母：模长乘积——余弦只比方向、与长度无关'),
        (['norm = Math.sqrt(norm) + 1e-8', 'normalized[i] = embedding[i] / norm', 'this.enrollment = normalized'],
         '式(16)：L2 归一化，此后每次判定退化为一次内积，代价与音频时长无关'),
        (['this.activeThreshold ='],
         '式(18)：净语音 <1.5 秒时阈值 ×0.7 = 0.35（短注册补偿）'),
        (['similarity >= this.activeThreshold', "if (this.enrollment === null)"],
         '式(17)：阈值判定；未注册恒放行——没有目标时不应拦截一切'),
    ],
    [
        (['rms(chunk) < this.silenceRms'],
         '式(19)：静音直判——不推理、不更新平滑状态（静音的「声纹」是乱数）'),
        (['this.pending < this.hopSamples'],
         '凑步中（<500 ms）沿用上次结论：放行决策连续，不逐块翻转'),
        (['this.filter.judge(this.buf)'],
         '最近 1 秒滑窗判定：历史块只作上下文、不重复发送，出结果节奏不变但更稳'),
        (['this.smoothing * similarity'],
         '式(20)：指数平滑（λ=0.5）——等效平均约两个判定窗，字幕不闪烁'),
        (['this.score >= this.threshold'],
         '式(22)：独立阈值 0.25，落在两类分布间隙中央；不可沿用整句的 0.5'),
    ],
    [
        (['_cached_spk_embedding = engine.compute_enroll_embedding'],
         '式(28)：嵌入只算一次、逐窗注入——省 (K−1)·C1 且条件逐窗严格一致'),
        (['compute_fbank'],
         '式(3)~(8)：Fbank+CMN 在原生库内完成（依赖库实现）'),
        (['model.spk_model(feats)'],
         '式(9)~(14)：网络前向在预训练权重内部（外置知识）'),
        (['extract_float(window, self._cached_spk_embedding)'],
         '热路径：每个窗口直接注入缓存的嵌入'),
    ],
    [
        (['torch.stft'],
         '式(29)：STFT——工程调用，变换在 PyTorch 库内（依赖库实现）'),
        (['subband_spec.append', 'subband_mix_spec.append'],
         '式(30)：频带分割 + 复数谱旁路保留（第⑤步复数乘法要用真实观测）'),
        (['bn_func('],
         '式(31)(32)：归一化与线性层参数都在预训练权重内（外置，工程只逐带调用）'),
        (['model.separator('],
         '式(33)~(36)：双路径注意力与说话人注入在权重内部（外置），工程只传入缓存嵌入'),
        (['this_output[:, 0] * torch.sigmoid(this_output[:, 1])'],
         '式(37)：M = A ⊙ sigmoid(B)——sigmoid 压到 (0,1)，掩码天然有界'),
        (['est_real =', 'est_imag ='],
         '式(38)(39)：复数乘法展开——CRM 的核心两行，输出锚定在真实观测上'),
        (['torch.istft'],
         '式(41)：逆变换（依赖库实现），与分析窗互逆、自补偿'),
        (['set_output_norm(False)'],
         '逐窗不归一化：为 COLA 拼接铺路（3.1 节尺度不变性的工程配套）'),
    ],
    [
        (['ramp = np.linspace', 'win[: self._cf_samples] = ramp', 'win[-self._cf_samples:] = 1.0 - ramp'],
         '式(42)：梯形合成窗——头尾 ramp 互补，是重叠区「和恒为 1」的前提；半开区间保证衔接点恰为 1'),
        (['self._out_overlap[:n] += seg'],
         '式(43)(45)：重叠区自然叠加 = 前窗淡出 + 本窗淡入的凸组合，接缝无跳变'),
        (['ready = self._out_overlap[:hop].copy()'],
         '头部 14400 个采样不会再被后续窗影响，可直接出队送回放与识别'),
    ],
    [
        (['voice_ratio = float(np.mean'],
         '式(46)：窗口语音占比（逐帧标志与输入缓冲逐采样对齐）'),
        (['voice_ratio < VOICE_RATIO_THRESHOLD', 'seg = window * self._synth_win'],
         '式(47)：静音窗零推理直通——关键细节：仍乘合成窗、仍走 OLA，接缝照样平滑'),
        (['self._engine.extract_float(window, self._cached_spk_embedding)'],
         '有语音窗：注入缓存嵌入做 BSRNN 单窗分离'),
        (['self._ola_take(self._hop_samples)'],
         '每个步长出队 0.9 秒 → 分离音频回放 + 喂识别'),
        (['drop = len(self._in_buf) - self._window_samples', 'self.dropped_seconds += drop'],
         '式(48)：超窗 + 0.5 秒容忍量即丢最旧、保最新——延迟钳制在有界，丢弃量可观测'),
    ],
    [
        (['HOP_MS = (WINDOW_SECONDS'],
         '步长 = 窗长 − 交叉淡化 = 0.9 秒（式 42 的 H）'),
        (['(tse_ms + asr_ms) / HOP_MS'],
         '式(49)：RTF 以步长为分母——「每秒音频需要多少秒 CPU」的真实口径'),
    ],
]

_fcache = {}


def F(path, size):
    key = (path, round(size * SCALE))
    if key not in _fcache:
        _fcache[key] = ImageFont.truetype(path, int(size * SCALE))
    return _fcache[key]


def mono(size=CODE_FONT):
    return F(CONSOLA, size)


def cjk(size=CJK_FONT, bold=False):
    return F(YAHEI_BD if bold else YAHEI, size)


_probe = ImageDraw.Draw(Image.new('RGB', (8, 8)))


def runs_of(line):
    """按 ASCII / 非 ASCII 切 run，分别用 Consolas / 雅黑。"""
    runs, buf, cur_ascii = [], '', None
    for ch in line:
        a = ord(ch) < 128
        if cur_ascii is None or a == cur_ascii:
            buf += ch
        else:
            runs.append((buf, cur_ascii))
            buf = ch
        cur_ascii = a
    if buf:
        runs.append((buf, cur_ascii))
    return [(t, a) for t, a in runs if t]


def text_width(s):
    """按 run 分别用 Consolas / 雅黑测量，混排行宽才准（返回 SCALE 倍像素）。"""
    return sum(_probe.textlength(t, font=mono() if a else cjk()) for t, a in runs_of(s))


def wrap_line(line, max_px):
    """按宽度折行；返回 [(片段起始字符下标, 片段文本)]，下标用于注释跨行着色。
    ASCII 词尽量不拆，CJK 逐字可拆。max_px 为 SCALE 倍像素。"""
    if text_width(line) <= max_px:
        return [(0, line)]
    out, cur, base = [], '', 0          # base = cur 首字符在原行中的下标
    for word in re.split(r'(\s+)', line):
        w_i = 0
        while w_i < len(word) and text_width(cur + word[w_i:]) > max_px:
            seg = ''
            for ch in word[w_i:]:
                if seg and text_width(cur + seg + ch) > max_px:
                    break
                seg += ch
            if not seg:                 # 当前行已满：先断行再装
                out.append((base, cur.rstrip()))
                base += len(cur)
                cur = ''
                continue
            out.append((base, (cur + seg).rstrip()))
            base += len(cur) + len(seg)
            cur, w_i = '', w_i + len(seg)
        cur += word[w_i:]
    if cur.strip():
        out.append((base, cur.rstrip()))
    return out or [(0, '')]


def plan_block(title, lines, spec):
    """预折行 + 测量，返回一个代码块的全部绘制信息（未拆分）。"""
    raw = [l.rstrip() for l in lines]
    rows = []                            # (逻辑行号, 片段起始下标, 片段文本)
    for li, line in enumerate(raw):
        for start, seg in wrap_line(line, CONTENT_MAX * SCALE):
            rows.append((li, start, seg))
    gutter_w = 14 + max(18, len(str(len(raw))) * 9 + 8)
    code_x = 14 + gutter_w
    win_w = max(520, code_x + max(text_width(seg) for _li, _st, seg in rows) / SCALE + 20)
    # 红框组 -> 逻辑行区间
    gid = {}
    for gi, (matches, _n) in enumerate(spec):
        hit = [i for i, l in enumerate(raw) if any(m in l for m in matches)]
        if hit:
            gid[gi] = (min(hit), max(hit))
    return {'title': title, 'raw': raw, 'rows': rows, 'gutter_w': gutter_w,
            'code_x': code_x, 'win_w': win_w, 'gid': gid}


def legend_height(block, gids):
    """注释面板高度（1x px）：标题 + 每条注释按实际折行计。"""
    if not gids:
        return 0
    h = 30
    for gi in gids:
        note = SPECS[block['spec_i']][gi][1]
        h += len(wrap_line(note, (block['win_w'] - 56) * SCALE)) * 19 + 10
    return h + 14


def split_block(block):
    """总高超过一页可排高度时，在逻辑行边界拆成多份。
    红框组覆盖的行区间保持完整（不跨份）；注释面板只列本份的组。"""
    cap = MAX_H_IN * block['win_w'] / MAX_W_IN          # 本窗口宽度下允许的 1x 总高
    n_lines = len(block['raw'])
    rows_of_line = [0] * n_lines
    for li, _st, _seg in block['rows']:
        rows_of_line[li] += 1
    # 粘连块：空闲行各自成块，红框组区间整体成块（含组前紧贴的空闲行并入，减少碎片）
    units, i = [], 0
    while i < n_lines:
        j = i
        for g0, g1 in block['gid'].values():
            if g0 <= i <= g1:
                j = max(j, g1)
        units.append((i, j))
        i = j + 1
    parts, cur = [], []
    for u0, u1 in units:
        trial = cur + [(u0, u1)]
        h = 32 + sum(rows_of_line[l] for a, b in trial for l in range(a, b + 1)) * LINE_H \
            + 12 + legend_height(block, groups_in(block, trial)) + 28
        if cur and h > cap:
            parts.append(cur)
            cur = [(u0, u1)]
        else:
            cur = trial
    if cur:
        parts.append(cur)
    return parts


def groups_in(block, line_units):
    """给定若干连续行区间，返回起始行落在其中的红框组。"""
    lo = min(u[0] for u in line_units)
    hi = max(u[1] for u in line_units)
    return [gi for gi, (g0, g1) in sorted(block['gid'].items()) if lo <= g0 <= hi]


def render_part(block, lines_range, gids, path):
    """渲染一份（一个窗口 + 本份注释面板）。"""
    X = lambda v: v * SCALE
    lo, hi = lines_range[0][0], lines_range[-1][1]
    rows = [(li, st, seg) for li, st, seg in block['rows'] if lo <= li <= hi]
    raw = block['raw']
    win_w, code_x, gutter_w = block['win_w'], block['code_x'], block['gutter_w']
    bar_h, pad_t, pad_b = 0, 16, 16
    code_h = len(rows) * LINE_H
    win_h = bar_h + pad_t + code_h + pad_b
    code_y = bar_h + pad_t
    # 逻辑行 -> 物理行序号
    first_row, last_row = {}, {}
    for ri, (li, _st, _seg) in enumerate(rows):
        first_row.setdefault(li, ri)
        last_row[li] = ri
    gid = {gi: block['gid'][gi] for gi in gids}
    leg_h = legend_height(block, gids)

    total_w, total_h = win_w + 28, win_h + (12 + leg_h if leg_h else 0) + 28
    img = Image.new('RGB', (int(total_w * SCALE), int(total_h * SCALE)), 'white')
    d = ImageDraw.Draw(img)
    ox, oy = X(14), X(14)

    d.rounded_rectangle([ox, oy, ox + X(win_w), oy + X(win_h)], radius=X(8),
                        fill=WIN_BG, outline=WIN_BORDER, width=SCALE)
    # —— 行号 + 代码（注释绿色；折行续行带 » 标记）——
    is_ts = '.ts' in block['title']
    marker = '//' if is_ts else '#'
    marker_pos = {li: raw[li].find(marker) for li in range(lo, hi + 1)}
    num_f = mono(10.5)
    for ri, (li, start, seg) in enumerate(rows):
        yy = oy + X(code_y + ri * LINE_H + LINE_H / 2)
        if ri == first_row[li]:
            d.text((ox + X(gutter_w - 8), yy), str(li + 1), font=num_f, fill=LGRAY, anchor='rm')
        else:
            d.text((ox + X(code_x), yy), '»', font=mono(11), fill=LGRAY, anchor='lm')
        # 注释边界：以整行的注释符位置对照本片段的字符区间
        m = marker_pos[li]
        cut = None
        if m >= 0:
            if start >= m:
                cut = 0
            elif start < m < start + len(seg):
                cut = m - start
        code_part, comment_part = (seg, '') if cut is None else \
            ((seg, '') if cut == 0 else (seg[:cut], seg[cut:]))
        xx = ox + X(code_x + (0 if ri == first_row[li] else 18))
        for part, col in ((code_part, INK), (comment_part, COMMENT)):
            for text, ascii_run in runs_of(part):
                f = mono() if ascii_run else cjk()
                d.text((xx, yy), text, font=f, fill=col, anchor='lm')
                xx += d.textlength(text, font=f)
    # —— 红框 + 编号角标 ——
    badge_pos = []
    for no, gi in enumerate(sorted(gid), start=1):
        l0, l1 = gid[gi]
        y0 = oy + X(code_y + first_row[l0] * LINE_H - 2)
        y1 = oy + X(code_y + (last_row[l1] + 1) * LINE_H + 1)
        bx0, bx1 = ox + X(code_x - gutter_w + 4), ox + X(win_w - 10)
        d.rounded_rectangle([bx0, y0, bx1, y1], radius=X(5), outline=RED, width=int(2.2 * SCALE))
        cx, cy = bx1 - X(4), y0
        r = X(10)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=RED)
        d.text((cx, cy + SCALE), str(no), font=F(CONSOLA, 12), fill='white', anchor='mm')
        badge_pos.append(no)
    # —— 注释面板 ——
    if gids:
        ly = oy + X(win_h + 12)
        d.rounded_rectangle([ox, ly, ox + X(win_w), ly + X(leg_h)], radius=X(8),
                            fill=LEGEND_BG, outline=LEGEND_BORDER, width=SCALE + 1)
        d.text((ox + X(14), ly + X(17)), '红框注释', font=cjk(12.5, True), fill=LEGEND_TITLE, anchor='lm')
        ey = ly + X(30)
        for no, gi in zip(badge_pos, sorted(gids)):
            note = SPECS[block['spec_i']][gi][1]
            note_rows = [t for _s, t in wrap_line(note, (win_w - 56) * SCALE)]
            r = X(8)
            cy = ey + X(9)
            d.ellipse([ox + X(14) - r, cy - r, ox + X(14) + r, cy + r], fill=RED)
            d.text((ox + X(14), cy + SCALE), str(no), font=F(CONSOLA, 10), fill='white', anchor='mm')
            for k, rr in enumerate(note_rows):
                d.text((ox + X(30), cy + X(k * 19)), rr, font=cjk(11.5), fill=GRAY, anchor='lm')
            ey += X(len(note_rows) * 19 + 10)
    # dpi 同时受宽高约束：保证放进 docx 后既不超页宽也不超页高
    W, H = img.size
    dpi = max(96, int(W / MAX_W_IN) + 1, int(H / MAX_H_IN) + 1)
    img.save(path, dpi=(dpi, dpi))
    return W, H


def render_all():
    os.makedirs(PNG_DIR, exist_ok=True)
    with open(MD, encoding='utf-8') as f:
        text = f.read()
    blocks = re.findall(r'```[a-z]*\n(.*?)```', text, flags=re.S)
    assert len(blocks) == len(SPECS) == len(TITLES), \
        f'代码块 {len(blocks)} / 规格 {len(SPECS)} / 标题 {len(TITLES)} 数量不一致'
    outs, images_per_block = [], []
    for i, (block_text, spec, title) in enumerate(zip(blocks, SPECS, TITLES), start=1):
        lines = block_text.rstrip('\n').split('\n')
        block = plan_block(title, lines, spec)
        block['spec_i'] = i - 1
        parts = split_block(block)
        names = []
        for j, line_units in enumerate(parts, start=1):
            name = f'code-{i:02d}.png' if len(parts) == 1 else f'code-{i:02d}-{j}.png'
            w, h = render_part(block, line_units, groups_in(block, line_units),
                               os.path.join(PNG_DIR, name))
            names.append(name)
            outs.append((i, j, len(parts), name, w, h))
        images_per_block.append(names)
    # 生成临时 md：围栏替换为图片引用（多份之间空行，pandoc 才不会并排）
    idx = 0

    def sub(m):
        nonlocal idx
        names = images_per_block[idx]
        idx += 1
        return '\n\n'.join(f'![](png/{n})' for n in names) + '\n'
    tmp = re.sub(r'```[a-z]*\n.*?```', sub, text, flags=re.S)
    with open(TMP_MD, 'w', encoding='utf-8') as f:
        f.write(tmp)
    return outs, TMP_MD


if __name__ == '__main__':
    for i, j, n, name, w, h in render_all()[0]:
        tag = f' {j}/{n}' if n > 1 else ''
        print(f'code block {i}{tag}: png/{name} ({w}x{h}px, '
              f'{w / max(w / MAX_W_IN, h / MAX_H_IN):.2f}in x {h / max(w / MAX_W_IN, h / MAX_H_IN):.2f}in)')
