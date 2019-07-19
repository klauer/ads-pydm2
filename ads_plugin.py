import logging

from qtpy import QtCore

from pydm.utilities.channel import parse_channel_config
from pydm.data_plugins.plugin import PyDMPlugin, PyDMConnection

from ads_pcds import get_connection, parse_address, Symbol

logger = logging.getLogger(__name__)


class SymbolForPydm(Symbol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data = {'CONNECTION': False}

    def value_updated(self, timestamp, value):
        self.data.update(**{
            'CONNECTION': True,
            'VALUE': value,
            'WRITE_ACCESS': True,
            # 'TIMESTAMP': time.time(),
        })
        self.pydm_connection.send_new_value(self.data)

    def set_connection(self, pydm_connection):
        self.pydm_connection = pydm_connection
        self.start()


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
        self.symbol = self.plc.get_symbol(self.symbol_name, self.poll_rate,
                                          cls=SymbolForPydm)
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
