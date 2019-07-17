import logging
from collections import OrderedDict

from qtpy.QtCore import Slot

from pydm.utilities.channel import parse_channel_config
from pydm.data_store import DataKeys
from pydm.data_plugins.plugin import PyDMPlugin, PyDMConnection


import pyads


logger = logging.getLogger(__name__)


def parse_address(addr):
    'ads://<host>[:<port>][/@reserved]/<variable>'
    host_info, _, variable = addr.partition('/')

    if ':' in host_info:
        host, port = host_info.split(':')
    else:
        host, port = host_info, 851

    if '@' in host:
        ams_id, ip_address = host.split('@')
    elif host.count('.') == 3:
        ip_address = host
        ams_id = '{}.1.1'.format(ip_address)
    elif host.count('.') == 5:
        ams_id = host
        if not ams_id.endswith('.1.1'):
            raise ValueError('Cannot assume IP address without an AMS ID '
                             'that ends with .1.1')
        ip_address = ams_id[:-4]
    else:
        raise ValueError(f'Cannot parse host string: {host!r}')

    if variable.startswith('@') and '/' in variable:
        poll_info, _, variable = variable.partition('/')
        reserved = poll_info.lstrip('@')
    else:
        # for future usage
        reserved = 0.5

    return {'ip_address': ip_address,
            'host': host,
            'ams_id': ams_id,
            'port': int(port),
            'reserved': float(reserved),
            'variable': variable,
            'use_notify': True,
            }


class Variable:
    def __init__(self, plc, variable):
        self.plc = plc
        self.variable = variable
        self._conn = None

    @property
    def connection(self):
        return self._conn

    @connection.setter
    def connection(self, conn):
        self._conn = conn
        self._conn.data[DataKeys.VALUE] = 3
        self._conn.send_to_channel()


class Plc:
    def __init__(self, ip_address, ams_id, port):
        self.ip_address = ip_address
        self.ams_id = ams_id
        self.port = port
        self.variables = {}
        self.ads = pyads.Connection(ams_id, port, ip_address=ip_address)

    def __delitem__(self, variable):
        _ = self.variables.pop(variable)
        if not self.variables:
            self.ads.close()

    def __getitem__(self, variable):
        try:
            return self.variables[variable]
        except KeyError:
            self.variables[variable] = Variable(self, variable)
            if not self.ads.is_open:
                self.ads.open()

            return self.variables[variable]


_PLCS = {}


def get_connection(ip_address, ams_id, port):
    key = (ip_address, ams_id, port)
    try:
        return _PLCS[key]
    except KeyError:
        plc = Plc(ip_address, ams_id, port)
        _PLCS[key] = plc
        return plc


class Connection(PyDMConnection):
    def __init__(self, channel, address, protocol=None, parent=None):
        super().__init__(channel, address, protocol, parent)
        conn = parse_channel_config(address, force_dict=True)['connection']
        address = conn.get('parameters', {}).get('address')

        self.address = parse_address(address)
        self.ip_address = self.address['ip_address']
        self.ams_id = self.address['ams_id']
        self.port = self.address['port']
        self.conn = get_connection(ip_address=self.ip_address,
                                   ams_id=self.ams_id, port=self.port)

        self.variable_name = self.address['variable']
        self.variable = self.conn[self.variable_name]
        self.variable.connection = self

    def send_new_value(self, payload):
        # if isinstance(payload, Disconnected):
        #     self.data = {'CONNECTION': False}
        # else:
        #     self.data = payload.todict(None, OrderedDict)
        #     if self.nt_id != payload.getID():
        #         self.nt_id = payload.getID()
        #         intro_keys = nt_introspection.get(self.nt_id, None)
        #         if intro_keys is not None:
        #             self.introspection = DataKeys.generate_introspection_for(
        #                 **intro_keys
        #             )
        #     pre_process(self.data, payload.getID())
        #     self.data['CONNECTION'] = True
        #     self.data['WRITE_ACCESS'] = True
        # self.send_to_channel()
        ...

    @Slot(dict)
    def receive_from_channel(self, payload):
        ...

    def close(self):
        super().close()
        del self.conn[self.variable_name]
        print('connection closed')


class ADSPlugin(PyDMPlugin):
    protocol = 'ads'
    connection_class = Connection
