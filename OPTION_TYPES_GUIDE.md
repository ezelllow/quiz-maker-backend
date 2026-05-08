# Option Types System

## How to Mark Different Option Types

Add a **prefix** to the `Options` column to specify how options should be rendered.

---

## **1. TEXT OPTIONS (Default)**
Standard multiple choice with text answers.

**Format:** No prefix needed (or start with `TEXT:`)

```
A) Option A text
B) Option B text  
C) Option C text
D) Option D text
```

**Example from your sheet:**
```
A moving with non-uniform acceleration and then stops
B moving with non-uniform acceleration and then with uniform speed
C moving with uniform acceleration and then stops
D moving with uniform acceleration and then with uniform speed
```

---

## **2. IMAGE OPTIONS**
All options (A, B, C, D) are shown as one diagram image.

**Format:** Start with `IMAGE:`

```
IMAGE:
```

**In the sheet:**
- `Options` column: `IMAGE:`
- `Diagram` column: The image URL (same as for regular diagrams)
- The quiz will display the image, and students select A/B/C/D with buttons below

**Example:** PHY-ACSBR2019-P1-4E5N-002- (the pendulum diagrams row)
```
Options: IMAGE:
Diagram: [Image showing all 4 pendulum positions]
```

---

## **3. TABLE OPTIONS**
Options formatted as a table with rows and columns.

**Format:** Start with `TABLE:` then format as tab-separated or pipe-separated

```
TABLE:
Header1 | Header2 | Header3
A) Row1Val1 | Row1Val2 | Row1Val3
B) Row2Val1 | Row2Val2 | Row2Val3
C) Row3Val1 | Row3Val2 | Row3Val3
D) Row4Val1 | Row4Val2 | Row4Val3
```

**Example:** PHY-ACSBR2019-P1-4E5N-005- (mass and weight table)

Current format in sheet:
```
A mass    | no change
B increases | no change
C no change | decreases
D no change | increases
```

Should be formatted as:
```
TABLE:
Property | Effect
A) mass | no change
B) increases | no change
C) no change | decreases
D) no change | increases
```

Or with better headers:
```
TABLE:
 | mass | weight
A) decreases | no change
B) increases | no change
C) no change | decreases
D) no change | increases
```

---

## **Implementation Steps**

1. **Go through your Questions sheet**
2. **Identify option types:**
   - Most are TEXT (keep as-is)
   - Some are IMAGE (mark with `IMAGE:`)
   - Some are TABLE (reformat with `TABLE:` prefix)

3. **Update the `Options` column:**
   - Leave text-based questions unchanged
   - For image options: Delete the text, replace with `IMAGE:`
   - For table options: Reformat with `TABLE:` prefix and proper column structure

4. **No changes needed to:**
   - `Diagram` column
   - `Answer` column
   - Any other columns

---

## **Examples**

### TEXT Option (Current - Keep as-is)
```
Row: PHY-ACSBR2019-P1-4E5N-003-
Options: A moving with non-uniform acceleration and then stops
         B moving with non-uniform acceleration and then with uniform speed
         C moving with uniform acceleration and then stops
         D moving with uniform acceleration and then with uniform speed
```

### IMAGE Option (Update to)
```
Row: PHY-ACSBR2019-P1-4E5N-002-
Options: IMAGE:
Diagram: [pendulum diagram showing all 4 positions]
```

### TABLE Option (Update to)
```
Row: PHY-ACSBR2019-P1-4E5N-005-
Options: TABLE:
         | mass | weight
         A) decreases | no change
         B) increases | no change
         C) no change | decreases
         D) no change | increases
```

---

## **Benefits**

✅ Clear marking system  
✅ Easy for backend to parse  
✅ Frontend renders appropriately  
✅ Maintains compatibility with text questions  
✅ No new columns needed  

---

## **Next Steps**

1. Update your sheet with these markers
2. I'll update the backend to parse these formats
3. I'll update the frontend to render them properly
4. Test the quiz!

---

## **Questions?**

- How many questions have IMAGE options?
- How many have TABLE options?
- Which rows need updating?
