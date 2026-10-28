"""Experiment entry point: ``python -m experiments.run --config <yaml>``."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Skypiea experiment.")
    parser.add_argument("--config", required=True, help="path to an experiment YAML file")
    args = parser.parse_args(argv)
    print(f"{args.config}: not implemented yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
