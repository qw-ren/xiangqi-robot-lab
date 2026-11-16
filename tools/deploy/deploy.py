"""Package a local Git commit; optional source-only SSH deployment."""
import argparse
import re
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-only', action='store_true')
    parser.add_argument('--host')
    parser.add_argument('--identity', type=Path)
    parser.add_argument('--release-base', default='/home/dofbot/xiangqi-robot-lab/releases')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    dirty = subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True)
    if dirty.strip():
        parser.error('Commit or stash changes before packaging')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if not re.fullmatch('[0-9a-f]{40}', revision):
        parser.error('Invalid commit SHA')
    artifacts = root/'artifacts/deploy'
    artifacts.mkdir(parents=True, exist_ok=True)
    archive = artifacts/(revision+'.tar.gz')
    subprocess.run(['git', 'archive', '--format=tar.gz', '-o', str(archive), 'HEAD'], cwd=root, check=True)
    print('Package:', archive)
    print('Commit:', revision)
    if args.package_only:
        return
    if not args.host or not re.fullmatch('[A-Za-z0-9_.@:-]+', args.host):
        parser.error('Supply a valid --host')
    if not args.identity or not args.identity.is_file():
        parser.error('Supply a readable --identity')
    if not re.fullmatch('/[A-Za-z0-9_./-]+', args.release_base) or '..' in args.release_base:
        parser.error('Invalid --release-base')
    release = args.release_base+'/'+revision
    remote_archive = release+'.tar.gz'
    ssh = ['ssh', '-i', str(args.identity.resolve()), '-o', 'BatchMode=yes', args.host]
    subprocess.run(ssh+["mkdir -p '"+args.release_base+"' && test ! -e '"+release+"'"], check=True)
    subprocess.run(['scp', '-i', str(args.identity.resolve()), str(archive), args.host+':'+remote_archive], check=True)
    subprocess.run(ssh+["mkdir '"+release+"' && tar -xzf '"+remote_archive+"' -C '"+release+"'"], check=True)
    print('Deployed source only:', release)
    print('No dependencies installed or services started/switched.')


if __name__ == '__main__':
    main()
