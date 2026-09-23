#!/usr/bin/env python3
"""Build and start Orbit using the current rootless Docker engine."""
import argparse
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='192.168.20.10', help='Private host address for the web interface')
    parser.add_argument('--port', default=18008, type=int)
    parser.add_argument('--name', default='orbit')
    parser.add_argument('--image', default='orbit:local')
    parser.add_argument('--config', default='volumes.json', help='Host scan-target configuration')
    parser.add_argument('--config-volume', default='orbit-config')
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
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text())
    docker('build', '-t', args.image, '.')
    docker('volume', 'create', args.config_volume)
    helper = f'{args.name}-config-{uuid.uuid4().hex[:12]}'
    helper_created = False
    try:
        docker('create', '--name', helper,
               '--mount', f'type=volume,source={args.config_volume},target=/config',
               args.image, 'python', '-c',
               "import os; os.chmod('/config/volumes.json', 0o444)")
        helper_created = True
        docker('cp', str(config_path), f'{helper}:/config/volumes.json')
        docker('start', '--attach', helper)
    finally:
        if helper_created:
            docker('rm', helper)
    command = ['run', '--detach', '--name', args.name, '--restart', 'unless-stopped',
               '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
               '--pids-limit', '128', '--memory', '1g', '--cpus', '1',
               '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m',
               '--publish', f'{args.bind}:{args.port}:8080',
               '--env', 'ORBIT_CONFIG=/config/volumes.json',
               '--mount', f'type=volume,source={args.config_volume},target=/config,readonly',
               '--mount', f'type=volume,source={args.data_volume},target=/data',
               '--label', 'org.opencontainers.image.title=Orbit disk usage']
    for volume in config['volumes']:
        # Docker requires slave propagation when the source contains its own
        # data root. Current kernels make existing recursive mounts read-only.
        source = volume.get('mount', volume['path'])
        mode = 'bind-propagation=rslave' if source == '/' else 'bind-recursive=readonly,bind-propagation=rprivate'
        command += ['--mount', f"type=bind,source={source},target={volume['path']},readonly,{mode}"]
    docker(*command, args.image)
    print(f'Orbit: http://{args.bind}:{args.port}/')


if __name__ == '__main__':
    main()
