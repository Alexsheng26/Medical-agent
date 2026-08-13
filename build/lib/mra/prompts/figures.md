Plan the main-text figures for this work.

You are deciding **what the figures argue**, not drawing them. No colours, no
fonts, no software. A figure comes back from a supervisor or a reviewer for one
of four reasons, and all four are decided before anything is plotted:

1. **The panel does not establish what the caption says.** A correlation panel
   under a caption with the word "drives" is the commonest version.
2. **The plot form hides the data.** A bar with an error bar over n = 3 hides
   three points that should be shown. A box plot over n = 5 implies a
   distribution nobody measured.
3. **A control panel is missing.** The specificity comparison, the compartment
   that should be negative, the vehicle arm.
4. **The figures do not chain.** Five panels that each show something, arranged
   in an order that argues nothing.

Fix those here, where it is free.

## Rules

**Every panel is tied to real columns.** `source` names the supplied file and
the columns by name. When a panel needs something that was not supplied, say
`not in the supplied data` and list it under that figure's `missing` — do not
quietly assume it exists.

**`claim` is a sentence that could be checked against the panel.** "Shows TREM2
expression across stages" is not a claim; "septal TREM2+ density rises
monotonically from F2 to F4" is.

**`plot_type` names the form a reviewer expects at this n**, and says why. Below
about n = 10 per group, show the points. When the unit of analysis is a cell but
the replicate is a patient, say which one the panel plots — mixing them is the
single most common statistical objection to a quantitative figure.

**The caption states n, the test, and what the error bars are.** A caption
without those three gets sent back on its own.

**`caption_overclaims`** — write the sentence the researcher will want, then why
these panels do not support it. Be concrete and use their numbers.

**`better_as_table`** — a five-row comparison, a list of antibodies, a cohort
description: these are tables. Forcing them into a figure wastes a figure slot,
and main-text slots are the scarcest thing in a submission.

**`story`** — walk figure by figure and say what each one adds to the argument.
If one does not add anything, say which and say so plainly.

## The journal's figure conventions

{profile}

## What is already published in this area

Use this only to judge which comparisons a reader will already expect, and which
panel would be the one that is actually new.

{context}

## The researcher's data

{data}
