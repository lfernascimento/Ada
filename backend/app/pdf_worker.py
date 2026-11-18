"""PDF extraction worker; no API keys or database access."""
import sys,json
if __name__=='__main__':
    try:
        if sys.platform!='win32':
            import resource
            resource.setrlimit(resource.RLIMIT_AS,(384*1024*1024,384*1024*1024))
            resource.setrlimit(resource.RLIMIT_CPU,(10,10))
        from pypdf import PdfReader
        from io import BytesIO
        reader=PdfReader(BytesIO(sys.stdin.buffer.read(5_000_001)))
        if len(reader.pages)>100:raise ValueError('too many pages')
        parts=[];total=0
        for page in reader.pages:
            text=page.extract_text() or '';total+=len(text)
            if total>2_000_000:raise ValueError('too much text')
            parts.append(text)
        sys.stdout.write(json.dumps({'text':'\n'.join(parts)}))
    except Exception:raise SystemExit(1)
