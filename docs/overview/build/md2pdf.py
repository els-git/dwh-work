import sys, markdown, pathlib
src, out_html = sys.argv[1], sys.argv[2]
import re
def prep(text):
    out, prev, in_code = [], '', False
    for line in text.split('\n'):
        if line.startswith('```'): in_code = not in_code
        if not in_code:
            m = re.match(r'^( {1,3})(\S.*)$', line)
            if m: line = '    ' + m.group(2)
            is_item = re.match(r'^\s*([-*]|\d+\.) ', line)
            prev_item = re.match(r'^\s*([-*]|\d+\.) ', prev)
            if is_item and prev.strip() and not prev_item: out.append('')
            if line.startswith('**') and prev.startswith('**'): out.append('')
        out.append(line); prev = line
    return '\n'.join(out)
body = markdown.markdown(prep(pathlib.Path(src).read_text(encoding='utf-8')), extensions=['tables', 'fenced_code', 'sane_lists'])
css = """
@page { size: A4; margin: 16mm 15mm 16mm 15mm; }
body { font-family: 'DejaVu Sans', Arial, sans-serif; font-size: 10pt; line-height: 1.45; color: #1d2433; }
h1 { font-size: 21pt; color: #0b1b2e; border-bottom: 3px solid #e8892f; padding-bottom: 6px; margin: 0 0 10px; }
h2 { font-size: 14pt; color: #0b1b2e; margin: 20px 0 8px; border-bottom: 1px solid #d6dbe3; padding-bottom: 3px; page-break-after: avoid; }
h3 { font-size: 11.5pt; color: #b2601b; margin: 14px 0 6px; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 8.8pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th { background: #0f2238; color: #fff; text-align: left; padding: 5px 6px; }
td { border-bottom: 1px solid #dde2ea; padding: 4px 6px; vertical-align: top; }
tr:nth-child(even) td { background: #f5f7fa; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 8.6pt; background: #eef1f5; padding: 0 3px; border-radius: 3px; }
pre { background: #0f2238; color: #e6edf5; padding: 10px 12px; border-radius: 6px; font-size: 7.8pt; line-height: 1.35; page-break-inside: avoid; }
pre code { background: none; color: inherit; padding: 0; }
hr { border: 0; border-top: 1px solid #d6dbe3; margin: 10px 0; }
li { margin: 2px 0; }
"""
pathlib.Path(out_html).write_text(f"<!doctype html><html lang=ru><head><meta charset=utf-8><style>{css}</style></head><body>{body}</body></html>", encoding='utf-8')
