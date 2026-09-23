#!/usr/bin/env python3
"""Build and start Orbit using the current rootless Docker engine."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='127.0.0.1', help='Host address for the web interface')
    parser.add_argument('--port', default=18008, type=int)
    parser.add_argument('--name', default='orbit')
    parser.add_argument('--image', default='orbit:local')
    parser.add_argument('--data-volume', default='orbit-data')
    args = parser.parse_args()
    def docker(*command, capture=False):
        return subprocess.run(['docker', *command], cwd=ROOT, check=True, text=True,
                              stdout=subprocess.PIPE if capture else None).stdout
    security = json.loads(docker('info', '--format', '{{json .SecurityOptions}}', capture=True))
    if 'name=rootless' not in security:
        parser.error('Select the rootless Docker engine before deployment.')
    existing = docker('ps', '-a', '--filter', f'name=^/{args.name}$', '--format', '{{.ID}}', capture=True).strip()
    if existing:
        parser.error(f'Container {args.name} already exists. Stop and rename it for rollback before deploying an update.')
    docker('build', '-t', args.image, '.')
    command = ['run', '--detach', '--name', args.name, '--restart', 'unless-stopped',
               '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
               '--pids-limit', '128', '--memory', '1g', '--cpus', '1',
               '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m',
               '--publish', f'{args.bind}:{args.port}:8080',
               '--mount', 'type=bind,source=/,target=/host,readonly,bind-propagation=rslave',
               '--mount', f'type=volume,source={args.data_volume},target=/data',
               '--label', 'org.opencontainers.image.title=Orbit disk usage']
    docker(*command, args.image)
    print(f'Orbit: http://{args.bind}:{args.port}/')


if __name__ == '__main__':
    main()
