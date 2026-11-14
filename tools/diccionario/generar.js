// Genera Muevo_Diccionario_de_Datos.docx a partir de build/esquema.json, dominios.json y descripciones.json.
// Lo ejecuta build.py; no se corre a mano.
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  HeadingLevel, AlignmentType, WidthType, ShadingType, BorderStyle,
  PageBreak, VerticalAlign,
} = require("docx");

const [, , outPath] = process.argv;
const AQUI = __dirname;
const schema = JSON.parse(fs.readFileSync(path.join(AQUI, "build", "esquema.json"), "utf-8"));
const domains = JSON.parse(fs.readFileSync(path.join(AQUI, "dominios.json"), "utf-8"));
const descriptions = JSON.parse(fs.readFileSync(path.join(AQUI, "descripciones.json"), "utf-8"));

const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const hoy = new Date();

const tableByName = {};
for (const t of schema.tables) tableByName[t.table] = t;

const orderedTables = [];
for (const d of domains) for (const tname of d.tables) orderedTables.push({ domain: d.name, tname });
orderedTables.forEach((o, i) => { o.num = i + 1; });

const DARK = "1A1A1A";
const BORDER_GRAY = "CCCCCC";
const FONT = "Arial";

function thinBorders() {
  const b = { style: BorderStyle.SINGLE, size: 1, color: BORDER_GRAY };
  return { top: b, bottom: b, left: b, right: b };
}

function headerCell(text, width, align = AlignmentType.LEFT) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    shading: { type: ShadingType.CLEAR, fill: DARK, color: "auto" },
    borders: thinBorders(),
    verticalAlign: VerticalAlign.CENTER,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: [new Paragraph({ alignment: align, children: [new TextRun({ text, bold: true, color: "FFFFFF", size: 18, font: FONT })] })],
  });
}

function bodyCell(text, width, align = AlignmentType.LEFT, opts = {}) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    borders: thinBorders(),
    verticalAlign: VerticalAlign.CENTER,
    margins: { top: 40, bottom: 40, left: 100, right: 100 },
    children: [new Paragraph({
      alignment: align,
      children: [new TextRun({ text: text == null ? "" : String(text), size: 17, font: FONT, color: opts.color || "1A1A1A", bold: !!opts.bold })],
    })],
  });
}

const SUMMARY_WIDTHS = [500, 2800, 3500, 1300, 1260];
function buildSummaryTable() {
  const rows = [new TableRow({
    tableHeader: true,
    children: [
      headerCell("#", SUMMARY_WIDTHS[0], AlignmentType.CENTER), headerCell("Tabla", SUMMARY_WIDTHS[1]),
      headerCell("Dominio", SUMMARY_WIDTHS[2]), headerCell("Columnas", SUMMARY_WIDTHS[3], AlignmentType.CENTER),
      headerCell("Clase", SUMMARY_WIDTHS[4]),
    ],
  })];
  for (const o of orderedTables) {
    const t = tableByName[o.tname];
    rows.push(new TableRow({
      children: [
        bodyCell(o.num, SUMMARY_WIDTHS[0], AlignmentType.CENTER), bodyCell(t.table, SUMMARY_WIDTHS[1]),
        bodyCell(o.domain, SUMMARY_WIDTHS[2]), bodyCell(t.num_cols, SUMMARY_WIDTHS[3], AlignmentType.CENTER),
        bodyCell(t.class, SUMMARY_WIDTHS[4]),
      ],
    }));
  }
  return new Table({ width: { size: 9360, type: WidthType.DXA }, columnWidths: SUMMARY_WIDTHS, rows });
}

const COL_WIDTHS = [1700, 1100, 700, 700, 700, 4460];
function buildColumnsTable(t) {
  const rows = [new TableRow({
    tableHeader: true,
    children: [
      headerCell("Columna", COL_WIDTHS[0]), headerCell("Tipo", COL_WIDTHS[1]),
      headerCell("PK", COL_WIDTHS[2], AlignmentType.CENTER), headerCell("FK", COL_WIDTHS[3], AlignmentType.CENTER),
      headerCell("Null", COL_WIDTHS[4], AlignmentType.CENTER), headerCell("Notas", COL_WIDTHS[5]),
    ],
  })];
  for (const c of t.cols) {
    rows.push(new TableRow({
      children: [
        bodyCell(c.name, COL_WIDTHS[0], AlignmentType.LEFT, { bold: true }), bodyCell(c.type, COL_WIDTHS[1]),
        bodyCell(c.pk ? "✓" : "", COL_WIDTHS[2], AlignmentType.CENTER), bodyCell(c.fk ? "✓" : "", COL_WIDTHS[3], AlignmentType.CENTER),
        bodyCell(c.nullable ? "Sí" : "No", COL_WIDTHS[4], AlignmentType.CENTER),
        bodyCell(c.notes, COL_WIDTHS[5], AlignmentType.LEFT, { color: "444444" }),
      ],
    }));
  }
  return new Table({ width: { size: 9360, type: WidthType.DXA }, columnWidths: COL_WIDTHS, rows });
}

const children = [];
children.push(
  new Paragraph({ spacing: { before: 2400 }, children: [new TextRun({ text: "Muevo", bold: true, size: 56, font: FONT, color: DARK })] }),
  new Paragraph({ spacing: { before: 200, after: 400 }, children: [new TextRun({ text: "Diccionario de datos", size: 36, font: FONT, color: "444444" })] }),
  new Paragraph({ spacing: { after: 100 }, children: [new TextRun({ text: "Backend B2B2B — PostgreSQL · SQLAlchemy · Alembic", size: 22, font: FONT, color: "666666" })] }),
  new Paragraph({ spacing: { after: 100 }, children: [new TextRun({ text: `${schema.num_tables} tablas en ${domains.length} dominios — generado desde los modelos (models.py)`, size: 18, font: FONT, color: "666666" })] }),
  new Paragraph({ spacing: { after: 100 }, children: [new TextRun({ text: `Actualizado: ${MESES[hoy.getMonth()]} ${hoy.getFullYear()}`, italics: true, size: 18, font: FONT, color: "888888" })] }),
  new Paragraph({ children: [new PageBreak()] }),
  new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { after: 200, before: 200 }, children: [new TextRun({ text: "Resumen de tablas", font: FONT })] }),
  buildSummaryTable(),
  new Paragraph({ children: [new PageBreak()] }),
);

domains.forEach((d, dIdx) => {
  children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { after: 200, before: 200 }, children: [new TextRun({ text: d.name, font: FONT })] }));
  for (const tname of d.tables) {
    const t = tableByName[tname];
    const o = orderedTables.find(x => x.tname === tname);
    children.push(
      new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 300, after: 80 }, children: [new TextRun({ text: `${o.num}. ${t.table}`, font: FONT })] }),
      new Paragraph({ spacing: { after: 60 }, children: [new TextRun({ text: `Clase SQLAlchemy: ${t.class}`, italics: true, color: "666666", size: 18, font: FONT })] }),
      new Paragraph({ spacing: { after: 160 }, children: [new TextRun({ text: descriptions[t.table], color: "444444", size: 19, font: FONT })] }),
      buildColumnsTable(t),
    );
  }
  if (dIdx < domains.length - 1) children.push(new Paragraph({ children: [new PageBreak()] }));
});

const doc = new Document({
  sections: [{ properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } }, children }],
  styles: {
    default: { document: { run: { font: FONT, size: 20, color: "1A1A1A" } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 30, bold: true, color: DARK, font: FONT }, paragraph: { spacing: { before: 200, after: 200 } } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 24, bold: true, color: DARK, font: FONT }, paragraph: { spacing: { before: 240, after: 80 } } },
    ],
  },
});

Packer.toBuffer(doc).then(buf => { fs.writeFileSync(outPath, buf); console.log("Escrito:", outPath, buf.length, "bytes"); });
