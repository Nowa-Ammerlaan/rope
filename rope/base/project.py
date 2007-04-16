import os
import re

import rope.base.change
import rope.base.fscommands
import rope.base.history
import rope.base.prefs
import rope.base.pycore
from rope.base import taskhandle
from rope.base.exceptions import RopeError
from rope.base.resources import File, Folder


class _Project(object):

    def __init__(self, fscommands):
        self.observers = []
        self.file_access = rope.base.fscommands.FileAccess()
        self._history = None
        self.operations = rope.base.change._ResourceOperations(self, fscommands)
        self.prefs = rope.base.prefs.Prefs()
        self._pycore = None

    def get_resource(self, resource_name):
        """Get a resource in a project.

        `resource_name` is the path of a resource in a project.  It
        is the path of a resource relative to project root.  Project
        root folder address is an empty string.  If the resource does
        not exist a `RopeError` exception would be raised.  Use
        `get_file()` and `get_folder()` when you need to get non-
        existent `Resource`\s.

        """
        path = self._get_resource_path(resource_name)
        if not os.path.exists(path):
            raise RopeError('Resource <%s> does not exist' % resource_name)
        elif os.path.isfile(path):
            return File(self, resource_name)
        elif os.path.isdir(path):
            return Folder(self, resource_name)
        else:
            raise RopeError('Unknown resource ' + resource_name)

    def validate(self, folder):
        """Validate files and folders contained in this folder

        This method asks all registered to validate all of the files
        and folders contained in this folder that they are interested
        in.

        """
        for observer in list(self.observers):
            observer.validate(folder)

    def add_observer(self, observer):
        """Register a `ResourceObserver`

        See `FilteredResourceObserver`.
        """
        self.observers.append(observer)

    def remove_observer(self, observer):
        """Remove a registered `ResourceObserver`"""
        if observer in self.observers:
            self.observers.remove(observer)

    def do(self, changes, task_handle=taskhandle.NullTaskHandle()):
        """Apply the changes in a `ChangeSet`

        Most of the time you call this function for committing the
        changes for a refactoring.
        """
        self.history.do(changes, task_handle=task_handle)

    def get_pycore(self):
        if self._pycore is None:
            self._pycore = rope.base.pycore.PyCore(self)
        return self._pycore

    def get_file(self, path):
        """Get the file with `path`(it may not exist)"""
        return File(self, path)

    def get_folder(self, path):
        """Get the folder with `path`(it may not exist)"""
        return Folder(self, path)

    def is_ignored(self, resource):
        return False

    def close(self):
        """Closes project open resources"""

    def sync(self):
        """Closes project open resources"""
        self.close()

    def get_prefs(self):
        return self.prefs

    def _get_resource_path(self, name):
        pass

    def _get_history(self):
        if self._history is None:
            self._history = rope.base.history.History(self)
        return self._history

    history = property(_get_history)
    pycore = property(get_pycore)


class Project(_Project):
    """A Project containing files and folders"""

    def __init__(self, projectroot, fscommands=None,
                 ropefolder='.ropeproject', **prefs):
        """A rope project

        :parameters:
            - `projectroot`: The address of the root folder of the project
            - `fscommands`: Implements the file system operations rope uses
              have a look at `rope.base.fscommands`
            - `ropefolder`: The name of the folder in which rope stores
              project configurations and data.  Pass `None` for not using
              such a folder at all.
            - `prefs`: Specify project preferences.  These values
              overwrite config file preferences.

        """
        self._address = os.path.abspath(os.path.expanduser(projectroot))
        if not os.path.exists(self._address):
            os.mkdir(self._address)
        elif not os.path.isdir(self._address):
            raise RopeError('Project root exists and is not a directory')
        if fscommands is None:
            fscommands = rope.base.fscommands.create_fscommands(self._address)
        super(Project, self).__init__(fscommands)
        self.ignored = _IgnoredResources()
        self.file_list = _FileListCacher(self)
        self.prefs.add_callback('ignored_resources', self.ignored.set_ignored)
        self.prefs['ignored_resources'] = ['*.pyc', '.svn', '*~', '.ropeproject']
        self._init_rope_folder(ropefolder)
        self._init_prefs(prefs)

    def get_files(self):
        return self.file_list.get_files()

    def _get_resource_path(self, name):
        return os.path.join(self._address, *name.split('/'))

    def _init_rope_folder(self, ropefolder):
        self._ropefolder = None
        if ropefolder is not None:
            self._ropefolder = self.get_folder(ropefolder)
            if not self._ropefolder.exists():
                self._ropefolder.create()

    def _init_prefs(self, prefs):
        run_globals = {}
        if self.ropefolder is not None:
            if self._ropefolder.has_child('config.py'):
                config = self._ropefolder.get_child('config.py')
            else:
                config = self._ropefolder.create_file('config.py')
                config.write(_DEFAULT_CONFIG_PY)
            run_globals.update({'__name__': '__main__',
                                '__builtins__': __builtins__,
                                '__file__': config.real_path})
            execfile(config.real_path, run_globals)
            if 'set_prefs' in run_globals:
                run_globals['set_prefs'](self.prefs)
        for key, value in prefs.items():
            self.prefs[key] = value
        self._init_other_parts()
        if 'project_opened' in run_globals:
            run_globals['project_opened'](self)

    def _init_other_parts(self):
        # Forcing the creation of `self.pycore` to register observers
        self.pycore

    def is_ignored(self, resource):
        return self.ignored.is_ignored(resource)

    def close(self):
        self.pycore.object_info.sync()
        self.history.sync()

    def set(self, key, value):
        """Set the `key` preference to `value`"""
        self.prefs.set(key, value)

    root = property(lambda self: self.get_resource(''))
    address = property(lambda self: self._address)
    ropefolder = property(lambda self: self._ropefolder)


class NoProject(_Project):
    """A null object for holding out of project files.

    This class is singleton use `get_no_project` global function
    """

    def __init__(self):
        fscommands = rope.base.fscommands.FileSystemCommands()
        self.root = None
        super(NoProject, self).__init__(fscommands)

    def _get_resource_path(self, name):
        real_name = os.path.abspath(name).replace('/', os.path.sep)
        return os.path.abspath(real_name)

    def get_resource(self, name):
        universal_name = os.path.abspath(name).replace(os.path.sep, '/')
        return super(NoProject, self).get_resource(universal_name)

    def get_files(self):
        return []

    _no_project = None


def get_no_project():
    if NoProject._no_project is None:
        NoProject._no_project = NoProject()
    return NoProject._no_project


class ResourceObserver(object):
    """Provides the interface for observing resources

    `ResourceObserver`\s can be registered using `Project.
    add_observer()`.  But most of the time `FilteredResourceObserver`
    should be used.  `ResourceObserver`\s report all changes passed
    to them and they don't report changes to all resources.  For
    example if a folder is removed, it only calls `removed()` for that
    folder and not its contents.  You can use
    `FilteredResourceObserver` if you are interested in changes only
    to a list of resources.  And you want changes to be reported on
    individual resources.

    """

    def __init__(self, changed=None, moved=None, created=None,
                 removed=None, validate=None):
        self.changed = changed
        self.moved = moved
        self.created = created
        self.removed = removed
        self._validate = validate

    def resource_changed(self, resource):
        """It is called when the resource changes"""
        if self.changed is not None:
            self.changed(resource)

    def resource_moved(self, resource, new_resource):
        """It is called when a resource is moved"""
        if self.moved is not None:
            self.moved(resource, new_resource)

    def resource_created(self, resource):
        """Is called when a new resource is created"""
        if self.created is not None:
            self.created(resource)

    def resource_removed(self, resource):
        """Is called when a new resource is removed"""
        if self.removed is not None:
            self.removed(resource)

    def validate(self, resource):
        """Validate the existence of this resource and its children.

        This function is called when rope need to update its resource
        cache about the files that might have been changed or removed
        by other processes.

        """
        if self._validate is not None:
            self._validate(resource)


class FilteredResourceObserver(object):
    """A useful decorator for `ResourceObserver`

    Most resource observers have a list of resources and are
    interested only in changes to those files.  This class satisfies
    this need.  It dispatches resource changed and removed messages.
    It performs these tasks:

    * Changes to files and folders are analyzed to check whether any
      of the interesting resources are changed or not.  If they are,
      it reports these changes to `resource_observer` passed to the
      constructor.
    * When a resource is removed it checks whether any of the
      interesting resources are contained in that folder and reports
      them to `resource_observer`.
    * When validating a folder it validates all of the interesting
      files in that folder.

    Since most resource observers are interested in a list of
    resources that change over time, `add_resource` and
    `remove_resource` might be useful.

    """

    def __init__(self, resource_observer, initial_resources=None,
                 timekeeper=None):
        self.observer = resource_observer
        self.resources = {}
        if timekeeper is not None:
            self.timekeeper = timekeeper
        else:
            self.timekeeper = Timekeeper()
        if initial_resources is not None:
            for resource in initial_resources:
                self.add_resource(resource)

    def add_resource(self, resource):
        """Add a resource to the list of interesting resources"""
        if resource.exists():
            self.resources[resource] = self.timekeeper.getmtime(resource)
        else:
            self.resources[resource] = None

    def remove_resource(self, resource):
        """Add a resource to the list of interesting resources"""
        if resource in self.resources:
            del self.resources[resource]

    def resource_changed(self, resource):
        changes = _Changes()
        self._update_changes_caused_by_changed(changes, resource)
        self._perform_changes(changes)

    def _update_changes_caused_by_changed(self, changes, changed):
        if changed in self.resources:
            changes.add_changed(changed)
        if self._is_parent_changed(changed):
            changes.add_changed(changed.parent)

    def _update_changes_caused_by_moved(self, changes, resource,
                                        new_resource=None):
        if resource in self.resources:
            changes.add_removed(resource, new_resource)
        if new_resource in self.resources:
            changes.add_created(new_resource)
        if resource.is_folder():
            for file in list(self.resources):
                if resource.contains(file):
                    new_file = self._calculate_new_resource(
                        resource, new_resource, file)
                    changes.add_removed(file, new_file)
        if self._is_parent_changed(resource):
            changes.add_changed(resource.parent)
        if new_resource is not None:
            if self._is_parent_changed(new_resource):
                changes.add_changed(new_resource.parent)

    def _is_parent_changed(self, child):
        return child.parent in self.resources

    def resource_moved(self, resource, new_resource):
        changes = _Changes()
        self._update_changes_caused_by_moved(changes, resource, new_resource)
        self._perform_changes(changes)

    def resource_created(self, resource):
        changes = _Changes()
        self._update_changes_caused_by_created(changes, resource)
        self._perform_changes(changes)

    def _update_changes_caused_by_created(self, changes, resource):
        if resource in self.resources:
            changes.add_created(resource)
        if self._is_parent_changed(resource):
            changes.add_changed(resource.parent)

    def resource_removed(self, resource):
        changes = _Changes()
        self._update_changes_caused_by_moved(changes, resource)
        self._perform_changes(changes)

    def _perform_changes(self, changes):
        for resource in changes.changes:
            self.observer.resource_changed(resource)
            self.resources[resource] = self.timekeeper.getmtime(resource)
        for resource, new_resource in changes.moves.iteritems():
            self.resources[resource] = None
            if new_resource is not None:
                self.observer.resource_moved(resource, new_resource)
            else:
                self.observer.resource_removed(resource)
        for resource in changes.creations:
            self.observer.resource_created(resource)
            self.resources[resource] = self.timekeeper.getmtime(resource)

    def validate(self, resource):
        moved = self._search_resource_moves(resource)
        changed = self._search_resource_changes(resource)
        changes = _Changes()
        for file in moved:
            if file in self.resources:
                self._update_changes_caused_by_moved(changes, file)
        for file in changed:
            if file in self.resources:
                self._update_changes_caused_by_changed(changes, file)
        for file in self._search_resource_creations(resource):
            if file in self.resources:
                changes.add_created(file)
        self._perform_changes(changes)

    def _search_resource_creations(self, resource):
        creations = set()
        if resource in self.resources and resource.exists() and \
           self.resources[resource] == None:
            creations.add(resource)
        if resource.is_folder():
            for file in self.resources:
                if file.exists() and resource.contains(file) and \
                   self.resources[file] == None:
                    creations.add(file)
        return creations

    def _search_resource_moves(self, resource):
        all_moved = set()
        if resource in self.resources and not resource.exists():
            all_moved.add(resource)
        if resource.is_folder():
            for file in self.resources:
                if resource.contains(file):
                    if not file.exists():
                        all_moved.add(file)
        moved = set(all_moved)
        for folder in [file for file in all_moved if file.is_folder()]:
            if folder in moved:
                for file in list(moved):
                    if folder.contains(file):
                        moved.remove(file)
        return moved

    def _search_resource_changes(self, resource):
        changed = set()
        if resource in self.resources and self._is_changed(resource):
            changed.add(resource)
        if resource.is_folder():
            for file in self.resources:
                if file.exists() and resource.contains(file):
                    if self._is_changed(file):
                        changed.add(file)
        return changed

    def _is_changed(self, resource):
        if self.resources[resource] is None:
            return False
        return self.resources[resource] != self.timekeeper.getmtime(resource)

    def _calculate_new_resource(self, main, new_main, resource):
        if new_main is None:
            return None
        diff = resource.path[len(main.path):]
        return resource.project.get_resource(new_main.path + diff)


class Timekeeper(object):

    def getmtime(self, resource):
        """Return the modification time of a `Resource`."""
        return os.path.getmtime(resource.real_path)


class _Changes(object):

    def __init__(self):
        self.changes = set()
        self.creations = set()
        self.moves = {}

    def add_changed(self, resource):
        self.changes.add(resource)

    def add_removed(self, resource, new_resource=None):
        self.moves[resource] = new_resource

    def add_created(self, resource):
        self.creations.add(resource)


class _IgnoredResources(object):

    def __init__(self):
        self.patterns = []
        self._ignored_patterns = []

    def set_ignored(self, patterns):
        """Specify which resources to ignore

        `patterns` is a `list` of `str`\s that can contain ``*`` and
        ``?`` signs for matching resource names.

        """
        self._ignored_patterns = None
        self.patterns = patterns

    def _add_ignored_pattern(self, pattern):
        re_pattern = pattern.replace('.', '\\.').\
                     replace('*', '.*').replace('?', '.')
        re_pattern = '(.*/)?' + re_pattern + '(/.*)?'
        self.ignored_patterns.append(re.compile(re_pattern))

    def is_ignored(self, resource):
        for pattern in self.ignored_patterns:
            if pattern.match(resource.path):
                return True
        return False

    def _get_compiled_patterns(self):
        if self._ignored_patterns is None:
            self._ignored_patterns = []
            for pattern in self.patterns:
                self._add_ignored_pattern(pattern)
        return self._ignored_patterns

    ignored_patterns = property(_get_compiled_patterns)


class _FileListCacher(object):

    def __init__(self, project):
        self.project = project
        self._list = None
        self.observer = None

    def get_files(self):
        if self._list is None:
            if self.observer is None:
                self._init_observer()
            self._list = self._get_files_recursively(self.project.root)
        return self._list

    def _get_files_recursively(self, folder):
        result = set()
        for file in folder.get_files():
            if not self.project.is_ignored(file):
                result.add(file)
        for child in folder.get_folders():
            if not self.project.is_ignored(child):
                result.update(self._get_files_recursively(child))
        return result

    def _init_observer(self):
        if self.observer is None:
            self.observer = ResourceObserver(
                self._changed, self._moved, self._created,
                self._removed, self._validate)
            self.project.add_observer(self.observer)

    def _changed(self, resource):
        if resource.is_folder():
            self._list = None

    def _moved(self, resource, new_resource):
        if resource.is_folder():
            self._list = None
        elif self._list is not None:
            self._removed(resource)
            self._created(new_resource)

    def _created(self, resource):
        if not resource.is_folder() and self._list is not None:
            if not self.project.is_ignored(resource):
                self._list.add(resource)

    def _removed(self, resource):
        if resource.is_folder():
            self._list = None
        else:
            if self._list is not None and resource in self._list:
                self._list.remove(resource)

    def _validate(self, resource):
        self._list = None


_DEFAULT_CONFIG_PY = '''# The default ``config.py``


def set_prefs(prefs):
    """This function is called before the project is opened"""

    # Specify which files and folders to ignore in the project.
    # Changes to ignored resources are not added to the history and
    # VCSs.  Also they are not shown in "Find File" dialog.
    prefs['ignored_resources'] = ['*.pyc', '.svn', '*~', '.ropeproject']

    # Possible values are 'memory' and 'shelve' for now.  The default
    # is 'memory'.  If 'shelve', object information is saved to disk
    # so that it can be used in future sessions.
    prefs['objectdb_type'] = 'shelve'

    # Shows whether to save history across sessions.  Defaults to
    # `False`.
    prefs['save_history'] = True
    prefs['max_history_items'] = 100

    # If `False` when running modules or unit tests "Dynamic Object
    # Inference" is turned off.  This makes them much faster.  The
    # default is `True`.
    prefs['perform_doi'] = True

    # Rope can test the validity of its object DB when running.  You
    # can turn this feature off by using `False`.  Defaults to
    # `True`.
    prefs['validate_objectdb'] = True

    # If `True`, rope will analyze each module after it is saved.
    prefs['automatic_soi'] = True

    # Set the number spaces used for indenting.  According to
    # :PEP:`8`, it is best to use 4 spaces.  Since most of rope's
    # unit-tests use 4 spaces it is more reliable, too.
    prefs['indent_size'] = 4


def project_opened(project):
    """This function is called after the project is opened"""
    # Do whatever you like here!
'''
