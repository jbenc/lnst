"""
This module defines the InterfaceManager class that contains a database of
the available devices, handles netlink messages updating these devices and
provides an interface for creating software interfaces from config objects.

Copyright 2014 Red Hat, Inc.
Licensed under the GNU General Public License, version 2 as
published by the Free Software Foundation; see COPYING for details.
"""

__author__ = """
olichtne@redhat.com (Ondrej Lichtner)
"""

import re
import select
import logging
from lnst.Slave.NetConfigCommon import get_option
from lnst.Common.NetUtils import normalize_hwaddr
from lnst.Common.NetUtils import scan_netdevs
from lnst.Common.ExecCmd import exec_cmd
from lnst.Common.ConnectionHandler import recv_data
from lnst.Common.DeviceError import DeviceNotFound
from lnst.Common.InterfaceManagerError import InterfaceManagerError
from lnst.Slave.DevlinkManager import DevlinkManager
from pyroute2 import IPRSocket
from pyroute2.netlink.rtnl import RTNLGRP_IPV4_IFADDR
from pyroute2.netlink.rtnl import RTNLGRP_IPV6_IFADDR
from pyroute2.netlink.rtnl import RTNLGRP_LINK
try:
    from pyroute2.netlink.iproute import RTM_NEWLINK
    from pyroute2.netlink.iproute import RTM_DELLINK
    from pyroute2.netlink.iproute import RTM_NEWADDR
    from pyroute2.netlink.iproute import RTM_DELADDR
except ImportError:
    from pyroute2.iproute import RTM_NEWLINK
    from pyroute2.iproute import RTM_DELLINK
    from pyroute2.iproute import RTM_NEWADDR
    from pyroute2.iproute import RTM_DELADDR

NL_GROUPS = RTNLGRP_IPV4_IFADDR | RTNLGRP_IPV6_IFADDR | RTNLGRP_LINK

class InterfaceManager(object):
    def __init__(self, server_handler):
        self._device_classes = {}

        self._devices = {} #if_index to device

        self._nl_socket = IPRSocket()
        self._nl_socket.bind(groups=NL_GROUPS)

        self._dl_manager = DevlinkManager()

        self._server_handler = server_handler

    def clear_dev_classes(self):
        self._device_classes = {}

    def add_device_class(self, name, cls):
        if name in self._device_classes:
            raise InterfaceManagerError("Device class name conflict %s" % name)

        self._device_classes[name] = cls
        return cls

    def reconnect_netlink(self):
        if self._nl_socket != None:
            self._nl_socket.close()
            self._nl_socket = None
        self._nl_socket = IPRSocket()
        self._nl_socket.bind(groups=NL_GROUPS)

        self.rescan_devices()

    def get_nl_socket(self):
        return self._nl_socket

    def rescan_devices(self):
        devices_to_remove = self._devices.keys()
        devs = scan_netdevs()
        for dev in devs:
            if dev['index'] not in self._devices:
                device = self._device_classes["Device"](self)
                device._init_netlink(dev['netlink_msg'])
                self._devices[dev['index']] = device

                update_msg = {"type": "dev_created",
                              "dev_data": device._get_if_data()}
                self._server_handler.send_data_to_ctl(update_msg)
            else:
                self._devices[dev['index']]._update_netlink(dev['netlink_msg'])
                devices_to_remove.remove(dev['index'])

            self._devices[dev['index']]._clear_ips()
            for addr_msg in dev['ip_addrs']:
                self._devices[dev['index']]._update_netlink(addr_msg)
        for i in devices_to_remove:
            dev_name = self._devices[i].name
            logging.debug("Deleting Device with if_index %d, name %s because "\
                          "it doesn't exist anymore." % (i, dev_name))

            self._devices[i]._deleted = True
            del self._devices[i]

            del_msg = {"type": "dev_deleted",
                       "if_index": i}
            self._server_handler.send_data_to_ctl(del_msg)

        self._dl_manager.rescan_ports()
        for device in self._devices.values():
            dl_port = self._dl_manager.get_port(device.name)
            device._set_devlink(dl_port)

    def handle_netlink_msgs(self, msgs):
        for msg in msgs:
            self._handle_netlink_msg(msg)

        self._dl_manager.rescan_ports()
        for device in self._devices.values():
            dl_port = self._dl_manager.get_port(device.name)
            device._set_devlink(dl_port)

    def _handle_netlink_msg(self, msg):
        if msg['header']['type'] in [RTM_NEWLINK, RTM_NEWADDR, RTM_DELADDR]:
            if msg['index'] in self._devices:
                self._devices[msg['index']]._update_netlink(msg)
            elif msg['header']['type'] == RTM_NEWLINK:
                dev = self._device_classes["Device"](self)
                dev._init_netlink(msg)
                self._devices[msg['index']] = dev

                update_msg = {"type": "dev_created",
                              "dev_data": dev._get_if_data()}
                self._server_handler.send_data_to_ctl(update_msg)
        elif msg['header']['type'] == RTM_DELLINK:
            if msg['index'] in self._devices:
                dev = self._devices[msg['index']]
                dev._deleted = True

                del self._devices[msg['index']]

                del_msg = {"type": "dev_deleted",
                           "if_index": msg['index']}
                self._server_handler.send_data_to_ctl(del_msg)
        else:
            return

    def get_device(self, if_index):
        self.rescan_devices()
        if if_index in self._devices:
            return self._devices[if_index]
        else:
            raise DeviceNotFound()

    def get_devices(self):
        self.rescan_devices()
        return self._devices.values()

    def get_device_by_hwaddr(self, hwaddr):
        self.rescan_devices()
        for dev in self._devices.values():
            if dev.hwaddr == hwaddr:
                return dev
        raise DeviceNotFound()

    def get_device_by_name(self, name):
        self.rescan_devices()
        for dev in self._devices.values():
            if dev.name == name:
                return dev
        raise DeviceNotFound()

    def get_device_by_params(self, params):
        self.rescan_devices()
        matched = None
        for dev in self._devices.values():
            matched = dev
            dev_data = dev.get_if_data()
            for key, value in params.iteritems():
                if key not in dev_data or dev_data[key] != value:
                    matched = None
                    break

            if matched:
                break

        return matched

    def deconfigure_all(self):
        for dev in self._devices.itervalues():
            pass
            # dev.clear_configuration()

    def create_device(self, clsname, args=[], kwargs={}):
        devcls = self._device_classes[clsname]

        device = devcls(self, *args, **kwargs)
        device._create()

        devs = scan_netdevs()
        for dev in devs:
            if dev["name"] == device.name:
                device._init_netlink(dev['netlink_msg'])
                self._devices[dev['index']] = device
                return device

        return None

    def replace_dev(self, if_id, dev):
        del self._devices[if_id]
        self._devices[if_id] = dev

    def _is_name_used(self, name):
        self.rescan_devices()
        for device in self._devices.itervalues():
            if name == device.name:
                return True

        out, _ = exec_cmd("ovs-vsctl --columns=name list Interface",
                          log_outputs=False, die_on_err=False)
        for line in out.split("\n"):
            m = re.match(r'.*: \"(.*)\"', line)
            if m is not None:
                if name == m.group(1):
                    return True
        return False

    def assign_name(self, prefix):
        index = 0
        while (self._is_name_used(prefix + str(index))):
            index += 1
        return prefix + str(index)

    def _assign_name_pair(self, prefix):
        index1 = 0
        index2 = 0
        while (self._is_name_used(prefix + str(index1))):
            index1 += 1
        index2 = index1 + 1
        while (self._is_name_used(prefix + str(index2))):
            index2 += 1
        return prefix + str(index1), prefix + str(index2)
