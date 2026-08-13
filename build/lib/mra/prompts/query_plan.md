Build a PubMed search strategy for the researcher's topic.

A good strategy for a translational question usually has three axes joined by
AND, each axis being a set of synonyms joined by OR:

- the **disease or clinical context** (MeSH descriptor plus common author terms)
- the **mechanism, molecule, pathway or cell type**
- optionally the **model system or study design**, when the question is about
  evidence class rather than biology

Rules for the query string:

- Use real PubMed field tags: `[MeSH]`, `[tiab]`, `[ti]`, `[pt]`, `[dp]`.
- Prefer `"Exact Phrase"[MeSH]` for concepts that have a descriptor, and pair it
  with `[tiab]` free text for terms too recent to be indexed.
- Gene and protein names need their aliases. TGF-beta appears as `TGF-b`,
  `TGFB1`, `transforming growth factor beta`. Miss the alias, miss the paper.
- Do not add date or language limits unless the researcher asked for them.
- The query must be syntactically valid as typed into PubMed. Balanced
  parentheses and quotes.

For `alternate_queries`, give one that is deliberately narrower (for when the
main query returns thousands) and one deliberately broader (for when it returns
almost nothing). Say which is which in the rationale.
