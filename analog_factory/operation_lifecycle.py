"""Project lifecycle handlers shared by browser, HTTP, CLI and scoped agents."""
import base64
import binascii
import io
import zipfile

from .operation_catalog import (DOWNLOAD, ID, PATH, PROJECT, TEXT, WRITE, _download,
                                _example, _project, array_schema, object_schema)
from .operations import Operation
from .project_lifecycle import ArchiveLimits, ProjectLifecycle


TRASH_FILE = object_schema({'id': ID, 'path': PATH, 'trashed_at': TEXT, 'sha256': TEXT,
                           'size': {'type': 'integer', 'minimum': 0}},
                          ['id', 'path', 'trashed_at', 'sha256', 'size'])


def register_lifecycle(registry):
    lifecycle = ProjectLifecycle(registry.workspace.projects, registry.workspace)
    sample_zip = io.BytesIO()
    with zipfile.ZipFile(sample_zip, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('README.txt', (1980, 1, 1, 0, 0, 0)), 'Imported project source files.\n')
    examples = {'project.rename': {'name': 'Reviewed amplifier'},
                'file.move': {'destination': 'testbenches/reviewed.cir'},
                'project.import': {'content_base64': base64.b64encode(sample_zip.getvalue()).decode()}}

    def add(operation_id, name, description, schema, output, handler, *, mutation=False,
            undo=False, effects=(), preconditions=()):
        module = operation_id.split('.')[0]
        example = _example(operation_id, schema)[0]
        example['parameters'].update(examples.get(operation_id, {}))
        registry.register(Operation(
            operation_id=operation_id, name=name, description=description, module=module,
            input_schema=schema, output_schema=output, handler=handler,
            permissions=('write',) if mutation else ('read',),
            kind='business' if mutation else 'query', confirmation='agent' if mutation else 'none',
            versioned=mutation, undo=undo, idempotent=not mutation,
            side_effects=effects, preconditions=preconditions, tags=(module, 'lifecycle'),
            examples=(example,)))

    def import_project(context, parameters):
        if context.project_id:
            raise PermissionError('Project-scoped agents cannot import a different project')
        try:
            blob = base64.b64decode(parameters['content_base64'], validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError('content_base64 must contain a valid base64 ZIP archive') from error
        return lifecycle.import_archive(blob, parameters.get('name'),
                                        configure_allowed='configure' in context.permissions)

    def trashed(context, parameters):
        pid = parameters.get('project_id') or context.project_id
        return [p for p in lifecycle.trashed_projects() if not pid or p['id'] == pid]

    idle = ('No active run, orchestration job or agent turn owns the project',)
    add('project.rename', 'Rename project', 'Change a project display name while retaining its identity and all records.',
        _project({'name': {'type': 'string', 'minLength': 1, 'maxLength': 100}}, ['name']), PROJECT,
        lambda c, p: lifecycle.rename_project(p['project_id'], p['name']), mutation=True, undo=True,
        effects=('Updates project display name and modification time',))
    add('project.trash', 'Move project to trash',
        'Hide a project using a reversible tombstone while preserving its files, history and evidence.',
        _project(), PROJECT, lambda c, p: lifecycle.trash_project(p['project_id']),
        mutation=True, undo=True, preconditions=idle,
        effects=('Marks the project trashed and removes it from the active project list',))
    add('project.trash.list', 'List trashed projects',
        'List reversible project tombstones, restricted to the bound project for scoped callers.',
        object_schema({'project_id': ID}), array_schema(PROJECT), trashed)
    add('project.restore', 'Restore trashed project',
        'Restore a project from its tombstone with its original identifier and contents.',
        _project(), PROJECT, lambda c, p: lifecycle.restore_project(p['project_id']),
        mutation=True, undo=True, preconditions=idle,
        effects=('Removes the project tombstone and makes it selectable again',))
    add('project.import', 'Import project or restore backup',
        'Validate a bounded ZIP and atomically publish a new project; restores valid configuration/tool overrides only with configure permission. Imported execution evidence remains unverified and cannot replay archived commands.',
        object_schema({'content_base64': {'type': 'string', 'minLength': 1,
                                          'maxLength': 4 * ((ArchiveLimits().compressed_bytes + 2) // 3),
                                          'description': 'Base64 ZIP; at most 2 MiB compressed, 64 MiB expanded, 16 MiB per file, 4096 entries and 200:1 expansion.'},
                       'name': {'type': 'string', 'minLength': 1, 'maxLength': 100}}, ['content_base64']),
        PROJECT, import_project, mutation=True,
        preconditions=('Workspace-level caller; import never replaces an existing project',
                       'Archives containing nonempty project configuration also require configure permission'),
        effects=('Creates one new project only after every archive entry and record passes validation',))
    add('project.backup', 'Download complete project backup',
        'Create a checksummed ZIP of sources, revisions, file trash, conversations and evidence; excludes credentials and native agent state.',
        _project(), DOWNLOAD,
        lambda c, p: _download(lifecycle.backup(p['project_id']),
                              f'analog-factory-backup-{p["project_id"][:8]}.zip', 'application/zip'),
        preconditions=idle)
    add('file.move', 'Move or rename file',
        'Move one visible file to an unused path, carrying its revision history and refusing overwrite.',
        _project({'path': PATH, 'destination': PATH}, ['path', 'destination']),
        object_schema({'path': PATH, 'previous_path': PATH, 'sha256': TEXT}, ['path', 'previous_path', 'sha256']),
        lambda c, p: lifecycle.move_file(p['project_id'], p['path'], p['destination']),
        mutation=True, undo=True, preconditions=idle,
        effects=('Moves source content and revisions to the destination; never overwrites an existing file',))
    add('file.trash', 'Move file to trash', 'Move a visible file and its revisions into reversible project-local trash.',
        _project({'path': PATH}, ['path']), TRASH_FILE,
        lambda c, p: lifecycle.trash_file(p['project_id'], p['path']),
        mutation=True, undo=True, preconditions=idle,
        effects=('Stores the file and its history under a new trash identifier and removes the visible file',))
    add('file.trash.list', 'List trashed files', 'List file trash records and original paths in one project.',
        _project(), array_schema(TRASH_FILE), lambda c, p: lifecycle.trashed_files(p['project_id']))
    add('file.restore_deleted', 'Restore trashed file',
        'Restore content and history to the original path or a supplied unused destination; refuses overwrite.',
        _project({'trash_id': ID, 'destination': PATH}, ['trash_id']), WRITE,
        lambda c, p: lifecycle.restore_file(p['project_id'], p['trash_id'], p.get('destination')),
        mutation=True, undo=True, preconditions=idle,
        effects=('Restores a trashed file and its revision history, then removes its trash record',))
