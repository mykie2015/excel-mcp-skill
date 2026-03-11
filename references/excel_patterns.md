# Excel Formula and Modeling Patterns

## Lookup Patterns

### VLOOKUP (legacy / quick lookup)

Use when the lookup key is in the first column of a table range.

```excel
=VLOOKUP(A2,$F$2:$H$100,3,FALSE)
```

- `A2`: lookup value
- `$F$2:$H$100`: table array (key must be in first column)
- `3`: return column index inside the table array
- `FALSE`: exact match (recommended for IDs/codes)

Common safe variant with fallback:

```excel
=IFERROR(VLOOKUP(A2,$F$2:$H$100,3,FALSE),"Not found")
```

### INDEX/MATCH (more flexible than VLOOKUP)

Use when lookup column is not the first column, or when you want safer model evolution.

```excel
=INDEX($H$2:$H$100,MATCH(A2,$F$2:$F$100,0))
```

- `MATCH(...,0)` forces exact match
- Works for left or right lookups

Two-way lookup (row + column):

```excel
=INDEX($B$2:$G$100,MATCH($J2,$A$2:$A$100,0),MATCH(K$1,$B$1:$G$1,0))
```

## Conditional Aggregation

### SUMIFS

Use for multi-criteria sums.

```excel
=SUMIFS($E:$E,$A:$A,$H2,$B:$B,">="&$I$1,$B:$B,"<="&$J$1)
```

Example criteria pattern:
- Sum Amount in `E:E`
- Where Region in `A:A` equals `H2`
- Where Date in `B:B` is between start `I1` and end `J1`

Related patterns:

```excel
=COUNTIFS($A:$A,$H2,$C:$C,"Closed")
=AVERAGEIFS($E:$E,$A:$A,$H2,$C:$C,"Closed")
```

## Conditional Formatting Rules

Apply from `Home -> Conditional Formatting -> New Rule -> Use a formula`.

### Highlight duplicates in column A

```excel
=COUNTIF($A:$A,$A2)>1
```

### Flag overdue tasks (Due Date in D, Status in E)

```excel
=AND($D2<TODAY(),$E2<>"Done")
```

### Band rows by group change (group key in A)

```excel
=$A2<>$A1
```

### Highlight top 10% values in C

```excel
=$C2>=PERCENTILE.INC($C:$C,0.9)
```

## Chart Type Selection Guidance

Choose chart type by analytical intent:

- Trend over time: `Line` (or `Area` for cumulative emphasis)
- Compare categories: `Clustered Column` or `Bar` (bar is better for long labels)
- Part-to-whole at one point: `Pie/Donut` only for small category counts; otherwise `Stacked Bar`
- Composition over time: `Stacked Area` or `100% Stacked Column`
- Relationship/correlation: `Scatter` (add trendline when useful)
- Distribution: `Histogram` (or box plot if available)
- Actual vs target: `Combo` (columns for actuals + line for target)

Practical rules:
- Avoid 3D charts for analytical work
- Keep category count manageable (often <= 12 for readability)
- Start y-axis at zero for bar/column comparisons unless there is a strong reason not to

## Data Validation Patterns

Create via `Data -> Data Validation`.

### Dropdown list from range

- Source: `=$M$2:$M$20`
- Use named ranges for easier maintenance (example: `=StatusList`)

### Cascading dropdown (dependent list)

Parent in `A2`, child validation source in `B2`:

```excel
=INDIRECT($A2)
```

Requires named ranges that match parent values.

### Restrict to whole numbers in range

- Allow: `Whole number`
- Data: `between`
- Min: `1`
- Max: `100`

### Restrict to valid date window

- Allow: `Date`
- Start: `=DATE(2025,1,1)`
- End: `=DATE(2026,12,31)`

### Custom rule: unique ID in column A

Apply to `A2:A1000` with formula:

```excel
=COUNTIF($A:$A,A2)=1
```

## Cross-Workbook Reference Syntax

### Basic reference (workbook open or closed)

```excel
=[Sales.xlsx]Summary!$B$2
```

### External reference with full path (closed workbook)

```excel
='C:\Reports\[Sales.xlsx]Summary'!$B$2
```

### Reference to a range in another workbook

```excel
=SUM('[C:\Reports\Q1\Sales.xlsx]Summary'!$B$2:$B$100)
```

### Cross-workbook INDEX/MATCH

```excel
=INDEX('[C:\Reports\Master.xlsx]DimProduct'!$D:$D,
       MATCH(A2,'[C:\Reports\Master.xlsx]DimProduct'!$A:$A,0))
```

Notes:
- Keep external files in stable locations to avoid broken links
- Prefer absolute references for external ranges (`$A:$A`, `$B$2:$B$100`)
- If links break after file moves, use `Data -> Edit Links` to repoint sources
