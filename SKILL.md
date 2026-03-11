---
name: excel-mcp-skill
description: Manage Excel workbooks with MCP-first workflows and Python fallback. Use when users ask to read sheets, update cells, apply formulas, format tables, validate workbook outputs, or say things like "edit this Excel file", "summarize this workbook", "fix sheet formatting", "merge tabs", "create a report in .xlsx", or "use openpyxl/pandas if MCP is unavailable".
---

# Excel MCP Skill

## When to use

Use this skill when a user needs reliable Excel workbook work across one or more `.xlsx` files, including:
- Reading workbook structure, sheet data, formulas, and styles
- Writing values, formulas, and derived tables
- Applying formatting (number/date formats, widths, filters, freeze panes, conditional formats)
- Producing repeatable outputs where MCP tools are preferred but Python fallback is required when MCP is unavailable or incomplete

## Instructions

1. Confirm task scope and output contract.
- Identify input files, target sheets/ranges, expected output path, and any constraints (preserve formulas, keep styles, no structural changes, etc.).
- Restate success criteria in concrete terms before editing.

2. Use MCP workflow first for workbook operations.
- Connect to the Excel-capable MCP server and inspect workbook metadata (sheet names, used ranges, headers, table boundaries).
- Read only the ranges needed for the requested task to reduce risk and runtime.
- Apply changes in small, ordered batches: write values/formulas, then apply formatting, then recalculate if supported.
- After each batch, re-read affected ranges to verify values and formulas landed correctly.

3. Apply formatting and structure updates through MCP.
- Standardize data presentation: header style, column widths, number/date formats, filters, and freeze panes.
- Preserve existing named ranges, formulas, and chart references unless the user asked to restructure.
- If adding computed columns, keep formulas consistent and verify fill-down behavior.

4. Validate workbook integrity before handoff.
- Re-open or re-read the output workbook and check: expected sheet count, key totals, formula cells, and formatting in critical ranges.
- Confirm there are no broken references introduced by edits.
- Report what changed (sheets, ranges, formula columns, formatting actions).

5. Use Python fallback when MCP is unavailable or insufficient.
- Use `openpyxl` for cell-level edits, formulas, styles, worksheet structure, and preserving workbook fidelity.
- Use `pandas` for tabular transforms (join, group, pivot, cleanup), then write results back with `openpyxl`-compatible workflows.
- Keep fallback flow deterministic:
  1) load workbook,
  2) copy to output path when appropriate,
  3) apply data edits,
  4) apply formatting,
  5) save,
  6) reopen and verify critical cells/ranges.

6. Choose tools by operation type.
- Prefer MCP for direct workbook interactions when available.
- Prefer `openpyxl` for style-sensitive workbook edits and formula-safe updates.
- Prefer `pandas` for data reshaping and analytics, then write curated outputs to workbook sheets.

7. Communicate execution decisions and limits.
- State whether MCP or Python fallback was used and why.
- Call out limitations explicitly (for example: macro-enabled `.xlsm` handling, external links, unsupported conditional formatting edge cases).
- Provide a concise verification summary with concrete cell/range checks.

## Examples

- "Open `sales-q1.xlsx`, update `Forecast!D2:D500` with a 7% uplift formula, and keep existing styles."
- "Read `pipeline.xlsx`, summarize totals by owner into a new sheet named `Summary`, and format currency columns."
- "Fix date formatting in `Operations` sheet to `yyyy-mm-dd`, auto-fit columns A:F, and freeze the header row."
- "Merge `North` and `South` tabs into `All_Regions` with pandas, then write the result back to the workbook."
- "If MCP fails, use openpyxl to write values to `Budget!B2:G20`, preserve formulas in row 21, and verify totals."

## Common issues

| Issue | Likely cause | Resolution |
|---|---|---|
| MCP cannot open workbook | Bad path, file lock, or unsupported extension | Verify absolute path, close workbook in other apps, convert to `.xlsx` if needed |
| Written values not visible | Wrong sheet/range or stale read after write | Re-check target range, re-read affected cells, and confirm write operation order |
| Formulas replaced by static values | Dataframe export overwrote formula cells | Preserve formula columns explicitly; use `openpyxl` writes for mixed formula/value regions |
| Styles lost after update | Full-sheet overwrite via pandas writer | Write only target ranges, then reapply styles with `openpyxl` or MCP formatting calls |
| Date/currency formatting inconsistent | Raw values written without number formats | Set explicit number formats after writing data and verify sample cells |
| Totals changed unexpectedly | Partial range updates or missing rows | Validate row counts before/after, recompute checksums/totals, and compare key control cells |
| Fallback script fails on import | Missing `openpyxl` or `pandas` | Install required packages and rerun the workflow |
| Output workbook corrupted | Interrupted save or incompatible writer flow | Save to a new file, reopen for integrity check, and avoid mixing incompatible writers in one pass |
