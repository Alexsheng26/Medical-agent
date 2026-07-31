"""Command-line interface.

Commands that need no model call (`lint`, `refs`, `memory`, `status`) work
without an API key, so the deterministic checks can run offline.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import assess as assess_mod
from . import citations, deai, dialogue, journal as journal_mod, memory as memory_mod
from . import pipeline, writing
from .config import Config
from .llm import LLM, RefusalError
from .store import Store

GUIDE = """
科研中间体 · 使用流程

  第一步 建库          mra search "NASH 肝纤维化 巨噬细胞" --max 60
                       mra digest                      # 结构化提炼每篇文献
  第二步 磨假说        mra chat                        # 多轮苏格拉底式对话
                       mra hypothesis --note "第一版"  # 冻结为可版本比较的假说
                       mra proposal -o proposal.md     # 生成 proposal 框架
  第三步 学期刊        mra journal add "Hepatology" --samples ./samples/hepatology
  第四步 评估数据      mra assess --journal Hepatology data.csv --notes "n=12/组"
  第五步 写作          mra draft results --journal Hepatology --data data.csv -o results.md
                       mra finalize results.md --journal Hepatology
  长期沉淀            mra fingerprint ./my_papers      # 学习你自己的文风
                       mra memory --refresh            # 课题方向图谱

  不需要 API key 的命令：lint / refs / memory / status / guide
"""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    # httpx logs every request at INFO, which drowns our own output.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not args.command:
        parser.print_help()
        return 0

    cfg = Config.load(args.workspace)
    cfg.ensure_workspace()

    try:
        return args.func(args, cfg)
    except RefusalError as exc:
        print(f"\nThe model declined this request: {exc}", file=sys.stderr)
        print(
            "If this is legitimate research, rephrasing the clinical framing usually "
            "resolves it. Server-side fallbacks are already enabled.",
            file=sys.stderr,
        )
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


# --------------------------------------------------------------------- helpers


def _llm(cfg: Config) -> LLM:
    import os

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise ValueError(
            "No API credentials found. Set ANTHROPIC_API_KEY in your environment "
            "(or run `ant auth login`). Commands that need no model — lint, refs, "
            "memory, status — work without it."
        )
    return LLM(cfg)


def _store(cfg: Config) -> Store:
    return Store(cfg.db_path)


def _write_out(text: str, path: str | None, cfg: Config, default_name: str) -> Path:
    target = Path(path) if path else cfg.drafts_dir / default_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


# -------------------------------------------------------------------- commands


def cmd_guide(args, cfg: Config) -> int:
    print(GUIDE)
    return 0


def cmd_init(args, cfg: Config) -> int:
    if args.email:
        cfg.ncbi_email = args.email
    if args.language:
        cfg.chat_language = args.language
    path = cfg.save()
    Store(cfg.db_path).close()

    print(f"Workspace ready at {cfg.workspace}")
    print(f"  config      {path}")
    print(f"  knowledge   {cfg.db_path}")
    print(f"  drafts      {cfg.drafts_dir}")
    if not cfg.ncbi_email:
        print("\nTip: `mra init --email you@example.com` — NCBI asks for a contact address.")
    print("\nRun `mra guide` for the workflow.")
    return 0


def cmd_status(args, cfg: Config) -> int:
    with _store(cfg) as store:
        latest = store.latest_hypothesis()
        print(f"Workspace     {cfg.workspace}")
        print(f"Model         {cfg.model} (effort={cfg.effort})")
        print(f"Articles      {store.count_articles()}")
        print(f"Extracted     {store.count_cards()}")
        print(f"Journals      {', '.join(store.list_journals()) or '—'}")
        print(f"Hypotheses    {len(store.list_hypotheses())}")
        if latest:
            print(f"  latest v{latest[0]}: {latest[1].get('title', '')}")
        print(f"Chat turns    {len(store.chat_history(limit=10_000))}")
    return 0


def cmd_search(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = None if args.query else _llm(cfg)
        result = pipeline.search(
            cfg, store, llm, args.topic, retmax=args.max, raw_query=args.query
        )

        if result.plan:
            print(f"Planned query:\n  {result.query_used}\n")
            print(f"  rationale: {result.plan.rationale}")
            if result.plan.alternate_queries:
                print("  alternates:")
                for alt in result.plan.alternate_queries:
                    print(f"    · {alt}")
            print()

        print(f"PubMed returned {result.found} records; {result.added} new, "
              f"{result.skipped_no_abstract} skipped (no abstract).")
        print(f"Knowledge base now holds {store.count_articles()} papers.")
        if result.added:
            print("\nNext: `mra digest` to extract structured cards.")
    return 0


def cmd_digest(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = _llm(cfg)
        pending = len(store.pmids_without_cards())
        if not pending:
            print("Every stored article already has a card.")
            return 0

        print(f"Extracting {min(pending, args.limit or pending)} of {pending} pending articles…")

        def progress(index: int, total: int, pmid: str) -> None:
            print(f"  [{index}/{total}] PMID:{pmid}", end="\r", flush=True)

        ok, failed = pipeline.digest(cfg, store, llm, limit=args.limit, on_progress=progress)
        print(f"\nExtracted {ok} cards" + (f", {failed} failed" if failed else "."))
    return 0


def cmd_chat(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = _llm(cfg)

        if args.reset:
            store.clear_chat()
            print("Conversation cleared.")
            return 0

        if args.message:
            print(dialogue.respond(cfg, store, llm, args.message))
            return 0

        print("Interactive session. Blank line or Ctrl-D to exit.\n")
        while True:
            try:
                message = input("你 › ").strip()
            except EOFError:
                break
            if not message:
                break
            print()
            print(dialogue.respond(cfg, store, llm, message))
            print()
    return 0


def cmd_hypothesis(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = _llm(cfg)
        version, hypothesis = dialogue.consolidate(cfg, store, llm, note=args.note)

        print(f"Hypothesis v{version}: {hypothesis.title}\n")
        print(hypothesis.statement)
        print(f"\nGap: {hypothesis.knowledge_gap}")
        print(f"Novelty: {hypothesis.novelty_type} ({hypothesis.novelty_level}/5)\n")
        print("Mechanism chain:")
        for index, step in enumerate(hypothesis.mechanism_chain, 1):
            print(f"  {index}. {step}")
        print("\nTestable predictions:")
        for prediction in hypothesis.testable_predictions:
            print(f"  · {prediction}")
        print("\nWhere this is most likely to fail:")
        for weakness in hypothesis.open_weaknesses:
            print(f"  ! {weakness}")
        if hypothesis.challenging_pmids:
            print(f"\nComplicating evidence: {', '.join(hypothesis.challenging_pmids)}")
    return 0


def cmd_hypotheses(args, cfg: Config) -> int:
    with _store(cfg) as store:
        rows = store.list_hypotheses()
        if not rows:
            print("No hypotheses yet.")
            return 0
        for row in rows:
            payload = store.get_hypothesis(row["version"]) or {}
            note = f"  ({row['note']})" if row["note"] else ""
            print(f"v{row['version']:<3} {row['created_at'][:10]}  "
                  f"{payload.get('title', '')}{note}")
    return 0


def cmd_diff(args, cfg: Config) -> int:
    with _store(cfg) as store:
        print(dialogue.diff_hypotheses(store, args.old, args.new))
    return 0


def cmd_proposal(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = _llm(cfg)
        text, report = dialogue.write_proposal(cfg, store, llm, args.version)

        if args.references:
            text += "\n\n## References\n\n" + citations.reference_list(text, store)

        path = _write_out(text, args.output, cfg, "proposal.md")
        print(f"Written to {path}\n")
        print(report.summary())
    return 0


def cmd_journal_add(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = _llm(cfg)
        profile = journal_mod.build_profile(
            cfg,
            store,
            llm,
            args.name,
            sample_dir=Path(args.samples) if args.samples else None,
            pubmed_count=args.pubmed,
            years=args.years,
        )
        print(f"Profile stored for {profile.journal}.\n")
        print(f"Scope: {profile.scope_summary}\n")
        print(f"Titles: {profile.title_conventions}")
        print(f"Abstract: {profile.abstract_shape}\n")
        print("Structure:")
        for section in profile.article_structure:
            print(f"  {section.name}: {section.typical_paragraphs}")
        print("\nVoice:")
        for note in profile.voice_notes:
            print(f"  · {note}")
        if not args.samples:
            print(
                "\nNote: profiled from abstracts only. Add 3-5 full texts with "
                f"`--samples <dir>` before relying on this for section drafting."
            )
    return 0


def cmd_journal_list(args, cfg: Config) -> int:
    with _store(cfg) as store:
        names = store.list_journals()
        if not names:
            print("No journal profiles yet. `mra journal add \"Hepatology\"`")
            return 0
        for name in names:
            payload = store.get_journal(name) or {}
            meta = payload.get("_meta", {})
            source = "full text + abstracts" if meta.get("used_full_text") else "abstracts only"
            print(f"  {payload.get('journal', name)}  ({meta.get('sample_count', '?')} samples, {source})")
    return 0


def cmd_journal_show(args, cfg: Config) -> int:
    with _store(cfg) as store:
        print(journal_mod.profile_text(store, args.name))
    return 0


def cmd_assess(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = _llm(cfg)
        assessment = assess_mod.assess(
            cfg, store, llm, args.journal, [Path(p) for p in args.data], notes=args.notes
        )
        print(assess_mod.format_assessment(assessment))

        if args.output:
            path = _write_out(assess_mod.assessment_to_json(assessment), args.output, cfg, "")
            print(f"\nFull assessment written to {path}")
    return 0


def cmd_draft(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = _llm(cfg)
        data_text = "\n\n".join(assess_mod.load_data_description(Path(p)) for p in args.data)
        if args.notes:
            data_text = f"RESEARCHER'S NOTES:\n{args.notes}\n\n{data_text}"

        text = writing.draft(cfg, store, llm, args.section, args.journal, data_text)
        path = _write_out(text, args.output, cfg, f"{args.section}.v1.md")

        print(f"Written to {path}\n")
        print(citations.check(text, store).summary())
        print()
        print(deai.analyze(text).summary())
    return 0


def cmd_nativize(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = _llm(cfg)
        source = Path(args.file)
        text = source.read_text(encoding="utf-8")
        mem = memory_mod.Memory.load(cfg.memory_path)

        result = writing.nativize(cfg, store, llm, text, args.journal, memory=mem)
        path = _write_out(result, args.output, cfg, f"{source.stem}.native.md")
        print(f"Written to {path}")
    return 0


def cmd_lint(args, cfg: Config) -> int:
    """Deterministic AI-tell check. No API call, no key needed."""
    text = Path(args.file).read_text(encoding="utf-8")
    print(deai.analyze(text).summary())
    return 0


def cmd_polish(args, cfg: Config) -> int:
    llm = _llm(cfg)
    source = Path(args.file)
    text = source.read_text(encoding="utf-8")

    def on_round(index: int, before: float, after: float) -> None:
        print(f"  round {index}: {before:.1f} → {after:.1f}")

    result = writing.polish(
        cfg, llm, text, target=args.target, max_rounds=args.rounds, on_round=on_round
    )
    path = _write_out(result.text, args.output, cfg, f"{source.stem}.v2.md")

    print(f"\n{result.summary()}")
    print(f"\nWritten to {path}")
    return 0


def cmd_finalize(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = _llm(cfg)
        source = Path(args.file)
        text = source.read_text(encoding="utf-8")
        mem = memory_mod.Memory.load(cfg.memory_path)

        paths = writing.write_versions(
            cfg, store, llm, text, args.journal, source.stem, memory=mem
        )
        print("Deliverables:")
        for label, path in paths.items():
            print(f"  {label:<7} {path}")
        print()
        print(paths["report"].read_text(encoding="utf-8"))
    return 0


def cmd_refs(args, cfg: Config) -> int:
    """Citation integrity check. No API call, no key needed."""
    with _store(cfg) as store:
        text = Path(args.file).read_text(encoding="utf-8")
        report = citations.check(text, store)
        print(report.summary())
        if args.list:
            print("\nReferences:\n")
            print(citations.reference_list(text, store))
        return 0 if report.ok else 1


def cmd_fingerprint(args, cfg: Config) -> int:
    llm = _llm(cfg)
    mem = memory_mod.Memory.load(cfg.memory_path)
    fingerprint = memory_mod.build_fingerprint(cfg, llm, mem, Path(args.directory))

    print("Writing fingerprint stored.\n")
    print(f"Mean sentence length   {fingerprint.mean_sentence_length:.1f} words")
    print(f"Variability            {fingerprint.sentence_length_variability}")
    print(f"Voice                  {fingerprint.voice_preference}")
    print(f"Hedging                {fingerprint.hedging_style}\n")
    print("Distinctive traits:")
    for trait in fingerprint.distinctive_traits:
        print(f"  · {trait}")
    return 0


def cmd_memory(args, cfg: Config) -> int:
    mem = memory_mod.Memory.load(cfg.memory_path)
    if args.refresh:
        with _store(cfg) as store:
            mem.refresh_topics(store)
        mem.save()
        print("Topic graph refreshed.\n")
    print(mem.summary())
    return 0


def cmd_export(args, cfg: Config) -> int:
    """Dump the knowledge base as JSON so nothing is trapped in the tool."""
    with _store(cfg) as store:
        data = {
            "articles": [],
            "hypotheses": [
                {"version": r["version"], "note": r["note"],
                 "payload": store.get_hypothesis(r["version"])}
                for r in store.list_hypotheses()
            ],
            "journals": {name: store.get_journal(name) for name in store.list_journals()},
        }
        for pmid in store.all_pmids():
            article = store.get_article(pmid)
            if article:
                data["articles"].append(
                    {"article": article.__dict__, "card": store.get_card(pmid)}
                )

    path = _write_out(json.dumps(data, ensure_ascii=False, indent=2), args.output, cfg, "export.json")
    print(f"Exported {len(data['articles'])} articles to {path}")
    return 0


# ---------------------------------------------------------------- arg parsing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mra",
        description="Research intermediary agent for translational medicine. "
        "Run `mra guide` for the Chinese workflow overview.",
    )
    parser.add_argument("--workspace", help="Workspace directory (default: ./.mra)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    def add(name: str, func, help_text: str):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        return p

    add("guide", cmd_guide, "Print the workflow overview (Chinese)")

    p = add("init", cmd_init, "Create the workspace")
    p.add_argument("--email", help="Contact address for NCBI E-utilities")
    p.add_argument("--language", choices=["zh", "en"], help="Conversation language")

    add("status", cmd_status, "Show what is in the workspace")

    p = add("search", cmd_search, "Search PubMed and store results")
    p.add_argument("topic", help="Clinical question or topic")
    p.add_argument("--max", type=int, default=50, help="Max records to retrieve")
    p.add_argument("--query", help="Use this PubMed query verbatim, skipping planning")

    p = add("digest", cmd_digest, "Extract structured cards for stored articles")
    p.add_argument("--limit", type=int, help="Only process this many")

    p = add("chat", cmd_chat, "Scientific dialogue grounded in the knowledge base")
    p.add_argument("message", nargs="?", help="One-shot message (omit for interactive)")
    p.add_argument("--reset", action="store_true", help="Clear the conversation")

    p = add("hypothesis", cmd_hypothesis, "Freeze the conversation into a versioned hypothesis")
    p.add_argument("--note", default="", help="Short label for this version")

    add("hypotheses", cmd_hypotheses, "List hypothesis versions")

    p = add("diff", cmd_diff, "Compare two hypothesis versions")
    p.add_argument("old", type=int)
    p.add_argument("new", type=int)

    p = add("proposal", cmd_proposal, "Generate a proposal from a hypothesis")
    p.add_argument("--version", type=int, help="Hypothesis version (default: latest)")
    p.add_argument("-o", "--output")
    p.add_argument("--references", action="store_true", help="Append a reference list")

    p = sub.add_parser("journal", help="Journal style profiles")
    jsub = p.add_subparsers(dest="journal_command", required=True)

    jp = jsub.add_parser("add", help="Build a style profile")
    jp.set_defaults(func=cmd_journal_add)
    jp.add_argument("name", help='Journal name, e.g. "Hepatology"')
    jp.add_argument("--samples", help="Directory of full-text .txt samples")
    jp.add_argument("--pubmed", type=int, default=20, help="Abstracts to sample (0 to skip)")
    jp.add_argument("--years", type=int, default=5, help="How far back to sample")

    jp = jsub.add_parser("list", help="List stored profiles")
    jp.set_defaults(func=cmd_journal_list)

    jp = jsub.add_parser("show", help="Print a stored profile")
    jp.set_defaults(func=cmd_journal_show)
    jp.add_argument("name")

    p = add("assess", cmd_assess, "Score your data against a journal's bar")
    p.add_argument("data", nargs="+", help="Data files (csv/tsv/txt/md)")
    p.add_argument("--journal", required=True)
    p.add_argument("--notes", default="", help="Context the files do not carry")
    p.add_argument("-o", "--output", help="Also write the full assessment as JSON")

    p = add("draft", cmd_draft, "Draft a manuscript section in a journal's style")
    p.add_argument("section", choices=writing.SECTIONS)
    p.add_argument("--journal", required=True)
    p.add_argument("--data", nargs="+", required=True)
    p.add_argument("--notes", default="")
    p.add_argument("-o", "--output")

    p = add("nativize", cmd_nativize, "Rewrite text as a native-speaking scientist")
    p.add_argument("file")
    p.add_argument("--journal", required=True)
    p.add_argument("-o", "--output")

    p = add("lint", cmd_lint, "Check text for AI tells (offline, no API key)")
    p.add_argument("file")

    p = add("polish", cmd_polish, "Iteratively remove AI tells")
    p.add_argument("file")
    p.add_argument("--target", type=float, default=writing.DEFAULT_TARGET_SCORE)
    p.add_argument("--rounds", type=int, default=writing.DEFAULT_MAX_ROUNDS)
    p.add_argument("-o", "--output")

    p = add("finalize", cmd_finalize, "Produce v1 + nativized/de-AI'd v2 + report")
    p.add_argument("file")
    p.add_argument("--journal", required=True)

    p = add("refs", cmd_refs, "Verify citations against the knowledge base (offline)")
    p.add_argument("file")
    p.add_argument("--list", action="store_true", help="Print a formatted reference list")

    p = add("fingerprint", cmd_fingerprint, "Learn your writing voice from your own papers")
    p.add_argument("directory", help="Directory of your prior papers as .txt")

    p = add("memory", cmd_memory, "Show the topic graph and fingerprint status")
    p.add_argument("--refresh", action="store_true", help="Rebuild from the knowledge base")

    p = add("export", cmd_export, "Export everything as JSON")
    p.add_argument("-o", "--output")

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
