import enum
import ctypes
import functools
import logging
from collections import OrderedDict

from qtpy.QtCore import Slot

from pydm.utilities.channel import parse_channel_config
from pydm.data_store import DataKeys
from pydm.data_plugins.plugin import PyDMPlugin, PyDMConnection


import pyads
from pyads import structs, constants


logger = logging.getLogger(__name__)


class ADST_Type(enum.IntEnum):
    VOID = 0
    INT8 = 16
    UINT8 = 17
    INT16 = 2
    UINT16 = 18
    INT32 = 3
    UINT32 = 19
    INT64 = 20
    UINT64 = 21
    REAL32 = 4
    REAL64 = 5
    BIGTYPE = 65
    STRING = 30
    WSTRING = 31
    REAL80 = 32
    BIT = 33
    MAXTYPES = 34


ads_type_to_ctype = {
    # ADST_VOID
    ADST_Type.INT8: constants.PLCTYPE_BYTE,
    ADST_Type.UINT8: constants.PLCTYPE_UBYTE,
    ADST_Type.INT16: constants.PLCTYPE_INT,
    ADST_Type.UINT16: constants.PLCTYPE_UINT,
    ADST_Type.INT32: constants.PLCTYPE_DINT,
    ADST_Type.UINT32: constants.PLCTYPE_UDINT,
    ADST_Type.INT64: constants.PLCTYPE_LINT,
    ADST_Type.UINT64: constants.PLCTYPE_ULINT,
    ADST_Type.REAL32: constants.PLCTYPE_REAL,
    ADST_Type.REAL64: constants.PLCTYPE_LREAL,
    # ADST_BIGTYPE
    ADST_Type.STRING: constants.PLCTYPE_STRING,
    # ADST_WSTRING
    # ADST_REAL80
    ADST_Type.BIT: constants.PLCTYPE_BOOL,
}


def parse_address(addr):
    'ads://<host>[:<port>][/@reserved]/<symbol>'
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
        reserved = poll_info.lstrip('@')
    else:
        # for future usage
        reserved = 0.5

    return {'ip_address': ip_address,
            'host': host,
            'ams_id': ams_id,
            'port': int(port),
            'reserved': float(reserved),
            'symbol': symbol,
            'use_notify': True,
            }


def get_symbol_information(plc, symbol_name) -> structs.SAdsSymbolEntry:
    return plc.read_write(
        constants.ADSIGRP_SYM_INFOBYNAMEEX,
        0x0,
        structs.SAdsSymbolEntry,
        symbol_name,
        constants.PLCTYPE_STRING,
    )


def get_symbol_data_type(plc, symbol_name, *, custom_types=None):
    info = get_symbol_information(plc, symbol_name)
    type_name = info.type_name
    data_type_int = info.dataType

    if custom_types is None:
        custom_types = {}

    if data_type_int in custom_types:
        data_type = custom_types[data_type_int]
    elif type_name in custom_types:
        # Potential feature: allow mapping of type names to structures by
        # registering them in `custom_types`
        data_type = custom_types[type_name]
    elif data_type_int in ads_type_to_ctype:
        data_type = ads_type_to_ctype[data_type_int]
    elif type_name in ads_type_to_ctype:
        # Potential feature: allow mapping of type names to structures by
        # registering them in `ads_type_to_ctype`
        data_type = ads_type_to_ctype[type_name]
    else:
        raise ValueError(
            'Unsupported data type {!r} (number={} size={} comment={!r})'
            ''.format(type_name, data_type_int,
                      info.size, info.comment)
        )

    if data_type is constants.PLCTYPE_STRING:
        array_length = 1
    else:
        # String types are handled directly by adsSyncReadReqEx2.
        # Otherwise, if the reported size is larger than the data type
        # size, it is an array of that type:
        array_length = info.size // ctypes.sizeof(data_type)
        if array_length > 1:
            data_type = data_type * array_length

    return data_type, array_length


def enumerate_plc_symbols(plc):
    symbol_info = plc.read(constants.ADSIGRP_SYM_UPLOADINFO, 0x0,
                           structs.SAdsSymbolUploadInfo)

    symbol_buffer = bytearray(
        plc.read(constants.ADSIGRP_SYM_UPLOAD, 0,
                 ctypes.c_ubyte * symbol_info.nSymSize,
                 return_ctypes=True))

    symbol_buffer = bytearray(symbol_buffer)

    symbols = {}
    while symbol_buffer:
        if len(symbol_buffer) < ctypes.sizeof(structs.SAdsSymbolEntry):
            symbol_buffer += (bytearray(ctypes.sizeof(structs.SAdsSymbolEntry)
                                        - len(symbol_buffer)))
        entry = structs.SAdsSymbolEntry.from_buffer(symbol_buffer)
        if entry.entryLength == 0:
            break

        symbols[entry.name] = {'entry': entry,
                               'type': entry.type_name,
                               'comment': entry.comment}
        symbol_buffer = symbol_buffer[entry.entryLength:]

    return symbols


class Symbol:
    def __init__(self, plc, symbol):
        self.plc = plc
        self.symbol = symbol
        self.connection = None
        self.ads = self.plc.ads
        self.data_type = None
        self.array_size = None

    def set_connection(self, conn):
        self._conn = conn
        self.data_type, self.array_size = get_symbol_data_type(
            self.ads, self.symbol)

        self._conn.data[DataKeys.VALUE] = 3
        self._conn.send_to_channel()


class Plc:
    def __init__(self, ip_address, ams_id, port):
        self.ip_address = ip_address
        self.ams_id = ams_id
        self.port = port
        self.symbols = {}
        self.ads = pyads.Connection(ams_id, port, ip_address=ip_address)

    def clear_symbol(self, symbol):
        _ = self.symbols.pop(symbol)
        if not self.symbols:
            self.ads.close()

    def get_symbol(self, symbol_name):
        try:
            return self.symbols[symbol_name]
        except KeyError:
            if not self.ads.is_open:
                self.ads.open()
            self.symbols[symbol_name] = Symbol(self, symbol_name)
            return self.symbols[symbol_name]


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

        self.symbol_name = self.address['symbol']
        self.symbol = self.conn.get_symbol(self.symbol_name)
        self.symbol.set_connection(self)

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
        self.conn.clear_symbol(self.symbol_name)
        super().close()


class ADSPlugin(PyDMPlugin):
    protocol = 'ads'
    connection_class = Connection
