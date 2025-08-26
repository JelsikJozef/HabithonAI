#!/usr/bin/env python3
"""
Helper script to download machine translate (NMT) models (Marian, M2M100, NLLB) into the local cache.
Prepares the environment so translations work offline without network access.

Module CLI Parameters:
    --models (list[str]): One or more Hugging Face model identifiers to download.
        Default: ['Helsinki-NLP/opus-mt-en-de', 'facebook/m2m100_418M', 'facebook/nllb-200-distilled-600M']
    --cache_dir (str): Optional path to override the default Hugging Face cache directory.
    --allow_patterns (list[str]): Optional list of regex patterns to include only matching files.

Returns:
    This script does not return a value; exits with status code 0 on success or 1 on error.
"""
import argparse
import sys

try:
    from huggingface_hub import snapshot_download
except ImportError:
    print("Error: huggingface_hub is not installed. Install it with: pip install huggingface-hub")
    sys.exit(1)


def main():
    """
    Parse command-line arguments and download specified NMT models into local cache.

    Parameters:
        None (all inputs are via CLI arguments).

    CLI Arguments (via argparse):
        --models (list[str]): Hugging Face model IDs to download.
        --cache_dir (str): Custom cache directory path.
        --allow_patterns (list[str]): Regex patterns to filter files to download.

    Returns:
        None: Exits with code 0 on completion.
    """
    parser = argparse.ArgumentParser(
        description="Download NMT models for offline usage into local cache"
    )
    parser.add_argument(
        "--models", nargs='+', required=False,
        default=[
            "Helsinki-NLP/opus-mt-en-de",
            "facebook/m2m100_418M",
            "facebook/nllb-200-distilled-600M",
        ],
        help="List of Hugging Face model identifiers to download"
    )
    parser.add_argument(
        "--cache_dir", type=str, default=None,
        help="Optional cache directory override"
    )
    parser.add_argument(
        "--allow_patterns", nargs='+', default=None,
        help="Optional list of file patterns (regex) to include only certain files"
    )
    args = parser.parse_args()

    for model_id in args.models:
        print(f"Downloading model {model_id} ...")
        snapshot_download(
            repo_id=model_id,
            cache_dir=args.cache_dir,
            allow_patterns=args.allow_patterns,
            local_files_only=False,
        )
        print(f"Downloaded model {model_id} into cache.")

    print("All specified models have been downloaded.")


if __name__ == "__main__":
    main()
