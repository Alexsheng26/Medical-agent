Turn the researcher's question into English search terms for a keyword index.

The knowledge base holds English papers and is searched by literal word match,
so a question written in Chinese scores zero on every paper in it. Your job is
to supply the English words those papers would actually contain.

Rules:

- Output **search terms, not a translation of the sentence.** Drop everything
  that carries no retrieval value: pronouns, politeness, "please analyse",
  "these two papers", "what do you think".
- Give the terms a paper would use, not the layman's phrasing: `hepatic
  fibrosis` rather than `liver getting hard`.
- Include gene and protein symbols in their standard form (`TREM2`, `TGFB1`,
  `α-SMA`), and both the expansion and the abbreviation where a paper might use
  either (`surface-enhanced Raman scattering`, `SERS`).
- Include obvious synonyms and the alternative spelling a journal might use
  (`NASH` and `MASH`; `tumour` and `tumor`).
- 5 to 15 terms. Multi-word terms are fine and usually better.
- **If the question is already in English, do not echo it back.** You are
  called in that case only when the literal wording under-retrieved, which
  means the papers use different words. Give the ones the question did not:
  the symbol for a named protein, the expansion for an abbreviation, the term
  of art for a description (`myofibroblast` for "scar-forming cells", `SASP`
  for "senescence"), and the alternative a journal might prefer. Repeating the
  question's own words retrieves exactly what was already found.
- If the question carries no searchable subject at all (pure pleasantries, or a
  question about the tool itself rather than about science), return an empty
  list rather than inventing a topic. A wrong topic is worse than no topic: it
  retrieves confidently irrelevant papers.

## The researcher's question

{question}
