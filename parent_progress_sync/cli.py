"""Command-line entry point, suitable for cron or a scheduled CI job."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from typing import Sequence

from .config import Config, ConfigError
from .labels import LabelError
from .linear_client import LinearClient, LinearError
from .sync import ProgressSync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parent-progress-sync",
        description="Label Linear parent issues with their completed-subissue percentage band.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the changes that would be made without writing to Linear",
    )
    parser.add_argument(
        "--team",
        metavar="KEY",
        help="limit the sync to one team key (overrides LINEAR_TEAM_KEY)",
    )
    parser.add_argument(
        "--label-group",
        metavar="NAME",
        help="name of the managed label group (overrides LINEAR_LABEL_GROUP)",
    )
    parser.add_argument(
        "--bootstrap-labels",
        action="store_true",
        help="create the managed label group and any missing buckets",
    )
    parser.add_argument(
        "--cleanup-legacy-prefixes",
        action="store_true",
        help="also strip '[042%%] ' title prefixes left by earlier versions",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="enable debug logging, which includes issue titles",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        config = replace(config, dry_run=True)
    if args.team:
        config = replace(config, team_key=args.team)
    if args.label_group:
        config = replace(config, label_group=args.label_group)
    if args.bootstrap_labels:
        config = replace(config, bootstrap_labels=True)
    if args.cleanup_legacy_prefixes:
        config = replace(config, cleanup_legacy_prefixes=True)

    client = LinearClient(
        api_key=config.api_key,
        api_url=config.api_url,
        max_retries=config.max_retries,
    )

    try:
        report = ProgressSync(client, config).run()
    except LabelError as exc:
        print(f"Label setup error: {exc}", file=sys.stderr)
        return 3
    except LinearError as exc:
        print(f"Linear API error: {exc}", file=sys.stderr)
        return 1

    print(report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
