import argparse
import json
import sys
from typing import Optional
from ..adapters.container import build_default
from ..app.detect import detect_all
from ..app.pseudonymize import pseudonymize
from ..app.denomize import deanonymize


def main(argv: Optional[list[str]] = None):
    parser = argparse.ArgumentParser(description="Anonymization CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_detect = sub.add_parser("detect", help="Detect PII in text")
    p_detect.add_argument("text", help="Input text")
    p_detect.add_argument("--lang", dest="language", help="Language code", default=None)

    p_pseudo = sub.add_parser("pseudonymize", help="Pseudonymize text and store mappings")
    p_pseudo.add_argument("text", help="Input text")
    p_pseudo.add_argument("--context", required=True, help="Context ID for token mappings")
    p_pseudo.add_argument("--lang", dest="language", help="Language code", default=None)

    p_de = sub.add_parser("deanonymize", help="Restore text from tokens using stored mappings")
    p_de.add_argument("text", help="Anonymized text")
    p_de.add_argument("--context", required=True, help="Context ID for token mappings")

    args = parser.parse_args(argv)
    detectors, vault = build_default()

    if args.cmd == "detect":
        res = detect_all(args.text, detectors, language=args.language)
        print(json.dumps({
            "text": res.text,
            "entities": [e.__dict__ for e in res.entities]
        }, ensure_ascii=False, indent=2))
    elif args.cmd == "pseudonymize":
        res = pseudonymize(args.text, detectors, vault, context_id=args.context, language=args.language)
        print(json.dumps({
            "original_text": res.original_text,
            "pseudonymized_text": res.pseudonymized_text,
            "mappings": [m.__dict__ for m in res.mappings]
        }, ensure_ascii=False, indent=2))
    elif args.cmd == "deanonymize":
        res = deanonymize(args.text, vault, context_id=args.context)
        print(json.dumps({
            "anonymized_text": res.anonymized_text,
            "restored_text": res.restored_text,
            "mappings_used": [m.__dict__ for m in res.mappings_used]
        }, ensure_ascii=False, indent=2))
    else:
        parser.error("Unknown command")


if __name__ == "__main__":
    main()

