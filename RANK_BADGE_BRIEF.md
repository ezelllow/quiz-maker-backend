# Rank Badge Art Brief — HabitGo

Brief for generating the 9 custom rank badges. Hand the per-tier rows to your image
AI (Midjourney / DALL-E / etc.). The goal is a **consistent set**, not 9 separate
illustrations — the consistency rules below matter more than any single badge.

---

## Where the badges are used

One badge represents each O-Level band. It renders at five very different sizes
on a **dark background** (app background ≈ `#0B1020`):

| Location | Render size |
|----------|-------------|
| Top navbar badge | ~20 px |
| Daily Challenge intro pill | ~22 px |
| Dashboard rank panel | ~48 px |
| Settings rank card | ~56 px |
| Placement result screen | ~96 px |

So each badge must read clearly **both tiny and large**. Bold silhouette, few
elements, strong contrast.

---

## Hard technical specs (non-negotiable)

- **Format:** PNG, **transparent background**.
- **Size:** 512×512 px minimum, **square**, centred, with even padding.
- **No text.** No letters, no numbers, no tier names baked into the image.
- **Naming:** lowercase tier name, exactly:
  `beginner.png`, `apprentice.png`, `advanced.png`, `scholar.png`,
  `expert.png`, `elite.png`, `master.png`, `champion.png`, `legend.png`
- **Drop them in:** `C:\School\quiz-maker-frontend\public\ranks\`

---

## Consistency rules (the #1 risk with an AI set)

Apply ALL of these to every badge so the 9 look like one family:

1. **Same frame.** Every badge sits inside the *same* container shape — a **circular
   medallion with a metallic rim**. Only the rim material, the central motif, and the
   glow change between tiers. The frame itself does not.
2. **Same perspective.** Flat, front-facing, head-on. No 3/4 angles, no tilt.
3. **Same lighting.** Light source top-left, consistent across all 9.
4. **Same composition.** Motif centred, same relative size, same padding to the edge.
5. **One art style.** Clean vector-illustration look, soft cel shading, subtle depth.
   "Game rank-badge aesthetic." Pick the style once and keep it identical.
6. **Escalation is the story.** The set must visually climb from Beginner to Legend —
   materials get richer (stone → iron → bronze → silver → gold → radiant) and the
   glow intensifies. A glance at all 9 should read as a ladder.
7. **App palette for the glow.** Accent colours are electric blue `#5DA9FF`, cyan
   `#6EE7F9`, purple `#8B5CF6`. The high tiers' glow should lean into these.

---

## The 9 tiers

Generate one badge per row. The **rim**, **central motif**, **glow**, and **palette**
columns are what change; everything in "Consistency rules" stays fixed.

| # | Tier (filename) | Rim material | Central motif | Glow | Palette |
|---|-----------------|--------------|---------------|------|---------|
| F9 | **Beginner** (`beginner.png`) | rough grey stone | a small green sprout / seedling pushing up | none | muted slate + soft green |
| E8 | **Apprentice** (`apprentice.png`) | dull iron | a single spark or small flame | faint warm | iron grey + ember orange |
| D7 | **Advanced** (`advanced.png`) | bronze | an open book with rising sparks / an upward arrow above it | soft bronze | bronze + warm amber |
| C6 | **Scholar** (`scholar.png`) | polished brass | a laurel wreath framing a quill | gentle | brass + parchment cream |
| C5 | **Expert** (`expert.png`) | silver | a faceted glowing gem / crystal core | cool silver-blue | silver + soft cyan |
| B4 | **Elite** (`elite.png`) | blue-steel | a lightning bolt striking through a crystal | electric blue (`#5DA9FF`) | steel + electric blue |
| B3 | **Master** (`master.png`) | gold-edged steel | an ornate heraldic crest / shield with crossed elements | blue-gold | deep blue + gold |
| A2 | **Champion** (`champion.png`) | gold | a winged trophy or star crest with laurels | warm gold radiance | gold + white highlights |
| A1 | **Legend** (`legend.png`) | radiant gold with gem inlays | a radiant crown with a halo / aura, small orbiting stars | full radiant aura blending cyan + purple | brilliant gold + cyan/purple aura |

---

## Copy-paste prompt template

Fill the `[BRACKETS]` from the row above, one badge at a time:

> A **[TIER]**-rank badge for an educational app. A circular medallion emblem with a
> **[RIM MATERIAL]** metallic rim, centred on a fully transparent background. Central
> motif: **[CENTRAL MOTIF]**. **[GLOW]** glow. Colour palette: **[PALETTE]**. Flat,
> front-facing view, light source top-left. Clean vector-illustration style with soft
> cel shading and subtle depth — game rank-badge aesthetic. No text, no letters, no
> numbers. Square composition, even padding, transparent PNG. Part of a consistent
> 9-tier badge set.

Generate all 9 in one session, in tier order, so the model holds the style. If your
tool supports it, generate Beginner first, then reference it for the rest to lock the
frame and style.

---

## What happens after you drop the files in

Once the 9 PNGs are in `public/ranks/`, the integration is small and I'll handle it:

1. `RANK_TIER_ICONS` in `quiz_backend.py` switches from emoji strings to
   `/ranks/<tier>.png` paths (still one dict, still the single source of truth).
2. The five display spots switch from rendering `tier_icon` as text to rendering
   `<img src={tier_icon}>`, sized per location (≈20 px navbar → ≈96 px placement).
3. Build verification, then it's ready to ship with the rest of the rank work.

Until the files land, the app keeps showing the current emoji set — nothing breaks.

> Note: this is async homework you can do on your own time — it doesn't block any
> other build work, and the integration step is quick once the assets exist.
