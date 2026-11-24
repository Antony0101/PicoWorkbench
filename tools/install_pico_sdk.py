"""Install/remove PicoWorkbench's managed SDK, or explicitly install Linux prerequisites."""
from pathlib import Path
import argparse
import os
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from rp2040_sim.sdk_install import install_sdk, managed_sdk, remove_sdk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--install', action='store_true')
    action.add_argument('--remove', action='store_true')
    action.add_argument('--linux-dependencies', action='store_true', help='Run apt-get interactively; requires sudo/admin privileges')
    parser.add_argument('--archive', type=Path, help='Verified SDK archive for an offline installation')
    args = parser.parse_args()
    if args.linux_dependencies:
        command = ['apt-get', 'install', 'gcc-arm-none-eabi', 'libnewlib-arm-none-eabi',
            'libstdc++-arm-none-eabi-dev', 'libstdc++-arm-none-eabi-newlib', 'cmake', 'make', 'g++']
        return subprocess.call((['sudo'] if os.geteuid() else []) + command)
    if args.remove:
        path = managed_sdk()
        if input(f'Remove the managed SDK and any local changes in {path}? [y/N] ').lower() != 'y':
            return 0
        remove_sdk(path)
        print('SDK removed; project source files and external toolchains were preserved.')
    else:
        print(install_sdk(archive=args.archive, progress=print))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
