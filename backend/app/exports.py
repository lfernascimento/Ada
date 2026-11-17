"""Exportação de uma resposta existente, sem executar seu conteúdo."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from .db import connect

router = APIRouter()

@router.get('/agents/{agent_id}/conversations/{cid}/export')
def export_last_response(agent_id: int, cid: int, format: str='md'):
    if format not in ('md','docx','pdf','pptx','xlsx'): raise HTTPException(400,'Formato não suportado.')
    with connect() as c:
        row=c.execute("SELECT m.content FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.id=? AND c.agent_id=? AND m.role='assistant' ORDER BY m.id DESC LIMIT 1",(cid,agent_id)).fetchone()
    if not row: raise HTTPException(404,'Nenhuma resposta encontrada.')
    content=row['content']
    if format=='md': data=content.encode('utf-8'); mime='text/markdown; charset=utf-8'
    elif format=='docx':
        from docx import Document
        doc=Document(); doc.add_heading('Documento de produto',0)
        for line in content.splitlines():
            if line.startswith('# '): doc.add_heading(line[2:],level=1)
            elif line.startswith('## '): doc.add_heading(line[3:],level=2)
            elif line.startswith('### '): doc.add_heading(line[4:],level=3)
            else: doc.add_paragraph(line)
        out=BytesIO(); doc.save(out); data=out.getvalue(); mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    elif format=='xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment
        wb=Workbook(); ws=wb.active; ws.title='Conteúdo'; ws.append(['Seção','Conteúdo'])
        section='Documento'
        for line in content.splitlines():
            if line.lstrip().startswith('#'): section=line.lstrip('# ').strip() or section
            elif line.strip():
                safe = lambda value: ("'" + value) if value.lstrip().startswith(('=','+','-','@')) else value
                ws.append([safe(section),safe(line)])
        ws.column_dimensions['A'].width=28; ws.column_dimensions['B'].width=80
        for cell in ws[1]: cell.font=Font(bold=True)
        for row in ws.iter_rows(min_row=2):
            for cell in row: cell.alignment=Alignment(wrap_text=True,vertical='top')
        out=BytesIO(); wb.save(out); data=out.getvalue(); mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif format=='pptx':
        from pptx import Presentation
        from pptx.util import Pt
        prs=Presentation(); chunks=[]; section='Documento de produto'; lines=[]
        for line in content.splitlines():
            if line.startswith('#'):
                if lines: chunks.append((section,lines)); lines=[]
                section=line.lstrip('# ').strip() or section
            elif line.strip(): lines.append(line.strip())
        if lines: chunks.append((section,lines))
        if not chunks: chunks=[('Documento de produto',[content])]
        for section,lines in chunks:
            for start in range(0,len(lines),6):
                slide=prs.slides.add_slide(prs.slide_layouts[1]); slide.shapes.title.text=section[:120]
                tf=slide.placeholders[1].text_frame; tf.clear()
                for i,line in enumerate(lines[start:start+6]):
                    para=tf.paragraphs[0] if i==0 else tf.add_paragraph(); para.text=line[:350];para.font.size=Pt(18)
        out=BytesIO(); prs.save(out); data=out.getvalue(); mime='application/vnd.openxmlformats-officedocument.presentationml.presentation'
    else:
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.enums import TA_LEFT
        font=Path('C:/Windows/Fonts/arial.ttf')
        if font.is_file():
            pdfmetrics.registerFont(TTFont('AdaExport',str(font))); face='AdaExport'
        else: face='Helvetica'
        styles=getSampleStyleSheet(); style=styles['BodyText']; style.fontName=face; style.fontSize=10; style.leading=14; style.alignment=TA_LEFT
        title=styles['Title']; title.fontName=face
        out=BytesIO(); story=[Paragraph('Documento de produto',title),Spacer(1,12)]
        for line in content.splitlines():
            story.append(Paragraph(escape(line) if line else '&#160;',style)); story.append(Spacer(1,3))
        SimpleDocTemplate(out).build(story); data=out.getvalue(); mime='application/pdf'
    return Response(content=data,media_type=mime,headers={'Content-Disposition':f'attachment; filename="ada-documento.{format}"','X-Content-Type-Options':'nosniff'})
