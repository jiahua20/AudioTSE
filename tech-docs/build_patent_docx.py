# -*- coding: utf-8 -*-
"""构建《AudioTSE专利技术交底书-目标说话人提取.docx》的流水线：

  1) 交底书四张图 -> png/：fig8 总体流程主循环（本交底书专绘）
     + fig2 前向六步 + fig4 OLA + fig1 门控（均与论文共用 svg/ 源）
  2) 交底书 .md 直接 -> pandoc -> docx（自定义字体模板：宋体/黑体/Times New Roman + 页码）
  3) postprocess：pandoc OMML 的 WPS 兼容性修补（同论文 build_paper_docx.py，
     但无封面分节逻辑——交底书不做独立封面页）

用法（tech-docs/ 目录，AudioTSE 环境的 python）：python build_patent_docx.py
改图：编辑 svg/fig1~fig5 后重跑（fig3 由脚本生成，勿手改）；改文字：编辑 .md 后重跑。

注意：反向抑制交底书的构建脚本是 build_patent_reverse_docx.py（另一条线）。
"""
import os
import re
import subprocess
import zipfile

import pypandoc
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

import svg2png

TECH = os.path.dirname(os.path.abspath(__file__))
MD = os.path.join(TECH, 'AudioTSE专利技术交底书-目标说话人提取.md')
OUT = os.path.join(TECH, 'AudioTSE专利技术交底书-目标说话人提取.docx')
REF = os.path.join(TECH, '_reference_patent.docx')
FIGS = ('fig8-master-flow.svg', 'fig2-tse-forward.svg',
        'fig4-ola-crossfade.svg', 'fig1-gate-flow.svg')


def set_style(doc, name, latin, east, size=None, bold=None, black=False):
    st = doc.styles[name]
    st.font.name = latin
    rf = st.element.get_or_add_rPr().get_or_add_rFonts()
    rf.set(qn('w:ascii'), latin)
    rf.set(qn('w:hAnsi'), latin)
    rf.set(qn('w:eastAsia'), east)
    if size is not None:
        st.font.size = Pt(size)
    if bold is not None:
        st.font.bold = bold
    if black:
        st.font.color.rgb = RGBColor(0, 0, 0)


def build_reference():
    pandoc = pypandoc.get_pandoc_path()
    with open(REF, 'wb') as f:
        f.write(subprocess.run([pandoc, '--print-default-data-file', 'reference.docx'],
                               capture_output=True).stdout)
    doc = Document(REF)
    set_style(doc, 'Normal', 'Times New Roman', '宋体', size=10.5)
    doc.styles['Normal'].paragraph_format.line_spacing = 1.3
    for s in ('Body Text', 'First Paragraph', 'Compact', 'Block Text'):
        try:
            set_style(doc, s, 'Times New Roman', '宋体')
        except KeyError:
            pass
    # 正文：两端对齐 + 首行缩进 2 字符
    for s in ('Body Text', 'First Paragraph'):
        try:
            pf = doc.styles[s].paragraph_format
            pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            pf.first_line_indent = Pt(21)
        except KeyError:
            pass
    # 标题：居中（交底书首页顶部，不做下沉封面）
    set_style(doc, 'Title', 'Times New Roman', '黑体', size=18, bold=True, black=True)
    tp = doc.styles['Title'].paragraph_format
    tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tp.space_before = Pt(6)
    tp.space_after = Pt(16)
    for s in ('Author', 'Date'):
        try:
            set_style(doc, s, 'Times New Roman', '宋体', size=12, black=True)
            dpf = doc.styles[s].paragraph_format
            dpf.alignment = WD_ALIGN_PARAGRAPH.CENTER
            dpf.space_before = Pt(4)
        except KeyError:
            pass
    # 标题层级：字体 + 前后留白
    for s, size, before, after in (('Heading 1', 14, 18, 8), ('Heading 2', 12.5, 12, 6),
                                   ('Heading 3', 11.5, 10, 5)):
        set_style(doc, s, 'Times New Roman', '黑体', size=size, bold=True, black=True)
        pf = doc.styles[s].paragraph_format
        pf.space_before = Pt(before)
        pf.space_after = Pt(after)
    # 图：居中；题注：图下方居中、小一号
    for s, size, before, after in (('Captioned Figure', None, 10, 4),
                                   ('Image Caption', 9, 4, 16)):
        try:
            set_style(doc, s, 'Times New Roman', '宋体', size=size, black=True)
            pf = doc.styles[s].paragraph_format
            pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pf.space_before = Pt(before)
            pf.space_after = Pt(after)
        except KeyError:
            pass
    for s in ('Source Code', 'Verbatim Char'):
        try:
            set_style(doc, s, 'Consolas', '宋体', size=9)
        except KeyError:
            pass
    sec = doc.sections[0]
    sec.footer.is_linked_to_previous = False
    p = sec.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    b = OxmlElement('w:fldChar'); b.set(qn('w:fldCharType'), 'begin')
    i = OxmlElement('w:instrText'); i.set(qn('xml:space'), 'preserve'); i.text = ' PAGE '
    e = OxmlElement('w:fldChar'); e.set(qn('w:fldCharType'), 'end')
    run._r.append(b); run._r.append(i); run._r.append(e)
    doc.save(REF)


def postprocess_omml(docx_path):
    """pandoc OMML 的 WPS 兼容性修补（与论文 build_paper_docx.py 同源）：
    1) 删数学文本里的零宽空格；2) 清空 supHide/subHide 的 nary 隐藏槽
    （只删紧跟 </m:sub> / </m:naryPr> 的直接子级槽）；3) m:val on/off -> 1/0；
    4) settings.xml 补 m:mathPr 并把 Cambria Math 换成 Times New Roman；
    5) 版式：图片段居中免缩进、公式段免缩进、表格内文字压到 9.5pt。
    """
    zin = zipfile.ZipFile(docx_path)
    items = {n: zin.read(n) for n in zin.namelist()}
    zin.close()

    xml = items['word/document.xml'].decode('utf-8')
    zwsp = xml.count('​')
    xml = xml.replace('​', '')
    xml = xml.replace('m:val="on"', 'm:val="1"').replace('m:val="off"', 'm:val="0"')

    def fix_nary(mo):
        blk = mo.group(0)
        if '<m:supHide m:val="1"' in blk:
            blk = re.sub(r'(</m:sub>|</m:naryPr>)<m:sup>.*?</m:sup>', r'\1', blk, count=1, flags=re.S)
        if '<m:subHide m:val="1"' in blk:
            blk = re.sub(r'</m:naryPr><m:sub>.*?</m:sub>', '</m:naryPr>', blk, count=1, flags=re.S)
        return blk
    n_nary = len(re.findall(r'<m:nary>', xml))
    xml = re.sub(r'<m:nary>.*?</m:nary>', fix_nary, xml, flags=re.S)

    def fix_para(mo):
        p = mo.group(0)
        if '<w:drawing>' in p:
            inject = '<w:ind w:firstLine="0"/><w:jc w:val="center"/>'
        elif '<m:oMathPara>' in p:
            inject = '<w:ind w:firstLine="0"/>'
        else:
            return p
        m = re.search(r'<w:pPr>\s*(?:<w:pStyle w:val="[^"]+"\s*/>)?', p)
        if m:
            at = m.end()
            p = p[:at] + inject + p[at:]
        elif '<w:pPr>' in p:
            p = p.replace('<w:pPr>', '<w:pPr>' + inject, 1)
        else:
            p = re.sub(r'(<w:p\b[^>]*>)', r'\1<w:pPr>' + inject + '</w:pPr>', p, count=1)
        return p
    xml = re.sub(r'<w:p\b[^>]*>.*?</w:p>', fix_para, xml, flags=re.S)

    def shrink_table(mo):
        t = mo.group(0)
        t = t.replace('</w:rPr>', '<w:sz w:val="19"/><w:szCs w:val="19"/></w:rPr>')
        t = re.sub(r'<w:r>(?=<w:t)', '<w:r><w:rPr><w:sz w:val="19"/><w:szCs w:val="19"/></w:rPr>', t)
        return t
    xml = re.sub(r'<w:tbl>.*?</w:tbl>', shrink_table, xml, flags=re.S)
    items['word/document.xml'] = xml.encode('utf-8')

    s = items['word/settings.xml'].decode('utf-8')
    if '<m:mathPr>' not in s:
        mathpr = ('<m:mathPr><m:mathFont m:val="Times New Roman"/><m:brkBin m:val="before"/>'
                  '<m:brkBinSub m:val="--"/><m:smallFrac m:val="0"/><m:dispDef/>'
                  '<m:lMargin m:val="0"/><m:rMargin m:val="0"/><m:defJc m:val="centerGroup"/>'
                  '<m:wrapIndent m:val="1440"/><m:intLim m:val="subSup"/>'
                  '<m:naryLim m:val="undOvr"/></m:mathPr>')
        if 'xmlns:m=' not in s.split('>', 1)[0]:
            s = s.replace('<w:settings ', '<w:settings xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" ', 1)
        s = s.replace('</w:settings>', mathpr + '</w:settings>')
        items['word/settings.xml'] = s.encode('utf-8')
    else:
        items['word/settings.xml'] = s.replace(
            '<m:mathFont m:val="Cambria Math"/>',
            '<m:mathFont m:val="Times New Roman"/>').encode('utf-8')

    with zipfile.ZipFile(docx_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n, data in items.items():
            if n.endswith('.xml') and b'Cambria Math' in data:
                data = data.replace(b'Cambria Math', b'Times New Roman')
            zout.writestr(n, data)
    print(f'omml postprocess: removed {zwsp} zero-width spaces, fixed nary slots '
          f'({n_nary} nary), mathPr ensured')


if __name__ == '__main__':
    for name in FIGS:
        w, h = svg2png.Svg(os.path.join('svg', name)).render(
            os.path.join('png', name[:-4] + '.png'))
        print(f'figure: svg/{name} -> png/{name[:-4]}.png ({w}x{h}px)')

    build_reference()
    pypandoc.convert_file(MD, 'docx', format='markdown', outputfile=OUT,
                          extra_args=['--reference-doc=' + REF,
                                      '--resource-path=' + TECH])
    print('pandoc ->', os.path.basename(OUT))
    postprocess_omml(OUT)

    z = zipfile.ZipFile(OUT)
    xml = z.read('word/document.xml').decode('utf-8')
    media = [n for n in z.namelist() if n.startswith('word/media/')]
    text = re.sub(r'<[^>]+>', '', xml)
    print('equations:', xml.count('<m:oMath>') + xml.count('<m:oMathPara>'))
    print('tables:', xml.count('<w:tbl>'))
    print('embedded images:', len(media), f'(期望 {len(FIGS)} 图)')
    print('stray $:', text.count('$'))
    print('size: %.1f KB' % (os.path.getsize(OUT) / 1024))
    os.remove(REF)
    print('done')
