import argparse
import sys

def parse_args(args):
    parser = argparse.ArgumentParser(
        description="Multi-Source Candidate Data Transformer CLI (Foundation Skeleton)"
    )
    parser.add_argument(
        "-i", "--inputs",
        nargs="+",
        required=True,
        help="Path(s) to input source file(s) (e.g. JSON, CSV, TXT)"
    )
    parser.add_argument(
        "-c", "--config",
        help="Path to the runtime projection config JSON file"
    )
    parser.add_argument(
        "-o", "--output",
        default="outputs/canonical_candidates.json",
        help="Path to write the final projected JSON output (default: outputs/canonical_candidates.json)"
    )
    return parser.parse_args(args)

def main():
    parsed_args = parse_args(sys.argv[1:])
    print("Multi-Source Candidate Data Transformer CLI")
    print("===========================================")
    print(f"Inputs: {parsed_args.inputs}")
    print(f"Config: {parsed_args.config}")
    print(f"Output: {parsed_args.output}")
    print("Foundation CLI loaded successfully.")

if __name__ == "__main__":
    main()
