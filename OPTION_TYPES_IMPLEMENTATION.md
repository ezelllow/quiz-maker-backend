# Option Types Implementation Guide

## Overview

The quiz maker now supports three different option formats:

1. **TEXT** - Traditional multiple choice with text answers (A, B, C, D)
2. **TABLE** - Options formatted as a table with rows and columns
3. **IMAGE** - All options shown in a single diagram image

## How It Works

### 1. Google Sheet Setup

Add a **prefix** to the `Options` column to specify the option type:

#### TEXT OPTIONS (Default)
No prefix needed. Format as normal:
```
A) Option A text
B) Option B text
C) Option C text
D) Option D text
```

#### TABLE OPTIONS
Prefix with `TABLE:` and use pipe `|` separators for columns:
```
TABLE:
Property | Value
A) mass | no change
B) increases | no change
C) no change | decreases
D) no change | increases
```

#### IMAGE OPTIONS
Prefix with `IMAGE:` (the image will be in the Diagram column):
```
IMAGE:
```

### 2. Backend Processing

When questions are loaded, the backend:

1. **Detects option type** by checking for TEXT/TABLE/IMAGE prefixes
2. **Parses the format** appropriately:
   - **TEXT**: Splits by newlines, extracts letter and text
   - **TABLE**: Parses headers and rows, maps values to columns
   - **IMAGE**: Marks that the image should be displayed as options
3. **Returns structured data** in the API response with:
   - `option_type`: "TEXT", "TABLE", or "IMAGE"
   - `table_headers`: (for TABLE only) Column headers
   - `table_rows`: (for TABLE only) Row data with values per column

### 3. Frontend Rendering

The frontend automatically renders based on option_type:

#### TEXT Options
Displays as radio button list with text:
```
○ A) Option A text
○ B) Option B text
○ C) Option C text
○ D) Option D text
```

#### TABLE Options
Displays as an HTML table with selectable rows:
```
┌──────────┬──────────────┬────────┐
│ Option   │ Property     │ Value  │
├──────────┼──────────────┼────────┤
│ ○ A)     │ mass         │ no...  │
│ ○ B)     │ increases    │ no...  │
│ ○ C)     │ no change    │ decr.. │
│ ○ D)     │ no change    │ incr.. │
└──────────┴──────────────┴────────┘
```

#### IMAGE Options
Displays the diagram image with A/B/C/D radio buttons below:
```
┌─────────────────────────┐
│                         │
│   [Diagram Image]       │
│                         │
└─────────────────────────┘
○ A) Option A
○ B) Option B
○ C) Option C
○ D) Option D
```

## Setup Images

If a question has a **setup image** (not an option image), it will be displayed above the options if:
- The image exists in the Diagram column
- The option type is NOT "IMAGE" (because IMAGE type uses the diagram as the options)

So the hierarchy is:
1. If IMAGE type → Diagram shows as options (no setup image shown)
2. If TEXT or TABLE type → Diagram shows as setup image above options

## Step-by-Step Setup Instructions

### Step 1: Update Your Google Sheet

Go through each question and add the appropriate prefix to the `Options` column:

1. **Most questions**: Leave as-is (TEXT format)
   ```
   A) Option A
   B) Option B
   C) Option C
   D) Option D
   ```

2. **Table-format questions**: Add `TABLE:` prefix
   ```
   TABLE:
   Header1 | Header2 | Header3
   A) val1 | val2 | val3
   B) val4 | val5 | val6
   ...
   ```

3. **Image-option questions**: Replace options text with just `IMAGE:`
   ```
   IMAGE:
   ```

### Step 2: Verify Backend Changes

The backend has been updated with:
- `parse_option_type()` function to detect and parse formats
- New fields in Question model: `option_type`, `table_headers`, `table_rows`
- Automatic parsing during question loading

No additional setup needed - it happens automatically!

### Step 3: Verify Frontend Changes

The frontend has been updated with:
- `renderOptions()` function that handles all three types
- CSS styles for table and image options
- Conditional rendering based on `option_type`

No additional setup needed - it happens automatically!

### Step 4: Test the Quiz

1. Start the backend:
   ```bash
   cd C:\School\quizMaker
   python quiz_backend.py
   ```

2. Start the frontend:
   ```bash
   cd C:\School\quiz-maker-frontend
   npm run dev
   ```

3. Create a quiz and verify:
   - TEXT questions render with radio buttons
   - TABLE questions render as HTML tables
   - IMAGE questions render with image and buttons

## Example From Your Sheet

### TEXT Example (keep as-is)
```
Row: PHY-ACSBR2019-P1-4E5N-003-
Options: A) moving with non-uniform acceleration and then stops
         B) moving with non-uniform acceleration and then with uniform speed
         C) moving with uniform acceleration and then stops
         D) moving with uniform acceleration and then with uniform speed
```
→ Renders as radio buttons with text

### TABLE Example (add TABLE: prefix)
```
Row: PHY-ACSBR2019-P1-4E5N-005-
Current: A mass | no change
         B increases | no change
         C no change | decreases
         D no change | increases

Update to:
TABLE:
Property | Effect
A) mass | no change
B) increases | no change
C) no change | decreases
D) no change | increases
```
→ Renders as selectable table rows

### IMAGE Example (change to IMAGE:)
```
Row: PHY-ACSBR2019-P1-4E5N-002-
Current: A) [describes first diagram]
         B) [describes second diagram]
         C) [describes third diagram]
         D) [describes fourth diagram]

Update to:
IMAGE:

Diagram: [URL or file ID of image showing all 4 options]
```
→ Renders as image with A/B/C/D buttons

## Troubleshooting

### Table not rendering
- Check that `TABLE:` prefix is present
- Verify columns are separated by `|` (pipes)
- Ensure first line after TABLE: has headers
- Ensure each data row starts with A), B), C), or D)

### Image not showing
- Check that Diagram column has a URL or file ID
- Verify Option type is set to `IMAGE:`
- Check image URL is accessible
- Try refreshing the browser

### Answer validation
- For TABLE options, make sure answer is just the letter (A, B, C, or D)
- For IMAGE options, answer should be the letter (A, B, C, or D)
- For TEXT options, answer should match the option letter

## Next Steps

1. **Update your Google Sheet** with option type markers
2. **Test with a few questions** of each type
3. **Run the full quiz** to verify rendering
4. **Adjust as needed** - the system is flexible!

---

**Questions or issues?** Check the console output in both backend and frontend for error messages. The backend logs which questions are loaded and their types.
