import logging

from qtpy import QtCore, QtWidgets

from pydm.utilities.channel import parse_channel_config
from pydm.data_plugins.plugin import PyDMPlugin, PyDMConnection, BaseParameterEditor

from ads_pcds import (get_connection, parse_address, Symbol,
                      make_address)
from ads_pcds.ads import enumerate_plc_symbols

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

    def receive_from_channel(self, payload):
        value = payload['VALUE']
        self.symbol.write(value)

    def close(self):
        print('connection closed', self.symbol_name)
        self.plc.clear_symbol(self.symbol_name)
        super().close()


class AdsBrowser(QtWidgets.QDialog):
    symbol_selected = QtCore.Signal(dict)

    def __init__(self, ip_address, ams_id, port, *, parent=None):
        super().__init__(parent=parent)
        self.plc = get_connection(ip_address, ams_id, port)
        self.plc.ads.open()

        self.symbol_table = QtWidgets.QTableWidget()
        self.symbol_table.setColumnCount(3)
        self.layout = QtWidgets.QVBoxLayout()
        self.layout.addWidget(self.symbol_table)
        self.setLayout(self.layout)

        self.update_symbols()

    def closeEvent(self, ev):
        super().closeEvent(ev)
        self.plc.ads.close()

    def update_symbols(self):
        self.symbols = enumerate_plc_symbols(self.plc.ads)

        self.symbol_table.clear()
        self.symbol_table.setRowCount(len(self.symbols))
        table = self.symbol_table
        table.setHorizontalHeaderLabels(['Name', 'Type', 'Comment'])
        table.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.AdjustToContents)
        for row, (symbol_name, info) in enumerate(self.symbols.items()):
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(symbol_name))
            table.setItem(row, 1, QtWidgets.QTableWidgetItem(info['type']))
            table.setItem(row, 2, QtWidgets.QTableWidgetItem(info['comment']))


class AdsParameterEditor(BaseParameterEditor):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QtWidgets.QFormLayout()
        self.setLayout(self.layout)

        self.layout.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.AllNonFixedFieldsGrow
        )

        self.uri_widget = None
        self.ip_widget = None
        self.ams_id_widget = None
        self.port_widget = None
        self.poll_rate_widget = None
        self.symbol_widget = None

        for label, widget_name, cls in [
                ('URI', 'uri_widget', QtWidgets.QLineEdit),
                ('IP address', 'ip_widget', QtWidgets.QLineEdit),
                ('AMS ID', 'ams_id_widget', QtWidgets.QLineEdit),
                ('Symbol', 'symbol_widget', QtWidgets.QLineEdit),
                ('Port', 'port_widget', QtWidgets.QLineEdit),
                ('Poll rate', 'poll_rate_widget', QtWidgets.QLineEdit),
                ]:
            widget = cls(self)
            setattr(self, widget_name, widget)
            callback = getattr(self, f'{widget_name}_changed', None)
            if callback is not None:
                widget.editingFinished.connect(callback)

            self.layout.addRow(QtWidgets.QLabel(label), widget)

        self.update_widget = QtWidgets.QPushButton('Update URI')
        self.update_widget.clicked.connect(self._update_uri)

        self.browse_widget = QtWidgets.QPushButton('Browse')
        self.browse_widget.clicked.connect(self._browse)

        self.layout.addRow(self.browse_widget, self.update_widget)

    def _browse(self):
        try:
            info = self.address_info
            self.browser = AdsBrowser(info['ip_address'], info['ams_id'],
                                      info['port'], parent=self)
            self.browser.show()
        except Exception:
            logger.exception('Unable to show browser :(')
            return

    def _update_uri(self):
        ip_address = self.ip_widget.text() or None
        ams_id = self.ams_id_widget.text() or None
        port = self.port_widget.text() or None
        symbol = self.symbol_widget.text() or None
        poll_rate = self.poll_rate_widget.text() or None
        try:
            address = make_address(ip_address, ams_id, port=port,
                                   symbol=symbol, poll_rate=poll_rate)
        except Exception:
            logger.exception('Unable to make address')
            return

        self.uri_widget.setText(address[6:])

    @property
    def address_info(self):
        text = self.uri_widget.text()
        return parse_address(text, allow_macros=True)

    def uri_widget_changed(self):
        try:
            info = self.address_info
        except Exception:
            logger.exception('Unable to parse address')
            return
        self.ip_widget.setText(info['ip_address'])
        self.ams_id_widget.setText(info['ams_id'])
        self.port_widget.setText(str(info['port']))
        self.symbol_widget.setText(str(info['symbol']))
        self.poll_rate_widget.setText(str(info['poll_rate']))

    @property
    def parameters(self):
        return {'address': self.uri_widget.text()}

    @parameters.setter
    def parameters(self, params):
        address = params.get('address', '')
        self.uri_widget.setText(address)

    def validate(self):
        return True, ''

    def clear(self):
        self.uri_widget.setText('')

    @staticmethod
    def get_repr(parameters):
        return parameters.get('address', '')


class ADSPlugin(PyDMPlugin):
    protocol = 'ads'
    connection_class = Connection
    param_editor = AdsParameterEditor
