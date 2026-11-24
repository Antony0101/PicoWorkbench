"""Build unchanged official examples, run their oracles, and record honest results."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from rp2040_sim.sdk_acceptance import validate_example
from rp2040_sim.sdk_examples import examples_directory, load_catalog
from rp2040_sim.sdk_projects import ProjectBuilder, SdkProjectSpec
from rp2040_sim.sdk_tools import ToolchainProfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', action='append')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/phase4-review/sdk-results.json')
    args = parser.parse_args()
    root, catalog, profile = examples_directory(), load_catalog(), ToolchainProfile.discover()
    results = []
    for entry in catalog['workloads']:
        if args.target and entry['target'] not in args.target:
            continue
        print('Building', entry['target'], flush=True)
        job = ProjectBuilder(SdkProjectSpec(str(root), entry['target']), profile,
            log=lambda text: print(text, end='', flush=True))
        try:
            built = job.build()
            if not built.manifest.verified:
                raise ValueError('; '.join(built.manifest.reasons))
            modes = ('sdk', 'rom') if entry['target'] in ('blink', 'hello_serial') else ('rom',)
            for mode in modes:
                try:
                    result = validate_example(entry, built.path, mode)
                except Exception as error:
                    result = {'passed': False, 'target': entry['target'], 'startup': mode, 'error': str(error)}
                results.append(result)
                print('\nRESULT', json.dumps(result), flush=True)
        except Exception as error:
            results.append({'passed': False, 'target': entry['target'], 'error': str(error)})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'upstream': catalog, 'toolchain': profile.to_dict(), 'results': results}, indent=2) + '\n')
    return 0 if results and all(result['passed'] for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
