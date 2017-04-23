# Android Device Testing Framework ("dtf")
# Copyright 2013-2015 Jake Valletta (@jake_valletta)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""dtf public package querying"""

from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

import imp
import inspect
import os
import os.path
import shlex
import sys
import subprocess
import traceback
from contextlib import contextmanager

import dtf.adb as adb
import dtf.core.compat as compat
import dtf.core.packagemanager as pm
import dtf.core.utils as utils
import dtf.logging as log
import dtf.properties as prop
from dtf.exceptions import DtfException
from dtf.globals import (DTF_BINARIES_DIR, DTF_LIBRARIES_DIR,
                         DTF_MODULES_DIR, DTF_DB, DTF_INCLUDED_DIR)

TAG = "dtf-packages"

USAGE_TAGS = ['-h', '--help', 'help']


# Internal
@contextmanager
def stdout_redirector(stream):

    """Redirect stdout to string object"""

    old_stdout = sys.stdout
    sys.stdout = stream
    try:
        yield
    finally:
        sys.stdout = old_stdout


def __update_path():

    """Update path with dtf libraries"""

    # The first time `dtf` is executed, there is no main.db.
    # As such, we basically need to assume that if main.db
    # doesn't exist, don't do this.
    if not os.path.isfile(DTF_DB):
        return 0

    for lib in pm.get_libraries(name_only=True):

        lib_path = "%s/%s" % (DTF_LIBRARIES_DIR, lib)

        sys.path.append(lib_path)

    return 0


def __launch_python_module(path, cmd, args, chdir=True, skip_checks=False):

    """Launch a python module by path"""

    mod_class = None
    mod_inst = None

    # We should always be in TOP (unless we are `pm`).
    # However, we should save off the current directory.
    launch_dir = os.getcwd()

    if chdir and prop.TOP is not None:
        os.chdir(prop.TOP)

    # Next, get the path setup.
    if __update_path() != 0:
        log.e(TAG, "Unable to update library path!")
        return -7

    # If we got here, we try to load as a python module.
    try:
        module = imp.load_source(cmd, path)
    except:  # pylint:disable=bare-except
        msg = sys.exc_info()[0]
        log.e(TAG, "An Exception occured while calling load_source()")
        log.e(TAG, "Exception: %s" % msg)
        return -9

    if module is None:
        log.e(TAG, "Error launching module '%s'." % cmd)
        return -5

    try:
        mod_class = getattr(module, cmd)
        mod_inst = mod_class()
        mod_inst.launch_dir = launch_dir

    except AttributeError:
        log.e(TAG, "Unable to find class '%s' in module!" % cmd)
        return -6

    if not skip_checks and __do_python_prelaunch_checks(mod_inst) != 0:
        log.e(TAG, "Module prelaunch checks failed.")
        return -8

    # Save module name
    mod_inst.__self__ = type(mod_inst).__name__

    # Do python logging override
    try:
        log.LOG_LEVEL_STDOUT = int(os.environ['GLOG_LEVEL'])
    except KeyError:
        pass
    except ValueError:
        log.w(TAG, "Invalid GLOG_LEVEL value (0-5 is allowed)")

    return __do_launch_python_module(mod_inst, args)


def __launch_bash_module(module_path, args):

    """Launch a bash module by path"""

    # First, make sure we can even execute the file.
    if not utils.is_executable(module_path):
        log.e(TAG, "Module is marked executable!")
        return -6

    cmd = list()

    # Build the command string
    cmd = [module_path] + args

    # Update the environment
    new_env = os.environ

    # These are used for sourcing
    new_env['DTF_LOG'] = DTF_INCLUDED_DIR + "/dtf_log.sh"
    new_env['DTF_CORE'] = DTF_INCLUDED_DIR + "/dtf_core.sh"

    # We need to be in TOP to get the serial.
    # First, store the current dir.
    new_env['LAUNCH_DIR'] = os.getcwd()
    os.chdir(prop.TOP)

    # We want the serial to be already set
    serial = adb.get_mode_serial()
    new_env['ANDROID_SERIAL'] = serial

    # Global exception handling
    try:
        popen = subprocess.Popen(cmd, stdout=subprocess.PIPE, env=new_env)
        lines_iterator = iter(popen.stdout.readline, b"")

        for line in lines_iterator:
            sys.stdout.write(line)

        return popen.returncode

    except Exception:  # pylint:disable=broad-except
        try:
            exc_traceback = sys.exc_info()
        finally:
            log.e(TAG, "Unhandled Exception in module!")
            for line in traceback.format_exception(*exc_traceback)[3:]:
                line = line.strip("\n")
                if line == "":
                    continue
                print(line)
        return -5
    except KeyboardInterrupt:
        log.e(TAG, "Bash module forcibly killed!")
        return -6


def __do_python_prelaunch_checks(mod_inst):

    """Do pre-launch checks"""

    # Make sure requirements are met
    for requirement in mod_inst.requires:

        if utils.which(requirement) is None:
            log.e(TAG, "Unable to execute! Unmet dependency: %s"
                  % requirement)
            return -1

    # Check for a minimum SDK
    try:
        sdk = int(prop.get_prop('Info', 'sdk'))
    except prop.PropertyError:
        log.e(TAG, "Unable to get SDK, is this project corrupt?")
        return -1

    min_sdk = int(mod_inst.min_sdk)

    if min_sdk != 0 and sdk < min_sdk:
        log.e(TAG, "This module requires SDK %d or higher!" % min_sdk)
        return -1

    return 0


# pylint:disable=too-many-branches,too-many-return-statements
def __do_launch_python_module(mod_inst, args):

    """Perform the actual launch"""

    rtn = 0

    # There are two ways to launch a module.
    # The first is by having an execute(..) function.
    # The second, is to use the new sub_cmd decorators.
    # We check for execute, and call it. if we dont have it,
    # we look for @sub_cmds, and fail if we dont have any/cant
    # find one.
    try:
        if hasattr(mod_inst, 'execute'):
            return getattr(mod_inst, 'execute')(args)

        # no exec. how about a sub_cmd?
        else:
            sub_cmd_map = __determine_sub_cmd_map(mod_inst)

            # No mappings.
            if len(sub_cmd_map) == 0:
                log.e(TAG, "Module '%s' has no exec or mappings!"
                      % mod_inst.__self__)
                return -14

            # Mappings exist. But do args?
            if len(args) == 0:
                return __auto_generate_usage(mod_inst, sub_cmd_map)

            sub_cmd = args.pop(0)

            # First, check for usage.
            if sub_cmd in USAGE_TAGS:
                return __auto_generate_usage(mod_inst, sub_cmd_map)
            elif sub_cmd in sub_cmd_map:

                entry_method = sub_cmd_map[sub_cmd][0]

                launch = getattr(mod_inst, entry_method.sub_cmd_route)

                if __route_has_args(launch):
                    return launch(args)
                else:
                    return launch()
            else:
                __auto_generate_usage(mod_inst, sub_cmd_map)
                log.e(TAG, "Module '%s' has no mapping for '%s'!"
                      % (mod_inst.__self__, sub_cmd))
                return -15

    # Global exception handling
    except Exception:  # pylint:disable=broad-except
        try:
            exc_traceback = sys.exc_info()
        finally:
            log.e(TAG, "Unhandled Exception in module!")
            for line in traceback.format_exception(*exc_traceback)[3:]:
                line = line.strip("\n")
                if not line:
                    continue
                print(line)

        rtn = -10
    except KeyboardInterrupt:
        log.e(TAG, "Python module forcibly killed!")
        rtn = -11

    return rtn


def __determine_sub_cmd_map(mod_inst):

    """Determine the mapping for sub commands"""

    arg_map = {}

    for _, inst in inspect.getmembers(mod_inst):
        if hasattr(inst, 'sub_cmd_name'):
            arg_map[inst.sub_cmd_name] = (inst, inst.sub_cmd_usage)

    return arg_map


def __route_has_args(method_inst):

    """Check if arguments exist"""

    args = inspect.getargspec(method_inst)

    return bool(len(args[0]) > 1)


def __auto_generate_usage(mod_inst, arg_map):

    """Generate a usage"""

    print("dtf Module %s v%s" % (mod_inst.name, mod_inst.version))
    print("")
    print("Subcommands:")

    for key, args in arg_map.iteritems():
        print("  %s%s" % (key.ljust(13), args[1]))

    print("")

    return 0
# End Internal


# Launching stuff
def launch_builtin_module(cmd, args, chdir=True, skip_checks=False):

    """Launch a dtf built-in python command"""

    launch_path = "%s/core/cmds/%s.py" % (utils.get_pydtf_dir(), cmd)

    return __launch_python_module(launch_path, cmd, args, chdir=chdir,
                                  skip_checks=skip_checks)


def launch_local_module(root, cmd, args):

    """Launch a local module"""

    module_path = "%s/%s/%s" % (root, utils.LOCAL_MODULES_DIRECTORY, cmd)

    # If we are dealing with a bash script, just run and exit.
    if pm.is_bash_module(module_path):
        log.d(TAG, "This is a bash module!")

        return __launch_bash_module(module_path, args)

    return __launch_python_module(module_path, cmd, args)


def launch_module(cmd, args, redirect=False):

    """Launch a global (non-local module)"""

    module_path = "%s/%s" % (DTF_MODULES_DIR, cmd)

    # If the caller explicitly asked to save stdout, lets do it.
    if redirect:
        captured_f = compat.StringIO()

        with stdout_redirector(captured_f):

            if pm.is_bash_module(module_path):
                rtn = __launch_bash_module(module_path, args)
            else:
                rtn = __launch_python_module(module_path, cmd, args)

        out = captured_f.getvalue()
        captured_f.close()

        return out, rtn

    else:
        # If we are dealing with a bash script, just run and exit.
        if pm.is_bash_module(module_path):
            return __launch_bash_module(module_path, args)
        return __launch_python_module(module_path, cmd, args)


def launch_binary(binary, args, launcher=None):

    """Launch a binary"""

    path_to_binary = "%s/%s" % (DTF_BINARIES_DIR, binary)

    if not is_binary_installed(binary):
        raise DtfException("Binary %s not found!" % binary)

    if args is None:
        lex_args = []
    else:
        lex_args = shlex.split(args)

    if launcher is None:
        cmd = [path_to_binary] + lex_args

    else:
        lex_launcher = shlex.split(launcher)
        cmd = lex_launcher + [path_to_binary] + lex_args

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, shell=False)

    stdout = proc.stdout.read().split("\n")
    stderr = proc.stderr.read().split("\n")

    rtn = proc.wait()

    return stdout, stderr, rtn


# Determining if stuff is installed
def is_binary_installed(name):

    """Determine if binary is installed"""

    return pm.is_binary_installed(name)


def is_library_installed(name):

    """Determine if library is installed"""

    return pm.is_library_installed(name)


def is_module_installed(name):

    """Determine if module is installed"""

    return pm.is_module_installed(name)


def is_package_installed(name):

    """Determine if package is installed"""

    return pm.is_package_installed(name)


def find_local_module(root, name):

    """Determine if a local module exists"""

    return pm.find_local_module(root, name)
