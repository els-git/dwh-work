# Сборка PDF обзорных документов

Нужны Python 3 с пакетом `markdown` и Node.js с Playwright (Chromium).

```bash
python3 docs/overview/build/md2pdf.py docs/overview/DWH_TECHNICAL_OVERVIEW.md /tmp/tech.html
node docs/overview/build/pdf.cjs /tmp/tech.html "$PWD/docs/overview/DWH_TECHNICAL_OVERVIEW.pdf"
node docs/overview/build/pdf.cjs "$PWD/docs/overview/DWH_BUSINESS_PRESENTATION.html" "$PWD/docs/overview/DWH_BUSINESS_PRESENTATION.pdf" slides
```

Снимки экранов в `img/` сняты с интерфейса выпуска `2026.11.88` на демонстрационных (вымышленных) данных; реальных данных и паролей в них нет.
