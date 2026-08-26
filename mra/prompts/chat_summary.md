You are compressing the earlier part of a scientific dialogue so it can survive
outside the model's context window.

This is not a summary for a reader. It is the working memory of the
conversation, and the next turns will reason from it as if they had been
present. What you drop is gone.

## What must survive, in this order

1. **What was ruled out, and on what grounds.** A mechanism considered and
   rejected is the single most expensive thing in the conversation to
   reconstruct — without it the dialogue will propose it again three turns
   later and waste the researcher's time. Record the rejection *and its
   reason*, not just the topic.
2. **Which papers were treated as decisive**, by identifier, separated into
   those that supported the direction and those that challenged it. Keep
   `[PMID:x]` and `[LOCAL:x]` markers exactly as written — downstream steps
   verify them against the knowledge base and a mangled identifier reads as a
   fabrication.
3. **Falsification tests proposed** — what result would kill the current claim.
4. **The claim as it currently stands**, and the weaknesses left open.
5. **Anything the researcher stated as fact about their own unpublished data**
   — cohort sizes, assays, what they have and have not done. This never appears
   in the literature and cannot be recovered from anywhere else.

## What to drop

Pleasantries, restated abstracts, anything the retrieval step will fetch again
on its own, and your own earlier hedging. A retrieved paper's content comes
back every turn; the fact that *this conversation rejected its interpretation*
does not.

## Form

Plain prose under the five headings above, in {language}. No preamble. Be
specific — "ruled out Kupffer-cell origin because the fate-mapping in
[PMID:30778899] shows replacement by monocyte-derived cells by week 8" is
useful; "discussed macrophage origin" is not.

If an earlier summary is given, fold the new messages into it and return one
combined summary, keeping everything from the earlier one that still stands.

---

EARLIER SUMMARY (may be empty):
{previous}

---

MESSAGES TO FOLD IN:
{messages}
