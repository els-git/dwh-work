const { chromium } = require('playwright');
(async () => { const [, , inp, out, mode] = process.argv; const b = await chromium.launch(); const p = await b.newPage();
  await p.goto('file://' + inp); await p.waitForTimeout(800);
  if (mode === 'slides') await p.pdf({ path: out, width: '1280px', height: '720px', printBackground: true, margin: { top: 0, bottom: 0, left: 0, right: 0 } });
  else await p.pdf({ path: out, format: 'A4', printBackground: true, displayHeaderFooter: true, headerTemplate: '<span></span>',
    footerTemplate: '<div style="font-size:8px;width:100%;text-align:center;color:#888">DWH — техническое описание · стр. <span class="pageNumber"></span> из <span class="totalPages"></span></div>',
    margin: { top: '14mm', bottom: '16mm', left: '14mm', right: '14mm' } });
  await b.close(); })();
