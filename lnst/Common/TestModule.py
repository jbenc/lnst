"""
Defines the BaseTestModule class and the TestModuleError exception.

Copyright 2017 Red Hat, Inc.
Licensed under the GNU General Public License, version 2 as
published by the Free Software Foundation; see COPYING for details.
"""

__author__ = """
olichtne@redhat.com (Ondrej Lichtner)
"""

import copy
from lnst.Common.Parameters import Parameters, Param
from lnst.Common.LnstError import LnstError

class TestModuleError(LnstError):
    """Exception used by BaseTestModule and derived classes"""
    pass

class BaseTestModule(object):
    """Base class for test modules

    All user defined testmodule classes should inherit from this class. The
    class itself defines the interface for a test module that is required by
    LNST - the virtual run method.

    It also implements the __init__ method that should be called by the derived
    classes as it implements Parameter checking.

    Derived classes can define the test parameters by assigning 'Param'
    instances to class attributes, these will be parsed during initialization
    and copied to the self.params instance attribute and loaded with values
    provided to the __init__ method. This will also check mandatory attributes.
    """
    def __init__(self, **kwargs):
        """
        Args:
            kwargs -- dictionary of arbitrary named arguments that correspond
                to class attributes (Param type). Values will be parsed and
                set to Param instances under the self.params object.
        """
        #by defaults loads the params into self.params - no checks pseudocode:
        self.params = Parameters()
        for x in dir(self):
            val = getattr(self, x)
            if isinstance(val, Param):
                setattr(self.params, x, copy.deepcopy(val))

        for name, val in kwargs.items():
            try:
                param = getattr(self.params, name)
            except:
                raise TestModuleError("Unknown parameter {}".format(name))
            param.val = val

        for name, param in self.params:
            if param.mandatory and not param.set:
                raise TestModuleError("Parameter {} is mandatory".format(name))

        self._res_data = None

    def run(self):
        raise NotImplementedError("Method 'run' MUST be defined")

    def _get_res_data(self):
        return self._res_data
