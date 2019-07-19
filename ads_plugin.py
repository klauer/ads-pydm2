import logging

from qtpy import QtCore

from pydm.utilities.channel import parse_channel_config
from pydm.data_plugins.plugin import PyDMPlugin, PyDMConnection


from ads_pcds import get_connection

logger = logging.getLogger(__name__)


def parse_address(addr):
    'ads://<host>[:<port>][/@poll_rate]/<symbol>'
    host_info, _, symbol = addr.partition('/')

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

    if symbol.startswith('@') and '/' in symbol:
        poll_info, _, symbol = symbol.partition('/')
        poll_rate = poll_info.lstrip('@')
    else:
        poll_rate = None

    return {'ip_address': ip_address,
            'host': host,
            'ams_id': ams_id,
            'port': int(port),
            'poll_rate': float(poll_rate) if poll_rate is not None else None,
            'symbol': symbol,
            'use_notify': True,
            }


class Connection(PyDMConnection):
    def __init__(self, channel, address, protocol=None, parent=None):
        super().__init__(channel, address, protocol, parent)
        conn = parse_channel_config(address, force_dict=True)['connection']
        address = conn.get('parameters', {}).get('address')

        self.address = parse_address(address)
        self.ip_address = self.address['ip_address']
        self.ams_id = self.address['ams_id']
        self.port = self.address['port']
        self.poll_rate = self.address['poll_rate']
        self.plc = get_connection(ip_address=self.ip_address,
                                  ams_id=self.ams_id, port=self.port)

        self.symbol_name = self.address['symbol']
        self.symbol = self.plc.get_symbol(self.symbol_name, self.poll_rate)
        self.symbol.set_connection(self)

    def send_new_value(self, payload):
        self.data.update(payload)
        self.send_to_channel()

    @QtCore.Slot(dict)
    def receive_from_channel(self, payload):
        value = payload['VALUE']
        self.symbol.write(value)

    def close(self):
        print('connection closed', self.symbol_name)
        self.plc.clear_symbol(self.symbol_name)
        super().close()


class ADSPlugin(PyDMPlugin):
    protocol = 'ads'
    connection_class = Connection
