import difflib
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
import warnings

from StringIO import StringIO

from nose.tools import assert_equal

from pythoscope.generator import add_tests_to_project
from pythoscope.logger import DEBUG, INFO, get_output, log, set_output
from pythoscope.store import CodeTreesManager, CodeTreeNotFound, Execution, \
    Function, ModuleNotFound, PointOfEntry, Project
from pythoscope.util import quoted_block, read_file_contents, set, \
    write_content_to_file


UNPICKABLE_OBJECT = types.ClassType('class', (), {})

DATA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), 'data'))

def data(name):
    "Return a location of a test data with a given name."
    return os.path.join(DATA_PATH, name)

def read_data(name):
    return read_file_contents(data(name))

def P(path):
    "Convert given path with slashes to proper format for OS we're running on."
    return os.path.join(*path.split("/"))

def assert_length(collection, expected_length):
    actual_length = len(collection)
    assert expected_length == actual_length,\
           "Expected collection to have %d elements, it had %d instead." %\
           (expected_length, actual_length)

def assert_contains(haystack, needle):
    assert needle in haystack,\
           "Expected\n%s\nto contain %r, but it didn't." % (quoted_block(haystack), needle)

def assert_contains_once(haystack, needle):
    repeated = len(re.findall(re.escape(needle), haystack))
    assert repeated == 1, "Expected\n%s\nto contain %r once, but it contained it %d times instead." %\
           (quoted_block(haystack), needle, repeated)

def assert_doesnt_contain(haystack, needle):
    assert needle not in haystack,\
           "Expected\n%s\nto NOT contain %r, but it did." % (quoted_block(haystack), needle)

def assert_single_class(info, name):
    assert_length(info.classes, 1)
    assert_equal(name, info.classes[0].name)

def assert_function(function, name, args):
    assert_equal(name, function.name)
    assert_equal(args, function.args)

def assert_single_function(info, name, args=None):
    assert_length(info.functions, 1)
    assert_equal(name, info.functions[0].name)
    if args is not None:
        assert_equal(args, info.functions[0].args)

def assert_equal_sets(collection1, collection2):
    """Assert that both collections have the same number and set of elements.
    """
    # Checking length of both collections first, so we catch duplicates that
    # appear in one collection and not the other.
    assert_length(collection2, len(collection1))
    assert_equal(set(collection1), set(collection2))

def assert_equal_strings(s1, s2):
    assert_equal(s1, s2, "Strings not equal. Diff:\n\n%s" % ''.join(difflib.ndiff(s1.splitlines(True), s2.splitlines(True))))

def assert_not_raises(exception, callable):
    try:
        callable()
    except exception:
        assert False, "Exception %s has been raised." % exception

def assert_instance(obj, objtype):
    assert isinstance(obj, objtype), \
           "Expected object %r to be of type %r, it was of type %r instead." % \
           (obj, objtype, type(obj))

def assert_matches(regexp, string, anywhere=False):
    if anywhere:
        match = re.search
    else:
        match = re.match
    assert match(regexp, string), \
        "Expected\n%s\nto match r'%s', but it didn't." % (quoted_block(string), regexp)

class PointOfEntryMock(PointOfEntry):
    def __init__(self, project=None, name="poe", content=""):
        if project is None:
            project = Project('.')
        PointOfEntry.__init__(self, project, name)
        self.content = content

    def clear_previous_run(self):
        pass

    def get_content(self):
        return self.content

# Use memory to store code trees during testing, not the file system.
class MemoryCodeTreesManager(CodeTreesManager):
    def __init__(self, code_trees_path):
        self.code_trees_path = code_trees_path
        self._code_trees = {}

    def remember_code_tree(self, code_tree, module_subpath):
        self._code_trees[module_subpath] = code_tree

    def recall_code_tree(self, module_subpath):
        try:
            return self._code_trees[module_subpath]
        except KeyError:
            raise CodeTreeNotFound(module_subpath)

    def forget_code_tree(self, module_subpath):
        self._code_trees.pop(module_subpath, None)

def EmptyProject():
    project = Project(path=os.path.realpath("."),
                      code_trees_manager_class=MemoryCodeTreesManager)
    # Preserve the default value.
    project.new_tests_directory = "tests"
    return project

def ProjectInDirectory(project_path):
    putdir(project_path, ".pythoscope")
    putdir(project_path, P(".pythoscope/code-trees"))
    project = Project(project_path)
    return project

def with_modules(project, paths, create_files=True):
    for path in paths:
        project.create_module(os.path.join(project.path, path))
        if create_files:
            putfile(project.path, path, "")
    return project
Project.with_modules = with_modules

def with_points_of_entry(project, paths):
    poe_path = putdir(project.path, P(".pythoscope/points-of-entry"))
    for path in paths:
        putfile(poe_path, path, "")
    return project
Project.with_points_of_entry = with_points_of_entry

def ProjectWithModules(paths, project_type=EmptyProject):
    project = project_type()
    for path in paths:
        project.create_module(os.path.join(project.path, path))
    return project

def TestableProject(path, more_modules=[]):
    project = ProjectWithModules(["module.py"] + more_modules, lambda: ProjectInDirectory(path))
    project["module"].add_object(Function("function"))
    return project
TestableProject.__test__ = False

def EmptyProjectExecution():
    return Execution(EmptyProject())

def get_test_module_contents(project):
    """Get contents of the first test module of a project.
    """
    try:
        return project[P("tests/test_module.py")].get_content()
    except ModuleNotFound:
        return "" # No test module was generated.
get_test_module_contents.__test__ = False

def generate_single_test_module(template='unittest', **module_kwds):
    """Return test module contents generated for given module.
    """
    project = EmptyProject()
    project.create_module("module.py", **module_kwds)
    add_tests_to_project(project, ["module.py"], template, False)
    return get_test_module_contents(project)
generate_single_test_module.__test__ = False

def get_test_cases(project):
    return list(project.iter_test_cases())
get_test_cases.__test__ = False

def last_exception_as_string():
    exc_type, exc_value = sys.exc_info()[:2]
    # Special case for string exceptions.
    if isinstance(exc_type, str):
        return exc_type
    else:
        return repr(exc_value)

###############################################################################
# Temporary directories/files helpers

def tmpdir():
    return tempfile.mkdtemp(prefix="pythoscope-")

def mkdirs(path):
    try:
        os.makedirs(path)
    except OSError, err:
        # os.makedirs raises OSError(17, 'File exists') when last part of
        # the path exists and we don't care.
        if err.errno != 17:
            raise

def putfile(directory, filename, contents):
    filepath = os.path.join(directory, filename)
    mkdirs(os.path.dirname(filepath))
    write_content_to_file(contents, filepath)
    return filepath

def putdir(directory, dirname):
    dirpath = os.path.join(directory, dirname)
    mkdirs(dirpath)
    return dirpath

rmtree = shutil.rmtree

###############################################################################
# Test superclasses
#   Subclass one of those to get a desired test fixture.

class Test(object):
    """Common ancestor for all test classes to make super on setUp and tearDown
    work properly. We can't use unittest.TestCase, because it doesn't allow
    nose generator methods.
    """
    def setUp(self):
        pass
    def tearDown(self):
        pass

class TempDirectory(Test):
    def setUp(self):
        self.tmpdir = tmpdir()
        super(TempDirectory, self).setUp()

    def tearDown(self):
        rmtree(self.tmpdir)
        super(TempDirectory, self).tearDown()

class CustomSeparator(Test):
    """Subclass CustomSeparator to test your code with alternative os.path.sep.
    """
    def setUp(self):
        self.old_sep = os.path.sep
        os.path.sep = '#'
        super(CustomSeparator, self).setUp()

    def tearDown(self):
        os.path.sep = self.old_sep
        super(CustomSeparator, self).tearDown()

class CapturedLogger(Test):
    """Capture all log output and make it available to test via
    _get_log_output() method.
    """
    log_level = INFO

    def setUp(self):
        self._old_output = get_output()
        self._old_level = log.level
        self.captured = StringIO()
        set_output(self.captured)
        log.level = self.log_level
        super(CapturedLogger, self).setUp()

    def tearDown(self):
        set_output(self._old_output)
        log.level = self._old_level
        super(CapturedLogger, self).tearDown()

    def _get_log_output(self):
        return self.captured.getvalue()

class CapturedDebugLogger(CapturedLogger):
    log_level = DEBUG

class IgnoredWarnings(Test):
    def setUp(self):
        warnings.filterwarnings('ignore')
        super(IgnoredWarnings, self).setUp()

    def tearDown(self):
        warnings.resetwarnings()
        super(IgnoredWarnings, self).tearDown()
