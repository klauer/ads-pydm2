import ctypes
import enum
import logging
import queue
import struct
import threading
import time

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


def get_symbol_information(plc, symbol_name) -> structs.SAdsSymbolEntry:
    return plc.read_write(
        constants.ADSIGRP_SYM_INFOBYNAMEEX,
        0x0,
        structs.SAdsSymbolEntry,
        symbol_name,
        constants.PLCTYPE_STRING,
    )


def unpack_notification(notification, plc_datatype):
    contents = notification.contents
    data_size = contents.cbSampleSize
    # Get dynamically sized data array
    data = (ctypes.c_ubyte * data_size).from_address(
        ctypes.addressof(contents) +
        structs.SAdsNotificationHeader.data.offset)

    datatype_map = {
        constants.PLCTYPE_BOOL: "<?",
        constants.PLCTYPE_BYTE: "<c",
        constants.PLCTYPE_DINT: "<i",
        constants.PLCTYPE_DWORD: "<I",
        constants.PLCTYPE_INT: "<h",
        constants.PLCTYPE_LREAL: "<d",
        constants.PLCTYPE_REAL: "<f",
        constants.PLCTYPE_SINT: "<b",
        constants.PLCTYPE_UDINT: "<L",
        constants.PLCTYPE_UINT: "<H",
        constants.PLCTYPE_USINT: "<B",
        constants.PLCTYPE_WORD: "<H",
    }

    if plc_datatype == constants.PLCTYPE_STRING:
        # read only until null-termination character
        value = bytearray(data).split(b"\0", 1)[0].decode("utf-8")

    elif issubclass(plc_datatype, ctypes.Structure):
        value = plc_datatype()
        fit_size = min(data_size, ctypes.sizeof(value))
        ctypes.memmove(ctypes.addressof(value), ctypes.addressof(data),
                       fit_size)
    elif plc_datatype not in datatype_map:
        value = bytearray(data)
    else:
        value, = struct.unpack(datatype_map[plc_datatype], bytearray(data))

    timestamp = pyads.filetimes.filetime_to_dt(contents.nTimeStamp)
    return timestamp, value


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

    if symbol_info is None:
        raise RuntimeError('PLC connection not open')

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
    def __init__(self, plc, symbol, poll_rate):
        self._subscribed = False
        self.plc = plc
        self.symbol = symbol
        self.connection = None
        self.ads = self.plc.ads
        self.data_type = None
        self.array_size = None
        self.notification_handle = None
        self.poll_rate = poll_rate

    def value_updated(self, timestamp, value):
        'Value update hook for subclasses'

    def _notification_update(self, notification, name):
        timestamp, value = unpack_notification(notification, self.data_type)
        self.value_updated(timestamp, value)

    def _update_data_type(self):
        self.data_type, self.array_size = get_symbol_data_type(
            self.ads, self.symbol)

    def read(self):
        if self.data_type is None:
            self._update_data_type()
        return self.ads.read_by_name(self.symbol, plc_datatype=self.data_type)

    def write(self, value):
        try:
            if self.data_type is None:
                self._update_data_type()
            if self.data_type not in (constants.PLCTYPE_REAL,
                                      constants.PLCTYPE_LREAL):
                # TODO ... int types
                value = int(value)
            self.ads.write_by_name(self.symbol, value=value,
                                   plc_datatype=self.data_type)
        except Exception:
            logger.exception('Failed to write %s to %s', self.symbol, value)

    def _poll(self):
        if self.data_type is None:
            self._update_data_type()
        value = self.ads.read_by_name(self.symbol, plc_datatype=self.data_type)
        self.value_updated(time.time(), value)

    def start(self):
        if self._subscribed:
            return

        self._subscribed = True

        def init():
            if self.poll_rate is None:
                self._update_data_type()
                attr = pyads.NotificationAttrib(ctypes.sizeof(self.data_type))
                self.notification_handle = self.ads.add_device_notification(
                    self.symbol, attr, self._notification_update)
            else:
                self._poll()

        self.plc.add_to_queue(init)
        if self.poll_rate is not None:
            self.plc.add_to_poll_thread(self.poll_rate, self._poll)

    def stop(self):
        if not self._subscribed:
            return

        self.plc.stop_polling(self.poll_rate, self._poll)
        handle = self.notification_handle
        if self.poll_rate is None and handle is not None:
            self.notification_handle = None
            self.ads.del_device_notification(*handle)

        self._subscribed = False


class Plc:
    def __init__(self, ip_address, ams_id, port):
        self.running = True
        self.ip_address = ip_address
        self.ams_id = ams_id
        self.port = port
        self.symbols = {}
        self.ads = pyads.Connection(ams_id, port, ip_address=ip_address)
        self.queue = queue.Queue()
        self.thread = threading.Thread(target=self._thread, daemon=True)
        self.thread.start()
        self.poll_threads = {}

    def stop_polling(self, rate, func, *args, **kwargs):
        if rate not in self.poll_threads:
            return

        # TODO cid
        self.poll_threads[rate]['calls'].remove((func, args, kwargs))

    def add_to_poll_thread(self, rate, func, *args, **kwargs):
        if rate not in self.poll_threads:
            thread = threading.Thread(target=self._poll_thread, args=(rate, ),
                                      daemon=True)
            self.poll_threads[rate] = dict(thread=thread, calls=[])
            thread.start()

        self.poll_threads[rate]['calls'].append((func, args, kwargs))

    def stop(self):
        self.running = False
        self.add_to_queue(lambda: None)

    def add_to_queue(self, func, *args, **kwargs):
        self.queue.put((func, args, kwargs))

    def _poll_thread(self, rate):
        while self.running:
            info = self.poll_threads[rate]
            t0 = time.time()
            for item in list(info['calls']):
                func, args, kwargs = item
                try:
                    func(*args, **kwargs)
                except Exception:
                    logger.exception(
                        'Poll thread %s:%s:%d @ %.3f sec failure: '
                        '%s(*%r, **%r)',
                        self.ip_address, self.ams_id, self.port,
                        rate, func.__name__, args, kwargs
                    )
                    info['calls'].remove(item)
            elapsed = time.time() - t0
            time.sleep(max((0, rate - elapsed)))

    def _thread(self):
        while self.running:
            func, args, kwargs = self.queue.get()
            try:
                func(*args, **kwargs)
            except Exception:
                logger.exception('PLC thread %s:%s:%d failure: %s(*%r, **%r)',
                                 self.ip_address, self.ams_id, self.port,
                                 func.__name__, args, kwargs)
        self.ads.close()

    def clear_symbol(self, symbol):
        _ = self.symbols.pop(symbol)
        if not self.symbols:
            self.ads.close()

    def get_symbol(self, symbol_name, poll_rate, *, cls=Symbol):
        key = (symbol_name, poll_rate, cls)
        try:
            return self.symbols[key]
        except KeyError:
            if not self.ads.is_open:
                self.ads.open()
            self.symbols[key] = cls(self, symbol_name, poll_rate)
            return self.symbols[key]


_PLCS = {}


def get_connection(ip_address, ams_id, port):
    key = (ip_address, ams_id, port)
    try:
        return _PLCS[key]
    except KeyError:
        plc = Plc(ip_address, ams_id, port)
        _PLCS[key] = plc
        return plc
