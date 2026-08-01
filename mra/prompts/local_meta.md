Read the front matter of this document and extract its bibliographic metadata.

This text came off the researcher's own disk — a PDF, a saved manuscript, a
preprint. It has not been through an indexing database, so nothing about its
structure is guaranteed and PDF extraction may have mangled the layout.

Rules:

- **Leave a field empty rather than guessing.** A working paper or thesis
  chapter genuinely may have no journal and no year. An invented one is worse
  than a blank, because everything downstream treats these as facts.
- Take the title from the title, not from a running header. Extractors often
  put the journal name and page numbers at the top of every page.
- Authors as surname plus initials (`Chen W`), in the order printed.
- `doi` without the `https://doi.org/` prefix.
- `pmid` only if the document actually prints one. Do not derive it from
  anything else.
- If the text is too garbled to read reliably, return empty fields. That result
  is useful — it tells the researcher this file needs attention.

## Document front matter

{text}
