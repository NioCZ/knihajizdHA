import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = process.argv[2];
const outputDirectory = process.argv[3];
const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const overview = await workbook.inspect({
  kind: "workbook,sheet,table",
  maxChars: 8000,
  tableMaxRows: 6,
  tableMaxCols: 24,
  tableMaxCellChars: 100,
});
console.log(overview.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 50 },
  summary: "formula error scan",
});
console.log(errors.ndjson);

await fs.mkdir(outputDirectory, { recursive: true });
for (const sheetName of ["Kniha jízd", "Raw data"]) {
  const preview = await workbook.render({
    sheetName,
    autoCrop: "all",
    scale: 1.5,
    format: "png",
  });
  const safeName = sheetName === "Kniha jízd" ? "summary" : "raw";
  await fs.writeFile(
    `${outputDirectory}/${safeName}.png`,
    new Uint8Array(await preview.arrayBuffer()),
  );
}
