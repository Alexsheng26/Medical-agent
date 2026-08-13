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
from . import backends
from . import doctor as doctor_mod
from . import citations, deai, dialogue, journal as journal_mod, memory as memory_mod
from . import brief as brief_mod
from . import figures as figures_mod
from . import ingest, pipeline, review as review_mod, writing
from .config import Config
from .llm import LLM, RefusalError
from .usage import Ledger
from .store import Store

GUIDE = """
科研中间体 · 使用流程

  第一步 建库          mra search "NASH 肝纤维化 巨噬细胞" --max 60
                       mra import result.xml           # 或：导入浏览器存下的 PubMed XML
                       mra digest                      # 结构化提炼每篇文献
  第二步 磨假说        mra chat                        # 多轮苏格拉底式对话
                       mra hypothesis --note "第一版"  # 冻结为可版本比较的假说
                       mra proposal -o proposal.md     # 生成 proposal 框架
                       mra review "主题" --outline-only # 综述：先看大纲，再决定写不写
  第三步 定期刊        mra assess data.csv --notes "n=12/组" # 评分 + 排序推荐候选期刊
                       mra journal add "Hepatology" --samples ./samples/hepatology
  第四步 对标评估      mra assess data.csv --journal Hepatology  # 对着该刊门槛再评一次
  第五步 写作          mra draft results --journal Hepatology --data data.csv -o results.md
                       mra finalize results.md --journal Hepatology
  长期沉淀            mra fingerprint ./my_papers      # 学习你自己的文风
                       mra memory --refresh            # 课题方向图谱

  无人值守            mra watch add "NASH 纤维化"     # 保存检索式（只规划一次）
                       mra sync --quiet --max-cost 2.00 # 挂 cron，写简报

  不需要 API key 的命令：lint / refs / memory / usage / status / guide
                        （import 处理 .xml 时也不需要）
"""


def _force_utf8_output() -> None:
    """Print UTF-8 regardless of the console's code page.

    Windows picks the locale code page for a redirected stream, and cp936 — the
    default on a Chinese install — cannot encode `✓ ✗ ⚠ ↻`. Without this,
    `mra refs draft.md > out.txt` and the scheduled `mra sync >> sync.log` both
    die on UnicodeEncodeError, which is exactly the unattended path where nobody
    is watching. `errors="replace"` keeps a stream that cannot be reconfigured
    (a pipe under some launchers) from taking the whole run down over a glyph.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # already detached, or not a real stream
                pass


def read_text(path: Path) -> str:
    """Read a manuscript the researcher wrote, failing loudly on encoding.

    Not `errors="replace"`: silently substituting characters in a manuscript
    corrupts the text you are about to submit. A Word export saved as GBK on
    Windows is common enough to deserve an instruction rather than a traceback.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit(
            f"{path} is not UTF-8 ({exc.reason} at byte {exc.start}).\n"
            "Re-save it as UTF-8 — in Notepad, 文件 → 另存为 → 编码选 UTF-8; "
            "in VS Code, click the encoding in the status bar → Save with Encoding."
        ) from exc


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
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
        return _run(args, cfg)
    finally:
        _report_usage(args.command)


def _run(args, cfg: Config) -> int:
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
    except (ValueError, FileNotFoundError, backends.BackendUnavailable) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except backends.BackendError as exc:
        # The provider answered, but not with something usable. The message
        # already names which commands need what, so print it as-is.
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except backends.api_error_types() as exc:
        # A billing or auth failure used to surface as a forty-line traceback
        # ending in a JSON blob, which tells a first-time user nothing.
        print(f"\n{backends.describe_api_error(exc)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


def _report_usage(command: str) -> None:
    """Print what this command cost. Runs even on failure — a command that
    died half way through still spent money, and hiding that is worse than
    the failure itself."""
    if _LEDGER is None:
        return
    line = _LEDGER.line()
    if line:
        _LEDGER.save(command)
        print(f"\n{line}", file=sys.stderr)


# --------------------------------------------------------------------- helpers


_LEDGER: Ledger | None = None


def _ledger(cfg: Config) -> Ledger:
    global _LEDGER
    if _LEDGER is None:
        _LEDGER = Ledger.load(cfg.usage_path, cfg.model, cfg.prices)
    return _LEDGER


def _llm(cfg: Config) -> LLM:
    """Build the model client, checking the credential the *configured provider*
    actually reads.

    Hard-coding the Anthropic variables here meant that pointing the tool at an
    OpenAI-compatible endpoint left every command except `doctor` refusing to
    start — and `doctor` passed, because it builds the client directly. A green
    self-check followed by nothing working is worse than either alone.
    """
    provider = (cfg.provider or "anthropic").lower()
    variable, present = doctor_mod.key_variable(provider)
    if not present:
        raise ValueError(
            f"No API credentials found for provider {provider!r}. "
            f"Set {variable} in your environment. Commands that need no model — "
            "lint, refs, memory, status — work without it."
        )
    return LLM(cfg, ledger=_ledger(cfg))


def _store(cfg: Config) -> Store:
    return Store(cfg.db_path)


CONFIRM_ABOVE = 1.00


def _confirm_spend(estimate: float | None, args) -> bool:
    """Ask before a large spend. Returns False when the researcher declines.

    A job this size run from a script with no ceiling is the case worth
    refusing rather than guessing at: without a terminal there is nobody to
    answer, and proceeding silently is how a batch job becomes a surprise bill.
    """
    if getattr(args, "yes", False) or args.max_cost is not None:
        return True
    if estimate is None or estimate < CONFIRM_ABOVE:
        return True

    if not sys.stdin.isatty():
        print(
            f"\nThis is estimated at ${estimate:.2f} and nothing is watching for an "
            "answer. Re-run with --max-cost to set a ceiling, or --yes to accept.",
            file=sys.stderr,
        )
        return False

    return input("Continue? [y/N] ").strip().lower() in {"y", "yes"}


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


def cmd_doctor(args, cfg: Config) -> int:
    """Two tiny calls that answer 'will this actually work here'.

    The developing environment cannot reach every provider, so whether a given
    endpoint works is a question only the researcher's own machine can answer.
    """
    report = doctor_mod.run(cfg)
    print(doctor_mod.format_report(cfg, report))
    return 0 if report.prose_ok else 1


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


def cmd_import(args, cfg: Config) -> int:
    """Load documents from disk: PubMed XML, PDFs, or plain text.

    XML needs no model call. PDFs and text use one small call per file to read
    the metadata off the front matter, unless --no-metadata is passed.
    """
    paths = [Path(p) for p in args.files]
    needs_model = any(
        p.suffix.lower() in (ingest.PDF_SUFFIXES | ingest.TEXT_SUFFIXES) for p in paths
    )
    llm = _llm(cfg) if (needs_model and not args.no_metadata) else None

    with _store(cfg) as store:
        result = pipeline.import_files(
            cfg, store, paths, llm=llm, topic=args.topic,
            on_file=lambda path, n, warns: print(
                f"  {path.name}: {n} record(s)" + ("  ⚠" if warns else "")
            ),
        )
        print(f"\nRead {result.found}; {result.added} new, "
              f"{result.skipped_no_abstract} skipped.")

        if result.warnings:
            print("\nNeeds attention:")
            for warning in result.warnings:
                print(f"  ! {warning}")

        print(f"\nKnowledge base now holds {store.count_articles()} documents.")
        if result.added:
            print("Next: `mra digest` to extract structured cards.")
    return 0


def cmd_watch_add(args, cfg: Config) -> int:
    with _store(cfg) as store:
        if args.query:
            query = args.query
        else:
            # Plan once, here. `mra sync` replays the stored string verbatim so
            # an unattended run costs no model call and cannot drift.
            plan = pipeline.plan_query(_llm(cfg), args.topic, cfg.chat_language)
            query = plan.pubmed_query
            print(f"Planned query:\n  {query}\n  {plan.rationale}\n")

        name = args.name or _slug(args.topic)
        store.add_watch(name, query, topic=args.topic, retmax=args.max)
        print(f"Watch '{name}' saved. `mra sync` will replay this query verbatim.")
        print("Edit it any time with `mra watch add --name "
              f"{name} --query '<new query>' \"{args.topic}\"`.")
    return 0


def cmd_watch_list(args, cfg: Config) -> int:
    with _store(cfg) as store:
        watches = store.list_watches()
        if not watches:
            print("No watches. Add one with `mra watch add \"<topic>\"`.")
            return 0
        for w in watches:
            last = w["last_run_at"][:10] if w["last_run_at"] else "never"
            print(f"  {w['name']:<16} last run {last}  (+{w['last_added']})  max {w['retmax']}")
            print(f"    {w['query']}")
    return 0


def cmd_watch_remove(args, cfg: Config) -> int:
    with _store(cfg) as store:
        if store.remove_watch(args.name):
            print(f"Removed watch '{args.name}'.")
            return 0
        print(f"No watch named '{args.name}'.", file=sys.stderr)
        return 1


def cmd_sync(args, cfg: Config) -> int:
    """Replay every saved watch, extract what is new, write a brief.

    Built for cron: idempotent, bounded by a spend ceiling, and tolerant of one
    watch failing.
    """
    with _store(cfg) as store:
        llm = _llm(cfg)
        events: list[str] = []

        result = pipeline.sync(
            cfg, store, llm,
            max_cost=args.max_cost if args.max_cost is not None else cfg.default_max_cost,
            do_digest=not args.no_digest,
            on_event=lambda m: (events.append(m), None if args.quiet else print(m))[1],
        )

        # Refresh the topic graph so the direction map keeps up on its own.
        mem = memory_mod.Memory.load(cfg.memory_path)
        mem.refresh_topics(store)
        mem.save()

        result.brief_path = brief_mod.write_brief(
            cfg, store, None if args.no_digest else llm, result.new_ids,
            watch_summary="\n".join(f"- {e}" for e in events),
            errors=result.errors,
        )

        if result.new_ids or not args.quiet:
            print(f"\n{len(result.new_ids)} new; {result.digested} extracted"
                  + (f", {result.digest_failed} failed" if result.digest_failed else ""))
            print(f"Brief: {result.brief_path}")

        for error in result.errors:
            print(f"  ! {error}", file=sys.stderr)

        if result.stopped_on_budget:
            ceiling = args.max_cost if args.max_cost is not None else cfg.default_max_cost
            print(f"  ! Stopped at the ${ceiling:.2f} ceiling; "
                  "re-run to continue where it left off.", file=sys.stderr)
            return 2
        return 1 if result.errors else 0


def _slug(text: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())[:24].strip("-")
    return slug or "watch"


def cmd_digest(args, cfg: Config) -> int:
    """Extract cards, with the price shown before the money is spent.

    This is the most expensive command in the tool — one call per paper — and
    the launcher put it one keystroke away. Importing a large PubMed export and
    running it is a plausible accident worth tens of dollars, so the estimate is
    always printed and a large job asks first.
    """
    with _store(cfg) as store:
        llm = _llm(cfg)
        pending = store.pmids_without_cards()
        if not pending:
            print("Every stored article already has a card.")
            return 0

        planned = pending[: args.limit] if args.limit else pending
        estimate, chars = pipeline.estimate_digest(store, cfg.model, planned)

        print(f"Extracting {len(planned)} of {len(pending)} pending articles "
              f"({chars // 1000}k characters).")
        if estimate is None:
            print(f"  Cost unknown — no price on file for {cfg.model}.")
        else:
            print(f"  Estimated cost: about ${estimate:.2f}"
                  + (f", hard stop at ${args.max_cost:.2f}" if args.max_cost else ""))

        if not _confirm_spend(estimate, args):
            return 2

        ledger = _ledger(cfg)

        def over_budget() -> bool:
            if args.max_cost is None:
                return False
            spent = ledger.session.cost(cfg.model)
            return spent is not None and spent >= args.max_cost

        def progress(index: int, total: int, pmid: str) -> None:
            print(f"  [{index}/{total}] PMID:{pmid}", end="\r", flush=True)

        ok, failed = pipeline.digest(
            cfg, store, llm, limit=args.limit, on_progress=progress, stop_check=over_budget
        )
        print(f"\nExtracted {ok} cards" + (f", {failed} failed" if failed else "."))
        if over_budget():
            print(f"Stopped at the ${args.max_cost:.2f} ceiling. "
                  f"{len(pending) - ok - failed} papers still pending — re-run to continue.")
            return 2
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


def cmd_review(args, cfg: Config) -> int:
    """Plan a review, show the plan, then write it.

    The outline is printed and confirmed before the sections are written. A
    review is the most expensive thing this tool produces — one call per section
    — and a structure the researcher would have changed is worth catching for
    the price of the outline rather than the price of the whole draft.
    """
    with _store(cfg) as store:
        llm = _llm(cfg)
        outline, available = review_mod.plan(cfg, store, llm, args.topic)
        print(review_mod.format_outline(outline, store, available))

        if args.outline_only:
            path = _write_out(
                json.dumps(outline.model_dump(), ensure_ascii=False, indent=2),
                args.output, cfg, "review.outline.json",
            )
            print(f"\n大纲已写入 {path}。改完之后去掉 --outline-only 再跑一次。")
            return 0

        sections = len(outline.sections)
        if not args.yes:
            print(f"\n下一步要写 {sections} 节，每节一次调用。")
            if input("继续？[y/N] ").strip().lower() not in {"y", "yes"}:
                print("已停在大纲这一步，没有花写作的钱。")
                return 0

        def progress(index: int, total: int, heading: str) -> None:
            print(f"  [{index}/{total}] {heading}", flush=True)

        print()
        text, report = review_mod.write(
            cfg, store, llm, outline, journal=args.journal or "", on_section=progress
        )
        if args.references:
            text += "\n\n## References\n\n" + citations.reference_list(text, store)

        path = _write_out(text, args.output, cfg, "review.md")
        print(f"\nWritten to {path}\n")
        print(report.summary())
        print()
        print(deai.analyze(text).summary())
    return 0


def cmd_figures(args, cfg: Config) -> int:
    """Plan the figures. Not draw them — see mra/figures.py for why."""
    with _store(cfg) as store:
        llm = _llm(cfg)
        paths = [Path(p) for p in args.data]
        plan = figures_mod.plan(
            cfg, store, llm, paths, journal=args.journal or "", notes=args.notes
        )
        print(figures_mod.format_figures(plan))

        if unsourced := figures_mod.unsourced_panels(plan, paths):
            print("\n⚠ 下列 panel 引用的列在你给的文件里找不到——"
                  "可能是你有但没附上，也可能是模型想当然了，自己核一下：")
            for item in unsourced:
                print(f"    {item}")

        if args.output:
            path = _write_out(figures_mod.to_json(plan), args.output, cfg, "")
            print(f"\nFull plan written to {path}")
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
    """Two modes on one command, dispatched on whether a target is named.

    Without --journal the question is "where should this go", which is the
    question a researcher actually has before they have chosen; with it, the
    question is "does this clear that journal's bar".
    """
    with _store(cfg) as store:
        llm = _llm(cfg)
        paths = [Path(p) for p in args.data]

        if args.journal:
            result = assess_mod.assess(cfg, store, llm, args.journal, paths, notes=args.notes)
            print(assess_mod.format_assessment(result))
        else:
            result = assess_mod.recommend(cfg, store, llm, paths, notes=args.notes)
            print(assess_mod.format_recommendation(result, store))

        if args.output:
            path = _write_out(assess_mod.assessment_to_json(result), args.output, cfg, "")
            print(f"\nFull assessment written to {path}")
    return 0


def cmd_draft(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = _llm(cfg)
        data_text = "\n\n".join(assess_mod.load_data_description(Path(p)) for p in args.data)
        if args.notes:
            data_text = f"RESEARCHER'S NOTES:\n{args.notes}\n\n{data_text}"

        text, notes = writing.draft(cfg, store, llm, args.section, args.journal, data_text)
        path = _write_out(text, args.output, cfg, f"{args.section}.v1.md")

        if notes:
            # On screen, not in the file. These are usually the most useful part
            # of the run — a number that disagrees with another number — and the
            # file has to stay submittable.
            print(f"{notes}\n\n{'─' * 60}\n")
        print(f"Written to {path}\n")
        print(citations.check(text, store).summary())
        print()
        print(deai.analyze(text).summary())
    return 0


def cmd_nativize(args, cfg: Config) -> int:
    with _store(cfg) as store:
        llm = _llm(cfg)
        source = Path(args.file)
        text = read_text(source)
        mem = memory_mod.Memory.load(cfg.memory_path)

        result = writing.nativize(cfg, store, llm, text, args.journal, memory=mem)
        path = _write_out(result, args.output, cfg, f"{source.stem}.native.md")
        print(f"Written to {path}")
    return 0


def cmd_lint(args, cfg: Config) -> int:
    """Deterministic AI-tell check. No API call, no key needed."""
    text = read_text(Path(args.file))
    print(deai.analyze(text).summary())
    return 0


def cmd_polish(args, cfg: Config) -> int:
    llm = _llm(cfg)
    source = Path(args.file)
    text = read_text(source)

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
        text = read_text(source)
        mem = memory_mod.Memory.load(cfg.memory_path)

        paths = writing.write_versions(
            cfg, store, llm, text, args.journal, source.stem, memory=mem
        )
        print("Deliverables:")
        for label, path in paths.items():
            print(f"  {label:<7} {path}")
        print()
        print(read_text(paths["report"]))
    return 0


def cmd_refs(args, cfg: Config) -> int:
    """Citation integrity check. No API call, no key needed."""
    with _store(cfg) as store:
        text = read_text(Path(args.file))
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


def cmd_usage(args, cfg: Config) -> int:
    """Token and cost accounting. No API call."""
    ledger = Ledger.load(cfg.usage_path, cfg.model, cfg.prices)
    print(ledger.report())
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

    add("doctor", cmd_doctor, "Check the model connection actually works")
    add("status", cmd_status, "Show what is in the workspace")

    p = add("search", cmd_search, "Search PubMed and store results")
    p.add_argument("topic", help="Clinical question or topic")
    p.add_argument("--max", type=int, default=50, help="Max records to retrieve")
    p.add_argument("--query", help="Use this PubMed query verbatim, skipping planning")

    p = add("import", cmd_import, "Load PubMed XML, PDFs, or plain text from disk")
    p.add_argument("files", nargs="+", help="Files: .xml (PubMed), .pdf, .txt, .md")
    p.add_argument("--topic", default="", help="Label these records with a topic")
    p.add_argument("--no-metadata", action="store_true",
                   help="Skip metadata extraction for PDFs/text (no API call)")

    p = sub.add_parser("watch", help="Saved searches replayed by `mra sync`")
    wsub = p.add_subparsers(dest="watch_command", required=True)

    wp = wsub.add_parser("add", help="Save a search (plans the query once)")
    wp.set_defaults(func=cmd_watch_add)
    wp.add_argument("topic", help="Topic or clinical question")
    wp.add_argument("--name", help="Short name (default: derived from the topic)")
    wp.add_argument("--query", help="Use this PubMed query verbatim, skipping planning")
    wp.add_argument("--max", type=int, default=50, help="Max records per run")

    wp = wsub.add_parser("list", help="List saved watches")
    wp.set_defaults(func=cmd_watch_list)

    wp = wsub.add_parser("remove", help="Delete a watch")
    wp.set_defaults(func=cmd_watch_remove)
    wp.add_argument("name")

    p = add("sync", cmd_sync, "Run every watch, extract what is new, write a brief")
    p.add_argument("--max-cost", type=float, default=None,
                   help="Stop once this much has been spent (default: from config)")
    p.add_argument("--no-digest", action="store_true", help="Fetch only; do not extract")
    p.add_argument("--quiet", action="store_true", help="Only speak when there is news")

    p = add("digest", cmd_digest, "Extract structured cards for stored articles")
    p.add_argument("--limit", type=int, help="Only process this many")
    p.add_argument("--max-cost", type=float,
                   help="Stop cleanly once this much has been spent (exit code 2)")
    p.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation")

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
    jp.add_argument("--samples", help="Directory of full-text samples (.pdf/.txt/.md)")
    jp.add_argument("--pubmed", type=int, default=20, help="Abstracts to sample (0 to skip)")
    jp.add_argument("--years", type=int, default=5, help="How far back to sample")

    jp = jsub.add_parser("list", help="List stored profiles")
    jp.set_defaults(func=cmd_journal_list)

    jp = jsub.add_parser("show", help="Print a stored profile")
    jp.set_defaults(func=cmd_journal_show)
    jp.add_argument("name")

    p = add("review", cmd_review, "Write a review article from your knowledge base")
    p.add_argument("topic", help='综述主题，例如 "TREM2 与 MASH 纤维化"')
    p.add_argument("--journal", help="Match this journal's conventions (needs a stored profile)")
    p.add_argument("--outline-only", action="store_true",
                   help="Only plan it — cheap, and lets you fix the structure first")
    p.add_argument("--references", action="store_true", help="Append a reference list")
    p.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation")
    p.add_argument("-o", "--output")

    p = add("assess", cmd_assess, "Score your data, and pick a journal if you have not")
    p.add_argument("data", nargs="+", help="Data files (csv/tsv/txt/md)")
    p.add_argument(
        "--journal",
        help="Score against this journal's stored profile. Omit to get ranked "
        "journal recommendations instead.",
    )
    p.add_argument("--notes", default="", help="Context the files do not carry")
    p.add_argument("-o", "--output", help="Also write the full assessment as JSON")

    p = add("figures", cmd_figures, "Plan what your figures argue (not draw them)")
    p.add_argument("data", nargs="+", help="Data files (csv/tsv/txt/md)")
    p.add_argument("--journal", help="Follow this journal's figure conventions")
    p.add_argument("--notes", default="", help="Context the files do not carry")
    p.add_argument("-o", "--output", help="Also write the full plan as JSON")

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

    add("usage", cmd_usage, "Show token usage and what it has cost")

    p = add("export", cmd_export, "Export everything as JSON")
    p.add_argument("-o", "--output")

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
