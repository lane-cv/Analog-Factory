"""Run with python3 -m analog_factory; no third-party packages are required."""
import argparse
import json
import sys
import webbrowser
from pathlib import Path

from .service import Workspace
from .templates import tuning_workflow


def main(argv=None):
    parser = argparse.ArgumentParser(description='Analog-factory local analog IC workspace')
    parser.add_argument('--workspace', type=Path, default=Path.cwd() / '.workspace', help='Data directory (default: ./.workspace)')
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve', help='Launch local browser workspace')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--open', action='store_true', help='Open the browser')
    sub.add_parser('doctor', help='Report tools, PDK, desktop and integration readiness')
    create = sub.add_parser('create', help='Create a project')
    create.add_argument('name')
    create.add_argument('--template', choices=['amplifier', 'sky130'], default='amplifier')
    sub.add_parser('projects', help='List projects')
    run = sub.add_parser('run', help='Execute a real EDA task and wait for completion')
    run.add_argument('project')
    run.add_argument('--kind', default='simulate', choices=['simulate', 'drc', 'lvs', 'extract', 'xschem', 'magic'])
    run.add_argument('--options', default='{}', help='JSON adapter options')
    run.add_argument('--timeout', type=float, default=60)
    tune = sub.add_parser('tune', help='Run bounded bias tuning with ngspice')
    tune.add_argument('project')
    tune.add_argument('--workflow', type=Path)
    evaluation = sub.add_parser('evaluate', help='Collect isolated random-search baseline episodes')
    evaluation.add_argument('project')
    evaluation.add_argument('--episodes', type=int, default=3)
    evaluation.add_argument('--seed', type=int, default=42)
    demo = sub.add_parser('demo', help='Create amplifier, simulate, and tune; preserve evidence')
    demo.add_argument('--name', default='Amplifier validation')
    export = sub.add_parser('export', help='Archive source files and run evidence')
    export.add_argument('project')
    export.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    workspace = Workspace(args.workspace)
    try:
        if args.command == 'serve':
            from .server import create_server
            server = create_server(workspace, args.port)
            url = f'http://127.0.0.1:{server.server_port}'
            print(f'Analog-factory: {url}\nWorkspace: {workspace.root}', flush=True)
            if args.open:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        if args.command == 'doctor':
            result = workspace.status()
        elif args.command == 'projects':
            result = workspace.projects.list()
        elif args.command == 'create':
            result = workspace.projects.create(args.name, args.template)
        elif args.command == 'run':
            run = workspace.run(args.project, args.kind, json.loads(args.options), timeout=args.timeout)
            result = workspace.runner.wait(workspace.projects.path(args.project), run['id'], timeout=args.timeout + 10)
        elif args.command == 'tune':
            workflow = json.loads(args.workflow.read_text()) if args.workflow else tuning_workflow()
            job = workspace.start_workflow(args.project, workflow)
            result = workspace.jobs.wait(workspace.projects.path(args.project), job['id'], timeout=3610)
        elif args.command == 'evaluate':
            job = workspace.evaluate(args.project, {'episodes': args.episodes, 'seed': args.seed})
            result = workspace.jobs.wait(workspace.projects.path(args.project), job['id'], timeout=1300)
        elif args.command == 'export':
            args.output.write_bytes(workspace.projects.export(args.project))
            result = {'archive': str(args.output.resolve())}
        elif args.command == 'demo':
            project = workspace.projects.create(args.name)
            pid = project['id']
            run = workspace.run(pid, 'simulate', {})
            baseline = workspace.runner.wait(workspace.projects.path(pid), run['id'])
            if baseline['status'] != 'succeeded':
                print(json.dumps(baseline, indent=2))
                return 1
            job = workspace.start_workflow(pid, tuning_workflow())
            tuned = workspace.jobs.wait(workspace.projects.path(pid), job['id'])
            result = {'project': project, 'baseline': baseline, 'tuning': tuned}
            print(json.dumps(result, indent=2))
            return 0 if tuned.get('satisfied') and tuned['status'] == 'succeeded' else 1
        print(json.dumps(result, indent=2))
        return 1 if isinstance(result, dict) and result.get('status') in ('failed', 'cancelled', 'timed_out', 'unavailable', 'interrupted') else 0
    except (ValueError, OSError, RuntimeError, TimeoutError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
