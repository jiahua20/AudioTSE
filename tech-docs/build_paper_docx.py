# -*- coding: utf-8 -*-
"""构建《AudioTSE技术论文-声纹门控与TSE.docx》的完整流水线：

  1) svg/*.svg   -> png/*.png           （流程图，见 svg2png.py）
  2) md 代码围栏 -> png/code-*.png      （截图式代码图：红框 + 编号注释，见 code_screens.py），
     并产出 _build_paper.md（围栏替换为图片引用）
  3) pandoc _build_paper.md -> docx     （自定义字体模板：宋体/黑体/Times New Roman + 页码）

用法：python build_paper_docx.py
改图：用浏览器或 draw.io 打开 svg/*.svg 编辑后重跑；改文字：编辑 .md 后重跑。
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
import code_screens

TECH = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(TECH, 'AudioTSE技术论文-声纹门控与TSE.docx')
REF = os.path.join(TECH, '_reference.docx')


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
    set_style(doc, 'Title', 'Times New Roman', '黑体', size=18, bold=True, black=True)
    doc.styles['Title'].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for s in ('Author', 'Date'):
        try:
            set_style(doc, s, 'Times New Roman', '宋体', size=12, black=True)
            doc.styles[s].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        except KeyError:
            pass
    set_style(doc, 'Heading 1', 'Times New Roman', '黑体', size=15, bold=True, black=True)
    set_style(doc, 'Heading 2', 'Times New Roman', '黑体', size=13, bold=True, black=True)
    set_style(doc, 'Heading 3', 'Times New Roman', '黑体', size=11.5, bold=True, black=True)
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
    """pandoc OMML 的 WPS 兼容性修补：
    1) 删掉数学文本里的零宽空格（pandoc 塞在隐藏上标槽里，WPS 屏幕排版
       不认 supHide，会为其保留整个字高——根号顶线因此悬空约一行）；
    2) supHide/subHide 为 on 的 nary，连隐藏槽元素一并清空（双保险）；
    3) m: 布尔属性 on/off 规范成 Word 标准的 1/0；
    4) settings.xml 补 m:mathPr（钉死 Cambria Math 数学字体）。
    """
    import zipfile
    zin = zipfile.ZipFile(docx_path)
    items = {n: zin.read(n) for n in zin.namelist()}
    zin.close()

    xml = items['word/document.xml'].decode('utf-8')
    zwsp = xml.count('​')
    xml = xml.replace('​', '')
    xml = xml.replace('m:val="on"', 'm:val="1"').replace('m:val="off"', 'm:val="0"')

    def fix_nary(mo):
        blk = mo.group(0)
        # 只删 nary 直接子级的槽（紧跟 </m:sub> 或 </m:naryPr>），
        # 不能删嵌套在 m:sub 内容里的 <m:sup>——式(11) 的下标 t′ 就在那里
        if '<m:supHide m:val="1"' in blk:
            blk = re.sub(r'(</m:sub>|</m:naryPr>)<m:sup>.*?</m:sup>', r'\1', blk, count=1, flags=re.S)
        if '<m:subHide m:val="1"' in blk:
            blk = re.sub(r'</m:naryPr><m:sub>.*?</m:sub>', '</m:naryPr>', blk, count=1, flags=re.S)
        return blk
    n_nary = len(re.findall(r'<m:nary>', xml))
    xml = re.sub(r'<m:nary>.*?</m:nary>', fix_nary, xml, flags=re.S)
    items['word/document.xml'] = xml.encode('utf-8')

    s = items['word/settings.xml'].decode('utf-8')
    if '<m:mathPr>' not in s:
        mathpr = ('<m:mathPr><m:mathFont m:val="Cambria Math"/><m:brkBin m:val="before"/>'
                  '<m:brkBinSub m:val="--"/><m:smallFrac m:val="0"/><m:dispDef/>'
                  '<m:lMargin m:val="0"/><m:rMargin m:val="0"/><m:defJc m:val="centerGroup"/>'
                  '<m:wrapIndent m:val="1440"/><m:intLim m:val="subSup"/>'
                  '<m:naryLim m:val="undOvr"/></m:mathPr>')
        if 'xmlns:m=' not in s.split('>', 1)[0]:
            s = s.replace('<w:settings ', '<w:settings xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" ', 1)
        s = s.replace('</w:settings>', mathpr + '</w:settings>')
        items['word/settings.xml'] = s.encode('utf-8')

    with zipfile.ZipFile(docx_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n, data in items.items():
            zout.writestr(n, data)
    print(f'omml postprocess: removed {zwsp} zero-width spaces, fixed nary slots '
          f'({n_nary} nary), mathPr ensured')


if __name__ == '__main__':
    for src, dst, w, h in svg2png.render_all():
        print(f'figure: {src} -> png/{dst} ({w}x{h}px)')
    code_outs, tmp_md = code_screens.render_all()
    for i, j, n, name, w, h in code_outs:
        tag = f' {j}/{n}' if n > 1 else ''
        print(f'code screenshot {i}{tag}: png/{name} ({w}x{h}px)')

    build_reference()
    pypandoc.convert_file(tmp_md, 'docx', format='markdown', outputfile=OUT,
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
    print('embedded images:', len(media), f'(期望 {5 + len(code_outs)} = 5 图 + {len(code_outs)} 代码截图)')
    print('stray $:', text.count('$'))
    print('size: %.1f KB' % (os.path.getsize(OUT) / 1024))
    os.remove(REF)
    os.remove(tmp_md)
    print('done')
