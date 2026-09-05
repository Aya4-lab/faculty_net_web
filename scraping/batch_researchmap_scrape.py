"""Deprecated compatibility wrapper for the researchmap batch fetch flow.

Use build_kanazawa_index.py for Step 1 and fetch_researcher_details.py for Step 2.
"""

import argparse

from fetch_researcher_details import run_fetch


def main():
    parser = argparse.ArgumentParser(description="Deprecated wrapper around fetch_researcher_details.py.")
    parser.add_argument("--index", default="kanazawa_researchers_index.json", help="Index file path")
    parser.add_argument("--json-ja", default="ridb_yugo_faculty_ja.json", help="Input Japanese RIDB JSON")
    parser.add_argument("--json-en", default="ridb_yugo_faculty_en.json", help="Input English RIDB JSON")
    parser.add_argument("--out-ja", default="remp_yugo_faculty_ja.json", help="Output Japanese JSON")
    parser.add_argument("--out-en", default="remp_yugo_faculty_en.json", help="Output English JSON")
    parser.add_argument("--start-idx", type=int, default=0, help="Start target index")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of researchers (0 = all)")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests")
    parser.add_argument("--max-papers", type=int, default=10, help="Papers limit per researcher")
    parser.add_argument("--no-abstract", action="store_true", help="Skip fetching paper abstract")
    parser.add_argument("--single-source", action="store_true", help="Use only the Japanese RIDB file and reuse it for EN output names")
    parser.add_argument("--render", action="store_true", help="Use Playwright for dynamic rendering")
    args = parser.parse_args()

    lim = args.limit if args.limit and args.limit > 0 else None
    print("batch_researchmap_scrape.py is deprecated. Use fetch_researcher_details.py for Step 2.")
    run_fetch(
        index_file=args.index,
        filter_yugo=not args.single_source,
        json_ja=args.json_ja,
        json_en=args.json_en,
        out_ja=args.out_ja,
        out_en=args.out_en,
        start_idx=args.start_idx,
        limit=lim,
        delay=args.delay,
        max_papers=args.max_papers,
        render=args.render,
        fetch_abstract=not args.no_abstract,
        single_source=args.single_source,
    )


if __name__ == "__main__":
    main()