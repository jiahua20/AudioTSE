# -*- coding: utf-8 -*-
"""svg -> png 批量导出（渲染本目录 svg/ 下手写的 SVG 子集）。

支持子集（自行绘制时限定这些元素，浏览器/draw.io 中打开源文件效果一致）：
  rect(x,y,width,height,rx)、circle、line、polyline、polygon、
  path(仅 M/L/H/V/Z 绝对坐标)、text/tspan(text-anchor, font-size, font-weight, fill)、
  g(transform 仅 translate)、stroke-dasharray、fill/stroke=none
标注白底：text 元素加 data-halo="1" 属性时，文字下先画白色圆角底。
"""
import math
import os
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SVG_DIR = os.path.join(HERE, 'svg')
PNG_DIR = os.path.join(HERE, 'png')
SCALE = 3
MAX_W_IN = 6.1

YAHEI = r'C:\Windows\Fonts\msyh.ttc'
YAHEI_BD = r'C:\Windows\Fonts\msyhbd.ttc'
FALLBACK = r'C:\Windows\Fonts\simhei.ttf'
# 组合记号（如 r̂ 的 U+0302）雅黑没有字形，从 Arial 取（实测中心对齐良好）
MARK_FONT = r'C:\Windows\Fonts\arial.ttf'
_fonts = {}


def _is_mark(ch):
    return 0x300 <= ord(ch) <= 0x36F


def font(size, bold=False):
    key = (round(size, 1), bold)
    if key not in _fonts:
        path = YAHEI_BD if bold else YAHEI
        if not os.path.exists(path):
            path = FALLBACK
        _fonts[key] = ImageFont.truetype(path, int(size))
    return _fonts[key]


def mark_font(size):
    key = ('mark', round(size, 1))
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype(MARK_FONT, int(size))
        except OSError:
            _fonts[key] = None
    return _fonts[key]


def _pts(attr):
    if not attr:
        return []
    out, vals = [], attr.replace(',', ' ').split()
    for i in range(0, len(vals) - 1, 2):
        out.append((float(vals[i]), float(vals[i + 1])))
    return out


def _color(v, default=None):
    if v is None or v == '':
        return default
    if v == 'none':
        return None
    return v if v.startswith('#') else '#' + v


def _parse_path(d):
    """仅支持 M/L/H/V/Z（绝对坐标）"""
    cmds, cur, start = [], [0.0, 0.0], None
    tokens, i = [], 0
    while i < len(d):
        c = d[i]
        if c.isalpha():
            tokens.append(c)
            i += 1
        elif c in ' -.,0123456789':
            j = i + 1 if c in '-.' else i
            num = c
            if c in '-.':
                num = ''
            while j < len(d) and (d[j].isdigit() or d[j] == '.' or (d[j] in '+-' and d[j - 1] in 'eE')):
                num += d[j]
                j += 1
            if c not in '-.':
                num = c
            tokens.append(float(num))
            i = j
        else:
            i += 1
    k = 0
    while k < len(tokens):
        t = tokens[k]
        if t == 'M':
            cur = [tokens[k + 1], tokens[k + 2]]
            start = list(cur)
            k += 3
        elif t == 'L':
            cmds.append((cur[0], cur[1], tokens[k + 1], tokens[k + 2]))
            cur = [tokens[k + 1], tokens[k + 2]]
            k += 3
        elif t == 'H':
            cmds.append((cur[0], cur[1], tokens[k + 1], cur[1]))
            cur[0] = tokens[k + 1]
            k += 2
        elif t == 'V':
            cmds.append((cur[0], cur[1], cur[0], tokens[k + 1]))
            cur[1] = tokens[k + 1]
            k += 2
        elif t == 'Z':
            if start:
                cmds.append((cur[0], cur[1], start[0], start[1]))
            k += 1
        elif isinstance(t, float):  # 隐式 L
            cmds.append((cur[0], cur[1], tokens[k], tokens[k + 1]))
            cur = [tokens[k], tokens[k + 1]]
            k += 2
        else:
            k += 1
    return cmds


class Svg:
    def __init__(self, path):
        self.root = ET.parse(path).getroot()
        self.items = []
        self._walk(self.root, (0.0, 0.0))

    def _walk(self, el, off):
        for ch in el:
            tag = ch.tag.split('}')[-1]
            tr = ch.get('transform', '')
            o = off
            if tr.startswith('translate'):
                inner = tr[tr.find('(') + 1:tr.find(')')]
                a, b = (inner.replace(',', ' ').split() + ['0', '0'])[:2]
                o = (off[0] + float(a), off[1] + float(b))
            if tag == 'g':
                self._walk(ch, o)
            elif tag in ('rect', 'circle', 'line', 'polyline', 'polygon', 'path', 'text'):
                self.items.append((tag, ch, o))

    @staticmethod
    def _draw_text(d, pos, txt, f, fs, col, anchor):
        """整串绘制；含组合记号时逐字符绘制，记号从 Arial 取、按基字符中心对齐。"""
        if not any(_is_mark(c) for c in txt):
            d.text(pos, txt, font=f, fill=col, anchor=anchor)
            return
        mf = mark_font(fs * SCALE)
        total = sum(f.getlength(c) for c in txt if not _is_mark(c))
        pen = {'ls': pos[0], 'ms': pos[0] - total / 2, 'rs': pos[0] - total}[anchor]
        prev_x, prev_c = None, None
        for c in txt:
            if _is_mark(c):
                if prev_c is not None and mf is not None:
                    bb, mb = f.getbbox(prev_c), mf.getbbox(c)
                    if bb and mb and bb[2] > bb[0] and mb[2] > mb[0]:
                        center = prev_x + (bb[0] + bb[2]) / 2
                        d.text((center - (mb[0] + mb[2]) / 2, pos[1]), c,
                               font=mf, fill=col, anchor='ls')
            else:
                d.text((pen, pos[1]), c, font=f, fill=col, anchor='ls')
                prev_x, prev_c = pen, c
                pen += f.getlength(c)

    def render(self, png_path):
        # 先收集所有坐标点求画布范围
        xs, ys = [], []

        def add(x, y):
            xs.append(x)
            ys.append(y)

        geoms = []
        for tag, el, (ox, oy) in self.items:
            a = dict(el.attrib)
            x0, y0 = ox, oy
            if tag == 'rect':
                g = ('rect', x0 + float(a.get('x', 0)), y0 + float(a.get('y', 0)),
                     float(a['width']), float(a['height']), a)
                add(g[1], g[2]); add(g[1] + g[3], g[2] + g[4])
            elif tag == 'circle':
                cx, cy, r = x0 + float(a['cx']), y0 + float(a['cy']), float(a['r'])
                g = ('circle', cx, cy, r, a)
                add(cx - r, cy - r); add(cx + r, cy + r)
            elif tag == 'line':
                g = ('line', x0 + float(a['x1']), y0 + float(a['y1']),
                     x0 + float(a['x2']), y0 + float(a['y2']), a)
                add(g[1], g[2]); add(g[3], g[4])
            elif tag in ('polyline', 'polygon'):
                pts = [(x0 + px, y0 + py) for px, py in _pts(a.get('points'))]
                g = (tag, pts, a)
                for p in pts:
                    add(*p)
            elif tag == 'path':
                cmds = [(x0 + a1, y0 + b1, x0 + a2, y0 + b2) for a1, b1, a2, b2 in _parse_path(a.get('d', ''))]
                g = ('path', cmds, a)
                for c in cmds:
                    add(c[0], c[1]); add(c[2], c[3])
            elif tag == 'text':
                fx = float(a.get('x', 0)) + x0
                fy = float(a.get('y', 0)) + y0
                fs = float(a.get('font-size', 14))
                txt = ''.join(el.itertext()).strip()
                # 估算文字宽度：ASCII 约 0.52em，中文约 1.0em，组合记号 0；再按锚点定区间
                n_ascii = sum(1 for c in txt if ord(c) < 128)
                n_mark = sum(1 for c in txt if _is_mark(c))
                tw = (n_ascii * 0.52 + (len(txt) - n_ascii - n_mark) * 1.0) * fs
                anchor = a.get('text-anchor', 'start')
                if anchor == 'middle':
                    tx0, tx1 = fx - tw / 2, fx + tw / 2
                elif anchor == 'end':
                    tx0, tx1 = fx - tw, fx
                else:
                    tx0, tx1 = fx, fx + tw
                g = ('text', fx, fy, fs, txt, a, el)
                add(tx0 - 2, fy - fs)
                add(tx1 + 2, fy + fs * 0.4)
            geoms.append(g)
        m = 12
        bx0, by0 = min(xs) - m, min(ys) - m
        W, H = int((max(xs) + m - bx0) * SCALE) + 1, int((max(ys) + m - by0) * SCALE) + 1
        img = Image.new('RGB', (W, H), 'white')
        d = ImageDraw.Draw(img)

        def T(p):
            return ((p[0] - bx0) * SCALE, (p[1] - by0) * SCALE)

        for g in geoms:
            tag = g[0]
            if tag == 'rect':
                _, x, y, w, h, a = g
                self._rect(d, T((x, y)), (w * SCALE, h * SCALE), a)
            elif tag == 'circle':
                _, cx, cy, r, a = g
                c = T((cx, cy))
                self._ellipse(d, c, r * SCALE, a)
            elif tag == 'line':
                _, x1, y1, x2, y2, a = g
                self._seg(d, T((x1, y1)), T((x2, y2)), a)
            elif tag in ('polyline', 'polygon'):
                _, pts, a = g
                self._pl(d, [T(p) for p in pts], a, close=(tag == 'polygon'))
            elif tag == 'path':
                _, cmds, a = g
                for c in cmds:
                    self._seg(d, T((c[0], c[1])), T((c[2], c[3])), a)
            elif tag == 'text':
                _, fx, fy, fs, txt, a, el = g
                self._text(d, T((fx, fy)), fs, el, a, bx0, by0)
        dpi = max(96, int(W / MAX_W_IN) + 1)
        img.save(png_path, dpi=(dpi, dpi))
        return W, H

    def _pen(self, a):
        return (_color(a.get('stroke'), None),
                max(1, round(float(a.get('stroke-width', 1.5)) * SCALE / 1.5)),
                a.get('stroke-dasharray'))

    @staticmethod
    def _dashed(d, p1, p2, col, lw, dash):
        dash_px = [float(v) * SCALE for v in dash.replace(' ', ',').split(',') if v]
        seg, acc = [], 0.0
        import math as _m
        L = _m.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if L == 0:
            return
        ux, uy = (p2[0] - p1[0]) / L, (p2[1] - p1[1]) / L
        di, t = 0, 0.0
        while t < L:
            step = min(dash_px[di % 2], L - t)
            if di % 2 == 0:
                s = (p1[0] + ux * t, p1[1] + uy * t)
                e = (p1[0] + ux * (t + step), p1[1] + uy * (t + step))
                d.line([s, e], fill=col, width=lw)
            t += step
            di += 1

    def _seg(self, d, p1, p2, a):
        col, lw, dash = self._pen(a)
        if col is None:
            return
        if dash:
            self._dashed(d, p1, p2, col, lw, dash)
        else:
            d.line([p1, p2], fill=col, width=lw)

    def _pl(self, d, pts, a, close=False):
        fill = _color(a.get('fill'), None)
        if close and fill:
            d.polygon(pts, fill=fill)
        col, lw, dash = self._pen(a)
        if col:
            seq = pts + [pts[0]] if close else pts
            if dash:
                for p1, p2 in zip(seq, seq[1:]):
                    self._dashed(d, p1, p2, col, lw, dash)
            else:
                d.line(seq, fill=col, width=lw, joint='curve')

    def _rect(self, d, pos, size, a):
        x, y = pos
        w, h = size
        fill = _color(a.get('fill'), None)
        col, lw, dash = self._pen(a)
        rx = float(a.get('rx', 0)) * SCALE
        if fill:
            d.rounded_rectangle([x, y, x + w, y + h], radius=rx, fill=fill,
                                outline=col, width=lw if col else 0)
        elif col:
            d.rounded_rectangle([x, y, x + w, y + h], radius=rx, outline=col, width=lw)

    def _ellipse(self, d, c, r, a):
        fill = _color(a.get('fill'), None)
        col, _lw, _dash = self._pen(a)
        box = [c[0] - r, c[1] - r, c[0] + r, c[1] + r]
        d.ellipse(box, fill=fill, outline=col, width=max(1, round(float(a.get('stroke-width', 1.5)) * SCALE / 1.5)))

    def _text(self, d, pos, fs, el, a, bx0, by0):
        col = _color(a.get('fill'), '#1F2933') or '#1F2933'
        bold = a.get('font-weight') in ('bold', '700', '600')
        f = font(fs * SCALE, bold)
        anchor = {'start': 'ls', 'middle': 'ms', 'end': 'rs'}.get(a.get('text-anchor', 'start'), 'ls')
        # 白底光环（边的标签不被线穿过）
        if a.get('data-halo'):
            txt = ''.join(el.itertext())
            w = d.textlength(txt, font=f)
            pad = 4 * SCALE // 2
            d.rectangle([pos[0] - w / 2 - pad, pos[1] - fs * SCALE - pad,
                         pos[0] + w / 2 + pad, pos[1] + pad], fill='white')
        # 主体（text 自身 + tspan 绝对/相对定位）
        ox, oy = float(a.get('x', 0)), float(a.get('y', 0))
        if el.text and el.text.strip():
            self._draw_text(d, pos, el.text.strip(), f, fs, col, anchor)
        cur_x, cur_y = ox, oy
        for ts in el.findall('tspan'):
            ta = ts.attrib
            if 'x' in ta:
                cur_x = float(ta['x'])
            if 'dy' in ta:
                cur_y += float(ta['dy'])
            t = (ts.text or '').strip()
            if not t:
                continue
            p = ((cur_x - bx0) * SCALE, (cur_y - by0) * SCALE)
            tf = font(float(ta.get('font-size', fs)) * SCALE,
                      ta.get('font-weight') in ('bold', '700', '600') or bold)
            tcol = _color(ta.get('fill'), col) or col
            tanchor = {'start': 'ls', 'middle': 'ms', 'end': 'rs'}.get(ta.get('text-anchor', a.get('text-anchor', 'start')), 'ls')
            d.text(p, t, font=tf, fill=tcol, anchor=tanchor)


def gen_fig3_svg(path):
    """fig3 频带分割：32 个小矩形对齐 + 色块图例行（程序确定性生成）。"""
    bands = [3] * 15 + [6] * 10 + [16] * 5 + [64] + [8]
    groups = [
        ('0～1.4 kHz', '15 带 × 93.75 Hz', '基频与第一、二共振峰（细分，信息最密）', '#D6E6FB', '#C3DCF9', 1400),
        ('1.4～3.3 kHz', '10 带 × 187.5 Hz', '第二、三共振峰与辅音起始', '#D5EEDF', '#C2E7D0', 1900),
        ('3.3～5.8 kHz', '5 带 × 500 Hz', '中高频', '#FDEFCC', '#FBE7AE', 2500),
        ('5.8～7.8 kHz', '1 带 × 2 kHz', '高频摩擦音（粗分）', '#F9D5D5', '#F5C2C2', 2000),
        ('7.8～8 kHz', '1 带 × 250 Hz', '剩余频段', '#E2E8F0', '#D3DCE8', 250),
    ]
    PX = 0.085          # px / Hz，频带矩形与频率刻度共用同一比例
    HZ = 31.25          # 每频点 Hz 数
    x0, y0, h = 26, 108, 64
    parts = []
    parts.append(f'<text x="{x0 + 4000 * PX:.1f}" y="{y0 - 40}" font-size="12.5" fill="#718096" '
                 f'text-anchor="middle" font-family="Microsoft YaHei">频率（Hz）</text>')
    x, gi = x0, 0
    for bi, bw in enumerate(bands):
        while gi + 1 < 5 and bi >= [15, 25, 30, 31][gi]:
            gi += 1
        w = bw * HZ * PX
        fill = groups[gi][3] if bi % 2 == 0 else groups[gi][4]
        parts.append(f'<rect x="{x:.1f}" y="{y0}" width="{max(w, 2):.1f}" height="{h}" '
                     f'fill="{fill}" stroke="#FFFFFF" stroke-width="1"/>')
        x += w
    # 组内带数标注（色带下方各組中心；末组太窄由图例覆盖）
    counts = [15, 10, 5, 1, 1]
    gx = x0
    for g in range(5):
        span = groups[g][5] * PX
        if span >= 100:
            parts.append(f'<text x="{gx + span / 2:.1f}" y="{y0 + h + 20}" font-size="12.5" '
                         f'font-weight="700" fill="#1F2933" text-anchor="middle" '
                         f'font-family="Microsoft YaHei">{counts[g]} 带</text>')
        gx += span
    # 频率刻度（7.8k 与 8k 相距过近，只保留 7.8k 标签）
    allb = [x0 + v * PX for v in (0, 1400, 3300, 5800, 7800, 8000)]
    for i, (tx, lab) in enumerate(zip(allb, ['0', '1.4k', '3.3k', '5.8k', '7.8k', ''])):
        parts.append(f'<line x1="{tx:.1f}" y1="{y0-10}" x2="{tx:.1f}" y2="{y0}" stroke="#718096" stroke-width="1.3"/>')
        if lab:
            parts.append(f'<text x="{tx:.1f}" y="{y0-18}" font-size="12" fill="#718096" '
                         f'text-anchor="middle" font-family="Microsoft YaHei">{lab}</text>')
    # 色块图例行（每行独占全宽，互不遮挡）
    ly = y0 + h + 44
    for i, (rng, width_desc, use, c1, _c2, _span) in enumerate(groups):
        yy = ly + i * 27
        parts.append(f'<rect x="60" y="{yy-13}" width="22" height="16" rx="3" fill="{c1}" stroke="#718096" stroke-width="1"/>')
        parts.append(f'<text x="94" y="{yy}" font-size="13" font-weight="700" fill="#1F2933" '
                     f'text-anchor="start" font-family="Microsoft YaHei">{rng}</text>')
        parts.append(f'<text x="212" y="{yy}" font-size="12.5" fill="#4A5568" '
                     f'text-anchor="start" font-family="Microsoft YaHei">{width_desc}</text>')
        parts.append(f'<text x="352" y="{yy}" font-size="12.5" fill="#718096" '
                     f'text-anchor="start" font-family="Microsoft YaHei">{use}</text>')
    height = ly + 5 * 27 + 4
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="732" height="{}" '
           'font-family="Microsoft YaHei">\n'.format(int(height)) + '\n'.join(parts) + '\n</svg>\n')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(svg)


def render_all():
    os.makedirs(PNG_DIR, exist_ok=True)
    gen_fig3_svg(os.path.join(SVG_DIR, 'fig3-band-split.svg'))
    outs = []
    for name in sorted(os.listdir(SVG_DIR)):
        if not name.endswith('.svg'):
            continue
        dst = os.path.join(PNG_DIR, name[:-4] + '.png')
        w, h = Svg(os.path.join(SVG_DIR, name)).render(dst)
        outs.append((name, os.path.basename(dst), w, h))
    return outs


if __name__ == '__main__':
    for src, dst, w, h in render_all():
        print(f'{src} -> png/{dst}  ({w}x{h}px)')
