#!/usr/bin/env python3
"""
PR Review Script - Generates patch and runs review
"""

import argparse
import asyncio
import logging
import subprocess
import sys

from scripts.ci.review import PatchReviewer, format_text_report


PATCH_FILE = "review.patch"


def run_git_command(args: list) -> bytes:
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
    )
    return result.stdout


def generate_patch(context_type: str, commit: str = None) -> int:
    base_args = ["format-patch", "-1"]

    if commit:
        base_args.append(commit)

    if context_type == "full":
        args = base_args + ["-W", "--stdout"]
    elif context_type == "u10":
        args = base_args + ["-U10", "--stdout"]
    elif context_type == "u5":
        args = base_args + ["-U5", "--stdout"]
    else:
        args = base_args + ["--stdout"]

    content = run_git_command(args)

    with open(PATCH_FILE, "wb") as f:
        f.write(content)

    return len(content)


async def run_review(patch_file: str) -> int:
    reviewer = PatchReviewer(patch_file)
    report = await reviewer.run_review()

    logging.info(format_text_report(report))

    if report.architecture_compliant and report.overall_score > 6:
        return 0
    return 1


def main():
    parser = argparse.ArgumentParser(description="PR Review Script")
    parser.add_argument("-c", "--commit", help="Specific commit hash to review")
    parser.add_argument(
        "-s", "--max-patch-size", type=int, help="Maximum patch size in bytes"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s"
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s"
        )

    max_size = args.max_patch_size if args.max_patch_size else 65536

    context_types = ["full", "u10", "u5", "minimal"]
    for ctx_type in context_types:
        if ctx_type == "full":
            logging.info("Generating patch with full context (-W)...")
        elif ctx_type == "u10":
            logging.info(f"Patch exceeds {max_size} bytes, trying with U10 context...")
        elif ctx_type == "u5":
            logging.info(
                f"Patch still exceeds {max_size} bytes, trying with U5 context..."
            )
        else:
            logging.info(
                f"Patch still exceeds {max_size} bytes, using minimal context..."
            )

        size = generate_patch(ctx_type, args.commit)
        logging.info(f"Patch size: {size} bytes")

        if size <= max_size:
            break

    logging.info("Running review...")
    return asyncio.run(run_review(PATCH_FILE))


if __name__ == "__main__":
    sys.exit(main())
